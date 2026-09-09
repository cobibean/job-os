"""One complete OAuth → remote MCP → real JobOS API → document bytes proof."""

import asyncio
import base64
import io
import sqlite3
from contextlib import closing
from hashlib import sha256

import httpx
import pytest
from docx import Document
from jobos_api.app import create_app
from jobos_api.career_profile_complete import CareerProfileCompleteStore
from jobos_api.career_profile_migration import (
    CareerProfileMigrationBundle,
    CareerProfileMigrationService,
)
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore
from jobos_mcp.external import create_external_server
from jobos_mcp.jobs import JobOsMcpClient
from jobos_mcp.remote_auth import OwnerOAuthProvider
from jobos_mcp.remote_http import create_http_app
from starlette.testclient import TestClient
from test_remote_http import BASE, PASSWORD, authorize, exchange


@pytest.mark.parametrize("job_provider", ["sqlite", "job-hunter"])
def test_authenticated_remote_jobs_profile_and_files(tmp_path, job_provider):
    internal, external, device = (
        "synthetic-internal-token",
        "synthetic-external-token",
        "synthetic-device-token",
    )
    provider_options = {}
    storage = None
    if job_provider == "job-hunter":
        provider = pytest.importorskip("job_hunter.storage")
        database = tmp_path / "provider-jobs.db"
        storage = provider.JobStorage(database)
        provider_options = {"job_provider": job_provider, "job_hunter_db_path": database}
    settings = Settings(
        **provider_options,
        device_token=device,
        mcp_token=internal,
        external_mcp_token=external,
        state_db_path=tmp_path / "state.db",
        career_profile_enabled=True,
    )
    JobOsStateStore(settings.state_db_path).initialize(owner_device_id=settings.device_id)
    CareerProfileCompleteStore(
        settings.state_db_path, settings.resolved_evidence_vault_root()
    ).initialize()
    CareerProfileMigrationService(
        settings.state_db_path, settings.resolved_evidence_vault_root()
    ).run(
        CareerProfileMigrationBundle(
            schema_version=1, bundle_label="Synthetic MCP integration", evidence=[], facts=[]
        )
    )
    api_app = create_app(settings)
    mcp_client = JobOsMcpClient(
        base_url="http://api",
        device_token="unused",
        mcp_token=external,
        external=True,
        agent_token=internal,
        transport=httpx.ASGITransport(app=api_app),
    )
    server = create_external_server(
        mcp_client, artifact_root=settings.resolved_local_artifact_root()
    )
    provider = OwnerOAuthProvider(tmp_path / "oauth.db", BASE, PASSWORD)
    try:
        with (
            TestClient(api_app) as api,
            TestClient(create_http_app(server, provider), base_url=BASE) as remote,
        ):
            owner = {"Authorization": "Bearer " + device}
            assert (
                api.patch(
                    "/v1/career-profile/agents/trusted-local-mcp",
                    headers=owner,
                    json={"trust_mode": "direct"},
                ).status_code
                == 200
            )
            assert (
                api.put(
                    "/v1/career-profile/agents/trusted-local-mcp/context",
                    headers=owner,
                    json={
                        "expected_profile_revision": 0,
                        "expected_authority_epoch": 0,
                        "idempotency_key": "synthetic-remote-scope",
                        "mode": "broader",
                        "selected_item_ids": [],
                        "selected_areas": [],
                    },
                ).status_code
                == 200
            )
            assert (
                api.post(
                    "/v1/career-profile/authority/activate",
                    headers=owner,
                    json={
                        "expected_profile_revision": 0,
                        "expected_authority_epoch": 0,
                        "idempotency_key": "synthetic-remote-authority",
                        "confirmation": "CUT OVER CAREER PROFILE AUTHORITY",
                    },
                ).status_code
                == 200
            )
            info, code = authorize(remote)
            tokens = exchange(remote, info, code).json()
            headers = {
                "Authorization": "Bearer " + tokens["access_token"],
                "Accept": "application/json, text/event-stream",
            }

            def rpc(method, params, *, expect_error=False):
                response = remote.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                assert response.status_code == 200, response.text
                body = response.json()
                assert "error" not in body, body
                result = body["result"]
                assert bool(result.get("isError")) is expect_error, result
                return result

            def tool(name, **arguments):
                return rpc("tools/call", {"name": name, "arguments": arguments})[
                    "structuredContent"
                ]

            catalog = rpc("tools/list", {})["tools"]
            assert len(catalog) == 50
            assert all(
                "turn_id" not in item["inputSchema"].get("properties", {}) for item in catalog
            )
            profile = tool("career_profile_get")
            assert profile is not None
            edited = tool(
                "career_profile_edit",
                expected_profile_revision=0,
                operation="item.create",
                reason="Synthetic integration test",
                value={"kind": "skill", "name": "Synthetic Python"},
            )
            assert edited["outcome"] == "applied"
            found = tool("career_profile_search", query="Synthetic Python")
            assert "Synthetic Python" in str(found)
            with closing(sqlite3.connect(settings.state_db_path)) as connection:
                initial_conversations = connection.execute("SELECT * FROM conversations").fetchall()
            ingest = {
                "company_name": "(FAKE) Example",
                "title": "(FAKE) Engineer",
                "canonical_url": "https://example.com/remote-smoke",
                "location_text": "Remote",
                "description_text": "Synthetic role",
                "application_url": "https://example.com/apply",
                "listing_source_url": "https://example.com/remote-smoke",
                "listing_capture_method": "web_extract",
                "listing_evidence": {"note": "Synthetic source excerpt"},
                "idempotency_key": "synthetic-ingest",
            }
            for invalid in (
                {"company_name": " "},
                {"listing_source_url": "https://user:password@example.com/jobs"},
                {"listing_capture_method": " "},
                {"listing_sha256": "not-a-digest"},
                {"listing_captured_at": "not-a-date"},
                {"canonical_url": "file:///synthetic-job"},
            ):
                rpc(
                    "tools/call",
                    {
                        "name": "job_ingest",
                        "arguments": {**ingest, **invalid},
                    },
                    expect_error=True,
                )
            missing_source = {
                key: value for key, value in ingest.items() if key != "listing_source_url"
            }
            rpc(
                "tools/call", {"name": "job_ingest", "arguments": missing_source}, expect_error=True
            )
            job = tool("job_ingest", **ingest)
            assert job["created"] is True
            assert "conversation_id" not in job
            job_id = job["job"]["job_id"]
            assert job["job"]["listing_completeness"] == (
                "partial" if job_provider == "job-hunter" else "unknown"
            )
            assert job["job"]["listing_verified_at"] is None
            assert job["job"]["listing_capture_method"] == "web_extract"
            assert job["job"]["listing_evidence"]["note"] == ingest["listing_evidence"]["note"]
            tool("job_update_status", job_id=job_id, target_status="shortlisted")
            assert tool("job_inspect", job_id=job_id)["status_group"] == "Considering"
            assert tool("job_ingest", **ingest) == job  # Replay, not a second mutation.
            conflict = rpc(
                "tools/call",
                {
                    "name": "job_ingest",
                    "arguments": {**ingest, "title": "Changed title"},
                },
                expect_error=True,
            )
            assert "http_409" in str(conflict)
            duplicate = tool(
                "job_ingest",
                **{
                    **ingest,
                    "canonical_url": "https://EXAMPLE.com/remote-smoke/#listing",
                    "idempotency_key": "synthetic-reimport",
                },
            )
            assert duplicate["created"] is False
            assert duplicate["job"]["job_id"] == job_id
            assert duplicate["job"]["status"] == "shortlisted"
            assert tool("job_inspect", job_id=job_id)["status_group"] == "Considering"
            complete_text = (
                "Synthetic Engineer at (FAKE) Example. This fictional role builds local-first "
                "software used only in isolated integration tests. Remote location.\n"
                "Responsibilities\n"
                "- Build and maintain synthetic Python examples with documented interfaces.\n"
                "- Review changes and verify behavior with repeatable integration tests.\n"
                "Requirements\n"
                "- Experience writing Python and maintaining automated test suites.\n"
                "- Ability to explain implementation decisions and document source evidence."
            )
            complete = tool(
                "job_ingest",
                **{
                    **ingest,
                    "full_listing_text": complete_text,
                    "listing_completeness": "complete",
                    "listing_captured_at": duplicate["job"]["listing_captured_at"],
                    "listing_verified_at": duplicate["job"]["listing_captured_at"],
                    "listing_sha256": sha256(complete_text.encode()).hexdigest(),
                    "listing_evidence": {
                        "schema_version": 1,
                        "coverage": "complete",
                        "end_of_listing_seen": True,
                        "verified_text_sha256": sha256(complete_text.encode()).hexdigest(),
                    },
                    "idempotency_key": "synthetic-complete",
                },
            )
            assert complete["job"]["listing_completeness"] == "complete"
            assert complete["job"]["full_listing_text"] == complete_text
            partial = tool(
                "job_ingest",
                **{
                    **ingest,
                    "listing_completeness": "partial",
                    "idempotency_key": "synthetic-partial",
                },
            )
            assert partial["job"]["full_listing_text"] == complete_text
            assert partial["job"]["listing_completeness"] == "complete"
            assert partial["job"]["status_group"] == "Considering"
            if storage is not None:
                persisted = storage.get_job(job_id)
                assert persisted.source_system == "jobos_ingest"
                assert persisted.source_key == "jobos_ingest"
                assert persisted.source_metadata["capture_method"] == "web_extract"
                assert persisted.listing_capture_method == "web_extract"
            # No implicit external context, chat turn, or browser is needed for ingest/status.
            with closing(sqlite3.connect(settings.state_db_path)) as connection:
                assert (
                    connection.execute("SELECT * FROM conversations").fetchall()
                    == initial_conversations
                )
                assert (
                    connection.execute("SELECT count(*) FROM conversation_turns").fetchone()[0] == 0
                )
            generated = tool(
                "document_generate",
                job_id=job_id,
                document_key="resume",
                document_label="Synthetic remote resume",
                markdown=(
                    "# Synthetic Candidate\n## Skills\n- Python\n## Experience\nBuilt examples."
                ),
            )
            assert generated["published"] is True
            artifacts = generated["documents"]["artifacts"]
            assert len(artifacts) == 2
            for artifact in artifacts:
                data = tool("artifact_read", artifact_id=artifact["artifact_id"])
                content = base64.b64decode(data["content"])
                assert not data["has_more"]
                assert sha256(content).hexdigest() == artifact["sha256"]
                if content.startswith(b"PK"):
                    assert "Synthetic Candidate" in [
                        p.text for p in Document(io.BytesIO(content)).paragraphs
                    ]
                else:
                    assert content.startswith(b"%PDF-")
            assert len(tool("document_list", job_id=job_id)["artifacts"]) == 2
    finally:
        asyncio.run(mcp_client.aclose())

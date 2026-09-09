"""One complete OAuth → remote MCP → real JobOS API → document bytes proof."""

import asyncio
import base64
import io
from hashlib import sha256

import httpx
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


def test_authenticated_remote_jobs_profile_and_files(tmp_path):
    internal, external, device = (
        "synthetic-internal-token",
        "synthetic-external-token",
        "synthetic-device-token",
    )
    settings = Settings(
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

            def rpc(method, params):
                response = remote.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                assert response.status_code == 200, response.text
                body = response.json()
                assert "error" not in body, body
                result = body["result"]
                assert not result.get("isError"), result
                return result

            def tool(name, **arguments):
                return rpc("tools/call", {"name": name, "arguments": arguments})[
                    "structuredContent"
                ]

            catalog = rpc("tools/list", {})["tools"]
            assert len(catalog) == 49
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
            job = tool(
                "job_create_from_browser",
                company_name="(FAKE) Example",
                title="(FAKE) Engineer",
                canonical_url="https://example.com/remote-smoke",
                location_text="Remote",
                description_text="Synthetic role",
                application_url="https://example.com/apply",
            )
            job_id = job["job"]["job_id"]
            generated = tool(
                "document_generate",
                job_id=job_id,
                document_key="resume",
                document_label="Synthetic remote resume",
                markdown=(
                    "# Synthetic Candidate\n## Skills\n- Python\n"
                    "## Experience\nBuilt examples."
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

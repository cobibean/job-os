from __future__ import annotations

import base64
import hashlib
import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.career_profile_collaboration import CareerProfileCollaborationStore
from jobos_api.career_profile_complete import CareerProfileCompleteStore
from jobos_api.career_profile_context import (
    CareerProfileContextScopeUpdate,
    CareerProfileContextStore,
)
from jobos_api.installation_profiles import (
    InstallationProfileRegistry,
    effective_profile_runtime,
)
from jobos_api.job_repository import CreateJobCommand
from jobos_api.settings import Settings
from jobos_api.sqlite_job_repository import SQLiteJobRepository


def job(job_id: str, url: str) -> CreateJobCommand:
    return CreateJobCommand(
        job_id=job_id,
        company_name="(FAKE) Example Labs",
        title="(FAKE) Product Builder",
        canonical_url=url,
        location_text="Remote",
        description_text="(FAKE) synthetic isolation record",
        application_url=f"{url}/apply",
        observed_at=datetime(2026, 8, 23, 12, tzinfo=UTC),
    )


def active_settings(registry: InstallationProfileRegistry, base: dict[str, object]) -> Settings:
    data, profile = registry.active_profile()
    effective = effective_profile_runtime(base, profile, registry.installation_root)
    return Settings(
        device_token="isolation-device-token",
        mcp_token="isolation-mcp-token-value",
        state_db_path=effective["state_db_path"],
        jobs_db_path=effective["jobs_db_path"],
        local_artifact_root=effective["local_artifact_root"],
        artifact_roots=effective["artifact_roots"],
        job_provider=effective["job_provider"],
        artifact_provider=effective["artifact_provider"],
        installation_profile_id=profile.profile_id,
        installation_profile_name=profile.display_name,
        installation_registry_path=registry.path,
        profile_registry_revision=data.registry_revision,
        profile_switch_driver="desktop",
        career_profile_enabled=True,
        career_profile_agent_id="shared-agent",
        career_profile_agent_display_name="Shared Agent",
    )


def headers(settings: Settings, profile_id: str | None = None) -> dict[str, str]:
    return {
        "Authorization": "Bearer isolation-device-token",
        "X-JobOS-Profile-Id": profile_id or settings.installation_profile_id,
    }


def test_synthetic_a_b_a_restart_and_stale_client_isolation(tmp_path):
    installation = tmp_path / "installation"
    base = {
        "job_provider": "sqlite",
        "artifact_provider": "local",
        "state_db_path": installation / "legacy/state/jobos.db",
        "jobs_db_path": installation / "legacy/jobs/jobs.db",
        "local_artifact_root": installation / "legacy/artifacts",
        "artifact_roots": (),
        "job_hunter_db_path": None,
        "facade_source_path": None,
    }
    registry = InstallationProfileRegistry(installation / "installation-profiles.json")
    profile_a_data = registry.load_or_bootstrap(base)
    profile_a_id = profile_a_data.active_profile_id
    settings_a = active_settings(registry, base)
    repository_a = SQLiteJobRepository(
        settings_a.resolved_jobs_db_path(),
        installation_profile_id=profile_a_id,
    )
    repository_a.create_job(job("fake-a-job", "https://jobs.example.test/a"))
    repository_a.create_job(job("fake-a-job-2", "https://jobs.example.test/a-2"))
    repository_a.update_status("fake-a-job", "shortlisted", reason="(FAKE) reviewed")
    repository_a.update_status("fake-a-job", "applied", reason="(FAKE) submitted")

    with TestClient(create_app(settings_a)) as client:
        user_headers = headers(settings_a)
        created_item = client.post(
            "/v1/career-profile/items",
            headers=user_headers,
            json={
                "expected_profile_revision": 0,
                "idempotency_key": "fake-profile-a-item",
                "value": {"kind": "skill", "name": "(FAKE) Profile A systems"},
                "evidence_ids": [],
            },
        )
        assert created_item.status_code == 201, created_item.text
        profile_item_id = created_item.json()["items"][0]["item_id"]
        evidence_bytes = b"(FAKE) profile A evidence\n"
        imported_evidence = client.post(
            "/v1/career-profile/evidence",
            headers=user_headers,
            json={
                "expected_profile_revision": 1,
                "idempotency_key": "fake-profile-a-evidence",
                "original_filename": "(FAKE)-profile-a-evidence.txt",
                "media_type": "text/plain",
                "provenance": {
                    "source_kind": "supporting_document",
                    "source_label": "(FAKE) profile A evidence",
                    "method": "user_import",
                },
                "content_base64": base64.b64encode(evidence_bytes).decode(),
                "extractions": [],
            },
        )
        assert imported_evidence.status_code == 201, imported_evidence.text
        evidence_id = imported_evidence.json()["source_evidence"][0]["evidence_id"]
        proposal = client.post(
            "/v1/career-profile/agent-edits",
            headers={
                "Authorization": "Bearer isolation-mcp-token-value",
                "X-JobOS-MCP-Token": "isolation-mcp-token-value",
                "X-JobOS-Agent-Id": "shared-agent",
                "X-JobOS-Agent-Token": "isolation-mcp-token-value",
            },
            json={
                "expected_profile_revision": 2,
                "idempotency_key": "fake-profile-a-proposal",
                "operation": "item.create",
                "target_id": None,
                "reason": "(FAKE) remember a profile A skill",
                "value": {"kind": "skill", "name": "(FAKE) Proposed profile A skill"},
                "evidence_ids": [],
            },
        )
        assert proposal.status_code == 200, proposal.text
        proposal_id = proposal.json()["proposal"]["proposal_id"]

        first_conversation = client.post(
            "/v1/conversations", headers=user_headers, json={}
        )
        second_conversation = client.post(
            "/v1/conversations", headers=user_headers, json={}
        )
        assert first_conversation.status_code == second_conversation.status_code == 201
        conversation_id = first_conversation.json()["conversation_id"]
        second_conversation_id = second_conversation.json()["conversation_id"]
        with sqlite3.connect(settings_a.state_db_path) as connection:
            connection.execute(
                "UPDATE conversations SET stored_session_id = ? WHERE conversation_id = ?",
                ("(FAKE)-profile-a-session", conversation_id),
            )
            connection.execute(
                "UPDATE conversations SET stored_session_id = ? WHERE conversation_id = ?",
                ("(FAKE)-profile-a-session-2", second_conversation_id),
            )
        workspace = client.get("/v1/workspace", headers=user_headers).json()
        workspace_command = {
            key: value
            for key, value in workspace.items()
            if key
            not in {
                "repaired_presets",
                "repaired_browser",
                "browser_repair_reasons",
            }
        }
        workspace_command.update(
            {
                "origin": "user",
                "idempotency_key": "fake-profile-a-workspace",
                "browser_tabs": [
                    {
                        "tab_id": "fake-profile-a-tab",
                        "url": "https://jobs.example.test/a",
                        "title": "(FAKE) Profile A job",
                        "favicon_url": None,
                        "associated_job_id": "fake-a-job",
                    }
                ],
                "active_browser_tab_id": "fake-profile-a-tab",
                "browse_query": "(FAKE) profile A query",
            }
        )
        saved_workspace = client.put(
            "/v1/workspace", headers=user_headers, json=workspace_command
        )
        assert saved_workspace.status_code == 200, saved_workspace.text
        reordered = client.put(
            "/v1/jobs/order",
            headers=user_headers,
            json={
                "job_ids": ["fake-a-job-2", "fake-a-job"],
                "origin": "user",
                "idempotency_key": "fake-profile-a-order",
            },
        )
        assert reordered.status_code == 200, reordered.text
        document = client.post(
            "/v1/jobs/fake-a-job/editable-documents",
            headers=user_headers,
            json={
                "mode": "blank",
                "document_key": "resume",
                "idempotency_key": "fake-profile-a-document",
            },
        )
        assert document.status_code == 201, document.text
        document_id = document.json()["document_id"]
        complete_a = CareerProfileCompleteStore(
            settings_a.state_db_path,
            settings_a.resolved_evidence_vault_root(),
        )
        collaboration_a = CareerProfileCollaborationStore(settings_a.state_db_path, complete_a)
        collaboration_a.update_trust_mode(agent_id="shared-agent", trust_mode="direct")
        profile_a = complete_a.current()
        CareerProfileContextStore(settings_a.state_db_path, complete_a).update_scope(
            principal=f"device:{settings_a.device_id}",
            agent_id="shared-agent",
            command=CareerProfileContextScopeUpdate(
                expected_profile_revision=profile_a.profile_revision,
                expected_authority_epoch=profile_a.authority_epoch,
                idempotency_key="fake-profile-a-context-grant",
                mode="broader",
            ),
        )
    settings_a.resolved_local_artifact_root().mkdir(parents=True, exist_ok=True)
    artifact_a = settings_a.resolved_local_artifact_root() / "(FAKE)-a.txt"
    artifact_a.write_text("(FAKE) profile A artifact", encoding="utf-8")
    checksum_a = hashlib.sha256(artifact_a.read_bytes()).hexdigest()

    created = registry.create("Fresh setup", idempotency_key="create-profile-b")
    profile_b_id = next(profile.profile_id for profile in created.profiles if not profile.active)
    registry.activate(
        profile_b_id,
        expected_registry_revision=created.registry_revision,
        idempotency_key="activate-profile-b",
        driver="desktop",
    )
    settings_b = active_settings(registry, base)

    with TestClient(create_app(settings_b)) as client:
        assert client.get("/v1/jobs", headers=headers(settings_b)).json()["jobs"] == []
        assert client.get("/v1/conversations", headers=headers(settings_b)).json()["conversations"]
        blank_profile = client.get(
            "/v1/career-profile", headers=headers(settings_b)
        ).json()
        assert blank_profile["items"] == []
        assert blank_profile["source_evidence"] == []
        blank_workspace = client.get("/v1/workspace", headers=headers(settings_b)).json()
        assert blank_workspace["browser_tabs"] == []
        assert blank_workspace["browse_query"] == ""
        with sqlite3.connect(settings_b.state_db_path) as connection:
            assert (
                connection.execute(
                    "SELECT stored_session_id FROM conversations "
                    "WHERE stored_session_id IS NOT NULL"
                ).fetchall()
                == []
            )
            assert connection.execute("SELECT COUNT(*) FROM career_profile_items").fetchone() == (
                0,
            )
            assert connection.execute(
                "SELECT COUNT(*) FROM career_profile_change_proposals"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM career_profile_evidence"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM editable_documents"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT trust_mode, active FROM career_profile_connected_agents "
                "WHERE agent_id = 'shared-agent'"
            ).fetchone() == ("review", 1)
            assert connection.execute(
                "SELECT mode FROM career_profile_context_grants WHERE agent_id = 'shared-agent'"
            ).fetchone() == ("none",)
        for route in (
            "/v1/jobs",
            "/v1/conversations",
            "/v1/workspace",
            "/v1/career-profile",
            "/v1/desktop/capabilities",
        ):
            stale = client.get(route, headers=headers(settings_b, profile_a_id))
            assert stale.status_code == 409, route
            assert stale.json()["code"] == "profile_context_changed"
        profile_a_conversation = client.get(
            f"/v1/conversations/{conversation_id}",
            headers={
                "Authorization": "Bearer isolation-mcp-token-value",
                "X-JobOS-MCP-Token": "isolation-mcp-token-value",
            },
        )
        assert profile_a_conversation.status_code == 404

    repository_b = SQLiteJobRepository(
        settings_b.resolved_jobs_db_path(),
        installation_profile_id=profile_b_id,
    )
    repository_b.create_job(job("fake-b-job", "https://jobs.example.test/b"))
    settings_b.resolved_local_artifact_root().mkdir(parents=True, exist_ok=True)
    artifact_b = settings_b.resolved_local_artifact_root() / "(FAKE)-b.txt"
    artifact_b.write_text("(FAKE) profile B artifact", encoding="utf-8")
    checksum_b = hashlib.sha256(artifact_b.read_bytes()).hexdigest()
    collaboration_a.disconnect(agent_id="shared-agent")
    complete_b = CareerProfileCompleteStore(
        settings_b.state_db_path,
        settings_b.resolved_evidence_vault_root(),
    )
    assert (
        CareerProfileCollaborationStore(settings_b.state_db_path, complete_b)
        .get_agent("shared-agent")
        .active
        is True
    )
    with TestClient(create_app(settings_b)) as restarted:
        jobs_b = restarted.get("/v1/jobs", headers=headers(settings_b)).json()["jobs"]
        assert [item["job_id"] for item in jobs_b] == ["fake-b-job"]

    current = registry.load()
    registry.activate(
        profile_a_id,
        expected_registry_revision=current.registry_revision,
        idempotency_key="return-profile-a",
        driver="desktop",
    )
    restored_a = active_settings(registry, base)
    with TestClient(create_app(restored_a)) as client:
        jobs_a = client.get("/v1/jobs", headers=headers(restored_a)).json()["jobs"]
        assert {item["job_id"] for item in jobs_a} == {"fake-a-job", "fake-a-job-2"}
        assert next(item for item in jobs_a if item["job_id"] == "fake-a-job")[
            "status"
        ] == "applied"
        restored_order = client.get(
            "/v1/workspace/jobs", headers=headers(restored_a)
        ).json()
        assert restored_order["manual_order"] == ["fake-a-job-2", "fake-a-job"]
        restored_profile = client.get(
            "/v1/career-profile", headers=headers(restored_a)
        ).json()
        assert [item["item_id"] for item in restored_profile["items"]] == [profile_item_id]
        assert [item["evidence_id"] for item in restored_profile["source_evidence"]] == [
            evidence_id
        ]
        restored_workspace = client.get(
            "/v1/workspace", headers=headers(restored_a)
        ).json()
        assert restored_workspace["active_browser_tab_id"] == "fake-profile-a-tab"
        assert restored_workspace["browse_query"] == "(FAKE) profile A query"
        restored_document = client.get(
            f"/v1/editable-documents/{document_id}", headers=headers(restored_a)
        )
        assert restored_document.status_code == 200
        with sqlite3.connect(restored_a.state_db_path) as connection:
            assert connection.execute(
                "SELECT stored_session_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone() == ("(FAKE)-profile-a-session",)
            assert connection.execute(
                "SELECT stored_session_id FROM conversations WHERE conversation_id = ?",
                (second_conversation_id,),
            ).fetchone() == ("(FAKE)-profile-a-session-2",)
            assert connection.execute(
                "SELECT proposal_id FROM career_profile_change_proposals"
            ).fetchone() == (proposal_id,)
    assert hashlib.sha256(artifact_a.read_bytes()).hexdigest() == checksum_a
    assert not (settings_a.resolved_local_artifact_root() / "(FAKE)-b.txt").exists()
    assert hashlib.sha256(artifact_b.read_bytes()).hexdigest() == checksum_b

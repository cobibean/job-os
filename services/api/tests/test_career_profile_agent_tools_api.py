import base64
from pathlib import Path

from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.career_profile_complete import CareerProfileCompleteStore
from jobos_api.career_profile_migration import (
    CareerProfileMigrationBundle,
    CareerProfileMigrationService,
)
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore

DEVICE_TOKEN = "agent-tools-device-token"
MCP_TOKEN = "agent-tools-mcp-token"
AGENT_TOKEN = "agent-tools-agent-token"
AGENT_ID = "job-hunter"


def configured_settings(database: Path) -> Settings:
    return Settings(
        device_id="primary-device",
        device_token=DEVICE_TOKEN,
        mcp_token=MCP_TOKEN,
        state_db_path=database,
        career_profile_enabled=True,
        career_profile_agent_id=AGENT_ID,
        career_profile_agent_display_name="Job Hunter",
        career_profile_agent_token=AGENT_TOKEN,
    )


def app_with_migration_candidate(database: Path):
    settings = configured_settings(database)
    JobOsStateStore(database).initialize(owner_device_id="primary-device")
    CareerProfileCompleteStore(
        database, settings.resolved_evidence_vault_root()
    ).initialize()
    CareerProfileMigrationService(
        database, settings.resolved_evidence_vault_root()
    ).run(
        CareerProfileMigrationBundle(
            schema_version=1,
            bundle_label="Synthetic empty agent-tools baseline",
            evidence=[],
            facts=[],
        )
    )
    return create_app(settings)


def user_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEVICE_TOKEN}"}


def agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {MCP_TOKEN}",
        "X-JobOS-MCP-Token": MCP_TOKEN,
        "X-JobOS-Agent-Id": AGENT_ID,
        "X-JobOS-Agent-Token": AGENT_TOKEN,
    }


def batch_edit(revision: int, key: str, edits: list[dict[str, object]]) -> dict[str, object]:
    return {
        "expected_profile_revision": revision,
        "idempotency_key": key,
        "edits": edits,
    }


def create_edit(kind: str, reason: str, **value: object) -> dict[str, object]:
    return {
        "operation": "item.create",
        "target_id": None,
        "reason": reason,
        "value": {"kind": kind, **value},
        "evidence_ids": [],
    }


def enable_full_agent_context(client: TestClient, *, revision: int) -> None:
    scope = client.put(
        f"/v1/career-profile/agents/{AGENT_ID}/context",
        headers=user_headers(),
        json={
            "expected_profile_revision": revision,
            "expected_authority_epoch": 0,
            "idempotency_key": "agent-tools-context-broader-0001",
            "mode": "broader",
            "selected_item_ids": [],
            "selected_areas": [],
        },
    )
    assert scope.status_code == 200, scope.text
    activated = client.post(
        "/v1/career-profile/authority/activate",
        headers=user_headers(),
        json={
            "expected_profile_revision": revision,
            "expected_authority_epoch": 0,
            "idempotency_key": "agent-tools-cutover-0001",
            "confirmation": "CUT OVER CAREER PROFILE AUTHORITY",
        },
    )
    assert activated.status_code == 200, activated.text


def test_agent_batch_edit_is_atomic_idempotent_and_keeps_evidence_optional(tmp_path: Path):
    app = app_with_migration_candidate(tmp_path / "jobos.db")

    with TestClient(app) as client:
        direct = client.patch(
            f"/v1/career-profile/agents/{AGENT_ID}",
            headers=user_headers(),
            json={"trust_mode": "direct"},
        )
        assert direct.status_code == 200, direct.text
        enable_full_agent_context(client, revision=0)

        command = batch_edit(
            0,
            "agent-tools-batch-0001",
            [
                create_edit("skill", "The user said they use Python", name="Python"),
                create_edit(
                    "experience",
                    "The user described their current role",
                    organization="Example Labs",
                    role="Product Engineer",
                    current=True,
                ),
            ],
        )
        created = client.post(
            "/v1/career-profile/agent-edits/batch",
            headers=agent_headers(),
            json=command,
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["profile_revision"] == 2
        assert [result["outcome"] for result in body["results"]] == ["applied", "applied"]
        assert all(result["target_id"].startswith("cpi_") for result in body["results"])
        current = client.get("/v1/career-profile", headers=user_headers()).json()
        assert all(item["evidence_ids"] == [] for item in current["items"])

        replay = client.post(
            "/v1/career-profile/agent-edits/batch",
            headers=agent_headers(),
            json=command,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == body

        first_target = body["results"][0]["target_id"]
        duplicate_target = client.post(
            "/v1/career-profile/agent-edits/batch",
            headers=agent_headers(),
            json=batch_edit(
                2,
                "agent-tools-duplicate-target-0001",
                [
                    {
                        "operation": "item.update",
                        "target_id": first_target,
                        "reason": "First conflicting sibling",
                        "value": {"kind": "skill", "name": "Python 1"},
                        "evidence_ids": [],
                    },
                    {
                        "operation": "item.update",
                        "target_id": first_target,
                        "reason": "Second conflicting sibling",
                        "value": {"kind": "skill", "name": "Python 2"},
                        "evidence_ids": [],
                    },
                ],
            ),
        )
        assert duplicate_target.status_code == 422

        failed = client.post(
            "/v1/career-profile/agent-edits/batch",
            headers=agent_headers(),
            json=batch_edit(
                2,
                "agent-tools-batch-rollback-0001",
                [
                    create_edit("skill", "This must roll back", name="Rust"),
                    {
                        "operation": "item.update",
                        "target_id": "cpi_missingmissing1234",
                        "reason": "Missing target forces rollback",
                        "value": {"kind": "skill", "name": "Missing"},
                        "evidence_ids": [],
                    },
                ],
            ),
        )
        assert failed.status_code == 403, failed.text
        current = client.get("/v1/career-profile", headers=user_headers()).json()
        assert current["profile_revision"] == 2
        assert {item["value"].get("name") for item in current["items"]} == {"Python", None}


def test_agent_batch_edit_creates_review_proposals_without_mutating_profile(tmp_path: Path):
    app = app_with_migration_candidate(tmp_path / "jobos.db")

    with TestClient(app) as client:
        enable_full_agent_context(client, revision=0)
        proposed = client.post(
            "/v1/career-profile/agent-edits/batch",
            headers=agent_headers(),
            json=batch_edit(
                0,
                "agent-tools-review-batch-0001",
                [
                    create_edit("skill", "The user mentioned Python", name="Python"),
                    create_edit("skill", "The user mentioned TypeScript", name="TypeScript"),
                ],
            ),
        )
        assert proposed.status_code == 200, proposed.text
        body = proposed.json()
        assert body["profile_revision"] == 0
        assert [result["outcome"] for result in body["results"]] == [
            "proposal",
            "proposal",
        ]
        assert all(result["proposal_id"].startswith("cpp_") for result in body["results"])

        changes = client.get(
            "/v1/career-profile/agent-changes",
            headers=agent_headers(),
            params={"status": "pending", "limit": 10},
        )
        assert changes.status_code == 200, changes.text
        proposals = changes.json()["proposals"]
        assert len(proposals) == 2
        assert changes.json()["applied_revisions"] == []

        for expected_revision, proposal in enumerate(proposals):
            accepted = client.post(
                f"/v1/career-profile/proposals/{proposal['proposal_id']}/decision",
                headers=user_headers(),
                json={
                    "expected_profile_revision": expected_revision,
                    "idempotency_key": f"agent-tools-accept-sibling-{expected_revision:04d}",
                    "proposal_sha256": proposal["proposal_sha256"],
                    "decision": "accept",
                },
            )
            assert accepted.status_code == 200, accepted.text
        current = client.get("/v1/career-profile", headers=user_headers()).json()
        assert current["profile_revision"] == 2
        assert {item["value"]["name"] for item in current["items"]} == {
            "Python",
            "TypeScript",
        }


def test_agent_search_changes_and_evidence_are_projection_scoped(tmp_path: Path):
    app = app_with_migration_candidate(tmp_path / "jobos.db")
    evidence_bytes = b"Synthetic evidence: Python platform work and measurable delivery."

    with TestClient(app) as client:
        direct = client.patch(
            f"/v1/career-profile/agents/{AGENT_ID}",
            headers=user_headers(),
            json={"trust_mode": "direct"},
        )
        assert direct.status_code == 200, direct.text
        enable_full_agent_context(client, revision=0)
        created = client.post(
            "/v1/career-profile/agent-edits/batch",
            headers=agent_headers(),
            json=batch_edit(
                0,
                "agent-tools-search-seed-0001",
                [
                    create_edit("skill", "User described this skill", name="Python"),
                    create_edit("skill", "User described this skill", name="TypeScript"),
                ],
            ),
        )
        assert created.status_code == 200, created.text

        imported = client.post(
            "/v1/career-profile/agent-evidence",
            headers=agent_headers(),
            json={
                "expected_profile_revision": 2,
                "idempotency_key": "agent-tools-evidence-import-0001",
                "original_filename": "synthetic-profile-notes.txt",
                "media_type": "text/plain",
                "captured_at": None,
                "provenance": {
                    "source_kind": "supporting_document",
                    "source_label": "Synthetic conversation notes",
                    "method": "agent_import",
                },
                "content_base64": base64.b64encode(evidence_bytes).decode(),
                "extractions": [],
            },
        )
        assert imported.status_code == 201, imported.text
        assert set(imported.json()) == {"profile_revision", "evidence"}
        assert imported.json()["profile_revision"] == 3
        evidence = imported.json()["evidence"]

        searched = client.get(
            "/v1/career-profile/agent-search",
            headers=agent_headers(),
            params={"query": "python", "kinds": "skill", "limit": 10},
        )
        assert searched.status_code == 200, searched.text
        assert searched.json()["profile_revision"] == 3
        assert [item["value"]["name"] for item in searched.json()["items"]] == ["Python"]
        assert searched.json()["source_evidence"] == []

        evidence_search = client.get(
            "/v1/career-profile/agent-search",
            headers=agent_headers(),
            params={"query": "conversation notes", "areas": "my_evidence", "limit": 10},
        )
        assert evidence_search.status_code == 200, evidence_search.text
        assert [source["evidence_id"] for source in evidence_search.json()["source_evidence"]] == [
            evidence["evidence_id"]
        ]

        inspected = client.get(
            f"/v1/career-profile/agent-evidence/{evidence['evidence_id']}",
            headers=agent_headers(),
            params={"byte_start": 0, "byte_length": 24},
        )
        assert inspected.status_code == 200, inspected.text
        inspect_body = inspected.json()
        assert inspect_body["evidence"] == evidence
        assert base64.b64decode(inspect_body["content_base64"]) == evidence_bytes[:24]
        assert inspect_body["text"] == evidence_bytes[:24].decode()
        assert inspect_body["next_byte_start"] == 24

        changes = client.get(
            "/v1/career-profile/agent-changes",
            headers=agent_headers(),
            params={"status": "all", "limit": 20},
        )
        assert changes.status_code == 200, changes.text
        assert changes.json()["profile_revision"] == 3
        assert len(changes.json()["applied_revisions"]) == 3
        assert changes.json()["proposals"] == []

        restricted = client.put(
            f"/v1/career-profile/agents/{AGENT_ID}/context",
            headers=user_headers(),
            json={
                "expected_profile_revision": 3,
                "expected_authority_epoch": 1,
                "idempotency_key": "agent-tools-context-career-only-0001",
                "mode": "selected",
                "selected_item_ids": [created.json()["results"][0]["target_id"]],
                "selected_areas": [],
            },
        )
        assert restricted.status_code == 200, restricted.text
        blocked = client.get(
            f"/v1/career-profile/agent-evidence/{evidence['evidence_id']}",
            headers=agent_headers(),
        )
        assert blocked.status_code == 403

        hidden_target = created.json()["results"][1]["target_id"]
        blocked_update = client.post(
            "/v1/career-profile/agent-edits/batch",
            headers=agent_headers(),
            json=batch_edit(
                3,
                "agent-tools-hidden-target-update-0001",
                [
                    {
                        "operation": "item.update",
                        "target_id": hidden_target,
                        "reason": "Attempt to mutate an item outside shared context",
                        "value": {"kind": "skill", "name": "Hidden update"},
                        "evidence_ids": [],
                    }
                ],
            ),
        )
        assert blocked_update.status_code == 403

        allowed_target = created.json()["results"][0]["target_id"]
        blocked_link = client.post(
            "/v1/career-profile/agent-edits/batch",
            headers=agent_headers(),
            json=batch_edit(
                3,
                "agent-tools-hidden-evidence-link-0001",
                [
                    {
                        "operation": "item.update",
                        "target_id": allowed_target,
                        "reason": "Attempt to link Evidence outside shared context",
                        "value": {"kind": "skill", "name": "Python"},
                        "evidence_ids": [evidence["evidence_id"]],
                    }
                ],
            ),
        )
        assert blocked_link.status_code == 403

        scoped_changes = client.get(
            "/v1/career-profile/agent-changes",
            headers=agent_headers(),
            params={"status": "all", "limit": 20},
        )
        assert scoped_changes.status_code == 200, scoped_changes.text
        scoped_payload = scoped_changes.json()
        assert scoped_payload["proposals"] == []
        assert [revision["item_id"] for revision in scoped_payload["applied_revisions"]] == [
            allowed_target
        ]
        assert "TypeScript" not in scoped_changes.text
        assert "Synthetic conversation notes" not in scoped_changes.text
        current = client.get("/v1/career-profile", headers=user_headers()).json()
        assert current["profile_revision"] == 3

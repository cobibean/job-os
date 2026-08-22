import base64
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore

DEVICE_TOKEN = "career-product-device-token"
MCP_TOKEN = "career-product-mcp-token"
AGENT_TOKEN = "career-product-agent-token"
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


def user_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEVICE_TOKEN}"}


def agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {MCP_TOKEN}",
        "X-JobOS-MCP-Token": MCP_TOKEN,
        "X-JobOS-Agent-Id": AGENT_ID,
        "X-JobOS-Agent-Token": AGENT_TOKEN,
    }


@pytest.mark.parametrize("active_status", ["queued", "running", "waiting"])
@pytest.mark.parametrize(
    ("operation", "confirmation", "completed_operation"),
    [
        ("evidence", "ERASE_EVIDENCE_PERMANENTLY", "evidence_erased"),
        ("reset", "RESET_CAREER_PROFILE_PERMANENTLY", "career_profile_reset"),
    ],
)
def test_destructive_profile_operations_wait_for_active_turns(
    tmp_path: Path,
    active_status: str,
    operation: str,
    confirmation: str,
    completed_operation: str,
):
    database = tmp_path / "jobos.db"
    app = create_app(configured_settings(database))

    with TestClient(app) as client:
        imported = client.post(
            "/v1/career-profile/evidence",
            headers=user_headers(),
            json={
                "expected_profile_revision": 0,
                "idempotency_key": f"active-{active_status}-{operation}-import-0001",
                "original_filename": "(FAKE)-active-turn-evidence.txt",
                "media_type": "text/plain",
                "captured_at": None,
                "provenance": {
                    "source_kind": "supporting_document",
                    "source_label": "(FAKE) Active turn evidence",
                    "method": "user_import",
                },
                "content_base64": base64.b64encode(b"(FAKE) preserve until work stops").decode(),
                "extractions": [],
            },
        )
        assert imported.status_code == 201, imported.text
        evidence_id = imported.json()["source_evidence"][0]["evidence_id"]

        state = JobOsStateStore(database)
        conversation = state.conversation_store(state.first_active_conversation_id())
        turn = conversation.create_turn(
            text="(FAKE) active work must finish before destructive profile changes",
            context={},
            idempotency_key=f"active-{active_status}-{operation}-turn-0001",
            actor_id="primary-device",
        )
        turn_id = str(turn["turn_id"])
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE conversation_turns SET status = ? WHERE turn_id = ?",
                (active_status, turn_id),
            )

        path = (
            f"/v1/career-profile/evidence/{evidence_id}/erase"
            if operation == "evidence"
            else "/v1/career-profile/reset"
        )
        command = {
            "expected_profile_revision": 1,
            "idempotency_key": f"active-{active_status}-{operation}-destructive-0001",
            "confirmation": confirmation,
        }
        blocked = client.post(path, headers=user_headers(), json=command)

        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"] == (
            "Finish or stop active agent work before erasing Career Profile data"
        )
        assert client.get(
            f"/v1/career-profile/evidence/{evidence_id}/content", headers=user_headers()
        ).status_code == 200
        assert [
            source["evidence_id"]
            for source in client.get("/v1/career-profile", headers=user_headers()).json()[
                "source_evidence"
            ]
        ] == [evidence_id]
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM career_profile_erasure_journal"
            ).fetchone() == (0,)
            connection.execute(
                "UPDATE conversation_turns SET status = 'completed' WHERE turn_id = ?",
                (turn_id,),
            )

        completed = client.post(path, headers=user_headers(), json=command)
        assert completed.status_code == 200, completed.text
        assert completed.json() == {"operation": completed_operation, "completed": True}
        assert client.get(
            f"/v1/career-profile/evidence/{evidence_id}/content", headers=user_headers()
        ).status_code == 404


def test_owner_controls_exact_context_preview_and_agent_cannot_read_owner_profile(
    tmp_path: Path,
):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        created = client.post(
            "/v1/career-profile/items",
            headers=user_headers(),
            json={
                "expected_profile_revision": 0,
                "idempotency_key": "api-context-skill-0001",
                "value": {"kind": "skill", "name": "(FAKE) TypeScript"},
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()
        skill_id = profile["items"][0]["item_id"]

        initial = client.get(
            f"/v1/career-profile/agents/{AGENT_ID}/context",
            headers=user_headers(),
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["mode"] == "none"

        selected = client.put(
            f"/v1/career-profile/agents/{AGENT_ID}/context",
            headers=user_headers(),
            json={
                "expected_profile_revision": 1,
                "expected_authority_epoch": 0,
                "idempotency_key": "api-select-context-0001",
                "mode": "selected",
                "selected_item_ids": [skill_id],
                "selected_areas": [],
            },
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["selected_item_ids"] == [skill_id]

        preview = client.post(
            f"/v1/career-profile/agents/{AGENT_ID}/context/preview",
            headers=user_headers(),
        )
        assert preview.status_code == 200, preview.text
        preview_body = preview.json()
        assert "snapshot_id" not in preview_body
        assert [
            item["item_id"] for item in preview_body["projection"]["items"]
        ] == [skill_id]

        agent_preview = client.post(
            f"/v1/career-profile/agents/{AGENT_ID}/context/preview",
            headers=agent_headers(),
        )
        assert agent_preview.status_code == 403

        canonical_owner_read = client.get(
            "/v1/career-profile",
            headers=user_headers(),
        )
        assert canonical_owner_read.status_code == 200, canonical_owner_read.text
        assert canonical_owner_read.json() == profile

        canonical_agent_read = client.get(
            "/v1/career-profile",
            headers=agent_headers(),
        )
        assert canonical_agent_read.status_code == 403

        agent_expansion = client.put(
            f"/v1/career-profile/agents/{AGENT_ID}/context",
            headers=agent_headers(),
            json={
                "expected_profile_revision": 1,
                "expected_authority_epoch": 0,
                "idempotency_key": "api-agent-expand-context-0001",
                "mode": "broader",
                "selected_item_ids": [],
                "selected_areas": [],
            },
        )
        assert agent_expansion.status_code == 403


def test_selected_context_requires_explicit_my_evidence_selection(tmp_path: Path):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        imported = client.post(
            "/v1/career-profile/evidence",
            headers=user_headers(),
            json={
                "expected_profile_revision": 0,
                "idempotency_key": "api-context-evidence-import-0001",
                "original_filename": "(FAKE)-context-evidence.txt",
                "media_type": "text/plain",
                "captured_at": None,
                "provenance": {
                    "source_kind": "supporting_document",
                    "source_label": "(FAKE) Context evidence",
                    "method": "user_import",
                },
                "content_base64": base64.b64encode(
                    b"(FAKE) evidence linked to a selected skill"
                ).decode(),
                "extractions": [],
            },
        )
        assert imported.status_code == 201, imported.text
        evidence_id = imported.json()["source_evidence"][0]["evidence_id"]

        created = client.post(
            "/v1/career-profile/items",
            headers=user_headers(),
            json={
                "expected_profile_revision": 1,
                "idempotency_key": "api-context-linked-skill-0001",
                "value": {"kind": "skill", "name": "(FAKE) Python"},
                "evidence_ids": [evidence_id],
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()
        item_id = profile["items"][0]["item_id"]

        selected_item = client.put(
            f"/v1/career-profile/agents/{AGENT_ID}/context",
            headers=user_headers(),
            json={
                "expected_profile_revision": 2,
                "expected_authority_epoch": 0,
                "idempotency_key": "api-context-select-linked-item-0001",
                "mode": "selected",
                "selected_item_ids": [item_id],
                "selected_areas": [],
            },
        )
        assert selected_item.status_code == 200, selected_item.text

        item_preview = client.post(
            f"/v1/career-profile/agents/{AGENT_ID}/context/preview",
            headers=user_headers(),
        )
        assert item_preview.status_code == 200, item_preview.text
        item_projection = item_preview.json()["projection"]
        assert [item["item_id"] for item in item_projection["items"]] == [item_id]
        assert item_projection["source_evidence"] == []

        selected_evidence = client.put(
            f"/v1/career-profile/agents/{AGENT_ID}/context",
            headers=user_headers(),
            json={
                "expected_profile_revision": 2,
                "expected_authority_epoch": 0,
                "idempotency_key": "api-context-select-evidence-area-0001",
                "mode": "selected",
                "selected_item_ids": [],
                "selected_areas": ["my_evidence"],
            },
        )
        assert selected_evidence.status_code == 200, selected_evidence.text

        evidence_preview = client.post(
            f"/v1/career-profile/agents/{AGENT_ID}/context/preview",
            headers=user_headers(),
        )
        assert evidence_preview.status_code == 200, evidence_preview.text
        evidence_projection = evidence_preview.json()["projection"]
        assert evidence_projection["items"] == []
        assert [
            source["evidence_id"] for source in evidence_projection["source_evidence"]
        ] == [evidence_id]


def test_explicit_export_and_restore_replace_current_state_with_one_new_baseline(tmp_path: Path):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        missing_choice = client.post(
            "/v1/career-profile/export",
            headers=user_headers(),
            json={"expected_profile_revision": 0},
        )
        assert missing_choice.status_code == 422

        first = client.post(
            "/v1/career-profile/items",
            headers=user_headers(),
            json={
                "expected_profile_revision": 0,
                "idempotency_key": "api-export-skill-0001",
                "value": {"kind": "skill", "name": "(FAKE) TypeScript"},
            },
        )
        assert first.status_code == 201, first.text
        first_item_id = first.json()["items"][0]["item_id"]

        exported = client.post(
            "/v1/career-profile/export",
            headers=user_headers(),
            json={
                "expected_profile_revision": 1,
                "evidence_mode": "profile_only",
                "selected_evidence_ids": [],
            },
        )
        assert exported.status_code == 200, exported.text
        archive = exported.json()["content_base64"]

        agent_export = client.post(
            "/v1/career-profile/export",
            headers=agent_headers(),
            json={
                "expected_profile_revision": 1,
                "evidence_mode": "profile_only",
                "selected_evidence_ids": [],
            },
        )
        assert agent_export.status_code == 403

        second = client.post(
            "/v1/career-profile/items",
            headers=user_headers(),
            json={
                "expected_profile_revision": 1,
                "idempotency_key": "api-post-export-project-0001",
                "value": {"kind": "project", "name": "(FAKE) Later project"},
            },
        )
        assert second.status_code == 201, second.text
        assert second.json()["profile_revision"] == 2

        command = {
            "expected_profile_revision": 2,
            "idempotency_key": "api-restore-baseline-0001",
            "confirmation": "RESTORE_CAREER_PROFILE_BASELINE",
            "archive_base64": archive,
        }
        agent_restore = client.post(
            "/v1/career-profile/restore",
            headers=agent_headers(),
            json=command,
        )
        assert agent_restore.status_code == 403
        restored = client.post(
            "/v1/career-profile/restore",
            headers=user_headers(),
            json=command,
        )
        assert restored.status_code == 200, restored.text
        restored_body = restored.json()
        assert restored_body["profile"]["profile_revision"] == (
            second.json()["profile_revision"] + 1
        )
        assert [
            item["item_id"] for item in restored_body["profile"]["items"]
        ] == [first_item_id]
        assert restored_body["restored_evidence_ids"] == []

        replay = client.post(
            "/v1/career-profile/restore",
            headers=user_headers(),
            json=command,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == restored_body

        history = client.get(
            "/v1/career-profile/history",
            headers=user_headers(),
        )
        assert history.status_code == 200, history.text
        revisions = history.json()["revisions"]
        assert len(revisions) == 1
        assert revisions[0]["reason"] == "Portable Career Profile restored as a new baseline"
        assert revisions[0]["undoable"] is False

        context = client.get(
            f"/v1/career-profile/agents/{AGENT_ID}/context",
            headers=user_headers(),
        )
        assert context.status_code == 200, context.text
        assert context.json()["mode"] == "none"

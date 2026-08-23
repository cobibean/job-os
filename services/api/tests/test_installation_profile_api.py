from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.installation_profiles import InstallationProfileRegistry
from jobos_api.settings import Settings


def configured_settings(tmp_path) -> Settings:
    state_path = tmp_path / "state" / "jobos.db"
    runtime = {
        "job_provider": "sqlite",
        "artifact_provider": "local",
        "state_db_path": state_path,
        "jobs_db_path": tmp_path / "jobs" / "jobs.db",
        "local_artifact_root": tmp_path / "artifacts",
        "artifact_roots": (),
        "job_hunter_db_path": None,
        "facade_source_path": None,
    }
    registry_path = tmp_path / "installation-profiles.json"
    data = InstallationProfileRegistry(registry_path).load_or_bootstrap(runtime)
    return Settings(
        device_token="profile-device-token",
        mcp_token="profile-mcp-token-value",
        state_db_path=state_path,
        jobs_db_path=tmp_path / "jobs" / "jobs.db",
        local_artifact_root=tmp_path / "artifacts",
        installation_profile_id=data.active_profile_id,
        installation_profile_name="Personal",
        installation_registry_path=registry_path,
        profile_registry_revision=data.registry_revision,
        profile_switch_driver="desktop",
    )


def user_headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": "Bearer profile-device-token",
        "X-JobOS-Profile-Id": settings.installation_profile_id,
    }


def test_profile_routes_require_direct_device_and_return_metadata_only(tmp_path):
    settings = configured_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        unauthenticated = client.get("/v1/installation-profiles")
        mcp = client.get(
            "/v1/installation-profiles",
            headers={
                "Authorization": "Bearer profile-mcp-token-value",
                "X-JobOS-MCP-Token": "profile-mcp-token-value",
            },
        )
        agent = client.get(
            "/v1/installation-profiles",
            headers={
                **user_headers(settings),
                "X-JobOS-Agent-Id": "connected-agent",
            },
        )
        accepted = client.get("/v1/installation-profiles", headers=user_headers(settings))

    assert unauthenticated.status_code == 401
    assert mcp.status_code == agent.status_code == 403
    assert accepted.status_code == 200
    encoded = accepted.text
    for forbidden in ("storage_mode", "state_db_path", "artifact", "token", str(tmp_path)):
        assert forbidden not in encoded


def test_create_rename_and_desktop_activation_are_idempotent_and_revision_fenced(tmp_path):
    settings = configured_settings(tmp_path)
    app = create_app(settings)
    headers = user_headers(settings)
    with TestClient(app) as client:
        created = client.post(
            "/v1/installation-profiles",
            headers=headers,
            json={"display_name": "Fresh setup", "idempotency_key": "create-fresh"},
        )
        replay = client.post(
            "/v1/installation-profiles",
            headers=headers,
            json={"display_name": "Fresh setup", "idempotency_key": "create-fresh"},
        )
        target = next(item for item in created.json()["profiles"] if not item["active"])
        duplicate = client.post(
            "/v1/installation-profiles",
            headers=headers,
            json={"display_name": "fresh SETUP", "idempotency_key": "create-duplicate"},
        )
        stale = client.patch(
            f"/v1/installation-profiles/{target['profile_id']}",
            headers=headers,
            json={
                "display_name": "Renamed",
                "expected_registry_revision": 1,
                "idempotency_key": "rename-stale",
            },
        )
        renamed = client.patch(
            f"/v1/installation-profiles/{target['profile_id']}",
            headers=headers,
            json={
                "display_name": "Renamed",
                "expected_registry_revision": created.json()["registry_revision"],
                "idempotency_key": "rename-current",
            },
        )
        activation = client.post(
            f"/v1/installation-profiles/{target['profile_id']}/activate",
            headers=headers,
            json={
                "expected_registry_revision": renamed.json()["registry_revision"],
                "idempotency_key": "activate-renamed",
            },
        )
        status = client.get(
            f"/v1/installation-profiles/switches/{activation.json()['switch_id']}",
            headers=headers,
        )

    assert created.status_code == 201
    assert created.headers["x-jobos-created-profile-id"] == target["profile_id"]
    assert replay.headers["x-jobos-created-profile-id"] == target["profile_id"]
    assert replay.json() == created.json()
    assert duplicate.status_code == stale.status_code == 409
    assert renamed.status_code == 200
    assert activation.status_code == 202
    assert status.json() == {
        "switch_id": activation.json()["switch_id"],
        "target_profile_id": target["profile_id"],
        "status": "succeeded",
        "active_profile_id": target["profile_id"],
        "error_code": None,
    }


def test_already_active_is_noop_and_missing_target_is_stable(tmp_path):
    settings = configured_settings(tmp_path)
    app = create_app(settings)
    headers = user_headers(settings)
    with TestClient(app) as client:
        active = client.post(
            f"/v1/installation-profiles/{settings.installation_profile_id}/activate",
            headers=headers,
            json={"expected_registry_revision": 1, "idempotency_key": "active-noop"},
        )
        current = client.get(
            "/v1/installation-profiles", headers=headers
        ).json()["registry_revision"]
        missing = client.post(
            "/v1/installation-profiles/jprof_ffffffffffffffffffffffffffffffff/activate",
            headers=headers,
            json={"expected_registry_revision": current, "idempotency_key": "missing"},
        )

    assert active.status_code == 202
    assert active.json()["from_profile_id"] == active.json()["to_profile_id"]
    assert missing.status_code == 404
    assert missing.json()["code"] == "installation_profile_not_found"


def test_active_turn_blocks_activation_before_registry_changes(tmp_path):
    settings = configured_settings(tmp_path)
    registry = InstallationProfileRegistry(settings.installation_registry_path)
    created = registry.create("Fresh setup", idempotency_key="direct-create")
    target = next(item for item in created.profiles if not item.active)
    app = create_app(settings)
    with TestClient(app) as client:
        with sqlite3.connect(settings.state_db_path) as connection:
            conversation_id = connection.execute(
                "SELECT conversation_id FROM conversations ORDER BY position LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO conversation_turns(
                    turn_id, conversation_id, message_id, text, context_json, status
                ) VALUES (
                    'turn_profile_block', ?, 'message-profile-block',
                    '(FAKE)', '{}', 'running'
                )
                """,
                (conversation_id,),
            )
        before = registry.load()
        blocked = client.post(
            f"/v1/installation-profiles/{target.profile_id}/activate",
            headers=user_headers(settings),
            json={
                "expected_registry_revision": before.registry_revision,
                "idempotency_key": "blocked-active-turn",
            },
        )
        after = registry.load()

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "profile_switch_blocked"
    assert after.active_profile_id == before.active_profile_id
    assert after.pending_switch is None


@pytest.mark.parametrize("maintenance", ["erasure", "restore"])
def test_career_profile_maintenance_blocks_activation_before_registry_changes(
    tmp_path, maintenance
):
    settings = configured_settings(tmp_path)
    registry = InstallationProfileRegistry(settings.installation_registry_path)
    created = registry.create("Fresh setup", idempotency_key="maintenance-create")
    target = next(item for item in created.profiles if not item.active)
    with TestClient(create_app(settings)) as client:
        with sqlite3.connect(settings.state_db_path) as connection:
            if maintenance == "erasure":
                connection.execute(
                    """
                    INSERT INTO career_profile_erasure_journal(
                        operation_id, operation, actor_principal, idempotency_key,
                        request_hash, storage_names_json
                    ) VALUES ('erase-profile-switch', 'profile.reset', 'device:primary-device',
                              'erase-profile-switch', 'synthetic', '[]')
                    """
                )
            else:
                connection.execute(
                    """
                    INSERT INTO career_profile_restore_journal(
                        operation_id, actor_principal, idempotency_key,
                        request_hash, phase, had_live_vault
                    ) VALUES ('restore-profile-switch', 'device:primary-device',
                              'restore-profile-switch', ?, 'swap_pending', 0)
                    """,
                    ("0" * 64,),
                )
        before = registry.load()
        blocked = client.post(
            f"/v1/installation-profiles/{target.profile_id}/activate",
            headers=user_headers(settings),
            json={
                "expected_registry_revision": before.registry_revision,
                "idempotency_key": f"blocked-{maintenance}",
            },
        )
        after = registry.load()

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "profile_switch_blocked"
    assert after == before

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import DeviceCredential, Settings

PRIMARY_TOKEN = "primary-device-token-for-tests"
SECONDARY_TOKEN = "secondary-device-token-tests"
MCP_TOKEN = "trusted-mcp-token-for-tests"


def settings(database: Path, *, career_profile_enabled: bool = True) -> Settings:
    return Settings(
        device_id="primary-device",
        device_token=PRIMARY_TOKEN,
        device_credentials=(DeviceCredential(device_id="secondary-device", token=SECONDARY_TOKEN),),
        mcp_token=MCP_TOKEN,
        state_db_path=database,
        career_profile_enabled=career_profile_enabled,
    )


def auth(token: str = PRIMARY_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def work_arrangement_payload(
    revision: int,
    key: str,
    *,
    mode: str = "remote",
) -> dict[str, object]:
    return {
        "expected_profile_revision": revision,
        "idempotency_key": key,
        "value": {
            "mode": mode,
            "strength": "strong_preference",
            "note": "(FAKE) Explain how this affects matching",
        },
    }


def test_career_profile_is_dormant_until_explicitly_enabled(tmp_path: Path) -> None:
    database = tmp_path / "jobos.db"
    app = create_app(settings(database, career_profile_enabled=False))

    with TestClient(app) as client:
        response = client.get("/v1/career-profile/work-arrangement", headers=auth())

    assert response.status_code == 404
    assert response.json()["message"] == "Career Profile is not enabled"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM career_profiles").fetchone() == (0,)


def test_work_arrangement_namespace_is_required_in_generated_contract(tmp_path: Path) -> None:
    schema = create_app(settings(tmp_path / "jobos.db")).openapi()

    required = schema["components"]["schemas"]["WorkArrangementRecord"]["required"]
    assert "namespace" in required


def test_career_profile_routes_require_authentication_and_bounded_snapshot_scope(
    tmp_path: Path,
) -> None:
    app = create_app(settings(tmp_path / "jobos.db"))
    protected_calls = (
        ("get", "/v1/career-profile/work-arrangement", None),
        (
            "put",
            "/v1/career-profile/work-arrangement",
            work_arrangement_payload(0, "blocked-write"),
        ),
        ("get", "/v1/career-profile/work-arrangement/history", None),
        (
            "post",
            "/v1/career-profile/work-arrangement/restore",
            {
                "expected_profile_revision": 1,
                "idempotency_key": "blocked-restore",
                "target_profile_revision": 1,
            },
        ),
        ("post", "/v1/career-profile/snapshots", {}),
        ("get", "/v1/career-profile/snapshots/cps_unknown_snapshot_123", None),
    )

    with TestClient(app) as client:
        for method, path, payload in protected_calls:
            response = client.request(method, path, json=payload)
            assert response.status_code == 401, (method, path, response.text)
            assert response.json()["code"] == "http_401"

            forbidden = client.request(
                method,
                path,
                headers=auth(SECONDARY_TOKEN),
                json=payload,
            )
            assert forbidden.status_code == 403, (method, path, forbidden.text)
            assert forbidden.json()["code"] == "http_403"

        invalid_scope = client.post(
            "/v1/career-profile/snapshots",
            headers=auth(),
            json={"scopes": ["identity"]},
        )

    assert invalid_scope.status_code == 422
    assert invalid_scope.json()["code"] == "request_validation_failed"


def test_authenticated_api_edits_replays_rejects_stale_and_restores(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "jobos.db"))
    with TestClient(app) as client:
        empty = client.get("/v1/career-profile/work-arrangement", headers=auth())
        created = client.put(
            "/v1/career-profile/work-arrangement",
            headers=auth(),
            json=work_arrangement_payload(0, "api-create-work-arrangement"),
        )
        replay = client.put(
            "/v1/career-profile/work-arrangement",
            headers=auth(),
            json=work_arrangement_payload(0, "api-create-work-arrangement"),
        )
        stale = client.put(
            "/v1/career-profile/work-arrangement",
            headers=auth(),
            json=work_arrangement_payload(0, "api-stale-write", mode="onsite"),
        )
        edited = client.put(
            "/v1/career-profile/work-arrangement",
            headers=auth(),
            json=work_arrangement_payload(1, "api-edit-work-arrangement", mode="hybrid"),
        )
        restored = client.post(
            "/v1/career-profile/work-arrangement/restore",
            headers=auth(),
            json={
                "expected_profile_revision": 2,
                "idempotency_key": "api-restore-work-arrangement",
                "target_profile_revision": 1,
            },
        )
        history = client.get(
            "/v1/career-profile/work-arrangement/history",
            headers=auth(),
        )

    assert empty.json() == {"profile_revision": 0, "record": None}
    assert created.status_code == 200
    assert replay.json() == created.json()
    assert stale.status_code == 409
    assert stale.json()["code"] == "http_409"
    assert "current revision is 1" in stale.json()["message"]
    assert edited.json()["record"]["value"]["mode"] == "hybrid"
    assert restored.json()["profile_revision"] == 3
    assert restored.json()["record"]["value"]["mode"] == "remote"
    assert [item["profile_revision"] for item in history.json()["revisions"]] == [3, 2, 1]
    assert history.json()["revisions"][0]["operation"] == "restore"
    assert history.json()["revisions"][0]["restored_from_profile_revision"] == 1


def test_snapshot_is_immutable_and_exact_principal_authorized(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "jobos.db"))
    with TestClient(app) as client:
        client.put(
            "/v1/career-profile/work-arrangement",
            headers=auth(),
            json=work_arrangement_payload(0, "snapshot-api-source", mode="remote"),
        )
        snapshot = client.post(
            "/v1/career-profile/snapshots",
            headers=auth(),
            json={},
        )
        snapshot_id = snapshot.json()["snapshot_id"]
        client.put(
            "/v1/career-profile/work-arrangement",
            headers=auth(),
            json=work_arrangement_payload(1, "snapshot-api-later", mode="onsite"),
        )
        resolved = client.get(
            f"/v1/career-profile/snapshots/{snapshot_id}",
            headers=auth(),
        )
        forbidden = client.get(
            f"/v1/career-profile/snapshots/{snapshot_id}",
            headers=auth(SECONDARY_TOKEN),
        )
        missing = client.get(
            "/v1/career-profile/snapshots/cps_unknown_snapshot_123",
            headers=auth(),
        )

    assert snapshot.status_code == 201
    assert resolved.json() == snapshot.json()
    assert resolved.json()["projection"]["work_arrangement"]["value"]["mode"] == "remote"
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "http_403"
    assert missing.status_code == 404


def test_restart_preserves_current_history_and_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "jobos.db"
    first_app = create_app(settings(database))
    with TestClient(first_app) as client:
        saved = client.put(
            "/v1/career-profile/work-arrangement",
            headers=auth(),
            json=work_arrangement_payload(0, "restart-persist-write"),
        )
        snapshot = client.post(
            "/v1/career-profile/snapshots",
            headers=auth(),
            json={},
        )

    restarted_app = create_app(settings(database))
    with TestClient(restarted_app) as client:
        current = client.get("/v1/career-profile/work-arrangement", headers=auth())
        history = client.get(
            "/v1/career-profile/work-arrangement/history",
            headers=auth(),
        )
        resolved = client.get(
            f"/v1/career-profile/snapshots/{snapshot.json()['snapshot_id']}",
            headers=auth(),
        )

    assert current.json() == saved.json()
    assert len(history.json()["revisions"]) == 1
    assert resolved.json() == snapshot.json()

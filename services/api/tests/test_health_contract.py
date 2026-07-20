from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings


def test_health_reports_ready_phase_six_api_with_agent_connectivity_separate(tmp_path):
    app = create_app(
        Settings(
            device_token="test-device-token",
            state_db_path=tmp_path / "jobos.db",
        )
    )

    with TestClient(app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "jobos-api",
        "version": "0.1.0",
        "state_schema": 8,
        "agent_connection": "offline",
    }


def test_device_session_requires_the_runtime_credential(tmp_path):
    app = create_app(
        Settings(
            device_token="test-device-token",
            state_db_path=tmp_path / "jobos.db",
        )
    )

    with TestClient(app) as client:
        rejected = client.get("/v1/device-session")
        accepted = client.get(
            "/v1/device-session",
            headers={"Authorization": "Bearer test-device-token"},
        )

    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "Device authentication required"
    assert accepted.status_code == 200
    assert accepted.json() == {
        "authenticated": True,
        "transport": "private-tailscale",
        "api_version": "0.1.0",
    }


def test_version_and_openapi_describe_the_shared_workspace_contract(tmp_path):
    app = create_app(
        Settings(
            device_token="test-device-token",
            state_db_path=tmp_path / "jobos.db",
        )
    )

    with TestClient(app) as client:
        version = client.get("/v1/version")
        openapi = client.get("/openapi.json")

    assert version.status_code == 200
    assert version.json() == {
        "api_version": "0.1.0",
        "contract": "jobos-v1-phase7-parity",
    }
    assert set(openapi.json()["paths"]) == {
        "/v1/health",
        "/v1/version",
        "/v1/device-session",
        "/v1/jobs",
        "/v1/jobs/order",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/status",
        "/v1/jobs/{job_id}/history",
        "/v1/jobs/{job_id}/artifacts",
        "/v1/jobs/{job_id}/artifacts/refresh",
        "/v1/jobs/{job_id}/artifacts/register",
        "/v1/jobs/{job_id}/artifacts/{artifact_id}/approve",
        "/v1/artifacts/{artifact_id}/content",
        "/v1/artifacts/{artifact_id}/download",
        "/v1/workspace/jobs",
        "/v1/workspace",
        "/v1/workspace/jobs/selection",
        "/v1/workspace/jobs/sort",
        "/v1/events",
        "/v1/events/stream",
        "/v1/conversations/current",
        "/v1/conversations/current/messages",
        "/v1/conversations/current/turns/{turn_id}/cancel",
        "/v1/conversations/current/turns/{turn_id}/retry",
        "/v1/conversations/current/events/stream",
        "/v1/desktop/capabilities",
        "/v1/browser/commands",
        "/v1/jobs/{job_id}/artifacts/render",
        "/v1/activity",
    }
    schemas = openapi.json()["components"]["schemas"]
    assert set(schemas["HealthResponse"]["required"]) == {
        "status",
        "service",
        "version",
        "state_schema",
        "agent_connection",
    }
    assert set(schemas["VersionResponse"]["required"]) == {"api_version", "contract"}
    assert set(schemas["DeviceSessionResponse"]["required"]) == {
        "authenticated",
        "transport",
        "api_version",
    }
    assert set(schemas["JobListItem"]["required"]) == {
        "job_id",
        "company",
        "title",
        "status",
        "status_group",
        "canonical_url",
        "discovered_at",
        "last_seen_at",
    }
    assert set(schemas["WorkspaceSnapshotCommand"]["required"]) == {
        "revision",
        "origin",
        "idempotency_key",
        "selected_preset",
        "layouts",
        "selected_job_id",
        "active_center_surface",
    }
    assert "idempotency_key" not in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert "origin" not in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert set(schemas["BrowserTabMetadata"]["required"]) == {"tab_id", "url"}
    assert "browser_tabs" in schemas["WorkspaceSnapshotCommand"]["properties"]
    assert "active_browser_tab_id" in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert "active_artifact_id" in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert "active_artifact_page" in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert "active_artifact_zoom" in schemas["WorkspaceSnapshotResponse"]["properties"]

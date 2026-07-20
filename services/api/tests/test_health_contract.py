from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings


def test_health_reports_a_ready_phase_one_api(tmp_path):
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
        "state_schema": 2,
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


def test_version_and_openapi_describe_the_connected_shell_contract(tmp_path):
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
        "contract": "jobos-v1-phase1",
    }
    assert set(openapi.json()["paths"]) == {
        "/v1/health",
        "/v1/version",
        "/v1/device-session",
    }
    schemas = openapi.json()["components"]["schemas"]
    assert set(schemas["HealthResponse"]["required"]) == {
        "status",
        "service",
        "version",
        "state_schema",
    }
    assert set(schemas["VersionResponse"]["required"]) == {"api_version", "contract"}
    assert set(schemas["DeviceSessionResponse"]["required"]) == {
        "authenticated",
        "transport",
        "api_version",
    }

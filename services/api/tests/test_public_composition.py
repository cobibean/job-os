from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings


def test_public_composition_starts_without_private_services(tmp_path):
    """The application core must compose without JobHunter, Hermes, or Tailscale."""
    settings = Settings(
        device_token="local-device-token",
        mcp_token="local-mcp-test-token",
        state_db_path=tmp_path / "jobos.db",
        job_hunter_db_path=None,
        artifact_roots=(),
        hermes_dashboard_url=None,
        hermes_dashboard_token=None,
        hermes_job_hunter_cwd=None,
    )

    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["agent_connection"] == "offline"

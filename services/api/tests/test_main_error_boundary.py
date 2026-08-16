import importlib
import sys

from fastapi.testclient import TestClient


def test_untrusted_local_artifact_root_returns_setup_required(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    artifact_root = tmp_path / "artifacts"
    artifact_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("JOBOS_DEVICE_TOKEN", "local-device-token")
    monkeypatch.setenv("JOBOS_MCP_TOKEN", "local-mcp-test-token")
    monkeypatch.setenv("JOBOS_STATE_DB_PATH", str(tmp_path / "state/jobos.db"))
    monkeypatch.setenv("JOBOS_JOBS_DB_PATH", str(tmp_path / "jobs/jobs.db"))
    monkeypatch.setenv("JOBOS_LOCAL_ARTIFACT_ROOT", str(artifact_root))
    sys.modules.pop("jobos_api.main", None)

    main = importlib.import_module("jobos_api.main")
    with TestClient(main.app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 503
    assert response.json()["status"] == "setup_required"
    assert "artifact" in response.json()["detail"].casefold()
    sys.modules.pop("jobos_api.main", None)

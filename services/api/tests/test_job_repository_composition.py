from __future__ import annotations

import importlib
import sys

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.artifact_gateway import UnavailableArtifactGateway
from jobos_api.composition import create_job_services
from jobos_api.job_repository import Conflict, NotFound, Unavailable, Validation
from jobos_api.settings import Settings
from jobos_api.sqlite_job_repository import SQLiteJobRepository


def settings(tmp_path, **overrides):
    return Settings(
        device_token="composition-device-token",
        mcp_token="composition-trusted-token",
        state_db_path=tmp_path / "state" / "jobos.db",
        **overrides,
    )


def test_public_default_uses_separate_local_sqlite_without_job_hunter(tmp_path):
    sys.modules.pop("job_hunter", None)
    configured = settings(tmp_path)
    repository, _ = create_job_services(configured)

    assert isinstance(repository, SQLiteJobRepository)
    assert repository.database_path == tmp_path / "state" / "jobs.db"
    assert repository.database_path != configured.state_db_path
    assert "job_hunter" not in sys.modules


def test_explicit_jobs_path_and_public_runtime_create_mutable_jobs(tmp_path):
    configured = settings(tmp_path, jobs_db_path=tmp_path / "canonical" / "jobs.sqlite3")
    app = create_app(configured)
    headers = {"Authorization": "Bearer composition-device-token"}
    payload = {
        "company_name": "Synthetic Co",
        "title": "Local Role",
        "canonical_url": "https://example.com/jobs/local",
        "location_text": "Remote",
        "description_text": "A synthetic complete listing.",
        "application_url": "https://example.com/jobs/local/apply",
        "idempotency_key": "public-local-create",
    }

    with TestClient(app) as client:
        created = client.post("/v1/jobs", headers=headers, json=payload)
        listed = client.get("/v1/jobs", headers=headers)

    assert created.status_code == 200
    assert created.json()["created"] is True
    assert listed.json()["jobs"][0]["job_id"] == created.json()["job"]["job_id"]
    assert configured.jobs_db_path.is_file()


def test_environment_exposes_explicit_provider_and_jobs_database_path(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBOS_DEVICE_TOKEN", "environment-device-token")
    monkeypatch.setenv("JOBOS_MCP_TOKEN", "environment-trusted-token")
    monkeypatch.setenv("JOBOS_STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("JOBOS_JOB_PROVIDER", "sqlite")
    monkeypatch.setenv("JOBOS_JOBS_DB_PATH", str(tmp_path / "configured-jobs.db"))
    sys.modules.pop("jobos_api.main", None)
    main = importlib.import_module("jobos_api.main")

    configured = main.settings_from_environment()

    assert configured.job_provider == "sqlite"
    assert configured.jobs_db_path == tmp_path / "configured-jobs.db"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (NotFound("missing"), 404),
        (Conflict("conflict"), 409),
        (Validation("invalid"), 422),
        (Unavailable("offline"), 503),
    ],
)
def test_stable_repository_errors_map_to_http_contract(tmp_path, error, status_code):
    class FailingRepository:
        def initialize(self):
            return None

        def get_job(self, job_id):
            raise error

    app = create_app(
        settings(tmp_path),
        job_repository=FailingRepository(),  # type: ignore[arg-type]
        artifact_gateway=UnavailableArtifactGateway(),
    )
    with TestClient(app) as client:
        response = client.get(
            "/v1/jobs/failing", headers={"Authorization": "Bearer composition-device-token"}
        )

    assert response.status_code == status_code


def test_private_installed_environment_explicitly_selects_job_hunter(tmp_path):
    from jobos_api.macos_runtime import RuntimeServiceConfig, build_service_environment

    config = RuntimeServiceConfig.from_mapping(
        {
            "schema_version": 1,
            "label": "com.cobibean.jobos.api",
            "jobos_root": str(tmp_path / "job-os"),
            "python_path": str(tmp_path / "python"),
            "facade_source_path": str(tmp_path / "job-hunter/src"),
            "state_db_path": str(tmp_path / "state.db"),
            "job_hunter_db_path": str(tmp_path / "private-jobs.db"),
            "artifact_roots": [str(tmp_path / "artifacts")],
            "hermes_dashboard_url": "ws://127.0.0.1:9000",
            "hermes_job_hunter_cwd": str(tmp_path / "job-hunter"),
            "device_id": "primary-device",
            "remote_device_ids": [],
            "host": "127.0.0.1",
            "port": 8765,
        }
    )
    environment = build_service_environment(
        config,
        device_token="device-token-long",
        mcp_token="mcp-token-long-value",
        hermes_dashboard_token="dashboard-token-long",
        base_environment={},
    )

    assert environment["JOBOS_JOB_PROVIDER"] == "job-hunter"
    assert environment["JOBOS_JOB_HUNTER_DB_PATH"] == str(tmp_path / "private-jobs.db")

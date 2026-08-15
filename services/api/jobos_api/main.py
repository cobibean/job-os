import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from jobos_api.app import create_app
from jobos_api.local_config import (
    LocalConfigError,
    config_path,
    default_data_dir,
    settings_from_config,
)
from jobos_api.settings import Settings, parse_device_credentials


def settings_from_environment() -> Settings:
    token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
    mcp_token = os.environ.get("JOBOS_MCP_TOKEN", "")
    if not token and not mcp_token:
        configured_path = Path(
            os.environ.get("JOBOS_CONFIG_PATH", config_path(default_data_dir()))
        )
        return settings_from_config(configured_path)
    if not token or not mcp_token:
        raise LocalConfigError(
            "JOBOS_DEVICE_TOKEN and JOBOS_MCP_TOKEN must be configured together."
        )
    state_db_path = Path(os.environ.get("JOBOS_STATE_DB_PATH", "data/jobos.db"))
    jobs_db = os.environ.get("JOBOS_JOBS_DB_PATH")
    job_hunter_db = os.environ.get("JOBOS_JOB_HUNTER_DB_PATH")
    artifact_roots = tuple(
        Path(value)
        for value in os.environ.get("JOBOS_ARTIFACT_ROOTS", "").split(os.pathsep)
        if value
    )
    hermes_url = os.environ.get("JOBOS_HERMES_DASHBOARD_URL")
    hermes_token = os.environ.get("JOBOS_HERMES_DASHBOARD_TOKEN")
    hermes_cwd = os.environ.get("JOBOS_HERMES_JOB_HUNTER_CWD")
    return Settings(
        device_token=token,
        mcp_token=mcp_token,
        device_id=os.environ.get("JOBOS_DEVICE_ID", "primary-device"),
        device_credentials=parse_device_credentials(
            os.environ.get("JOBOS_DEVICE_CREDENTIALS_JSON")
        ),
        state_db_path=state_db_path,
        job_provider=os.environ.get("JOBOS_JOB_PROVIDER", "sqlite"),
        jobs_db_path=Path(jobs_db) if jobs_db else None,
        job_hunter_db_path=Path(job_hunter_db) if job_hunter_db else None,
        artifact_roots=artifact_roots,
        hermes_dashboard_url=hermes_url,
        hermes_dashboard_token=hermes_token,
        hermes_job_hunter_cwd=Path(hermes_cwd) if hermes_cwd else None,
        hermes_request_timeout=float(os.environ.get("JOBOS_HERMES_REQUEST_TIMEOUT", "5")),
    )


def create_application() -> FastAPI:
    return create_app(settings_from_environment())


def _configuration_error_app(error: Exception) -> FastAPI:
    unavailable = FastAPI(title="JobOS API", version="0.1.0")
    message = str(error)

    @unavailable.get("/v1/health")
    def health() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"status": "setup_required", "detail": message},
        )

    return unavailable


try:
    app = create_application()
except (LocalConfigError, OSError, ValueError) as error:
    app = _configuration_error_app(error)

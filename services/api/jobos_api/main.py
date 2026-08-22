import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from jobos_api.app import create_app
from jobos_api.artifact_repository import ArtifactStorageError
from jobos_api.local_config import (
    LocalConfigError,
    config_path,
    default_data_dir,
    settings_from_config,
)
from jobos_api.responses import ApiErrorResponse
from jobos_api.settings import Settings, parse_device_credentials


def settings_from_environment() -> Settings:
    token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
    mcp_token = os.environ.get("JOBOS_MCP_TOKEN", "")
    if not token and not mcp_token:
        configured_path = Path(os.environ.get("JOBOS_CONFIG_PATH", config_path(default_data_dir())))
        configured = settings_from_config(configured_path)
        updates: dict[str, object] = {
            "career_profile_enabled": os.environ.get("JOBOS_CAREER_PROFILE_ENABLED") == "1"
        }
        for environment_name, field_name in (
            ("JOBOS_CAREER_PROFILE_AGENT_ID", "career_profile_agent_id"),
            ("JOBOS_CAREER_PROFILE_AGENT_DISPLAY_NAME", "career_profile_agent_display_name"),
            ("JOBOS_CAREER_PROFILE_AGENT_TOKEN", "career_profile_agent_token"),
        ):
            if value := os.environ.get(environment_name):
                updates[field_name] = value
        return configured.model_copy(update=updates)
    if not token or not mcp_token:
        raise LocalConfigError(
            "JOBOS_DEVICE_TOKEN and JOBOS_MCP_TOKEN must be configured together."
        )
    application_data = default_data_dir()
    state_db_path = Path(os.environ.get("JOBOS_STATE_DB_PATH", application_data / "state/jobos.db"))
    jobs_db = os.environ.get("JOBOS_JOBS_DB_PATH")
    job_hunter_db = os.environ.get("JOBOS_JOB_HUNTER_DB_PATH")
    artifact_roots = tuple(
        Path(value)
        for value in os.environ.get("JOBOS_ARTIFACT_ROOTS", "").split(os.pathsep)
        if value
    )
    local_artifact_root = os.environ.get("JOBOS_LOCAL_ARTIFACT_ROOT")
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
        jobs_db_path=Path(jobs_db) if jobs_db else application_data / "jobs/jobs.db",
        job_hunter_db_path=Path(job_hunter_db) if job_hunter_db else None,
        artifact_provider=os.environ.get("JOBOS_ARTIFACT_PROVIDER", "local"),
        transport=os.environ.get("JOBOS_TRANSPORT", "local-loopback"),
        local_artifact_root=(
            Path(local_artifact_root) if local_artifact_root else application_data / "artifacts"
        ),
        artifact_roots=artifact_roots,
        hermes_dashboard_url=hermes_url,
        hermes_dashboard_token=hermes_token,
        hermes_job_hunter_cwd=Path(hermes_cwd) if hermes_cwd else None,
        hermes_request_timeout=float(os.environ.get("JOBOS_HERMES_REQUEST_TIMEOUT", "5")),
        career_profile_enabled=os.environ.get("JOBOS_CAREER_PROFILE_ENABLED") == "1",
        career_profile_agent_id=os.environ.get(
            "JOBOS_CAREER_PROFILE_AGENT_ID", "trusted-local-mcp"
        ),
        career_profile_agent_display_name=os.environ.get(
            "JOBOS_CAREER_PROFILE_AGENT_DISPLAY_NAME", "JobOS Agent"
        ),
        career_profile_agent_token=os.environ.get("JOBOS_CAREER_PROFILE_AGENT_TOKEN"),
    )


def create_application() -> FastAPI:
    return create_app(settings_from_environment())


def _configuration_error_app(_: Exception) -> FastAPI:
    unavailable = FastAPI(title="JobOS API", version="0.1.0")
    message = "JobOS setup is unavailable; verify the local configuration and artifact storage"

    @unavailable.get(
        "/v1/health",
        status_code=503,
        response_model=ApiErrorResponse,
        responses={503: {"model": ApiErrorResponse}},
    )
    def health() -> JSONResponse:
        correlation_id = uuid4().hex
        payload = ApiErrorResponse(
            error_schema="jobos-error-v1",
            code="setup_required",
            message=message,
            retryable=True,
            correlation_id=correlation_id,
            detail=message,
        )
        return JSONResponse(
            status_code=503,
            content=payload.model_dump(),
            headers={"X-Correlation-ID": correlation_id},
        )

    return unavailable


try:
    app = create_application()
except (ArtifactStorageError, LocalConfigError, OSError, ValueError) as error:
    app = _configuration_error_app(error)

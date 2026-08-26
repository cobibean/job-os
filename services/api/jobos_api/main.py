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
from jobos_api.settings import (
    Settings,
    parse_command_args,
    parse_device_credentials,
    parse_device_ids,
)


def settings_from_environment() -> Settings:
    token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
    mcp_token = os.environ.get("JOBOS_MCP_TOKEN", "")
    if not token and not mcp_token:
        configured_path = Path(os.environ.get("JOBOS_CONFIG_PATH", config_path(default_data_dir())))
        configured = settings_from_config(configured_path)
        updates: dict[str, object] = {
            "career_profile_enabled": os.environ.get("JOBOS_CAREER_PROFILE_ENABLED") == "1",
            "career_profile_owner_device_ids": parse_device_ids(
                os.environ.get("JOBOS_CAREER_PROFILE_OWNER_DEVICE_IDS_JSON")
            ),
        }
        if codex_binary := os.environ.get("JOBOS_CODEX_APP_SERVER_PATH"):
            updates["codex_app_server_path"] = Path(codex_binary)
        if codex_home := os.environ.get("JOBOS_CODEX_HOME"):
            updates["codex_home_path"] = Path(codex_home)
        if codex_timeout := os.environ.get("JOBOS_CODEX_REQUEST_TIMEOUT"):
            updates["codex_request_timeout"] = float(codex_timeout)
        if codex_mcp_command := os.environ.get("JOBOS_CODEX_MCP_COMMAND"):
            updates["codex_mcp_command"] = Path(codex_mcp_command)
        if codex_mcp_args := os.environ.get("JOBOS_CODEX_MCP_ARGS_JSON"):
            updates["codex_mcp_args"] = parse_command_args(codex_mcp_args)
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
        hermes_default_model_id=os.environ.get("JOBOS_HERMES_DEFAULT_MODEL_ID"),
        hermes_default_reasoning_effort=os.environ.get(
            "JOBOS_HERMES_DEFAULT_REASONING_EFFORT"
        ),
        hermes_request_timeout=float(os.environ.get("JOBOS_HERMES_REQUEST_TIMEOUT", "5")),
        codex_app_server_path=(
            Path(value) if (value := os.environ.get("JOBOS_CODEX_APP_SERVER_PATH")) else None
        ),
        codex_home_path=(Path(value) if (value := os.environ.get("JOBOS_CODEX_HOME")) else None),
        codex_publication_root=Path(
            os.environ.get(
                "JOBOS_CODEX_PUBLICATION_ROOT",
                application_data / "artifacts/publication-inbox",
            )
        ),
        codex_request_timeout=float(os.environ.get("JOBOS_CODEX_REQUEST_TIMEOUT", "15")),
        codex_mcp_command=(
            Path(value) if (value := os.environ.get("JOBOS_CODEX_MCP_COMMAND")) else None
        ),
        codex_mcp_args=parse_command_args(os.environ.get("JOBOS_CODEX_MCP_ARGS_JSON")),
        career_profile_enabled=os.environ.get("JOBOS_CAREER_PROFILE_ENABLED") == "1",
        career_profile_owner_device_ids=parse_device_ids(
            os.environ.get("JOBOS_CAREER_PROFILE_OWNER_DEVICE_IDS_JSON")
        ),
        career_profile_agent_id=os.environ.get(
            "JOBOS_CAREER_PROFILE_AGENT_ID", "trusted-local-mcp"
        ),
        career_profile_agent_display_name=os.environ.get(
            "JOBOS_CAREER_PROFILE_AGENT_DISPLAY_NAME", "JobOS Agent"
        ),
        career_profile_agent_token=os.environ.get("JOBOS_CAREER_PROFILE_AGENT_TOKEN"),
        installation_profile_id=os.environ.get("JOBOS_INSTALLATION_PROFILE_ID"),
        installation_profile_name=os.environ.get("JOBOS_INSTALLATION_PROFILE_NAME", "Personal"),
        installation_registry_path=Path(
            os.environ.get(
                "JOBOS_INSTALLATION_REGISTRY_PATH",
                application_data / "installation-profiles.json",
            )
        ),
        profile_registry_revision=int(os.environ.get("JOBOS_PROFILE_REGISTRY_REVISION", "1")),
        profile_switch_driver=os.environ.get("JOBOS_PROFILE_SWITCH_DRIVER", "desktop"),
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

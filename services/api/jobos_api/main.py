import os
from pathlib import Path

from jobos_api.app import create_app
from jobos_api.settings import Settings, parse_device_credentials


def settings_from_environment() -> Settings:
    token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
    if not token:
        raise RuntimeError("JOBOS_DEVICE_TOKEN is required")
    mcp_token = os.environ.get("JOBOS_MCP_TOKEN", "")
    if not mcp_token:
        raise RuntimeError("JOBOS_MCP_TOKEN is required")
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


app = create_app(settings_from_environment())

import os
from pathlib import Path

from jobos_api.app import create_app
from jobos_api.settings import Settings


def settings_from_environment() -> Settings:
    token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
    if not token:
        raise RuntimeError("JOBOS_DEVICE_TOKEN is required")
    state_db_path = Path(os.environ.get("JOBOS_STATE_DB_PATH", "data/jobos.db"))
    job_hunter_db = os.environ.get("JOBOS_JOB_HUNTER_DB_PATH")
    return Settings(
        device_token=token,
        state_db_path=state_db_path,
        job_hunter_db_path=Path(job_hunter_db) if job_hunter_db else None,
    )


app = create_app(settings_from_environment())

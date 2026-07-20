from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """Runtime inputs for the API process.

    Secrets enter through the process environment at the composition root. The
    Settings Interface remains explicit so tests never depend on global state.
    """

    model_config = ConfigDict(frozen=True)

    device_token: str = Field(min_length=16)
    device_id: str = Field(default="primary-device", min_length=1, max_length=100)
    state_db_path: Path
    job_hunter_db_path: Path | None = None
    artifact_roots: tuple[Path, ...] = ()
    hermes_dashboard_url: str | None = None
    hermes_dashboard_token: str | None = Field(default=None, min_length=16, repr=False)
    hermes_job_hunter_cwd: Path | None = None
    hermes_request_timeout: float = Field(default=5.0, gt=0, le=30)

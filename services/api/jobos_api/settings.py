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

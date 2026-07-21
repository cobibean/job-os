import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeviceCredential(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    device_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    token: str = Field(min_length=16, max_length=4096, repr=False)


def parse_device_credentials(raw: str | None) -> tuple[DeviceCredential, ...]:
    if not raw:
        return ()
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or any(
            not isinstance(device_id, str) or not isinstance(token, str)
            for device_id, token in value.items()
        ):
            raise ValueError
        return tuple(
            DeviceCredential(device_id=device_id, token=token)
            for device_id, token in value.items()
        )
    except (json.JSONDecodeError, ValueError):
        raise ValueError("device credential registry is invalid") from None


class Settings(BaseModel):
    """Runtime inputs for the API process.

    Secrets enter through the process environment at the composition root. The
    Settings Interface remains explicit so tests never depend on global state.
    """

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    device_token: str = Field(min_length=16, max_length=4096, repr=False)
    device_id: str = Field(default="primary-device", min_length=1, max_length=100)
    device_credentials: tuple[DeviceCredential, ...] = Field(default=(), repr=False)
    state_db_path: Path
    job_hunter_db_path: Path | None = None
    artifact_roots: tuple[Path, ...] = ()
    hermes_dashboard_url: str | None = None
    hermes_dashboard_token: str | None = Field(default=None, min_length=16, repr=False)
    hermes_job_hunter_cwd: Path | None = None
    hermes_request_timeout: float = Field(default=5.0, gt=0, le=30)

    @model_validator(mode="after")
    def validate_unique_device_credentials(self) -> "Settings":
        device_ids = [self.device_id, *(item.device_id for item in self.device_credentials)]
        tokens = [self.device_token, *(item.token for item in self.device_credentials)]
        if len(set(device_ids)) != len(device_ids):
            raise ValueError("device identifiers must be unique")
        if len(set(tokens)) != len(tokens):
            raise ValueError("device credentials must be unique")
        return self

    def device_credential_registry(self) -> dict[str, str]:
        return {
            self.device_id: self.device_token,
            **{item.device_id: item.token for item in self.device_credentials},
        }

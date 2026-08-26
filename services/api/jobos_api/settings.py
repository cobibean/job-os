import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MCP_RUNTIME_DEVICE_ID = "jobos-mcp-runtime"


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
            DeviceCredential(device_id=device_id, token=token) for device_id, token in value.items()
        )
    except (json.JSONDecodeError, ValueError):
        raise ValueError("device credential registry is invalid") from None


def parse_device_ids(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError
        return tuple(value)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("device identifier list is invalid") from None


def parse_command_args(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError
        return tuple(value)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("command arguments are invalid") from None


class Settings(BaseModel):
    """Runtime inputs for the API process.

    Secrets enter through the process environment at the composition root. The
    Settings Interface remains explicit so tests never depend on global state.
    """

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    device_token: str = Field(min_length=16, max_length=4096, repr=False)
    mcp_token: str = Field(min_length=16, max_length=4096, repr=False)
    device_id: str = Field(default="primary-device", min_length=1, max_length=100)
    device_credentials: tuple[DeviceCredential, ...] = Field(default=(), repr=False)
    state_db_path: Path
    job_provider: Literal["sqlite", "job-hunter"] = "sqlite"
    jobs_db_path: Path | None = None
    job_hunter_db_path: Path | None = None
    artifact_provider: Literal["local", "gateway"] = "local"
    local_artifact_root: Path | None = None
    artifact_roots: tuple[Path, ...] = ()
    transport: Literal["local-loopback", "private-remote"] = "local-loopback"
    hermes_dashboard_url: str | None = None
    hermes_dashboard_token: str | None = Field(default=None, min_length=16, repr=False)
    hermes_job_hunter_cwd: Path | None = None
    hermes_default_model_id: str | None = Field(default=None, min_length=1, max_length=256)
    hermes_default_reasoning_effort: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    hermes_request_timeout: float = Field(default=5.0, gt=0, le=30)
    codex_app_server_path: Path | None = None
    codex_home_path: Path | None = None
    codex_publication_root: Path | None = None
    codex_request_timeout: float = Field(default=15.0, gt=0, le=60)
    codex_mcp_command: Path | None = None
    codex_mcp_args: tuple[str, ...] = ()
    career_profile_enabled: bool = False
    career_profile_owner_device_ids: tuple[str, ...] = ()
    career_profile_agent_id: str = Field(
        default="trusted-local-mcp",
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    career_profile_agent_display_name: str = Field(
        default="JobOS Agent", min_length=1, max_length=120
    )
    career_profile_agent_token: str | None = Field(
        default=None,
        min_length=16,
        max_length=4096,
        repr=False,
    )
    installation_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    installation_profile_name: str = Field(min_length=1, max_length=64)
    installation_registry_path: Path
    profile_registry_revision: int = Field(default=1, ge=1)
    profile_switch_driver: Literal["launchd", "desktop"] = "desktop"

    @model_validator(mode="before")
    @classmethod
    def populate_legacy_profile_context(cls, value: object) -> object:
        """Keep direct test/development construction compatible.

        Real source and launchd startup always replace these derived compatibility
        values with the installation registry's random identity.
        """
        if not isinstance(value, dict):
            return value
        values = dict(value)
        state_value = values.get("state_db_path")
        if state_value is not None:
            state_path = Path(state_value)
            # Direct construction predates installation profiles and commonly creates
            # multiple state database fixtures beside one shared default jobs.db.
            # Treat that directory as the compatibility installation boundary. Real
            # source and launchd composition always provide the random registry ID.
            installation_root = state_path.parent.absolute()
            digest = hashlib.sha256(str(installation_root).encode()).hexdigest()[:32]
            if not values.get("installation_profile_id"):
                values["installation_profile_id"] = f"jprof_{digest}"
            if not values.get("installation_profile_name"):
                values["installation_profile_name"] = "Personal"
            if not values.get("installation_registry_path"):
                values["installation_registry_path"] = (
                    state_path.parent / "installation-profiles.json"
                )
        return values

    @model_validator(mode="after")
    def validate_unique_device_credentials(self) -> "Settings":
        if (self.hermes_default_model_id is None) != (
            self.hermes_default_reasoning_effort is None
        ):
            raise ValueError("Hermes defaults require model and reasoning effort")
        device_ids = [
            self.device_id,
            MCP_RUNTIME_DEVICE_ID,
            *(item.device_id for item in self.device_credentials),
        ]
        tokens = [
            self.device_token,
            self.mcp_token,
            *(item.token for item in self.device_credentials),
            *([self.career_profile_agent_token] if self.career_profile_agent_token else []),
        ]
        if len(set(device_ids)) != len(device_ids):
            raise ValueError("device identifiers must be unique")
        if len(set(tokens)) != len(tokens):
            raise ValueError("device credentials must be unique")
        remote_ids = {item.device_id for item in self.device_credentials}
        if (
            len(set(self.career_profile_owner_device_ids))
            != len(self.career_profile_owner_device_ids)
            or any(
                device_id not in remote_ids
                for device_id in self.career_profile_owner_device_ids
            )
        ):
            raise ValueError("Career Profile owner devices must be authorized remote devices")
        return self

    def device_credential_registry(self) -> dict[str, str]:
        return {
            self.device_id: self.device_token,
            MCP_RUNTIME_DEVICE_ID: self.mcp_token,
            **{item.device_id: item.token for item in self.device_credentials},
        }

    def resolved_career_profile_agent_token(self) -> str:
        """Resolve an optional agent credential with local-runtime compatibility."""
        return self.career_profile_agent_token or self.mcp_token

    def resolved_jobs_db_path(self) -> Path:
        return self.jobs_db_path or self.state_db_path.parent / "jobs.db"

    def resolved_local_artifact_root(self) -> Path:
        return self.local_artifact_root or self.state_db_path.parent / "artifacts"

    def resolved_evidence_vault_root(self) -> Path:
        return self.state_db_path.parent / "career-profile-evidence"

    def resolved_codex_home(self) -> Path:
        return self.codex_home_path or self.state_db_path.parent.parent / "codex-home"

    def resolved_codex_publication_root(self) -> Path:
        return (
            self.codex_publication_root
            or self.resolved_local_artifact_root() / "publication-inbox"
        )

    def resolved_artifact_roots(self) -> tuple[Path, ...]:
        roots = (self.resolved_local_artifact_root(), *self.artifact_roots)
        return tuple(dict.fromkeys(roots))

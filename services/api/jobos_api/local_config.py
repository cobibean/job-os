from __future__ import annotations

import json
import os
import stat
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from jobos_api.installation_profiles import (
    InstallationProfileRegistry,
    effective_profile_runtime,
)
from jobos_api.macos_keychain import (
    delete_keychain_secret,
    keychain_helper_path,
    read_keychain_secret,
    store_keychain_secret,
)
from jobos_api.settings import Settings

CONFIG_SCHEMA_VERSION = 1
DEVICE_KEYCHAIN_SERVICE = "com.cobibean.jobos.device-token"
MCP_KEYCHAIN_SERVICE = "com.cobibean.jobos.mcp-token"


class LocalConfigError(RuntimeError):
    """Actionable public configuration failure."""


def default_data_dir(environment: dict[str, str] | None = None) -> Path:
    values = environment or os.environ
    if values.get("JOBOS_DATA_DIR"):
        return Path(values["JOBOS_DATA_DIR"]).expanduser()
    if sys.platform == "darwin":
        return Path(values.get("HOME", str(Path.home()))) / "Library/Application Support/JobOS"
    root = values.get("XDG_DATA_HOME")
    return Path(root).expanduser() / "JobOS" if root else Path.home() / ".local/share/JobOS"


def config_path(data_dir: Path) -> Path:
    return data_dir / "config.json"


def read_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LocalConfigError(
            f"JobOS is not initialized. Run `jobos-init --data-dir {path.parent}`."
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise LocalConfigError(
            "JobOS config.json is unreadable or invalid. Retry setup or restore the file."
        ) from error
    if not isinstance(value, dict) or value.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        raise LocalConfigError("JobOS config.json uses an unsupported schema version.")
    return value


def _resolved(data_dir: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise LocalConfigError(f"JobOS config field {field} is invalid.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else data_dir / path


def _file_credentials(path: Path) -> tuple[str, str]:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise LocalConfigError("JobOS credential fallback must be a regular file.")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o600:
            raise LocalConfigError(
                "JobOS credential fallback must have permissions 0600. Run setup again."
            )
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise LocalConfigError("JobOS credentials are invalid. Retry setup.")
        return (
            _credential_value(value.get("deviceToken")),
            _credential_value(value.get("mcpToken")),
        )
    except LocalConfigError:
        raise
    except (FileNotFoundError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise LocalConfigError("JobOS credentials are unavailable. Retry setup.") from error


def _credential_value(value: object) -> str:
    if not isinstance(value, str):
        raise LocalConfigError("JobOS credentials are invalid. Retry setup.")
    credential = value.strip()
    invalid_control_character = any(character in credential for character in "\r\n\0")
    if not credential or len(credential) > 4096 or invalid_control_character:
        raise LocalConfigError("JobOS credentials are invalid. Retry setup.")
    return credential


def load_credentials(config: dict[str, Any], data_dir: Path) -> tuple[str, str]:
    store = config.get("credentialStore")
    if not isinstance(store, dict):
        raise LocalConfigError("JobOS credential configuration is invalid.")
    provider = store.get("provider")
    device_id = config.get("deviceId")
    if not isinstance(device_id, str) or not device_id:
        raise LocalConfigError("JobOS device configuration is invalid.")
    if provider == "keychain":
        try:
            device = read_keychain_secret(DEVICE_KEYCHAIN_SERVICE, device_id)
            mcp = read_keychain_secret(MCP_KEYCHAIN_SERVICE, device_id)
        except RuntimeError as error:
            raise LocalConfigError(
                "JobOS Keychain credentials are unavailable. Retry setup."
            ) from error
        if not device or not mcp:
            raise LocalConfigError("JobOS Keychain credentials are incomplete. Retry setup.")
        return device, mcp
    if provider == "file":
        return _file_credentials(_resolved(data_dir, store.get("path"), "credentialStore.path"))
    raise LocalConfigError("JobOS credential provider is unsupported.")


def settings_from_config(path: Path) -> Settings:
    config = read_config(path)
    data_dir = path.parent
    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise LocalConfigError("JobOS path configuration is invalid.")
    device_token, mcp_token = load_credentials(config, data_dir)
    artifact_provider = config.get("artifactProvider", "local")
    if artifact_provider not in {"local", "gateway"}:
        raise LocalConfigError("JobOS artifact provider is unsupported.")
    artifact_root = _resolved(data_dir, paths.get("artifacts"), "paths.artifacts")
    base_runtime: dict[str, object] = {
        "job_provider": str(config.get("jobProvider", "sqlite")),
        "artifact_provider": artifact_provider,
        "state_db_path": _resolved(data_dir, paths.get("stateDatabase"), "paths.stateDatabase"),
        "jobs_db_path": _resolved(data_dir, paths.get("jobsDatabase"), "paths.jobsDatabase"),
        "local_artifact_root": artifact_root,
        "artifact_roots": (artifact_root,),
        "job_hunter_db_path": None,
        "facade_source_path": None,
    }
    registry_path = data_dir / "installation-profiles.json"
    registry = InstallationProfileRegistry(registry_path)
    registry_data = registry.load_or_bootstrap(base_runtime)
    active = next(
        profile
        for profile in registry_data.profiles
        if profile.profile_id == registry_data.active_profile_id
    )
    effective = effective_profile_runtime(base_runtime, active, data_dir)
    return Settings(
        device_token=device_token,
        mcp_token=mcp_token,
        device_id=str(config["deviceId"]),
        state_db_path=effective["state_db_path"],
        jobs_db_path=effective["jobs_db_path"],
        artifact_provider=effective["artifact_provider"],
        local_artifact_root=effective["local_artifact_root"],
        artifact_roots=effective["artifact_roots"],
        job_provider=effective["job_provider"],
        job_hunter_db_path=effective["job_hunter_db_path"],
        transport=("private-remote" if config.get("mode") == "remote-client" else "local-loopback"),
        installation_profile_id=active.profile_id,
        installation_profile_name=active.display_name,
        installation_registry_path=registry_path,
        profile_registry_revision=registry_data.registry_revision,
        profile_switch_driver="desktop",
    )


def store_credentials(
    *,
    data_dir: Path,
    device_id: str,
    device_token: str,
    mcp_token: str,
    credentials_dir: Path | None = None,
) -> dict[str, str]:
    helper = keychain_helper_path()
    if sys.platform == "darwin" and helper.is_file() and os.access(helper, os.X_OK):
        try:
            store_keychain_secret(DEVICE_KEYCHAIN_SERVICE, device_id, device_token)
            store_keychain_secret(MCP_KEYCHAIN_SERVICE, device_id, mcp_token)
            return {"provider": "keychain"}
        except RuntimeError as error:
            with suppress(RuntimeError):
                delete_keychain_secret(DEVICE_KEYCHAIN_SERVICE, device_id)
            with suppress(RuntimeError):
                delete_keychain_secret(MCP_KEYCHAIN_SERVICE, device_id)
            raise LocalConfigError(
                "JobOS Keychain setup failed. Retry setup or explicitly use the "
                "local-file fallback."
            ) from error
    credentials_dir = credentials_dir or data_dir / "credentials"
    credentials_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(credentials_dir, 0o700)
    target = credentials_dir / "local.json"
    temporary = credentials_dir / f".local.{uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"deviceToken": device_token, "mcpToken": mcp_token}, output)
            output.write("\n")
        os.chmod(temporary, 0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    os.chmod(target, 0o600)
    try:
        configured_path = str(target.relative_to(data_dir))
    except ValueError:
        configured_path = str(target)
    return {"provider": "file", "path": configured_path}

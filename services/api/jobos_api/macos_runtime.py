from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from jobos_api.codex_runtime import CODEX_APP_SERVER_SHA256
from jobos_api.installation_profiles import (
    InstallationProfileRecord,
    InstallationProfileRegistry,
    InstallationProfileRegistryData,
    InstallationProfileRegistryError,
    effective_profile_runtime,
    ensure_managed_profile_storage,
)
from jobos_api.macos_keychain import (
    delete_keychain_secret,
    read_keychain_secret,
    store_keychain_secret,
)

SERVICE_LABEL = "com.cobibean.jobos.api"
DEVICE_TOKEN_SERVICE = "com.cobibean.jobos.device-token"
MCP_TOKEN_SERVICE = "com.cobibean.jobos.mcp-token"
HERMES_TOKEN_SERVICE = "com.cobibean.jobos.hermes-dashboard-token"
_CONFIG_FIELDS = {
    "schema_version",
    "label",
    "jobos_root",
    "python_path",
    "job_provider",
    "artifact_provider",
    "facade_source_path",
    "state_db_path",
    "jobs_db_path",
    "local_artifact_root",
    "job_hunter_db_path",
    "artifact_roots",
    "hermes_dashboard_url",
    "hermes_job_hunter_cwd",
    "hermes_default_model_id",
    "hermes_default_reasoning_effort",
    "keychain_helper_path",
    "keychain_helper_sha256",
    "codex_app_server_path",
    "codex_home_path",
    "device_id",
    "remote_device_ids",
    "career_profile_owner_device_ids",
    "host",
    "port",
}
_REQUIRED_CONFIG_FIELDS = {
    "schema_version",
    "label",
    "jobos_root",
    "python_path",
    "job_provider",
    "artifact_provider",
    "state_db_path",
    "jobs_db_path",
    "local_artifact_root",
    "device_id",
    "remote_device_ids",
    "host",
    "port",
}
_DEVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def _absolute_path(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(f"{field} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    return path


def _optional_absolute_path(value: object, field: str) -> Path | None:
    return None if value is None else _absolute_path(value, field)


def _loopback_dashboard_url(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Hermes dashboard URL must use loopback")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "ws"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Hermes dashboard URL must use loopback")
    return value.rstrip("/")


@dataclass(frozen=True)
class RuntimeServiceConfig:
    schema_version: int
    label: str
    jobos_root: Path
    python_path: Path
    job_provider: Literal["sqlite", "job-hunter"]
    artifact_provider: Literal["local", "gateway"]
    facade_source_path: Path | None
    state_db_path: Path
    jobs_db_path: Path
    local_artifact_root: Path
    job_hunter_db_path: Path | None
    artifact_roots: tuple[Path, ...]
    hermes_dashboard_url: str | None
    hermes_job_hunter_cwd: Path | None
    hermes_default_model_id: str | None
    hermes_default_reasoning_effort: str | None
    keychain_helper_path: Path | None
    keychain_helper_sha256: str | None
    codex_app_server_path: Path | None
    codex_home_path: Path | None
    device_id: str
    remote_device_ids: tuple[str, ...]
    career_profile_owner_device_ids: tuple[str, ...]
    host: str
    port: int

    @classmethod
    def from_mapping(cls, value: object) -> RuntimeServiceConfig:
        if not isinstance(value, dict):
            raise ValueError("runtime config must be an object")
        value = dict(value)
        legacy_private_profile = (
            "job_provider" not in value
            and "artifact_provider" not in value
            and value.get("facade_source_path") is not None
            and value.get("job_hunter_db_path") is not None
        )
        if legacy_private_profile:
            state_path = _absolute_path(value.get("state_db_path"), "state_db_path")
            roots = value.get("artifact_roots")
            if not isinstance(roots, list) or not roots:
                raise ValueError("legacy private runtime requires artifact roots")
            value.update(
                {
                    "job_provider": "job-hunter",
                    "artifact_provider": "gateway",
                    "jobs_db_path": str(state_path.parent / "jobs.db"),
                    "local_artifact_root": roots[0],
                }
            )
        unknown = set(value) - _CONFIG_FIELDS
        if unknown:
            raise ValueError("runtime config contains unknown fields")
        missing = _REQUIRED_CONFIG_FIELDS - set(value)
        if missing:
            raise ValueError("runtime config is missing required fields")
        if value["schema_version"] != 1:
            raise ValueError("runtime config schema is unsupported")
        if value["label"] != SERVICE_LABEL:
            raise ValueError("runtime service label is invalid")
        if value["host"] != "127.0.0.1":
            raise ValueError("runtime service must bind to loopback")
        port = value["port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            raise ValueError("runtime service port is invalid")
        device_id = value["device_id"]
        if not isinstance(device_id, str) or not _DEVICE_PATTERN.fullmatch(device_id):
            raise ValueError("runtime device identifier is invalid")
        remote_device_ids = value["remote_device_ids"]
        if (
            not isinstance(remote_device_ids, list)
            or any(
                not isinstance(remote_id, str) or not _DEVICE_PATTERN.fullmatch(remote_id)
                for remote_id in remote_device_ids
            )
            or device_id in remote_device_ids
            or len(set(remote_device_ids)) != len(remote_device_ids)
        ):
            raise ValueError("remote device identifiers are invalid")
        career_profile_owner_device_ids = value.get("career_profile_owner_device_ids", [])
        if (
            not isinstance(career_profile_owner_device_ids, list)
            or any(
                not isinstance(owner_id, str) or owner_id not in remote_device_ids
                for owner_id in career_profile_owner_device_ids
            )
            or len(set(career_profile_owner_device_ids))
            != len(career_profile_owner_device_ids)
        ):
            raise ValueError("Career Profile owner device identifiers are invalid")
        job_provider = value["job_provider"]
        artifact_provider = value["artifact_provider"]
        if job_provider not in {"sqlite", "job-hunter"}:
            raise ValueError("runtime job provider is invalid")
        if artifact_provider not in {"local", "gateway"}:
            raise ValueError("runtime artifact provider is invalid")
        roots = value.get("artifact_roots", [])
        if not isinstance(roots, list):
            raise ValueError("artifact roots must be a list")
        facade_source = _optional_absolute_path(
            value.get("facade_source_path"), "facade_source_path"
        )
        job_hunter_db = _optional_absolute_path(
            value.get("job_hunter_db_path"), "job_hunter_db_path"
        )
        hermes_cwd = value.get("hermes_job_hunter_cwd")
        hermes_default_model_id = value.get("hermes_default_model_id")
        hermes_default_reasoning_effort = value.get("hermes_default_reasoning_effort")
        if (hermes_default_model_id is None) != (hermes_default_reasoning_effort is None):
            raise ValueError("Hermes defaults require model and reasoning effort")
        if hermes_default_model_id is not None and (
            not isinstance(hermes_default_model_id, str)
            or not 1 <= len(hermes_default_model_id) <= 256
            or any(char in hermes_default_model_id for char in "\r\n\0")
        ):
            raise ValueError("Hermes default model is invalid")
        if hermes_default_reasoning_effort is not None and (
            not isinstance(hermes_default_reasoning_effort, str)
            or not 1 <= len(hermes_default_reasoning_effort) <= 64
            or any(char in hermes_default_reasoning_effort for char in "\r\n\0")
        ):
            raise ValueError("Hermes default reasoning effort is invalid")
        if (job_provider == "job-hunter" or artifact_provider == "gateway") and (
            facade_source is None or job_hunter_db is None
        ):
            raise ValueError("private providers require facade and JobHunter paths")
        if artifact_provider == "gateway" and not roots:
            raise ValueError("the gateway provider requires artifact roots")
        keychain_helper = _optional_absolute_path(
            value.get("keychain_helper_path"), "keychain_helper_path"
        )
        keychain_helper_sha256 = value.get("keychain_helper_sha256")
        if keychain_helper_sha256 is not None and (
            not isinstance(keychain_helper_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", keychain_helper_sha256)
        ):
            raise ValueError("installed Keychain helper hash is invalid")
        codex_app_server = _optional_absolute_path(
            value.get("codex_app_server_path"), "codex_app_server_path"
        )
        codex_home = _optional_absolute_path(value.get("codex_home_path"), "codex_home_path")
        codex_fields = (
            keychain_helper,
            keychain_helper_sha256,
            codex_app_server,
            codex_home,
        )
        if any(item is not None for item in codex_fields) and any(
            item is None for item in codex_fields
        ):
            raise ValueError("installed Codex runtime fields must be configured together")
        return cls(
            schema_version=1,
            label=SERVICE_LABEL,
            jobos_root=_absolute_path(value["jobos_root"], "jobos_root"),
            python_path=_absolute_path(value["python_path"], "python_path"),
            job_provider=job_provider,
            artifact_provider=artifact_provider,
            facade_source_path=facade_source,
            state_db_path=_absolute_path(value["state_db_path"], "state_db_path"),
            jobs_db_path=_absolute_path(value["jobs_db_path"], "jobs_db_path"),
            local_artifact_root=_absolute_path(value["local_artifact_root"], "local_artifact_root"),
            job_hunter_db_path=job_hunter_db,
            artifact_roots=tuple(_absolute_path(root, "artifact_roots") for root in roots),
            hermes_dashboard_url=_loopback_dashboard_url(value.get("hermes_dashboard_url")),
            hermes_job_hunter_cwd=(
                _absolute_path(hermes_cwd, "hermes_job_hunter_cwd")
                if hermes_cwd is not None
                else None
            ),
            hermes_default_model_id=hermes_default_model_id,
            hermes_default_reasoning_effort=hermes_default_reasoning_effort,
            keychain_helper_path=keychain_helper,
            keychain_helper_sha256=keychain_helper_sha256,
            codex_app_server_path=codex_app_server,
            codex_home_path=codex_home,
            device_id=device_id,
            remote_device_ids=tuple(remote_device_ids),
            career_profile_owner_device_ids=tuple(career_profile_owner_device_ids),
            host="127.0.0.1",
            port=port,
        )

    @classmethod
    def load(cls, path: Path) -> RuntimeServiceConfig:
        try:
            return cls.from_mapping(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("runtime config could not be loaded") from error

    def to_mapping(self) -> dict[str, Any]:
        value = asdict(self)
        for field in (
            "jobos_root",
            "python_path",
            "facade_source_path",
            "state_db_path",
            "jobs_db_path",
            "local_artifact_root",
            "job_hunter_db_path",
            "hermes_job_hunter_cwd",
            "keychain_helper_path",
            "codex_app_server_path",
            "codex_home_path",
        ):
            if value[field] is not None:
                value[field] = str(value[field])
        value["artifact_roots"] = [str(path) for path in self.artifact_roots]
        value["remote_device_ids"] = list(self.remote_device_ids)
        value["career_profile_owner_device_ids"] = list(
            self.career_profile_owner_device_ids
        )
        return value


def build_service_environment(
    config: RuntimeServiceConfig,
    *,
    device_token: str,
    mcp_token: str,
    remote_device_tokens: dict[str, str] | None = None,
    hermes_dashboard_token: str | None,
    base_environment: dict[str, str] | None = None,
    installation_profile_id: str | None = None,
    installation_profile_name: str = "Personal",
    installation_registry_path: Path | None = None,
    profile_registry_revision: int = 1,
    profile_switch_driver: Literal["launchd", "desktop"] = "launchd",
    service_config_path: Path | None = None,
) -> dict[str, str]:
    if not 16 <= len(device_token) <= 4096 or any(char in device_token for char in "\r\n\0"):
        raise ValueError("device credential is invalid")
    if not 16 <= len(mcp_token) <= 4096 or any(char in mcp_token for char in "\r\n\0"):
        raise ValueError("MCP credential is invalid")
    if hermes_dashboard_token is not None and (
        not 16 <= len(hermes_dashboard_token) <= 4096
        or any(char in hermes_dashboard_token for char in "\r\n\0")
    ):
        raise ValueError("Hermes credential is invalid")
    for remote_id, remote_token in (remote_device_tokens or {}).items():
        if (
            not _DEVICE_PATTERN.fullmatch(remote_id)
            or not 16 <= len(remote_token) <= 4096
            or any(char in remote_token for char in "\r\n\0")
        ):
            raise ValueError("remote device credential is invalid")
    source = base_environment if base_environment is not None else os.environ
    default_path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if base_environment is None:
        path = os.pathsep.join(
            dict.fromkeys(
                [
                    *default_path.split(os.pathsep),
                    *source.get("PATH", "").split(os.pathsep),
                ]
            )
        )
    else:
        path = source.get("PATH", default_path)
    environment = {
        "PATH": path,
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": os.pathsep.join(
            [
                str(config.jobos_root / "services/api"),
                *([str(config.facade_source_path)] if config.facade_source_path else []),
            ]
        ),
        "JOBOS_DEVICE_TOKEN": device_token,
        "JOBOS_MCP_TOKEN": mcp_token,
        "JOBOS_DEVICE_ID": config.device_id,
        "JOBOS_STATE_DB_PATH": str(config.state_db_path),
        "JOBOS_JOB_PROVIDER": config.job_provider,
        "JOBOS_ARTIFACT_PROVIDER": config.artifact_provider,
        "JOBOS_JOBS_DB_PATH": str(config.jobs_db_path),
        "JOBOS_LOCAL_ARTIFACT_ROOT": str(config.local_artifact_root),
    }
    if config.job_hunter_db_path:
        environment["JOBOS_JOB_HUNTER_DB_PATH"] = str(config.job_hunter_db_path)
    if config.artifact_roots:
        environment["JOBOS_ARTIFACT_ROOTS"] = os.pathsep.join(map(str, config.artifact_roots))
    if config.hermes_dashboard_url:
        environment["JOBOS_HERMES_DASHBOARD_URL"] = config.hermes_dashboard_url
    if hermes_dashboard_token:
        environment["JOBOS_HERMES_DASHBOARD_TOKEN"] = hermes_dashboard_token
    if config.hermes_job_hunter_cwd:
        environment["JOBOS_HERMES_JOB_HUNTER_CWD"] = str(config.hermes_job_hunter_cwd)
    if config.hermes_default_model_id and config.hermes_default_reasoning_effort:
        environment["JOBOS_HERMES_DEFAULT_MODEL_ID"] = config.hermes_default_model_id
        environment["JOBOS_HERMES_DEFAULT_REASONING_EFFORT"] = (
            config.hermes_default_reasoning_effort
        )
    if config.codex_app_server_path:
        if service_config_path is None:
            raise ValueError("installed Codex runtime requires the service config path")
        environment.update(
            {
                "JOBOS_KEYCHAIN_HELPER_PATH": str(config.keychain_helper_path),
                "JOBOS_CODEX_APP_SERVER_PATH": str(config.codex_app_server_path),
                "JOBOS_CODEX_HOME": str(config.codex_home_path),
                "JOBOS_CODEX_MCP_COMMAND": str(config.python_path),
                "JOBOS_CODEX_MCP_ARGS_JSON": json.dumps(
                    [
                        str(config.jobos_root / "scripts/macos/jobos_mcp_runtime.py"),
                        str(service_config_path),
                    ],
                    separators=(",", ":"),
                ),
            }
        )
    if remote_device_tokens:
        environment["JOBOS_DEVICE_CREDENTIALS_JSON"] = json.dumps(
            remote_device_tokens,
            separators=(",", ":"),
            sort_keys=True,
        )
    if source.get("JOBOS_CAREER_PROFILE_ENABLED") == "1":
        environment["JOBOS_CAREER_PROFILE_ENABLED"] = "1"
        if config.career_profile_owner_device_ids:
            environment["JOBOS_CAREER_PROFILE_OWNER_DEVICE_IDS_JSON"] = json.dumps(
                config.career_profile_owner_device_ids,
                separators=(",", ":"),
            )
        environment["JOBOS_CAREER_PROFILE_AGENT_ID"] = source.get(
            "JOBOS_CAREER_PROFILE_AGENT_ID", "trusted-local-mcp"
        )
        environment["JOBOS_CAREER_PROFILE_AGENT_DISPLAY_NAME"] = source.get(
            "JOBOS_CAREER_PROFILE_AGENT_DISPLAY_NAME", "JobOS Agent"
        )
        if agent_token := source.get("JOBOS_CAREER_PROFILE_AGENT_TOKEN"):
            environment["JOBOS_CAREER_PROFILE_AGENT_TOKEN"] = agent_token
    if installation_profile_id is not None:
        environment.update(
            {
                "JOBOS_INSTALLATION_PROFILE_ID": installation_profile_id,
                "JOBOS_INSTALLATION_PROFILE_NAME": installation_profile_name,
                "JOBOS_INSTALLATION_REGISTRY_PATH": str(installation_registry_path),
                "JOBOS_PROFILE_REGISTRY_REVISION": str(profile_registry_revision),
                "JOBOS_PROFILE_SWITCH_DRIVER": profile_switch_driver,
            }
        )
    return environment


def installation_registry_path_for_runtime(config_path: Path) -> Path:
    if config_path.name == "runtime.json" and config_path.parent.name == "service":
        return config_path.parent.parent / "installation-profiles.json"
    return config_path.parent / "installation-profiles.json"


def resolve_runtime_profile(
    config: RuntimeServiceConfig,
    registry_path: Path,
) -> tuple[RuntimeServiceConfig, InstallationProfileRegistryData, InstallationProfileRecord]:
    registry = InstallationProfileRegistry(registry_path)
    data = registry.load_or_bootstrap(config)
    profile = next(item for item in data.profiles if item.profile_id == data.active_profile_id)
    if profile.storage_mode == "managed":
        ensure_managed_profile_storage(registry_path.parent, profile.profile_id)
    effective = effective_profile_runtime(config, profile, registry_path.parent)
    resolved = replace(
        config,
        job_provider=effective["job_provider"],
        artifact_provider=effective["artifact_provider"],
        facade_source_path=effective["facade_source_path"],
        state_db_path=Path(effective["state_db_path"]),
        jobs_db_path=Path(effective["jobs_db_path"]),
        local_artifact_root=Path(effective["local_artifact_root"]),
        job_hunter_db_path=(
            Path(effective["job_hunter_db_path"])
            if effective["job_hunter_db_path"] is not None
            else None
        ),
        artifact_roots=tuple(Path(item) for item in effective["artifact_roots"]),
    )
    return resolved, data, profile


def build_uvicorn_arguments(config: RuntimeServiceConfig) -> list[str]:
    return [
        str(config.python_path),
        "-m",
        "uvicorn",
        "jobos_api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(config.port),
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_codex_paths(
    jobos_app: Path,
    data_dir: Path,
    *,
    expected_keychain_helper_sha256: str,
) -> dict[str, str]:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_keychain_helper_sha256):
        raise ValueError("expected Keychain helper SHA-256 is invalid")
    if not jobos_app.is_absolute() or jobos_app.is_symlink() or not jobos_app.is_dir():
        raise ValueError("JobOS app must be an absolute installed application")
    app_root = jobos_app.resolve(strict=True)

    def installed_resource(relative_path: Path) -> Path:
        candidate = jobos_app
        for part in relative_path.parts:
            candidate /= part
            if candidate.is_symlink():
                raise ValueError("installed JobOS Codex runtime contains a symlink")
        try:
            candidate.resolve(strict=True).relative_to(app_root)
        except (FileNotFoundError, ValueError) as error:
            raise ValueError("installed JobOS Codex runtime is incomplete") from error
        return candidate

    app_server = installed_resource(
        Path("Contents/Resources/codex-runtime/bin/codex-app-server")
    )
    receipt_path = installed_resource(
        Path("Contents/Resources/codex-runtime/JOBOS_CODEX_RUNTIME_RECEIPT.json")
    )
    keychain_helper = installed_resource(Path("Contents/Resources/jobos-keychain"))
    for path in (app_server, receipt_path, keychain_helper):
        if path.is_symlink() or not path.is_file():
            raise ValueError("installed JobOS Codex runtime is incomplete")
    if not os.access(app_server, os.X_OK) or not os.access(keychain_helper, os.X_OK):
        raise ValueError("installed JobOS Codex runtime is not executable")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt_hash = receipt["app_server_binary"]["sha256"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("installed JobOS Codex runtime receipt is invalid") from error
    if receipt_hash != CODEX_APP_SERVER_SHA256 or _sha256(app_server) != CODEX_APP_SERVER_SHA256:
        raise ValueError("installed JobOS Codex runtime failed integrity verification")
    helper_hash = _sha256(keychain_helper)
    if helper_hash != expected_keychain_helper_sha256:
        raise ValueError("installed JobOS Keychain helper failed integrity verification")
    return {
        "keychain_helper_path": str(keychain_helper),
        "keychain_helper_sha256": helper_hash,
        "codex_app_server_path": str(app_server),
        "codex_home_path": str(data_dir / "codex"),
    }


def build_local_runtime_config(
    *,
    jobos_root: Path,
    python_path: Path,
    data_dir: Path,
    device_id: str,
    port: int,
    jobos_app: Path | None = None,
    keychain_helper_sha256: str | None = None,
) -> RuntimeServiceConfig:
    if jobos_app is not None and keychain_helper_sha256 is None:
        raise ValueError("installed JobOS requires an expected Keychain helper SHA-256")
    codex_paths = (
        installed_codex_paths(
            jobos_app,
            data_dir,
            expected_keychain_helper_sha256=keychain_helper_sha256,
        )
        if jobos_app is not None and keychain_helper_sha256 is not None
        else {}
    )
    return RuntimeServiceConfig.from_mapping(
        {
            "schema_version": 1,
            "label": SERVICE_LABEL,
            "jobos_root": str(jobos_root),
            "python_path": str(python_path),
            "job_provider": "sqlite",
            "artifact_provider": "local",
            "facade_source_path": None,
            "state_db_path": str(data_dir / "state/jobos.db"),
            "jobs_db_path": str(data_dir / "jobs/jobs.db"),
            "local_artifact_root": str(data_dir / "artifacts"),
            "job_hunter_db_path": None,
            "artifact_roots": [],
            "hermes_dashboard_url": None,
            "hermes_job_hunter_cwd": None,
            "hermes_default_model_id": None,
            "hermes_default_reasoning_effort": None,
            "keychain_helper_path": codex_paths.get("keychain_helper_path"),
            "keychain_helper_sha256": codex_paths.get("keychain_helper_sha256"),
            "codex_app_server_path": codex_paths.get("codex_app_server_path"),
            "codex_home_path": codex_paths.get("codex_home_path"),
            "device_id": device_id,
            "remote_device_ids": [],
            "host": "127.0.0.1",
            "port": port,
        }
    )


def render_launchd_plist(
    config: RuntimeServiceConfig,
    *,
    config_path: Path,
    launcher_path: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> bytes:
    return plistlib.dumps(
        {
            "Label": config.label,
            "ProgramArguments": [
                str(config.python_path),
                str(launcher_path),
                "service",
                "--config",
                str(config_path),
            ],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 5,
            "ProcessType": "Background",
            "StandardOutPath": str(stdout_path),
            "StandardErrorPath": str(stderr_path),
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def render_desktop_runtime(config: RuntimeServiceConfig) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "mode": "local-service",
        "apiBaseUrl": f"http://127.0.0.1:{config.port}",
        "deviceId": config.device_id,
        "launchdLabel": config.label,
    }


def launchd_install_commands(uid: int, plist_path: Path, label: str) -> list[list[str]]:
    domain = f"gui/{uid}"
    service = f"{domain}/{label}"
    return [
        ["/bin/launchctl", "bootout", service],
        ["/bin/launchctl", "bootstrap", domain, str(plist_path)],
        ["/bin/launchctl", "kickstart", "-k", service],
    ]


@dataclass(frozen=True)
class RuntimeInstallation:
    service_config_path: Path
    desktop_config_path: Path
    plist_path: Path
    stdout_path: Path
    stderr_path: Path


def _installation_paths(home: Path, label: str) -> RuntimeInstallation:
    support = home / "Library/Application Support/JobOS"
    return RuntimeInstallation(
        service_config_path=support / "service/runtime.json",
        desktop_config_path=support / "runtime.json",
        plist_path=home / f"Library/LaunchAgents/{label}.plist",
        stdout_path=support / "logs/api.log",
        stderr_path=support / "logs/api.error.log",
    )


def _write_private_file(path: Path, contents: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_command(command: list[str], allow_failure: bool = False) -> None:
    try:
        subprocess.run(
            command,
            check=not allow_failure,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL if allow_failure else subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("JobOS launchd operation failed") from error


def _service_is_loaded(uid: int, label: str) -> bool:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{uid}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    if result.returncode == 0:
        return True
    if result.returncode in {3, 113}:
        return False
    raise RuntimeError("JobOS launchd status could not be determined")


def _wait_for_service_state(
    uid: int,
    label: str,
    expected: bool,
    *,
    is_loaded: Callable[[int, str], bool],
    sleep: Callable[[float], None],
) -> None:
    for delay in (0.0, 0.1, 0.25, 0.5, 1.0):
        if delay:
            sleep(delay)
        if is_loaded(uid, label) is expected:
            return
    state = "load" if expected else "unload"
    raise RuntimeError(f"JobOS launchd service did not {state}")


def _verify_authenticated_readiness(
    config: RuntimeServiceConfig,
    device_id: str,
    device_token: str,
) -> None:
    base_url = f"http://127.0.0.1:{config.port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/v1/health", timeout=1) as health:
                health_value = json.loads(health.read())
            request = Request(
                f"{base_url}/v1/device-session",
                headers={
                    "Authorization": f"Bearer {device_token}",
                    "X-JobOS-Device-Id": device_id,
                },
            )
            with urlopen(request, timeout=1) as session:
                session_value = json.loads(session.read())
            if health_value.get("status") == "ready" and session_value.get("authenticated") is True:
                return
        except (HTTPError, URLError, OSError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("JobOS API did not become authenticated and ready")


def _verify_profile_readiness(
    config: RuntimeServiceConfig,
    device_id: str,
    device_token: str,
    expected_profile_id: str,
) -> None:
    base_url = f"http://127.0.0.1:{config.port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            request = Request(
                f"{base_url}/v1/device-session",
                headers={
                    "Authorization": f"Bearer {device_token}",
                    "X-JobOS-Device-Id": device_id,
                },
            )
            with urlopen(request, timeout=1) as session:
                value = json.loads(session.read())
            if (
                value.get("authenticated") is True
                and value.get("installation_profile_id") == expected_profile_id
            ):
                return
        except (HTTPError, URLError, OSError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("JobOS API did not open the expected JobOS Profile")


def spawn_profile_switch_helper(
    registry_path: Path,
    target_profile_id: str,
    switch_id: str,
) -> None:
    arguments = [
        sys.executable,
        "-m",
        "jobos_api.macos_runtime",
        "profile-switch",
        "--registry",
        str(registry_path),
        "--target-profile-id",
        target_profile_id,
        "--switch-id",
        switch_id,
    ]
    try:
        subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            cwd="/",
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONUNBUFFERED": "1"},
        )
    except OSError as error:
        raise RuntimeError("JobOS Profile switch helper could not start") from error


def run_profile_switch(
    registry_path: Path,
    target_profile_id: str,
    switch_id: str,
    *,
    uid: int,
    run: Callable[[list[str], bool], None] = _run_command,
    read_secret: Callable[[str, str], str | None] = read_keychain_secret,
    verify_profile: Callable[[RuntimeServiceConfig, str, str, str], None] = (
        _verify_profile_readiness
    ),
) -> None:
    registry = InstallationProfileRegistry(registry_path)
    try:
        registry.claim_switch(switch_id, target_profile_id)
    except InstallationProfileRegistryError as error:
        with suppress(Exception):
            registry.fail_pending_switch(switch_id, "registry_write_failed")
        raise RuntimeError("JobOS Profile switch could not start") from error
    config_path = registry_path.parent / "service/runtime.json"
    base_config: RuntimeServiceConfig | None = None
    restart: list[str] | None = None
    device_token: str | None = None
    try:
        base_config = RuntimeServiceConfig.load(config_path)
        read_secret = _configured_read_secret(base_config, read_secret)
        restart = [
            "/bin/launchctl",
            "kickstart",
            "-k",
            f"gui/{uid}/{base_config.label}",
        ]
        target_config, _, _ = resolve_runtime_profile(base_config, registry_path)
        validate_runtime_paths(target_config)
        device_token = read_secret(DEVICE_TOKEN_SERVICE, base_config.device_id)
    except Exception:
        error_code = "target_startup_failed"
    else:
        if device_token is None:
            error_code = "device_credential_unavailable"
        else:
            try:
                run(restart, False)
            except Exception:
                error_code = "launchd_restart_failed"
            else:
                try:
                    verify_profile(
                        target_config,
                        base_config.device_id,
                        device_token,
                        target_profile_id,
                    )
                    registry.complete_switch(switch_id, target_profile_id)
                    return
                except Exception:
                    error_code = "target_startup_failed"

    previous_profile_id = registry.rollback_switch(switch_id, error_code)
    try:
        if base_config is None:
            base_config = RuntimeServiceConfig.load(config_path)
        if restart is None:
            restart = [
                "/bin/launchctl",
                "kickstart",
                "-k",
                f"gui/{uid}/{base_config.label}",
            ]
        previous_config, _, _ = resolve_runtime_profile(base_config, registry_path)
        run(restart, False)
        if device_token is None:
            device_token = read_secret(DEVICE_TOKEN_SERVICE, base_config.device_id)
        if device_token is None:
            raise RuntimeError("device credential unavailable")
        verify_profile(
            previous_config,
            base_config.device_id,
            device_token,
            previous_profile_id,
        )
    except Exception as rollback_error:
        registry.replace_last_switch_error(switch_id, "rollback_startup_failed")
        raise RuntimeError(
            "JobOS Profile switch rollback could not be verified"
        ) from rollback_error
    raise RuntimeError("JobOS Profile switch rolled back")


@dataclass(frozen=True)
class _FileSnapshot:
    contents: bytes | None
    mode: int | None


def _snapshot_file(path: Path) -> _FileSnapshot:
    if not path.exists():
        return _FileSnapshot(None, None)
    return _FileSnapshot(path.read_bytes(), path.stat().st_mode & 0o777)


def _restore_file(path: Path, snapshot: _FileSnapshot) -> None:
    if snapshot.contents is None:
        path.unlink(missing_ok=True)
        return
    _write_private_file(path, snapshot.contents, snapshot.mode or 0o600)


def _restore_secret(
    service: str,
    account: str,
    previous: str | None,
    *,
    store_secret: Callable[[str, str, str], None],
    delete_secret: Callable[[str, str], None],
) -> None:
    if previous is None:
        delete_secret(service, account)
    else:
        store_secret(service, account, previous)


def _validated_keychain_helper(config: RuntimeServiceConfig) -> Path | None:
    helper = config.keychain_helper_path
    if helper is None:
        return None
    if (
        not helper.is_absolute()
        or helper.is_symlink()
        or not helper.is_file()
        or not os.access(helper, os.X_OK)
        or config.keychain_helper_sha256 is None
        or _sha256(helper) != config.keychain_helper_sha256
    ):
        raise RuntimeError("installed JobOS Keychain helper failed integrity verification")
    return helper


def _configured_store_secret(
    config: RuntimeServiceConfig,
    callback: Callable[[str, str, str], None],
) -> Callable[[str, str, str], None]:
    if config.keychain_helper_path is not None and callback is store_keychain_secret:
        return partial(store_keychain_secret, helper_path=_validated_keychain_helper(config))
    return callback


def _configured_read_secret(
    config: RuntimeServiceConfig,
    callback: Callable[[str, str], str | None],
) -> Callable[[str, str], str | None]:
    if config.keychain_helper_path is not None and callback is read_keychain_secret:
        return partial(read_keychain_secret, helper_path=_validated_keychain_helper(config))
    return callback


def _configured_delete_secret(
    config: RuntimeServiceConfig,
    callback: Callable[[str, str], None],
) -> Callable[[str, str], None]:
    if config.keychain_helper_path is not None and callback is delete_keychain_secret:
        return partial(delete_keychain_secret, helper_path=_validated_keychain_helper(config))
    return callback


def install_runtime(
    config: RuntimeServiceConfig,
    *,
    home: Path,
    launcher_path: Path,
    uid: int,
    device_token: str,
    mcp_token: str,
    hermes_dashboard_token: str | None,
    store_secret: Callable[[str, str, str], None] = store_keychain_secret,
    read_secret: Callable[[str, str], str | None] = read_keychain_secret,
    delete_secret: Callable[[str, str], None] = delete_keychain_secret,
    run: Callable[[list[str], bool], None] = _run_command,
    sleep: Callable[[float], None] = time.sleep,
    is_loaded: Callable[[int, str], bool] = _service_is_loaded,
    verify_ready: Callable[[RuntimeServiceConfig, str, str], None] = (
        _verify_authenticated_readiness
    ),
) -> RuntimeInstallation:
    validate_runtime_paths(config)
    if not launcher_path.is_absolute() or not launcher_path.is_file():
        raise RuntimeError("JobOS runtime launcher is unavailable")
    paths = _installation_paths(home, config.label)
    store_secret = _configured_store_secret(config, store_secret)
    read_secret = _configured_read_secret(config, read_secret)
    delete_secret = _configured_delete_secret(config, delete_secret)
    build_service_environment(
        config,
        device_token=device_token,
        mcp_token=mcp_token,
        hermes_dashboard_token=hermes_dashboard_token,
        base_environment={},
        service_config_path=paths.service_config_path,
    )
    if config.hermes_dashboard_url and not hermes_dashboard_token:
        raise ValueError("Hermes credential is required for the configured dashboard")

    service_config = (json.dumps(config.to_mapping(), indent=2, sort_keys=True) + "\n").encode()
    desktop_config = (
        json.dumps(render_desktop_runtime(config), indent=2, sort_keys=True) + "\n"
    ).encode()
    plist = render_launchd_plist(
        config,
        config_path=paths.service_config_path,
        launcher_path=launcher_path,
        stdout_path=paths.stdout_path,
        stderr_path=paths.stderr_path,
    )

    commands = launchd_install_commands(uid, paths.plist_path, config.label)
    file_snapshots = {
        path: _snapshot_file(path)
        for path in (
            paths.service_config_path,
            paths.desktop_config_path,
            paths.plist_path,
        )
    }
    previous_device_token = read_secret(DEVICE_TOKEN_SERVICE, config.device_id)
    previous_mcp_token = read_secret(MCP_TOKEN_SERVICE, config.device_id)
    previous_hermes_token = read_secret(HERMES_TOKEN_SERVICE, config.device_id)
    previously_loaded = is_loaded(uid, config.label)

    try:
        _write_private_file(paths.service_config_path, service_config, 0o600)
        _write_private_file(paths.desktop_config_path, desktop_config, 0o600)
        _write_private_file(paths.plist_path, plist, 0o644)
        paths.stdout_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        store_secret(DEVICE_TOKEN_SERVICE, config.device_id, device_token)
        store_secret(MCP_TOKEN_SERVICE, config.device_id, mcp_token)
        if hermes_dashboard_token:
            store_secret(HERMES_TOKEN_SERVICE, config.device_id, hermes_dashboard_token)
        else:
            delete_secret(HERMES_TOKEN_SERVICE, config.device_id)

        run(commands[0], True)
        _wait_for_service_state(
            uid,
            config.label,
            False,
            is_loaded=is_loaded,
            sleep=sleep,
        )
        for attempt, delay in enumerate((0.0, 0.5, 1.5)):
            if delay:
                sleep(delay)
            try:
                run(commands[1], False)
                break
            except RuntimeError:
                if attempt == 2:
                    raise
        _wait_for_service_state(
            uid,
            config.label,
            True,
            is_loaded=is_loaded,
            sleep=sleep,
        )
        run(commands[2], False)
        verify_ready(config, config.device_id, device_token)
        return paths
    except Exception as error:
        run(commands[0], True)
        for path, snapshot in file_snapshots.items():
            _restore_file(path, snapshot)
        _restore_secret(
            DEVICE_TOKEN_SERVICE,
            config.device_id,
            previous_device_token,
            store_secret=store_secret,
            delete_secret=delete_secret,
        )
        _restore_secret(
            MCP_TOKEN_SERVICE,
            config.device_id,
            previous_mcp_token,
            store_secret=store_secret,
            delete_secret=delete_secret,
        )
        _restore_secret(
            HERMES_TOKEN_SERVICE,
            config.device_id,
            previous_hermes_token,
            store_secret=store_secret,
            delete_secret=delete_secret,
        )
        if previously_loaded and file_snapshots[paths.plist_path].contents is not None:
            run(commands[1], False)
            run(commands[2], False)
        raise RuntimeError("JobOS runtime installation rolled back") from error


def authorize_remote_device(
    config_path: Path,
    *,
    device_id: str,
    device_token: str,
    uid: int,
    store_secret: Callable[[str, str, str], None] = store_keychain_secret,
    read_secret: Callable[[str, str], str | None] = read_keychain_secret,
    delete_secret: Callable[[str, str], None] = delete_keychain_secret,
    run: Callable[[list[str], bool], None] = _run_command,
    verify_ready: Callable[[RuntimeServiceConfig, str, str], None] = (
        _verify_authenticated_readiness
    ),
) -> RuntimeServiceConfig:
    config = RuntimeServiceConfig.load(config_path)
    store_secret = _configured_store_secret(config, store_secret)
    read_secret = _configured_read_secret(config, read_secret)
    delete_secret = _configured_delete_secret(config, delete_secret)
    if (
        not _DEVICE_PATTERN.fullmatch(device_id)
        or device_id == config.device_id
        or not 16 <= len(device_token) <= 4096
        or any(char in device_token for char in "\r\n\0")
    ):
        raise ValueError("remote device credential is invalid")
    if device_id in config.remote_device_ids:
        raise ValueError("remote device is already authorized")
    registered_tokens = []
    for registered_id in (config.device_id, *config.remote_device_ids):
        registered_token = read_secret(DEVICE_TOKEN_SERVICE, registered_id)
        if registered_token is None:
            raise RuntimeError("registered device credential is unavailable")
        registered_tokens.append(registered_token)
    if any(
        hmac.compare_digest(device_token.encode(), registered.encode())
        for registered in registered_tokens
    ):
        raise ValueError("remote device credential must be unique")

    updated = replace(
        config,
        remote_device_ids=(*config.remote_device_ids, device_id),
    )
    persisted = (json.dumps(updated.to_mapping(), indent=2, sort_keys=True) + "\n").encode()
    previous_config = _snapshot_file(config_path)
    restart = [
        "/bin/launchctl",
        "kickstart",
        "-k",
        f"gui/{uid}/{config.label}",
    ]
    try:
        store_secret(DEVICE_TOKEN_SERVICE, device_id, device_token)
        _write_private_file(config_path, persisted, 0o600)
        run(restart, False)
        verify_ready(updated, device_id, device_token)
        return updated
    except Exception as error:
        _restore_file(config_path, previous_config)
        delete_secret(DEVICE_TOKEN_SERVICE, device_id)
        run(restart, False)
        raise RuntimeError("JobOS remote authorization rolled back") from error


def runtime_status(
    config_path: Path,
    *,
    uid: int,
    is_loaded: Callable[[int, str], bool] = _service_is_loaded,
    read_secret: Callable[[str, str], str | None] = read_keychain_secret,
    verify_ready: Callable[[RuntimeServiceConfig, str, str], None] = (
        _verify_authenticated_readiness
    ),
) -> dict[str, object]:
    config = RuntimeServiceConfig.load(config_path)
    read_secret = _configured_read_secret(config, read_secret)
    loaded = is_loaded(uid, config.label)
    authenticated = False
    if loaded:
        token = read_secret(DEVICE_TOKEN_SERVICE, config.device_id)
        if token is not None:
            try:
                verify_ready(config, config.device_id, token)
                authenticated = True
            except RuntimeError:
                pass
    return {
        "label": config.label,
        "loaded": loaded,
        "authenticated": authenticated,
        "endpoint": f"http://127.0.0.1:{config.port}",
    }


def uninstall_runtime(
    config_path: Path,
    *,
    home: Path,
    uid: int,
    run: Callable[[list[str], bool], None] = _run_command,
    sleep: Callable[[float], None] = time.sleep,
    is_loaded: Callable[[int, str], bool] = _service_is_loaded,
    delete_secret: Callable[[str, str], None] = delete_keychain_secret,
) -> None:
    config = RuntimeServiceConfig.load(config_path)
    delete_secret = _configured_delete_secret(config, delete_secret)
    paths = _installation_paths(home, config.label)
    service = f"gui/{uid}/{config.label}"
    run(["/bin/launchctl", "bootout", service], True)
    _wait_for_service_state(
        uid,
        config.label,
        False,
        is_loaded=is_loaded,
        sleep=sleep,
    )
    for device_id in (config.device_id, *config.remote_device_ids):
        delete_secret(DEVICE_TOKEN_SERVICE, device_id)
    delete_secret(HERMES_TOKEN_SERVICE, config.device_id)
    delete_secret(MCP_TOKEN_SERVICE, config.device_id)
    for path in (
        paths.plist_path,
        paths.desktop_config_path,
        paths.service_config_path,
    ):
        path.unlink(missing_ok=True)


def _read_keychain(service: str, account: str, *, helper_path: Path | None = None) -> str:
    value = read_keychain_secret(service, account, helper_path=helper_path)
    if value is None:
        raise RuntimeError("required JobOS Keychain credential is unavailable")
    return value


def validate_runtime_paths(config: RuntimeServiceConfig) -> None:
    required_files = (config.python_path,)
    required_directories = (config.jobos_root / "services/api/jobos_api",)
    if config.facade_source_path:
        required_directories += (config.facade_source_path,)
    if config.job_hunter_db_path:
        required_files += (config.job_hunter_db_path,)
    required_directories += config.artifact_roots
    if config.hermes_job_hunter_cwd:
        required_directories += (config.hermes_job_hunter_cwd,)
    if config.codex_app_server_path:
        assert config.keychain_helper_path is not None
        assert config.keychain_helper_sha256 is not None
        required_files += (
            config.codex_app_server_path,
            config.keychain_helper_path,
            config.jobos_root / "scripts/macos/jobos_mcp_runtime.py",
        )
        if not os.access(config.codex_app_server_path, os.X_OK) or not os.access(
            config.keychain_helper_path, os.X_OK
        ):
            raise RuntimeError("installed JobOS Codex runtime is not executable")
    if any(not path.is_file() for path in required_files):
        raise RuntimeError("required JobOS runtime file is unavailable")
    if any(not path.is_dir() for path in required_directories):
        raise RuntimeError("required JobOS runtime directory is unavailable")
    if (
        config.codex_app_server_path
        and _sha256(config.codex_app_server_path) != CODEX_APP_SERVER_SHA256
    ):
        raise RuntimeError("installed JobOS Codex runtime failed integrity verification")
    if (
        config.keychain_helper_path
        and _sha256(config.keychain_helper_path) != config.keychain_helper_sha256
    ):
        raise RuntimeError("installed JobOS Keychain helper failed integrity verification")
    for directory in {
        config.state_db_path.parent,
        config.jobs_db_path.parent,
        config.local_artifact_root,
        *([config.codex_home_path] if config.codex_home_path else []),
    }:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)


def run_service(config_path: Path) -> None:
    base_config = RuntimeServiceConfig.load(config_path)
    registry_path = installation_registry_path_for_runtime(config_path)
    config, registry_data, profile = resolve_runtime_profile(base_config, registry_path)
    validate_runtime_paths(config)
    helper_path = config.keychain_helper_path
    device_token = _read_keychain(
        DEVICE_TOKEN_SERVICE, config.device_id, helper_path=helper_path
    )
    mcp_token = read_keychain_secret(
        MCP_TOKEN_SERVICE, config.device_id, helper_path=helper_path
    )
    if mcp_token is None:
        mcp_token = secrets.token_urlsafe(48)
        store_keychain_secret(
            MCP_TOKEN_SERVICE, config.device_id, mcp_token, helper_path=helper_path
        )
    remote_device_tokens = {
        device_id: _read_keychain(
            DEVICE_TOKEN_SERVICE, device_id, helper_path=helper_path
        )
        for device_id in config.remote_device_ids
    }
    hermes_token = (
        _read_keychain(HERMES_TOKEN_SERVICE, config.device_id, helper_path=helper_path)
        if config.hermes_dashboard_url
        else None
    )
    environment = build_service_environment(
        config,
        device_token=device_token,
        mcp_token=mcp_token,
        remote_device_tokens=remote_device_tokens,
        hermes_dashboard_token=hermes_token,
        installation_profile_id=profile.profile_id,
        installation_profile_name=profile.display_name,
        installation_registry_path=registry_path,
        profile_registry_revision=registry_data.registry_revision,
        profile_switch_driver="launchd",
        service_config_path=config_path,
    )
    arguments = build_uvicorn_arguments(config)
    os.execve(arguments[0], arguments, environment)


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JobOS macOS runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install", help="install the per-user API service")
    install.add_argument("--config", type=Path, required=True)
    install.add_argument("--home", type=Path, default=Path.home())
    install.add_argument("--launcher", type=Path, required=True)
    install_local = subparsers.add_parser(
        "install-local", help="install the public local SQLite API service"
    )
    install_local.add_argument("--jobos-root", type=Path, required=True)
    install_local.add_argument("--python", dest="python_path", type=Path, required=True)
    install_local.add_argument("--data-dir", type=Path, required=True)
    install_local.add_argument("--device-id", default="primary-device")
    install_local.add_argument("--port", type=int, default=8766)
    install_local.add_argument("--home", type=Path, default=Path.home())
    install_local.add_argument("--launcher", type=Path, required=True)
    install_local.add_argument("--jobos-app", type=Path)
    install_local.add_argument("--keychain-helper-sha256")
    authorize = subparsers.add_parser(
        "authorize-remote",
        help="authorize one remote desktop device",
    )
    authorize.add_argument("--config", type=Path, required=True)
    authorize.add_argument("--device-id", required=True)
    status = subparsers.add_parser("status", help="report service and API readiness")
    status.add_argument("--config", type=Path, required=True)
    uninstall = subparsers.add_parser("uninstall", help="remove the per-user API service")
    uninstall.add_argument("--config", type=Path, required=True)
    uninstall.add_argument("--home", type=Path, default=Path.home())
    service = subparsers.add_parser("service", help="run the launchd-owned API")
    service.add_argument("--config", type=Path, required=True)
    profile_switch = subparsers.add_parser(
        "profile-switch", help="complete one pending JobOS Profile switch"
    )
    profile_switch.add_argument("--registry", type=Path, required=True)
    profile_switch.add_argument("--target-profile-id", required=True)
    profile_switch.add_argument("--switch-id", required=True)
    source_rollback = subparsers.add_parser(
        "source-profile-rollback",
        help="roll back one failed desktop-driven JobOS Profile switch",
    )
    source_rollback.add_argument("--registry", type=Path, required=True)
    source_rollback.add_argument("--switch-id", required=True)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(arguments if arguments is not None else sys.argv[1:])
    try:
        if options.command in {"install", "install-local"}:
            device_token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
            if not device_token:
                raise RuntimeError("JOBOS_DEVICE_TOKEN is required for installation")
            mcp_token = os.environ.get("JOBOS_MCP_TOKEN") or secrets.token_urlsafe(48)
            config = (
                RuntimeServiceConfig.load(options.config)
                if options.command == "install"
                else build_local_runtime_config(
                    jobos_root=options.jobos_root,
                    python_path=options.python_path,
                    data_dir=options.data_dir,
                    device_id=options.device_id,
                    port=options.port,
                    jobos_app=options.jobos_app,
                    keychain_helper_sha256=options.keychain_helper_sha256,
                )
            )
            install_runtime(
                config,
                home=options.home,
                launcher_path=options.launcher,
                uid=os.getuid(),
                device_token=device_token,
                mcp_token=mcp_token,
                hermes_dashboard_token=os.environ.get("JOBOS_HERMES_DASHBOARD_TOKEN"),
            )
            print(f"JobOS runtime installed: {config.label}")
        elif options.command == "authorize-remote":
            device_token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
            if not device_token:
                raise RuntimeError("JOBOS_DEVICE_TOKEN is required for authorization")
            authorize_remote_device(
                options.config,
                device_id=options.device_id,
                device_token=device_token,
                uid=os.getuid(),
            )
            print(f"JobOS remote device authorized: {options.device_id}")
        elif options.command == "status":
            print(
                json.dumps(
                    runtime_status(options.config, uid=os.getuid()),
                    sort_keys=True,
                )
            )
        elif options.command == "uninstall":
            uninstall_runtime(
                options.config,
                home=options.home,
                uid=os.getuid(),
            )
            print("JobOS runtime uninstalled")
        elif options.command == "service":
            run_service(options.config)
        elif options.command == "profile-switch":
            run_profile_switch(
                options.registry,
                options.target_profile_id,
                options.switch_id,
                uid=os.getuid(),
            )
        elif options.command == "source-profile-rollback":
            InstallationProfileRegistry(options.registry).rollback_completed_source_switch(
                options.switch_id,
                "target_startup_failed",
            )
    except (RuntimeError, ValueError) as error:
        print(f"JobOS runtime failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

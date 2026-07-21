from __future__ import annotations

import argparse
import hmac
import json
import os
import plistlib
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from jobos_api.macos_keychain import (
    delete_keychain_secret,
    read_keychain_secret,
    store_keychain_secret,
)

SERVICE_LABEL = "com.cobibean.jobos.api"
DEVICE_TOKEN_SERVICE = "com.cobibean.jobos.device-token"
HERMES_TOKEN_SERVICE = "com.cobibean.jobos.hermes-dashboard-token"
_CONFIG_FIELDS = {
    "schema_version",
    "label",
    "jobos_root",
    "python_path",
    "facade_source_path",
    "state_db_path",
    "job_hunter_db_path",
    "artifact_roots",
    "hermes_dashboard_url",
    "hermes_job_hunter_cwd",
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
    facade_source_path: Path
    state_db_path: Path
    job_hunter_db_path: Path
    artifact_roots: tuple[Path, ...]
    hermes_dashboard_url: str | None
    hermes_job_hunter_cwd: Path | None
    device_id: str
    remote_device_ids: tuple[str, ...]
    host: str
    port: int

    @classmethod
    def from_mapping(cls, value: object) -> RuntimeServiceConfig:
        if not isinstance(value, dict):
            raise ValueError("runtime config must be an object")
        unknown = set(value) - _CONFIG_FIELDS
        if unknown:
            raise ValueError("runtime config contains unknown fields")
        missing = _CONFIG_FIELDS - set(value)
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
                not isinstance(remote_id, str)
                or not _DEVICE_PATTERN.fullmatch(remote_id)
                for remote_id in remote_device_ids
            )
            or device_id in remote_device_ids
            or len(set(remote_device_ids)) != len(remote_device_ids)
        ):
            raise ValueError("remote device identifiers are invalid")
        roots = value["artifact_roots"]
        if not isinstance(roots, list) or not roots:
            raise ValueError("artifact roots must be a non-empty list")
        hermes_cwd = value["hermes_job_hunter_cwd"]
        return cls(
            schema_version=1,
            label=SERVICE_LABEL,
            jobos_root=_absolute_path(value["jobos_root"], "jobos_root"),
            python_path=_absolute_path(value["python_path"], "python_path"),
            facade_source_path=_absolute_path(
                value["facade_source_path"], "facade_source_path"
            ),
            state_db_path=_absolute_path(value["state_db_path"], "state_db_path"),
            job_hunter_db_path=_absolute_path(
                value["job_hunter_db_path"], "job_hunter_db_path"
            ),
            artifact_roots=tuple(
                _absolute_path(root, "artifact_roots") for root in roots
            ),
            hermes_dashboard_url=_loopback_dashboard_url(value["hermes_dashboard_url"]),
            hermes_job_hunter_cwd=(
                _absolute_path(hermes_cwd, "hermes_job_hunter_cwd")
                if hermes_cwd is not None
                else None
            ),
            device_id=device_id,
            remote_device_ids=tuple(remote_device_ids),
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
            "job_hunter_db_path",
            "hermes_job_hunter_cwd",
        ):
            if value[field] is not None:
                value[field] = str(value[field])
        value["artifact_roots"] = [str(path) for path in self.artifact_roots]
        value["remote_device_ids"] = list(self.remote_device_ids)
        return value


def build_service_environment(
    config: RuntimeServiceConfig,
    *,
    device_token: str,
    remote_device_tokens: dict[str, str] | None = None,
    hermes_dashboard_token: str | None,
    base_environment: dict[str, str] | None = None,
) -> dict[str, str]:
    if not 16 <= len(device_token) <= 4096 or any(char in device_token for char in "\r\n\0"):
        raise ValueError("device credential is invalid")
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
    environment = {
        "PATH": source.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": os.pathsep.join(
            (str(config.jobos_root / "services/api"), str(config.facade_source_path))
        ),
        "JOBOS_DEVICE_TOKEN": device_token,
        "JOBOS_DEVICE_ID": config.device_id,
        "JOBOS_STATE_DB_PATH": str(config.state_db_path),
        "JOBOS_JOB_HUNTER_DB_PATH": str(config.job_hunter_db_path),
        "JOBOS_ARTIFACT_ROOTS": os.pathsep.join(map(str, config.artifact_roots)),
    }
    if config.hermes_dashboard_url:
        environment["JOBOS_HERMES_DASHBOARD_URL"] = config.hermes_dashboard_url
    if hermes_dashboard_token:
        environment["JOBOS_HERMES_DASHBOARD_TOKEN"] = hermes_dashboard_token
    if config.hermes_job_hunter_cwd:
        environment["JOBOS_HERMES_JOB_HUNTER_CWD"] = str(config.hermes_job_hunter_cwd)
    if remote_device_tokens:
        environment["JOBOS_DEVICE_CREDENTIALS_JSON"] = json.dumps(
            remote_device_tokens,
            separators=(",", ":"),
            sort_keys=True,
        )
    return environment


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
            if (
                health_value.get("status") == "ready"
                and session_value.get("authenticated") is True
            ):
                return
        except (HTTPError, URLError, OSError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("JobOS API did not become authenticated and ready")


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


def install_runtime(
    config: RuntimeServiceConfig,
    *,
    home: Path,
    launcher_path: Path,
    uid: int,
    device_token: str,
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
    build_service_environment(
        config,
        device_token=device_token,
        hermes_dashboard_token=hermes_dashboard_token,
        base_environment={},
    )
    if config.hermes_dashboard_url and not hermes_dashboard_token:
        raise ValueError("Hermes credential is required for the configured dashboard")

    paths = _installation_paths(home, config.label)
    service_config = (
        json.dumps(config.to_mapping(), indent=2, sort_keys=True) + "\n"
    ).encode()
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
    previous_hermes_token = read_secret(HERMES_TOKEN_SERVICE, config.device_id)
    previously_loaded = is_loaded(uid, config.label)

    try:
        _write_private_file(paths.service_config_path, service_config, 0o600)
        _write_private_file(paths.desktop_config_path, desktop_config, 0o600)
        _write_private_file(paths.plist_path, plist, 0o644)
        paths.stdout_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        store_secret(DEVICE_TOKEN_SERVICE, config.device_id, device_token)
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
    persisted = (
        json.dumps(updated.to_mapping(), indent=2, sort_keys=True) + "\n"
    ).encode()
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
    for path in (
        paths.plist_path,
        paths.desktop_config_path,
        paths.service_config_path,
    ):
        path.unlink(missing_ok=True)


def _read_keychain(service: str, account: str) -> str:
    value = read_keychain_secret(service, account)
    if value is None:
        raise RuntimeError("required JobOS Keychain credential is unavailable")
    return value


def validate_runtime_paths(config: RuntimeServiceConfig) -> None:
    required_files = (config.python_path, config.job_hunter_db_path)
    required_directories = (
        config.jobos_root / "services/api/jobos_api",
        config.facade_source_path,
        *config.artifact_roots,
    )
    if config.hermes_job_hunter_cwd:
        required_directories += (config.hermes_job_hunter_cwd,)
    if any(not path.is_file() for path in required_files):
        raise RuntimeError("required JobOS runtime file is unavailable")
    if any(not path.is_dir() for path in required_directories):
        raise RuntimeError("required JobOS runtime directory is unavailable")
    config.state_db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def run_service(config_path: Path) -> None:
    config = RuntimeServiceConfig.load(config_path)
    validate_runtime_paths(config)
    device_token = _read_keychain(DEVICE_TOKEN_SERVICE, config.device_id)
    remote_device_tokens = {
        device_id: _read_keychain(DEVICE_TOKEN_SERVICE, device_id)
        for device_id in config.remote_device_ids
    }
    hermes_token = (
        _read_keychain(HERMES_TOKEN_SERVICE, config.device_id)
        if config.hermes_dashboard_url
        else None
    )
    environment = build_service_environment(
        config,
        device_token=device_token,
        remote_device_tokens=remote_device_tokens,
        hermes_dashboard_token=hermes_token,
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
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(arguments if arguments is not None else sys.argv[1:])
    try:
        if options.command == "install":
            device_token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
            if not device_token:
                raise RuntimeError("JOBOS_DEVICE_TOKEN is required for installation")
            config = RuntimeServiceConfig.load(options.config)
            install_runtime(
                config,
                home=options.home,
                launcher_path=options.launcher,
                uid=os.getuid(),
                device_token=device_token,
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
    except (RuntimeError, ValueError) as error:
        print(f"JobOS runtime failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

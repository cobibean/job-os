from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class McpRuntimeLaunch:
    executable: Path
    arguments: tuple[str, ...]
    environment: dict[str, str]


def _absolute_existing_path(
    value: object,
    label: str,
    *,
    directory: bool = False,
    preserve_symlink: bool = False,
) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"JobOS {label} is unavailable")
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError(f"JobOS {label} must be absolute")
    if preserve_symlink:
        if not path.exists():
            raise RuntimeError(f"JobOS {label} is unavailable")
    else:
        try:
            path = path.resolve(strict=True)
        except OSError as error:
            raise RuntimeError(f"JobOS {label} is unavailable") from error
    if directory and not path.is_dir():
        raise RuntimeError(f"JobOS {label} must be a directory")
    if not directory and not path.is_file():
        raise RuntimeError(f"JobOS {label} must be a file")
    return path


def _required_secret(credentials: Mapping[str, Any], field: str) -> str:
    value = credentials.get(field)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"JobOS {field.replace('_', ' ')} is unavailable")
    return value


def _release_path(
    jobos_root: Path,
    relative_path: str,
    label: str,
    *,
    directory: bool = False,
) -> Path:
    expected_path = jobos_root / relative_path
    resolved_path = _absolute_existing_path(
        str(expected_path), label, directory=directory
    )
    if resolved_path != expected_path:
        raise RuntimeError(f"JobOS {label} must stay inside the runtime release")
    return resolved_path


def build_mcp_runtime_launch(
    runtime: Mapping[str, Any],
    credentials: Mapping[str, Any],
    *,
    base_environment: Mapping[str, str] | None = None,
) -> McpRuntimeLaunch:
    jobos_root = _absolute_existing_path(runtime.get("jobos_root"), "runtime root", directory=True)
    python_path = _absolute_existing_path(
        runtime.get("python_path"), "Python executable", preserve_symlink=True
    )
    mcp_source = _release_path(
        jobos_root, "services/mcp", "release MCP source", directory=True
    )
    mcp_package = _release_path(
        jobos_root,
        "services/mcp/jobos_mcp",
        "release MCP package",
        directory=True,
    )
    _release_path(
        jobos_root,
        "services/mcp/jobos_mcp/__init__.py",
        "release MCP package marker",
    )
    _release_path(
        jobos_root,
        "services/mcp/jobos_mcp/__main__.py",
        "release MCP entry point",
    )
    _release_path(
        jobos_root,
        "services/mcp/jobos_mcp/server.py",
        "release MCP server",
    )
    _release_path(
        jobos_root,
        "services/mcp/jobos_mcp/jobs.py",
        "release MCP client",
    )
    if any(path.is_symlink() for path in mcp_package.rglob("*")):
        raise RuntimeError("JobOS release MCP package cannot contain symbolic links")

    host = runtime.get("host")
    port = runtime.get("port")
    valid_port = isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535
    if host != "127.0.0.1" or not valid_port:
        raise RuntimeError("JobOS MCP requires a loopback API runtime")

    environment = dict(os.environ if base_environment is None else base_environment)
    environment.update(
        {
            "PYTHONPATH": str(mcp_source),
            "PYTHONUNBUFFERED": "1",
            "JOBOS_API_BASE_URL": f"http://127.0.0.1:{port}",
            "JOBOS_DEVICE_TOKEN": _required_secret(credentials, "device_token"),
            "JOBOS_MCP_TOKEN": _required_secret(credentials, "mcp_token"),
            "JOBOS_RUNTIME_ROOT": str(jobos_root),
        }
    )
    arguments = (str(python_path), "-P", "-m", "jobos_mcp")
    return McpRuntimeLaunch(
        executable=python_path,
        arguments=arguments,
        environment=environment,
    )


def keychain_credentials(runtime: Mapping[str, Any]) -> dict[str, str]:
    jobos_root = _absolute_existing_path(runtime.get("jobos_root"), "runtime root", directory=True)
    helper = _absolute_existing_path(runtime.get("keychain_helper_path"), "Keychain helper")
    expected_helper_hash = runtime.get("keychain_helper_sha256")
    if not isinstance(expected_helper_hash, str) or len(expected_helper_hash) != 64:
        raise RuntimeError("JobOS Keychain helper integrity metadata is unavailable")
    helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
    if helper_hash != expected_helper_hash:
        raise RuntimeError("JobOS Keychain helper failed integrity verification")
    device_id = runtime.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        raise RuntimeError("JobOS device identifier is unavailable")
    api_source = jobos_root / "services/api"
    if not api_source.is_dir():
        raise RuntimeError("JobOS API release source is unavailable")
    sys.path.insert(0, str(api_source))
    os.environ["JOBOS_KEYCHAIN_HELPER_PATH"] = str(helper)
    keychain = importlib.import_module("jobos_api.macos_keychain")

    device_token = keychain.read_keychain_secret("com.cobibean.jobos.device-token", device_id)
    mcp_token = keychain.read_keychain_secret("com.cobibean.jobos.mcp-token", device_id)
    if device_token is None or mcp_token is None:
        raise RuntimeError("required JobOS Keychain credential is unavailable")
    return {"device_token": device_token, "mcp_token": mcp_token}


def main(arguments: list[str] | None = None) -> None:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) != 1:
        raise RuntimeError("Usage: jobos_mcp_runtime.py <runtime.json>")

    runtime_path = _absolute_existing_path(values[0], "runtime configuration")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if not isinstance(runtime, dict):
        raise RuntimeError("JobOS MCP runtime file must contain a JSON object")

    launch = build_mcp_runtime_launch(runtime, keychain_credentials(runtime))
    os.execve(str(launch.executable), list(launch.arguments), launch.environment)


if __name__ == "__main__":
    main()

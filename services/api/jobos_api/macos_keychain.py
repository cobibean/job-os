from __future__ import annotations

import os
import subprocess
from pathlib import Path

_NOT_FOUND_EXIT = 44
_MAX_SECRET_BYTES = 4096
_DEFAULT_HELPER = (
    Path(__file__).resolve().parents[3] / "apps" / "desktop" / "build" / "jobos-keychain"
)


def keychain_helper_path() -> Path:
    override = os.environ.get("JOBOS_KEYCHAIN_HELPER_PATH")
    return Path(override).expanduser().resolve() if override else _DEFAULT_HELPER


def _name(value: str, field: str) -> str:
    candidate = value.strip()
    if not candidate or "\0" in candidate or len(candidate) > 255:
        raise ValueError(f"{field} is invalid")
    return candidate


def _run_helper(
    command: str,
    service: str,
    account: str,
    *,
    secret: str | None = None,
    helper_path: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    helper = helper_path or keychain_helper_path()
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise RuntimeError("JobOS Keychain helper is unavailable")
    service_name = _name(service, "Keychain service")
    account_name = _name(account, "Keychain account")
    secret_bytes = None if secret is None else secret.encode("utf-8")
    try:
        return subprocess.run(
            [str(helper), command, service_name, account_name],
            input=secret_bytes,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("JobOS Keychain operation failed") from error


def read_keychain_secret(
    service: str, account: str, *, helper_path: Path | None = None
) -> str | None:
    result = _run_helper("get", service, account, helper_path=helper_path)
    if result.returncode == _NOT_FOUND_EXIT:
        return None
    if result.returncode != 0:
        raise RuntimeError("JobOS Keychain credential could not be read")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("JobOS Keychain credential is invalid") from error


def store_keychain_secret(
    service: str,
    account: str,
    secret: str,
    *,
    helper_path: Path | None = None,
) -> None:
    secret_bytes = secret.encode("utf-8")
    if not secret or "\0" in secret or len(secret_bytes) > _MAX_SECRET_BYTES:
        raise ValueError("Keychain credential is invalid")
    result = _run_helper("set", service, account, secret=secret, helper_path=helper_path)
    if result.returncode != 0:
        raise RuntimeError("JobOS Keychain credential could not be stored")


def delete_keychain_secret(
    service: str, account: str, *, helper_path: Path | None = None
) -> None:
    result = _run_helper("delete", service, account, helper_path=helper_path)
    if result.returncode != 0:
        raise RuntimeError("JobOS Keychain credential could not be deleted")

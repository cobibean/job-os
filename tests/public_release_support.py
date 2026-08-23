from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

REDACTIONS = (
    (
        re.compile(
            r"(?i)([\"']?authorization[\"']?\s*[:=]\s*)"
            r"(?:\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'|"
            r"\"(?:\\[\s\S]|[^\"\\])*\Z|'(?:\\[\s\S]|[^'\\])*\Z|"
            r"[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*)"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)([\"']?(?:jobos[_-])?(?:device|mcp)[_-]?token[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}\]]+)"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"/(?:Users|home)/[^/\s]+"), "/home/[REDACTED]"),
)


def redact(value: str) -> str:
    for pattern, replacement in REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def isolated_environment(root: Path) -> dict[str, str]:
    platform_keys = (
        "USER",
        "LOGNAME",
        "__CF_USER_TEXT_ENCODING",
        "SECURITYSESSIONID",
    ) if sys.platform == "darwin" else ()
    allowed = {
        key: os.environ[key]
        for key in ("PATH", "SHELL", "LANG", "LC_ALL", "SYSTEMROOT", *platform_keys)
        if os.environ.get(key)
    }
    directories = {
        "HOME": root / "home",
        "TMPDIR": root / "tmp",
        "XDG_DATA_HOME": root / "xdg/data",
        "XDG_CONFIG_HOME": root / "xdg/config",
        "XDG_CACHE_HOME": root / "xdg/cache",
        "XDG_RUNTIME_DIR": root / "xdg/runtime",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, mode=0o700)
    allowed.update({key: str(value) for key, value in directories.items()})
    allowed.update(
        {
            "JOBOS_DATA_DIR": str(root / "profile"),
            "JOBOS_CONFIG_PATH": str(root / "profile/config.json"),
            "JOBOS_KEYCHAIN_HELPER_PATH": str(root / "absent/jobos-keychain"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return allowed


def run_json(
    arguments: list[str], *, environment: dict[str, str], timeout: int = 15
) -> dict[str, Any]:
    result = subprocess.run(
        arguments,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = redact(result.stderr).strip().splitlines()[-20:]
        raise AssertionError(
            f"{arguments[0]} failed with exit code {result.returncode}: {' | '.join(detail)}"
        )
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


class ApiProcess:
    def __init__(self, root: Path, environment: dict[str, str], token: str) -> None:
        self.root = root
        self.environment = environment
        self.token = token
        self.process: subprocess.Popen[str] | None = None
        self.base_url = ""
        self._log = None

    def start(self, label: str) -> None:
        self._label = label
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.set_inheritable(True)
        port = listener.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        self._log = (self.root / "logs" / f"api-{label}.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "uvicorn",
                "jobos_api.main:app",
                "--fd",
                str(listener.fileno()),
                "--no-access-log",
                "--log-level",
                "warning",
            ],
            env=self.environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            text=True,
            pass_fds=(listener.fileno(),),
        )
        listener.close()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("JobOS API exited before readiness")
            try:
                response = httpx.get(f"{self.base_url}/v1/health", timeout=1)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise AssertionError("JobOS API did not become ready within 15 seconds")

    def client(self) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self.token}"}
        profiles = httpx.get(
            f"{self.base_url}/v1/installation-profiles",
            headers=headers,
            timeout=10,
        )
        if profiles.status_code == 200:
            active_profile_id = profiles.json().get("active_profile_id")
            if isinstance(active_profile_id, str) and active_profile_id:
                headers["X-JobOS-Profile-Id"] = active_profile_id
        return httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=10,
        )

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
            path = self.root / "logs" / f"api-{self._label}.log"
            path.write_text(redact(path.read_text(encoding="utf-8")), encoding="utf-8")
        self.process = None


@pytest.fixture
def clean_runtime(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    (root / "logs").mkdir(mode=0o700)
    environment = isolated_environment(root)
    yield root, environment

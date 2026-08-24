"""Bounded, credential-isolated runner for packaged-host acceptance commands."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

SENSITIVE_ENV = re.compile(
    r"(?:AUTH|TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY|ACCESS_KEY|PRIVATE_KEY)", re.I
)
MIN_LISTENER_AUDIT_SECONDS = 0.05


class PackagedHostError(AssertionError):
    pass


@dataclass(frozen=True)
class PackagedHostResult:
    returncode: int
    stdout: str
    stderr: str
    home: Path
    tmpdir: Path
    codex_home: Path
    listeners: tuple[str, ...]
    listener_audit_count: int
    listener_audit_seconds: float


def isolated_host_environment(root: Path) -> dict[str, str]:
    """Build a minimal environment without inherited provider/auth material."""

    keep = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SHELL", "LANG", "LC_ALL", "SYSTEMROOT"}
        and not SENSITIVE_ENV.search(key)
    }
    directories = {
        "HOME": root / "home",
        "TMPDIR": root / "tmp",
        "CODEX_HOME": root / "codex-home",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    keep.update({key: str(path) for key, path in directories.items()})
    keep["PYTHONNOUSERSITE"] = "1"
    keep["PYTHONDONTWRITEBYTECODE"] = "1"
    return keep


def _process_group_pids(process_group_id: int) -> set[int]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,pgid="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PackagedHostError("process_group_audit_unavailable")
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) != 2:
            continue
        pid, process_group = (int(value) for value in columns)
        if process_group == process_group_id:
            pids.add(pid)
    return pids


def _listeners(process_group_id: int) -> tuple[str, ...]:
    if shutil.which("lsof") is None:
        raise PackagedHostError("listener_audit_unavailable")
    pids = _process_group_pids(process_group_id)
    if not pids:
        return ()
    result = subprocess.run(
        [
            "lsof",
            "-nP",
            "-a",
            "-p",
            ",".join(str(pid) for pid in sorted(pids)),
            "-iTCP",
            "-sTCP:LISTEN",
            "-Fn",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1} or (result.returncode == 1 and result.stderr.strip()):
        raise PackagedHostError("listener_audit_failed")
    return tuple(sorted(line[1:] for line in result.stdout.splitlines() if line.startswith("n")))


def _terminate_group(process: subprocess.Popen[str]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)


def run_packaged_host(
    command: list[str],
    *,
    root: Path,
    timeout: float = 5.0,
    allowed_listeners: tuple[str, ...] = (),
) -> PackagedHostResult:
    environment = isolated_host_environment(root)
    process = subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    observed: set[str] = set()
    listener_audit_count = 0
    started = time.monotonic()
    deadline = started + timeout
    try:
        while True:
            if time.monotonic() >= deadline:
                raise PackagedHostError("packaged_host_timeout")
            observed.update(_listeners(process.pid))
            listener_audit_count += 1
            unexpected = {
                listener
                for listener in observed
                if not any(re.fullmatch(pattern, listener) for pattern in allowed_listeners)
            }
            if unexpected:
                raise PackagedHostError("unexpected_listener_opened")
            if process.poll() is not None:
                break
            time.sleep(0.01)
        listener_audit_seconds = time.monotonic() - started
        stdout, stderr = process.communicate(timeout=1)
        if process.returncode != 0:
            raise PackagedHostError("packaged_host_failed")
        if listener_audit_count < 2 or listener_audit_seconds < MIN_LISTENER_AUDIT_SECONDS:
            raise PackagedHostError("insufficient_listener_audit_window")
        survivors = _process_group_pids(process.pid) - {process.pid}
        if survivors:
            raise PackagedHostError("packaged_host_descendant_survived")
    except Exception:
        _terminate_group(process)
        raise
    return PackagedHostResult(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        home=Path(environment["HOME"]),
        tmpdir=Path(environment["TMPDIR"]),
        codex_home=Path(environment["CODEX_HOME"]),
        listeners=tuple(sorted(observed)),
        listener_audit_count=listener_audit_count,
        listener_audit_seconds=listener_audit_seconds,
    )

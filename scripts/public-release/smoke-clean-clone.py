#!/usr/bin/env python3
"""Prove the public JobOS source path from an exact, detached Git revision."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NODE_VERSION = "v26.5.0"
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


def redacted(value: str) -> str:
    for pattern, replacement in REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def base_environment(home: Path) -> dict[str, str]:
    platform_keys = (
        "USER",
        "LOGNAME",
        "__CF_USER_TEXT_ENCODING",
        "SECURITYSESSIONID",
    ) if sys.platform == "darwin" else ()
    allowed = {
        key: os.environ[key]
        for key in ("PATH", "SHELL", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", *platform_keys)
        if os.environ.get(key)
    }
    allowed.update(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CI": "1",
        }
    )
    return allowed


def run(
    arguments: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: float,
    log: Path,
) -> None:
    started = time.monotonic()
    process = subprocess.Popen(
            arguments,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    try:
        output, _ = process.communicate(timeout=max(1, timeout))
    except subprocess.TimeoutExpired as error:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            output = ""
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            killed_output, _ = process.communicate(timeout=5)
            output += killed_output
        log.write_text(redacted(output) + "\ncommand timed out\n", encoding="utf-8")
        raise RuntimeError(f"command timed out after {timeout:.0f}s: {arguments[0]}") from error
    log.write_text(redacted(output), encoding="utf-8")
    if process.returncode:
        raise RuntimeError(
            f"command failed ({process.returncode}, {time.monotonic() - started:.1f}s): "
            f"{arguments[0]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--keep-on-failure", action="store_true")
    args = parser.parse_args()
    if args.timeout < 60:
        parser.error("--timeout must be at least 60 seconds")
    if sys.platform not in {"darwin", "linux"}:
        parser.error("the clean-clone proof supports macOS and the Linux source subset only")

    source = args.source.expanduser().resolve(strict=True)
    root = Path(tempfile.mkdtemp(prefix="jobos-clean-clone-"))
    clone = root / "checkout"
    logs = root / "logs"
    home = root / "home"
    logs.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    environment = base_environment(home)
    deadline = time.monotonic() + args.timeout

    def remaining(limit: int) -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise RuntimeError("overall clean-clone timeout expired")
        return min(value, limit)

    try:
        resolved = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--verify", f"{args.ref}^{{commit}}"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", resolved):
            raise RuntimeError("ref did not resolve to a full Git commit")
        run(
            ["git", "clone", "--no-local", "--no-checkout", str(source), str(clone)],
            cwd=root,
            environment=environment,
            timeout=remaining(120),
            log=logs / "clone.log",
        )
        run(
            ["git", "checkout", "--detach", resolved],
            cwd=clone,
            environment=environment,
            timeout=remaining(30),
            log=logs / "checkout.log",
        )
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=clone,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        if actual != resolved:
            raise RuntimeError("fresh checkout does not match the requested exact revision")
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=clone,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
        if status:
            raise RuntimeError("fresh checkout contains unexpected changes")
        node = subprocess.run(
            ["node", "--version"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        if node != NODE_VERSION:
            raise RuntimeError(f"Node {NODE_VERSION} is required; found {node}")

        run(
            ["pnpm", "install", "--frozen-lockfile"],
            cwd=clone,
            environment=environment,
            timeout=remaining(300),
            log=logs / "pnpm-install.log",
        )
        run(
            ["uv", "sync", "--all-packages", "--frozen"],
            cwd=clone,
            environment=environment,
            timeout=remaining(300),
            log=logs / "uv-sync.log",
        )
        if sys.platform == "darwin":
            run(
                ["pnpm", "build"],
                cwd=clone,
                environment=environment,
                timeout=remaining(180),
                log=logs / "desktop-build.log",
            )
        runtime_environment = {
            **environment,
            "JOBOS_CLEAN_CLONE": "1",
            "JOBOS_CLEAN_CLONE_PLATFORM": "complete" if sys.platform == "darwin" else "source",
        }
        run(
            [
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "pytest",
                "-p",
                "no:cacheprovider",
                "tests/public-release/test_clean_clone.py",
            ],
            cwd=clone,
            environment=runtime_environment,
            timeout=remaining(120),
            log=logs / "golden-path.log",
        )
        result = {"status": "passed", "commit": resolved, "platform": sys.platform}
        (logs / "result.json").write_text(
            json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        failure = {"status": "failed", "error": redacted(str(error)), "platform": sys.platform}
        (logs / "result.json").write_text(
            json.dumps(failure, sort_keys=True) + "\n", encoding="utf-8"
        )
        if args.keep_on_failure:
            print(f"clean-clone proof failed; retained redacted logs at {logs}", file=sys.stderr)
        else:
            print("clean-clone proof failed; sanitized diagnostics follow", file=sys.stderr)
        for log in sorted(logs.glob("*.log")):
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
            if tail:
                print(f"[{log.name}]", file=sys.stderr)
                print(redacted("\n".join(tail)), file=sys.stderr)
        if not args.keep_on_failure:
            shutil.rmtree(root)
        return 1
    else:
        print(json.dumps(result, sort_keys=True))
        shutil.rmtree(root)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

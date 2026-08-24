#!/usr/bin/env python3
"""Run and record the Phase 0 regression baseline without retaining command output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/acceptance/connected-agents/phase-0/baseline-manifest.json"
DEFAULT_OUTPUT = ROOT / "docs/acceptance/connected-agents/phase-0/verification-results.json"


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def code_snapshot_sha256(excluded: Path = DEFAULT_OUTPUT) -> str:
    """Hash current tracked contents while excluding the self-referential receipt."""

    excluded_relative = str(excluded.resolve().relative_to(ROOT))
    tracked = _git("ls-files", "-z").split("\0")
    digest = hashlib.sha256()
    for relative_path in sorted(path for path in tracked if path and path != excluded_relative):
        path = ROOT / relative_path
        digest.update(relative_path.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"(missing)")
        digest.update(b"\0")
    return digest.hexdigest()


def checkout_receipt(output: Path) -> dict[str, object]:
    staged_diff = _git("diff", "--cached", "--binary")
    working_diff = _git("diff", "--binary")
    untracked = [
        path
        for path in _git("ls-files", "--others", "--exclude-standard").splitlines()
        if path
    ]
    return {
        "head_at_execution": _git("rev-parse", "HEAD"),
        "head_tree_at_execution": _git("rev-parse", "HEAD^{tree}"),
        "index_tree_at_execution": _git("write-tree"),
        "staged_diff_sha256": hashlib.sha256(staged_diff.encode()).hexdigest(),
        "working_diff_sha256": hashlib.sha256(working_diff.encode()).hexdigest(),
        "untracked_paths": untracked,
        "code_snapshot_sha256": code_snapshot_sha256(output),
        "snapshot_excludes": str(output.resolve().relative_to(ROOT)),
    }


def isolated_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SHELL", "LANG", "LC_ALL", "SYSTEMROOT"}
    }
    environment.update(
        {
            "JOBOS_DEVICE_TOKEN": "(FAKE)-phase0-device-token",
            "JOBOS_MCP_TOKEN": "(FAKE)-phase0-mcp-token",
            "JOBOS_JOB_PROVIDER": "sqlite",
            "JOBOS_ARTIFACT_PROVIDER": "local",
            "JOBOS_PHASE0_RECORDING": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _version(command: list[str], environment: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return (result.stdout or result.stderr).strip()


def _test_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in ("passed", "skipped", "failed"):
        matches = re.findall(rf"(\d+) {label}", output)
        if matches:
            counts[label] = sum(int(value) for value in matches)
    node_pass = re.findall(r"(?:^|\n)[ℹ#]?\s*pass\s+(\d+)", output)
    node_fail = re.findall(r"(?:^|\n)[ℹ#]?\s*fail\s+(\d+)", output)
    if node_pass:
        counts["node_pass"] = sum(int(value) for value in node_pass)
    if node_fail:
        counts["node_fail"] = sum(int(value) for value in node_fail)
    return counts


def run_command(command: str, environment: dict[str, str]) -> dict[str, object]:
    started = datetime.now(UTC)
    result = subprocess.run(
        shlex.split(command),
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    return {
        "command": command,
        "exit_code": result.returncode,
        "result": "passed" if result.returncode == 0 else "failed",
        "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
        "test_counts": _test_counts(output),
        "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
    }


def installed_hermes_smoke() -> dict[str, object]:
    """Read only the installed control's safe health and conversation state."""

    try:
        import httpx
        from jobos_api.local_config import default_data_dir, settings_from_config

        data_dir = default_data_dir()
        settings = settings_from_config(data_dir / "config.json")
        runtime = json.loads((data_dir / "service/runtime.json").read_text(encoding="utf-8"))
        base_url = f"http://{runtime['host']}:{runtime['port']}"
        headers = {
            "Authorization": f"Bearer {settings.device_token}",
            "X-JobOS-Profile-Id": settings.installation_profile_id,
        }
        health_response = httpx.get(f"{base_url}/v1/health", headers=headers, timeout=5)
        conversations_response = httpx.get(
            f"{base_url}/v1/conversations", headers=headers, timeout=5
        )
        body = conversations_response.json()
        conversations = (
            body
            if isinstance(body, list)
            else body.get("items", body.get("conversations", []))
        )
        app = subprocess.run(
            ["pgrep", "-f", "/Applications/JobOS.app/Contents/MacOS/JobOS"],
            check=False,
            capture_output=True,
            text=True,
        )
        health = health_response.json()
        passed = (
            health_response.status_code == 200
            and health.get("status") == "ready"
            and health.get("agent") == "online"
            and conversations_response.status_code == 200
            and all(item.get("recovery_state") == "ready" for item in conversations)
            and not any(item.get("active_turn_id") for item in conversations)
            and app.returncode == 0
        )
        return {
            "mode": "authenticated_read_only",
            "result": "passed" if passed else "failed",
            "health_status": health_response.status_code,
            "service_status": health.get("status"),
            "agent_status": health.get("agent"),
            "state_schema": health.get("state_schema"),
            "transport": health.get("transport"),
            "conversations_status": conversations_response.status_code,
            "conversation_count": len(conversations),
            "all_recovery_ready": all(
                item.get("recovery_state") == "ready" for item in conversations
            ),
            "active_turn_count": sum(
                bool(item.get("active_turn_id")) for item in conversations
            ),
            "installed_app_running": app.returncode == 0,
            "mutation_performed": False,
        }
    except Exception as error:  # fail closed while keeping secrets and paths out of evidence
        return {
            "mode": "authenticated_read_only",
            "result": "failed",
            "error_type": type(error).__name__,
            "mutation_performed": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--include-full-gates", action="store_true")
    parser.add_argument("--installed-smoke", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    selected = [
        item
        for item in manifest["reg_01_command_map"]
        if args.include_full_gates or not item["execution"].startswith("reserved_")
    ]
    environment = isolated_environment()
    commands = [run_command(item["command"], environment) for item in selected]
    receipt = {
        "schema_version": 1,
        "acceptance_phase": 0,
        "acceptance_ids": ["REG-01"],
        "executed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_baseline_commit": manifest["source"]["commit"],
        "checkout": checkout_receipt(args.output),
        "environment": {
            "platform": f"{platform.system()} {platform.machine()}",
            "node": _version(["node", "--version"], environment),
            "pnpm": _version(["pnpm", "--version"], environment),
            "uv": _version(["uv", "--version"], environment),
            "python": platform.python_version(),
        },
        "commands": commands,
        "installed_hermes_control": installed_hermes_smoke() if args.installed_smoke else None,
        "all_passed": all(item["result"] == "passed" for item in commands),
        "credentials_recorded": False,
        "raw_command_output_recorded": False,
    }
    if receipt["installed_hermes_control"] is not None:
        receipt["all_passed"] = receipt["all_passed"] and (
            receipt["installed_hermes_control"]["result"] == "passed"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if receipt["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

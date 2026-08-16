from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).parents[2]
SMOKE = load_module("jobos_smoke_clean_clone", ROOT / "scripts/public-release/smoke-clean-clone.py")
SUPPORT = load_module("jobos_public_release_support_unit", ROOT / "tests/public_release_support.py")


@pytest.mark.parametrize(
    ("value", "secret_fragments"),
    [
        ("JOBOS_MCP_TOKEN=sensitive-value-123", ("sensitive-value-123",)),
        ("JOBOS_DEVICE_TOKEN='sensitive-value-123'", ("sensitive-value-123",)),
        ('"mcp_token": "sensitive-value-123"', ("sensitive-value-123",)),
        ('"deviceToken": "sensitive-value-123"', ("sensitive-value-123",)),
        ("Authorization: Bearer sensitive-value-123", ("sensitive-value-123",)),
        ('"Authorization": "Bearer sensitive-value-123"', ("sensitive-value-123",)),
        ("authorization=\"sensitive-value-123\"", ("sensitive-value-123",)),
        ("Authorization: Basic basic-credential-456", ("basic-credential-456",)),
        ("Authorization: Token token-credential-789", ("token-credential-789",)),
        (
            "Authorization: AWS4-HMAC-SHA256 Credential=aws-credential, "
            "SignedHeaders=host, Signature=aws-signature",
            ("aws-credential", "aws-signature"),
        ),
        (
            '"Authorization": "Digest username=\\"Mufasa\\", '
            'realm=\\"testrealm\\", response=\\"digest-response\\""',
            ("Mufasa", "testrealm", "digest-response"),
        ),
        (
            "Authorization: Digest username=Mufasa,\n response=folded-response",
            ("Mufasa", "folded-response"),
        ),
        (
            '"Authorization": "Digest username=Mufasa,\nresponse=malformed-response',
            ("Mufasa", "malformed-response"),
        ),
        (
            '"Authorization": "Digest username=Mufasa,\\\nresponse=escaped-lf-response',
            ("Mufasa", "escaped-lf-response"),
        ),
        (
            '"Authorization": "Digest username=Mufasa,\\\r\nresponse=escaped-crlf-response',
            ("Mufasa", "escaped-crlf-response"),
        ),
    ],
)
@pytest.mark.parametrize("redactor", [SMOKE.redacted, SUPPORT.redact])
def test_release_log_redaction_covers_token_and_authorization_formats(
    value, secret_fragments, redactor
):
    result = redactor(value)
    for secret_fragment in secret_fragments:
        assert secret_fragment not in result
    assert "[REDACTED]" in result


def test_run_terminates_the_entire_process_group_on_timeout(tmp_path: Path):
    child_pid_path = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    with pytest.raises(RuntimeError, match="command timed out"):
        SMOKE.run(
            [sys.executable, "-c", script, str(child_pid_path)],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout=0.1,
            log=tmp_path / "timeout.log",
        )
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("descendant process survived the timeout cleanup")


def test_run_kills_sigterm_resistant_descendant_after_parent_exits(tmp_path: Path):
    child_pid_path = tmp_path / "resistant-child.pid"
    child_ready_path = tmp_path / "resistant-child.ready"
    child_script = (
        "import pathlib, signal, sys, time; "
        "assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN; "
        "pathlib.Path(sys.argv[1]).write_text('ready'); "
        "time.sleep(60)"
    )
    parent_script = (
        "import pathlib, signal, subprocess, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "child=subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[3]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "ready=pathlib.Path(sys.argv[3]); "
        "deadline=time.monotonic()+5; "
        "exec(\"while not ready.exists() and time.monotonic() < deadline:\\n time.sleep(0.01)\"); "
        "assert ready.exists(); "
        "signal.signal(signal.SIGTERM, signal.SIG_DFL); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    with pytest.raises(RuntimeError, match="command timed out"):
        SMOKE.run(
            [
                sys.executable,
                "-c",
                parent_script,
                str(child_pid_path),
                child_script,
                str(child_ready_path),
            ],
            cwd=tmp_path,
            environment=dict(os.environ),
            timeout=2,
            log=tmp_path / "resistant-timeout.log",
        )
    assert child_ready_path.read_text(encoding="utf-8") == "ready"
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("SIGTERM-resistant descendant survived timeout cleanup")


@pytest.mark.parametrize("keep_on_failure", [False, True])
def test_failure_artifacts_are_retained_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep_on_failure: bool
):
    temporary_root = tmp_path / ("retained" if keep_on_failure else "removed")

    def make_temporary_root(prefix: str) -> str:
        assert prefix == "jobos-clean-clone-"
        temporary_root.mkdir(mode=0o700)
        return str(temporary_root)

    monkeypatch.setattr(SMOKE.tempfile, "mkdtemp", make_temporary_root)
    arguments = [
        "smoke-clean-clone.py",
        "--source",
        str(ROOT),
        "--ref",
        "refs/heads/definitely-missing-clean-clone-ref",
    ]
    if keep_on_failure:
        arguments.append("--keep-on-failure")
    monkeypatch.setattr(sys, "argv", arguments)

    assert SMOKE.main() == 1
    assert temporary_root.exists() is keep_on_failure

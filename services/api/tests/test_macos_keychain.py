import os
import secrets
import subprocess
import sys

import pytest
from jobos_api import macos_keychain
from jobos_api.macos_keychain import (
    delete_keychain_secret,
    read_keychain_secret,
    store_keychain_secret,
)


def test_store_passes_secret_only_on_stdin(monkeypatch, tmp_path):
    helper = tmp_path / "jobos-keychain"
    helper.write_text("helper")
    helper.chmod(0o700)
    monkeypatch.setenv("JOBOS_KEYCHAIN_HELPER_PATH", str(helper))
    token = "credential-that-must-not-appear-in-argv"
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(macos_keychain.subprocess, "run", run)

    store_keychain_secret("com.cobibean.jobos.device-token", "mini", token)

    command, options = calls[0]
    assert command == [
        str(helper),
        "set",
        "com.cobibean.jobos.device-token",
        "mini",
    ]
    assert token not in " ".join(command)
    assert options["input"] == token.encode()
    assert options["capture_output"] is True
    assert options["timeout"] == 10


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("JOBOS_RUN_KEYCHAIN_INTEGRATION") != "1",
    reason="explicit macOS Keychain integration",
)
def test_keychain_round_trip_does_not_require_secret_command_arguments():
    service = "com.cobibean.jobos.test-native-keychain"
    account = f"pytest-{secrets.token_hex(8)}"
    original = secrets.token_hex(32)
    replacement = secrets.token_hex(32)

    try:
        assert read_keychain_secret(service, account) is None
        store_keychain_secret(service, account, original)
        assert read_keychain_secret(service, account) == original
        store_keychain_secret(service, account, replacement)
        assert read_keychain_secret(service, account) == replacement
    finally:
        delete_keychain_secret(service, account)

    assert read_keychain_secret(service, account) is None

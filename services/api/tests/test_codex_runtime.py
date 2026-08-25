from __future__ import annotations

import json
import os
import stat
import tomllib
from pathlib import Path

import pytest
from jobos_api.codex_runtime import (
    CODEX_CONFIG,
    CodexAppServerProcess,
    CodexRpcError,
    CodexRuntimeError,
    prepare_codex_home,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_prepare_codex_home_is_keyring_only_and_private(tmp_path: Path) -> None:
    codex_home = tmp_path / "jobos" / "codex-home"

    prepare_codex_home(codex_home, standalone_home=tmp_path / "standalone")

    config = codex_home / "config.toml"
    assert config.read_text(encoding="utf-8") == CODEX_CONFIG
    assert stat.S_IMODE(codex_home.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert not (codex_home / "auth.json").exists()


def test_prepare_codex_home_rejects_standalone_plaintext_and_symlink(tmp_path: Path) -> None:
    standalone = tmp_path / ".codex"
    with pytest.raises(CodexRuntimeError, match="isolated"):
        prepare_codex_home(standalone, standalone_home=standalone)

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "auth.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CodexRuntimeError, match="Plaintext"):
        prepare_codex_home(unsafe, standalone_home=standalone)

    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(CodexRuntimeError, match="unsafe"):
        prepare_codex_home(alias, standalone_home=standalone)


def test_prepare_codex_home_configures_only_the_trusted_jobos_mcp(tmp_path: Path) -> None:
    codex_home = tmp_path / "jobos" / "codex-home"
    launcher = tmp_path / "jobos-mcp-launcher"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o700)

    prepare_codex_home(codex_home, standalone_home=tmp_path / "standalone")
    prepare_codex_home(
        codex_home,
        standalone_home=tmp_path / "standalone",
        mcp_command=launcher,
        mcp_args=("--runtime", "(FAKE)-runtime.json"),
    )

    config = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert config.startswith(CODEX_CONFIG)
    assert "[mcp_servers.jobos]" in config
    assert f'command = "{launcher}"' in config
    assert 'args = ["--runtime", "(FAKE)-runtime.json"]' in config
    assert "token" not in config.lower()

    with pytest.raises(CodexRuntimeError, match="tools unavailable"):
        prepare_codex_home(
            tmp_path / "missing-home",
            standalone_home=tmp_path / "standalone",
            mcp_command=tmp_path / "missing-launcher",
        )


def test_prepare_codex_home_updates_previous_jobos_owned_mcp_config(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    old_launcher = tmp_path / "old-launcher"
    new_launcher = tmp_path / "new-launcher"
    for launcher in (old_launcher, new_launcher):
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o700)

    prepare_codex_home(codex_home, mcp_command=old_launcher, mcp_args=("old",))
    prepare_codex_home(codex_home, mcp_command=new_launcher, mcp_args=("new",))

    config = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert str(new_launcher) in config
    assert str(old_launcher) not in config
    assert 'args = ["new"]' in config


def test_prepare_codex_home_preserves_live_config_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    codex_home = tmp_path / "codex-home"
    old_launcher = tmp_path / "old-launcher"
    new_launcher = tmp_path / "new-launcher"
    for launcher in (old_launcher, new_launcher):
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o700)
    prepare_codex_home(codex_home, mcp_command=old_launcher, mcp_args=("old",))
    original = (codex_home / "config.toml").read_bytes()

    def fail_replace(_source, _destination) -> None:
        raise OSError("(FAKE) interrupted replacement")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(CodexRuntimeError, match="configuration is unavailable"):
        prepare_codex_home(codex_home, mcp_command=new_launcher, mcp_args=("new",))

    assert (codex_home / "config.toml").read_bytes() == original
    assert list(codex_home.glob(".config.toml.*")) == []


def test_prepare_codex_home_writes_valid_unicode_toml(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    launcher = tmp_path / "jobos-\U0001F525-launcher"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o700)

    prepare_codex_home(codex_home, mcp_command=launcher, mcp_args=("--label", "\U0001F525"))

    parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["jobos"]["command"] == str(launcher)
    assert parsed["mcp_servers"]["jobos"]["args"] == ["--label", "\U0001F525"]


@pytest.mark.anyio
async def test_stdio_supervisor_initializes_and_uses_dedicated_home(tmp_path: Path) -> None:
    requests_path = tmp_path / "requests.jsonl"
    server = tmp_path / "fake-codex-app-server"
    server.write_text(
        f"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

output = Path({str(requests_path)!r})
for line in sys.stdin:
    message = json.loads(line)
    with output.open("a", encoding="utf-8") as stream:
        record = {{
            "message": message,
            "codex_home": os.environ["CODEX_HOME"],
            "device_id": os.environ.get("JOBOS_DEVICE_ID"),
        }}
        stream.write(json.dumps(record) + "\\n")
    if "id" not in message:
        continue
    if message["method"] == "initialize":
        result = {{"serverInfo": {{"name": "fake"}}}}
    else:
        result = {{"data": []}}
    print(json.dumps({{"id": message["id"], "result": result}}), flush=True)
""",
        encoding="utf-8",
    )
    server.chmod(0o700)
    codex_home = tmp_path / "dedicated-codex-home"
    client = CodexAppServerProcess(
        server,
        codex_home,
        request_timeout=2,
        verify_binary=lambda _: None,
        mcp_device_id="(FAKE)-device",
    )
    try:
        await client.start()
        assert await client.request("model/list", {"includeHidden": False}) == {"data": []}
    finally:
        await client.close()

    records = [json.loads(line) for line in requests_path.read_text().splitlines()]
    assert [record["message"]["method"] for record in records] == [
        "initialize",
        "initialized",
        "model/list",
    ]
    assert {record["codex_home"] for record in records} == {str(codex_home)}
    assert {record["device_id"] for record in records} == {"(FAKE)-device"}


@pytest.mark.anyio
async def test_stdio_supervisor_maps_rpc_rejection_without_leaking_message(tmp_path: Path) -> None:
    server = tmp_path / "fake-codex-app-server"
    server.write_text(
        """#!/usr/bin/env python3
import json
import sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message["method"] == "initialize":
        result = {"serverInfo": {"name": "fake"}}
        print(json.dumps({"id": message["id"], "result": result}), flush=True)
    else:
        error = {"code": -32601, "message": "private upstream detail"}
        print(json.dumps({"id": message["id"], "error": error}), flush=True)
""",
        encoding="utf-8",
    )
    server.chmod(0o700)
    client = CodexAppServerProcess(
        server,
        tmp_path / "codex-home",
        request_timeout=2,
        verify_binary=lambda _: None,
    )
    try:
        await client.start()
        with pytest.raises(CodexRpcError, match="rejected") as captured:
            await client.request("model/list")
        assert "private upstream detail" not in str(captured.value)
        assert captured.value.rpc_code == -32601
    finally:
        await client.close()


@pytest.mark.anyio
async def test_stdio_supervisor_fails_closed_when_runtime_crashes(tmp_path: Path) -> None:
    server = tmp_path / "fake-codex-app-server"
    server.write_text(
        """#!/usr/bin/env python3
import json
import sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        print(json.dumps({"id": message["id"], "result": {"serverInfo": {}}}), flush=True)
    elif message.get("method") == "initialized":
        continue
    else:
        raise SystemExit(7)
""",
        encoding="utf-8",
    )
    server.chmod(0o700)
    client = CodexAppServerProcess(
        server,
        tmp_path / "codex-home",
        request_timeout=1.0,
        verify_binary=lambda _: None,
    )
    try:
        await client.start()
        with pytest.raises(CodexRuntimeError, match="timed out|stopped"):
            await client.request("model/list")
    finally:
        await client.close()

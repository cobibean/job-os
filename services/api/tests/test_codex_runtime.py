from __future__ import annotations

import json
import stat
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
        record = {{"message": message, "codex_home": os.environ["CODEX_HOME"]}}
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
        request_timeout=0.25,
        verify_binary=lambda _: None,
    )
    try:
        await client.start()
        with pytest.raises(CodexRuntimeError, match="timed out|stopped"):
            await client.request("model/list")
    finally:
        await client.close()

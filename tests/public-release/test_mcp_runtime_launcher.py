from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = REPOSITORY_ROOT / "scripts/macos/jobos_mcp_runtime.py"


def load_launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("jobos_mcp_runtime_tested", LAUNCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


jobos_mcp_runtime = load_launcher()


def runtime_files(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    release = tmp_path / "releases/release-new"
    mcp_package = release / "services/mcp/jobos_mcp"
    mcp_package.mkdir(parents=True)
    (mcp_package / "__init__.py").write_text("", encoding="utf-8")
    (mcp_package / "__main__.py").write_text("", encoding="utf-8")
    (mcp_package / "server.py").write_text("", encoding="utf-8")
    (mcp_package / "jobs.py").write_text("", encoding="utf-8")
    python_path = tmp_path / "shared-venv/bin/python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("python", encoding="utf-8")
    runtime: dict[str, object] = {
        "jobos_root": str(release),
        "python_path": str(python_path),
        "host": "127.0.0.1",
        "port": 8766,
    }
    credentials: dict[str, object] = {
        "device_token": "device-secret-value",
        "mcp_token": "mcp-secret-value",
    }
    return runtime, credentials


def test_mcp_runtime_launch_pins_imports_to_the_api_release(tmp_path):
    runtime, credentials = runtime_files(tmp_path)
    mutable_checkout = tmp_path / "mutable-checkout/services/mcp"
    mutable_checkout.mkdir(parents=True)

    launch = jobos_mcp_runtime.build_mcp_runtime_launch(
        runtime,
        credentials,
        base_environment={"PYTHONPATH": str(mutable_checkout), "PATH": "/usr/bin:/bin"},
    )

    release = Path(str(runtime["jobos_root"]))
    assert launch.arguments == (
        str(runtime["python_path"]),
        "-P",
        "-m",
        "jobos_mcp",
    )
    assert launch.environment["PYTHONPATH"] == str(release / "services/mcp")
    assert launch.environment["JOBOS_RUNTIME_ROOT"] == str(release)
    assert launch.environment["JOBOS_API_BASE_URL"] == "http://127.0.0.1:8766"
    assert mutable_checkout.as_posix() not in launch.environment["PYTHONPATH"]


def test_mcp_runtime_rejects_missing_release_source_and_non_loopback_api(tmp_path):
    runtime, credentials = runtime_files(tmp_path)
    release = Path(str(runtime["jobos_root"]))
    mcp_package = release / "services/mcp/jobos_mcp"
    (mcp_package / "__init__.py").unlink()
    (mcp_package / "__main__.py").unlink()
    (mcp_package / "server.py").unlink()
    (mcp_package / "jobs.py").unlink()
    mcp_package.rmdir()

    with pytest.raises(RuntimeError, match="release MCP package"):
        jobos_mcp_runtime.build_mcp_runtime_launch(runtime, credentials)

    mcp_package.mkdir()
    with pytest.raises(RuntimeError, match="release MCP package marker"):
        jobos_mcp_runtime.build_mcp_runtime_launch(runtime, credentials)

    (mcp_package / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="release MCP entry point"):
        jobos_mcp_runtime.build_mcp_runtime_launch(runtime, credentials)

    (mcp_package / "__main__.py").write_text("", encoding="utf-8")
    (mcp_package / "server.py").write_text("", encoding="utf-8")
    (mcp_package / "jobs.py").write_text("", encoding="utf-8")
    runtime["host"] = "0.0.0.0"
    with pytest.raises(RuntimeError, match="loopback"):
        jobos_mcp_runtime.build_mcp_runtime_launch(runtime, credentials)


def test_mcp_runtime_rejects_release_package_symlinks(tmp_path):
    runtime, credentials = runtime_files(tmp_path)
    release = Path(str(runtime["jobos_root"]))
    mcp_package = release / "services/mcp/jobos_mcp"
    external_package = tmp_path / "mutable/jobos_mcp"
    external_package.mkdir(parents=True)
    (external_package / "__init__.py").write_text("", encoding="utf-8")
    (external_package / "__main__.py").write_text("", encoding="utf-8")
    (external_package / "server.py").write_text("", encoding="utf-8")
    (external_package / "jobs.py").write_text("", encoding="utf-8")

    (mcp_package / "__init__.py").unlink()
    (mcp_package / "__main__.py").unlink()
    (mcp_package / "server.py").unlink()
    (mcp_package / "jobs.py").unlink()
    mcp_package.rmdir()
    mcp_package.symlink_to(external_package, target_is_directory=True)

    with pytest.raises(RuntimeError, match="must stay inside the runtime release"):
        jobos_mcp_runtime.build_mcp_runtime_launch(runtime, credentials)

    mcp_package.unlink()
    mcp_package.mkdir()
    for name in ("__init__.py", "__main__.py", "jobs.py"):
        (mcp_package / name).write_text("", encoding="utf-8")
    (mcp_package / "server.py").symlink_to(external_package / "server.py")

    with pytest.raises(RuntimeError, match="must stay inside the runtime release"):
        jobos_mcp_runtime.build_mcp_runtime_launch(runtime, credentials)


def test_mcp_runtime_main_executes_the_release_module(tmp_path, monkeypatch):
    runtime, credentials = runtime_files(tmp_path)
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    captured: dict[str, object] = {}

    def capture_execve(executable, arguments, environment):
        captured.update(
            executable=executable,
            arguments=arguments,
            environment=environment,
        )

    monkeypatch.setattr(jobos_mcp_runtime.os, "execve", capture_execve)
    monkeypatch.setattr(
        jobos_mcp_runtime,
        "keychain_credentials",
        lambda _runtime: credentials,
    )
    jobos_mcp_runtime.main([str(runtime_path)])

    assert captured["executable"] == str(runtime["python_path"])
    assert captured["arguments"] == [
        str(runtime["python_path"]),
        "-P",
        "-m",
        "jobos_mcp",
    ]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["PYTHONPATH"] == str(Path(str(runtime["jobos_root"])) / "services/mcp")


def test_mcp_runtime_main_accepts_the_legacy_profile_credentials_argument(
    tmp_path, monkeypatch
):
    runtime, credentials = runtime_files(tmp_path)
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    legacy_credentials_path = tmp_path / "demo-credentials.json"
    legacy_credentials_path.write_text("deliberately unreadable", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        jobos_mcp_runtime,
        "keychain_credentials",
        lambda _runtime: credentials,
    )
    monkeypatch.setattr(
        jobos_mcp_runtime.os,
        "execve",
        lambda executable, arguments, environment: captured.update(
            executable=executable,
            arguments=arguments,
            environment=environment,
        ),
    )

    jobos_mcp_runtime.main([str(runtime_path), str(legacy_credentials_path)])

    assert captured["executable"] == str(runtime["python_path"])
    assert captured["arguments"] == [
        str(runtime["python_path"]),
        "-P",
        "-m",
        "jobos_mcp",
    ]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["JOBOS_DEVICE_TOKEN"] == "device-secret-value"


def test_mcp_runtime_reads_credentials_from_keychain_without_a_credentials_file(
    tmp_path, monkeypatch
):
    runtime, _ = runtime_files(tmp_path)
    release = Path(str(runtime["jobos_root"]))
    (release / "services/api").mkdir(parents=True)
    helper = tmp_path / "JobOS.app/Contents/Resources/jobos-keychain"
    helper.parent.mkdir(parents=True)
    helper.write_text("synthetic helper", encoding="utf-8")
    runtime.update(
        {
            "device_id": "mini-device",
            "keychain_helper_path": str(helper),
            "keychain_helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        }
    )
    reads = []
    keychain = ModuleType("synthetic_keychain")

    def read_secret(service, account):
        reads.append((service, account))
        return "device-secret-value" if "device-token" in service else "mcp-secret-value"

    keychain.__dict__["read_keychain_secret"] = read_secret
    monkeypatch.setattr(jobos_mcp_runtime.importlib, "import_module", lambda _name: keychain)

    credentials = jobos_mcp_runtime.keychain_credentials(runtime)

    assert credentials == {
        "device_token": "device-secret-value",
        "mcp_token": "mcp-secret-value",
    }
    assert reads == [
        ("com.cobibean.jobos.device-token", "mini-device"),
        ("com.cobibean.jobos.mcp-token", "mini-device"),
    ]
    assert jobos_mcp_runtime.os.environ["JOBOS_KEYCHAIN_HELPER_PATH"] == str(helper)


def test_mcp_runtime_rejects_tampered_keychain_helper_before_reading_secrets(tmp_path):
    runtime, _ = runtime_files(tmp_path)
    release = Path(str(runtime["jobos_root"]))
    (release / "services/api").mkdir(parents=True)
    helper = tmp_path / "JobOS.app/Contents/Resources/jobos-keychain"
    helper.parent.mkdir(parents=True)
    helper.write_text("trusted helper", encoding="utf-8")
    runtime.update(
        {
            "device_id": "mini-device",
            "keychain_helper_path": str(helper),
            "keychain_helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        }
    )
    helper.write_text("tampered helper", encoding="utf-8")

    with pytest.raises(RuntimeError, match="failed integrity verification"):
        jobos_mcp_runtime.keychain_credentials(runtime)


@pytest.mark.anyio
async def test_release_pinned_process_exposes_correlated_browser_snapshot(tmp_path):
    stale_cwd = tmp_path / "stale"
    stale_source = stale_cwd / "jobos_mcp"
    stale_source.mkdir(parents=True)
    (stale_source / "__init__.py").write_text("", encoding="utf-8")
    (stale_source / "__main__.py").write_text(
        "raise RuntimeError('mutable MCP checkout was imported')\n", encoding="utf-8"
    )
    runtime = {
        "jobos_root": str(REPOSITORY_ROOT),
        "python_path": sys.executable,
        "host": "127.0.0.1",
        "port": 8766,
    }
    credentials = {
        "device_token": "test-device-token",
        "mcp_token": "test-mcp-token",
    }
    launch = jobos_mcp_runtime.build_mcp_runtime_launch(
        runtime,
        credentials,
        base_environment={"PYTHONPATH": str(stale_cwd)},
    )
    parameters = StdioServerParameters(
        command=str(launch.executable),
        args=list(launch.arguments[1:]),
        env=launch.environment,
        cwd=stale_cwd,
    )

    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()

    snapshot = next(tool for tool in tools.tools if tool.name == "browser_snapshot")
    schema = snapshot.model_dump(by_alias=True)["inputSchema"]
    assert "conversation_id" in schema["required"]
    assert schema["properties"]["conversation_id"]["pattern"] == (
        "^conv_[A-Za-z0-9_-]{1,128}$"
    )
    assert "text_start" not in schema["required"]
    assert "text_length" not in schema["required"]
    assert "include_targets" not in schema["required"]
    assert schema["properties"]["text_start"]["default"] == 0
    assert schema["properties"]["text_length"]["default"] == 12_000
    assert schema["properties"]["include_targets"]["default"] is True

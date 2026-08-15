import json
import plistlib
from pathlib import Path

import pytest
from jobos_api.macos_runtime import (
    RuntimeServiceConfig,
    authorize_remote_device,
    build_service_environment,
    build_uvicorn_arguments,
    install_runtime,
    launchd_install_commands,
    parse_arguments,
    render_desktop_runtime,
    render_launchd_plist,
    uninstall_runtime,
)


def runtime_mapping(tmp_path: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "label": "com.cobibean.jobos.api",
        "jobos_root": str(tmp_path / "job-os"),
        "python_path": str(tmp_path / "job-os/.venv/bin/python"),
        "facade_source_path": str(tmp_path / "facade/src"),
        "state_db_path": str(tmp_path / "state/jobos.db"),
        "job_hunter_db_path": str(tmp_path / "job-hunter/data/jobs/jobs.db"),
        "artifact_roots": [str(tmp_path / "job-hunter/resume/exports")],
        "hermes_dashboard_url": "ws://127.0.0.1:9119/api/ws",
        "hermes_job_hunter_cwd": str(tmp_path / "job-hunter"),
        "device_id": "mini-device",
        "remote_device_ids": [],
        "host": "127.0.0.1",
        "port": 8766,
    }


def test_runtime_config_accepts_only_explicit_loopback_service_fields(tmp_path):
    config = RuntimeServiceConfig.from_mapping(runtime_mapping(tmp_path))

    assert config.host == "127.0.0.1"
    assert config.port == 8766
    assert config.device_id == "mini-device"
    assert "token" not in repr(config).lower()

    unsafe = runtime_mapping(tmp_path)
    unsafe["host"] = "0.0.0.0"
    with pytest.raises(ValueError, match="loopback"):
        RuntimeServiceConfig.from_mapping(unsafe)

    unknown = runtime_mapping(tmp_path)
    unknown["tailnet_hostname"] = "must-not-be-tracked"
    with pytest.raises(ValueError, match="unknown"):
        RuntimeServiceConfig.from_mapping(unknown)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("label", "attacker.controlled.service", "label"),
        ("python_path", "relative/python", "absolute"),
        ("port", 0, "port"),
        ("device_id", "bad/device", "device"),
        ("hermes_dashboard_url", "http://192.168.1.2:9119", "loopback"),
    ],
)
def test_runtime_config_rejects_unsafe_service_inputs(tmp_path, field, value, message):
    mapping = runtime_mapping(tmp_path)
    mapping[field] = value
    with pytest.raises(ValueError, match=message):
        RuntimeServiceConfig.from_mapping(mapping)


def test_service_environment_and_uvicorn_command_are_fixed_and_loopback_only(tmp_path):
    config = RuntimeServiceConfig.from_mapping(runtime_mapping(tmp_path))
    environment = build_service_environment(
        config,
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        hermes_dashboard_token="hermes-secret-value",
        base_environment={"PATH": "/usr/bin:/bin", "UNRELATED": "omitted"},
    )

    assert environment == {
        "PATH": "/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": (
            f"{tmp_path / 'job-os/services/api'}:{tmp_path / 'facade/src'}"
        ),
        "JOBOS_DEVICE_TOKEN": "device-secret-value",
        "JOBOS_MCP_TOKEN": "mcp-secret-value",
        "JOBOS_DEVICE_ID": "mini-device",
        "JOBOS_STATE_DB_PATH": str(tmp_path / "state/jobos.db"),
        "JOBOS_JOB_PROVIDER": "job-hunter",
        "JOBOS_JOB_HUNTER_DB_PATH": str(tmp_path / "job-hunter/data/jobs/jobs.db"),
        "JOBOS_ARTIFACT_ROOTS": str(tmp_path / "job-hunter/resume/exports"),
        "JOBOS_HERMES_DASHBOARD_URL": "ws://127.0.0.1:9119/api/ws",
        "JOBOS_HERMES_DASHBOARD_TOKEN": "hermes-secret-value",
        "JOBOS_HERMES_JOB_HUNTER_CWD": str(tmp_path / "job-hunter"),
    }
    assert build_uvicorn_arguments(config) == [
        str(tmp_path / "job-os/.venv/bin/python"),
        "-m",
        "uvicorn",
        "jobos_api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8766",
    ]

    remote_environment = build_service_environment(
        config,
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        remote_device_tokens={"macbook-device": "macbook-secret-value"},
        hermes_dashboard_token=None,
        base_environment={},
    )
    assert json.loads(remote_environment["JOBOS_DEVICE_CREDENTIALS_JSON"]) == {
        "macbook-device": "macbook-secret-value"
    }


def test_launchd_plist_and_persisted_configs_never_contain_secrets(tmp_path):
    config = RuntimeServiceConfig.from_mapping(runtime_mapping(tmp_path))
    plist_bytes = render_launchd_plist(
        config,
        config_path=tmp_path / "Application Support/JobOS/service/runtime.json",
        launcher_path=tmp_path / "job-os/scripts/macos/jobos_runtime.py",
        stdout_path=tmp_path / "Application Support/JobOS/logs/api.log",
        stderr_path=tmp_path / "Application Support/JobOS/logs/api.error.log",
    )
    plist = plistlib.loads(plist_bytes)
    serialized = plist_bytes + json.dumps(config.to_mapping()).encode()

    assert plist["Label"] == "com.cobibean.jobos.api"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ThrottleInterval"] == 5
    assert plist["ProgramArguments"] == [
        str(tmp_path / "job-os/.venv/bin/python"),
        str(tmp_path / "job-os/scripts/macos/jobos_runtime.py"),
        "service",
        "--config",
        str(tmp_path / "Application Support/JobOS/service/runtime.json"),
    ]
    assert b"device-secret" not in serialized
    assert b"hermes-secret" not in serialized
    assert "EnvironmentVariables" not in plist

    desktop = render_desktop_runtime(config)
    assert desktop == {
        "schemaVersion": 1,
        "mode": "local-service",
        "apiBaseUrl": "http://127.0.0.1:8766",
        "deviceId": "mini-device",
        "launchdLabel": "com.cobibean.jobos.api",
    }


def test_launchd_install_commands_target_one_per_user_label(tmp_path):
    plist_path = tmp_path / "Library/LaunchAgents/com.cobibean.jobos.api.plist"
    assert launchd_install_commands(501, plist_path, "com.cobibean.jobos.api") == [
        ["/bin/launchctl", "bootout", "gui/501/com.cobibean.jobos.api"],
        ["/bin/launchctl", "bootstrap", "gui/501", str(plist_path)],
        ["/bin/launchctl", "kickstart", "-k", "gui/501/com.cobibean.jobos.api"],
    ]


def test_install_writes_private_configs_provisions_keychain_and_bootstraps_launchd(tmp_path):
    mapping = runtime_mapping(tmp_path)
    for directory in (
        tmp_path / "job-os/services/api/jobos_api",
        tmp_path / "facade/src",
        tmp_path / "job-hunter/resume/exports",
        tmp_path / "job-hunter",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    for file_path in (
        tmp_path / "job-os/.venv/bin/python",
        tmp_path / "job-os/scripts/macos/jobos_runtime.py",
        tmp_path / "job-hunter/data/jobs/jobs.db",
    ):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("test", encoding="utf-8")
    config = RuntimeServiceConfig.from_mapping(mapping)
    keychain_writes = []
    commands = []
    loaded = False

    def run(command, allow_failure=False):
        nonlocal loaded
        commands.append((command, allow_failure))
        if "bootout" in command:
            loaded = False
        elif "bootstrap" in command:
            loaded = True

    result = install_runtime(
        config,
        home=tmp_path / "home",
        launcher_path=tmp_path / "job-os/scripts/macos/jobos_runtime.py",
        uid=501,
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        hermes_dashboard_token="hermes-secret-value",
        store_secret=lambda service, account, secret: keychain_writes.append(
            (service, account, secret)
        ),
        read_secret=lambda _service, _account: None,
        delete_secret=lambda _service, _account: None,
        run=run,
        is_loaded=lambda _uid, _label: loaded,
        verify_ready=lambda _config, _device_id, _token: None,
    )

    assert result.desktop_config_path == (
        tmp_path / "home/Library/Application Support/JobOS/runtime.json"
    )
    assert result.service_config_path.stat().st_mode & 0o777 == 0o600
    assert result.desktop_config_path.stat().st_mode & 0o777 == 0o600
    assert result.plist_path.stat().st_mode & 0o777 == 0o644
    persisted = (
        result.service_config_path.read_bytes()
        + result.desktop_config_path.read_bytes()
        + result.plist_path.read_bytes()
    )
    assert b"device-secret-value" not in persisted
    assert b"mcp-secret-value" not in persisted
    assert b"hermes-secret-value" not in persisted
    assert keychain_writes == [
        ("com.cobibean.jobos.device-token", "mini-device", "device-secret-value"),
        ("com.cobibean.jobos.mcp-token", "mini-device", "mcp-secret-value"),
        (
            "com.cobibean.jobos.hermes-dashboard-token",
            "mini-device",
            "hermes-secret-value",
        ),
    ]
    assert commands == [
        (["/bin/launchctl", "bootout", "gui/501/com.cobibean.jobos.api"], True),
        (
            [
                "/bin/launchctl",
                "bootstrap",
                "gui/501",
                str(result.plist_path),
            ],
            False,
        ),
        (
            [
                "/bin/launchctl",
                "kickstart",
                "-k",
                "gui/501/com.cobibean.jobos.api",
            ],
            False,
        ),
    ]


def test_runtime_cli_requires_explicit_non_secret_install_inputs(tmp_path):
    options = parse_arguments(
        [
            "install",
            "--config",
            str(tmp_path / "candidate.json"),
            "--home",
            str(tmp_path / "home"),
            "--launcher",
            str(tmp_path / "jobos_runtime.py"),
        ]
    )

    assert options.command == "install"
    assert options.config == tmp_path / "candidate.json"
    assert options.home == tmp_path / "home"
    assert options.launcher == tmp_path / "jobos_runtime.py"

    authorize = parse_arguments(
        [
            "authorize-remote",
            "--config",
            str(tmp_path / "runtime.json"),
            "--device-id",
            "macbook-device",
        ]
    )
    assert authorize.command == "authorize-remote"
    assert authorize.device_id == "macbook-device"


def test_authorize_remote_device_updates_only_ids_and_keychain_then_restarts(tmp_path):
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps(runtime_mapping(tmp_path)), encoding="utf-8")
    keychain_writes = []
    commands = []

    authorize_remote_device(
        config_path,
        device_id="macbook-device",
        device_token="macbook-device-token-value",
        uid=501,
        store_secret=lambda service, account, secret: keychain_writes.append(
            (service, account, secret)
        ),
        read_secret=lambda _service, account: (
            "mini-device-token-value" if account == "mini-device" else None
        ),
        delete_secret=lambda _service, _account: None,
        run=lambda command, allow_failure=False: commands.append((command, allow_failure)),
        verify_ready=lambda _config, _device_id, _token: None,
    )

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["remote_device_ids"] == ["macbook-device"]
    assert "macbook-device-token-value" not in config_path.read_text(encoding="utf-8")
    assert keychain_writes == [
        (
            "com.cobibean.jobos.device-token",
            "macbook-device",
            "macbook-device-token-value",
        )
    ]
    assert commands == [
        (
            [
                "/bin/launchctl",
                "kickstart",
                "-k",
                "gui/501/com.cobibean.jobos.api",
            ],
            False,
        )
    ]


def test_authorize_rejects_duplicate_token_before_mutation(tmp_path):
    config_path = tmp_path / "runtime.json"
    original = json.dumps(runtime_mapping(tmp_path))
    config_path.write_text(original, encoding="utf-8")
    mutations = []

    with pytest.raises(ValueError, match="unique"):
        authorize_remote_device(
            config_path,
            device_id="macbook-device",
            device_token="mini-device-token-value",
            uid=501,
            read_secret=lambda _service, _account: "mini-device-token-value",
            store_secret=lambda *_args: mutations.append("store"),
            delete_secret=lambda *_args: mutations.append("delete"),
            run=lambda *_args: mutations.append("run"),
        )

    assert mutations == []
    assert config_path.read_text(encoding="utf-8") == original


def test_authorize_rolls_back_config_and_keychain_when_readiness_fails(tmp_path):
    config_path = tmp_path / "runtime.json"
    original = json.dumps(runtime_mapping(tmp_path))
    config_path.write_text(original, encoding="utf-8")
    deleted = []
    commands = []

    with pytest.raises(RuntimeError, match="rolled back"):
        authorize_remote_device(
            config_path,
            device_id="macbook-device",
            device_token="macbook-device-token-value",
            uid=501,
            read_secret=lambda _service, _account: "mini-device-token-value",
            store_secret=lambda *_args: None,
            delete_secret=lambda service, account: deleted.append((service, account)),
            run=lambda command, allow_failure=False: commands.append(command),
            verify_ready=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("not ready")
            ),
        )

    assert config_path.read_text(encoding="utf-8") == original
    assert deleted == [
        ("com.cobibean.jobos.device-token", "macbook-device")
    ]
    assert len(commands) == 2


def test_install_restores_previous_files_credentials_and_service_on_failure(tmp_path):
    mapping = runtime_mapping(tmp_path)
    for directory in (
        tmp_path / "job-os/services/api/jobos_api",
        tmp_path / "facade/src",
        tmp_path / "job-hunter/resume/exports",
        tmp_path / "job-hunter",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    for file_path in (
        tmp_path / "job-os/.venv/bin/python",
        tmp_path / "job-os/scripts/macos/jobos_runtime.py",
        tmp_path / "job-hunter/data/jobs/jobs.db",
    ):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("test", encoding="utf-8")
    config = RuntimeServiceConfig.from_mapping(mapping)
    home = tmp_path / "home"
    support = home / "Library/Application Support/JobOS"
    old_files = {
        support / "service/runtime.json": b"old-service-config",
        support / "runtime.json": b"old-desktop-config",
        home / "Library/LaunchAgents/com.cobibean.jobos.api.plist": b"old-plist",
    }
    for path, contents in old_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    secrets = {
        ("com.cobibean.jobos.device-token", "mini-device"): "old-device-token-value",
        (
            "com.cobibean.jobos.hermes-dashboard-token",
            "mini-device",
        ): "old-hermes-token-value",
    }
    loaded = True

    def run(command, _allow_failure=False):
        nonlocal loaded
        if "bootout" in command:
            loaded = False
        elif "bootstrap" in command:
            loaded = True

    def delete_secret(service, account):
        secrets.pop((service, account), None)

    with pytest.raises(RuntimeError, match="rolled back"):
        install_runtime(
            config,
            home=home,
            launcher_path=tmp_path / "job-os/scripts/macos/jobos_runtime.py",
            uid=501,
            device_token="new-device-token-value",
            mcp_token="new-mcp-token-value",
            hermes_dashboard_token="new-hermes-token-value",
            store_secret=lambda service, account, secret: secrets.__setitem__(
                (service, account), secret
            ),
            read_secret=lambda service, account: secrets.get((service, account)),
            delete_secret=delete_secret,
            run=run,
            is_loaded=lambda _uid, _label: loaded,
            verify_ready=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("not ready")
            ),
        )

    assert loaded is True
    assert all(path.read_bytes() == contents for path, contents in old_files.items())
    assert secrets[("com.cobibean.jobos.device-token", "mini-device")] == (
        "old-device-token-value"
    )
    assert secrets[
        ("com.cobibean.jobos.hermes-dashboard-token", "mini-device")
    ] == "old-hermes-token-value"


def test_uninstall_removes_exact_service_files_and_registered_credentials(tmp_path):
    config_path = tmp_path / "runtime.json"
    mapping = runtime_mapping(tmp_path)
    mapping["remote_device_ids"] = ["macbook-device"]
    config_path.write_text(json.dumps(mapping), encoding="utf-8")
    home = tmp_path / "home"
    files = (
        home / "Library/LaunchAgents/com.cobibean.jobos.api.plist",
        home / "Library/Application Support/JobOS/runtime.json",
        home / "Library/Application Support/JobOS/service/runtime.json",
    )
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("disposable", encoding="utf-8")
    deleted = []

    uninstall_runtime(
        config_path,
        home=home,
        uid=501,
        run=lambda *_args: None,
        is_loaded=lambda _uid, _label: False,
        delete_secret=lambda service, account: deleted.append((service, account)),
    )

    assert all(not path.exists() for path in files)
    assert deleted == [
        ("com.cobibean.jobos.device-token", "mini-device"),
        ("com.cobibean.jobos.device-token", "macbook-device"),
        ("com.cobibean.jobos.hermes-dashboard-token", "mini-device"),
        ("com.cobibean.jobos.mcp-token", "mini-device"),
    ]

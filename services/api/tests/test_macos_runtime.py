import hashlib
import json
import plistlib
from pathlib import Path

import jobos_api.macos_runtime as macos_runtime
import pytest
from jobos_api.installation_profiles import (
    InstallationProfileConflict,
    InstallationProfileRegistry,
    InstallationProfileRegistryError,
)
from jobos_api.macos_runtime import (
    RuntimeServiceConfig,
    authorize_remote_device,
    build_local_runtime_config,
    build_service_environment,
    build_uvicorn_arguments,
    install_runtime,
    launchd_install_commands,
    parse_arguments,
    render_desktop_runtime,
    render_launchd_plist,
    run_profile_switch,
    uninstall_runtime,
    validate_runtime_paths,
)


def runtime_mapping(tmp_path: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "label": "com.cobibean.jobos.api",
        "jobos_root": str(tmp_path / "job-os"),
        "python_path": str(tmp_path / "job-os/.venv/bin/python"),
        "job_provider": "job-hunter",
        "artifact_provider": "gateway",
        "facade_source_path": str(tmp_path / "facade/src"),
        "state_db_path": str(tmp_path / "state/jobos.db"),
        "jobs_db_path": str(tmp_path / "jobs/jobs.db"),
        "local_artifact_root": str(tmp_path / "artifacts"),
        "job_hunter_db_path": str(tmp_path / "job-hunter/data/jobs/jobs.db"),
        "artifact_roots": [str(tmp_path / "job-hunter/resume/exports")],
        "hermes_dashboard_url": "ws://127.0.0.1:9119/api/ws",
        "hermes_job_hunter_cwd": str(tmp_path / "job-hunter"),
        "hermes_default_model_id": "gpt-5.6-sol-900k",
        "hermes_default_reasoning_effort": "medium",
        "device_id": "mini-device",
        "remote_device_ids": [],
        "host": "127.0.0.1",
        "port": 8766,
    }


def local_runtime_mapping(tmp_path: Path) -> dict[str, object]:
    value = runtime_mapping(tmp_path)
    value.update(
        {
            "job_provider": "sqlite",
            "artifact_provider": "local",
            "facade_source_path": None,
            "job_hunter_db_path": None,
            "artifact_roots": [],
            "hermes_dashboard_url": None,
            "hermes_job_hunter_cwd": None,
            "hermes_default_model_id": None,
            "hermes_default_reasoning_effort": None,
        }
    )
    return value


def legacy_private_runtime_mapping(tmp_path: Path) -> dict[str, object]:
    value = runtime_mapping(tmp_path)
    for field in (
        "job_provider",
        "artifact_provider",
        "jobs_db_path",
        "local_artifact_root",
    ):
        value.pop(field)
    return value


def pending_profile_switch(tmp_path):
    support = tmp_path / "Library/Application Support/JobOS"
    mapping = local_runtime_mapping(tmp_path)
    Path(mapping["python_path"]).parent.mkdir(parents=True)
    Path(mapping["python_path"]).write_text("synthetic python", encoding="utf-8")
    (Path(mapping["jobos_root"]) / "services/api/jobos_api").mkdir(parents=True)
    config_path = support / "service/runtime.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(mapping), encoding="utf-8")
    config = RuntimeServiceConfig.from_mapping(mapping)
    registry_path = support / "installation-profiles.json"
    registry = InstallationProfileRegistry(registry_path)
    data = registry.load_or_bootstrap(config)
    created = registry.create("Fresh setup", idempotency_key="create-switch-target")
    target = next(profile for profile in created.profiles if not profile.active)
    accepted = registry.activate(
        target.profile_id,
        expected_registry_revision=created.registry_revision,
        idempotency_key="activate-switch-target",
        driver="launchd",
    )
    return registry, registry_path, target.profile_id, accepted.switch_id, data.active_profile_id


def test_profile_switch_helper_requires_exact_target_identity_and_records_success(tmp_path):
    registry, registry_path, target_id, switch_id, _ = pending_profile_switch(tmp_path)
    commands = []
    verified = []

    run_profile_switch(
        registry_path,
        target_id,
        switch_id,
        uid=501,
        run=lambda command, allow_failure: commands.append((command, allow_failure)),
        read_secret=lambda _service, _account: "synthetic-device-token",
        verify_profile=lambda _config, _device, _token, expected: verified.append(expected),
    )

    assert commands == [
        (["/bin/launchctl", "kickstart", "-k", "gui/501/com.cobibean.jobos.api"], False)
    ]
    assert verified == [target_id]
    status = registry.switch_status(switch_id)
    assert status.status == "succeeded"
    assert status.active_profile_id == target_id


def test_profile_switch_helper_rolls_back_target_timeout_and_preserves_both_roots(tmp_path):
    registry, registry_path, target_id, switch_id, previous_id = pending_profile_switch(tmp_path)
    verified = []

    def verify(_config, _device, _token, expected):
        verified.append(expected)
        if expected == target_id:
            raise RuntimeError("synthetic target timeout")

    with pytest.raises(RuntimeError, match="rolled back"):
        run_profile_switch(
            registry_path,
            target_id,
            switch_id,
            uid=501,
            run=lambda _command, _allow_failure: None,
            read_secret=lambda _service, _account: "synthetic-device-token",
            verify_profile=verify,
        )

    status = registry.switch_status(switch_id)
    assert verified == [target_id, previous_id]
    assert status.status == "rolled_back"
    assert status.active_profile_id == previous_id
    assert status.error_code == "target_startup_failed"
    assert (registry_path.parent / "profiles" / target_id).is_dir()


def test_profile_switch_helper_records_kickstart_and_rollback_readiness_failures(tmp_path):
    registry, registry_path, target_id, switch_id, _ = pending_profile_switch(tmp_path)
    attempts = 0

    def run(_command, _allow_failure):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic kickstart failure")

    with pytest.raises(RuntimeError, match="rollback could not be verified"):
        run_profile_switch(
            registry_path,
            target_id,
            switch_id,
            uid=501,
            run=run,
            read_secret=lambda _service, _account: "synthetic-device-token",
            verify_profile=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("synthetic rollback readiness failure")
            ),
        )

    status = registry.switch_status(switch_id)
    assert attempts == 2
    assert status.status == "rolled_back"
    assert status.error_code == "rollback_startup_failed"


def test_profile_switch_helper_rejects_stale_or_concurrently_claimed_switch(tmp_path):
    registry, registry_path, target_id, switch_id, _ = pending_profile_switch(tmp_path)
    with pytest.raises(InstallationProfileConflict):
        run_profile_switch(
            registry_path,
            target_id,
            "jpswitch_ffffffffffffffffffffffffffffffff",
            uid=501,
            run=lambda *_args: None,
        )
    registry.claim_switch(switch_id, target_id)
    with pytest.raises(InstallationProfileConflict):
        run_profile_switch(
            registry_path,
            target_id,
            switch_id,
            uid=501,
            run=lambda *_args: None,
        )


def test_profile_switch_helper_rolls_back_a_registry_claim_write_failure(
    tmp_path, monkeypatch
):
    registry, registry_path, target_id, switch_id, previous_id = pending_profile_switch(tmp_path)
    original = InstallationProfileRegistry._write_unlocked
    failed = False

    def fail_claim_once(self, data):
        nonlocal failed
        if (
            data.pending_switch is not None
            and data.pending_switch.status == "activating"
            and not failed
        ):
            failed = True
            raise InstallationProfileRegistryError("synthetic registry write failure")
        return original(self, data)

    monkeypatch.setattr(InstallationProfileRegistry, "_write_unlocked", fail_claim_once)
    with pytest.raises(RuntimeError, match="could not start"):
        run_profile_switch(
            registry_path,
            target_id,
            switch_id,
            uid=501,
            run=lambda *_args: pytest.fail("launchd must not run after a failed claim"),
        )

    status = registry.switch_status(switch_id)
    assert status.status == "rolled_back"
    assert status.active_profile_id == previous_id
    assert status.error_code == "registry_write_failed"

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


def test_legacy_private_schema_one_config_migrates_without_breaking_installations(tmp_path):
    config = RuntimeServiceConfig.from_mapping(legacy_private_runtime_mapping(tmp_path))

    assert config.job_provider == "job-hunter"
    assert config.artifact_provider == "gateway"
    assert config.jobs_db_path == tmp_path / "state/jobs.db"
    assert config.local_artifact_root == tmp_path / "job-hunter/resume/exports"


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
        base_environment={
            "PATH": "/usr/bin:/bin",
            "UNRELATED": "omitted",
            "JOBOS_CAREER_PROFILE_ENABLED": "1",
        },
    )

    assert environment == {
        "PATH": "/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": (f"{tmp_path / 'job-os/services/api'}:{tmp_path / 'facade/src'}"),
        "JOBOS_DEVICE_TOKEN": "device-secret-value",
        "JOBOS_MCP_TOKEN": "mcp-secret-value",
        "JOBOS_DEVICE_ID": "mini-device",
        "JOBOS_STATE_DB_PATH": str(tmp_path / "state/jobos.db"),
        "JOBOS_JOBS_DB_PATH": str(tmp_path / "jobs/jobs.db"),
        "JOBOS_LOCAL_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        "JOBOS_JOB_PROVIDER": "job-hunter",
        "JOBOS_ARTIFACT_PROVIDER": "gateway",
        "JOBOS_JOB_HUNTER_DB_PATH": str(tmp_path / "job-hunter/data/jobs/jobs.db"),
        "JOBOS_ARTIFACT_ROOTS": str(tmp_path / "job-hunter/resume/exports"),
        "JOBOS_HERMES_DASHBOARD_URL": "ws://127.0.0.1:9119/api/ws",
        "JOBOS_HERMES_DASHBOARD_TOKEN": "hermes-secret-value",
        "JOBOS_HERMES_JOB_HUNTER_CWD": str(tmp_path / "job-hunter"),
        "JOBOS_HERMES_DEFAULT_MODEL_ID": "gpt-5.6-sol-900k",
        "JOBOS_HERMES_DEFAULT_REASONING_EFFORT": "medium",
        "JOBOS_CAREER_PROFILE_ENABLED": "1",
        "JOBOS_CAREER_PROFILE_AGENT_ID": "trusted-local-mcp",
        "JOBOS_CAREER_PROFILE_AGENT_DISPLAY_NAME": "JobOS Agent",
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

    owner_mapping = runtime_mapping(tmp_path)
    owner_mapping["remote_device_ids"] = ["macbook-device"]
    owner_mapping["career_profile_owner_device_ids"] = ["macbook-device"]
    remote_environment = build_service_environment(
        RuntimeServiceConfig.from_mapping(owner_mapping),
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        remote_device_tokens={"macbook-device": "macbook-secret-value"},
        hermes_dashboard_token=None,
        base_environment={"JOBOS_CAREER_PROFILE_ENABLED": "1"},
    )
    assert json.loads(remote_environment["JOBOS_DEVICE_CREDENTIALS_JSON"]) == {
        "macbook-device": "macbook-secret-value"
    }
    assert json.loads(
        remote_environment["JOBOS_CAREER_PROFILE_OWNER_DEVICE_IDS_JSON"]
    ) == ["macbook-device"]
    assert remote_environment["PATH"] == (
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    )


def test_service_environment_adds_installed_tool_paths_to_launchd_path(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")

    environment = build_service_environment(
        RuntimeServiceConfig.from_mapping(runtime_mapping(tmp_path)),
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        hermes_dashboard_token=None,
    )

    assert environment["PATH"] == (
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    )


def test_local_service_environment_has_no_private_provider_inputs(tmp_path):
    config = RuntimeServiceConfig.from_mapping(local_runtime_mapping(tmp_path))
    environment = build_service_environment(
        config,
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        hermes_dashboard_token=None,
        base_environment={"PATH": "/usr/bin:/bin"},
    )

    assert environment["JOBOS_JOB_PROVIDER"] == "sqlite"
    assert environment["JOBOS_ARTIFACT_PROVIDER"] == "local"
    assert environment["PYTHONPATH"] == str(tmp_path / "job-os/services/api")
    assert not any("JOB_HUNTER" in key or "HERMES" in key for key in environment)


def test_installed_app_runtime_wires_codex_and_keychain_without_persisting_secrets(
    tmp_path, monkeypatch
):
    app = tmp_path / "Applications/JobOS.app"
    resources = app / "Contents/Resources"
    app_server = resources / "codex-runtime/bin/codex-app-server"
    receipt = resources / "codex-runtime/JOBOS_CODEX_RUNTIME_RECEIPT.json"
    keychain_helper = resources / "jobos-keychain"
    for path, contents in (
        (app_server, b"synthetic installed app server"),
        (keychain_helper, b"synthetic keychain helper"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        path.chmod(0o755)
    digest = hashlib.sha256(app_server.read_bytes()).hexdigest()
    receipt.write_text(
        json.dumps({"app_server_binary": {"sha256": digest}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(macos_runtime, "CODEX_APP_SERVER_SHA256", digest)
    data_dir = tmp_path / "data"
    config = build_local_runtime_config(
        jobos_root=tmp_path / "release",
        python_path=tmp_path / "release/.venv/bin/python",
        data_dir=data_dir,
        device_id="mini-device",
        port=8766,
        jobos_app=app,
        keychain_helper_sha256=hashlib.sha256(keychain_helper.read_bytes()).hexdigest(),
    )
    service_config = tmp_path / "home/Library/Application Support/JobOS/service/runtime.json"
    environment = build_service_environment(
        config,
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        hermes_dashboard_token=None,
        base_environment={"PATH": "/usr/bin:/bin"},
        service_config_path=service_config,
    )

    assert config.codex_app_server_path == app_server
    assert config.codex_home_path == data_dir / "codex"
    assert config.keychain_helper_sha256 == hashlib.sha256(keychain_helper.read_bytes()).hexdigest()
    assert environment["JOBOS_KEYCHAIN_HELPER_PATH"] == str(keychain_helper)
    assert environment["JOBOS_CODEX_APP_SERVER_PATH"] == str(app_server)
    assert environment["JOBOS_CODEX_HOME"] == str(data_dir / "codex")
    assert environment["JOBOS_CODEX_MCP_COMMAND"] == str(config.python_path)
    assert json.loads(environment["JOBOS_CODEX_MCP_ARGS_JSON"]) == [
        str(config.jobos_root / "scripts/macos/jobos_mcp_runtime.py"),
        str(service_config),
    ]
    persisted = json.dumps(config.to_mapping())
    assert "device-secret-value" not in persisted
    assert "mcp-secret-value" not in persisted


def test_installed_app_runtime_requires_operator_supplied_keychain_helper_hash(tmp_path):
    app = tmp_path / "Applications/JobOS.app"
    app.mkdir(parents=True)

    with pytest.raises(ValueError, match="expected Keychain helper SHA-256"):
        build_local_runtime_config(
            jobos_root=tmp_path / "release",
            python_path=tmp_path / "python",
            data_dir=tmp_path / "data",
            device_id="mini-device",
            port=8766,
            jobos_app=app,
        )


def test_installed_app_runtime_rejects_invalid_keychain_helper_hash(tmp_path):
    app = tmp_path / "Applications/JobOS.app"
    app.mkdir(parents=True)

    with pytest.raises(ValueError, match="expected Keychain helper SHA-256 is invalid"):
        build_local_runtime_config(
            jobos_root=tmp_path / "release",
            python_path=tmp_path / "python",
            data_dir=tmp_path / "data",
            device_id="mini-device",
            port=8766,
            jobos_app=app,
            keychain_helper_sha256="not-a-sha256",
        )


def test_installed_app_runtime_rejects_tampered_binary(tmp_path, monkeypatch):
    app = tmp_path / "Applications/JobOS.app"
    resources = app / "Contents/Resources"
    app_server = resources / "codex-runtime/bin/codex-app-server"
    app_server.parent.mkdir(parents=True)
    app_server.write_bytes(b"tampered")
    app_server.chmod(0o755)
    keychain_helper = resources / "jobos-keychain"
    keychain_helper.write_bytes(b"helper")
    keychain_helper.chmod(0o755)
    (resources / "codex-runtime/JOBOS_CODEX_RUNTIME_RECEIPT.json").write_text(
        json.dumps({"app_server_binary": {"sha256": "0" * 64}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(macos_runtime, "CODEX_APP_SERVER_SHA256", "f" * 64)

    with pytest.raises(ValueError, match="integrity"):
        build_local_runtime_config(
            jobos_root=tmp_path / "release",
            python_path=tmp_path / "python",
            data_dir=tmp_path / "data",
            device_id="mini-device",
            port=8766,
            jobos_app=app,
            keychain_helper_sha256="f" * 64,
        )


def test_installed_app_runtime_rejects_untrusted_keychain_helper(tmp_path, monkeypatch):
    app = tmp_path / "Applications/JobOS.app"
    resources = app / "Contents/Resources"
    app_server = resources / "codex-runtime/bin/codex-app-server"
    keychain_helper = resources / "jobos-keychain"
    app_server.parent.mkdir(parents=True)
    app_server.write_bytes(b"synthetic installed app server")
    app_server.chmod(0o755)
    keychain_helper.write_bytes(b"untrusted helper")
    keychain_helper.chmod(0o755)
    digest = hashlib.sha256(app_server.read_bytes()).hexdigest()
    (resources / "codex-runtime/JOBOS_CODEX_RUNTIME_RECEIPT.json").write_text(
        json.dumps({"app_server_binary": {"sha256": digest}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(macos_runtime, "CODEX_APP_SERVER_SHA256", digest)

    with pytest.raises(ValueError, match="Keychain helper failed integrity"):
        build_local_runtime_config(
            jobos_root=tmp_path / "release",
            python_path=tmp_path / "python",
            data_dir=tmp_path / "data",
            device_id="mini-device",
            port=8766,
            jobos_app=app,
            keychain_helper_sha256="f" * 64,
        )


def test_installed_app_runtime_rejects_non_executable_tools(tmp_path, monkeypatch):
    app = tmp_path / "Applications/JobOS.app"
    resources = app / "Contents/Resources"
    app_server = resources / "codex-runtime/bin/codex-app-server"
    keychain_helper = resources / "jobos-keychain"
    app_server.parent.mkdir(parents=True)
    app_server.write_bytes(b"synthetic installed app server")
    keychain_helper.write_bytes(b"synthetic keychain helper")
    app_server.chmod(0o755)
    keychain_helper.chmod(0o644)
    digest = hashlib.sha256(app_server.read_bytes()).hexdigest()
    (resources / "codex-runtime/JOBOS_CODEX_RUNTIME_RECEIPT.json").write_text(
        json.dumps({"app_server_binary": {"sha256": digest}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(macos_runtime, "CODEX_APP_SERVER_SHA256", digest)

    with pytest.raises(ValueError, match="not executable"):
        build_local_runtime_config(
            jobos_root=tmp_path / "release",
            python_path=tmp_path / "python",
            data_dir=tmp_path / "data",
            device_id="mini-device",
            port=8766,
            jobos_app=app,
            keychain_helper_sha256="f" * 64,
        )


def test_runtime_rejects_tampered_installed_keychain_helper(tmp_path, monkeypatch):
    mapping = local_runtime_mapping(tmp_path)
    helper = tmp_path / "job-os/bin/jobos-keychain"
    app_server = tmp_path / "job-os/bin/codex-app-server"
    for path in (helper, app_server):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"trusted")
        path.chmod(0o755)
    mapping.update(
        {
            "keychain_helper_path": str(helper),
            "keychain_helper_sha256": hashlib.sha256(b"trusted").hexdigest(),
            "codex_app_server_path": str(app_server),
            "codex_home_path": str(tmp_path / "codex-home"),
        }
    )
    (tmp_path / "job-os/services/api/jobos_api").mkdir(parents=True)
    (tmp_path / "job-os/scripts/macos").mkdir(parents=True)
    (tmp_path / "job-os/scripts/macos/jobos_mcp_runtime.py").write_text("test")
    python_path = Path(str(mapping["python_path"]))
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("test")
    monkeypatch.setattr(
        macos_runtime, "CODEX_APP_SERVER_SHA256", hashlib.sha256(b"trusted").hexdigest()
    )
    helper.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="Keychain helper failed integrity"):
        validate_runtime_paths(RuntimeServiceConfig.from_mapping(mapping))

    config = RuntimeServiceConfig.from_mapping(mapping)
    with pytest.raises(RuntimeError, match="Keychain helper failed integrity"):
        macos_runtime._configured_store_secret(config, macos_runtime.store_keychain_secret)
    with pytest.raises(RuntimeError, match="Keychain helper failed integrity"):
        macos_runtime._configured_read_secret(config, macos_runtime.read_keychain_secret)
    with pytest.raises(RuntimeError, match="Keychain helper failed integrity"):
        macos_runtime._configured_delete_secret(config, macos_runtime.delete_keychain_secret)


def test_installed_app_runtime_rejects_symlinked_resource_ancestors(tmp_path):
    app = tmp_path / "Applications/JobOS.app"
    app.mkdir(parents=True)
    external_contents = tmp_path / "external/Contents"
    external_contents.mkdir(parents=True)
    (app / "Contents").symlink_to(external_contents, target_is_directory=True)

    with pytest.raises(ValueError, match="contains a symlink"):
        build_local_runtime_config(
            jobos_root=tmp_path / "release",
            python_path=tmp_path / "python",
            data_dir=tmp_path / "data",
            device_id="mini-device",
            port=8766,
            jobos_app=app,
            keychain_helper_sha256="f" * 64,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("job_provider", "job-hunter"), ("artifact_provider", "gateway")],
)
def test_private_provider_selection_requires_private_paths(tmp_path, field, value):
    mapping = local_runtime_mapping(tmp_path)
    mapping[field] = value
    with pytest.raises(ValueError, match="private providers"):
        RuntimeServiceConfig.from_mapping(mapping)


def test_install_accepts_public_local_runtime_without_private_trees(tmp_path):
    mapping = local_runtime_mapping(tmp_path)
    (tmp_path / "job-os/services/api/jobos_api").mkdir(parents=True)
    for file_path in (
        tmp_path / "job-os/.venv/bin/python",
        tmp_path / "job-os/scripts/macos/jobos_runtime.py",
    ):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("test", encoding="utf-8")
    loaded = False

    def run(command, allow_failure=False):
        nonlocal loaded
        if "bootout" in command:
            loaded = False
        elif "bootstrap" in command:
            loaded = True

    config = RuntimeServiceConfig.from_mapping(mapping)
    install_runtime(
        config,
        home=tmp_path / "home",
        launcher_path=tmp_path / "job-os/scripts/macos/jobos_runtime.py",
        uid=501,
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        hermes_dashboard_token=None,
        store_secret=lambda *_args: None,
        read_secret=lambda *_args: None,
        delete_secret=lambda *_args: None,
        run=run,
        is_loaded=lambda _uid, _label: loaded,
        verify_ready=lambda *_args: None,
    )

    assert (tmp_path / "jobs").is_dir()
    assert (tmp_path / "artifacts").is_dir()


def test_install_accepts_installed_codex_runtime_with_service_config_path(
    tmp_path, monkeypatch
):
    mapping = local_runtime_mapping(tmp_path)
    mapping.update(
        {
            "keychain_helper_path": str(tmp_path / "job-os/bin/jobos-keychain"),
            "keychain_helper_sha256": hashlib.sha256(
                b'#!/bin/sh\nif [ "$1" = "get" ]; then exit 44; fi\nexit 0\n'
            ).hexdigest(),
            "codex_app_server_path": str(tmp_path / "job-os/bin/codex-app-server"),
            "codex_home_path": str(tmp_path / "codex-home"),
        }
    )
    (tmp_path / "job-os/services/api/jobos_api").mkdir(parents=True)
    for file_path in (
        tmp_path / "job-os/.venv/bin/python",
        tmp_path / "job-os/scripts/macos/jobos_runtime.py",
        tmp_path / "job-os/scripts/macos/jobos_mcp_runtime.py",
        tmp_path / "job-os/bin/codex-app-server",
    ):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("test", encoding="utf-8")
    keychain_helper = tmp_path / "job-os/bin/jobos-keychain"
    keychain_helper.write_text(
        '#!/bin/sh\nif [ "$1" = "get" ]; then exit 44; fi\nexit 0\n',
        encoding="utf-8",
    )
    keychain_helper.chmod(0o755)
    (tmp_path / "job-os/bin/codex-app-server").chmod(0o755)
    monkeypatch.setattr(
        macos_runtime,
        "CODEX_APP_SERVER_SHA256",
        hashlib.sha256(b"test").hexdigest(),
    )
    loaded = False

    def run(command, allow_failure=False):
        nonlocal loaded
        if "bootout" in command:
            loaded = False
        elif "bootstrap" in command:
            loaded = True

    result = install_runtime(
        RuntimeServiceConfig.from_mapping(mapping),
        home=tmp_path / "home",
        launcher_path=tmp_path / "job-os/scripts/macos/jobos_runtime.py",
        uid=501,
        device_token="device-secret-value",
        mcp_token="mcp-secret-value",
        hermes_dashboard_token=None,
        run=run,
        is_loaded=lambda _uid, _label: loaded,
        verify_ready=lambda *_args: None,
    )

    assert result.service_config_path.is_file()
    assert (tmp_path / "codex-home").is_dir()


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

    local = parse_arguments(
        [
            "install-local",
            "--jobos-root",
            str(tmp_path / "job-os"),
            "--python",
            str(tmp_path / "python"),
            "--data-dir",
            str(tmp_path / "data"),
            "--home",
            str(tmp_path / "home"),
            "--launcher",
            str(tmp_path / "jobos_runtime.py"),
        ]
    )
    assert local.command == "install-local"
    assert local.data_dir == tmp_path / "data"


def test_install_local_cli_builds_and_installs_public_profile(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setenv("JOBOS_DEVICE_TOKEN", "device-token-long-value")
    monkeypatch.setenv("JOBOS_MCP_TOKEN", "mcp-token-long-value")
    monkeypatch.setattr(
        macos_runtime,
        "install_runtime",
        lambda config, **kwargs: captured.update(config=config, kwargs=kwargs),
    )

    result = macos_runtime.main(
        [
            "install-local",
            "--jobos-root",
            str(tmp_path / "job-os"),
            "--python",
            str(tmp_path / "python"),
            "--data-dir",
            str(tmp_path / "data"),
            "--home",
            str(tmp_path / "home"),
            "--launcher",
            str(tmp_path / "jobos_runtime.py"),
        ]
    )

    assert result == 0
    config = captured["config"]
    assert config.job_provider == "sqlite"
    assert config.artifact_provider == "local"
    assert config.facade_source_path is None
    assert config.job_hunter_db_path is None


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
            verify_ready=lambda *_args: (_ for _ in ()).throw(RuntimeError("not ready")),
        )

    assert config_path.read_text(encoding="utf-8") == original
    assert deleted == [("com.cobibean.jobos.device-token", "macbook-device")]
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
            verify_ready=lambda *_args: (_ for _ in ()).throw(RuntimeError("not ready")),
        )

    assert loaded is True
    assert all(path.read_bytes() == contents for path, contents in old_files.items())
    assert secrets[("com.cobibean.jobos.device-token", "mini-device")] == ("old-device-token-value")
    assert (
        secrets[("com.cobibean.jobos.hermes-dashboard-token", "mini-device")]
        == "old-hermes-token-value"
    )


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

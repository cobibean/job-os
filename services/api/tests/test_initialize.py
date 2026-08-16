from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
from jobos_api.initialize import initialize_jobos
from jobos_api.local_config import (
    LocalConfigError,
    load_credentials,
    read_config,
    settings_from_config,
    store_credentials,
)
from jobos_api.main import settings_from_environment
from jobos_api.sqlite_job_repository import SQLiteJobRepository


def test_initialization_is_idempotent_secure_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    first = initialize_jobos(tmp_path)
    config_bytes = (tmp_path / "config.json").read_bytes()
    config = read_config(tmp_path / "config.json")
    credentials_path = tmp_path / "credentials/local.json"
    credential_bytes = credentials_path.read_bytes()
    second = initialize_jobos(tmp_path)

    assert first.created is True
    assert first.demo_seeded is True
    assert second.created is False
    assert second.demo_seeded is False
    assert (tmp_path / "config.json").read_bytes() == config_bytes
    assert credentials_path.read_bytes() == credential_bytes
    assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "config.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "jobs").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "state/jobos.db").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "jobs/jobs.db").stat().st_mode) == 0o600
    device_token, mcp_token = load_credentials(config, tmp_path)
    serialized_config = json.dumps(config)
    serialized_result = json.dumps(second.safe_dict())
    assert device_token not in serialized_config + serialized_result
    assert mcp_token not in serialized_config + serialized_result
    assert config["apiBaseUrl"] == "http://127.0.0.1:8766"
    assert config["jobProvider"] == "sqlite"
    assert config["artifactProvider"] == "local"
    assert config["agentProvider"] == "offline"


def test_initialization_repairs_a_missing_file_credential(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    initialize_jobos(tmp_path)
    config = read_config(tmp_path / "config.json")
    original_device_id = config["deviceId"]
    credential_path = tmp_path / "credentials/local.json"
    credential_path.unlink()

    result = initialize_jobos(tmp_path)
    repaired_config = read_config(tmp_path / "config.json")
    device_token, mcp_token = load_credentials(repaired_config, tmp_path)

    assert result.created is False
    assert repaired_config["deviceId"] == original_device_id
    assert repaired_config["credentialStore"] == {
        "provider": "file",
        "path": "credentials/local.json",
    }
    assert device_token and mcp_token
    assert stat.S_IMODE(credential_path.stat().st_mode) == 0o600


def test_initialization_repairs_malformed_credential_values(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    initialize_jobos(tmp_path)
    credential_path = tmp_path / "credentials/local.json"
    credential_path.write_text(
        json.dumps({"deviceToken": {"unsafe": True}, "mcpToken": 42}),
        encoding="utf-8",
    )
    credential_path.chmod(0o600)

    result = initialize_jobos(tmp_path)
    device_token, mcp_token = load_credentials(read_config(tmp_path / "config.json"), tmp_path)

    assert result.created is False
    assert device_token and mcp_token


def test_initialization_writes_an_exact_custom_config_path(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    data_dir = tmp_path / "data"
    target = tmp_path / "configuration/custom-name.json"

    initialize_jobos(data_dir, config_path_override=target)

    assert target.is_file()
    assert not (target.parent / "config.json").exists()
    assert (data_dir / "jobs/jobs.db").is_file()
    assert (data_dir / "credentials/local.json").is_file()
    assert settings_from_config(target).job_provider == "sqlite"


def test_concurrent_initialization_converges_on_one_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: initialize_jobos(tmp_path), range(2)))

    assert sorted(result.created for result in results) == [False, True]
    assert sorted(result.demo_seeded for result in results) == [False, True]
    assert len(SQLiteJobRepository(tmp_path / "jobs/jobs.db").list_jobs()) == 1


def test_initialization_can_disable_demo_for_a_clean_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    result = initialize_jobos(tmp_path, demo_enabled=False)
    assert result.demo_seeded is False


def test_initialization_paths_are_configurable(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    profile = tmp_path / "profile"
    initialize_jobos(
        profile,
        state_db_path=tmp_path / "custom/state.sqlite3",
        jobs_db_path=tmp_path / "custom/jobs.sqlite3",
        artifacts_path=tmp_path / "custom/artifacts",
        logs_path=tmp_path / "custom/logs",
        credentials_path=tmp_path / "custom/credentials",
    )
    assert (tmp_path / "custom/state.sqlite3").is_file()
    assert (tmp_path / "custom/jobs.sqlite3").is_file()
    assert (tmp_path / "custom/artifacts").is_dir()
    assert (tmp_path / "custom/logs").is_dir()
    assert (tmp_path / "custom/credentials/local.json").is_file()


def test_missing_configuration_has_an_actionable_service_error(tmp_path, monkeypatch):
    monkeypatch.delenv("JOBOS_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("JOBOS_MCP_TOKEN", raising=False)
    monkeypatch.setenv("JOBOS_CONFIG_PATH", str(tmp_path / "missing/config.json"))
    with pytest.raises(RuntimeError, match="jobos-init --data-dir"):
        settings_from_environment()


def test_token_configured_source_defaults_use_application_data_not_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JOBOS_DEVICE_TOKEN", "source-default-device-token")
    monkeypatch.setenv("JOBOS_MCP_TOKEN", "source-default-mcp-token")
    monkeypatch.delenv("JOBOS_STATE_DB_PATH", raising=False)
    monkeypatch.delenv("JOBOS_LOCAL_ARTIFACT_ROOT", raising=False)

    configured = settings_from_environment()

    assert configured.state_db_path == tmp_path / "Library/Application Support/JobOS/state/jobos.db"
    assert configured.resolved_jobs_db_path() == (
        tmp_path / "Library/Application Support/JobOS/jobs/jobs.db"
    )
    assert configured.resolved_local_artifact_root() == (
        tmp_path / "Library/Application Support/JobOS/artifacts"
    )


def test_file_credentials_reject_symlinks(tmp_path):
    target = tmp_path / "outside.json"
    target.write_text('{"deviceToken":"device","mcpToken":"mcp"}', encoding="utf-8")
    target.chmod(0o600)
    credential = tmp_path / "credentials.json"
    credential.symlink_to(target)
    config = {
        "deviceId": "local-test",
        "credentialStore": {"provider": "file", "path": credential.name},
    }

    with pytest.raises(LocalConfigError, match="regular file"):
        load_credentials(config, tmp_path)


def test_keychain_operation_failure_does_not_silently_fallback(tmp_path, monkeypatch):
    helper = tmp_path / "jobos-keychain"
    helper.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    helper.chmod(0o700)
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "darwin")
    monkeypatch.setattr("jobos_api.local_config.keychain_helper_path", lambda: helper)
    monkeypatch.setattr(
        "jobos_api.local_config.store_keychain_secret",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("failed")),
    )

    with pytest.raises(LocalConfigError, match="Keychain setup failed"):
        store_credentials(
            data_dir=tmp_path,
            device_id="local-test",
            device_token="device-secret",
            mcp_token="mcp-secret",
        )

    assert not (tmp_path / "credentials/local.json").exists()

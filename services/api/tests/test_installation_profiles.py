from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jobos_api.installation_profiles as installation_profiles
import pytest
from jobos_api.installation_profiles import (
    MAX_PROFILES,
    AnchoredRuntime,
    InstallationProfileConflict,
    InstallationProfileLimitReached,
    InstallationProfileRecord,
    InstallationProfileRegistry,
    InstallationProfileRegistryData,
    InstallationProfileRegistryError,
    InstallationProfileStorageError,
    effective_profile_runtime,
    managed_profile_paths,
    public_profile_list,
)
from pydantic import ValidationError

PROFILE_A = "jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PROFILE_B = "jprof_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NOW = datetime(2026, 8, 23, 16, tzinfo=UTC)


def anchored_runtime(root: Path) -> AnchoredRuntime:
    return AnchoredRuntime(
        job_provider="sqlite",
        artifact_provider="local",
        state_db_path=(root / "existing/state.db").absolute(),
        jobs_db_path=(root / "existing/jobs.db").absolute(),
        local_artifact_root=(root / "existing/artifacts").absolute(),
        artifact_roots=((root / "existing/artifacts").absolute(),),
    )


def registry_data(root: Path) -> InstallationProfileRegistryData:
    return InstallationProfileRegistryData(
        registry_revision=1,
        active_profile_id=PROFILE_A,
        profiles=(
            InstallationProfileRecord(
                profile_id=PROFILE_A,
                display_name="Personal",
                storage_mode="anchored",
                created_at=NOW,
                updated_at=NOW,
                anchored_runtime=anchored_runtime(root),
            ),
        ),
    )


def test_registry_schema_is_strict_and_rejects_invalid_identity_and_names(tmp_path):
    value = registry_data(tmp_path).model_dump(mode="json")
    value["unexpected"] = True
    with pytest.raises(ValidationError):
        InstallationProfileRegistryData.model_validate(value)

    value.pop("unexpected")
    value["active_profile_id"] = "profile-personal"
    with pytest.raises(ValidationError):
        InstallationProfileRegistryData.model_validate(value)

    with pytest.raises(ValidationError):
        InstallationProfileRecord(
            profile_id=PROFILE_B,
            display_name="bad/name",
            storage_mode="managed",
            created_at=NOW,
            updated_at=NOW,
        )


def test_registry_rejects_casefolded_duplicate_names_and_more_than_32_profiles(tmp_path):
    first = registry_data(tmp_path).profiles[0]
    duplicate = InstallationProfileRecord(
        profile_id=PROFILE_B,
        display_name="personal",
        storage_mode="managed",
        created_at=NOW,
        updated_at=NOW,
    )
    with pytest.raises(ValidationError):
        InstallationProfileRegistryData(
            registry_revision=2,
            active_profile_id=PROFILE_A,
            profiles=(first, duplicate),
        )

    profiles = tuple(
        InstallationProfileRecord(
            profile_id=f"jprof_{index:032x}",
            display_name=f"Synthetic {index}",
            storage_mode="managed",
            created_at=NOW,
            updated_at=NOW,
        )
        for index in range(MAX_PROFILES + 1)
    )
    with pytest.raises(ValidationError):
        InstallationProfileRegistryData(
            registry_revision=1,
            active_profile_id=profiles[0].profile_id,
            profiles=profiles,
        )


def test_public_list_is_deterministic_active_first_and_contains_no_storage_data(tmp_path):
    first = registry_data(tmp_path).profiles[0]
    second = InstallationProfileRecord(
        profile_id=PROFILE_B,
        display_name="Fresh setup",
        storage_mode="managed",
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
    )
    public = public_profile_list(
        InstallationProfileRegistryData(
            registry_revision=2,
            active_profile_id=PROFILE_A,
            profiles=(second, first),
        )
    )

    assert [item.profile_id for item in public.profiles] == [PROFILE_A, PROFILE_B]
    encoded = public.model_dump_json()
    for forbidden in ("storage_mode", "anchored_runtime", "state_db_path", "token"):
        assert forbidden not in encoded


def test_bootstrap_adopts_existing_runtime_without_moving_or_rewriting_files(tmp_path):
    installation_root = tmp_path / "application-support"
    state_path = tmp_path / "outside" / "jobos.db"
    state_path.parent.mkdir()
    state_path.write_bytes(b"existing-state-bytes")
    jobs_path = tmp_path / "outside" / "jobs.db"
    jobs_path.write_bytes(b"existing-jobs-bytes")
    artifacts = tmp_path / "outside" / "artifacts"
    artifacts.mkdir()
    marker = artifacts / "marker.txt"
    marker.write_text("existing artifact", encoding="utf-8")
    runtime = AnchoredRuntime(
        job_provider="sqlite",
        artifact_provider="local",
        state_db_path=state_path,
        jobs_db_path=jobs_path,
        local_artifact_root=artifacts,
        artifact_roots=(artifacts,),
    )

    result = InstallationProfileRegistry(
        installation_root / "installation-profiles.json"
    ).load_or_bootstrap(runtime, now=NOW)

    assert result.profiles[0].display_name == "Personal"
    assert result.profiles[0].storage_mode == "anchored"
    assert result.profiles[0].anchored_runtime == runtime
    assert state_path.read_bytes() == b"existing-state-bytes"
    assert jobs_path.read_bytes() == b"existing-jobs-bytes"
    assert marker.read_text(encoding="utf-8") == "existing artifact"
    assert not (installation_root / "profiles").exists()


def test_managed_paths_depend_only_on_profile_id_and_force_local_providers(tmp_path):
    paths = managed_profile_paths(tmp_path, PROFILE_B)
    profile = InstallationProfileRecord(
        profile_id=PROFILE_B,
        display_name="A name that never becomes a directory",
        storage_mode="managed",
        created_at=NOW,
        updated_at=NOW,
    )
    effective = effective_profile_runtime(
        {
            "job_provider": "job-hunter",
            "artifact_provider": "gateway",
            "state_db_path": tmp_path / "old-state.db",
            "jobs_db_path": tmp_path / "old-jobs.db",
            "local_artifact_root": tmp_path / "old-artifacts",
            "job_hunter_db_path": tmp_path / "private.db",
            "facade_source_path": tmp_path / "private-source",
            "artifact_roots": (tmp_path / "gateway",),
            "hermes_dashboard_url": "http://127.0.0.1:9999",
        },
        profile,
        tmp_path,
    )

    assert paths.root == tmp_path / "profiles" / PROFILE_B
    assert "A name" not in str(paths.root)
    assert effective["job_provider"] == "sqlite"
    assert effective["artifact_provider"] == "local"
    assert effective["state_db_path"] == paths.state_db_path
    assert effective["job_hunter_db_path"] is None
    assert effective["facade_source_path"] is None
    assert effective["artifact_roots"] == ()
    assert effective["hermes_dashboard_url"] == "http://127.0.0.1:9999"


def test_managed_paths_reject_malformed_ids_and_symlinked_parent(tmp_path):
    with pytest.raises(ValueError):
        managed_profile_paths(tmp_path, "../../escape")

    outside = tmp_path / "outside"
    outside.mkdir()
    installation_root = tmp_path / "support"
    installation_root.mkdir()
    (installation_root / "profiles").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InstallationProfileStorageError):
        managed_profile_paths(installation_root, PROFILE_B)


def test_registry_write_is_private_atomic_and_cleans_temporary_files(tmp_path):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    registry.write(registry_data(tmp_path))

    assert registry.path.stat().st_mode & 0o777 == 0o600
    assert registry.lock_path.stat().st_mode & 0o777 == 0o600
    assert registry.load() == registry_data(tmp_path)
    assert not list(tmp_path.glob(".installation-profiles.json.*.tmp"))


def test_registry_lock_rejects_a_symlink(tmp_path):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    target = tmp_path / "outside.lock"
    target.touch()
    registry.lock_path.symlink_to(target)

    with pytest.raises(InstallationProfileRegistryError):
        registry.write(registry_data(tmp_path))


def test_interrupted_registry_replace_preserves_old_bytes_and_cleans_temp(
    tmp_path, monkeypatch
):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    original = registry_data(tmp_path)
    registry.write(original)
    replacement = original.model_copy(update={"registry_revision": 2})

    def interrupt_replace(_source, _target):
        raise OSError("synthetic interrupted replace")

    monkeypatch.setattr(installation_profiles.os, "replace", interrupt_replace)
    with pytest.raises(InstallationProfileRegistryError):
        registry.write(replacement)

    assert registry.load() == original
    assert not list(tmp_path.glob(".installation-profiles.json.*.tmp"))


def test_registry_rejects_oversized_and_corrupt_files(tmp_path):
    path = tmp_path / "installation-profiles.json"
    path.write_bytes(b"{" + b" " * (1024 * 1024))
    with pytest.raises(InstallationProfileRegistryError):
        InstallationProfileRegistry(path).load()

    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(InstallationProfileRegistryError):
        InstallationProfileRegistry(path).load()

    managed_only = registry_data(tmp_path).model_dump(mode="json")
    managed_only["profiles"][0]["storage_mode"] = "managed"
    managed_only["profiles"][0]["anchored_runtime"] = None
    path.write_text(json.dumps(managed_only), encoding="utf-8")
    with pytest.raises(InstallationProfileRegistryError):
        InstallationProfileRegistry(path).load()


def test_create_is_idempotent_and_stale_rename_fails(tmp_path):
    path = tmp_path / "installation-profiles.json"
    registry = InstallationProfileRegistry(path)
    registry.write(registry_data(tmp_path))

    created = registry.create(
        "Fresh setup", idempotency_key="create-1", profile_id=PROFILE_B, now=NOW
    )
    replay = InstallationProfileRegistry(path).create(
        "Fresh setup", idempotency_key="create-1", profile_id=PROFILE_B, now=NOW
    )
    assert replay == created
    assert created.registry_revision == 2
    assert b"create-1" not in path.read_bytes()
    with pytest.raises(InstallationProfileConflict, match="Idempotency key"):
        InstallationProfileRegistry(path).create(
            "Different request", idempotency_key="create-1", now=NOW
        )
    with pytest.raises(InstallationProfileConflict):
        registry.rename(
            PROFILE_B,
            "Renamed",
            expected_registry_revision=1,
            idempotency_key="rename-1",
        )


def test_create_replay_persists_the_random_profile_identity(tmp_path, monkeypatch):
    path = tmp_path / "installation-profiles.json"
    registry = InstallationProfileRegistry(path)
    registry.write(registry_data(tmp_path))
    monkeypatch.setattr(installation_profiles.secrets, "token_hex", lambda _size: "c" * 32)

    created, created_profile_id = registry.create_with_identity(
        "Fresh setup",
        idempotency_key="random-create-key",
        now=NOW,
    )
    monkeypatch.setattr(installation_profiles.secrets, "token_hex", lambda _size: "d" * 32)
    replay, replay_profile_id = InstallationProfileRegistry(path).create_with_identity(
        "Fresh setup",
        idempotency_key="random-create-key",
        now=NOW,
    )

    assert created_profile_id == "jprof_" + "c" * 32
    assert replay_profile_id == created_profile_id
    assert replay == created
    assert registry.load().idempotency_replays[-1].result_profile_id == created_profile_id


def test_profile_cap_and_two_concurrent_creates_are_serialized(tmp_path):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    registry.write(registry_data(tmp_path))

    def create(index: int):
        return registry.create(
            f"Synthetic {index}",
            idempotency_key=f"create-{index}",
            profile_id=f"jprof_{index:032x}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(create, (2, 3)))
    assert registry.load().registry_revision == 3

    for index in range(4, 33):
        create(index)
    with pytest.raises(InstallationProfileLimitReached):
        create(33)


def test_concurrent_replay_of_the_same_create_key_is_idempotent(tmp_path):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    registry.write(registry_data(tmp_path))

    def create_replay(_index: int):
        return registry.create(
            "Fresh setup",
            idempotency_key="same-create-key",
            profile_id=PROFILE_B,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(create_replay, (1, 2))

    assert first == second
    assert registry.load().registry_revision == 2


def test_failed_source_switch_rolls_back_only_the_exact_completed_switch(tmp_path):
    path = tmp_path / "installation-profiles.json"
    registry = InstallationProfileRegistry(path)
    registry.write(registry_data(tmp_path))
    created = registry.create(
        "Fresh setup", idempotency_key="source-create", profile_id=PROFILE_B, now=NOW
    )
    accepted = registry.activate(
        PROFILE_B,
        expected_registry_revision=created.registry_revision,
        idempotency_key="source-activate",
        driver="desktop",
        now=NOW,
    )

    previous = InstallationProfileRegistry(path).rollback_completed_source_switch(
        accepted.switch_id,
        "target_startup_failed",
    )

    rolled_back = registry.load()
    assert previous == PROFILE_A
    assert rolled_back.active_profile_id == PROFILE_A
    assert rolled_back.last_switch is not None
    assert rolled_back.last_switch.status == "rolled_back"
    assert rolled_back.last_switch.error_code == "target_startup_failed"
    with pytest.raises(InstallationProfileConflict):
        registry.rollback_completed_source_switch(
            accepted.switch_id,
            "target_startup_failed",
        )

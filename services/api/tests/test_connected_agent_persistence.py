from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import jobos_api.installation_profiles as installation_profiles_module
import pytest
from jobos_api.installation_profiles import (
    AnchoredRuntime,
    ConnectedAgentRecord,
    HermesConnectionConfiguration,
    InstallationProfileConflict,
    InstallationProfileRecord,
    InstallationProfileRegistry,
    InstallationProfileRegistryData,
    InstallationProfileRegistryError,
    codex_account_fingerprint,
)
from jobos_api.state_store import ConversationBusy, ConversationLimit, JobOsStateStore
from pydantic import ValidationError

PROFILE_A = "jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PROFILE_B = "jprof_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
LEGACY_FIXTURE_PROFILE = "jprof_11111111111111111111111111111111"
NOW = datetime(2026, 8, 23, 16, tzinfo=UTC)


def legacy_sqlite_snapshot(
    path: Path,
    columns_by_table: dict[str, tuple[str, ...]] | None = None,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[tuple[object, ...], ...]]]:
    with sqlite3.connect(path) as connection:
        if columns_by_table is None:
            tables = [
                str(row[0])
                for row in connection.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                         AND name NOT IN ('schema_migrations', 'connected_agent_migration_journal')
                       ORDER BY name"""
                )
            ]
            columns_by_table = {
                table: tuple(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM pragma_table_info(?)", (table,)
                    )
                )
                for table in tables
            }
        rows = {}
        for table, columns in columns_by_table.items():
            projection = ", ".join(f'"{column}"' for column in columns)
            values = connection.execute(
                f'SELECT {projection} FROM "{table}"'
            ).fetchall()
            rows[table] = tuple(sorted((tuple(value) for value in values), key=repr))
    return columns_by_table, rows


def anchored_runtime(root: Path) -> AnchoredRuntime:
    return AnchoredRuntime(
        job_provider="sqlite",
        artifact_provider="local",
        state_db_path=(root / "existing/state.db").absolute(),
        jobs_db_path=(root / "existing/jobs.db").absolute(),
        local_artifact_root=(root / "existing/artifacts").absolute(),
        artifact_roots=((root / "existing/artifacts").absolute(),),
    )


def registry_data(
    root: Path, runtime: AnchoredRuntime | None = None
) -> InstallationProfileRegistryData:
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
                anchored_runtime=runtime or anchored_runtime(root),
            ),
        ),
    )


def test_fresh_registry_v2_has_no_silent_agent_or_profile_default(tmp_path):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")

    data = registry.load_or_bootstrap(anchored_runtime(tmp_path), now=NOW)

    assert data.schema_version == 2
    assert data.connected_agents == ()
    assert data.connected_agent_migration is None
    assert data.profiles[0].default_connected_agent_id is None


def test_migrated_profiles_keep_independent_defaults_and_disconnect_does_not_reassign(
    tmp_path,
):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    initial = registry_data(root, runtime)
    managed_profile = InstallationProfileRecord(
        profile_id=PROFILE_B,
        display_name="Work",
        storage_mode="managed",
        created_at=NOW,
        updated_at=NOW,
    )
    registry.write(
        initial.model_copy(
            update={"schema_version": 1, "profiles": (*initial.profiles, managed_profile)}
        )
    )
    upgraded = registry.load_or_bootstrap(runtime, now=NOW)
    legacy_agent = upgraded.connected_agents[0]
    assert {profile.default_connected_agent_id for profile in upgraded.profiles} == {
        legacy_agent.id
    }
    registry.resume_connected_agent_migration(runtime)

    current = registry.load()
    second = registry.create_connected_agent(
        provider="hermes",
        display_name="Work Hermes",
        avatar_id="hermes",
        default_model_id="model-work",
        default_reasoning_effort="medium",
        connection_config={"endpoint_url": "http://127.0.0.1:9220"},
        credential_reference="vault_ref_work_hermes",
        expected_registry_revision=current.registry_revision,
        idempotency_key="create-work-hermes",
        agent_id="jagent_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        now=NOW,
    )
    current = registry.load()
    registry.set_profile_default_connected_agent(
        PROFILE_B,
        second.id,
        expected_registry_revision=current.registry_revision,
        idempotency_key="set-work-default",
        now=NOW,
    )
    changed = registry.load()
    defaults = {
        profile.profile_id: profile.default_connected_agent_id for profile in changed.profiles
    }
    assert defaults == {PROFILE_A: legacy_agent.id, PROFILE_B: second.id}

    registry.disconnect_connected_agent(
        second.id,
        expected_registry_revision=changed.registry_revision,
        idempotency_key="disconnect-work-hermes",
        now=NOW,
    )
    after_disconnect = registry.load()
    assert (
        next(
            profile.default_connected_agent_id
            for profile in after_disconnect.profiles
            if profile.profile_id == PROFILE_B
        )
        == second.id
    )


def test_connected_agents_persist_cardinality_defaults_disconnect_and_replay(tmp_path):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    initial = registry.load_or_bootstrap(anchored_runtime(tmp_path), now=NOW)
    create_hermes = {
        "provider": "hermes",
        "display_name": "Hermes One",
        "avatar_id": "hermes",
        "default_model_id": "anthropic/claude-opus",
        "default_reasoning_effort": "high",
        "connection_config": {"endpoint_url": "http://127.0.0.1:9120"},
        "credential_reference": "vault_ref_hermes_1",
        "expected_registry_revision": initial.registry_revision,
        "idempotency_key": "create-hermes-one",
        "agent_id": "jagent_11111111111111111111111111111111",
        "now": NOW,
    }
    first = registry.create_connected_agent(**create_hermes)
    assert registry.create_connected_agent(**create_hermes) == first
    assert len(registry.load().connected_agents) == 1

    current = registry.load()
    default = registry.set_profile_default_connected_agent(
        current.active_profile_id,
        first.id,
        expected_registry_revision=current.registry_revision,
        idempotency_key="set-default",
        now=NOW,
    )
    assert default.default_connected_agent_id == first.id

    current = registry.load()
    codex = registry.create_connected_agent(
        provider="codex",
        display_name="Codex",
        avatar_id="codex",
        default_model_id="gpt-5.4",
        default_reasoning_effort="medium",
        connection_config={"runtime_namespace": "jobos-codex"},
        credential_reference="vault_ref_codex_1",
        expected_registry_revision=current.registry_revision,
        idempotency_key="create-codex",
        agent_id="jagent_22222222222222222222222222222222",
        now=NOW,
    )
    current = registry.load()
    with pytest.raises(InstallationProfileConflict, match="durable Codex"):
        registry.create_connected_agent(
            provider="codex",
            display_name="Other Codex",
            avatar_id="codex",
            default_model_id="gpt-5.4",
            default_reasoning_effort="medium",
            connection_config={"runtime_namespace": "jobos-codex-two"},
            credential_reference="vault_ref_codex_2",
            expected_registry_revision=current.registry_revision,
            idempotency_key="create-second-codex",
            agent_id="jagent_33333333333333333333333333333333",
            now=NOW,
        )

    disconnected = registry.disconnect_connected_agent(
        codex.id,
        expected_registry_revision=current.registry_revision,
        idempotency_key="disconnect-codex",
        now=NOW,
    )
    assert disconnected.lifecycle == "disconnected"
    assert disconnected.connection_config is None
    assert disconnected.credential_reference is None
    persisted = registry.load()
    assert next(item for item in persisted.connected_agents if item.id == codex.id) == disconnected
    assert persisted.profiles[0].default_connected_agent_id == first.id
    assert "vault_ref_codex_1" not in registry.path.read_text(encoding="utf-8")

    with pytest.raises(InstallationProfileConflict, match="durable Codex"):
        registry.create_connected_agent(
            provider="codex",
            display_name="Codex Replacement",
            avatar_id="codex",
            default_model_id="gpt-5.4",
            default_reasoning_effort="medium",
            connection_config={"runtime_namespace": "replacement"},
            credential_reference="vault_ref_codex_replacement",
            expected_registry_revision=persisted.registry_revision,
            idempotency_key="replace-disconnected-codex",
            agent_id="jagent_66666666666666666666666666666666",
            now=NOW,
        )

    second_hermes = registry.create_connected_agent(
        provider="hermes",
        display_name="Hermes Two",
        avatar_id="hermes",
        default_model_id="model-two",
        default_reasoning_effort="medium",
        connection_config={"endpoint_url": "http://127.0.0.1:9220"},
        credential_reference="vault_ref_hermes_2",
        expected_registry_revision=persisted.registry_revision,
        idempotency_key="create-hermes-two",
        agent_id="jagent_77777777777777777777777777777777",
        now=NOW,
    )
    assert second_hermes.provider == "hermes"
    assert sum(item.provider == "hermes" for item in registry.load().connected_agents) == 2


def test_registry_write_revalidates_copy_updates_and_rejects_secret_config(tmp_path):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    data = registry.load_or_bootstrap(anchored_runtime(tmp_path), now=NOW)
    invalid = ConnectedAgentRecord(
        id="jagent_44444444444444444444444444444444",
        provider="hermes",
        display_name="Hermes",
        avatar_id="hermes",
        default_model_id="model",
        default_reasoning_effort="high",
        lifecycle="connected",
        connection_config=HermesConnectionConfiguration(endpoint_url="http://127.0.0.1:9120"),
        credential_reference="vault_ref_safe",
        created_at=NOW,
        updated_at=NOW,
    ).model_copy(update={"default_model_id": None})
    with pytest.raises(InstallationProfileRegistryError, match="update is invalid"):
        registry.write(data.model_copy(update={"connected_agents": (invalid,)}))
    with pytest.raises(ValidationError):
        HermesConnectionConfiguration(endpoint_url="http://user:secret@127.0.0.1:9120")
    with pytest.raises(ValidationError):
        ConnectedAgentRecord(
            id="jagent_88888888888888888888888888888888",
            provider="hermes",
            display_name="Hermes",
            avatar_id="hermes",
            default_model_id="model",
            default_reasoning_effort="high",
            lifecycle="connected",
            connection_config=HermesConnectionConfiguration(endpoint_url="http://127.0.0.1:9120"),
            credential_reference="(FAKE)-OAUTH-ACCESS-TOKEN-canary-111",
            created_at=NOW,
            updated_at=NOW,
        )
    with pytest.raises(ValidationError):
        ConnectedAgentRecord(
            id="jagent_99999999999999999999999999999999",
            provider="hermes",
            display_name="Hermes",
            avatar_id="hermes",
            default_model_id="model",
            default_reasoning_effort="high",
            lifecycle="connected",
            connection_config=HermesConnectionConfiguration(endpoint_url="http://127.0.0.1:9120"),
            credential_reference="vault_ref_safe",
            account_summary={"display_name": "(FAKE)-OAUTH-ACCESS-TOKEN-canary-111"},
            created_at=NOW,
            updated_at=NOW,
        )
    persisted = registry.path.read_text(encoding="utf-8")
    assert "user:secret" not in persisted
    assert "(FAKE)-OAUTH-ACCESS-TOKEN-canary-111" not in persisted

    safe_agent = ConnectedAgentRecord(
        id="jagent_44444444444444444444444444444444",
        provider="hermes",
        display_name="Hermes",
        avatar_id="hermes",
        default_model_id="model",
        default_reasoning_effort="high",
        lifecycle="connected",
        connection_config=HermesConnectionConfiguration(endpoint_url="http://127.0.0.1:9120"),
        credential_reference="vault_ref_safe",
        created_at=NOW,
        updated_at=NOW,
    )
    secret_variants = (
        {"display_name": "OAuth access token canary"},
        {"avatar_id": "client_secret_canary"},
        {"default_model_id": "api-key-canary"},
        {"default_reasoning_effort": "device_code_canary"},
        {"credential_reference": "vault_ref_client_secret_canary"},
        {
            "connection_config": HermesConnectionConfiguration(
                endpoint_url="http://127.0.0.1:9120"
            ).model_copy(update={"runtime_profile": "oauth-access-token"})
        },
    )
    for update in secret_variants:
        with pytest.raises(InstallationProfileRegistryError, match="update is invalid"):
            registry.write(
                data.model_copy(
                    update={"connected_agents": (safe_agent.model_copy(update=update),)}
                )
            )
    with pytest.raises(ValidationError):
        HermesConnectionConfiguration(endpoint_url="http://127.0.0.1:9120/api-key-canary")


def test_offline_v1_v31_migration_is_exact_idempotent_and_unknown_model_stays_locked(
    tmp_path,
):
    root = tmp_path / "installation"
    state_path = root / "legacy/state.db"
    state_path.parent.mkdir(parents=True)
    fixture = Path(__file__).parents[3] / "tests/connected_agents/fixtures/(FAKE)-profile-v31.sql"
    with sqlite3.connect(state_path) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))
    runtime = AnchoredRuntime(
        job_provider="sqlite",
        artifact_provider="local",
        state_db_path=state_path,
        jobs_db_path=(root / "legacy/jobs.db").absolute(),
        local_artifact_root=(root / "legacy/artifacts").absolute(),
    )
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    fixture_profile = (
        registry_data(root, runtime)
        .profiles[0]
        .model_copy(update={"profile_id": LEGACY_FIXTURE_PROFILE})
    )
    registry.write(
        registry_data(root, runtime).model_copy(
            update={
                "schema_version": 1,
                "active_profile_id": LEGACY_FIXTURE_PROFILE,
                "profiles": (fixture_profile,),
            }
        )
    )
    legacy_columns, before = legacy_sqlite_snapshot(state_path)
    original_profile_updated_at = fixture_profile.updated_at

    upgraded = registry.load_or_bootstrap(runtime, now=NOW)
    agent = upgraded.connected_agents[0]
    assert agent.lifecycle == "disconnected"
    assert upgraded.profiles[0].default_connected_agent_id == agent.id
    assert upgraded.profiles[0].updated_at == original_profile_updated_at
    completed = registry.resume_connected_agent_migration(runtime, owner_device_id="device-a")
    assert completed.connected_agent_migration is not None
    assert {item.status for item in completed.connected_agent_migration.profiles} == {"complete"}

    _, after = legacy_sqlite_snapshot(state_path, legacy_columns)
    with sqlite3.connect(state_path) as connection:
        binding = connection.execute(
            """SELECT connected_agent_id, provider, model_id, reasoning_effort,
                      binding_state, creation_state, lock_reason
               FROM conversations WHERE conversation_id = 'conv_current'"""
        ).fetchone()
        versions = [
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
    assert after == before
    assert binding == (
        agent.id,
        "hermes",
        None,
        None,
        "legacy_awaiting_resolution",
        "locked",
        "LEGACY_MODEL_UNRESOLVED",
    )
    assert versions[-1] == 32
    assert registry.resume_connected_agent_migration(runtime) == completed
    assert registry.load().connected_agents == (agent,)

    JobOsStateStore(state_path).create_conversation(
        actor_id="device-a",
        connected_agent_id="jagent_55555555555555555555555555555555",
        provider="codex",
        model_id="fake-codex-model",
        reasoning_effort="medium",
    )
    assert registry.resume_connected_agent_migration(runtime) == completed


def test_completed_provider_offline_migration_accepts_late_hermes_runtime_configuration(
    tmp_path,
):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    JobOsStateStore(runtime.state_db_path).initialize(installation_profile_id=PROFILE_A)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    registry.load_or_bootstrap(runtime, now=NOW)

    completed = registry.resume_connected_agent_migration(runtime)
    assert completed.connected_agent_migration is not None
    assert completed.connected_agents[0].lifecycle == "disconnected"
    assert registry.legacy_hermes_configuration_required() is True

    repaired = registry.apply_legacy_hermes_configuration(
        endpoint_url="http://127.0.0.1:9120",
        default_model_id="gpt-5.6-sol-900k",
        default_reasoning_effort="medium",
        now=NOW,
    )

    assert repaired.lifecycle == "connected"
    assert repaired.connection_config is not None
    assert repaired.connection_config.model_dump() == {
        "endpoint_url": "http://127.0.0.1:9120",
        "runtime_profile": None,
    }
    assert repaired.disconnected_at is None
    assert repaired.default_model_id == "gpt-5.6-sol-900k"
    assert repaired.default_reasoning_effort == "medium"
    assert registry.legacy_hermes_configuration_required() is False


def test_completed_legacy_migration_cannot_reconnect_disconnected_agent_on_restart(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    JobOsStateStore(runtime.state_db_path).initialize(installation_profile_id=PROFILE_A)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    registry.load_or_bootstrap(runtime, now=NOW)
    registry.apply_legacy_hermes_configuration(endpoint_url="http://127.0.0.1:9120")
    completed = registry.resume_connected_agent_migration(runtime)
    journal = completed.connected_agent_migration
    assert journal is not None
    disconnected = registry.disconnect_connected_agent(
        journal.connected_agent_id,
        expected_registry_revision=completed.registry_revision,
        idempotency_key="disconnect-after-migration",
        now=NOW,
    )
    assert disconnected.lifecycle == "disconnected"
    assert registry.legacy_hermes_configuration_required() is False

    with pytest.raises(InstallationProfileConflict, match="already complete"):
        registry.apply_legacy_hermes_configuration(endpoint_url="http://127.0.0.1:9120")
    persisted = registry.load()
    migrated = next(
        item for item in persisted.connected_agents if item.id == journal.connected_agent_id
    )
    assert migrated.lifecycle == "disconnected"
    assert migrated.connection_config is None


def test_pending_legacy_migration_cannot_reconnect_explicitly_disconnected_agent(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    JobOsStateStore(runtime.state_db_path).initialize(installation_profile_id=PROFILE_A)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    pending = registry.load_or_bootstrap(runtime, now=NOW)
    journal = pending.connected_agent_migration
    assert journal is not None
    assert {item.status for item in journal.profiles} == {"pending"}

    disconnected = registry.disconnect_connected_agent(
        journal.connected_agent_id,
        expected_registry_revision=pending.registry_revision,
        idempotency_key="disconnect-during-migration",
        now=NOW,
    )

    assert disconnected.lifecycle == "disconnected"
    assert disconnected.updated_at > disconnected.created_at
    persisted = registry.load()
    registry.write(persisted.model_copy(update={"idempotency_replays": ()}))
    assert registry.legacy_hermes_configuration_required() is False
    with pytest.raises(InstallationProfileConflict, match="target was modified"):
        registry.apply_legacy_hermes_configuration(endpoint_url="http://127.0.0.1:9120")


def test_known_legacy_defaults_seal_exact_model_and_binding_is_immutable(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    state = JobOsStateStore(runtime.state_db_path)
    state.initialize(installation_profile_id=PROFILE_A)
    conversation_id = state.first_active_conversation_id()
    state.conversation_store(conversation_id).save_stored_session_id("opaque-session-known")
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    registry.load_or_bootstrap(runtime, now=NOW)
    agent = registry.apply_legacy_hermes_configuration(
        endpoint_url="http://127.0.0.1:9120",
        default_model_id="anthropic/claude-opus",
        default_reasoning_effort="high",
        now=NOW,
    )
    registry.resume_connected_agent_migration(runtime)
    store = JobOsStateStore(runtime.state_db_path)
    conversation = store.conversation_store(conversation_id)
    assert conversation.binding()["binding_state"] == "legacy_awaiting_resolution"
    with pytest.raises(ValueError, match="model_id"):
        conversation.seal_legacy_binding(
            expected_connected_agent_id=agent.id,
            expected_provider_session_id="opaque-session-known",
            model_id="m" * 257,
            reasoning_effort="high",
        )
    with pytest.raises(ValueError, match="reasoning_effort"):
        conversation.seal_legacy_binding(
            expected_connected_agent_id=agent.id,
            expected_provider_session_id="opaque-session-known",
            model_id="anthropic/claude-opus",
            reasoning_effort="e" * 65,
        )
    assert conversation.seal_legacy_binding(
        expected_connected_agent_id=agent.id,
        expected_provider_session_id="opaque-session-known",
        model_id="anthropic/claude-opus",
        reasoning_effort="high",
    )
    assert conversation.binding() == {
        "connected_agent_id": agent.id,
        "provider": "hermes",
        "model_id": "anthropic/claude-opus",
        "reasoning_effort": "high",
        "binding_state": "sealed",
        "provider_session_id": "opaque-session-known",
        "connection_account_fingerprint": None,
        "creation_state": "ready",
        "lock_reason": None,
    }
    with (
        sqlite3.connect(runtime.state_db_path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(
            "UPDATE conversations SET model_id = 'different' WHERE conversation_id = ?",
            (conversation_id,),
        )


def test_profile_wide_five_chat_cap_is_cross_device_transactional_and_archive_frees_slot(
    tmp_path,
):
    store = JobOsStateStore(tmp_path / "profile.db")
    store.initialize(owner_device_id="device-a")
    agent_id = "jagent_55555555555555555555555555555555"

    def create(actor: str):
        return store.create_conversation(
            actor_id=actor,
            connected_agent_id=agent_id,
            provider="hermes",
            model_id="model",
            reasoning_effort="high",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(create, f"device-{index}") for index in range(8)]
    results = []
    limits = 0
    for future in futures:
        try:
            results.append(future.result())
        except ConversationLimit:
            limits += 1
    # The compatibility conversation created by schema bootstrap already occupies one slot.
    assert len(results) == 4
    assert limits == 4
    assert len(store.list_active_conversations(owner_device_id="another-device")) == 5
    store.archive_conversation(str(results[0]["conversation_id"]), actor_id="other-device")
    replacement = create("replacement-device")
    assert replacement["position"] == 5


def test_interleaved_legacy_chats_migrate_to_five_active_without_data_loss(
    tmp_path,
):
    root = tmp_path / "installation"
    state_path = root / "legacy/state.db"
    state_path.parent.mkdir(parents=True)
    fixture = Path(__file__).parents[3] / "tests/connected_agents/fixtures/(FAKE)-profile-v31.sql"
    with sqlite3.connect(state_path) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))
        # v31 positions were unique per owner, so interleaved devices could both
        # own positions 1..3. v32 deterministically keeps the oldest five active
        # and archives overflow without deleting any chat.
        for index in range(2, 8):
            owner_index = index % 2
            owner_position = ((index - 2) // 2) + 1
            connection.execute(
                """INSERT INTO conversations(
                       conversation_id, position, title, stored_session_id,
                       created_at, updated_at, owner_device_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"conv_legacy_{index}",
                    owner_position,
                    f"(FAKE) Legacy chat {index}",
                    f"(FAKE)-opaque-hermes-session-{index}",
                    f"2026-08-0{index}T12:00:00Z",
                    f"2026-08-0{index}T12:00:00Z",
                    f"(FAKE)-device-{owner_index}",
                ),
            )
        connection.commit()
    runtime = AnchoredRuntime(
        job_provider="sqlite",
        artifact_provider="local",
        state_db_path=state_path,
        jobs_db_path=(root / "legacy/jobs.db").absolute(),
        local_artifact_root=(root / "legacy/artifacts").absolute(),
    )
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    fixture_profile = (
        registry_data(root, runtime)
        .profiles[0]
        .model_copy(update={"profile_id": LEGACY_FIXTURE_PROFILE})
    )
    registry.write(
        registry_data(root, runtime).model_copy(
            update={
                "schema_version": 1,
                "active_profile_id": LEGACY_FIXTURE_PROFILE,
                "profiles": (fixture_profile,),
            }
        )
    )
    upgraded = registry.load_or_bootstrap(runtime, now=NOW)
    completed = registry.resume_connected_agent_migration(runtime)
    agent = completed.connected_agents[0]

    store = JobOsStateStore(state_path)
    active = store.list_active_conversations(owner_device_id="new-authorized-device")
    assert len(active) == 5
    assert {item["binding_state"] for item in active} == {"legacy_awaiting_resolution"}
    assert {item["lock_reason"] for item in active} == {"LEGACY_MODEL_UNRESOLVED"}
    with pytest.raises(ConversationLimit):
        store.create_conversation(
            actor_id="new-authorized-device",
            connected_agent_id=agent.id,
            provider="hermes",
            model_id="model",
            reasoning_effort="high",
        )
    with sqlite3.connect(state_path) as connection:
        total, archived = connection.execute(
            "SELECT COUNT(*), SUM(archived_at IS NOT NULL) FROM conversations"
        ).fetchone()
    assert (total, archived) == (7, 2)

    store.archive_conversation(
        str(active[0]["conversation_id"]), actor_id="other-authorized-device"
    )
    replacement = store.create_conversation(
        actor_id="new-authorized-device",
        connected_agent_id=agent.id,
        provider="hermes",
        model_id="model",
        reasoning_effort="high",
    )
    assert replacement["position"] == 5
    assert upgraded.connected_agent_migration is not None


def test_interrupted_cross_store_journal_resumes_without_duplicates(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    store = JobOsStateStore(runtime.state_db_path)
    store.initialize(installation_profile_id=PROFILE_A)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    upgraded = registry.load_or_bootstrap(runtime, now=NOW)
    assert upgraded.connected_agent_migration is not None
    journal = upgraded.connected_agent_migration

    # Simulate a crash after SQLite commits but before the registry advances.
    store.initialize(
        installation_profile_id=PROFILE_A,
        connected_agent_migration_id=journal.migration_id,
        legacy_connected_agent_id=journal.connected_agent_id,
    )
    assert store.connected_agent_migration_status(journal.migration_id) == "sqlite_complete"
    assert registry.migration_for_profile(PROFILE_A) == (
        journal.migration_id,
        journal.connected_agent_id,
        "pending",
    )

    completed = registry.resume_connected_agent_migration(runtime)
    assert completed.connected_agent_migration is not None
    assert completed.connected_agent_migration.profiles[0].status == "complete"
    with sqlite3.connect(runtime.state_db_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM connected_agent_migration_journal").fetchone()[
                0
            ]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(DISTINCT connected_agent_id) FROM conversations"
            ).fetchone()[0]
            == 1
        )


def test_corrupt_cross_store_agent_reference_fails_closed(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    registry.load_or_bootstrap(runtime, now=NOW)
    raw = registry.path.read_text(encoding="utf-8")
    raw = raw.replace(
        '"connected_agents": [',
        '"connected_agents_removed": [',
        1,
    )
    registry.path.write_text(raw, encoding="utf-8")

    with pytest.raises(InstallationProfileRegistryError, match="invalid"):
        registry.load()


def test_corrupt_migration_provider_and_identity_fail_closed(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    migrated = registry.load_or_bootstrap(runtime, now=NOW)
    journal = migrated.connected_agent_migration
    assert journal is not None
    agent = migrated.connected_agents[0]

    with pytest.raises(InstallationProfileRegistryError, match="update is invalid"):
        registry.write(
            migrated.model_copy(
                update={
                    "connected_agents": (agent.model_copy(update={"provider": "codex"}),)
                }
            )
        )

    replacement_id = "jagent_ffffffffffffffffffffffffffffffff"
    replacement = agent.model_copy(update={"id": replacement_id})
    wrong_profiles = tuple(
        profile.model_copy(update={"default_connected_agent_id": replacement_id})
        for profile in migrated.profiles
    )
    with pytest.raises(InstallationProfileRegistryError, match="update is invalid"):
        registry.write(
            migrated.model_copy(
                update={
                    "profiles": wrong_profiles,
                    "connected_agents": (replacement,),
                    "connected_agent_migration": journal.model_copy(
                        update={"connected_agent_id": replacement_id}
                    ),
                }
            )
        )


def test_completed_migration_receipt_survives_later_profile_creation(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    JobOsStateStore(runtime.state_db_path).initialize(installation_profile_id=PROFILE_A)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    registry.load_or_bootstrap(runtime, now=NOW)
    completed = registry.resume_connected_agent_migration(runtime)

    _profiles, created_profile_id = registry.create_with_identity(
        "Later profile",
        idempotency_key="create-profile-after-migration",
        profile_id="jprof_cccccccccccccccccccccccccccccccc",
        now=NOW,
    )

    current = registry.load()
    assert created_profile_id in {profile.profile_id for profile in current.profiles}
    assert current.connected_agent_migration == completed.connected_agent_migration
    assert registry.resume_connected_agent_migration(runtime) == current


def test_completed_registry_migration_revalidates_every_sqlite_receipt(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    JobOsStateStore(runtime.state_db_path).initialize(installation_profile_id=PROFILE_A)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    registry.load_or_bootstrap(runtime, now=NOW)
    completed = registry.resume_connected_agent_migration(runtime)
    journal = completed.connected_agent_migration
    assert journal is not None

    with sqlite3.connect(runtime.state_db_path) as connection:
        connection.execute(
            "DELETE FROM connected_agent_migration_journal WHERE migration_id = ?",
            (journal.migration_id,),
        )

    with pytest.raises(InstallationProfileRegistryError, match="receipt"):
        registry.resume_connected_agent_migration(runtime)


def test_completed_registry_migration_revalidates_full_conversation_state(tmp_path):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    store = JobOsStateStore(runtime.state_db_path)
    store.initialize(installation_profile_id=PROFILE_A)
    created = store.create_conversation(actor_id="device-a")
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    registry.load_or_bootstrap(runtime, now=NOW)
    completed = registry.resume_connected_agent_migration(runtime)
    assert completed.connected_agent_migration is not None

    # Simulate corruption beneath the normal SQLite invariant triggers. A completed
    # registry receipt must never mask a damaged model/lock binding.
    with sqlite3.connect(runtime.state_db_path) as connection:
        connection.execute("DROP TRIGGER conversations_binding_valid_update")
        connection.execute(
            "UPDATE conversations SET creation_state = 'ready' WHERE conversation_id = ?",
            (created["conversation_id"],),
        )

    with pytest.raises(InstallationProfileRegistryError, match="binding"):
        registry.resume_connected_agent_migration(runtime)


def test_connected_agent_mutation_replays_return_original_responses(tmp_path):
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    current = registry.load_or_bootstrap(anchored_runtime(tmp_path), now=NOW)
    profile_id = current.active_profile_id
    agent = registry.create_connected_agent(
        provider="hermes",
        display_name="Hermes",
        avatar_id="hermes",
        default_model_id="model-one",
        default_reasoning_effort="high",
        connection_config={"endpoint_url": "http://127.0.0.1:9120"},
        credential_reference="vault_ref_replay",
        expected_registry_revision=current.registry_revision,
        idempotency_key="create-replay-agent",
        agent_id="jagent_dddddddddddddddddddddddddddddddd",
        now=NOW,
    )
    assert agent.connection_config is None
    assert agent.credential_reference is None
    persisted_agent = next(item for item in registry.load().connected_agents if item.id == agent.id)
    assert persisted_agent.connection_config is not None
    assert persisted_agent.credential_reference == "vault_ref_replay"
    first_revision = registry.load().registry_revision
    first_update = registry.update_connected_agent(
        agent.id,
        display_name="Hermes First",
        avatar_id="hermes",
        default_model_id="model-one",
        default_reasoning_effort="high",
        expected_registry_revision=first_revision,
        idempotency_key="update-first",
        now=NOW,
    )
    second_revision = registry.load().registry_revision
    registry.update_connected_agent(
        agent.id,
        display_name="Hermes Second",
        avatar_id="hermes",
        default_model_id="model-two",
        default_reasoning_effort="medium",
        expected_registry_revision=second_revision,
        idempotency_key="update-second",
        now=NOW,
    )
    assert registry.update_connected_agent(
        agent.id,
        display_name="Hermes First",
        avatar_id="hermes",
        default_model_id="model-one",
        default_reasoning_effort="high",
        expected_registry_revision=first_revision,
        idempotency_key="update-first",
        now=NOW,
    ) == first_update

    default_revision = registry.load().registry_revision
    first_default = registry.set_profile_default_connected_agent(
        profile_id,
        agent.id,
        expected_registry_revision=default_revision,
        idempotency_key="default-first",
        now=NOW,
    )
    clear_revision = registry.load().registry_revision
    registry.set_profile_default_connected_agent(
        profile_id,
        None,
        expected_registry_revision=clear_revision,
        idempotency_key="default-clear",
        now=NOW,
    )
    assert registry.set_profile_default_connected_agent(
        profile_id,
        agent.id,
        expected_registry_revision=default_revision,
        idempotency_key="default-first",
        now=NOW,
    ) == first_default

    disconnect_revision = registry.load().registry_revision
    registry.disconnect_connected_agent(
        agent.id,
        expected_registry_revision=disconnect_revision,
        idempotency_key="disconnect-replay-agent",
        now=NOW,
    )
    replayed_create = registry.create_connected_agent(
        provider="hermes",
        display_name="Hermes",
        avatar_id="hermes",
        default_model_id="model-one",
        default_reasoning_effort="high",
        connection_config={"endpoint_url": "http://127.0.0.1:9120"},
        credential_reference="vault_ref_replay",
        expected_registry_revision=current.registry_revision,
        idempotency_key="create-replay-agent",
        agent_id="jagent_dddddddddddddddddddddddddddddddd",
        now=NOW,
    )
    assert replayed_create == agent
    assert registry.update_connected_agent(
        agent.id,
        display_name="Hermes First",
        avatar_id="hermes",
        default_model_id="model-one",
        default_reasoning_effort="high",
        expected_registry_revision=first_revision,
        idempotency_key="update-first",
        now=NOW,
    ) == first_update


def test_conversation_account_fingerprint_is_sha256_at_code_and_sql_boundaries(tmp_path):
    opaque_account_id = "acct_opaque_123"
    expected_fingerprint = hashlib.sha256(
        f"codex-account-v1\0{opaque_account_id}".encode()
    ).hexdigest()
    assert codex_account_fingerprint(opaque_account_id) == expected_fingerprint
    assert codex_account_fingerprint(opaque_account_id) == expected_fingerprint
    assert codex_account_fingerprint("acct_opaque_456") != expected_fingerprint
    with pytest.raises(ValueError, match="opaque account"):
        codex_account_fingerprint("person@example.com\n")

    database = tmp_path / "profile.db"
    store = JobOsStateStore(database)
    store.initialize(owner_device_id="device-a")
    binding = {
        "connected_agent_id": "jagent_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "provider": "codex",
        "model_id": "gpt-5",
        "reasoning_effort": "medium",
    }

    with pytest.raises(ValueError, match="fingerprint"):
        store.create_conversation(
            actor_id="device-a",
            connection_account_fingerprint="raw-account@example.com",
            **binding,
        )
    with pytest.raises(ValueError, match="Connected Agent"):
        store.create_conversation(
            actor_id="device-a",
            connection_account_fingerprint=expected_fingerprint,
            **{**binding, "connected_agent_id": "not-an-agent"},
        )
    with pytest.raises(ValueError, match="model"):
        store.create_conversation(
            actor_id="device-a",
            connection_account_fingerprint=expected_fingerprint,
            **{**binding, "model_id": ""},
        )
    with pytest.raises(ValueError, match="reasoning"):
        store.create_conversation(
            actor_id="device-a",
            connection_account_fingerprint=expected_fingerprint,
            **{**binding, "reasoning_effort": "x" * 65},
        )

    created = store.create_conversation(
        actor_id="device-a",
        connection_account_fingerprint=expected_fingerprint,
        **binding,
    )
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE conversations SET connection_account_fingerprint = ? WHERE conversation_id = ?",
            ("b" * 64, created["conversation_id"]),
        )

    invalid_bindings = (
        ("jagent_" + "a" * 31 + "z", "gpt-5", "medium"),
        (binding["connected_agent_id"], "", "medium"),
        (binding["connected_agent_id"], "gpt-5", "x" * 65),
    )
    for index, (agent_id, model_id, reasoning_effort) in enumerate(invalid_bindings, start=2):
        with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO conversations(
                    conversation_id, position, title, owner_device_id,
                    connected_agent_id, provider, model_id, reasoning_effort,
                    binding_state, creation_state
                ) VALUES (?, ?, 'Invalid binding', 'device-a', ?, 'codex', ?, ?,
                          'sealed', 'provisioning')
                """,
                (f"conv_invalid_{index}", index, agent_id, model_id, reasoning_effort),
            )

    malformed_journal_ids = (
        ("jagentmig_" + "a" * 31 + "z", binding["connected_agent_id"]),
        ("jagentmig_" + "a" * 32, "jagent_" + "a" * 31 + "z"),
    )
    for migration_id, agent_id in malformed_journal_ids:
        with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO connected_agent_migration_journal(
                    migration_id, connected_agent_id, status
                ) VALUES (?, ?, 'sqlite_complete')
                """,
                (migration_id, agent_id),
            )


def test_unbound_locked_conversation_rejects_turns(tmp_path):
    store = JobOsStateStore(tmp_path / "profile.db")
    store.initialize(owner_device_id="device-a", installation_profile_id=PROFILE_A)
    created = store.create_conversation(actor_id="device-a")
    conversation_id = str(created["conversation_id"])
    with sqlite3.connect(tmp_path / "profile.db") as connection:
        connection.execute(
            """
            UPDATE conversations
            SET creation_state = 'locked', lock_reason = 'AGENT_NOT_CONFIGURED'
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        )
    conversation = store.conversation_store(conversation_id)

    with pytest.raises(ConversationBusy, match="AGENT_NOT_CONFIGURED"):
        conversation.create_turn(
            text="This must not route",
            context={},
            idempotency_key="locked-turn",
            actor_id="device-a",
        )


def test_registry_upgrade_replace_failure_preserves_v1_and_retries(tmp_path, monkeypatch):
    root = tmp_path / "installation"
    runtime = anchored_runtime(root)
    registry = InstallationProfileRegistry(root / "installation-profiles.json")
    registry.write(registry_data(root, runtime).model_copy(update={"schema_version": 1}))
    before = registry.path.read_bytes()
    original_replace = installation_profiles_module.os.replace

    def fail_replace(_source, _target):
        raise OSError("injected registry replace failure")

    monkeypatch.setattr(installation_profiles_module.os, "replace", fail_replace)
    with pytest.raises(InstallationProfileRegistryError, match="could not be saved"):
        registry.load_or_bootstrap(runtime, now=NOW)
    assert registry.path.read_bytes() == before
    assert registry.load().schema_version == 1

    monkeypatch.setattr(installation_profiles_module.os, "replace", original_replace)
    assert registry.load_or_bootstrap(runtime, now=NOW).schema_version == 2


def test_sqlite_v32_statement_failure_rolls_back_and_retries(tmp_path, monkeypatch):
    state_path = tmp_path / "legacy-state.db"
    fixture = Path(__file__).parents[3] / "tests/connected_agents/fixtures/(FAKE)-profile-v31.sql"
    with sqlite3.connect(state_path) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))
    legacy_columns, before = legacy_sqlite_snapshot(state_path)
    original_apply = JobOsStateStore._apply_migration_statements

    def fail_v32(connection, migration):
        if migration.version == 32:
            connection.execute(migration.statements[0])
            raise RuntimeError("injected v32 statement failure")
        original_apply(connection, migration)

    monkeypatch.setattr(
        JobOsStateStore,
        "_apply_migration_statements",
        staticmethod(fail_v32),
    )
    store = JobOsStateStore(state_path)
    with pytest.raises(RuntimeError, match="injected v32 statement failure"):
        store.initialize(
            installation_profile_id=LEGACY_FIXTURE_PROFILE,
            connected_agent_migration_id="jagentmig_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            legacy_connected_agent_id="jagent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
    assert store.health().schema_version == 31
    _, after_failure = legacy_sqlite_snapshot(state_path, legacy_columns)
    assert after_failure == before

    monkeypatch.setattr(
        JobOsStateStore,
        "_apply_migration_statements",
        staticmethod(original_apply),
    )
    store.initialize(
        installation_profile_id=LEGACY_FIXTURE_PROFILE,
        connected_agent_migration_id="jagentmig_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        legacy_connected_agent_id="jagent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert store.health().schema_version == 32

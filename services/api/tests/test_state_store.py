import json
import sqlite3

import pytest
from jobos_api.state_store import (
    SCHEMA_VERSION,
    IncompatibleSchemaError,
    JobOsStateStore,
    Migration,
)


def applied_versions(path):
    with sqlite3.connect(path) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]


def metadata_columns(path):
    with sqlite3.connect(path) as connection:
        return {
            row[1] for row in connection.execute("PRAGMA table_info(jobos_metadata)").fetchall()
        }


def test_initialization_applies_every_migration_once(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)

    first = store.initialize()
    second = store.initialize()

    assert first.schema_version == SCHEMA_VERSION == 5
    assert second.schema_version == SCHEMA_VERSION
    assert applied_versions(database) == [1, 2, 3, 4, 5]
    assert metadata_columns(database) == {"key", "value", "updated_at"}


def test_initialization_upgrades_a_behind_database(tmp_path):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")

    result = JobOsStateStore(database).initialize()

    assert result.schema_version == SCHEMA_VERSION
    assert applied_versions(database) == [1, 2, 3, 4, 5]
    assert metadata_columns(database) == {"key", "value", "updated_at"}


@pytest.mark.parametrize("versions", ([1, 2, 3, 4, 5, 6], [2]))
def test_initialization_rejects_ahead_or_incompatible_history(tmp_path, versions):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            [(version,) for version in versions],
        )

    with pytest.raises(IncompatibleSchemaError):
        JobOsStateStore(database).initialize()

    assert applied_versions(database) == versions


def test_a_failed_migration_rolls_back_its_schema_and_ledger_entry(tmp_path):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        migration = Migration(
            version=1,
            statements=("CREATE TABLE partial_change (id INTEGER)", "INVALID SQL"),
        )

        with pytest.raises(sqlite3.OperationalError):
            JobOsStateStore._apply_migration(connection, migration)

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "partial_change" not in tables
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (0,)


def test_workspace_snapshot_is_atomic_revisioned_and_preserves_job_selection(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    store.save_job_selection("job-7", "user")

    initial = store.workspace_snapshot("device-a")
    saved = store.save_workspace_snapshot(
        "device-a",
        expected_revision=0,
        snapshot={
            **initial.snapshot,
            "selected_preset": "research",
            "selected_job_id": "job-7",
            "active_center_surface": "browser",
        },
        idempotency_key="workspace-save-atomic-1",
        origin="user",
        actor_id="device-a",
    )

    assert saved.revision == 1
    assert store.workspace_snapshot("device-a").snapshot["selected_job_id"] == "job-7"

    with pytest.raises(Exception, match="revision conflict"):
        store.save_workspace_snapshot(
            "device-a",
            expected_revision=0,
            snapshot=initial.snapshot,
            idempotency_key="workspace-save-stale-1",
            origin="user",
            actor_id="device-a",
        )

    assert store.workspace_snapshot("device-a").revision == 1


def test_stale_layout_snapshot_never_rolls_back_newer_job_selection(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    store.save_job_selection("job-1", "user")
    stale_layout = store.workspace_snapshot("device-a")

    store.save_job_selection("job-2", "mcp")
    saved = store.save_workspace_snapshot(
        "device-a",
        expected_revision=stale_layout.revision,
        snapshot={
            **stale_layout.snapshot,
            "selected_preset": "research",
        },
        idempotency_key="workspace-selection-race-1",
        origin="mcp",
        actor_id="device-a",
    )

    assert saved.snapshot["selected_job_id"] == "job-2"
    assert store.job_workspace_state().selected_job_id == "job-2"
    assert store.workspace_snapshot("device-a").snapshot["selected_job_id"] == "job-2"


def test_workspace_snapshot_retry_is_idempotent_and_records_one_safe_audit(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    initial = store.workspace_snapshot("device-a")
    command = {
        **initial.snapshot,
        "selected_preset": "research",
        "active_center_surface": "browser",
    }

    first = store.save_workspace_snapshot(
        "device-a",
        expected_revision=0,
        snapshot=command,
        idempotency_key="workspace-save-1",
        origin="user",
        actor_id="device-a",
    )
    retry = store.save_workspace_snapshot(
        "device-a",
        expected_revision=0,
        snapshot=command,
        idempotency_key="workspace-save-1",
        origin="user",
        actor_id="device-a",
    )
    audit = store.list_mutation_audit()

    assert first == retry
    assert store.workspace_snapshot("device-a").revision == 1
    assert len(audit) == 1
    assert audit[0] == {
        "origin": "user",
        "actor_id": "device-a",
        "target_resource": "workspace/device-a",
        "command_name": "workspace_snapshot.save",
        "outcome": "succeeded",
        "occurred_at": audit[0]["occurred_at"],
        "detail": {
            "revision": 1,
            "selected_preset": "research",
            "active_center_surface": "browser",
            "repaired_presets": [],
        },
    }
    assert "selected_job_id" not in audit[0]["detail"]


def test_corrupt_layout_repairs_only_the_affected_preset(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    store.save_job_selection("job-9", "user")
    initial = store.workspace_snapshot("device-a")
    corrupt = {
        **initial.snapshot,
        "selected_preset": "review",
        "layouts": {
            **initial.snapshot["layouts"],
            "review": {"order": ["unknown"], "widths": {"agent": -1}},
        },
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO workspace_snapshots(device_id, revision, snapshot_json) VALUES (?, ?, ?)",
            ("device-a", 4, json.dumps(corrupt)),
        )

    restored = store.workspace_snapshot("device-a")

    assert restored.revision == 4
    assert restored.repaired_presets == ("review",)
    assert restored.snapshot["layouts"]["review"] == initial.snapshot["layouts"]["review"]
    assert restored.snapshot["layouts"]["research"] == initial.snapshot["layouts"]["research"]
    assert restored.snapshot["selected_job_id"] == "job-9"

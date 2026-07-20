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

    assert first.schema_version == SCHEMA_VERSION == 2
    assert second.schema_version == SCHEMA_VERSION
    assert applied_versions(database) == [1, 2]
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
    assert applied_versions(database) == [1, 2]
    assert metadata_columns(database) == {"key", "value", "updated_at"}


@pytest.mark.parametrize("versions", ([1, 2, 3], [2]))
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

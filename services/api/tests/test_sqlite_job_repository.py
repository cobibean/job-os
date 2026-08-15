from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest
from job_repository_contract import command, exercise_repository_contract
from jobos_api.job_repository import Conflict, NotFound
from jobos_api.job_repository_migrations import (
    MIGRATIONS,
    Migration,
    MigrationError,
    MigrationHistoryError,
)
from jobos_api.sqlite_job_repository import SQLiteJobRepository


def test_sqlite_satisfies_repository_contract_and_restarts(tmp_path):
    database = tmp_path / "jobs.db"
    repository = SQLiteJobRepository(database)
    exercise_repository_contract(repository)

    restarted = SQLiteJobRepository(database)
    record = restarted.get_job("contract-created")
    assert record.status == "applied"
    assert record.description == "Updated by contract."
    with pytest.raises(FrozenInstanceError):
        record.title = "Mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record.listing_evidence["changed"] = True  # type: ignore[index]
    immutable_record = restarted.create_job(
        command("immutability", "https://jobs.example.com/roles/immutable")
    )
    with pytest.raises(TypeError):
        immutable_record.listing_evidence["coverage"]["changed"] = True  # type: ignore[index]
    with pytest.raises(NotFound):
        restarted.get_job("missing")


def test_missing_listing_completeness_defaults_to_unknown(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")

    record = repository.create_job(
        replace(command("unknown-completeness"), listing_completeness=None)
    )

    assert record.listing_completeness == "unknown"


def test_duplicate_refresh_preserves_newer_higher_quality_listing(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")
    captured_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    original = repository.create_job(command("original", observed_at=captured_at))

    lower_quality = repository.create_job(
        replace(
            command("lower-quality", observed_at=captured_at + timedelta(hours=1)),
            company_name="Fresh Company Name",
            description_text="A newer summary must not erase the full listing.",
            full_listing_text="A newer summary must not erase the full listing.",
            analysis_text=None,
            listing_completeness=None,
            listing_captured_at=captured_at + timedelta(hours=1),
            listing_verified_at=None,
            listing_sha256=None,
            listing_evidence={},
        )
    )

    assert lower_quality.job_id == original.job_id
    assert lower_quality.company == "Fresh Company Name"
    assert lower_quality.last_seen_at == captured_at + timedelta(hours=1)
    assert lower_quality.description == original.description
    assert lower_quality.full_listing_text == original.full_listing_text
    assert lower_quality.analysis_text == original.analysis_text
    assert lower_quality.listing_completeness == "complete"
    assert lower_quality.listing_verified_at == original.listing_verified_at
    assert lower_quality.listing_sha256 == original.listing_sha256
    assert lower_quality.listing_evidence == original.listing_evidence


def test_stale_duplicate_does_not_regress_metadata_or_last_seen(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")
    captured_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    original = repository.create_job(command("original", observed_at=captured_at))

    stale = repository.create_job(
        replace(
            command("stale", observed_at=captured_at - timedelta(hours=1)),
            company_name="Stale Company Name",
            title="Stale title",
            description_text="Stale listing text",
            full_listing_text="Stale listing text",
            listing_captured_at=captured_at - timedelta(hours=1),
        )
    )

    assert stale.job_id == original.job_id
    assert stale.company == original.company
    assert stale.title == original.title
    assert stale.description == original.description
    assert stale.last_seen_at == original.last_seen_at


def test_older_higher_completeness_cannot_move_capture_backward(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")
    captured_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    original = repository.create_job(
        replace(
            command("original", observed_at=captured_at),
            listing_completeness="partial",
        )
    )

    older_complete = repository.create_job(
        replace(
            command("older-complete", observed_at=captured_at + timedelta(hours=1)),
            description_text="Older complete capture.",
            full_listing_text="Older complete capture.",
            listing_captured_at=captured_at - timedelta(hours=1),
        )
    )

    assert older_complete.job_id == original.job_id
    assert older_complete.description == original.description
    assert older_complete.listing_completeness == "partial"
    assert older_complete.listing_captured_at == original.listing_captured_at
    assert older_complete.listing_sha256 == original.listing_sha256


def test_newer_equal_quality_cannot_erase_verified_provenance(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")
    captured_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    original = repository.create_job(command("original", observed_at=captured_at))

    unverified = repository.create_job(
        replace(
            command("unverified", observed_at=captured_at + timedelta(hours=1)),
            description_text="Unverified replacement.",
            full_listing_text="Unverified replacement.",
            analysis_text=None,
            listing_captured_at=captured_at + timedelta(hours=1),
            listing_verified_at=None,
            listing_sha256=None,
            listing_evidence={},
        )
    )

    assert unverified.job_id == original.job_id
    assert unverified.description == original.description
    assert unverified.listing_verified_at == original.listing_verified_at
    assert unverified.analysis_text == original.analysis_text
    assert unverified.listing_evidence == original.listing_evidence
    assert unverified.listing_sha256 == original.listing_sha256


def test_fresher_verified_capture_can_replace_without_analysis(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")
    captured_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    original = repository.create_job(command("original", observed_at=captured_at))

    fresher = repository.create_job(
        replace(
            command("fresher", observed_at=captured_at + timedelta(hours=1)),
            description_text="Fresher verified listing.",
            full_listing_text="Fresher verified listing.",
            analysis_text=None,
            listing_captured_at=captured_at + timedelta(hours=1),
            listing_verified_at=captured_at + timedelta(hours=1),
            listing_sha256="b" * 64,
        )
    )

    assert fresher.job_id == original.job_id
    assert fresher.description == "Fresher verified listing."
    assert fresher.analysis_text is None
    assert fresher.listing_captured_at == captured_at + timedelta(hours=1)
    assert fresher.listing_sha256 == "b" * 64


def test_description_update_invalidates_derived_listing_provenance(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")
    created = repository.create_job(command("description-update"))
    assert created.listing_completeness == "complete"
    assert created.listing_verified_at is not None
    assert created.listing_evidence

    updated = repository.update_description(
        created.job_id,
        "New listing bytes.",
        source="manual_test",
        provenance="synthetic",
    )

    assert updated.description == "New listing bytes."
    assert updated.full_listing_text == "New listing bytes."
    assert updated.analysis_text is None
    assert updated.listing_completeness == "unknown"
    assert updated.listing_verified_at is None
    assert updated.listing_source_url is None
    assert updated.listing_evidence == {}
    assert updated.listing_sha256 != created.listing_sha256


def test_duplicate_url_creation_is_race_safe(tmp_path):
    database = tmp_path / "jobs.db"
    SQLiteJobRepository(database)

    def create(index: int):
        repository = SQLiteJobRepository(database)
        return repository.create_job(command(f"concurrent-{index}"))

    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(create, range(16)))

    repository = SQLiteJobRepository(database)
    assert len({record.job_id for record in records}) == 1
    assert len(repository.list_jobs()) == 1
    assert records[0].job_id in {f"concurrent-{index}" for index in range(16)}


def test_concurrent_initialization_applies_schema_once(tmp_path):
    database = tmp_path / "jobs.db"
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: SQLiteJobRepository(database), range(16)))

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version, COUNT(*) FROM job_repository_migrations GROUP BY version"
        ).fetchall() == [(1, 1)]


def _second_migration(counter: list[str], *, destructive: bool = False) -> Migration:
    def apply(connection: sqlite3.Connection) -> None:
        counter.append("applied")
        connection.execute("CREATE TABLE migration_two(value TEXT)")

    return Migration(
        version=2,
        name="migration_two",
        checksum="sha256:migration-two",
        apply=apply,
        destructive=destructive,
    )


def test_migration_applies_once_and_non_destructive_steps_make_no_backup(tmp_path):
    database = tmp_path / "jobs.db"
    backup_directory = tmp_path / "backups"
    counter: list[str] = []
    migrations = (*MIGRATIONS, _second_migration(counter))

    SQLiteJobRepository(
        database, migrations=migrations, backup_directory=backup_directory
    )
    SQLiteJobRepository(
        database, migrations=migrations, backup_directory=backup_directory
    )

    assert counter == ["applied"]
    assert not backup_directory.exists()


def test_failed_migration_rolls_back_schema_and_ledger(tmp_path):
    database = tmp_path / "jobs.db"
    SQLiteJobRepository(database)

    def fail(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE must_rollback(value TEXT)")
        raise RuntimeError("injected migration failure")

    migration = Migration(
        version=2,
        name="failing",
        checksum="sha256:failing",
        apply=fail,
    )
    with pytest.raises(MigrationError, match="injected migration failure"):
        SQLiteJobRepository(database, migrations=(*MIGRATIONS, migration))

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'must_rollback'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT version FROM job_repository_migrations ORDER BY version"
        ).fetchall() == [(1,)]


@pytest.mark.parametrize("history_kind", ["ahead", "non-prefix"])
def test_ahead_and_non_prefix_histories_fail_clearly(tmp_path, history_kind):
    database = tmp_path / f"{history_kind}.db"
    SQLiteJobRepository(database)
    with sqlite3.connect(database) as connection:
        if history_kind == "ahead":
            connection.execute(
                """
                INSERT INTO job_repository_migrations(version, name, checksum, applied_at)
                VALUES (99, 'future', 'sha256:future', '2026-08-15T00:00:00+00:00')
                """
            )
        else:
            connection.execute(
                "UPDATE job_repository_migrations SET checksum = 'sha256:diverged'"
            )
        connection.commit()

    with pytest.raises(MigrationHistoryError, match="ahead|valid prefix"):
        SQLiteJobRepository(database)


def test_destructive_migration_requires_and_verifies_pre_migration_backup(tmp_path):
    database = tmp_path / "jobs.db"
    backup_directory = tmp_path / "backups"
    counter: list[str] = []
    SQLiteJobRepository(database, migrations=(*MIGRATIONS, _second_migration(counter)))

    def destructive(connection: sqlite3.Connection) -> None:
        connection.execute("DROP TABLE migration_two")

    migration = Migration(
        version=3,
        name="drop_migration_two",
        checksum="sha256:drop-migration-two",
        apply=destructive,
        destructive=True,
    )
    SQLiteJobRepository(
        database,
        migrations=(*MIGRATIONS, _second_migration(counter), migration),
        backup_directory=backup_directory,
    )

    backups = list(backup_directory.glob("*.sqlite3"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'migration_two'"
        ).fetchone() == ("migration_two",)


def test_destructive_migration_does_not_run_when_backup_cannot_be_created(tmp_path):
    database = tmp_path / "jobs.db"
    counter: list[str] = []
    second = _second_migration(counter)
    SQLiteJobRepository(database, migrations=(*MIGRATIONS, second))
    invalid_backup_directory = tmp_path / "not-a-directory"
    invalid_backup_directory.write_text("blocked", encoding="utf-8")
    destructive_calls: list[str] = []

    def destructive(connection: sqlite3.Connection) -> None:
        destructive_calls.append("called")
        connection.execute("DROP TABLE migration_two")

    migration = Migration(
        version=3,
        name="blocked_destructive",
        checksum="sha256:blocked-destructive",
        apply=destructive,
        destructive=True,
    )
    with pytest.raises(MigrationError, match="initialization failed"):
        SQLiteJobRepository(
            database,
            migrations=(*MIGRATIONS, second, migration),
            backup_directory=invalid_backup_directory,
        )

    assert destructive_calls == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM job_repository_migrations"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'migration_two'"
        ).fetchone() == ("migration_two",)


def test_job_id_conflicts_do_not_overwrite_an_unrelated_url(tmp_path):
    repository = SQLiteJobRepository(tmp_path / "jobs.db")
    repository.create_job(command("same-id", "https://example.com/first"))
    with pytest.raises(Conflict):
        repository.create_job(command("same-id", "https://example.com/second"))

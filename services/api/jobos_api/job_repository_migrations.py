from __future__ import annotations

import fcntl
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from jobos_api.job_repository import Unavailable


class MigrationError(Unavailable):
    """The canonical jobs schema could not be initialized safely."""


class MigrationHistoryError(MigrationError):
    """The database ledger is ahead of or diverges from this application."""


MigrationStep = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    checksum: str
    apply: MigrationStep
    destructive: bool = False


def _create_canonical_jobs_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE canonical_jobs (
            job_id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL UNIQUE,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            description TEXT NOT NULL,
            location TEXT,
            application_url TEXT,
            full_listing_text TEXT,
            analysis_text TEXT,
            listing_completeness TEXT,
            listing_source_url TEXT,
            listing_captured_at TEXT,
            listing_verified_at TEXT,
            listing_capture_method TEXT,
            listing_sha256 TEXT,
            listing_evidence_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE canonical_job_history (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES canonical_jobs(job_id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            occurred_at TEXT NOT NULL,
            reason TEXT,
            source TEXT,
            provenance TEXT,
            from_sha256 TEXT,
            to_sha256 TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX canonical_job_history_job_id ON canonical_job_history(job_id, event_id)"
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="create_canonical_jobs",
        checksum="sha256:7681d21619b250c8c289418ba6578161e457d2c08f8c4da9f9a8814097450106",
        apply=_create_canonical_jobs_schema,
    ),
)

_INITIALIZATION_LOCK = Lock()


def _validate_catalog(migrations: Sequence[Migration]) -> None:
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise ValueError("Job repository migrations must be a contiguous sequence starting at 1")
    if len({migration.name for migration in migrations}) != len(migrations):
        raise ValueError("Job repository migration names must be unique")


@contextmanager
def _process_lock(database_path: Path):
    lock_path = database_path.with_name(f".{database_path.name}.migrate.lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_ledger(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS job_repository_migrations (
                application_order INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL UNIQUE,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _applied_history(connection: sqlite3.Connection) -> list[tuple[int, str, str]]:
    rows = connection.execute(
        """
        SELECT version, name, checksum
        FROM job_repository_migrations
        ORDER BY application_order
        """
    ).fetchall()
    return [(int(row["version"]), str(row["name"]), str(row["checksum"])) for row in rows]


def _validate_history(
    applied: Sequence[tuple[int, str, str]], migrations: Sequence[Migration]
) -> None:
    expected = [(item.version, item.name, item.checksum) for item in migrations]
    if applied == expected[: len(applied)]:
        return
    known_versions = {item.version for item in migrations}
    ahead = [version for version, _, _ in applied if version not in known_versions]
    if ahead:
        raise MigrationHistoryError(
            f"Canonical jobs database schema is ahead of this application: {ahead}"
        )
    raise MigrationHistoryError(
        "Canonical jobs database migration history is not a valid prefix of this application"
    )


def _verified_backup(
    database_path: Path, migration: Migration, backup_directory: Path | None
) -> Path:
    directory = backup_directory or database_path.with_name(f"{database_path.name}.backups")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = directory / (
        f"{database_path.stem}-before-v{migration.version}-{stamp}-{uuid4().hex[:8]}.sqlite3"
    )
    source = sqlite3.connect(database_path, timeout=30)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        destination.commit()
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise MigrationError(
                f"Backup verification failed before destructive migration {migration.version}"
            )
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        destination.close()
        source.close()
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        backup_path.unlink(missing_ok=True)
        raise MigrationError(
            f"Backup verification failed before destructive migration {migration.version}"
        )
    return backup_path


def initialize_job_repository_database(
    database_path: Path,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
    backup_directory: Path | None = None,
) -> None:
    """Apply each pending migration once under thread and process locks."""
    _validate_catalog(migrations)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _INITIALIZATION_LOCK, _process_lock(database_path):
            connection = _connect(database_path)
            try:
                _create_ledger(connection)
                applied = _applied_history(connection)
                _validate_history(applied, migrations)
                for migration in migrations[len(applied) :]:
                    if migration.destructive:
                        _verified_backup(database_path, migration, backup_directory)
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        migration.apply(connection)
                        connection.execute(
                            """
                            INSERT INTO job_repository_migrations(
                                version, name, checksum, applied_at
                            )
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                migration.version,
                                migration.name,
                                migration.checksum,
                                datetime.now(UTC).isoformat(),
                            ),
                        )
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
            finally:
                connection.close()
    except MigrationError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise MigrationError(f"Canonical jobs database initialization failed: {error}") from error
    except Exception as error:
        raise MigrationError(
            f"Canonical jobs database migration failed: {error}"
        ) from error

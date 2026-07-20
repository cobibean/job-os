import sqlite3
from dataclasses import dataclass
from pathlib import Path


class IncompatibleSchemaError(RuntimeError):
    """The state database cannot be safely opened by this JobOS build."""


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        version=1,
        # The shipped Phase 1 baseline stamped version 1 without owned tables.
        # Keeping that history explicit lets existing databases upgrade safely.
        statements=(),
    ),
    Migration(
        version=2,
        statements=(
            """
            CREATE TABLE jobos_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """,
        ),
    ),
)
SCHEMA_VERSION = MIGRATIONS[-1].version


@dataclass(frozen=True)
class StateHealth:
    schema_version: int


class JobOsStateStore:
    """Owns JobOS workbench persistence behind one small Interface."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> StateHealth:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            self._ensure_migration_ledger(connection)
            applied = self._applied_versions(connection)
            self._assert_compatible(applied)
            for migration in MIGRATIONS[len(applied) :]:
                self._apply_migration(connection, migration)
        return StateHealth(schema_version=SCHEMA_VERSION)

    @staticmethod
    def _ensure_migration_ledger(connection: sqlite3.Connection) -> None:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    @staticmethod
    def _applied_versions(connection: sqlite3.Connection) -> list[int]:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

    @staticmethod
    def _assert_compatible(applied: list[int]) -> None:
        if applied and applied[-1] > SCHEMA_VERSION:
            raise IncompatibleSchemaError(
                f"state schema {applied[-1]} is newer than supported schema {SCHEMA_VERSION}"
            )
        expected = list(range(1, len(applied) + 1))
        if applied != expected:
            raise IncompatibleSchemaError(
                f"state migration history is not an ordered prefix: {applied}"
            )

    @staticmethod
    def _apply_migration(connection: sqlite3.Connection, migration: Migration) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)",
                (migration.version,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def health(self) -> StateHealth:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        version = int(row[0]) if row and row[0] is not None else 0
        return StateHealth(schema_version=version)

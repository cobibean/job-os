import json
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
    Migration(
        version=3,
        statements=(
            """
            CREATE TABLE job_workspace (
                workspace_id INTEGER PRIMARY KEY CHECK (workspace_id = 1),
                selected_job_id TEXT,
                sort_mode TEXT NOT NULL DEFAULT 'manual',
                manual_order_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "INSERT INTO job_workspace(workspace_id) VALUES (1)",
            """
            CREATE TABLE job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                job_id TEXT,
                origin TEXT NOT NULL,
                occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                payload_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
        ),
    ),
)
SCHEMA_VERSION = MIGRATIONS[-1].version


@dataclass(frozen=True)
class StateHealth:
    schema_version: int


@dataclass(frozen=True)
class JobWorkspaceState:
    selected_job_id: str | None
    sort_mode: str
    manual_order: list[str]


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

    def manual_order(self) -> list[str]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT manual_order_json FROM job_workspace WHERE workspace_id = 1"
            ).fetchone()
        return list(json.loads(row[0])) if row else []

    def job_workspace_state(self) -> JobWorkspaceState:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT selected_job_id, sort_mode, manual_order_json
                FROM job_workspace
                WHERE workspace_id = 1
                """
            ).fetchone()
        if row is None:
            return JobWorkspaceState(None, "manual", [])
        return JobWorkspaceState(row[0], str(row[1]), list(json.loads(row[2])))

    def save_job_selection(self, job_id: str, origin: str) -> int:
        payload = json.dumps({"selected_job_id": job_id}, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE job_workspace
                SET selected_job_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = 1
                """,
                (job_id,),
            )
            cursor = connection.execute(
                """
                INSERT INTO job_events(event_type, job_id, origin, payload_json)
                VALUES ('job_selected', ?, ?, ?)
                """,
                (job_id, origin, payload),
            )
            connection.commit()
        return int(cursor.lastrowid)

    def save_job_sort(self, sort_mode: str, origin: str) -> int:
        payload = json.dumps({"sort_mode": sort_mode}, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE job_workspace
                SET sort_mode = ?, updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = 1
                """,
                (sort_mode,),
            )
            cursor = connection.execute(
                """
                INSERT INTO job_events(event_type, origin, payload_json)
                VALUES ('job_sort_changed', ?, ?)
                """,
                (origin, payload),
            )
            connection.commit()
        return int(cursor.lastrowid)

    def save_manual_order(self, job_ids: list[str], origin: str) -> int:
        payload = json.dumps({"job_ids": job_ids}, separators=(",", ":"))
        order_json = json.dumps(job_ids, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE job_workspace
                SET manual_order_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = 1
                """,
                (order_json,),
            )
            cursor = connection.execute(
                """
                INSERT INTO job_events(event_type, origin, payload_json)
                VALUES ('job_order_changed', ?, ?)
                """,
                (origin, payload),
            )
            connection.commit()
        return int(cursor.lastrowid)

    def record_status_event(
        self,
        *,
        job_id: str,
        origin: str,
        from_status: str,
        to_status: str,
    ) -> int:
        payload = json.dumps(
            {"from_status": from_status, "to_status": to_status},
            separators=(",", ":"),
        )
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO job_events(event_type, job_id, origin, payload_json)
                VALUES ('job_status_changed', ?, ?, ?)
                """,
                (job_id, origin, payload),
            )
        return int(cursor.lastrowid)

    def list_job_events(self, after: int = 0) -> list[dict[str, object]]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT event_id, event_type, job_id, origin, occurred_at, payload_json
                FROM job_events
                WHERE event_id > ?
                ORDER BY event_id
                """,
                (after,),
            ).fetchall()
        events = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            events.append(
                {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "job_id": row["job_id"],
                    "origin": row["origin"],
                    "occurred_at": row["occurred_at"],
                    **payload,
                }
            )
        return events

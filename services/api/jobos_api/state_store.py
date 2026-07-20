import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path


class IncompatibleSchemaError(RuntimeError):
    """The state database cannot be safely opened by this JobOS build."""


class WorkspaceRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"workspace revision conflict; current revision is {current_revision}")


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
    Migration(
        version=4,
        statements=(
            """
            CREATE TABLE workspace_snapshots (
                device_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


@dataclass(frozen=True)
class WorkspaceSnapshotRecord:
    revision: int
    snapshot: dict[str, object]
    repaired_presets: tuple[str, ...] = ()


PANEL_IDS = ("jobs", "center", "agent")
PRESET_DEFAULTS: dict[str, dict[str, object]] = {
    "research": {
        "order": ["jobs", "center", "agent"],
        "widths": {"jobs": 260, "center": 760, "agent": 350},
        "collapsed": [],
    },
    "review": {
        "order": ["jobs", "center", "agent"],
        "widths": {"jobs": 280, "center": 700, "agent": 380},
        "collapsed": [],
    },
    "agent-focus": {
        "order": ["jobs", "center", "agent"],
        "widths": {"jobs": 220, "center": 420, "agent": 650},
        "collapsed": [],
    },
}


def canonical_workspace_snapshot(selected_job_id: str | None = None) -> dict[str, object]:
    return {
        "selected_preset": "review",
        "layouts": deepcopy(PRESET_DEFAULTS),
        "selected_job_id": selected_job_id,
        "active_center_surface": "document",
    }


def _valid_layout(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    order = value.get("order")
    widths = value.get("widths")
    collapsed = value.get("collapsed")
    return (
        isinstance(order, list)
        and len(order) == 3
        and set(order) == set(PANEL_IDS)
        and isinstance(widths, dict)
        and set(widths) == set(PANEL_IDS)
        and all(
            isinstance(widths[panel], int) and 180 <= widths[panel] <= 1600
            for panel in PANEL_IDS
        )
        and isinstance(collapsed, list)
        and len(collapsed) == len(set(collapsed))
        and set(collapsed).issubset(PANEL_IDS)
    )


def normalize_workspace_snapshot(
    value: object, selected_job_id: str | None
) -> tuple[dict[str, object], tuple[str, ...]]:
    canonical = canonical_workspace_snapshot(selected_job_id)
    if not isinstance(value, dict):
        return canonical, tuple(PRESET_DEFAULTS)
    selected_preset = value.get("selected_preset")
    canonical["selected_preset"] = (
        selected_preset if selected_preset in PRESET_DEFAULTS else "review"
    )
    active_surface = value.get("active_center_surface")
    canonical["active_center_surface"] = (
        active_surface if active_surface in ("browser", "document") else "document"
    )
    layouts = value.get("layouts")
    repaired: list[str] = []
    if isinstance(layouts, dict):
        for preset in PRESET_DEFAULTS:
            layout = layouts.get(preset)
            if _valid_layout(layout):
                canonical["layouts"][preset] = deepcopy(layout)  # type: ignore[index]
            else:
                repaired.append(preset)
    else:
        repaired.extend(PRESET_DEFAULTS)
    canonical["selected_job_id"] = selected_job_id
    return canonical, tuple(repaired)


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

    def workspace_snapshot(self, device_id: str) -> WorkspaceSnapshotRecord:
        selected_job_id = self.job_workspace_state().selected_job_id
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT revision, snapshot_json FROM workspace_snapshots WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return WorkspaceSnapshotRecord(0, canonical_workspace_snapshot(selected_job_id))
        try:
            raw: object = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            raw = None
        snapshot, repaired = normalize_workspace_snapshot(raw, selected_job_id)
        return WorkspaceSnapshotRecord(int(row[0]), snapshot, repaired)

    def save_workspace_snapshot(
        self,
        device_id: str,
        *,
        expected_revision: int,
        snapshot: dict[str, object],
    ) -> WorkspaceSnapshotRecord:
        selected_job_id = snapshot.get("selected_job_id")
        normalized, repaired = normalize_workspace_snapshot(
            snapshot,
            selected_job_id if isinstance(selected_job_id, str) else None,
        )
        payload = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM workspace_snapshots WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            current_revision = int(row[0]) if row else 0
            if current_revision != expected_revision:
                connection.rollback()
                raise WorkspaceRevisionConflict(current_revision)
            revision = current_revision + 1
            connection.execute(
                """
                INSERT INTO workspace_snapshots(device_id, revision, snapshot_json)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    revision = excluded.revision,
                    snapshot_json = excluded.snapshot_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (device_id, revision, payload),
            )
            connection.execute(
                """
                UPDATE job_workspace
                SET selected_job_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = 1
                """,
                (normalized["selected_job_id"],),
            )
            connection.commit()
        return WorkspaceSnapshotRecord(revision, normalized, repaired)

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

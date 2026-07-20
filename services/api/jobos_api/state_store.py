import json
import secrets
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .browser_policy import (
    BROWSER_TAB_LIMIT,
    BROWSER_TITLE_LIMIT,
    safe_browser_url,
    sanitize_browser_title,
)
from .documents import VerifiedArtifact
from .redaction import redact_detail, sanitize_summary, sanitize_user_text


class IncompatibleSchemaError(RuntimeError):
    """The state database cannot be safely opened by this JobOS build."""


class WorkspaceRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"workspace revision conflict; current revision is {current_revision}")


class IdempotencyConflict(RuntimeError):
    """An idempotency key was reused for a different command payload."""


class ConversationBusy(RuntimeError):
    """A serialized agent turn is already active."""


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
    Migration(
        version=5,
        statements=(
            "ALTER TABLE job_events ADD COLUMN actor_id TEXT",
            "ALTER TABLE job_events ADD COLUMN target_resource TEXT",
            "ALTER TABLE job_events ADD COLUMN command_name TEXT",
            "ALTER TABLE job_events ADD COLUMN outcome TEXT",
            "ALTER TABLE job_events ADD COLUMN idempotency_key TEXT",
            "ALTER TABLE job_events ADD COLUMN request_hash TEXT",
            "ALTER TABLE job_events ADD COLUMN result_json TEXT",
            """
            CREATE UNIQUE INDEX job_events_mutation_idempotency
            ON job_events(actor_id, target_resource, command_name, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """,
        ),
    ),
    Migration(
        version=6,
        statements=(
            """
            CREATE TABLE document_artifacts (
                artifact_id TEXT PRIMARY KEY,
                registry_key TEXT NOT NULL UNIQUE,
                job_id TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                artifact_revision TEXT NOT NULL,
                media_type TEXT NOT NULL,
                sha256 TEXT,
                render_status TEXT NOT NULL,
                canonical_path TEXT,
                filename TEXT,
                failure_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (render_status IN ('succeeded', 'failed', 'rendering'))
            )
            """,
            "CREATE INDEX document_artifacts_job ON document_artifacts(job_id, created_at)",
            """
            CREATE TABLE job_document_state (
                job_id TEXT PRIMARY KEY,
                current_artifact_id TEXT,
                last_successful_artifact_id TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(current_artifact_id) REFERENCES document_artifacts(artifact_id),
                FOREIGN KEY(last_successful_artifact_id) REFERENCES document_artifacts(artifact_id)
            )
            """,
        ),
    ),
    Migration(
        version=7,
        statements=(
            """
            CREATE TABLE conversations (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                conversation_id TEXT NOT NULL UNIQUE,
                stored_session_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "INSERT INTO conversations(singleton_id, conversation_id) VALUES (1, 'conv_current')",
            """
            CREATE TABLE conversation_turns (
                turn_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                source_turn_id TEXT,
                text TEXT NOT NULL,
                context_json TEXT NOT NULL,
                status TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (
                    status IN ('queued', 'running', 'waiting', 'completed', 'failed', 'interrupted')
                ),
                FOREIGN KEY(source_turn_id) REFERENCES conversation_turns(turn_id)
            )
            """,
            "CREATE INDEX conversation_turns_status ON conversation_turns(status, created_at)",
            """
            CREATE TABLE conversation_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT,
                event_type TEXT NOT NULL,
                state TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                source_event_id TEXT UNIQUE,
                occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(turn_id) REFERENCES conversation_turns(turn_id)
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
    repaired_browser: bool = False
    browser_repair_reasons: tuple[str, ...] = ()


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
        "browser_tabs": [],
        "active_browser_tab_id": None,
        "active_artifact_id": None,
        "active_artifact_page": 1,
        "active_artifact_zoom": 1.0,
    }


def _valid_browser_tab(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    tab_id = value.get("tab_id")
    url = value.get("url")
    title = value.get("title")
    favicon_url = value.get("favicon_url")
    associated_job_id = value.get("associated_job_id")
    return (
        isinstance(tab_id, str)
        and 0 < len(tab_id) <= 128
        and safe_browser_url(url, allow_blank=True)
        and isinstance(title, str)
        and len(title) <= BROWSER_TITLE_LIMIT
        and (favicon_url is None or (safe_browser_url(favicon_url, allow_blank=False)))
        and (
            associated_job_id is None
            or (isinstance(associated_job_id, str) and len(associated_job_id) <= 512)
        )
    )


def _valid_layout(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    order = value.get("order")
    widths = value.get("widths")
    collapsed = value.get("collapsed")
    return (
        isinstance(order, list)
        and len(order) == 3
        and all(isinstance(panel, str) for panel in order)
        and set(order) == set(PANEL_IDS)
        and isinstance(widths, dict)
        and set(widths) == set(PANEL_IDS)
        and all(
            isinstance(widths[panel], int) and 180 <= widths[panel] <= 1600 for panel in PANEL_IDS
        )
        and isinstance(collapsed, list)
        and all(isinstance(panel, str) for panel in collapsed)
        and len(collapsed) == len(set(collapsed))
        and set(collapsed).issubset(PANEL_IDS)
    )


def normalize_workspace_snapshot(
    value: object, selected_job_id: str | None
) -> tuple[dict[str, object], tuple[str, ...]]:
    canonical = canonical_workspace_snapshot(selected_job_id)
    if not isinstance(value, dict):
        canonical["_repaired_browser"] = True
        canonical["_browser_repair_reasons"] = ["metadata_adjusted"]
        return canonical, tuple(PRESET_DEFAULTS)
    selected_preset = value.get("selected_preset")
    canonical["selected_preset"] = (
        selected_preset
        if isinstance(selected_preset, str) and selected_preset in PRESET_DEFAULTS
        else "review"
    )
    active_surface = value.get("active_center_surface")
    canonical["active_center_surface"] = (
        active_surface
        if isinstance(active_surface, str) and active_surface in ("browser", "document")
        else "document"
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
    browser_tabs = value.get("browser_tabs", [])
    active_tab_id = value.get("active_browser_tab_id")
    browser_repair_reasons: set[str] = set()
    recovered_tabs: list[dict[str, object]] = []
    seen_tab_ids: set[str] = set()
    if isinstance(browser_tabs, list):
        for tab in browser_tabs:
            if not _valid_browser_tab(tab):
                browser_repair_reasons.add("dropped_tabs")
                continue
            tab_id = tab["tab_id"]
            if tab_id in seen_tab_ids:
                browser_repair_reasons.add("dropped_tabs")
                continue
            if len(recovered_tabs) >= BROWSER_TAB_LIMIT:
                browser_repair_reasons.add("dropped_tabs")
                continue
            seen_tab_ids.add(tab_id)
            if set(tab).difference({"tab_id", "url", "title", "favicon_url", "associated_job_id"}):
                browser_repair_reasons.add("metadata_adjusted")
            safe_title = sanitize_browser_title(tab["title"])
            if safe_title != tab["title"]:
                browser_repair_reasons.add("protected_title")
            recovered_tabs.append(
                {
                    "tab_id": tab_id,
                    "url": tab["url"],
                    "title": safe_title,
                    "favicon_url": tab.get("favicon_url"),
                    "associated_job_id": tab.get("associated_job_id"),
                }
            )
    else:
        browser_repair_reasons.add("dropped_tabs")
    canonical["browser_tabs"] = recovered_tabs
    if active_tab_id is None and not recovered_tabs:
        canonical["active_browser_tab_id"] = None
    elif isinstance(active_tab_id, str) and active_tab_id in seen_tab_ids:
        canonical["active_browser_tab_id"] = active_tab_id
    else:
        canonical["active_browser_tab_id"] = recovered_tabs[0]["tab_id"] if recovered_tabs else None
        browser_repair_reasons.add("reselected_active_tab")
    ordered_reasons = [
        reason
        for reason in (
            "protected_title",
            "dropped_tabs",
            "reselected_active_tab",
            "metadata_adjusted",
        )
        if reason in browser_repair_reasons
    ]
    canonical["_repaired_browser"] = bool(ordered_reasons)
    canonical["_browser_repair_reasons"] = ordered_reasons
    canonical["selected_job_id"] = selected_job_id
    active_artifact_id = value.get("active_artifact_id")
    canonical["active_artifact_id"] = (
        active_artifact_id
        if isinstance(active_artifact_id, str) and 1 <= len(active_artifact_id) <= 84
        else None
    )
    active_artifact_page = value.get("active_artifact_page")
    canonical["active_artifact_page"] = (
        active_artifact_page
        if isinstance(active_artifact_page, int) and 1 <= active_artifact_page <= 5000
        else 1
    )
    active_artifact_zoom = value.get("active_artifact_zoom")
    canonical["active_artifact_zoom"] = (
        float(active_artifact_zoom)
        if isinstance(active_artifact_zoom, (int, float))
        and not isinstance(active_artifact_zoom, bool)
        and 0.5 <= active_artifact_zoom <= 3.0
        else 1.0
    )
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
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.execute("BEGIN")
            selection_row = connection.execute(
                "SELECT selected_job_id FROM job_workspace WHERE workspace_id = 1"
            ).fetchone()
            row = connection.execute(
                "SELECT revision, snapshot_json FROM workspace_snapshots WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        selected_job_id = selection_row[0] if selection_row else None
        if row is None:
            return WorkspaceSnapshotRecord(0, canonical_workspace_snapshot(selected_job_id))
        try:
            raw: object = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            raw = None
        snapshot, repaired = normalize_workspace_snapshot(raw, selected_job_id)
        repaired_browser = bool(snapshot.pop("_repaired_browser", False))
        browser_repair_reasons = tuple(snapshot.pop("_browser_repair_reasons", ()))
        return WorkspaceSnapshotRecord(
            int(row[0]), snapshot, repaired, repaired_browser, browser_repair_reasons
        )

    def save_workspace_snapshot(
        self,
        device_id: str,
        *,
        expected_revision: int,
        snapshot: dict[str, object],
        idempotency_key: str,
        origin: str,
        actor_id: str,
    ) -> WorkspaceSnapshotRecord:
        target_resource = f"workspace/{device_id}"
        command_name = "workspace_snapshot.save"
        request_hash = sha256(
            json.dumps(
                {
                    "expected_revision": expected_revision,
                    "snapshot": snapshot,
                    "origin": origin,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                """
                SELECT request_hash, result_json
                FROM job_events
                WHERE actor_id = ? AND target_resource = ? AND command_name = ?
                    AND idempotency_key = ?
                """,
                (actor_id, target_resource, command_name, idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior[0] != request_hash:
                    connection.rollback()
                    raise IdempotencyConflict(
                        "Idempotency key was already used for a different workspace command"
                    )
                result = json.loads(prior[1])
                connection.rollback()
                return WorkspaceSnapshotRecord(
                    int(result["revision"]),
                    result["snapshot"],
                    tuple(result["repaired_presets"]),
                    bool(result.get("repaired_browser", False)),
                    tuple(result.get("browser_repair_reasons", ())),
                )
            selection_row = connection.execute(
                "SELECT selected_job_id FROM job_workspace WHERE workspace_id = 1"
            ).fetchone()
            selected_job_id = selection_row[0] if selection_row else None
            normalized, repaired = normalize_workspace_snapshot(snapshot, selected_job_id)
            repaired_browser = bool(normalized.pop("_repaired_browser", False))
            browser_repair_reasons = tuple(normalized.pop("_browser_repair_reasons", ()))
            payload = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
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
            result_json = json.dumps(
                {
                    "revision": revision,
                    "snapshot": normalized,
                    "repaired_presets": repaired,
                    "repaired_browser": repaired_browser,
                    "browser_repair_reasons": browser_repair_reasons,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            detail = json.dumps(
                {
                    "revision": revision,
                    "selected_preset": normalized["selected_preset"],
                    "active_center_surface": normalized["active_center_surface"],
                    "repaired_presets": repaired,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            connection.execute(
                """
                INSERT INTO job_events(
                    event_type, origin, payload_json, actor_id, target_resource,
                    command_name, outcome, idempotency_key, request_hash, result_json
                )
                VALUES ('workspace_snapshot_saved', ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?)
                """,
                (
                    origin,
                    detail,
                    actor_id,
                    target_resource,
                    command_name,
                    idempotency_key,
                    request_hash,
                    result_json,
                ),
            )
            connection.commit()
        return WorkspaceSnapshotRecord(
            revision,
            normalized,
            repaired,
            repaired_browser,
            browser_repair_reasons,
        )

    def stored_session_id(self) -> str | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT stored_session_id FROM conversations WHERE singleton_id = 1"
            ).fetchone()
        return row[0] if row else None

    def save_stored_session_id(self, stored_session_id: str) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE conversations SET stored_session_id = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1",
                (stored_session_id[:256],),
            )

    def save_stored_session_id_if_current(
        self, expected_session_id: str | None, stored_session_id: str
    ) -> bool:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET stored_session_id = ?, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1 AND stored_session_id IS ?",
                (stored_session_id[:256], expected_session_id),
            )
        return cursor.rowcount == 1

    def prepare_turn_submission(
        self,
        turn_id: str,
        expected_session_id: str | None,
        stored_session_id: str,
    ) -> bool:
        """Persist attachment identity iff this is still the active working turn."""
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM conversation_turns "
                "WHERE turn_id = ? AND status = 'running' AND cancel_requested = 0",
                (turn_id,),
            ).fetchone()
            if not active:
                connection.rollback()
                return False
            current = connection.execute(
                "SELECT stored_session_id FROM conversations WHERE singleton_id = 1"
            ).fetchone()
            if not current:
                connection.rollback()
                return False
            if current[0] == expected_session_id:
                connection.execute(
                    "UPDATE conversations SET stored_session_id = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE singleton_id = 1",
                    (stored_session_id[:256],),
                )
            elif current[0] != stored_session_id:
                connection.rollback()
                return False
            connection.commit()
        return True

    def recovery_turn_id(self) -> str | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM jobos_metadata WHERE key = 'agent_recovery_turn_id'"
            ).fetchone()
        return row[0] if row else None

    def clear_recovery_turn_if_current(self, turn_id: str) -> bool:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "DELETE FROM jobos_metadata WHERE key = 'agent_recovery_turn_id' AND value = ?",
                (turn_id,),
            )
        return cursor.rowcount == 1

    def create_conversation_turn(
        self,
        *,
        text: str,
        context: dict[str, object],
        idempotency_key: str,
        actor_id: str,
        source_turn_id: str | None = None,
    ) -> dict[str, str | None]:
        safe_text = sanitize_user_text(text)
        command_name = (
            "conversation.message.submit" if source_turn_id is None else "conversation.turn.retry"
        )
        target_resource = "conversation/current"
        request_hash = sha256(
            json.dumps(
                {"text": safe_text, "context": context, "source_turn_id": source_turn_id},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                """
                SELECT request_hash, result_json FROM job_events
                WHERE actor_id = ? AND target_resource = ? AND command_name = ?
                    AND idempotency_key = ?
                """,
                (actor_id, target_resource, command_name, idempotency_key),
            ).fetchone()
            if prior:
                if prior[0] != request_hash:
                    connection.rollback()
                    raise IdempotencyConflict(
                        "Idempotency key was already used for a different conversation command"
                    )
                result = json.loads(prior[1])
                connection.rollback()
                return result
            active = connection.execute(
                """
                SELECT turn_id FROM conversation_turns
                WHERE status IN ('queued', 'running', 'waiting') LIMIT 1
                """
            ).fetchone()
            if active:
                connection.rollback()
                raise ConversationBusy("An agent turn is already active")
            recovery = connection.execute(
                "SELECT value FROM jobos_metadata WHERE key = 'agent_recovery_turn_id'"
            ).fetchone()
            if recovery:
                connection.rollback()
                raise ConversationBusy("Remote agent cleanup must be confirmed before new work")
            message_id = f"msg_{secrets.token_urlsafe(16)}"
            turn_id = f"turn_{secrets.token_urlsafe(16)}"
            safe_context = redact_detail(context)
            safe_context.pop("redacted", None)
            connection.execute(
                """
                INSERT INTO conversation_turns(
                    turn_id, message_id, source_turn_id, text, context_json, status
                ) VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (
                    turn_id,
                    message_id,
                    source_turn_id,
                    safe_text,
                    json.dumps(safe_context, separators=(",", ":"), sort_keys=True),
                ),
            )
            user_cursor = connection.execute(
                """
                INSERT INTO conversation_events(turn_id, event_type, state, summary, detail_json)
                VALUES (?, 'user_message', 'completed', ?, ?)
                """,
                (
                    turn_id,
                    safe_text,
                    json.dumps(
                        {"message_id": message_id, "text": safe_text}, separators=(",", ":")
                    ),
                ),
            )
            turn_cursor = connection.execute(
                """
                INSERT INTO conversation_events(turn_id, event_type, state, summary, detail_json)
                VALUES (?, 'turn', 'working', 'Agent working', ?)
                """,
                (
                    turn_id,
                    json.dumps(
                        {"context": safe_context, "source_turn_id": source_turn_id},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            result: dict[str, str | None] = {
                "turn_id": turn_id,
                "message_id": message_id,
                "source_turn_id": source_turn_id,
            }
            connection.execute(
                """
                INSERT INTO job_events(
                    event_type, origin, payload_json, actor_id, target_resource,
                    command_name, outcome, idempotency_key, request_hash, result_json
                ) VALUES ('conversation_turn_created', 'user', '{}', ?, ?, ?, 'succeeded', ?, ?, ?)
                """,
                (
                    actor_id,
                    target_resource,
                    command_name,
                    idempotency_key,
                    request_hash,
                    json.dumps(result, separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()
        assert user_cursor.lastrowid and turn_cursor.lastrowid
        return result

    def append_conversation_event(
        self,
        *,
        turn_id: str | None,
        event_type: str,
        state: str,
        summary: str,
        detail: dict[str, object] | None = None,
        source_event_id: str | None = None,
    ) -> int | None:
        safe_detail = redact_detail(detail or {})
        with sqlite3.connect(self._path) as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO conversation_events(
                        turn_id, event_type, state, summary, detail_json, source_event_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        event_type[:50],
                        state[:30],
                        sanitize_summary(summary),
                        json.dumps(safe_detail, separators=(",", ":"), sort_keys=True),
                        source_event_id[:256] if source_event_id else None,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
        return int(cursor.lastrowid)

    def recover_active_conversation_turns(self) -> int:
        """Interrupt turns whose remote execution state cannot survive an API restart."""
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT turn_id FROM conversation_turns
                WHERE status IN ('queued', 'running', 'waiting')
                ORDER BY created_at, rowid
                """
            ).fetchall()
            for (turn_id,) in active:
                connection.execute(
                    """
                    UPDATE conversation_turns
                    SET status = 'interrupted', updated_at = CURRENT_TIMESTAMP
                    WHERE turn_id = ? AND status IN ('queued', 'running', 'waiting')
                    """,
                    (turn_id,),
                )
                connection.execute(
                    """
                    INSERT INTO conversation_events(
                        turn_id, event_type, state, summary, detail_json
                    ) VALUES (?, 'status', 'interrupted', ?, ?)
                    """,
                    (
                        turn_id,
                        "Turn interrupted by API restart; retry to continue",
                        json.dumps(
                            {"actionable": True, "reason": "api_restart", "retry": True},
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ),
                )
            connection.commit()
        return len(active)

    def update_turn_status(
        self, turn_id: str, status: str, *, cancel_requested: bool = False
    ) -> bool:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                """
                UPDATE conversation_turns
                SET status = ?, cancel_requested = MAX(cancel_requested, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE turn_id = ?
                """,
                (status, int(cancel_requested), turn_id),
            )
        return cursor.rowcount > 0

    def transition_active_turn_status(
        self, turn_id: str, status: str, *, expected: tuple[str, ...]
    ) -> bool:
        placeholders = ",".join("?" for _ in expected)
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                f"""
                UPDATE conversation_turns
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE turn_id = ? AND status IN ({placeholders})
                """,
                (status, turn_id, *expected),
            )
        return cursor.rowcount == 1

    def request_turn_cancel(self, turn_id: str) -> bool:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversation_turns SET cancel_requested = 1, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE turn_id = ? AND status IN ('queued', 'running', 'waiting')",
                (turn_id,),
            )
        return cursor.rowcount == 1

    def settle_active_turn(
        self,
        turn_id: str,
        status: str,
        *,
        event_type: str,
        summary: str,
        detail: dict[str, object] | None = None,
        source_event_id: str | None = None,
        cancel_requested: bool = False,
        quarantine: bool = False,
    ) -> bool:
        """Atomically let exactly one terminal status and durable event win."""
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("Turn settlement must be terminal")
        safe_detail = redact_detail(detail or {})
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE conversation_turns
                SET status = ?, cancel_requested = MAX(cancel_requested, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE turn_id = ? AND status IN ('queued', 'running', 'waiting')
                """,
                (status, int(cancel_requested), turn_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            try:
                connection.execute(
                    """
                    INSERT INTO conversation_events(
                        turn_id, event_type, state, summary, detail_json, source_event_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        event_type[:50],
                        status,
                        sanitize_summary(summary),
                        json.dumps(safe_detail, separators=(",", ":"), sort_keys=True),
                        source_event_id[:256] if source_event_id else None,
                    ),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return False
            if quarantine:
                connection.execute(
                    """
                    INSERT INTO jobos_metadata(key, value, updated_at)
                    VALUES ('agent_recovery_turn_id', ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value, updated_at = CURRENT_TIMESTAMP
                    """,
                    (turn_id,),
                )
            connection.commit()
        return True

    def turn_record(self, turn_id: str) -> dict[str, object] | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM conversation_turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["context"] = json.loads(str(result.pop("context_json")))
        return result

    def conversation_events_after(self, after: int) -> list[dict[str, object]]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM conversation_events WHERE event_id > ? ORDER BY event_id",
                (after,),
            ).fetchall()
        entries: list[dict[str, object]] = []
        for row in rows:
            detail = json.loads(row["detail_json"])
            entry: dict[str, object] = {
                "event_id": int(row["event_id"]),
                "turn_id": row["turn_id"],
                "type": row["event_type"],
                "state": row["state"],
                "summary": row["summary"],
                "detail": detail,
                "occurred_at": row["occurred_at"],
            }
            if row["event_type"] == "user_message":
                entry.update({"message_id": detail.get("message_id"), "text": detail.get("text")})
            elif row["event_type"] == "turn":
                entry.update(
                    {
                        "context": detail.get("context", {}),
                        "source_turn_id": detail.get("source_turn_id"),
                    }
                )
            entries.append(entry)
        return entries

    def conversation_snapshot(self) -> dict[str, object]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            conversation = connection.execute(
                "SELECT conversation_id FROM conversations WHERE singleton_id = 1"
            ).fetchone()
            connection.row_factory = sqlite3.Row
            active = connection.execute(
                """
                SELECT turn_id, status, cancel_requested FROM conversation_turns
                WHERE status IN ('queued', 'running', 'waiting') ORDER BY created_at LIMIT 1
                """
            ).fetchone()
        entries = self.conversation_events_after(0)
        return {
            "conversation_id": conversation[0] if conversation else "conv_current",
            "entries": entries,
            "active_turn": (
                {
                    "turn_id": active["turn_id"],
                    "status": active["status"],
                    "cancel_requested": bool(active["cancel_requested"]),
                }
                if active
                else None
            ),
            "latest_event_id": entries[-1]["event_id"] if entries else 0,
        }

    def register_document_artifacts(
        self, job_id: str, artifacts: list[VerifiedArtifact]
    ) -> tuple[str | None, str | None]:
        if any(artifact.job_id != job_id for artifact in artifacts):
            raise ValueError("Artifact job association does not match the requested job")
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT current_artifact_id, last_successful_artifact_id "
                "FROM job_document_state WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            current_id = state[0] if state else None
            last_successful_id = state[1] if state else None
            ids_by_sequence: dict[int, tuple[str, str]] = {}
            for artifact in artifacts:
                row = connection.execute(
                    "SELECT artifact_id FROM document_artifacts WHERE registry_key = ?",
                    (artifact.registry_key,),
                ).fetchone()
                artifact_id = row[0] if row else f"art_{secrets.token_urlsafe(18)}"
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO document_artifacts(
                            artifact_id, registry_key, job_id, source_revision,
                            artifact_revision, media_type, sha256, render_status,
                            canonical_path, filename, failure_message
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            artifact_id,
                            artifact.registry_key,
                            artifact.job_id,
                            artifact.source_revision,
                            artifact.artifact_revision,
                            artifact.media_type,
                            artifact.sha256 or None,
                            artifact.render_status,
                            artifact.canonical_path,
                            artifact.filename,
                            artifact.failure_message,
                        ),
                    )
                ids_by_sequence[artifact.render_sequence] = (
                    artifact_id,
                    artifact.render_status,
                )
            if artifacts:
                current_sequence = max(ids_by_sequence)
                current_id = ids_by_sequence[current_sequence][0]
                successful_sequences = [
                    sequence
                    for sequence, (_, status) in ids_by_sequence.items()
                    if status == "succeeded"
                ]
                last_successful_id = (
                    ids_by_sequence[max(successful_sequences)][0] if successful_sequences else None
                )
                connection.execute(
                    """
                    INSERT INTO job_document_state(
                        job_id, current_artifact_id, last_successful_artifact_id
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        current_artifact_id = excluded.current_artifact_id,
                        last_successful_artifact_id = excluded.last_successful_artifact_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (job_id, current_id, last_successful_id),
                )
            connection.commit()
        return current_id, last_successful_id

    def list_document_artifacts(
        self, job_id: str
    ) -> tuple[list[dict[str, object]], str | None, str | None]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            state = connection.execute(
                "SELECT current_artifact_id, last_successful_artifact_id "
                "FROM job_document_state WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            rows = connection.execute(
                "SELECT * FROM document_artifacts WHERE job_id = ? ORDER BY rowid DESC",
                (job_id,),
            ).fetchall()
        return (
            [dict(row) for row in rows],
            state["current_artifact_id"] if state else None,
            state["last_successful_artifact_id"] if state else None,
        )

    def get_document_artifact(self, artifact_id: str) -> dict[str, object] | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM document_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_mutation_audit(self) -> list[dict[str, object]]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT origin, actor_id, target_resource, command_name, outcome,
                    occurred_at, payload_json
                FROM job_events
                WHERE command_name IS NOT NULL
                ORDER BY event_id
                """
            ).fetchall()
        return [
            {
                "origin": row["origin"],
                "actor_id": row["actor_id"],
                "target_resource": row["target_resource"],
                "command_name": row["command_name"],
                "outcome": row["outcome"],
                "occurred_at": row["occurred_at"],
                "detail": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

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
                WHERE event_id > ? AND command_name IS NULL
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

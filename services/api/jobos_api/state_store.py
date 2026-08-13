import json
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
from .document_files import DocumentFileRecord
from .documents import VerifiedArtifact
from .redaction import (
    redact_detail,
    sanitize_assistant_text,
    sanitize_summary,
    sanitize_user_text,
)


class IncompatibleSchemaError(RuntimeError):
    """The state database cannot be safely opened by this JobOS build."""


class WorkspaceRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"workspace revision conflict; current revision is {current_revision}")


class IdempotencyConflict(RuntimeError):
    """An idempotency key was reused for a different command payload."""


class EditableDocumentConflict(RuntimeError):
    def __init__(self, current: dict[str, object]) -> None:
        self.current = current
        super().__init__("Editable document revision conflict")


def mutation_activity_source_id(
    *, actor_id: str, target_resource: str, command_name: str, idempotency_key: str
) -> str:
    identity = json.dumps(
        [actor_id, target_resource, command_name, idempotency_key],
        separators=(",", ":"),
    )
    return f"action:{sha256(identity.encode()).hexdigest()}"


def _conversation_detail(
    event_type: str, detail: dict[str, object] | None
) -> dict[str, object]:
    raw_detail = detail or {}
    safe_detail = redact_detail(raw_detail)
    assistant_text = raw_detail.get("text")
    if (
        event_type == "assistant_message"
        and raw_detail.get("type") == "message.complete"
        and isinstance(assistant_text, str)
    ):
        safe_detail["text"] = sanitize_assistant_text(assistant_text)
    return safe_detail


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
    Migration(
        version=8,
        statements=(
            "ALTER TABLE job_document_state ADD COLUMN approved_artifact_id TEXT",
            "ALTER TABLE job_document_state ADD COLUMN approved_at TEXT",
        ),
    ),
    Migration(
        version=9,
        statements=(
            "ALTER TABLE conversations ADD COLUMN isolated_turn_id TEXT",
            "ALTER TABLE conversations ADD COLUMN isolated_previous_session_id TEXT",
        ),
    ),
    Migration(
        version=10,
        statements=(
            "ALTER TABLE conversations ADD COLUMN isolated_agent_session_id TEXT",
            "ALTER TABLE conversations ADD COLUMN ignored_agent_session_id TEXT",
        ),
    ),
    Migration(
        version=11,
        statements=(
            "ALTER TABLE document_artifacts ADD COLUMN document_key TEXT NOT NULL DEFAULT 'resume'",
            "ALTER TABLE document_artifacts ADD COLUMN document_label TEXT "
            "NOT NULL DEFAULT 'Resume'",
            "ALTER TABLE document_artifacts ADD COLUMN render_sequence INTEGER NOT NULL DEFAULT 0",
            """
            UPDATE document_artifacts AS artifact
            SET render_sequence = (
                SELECT COUNT(*)
                FROM document_artifacts AS ordered
                WHERE ordered.job_id = artifact.job_id
                  AND ordered.rowid <= artifact.rowid
            )
            """,
            """
            UPDATE job_document_state
            SET approved_artifact_id = NULL,
                approved_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE approved_artifact_id IN (
                SELECT artifact_id
                FROM document_artifacts
                WHERE media_type != 'application/pdf'
            )
            """,
        ),
    ),
    Migration(
        version=12,
        statements=(
            """
            CREATE TABLE editable_documents (
                document_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                document_key TEXT NOT NULL,
                document_label TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                content_json TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                comments_json TEXT NOT NULL DEFAULT '[]',
                import_report_json TEXT NOT NULL DEFAULT '{"issues":[]}',
                source_artifact_id TEXT,
                source_filename TEXT,
                source_sha256 TEXT,
                published_revision INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (document_key IN ('resume', 'cover_letter', 'references')),
                UNIQUE(job_id, document_key),
                FOREIGN KEY(source_artifact_id) REFERENCES document_artifacts(artifact_id)
            )
            """,
            "CREATE INDEX editable_documents_job ON editable_documents(job_id, document_key)",
            """
            CREATE TABLE editable_document_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                document_revision INTEGER NOT NULL CHECK (document_revision >= 1),
                reason TEXT NOT NULL,
                actor TEXT NOT NULL,
                label TEXT,
                content_json TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                comments_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (reason IN (
                    'import', 'before_agent_edit', 'manual', 'before_publish', 'before_restore'
                )),
                CHECK (actor IN ('user', 'jobhunter', 'import', 'system')),
                FOREIGN KEY(document_id)
                    REFERENCES editable_documents(document_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX editable_document_snapshots_document
                ON editable_document_snapshots(document_id, created_at DESC)
            """,
            "ALTER TABLE document_artifacts ADD COLUMN editable_document_id TEXT",
            "ALTER TABLE document_artifacts ADD COLUMN editable_document_revision INTEGER",
        ),
    ),
    Migration(
        version=13,
        statements=(
            """
            CREATE TABLE document_files (
                document_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                document_key TEXT NOT NULL,
                document_label TEXT NOT NULL,
                filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                observed_revision INTEGER NOT NULL CHECK (observed_revision >= 1),
                capabilities_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                CHECK (document_key IN ('resume', 'cover_letter', 'references')),
                UNIQUE(job_id, document_key)
            )
            """,
            "CREATE INDEX document_files_job ON document_files(job_id, document_key)",
            """
            CREATE TABLE document_file_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                observed_revision INTEGER NOT NULL CHECK (observed_revision >= 1),
                sha256 TEXT NOT NULL,
                filename TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                UNIQUE(document_id, observed_revision),
                FOREIGN KEY(document_id) REFERENCES document_files(document_id) ON DELETE CASCADE
            )
            """,
        ),
    ),
    Migration(
        version=14,
        statements=(
            """
            ALTER TABLE document_files
            ADD COLUMN observed_device_id TEXT NOT NULL DEFAULT 'legacy'
            """,
            "ALTER TABLE document_file_observations RENAME TO document_file_observations_v13",
            """
            CREATE TABLE document_file_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                observed_revision INTEGER NOT NULL CHECK (observed_revision >= 1),
                observed_device_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                filename TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                UNIQUE(document_id, observed_device_id, observed_revision),
                FOREIGN KEY(document_id) REFERENCES document_files(document_id) ON DELETE CASCADE
            )
            """,
            """
            INSERT INTO document_file_observations(
                observation_id, document_id, observed_revision, observed_device_id,
                sha256, filename, capabilities_json, observed_at
            )
            SELECT observation_id, document_id, observed_revision, 'legacy',
                   sha256, filename, capabilities_json, observed_at
            FROM document_file_observations_v13
            """,
            "DROP TABLE document_file_observations_v13",
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
LEGACY_AGENT_FOCUS_ORDER = ["jobs", "center", "agent"]
LEGACY_AGENT_FOCUS_WIDTHS = {"jobs": 220, "center": 420, "agent": 650}
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
        "order": ["jobs", "agent", "center"],
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
        "active_top_level_workspace": "review",
        "browse_mode": "list",
        "browse_focus_job_id": None,
        "browse_query": "",
        "browse_status_group": "",
        "browse_sort_mode": "manual",
        "browse_rail_width": 292,
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
    top_level_workspace = value.get("active_top_level_workspace")
    canonical["active_top_level_workspace"] = (
        top_level_workspace
        if isinstance(top_level_workspace, str)
        and top_level_workspace in ("research", "review", "agent-focus", "browse")
        else canonical["selected_preset"]
    )
    browse_mode = value.get("browse_mode")
    canonical["browse_mode"] = browse_mode if browse_mode in ("list", "swipe") else "list"
    browse_focus_job_id = value.get("browse_focus_job_id")
    canonical["browse_focus_job_id"] = (
        browse_focus_job_id
        if isinstance(browse_focus_job_id, str) and len(browse_focus_job_id) <= 512
        else None
    )
    browse_query = value.get("browse_query")
    canonical["browse_query"] = (
        browse_query
        if isinstance(browse_query, str) and len(browse_query) <= 500
        else ""
    )
    browse_status_group = value.get("browse_status_group")
    canonical["browse_status_group"] = (
        browse_status_group
        if isinstance(browse_status_group, str)
        and browse_status_group
        in ("", "Inbox", "Considering", "Applied", "Interviewing", "Closed", "Inactive")
        else ""
    )
    browse_sort_mode = value.get("browse_sort_mode")
    canonical["browse_sort_mode"] = (
        browse_sort_mode
        if browse_sort_mode in ("manual", "recent", "alphabetical", "status")
        else "manual"
    )
    browse_rail_width = value.get("browse_rail_width")
    canonical["browse_rail_width"] = (
        browse_rail_width
        if isinstance(browse_rail_width, int) and 260 <= browse_rail_width <= 360
        else 292
    )
    layouts = value.get("layouts")
    repaired: list[str] = []
    if isinstance(layouts, dict):
        for preset in PRESET_DEFAULTS:
            layout = layouts.get(preset)
            if _valid_layout(layout):
                normalized_layout = deepcopy(layout)
                if (
                    preset == "agent-focus"
                    and normalized_layout["order"] == LEGACY_AGENT_FOCUS_ORDER
                    and normalized_layout["widths"] == LEGACY_AGENT_FOCUS_WIDTHS
                ):
                    normalized_layout["order"] = deepcopy(PRESET_DEFAULTS[preset]["order"])
                canonical["layouts"][preset] = normalized_layout  # type: ignore[index]
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

    def save_stored_session_id(self, stored_session_id: str | None) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE conversations SET stored_session_id = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1",
                (stored_session_id[:256] if stored_session_id else None,),
            )

    def save_stored_session_id_if_current(
        self, expected_session_id: str | None, stored_session_id: str | None
    ) -> bool:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET stored_session_id = ?, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1 AND stored_session_id IS ?",
                (stored_session_id[:256] if stored_session_id else None, expected_session_id),
            )
        return cursor.rowcount == 1

    def begin_isolated_agent_session(self, turn_id: str) -> None:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET isolated_turn_id = ?, "
                "isolated_previous_session_id = stored_session_id, stored_session_id = NULL, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1 AND isolated_turn_id IS NULL",
                (turn_id[:256],),
            )
        if cursor.rowcount != 1:
            raise ConversationBusy("An isolated agent session is already active")

    def restore_isolated_agent_session(self, turn_id: str) -> bool:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET stored_session_id = isolated_previous_session_id, "
                "ignored_agent_session_id = isolated_agent_session_id, "
                "isolated_agent_session_id = NULL, isolated_turn_id = NULL, "
                "isolated_previous_session_id = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1 AND isolated_turn_id = ?",
                (turn_id,),
            )
        return cursor.rowcount == 1

    def record_isolated_agent_session(self, turn_id: str, session_id: str) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE conversations SET isolated_agent_session_id = ?, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1 AND isolated_turn_id = ?",
                (session_id[:256], turn_id),
            )

    def consume_ignored_agent_session(self, session_id: str) -> bool:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET ignored_agent_session_id = NULL, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1 AND ignored_agent_session_id = ?",
                (session_id,),
            )
        return cursor.rowcount == 1

    def reset_conversation(self, *, actor_id: str) -> str:
        """Rotate the current conversation and detach its Hermes session atomically."""
        conversation_id = f"conv_{secrets.token_urlsafe(16)}"
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM conversation_turns "
                "WHERE status IN ('queued', 'running', 'waiting') LIMIT 1"
            ).fetchone()
            if active:
                connection.rollback()
                raise ConversationBusy(
                    "Finish or stop the active turn before starting a new session"
                )
            recovery = connection.execute(
                "SELECT 1 FROM jobos_metadata WHERE key = 'agent_recovery_turn_id'"
            ).fetchone()
            if recovery:
                connection.rollback()
                raise ConversationBusy(
                    "Remote agent cleanup must finish before starting a new session"
                )
            connection.execute("DELETE FROM conversation_events")
            connection.execute("DELETE FROM conversation_turns")
            connection.execute(
                "UPDATE conversations SET conversation_id = ?, stored_session_id = NULL, "
                "isolated_turn_id = NULL, isolated_previous_session_id = NULL, "
                "isolated_agent_session_id = NULL, ignored_agent_session_id = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE singleton_id = 1",
                (conversation_id,),
            )
            connection.execute(
                """
                INSERT INTO job_events(
                    event_type, origin, payload_json, actor_id, target_resource,
                    command_name, outcome, result_json
                ) VALUES (
                    'conversation_session_reset', 'user', '{}', ?,
                    'conversation/current', 'conversation.session.reset', 'succeeded', ?
                )
                """,
                (
                    actor_id,
                    json.dumps(
                        {"conversation_id": conversation_id},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            connection.commit()
        return conversation_id

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

    def recovery_agent_session_id(self, turn_id: str) -> str | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT ignored_agent_session_id FROM conversations
                WHERE singleton_id = 1
                  AND EXISTS (
                      SELECT 1 FROM jobos_metadata
                      WHERE key = 'agent_recovery_turn_id' AND value = ?
                  )
                """,
                (turn_id,),
            ).fetchone()
        return row[0] if row and row[0] else None

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
        request_hash = sha256(
            json.dumps(
                {"text": safe_text, "context": context, "source_turn_id": source_turn_id},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            conversation = connection.execute(
                "SELECT conversation_id FROM conversations WHERE singleton_id = 1"
            ).fetchone()
            target_resource = f"conversation/{conversation[0] if conversation else 'current'}"
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
            public_context = dict(safe_context)
            public_context.pop("_fresh_agent_session", None)
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
                        {"context": public_context, "source_turn_id": source_turn_id},
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
        safe_detail = _conversation_detail(event_type, detail)
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

    def ensure_conversation_event(
        self,
        *,
        turn_id: str | None,
        event_type: str,
        state: str,
        summary: str,
        detail: dict[str, object] | None = None,
        source_event_id: str,
    ) -> int:
        event_id = self.append_conversation_event(
            turn_id=turn_id,
            event_type=event_type,
            state=state,
            summary=summary,
            detail=detail,
            source_event_id=source_event_id,
        )
        if event_id is not None:
            return event_id
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT event_id FROM conversation_events WHERE source_event_id = ?",
                (source_event_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Conversation activity could not be recovered")
        return int(row[0])

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
        safe_detail = _conversation_detail(event_type, detail)
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

    def active_turn_origin_device_id(self) -> str | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT context_json FROM conversation_turns
                WHERE status IN ('queued', 'running', 'waiting')
                ORDER BY created_at LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        try:
            context = json.loads(str(row[0]))
        except (TypeError, ValueError):
            return None
        device_id = context.get("origin_device_id") if isinstance(context, dict) else None
        return device_id if isinstance(device_id, str) and device_id else None

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
            allocated = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'conversation_events'"
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
            "latest_event_id": max(
                int(str(entries[-1]["event_id"])) if entries else 0,
                int(allocated[0]) if allocated else 0,
            ),
        }

    def register_document_artifacts(
        self,
        job_id: str,
        artifacts: list[VerifiedArtifact],
        *,
        editable_document_id: str | None = None,
        editable_document_revision: int | None = None,
    ) -> tuple[str | None, str | None]:
        if (editable_document_id is None) != (editable_document_revision is None):
            raise ValueError("Editable document ID and revision must be supplied together")
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
                    "SELECT artifact_id, document_key, document_label, render_sequence "
                    "FROM document_artifacts WHERE registry_key = ?",
                    (artifact.registry_key,),
                ).fetchone()
                artifact_id = row[0] if row else f"art_{secrets.token_urlsafe(18)}"
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO document_artifacts(
                            artifact_id, registry_key, job_id, document_key, document_label,
                            render_sequence, source_revision, artifact_revision, media_type,
                            sha256, render_status, canonical_path, filename, failure_message,
                            editable_document_id, editable_document_revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            artifact_id,
                            artifact.registry_key,
                            artifact.job_id,
                            artifact.document_key,
                            artifact.document_label,
                            artifact.render_sequence,
                            artifact.source_revision,
                            artifact.artifact_revision,
                            artifact.media_type,
                            artifact.sha256 or None,
                            artifact.render_status,
                            artifact.canonical_path,
                            artifact.filename,
                            artifact.failure_message,
                            editable_document_id,
                            editable_document_revision,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE document_artifacts
                        SET document_key = ?, document_label = ?, render_sequence = ?,
                            editable_document_id = COALESCE(?, editable_document_id),
                            editable_document_revision = COALESCE(?, editable_document_revision)
                        WHERE artifact_id = ?
                        """,
                        (
                            artifact.document_key,
                            artifact.document_label,
                            artifact.render_sequence,
                            editable_document_id,
                            editable_document_revision,
                            artifact_id,
                        ),
                    )
                    if row[1] == "resume" and artifact.document_key != "resume":
                        connection.execute(
                            """
                            UPDATE job_document_state
                            SET approved_artifact_id = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE approved_artifact_id = ?
                            """,
                            (artifact_id,),
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
    ) -> tuple[list[dict[str, object]], str | None, str | None, str | None]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            state = connection.execute(
                "SELECT current_artifact_id, last_successful_artifact_id, approved_artifact_id "
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
            state["approved_artifact_id"] if state else None,
        )

    def approve_document_artifact(self, job_id: str, artifact_id: str) -> None:
        with sqlite3.connect(self._path) as connection:
            artifact = connection.execute(
                "SELECT job_id, document_key, media_type, render_status, sha256 "
                "FROM document_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if (
                artifact is None
                or artifact[0] != job_id
                or artifact[1] != "resume"
                or artifact[2] != "application/pdf"
                or artifact[3] != "succeeded"
                or not artifact[4]
            ):
                raise ValueError(
                    "Only a successful artifact registered for this job can be approved"
                )
            updated = connection.execute(
                """
                UPDATE job_document_state
                SET approved_artifact_id = ?, approved_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (artifact_id, job_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Artifact state is not registered for this job")
            connection.commit()

    @contextmanager
    def _editable_write_connection(
        self, connection: sqlite3.Connection | None = None
    ) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return
        with sqlite3.connect(self._path) as owned_connection:
            owned_connection.row_factory = sqlite3.Row
            owned_connection.execute("BEGIN IMMEDIATE")
            yield owned_connection

    @staticmethod
    def _editable_document_row(row: sqlite3.Row) -> dict[str, object]:
        value = dict(row)
        for source, target in (
            ("content_json", "content"),
            ("settings_json", "settings"),
            ("comments_json", "comments"),
            ("import_report_json", "import_report"),
        ):
            value[target] = json.loads(str(value.pop(source)))
        return value

    @staticmethod
    def new_editable_document_id() -> str:
        return f"edoc_{secrets.token_urlsafe(18)}"

    def editable_import_source(self, job_id: str, artifact_id: str) -> dict[str, object]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            artifact = connection.execute(
                "SELECT * FROM document_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if (
            artifact is None
            or artifact["job_id"] != job_id
            or artifact["media_type"]
            != "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or artifact["render_status"] != "succeeded"
            or not artifact["sha256"]
        ):
            raise ValueError("Source artifact must be a successful DOCX owned by this job")
        return dict(artifact)

    def create_editable_document(
        self,
        *,
        job_id: str,
        document_key: str,
        document_label: str,
        content: dict[str, object],
        settings: dict[str, object],
        comments: list[dict[str, object]],
        import_report: dict[str, object],
        source_artifact_id: str | None = None,
        source_filename: str | None = None,
        source_sha256: str | None = None,
        imported: bool = False,
        document_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        document_id = document_id or self.new_editable_document_id()
        with self._editable_write_connection(connection) as connection:
            if source_artifact_id is not None:
                artifact = connection.execute(
                    """
                    SELECT job_id, media_type, render_status, sha256
                    FROM document_artifacts
                    WHERE artifact_id = ?
                    """,
                    (source_artifact_id,),
                ).fetchone()
                if (
                    artifact is None
                    or artifact["job_id"] != job_id
                    or artifact["media_type"]
                    != "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    or artifact["render_status"] != "succeeded"
                    or not artifact["sha256"]
                    or artifact["sha256"] != source_sha256
                ):
                    raise ValueError("Source artifact must be a successful DOCX owned by this job")
            try:
                connection.execute(
                    """
                    INSERT INTO editable_documents(
                        document_id, job_id, document_key, document_label, schema_version,
                        revision, content_json, settings_json, comments_json, import_report_json,
                        source_artifact_id, source_filename, source_sha256
                    ) VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        job_id,
                        document_key,
                        document_label,
                        json.dumps(content, separators=(",", ":"), sort_keys=True),
                        json.dumps(settings, separators=(",", ":"), sort_keys=True),
                        json.dumps(comments, separators=(",", ":"), sort_keys=True),
                        json.dumps(import_report, separators=(",", ":"), sort_keys=True),
                        source_artifact_id,
                        source_filename,
                        source_sha256,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    "An editable document already exists for this job and type"
                ) from error
            if imported:
                self._insert_editable_snapshot(
                    connection,
                    document_id,
                    1,
                    "import",
                    "import",
                    "Imported DOCX",
                    content,
                    settings,
                    comments,
                )
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        assert row is not None
        return self._editable_document_row(row)

    def replace_editable_document_from_import(
        self,
        document_id: str,
        *,
        expected_revision: int,
        content: dict[str, object],
        settings: dict[str, object],
        import_report: dict[str, object],
        source_artifact_id: str,
        source_filename: str | None,
        source_sha256: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        with self._editable_write_connection(connection) as connection:
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            current = self._editable_document_row(row)
            if current["revision"] != expected_revision:
                raise EditableDocumentConflict(current)
            current_content = current["content"]
            current_settings = current["settings"]
            current_comments = current["comments"]
            assert isinstance(current_content, dict)
            assert isinstance(current_settings, dict)
            assert isinstance(current_comments, list)
            artifact = connection.execute(
                "SELECT job_id, media_type, render_status, sha256 "
                "FROM document_artifacts WHERE artifact_id = ?",
                (source_artifact_id,),
            ).fetchone()
            if (
                artifact is None
                or artifact["job_id"] != current["job_id"]
                or artifact["media_type"]
                != "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                or artifact["render_status"] != "succeeded"
                or not artifact["sha256"]
                or artifact["sha256"] != source_sha256
            ):
                raise ValueError("Source artifact must be a successful DOCX owned by this job")
            self._insert_editable_snapshot(
                connection,
                document_id,
                expected_revision,
                "before_restore",
                "import",
                "Before Replace from DOCX",
                current_content,
                current_settings,
                current_comments,
            )
            connection.execute(
                """
                UPDATE editable_documents
                SET content_json = ?, settings_json = ?, comments_json = '[]',
                    import_report_json = ?, source_artifact_id = ?, source_filename = ?,
                    source_sha256 = ?, revision = revision + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE document_id = ?
                """,
                (
                    json.dumps(content, separators=(",", ":"), sort_keys=True),
                    json.dumps(settings, separators=(",", ":"), sort_keys=True),
                    json.dumps(import_report, separators=(",", ":"), sort_keys=True),
                    source_artifact_id,
                    source_filename,
                    source_sha256,
                    document_id,
                ),
            )
            replaced = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        assert replaced is not None
        return self._editable_document_row(replaced)

    @staticmethod
    def _insert_editable_snapshot(
        connection: sqlite3.Connection,
        document_id: str,
        revision: int,
        reason: str,
        actor: str,
        label: str | None,
        content: dict[str, object],
        settings: dict[str, object],
        comments: list[dict[str, object]],
    ) -> str:
        snapshot_id = f"dsnap_{secrets.token_urlsafe(18)}"
        connection.execute(
            """
            INSERT INTO editable_document_snapshots(
                snapshot_id, document_id, document_revision, reason, actor, label,
                content_json, settings_json, comments_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                document_id,
                revision,
                reason,
                actor,
                label,
                json.dumps(content, separators=(",", ":"), sort_keys=True),
                json.dumps(settings, separators=(",", ":"), sort_keys=True),
                json.dumps(comments, separators=(",", ":"), sort_keys=True),
            ),
        )
        return snapshot_id

    def get_editable_document(
        self, document_id: str, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, object] | None:
        if connection is not None:
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        else:
            with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as read_connection:
                read_connection.row_factory = sqlite3.Row
                row = read_connection.execute(
                    "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
                ).fetchone()
        return self._editable_document_row(row) if row else None

    def get_job_editable_document(self, job_id: str, document_key: str) -> dict[str, object] | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE job_id = ? AND document_key = ?",
                (job_id, document_key),
            ).fetchone()
        return self._editable_document_row(row) if row else None

    def list_editable_documents(self, job_id: str) -> list[dict[str, object]]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM editable_documents WHERE job_id = ? ORDER BY document_key", (job_id,)
            ).fetchall()
        return [self._editable_document_row(row) for row in rows]

    @staticmethod
    def _document_file_row(row: sqlite3.Row) -> dict[str, object]:
        value = dict(row)
        value["capabilities"] = json.loads(str(value.pop("capabilities_json")))
        return value

    def observe_document_file(self, document: DocumentFileRecord) -> None:
        capabilities_json = json.dumps(
            document.capabilities.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        )
        values = (
            document.document_id,
            document.job_id,
            document.document_key,
            document.document_label,
            document.filename,
            document.sha256,
            document.observed_revision,
            document.observed_device_id,
            capabilities_json,
            document.observed_at,
        )
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT document_id
                FROM document_files
                WHERE job_id = ? AND document_key = ?
                """,
                (document.job_id, document.document_key),
            ).fetchone()
            if current is not None and str(current[0]) != document.document_id:
                raise ValueError(
                    "Document file identity changed for the same job and document key"
                )
            latest_on_device = connection.execute(
                """
                SELECT observed_revision, sha256, filename, capabilities_json
                FROM document_file_observations
                WHERE document_id = ? AND observed_device_id = ?
                ORDER BY observed_revision DESC
                LIMIT 1
                """,
                (document.document_id, document.observed_device_id),
            ).fetchone()
            if latest_on_device is not None:
                current_revision = int(latest_on_device[0])
                if document.observed_revision < current_revision:
                    return
                if document.observed_revision == current_revision:
                    incoming = (document.sha256, document.filename, capabilities_json)
                    if incoming != tuple(latest_on_device[1:]):
                        raise ValueError(
                            "Conflicting document file observation for the same device revision"
                        )
                    return
            connection.execute(
                """
                INSERT INTO document_files(
                    document_id, job_id, document_key, document_label, filename, sha256,
                    observed_revision, observed_device_id, capabilities_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, document_key) DO UPDATE SET
                    document_label = excluded.document_label,
                    filename = excluded.filename,
                    sha256 = excluded.sha256,
                    observed_revision = excluded.observed_revision,
                    observed_device_id = excluded.observed_device_id,
                    capabilities_json = excluded.capabilities_json,
                    observed_at = excluded.observed_at
                """,
                values,
            )
            connection.execute(
                """
                INSERT INTO document_file_observations(
                    document_id, observed_revision, observed_device_id,
                    sha256, filename, capabilities_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.document_id,
                    document.observed_revision,
                    document.observed_device_id,
                    document.sha256,
                    document.filename,
                    capabilities_json,
                    document.observed_at,
                ),
            )

    def list_document_files(self, job_id: str) -> list[dict[str, object]]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM document_files WHERE job_id = ? ORDER BY document_key", (job_id,)
            ).fetchall()
        return [self._document_file_row(row) for row in rows]

    def get_document_file(self, document_id: str) -> dict[str, object] | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM document_files WHERE document_id = ?", (document_id,)
            ).fetchone()
        return self._document_file_row(row) if row else None

    def save_editable_document(
        self,
        document_id: str,
        *,
        expected_revision: int,
        content: dict[str, object],
        settings: dict[str, object],
        comments: list[dict[str, object]],
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        with self._editable_write_connection(connection) as connection:
            cursor = connection.execute(
                """
                UPDATE editable_documents
                    SET content_json = ?, settings_json = ?, comments_json = ?,
                    revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE document_id = ? AND revision = ?
                """,
                (
                    json.dumps(content, separators=(",", ":"), sort_keys=True),
                    json.dumps(settings, separators=(",", ":"), sort_keys=True),
                    json.dumps(comments, separators=(",", ":"), sort_keys=True),
                    document_id,
                    expected_revision,
                ),
            )
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if cursor.rowcount != 1:
                if row is None:
                    raise KeyError(document_id)
                raise EditableDocumentConflict(self._editable_document_row(row))
        assert row is not None
        return self._editable_document_row(row)

    def create_editable_snapshot(
        self,
        document_id: str,
        *,
        expected_revision: int,
        reason: str,
        actor: str,
        label: str | None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        with self._editable_write_connection(connection) as connection:
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            current = self._editable_document_row(row)
            if current["revision"] != expected_revision:
                raise EditableDocumentConflict(current)
            snapshot_id = self._insert_editable_snapshot(
                connection,
                document_id,
                expected_revision,
                reason,
                actor,
                label,
                current["content"],
                current["settings"],
                current["comments"],
            )  # type: ignore[arg-type]
            snapshot = connection.execute(
                """
                SELECT snapshot_id, document_id, document_revision, reason, actor, label,
                    created_at
                FROM editable_document_snapshots
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
        return dict(snapshot)

    def mark_editable_document_published(
        self,
        document_id: str,
        *,
        expected_revision: int,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        with self._editable_write_connection(connection) as connection:
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            current = self._editable_document_row(row)
            if current["revision"] != expected_revision:
                raise EditableDocumentConflict(current)
            snapshot = connection.execute(
                """
                SELECT snapshot_id FROM editable_document_snapshots
                WHERE document_id = ? AND document_revision = ? AND reason = 'before_publish'
                LIMIT 1
                """,
                (document_id, expected_revision),
            ).fetchone()
            if snapshot is None:
                self._insert_editable_snapshot(
                    connection,
                    document_id,
                    expected_revision,
                    "before_publish",
                    "user",
                    f"Published revision {expected_revision}",
                    current["content"],  # type: ignore[arg-type]
                    current["settings"],  # type: ignore[arg-type]
                    current["comments"],  # type: ignore[arg-type]
                )
            cursor = connection.execute(
                """
                UPDATE editable_documents
                SET published_revision = ?, updated_at = CURRENT_TIMESTAMP
                WHERE document_id = ? AND revision = ?
                """,
                (expected_revision, document_id, expected_revision),
            )
            if cursor.rowcount != 1:
                latest = connection.execute(
                    "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
                ).fetchone()
                if latest is None:
                    raise KeyError(document_id)
                raise EditableDocumentConflict(self._editable_document_row(latest))
            published = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        assert published is not None
        return self._editable_document_row(published)

    def ensure_editable_publication_snapshot(
        self,
        document_id: str,
        *,
        expected_revision: int,
        actor: str,
    ) -> str:
        """Persist the publication checkpoint before any external artifact side effect."""
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            current = self._editable_document_row(row)
            if current["revision"] != expected_revision:
                raise EditableDocumentConflict(current)
            prior = connection.execute(
                """
                SELECT snapshot_id FROM editable_document_snapshots
                WHERE document_id = ? AND document_revision = ? AND reason = 'before_publish'
                LIMIT 1
                """,
                (document_id, expected_revision),
            ).fetchone()
            if prior is not None:
                return str(prior["snapshot_id"])
            return self._insert_editable_snapshot(
                connection,
                document_id,
                expected_revision,
                "before_publish",
                actor,
                f"Published revision {expected_revision}",
                current["content"],  # type: ignore[arg-type]
                current["settings"],  # type: ignore[arg-type]
                current["comments"],  # type: ignore[arg-type]
            )

    def list_editable_snapshots(self, document_id: str) -> list[dict[str, object]]:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT snapshot_id, document_id, document_revision, reason, actor, label,
                    created_at
                FROM editable_document_snapshots
                WHERE document_id = ?
                ORDER BY rowid DESC
                """,
                (document_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def restore_editable_snapshot(
        self,
        document_id: str,
        snapshot_id: str,
        *,
        expected_revision: int,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        with self._editable_write_connection(connection) as connection:
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            current = self._editable_document_row(row)
            if current["revision"] != expected_revision:
                raise EditableDocumentConflict(current)
            target = connection.execute(
                """
                SELECT * FROM editable_document_snapshots
                WHERE snapshot_id = ? AND document_id = ?
                """,
                (snapshot_id, document_id),
            ).fetchone()
            if target is None:
                raise KeyError(snapshot_id)
            self._insert_editable_snapshot(
                connection,
                document_id,
                expected_revision,
                "before_restore",
                "user",
                "Before restore",
                current["content"],
                current["settings"],
                current["comments"],
            )  # type: ignore[arg-type]
            connection.execute(
                """
                UPDATE editable_documents
                    SET content_json = ?, settings_json = ?, comments_json = ?,
                        revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE document_id = ?
                """,
                (
                    target["content_json"],
                    target["settings_json"],
                    target["comments_json"],
                    document_id,
                ),
            )
            restored = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        assert restored is not None
        return self._editable_document_row(restored)

    def save_agent_document_operation(
        self,
        document_id: str,
        *,
        expected_revision: int,
        content: dict[str, object],
        connection: sqlite3.Connection | None = None,
    ) -> tuple[dict[str, object], str]:
        with self._editable_write_connection(connection) as connection:
            row = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            current = self._editable_document_row(row)
            if current["revision"] != expected_revision:
                raise EditableDocumentConflict(current)
            snapshot_id = self._insert_editable_snapshot(
                connection,
                document_id,
                expected_revision,
                "before_agent_edit",
                "jobhunter",
                "Before JobHunter edit",
                current["content"],
                current["settings"],
                current["comments"],
            )  # type: ignore[arg-type]
            connection.execute(
                """
                UPDATE editable_documents
                    SET content_json = ?, revision = revision + 1,
                        updated_at = CURRENT_TIMESTAMP
                WHERE document_id = ?
                """,
                (json.dumps(content, separators=(",", ":"), sort_keys=True), document_id),
            )
            saved = connection.execute(
                "SELECT * FROM editable_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        assert saved is not None
        return self._editable_document_row(saved), snapshot_id

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

    def execute_editable_mutation(
        self,
        *,
        event_type: str,
        origin: str,
        actor_id: str,
        target_resource: str,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
        detail: dict[str, object],
        mutation: Callable[[sqlite3.Connection], dict[str, object]],
        job_id: str | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Apply an editable-state change and settle its replay row atomically."""
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
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
                if prior["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "Idempotency key was already used for a different command"
                    )
                if prior["result_json"] is None:
                    raise IdempotencyConflict("Mutation reservation is still pending")
                return json.loads(str(prior["result_json"])), True

            cursor = connection.execute(
                """
                INSERT INTO job_events(
                    event_type, job_id, origin, payload_json, actor_id, target_resource,
                    command_name, outcome, idempotency_key, request_hash, result_json
                ) VALUES ('mutation_pending', ?, ?, '{}', ?, ?, ?, 'pending', ?, ?, NULL)
                """,
                (
                    job_id,
                    origin,
                    actor_id,
                    target_resource,
                    command_name,
                    idempotency_key,
                    request_hash,
                ),
            )
            if cursor.lastrowid is None:  # pragma: no cover - SQLite INSERT guarantees this
                raise RuntimeError("Mutation reservation did not return an event ID")
            result = mutation(connection)
            result_job_id = result.get("job_id")
            if result_job_id is None and isinstance(result.get("document"), dict):
                result_job_id = result["document"].get("job_id")  # type: ignore[union-attr]
            settlement_detail = dict(detail)
            if "changed_block_ids" in result:
                settlement_detail["changed_block_ids"] = result["changed_block_ids"]
            self._settle_editable_mutation(
                connection,
                event_id=int(cursor.lastrowid),
                event_type=event_type,
                origin=origin,
                actor_id=actor_id,
                target_resource=target_resource,
                command_name=command_name,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=result,
                detail=settlement_detail,
                job_id=job_id or (str(result_job_id) if result_job_id is not None else None),
            )
        return result, False

    def _settle_editable_mutation(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: int,
        event_type: str,
        origin: str,
        actor_id: str,
        target_resource: str,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
        result: dict[str, object],
        detail: dict[str, object],
        job_id: str | None,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE job_events
            SET event_type = ?, job_id = ?, origin = ?, payload_json = ?, actor_id = ?,
                target_resource = ?, command_name = ?, outcome = 'completed',
                idempotency_key = ?, request_hash = ?, result_json = ?
            WHERE event_id = ? AND request_hash = ? AND result_json IS NULL
            """,
            (
                event_type,
                job_id,
                origin,
                json.dumps(redact_detail(detail), separators=(",", ":"), sort_keys=True),
                actor_id,
                target_resource,
                command_name,
                idempotency_key,
                request_hash,
                json.dumps(result, separators=(",", ":"), sort_keys=True),
                event_id,
                request_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise IdempotencyConflict("Mutation reservation is no longer pending")

    def mutation_result(
        self,
        *,
        actor_id: str,
        target_resource: str,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, object] | None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT request_hash, result_json FROM job_events
                WHERE actor_id = ? AND target_resource = ? AND command_name = ?
                    AND idempotency_key = ?
                """,
                (actor_id, target_resource, command_name, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise IdempotencyConflict("Idempotency key was already used for a different command")
        return json.loads(row[1]) if row[1] is not None else None

    def reserve_mutation(
        self,
        *,
        origin: str,
        actor_id: str,
        target_resource: str,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
        job_id: str | None = None,
    ) -> int:
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                """
                SELECT event_id, request_hash
                FROM job_events
                WHERE actor_id = ? AND target_resource = ? AND command_name = ?
                    AND idempotency_key = ?
                """,
                (actor_id, target_resource, command_name, idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior[1] != request_hash:
                    raise IdempotencyConflict(
                        "Idempotency key was already used for a different command"
                    )
                return int(prior[0])
            cursor = connection.execute(
                """
                INSERT INTO job_events(
                    event_type, job_id, origin, payload_json, actor_id, target_resource,
                    command_name, outcome, idempotency_key, request_hash, result_json
                ) VALUES ('mutation_pending', ?, ?, '{}', ?, ?, ?, 'pending', ?, ?, NULL)
                """,
                (
                    job_id,
                    origin,
                    actor_id,
                    target_resource,
                    command_name,
                    idempotency_key,
                    request_hash,
                ),
            )
            if cursor.lastrowid is None:  # pragma: no cover - SQLite INSERT guarantees this
                raise RuntimeError("Mutation reservation did not return an event ID")
            return int(cursor.lastrowid)

    def ensure_mutation_activity(
        self,
        *,
        actor_id: str,
        target_resource: str,
        command_name: str,
        idempotency_key: str,
    ) -> None:
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT origin, outcome, payload_json FROM job_events
                WHERE actor_id = ? AND target_resource = ? AND command_name = ?
                    AND idempotency_key = ?
                """,
                (actor_id, target_resource, command_name, idempotency_key),
            ).fetchone()
        if row is None or row[0] != "mcp":
            return
        stored_detail = json.loads(row[2])
        label = str(stored_detail.pop("label", command_name))
        stored_detail.pop("state", None)
        stored_detail.pop("origin", None)
        stored_detail.pop("outcome", None)
        outcome = str(row[1])
        self.ensure_conversation_event(
            turn_id=None,
            event_type="activity",
            state="completed" if outcome in {"completed", "succeeded"} else "failed",
            summary=label,
            detail={
                "origin": "mcp",
                "command": command_name,
                "outcome": outcome,
                **stored_detail,
            },
            source_event_id=mutation_activity_source_id(
                actor_id=actor_id,
                target_resource=target_resource,
                command_name=command_name,
                idempotency_key=idempotency_key,
            ),
        )

    def record_mutation_result(
        self,
        *,
        event_type: str,
        origin: str,
        actor_id: str,
        target_resource: str,
        command_name: str,
        outcome: str,
        idempotency_key: str,
        request_hash: str,
        result: dict[str, object],
        detail: dict[str, object],
        job_id: str | None = None,
        inject_event_id: bool = False,
        reserved_event_id: int | None = None,
    ) -> int:
        safe_detail = redact_detail(detail)
        with sqlite3.connect(self._path) as connection:
            if reserved_event_id is not None:
                event_id = reserved_event_id
                stored_result = {**result, "event_id": event_id} if inject_event_id else result
                cursor = connection.execute(
                    """
                    UPDATE job_events
                    SET event_type = ?, job_id = ?, origin = ?, payload_json = ?,
                        actor_id = ?, target_resource = ?, command_name = ?, outcome = ?,
                        idempotency_key = ?, request_hash = ?, result_json = ?
                    WHERE event_id = ? AND request_hash = ? AND result_json IS NULL
                    """,
                    (
                        event_type,
                        job_id,
                        origin,
                        json.dumps(safe_detail, separators=(",", ":"), sort_keys=True),
                        actor_id,
                        target_resource,
                        command_name,
                        outcome,
                        idempotency_key,
                        request_hash,
                        json.dumps(stored_result, separators=(",", ":"), sort_keys=True),
                        event_id,
                        request_hash,
                    ),
                )
                if cursor.rowcount != 1:
                    raise IdempotencyConflict("Mutation reservation is no longer pending")
                return event_id
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO job_events(
                        event_type, job_id, origin, payload_json, actor_id, target_resource,
                        command_name, outcome, idempotency_key, request_hash, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type[:50],
                        job_id,
                        origin,
                        json.dumps(safe_detail, separators=(",", ":"), sort_keys=True),
                        actor_id,
                        target_resource,
                        command_name,
                        outcome[:30],
                        idempotency_key,
                        request_hash,
                        json.dumps(result, separators=(",", ":"), sort_keys=True),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflict("Idempotency key was already used") from error
            if cursor.lastrowid is None:  # pragma: no cover - SQLite INSERT guarantees this
                raise RuntimeError("Mutation event insert did not return an event ID")
            event_id = int(cursor.lastrowid)
            if inject_event_id:
                stored_result = {**result, "event_id": event_id}
                connection.execute(
                    "UPDATE job_events SET result_json = ? WHERE event_id = ?",
                    (
                        json.dumps(stored_result, separators=(",", ":"), sort_keys=True),
                        event_id,
                    ),
                )
        return event_id

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
                    AND (command_name IS NULL OR event_type = 'job_description_updated')
                ORDER BY event_id
                """,
                (after,),
            ).fetchall()
        events = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if row["event_type"] == "job_description_updated":
                payload = {
                    key: payload[key] for key in ("source", "description_length") if key in payload
                }
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

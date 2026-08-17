# SQL statements stay adjacent to their scoped operations for auditability.
# ruff: noqa: E501

import json
import re
import secrets
import sqlite3
from hashlib import sha256
from pathlib import Path

from .redaction import redact_detail, sanitize_summary, sanitize_user_text
from .sqlite_connection import connect_sqlite
from .state_store import (
    ConversationBusy,
    ConversationNotFound,
    IdempotencyConflict,
    _conversation_detail,
)


def event_row(row: sqlite3.Row) -> dict[str, object]:
    detail = json.loads(str(row["detail_json"]))
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
        entry.update(message_id=detail.get("message_id"), text=detail.get("text"))
    elif row["event_type"] == "turn":
        entry.update(
            context=detail.get("context", {}),
            source_turn_id=detail.get("source_turn_id"),
        )
    return entry


class ConversationStore:
    """All durable turn/session operations for exactly one JobOS conversation."""

    def __init__(self, path: Path, conversation_id: str) -> None:
        self._path = path
        self.conversation_id = conversation_id

    def _require_active(self, connection: sqlite3.Connection) -> sqlite3.Row:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM conversations WHERE conversation_id = ? AND archived_at IS NULL",
            (self.conversation_id,),
        ).fetchone()
        if row is None:
            raise ConversationNotFound("Conversation not found")
        return row

    def stored_session_id(self) -> str | None:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = self._require_active(connection)
        value = row["stored_session_id"]
        return str(value) if value else None

    def save_stored_session_id(self, value: str | None) -> None:
        with connect_sqlite(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET stored_session_id = ?, updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ? AND archived_at IS NULL",
                (value[:256] if value else None, self.conversation_id),
            )
        if cursor.rowcount != 1:
            raise ConversationNotFound("Conversation not found")

    def save_stored_session_id_if_current(self, expected: str | None, value: str | None) -> bool:
        with connect_sqlite(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET stored_session_id = ?, updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ? AND archived_at IS NULL AND stored_session_id IS ?",
                (value[:256] if value else None, self.conversation_id, expected),
            )
        return cursor.rowcount == 1

    def begin_isolated_agent_session(self, turn_id: str) -> None:
        with connect_sqlite(self._path) as connection:
            cursor = connection.execute(
                """
                UPDATE conversations SET isolated_turn_id = ?,
                    isolated_previous_session_id = stored_session_id, stored_session_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE conversation_id = ? AND archived_at IS NULL AND isolated_turn_id IS NULL
                  AND EXISTS (SELECT 1 FROM conversation_turns WHERE turn_id = ? AND conversation_id = ?)
                """,
                (turn_id[:256], self.conversation_id, turn_id, self.conversation_id),
            )
        if cursor.rowcount != 1:
            raise ConversationBusy("An isolated agent session is already active")

    def restore_isolated_agent_session(self, turn_id: str) -> bool:
        with connect_sqlite(self._path) as connection:
            cursor = connection.execute(
                """
                UPDATE conversations SET stored_session_id = isolated_previous_session_id,
                    ignored_agent_session_id = isolated_agent_session_id,
                    isolated_agent_session_id = NULL, isolated_turn_id = NULL,
                    isolated_previous_session_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE conversation_id = ? AND isolated_turn_id = ?
                """,
                (self.conversation_id, turn_id),
            )
        return cursor.rowcount == 1

    def record_isolated_agent_session(self, turn_id: str, session_id: str) -> None:
        with connect_sqlite(self._path) as connection:
            connection.execute(
                "UPDATE conversations SET isolated_agent_session_id = ?, updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ? AND isolated_turn_id = ?",
                (session_id[:256], self.conversation_id, turn_id),
            )

    def consume_ignored_agent_session(self, session_id: str) -> bool:
        with connect_sqlite(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET ignored_agent_session_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ? AND ignored_agent_session_id = ?",
                (self.conversation_id, session_id),
            )
        return cursor.rowcount == 1

    def recovery_turn_id(self) -> str | None:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = self._require_active(connection)
        value = row["recovery_turn_id"]
        return str(value) if value else None

    def mark_recovery_turn(self, turn_id: str) -> None:
        with connect_sqlite(self._path) as connection:
            connection.execute(
                "UPDATE conversations SET recovery_turn_id = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE conversation_id = ? AND archived_at IS NULL",
                (turn_id, self.conversation_id),
            )
            connection.commit()

    def recovery_agent_session_id(self, turn_id: str) -> str | None:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT ignored_agent_session_id FROM conversations WHERE conversation_id = ? AND recovery_turn_id = ?",
                (self.conversation_id, turn_id),
            ).fetchone()
        return str(row[0]) if row and row[0] else None

    def clear_recovery_turn_if_current(self, turn_id: str) -> bool:
        with connect_sqlite(self._path) as connection:
            cursor = connection.execute(
                "UPDATE conversations SET recovery_turn_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ? AND recovery_turn_id = ?",
                (self.conversation_id, turn_id),
            )
        return cursor.rowcount == 1

    def create_turn(
        self,
        *,
        text: str,
        context: dict[str, object],
        idempotency_key: str,
        actor_id: str,
        source_turn_id: str | None = None,
    ) -> dict[str, str | bool | None]:
        safe_text = sanitize_user_text(text)
        command = (
            "conversation.message.submit" if source_turn_id is None else "conversation.turn.retry"
        )
        request_hash = sha256(
            json.dumps(
                {"text": safe_text, "context": context, "source_turn_id": source_turn_id},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        target = f"conversation/{self.conversation_id}"
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_active(connection)
            prior = connection.execute(
                "SELECT request_hash, result_json FROM job_events WHERE actor_id = ? AND target_resource = ? AND command_name = ? AND idempotency_key = ?",
                (actor_id, target, command, idempotency_key),
            ).fetchone()
            if prior:
                if prior[0] != request_hash:
                    connection.rollback()
                    raise IdempotencyConflict(
                        "Idempotency key was already used for a different conversation command"
                    )
                result = json.loads(str(prior[1]))
                connection.rollback()
                return {**result, "created": False}
            if (
                source_turn_id
                and connection.execute(
                    "SELECT 1 FROM conversation_turns WHERE turn_id = ? AND conversation_id = ?",
                    (source_turn_id, self.conversation_id),
                ).fetchone()
                is None
            ):
                connection.rollback()
                raise ConversationNotFound("Turn not found")
            if connection.execute(
                "SELECT 1 FROM conversation_turns WHERE conversation_id = ? AND status IN ('queued','running','waiting') LIMIT 1",
                (self.conversation_id,),
            ).fetchone():
                connection.rollback()
                raise ConversationBusy("An agent turn is already active")
            if connection.execute(
                "SELECT recovery_turn_id FROM conversations WHERE conversation_id = ?",
                (self.conversation_id,),
            ).fetchone()[0]:
                connection.rollback()
                raise ConversationBusy("Remote agent cleanup must be confirmed before new work")
            message_id = f"msg_{secrets.token_urlsafe(16)}"
            turn_id = f"turn_{secrets.token_urlsafe(16)}"
            safe_context = redact_detail(context)
            safe_context.pop("redacted", None)
            public_context = dict(safe_context)
            public_context.pop("_fresh_agent_session", None)
            connection.execute(
                "INSERT INTO conversation_turns(turn_id, conversation_id, message_id, source_turn_id, text, context_json, status) VALUES (?, ?, ?, ?, ?, ?, 'running')",
                (
                    turn_id,
                    self.conversation_id,
                    message_id,
                    source_turn_id,
                    safe_text,
                    json.dumps(safe_context, separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.execute(
                "INSERT INTO conversation_events(conversation_id, turn_id, event_type, state, summary, detail_json) VALUES (?, ?, 'user_message', 'completed', ?, ?)",
                (
                    self.conversation_id,
                    turn_id,
                    safe_text,
                    json.dumps(
                        {"message_id": message_id, "text": safe_text}, separators=(",", ":")
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO conversation_events(conversation_id, turn_id, event_type, state, summary, detail_json) VALUES (?, ?, 'turn', 'working', 'Agent working', ?)",
                (
                    self.conversation_id,
                    turn_id,
                    json.dumps(
                        {"context": public_context, "source_turn_id": source_turn_id},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            result = {
                "turn_id": turn_id,
                "message_id": message_id,
                "source_turn_id": source_turn_id,
            }
            connection.execute(
                "INSERT INTO job_events(event_type, origin, payload_json, actor_id, target_resource, command_name, outcome, idempotency_key, request_hash, result_json) VALUES ('conversation_turn_created', 'user', '{}', ?, ?, ?, 'succeeded', ?, ?, ?)",
                (
                    actor_id,
                    target,
                    command,
                    idempotency_key,
                    request_hash,
                    json.dumps(result, separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()
        return {**result, "created": True}

    create_conversation_turn = create_turn

    def record_agent_continuation(
        self,
        *,
        turn_id: str,
        status: str,
        event_type: str,
        summary: str,
        detail: dict[str, object],
        source_event_id: str | None = None,
    ) -> bool:
        """Atomically append one terminal assistant-only continuation."""
        if not re.fullmatch(r"turn_[A-Za-z0-9_-]{8,200}", turn_id):
            raise ValueError("Invalid continuation turn id")
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("Continuation must be terminal")
        safe_detail = _conversation_detail(event_type, detail)
        message_id = f"msg_{secrets.token_urlsafe(16)}"
        context = {"agent_continuation": True}
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_active(connection)
            if connection.execute(
                "SELECT 1 FROM conversation_turns WHERE turn_id = ? AND conversation_id = ?",
                (turn_id, self.conversation_id),
            ).fetchone():
                connection.rollback()
                return False
            connection.execute(
                "INSERT INTO conversation_turns(turn_id, conversation_id, message_id, source_turn_id, text, context_json, status) VALUES (?, ?, ?, NULL, '', ?, ?)",
                (
                    turn_id,
                    self.conversation_id,
                    message_id,
                    json.dumps(context, separators=(",", ":"), sort_keys=True),
                    status,
                ),
            )
            connection.execute(
                "INSERT INTO conversation_events(conversation_id, turn_id, event_type, state, summary, detail_json) VALUES (?, ?, 'turn', 'working', 'Agent completed background work', ?)",
                (
                    self.conversation_id,
                    turn_id,
                    json.dumps(
                        {"context": context, "source_turn_id": None},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO conversation_events(conversation_id, turn_id, event_type, state, summary, detail_json, source_event_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self.conversation_id,
                    turn_id,
                    event_type[:50],
                    status,
                    sanitize_summary(summary),
                    json.dumps(safe_detail, separators=(",", ":"), sort_keys=True),
                    source_event_id[:256] if source_event_id else None,
                ),
            )
            connection.commit()
        return True

    def append_event(
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
        with connect_sqlite(self._path) as connection:
            self._require_active(connection)
            if (
                turn_id
                and connection.execute(
                    "SELECT 1 FROM conversation_turns WHERE turn_id = ? AND conversation_id = ?",
                    (turn_id, self.conversation_id),
                ).fetchone()
                is None
            ):
                return None
            try:
                cursor = connection.execute(
                    "INSERT INTO conversation_events(conversation_id, turn_id, event_type, state, summary, detail_json, source_event_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.conversation_id,
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

    append_conversation_event = append_event

    def ensure_conversation_event(self, **values: object) -> int:
        event_id = self.append_event(**values)  # type: ignore[arg-type]
        if event_id is not None:
            return event_id
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT event_id FROM conversation_events WHERE conversation_id = ? AND source_event_id = ?",
                (self.conversation_id, values["source_event_id"]),
            ).fetchone()
        if row is None:
            raise RuntimeError("Conversation activity could not be recovered")
        return int(row[0])

    def turn_record(self, turn_id: str) -> dict[str, object] | None:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM conversation_turns WHERE turn_id = ? AND conversation_id = ?",
                (turn_id, self.conversation_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["context"] = json.loads(str(result.pop("context_json")))
        return result

    def prepare_turn_submission(self, turn_id: str, expected: str | None, stored: str) -> bool:
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM conversation_turns WHERE turn_id = ? AND conversation_id = ? AND status = 'running' AND cancel_requested = 0",
                (turn_id, self.conversation_id),
            ).fetchone()
            current = connection.execute(
                "SELECT stored_session_id FROM conversations WHERE conversation_id = ? AND archived_at IS NULL",
                (self.conversation_id,),
            ).fetchone()
            if not active or not current or current[0] not in (expected, stored):
                connection.rollback()
                return False
            if current[0] == expected:
                connection.execute(
                    "UPDATE conversations SET stored_session_id = ?, updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ?",
                    (stored[:256], self.conversation_id),
                )
            connection.commit()
        return True

    def _update_turn(self, turn_id: str, sql: str, parameters: tuple[object, ...]) -> bool:
        with connect_sqlite(self._path) as connection:
            cursor = connection.execute(sql, (*parameters, turn_id, self.conversation_id))
        return cursor.rowcount == 1

    def update_turn_status(
        self, turn_id: str, status: str, *, cancel_requested: bool = False
    ) -> bool:
        return self._update_turn(
            turn_id,
            "UPDATE conversation_turns SET status = ?, cancel_requested = MAX(cancel_requested, ?), updated_at = CURRENT_TIMESTAMP WHERE turn_id = ? AND conversation_id = ?",
            (status, int(cancel_requested)),
        )

    def transition_active_turn_status(
        self, turn_id: str, status: str, *, expected: tuple[str, ...]
    ) -> bool:
        placeholders = ",".join("?" for _ in expected)
        return self._update_turn(
            turn_id,
            f"UPDATE conversation_turns SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE status IN ({placeholders}) AND turn_id = ? AND conversation_id = ?",
            (status, *expected),
        )

    def request_turn_cancel(self, turn_id: str) -> bool:
        return self._update_turn(
            turn_id,
            "UPDATE conversation_turns SET cancel_requested = 1, updated_at = CURRENT_TIMESTAMP WHERE status IN ('queued','running','waiting') AND turn_id = ? AND conversation_id = ?",
            (),
        )

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
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("Turn settlement must be terminal")
        safe_detail = _conversation_detail(event_type, detail)
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE conversation_turns SET status = ?, cancel_requested = MAX(cancel_requested, ?), updated_at = CURRENT_TIMESTAMP WHERE turn_id = ? AND conversation_id = ? AND status IN ('queued','running','waiting')",
                (status, int(cancel_requested), turn_id, self.conversation_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            try:
                connection.execute(
                    "INSERT INTO conversation_events(conversation_id, turn_id, event_type, state, summary, detail_json, source_event_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.conversation_id,
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
                    "UPDATE conversations SET recovery_turn_id = ?, updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ?",
                    (turn_id, self.conversation_id),
                )
            connection.commit()
        return True

    def recover_active_conversation_turns(self) -> int:
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT turn_id FROM conversation_turns WHERE conversation_id = ? AND status IN ('queued','running','waiting') ORDER BY created_at, rowid",
                (self.conversation_id,),
            ).fetchall()
            for (turn_id,) in active:
                connection.execute(
                    "UPDATE conversation_turns SET status = 'interrupted', updated_at = CURRENT_TIMESTAMP WHERE turn_id = ? AND conversation_id = ? AND status IN ('queued','running','waiting')",
                    (turn_id, self.conversation_id),
                )
                connection.execute(
                    "INSERT INTO conversation_events(conversation_id, turn_id, event_type, state, summary, detail_json) VALUES (?, ?, 'status', 'interrupted', ?, ?)",
                    (
                        self.conversation_id,
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

    def events_after(self, after: int) -> list[dict[str, object]]:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM conversation_events WHERE conversation_id = ? AND event_id > ? ORDER BY event_id",
                (self.conversation_id, after),
            ).fetchall()
        return [event_row(row) for row in rows]

    conversation_events_after = events_after

    def snapshot(self) -> dict[str, object]:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            conversation = self._require_active(connection)
            active = connection.execute(
                "SELECT turn_id, status, cancel_requested FROM conversation_turns WHERE conversation_id = ? AND status IN ('queued','running','waiting') ORDER BY created_at LIMIT 1",
                (self.conversation_id,),
            ).fetchone()
            allocated = connection.execute(
                "SELECT COALESCE(MAX(event_id), 0) FROM conversation_events"
            ).fetchone()
        entries = self.events_after(0)
        return {
            "conversation_id": self.conversation_id,
            "title": conversation["title"],
            "position": int(conversation["position"]),
            "created_at": conversation["created_at"],
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
                int(entries[-1]["event_id"]) if entries else 0, int(allocated[0])
            ),
        }

    conversation_snapshot = snapshot

    def active_turn_origin_device_id(self) -> str | None:
        snapshot = self.snapshot()
        active = snapshot["active_turn"]
        if not isinstance(active, dict):
            return None
        turn = self.turn_record(str(active["turn_id"]))
        context = turn.get("context") if turn else None
        value = context.get("origin_device_id") if isinstance(context, dict) else None
        return value if isinstance(value, str) and value else None

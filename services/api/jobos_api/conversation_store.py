# SQL statements stay adjacent to their scoped operations for auditability.
# ruff: noqa: E501

import json
import re
import secrets
import sqlite3
from hashlib import sha256
from pathlib import Path

from .career_profile import (
    CareerProfileSnapshot,
    CareerProfileSnapshotRequest,
    create_snapshot_in_transaction,
    get_snapshot_in_connection,
)
from .career_profile_context import (
    CareerProfileContextSelectionError,
    CareerProfileContextSnapshot,
    CareerProfileContextStore,
)
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

    def binding(self) -> dict[str, object]:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = self._require_active(connection)
        return {
            "connected_agent_id": row["connected_agent_id"],
            "provider": row["provider"],
            "model_id": row["model_id"],
            "reasoning_effort": row["reasoning_effort"],
            "binding_state": row["binding_state"],
            "provider_session_id": row["stored_session_id"],
            "connection_account_fingerprint": row["connection_account_fingerprint"],
            "creation_state": row["creation_state"],
            "lock_reason": row["lock_reason"],
        }

    def seal_legacy_binding(
        self,
        *,
        expected_connected_agent_id: str,
        expected_provider_session_id: str,
        model_id: str,
        reasoning_effort: str,
    ) -> bool:
        if not model_id or len(model_id) > 256:
            raise ValueError("model_id must contain between 1 and 256 characters")
        if not reasoning_effort or len(reasoning_effort) > 64:
            raise ValueError("reasoning_effort must contain between 1 and 64 characters")
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE conversations
                SET model_id = ?, reasoning_effort = ?, binding_state = 'sealed',
                    creation_state = 'ready', lock_reason = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE conversation_id = ? AND connected_agent_id = ?
                  AND provider = 'hermes'
                  AND stored_session_id = ?
                  AND binding_state = 'legacy_awaiting_resolution'
                  AND model_id IS NULL AND reasoning_effort IS NULL
                """,
                (
                    model_id,
                    reasoning_effort,
                    self.conversation_id,
                    expected_connected_agent_id,
                    expected_provider_session_id,
                ),
            )
            connection.commit()
        return cursor.rowcount == 1

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
        career_profile_principal: str | None = None,
        career_profile_context: CareerProfileContextStore | None = None,
        career_profile_agent_id: str | None = None,
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
            conversation = self._require_active(connection)
            is_legacy_compatibility_chat = (
                conversation["connected_agent_id"] is None
                and conversation["creation_state"] == "locked"
                and conversation["lock_reason"] is None
            )
            if not is_legacy_compatibility_chat and (
                conversation["creation_state"] != "ready"
                or conversation["lock_reason"] is not None
            ):
                connection.rollback()
                raise ConversationBusy(
                    str(conversation["lock_reason"] or "Connected Agent provisioning is incomplete")
                )
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
            source = None
            if source_turn_id:
                source = connection.execute(
                    """
                    SELECT career_profile_snapshot_id, career_profile_revision,
                           career_profile_content_hash,
                           career_profile_context_snapshot_id,
                           career_profile_context_agent_id,
                           career_profile_context_revision,
                           career_profile_context_authority_epoch,
                           career_profile_context_content_hash
                    FROM conversation_turns
                    WHERE turn_id = ? AND conversation_id = ?
                    """,
                    (source_turn_id, self.conversation_id),
                ).fetchone()
            if source_turn_id and source is None:
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
            bound_snapshot: CareerProfileSnapshot | None = None
            if career_profile_principal is not None:
                if source is None:
                    bound_snapshot = create_snapshot_in_transaction(
                        connection,
                        principal=career_profile_principal,
                        request=CareerProfileSnapshotRequest(),
                    )
                else:
                    snapshot_id, revision, content_hash = source[:3]
                    if snapshot_id is None or revision is None or content_hash is None:
                        connection.rollback()
                        raise RuntimeError("Source turn has no valid Career Profile binding")
                    bound_snapshot = get_snapshot_in_connection(
                        connection, str(snapshot_id), principal=career_profile_principal
                    )
                    if bound_snapshot.profile_revision != int(
                        revision
                    ) or not secrets.compare_digest(bound_snapshot.content_hash, str(content_hash)):
                        connection.rollback()
                        raise RuntimeError("Source turn Career Profile binding is invalid")
            bound_context_snapshot: CareerProfileContextSnapshot | None = None
            if career_profile_context is not None:
                if career_profile_agent_id is None:
                    connection.rollback()
                    raise RuntimeError("Career Profile context agent is not configured")
                if source is None:
                    bound_context_snapshot = career_profile_context.create_snapshot_in_transaction(
                        connection,
                        agent_id=career_profile_agent_id,
                    )
                else:
                    career_profile_context.require_active_agent_in_connection(
                        connection, career_profile_agent_id
                    )
                    (
                        context_snapshot_id,
                        context_agent_id,
                        context_revision,
                        context_authority_epoch,
                        context_content_hash,
                    ) = source[3:]
                    if any(
                        value is None
                        for value in (
                            context_snapshot_id,
                            context_agent_id,
                            context_revision,
                            context_authority_epoch,
                            context_content_hash,
                        )
                    ):
                        connection.rollback()
                        raise RuntimeError(
                            "Source turn has no valid Career Profile context binding"
                        )
                    if not secrets.compare_digest(str(context_agent_id), career_profile_agent_id):
                        connection.rollback()
                        raise RuntimeError(
                            "Source turn Career Profile context belongs to another agent"
                        )
                    bound_context_snapshot = career_profile_context.get_snapshot_in_connection(
                        connection,
                        str(context_snapshot_id),
                        agent_id=career_profile_agent_id,
                    )
                    if (
                        bound_context_snapshot.profile_revision != int(context_revision)
                        or bound_context_snapshot.authority_epoch != int(context_authority_epoch)
                        or not secrets.compare_digest(
                            bound_context_snapshot.content_hash,
                            str(context_content_hash),
                        )
                    ):
                        connection.rollback()
                        raise RuntimeError("Source turn Career Profile context binding is invalid")
            safe_context = redact_detail(context)
            safe_context.pop("redacted", None)
            public_context = dict(safe_context)
            public_context.pop("_fresh_agent_session", None)
            connection.execute(
                """
                INSERT INTO conversation_turns(
                    turn_id, conversation_id, message_id, source_turn_id, text,
                    context_json, status, career_profile_snapshot_id,
                    career_profile_revision, career_profile_content_hash,
                    career_profile_context_snapshot_id,
                    career_profile_context_agent_id,
                    career_profile_context_revision,
                    career_profile_context_authority_epoch,
                    career_profile_context_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    self.conversation_id,
                    message_id,
                    source_turn_id,
                    safe_text,
                    json.dumps(safe_context, separators=(",", ":"), sort_keys=True),
                    bound_snapshot.snapshot_id if bound_snapshot else None,
                    bound_snapshot.profile_revision if bound_snapshot else None,
                    bound_snapshot.content_hash if bound_snapshot else None,
                    bound_context_snapshot.snapshot_id if bound_context_snapshot else None,
                    bound_context_snapshot.agent_id if bound_context_snapshot else None,
                    (bound_context_snapshot.profile_revision if bound_context_snapshot else None),
                    (bound_context_snapshot.authority_epoch if bound_context_snapshot else None),
                    bound_context_snapshot.content_hash if bound_context_snapshot else None,
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

    def bound_career_profile_snapshot(
        self, turn_id: str, *, principal: str
    ) -> CareerProfileSnapshot:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT career_profile_snapshot_id, career_profile_revision,
                       career_profile_content_hash
                FROM conversation_turns
                WHERE turn_id = ? AND conversation_id = ?
                """,
                (turn_id, self.conversation_id),
            ).fetchone()
            if row is None or any(value is None for value in row):
                raise RuntimeError("Turn has no valid Career Profile binding")
            snapshot = get_snapshot_in_connection(connection, str(row[0]), principal=principal)
        if snapshot.profile_revision != int(row[1]) or not secrets.compare_digest(
            snapshot.content_hash, str(row[2])
        ):
            raise RuntimeError("Turn Career Profile binding is invalid")
        return snapshot

    def bound_career_profile_context_snapshot(
        self,
        turn_id: str,
        *,
        context_store: CareerProfileContextStore,
        agent_id: str,
    ) -> CareerProfileContextSnapshot:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT career_profile_context_snapshot_id,
                       career_profile_context_agent_id,
                       career_profile_context_revision,
                       career_profile_context_authority_epoch,
                       career_profile_context_content_hash
                FROM conversation_turns
                WHERE turn_id = ? AND conversation_id = ?
                """,
                (turn_id, self.conversation_id),
            ).fetchone()
            if row is None or any(value is None for value in row):
                raise RuntimeError("Turn has no valid Career Profile context binding")
            if not secrets.compare_digest(str(row[1]), agent_id):
                raise RuntimeError("Turn Career Profile context belongs to another agent")
            snapshot = context_store.get_snapshot_in_connection(
                connection,
                str(row[0]),
                agent_id=agent_id,
            )
        if (
            snapshot.profile_revision != int(row[2])
            or snapshot.authority_epoch != int(row[3])
            or not secrets.compare_digest(snapshot.content_hash, str(row[4]))
        ):
            raise RuntimeError("Turn Career Profile context binding is invalid")
        return snapshot

    def record_agent_continuation(
        self,
        *,
        turn_id: str,
        status: str,
        event_type: str,
        summary: str,
        detail: dict[str, object],
        source_event_id: str | None = None,
        career_profile_principal: str | None = None,
        career_profile_context: CareerProfileContextStore | None = None,
        career_profile_agent_id: str | None = None,
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
            continuation_id = detail.get("continuation_id")
            continuation_digest = (
                sha256(continuation_id.encode()).hexdigest()
                if isinstance(continuation_id, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{8,200}", continuation_id)
                else None
            )
            binding = (
                connection.execute(
                    """
                    SELECT turn.career_profile_snapshot_id, turn.career_profile_revision,
                           turn.career_profile_content_hash,
                           turn.career_profile_context_snapshot_id,
                           turn.career_profile_context_agent_id,
                           turn.career_profile_context_revision,
                           turn.career_profile_context_authority_epoch,
                           turn.career_profile_context_content_hash
                    FROM conversation_continuation_bindings AS continuation
                    JOIN conversation_turns AS turn
                      ON turn.turn_id = continuation.source_turn_id
                     AND turn.conversation_id = continuation.conversation_id
                    WHERE continuation.conversation_id = ?
                      AND continuation.continuation_digest = ?
                    """,
                    (self.conversation_id, continuation_digest),
                ).fetchone()
                if continuation_digest is not None
                else None
            )
            if career_profile_principal is not None:
                legacy_binding = binding[:3] if binding is not None else None
                if legacy_binding is None or any(value is None for value in legacy_binding):
                    connection.rollback()
                    return False
                snapshot = get_snapshot_in_connection(
                    connection, str(legacy_binding[0]), principal=career_profile_principal
                )
                if snapshot.profile_revision != int(
                    legacy_binding[1]
                ) or not secrets.compare_digest(snapshot.content_hash, str(legacy_binding[2])):
                    connection.rollback()
                    return False
            if career_profile_context is not None:
                if career_profile_agent_id is None or binding is None:
                    connection.rollback()
                    return False
                try:
                    career_profile_context.require_active_agent_in_connection(
                        connection, career_profile_agent_id
                    )
                except CareerProfileContextSelectionError:
                    connection.rollback()
                    return False
                context_binding = binding[3:]
                if any(value is None for value in context_binding):
                    connection.rollback()
                    return False
                if not secrets.compare_digest(str(context_binding[1]), career_profile_agent_id):
                    connection.rollback()
                    return False
                context_snapshot = career_profile_context.get_snapshot_in_connection(
                    connection,
                    str(context_binding[0]),
                    agent_id=career_profile_agent_id,
                )
                if (
                    context_snapshot.profile_revision != int(context_binding[2])
                    or context_snapshot.authority_epoch != int(context_binding[3])
                    or not secrets.compare_digest(
                        context_snapshot.content_hash,
                        str(context_binding[4]),
                    )
                ):
                    connection.rollback()
                    return False
            connection.execute(
                """
                INSERT INTO conversation_turns(
                    turn_id, conversation_id, message_id, source_turn_id, text,
                    context_json, status, career_profile_snapshot_id,
                    career_profile_revision, career_profile_content_hash,
                    career_profile_context_snapshot_id,
                    career_profile_context_agent_id,
                    career_profile_context_revision,
                    career_profile_context_authority_epoch,
                    career_profile_context_content_hash
                ) VALUES (?, ?, ?, NULL, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    self.conversation_id,
                    message_id,
                    json.dumps(context, separators=(",", ":"), sort_keys=True),
                    status,
                    binding[0] if binding else None,
                    binding[1] if binding else None,
                    binding[2] if binding else None,
                    binding[3] if binding else None,
                    binding[4] if binding else None,
                    binding[5] if binding else None,
                    binding[6] if binding else None,
                    binding[7] if binding else None,
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

    def bind_agent_continuation(self, *, turn_id: str, continuation_id: str) -> None:
        """Bind a Hermes background continuation to the turn that spawned it."""
        if not re.fullmatch(r"turn_[A-Za-z0-9_-]{8,200}", turn_id):
            raise ValueError("Invalid source turn id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", continuation_id):
            raise ValueError("Invalid continuation id")
        digest = sha256(continuation_id.encode()).hexdigest()
        with connect_sqlite(self._path) as connection:
            self._require_active(connection)
            source = connection.execute(
                """SELECT 1 FROM conversation_turns
                   WHERE conversation_id = ? AND turn_id = ?""",
                (self.conversation_id, turn_id),
            ).fetchone()
            if source is None:
                raise ValueError("Unknown source turn")
            connection.execute(
                """INSERT INTO conversation_continuation_bindings(
                       conversation_id, continuation_digest, source_turn_id
                   ) VALUES (?, ?, ?)
                   ON CONFLICT(conversation_id, continuation_digest) DO UPDATE SET
                       source_turn_id = excluded.source_turn_id
                   WHERE source_turn_id = excluded.source_turn_id""",
                (self.conversation_id, digest, turn_id),
            )

    def append_event(
        self,
        *,
        turn_id: str | None,
        event_type: str,
        state: str,
        summary: str,
        detail: dict[str, object] | None = None,
        source_event_id: str | None = None,
        continuation_ids: tuple[str, ...] = (),
    ) -> int | None:
        safe_detail = _conversation_detail(event_type, detail)
        continuation_digests: list[str] = []
        for continuation_id in continuation_ids:
            if not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", continuation_id):
                raise ValueError("Invalid continuation id")
            continuation_digests.append(sha256(continuation_id.encode()).hexdigest())
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
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
                connection.rollback()
                return None
            if continuation_digests:
                if turn_id is None:
                    connection.rollback()
                    raise ValueError("Continuation binding requires a source turn")
                for continuation_digest in continuation_digests:
                    connection.execute(
                        """INSERT INTO conversation_continuation_bindings(
                               conversation_id, continuation_digest, source_turn_id
                           ) VALUES (?, ?, ?)
                           ON CONFLICT(conversation_id, continuation_digest) DO UPDATE SET
                               source_turn_id = excluded.source_turn_id
                           WHERE source_turn_id = excluded.source_turn_id""",
                        (self.conversation_id, continuation_digest, turn_id),
                    )
            connection.commit()
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

    def prepare_turn_submission(
        self,
        turn_id: str,
        expected: str | None,
        stored: str,
        *,
        career_profile_context: CareerProfileContextStore | None = None,
        career_profile_agent_id: str | None = None,
    ) -> bool:
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
            if career_profile_context is not None:
                if career_profile_agent_id is None:
                    connection.rollback()
                    return False
                try:
                    career_profile_context.require_active_agent_in_connection(
                        connection, career_profile_agent_id
                    )
                except CareerProfileContextSelectionError:
                    connection.rollback()
                    raise
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
            "job_context": {
                "selected_job_id": conversation["selected_job_id"],
                "active_artifact_id": conversation["active_artifact_id"],
                "active_artifact_page": int(conversation["active_artifact_page"]),
                "active_artifact_zoom": float(conversation["active_artifact_zoom"]),
            },
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

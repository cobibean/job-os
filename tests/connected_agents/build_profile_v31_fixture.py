"""Regenerate the visibly synthetic, real-schema v31 SQL fixture."""

from __future__ import annotations

import argparse
import sqlite3
import tempfile
from pathlib import Path

from jobos_api import state_store

PROFILE_ID = "jprof_11111111111111111111111111111111"
FIXED_TIME = "2026-08-01T12:00:00Z"


def _foreign_key_safe_dump(connection: sqlite3.Connection) -> str:
    """Keep dependent seed rows after the tables and rows they reference."""

    dependency_order = (
        'INSERT INTO "conversations"',
        'INSERT INTO "conversation_turns"',
        'INSERT INTO "conversation_events"',
    )
    deferred: dict[str, list[str]] = {prefix: [] for prefix in dependency_order}
    statements: list[str] = []
    for statement in connection.iterdump():
        prefix = next((item for item in dependency_order if statement.startswith(item)), None)
        if prefix is None:
            statements.append(statement)
        else:
            deferred[prefix].append(statement)
    commit_index = statements.index("COMMIT;")
    ordered_inserts = [statement for prefix in dependency_order for statement in deferred[prefix]]
    statements[commit_index:commit_index] = ordered_inserts
    return "\n".join(statements) + "\n"


def build(output: Path) -> None:
    if state_store.SCHEMA_VERSION != 31:
        raise RuntimeError("the pre-feature fixture generator must remain pinned to schema v31")
    with tempfile.TemporaryDirectory(prefix="jobos-(FAKE)-profile-v31-") as temporary:
        database = Path(temporary) / "profile.db"
        state_store.JobOsStateStore(database).initialize(
            owner_device_id="(FAKE)-authorized-macbook",
            installation_profile_id=PROFILE_ID,
        )
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE schema_migrations SET applied_at = ?", (FIXED_TIME,))
            connection.execute(
                """
                UPDATE job_workspace
                SET selected_job_id = ?, updated_at = ?
                WHERE workspace_id = 1
                """,
                ("(FAKE)-job-legacy-1", FIXED_TIME),
            )
            connection.execute(
                """
                UPDATE conversations
                SET title = ?, stored_session_id = ?, created_at = ?, updated_at = ?,
                    selected_job_id = ?
                WHERE conversation_id = 'conv_current'
                """,
                (
                    "(FAKE) Existing Hermes Chat",
                    "(FAKE)-opaque-hermes-session-1",
                    FIXED_TIME,
                    FIXED_TIME,
                    "(FAKE)-job-legacy-1",
                ),
            )
            connection.execute(
                """
                INSERT INTO conversation_turns(
                    turn_id, conversation_id, message_id, text, context_json, status,
                    cancel_requested, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "(FAKE)-turn-legacy-1",
                    "conv_current",
                    "(FAKE)-message-legacy-1",
                    "(FAKE) Summarize the synthetic role.",
                    '{"selected_job_id":"(FAKE)-job-legacy-1"}',
                    "completed",
                    0,
                    FIXED_TIME,
                    FIXED_TIME,
                ),
            )
            connection.executemany(
                """
                INSERT INTO conversation_events(
                    conversation_id, turn_id, event_type, state, summary, detail_json,
                    source_event_id, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        "conv_current",
                        "(FAKE)-turn-legacy-1",
                        "user_message",
                        "working",
                        "(FAKE) User message",
                        '{"text":"(FAKE) Summarize the synthetic role."}',
                        "(FAKE)-source-event-1",
                        FIXED_TIME,
                    ),
                    (
                        "conv_current",
                        "(FAKE)-turn-legacy-1",
                        "assistant_message",
                        "completed",
                        "(FAKE) Assistant response",
                        '{"text":"(FAKE) Synthetic role summary."}',
                        "(FAKE)-source-event-2",
                        FIXED_TIME,
                    ),
                ),
            )
            connection.execute("UPDATE jobos_metadata SET updated_at = ?", (FIXED_TIME,))
            connection.commit()
            dump = _foreign_key_safe_dump(connection)
        finally:
            connection.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dump, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .sqlite_connection import connect_sqlite

PROFILE_ID = "career_profile_global"
WORK_ARRANGEMENT_NAMESPACE = "search_preferences.work_arrangement"
OpaqueRecordId = Annotated[str, Field(pattern=r"^cpr_[A-Za-z0-9_-]{16,64}$")]
OpaqueSnapshotId = Annotated[str, Field(pattern=r"^cps_[A-Za-z0-9_-]{16,64}$")]
IdempotencyKey = Annotated[
    str,
    Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkArrangementValue(StrictModel):
    mode: Literal["remote", "hybrid", "onsite", "flexible"]
    strength: Literal["requirement", "strong_preference", "preference", "dealbreaker"]
    note: str | None = Field(default=None, max_length=1000)


class WorkArrangementRecord(StrictModel):
    record_id: OpaqueRecordId
    namespace: Literal["search_preferences.work_arrangement"]
    value: WorkArrangementValue
    profile_revision: int = Field(ge=1)
    item_revision: int = Field(ge=1)
    actor_principal: str
    updated_at: str


class WorkArrangementCurrent(StrictModel):
    profile_revision: int = Field(ge=0)
    record: WorkArrangementRecord | None


class WorkArrangementMutation(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    value: WorkArrangementValue


class WorkArrangementRestore(StrictModel):
    expected_profile_revision: int = Field(ge=1)
    idempotency_key: IdempotencyKey
    target_profile_revision: int = Field(ge=1)


class WorkArrangementRevision(StrictModel):
    revision_id: str
    profile_revision: int = Field(ge=1)
    record_id: OpaqueRecordId
    item_revision: int = Field(ge=1)
    actor_principal: str
    base_profile_revision: int = Field(ge=0)
    operation: Literal["set", "restore"]
    changed_fields: list[str]
    value: WorkArrangementValue
    restored_from_profile_revision: int | None = Field(default=None, ge=1)
    created_at: str


class WorkArrangementHistory(StrictModel):
    profile_revision: int = Field(ge=0)
    revisions: list[WorkArrangementRevision]


class CareerProfileSnapshotRequest(StrictModel):
    scopes: list[Literal["search_preferences.work_arrangement"]] = Field(
        default_factory=lambda: [WORK_ARRANGEMENT_NAMESPACE], min_length=1, max_length=1
    )


class CareerProfileProjection(StrictModel):
    work_arrangement: WorkArrangementRecord | None


class CareerProfileSnapshot(StrictModel):
    snapshot_id: OpaqueSnapshotId
    profile_revision: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    authorized_principal: str
    scopes: list[Literal["search_preferences.work_arrangement"]]
    projection: CareerProfileProjection
    created_at: str


class CareerProfileRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(
            f"Career Profile revision conflict; current revision is {current_revision}"
        )


class CareerProfileIdempotencyConflict(RuntimeError):
    """An idempotency key was reused for a different Career Profile command."""

    def __init__(self) -> None:
        super().__init__("Idempotency key was already used for a different Career Profile command")


class CareerProfileErasureInProgress(RuntimeError):
    """A destructive Career Profile operation must finish before other writes."""


def ensure_no_pending_erasure(connection: sqlite3.Connection) -> None:
    if connection.execute("SELECT 1 FROM career_profile_erasure_journal LIMIT 1").fetchone():
        raise CareerProfileErasureInProgress(
            "A Career Profile erasure is already being recovered"
        )


class CareerProfileRevisionNotFound(RuntimeError):
    """The requested Career Profile revision does not exist for this record."""


class CareerProfileSnapshotNotFound(RuntimeError):
    """The requested snapshot does not exist."""


class CareerProfileSnapshotForbidden(RuntimeError):
    """The authenticated principal is not authorized to resolve this snapshot."""


class CareerProfileSnapshotIntegrityError(RuntimeError):
    """The stored snapshot projection does not match its immutable content hash."""


def principal_for_device(device_id: str) -> str:
    return f"device:{device_id}"


def _opaque_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(18)}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _request_hash(command: str, payload: dict[str, object]) -> str:
    return sha256(_canonical_json({"command": command, **payload}).encode()).hexdigest()


def _snapshot_hash(
    profile_revision: int,
    scopes: Sequence[str],
    projection: CareerProfileProjection,
) -> str:
    return sha256(
        _canonical_json(
            {
                "profile_revision": profile_revision,
                "scopes": scopes,
                "projection": projection.model_dump(mode="json"),
            }
        ).encode()
    ).hexdigest()


def create_snapshot_in_transaction(
    connection: sqlite3.Connection,
    *,
    principal: str,
    request: CareerProfileSnapshotRequest,
) -> CareerProfileSnapshot:
    """Create an immutable authorized projection inside the caller's transaction."""
    ensure_no_pending_erasure(connection)
    scopes = list(dict.fromkeys(request.scopes))
    head_row = connection.execute(
        "SELECT head_revision FROM career_profiles WHERE profile_id = ?",
        (PROFILE_ID,),
    ).fetchone()
    if head_row is None:
        raise RuntimeError("Career Profile storage is not initialized")
    head = int(head_row[0])
    record_row = connection.execute(
        """
        SELECT record_id, value_json, item_revision, actor_principal, updated_at
        FROM career_profile_records
        WHERE profile_id = ? AND namespace = ?
        """,
        (PROFILE_ID, WORK_ARRANGEMENT_NAMESPACE),
    ).fetchone()
    record = CareerProfileStore._record_from_row(record_row, head) if record_row else None
    projection = CareerProfileProjection(work_arrangement=record)
    snapshot_id = _opaque_id("cps_")
    content_hash = _snapshot_hash(head, scopes, projection)
    connection.execute(
        """
        INSERT INTO career_profile_snapshots(
            snapshot_id, profile_revision, content_hash, authorized_principal,
            scopes_json, projection_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            head,
            content_hash,
            principal,
            _canonical_json(scopes),
            _canonical_json(projection.model_dump(mode="json")),
        ),
    )
    row = connection.execute(
        """
        SELECT snapshot_id, profile_revision, content_hash, authorized_principal,
               scopes_json, projection_json, created_at
        FROM career_profile_snapshots WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO career_profile_audit_events(
            actor_principal, action, profile_revision, affected_fields_json
        ) VALUES (?, 'snapshot.create', ?, ?)
        """,
        (principal, head, _canonical_json(scopes)),
    )
    assert row is not None
    return CareerProfileStore._snapshot_from_row(row)


def get_snapshot_in_connection(
    connection: sqlite3.Connection, snapshot_id: str, *, principal: str
) -> CareerProfileSnapshot:
    row = connection.execute(
        """
        SELECT snapshot_id, profile_revision, content_hash, authorized_principal,
               scopes_json, projection_json, created_at
        FROM career_profile_snapshots WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise CareerProfileSnapshotNotFound
    if not secrets.compare_digest(str(row[3]), principal):
        raise CareerProfileSnapshotForbidden
    snapshot = CareerProfileStore._snapshot_from_row(row)
    expected_hash = _snapshot_hash(
        snapshot.profile_revision, list(snapshot.scopes), snapshot.projection
    )
    if not secrets.compare_digest(snapshot.content_hash, expected_hash):
        raise CareerProfileSnapshotIntegrityError
    return snapshot


def _changed_fields(previous: dict[str, object] | None, current: dict[str, object]) -> list[str]:
    fields = ("mode", "strength", "note")
    return [
        field for field in fields if previous is None or previous.get(field) != current.get(field)
    ]


class CareerProfileStore:
    """Typed Career Profile persistence over the JobOS-owned state database."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        """Activate the empty profile only behind the explicit staging boundary."""
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO career_profiles(profile_id) VALUES (?)",
                (PROFILE_ID,),
            )
            connection.commit()

    def current_work_arrangement(self) -> WorkArrangementCurrent:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                """
                SELECT profile.head_revision, record.record_id, record.value_json,
                       record.item_revision, record.actor_principal, record.updated_at
                FROM career_profiles AS profile
                LEFT JOIN career_profile_records AS record
                  ON record.profile_id = profile.profile_id AND record.namespace = ?
                WHERE profile.profile_id = ?
                """,
                (WORK_ARRANGEMENT_NAMESPACE, PROFILE_ID),
            ).fetchone()
        if row is None:
            raise RuntimeError("Career Profile storage is not initialized")
        head = int(row[0])
        return WorkArrangementCurrent(
            profile_revision=head,
            record=self._record_from_row(row[1:], head) if row[1] is not None else None,
        )

    def set_work_arrangement(
        self,
        *,
        principal: str,
        command: WorkArrangementMutation,
    ) -> WorkArrangementCurrent:
        payload: dict[str, object] = {
            "expected_profile_revision": command.expected_profile_revision,
            "value": command.value.model_dump(mode="json"),
        }
        return self._mutate(
            principal=principal,
            idempotency_key=command.idempotency_key,
            request_hash=_request_hash("set_work_arrangement", payload),
            expected_profile_revision=command.expected_profile_revision,
            operation="set",
            value=command.value,
            restored_from_profile_revision=None,
        )

    def restore_work_arrangement(
        self,
        *,
        principal: str,
        command: WorkArrangementRestore,
    ) -> WorkArrangementCurrent:
        payload: dict[str, object] = {
            "expected_profile_revision": command.expected_profile_revision,
            "target_profile_revision": command.target_profile_revision,
        }
        request_hash = _request_hash("restore_work_arrangement", payload)
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._idempotent_replay(
                    connection,
                    principal=principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return WorkArrangementCurrent.model_validate(replay)
                ensure_no_pending_erasure(connection)
                head = self._head_revision(connection)
                if head != command.expected_profile_revision:
                    raise CareerProfileRevisionConflict(head)
                target = connection.execute(
                    """
                    SELECT resulting_value_json
                    FROM career_profile_revisions
                    WHERE profile_revision = ? AND namespace = ?
                    """,
                    (command.target_profile_revision, WORK_ARRANGEMENT_NAMESPACE),
                ).fetchone()
                if target is None:
                    raise CareerProfileRevisionNotFound
                value = WorkArrangementValue.model_validate_json(target[0])
                result = self._write_revision(
                    connection,
                    principal=principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    base_profile_revision=head,
                    operation="restore",
                    value=value,
                    restored_from_profile_revision=command.target_profile_revision,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def work_arrangement_history(self) -> WorkArrangementHistory:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                """
                SELECT profile.head_revision, revision.revision_id,
                       revision.profile_revision, revision.record_id, revision.item_revision,
                       revision.actor_principal, revision.base_profile_revision,
                       revision.operation, revision.changed_fields_json,
                       revision.resulting_value_json,
                       revision.restored_from_profile_revision, revision.created_at
                FROM career_profiles AS profile
                LEFT JOIN career_profile_revisions AS revision
                  ON revision.profile_id = profile.profile_id AND revision.namespace = ?
                WHERE profile.profile_id = ?
                ORDER BY revision.profile_revision DESC
                """,
                (WORK_ARRANGEMENT_NAMESPACE, PROFILE_ID),
            ).fetchall()
        if not rows:
            raise RuntimeError("Career Profile storage is not initialized")
        head = int(rows[0][0])
        return WorkArrangementHistory(
            profile_revision=head,
            revisions=[
                WorkArrangementRevision(
                    revision_id=row[1],
                    profile_revision=row[2],
                    record_id=row[3],
                    item_revision=row[4],
                    actor_principal=row[5],
                    base_profile_revision=row[6],
                    operation=row[7],
                    changed_fields=json.loads(row[8]),
                    value=WorkArrangementValue.model_validate_json(row[9]),
                    restored_from_profile_revision=row[10],
                    created_at=row[11],
                )
                for row in rows
                if row[1] is not None
            ],
        )

    def create_snapshot(
        self,
        *,
        principal: str,
        request: CareerProfileSnapshotRequest,
    ) -> CareerProfileSnapshot:
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                snapshot = create_snapshot_in_transaction(
                    connection, principal=principal, request=request
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return snapshot

    def get_snapshot(self, snapshot_id: str, *, principal: str) -> CareerProfileSnapshot:
        with connect_sqlite(f"file:{self._path}?mode=ro", uri=True) as connection:
            return get_snapshot_in_connection(connection, snapshot_id, principal=principal)

    def _mutate(
        self,
        *,
        principal: str,
        idempotency_key: str,
        request_hash: str,
        expected_profile_revision: int,
        operation: Literal["set", "restore"],
        value: WorkArrangementValue,
        restored_from_profile_revision: int | None,
    ) -> WorkArrangementCurrent:
        with connect_sqlite(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._idempotent_replay(
                    connection,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return WorkArrangementCurrent.model_validate(replay)
                ensure_no_pending_erasure(connection)
                head = self._head_revision(connection)
                if head != expected_profile_revision:
                    raise CareerProfileRevisionConflict(head)
                result = self._write_revision(
                    connection,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    base_profile_revision=head,
                    operation=operation,
                    value=value,
                    restored_from_profile_revision=restored_from_profile_revision,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _write_revision(
        self,
        connection: sqlite3.Connection,
        *,
        principal: str,
        idempotency_key: str,
        request_hash: str,
        base_profile_revision: int,
        operation: Literal["set", "restore"],
        value: WorkArrangementValue,
        restored_from_profile_revision: int | None,
    ) -> WorkArrangementCurrent:
        previous = connection.execute(
            """
            SELECT record_id, value_json, item_revision
            FROM career_profile_records
            WHERE profile_id = ? AND namespace = ?
            """,
            (PROFILE_ID, WORK_ARRANGEMENT_NAMESPACE),
        ).fetchone()
        record_id = previous[0] if previous is not None else _opaque_id("cpr_")
        item_revision = previous[2] + 1 if previous is not None else 1
        profile_revision = base_profile_revision + 1
        value_json = _canonical_json(value.model_dump(mode="json"))
        previous_value = json.loads(previous[1]) if previous is not None else None
        changed_fields = _changed_fields(previous_value, value.model_dump(mode="json"))
        revision_id = _opaque_id("cpv_")
        connection.execute(
            """
            INSERT INTO career_profile_revisions(
                revision_id, profile_revision, profile_id, record_id, namespace,
                item_revision, actor_principal, base_profile_revision, operation,
                previous_value_json, resulting_value_json, changed_fields_json,
                restored_from_profile_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                profile_revision,
                PROFILE_ID,
                record_id,
                WORK_ARRANGEMENT_NAMESPACE,
                item_revision,
                principal,
                base_profile_revision,
                operation,
                _canonical_json(previous_value) if previous_value is not None else None,
                value_json,
                _canonical_json(changed_fields),
                restored_from_profile_revision,
            ),
        )
        connection.execute(
            """
            INSERT INTO career_profile_records(
                record_id, profile_id, namespace, item_revision, value_json, actor_principal
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, namespace) DO UPDATE SET
                item_revision = excluded.item_revision,
                value_json = excluded.value_json,
                actor_principal = excluded.actor_principal,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                record_id,
                PROFILE_ID,
                WORK_ARRANGEMENT_NAMESPACE,
                item_revision,
                value_json,
                principal,
            ),
        )
        connection.execute(
            """
            UPDATE career_profiles
            SET head_revision = ?, updated_at = CURRENT_TIMESTAMP
            WHERE profile_id = ?
            """,
            (profile_revision, PROFILE_ID),
        )
        row = connection.execute(
            """
            SELECT record_id, value_json, item_revision, actor_principal, updated_at
            FROM career_profile_records
            WHERE profile_id = ? AND namespace = ?
            """,
            (PROFILE_ID, WORK_ARRANGEMENT_NAMESPACE),
        ).fetchone()
        assert row is not None
        result = WorkArrangementCurrent(
            profile_revision=profile_revision,
            record=self._record_from_row(row, profile_revision),
        )
        result_json = _canonical_json(result.model_dump(mode="json"))
        connection.execute(
            """
            INSERT INTO career_profile_idempotency(
                actor_principal, idempotency_key, request_hash, result_json
            ) VALUES (?, ?, ?, ?)
            """,
            (principal, idempotency_key, request_hash, result_json),
        )
        connection.execute(
            """
            INSERT INTO career_profile_audit_events(
                actor_principal, action, profile_revision, base_profile_revision,
                affected_fields_json, revision_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                principal,
                f"work_arrangement.{operation}",
                profile_revision,
                base_profile_revision,
                _canonical_json(changed_fields),
                revision_id,
            ),
        )
        return result

    @staticmethod
    def _head_revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT head_revision FROM career_profiles WHERE profile_id = ?", (PROFILE_ID,)
        ).fetchone()
        if row is None:
            raise RuntimeError("Career Profile storage is not initialized")
        return int(row[0])

    @staticmethod
    def _idempotent_replay(
        connection: sqlite3.Connection,
        *,
        principal: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, object] | None:
        ensure_no_pending_erasure(connection)
        row = connection.execute(
            """
            SELECT request_hash, result_json
            FROM career_profile_idempotency
            WHERE actor_principal = ? AND idempotency_key = ?
            """,
            (principal, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if not secrets.compare_digest(row[0], request_hash):
            raise CareerProfileIdempotencyConflict
        return json.loads(row[1])

    @staticmethod
    def _record_from_row(row: sqlite3.Row | tuple[object, ...], head: int) -> WorkArrangementRecord:
        return WorkArrangementRecord(
            record_id=str(row[0]),
            namespace=WORK_ARRANGEMENT_NAMESPACE,
            value=WorkArrangementValue.model_validate_json(str(row[1])),
            profile_revision=head,
            item_revision=int(str(row[2])),
            actor_principal=str(row[3]),
            updated_at=str(row[4]),
        )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row | tuple[object, ...]) -> CareerProfileSnapshot:
        return CareerProfileSnapshot(
            snapshot_id=str(row[0]),
            profile_revision=int(str(row[1])),
            content_hash=str(row[2]),
            authorized_principal=str(row[3]),
            scopes=json.loads(str(row[4])),
            projection=CareerProfileProjection.model_validate_json(str(row[5])),
            created_at=str(row[6]),
        )

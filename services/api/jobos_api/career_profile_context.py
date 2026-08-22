from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .career_profile import (
    CareerProfileIdempotencyConflict,
    CareerProfileRevisionConflict,
    IdempotencyKey,
    ensure_no_pending_erasure,
)
from .career_profile_complete import CareerProfileCompleteCurrent, CareerProfileCompleteStore
from .sqlite_connection import connect_sqlite

CareerProfileArea = Literal["my_career", "what_im_looking_for", "my_evidence"]
ContextMode = Literal["none", "selected", "broader"]
OpaqueContextSnapshotId = Annotated[
    str, Field(pattern=r"^cpcs_[A-Za-z0-9_-]{16,64}$")
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CareerProfileContextScopeUpdate(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    expected_authority_epoch: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    mode: ContextMode
    selected_item_ids: list[str] = Field(default_factory=list, max_length=200)
    selected_areas: list[CareerProfileArea] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def exact_shape(self) -> Self:
        if len(set(self.selected_item_ids)) != len(self.selected_item_ids):
            raise ValueError("selected Career Profile item IDs must be unique")
        if len(set(self.selected_areas)) != len(self.selected_areas):
            raise ValueError("selected Career Profile areas must be unique")
        if any(
            not re.fullmatch(r"cpi_[A-Za-z0-9_-]{16,64}", item_id)
            for item_id in self.selected_item_ids
        ):
            raise ValueError("selected Career Profile item ID is invalid")
        if self.mode == "selected":
            if not self.selected_item_ids and not self.selected_areas:
                raise ValueError("selected context requires at least one exact item or area")
        elif self.selected_item_ids or self.selected_areas:
            raise ValueError(f"{self.mode} context cannot include selected items or areas")
        return self


class CareerProfileContextScope(StrictModel):
    agent_id: str
    mode: ContextMode
    selected_item_ids: list[str]
    selected_areas: list[CareerProfileArea]
    updated_at: str


class CareerProfileContextSnapshot(StrictModel):
    snapshot_id: OpaqueContextSnapshotId
    agent_id: str
    profile_revision: int = Field(ge=0)
    authority_epoch: int = Field(ge=0)
    scope: CareerProfileContextScope
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    projection: CareerProfileCompleteCurrent
    created_at: str


class CareerProfileContextPreview(StrictModel):
    agent_id: str
    profile_revision: int = Field(ge=0)
    authority_epoch: int = Field(ge=0)
    scope: CareerProfileContextScope
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    projection: CareerProfileCompleteCurrent
    created_at: str


class CareerProfileContextSelectionError(RuntimeError):
    """A requested complete-profile projection exceeds the user's exact grant."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _request_hash(agent_id: str, command: CareerProfileContextScopeUpdate) -> str:
    return sha256(
        _canonical_json(
            {
                "command": "career_profile_context.update",
                "agent_id": agent_id,
                "payload": command.model_dump(mode="json"),
            }
        ).encode()
    ).hexdigest()


def _snapshot_hash(
    *,
    agent_id: str,
    profile_revision: int,
    authority_epoch: int,
    scope: CareerProfileContextScope,
    projection: CareerProfileCompleteCurrent,
) -> str:
    return sha256(
        _canonical_json(
            {
                "agent_id": agent_id,
                "profile_revision": profile_revision,
                "authority_epoch": authority_epoch,
                "scope": scope.model_dump(mode="json"),
                "projection": projection.model_dump(mode="json"),
            }
        ).encode()
    ).hexdigest()


class CareerProfileContextStore:
    """Persist user-owned grants and immutable dormant complete-profile projections."""

    def __init__(self, database, complete_profile: CareerProfileCompleteStore) -> None:
        self.database = database
        self.complete_profile = complete_profile

    def initialize(self) -> None:
        created_at = _now()
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO career_profile_context_grants(
                    agent_id, mode, selected_item_ids_json, selected_areas_json, updated_at
                )
                SELECT agent_id, 'none', '[]', '[]', ?
                FROM career_profile_connected_agents
                """,
                (created_at,),
            )
            connection.commit()

    def get_scope(self, agent_id: str) -> CareerProfileContextScope:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            self.require_active_agent_in_connection(connection, agent_id)
            row = connection.execute(
                """
                SELECT agent_id, mode, selected_item_ids_json, selected_areas_json, updated_at
                FROM career_profile_context_grants WHERE agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
        if row is None:
            raise CareerProfileContextSelectionError(
                "Career Profile context grant was not initialized"
            )
        return self._scope_from_row(row)

    def require_active_agent(self, agent_id: str) -> None:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            self.require_active_agent_in_connection(connection, agent_id)

    @staticmethod
    def require_active_agent_in_connection(
        connection: sqlite3.Connection,
        agent_id: str,
    ) -> None:
        active = connection.execute(
            "SELECT 1 FROM career_profile_connected_agents "
            "WHERE agent_id = ? AND active = 1",
            (agent_id,),
        ).fetchone()
        if active is None:
            raise CareerProfileContextSelectionError("Connected agent was not found")

    def update_scope(
        self,
        *,
        principal: str,
        agent_id: str,
        command: CareerProfileContextScopeUpdate,
    ) -> CareerProfileContextScope:
        request_hash = _request_hash(agent_id, command)
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    """
                    SELECT request_hash, result_json
                    FROM career_profile_context_idempotency
                    WHERE actor_principal = ? AND idempotency_key = ?
                    """,
                    (principal, command.idempotency_key),
                ).fetchone()
                if replay is not None:
                    if not secrets.compare_digest(str(replay[0]), request_hash):
                        raise CareerProfileIdempotencyConflict
                    result = CareerProfileContextScope.model_validate_json(str(replay[1]))
                    connection.rollback()
                    return result

                ensure_no_pending_erasure(connection)
                profile = self.complete_profile._current_in_connection(connection)  # noqa: SLF001
                if profile.profile_revision != command.expected_profile_revision:
                    raise CareerProfileRevisionConflict(profile.profile_revision)
                if profile.authority_epoch != command.expected_authority_epoch:
                    raise CareerProfileContextSelectionError(
                        "Career Profile authority changed; reload the context choice"
                    )
                self.require_active_agent_in_connection(connection, agent_id)

                if command.mode == "selected":
                    accepted_ids = {
                        item.item_id for item in profile.items if item.review_status == "accepted"
                    }
                    rejected = [
                        item_id
                        for item_id in command.selected_item_ids
                        if item_id not in accepted_ids
                    ]
                    if rejected:
                        raise CareerProfileContextSelectionError(
                            "Selected context can include only current accepted "
                            "Career Profile items"
                        )

                updated_at = _now()
                connection.execute(
                    """
                    INSERT INTO career_profile_context_grants(
                        agent_id, mode, selected_item_ids_json, selected_areas_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        mode = excluded.mode,
                        selected_item_ids_json = excluded.selected_item_ids_json,
                        selected_areas_json = excluded.selected_areas_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        agent_id,
                        command.mode,
                        _canonical_json(command.selected_item_ids),
                        _canonical_json(command.selected_areas),
                        updated_at,
                    ),
                )
                result = CareerProfileContextScope(
                    agent_id=agent_id,
                    mode=command.mode,
                    selected_item_ids=command.selected_item_ids,
                    selected_areas=command.selected_areas,
                    updated_at=updated_at,
                )
                connection.execute(
                    """
                    INSERT INTO career_profile_context_idempotency(
                        actor_principal, idempotency_key, request_hash, result_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        principal,
                        command.idempotency_key,
                        request_hash,
                        _canonical_json(result.model_dump(mode="json")),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO career_profile_audit_events(
                        actor_principal, action, profile_revision, affected_fields_json
                    ) VALUES (?, 'context.scope.update', ?, ?)
                    """,
                    (
                        principal,
                        profile.profile_revision,
                        _canonical_json(
                            [
                                f"connected_agents.{agent_id}.context.mode",
                                f"connected_agents.{agent_id}.context.selected_items",
                                f"connected_agents.{agent_id}.context.selected_areas",
                            ]
                        ),
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def preview(self, *, agent_id: str) -> CareerProfileContextPreview:
        """Project the saved grant without creating a reusable agent snapshot."""
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            ensure_no_pending_erasure(connection)
            self.require_active_agent_in_connection(connection, agent_id)
            scope_row = connection.execute(
                """
                SELECT agent_id, mode, selected_item_ids_json, selected_areas_json, updated_at
                FROM career_profile_context_grants WHERE agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
            if scope_row is None:
                raise CareerProfileContextSelectionError(
                    "Career Profile context grant was not initialized"
                )
            scope = self._scope_from_row(scope_row)
            current = self.complete_profile._current_in_connection(connection)  # noqa: SLF001
            projection = self._projection(current, scope)
        return CareerProfileContextPreview(
            agent_id=agent_id,
            profile_revision=current.profile_revision,
            authority_epoch=current.authority_epoch,
            scope=scope,
            content_hash=_snapshot_hash(
                agent_id=agent_id,
                profile_revision=current.profile_revision,
                authority_epoch=current.authority_epoch,
                scope=scope,
                projection=projection,
            ),
            projection=projection,
            created_at=_now(),
        )

    def create_snapshot_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        agent_id: str,
    ) -> CareerProfileContextSnapshot:
        """Freeze the current exact grant inside a caller-owned transaction."""
        ensure_no_pending_erasure(connection)
        self.require_active_agent_in_connection(connection, agent_id)
        scope_row = connection.execute(
            """
            SELECT agent_id, mode, selected_item_ids_json, selected_areas_json, updated_at
            FROM career_profile_context_grants WHERE agent_id = ?
            """,
            (agent_id,),
        ).fetchone()
        if scope_row is None:
            raise CareerProfileContextSelectionError(
                "Career Profile context grant was not initialized"
            )
        scope = self._scope_from_row(scope_row)
        current = self.complete_profile._current_in_connection(connection)  # noqa: SLF001
        projection = self._projection(current, scope)
        snapshot_id = f"cpcs_{secrets.token_urlsafe(18)}"
        created_at = _now()
        content_hash = _snapshot_hash(
            agent_id=agent_id,
            profile_revision=current.profile_revision,
            authority_epoch=current.authority_epoch,
            scope=scope,
            projection=projection,
        )
        connection.execute(
            """
            INSERT INTO career_profile_context_snapshots(
                snapshot_id, agent_id, profile_revision, authority_epoch,
                scope_json, content_hash, projection_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                agent_id,
                current.profile_revision,
                current.authority_epoch,
                _canonical_json(scope.model_dump(mode="json")),
                content_hash,
                _canonical_json(projection.model_dump(mode="json")),
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO career_profile_audit_events(
                actor_principal, action, profile_revision, affected_fields_json
            ) VALUES (?, 'context.snapshot.create', ?, ?)
            """,
            (
                f"agent:{agent_id}",
                current.profile_revision,
                _canonical_json([scope.mode, *scope.selected_areas, *scope.selected_item_ids]),
            ),
        )
        return CareerProfileContextSnapshot(
            snapshot_id=snapshot_id,
            agent_id=agent_id,
            profile_revision=current.profile_revision,
            authority_epoch=current.authority_epoch,
            scope=scope,
            content_hash=content_hash,
            projection=projection,
            created_at=created_at,
        )

    def get_snapshot(
        self, snapshot_id: str, *, agent_id: str
    ) -> CareerProfileContextSnapshot:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            return self.get_snapshot_in_connection(
                connection,
                snapshot_id,
                agent_id=agent_id,
            )

    def get_snapshot_in_connection(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
        *,
        agent_id: str,
    ) -> CareerProfileContextSnapshot:
        """Resolve and integrity-check a frozen grant inside an existing transaction."""
        row = connection.execute(
            """
            SELECT snapshot_id, agent_id, profile_revision, authority_epoch,
                   scope_json, content_hash, projection_json, created_at
            FROM career_profile_context_snapshots WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise CareerProfileContextSelectionError(
                "Career Profile context snapshot was not found"
            )
        if not secrets.compare_digest(str(row[1]), agent_id):
            raise CareerProfileContextSelectionError(
                "Career Profile context snapshot belongs to another agent"
            )
        snapshot = self._snapshot_from_row(row)
        expected_hash = _snapshot_hash(
            agent_id=snapshot.agent_id,
            profile_revision=snapshot.profile_revision,
            authority_epoch=snapshot.authority_epoch,
            scope=snapshot.scope,
            projection=snapshot.projection,
        )
        if not secrets.compare_digest(snapshot.content_hash, expected_hash):
            raise CareerProfileContextSelectionError(
                "Career Profile context snapshot failed its integrity check"
            )
        return snapshot

    @staticmethod
    def _projection(
        current: CareerProfileCompleteCurrent,
        scope: CareerProfileContextScope,
    ) -> CareerProfileCompleteCurrent:
        if scope.mode == "none":
            items = []
            evidence = []
        elif scope.mode == "broader":
            items = [item for item in current.items if item.review_status == "accepted"]
            evidence = [source for source in current.source_evidence if source.active]
        else:
            selected_ids = set(scope.selected_item_ids)
            selected_areas = set(scope.selected_areas)
            items = [
                item
                for item in current.items
                if item.review_status == "accepted"
                and (item.item_id in selected_ids or item.area in selected_areas)
            ]
            include_evidence_area = "my_evidence" in selected_areas
            evidence = [
                source
                for source in current.source_evidence
                if source.active and include_evidence_area
            ]
        return CareerProfileCompleteCurrent(
            profile_revision=current.profile_revision,
            authority_epoch=current.authority_epoch,
            items=items,
            source_evidence=evidence,
        )

    @staticmethod
    def _scope_from_row(row) -> CareerProfileContextScope:
        return CareerProfileContextScope(
            agent_id=str(row[0]),
            mode=cast(ContextMode, str(row[1])),
            selected_item_ids=json.loads(str(row[2])),
            selected_areas=json.loads(str(row[3])),
            updated_at=str(row[4]),
        )

    @staticmethod
    def _snapshot_from_row(row) -> CareerProfileContextSnapshot:
        return CareerProfileContextSnapshot(
            snapshot_id=str(row[0]),
            agent_id=str(row[1]),
            profile_revision=int(row[2]),
            authority_epoch=int(row[3]),
            scope=CareerProfileContextScope.model_validate_json(str(row[4])),
            content_hash=str(row[5]),
            projection=CareerProfileCompleteCurrent.model_validate_json(str(row[6])),
            created_at=str(row[7]),
        )

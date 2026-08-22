from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Self, TypeVar, cast

from pydantic import Field, model_validator

from .career_profile import (
    CareerProfileIdempotencyConflict,
    CareerProfileRevisionConflict,
    IdempotencyKey,
)
from .career_profile_complete import (
    CareerProfileCompleteCurrent,
    CareerProfileCompleteStore,
    CareerProfileItemNotFound,
    CareerProfileValueError,
    ItemProvenance,
    OpaqueEvidenceId,
    ProfileItemRecord,
    ProfileValue,
    StrictModel,
    TimestampText,
)
from .sqlite_connection import connect_sqlite

AgentId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")]
ProposalId = Annotated[str, Field(pattern=r"^cpp_[A-Za-z0-9_-]{16,64}$")]
RevisionId = Annotated[str, Field(pattern=r"^cpv_[A-Za-z0-9_-]{16,64}$")]
TrustMode = Literal["review", "direct"]
AgentEditOperation = Literal["item.create", "item.update", "item.remove"]
ProposalStatus = Literal["pending", "accepted", "rejected"]
ActorKind = Literal[
    "direct_user",
    "authenticated_user_instruction",
    "deterministic_source_mapping",
    "autonomous_agent",
    "user_proposal_decision",
]
ModelT = TypeVar("ModelT", bound=StrictModel)


class ConnectedAgent(StrictModel):
    agent_id: AgentId
    display_name: str = Field(min_length=1, max_length=120)
    principal: str
    trust_mode: TrustMode
    active: bool
    connected_at: TimestampText
    updated_at: TimestampText
    disconnected_at: TimestampText | None = None


class ConnectedAgentList(StrictModel):
    agents: list[ConnectedAgent]


class AgentTrustModeUpdate(StrictModel):
    trust_mode: TrustMode


class AgentProfileEditRequest(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    operation: AgentEditOperation
    target_id: str | None = None
    reason: str = Field(min_length=1, max_length=1000)
    value: ProfileValue | None = None
    evidence_ids: list[OpaqueEvidenceId] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def operation_shape(self) -> Self:
        if self.operation == "item.create":
            if self.target_id is not None or self.value is None:
                raise ValueError("item.create requires a value and cannot name an existing item")
        elif self.operation == "item.update":
            if (
                self.target_id is None
                or re.fullmatch(r"cpi_[A-Za-z0-9_-]{16,64}", self.target_id) is None
                or self.value is None
            ):
                raise ValueError("item.update requires an exact Career Profile item and value")
        elif (
            self.target_id is None
            or re.fullmatch(r"cpi_[A-Za-z0-9_-]{16,64}", self.target_id) is None
            or self.value is not None
            or self.evidence_ids
        ):
            raise ValueError("item.remove requires only an exact Career Profile item")
        return self


class CareerProfileChangeProposal(StrictModel):
    proposal_id: ProposalId
    agent_id: AgentId
    agent_display_name: str
    reason: str
    review_reason: str
    base_profile_revision: int = Field(ge=0)
    operation: AgentEditOperation
    target_id: str
    before: ProfileItemRecord | None
    after: ProfileItemRecord | None
    evidence_ids: list[OpaqueEvidenceId]
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: ProposalStatus
    created_at: TimestampText
    decided_at: TimestampText | None = None
    decided_by_principal: str | None = None


class CareerProfileProposalList(StrictModel):
    proposals: list[CareerProfileChangeProposal]


class AgentEditResult(StrictModel):
    outcome: Literal["applied", "proposal"]
    profile: CareerProfileCompleteCurrent
    proposal: CareerProfileChangeProposal | None = None


class ProposalDecisionRequest(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["accept", "reject"]


class ProposalDecisionResult(StrictModel):
    proposal: CareerProfileChangeProposal
    profile: CareerProfileCompleteCurrent


class ProfileHistoryRevision(StrictModel):
    revision_id: RevisionId
    profile_revision: int = Field(ge=1)
    base_profile_revision: int = Field(ge=0)
    actor_principal: str
    actor_kind: ActorKind
    operation: str
    item_id: str | None
    evidence_id: str | None
    before: dict[str, object] | None
    after: dict[str, object] | None
    affected_fields: list[str]
    reason: str | None
    proposal_id: str | None
    undo_of_revision_id: str | None
    undoable: bool
    created_at: TimestampText


class ProfileHistory(StrictModel):
    profile_revision: int = Field(ge=0)
    revisions: list[ProfileHistoryRevision]


class ProfileUndoRequest(StrictModel):
    expected_profile_revision: int = Field(ge=1)
    idempotency_key: IdempotencyKey


class ConnectedAgentAuthorizationError(RuntimeError):
    """The connected-agent identity is missing, revoked, or credential-mismatched."""


class CareerProfileCollaborationConflict(RuntimeError):
    """A proposal or Undo no longer matches the exact current profile state."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _opaque_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(18)}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _request_hash(command: str, payload: object) -> str:
    return sha256(
        _canonical_json({"command": command, "payload": payload}).encode()
    ).hexdigest()


def _token_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def _area_for_kind(kind: str) -> Literal["my_career", "what_im_looking_for", "my_evidence"]:
    if kind in {
        "identity",
        "education",
        "skill",
        "positioning",
        "experience",
        "project",
        "claim",
        "custom",
    }:
        return "my_career"
    return "what_im_looking_for"


def _proposal_payload(proposal: CareerProfileChangeProposal) -> dict[str, object]:
    return {
        "proposal_id": proposal.proposal_id,
        "agent_id": proposal.agent_id,
        "reason": proposal.reason,
        "review_reason": proposal.review_reason,
        "base_profile_revision": proposal.base_profile_revision,
        "operation": proposal.operation,
        "target_id": proposal.target_id,
        "before": proposal.before.model_dump(mode="json") if proposal.before else None,
        "after": proposal.after.model_dump(mode="json") if proposal.after else None,
        "evidence_ids": proposal.evidence_ids,
    }


def _proposal_digest(proposal: CareerProfileChangeProposal) -> str:
    return sha256(_canonical_json(_proposal_payload(proposal)).encode()).hexdigest()


class CareerProfileCollaborationStore:
    """Practical local-agent review, direct edit, history, and Undo boundary."""

    def __init__(
        self,
        database: Path,
        complete_profile: CareerProfileCompleteStore | None = None,
    ) -> None:
        self.database = database
        self.complete_profile = complete_profile

    def initialize(
        self,
        *,
        agent_id: str,
        display_name: str,
        token: str,
    ) -> ConnectedAgent:
        timestamp = _now()
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO career_profile_connected_agents("
                "agent_id, display_name, principal, token_sha256, connected_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    display_name,
                    f"agent:{agent_id}",
                    _token_digest(token),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        return self.get_agent(agent_id)

    def list_agents(self) -> ConnectedAgentList:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT agent_id, display_name, principal, trust_mode, active, connected_at, "
                "updated_at, disconnected_at FROM career_profile_connected_agents "
                "ORDER BY connected_at, agent_id"
            ).fetchall()
        return ConnectedAgentList(agents=[self._agent_from_row(row) for row in rows])

    def get_agent(self, agent_id: str) -> ConnectedAgent:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            return self._get_agent_in_connection(connection, agent_id)

    def authenticate(self, *, agent_id: str, token: str) -> ConnectedAgent:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT agent_id, display_name, principal, trust_mode, active, connected_at, "
                "updated_at, disconnected_at, token_sha256 "
                "FROM career_profile_connected_agents WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        if (
            row is None
            or not bool(row[4])
            or not hmac.compare_digest(str(row[8]), _token_digest(token))
        ):
            raise ConnectedAgentAuthorizationError(
                "Connected agent access is unavailable or has been revoked"
            )
        return self._agent_from_row(row[:8])

    def update_trust_mode(self, *, agent_id: str, trust_mode: TrustMode) -> ConnectedAgent:
        timestamp = _now()
        with connect_sqlite(self.database) as connection:
            updated = connection.execute(
                "UPDATE career_profile_connected_agents SET trust_mode = ?, updated_at = ? "
                "WHERE agent_id = ? AND active = 1",
                (trust_mode, timestamp, agent_id),
            )
            if updated.rowcount != 1:
                raise ConnectedAgentAuthorizationError("Connected agent was not found")
            connection.commit()
        return self.get_agent(agent_id)

    def disconnect(self, *, agent_id: str) -> ConnectedAgent:
        timestamp = _now()
        with connect_sqlite(self.database) as connection:
            updated = connection.execute(
                "UPDATE career_profile_connected_agents SET active = 0, updated_at = ?, "
                "disconnected_at = ? WHERE agent_id = ? AND active = 1",
                (timestamp, timestamp, agent_id),
            )
            if updated.rowcount != 1:
                raise ConnectedAgentAuthorizationError("Connected agent was not found")
            connection.commit()
        return self.get_agent(agent_id)

    def submit_edit(
        self,
        *,
        agent: ConnectedAgent,
        command: AgentProfileEditRequest,
    ) -> AgentEditResult:
        complete = self._complete()
        request_hash = _request_hash(
            "agent-edit",
            {"agent_id": agent.agent_id, **command.model_dump(mode="json")},
        )
        replay = self._replay(
            principal=agent.principal,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            model=AgentEditResult,
        )
        if replay is not None:
            return replay

        profile = complete.current()
        if profile.profile_revision != command.expected_profile_revision:
            raise CareerProfileRevisionConflict(profile.profile_revision)
        before = self._target_item(profile, command)
        self._validate_evidence_links(profile, before, command.evidence_ids)
        if command.value is not None and command.value.kind == "work_arrangement":
            raise CareerProfileValueError(
                "Work arrangement remains on the staging tracer endpoint until consumer cutover"
            )
        review_reason = self._review_reason(agent.trust_mode, before, command)

        if review_reason is not None:
            return self._create_proposal(
                complete=complete,
                agent=agent,
                command=command,
                profile=profile,
                before=before,
                review_reason=review_reason,
                request_hash=request_hash,
            )

        return self._apply_direct_edit(
            complete=complete,
            agent=agent,
            command=command,
            request_hash=request_hash,
        )

    def list_proposals(
        self, *, status: ProposalStatus | None = "pending"
    ) -> CareerProfileProposalList:
        query = self._proposal_select()
        parameters: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE proposal.status = ?"
            parameters = (status,)
        query += " ORDER BY proposal.created_at, proposal.proposal_id"
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return CareerProfileProposalList(
            proposals=[self._proposal_from_row(row) for row in rows]
        )

    def decide_proposal(
        self,
        *,
        proposal_id: str,
        principal: str,
        command: ProposalDecisionRequest,
    ) -> ProposalDecisionResult:
        complete = self._complete()
        request_hash = _request_hash(
            "proposal-decision",
            {"proposal_id": proposal_id, **command.model_dump(mode="json")},
        )
        replay = self._replay(
            principal=principal,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            model=ProposalDecisionResult,
        )
        if replay is not None:
            return replay

        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_in_connection(
                    connection,
                    principal=principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    model=ProposalDecisionResult,
                )
                if replay is not None:
                    connection.rollback()
                    return replay

                proposal = self._get_proposal_in_connection(connection, proposal_id)
                if proposal.status != "pending":
                    raise CareerProfileCollaborationConflict(
                        "This proposal was already decided"
                    )
                if not hmac.compare_digest(
                    command.proposal_sha256, proposal.proposal_sha256
                ) or not hmac.compare_digest(
                    proposal.proposal_sha256, _proposal_digest(proposal)
                ):
                    raise CareerProfileCollaborationConflict(
                        "The proposal payload changed and must be regenerated"
                    )
                head = complete._current_in_connection(  # noqa: SLF001
                    connection
                ).profile_revision
                if command.decision == "accept":
                    complete._check_head(  # noqa: SLF001 - shared transaction boundary
                        connection, command.expected_profile_revision
                    )
                    if proposal.base_profile_revision != head:
                        raise CareerProfileCollaborationConflict(
                            "This proposal is stale and must be regenerated"
                        )

                decided_at = _now()
                connection.execute(
                    "UPDATE career_profile_change_proposals SET status = ?, decided_at = ?, "
                    "decided_by_principal = ? WHERE proposal_id = ? AND status = 'pending'",
                    (command.decision + "ed", decided_at, principal, proposal_id),
                )
                if command.decision == "accept":
                    self._apply_proposal(connection, complete, proposal, principal, head)
                profile = complete._current_in_connection(  # noqa: SLF001
                    connection
                )
                decided = proposal.model_copy(
                    update={
                        "status": command.decision + "ed",
                        "decided_at": decided_at,
                        "decided_by_principal": principal,
                    }
                )
                result = ProposalDecisionResult(proposal=decided, profile=profile)
                self._record_result_in_connection(
                    connection,
                    principal=principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def history(self, *, limit: int = 100) -> ProfileHistory:
        complete = self._complete()
        profile = complete.current()
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT revision_id, profile_revision, base_profile_revision, actor_principal, "
                "operation, item_id, evidence_id, before_json, after_json, affected_fields_json, "
                "reason, proposal_id, undo_of_revision_id, actor_kind, created_at "
                "FROM career_profile_complete_revisions "
                "ORDER BY profile_revision DESC LIMIT ?",
                (limit,),
            ).fetchall()
        revisions: list[ProfileHistoryRevision] = []
        for row in rows:
            before = json.loads(str(row[7])) if row[7] is not None else None
            after = json.loads(str(row[8])) if row[8] is not None else None
            revisions.append(
                ProfileHistoryRevision(
                    revision_id=str(row[0]),
                    profile_revision=int(row[1]),
                    base_profile_revision=int(row[2]),
                    actor_principal=str(row[3]),
                    actor_kind=(
                        cast(ActorKind, str(row[13]))
                        if row[13] is not None
                        else self._legacy_actor_kind(
                            principal=str(row[3]),
                            proposal_id=(
                                str(row[11]) if row[11] is not None else None
                            ),
                            before=before,
                            after=after,
                        )
                    ),
                    operation=str(row[4]),
                    item_id=str(row[5]) if row[5] is not None else None,
                    evidence_id=str(row[6]) if row[6] is not None else None,
                    before=before,
                    after=after,
                    affected_fields=json.loads(str(row[9])),
                    reason=str(row[10]) if row[10] is not None else None,
                    proposal_id=str(row[11]) if row[11] is not None else None,
                    undo_of_revision_id=(
                        str(row[12]) if row[12] is not None else None
                    ),
                    undoable=(
                        int(row[1]) == profile.profile_revision
                        and str(row[4]) in {"item.upsert", "item.remove"}
                    ),
                    created_at=str(row[14]),
                )
            )
        return ProfileHistory(
            profile_revision=profile.profile_revision,
            revisions=revisions,
        )

    def undo(
        self,
        *,
        revision_id: str,
        principal: str,
        command: ProfileUndoRequest,
    ) -> CareerProfileCompleteCurrent:
        complete = self._complete()
        request_hash = _request_hash(
            "history-undo",
            {"revision_id": revision_id, **command.model_dump(mode="json")},
        )
        replay = self._replay(
            principal=principal,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            model=CareerProfileCompleteCurrent,
        )
        if replay is not None:
            return replay

        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_in_connection(
                    connection,
                    principal=principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    model=CareerProfileCompleteCurrent,
                )
                if replay is not None:
                    connection.rollback()
                    return replay

                head = complete._check_head(  # noqa: SLF001
                    connection, command.expected_profile_revision
                )
                row = connection.execute(
                    "SELECT profile_revision, operation, item_id, before_json, after_json "
                    "FROM career_profile_complete_revisions WHERE revision_id = ?",
                    (revision_id,),
                ).fetchone()
                if row is None:
                    raise CareerProfileCollaborationConflict("History entry was not found")
                if int(row[0]) != head:
                    raise CareerProfileCollaborationConflict(
                        "Undo is available only for the latest Career Profile change"
                    )
                operation = str(row[1])
                item_id = str(row[2]) if row[2] is not None else None
                if operation not in {"item.upsert", "item.remove"} or item_id is None:
                    raise CareerProfileCollaborationConflict(
                        "This Career Profile change cannot be undone"
                    )
                before = json.loads(str(row[3])) if row[3] is not None else None
                current = self._active_item_in_connection(connection, item_id)
                stored_item = connection.execute(
                    "SELECT item_revision FROM career_profile_items WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                if stored_item is None:
                    raise CareerProfileCollaborationConflict(
                        "The Career Profile item changed and cannot be undone"
                    )
                if before is None:
                    connection.execute(
                        "UPDATE career_profile_items SET active = 0, "
                        "item_revision = item_revision + 1, "
                        "actor_principal = ?, updated_at = ? WHERE item_id = ? AND active = 1",
                        (principal, _now(), item_id),
                    )
                    after = None
                    undo_operation = "item.remove"
                else:
                    restored = ProfileItemRecord.model_validate(before).model_copy(
                        update={
                            "item_revision": int(stored_item[0]) + 1,
                            "actor_principal": principal,
                            "updated_at": _now(),
                        }
                    )
                    self._write_item(connection, restored)
                    after = restored.model_dump(mode="json")
                    undo_operation = "item.upsert"
                complete._record_revision(  # noqa: SLF001
                    connection,
                    revision=head + 1,
                    base_revision=head,
                    principal=principal,
                    actor_kind="direct_user",
                    operation=undo_operation,
                    item_id=item_id,
                    evidence_id=None,
                    before=current.model_dump(mode="json") if current else None,
                    after=after,
                    affected=[f"items.{item_id}"],
                    reason="Undid the previous Career Profile change",
                    undo_of_revision_id=revision_id,
                )
                profile = complete._current_in_connection(connection)  # noqa: SLF001
                self._record_result_in_connection(
                    connection,
                    principal=principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    result=profile,
                )
                connection.commit()
                return profile
            except Exception:
                connection.rollback()
                raise

    def _complete(self) -> CareerProfileCompleteStore:
        if self.complete_profile is None:
            raise RuntimeError("Complete Career Profile store is not configured")
        return self.complete_profile

    def _apply_direct_edit(
        self,
        *,
        complete: CareerProfileCompleteStore,
        agent: ConnectedAgent,
        command: AgentProfileEditRequest,
        request_hash: str,
    ) -> AgentEditResult:
        assert command.value is not None
        target_id = command.target_id or _opaque_id("cpi_")
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_in_connection(
                    connection,
                    principal=agent.principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    model=AgentEditResult,
                )
                if replay is not None:
                    connection.rollback()
                    return replay

                current_agent = self._get_agent_in_connection(
                    connection, agent.agent_id
                )
                if not current_agent.active:
                    raise ConnectedAgentAuthorizationError(
                        "Connected agent access is unavailable or has been revoked"
                    )
                if current_agent.trust_mode != "direct":
                    raise CareerProfileCollaborationConflict(
                        "This agent is no longer allowed to edit directly; retry for review"
                    )

                head = complete._check_head(  # noqa: SLF001 - shared transaction boundary
                    connection, command.expected_profile_revision
                )
                current = self._active_item_in_connection(connection, target_id)
                if command.operation == "item.create":
                    if current is not None:
                        raise CareerProfileCollaborationConflict(
                            "The new item identifier is already in use"
                        )
                elif current is None:
                    raise CareerProfileItemNotFound

                timestamp = _now()
                updated = ProfileItemRecord(
                    item_id=target_id,
                    area=_area_for_kind(command.value.kind),
                    value=command.value,
                    review_status="accepted",
                    evidence_ids=command.evidence_ids,
                    provenance=ItemProvenance(
                        method=(
                            "agent_generated"
                            if command.operation == "item.create"
                            else "agent_edit"
                        ),
                        mutation_source="agent_inference",
                    ),
                    item_revision=current.item_revision + 1 if current else 1,
                    actor_principal=current_agent.principal,
                    created_at=current.created_at if current else timestamp,
                    updated_at=timestamp,
                )
                self._write_item(connection, updated)
                complete._record_revision(  # noqa: SLF001 - shared transaction boundary
                    connection,
                    revision=head + 1,
                    base_revision=head,
                    principal=current_agent.principal,
                    actor_kind="autonomous_agent",
                    operation="item.upsert",
                    item_id=target_id,
                    evidence_id=None,
                    before=current.model_dump(mode="json") if current else None,
                    after=updated.model_dump(mode="json"),
                    affected=[f"items.{target_id}"],
                    reason=command.reason,
                )
                profile = complete._current_in_connection(connection)  # noqa: SLF001
                result = AgentEditResult(outcome="applied", profile=profile)
                self._record_result_in_connection(
                    connection,
                    principal=current_agent.principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _create_proposal(
        self,
        *,
        complete: CareerProfileCompleteStore,
        agent: ConnectedAgent,
        command: AgentProfileEditRequest,
        profile: CareerProfileCompleteCurrent,
        before: ProfileItemRecord | None,
        review_reason: str,
        request_hash: str,
    ) -> AgentEditResult:
        target_id = command.target_id or _opaque_id("cpi_")
        timestamp = _now()
        after: ProfileItemRecord | None = None
        if command.operation != "item.remove":
            assert command.value is not None
            after = ProfileItemRecord(
                item_id=target_id,
                area=_area_for_kind(command.value.kind),
                value=command.value,
                review_status="accepted",
                evidence_ids=command.evidence_ids,
                provenance=ItemProvenance(
                    method=(
                        "agent_generated"
                        if command.operation == "item.create"
                        else "agent_edit"
                    ),
                    mutation_source="agent_inference",
                ),
                item_revision=before.item_revision + 1 if before else 1,
                actor_principal=agent.principal,
                created_at=before.created_at if before else timestamp,
                updated_at=timestamp,
            )
        proposal = CareerProfileChangeProposal(
            proposal_id=_opaque_id("cpp_"),
            agent_id=agent.agent_id,
            agent_display_name=agent.display_name,
            reason=command.reason,
            review_reason=review_reason,
            base_profile_revision=profile.profile_revision,
            operation=command.operation,
            target_id=target_id,
            before=before,
            after=after,
            evidence_ids=command.evidence_ids,
            proposal_sha256="0" * 64,
            status="pending",
            created_at=timestamp,
        )
        proposal = proposal.model_copy(
            update={"proposal_sha256": _proposal_digest(proposal)}
        )
        result = AgentEditResult(
            outcome="proposal",
            profile=profile,
            proposal=proposal,
        )
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_in_connection(
                    connection,
                    principal=agent.principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    model=AgentEditResult,
                )
                if replay is not None:
                    connection.rollback()
                    return replay

                current_agent = self._get_agent_in_connection(
                    connection, agent.agent_id
                )
                if not current_agent.active:
                    raise ConnectedAgentAuthorizationError(
                        "Connected agent access is unavailable or has been revoked"
                    )
                current_profile = complete._current_in_connection(  # noqa: SLF001
                    connection
                )
                if current_profile.profile_revision != command.expected_profile_revision:
                    raise CareerProfileRevisionConflict(
                        current_profile.profile_revision
                    )
                current_before = self._target_item(current_profile, command)
                self._validate_evidence_links(
                    current_profile, current_before, command.evidence_ids
                )
                if (
                    self._review_reason(
                        current_agent.trust_mode, current_before, command
                    )
                    is None
                ):
                    raise CareerProfileCollaborationConflict(
                        "This agent's edit mode changed; retry the edit"
                    )

                connection.execute(
                    "INSERT INTO career_profile_change_proposals("
                    "proposal_id, agent_id, reason, review_reason, base_profile_revision, "
                    "operation, target_id, before_json, after_json, evidence_ids_json, "
                    "payload_sha256, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        proposal.proposal_id,
                        proposal.agent_id,
                        proposal.reason,
                        proposal.review_reason,
                        proposal.base_profile_revision,
                        proposal.operation,
                        proposal.target_id,
                        _canonical_json(proposal.before.model_dump(mode="json"))
                        if proposal.before
                        else None,
                        _canonical_json(proposal.after.model_dump(mode="json"))
                        if proposal.after
                        else None,
                        _canonical_json(proposal.evidence_ids),
                        proposal.proposal_sha256,
                        proposal.created_at,
                    ),
                )
                self._record_result_in_connection(
                    connection,
                    principal=current_agent.principal,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    result=result,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return result

    @staticmethod
    def _target_item(
        profile: CareerProfileCompleteCurrent,
        command: AgentProfileEditRequest,
    ) -> ProfileItemRecord | None:
        if command.operation == "item.create":
            return None
        item = next(
            (candidate for candidate in profile.items if candidate.item_id == command.target_id),
            None,
        )
        if item is None:
            raise CareerProfileItemNotFound
        return item

    @staticmethod
    def _validate_evidence_links(
        profile: CareerProfileCompleteCurrent,
        before: ProfileItemRecord | None,
        evidence_ids: list[str],
    ) -> None:
        evidence = {item.evidence_id: item for item in profile.source_evidence}
        historical = set(before.evidence_ids if before else [])
        for evidence_id in evidence_ids:
            record = evidence.get(evidence_id)
            if record is None or (not record.active and evidence_id not in historical):
                raise CareerProfileValueError("New Evidence links must exist and be active")

    @staticmethod
    def _review_reason(
        trust_mode: TrustMode,
        before: ProfileItemRecord | None,
        command: AgentProfileEditRequest,
    ) -> str | None:
        if command.operation == "item.remove":
            return "Removing Career Profile information always needs your approval."
        assert command.value is not None
        if command.value.kind == "identity" or (
            before is not None and before.value.kind == "identity"
        ):
            return "Identity changes always need your approval."
        if before is not None and not set(before.evidence_ids).issubset(command.evidence_ids):
            return "Removing an Evidence link always needs your approval."
        if before is not None and before.value.kind == "claim":
            previous = before.value.model_dump(mode="json")
            current = command.value.model_dump(mode="json")
            if command.value.kind != "claim" or not set(previous["qualifiers"]).issubset(
                current["qualifiers"]
            ) or not set(previous["forbidden_uses"]).issubset(current["forbidden_uses"]):
                return "Loosening a claim boundary always needs your approval."
        if trust_mode == "review":
            return "This agent is set to Review every change."
        return None

    def _apply_proposal(
        self,
        connection: sqlite3.Connection,
        complete: CareerProfileCompleteStore,
        proposal: CareerProfileChangeProposal,
        principal: str,
        head: int,
    ) -> None:
        current = self._active_item_in_connection(connection, proposal.target_id)
        if proposal.operation == "item.create":
            if current is not None or proposal.before is not None or proposal.after is None:
                raise CareerProfileCollaborationConflict(
                    "The proposal target changed and must be regenerated"
                )
            self._write_item(connection, proposal.after)
            before_json = None
            after_json = proposal.after.model_dump(mode="json")
            operation = "item.upsert"
        elif proposal.operation == "item.update":
            if (
                current is None
                or proposal.before is None
                or proposal.after is None
                or current.model_dump(mode="json")
                != proposal.before.model_dump(mode="json")
            ):
                raise CareerProfileCollaborationConflict(
                    "The proposed item changed and must be regenerated"
                )
            self._write_item(connection, proposal.after)
            before_json = proposal.before.model_dump(mode="json")
            after_json = proposal.after.model_dump(mode="json")
            operation = "item.upsert"
        else:
            if (
                current is None
                or proposal.before is None
                or current.model_dump(mode="json")
                != proposal.before.model_dump(mode="json")
            ):
                raise CareerProfileCollaborationConflict(
                    "The proposed item changed and must be regenerated"
                )
            connection.execute(
                "UPDATE career_profile_items SET active = 0, item_revision = item_revision + 1, "
                "actor_principal = ?, updated_at = ? WHERE item_id = ? AND active = 1",
                (principal, _now(), proposal.target_id),
            )
            before_json = proposal.before.model_dump(mode="json")
            after_json = None
            operation = "item.remove"
        complete._record_revision(  # noqa: SLF001
            connection,
            revision=head + 1,
            base_revision=head,
            principal=principal,
            actor_kind="user_proposal_decision",
            operation=operation,
            item_id=proposal.target_id,
            evidence_id=None,
            before=before_json,
            after=after_json,
            affected=[f"items.{proposal.target_id}"],
            reason=proposal.reason,
            proposal_id=proposal.proposal_id,
        )

    @staticmethod
    def _write_item(connection: sqlite3.Connection, item: ProfileItemRecord) -> None:
        connection.execute(
            "INSERT INTO career_profile_items(item_id, value_json, provenance_json, "
            "review_status, evidence_ids_json, item_revision, actor_principal, active, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(item_id) DO UPDATE SET value_json = excluded.value_json, "
            "provenance_json = excluded.provenance_json, review_status = excluded.review_status, "
            "evidence_ids_json = excluded.evidence_ids_json, "
            "item_revision = excluded.item_revision, "
            "actor_principal = excluded.actor_principal, active = 1, "
            "updated_at = excluded.updated_at",
            (
                item.item_id,
                _canonical_json(item.value.model_dump(mode="json")),
                _canonical_json(item.provenance.model_dump(mode="json")),
                item.review_status,
                _canonical_json(item.evidence_ids),
                item.item_revision,
                item.actor_principal,
                item.created_at,
                item.updated_at,
            ),
        )

    @staticmethod
    def _active_item_in_connection(
        connection: sqlite3.Connection, item_id: str
    ) -> ProfileItemRecord | None:
        row = connection.execute(
            "SELECT value_json, provenance_json, review_status, evidence_ids_json, "
            "item_revision, actor_principal, created_at, updated_at "
            "FROM career_profile_items WHERE item_id = ? AND active = 1",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        return CareerProfileCompleteStore._item_from_row(  # noqa: SLF001
            (item_id, *row)
        )

    @staticmethod
    def _legacy_actor_kind(
        *,
        principal: str,
        proposal_id: str | None,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
    ) -> ActorKind:
        if proposal_id is not None:
            return "user_proposal_decision"
        if principal.startswith("device:"):
            return "direct_user"
        candidate = after or before or {}
        provenance = candidate.get("provenance")
        if isinstance(provenance, dict):
            source = provenance.get("mutation_source")
            if source == "authenticated_user_instruction":
                return "authenticated_user_instruction"
            if source == "deterministic_source_mapping":
                return "deterministic_source_mapping"
            if source == "agent_inference":
                return "autonomous_agent"
        if principal.startswith("agent:"):
            return "autonomous_agent"
        return "direct_user"

    @staticmethod
    def _proposal_select() -> str:
        return (
            "SELECT proposal.proposal_id, proposal.agent_id, agent.display_name, "
            "proposal.reason, proposal.review_reason, proposal.base_profile_revision, "
            "proposal.operation, proposal.target_id, proposal.before_json, proposal.after_json, "
            "proposal.evidence_ids_json, proposal.payload_sha256, proposal.status, "
            "proposal.created_at, proposal.decided_at, proposal.decided_by_principal "
            "FROM career_profile_change_proposals AS proposal "
            "JOIN career_profile_connected_agents AS agent ON agent.agent_id = proposal.agent_id"
        )

    def _get_proposal_in_connection(
        self, connection: sqlite3.Connection, proposal_id: str
    ) -> CareerProfileChangeProposal:
        row = connection.execute(
            self._proposal_select() + " WHERE proposal.proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise CareerProfileCollaborationConflict("Proposal was not found")
        return self._proposal_from_row(row)

    @staticmethod
    def _proposal_from_row(
        row: sqlite3.Row | tuple[object, ...],
    ) -> CareerProfileChangeProposal:
        return CareerProfileChangeProposal(
            proposal_id=str(row[0]),
            agent_id=str(row[1]),
            agent_display_name=str(row[2]),
            reason=str(row[3]),
            review_reason=str(row[4]),
            base_profile_revision=int(row[5]),
            operation=str(row[6]),  # type: ignore[arg-type]
            target_id=str(row[7]),
            before=(
                ProfileItemRecord.model_validate_json(str(row[8]))
                if row[8] is not None
                else None
            ),
            after=(
                ProfileItemRecord.model_validate_json(str(row[9]))
                if row[9] is not None
                else None
            ),
            evidence_ids=json.loads(str(row[10])),
            proposal_sha256=str(row[11]),
            status=str(row[12]),  # type: ignore[arg-type]
            created_at=str(row[13]),
            decided_at=str(row[14]) if row[14] is not None else None,
            decided_by_principal=str(row[15]) if row[15] is not None else None,
        )

    @staticmethod
    def _agent_from_row(row: sqlite3.Row | tuple[object, ...]) -> ConnectedAgent:
        return ConnectedAgent(
            agent_id=str(row[0]),
            display_name=str(row[1]),
            principal=str(row[2]),
            trust_mode=str(row[3]),  # type: ignore[arg-type]
            active=bool(row[4]),
            connected_at=str(row[5]),
            updated_at=str(row[6]),
            disconnected_at=str(row[7]) if row[7] is not None else None,
        )

    def _get_agent_in_connection(
        self, connection: sqlite3.Connection, agent_id: str
    ) -> ConnectedAgent:
        row = connection.execute(
            "SELECT agent_id, display_name, principal, trust_mode, active, connected_at, "
            "updated_at, disconnected_at FROM career_profile_connected_agents "
            "WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            raise ConnectedAgentAuthorizationError("Connected agent was not found")
        return self._agent_from_row(row)

    def _replay(
        self,
        *,
        principal: str,
        idempotency_key: str,
        request_hash: str,
        model: type[ModelT],
    ) -> ModelT | None:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            return self._replay_in_connection(
                connection,
                principal=principal,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                model=model,
            )

    @staticmethod
    def _replay_in_connection(
        connection: sqlite3.Connection,
        *,
        principal: str,
        idempotency_key: str,
        request_hash: str,
        model: type[ModelT],
    ) -> ModelT | None:
        row = connection.execute(
            "SELECT request_hash, result_json "
            "FROM career_profile_collaboration_idempotency "
            "WHERE actor_principal = ? AND idempotency_key = ?",
            (principal, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if not hmac.compare_digest(str(row[0]), request_hash):
            raise CareerProfileIdempotencyConflict
        return model.model_validate_json(str(row[1]))

    @staticmethod
    def _record_result_in_connection(
        connection: sqlite3.Connection,
        *,
        principal: str,
        idempotency_key: str,
        request_hash: str,
        result: StrictModel,
    ) -> None:
        connection.execute(
            "INSERT INTO career_profile_collaboration_idempotency("
            "actor_principal, idempotency_key, request_hash, result_json) "
            "VALUES (?, ?, ?, ?)",
            (
                principal,
                idempotency_key,
                request_hash,
                _canonical_json(result.model_dump(mode="json")),
            ),
        )

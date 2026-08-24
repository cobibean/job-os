import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent_gateway import (
    AgentContext,
    AgentGateway,
    AmbiguousDeliveryError,
    ConnectedAgentProvider,
    GatewayEvent,
)
from .career_profile_context import CareerProfileContextStore
from .conversation_store import ConversationStore
from .redaction import safe_error_summary, sanitize_user_text
from .state_store import ConversationBusy, JobOsStateStore

logger = logging.getLogger(__name__)

BROWSER_SAVE_IDEMPOTENCY_PREFIX = "browser-save-"
FRESH_AGENT_SESSION_CONTEXT_KEY = "_fresh_agent_session"


class ConversationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SendMessageRequest(ConversationModel):
    text: str = Field(min_length=1, max_length=12_000)
    idempotency_key: str = Field(min_length=8, max_length=200)

    @field_validator("text")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message text must not be blank")
        return value.strip()


class RetryTurnRequest(ConversationModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


class TurnMutationResponse(ConversationModel):
    turn_id: str
    message_id: str | None = None
    source_turn_id: str | None = None
    status: str | None = None
    created: bool | None = None


class ConnectionResponse(ConversationModel):
    state: Literal["online", "connecting", "offline"]


class ConversationJobContext(ConversationModel):
    selected_job_id: str | None = None
    active_artifact_id: str | None = None
    active_artifact_page: int = Field(default=1, ge=1)
    active_artifact_zoom: float = Field(default=1.0, ge=0.5, le=3.0)


class CreateConversationRequest(ConversationModel):
    selected_job_id: str | None = Field(default=None, max_length=512)


class ConversationDocumentViewRequest(ConversationModel):
    active_artifact_id: str | None = Field(default=None, pattern=r"^art_[A-Za-z0-9_-]{16,80}$")
    active_artifact_page: int = Field(default=1, ge=1, le=5000)
    active_artifact_zoom: float = Field(default=1.0, ge=0.5, le=3.0)
    origin: Literal["user", "mcp"] = "user"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class ConversationJobContextMutation(ConversationModel):
    event_id: int
    job_context: ConversationJobContext


class ConversationResponse(ConversationModel):
    conversation_id: str
    title: str
    position: int
    created_at: str
    entries: list[dict[str, object]]
    active_turn: dict[str, object] | None
    connection: ConnectionResponse
    recovery_state: Literal["ready", "recovering", "quarantined"]
    latest_event_id: int
    job_context: ConversationJobContext


class ConversationService:
    def __init__(
        self,
        store: ConversationStore | JobOsStateStore,
        gateway: AgentGateway,
        conversation_id: str | None = None,
        profile_id: str | None = None,
        career_profile_principal: str | None = None,
        career_profile_context: CareerProfileContextStore | None = None,
        career_profile_agent_id: str | None = None,
    ) -> None:
        if isinstance(store, JobOsStateStore):
            conversation_id = conversation_id or store.first_active_conversation_id()
            store = store.conversation_store(conversation_id)
        self.conversation_id = conversation_id or store.conversation_id
        self.profile_id = profile_id
        self.store = store
        self.gateway = gateway
        self.career_profile_principal = career_profile_principal
        self.career_profile_context = career_profile_context
        self.career_profile_agent_id = career_profile_agent_id
        self._event_task: asyncio.Task[None] | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._recovery_turn_id: str | None = None
        self._isolated_session_ids: set[str] = set()
        self._submission_lock = asyncio.Lock()
        self._turn_scope_lock = asyncio.Lock()
        self._event_consumer_restart_delay = 1.0

    @asynccontextmanager
    async def turn_scope_lease(self) -> AsyncIterator[None]:
        """Keep cancellation settlement outside an authorized MCP operation."""
        async with self._turn_scope_lock:
            yield

    def _restore_session_after_isolated_turn(self, turn_id: str) -> None:
        self.store.restore_isolated_agent_session(turn_id)

    async def start(self) -> None:
        with suppress(Exception):
            await self.gateway.start()
        self._event_task = asyncio.create_task(self._supervise_gateway_events())
        self._connection_task = asyncio.create_task(self._maintain_gateway_connection())
        await self._recover_persisted_active_turn()

    async def _maintain_gateway_connection(self) -> None:
        reconnect_delay = 1.0
        while True:
            if self.gateway.connection_state != "offline":
                reconnect_delay = 1.0
                await asyncio.sleep(1.0)
                continue
            try:
                await self.gateway.start()
                reconnect_delay = 1.0
                if self.gateway.connection_state == "offline":
                    await asyncio.sleep(reconnect_delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 10.0)

    async def _recover_persisted_active_turn(self) -> None:
        recovery_turn_id = self._recovery_turn_id or self.store.recovery_turn_id()
        if recovery_turn_id:
            try:
                await self._confirm_recovery(recovery_turn_id)
            except Exception as error:
                logger.warning(
                    "Agent startup recovery failed (%s, code=%s, reason=%s)",
                    type(error).__name__,
                    getattr(error, "code", None),
                    str(error) if isinstance(error, RuntimeError) else None,
                )
                self._recovery_turn_id = recovery_turn_id
                self.store.append_conversation_event(
                    turn_id=recovery_turn_id,
                    event_type="status",
                    state="working",
                    summary="Remote turn cleanup is pending",
                    detail={"actionable": True, "recovery_pending": True, "retry": True},
                    source_event_id=f"transport-recovery-pending:{recovery_turn_id}",
                )
                return
        snapshot = self.store.conversation_snapshot()
        active = snapshot.get("active_turn")
        if not isinstance(active, dict):
            return
        turn_id = str(active["turn_id"])
        stored_session_id = self.store.stored_session_id()
        if not stored_session_id:
            self._restore_session_after_isolated_turn(turn_id)
            self.store.recover_active_conversation_turns()
            return
        try:
            await self.gateway.recover_active_turn(stored_session_id, turn_id)
        except Exception:
            self._recovery_turn_id = turn_id
            self.store.mark_recovery_turn(turn_id)
            self.store.append_conversation_event(
                turn_id=turn_id,
                event_type="status",
                state="waiting",
                summary="Remote turn cleanup must be confirmed before new work can start",
                detail={"actionable": True, "recovery_pending": True, "retry": True},
                source_event_id=f"startup-recovery-pending:{turn_id}",
            )
            self.store.transition_active_turn_status(
                turn_id, "waiting", expected=("queued", "running", "waiting")
            )
            return
        won = self.store.settle_active_turn(
            turn_id,
            "interrupted",
            event_type="status",
            summary="Turn interrupted during safe API restart recovery",
            detail={"actionable": True, "reason": "api_restart", "retry": True},
            source_event_id=f"startup-recovery-complete:{turn_id}",
        )
        if won:
            self._restore_session_after_isolated_turn(turn_id)

    async def _confirm_recovery(self, turn_id: str) -> None:
        stored_session_id = (
            self.store.recovery_agent_session_id(turn_id) or self.store.stored_session_id()
        )
        if not stored_session_id:
            raise ConnectionError("Stored agent session is unavailable")
        await self.gateway.recover_active_turn(stored_session_id, turn_id)
        self.store.clear_recovery_turn_if_current(turn_id)
        if self._recovery_turn_id == turn_id:
            self._recovery_turn_id = None

    async def _ensure_recovery_clear(self) -> None:
        turn_id = self.store.recovery_turn_id()
        if not turn_id:
            return
        try:
            await self._confirm_recovery(turn_id)
        except Exception as error:
            logger.warning(
                "Agent recovery confirmation failed (%s, code=%s)",
                type(error).__name__,
                getattr(error, "code", None),
            )
            self._recovery_turn_id = turn_id
            raise ConversationBusy(
                "Remote agent cleanup must be confirmed before new work"
            ) from error

    async def close(self) -> None:
        tasks = [task for task in (self._event_task, self._connection_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.gateway.close()

    def snapshot(self) -> ConversationResponse:
        snapshot = self.store.conversation_snapshot()
        recovery_turn_id = self.store.recovery_turn_id()
        return ConversationResponse.model_validate(
            {
                **snapshot,
                "connection": ConnectionResponse(state=self.gateway.connection_state),
                "recovery_state": (
                    "recovering"
                    if recovery_turn_id and snapshot.get("active_turn") is not None
                    else "quarantined"
                    if recovery_turn_id
                    else "ready"
                ),
            }
        )

    async def send(
        self,
        command: SendMessageRequest,
        *,
        actor_id: str,
        context: dict[str, object],
    ) -> TurnMutationResponse:
        await self._ensure_recovery_clear()
        safe_text = sanitize_user_text(command.text)
        stored_context = dict(context)
        if command.idempotency_key.startswith(BROWSER_SAVE_IDEMPOTENCY_PREFIX):
            stored_context[FRESH_AGENT_SESSION_CONTEXT_KEY] = True
        created = self.store.create_conversation_turn(
            text=safe_text,
            context=stored_context,
            idempotency_key=command.idempotency_key,
            actor_id=actor_id,
            career_profile_principal=self.career_profile_principal,
            career_profile_context=self.career_profile_context,
            career_profile_agent_id=self.career_profile_agent_id,
        )
        turn = self.store.turn_record(str(created["turn_id"]))
        if turn and turn["status"] == "running" and created["created"] is True:
            turn_context = turn.get("context")
            if (
                isinstance(turn_context, dict)
                and turn_context.get(FRESH_AGENT_SESSION_CONTEXT_KEY) is True
            ):
                self.store.begin_isolated_agent_session(str(turn["turn_id"]))
            await self._dispatch(turn)
        return TurnMutationResponse(**created)

    async def retry(
        self, turn_id: str, command: RetryTurnRequest, *, actor_id: str
    ) -> TurnMutationResponse | None:
        await self._ensure_recovery_clear()
        source = self.store.turn_record(turn_id)
        if source is None:
            return None
        if source["status"] not in {"failed", "interrupted"}:
            raise ValueError("Only failed or interrupted turns can be retried")
        source_context = source.get("context")
        if isinstance(source_context, dict) and source_context.get("agent_continuation") is True:
            raise ValueError("Background continuation turns cannot be retried directly")
        created = self.store.create_conversation_turn(
            text=str(source["text"]),
            context=source["context"],
            source_turn_id=turn_id,
            idempotency_key=command.idempotency_key,
            actor_id=actor_id,
            career_profile_principal=self.career_profile_principal,
            career_profile_context=self.career_profile_context,
            career_profile_agent_id=self.career_profile_agent_id,
        )
        turn = self.store.turn_record(str(created["turn_id"]))
        if turn and created["created"] is True:
            turn_context = turn.get("context")
            if (
                isinstance(turn_context, dict)
                and turn_context.get(FRESH_AGENT_SESSION_CONTEXT_KEY) is True
            ):
                self.store.begin_isolated_agent_session(str(turn["turn_id"]))
            await self._dispatch(turn)
        return TurnMutationResponse(**created)

    async def cancel(self, turn_id: str) -> TurnMutationResponse | None:
        async with self._turn_scope_lock:
            return await self._cancel_under_lease(turn_id)

    async def _cancel_under_lease(self, turn_id: str) -> TurnMutationResponse | None:
        initial = self.store.turn_record(turn_id)
        if initial is None:
            return None
        if initial["status"] in {"running", "queued", "waiting"}:
            self.store.request_turn_cancel(turn_id)
        async with self._submission_lock:
            turn = self.store.turn_record(turn_id)
            if turn is None:
                return None
            if turn["status"] in {"running", "queued", "waiting"}:
                transport_confirmed = True
                recovery_turn_id = self._recovery_turn_id or self.store.recovery_turn_id()
                try:
                    if recovery_turn_id == turn_id:
                        stored_session_id = (
                            self.store.recovery_agent_session_id(turn_id)
                            or self.store.stored_session_id()
                        )
                        if not stored_session_id:
                            raise ConnectionError("Stored agent session is unavailable")
                        await self.gateway.recover_active_turn(stored_session_id, turn_id)
                        self.store.clear_recovery_turn_if_current(turn_id)
                    else:
                        await self.gateway.interrupt_turn(turn_id)
                except Exception:
                    transport_confirmed = False
                if recovery_turn_id == turn_id and not transport_confirmed:
                    current = self.store.turn_record(turn_id)
                    return TurnMutationResponse(
                        turn_id=turn_id,
                        message_id=str(turn["message_id"]),
                        source_turn_id=turn.get("source_turn_id"),
                        status=str(current["status"]) if current else "waiting",
                    )
                won = self.store.settle_active_turn(
                    turn_id,
                    "interrupted",
                    event_type="status",
                    summary=(
                        "Turn stopped"
                        if transport_confirmed
                        else "Turn stopped locally; agent interruption was not confirmed"
                    ),
                    detail={
                        "actionable": True,
                        "retry": True,
                        "transport_confirmed": transport_confirmed,
                    },
                    cancel_requested=True,
                )
                if won:
                    self._restore_session_after_isolated_turn(turn_id)
                if won and recovery_turn_id == turn_id:
                    self._recovery_turn_id = None
        current = self.store.turn_record(turn_id)
        return TurnMutationResponse(
            turn_id=turn_id,
            message_id=str(turn["message_id"]),
            source_turn_id=turn.get("source_turn_id"),
            status=str(current["status"]) if current else str(turn["status"]),
        )

    async def _dispatch(self, turn: dict[str, object]) -> None:
        turn_id = str(turn["turn_id"])
        try:
            prior_stored_id = self.store.stored_session_id()
            context = turn["context"]
            assert isinstance(context, dict)
            career_profile = None
            if (
                self.career_profile_context is None
                and self.career_profile_principal is not None
            ):
                snapshot = self.store.bound_career_profile_snapshot(
                    turn_id, principal=self.career_profile_principal
                )
                career_profile = {
                    "snapshot_id": snapshot.snapshot_id,
                    "profile_revision": snapshot.profile_revision,
                    "content_hash": snapshot.content_hash,
                    "projection": {
                        "work_arrangement": (
                            snapshot.projection.work_arrangement.value.model_dump(mode="json")
                            if snapshot.projection.work_arrangement is not None
                            else None
                        )
                    },
                }
            career_profile_context = None
            if self.career_profile_context is not None:
                if self.career_profile_agent_id is None:
                    raise RuntimeError("Career Profile context agent is not configured")
                context_snapshot = self.store.bound_career_profile_context_snapshot(
                    turn_id,
                    context_store=self.career_profile_context,
                    agent_id=self.career_profile_agent_id,
                )
                career_profile_context = context_snapshot.model_dump(mode="json")
            requested_session_id = (
                None if context.get(FRESH_AGENT_SESSION_CONTEXT_KEY) is True else prior_stored_id
            )
            stored_id, _ = await self.gateway.create_or_resume_conversation(requested_session_id)
            if context.get(FRESH_AGENT_SESSION_CONTEXT_KEY) is True:
                self._isolated_session_ids.add(stored_id)
                self.store.record_isolated_agent_session(turn_id, stored_id)
        except AmbiguousDeliveryError as error:
            logger.warning(
                "Agent conversation attachment is ambiguous (%s, code=%s)",
                type(error).__name__,
                getattr(error, "code", None),
            )
            self._restore_session_after_isolated_turn(turn_id)
            self._recovery_turn_id = turn_id
            self.store.mark_recovery_turn(turn_id)
            self.store.append_conversation_event(
                turn_id=turn_id,
                event_type="status",
                state="waiting",
                summary="Agent attachment outcome must be confirmed before retrying",
                detail={"actionable": True, "recovery_pending": True, "retry": False},
                source_event_id=f"ambiguous-attachment:{turn_id}",
            )
            self.store.transition_active_turn_status(
                turn_id, "waiting", expected=("queued", "running", "waiting")
            )
            return
        except Exception as error:
            logger.warning(
                "Agent conversation attachment failed (%s, code=%s)",
                type(error).__name__,
                getattr(error, "code", None),
            )
            current = self.store.turn_record(turn_id)
            if current and current["cancel_requested"]:
                return
            self.store.settle_active_turn(
                turn_id,
                "failed",
                event_type="error",
                summary=safe_error_summary(error),
                detail={"actionable": True, "retry": True},
            )
            self._restore_session_after_isolated_turn(turn_id)
            return
        try:
            async with self._submission_lock:
                if not self.store.prepare_turn_submission(
                    turn_id,
                    prior_stored_id,
                    stored_id,
                    career_profile_context=self.career_profile_context,
                    career_profile_agent_id=self.career_profile_agent_id,
                ):
                    return
                selected_job = context.get("selected_job")
                binding = self.store.binding()
                await self.gateway.submit_turn(
                    str(turn["text"]),
                    AgentContext(
                        turn_id=turn_id,
                        conversation_id=self.conversation_id,
                        selected_job_id=context.get("selected_job_id"),
                        workspace=dict(context.get("workspace", {})),
                        selected_job=(
                            {str(key): str(value) for key, value in selected_job.items()}
                            if isinstance(selected_job, dict)
                            else None
                        ),
                        career_profile=career_profile,
                        career_profile_context=career_profile_context,
                        profile_id=self.profile_id,
                        connected_agent_id=(
                            str(binding["connected_agent_id"])
                            if binding.get("connected_agent_id") is not None
                            else None
                        ),
                        provider=(
                            cast(ConnectedAgentProvider, binding["provider"])
                            if binding.get("provider") in {"hermes", "codex"}
                            else None
                        ),
                        model_id=(
                            str(binding["model_id"])
                            if binding.get("model_id") is not None
                            else None
                        ),
                        reasoning_effort=(
                            str(binding["reasoning_effort"])
                            if binding.get("reasoning_effort") is not None
                            else None
                        ),
                        permission_state={"scope": "global"},
                    ),
                )
        except Exception as error:
            logger.warning(
                "Agent turn submission failed (%s, code=%s)",
                type(error).__name__,
                getattr(error, "code", None),
            )
            current = self.store.turn_record(turn_id)
            if current and current["cancel_requested"]:
                return
            self.store.settle_active_turn(
                turn_id,
                "failed",
                event_type="error",
                summary=safe_error_summary(error),
                detail={"actionable": True, "retry": True},
                quarantine=True,
            )
            self._restore_session_after_isolated_turn(turn_id)

    async def _consume_gateway_events(self) -> None:
        async for event in self.gateway.stream_events():
            if event.event_type == "connection":
                state = event.detail.get("agent_connection")
                if state in {"online", "connecting", "offline"}:
                    self.store.append_conversation_event(
                        turn_id=event.turn_id,
                        event_type="status",
                        state="working",
                        summary=f"Agent {state}",
                        detail=self._event_detail(event, {"agent_connection": state}),
                        source_event_id=event.source_event_id,
                    )
                continue
            if event.event_type == "reconciliation":
                stored_session_id = event.detail.get("stored_session_id")
                if isinstance(stored_session_id, str) and (
                    stored_session_id in self._isolated_session_ids
                    or self.store.consume_ignored_agent_session(stored_session_id)
                ):
                    self._isolated_session_ids.discard(stored_session_id)
                    continue
                if isinstance(stored_session_id, str) and 0 < len(stored_session_id) <= 256:
                    self.store.save_stored_session_id(stored_session_id)
                self.store.append_conversation_event(
                    turn_id=event.turn_id,
                    event_type="status",
                    state="working",
                    summary="Agent session reconciled",
                    detail=self._event_detail(event, {}),
                    source_event_id=event.source_event_id,
                )
                continue
            turn_id = event.turn_id
            if turn_id and self.store.turn_record(turn_id) is None:
                if (
                    event.detail.get("agent_continuation") is True
                    and event.state in {"completed", "failed", "interrupted"}
                    and event.event_type in {"assistant_message", "error"}
                ):
                    self.store.record_agent_continuation(
                        turn_id=turn_id,
                        status=event.state,
                        event_type=event.event_type,
                        summary=event.summary,
                        detail=self._event_detail(event),
                        source_event_id=event.source_event_id,
                        career_profile_principal=self.career_profile_principal,
                        career_profile_context=self.career_profile_context,
                        career_profile_agent_id=self.career_profile_agent_id,
                    )
                continue
            is_terminal = bool(
                turn_id
                and event.state in {"completed", "failed", "interrupted"}
                and event.event_type in {"assistant_message", "error"}
            )
            if is_terminal:
                won = self.store.settle_active_turn(
                    str(turn_id),
                    event.state,
                    event_type=event.event_type,
                    summary=event.summary,
                    detail=self._event_detail(event),
                    source_event_id=event.source_event_id,
                    quarantine=event.detail.get("reason") == "transport_lost",
                )
                if won:
                    self._restore_session_after_isolated_turn(str(turn_id))
                continue
            continuation_ids = (
                tuple(_continuation_ids(event.detail))
                if turn_id and event.event_type == "activity"
                else ()
            )
            event_id = self.store.append_conversation_event(
                turn_id=turn_id,
                event_type=event.event_type,
                state=event.state,
                summary=event.summary,
                detail=self._event_detail(event),
                source_event_id=event.source_event_id,
                continuation_ids=continuation_ids,
            )
            if (
                event_id is not None
                and turn_id
                and event.event_type == "status"
                and event.state == "waiting"
            ):
                self.store.transition_active_turn_status(
                    turn_id, "waiting", expected=("queued", "running")
                )
            elif (
                event_id is not None
                and turn_id
                and event.event_type == "status"
                and event.state == "working"
            ):
                self.store.transition_active_turn_status(turn_id, "running", expected=("waiting",))

    @staticmethod
    def _event_detail(
        event: GatewayEvent, detail: dict[str, object] | None = None
    ) -> dict[str, object]:
        safe = dict(event.detail if detail is None else detail)
        if event.activity_id is not None:
            safe["activity_id"] = event.activity_id
        for key, value in (
            ("normalized_kind", event.normalized_kind),
            ("profile_id", event.profile_id),
            ("conversation_id", event.conversation_id),
            ("sequence", event.sequence),
            ("timestamp", event.timestamp),
        ):
            if value is not None:
                safe[key] = value
        return safe

    async def _supervise_gateway_events(self) -> None:
        while True:
            try:
                await self._consume_gateway_events()
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(
                    "Agent event consumer failed (%s, code=%s)",
                    type(error).__name__,
                    getattr(error, "code", None),
                )
                try:
                    snapshot = self.store.conversation_snapshot()
                    active = snapshot.get("active_turn")
                    if isinstance(active, dict):
                        self.store.settle_active_turn(
                            str(active["turn_id"]),
                            "failed",
                            event_type="error",
                            summary="Agent event processing failed",
                            detail={
                                "actionable": True,
                                "reason": "event_consumer_failure",
                                "retry": True,
                            },
                            quarantine=True,
                        )
                    else:
                        self.store.append_conversation_event(
                            turn_id=None,
                            event_type="error",
                            state="failed",
                            summary="Agent event processing failed",
                            detail={
                                "actionable": True,
                                "reason": "event_consumer_failure",
                                "retry": True,
                            },
                        )
                except Exception:
                    logger.exception("Unable to persist agent event consumer quarantine")
                await asyncio.sleep(self._event_consumer_restart_delay)


def _continuation_ids(value: object) -> set[str]:
    """Extract only explicit Hermes continuation identifiers from a tool result."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key in {"continuation_id", "delegation_id"}
                and isinstance(item, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{8,200}", item)
            ):
                found.add(item)
            else:
                found.update(_continuation_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_continuation_ids(item))
    return found


def encode_sse(entry: dict[str, object]) -> str:
    data = json.dumps(entry, separators=(",", ":"))
    event = entry.get("event")
    event_id = event.get("event_id") if isinstance(event, dict) else entry.get("event_id")
    return f"id: {event_id}\nevent: conversation\ndata: {data}\n\n"


class DisconnectProbe(Protocol):
    async def is_disconnected(self) -> bool: ...


async def conversation_event_source(
    store: JobOsStateStore,
    request: DisconnectProbe,
    *,
    cursor: int,
    once: bool = False,
    poll_interval: float = 0.1,
    heartbeat_interval: float = 15.0,
    owner_device_id: str | None = None,
) -> AsyncIterator[str]:
    """Stream ordered durable events promptly with an independent heartbeat clock."""
    current = max(0, cursor)
    heartbeat_at = time.monotonic() + heartbeat_interval
    yield "retry: 2000\n\n"
    while True:
        entries = store.all_conversation_events_after(current, owner_device_id=owner_device_id)
        for entry in entries:
            event = entry["event"]
            assert isinstance(event, dict)
            current = int(event["event_id"])
            yield encode_sse(entry)
        if once or await request.is_disconnected():
            return
        now = time.monotonic()
        if now >= heartbeat_at:
            yield ": heartbeat\n\n"
            heartbeat_at = time.monotonic() + heartbeat_interval
        delay = min(max(poll_interval, 0.01), max(heartbeat_at - time.monotonic(), 0.01))
        await asyncio.sleep(delay)

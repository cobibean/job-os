import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent_gateway import AgentContext, AgentGateway, GatewayEvent
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


class ConnectionResponse(ConversationModel):
    state: Literal["online", "connecting", "offline"]


class ConversationResponse(ConversationModel):
    conversation_id: str
    entries: list[dict[str, object]]
    active_turn: dict[str, object] | None
    connection: ConnectionResponse
    latest_event_id: int


class ConversationService:
    def __init__(self, store: JobOsStateStore, gateway: AgentGateway) -> None:
        self.store = store
        self.gateway = gateway
        self._event_task: asyncio.Task[None] | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._recovery_turn_id: str | None = None
        self._isolated_session_ids: set[str] = set()
        self._submission_lock = asyncio.Lock()
        self._closing = False

    def _restore_session_after_isolated_turn(self, turn_id: str) -> None:
        self.store.restore_isolated_agent_session(turn_id)

    async def start(self) -> None:
        self._closing = False
        try:
            await self.gateway.start()
        except Exception as error:
            logger.info("Agent gateway startup deferred (%s)", type(error).__name__)
        self._event_task = asyncio.create_task(self._supervise_gateway_events())
        self._connection_task = asyncio.create_task(self._maintain_gateway_connection())
        await self._recover_persisted_active_turn()

    async def _maintain_gateway_connection(self) -> None:
        delay = 0.25
        while True:
            try:
                if self.gateway.connection_state == "online":
                    delay = 0.25
                    await asyncio.sleep(delay)
                    continue
                await self.gateway.start()
                if self.gateway.connection_state == "online":
                    delay = 0.25
                else:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 5.0)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.info("Agent gateway reconnect pending (%s)", type(error).__name__)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)

    async def _recover_persisted_active_turn(self) -> None:
        recovery_turn_id = self.store.recovery_turn_id()
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
            self.store.recovery_agent_session_id(turn_id)
            or self.store.stored_session_id()
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
        self._closing = True
        if self._connection_task:
            self._connection_task.cancel()
            await asyncio.gather(self._connection_task, return_exceptions=True)
        if self._event_task:
            self._event_task.cancel()
            await asyncio.gather(self._event_task, return_exceptions=True)
        await self.gateway.close()

    def snapshot(self) -> ConversationResponse:
        snapshot = self.store.conversation_snapshot()
        return ConversationResponse(
            **snapshot,
            connection=ConnectionResponse(state=self.gateway.connection_state),
        )

    async def reset(self, *, actor_id: str) -> ConversationResponse:
        async with self._submission_lock:
            event_task = self._event_task
            self._event_task = None
            if event_task is not None:
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
            try:
                self.store.reset_conversation(actor_id=actor_id)
                await self.gateway.detach_conversation()
                self._recovery_turn_id = None
            finally:
                if event_task is not None:
                    self._event_task = asyncio.create_task(self._supervise_gateway_events())
        return self.snapshot()

    async def send(
        self,
        command: SendMessageRequest,
        *,
        actor_id: str,
        context: dict[str, object],
    ) -> TurnMutationResponse:
        await self._ensure_recovery_clear()
        safe_text = sanitize_user_text(command.text)
        latest_before = int(self.store.conversation_snapshot()["latest_event_id"])
        stored_context = dict(context)
        if command.idempotency_key.startswith(BROWSER_SAVE_IDEMPOTENCY_PREFIX):
            stored_context[FRESH_AGENT_SESSION_CONTEXT_KEY] = True
        created = self.store.create_conversation_turn(
            text=safe_text,
            context=stored_context,
            idempotency_key=command.idempotency_key,
            actor_id=actor_id,
        )
        turn = self.store.turn_record(str(created["turn_id"]))
        latest_after = int(self.store.conversation_snapshot()["latest_event_id"])
        if turn and turn["status"] == "running" and latest_after > latest_before:
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
        latest_before = int(self.store.conversation_snapshot()["latest_event_id"])
        created = self.store.create_conversation_turn(
            text=str(source["text"]),
            context=source["context"],
            source_turn_id=turn_id,
            idempotency_key=command.idempotency_key,
            actor_id=actor_id,
        )
        turn = self.store.turn_record(str(created["turn_id"]))
        latest_after = int(self.store.conversation_snapshot()["latest_event_id"])
        if turn and latest_after > latest_before:
            turn_context = turn.get("context")
            if (
                isinstance(turn_context, dict)
                and turn_context.get(FRESH_AGENT_SESSION_CONTEXT_KEY) is True
            ):
                self.store.begin_isolated_agent_session(str(turn["turn_id"]))
            await self._dispatch(turn)
        return TurnMutationResponse(**created)

    async def cancel(self, turn_id: str) -> TurnMutationResponse | None:
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
                try:
                    if self._recovery_turn_id == turn_id:
                        stored_session_id = self.store.stored_session_id()
                        if not stored_session_id:
                            raise ConnectionError("Stored agent session is unavailable")
                        await self.gateway.recover_active_turn(stored_session_id, turn_id)
                    else:
                        await self.gateway.interrupt_turn(turn_id)
                except Exception:
                    transport_confirmed = False
                if self._recovery_turn_id == turn_id and not transport_confirmed:
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
                if won and self._recovery_turn_id == turn_id:
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
            requested_session_id = (
                None
                if context.get(FRESH_AGENT_SESSION_CONTEXT_KEY) is True
                else prior_stored_id
            )
            stored_id, _ = await self.gateway.create_or_resume_conversation(
                requested_session_id
            )
            if context.get(FRESH_AGENT_SESSION_CONTEXT_KEY) is True:
                self._isolated_session_ids.add(stored_id)
                self.store.record_isolated_agent_session(turn_id, stored_id)
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
                if not self.store.prepare_turn_submission(turn_id, prior_stored_id, stored_id):
                    return
                selected_job = context.get("selected_job")
                await self.gateway.submit_turn(
                    str(turn["text"]),
                    AgentContext(
                        turn_id=turn_id,
                        selected_job_id=context.get("selected_job_id"),
                        workspace=dict(context.get("workspace", {})),
                        selected_job=(
                            {str(key): str(value) for key, value in selected_job.items()}
                            if isinstance(selected_job, dict)
                            else None
                        ),
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

    def _persist_gateway_event(self, event: GatewayEvent) -> None:
        if event.event_type == "connection":
            state = event.detail.get("agent_connection")
            if state in {"online", "connecting", "offline"}:
                self.store.append_conversation_event(
                    turn_id=None,
                    event_type="status",
                    state="working",
                    summary=f"Agent {state}",
                    detail={"agent_connection": state},
                )
            return
        if event.event_type == "reconciliation":
            stored_session_id = event.detail.get("stored_session_id")
            if isinstance(stored_session_id, str) and (
                stored_session_id in self._isolated_session_ids
                or self.store.consume_ignored_agent_session(stored_session_id)
            ):
                self._isolated_session_ids.discard(stored_session_id)
                return
            if isinstance(stored_session_id, str) and 0 < len(stored_session_id) <= 256:
                self.store.save_stored_session_id(stored_session_id)
            return
        turn_id = event.turn_id
        if turn_id and self.store.turn_record(turn_id) is None:
            return
        is_terminal = bool(
            turn_id
            and event.state in {"completed", "failed", "interrupted"}
            and event.event_type in {"assistant_message", "error"}
        )
        if is_terminal:
            self._restore_session_after_isolated_turn(str(turn_id))
            self.store.settle_active_turn(
                str(turn_id),
                event.state,
                event_type=event.event_type,
                summary=event.summary,
                detail={**event.detail, "activity_id": event.activity_id},
                source_event_id=event.source_event_id,
                quarantine=event.detail.get("reason") == "transport_lost",
            )
            return
        if (
            turn_id
            and event.event_type == "status"
            and event.state == "waiting"
        ):
            self.store.transition_active_turn_status(
                turn_id, "waiting", expected=("queued", "running")
            )
        elif (
            turn_id
            and event.event_type == "status"
            and event.state == "working"
        ):
            self.store.transition_active_turn_status(
                turn_id, "running", expected=("waiting",)
            )
        self.store.append_conversation_event(
            turn_id=turn_id,
            event_type=event.event_type,
            state=event.state,
            summary=event.summary,
            detail={**event.detail, "activity_id": event.activity_id},
            source_event_id=event.source_event_id,
        )

    async def _consume_gateway_events(self) -> None:
        async for event in self.gateway.stream_events():
            retry_delay = 0.05
            while True:
                try:
                    self._persist_gateway_event(event)
                    break
                except asyncio.CancelledError:
                    raise
                except (sqlite3.Error, OSError) as error:
                    logger.warning(
                        "Agent event persistence retrying (%s)", type(error).__name__
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 1.0)

    async def _supervise_gateway_events(self) -> None:
        while not self._closing:
            try:
                await self._consume_gateway_events()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Agent event stream restarting (%s)", type(error).__name__)
            if not self._closing:
                await asyncio.sleep(0.1)


def encode_sse(entry: dict[str, object]) -> str:
    data = json.dumps(entry, separators=(",", ":"))
    return f"id: {entry['event_id']}\nevent: conversation\ndata: {data}\n\n"


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
) -> AsyncIterator[str]:
    """Stream ordered durable events promptly with an independent heartbeat clock."""
    current = max(0, cursor)
    heartbeat_at = time.monotonic() + heartbeat_interval
    yield "retry: 2000\n\n"
    while True:
        entries = store.conversation_events_after(current)
        for entry in entries:
            current = int(entry["event_id"])
            yield encode_sse(entry)
        if once or await request.is_disconnected():
            return
        now = time.monotonic()
        if now >= heartbeat_at:
            yield ": heartbeat\n\n"
            heartbeat_at = time.monotonic() + heartbeat_interval
        delay = min(max(poll_interval, 0.01), max(heartbeat_at - time.monotonic(), 0.01))
        await asyncio.sleep(delay)

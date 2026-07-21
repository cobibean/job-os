import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent_gateway import AgentContext, AgentGateway
from .redaction import safe_error_summary, sanitize_user_text
from .state_store import ConversationBusy, JobOsStateStore


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
        self._recovery_turn_id: str | None = None
        self._submission_lock = asyncio.Lock()

    async def start(self) -> None:
        with suppress(Exception):
            await self.gateway.start()
        self._event_task = asyncio.create_task(self._consume_gateway_events())
        await self._recover_persisted_active_turn()

    async def _recover_persisted_active_turn(self) -> None:
        recovery_turn_id = self.store.recovery_turn_id()
        if recovery_turn_id:
            try:
                await self._confirm_recovery(recovery_turn_id)
            except Exception:
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
        self.store.settle_active_turn(
            turn_id,
            "interrupted",
            event_type="status",
            summary="Turn interrupted during safe API restart recovery",
            detail={"actionable": True, "reason": "api_restart", "retry": True},
            source_event_id=f"startup-recovery-complete:{turn_id}",
        )

    async def _confirm_recovery(self, turn_id: str) -> None:
        stored_session_id = self.store.stored_session_id()
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
            self._recovery_turn_id = turn_id
            raise ConversationBusy(
                "Remote agent cleanup must be confirmed before new work"
            ) from error

    async def close(self) -> None:
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
                    self._event_task = asyncio.create_task(self._consume_gateway_events())
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
        created = self.store.create_conversation_turn(
            text=safe_text,
            context=context,
            idempotency_key=command.idempotency_key,
            actor_id=actor_id,
        )
        turn = self.store.turn_record(str(created["turn_id"]))
        latest_after = int(self.store.conversation_snapshot()["latest_event_id"])
        if turn and turn["status"] == "running" and latest_after > latest_before:
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
            stored_id, _ = await self.gateway.create_or_resume_conversation(prior_stored_id)
        except Exception as error:
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
            return
        try:
            async with self._submission_lock:
                if not self.store.prepare_turn_submission(turn_id, prior_stored_id, stored_id):
                    return
                context = turn["context"]
                assert isinstance(context, dict)
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

    async def _consume_gateway_events(self) -> None:
        try:
            async for event in self.gateway.stream_events():
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
                    continue
                if event.event_type == "reconciliation":
                    stored_session_id = event.detail.get("stored_session_id")
                    if isinstance(stored_session_id, str) and 0 < len(stored_session_id) <= 256:
                        self.store.save_stored_session_id(stored_session_id)
                    continue
                turn_id = event.turn_id
                if turn_id and self.store.turn_record(turn_id) is None:
                    continue
                is_terminal = bool(
                    turn_id
                    and event.state in {"completed", "failed", "interrupted"}
                    and event.event_type in {"assistant_message", "error"}
                )
                if is_terminal:
                    self.store.settle_active_turn(
                        str(turn_id),
                        event.state,
                        event_type=event.event_type,
                        summary=event.summary,
                        detail={**event.detail, "activity_id": event.activity_id},
                        source_event_id=event.source_event_id,
                        quarantine=event.detail.get("reason") == "transport_lost",
                    )
                    continue
                event_id = self.store.append_conversation_event(
                    turn_id=turn_id,
                    event_type=event.event_type,
                    state=event.state,
                    summary=event.summary,
                    detail={**event.detail, "activity_id": event.activity_id},
                    source_event_id=event.source_event_id,
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
                    self.store.transition_active_turn_status(
                        turn_id, "running", expected=("waiting",)
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            return


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

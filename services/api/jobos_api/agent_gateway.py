import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

ConnectionState = Literal["online", "connecting", "offline"]
ConnectedAgentProvider = Literal["hermes", "codex"]
NormalizedEventKind = Literal[
    "turn_started",
    "assistant_text_delta",
    "reasoning_activity",
    "tool_started",
    "tool_progress",
    "tool_review_required",
    "tool_completed",
    "turn_completed",
    "turn_cancelled",
    "turn_failed",
    "connection_changed",
    "recovery_required",
]


class AmbiguousDeliveryError(RuntimeError):
    """The provider may have accepted an operation whose result was not observed."""


class DefinitiveSessionCreationError(RuntimeError):
    """The provider rejected session creation before accepting it."""


@dataclass(frozen=True)
class AgentContext:
    turn_id: str
    selected_job_id: str | None
    workspace: dict[str, object]
    conversation_id: str
    selected_job: dict[str, str] | None = None
    career_profile: dict[str, object] | None = None
    career_profile_context: dict[str, object] | None = None
    profile_id: str | None = None
    connected_agent_id: str | None = None
    provider: ConnectedAgentProvider | None = None
    model_id: str | None = None
    reasoning_effort: str | None = None
    permission_state: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayEvent:
    event_type: str
    state: str
    summary: str
    detail: dict[str, object] = field(default_factory=dict)
    turn_id: str | None = None
    source_event_id: str | None = None
    activity_id: str | None = None
    normalized_kind: NormalizedEventKind | None = None
    profile_id: str | None = None
    conversation_id: str | None = None
    sequence: int | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class RuntimeBinding:
    connected_agent_id: str | None
    provider: ConnectedAgentProvider | None
    model_id: str | None
    reasoning_effort: str | None
    binding_state: str | None
    creation_state: str
    lock_reason: str | None

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> "RuntimeBinding":
        provider_value = value.get("provider")
        if provider_value not in {None, "hermes", "codex"}:
            raise ValueError("Connected Agent provider is invalid")
        provider = cast(ConnectedAgentProvider | None, provider_value)
        binding = cls(
            connected_agent_id=(
                str(value["connected_agent_id"])
                if value.get("connected_agent_id") is not None
                else None
            ),
            provider=provider,
            model_id=str(value["model_id"]) if value.get("model_id") is not None else None,
            reasoning_effort=(
                str(value["reasoning_effort"])
                if value.get("reasoning_effort") is not None
                else None
            ),
            binding_state=(
                str(value["binding_state"]) if value.get("binding_state") is not None else None
            ),
            creation_state=str(value.get("creation_state") or "locked"),
            lock_reason=(
                str(value["lock_reason"]) if value.get("lock_reason") is not None else None
            ),
        )
        supplied = (
            binding.connected_agent_id,
            binding.provider,
            binding.model_id,
            binding.reasoning_effort,
        )
        if binding.connected_agent_id is None:
            if any(item is not None for item in supplied[1:]):
                raise ValueError("Conversation binding is incomplete")
            return binding
        if binding.binding_state == "sealed" and any(item is None for item in supplied):
            raise ValueError("Sealed conversation binding is incomplete")
        if binding.provider is None:
            raise ValueError("Conversation binding has no provider")
        return binding


class AgentGateway(Protocol):
    @property
    def connection_state(self) -> ConnectionState: ...

    async def start(self) -> None: ...

    async def create_or_resume_conversation(
        self, stored_session_id: str | None
    ) -> tuple[str, str]: ...

    async def detach_conversation(self) -> None: ...

    async def submit_turn(self, text: str, context: AgentContext) -> None: ...

    def stream_events(self) -> AsyncIterator[GatewayEvent]: ...

    async def interrupt_turn(self, turn_id: str) -> None: ...

    async def respond_to_review(
        self, turn_id: str, approval_id: str, *, approved: bool
    ) -> None: ...

    async def recover_active_turn(self, stored_session_id: str, turn_id: str) -> None: ...

    async def close(self) -> None: ...


class AgentGatewayFactory(Protocol):
    """Creates one isolated gateway connection per active JobOS conversation."""

    def create(self, conversation_id: str) -> AgentGateway: ...


class AgentRuntimeRouter:
    """Resolve an immutable chat binding to one isolated provider gateway."""

    def __init__(
        self,
        adapters: dict[
            ConnectedAgentProvider | tuple[ConnectedAgentProvider, str], AgentGatewayFactory
        ],
        *,
        profile_id: str,
        compatibility_provider: ConnectedAgentProvider = "hermes",
    ) -> None:
        self._adapters = dict(adapters)
        self._profile_id = profile_id
        self._compatibility_provider = compatibility_provider
        self._session_owners: dict[tuple[ConnectedAgentProvider, str, str], str] = {}
        self._session_lock = asyncio.Lock()

    def create(self, conversation_id: str, binding_value: dict[str, object]) -> AgentGateway:
        binding = RuntimeBinding.from_mapping(binding_value)
        raw_sequences = binding_value.get("_normalized_event_sequences", {})
        initial_sequences = (
            {
                str(turn_id): int(sequence)
                for turn_id, sequence in raw_sequences.items()
                if isinstance(turn_id, str) and isinstance(sequence, int) and sequence >= 0
            }
            if isinstance(raw_sequences, dict)
            else {}
        )
        provider = cast(
            ConnectedAgentProvider, binding.provider or self._compatibility_provider
        )
        factory = (
            self._adapters.get((provider, binding.connected_agent_id))
            if binding.connected_agent_id is not None
            else None
        ) or self._adapters.get(provider)
        if factory is None:
            raise ConnectionError("Connected Agent provider is unavailable")
        return _RoutedAgentGateway(
            router=self,
            gateway=factory.create(conversation_id),
            profile_id=self._profile_id,
            conversation_id=conversation_id,
            binding=binding,
            provider=provider,
            initial_sequences=initial_sequences,
        )

    async def claim_sessions(
        self,
        provider: ConnectedAgentProvider,
        identity: str,
        session_ids: tuple[str, ...],
        conversation_id: str,
    ) -> None:
        async with self._session_lock:
            keys = tuple(
                (provider, identity, session_id) for session_id in dict.fromkeys(session_ids)
            )
            if any(
                (owner := self._session_owners.get(key)) is not None and owner != conversation_id
                for key in keys
            ):
                raise RuntimeError("Provider session is already owned by another conversation")
            for key in keys:
                self._session_owners[key] = conversation_id


class _RoutedAgentGateway:
    """Seal scope, session ownership, cancellation, and normalized event ordering."""

    def __init__(
        self,
        *,
        router: AgentRuntimeRouter,
        gateway: AgentGateway,
        profile_id: str,
        conversation_id: str,
        binding: RuntimeBinding,
        provider: ConnectedAgentProvider,
        initial_sequences: dict[str, int],
    ) -> None:
        self._router = router
        self._gateway = gateway
        self._profile_id = profile_id
        self._conversation_id = conversation_id
        self._binding = binding
        self._provider: ConnectedAgentProvider = provider
        self._session_identity = binding.connected_agent_id or f"compatibility:{provider}"
        self._active_turn_id: str | None = None
        self._sequences = dict(initial_sequences)
        self._terminal_turns: set[str] = set()
        self._source_event_ids: set[tuple[str, str]] = set()
        self._events: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._event_task: asyncio.Task[None] | None = None

    @property
    def connection_state(self) -> ConnectionState:
        return self._gateway.connection_state

    async def start(self) -> None:
        await self._gateway.start()
        if self._event_task is None or self._event_task.done():
            self._event_task = asyncio.create_task(self._pump_events())

    async def create_or_resume_conversation(self, stored_session_id: str | None) -> tuple[str, str]:
        if stored_session_id is not None:
            await self._router.claim_sessions(
                self._provider,
                self._session_identity,
                (stored_session_id,),
                self._conversation_id,
            )
        stored, live = await self._gateway.create_or_resume_conversation(stored_session_id)
        try:
            await self._router.claim_sessions(
                self._provider,
                self._session_identity,
                (stored, live),
                self._conversation_id,
            )
        except BaseException:
            await self._gateway.detach_conversation()
            raise
        return stored, live

    async def detach_conversation(self) -> None:
        self._active_turn_id = None
        await self._gateway.detach_conversation()

    def _validate_turn(self, context: AgentContext) -> None:
        if (
            context.profile_id != self._profile_id
            or context.conversation_id != self._conversation_id
        ):
            raise ValueError("Trusted turn scope does not match the routed conversation")
        expected = (
            self._binding.connected_agent_id,
            self._binding.provider or self._provider,
            self._binding.model_id,
            self._binding.reasoning_effort,
        )
        actual = (
            context.connected_agent_id,
            context.provider,
            context.model_id,
            context.reasoning_effort,
        )
        if self._binding.connected_agent_id is not None and actual != expected:
            raise ValueError("Trusted turn binding does not match the routed conversation")
        if self._active_turn_id is not None and self._active_turn_id != context.turn_id:
            raise RuntimeError("A provider turn is already active for this conversation")

    async def submit_turn(self, text: str, context: AgentContext) -> None:
        self._validate_turn(context)
        self._active_turn_id = context.turn_id
        self._sequences.setdefault(context.turn_id, 0)
        self._terminal_turns.discard(context.turn_id)
        await self._events.put(
            self._normalized(
                GatewayEvent("status", "working", "", turn_id=context.turn_id),
                forced_kind="turn_started",
            )
        )
        try:
            await self._gateway.submit_turn(text, context)
        except BaseException:
            self._active_turn_id = None
            raise

    def stream_events(self) -> AsyncIterator[GatewayEvent]:
        return self._stream_events()

    async def _stream_events(self) -> AsyncIterator[GatewayEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def interrupt_turn(self, turn_id: str) -> None:
        if self._active_turn_id != turn_id:
            return
        try:
            await self._gateway.interrupt_turn(turn_id)
        finally:
            if self._active_turn_id == turn_id:
                self._active_turn_id = None

    async def respond_to_review(
        self, turn_id: str, approval_id: str, *, approved: bool
    ) -> None:
        if self._active_turn_id != turn_id:
            raise ValueError("Turn is not active for this conversation")
        await self._gateway.respond_to_review(turn_id, approval_id, approved=approved)

    async def recover_active_turn(self, stored_session_id: str, turn_id: str) -> None:
        await self._router.claim_sessions(
            self._provider,
            self._session_identity,
            (stored_session_id,),
            self._conversation_id,
        )
        self._active_turn_id = turn_id
        await self._gateway.recover_active_turn(stored_session_id, turn_id)
        if self._active_turn_id == turn_id:
            self._active_turn_id = None

    async def _pump_events(self) -> None:
        try:
            async for event in self._gateway.stream_events():
                if event.event_type == "reconciliation":
                    session_id = event.detail.get("stored_session_id")
                    if isinstance(session_id, str):
                        try:
                            await self._router.claim_sessions(
                                self._provider,
                                self._session_identity,
                                (session_id,),
                                self._conversation_id,
                            )
                        except RuntimeError:
                            await self._gateway.detach_conversation()
                            continue
                normalized = self._normalized(event)
                if normalized is not None:
                    await self._events.put(normalized)
        finally:
            await self._events.put(None)

    @staticmethod
    def _kind(event: GatewayEvent) -> NormalizedEventKind:
        if event.event_type == "connection":
            return "connection_changed"
        if event.event_type == "reconciliation":
            return "connection_changed"
        if event.event_type == "error":
            return (
                "recovery_required"
                if event.detail.get("reason") == "transport_lost"
                else "turn_failed"
            )
        if event.event_type == "assistant_message":
            if event.state == "completed":
                return "turn_completed"
            if event.state == "interrupted":
                return "turn_cancelled"
            if event.state == "failed":
                return "turn_failed"
            return "assistant_text_delta"
        if event.event_type == "activity":
            return "tool_completed" if event.state == "completed" else "tool_progress"
        if event.event_type == "status" and event.state == "waiting":
            return "tool_review_required"
        return "reasoning_activity"

    def _normalized(
        self,
        event: GatewayEvent,
        *,
        forced_kind: NormalizedEventKind | None = None,
    ) -> GatewayEvent | None:
        kind = forced_kind or self._kind(event)
        turn_id = event.turn_id or self._active_turn_id
        if kind == "connection_changed" or event.event_type == "reconciliation":
            if turn_id is None:
                # Keep lifecycle updates internal rather than emitting a malformed
                # normalized event without a trusted turn scope.
                return GatewayEvent(**{**event.__dict__, "normalized_kind": None})
            sequence = self._sequences.get(turn_id, 0) + 1
            self._sequences[turn_id] = sequence
            return GatewayEvent(
                **{
                    **event.__dict__,
                    "turn_id": turn_id,
                    "normalized_kind": kind,
                    "profile_id": self._profile_id,
                    "conversation_id": self._conversation_id,
                    "sequence": sequence,
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )
        if turn_id is None or turn_id != self._active_turn_id:
            return None
        source_event_id = event.source_event_id
        if source_event_id is not None:
            source_key = (turn_id, source_event_id)
            if source_key in self._source_event_ids:
                return None
            self._source_event_ids.add(source_key)
        if turn_id in self._terminal_turns:
            return None
        sequence = self._sequences.get(turn_id, 0) + 1
        self._sequences[turn_id] = sequence
        if source_event_id is None:
            material = json.dumps(
                [self._conversation_id, turn_id, kind, sequence, event.event_type, event.state],
                separators=(",", ":"),
            )
            source_event_id = f"jobos:{hashlib.sha256(material.encode()).hexdigest()}"
        if kind in {"turn_completed", "turn_cancelled", "turn_failed", "recovery_required"}:
            self._terminal_turns.add(turn_id)
            self._active_turn_id = None
        return GatewayEvent(
            event_type=event.event_type,
            state=event.state,
            summary=event.summary,
            detail=event.detail,
            turn_id=turn_id,
            source_event_id=source_event_id,
            activity_id=event.activity_id,
            normalized_kind=kind,
            profile_id=self._profile_id,
            conversation_id=self._conversation_id,
            sequence=sequence,
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

    async def close(self) -> None:
        if self._event_task is not None:
            self._event_task.cancel()
            await asyncio.gather(self._event_task, return_exceptions=True)
        await self._gateway.close()
        await self._events.put(None)


class OfflineAgentGatewayFactory:
    def create(self, conversation_id: str) -> AgentGateway:
        return OfflineAgentGateway()


class OfflineAgentGateway:
    @property
    def connection_state(self) -> ConnectionState:
        return "offline"

    async def start(self) -> None:
        return None

    async def create_or_resume_conversation(self, stored_session_id: str | None):
        raise ConnectionError("Agent gateway is not configured")

    async def detach_conversation(self) -> None:
        return None

    async def submit_turn(self, text: str, context: AgentContext) -> None:
        raise ConnectionError("Agent gateway is not configured")

    async def stream_events(self):
        if False:
            yield GatewayEvent("status", "offline", "Agent offline")

    async def interrupt_turn(self, turn_id: str) -> None:
        return None

    async def respond_to_review(
        self, turn_id: str, approval_id: str, *, approved: bool
    ) -> None:
        raise ConnectionError("Agent gateway is not configured")

    async def recover_active_turn(self, stored_session_id: str, turn_id: str) -> None:
        raise ConnectionError("Agent gateway is not configured")

    async def close(self) -> None:
        return None

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

ConnectionState = Literal["online", "connecting", "offline"]


@dataclass(frozen=True)
class AgentContext:
    turn_id: str
    selected_job_id: str | None
    workspace: dict[str, object]
    conversation_id: str
    selected_job: dict[str, str] | None = None


@dataclass(frozen=True)
class GatewayEvent:
    event_type: str
    state: str
    summary: str
    detail: dict[str, object] = field(default_factory=dict)
    turn_id: str | None = None
    source_event_id: str | None = None
    activity_id: str | None = None


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

    async def recover_active_turn(self, stored_session_id: str, turn_id: str) -> None: ...

    async def close(self) -> None: ...


class AgentGatewayFactory(Protocol):
    """Creates one isolated gateway connection per active JobOS conversation."""

    def create(self, conversation_id: str) -> AgentGateway: ...


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

    async def recover_active_turn(self, stored_session_id: str, turn_id: str) -> None:
        raise ConnectionError("Agent gateway is not configured")

    async def close(self) -> None:
        return None

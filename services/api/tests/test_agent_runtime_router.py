import asyncio
from collections.abc import AsyncIterator

import pytest
from jobos_api.agent_gateway import (
    AgentContext,
    AgentRuntimeRouter,
    GatewayEvent,
)

PROFILE_ID = "jprof_11111111111111111111111111111111"
AGENT_ID = "jagent_22222222222222222222222222222222"


def sealed_binding(*, provider: str = "hermes") -> dict[str, object]:
    return {
        "connected_agent_id": AGENT_ID,
        "provider": provider,
        "model_id": "(FAKE)-model-stable",
        "reasoning_effort": "medium",
        "binding_state": "sealed",
        "provider_session_id": None,
        "connection_account_fingerprint": None,
        "creation_state": "ready",
        "lock_reason": None,
    }


def trusted_context(conversation_id: str, turn_id: str) -> AgentContext:
    return AgentContext(
        turn_id=turn_id,
        selected_job_id=None,
        workspace={},
        conversation_id=conversation_id,
        profile_id=PROFILE_ID,
        connected_agent_id=AGENT_ID,
        provider="hermes",
        model_id="(FAKE)-model-stable",
        reasoning_effort="medium",
        permission_state={"scope": "global"},
    )


class QueueGateway:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.events: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self.submissions: list[tuple[str, AgentContext]] = []
        self.interruptions: list[str] = []
        self.detaches = 0
        self.closed = False

    @property
    def connection_state(self):
        return "online"

    async def start(self) -> None:
        return None

    async def create_or_resume_conversation(self, stored_session_id: str | None):
        return stored_session_id or self.session_id, f"live-{self.session_id}"

    async def detach_conversation(self) -> None:
        self.detaches += 1

    async def submit_turn(self, text: str, context: AgentContext) -> None:
        self.submissions.append((text, context))

    async def stream_events(self) -> AsyncIterator[GatewayEvent]:
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    async def interrupt_turn(self, turn_id: str) -> None:
        self.interruptions.append(turn_id)

    async def recover_active_turn(self, stored_session_id: str, turn_id: str) -> None:
        self.interruptions.append(turn_id)

    async def close(self) -> None:
        self.closed = True
        await self.events.put(None)


class QueueFactory:
    def __init__(self, *, shared_session: str | None = None) -> None:
        self.shared_session = shared_session
        self.gateways: dict[str, QueueGateway] = {}

    def create(self, conversation_id: str) -> QueueGateway:
        gateway = QueueGateway(self.shared_session or f"session-{conversation_id}")
        self.gateways[conversation_id] = gateway
        return gateway


def test_router_fails_closed_for_unregistered_provider_and_incomplete_binding():
    router = AgentRuntimeRouter({("hermes", AGENT_ID): QueueFactory()}, profile_id=PROFILE_ID)

    with pytest.raises(ConnectionError, match="provider is unavailable"):
        router.create("conv_codex", sealed_binding(provider="codex"))
    with pytest.raises(ValueError, match="incomplete"):
        router.create(
            "conv_incomplete",
            {**sealed_binding(), "model_id": None},
        )


def test_router_uses_provider_factory_for_agent_created_after_startup():
    factory = QueueFactory()
    router = AgentRuntimeRouter({"codex": factory}, profile_id=PROFILE_ID)

    routed = router.create("conv_new_codex", sealed_binding(provider="codex"))

    assert routed is not None
    assert "conv_new_codex" in factory.gateways


def test_router_rejects_cross_chat_session_reuse_and_wrong_turn_envelope():
    async def scenario() -> None:
        factory = QueueFactory(shared_session="(FAKE)-shared-session")
        router = AgentRuntimeRouter({("hermes", AGENT_ID): factory}, profile_id=PROFILE_ID)
        first = router.create("conv_first", sealed_binding())
        second = router.create("conv_second", sealed_binding())
        await first.start()
        await second.start()
        await first.create_or_resume_conversation(None)
        with pytest.raises(RuntimeError, match="already owned"):
            await second.create_or_resume_conversation(None)
        assert factory.gateways["conv_second"].detaches == 1
        with pytest.raises(ValueError, match="scope"):
            await first.submit_turn("wrong chat", trusted_context("conv_second", "turn_wrong_1234"))
        with pytest.raises(ValueError, match="binding"):
            wrong = trusted_context("conv_first", "turn_wrong_5678")
            object.__setattr__(wrong, "model_id", "(FAKE)-other-model")
            await first.submit_turn("wrong binding", wrong)
        await first.close()
        await second.close()

    asyncio.run(scenario())


def test_router_normalizes_orders_deduplicates_and_seals_terminal_outcome():
    async def scenario() -> None:
        factory = QueueFactory()
        router = AgentRuntimeRouter({("hermes", AGENT_ID): factory}, profile_id=PROFILE_ID)
        routed = router.create("conv_events", sealed_binding())
        await routed.start()
        await routed.create_or_resume_conversation(None)
        context = trusted_context("conv_events", "turn_events_1234")
        await routed.submit_turn("safe", context)
        provider = factory.gateways["conv_events"]
        duplicate = GatewayEvent(
            event_type="activity",
            state="working",
            summary="(FAKE) tool running",
            turn_id=context.turn_id,
            source_event_id="(FAKE)-source-tool",
            activity_id="(FAKE)-activity",
        )
        await provider.events.put(duplicate)
        await provider.events.put(duplicate)
        await provider.events.put(
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="(FAKE) done",
                turn_id=context.turn_id,
                source_event_id="(FAKE)-source-terminal",
            )
        )
        await provider.events.put(
            GatewayEvent(
                event_type="error",
                state="failed",
                summary="(FAKE) late",
                turn_id=context.turn_id,
                source_event_id="(FAKE)-source-late",
            )
        )

        stream = routed.stream_events()
        observed = [await asyncio.wait_for(anext(stream), 1) for _ in range(3)]
        assert [event.normalized_kind for event in observed] == [
            "turn_started",
            "tool_progress",
            "turn_completed",
        ]
        assert [event.sequence for event in observed] == [1, 2, 3]
        assert all(event.profile_id == PROFILE_ID for event in observed)
        assert all(event.conversation_id == "conv_events" for event in observed)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(stream), 0.05)
        await routed.close()

    asyncio.run(scenario())


def test_router_resumes_durable_turn_sequence_and_normalizes_reconciliation():
    async def scenario() -> None:
        factory = QueueFactory()
        router = AgentRuntimeRouter({("hermes", AGENT_ID): factory}, profile_id=PROFILE_ID)
        binding = {
            **sealed_binding(),
            "_normalized_event_sequences": {"turn_restart_1234": 2},
        }
        routed = router.create("conv_restart", binding)
        await routed.start()
        await routed.create_or_resume_conversation("stored-conv-restart")
        context = trusted_context("conv_restart", "turn_restart_1234")
        await routed.submit_turn("resume safely", context)
        await factory.gateways["conv_restart"].events.put(
            GatewayEvent(
                event_type="reconciliation",
                state="idle",
                summary="",
                detail={"stored_session_id": "stored-conv-restart"},
            )
        )

        stream = routed.stream_events()
        started = await asyncio.wait_for(anext(stream), 1)
        reconciled = await asyncio.wait_for(anext(stream), 1)

        assert started.sequence == 3
        assert reconciled.sequence == 4
        assert reconciled.normalized_kind == "connection_changed"
        assert reconciled.turn_id == context.turn_id
        await routed.close()

    asyncio.run(scenario())


def test_cancellation_is_scoped_to_the_current_chat_and_turn():
    async def scenario() -> None:
        factory = QueueFactory()
        router = AgentRuntimeRouter({("hermes", AGENT_ID): factory}, profile_id=PROFILE_ID)
        first = router.create("conv_cancel_a", sealed_binding())
        second = router.create("conv_cancel_b", sealed_binding())
        await first.start()
        await second.start()
        await first.create_or_resume_conversation(None)
        await second.create_or_resume_conversation(None)
        first_context = trusted_context("conv_cancel_a", "turn_cancel_a_1234")
        second_context = trusted_context("conv_cancel_b", "turn_cancel_b_1234")
        await first.submit_turn("first", first_context)
        await second.submit_turn("second", second_context)

        await first.interrupt_turn(second_context.turn_id)
        await first.interrupt_turn(first_context.turn_id)

        assert factory.gateways["conv_cancel_a"].interruptions == [first_context.turn_id]
        assert factory.gateways["conv_cancel_b"].interruptions == []
        await first.submit_turn(
            "next",
            trusted_context("conv_cancel_a", "turn_cancel_a_next_1234"),
        )
        await first.close()
        await second.close()

    asyncio.run(scenario())


def test_router_preserves_stock_hermes_continuation_ownership_after_foreground_terminal():
    async def scenario() -> None:
        factory = QueueFactory()
        router = AgentRuntimeRouter({("hermes", AGENT_ID): factory}, profile_id=PROFILE_ID)
        routed = router.create("conv_stock_continuation", sealed_binding())
        await routed.start()
        await routed.create_or_resume_conversation(None)
        provider = factory.gateways["conv_stock_continuation"]
        foreground = trusted_context("conv_stock_continuation", "turn_foreground_1234")
        await routed.submit_turn("foreground", foreground)
        stream = routed.stream_events()
        await asyncio.wait_for(anext(stream), 1)

        continuation_turn_id = "turn_cont_0123456789abcdef0123456789abcdef"
        continuation_detail = {
            "agent_continuation": True,
            "continuation_id": "deleg_stock_1234",
        }
        await provider.events.put(
            GatewayEvent(
                event_type="status",
                state="working",
                summary="Agent continuing completed background work",
                detail=continuation_detail,
                turn_id=continuation_turn_id,
            )
        )
        marker = await asyncio.wait_for(anext(stream), 1)
        assert marker.turn_id == continuation_turn_id

        await provider.events.put(
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="Foreground complete",
                detail={"text": "Foreground complete"},
                turn_id=foreground.turn_id,
            )
        )
        completed = await asyncio.wait_for(anext(stream), 1)
        assert completed.turn_id == foreground.turn_id

        with pytest.raises(RuntimeError, match="background continuation"):
            await routed.submit_turn(
                "must wait",
                trusted_context("conv_stock_continuation", "turn_user_race_1234"),
            )

        await provider.events.put(
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="Continuation complete",
                detail={**continuation_detail, "text": "Continuation complete"},
                turn_id=continuation_turn_id,
            )
        )
        continuation_completed = await asyncio.wait_for(anext(stream), 1)
        assert continuation_completed.turn_id == continuation_turn_id

        next_context = trusted_context("conv_stock_continuation", "turn_after_cont_1234")
        await routed.submit_turn("now continue", next_context)
        assert provider.submissions[-1] == ("now continue", next_context)
        await routed.close()

    asyncio.run(scenario())

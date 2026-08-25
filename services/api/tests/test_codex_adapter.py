from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from jobos_api.agent_gateway import AgentContext
from jobos_api.codex_adapter import CodexGatewayFactory
from jobos_api.codex_runtime import CodexRuntimeError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeCodexClient:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.requests: list[tuple[str, object | None]] = []
        self.subscribers: list[Callable[[str, object], Awaitable[None]]] = []
        self.started = False
        self.turns: list[dict[str, object]] = []

    @property
    def is_running(self) -> bool:
        return self.started

    async def start(self) -> None:
        self.started = True

    async def request(self, method: str, params: object | None = None) -> object:
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-a", "turns": []}}
        if method == "thread/resume":
            assert isinstance(params, dict)
            return {"thread": {"id": params["threadId"], "turns": self.turns}}
        if method == "mcpServerStatus/list":
            resources = [{"name": "capabilities", "uri": "jobos://capability-map"}]
            tools = {"document_publish": {"name": "document_publish", "inputSchema": {}}}
            if not self.ready:
                resources = []
            return {
                "data": [
                    {
                        "name": "jobos",
                        "authStatus": "unsupported",
                        "resources": resources,
                        "resourceTemplates": [],
                        "tools": tools,
                    }
                ]
            }
        if method == "turn/start":
            return {"turn": {"id": "provider-turn-a", "status": "inProgress"}}
        if method == "thread/read":
            assert isinstance(params, dict)
            return {"thread": {"id": params["threadId"], "turns": self.turns}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    async def notify(self, method: str, params: object | None = None) -> None:
        return None

    async def close(self) -> None:
        return None

    def subscribe(self, callback: Callable[[str, object], Awaitable[None]]) -> None:
        self.subscribers.append(callback)

    async def emit(self, method: str, params: object) -> None:
        if method == "jobos/runtime/disconnected":
            self.started = False
        for callback in tuple(self.subscribers):
            await callback(method, params)


def context(conversation_id: str = "conv_alpha") -> AgentContext:
    return AgentContext(
        turn_id="turn_12345678",
        selected_job_id="(FAKE)-job-alpha",
        selected_job={"id": "(FAKE)-job-alpha", "title": "(FAKE) Role"},
        workspace={"active_document": "(FAKE)-resume"},
        career_profile={"summary": "(FAKE) profile"},
        career_profile_context={"schema_version": 1},
        conversation_id=conversation_id,
        profile_id="jprof_00000000000000000000000000000000",
        connected_agent_id="cagent_codex",
        provider="codex",
        model_id="gpt-5.6-codex",
        reasoning_effort="medium",
        permission_state={"scope": "global"},
    )


@pytest.mark.anyio
async def test_codex_turn_uses_opaque_thread_and_exact_jobos_context(tmp_path: Path) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(client, cwd=tmp_path).create("conv_alpha")

    assert gateway.connection_state == "offline"
    assert await gateway.create_or_resume_conversation(None) == ("thread-a", "thread-a")
    assert gateway.connection_state == "online"
    await gateway.submit_turn("Tailor this", context())

    methods = [method for method, _ in client.requests]
    assert methods == ["thread/start", "mcpServerStatus/list", "turn/start"]
    thread_params = client.requests[0][1]
    assert isinstance(thread_params, dict)
    assert thread_params["sandbox"] == "read-only"
    assert thread_params["cwd"] == str(tmp_path)
    turn_params = client.requests[-1][1]
    assert isinstance(turn_params, dict)
    assert turn_params["threadId"] == "thread-a"
    assert turn_params["clientUserMessageId"] == "turn_12345678"
    assert turn_params["model"] == "gpt-5.6-codex"
    assert turn_params["effort"] == "medium"
    assert turn_params["approvalPolicy"] == "never"
    assert turn_params["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    prompt = turn_params["input"][0]["text"]  # type: ignore[index]
    assert "conversation_id=\"conv_alpha\"" in prompt
    assert "turn_id=\"turn_12345678\"" in prompt
    assert "(FAKE)-job-alpha" in prompt
    assert "Tailor this" in prompt
    client.started = False
    assert gateway.connection_state == "offline"


@pytest.mark.anyio
async def test_codex_rejects_turn_when_canonical_jobos_mcp_is_not_ready(tmp_path: Path) -> None:
    client = FakeCodexClient(ready=False)
    gateway = CodexGatewayFactory(client, cwd=tmp_path).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)

    with pytest.raises(CodexRuntimeError, match="JobOS tools unavailable") as captured:
        await gateway.submit_turn("Do work", context())

    assert captured.value.code == "JOBOS_TOOLS_UNAVAILABLE"
    assert "turn/start" not in [method for method, _ in client.requests]


@pytest.mark.anyio
async def test_codex_events_are_isolated_lossless_and_terminal(tmp_path: Path) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(client, cwd=tmp_path).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()

    await client.emit(
        "item/agentMessage/delta",
        {
            "threadId": "wrong-thread",
            "turnId": "provider-turn-a",
            "itemId": "message-a",
            "delta": "wrong",
        },
    )
    await client.emit(
        "item/agentMessage/delta",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "itemId": "message-a",
            "delta": "Hello ",
        },
    )
    delta = await anext(events)
    assert delta.turn_id == "turn_12345678"
    assert delta.summary == "Hello "
    assert delta.source_event_id and delta.source_event_id.startswith("codex:")

    await client.emit(
        "item/agentMessage/delta",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "itemId": "message-a",
            "delta": "Hello ",
        },
    )
    repeated_delta = await anext(events)
    assert repeated_delta.summary == "Hello "
    assert repeated_delta.source_event_id != delta.source_event_id

    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": True,
            "error": {"message": "(FAKE) transient upstream error"},
        },
    )
    retrying = await anext(events)
    assert retrying.event_type == "activity"
    assert retrying.state == "working"
    assert retrying.detail == {"reason": "provider_retry"}

    await client.emit(
        "turn/completed",
        {
            "threadId": "thread-a",
            "turn": {"id": "provider-turn-a", "status": "completed", "items": []},
        },
    )
    terminal = await anext(events)
    assert terminal.event_type == "assistant_message"
    assert terminal.state == "completed"
    assert terminal.turn_id == "turn_12345678"


@pytest.mark.anyio
async def test_codex_interrupt_and_recovery_use_exact_provider_turn(tmp_path: Path) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(client, cwd=tmp_path).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    await gateway.interrupt_turn("turn_12345678")

    assert client.requests[-1] == (
        "turn/interrupt",
        {"threadId": "thread-a", "turnId": "provider-turn-a"},
    )

    client.turns = [{"id": "provider-turn-recovered", "status": "inProgress", "items": []}]
    recovered = CodexGatewayFactory(client, cwd=tmp_path).create("conv_alpha")
    await recovered.recover_active_turn("thread-a", "turn_87654321")
    assert [method for method, _params in client.requests[-3:]] == [
        "thread/resume",
        "thread/read",
        "turn/interrupt",
    ]
    assert client.requests[-1] == (
        "turn/interrupt",
        {"threadId": "thread-a", "turnId": "provider-turn-recovered"},
    )


@pytest.mark.anyio
async def test_codex_recovery_fails_closed_when_multiple_turns_are_active(tmp_path: Path) -> None:
    client = FakeCodexClient()
    client.turns = [
        {"id": "provider-turn-a", "status": "inProgress"},
        {"id": "provider-turn-b", "status": "inProgress"},
    ]
    gateway = CodexGatewayFactory(client, cwd=tmp_path).create("conv_alpha")

    with pytest.raises(CodexRuntimeError, match="ambiguous"):
        await gateway.recover_active_turn("thread-a", "turn_87654321")


@pytest.mark.anyio
async def test_codex_rejects_symlinked_workspace(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "workspace"
    alias.symlink_to(target, target_is_directory=True)
    gateway = CodexGatewayFactory(FakeCodexClient(), cwd=alias).create("conv_alpha")

    with pytest.raises(CodexRuntimeError, match="unsafe"):
        await gateway.create_or_resume_conversation(None)


@pytest.mark.anyio
async def test_codex_runtime_disconnect_settles_turn_and_resumes_exact_thread(
    tmp_path: Path,
) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(client, cwd=tmp_path).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()

    await client.emit("jobos/runtime/disconnected", {})
    terminal = await anext(events)
    assert terminal.event_type == "error"
    assert terminal.state == "failed"
    assert terminal.turn_id == "turn_12345678"
    assert terminal.detail == {
        "reason": "transport_lost",
        "recovery_required": True,
    }
    assert gateway.connection_state == "offline"

    await gateway.start()
    assert client.requests[-1][0] == "thread/resume"
    assert client.requests[-1][1]["threadId"] == "thread-a"  # type: ignore[index]
    assert gateway.connection_state == "online"


@pytest.mark.anyio
async def test_codex_redacts_credentials_split_across_stream_deltas(tmp_path: Path) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(client, cwd=tmp_path).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()

    await client.emit(
        "item/agentMessage/delta",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "itemId": "message-secret",
            "delta": "The secret is sk-proj-abcdefgh",
        },
    )
    prefix = await anext(events)
    assert prefix.summary == "The secret is "

    await client.emit(
        "item/agentMessage/delta",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "itemId": "message-secret",
            "delta": "ijklmnopqrstuvwx done tail",
        },
    )
    redacted = await anext(events)
    assert redacted.summary == "[redacted] done "
    assert "sk-proj" not in prefix.summary + redacted.summary

    await client.emit(
        "turn/completed",
        {
            "threadId": "thread-a",
            "turn": {"id": "provider-turn-a", "status": "completed"},
        },
    )
    tail = await anext(events)
    terminal = await anext(events)
    assert tail.summary == "tail"
    assert terminal.state == "completed"
    assert "sk-proj" not in tail.summary

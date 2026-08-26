from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import jobos_api.codex_adapter as codex_adapter_module
import pytest
from jobos_api.agent_gateway import AgentContext
from jobos_api.codex_adapter import CodexGatewayFactory
from jobos_api.codex_runtime import CodexRpcError, CodexRuntimeError


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
        self.rate_limits: dict[str, object] = {}
        self.interrupt_error = False
        self.complete_before_interrupt_response = False
        self.resume_error: CodexRpcError | None = None
        self.server_request_handler: Callable[[str, object], Awaitable[object]] | None = None

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
            if self.resume_error is not None:
                raise self.resume_error
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
        if method == "account/rateLimits/read":
            return {"rateLimits": self.rate_limits}
        if method == "turn/start":
            return {"turn": {"id": "provider-turn-a", "status": "inProgress"}}
        if method == "thread/read":
            assert isinstance(params, dict)
            return {"thread": {"id": params["threadId"], "turns": self.turns}}
        if method == "turn/interrupt":
            if self.complete_before_interrupt_response:
                assert isinstance(params, dict)
                await self.emit(
                    "turn/completed",
                    {
                        "threadId": params["threadId"],
                        "turn": {"id": params["turnId"], "status": "interrupted"},
                    },
                )
            if self.interrupt_error:
                raise CodexRuntimeError(
                    "AGENT_PROVIDER_UNAVAILABLE", "(FAKE) interrupt unavailable"
                )
            return {}
        raise AssertionError(method)

    async def notify(self, method: str, params: object | None = None) -> None:
        return None

    async def close(self) -> None:
        return None

    def subscribe(self, callback: Callable[[str, object], Awaitable[None]]) -> None:
        self.subscribers.append(callback)

    def set_server_request_handler(
        self, callback: Callable[[str, object], Awaitable[object]]
    ) -> None:
        self.server_request_handler = callback

    async def server_request(self, method: str, params: object) -> object:
        assert self.server_request_handler is not None
        return await self.server_request_handler(method, params)

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
    publication_root = tmp_path / "artifacts" / "publication-inbox"
    publication_root.parent.mkdir()
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path / "codex-workspace",
        publication_root=publication_root,
    ).create("conv_alpha")

    assert gateway.connection_state == "offline"
    assert await gateway.create_or_resume_conversation(None) == ("thread-a", "thread-a")
    thread_start = cast(
        dict[str, object],
        next(params for method, params in client.requests if method == "thread/start"),
    )
    assert thread_start["cwd"] == str(tmp_path / "codex-workspace")
    assert gateway.connection_state == "online"
    await gateway.submit_turn("Tailor this", context())

    methods = [method for method, _ in client.requests]
    assert methods == [
        "thread/start",
        "mcpServerStatus/list",
        "account/rateLimits/read",
        "turn/start",
    ]
    thread_params = client.requests[0][1]
    assert isinstance(thread_params, dict)
    assert thread_params["sandbox"] == "workspace-write"
    assert thread_params["cwd"] == str(tmp_path / "codex-workspace")
    turn_params = client.requests[-1][1]
    assert isinstance(turn_params, dict)
    assert turn_params["threadId"] == "thread-a"
    assert turn_params["clientUserMessageId"] == "turn_12345678"
    assert turn_params["model"] == "gpt-5.6-codex"
    assert turn_params["effort"] == "medium"
    assert turn_params["approvalPolicy"] == "never"
    assert turn_params["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [str(publication_root)],
        "networkAccess": False,
    }
    assert publication_root.is_dir()
    prompt = turn_params["input"][0]["text"]  # type: ignore[index]
    assert "conversation_id=\"conv_alpha\"" in prompt
    assert "turn_id=\"turn_12345678\"" in prompt
    assert "(FAKE)-job-alpha" in prompt
    assert "Tailor this" in prompt
    client.started = False
    assert gateway.connection_state == "offline"


@pytest.mark.anyio
async def test_codex_attachment_is_idempotent_for_the_current_live_thread(
    tmp_path: Path,
) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")

    stored_id, _ = await gateway.create_or_resume_conversation(None)
    assert await gateway.create_or_resume_conversation(stored_id) == (stored_id, stored_id)

    assert [method for method, _ in client.requests] == ["thread/start"]


@pytest.mark.anyio
async def test_codex_replaces_a_missing_pre_turn_rollout(tmp_path: Path) -> None:
    client = FakeCodexClient()
    client.resume_error = CodexRpcError(
        -32600, "no rollout found for thread id thread-missing"
    )
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")

    assert await gateway.create_or_resume_conversation("thread-missing") == (
        "thread-a",
        "thread-a",
    )
    assert [method for method, _ in client.requests] == [
        "thread/resume",
        "thread/start",
    ]


@pytest.mark.anyio
async def test_codex_does_not_replace_a_thread_for_unrelated_resume_errors(
    tmp_path: Path,
) -> None:
    client = FakeCodexClient()
    client.resume_error = CodexRpcError(-32600, "another request was rejected")
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")

    with pytest.raises(CodexRpcError, match="Codex App Server rejected"):
        await gateway.create_or_resume_conversation("thread-missing")

    assert [method for method, _ in client.requests] == ["thread/resume"]


@pytest.mark.anyio
async def test_codex_rejects_turn_when_canonical_jobos_mcp_is_not_ready(tmp_path: Path) -> None:
    client = FakeCodexClient(ready=False)
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)

    with pytest.raises(CodexRuntimeError, match="JobOS tools unavailable") as captured:
        await gateway.submit_turn("Do work", context())

    assert captured.value.code == "JOBOS_TOOLS_UNAVAILABLE"
    assert "turn/start" not in [method for method, _ in client.requests]


@pytest.mark.anyio
async def test_codex_events_are_isolated_lossless_and_terminal(tmp_path: Path) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
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
        "item/started",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "item": {"id": "user-message-a", "type": "userMessage", "text": "Do work"},
        },
    )
    await client.emit(
        "item/completed",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "item": {
                "id": "assistant-message-a",
                "type": "agentMessage",
                "text": "Hello Hello",
            },
        },
    )
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
    assert terminal.summary == "Hello Hello"
    assert terminal.detail == {"type": "message.complete", "text": "Hello Hello"}


@pytest.mark.anyio
async def test_codex_interrupt_and_recovery_use_exact_provider_turn(tmp_path: Path) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    await gateway.interrupt_turn("turn_12345678")

    assert client.requests[-1] == (
        "turn/interrupt",
        {"threadId": "thread-a", "turnId": "provider-turn-a"},
    )

    client.turns = [{"id": "provider-turn-recovered", "status": "inProgress", "items": []}]
    recovered = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
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
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")

    with pytest.raises(CodexRuntimeError, match="ambiguous"):
        await gateway.recover_active_turn("thread-a", "turn_87654321")


@pytest.mark.anyio
async def test_codex_rejects_symlinked_workspace(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "workspace"
    alias.symlink_to(target, target_is_directory=True)
    gateway = CodexGatewayFactory(
        FakeCodexClient(),
        cwd=alias,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")

    with pytest.raises(CodexRuntimeError, match="unsafe"):
        await gateway.create_or_resume_conversation(None)


@pytest.mark.anyio
async def test_codex_rejects_symlinked_publication_parent_before_start(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    alias = tmp_path / "artifact-alias"
    alias.symlink_to(artifact_root, target_is_directory=True)
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path / "workspace",
        publication_root=alias / "publication-inbox",
    ).create("conv_alpha")

    with pytest.raises(CodexRuntimeError, match="publication inbox is unsafe"):
        await gateway.create_or_resume_conversation(None)

    assert client.started is False


@pytest.mark.anyio
async def test_codex_rejects_publication_root_replacement_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_stat = codex_adapter_module.os.stat

    def replaced_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == "publication-inbox" and kwargs.get("dir_fd") is not None:
            return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(codex_adapter_module.os, "stat", replaced_stat)
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path / "workspace",
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")

    with pytest.raises(CodexRuntimeError, match="publication inbox is unsafe"):
        await gateway.create_or_resume_conversation(None)

    assert client.started is False


@pytest.mark.anyio
async def test_codex_runtime_disconnect_settles_turn_and_resumes_exact_thread(
    tmp_path: Path,
) -> None:
    client = FakeCodexClient()
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
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
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
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


@pytest.mark.anyio
async def test_codex_rate_limit_is_scoped_and_never_blindly_retried(tmp_path: Path) -> None:
    client = FakeCodexClient()
    client.rate_limits = {
        "primary": {"usedPercent": 100, "resetsAt": int(time.time()) + 60},
        "secondary": {"usedPercent": 10, "resetsAt": int(time.time()) + 600},
    }
    factory = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    )
    factory.create("conv_warm")
    await client.emit(
        "account/rateLimits/updated",
        {
            "rateLimits": {
                "primary": {"usedPercent": 100, "resetsAt": int(time.time()) + 60},
                "secondary": {"usedPercent": 10, "resetsAt": int(time.time()) + 600},
            }
        },
    )
    await client.emit(
        "account/rateLimits/updated",
        {"rateLimits": {"credits": {"hasCredits": True, "unlimited": False}}},
    )
    gateway = factory.create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    requests_before_error = list(client.requests)
    events = gateway.stream_events()

    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": True,
            "error": {
                "message": "(FAKE) usage exhausted",
                "codexErrorInfo": "usageLimitExceeded",
            },
        },
    )
    await client.emit(
        "error",
        {
            "threadId": "other-thread",
            "turnId": "other-provider-turn",
            "willRetry": False,
            "error": {
                "message": "(FAKE) unrelated usage exhausted",
                "codexErrorInfo": "usageLimitExceeded",
            },
        },
    )

    limited = await anext(events)
    assert limited.event_type == "error"
    assert limited.state == "failed"
    assert limited.summary.startswith("Codex rate limit reached. Try again in ")
    assert limited.detail["reason"] == "rate_limited"
    assert limited.detail["retry"] is False
    retry_after = limited.detail["retry_after_seconds"]
    assert isinstance(retry_after, int)
    assert 1 <= retry_after <= 60
    assert client.requests == [
        *requests_before_error,
        (
            "turn/interrupt",
            {"threadId": "thread-a", "turnId": "provider-turn-a"},
        )
    ]
    assert gateway.connection_state == "online"


@pytest.mark.anyio
async def test_codex_rate_limit_refresh_preserves_reset_through_sparse_update(
    tmp_path: Path,
) -> None:
    client = FakeCodexClient()
    client.rate_limits = {
        "primary": {"usedPercent": 100, "resetsAt": int(time.time()) + 60}
    }
    factory = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    )
    gateway = factory.create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    await client.emit(
        "account/rateLimits/updated",
        {"rateLimits": {"credits": {"hasCredits": False, "unlimited": False}}},
    )
    events = gateway.stream_events()
    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": False,
            "error": {"codexErrorInfo": "usageLimitExceeded"},
        },
    )

    limited = await anext(events)
    assert limited.summary.startswith("Codex rate limit reached. Try again in ")
    assert limited.detail["retry"] is False
    retry_after = limited.detail["retry_after_seconds"]
    assert isinstance(retry_after, int)
    assert 1 <= retry_after <= 60


@pytest.mark.anyio
async def test_codex_expired_rate_limit_snapshot_omits_stale_countdown(tmp_path: Path) -> None:
    client = FakeCodexClient()
    factory = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    )
    gateway = factory.create("conv_alpha")
    await client.emit(
        "account/rateLimits/updated",
        {
            "rateLimits": {
                "primary": {"usedPercent": 100, "resetsAt": int(time.time()) - 1}
            }
        },
    )
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()
    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": False,
            "error": {"codexErrorInfo": "usageLimitExceeded"},
        },
    )

    limited = await anext(events)
    assert limited.summary == "Codex rate limit reached"
    assert limited.detail["retry_after_seconds"] is None


@pytest.mark.anyio
async def test_codex_full_rate_limit_refresh_replaces_stale_windows(tmp_path: Path) -> None:
    client = FakeCodexClient()
    factory = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    )
    gateway = factory.create("conv_alpha")
    await client.emit(
        "account/rateLimits/updated",
        {
            "rateLimits": {
                "primary": {"usedPercent": 100, "resetsAt": int(time.time()) + 60}
            }
        },
    )
    client.rate_limits = {"primary": None, "credits": {"hasCredits": True}}
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()
    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": False,
            "error": {"codexErrorInfo": "usageLimitExceeded"},
        },
    )

    limited = await anext(events)
    assert limited.summary == "Codex rate limit reached"
    assert limited.detail["retry_after_seconds"] is None


@pytest.mark.anyio
async def test_codex_rate_limit_interrupt_failure_requires_recovery(tmp_path: Path) -> None:
    client = FakeCodexClient()
    client.interrupt_error = True
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()
    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": True,
            "error": {"codexErrorInfo": "usageLimitExceeded"},
        },
    )

    limited = await anext(events)
    assert limited.summary == "Codex rate limit reached; recovery required"
    assert limited.detail == {
        "reason": "transport_lost",
        "cause": "rate_limited",
        "retry": False,
        "recovery_required": True,
    }


@pytest.mark.anyio
async def test_codex_rate_limit_refresh_keeps_update_delivered_after_read_response(
    tmp_path: Path,
) -> None:
    class RacingClient(FakeCodexClient):
        async def request(self, method: str, params: object | None = None) -> object:
            if method == "account/rateLimits/read":
                self.requests.append((method, params))
                await self.emit(
                    "account/rateLimits/updated",
                    {
                        "rateLimits": {
                            "primary": {
                                "usedPercent": 100,
                                "resetsAt": int(time.time()) + 120,
                            }
                        }
                    },
                )
                return {
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 100,
                            "resetsAt": int(time.time()) + 60,
                        }
                    }
                }
            return await super().request(method, params)

    client = RacingClient()
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()
    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": False,
            "error": {"codexErrorInfo": "usageLimitExceeded"},
        },
    )

    limited = await anext(events)
    retry_after = limited.detail["retry_after_seconds"]
    assert isinstance(retry_after, int)
    assert 61 <= retry_after <= 120


@pytest.mark.anyio
async def test_codex_rate_limit_survives_completion_before_interrupt_response(
    tmp_path: Path,
) -> None:
    client = FakeCodexClient()
    client.complete_before_interrupt_response = True
    client.rate_limits = {
        "primary": {"usedPercent": 100, "resetsAt": int(time.time()) + 60}
    }
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()
    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": True,
            "error": {"codexErrorInfo": "usageLimitExceeded"},
        },
    )

    limited = await anext(events)
    assert limited.detail["reason"] == "rate_limited"
    assert limited.detail["retry"] is False


@pytest.mark.anyio
async def test_codex_completed_rate_limit_does_not_require_recovery_on_interrupt_error(
    tmp_path: Path,
) -> None:
    client = FakeCodexClient()
    client.complete_before_interrupt_response = True
    client.interrupt_error = True
    gateway = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    ).create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Do work", context())
    events = gateway.stream_events()
    await client.emit(
        "error",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-a",
            "willRetry": True,
            "error": {"codexErrorInfo": "usageLimitExceeded"},
        },
    )

    limited = await anext(events)
    assert limited.detail["reason"] == "rate_limited"
    assert "recovery_required" not in limited.detail


@pytest.mark.anyio
async def test_codex_tool_review_is_scoped_and_requires_explicit_response(
    tmp_path: Path,
) -> None:
    client = FakeCodexClient()
    factory = CodexGatewayFactory(
        client,
        cwd=tmp_path,
        publication_root=tmp_path / "publication-inbox",
    )
    gateway = factory.create("conv_alpha")
    await gateway.create_or_resume_conversation(None)
    await gateway.submit_turn("Inspect the job", context())

    request = asyncio.create_task(
        client.server_request(
            "mcpServer/elicitation/request",
            {
                "threadId": "thread-a",
                "turnId": "provider-turn-a",
                "serverName": "jobos",
                "mode": "form",
                "message": 'Allow the jobos MCP server to run tool "job_inspect"?',
                "requestedSchema": {"type": "object", "properties": {}},
            },
        )
    )
    review = await anext(gateway.stream_events())
    approval_id = review.detail["approval_id"]
    assert review.state == "waiting"
    assert review.detail["tool_name"] == "job_inspect"
    assert isinstance(approval_id, str)

    with pytest.raises(ValueError, match="no longer pending"):
        await gateway.respond_to_review(
            "turn_wrong", approval_id, approved=True
        )
    await gateway.respond_to_review(
        "turn_12345678", approval_id, approved=True
    )
    assert await request == {"action": "accept", "content": {}}

    assert await client.server_request(
        "mcpServer/elicitation/request",
        {
            "threadId": "thread-a",
            "turnId": "provider-turn-stale",
            "serverName": "jobos",
            "mode": "form",
            "message": 'Allow the jobos MCP server to run tool "job_inspect"?',
            "requestedSchema": {"type": "object", "properties": {}},
        },
    ) == {"action": "decline", "content": {}}

    assert await client.server_request(
        "mcpServer/elicitation/request",
        {
            "threadId": "thread-other",
            "serverName": "jobos",
            "mode": "form",
            "message": 'Allow the jobos MCP server to run tool "job_inspect"?',
            "requestedSchema": {"type": "object", "properties": {}},
        },
    ) == {"action": "decline", "content": {}}

    for unsupported_schema in (
        {"type": "object", "properties": {}, "additionalProperties": True},
        {"type": "object", "properties": {}, "patternProperties": {}},
        {"type": "object", "properties": {}, "$ref": "#/$defs/review"},
        {"type": "object", "properties": {}, "oneOf": []},
    ):
        assert await client.server_request(
            "mcpServer/elicitation/request",
            {
                "threadId": "thread-a",
                "turnId": "provider-turn-a",
                "serverName": "jobos",
                "mode": "form",
                "message": 'Allow the jobos MCP server to run tool "job_inspect"?',
                "requestedSchema": unsupported_schema,
            },
        ) == {"action": "decline", "content": {}}

    disconnect_request = asyncio.create_task(
        client.server_request(
            "mcpServer/elicitation/request",
            {
                "threadId": "thread-a",
                "turnId": "provider-turn-a",
                "serverName": "jobos",
                "mode": "form",
                "message": 'Allow the jobos MCP server to run tool "job_inspect"?',
                "requestedSchema": {"type": "object", "properties": {}},
            },
        )
    )
    disconnect_review = await anext(gateway.stream_events())
    assert disconnect_review.state == "waiting"
    await gateway.handle_runtime_disconnect()
    assert await disconnect_request == {"action": "decline", "content": {}}

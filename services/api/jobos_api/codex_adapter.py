from __future__ import annotations

import asyncio
import math
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from .agent_gateway import AgentContext, ConnectionState, GatewayEvent
from .codex_runtime import (
    CodexRpcClient,
    CodexRpcError,
    CodexRuntimeError,
    jobos_mcp_ready,
)
from .hermes_adapter import _prompt_with_context
from .redaction import redact_detail, sanitize_assistant_text, sanitize_text

_TERMINAL_STATUSES = {"completed", "failed", "interrupted"}
_STREAM_TOKEN_TAIL = re.compile(r"[A-Za-z0-9_+=.\-]+$")
_JOBOS_TOOL_REVIEW = re.compile(
    r'^Allow the jobos MCP server to run tool "([A-Za-z][A-Za-z0-9_]{0,127})"\?$'
)
_TOOL_REVIEW_TIMEOUT_SECONDS = 300.0


class CodexGatewayFactory:
    """Create isolated JobOS conversation gateways over one private app-server."""

    def __init__(self, client: CodexRpcClient, *, cwd: Path) -> None:
        self._client = client
        self._cwd = cwd.expanduser().absolute()
        self._gateways: dict[str, CodexAppServerGateway] = {}
        self._rate_limit_snapshot: dict[str, object] = {}
        self._rate_limit_updates: list[dict[str, object]] = []
        self._rate_limit_refresh_lock = asyncio.Lock()
        self._subscribed = False
        self._client.set_server_request_handler(self._handle_server_request)

    def create(self, conversation_id: str) -> CodexAppServerGateway:
        if not self._subscribed:
            self._client.subscribe(self._dispatch)
            self._subscribed = True
        gateway = CodexAppServerGateway(
            client=self._client,
            cwd=self._cwd,
            conversation_id=conversation_id,
            unregister=self._unregister,
            rate_limit_snapshot=self._rate_limit_snapshot,
            refresh_rate_limits=self._refresh_rate_limits,
        )
        self._gateways[conversation_id] = gateway
        return gateway

    async def _refresh_rate_limits(self) -> dict[str, object]:
        async with self._rate_limit_refresh_lock:
            self._rate_limit_updates = []
            try:
                result = await self._client.request("account/rateLimits/read")
            except CodexRuntimeError:
                return dict(self._rate_limit_snapshot)
            snapshot = result.get("rateLimits") if isinstance(result, dict) else None
            if isinstance(snapshot, dict):
                refreshed = dict(snapshot)
                for update in self._rate_limit_updates:
                    refreshed = _merge_rate_limit_snapshot(refreshed, update)
                self._rate_limit_snapshot = refreshed
                self._rate_limit_updates = []
                for gateway in tuple(self._gateways.values()):
                    gateway.update_rate_limits(self._rate_limit_snapshot)
            return dict(self._rate_limit_snapshot)

    async def _dispatch(self, method: str, params: object) -> None:
        if method == "jobos/runtime/disconnected":
            for gateway in tuple(self._gateways.values()):
                await gateway.handle_runtime_disconnect()
            return
        if not isinstance(params, dict):
            return
        if method == "account/rateLimits/updated":
            snapshot = params.get("rateLimits")
            if not isinstance(snapshot, dict):
                return
            self._rate_limit_updates.append(snapshot)
            self._rate_limit_snapshot = _merge_rate_limit_snapshot(
                self._rate_limit_snapshot, snapshot
            )
            for gateway in tuple(self._gateways.values()):
                gateway.update_rate_limits(self._rate_limit_snapshot)
            return
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            thread = params.get("thread")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str):
            return
        for gateway in tuple(self._gateways.values()):
            if gateway.thread_id == thread_id:
                await gateway.handle_notification(method, params)

    async def _handle_server_request(self, method: str, params: object) -> object:
        if method != "mcpServer/elicitation/request" or not isinstance(params, dict):
            raise CodexRuntimeError(
                "AGENT_PROVIDER_UNAVAILABLE", "Unsupported Codex server request"
            )
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            return {"action": "decline", "content": {}}
        for gateway in tuple(self._gateways.values()):
            if gateway.thread_id == thread_id:
                return await gateway.handle_server_request(method, params)
        return {"action": "decline", "content": {}}

    def _unregister(self, conversation_id: str, gateway: CodexAppServerGateway) -> None:
        if self._gateways.get(conversation_id) is gateway:
            self._gateways.pop(conversation_id, None)


class CodexAppServerGateway:
    def __init__(
        self,
        *,
        client: CodexRpcClient,
        cwd: Path,
        conversation_id: str,
        unregister: Callable[[str, CodexAppServerGateway], None],
        rate_limit_snapshot: dict[str, object],
        refresh_rate_limits: Callable[[], Awaitable[dict[str, object]]],
    ) -> None:
        self._client = client
        self._cwd = cwd
        self._conversation_id = conversation_id
        self._unregister = unregister
        self._thread_id: str | None = None
        self._jobos_turn_id: str | None = None
        self._provider_turn_id: str | None = None
        self._event_namespace = secrets.token_hex(8)
        self._event_sequence = 0
        self._assistant_pending = ""
        self._rate_limit_snapshot = dict(rate_limit_snapshot)
        self._refresh_rate_limits = refresh_rate_limits
        self._rate_limit_settling: set[str] = set()
        self._rate_limit_completed: set[str] = set()
        self._rate_limit_tasks: set[asyncio.Task[None]] = set()
        self._events: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._pending_review_id: str | None = None
        self._pending_review_turn_id: str | None = None
        self._pending_review_future: asyncio.Future[bool] | None = None
        self._closed = False

    @property
    def connection_state(self) -> ConnectionState:
        return "online" if not self._closed and self._client.is_running else "offline"

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    async def start(self) -> None:
        if self._closed:
            raise ConnectionError("Codex gateway is closed")
        was_running = self._client.is_running
        await self._client.start()
        if self._cwd.parent.is_symlink() or self._cwd.is_symlink():
            raise CodexRuntimeError(
                "AGENT_PROVIDER_UNAVAILABLE", "Codex workspace is unsafe"
            )
        self._cwd.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._cwd.is_symlink() or not self._cwd.is_dir():
            raise CodexRuntimeError(
                "AGENT_PROVIDER_UNAVAILABLE", "Codex workspace is unsafe"
            )
        os.chmod(self._cwd, 0o700)
        if not was_running and self._thread_id is not None:
            result = await self._client.request(
                "thread/resume",
                {
                    "threadId": self._thread_id,
                    "cwd": str(self._cwd),
                    "approvalPolicy": "never",
                    "approvalsReviewer": "user",
                    "sandbox": "read-only",
                },
            )
            thread = result.get("thread") if isinstance(result, dict) else None
            resumed_id = thread.get("id") if isinstance(thread, dict) else None
            if resumed_id != self._thread_id:
                raise CodexRuntimeError(
                    "AGENT_PROVIDER_UNAVAILABLE", "Codex resumed the wrong conversation"
                )

    async def create_or_resume_conversation(
        self, stored_session_id: str | None
    ) -> tuple[str, str]:
        await self.start()
        if stored_session_id is not None and stored_session_id == self._thread_id:
            return stored_session_id, stored_session_id
        params: dict[str, object] = {
            "cwd": str(self._cwd),
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "sandbox": "read-only",
        }
        if stored_session_id is None:
            method = "thread/start"
        else:
            method = "thread/resume"
            params["threadId"] = stored_session_id
        replaced_missing_rollout = False
        try:
            result = await self._client.request(method, params)
        except CodexRpcError as error:
            missing_rollout_message = (
                f"no rollout found for thread id {stored_session_id}"
            )
            if (
                stored_session_id is None
                or error.rpc_code != -32600
                or error.safe_message != missing_rollout_message
            ):
                raise
            replaced_missing_rollout = True
            params.pop("threadId", None)
            result = await self._client.request("thread/start", params)
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexRuntimeError(
                "AGENT_PROVIDER_UNAVAILABLE", "Codex conversation lifecycle failed"
            )
        if (
            stored_session_id is not None
            and not replaced_missing_rollout
            and thread_id != stored_session_id
        ):
            raise CodexRuntimeError(
                "AGENT_PROVIDER_UNAVAILABLE", "Codex resumed the wrong conversation"
            )
        self._thread_id = thread_id
        return thread_id, thread_id

    async def detach_conversation(self) -> None:
        self._resolve_pending_review(False)
        self._thread_id = None
        self._jobos_turn_id = None
        self._provider_turn_id = None
        self._assistant_pending = ""

    async def _require_jobos_mcp(self) -> None:
        if self._thread_id is None:
            raise RuntimeError("Codex conversation is not attached")
        result = await self._client.request(
            "mcpServerStatus/list",
            {"threadId": self._thread_id, "detail": "full"},
        )
        if not jobos_mcp_ready(result):
            raise CodexRuntimeError("JOBOS_TOOLS_UNAVAILABLE", "JobOS tools unavailable")

    async def handle_runtime_disconnect(self) -> None:
        turn_id = self._jobos_turn_id
        provider_turn_id = self._provider_turn_id
        if turn_id is None or provider_turn_id is None:
            return
        self._resolve_pending_review(False)
        self._event_sequence += 1
        await self._events.put(
            GatewayEvent(
                "error",
                "failed",
                "Codex runtime disconnected",
                {"reason": "transport_lost", "recovery_required": True},
                turn_id,
                f"codex:{self._event_namespace}:{provider_turn_id}:{self._event_sequence}",
            )
        )
        self._jobos_turn_id = None
        self._provider_turn_id = None
        self._assistant_pending = ""

    async def submit_turn(self, text: str, context: AgentContext) -> None:
        if self._thread_id is None:
            raise RuntimeError("Codex conversation is not attached")
        await self._require_jobos_mcp()
        self._rate_limit_snapshot = await self._refresh_rate_limits()
        prompt = _prompt_with_context(
            text,
            {
                "selected_job_id": context.selected_job_id,
                "selected_job": context.selected_job,
                "workspace": context.workspace,
                "career_profile": context.career_profile,
                "career_profile_context": context.career_profile_context,
            },
            context.conversation_id,
            context.turn_id,
        )
        params: dict[str, object] = {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": prompt}],
            "clientUserMessageId": context.turn_id,
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }
        if context.model_id is not None:
            params["model"] = context.model_id
        if context.reasoning_effort is not None:
            params["effort"] = context.reasoning_effort
        result = await self._client.request("turn/start", params)
        turn = result.get("turn") if isinstance(result, dict) else None
        provider_turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(provider_turn_id, str) or not provider_turn_id:
            raise CodexRuntimeError("AGENT_PROVIDER_UNAVAILABLE", "Codex turn did not start")
        self._jobos_turn_id = context.turn_id
        self._provider_turn_id = provider_turn_id
        self._assistant_pending = ""

    def stream_events(self) -> AsyncIterator[GatewayEvent]:
        return self._stream_events()

    async def _stream_events(self) -> AsyncIterator[GatewayEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def interrupt_turn(self, turn_id: str) -> None:
        if (
            self._thread_id is None
            or self._jobos_turn_id != turn_id
            or self._provider_turn_id is None
        ):
            return
        self._resolve_pending_review(False)
        await self._client.request(
            "turn/interrupt",
            {"threadId": self._thread_id, "turnId": self._provider_turn_id},
        )

    async def handle_server_request(
        self, method: str, params: dict[str, object]
    ) -> object:
        message = params.get("message")
        requested_schema = params.get("requestedSchema")
        schema_is_empty = requested_schema in (
            {},
            {"type": "object", "properties": {}},
            {"type": "object", "properties": {}, "required": []},
        )
        match = _JOBOS_TOOL_REVIEW.fullmatch(message) if isinstance(message, str) else None
        if (
            method != "mcpServer/elicitation/request"
            or params.get("serverName") != "jobos"
            or params.get("mode") != "form"
            or params.get("turnId") != self._provider_turn_id
            or not schema_is_empty
            or match is None
            or self._jobos_turn_id is None
            or self._provider_turn_id is None
            or self._pending_review_future is not None
        ):
            return {"action": "decline", "content": {}}
        approval_id = f"approval_{secrets.token_urlsafe(18)}"
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending_review_id = approval_id
        self._pending_review_turn_id = self._jobos_turn_id
        self._pending_review_future = future
        self._event_sequence += 1
        await self._events.put(
            GatewayEvent(
                "status",
                "waiting",
                f'Allow JobOS tool “{match.group(1)}”?',
                {
                    "actionable": True,
                    "approval_id": approval_id,
                    "tool_name": match.group(1),
                },
                self._jobos_turn_id,
                f"codex:{self._event_namespace}:{self._provider_turn_id}:{self._event_sequence}",
            )
        )
        try:
            approved = await asyncio.wait_for(
                asyncio.shield(future), timeout=_TOOL_REVIEW_TIMEOUT_SECONDS
            )
        except TimeoutError:
            approved = False
        finally:
            if self._pending_review_future is future:
                self._pending_review_id = None
                self._pending_review_turn_id = None
                self._pending_review_future = None
        return {"action": "accept" if approved else "decline", "content": {}}

    async def respond_to_review(
        self, turn_id: str, approval_id: str, *, approved: bool
    ) -> None:
        if (
            self._pending_review_turn_id != turn_id
            or self._pending_review_id != approval_id
            or self._pending_review_future is None
            or self._pending_review_future.done()
        ):
            raise ValueError("Tool review is no longer pending")
        self._pending_review_future.set_result(approved)

    def _resolve_pending_review(self, approved: bool) -> None:
        future = self._pending_review_future
        if future is not None and not future.done():
            future.set_result(approved)

    async def recover_active_turn(self, stored_session_id: str, turn_id: str) -> None:
        await self.start()
        resumed = await self._client.request(
            "thread/resume",
            {
                "threadId": stored_session_id,
                "cwd": str(self._cwd),
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": "read-only",
            },
        )
        resumed_thread = resumed.get("thread") if isinstance(resumed, dict) else None
        if not isinstance(resumed_thread, dict) or resumed_thread.get("id") != stored_session_id:
            raise CodexRuntimeError("AGENT_PROVIDER_UNAVAILABLE", "Codex recovery failed")
        result = await self._client.request(
            "thread/read", {"threadId": stored_session_id, "includeTurns": True}
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict) or thread.get("id") != stored_session_id:
            raise CodexRuntimeError("AGENT_PROVIDER_UNAVAILABLE", "Codex recovery failed")
        turns = thread.get("turns")
        active = [
            item
            for item in turns if isinstance(item, dict) and item.get("status") == "inProgress"
        ] if isinstance(turns, list) else []
        if len(active) > 1:
            raise CodexRuntimeError("AGENT_PROVIDER_UNAVAILABLE", "Codex recovery is ambiguous")
        self._thread_id = stored_session_id
        if not active:
            return
        provider_turn_id = active[0].get("id")
        if not isinstance(provider_turn_id, str):
            raise CodexRuntimeError("AGENT_PROVIDER_UNAVAILABLE", "Codex recovery failed")
        self._jobos_turn_id = turn_id
        self._provider_turn_id = provider_turn_id
        await self._client.request(
            "turn/interrupt",
            {"threadId": stored_session_id, "turnId": provider_turn_id},
        )

    async def handle_notification(self, method: str, params: dict[str, object]) -> None:
        provider_turn_id = params.get("turnId")
        turn = params.get("turn")
        if not isinstance(provider_turn_id, str) and isinstance(turn, dict):
            provider_turn_id = turn.get("id")
        if (
            self._thread_id is None
            or params.get("threadId") != self._thread_id
            or self._jobos_turn_id is None
            or provider_turn_id != self._provider_turn_id
        ):
            return
        if (
            method == "turn/completed"
            and isinstance(provider_turn_id, str)
            and provider_turn_id in self._rate_limit_settling
        ):
            self._rate_limit_completed.add(provider_turn_id)
            return
        if method == "turn/completed" and self._assistant_pending:
            pending = sanitize_assistant_text(self._assistant_pending)
            self._assistant_pending = ""
            self._event_sequence += 1
            await self._events.put(
                GatewayEvent(
                    "assistant_message",
                    "streaming",
                    pending,
                    {"text": pending},
                    self._jobos_turn_id,
                    f"codex:{self._event_namespace}:{provider_turn_id}:{self._event_sequence}",
                )
            )
        if method == "error":
            error = params.get("error")
            error_info = error.get("codexErrorInfo") if isinstance(error, dict) else None
            if (
                error_info == "usageLimitExceeded"
                and params.get("willRetry") is True
                and isinstance(provider_turn_id, str)
            ):
                if provider_turn_id in self._rate_limit_settling:
                    return
                self._rate_limit_settling.add(provider_turn_id)
                task = asyncio.create_task(
                    self._settle_provider_rate_limit_retry(
                        self._thread_id,
                        provider_turn_id,
                        self._jobos_turn_id,
                        params,
                    )
                )
                self._rate_limit_tasks.add(task)
                task.add_done_callback(self._rate_limit_tasks.discard)
                return
        event = self._normalize(method, params)
        if event is not None:
            await self._events.put(event)
        if method == "turn/completed":
            self._resolve_pending_review(False)
            self._jobos_turn_id = None
            self._provider_turn_id = None
            self._assistant_pending = ""

    async def _settle_provider_rate_limit_retry(
        self,
        thread_id: str,
        provider_turn_id: str,
        jobos_turn_id: str,
        params: dict[str, object],
    ) -> None:
        try:
            await self._client.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": provider_turn_id},
            )
        except CodexRuntimeError:
            if provider_turn_id in self._rate_limit_completed:
                event = self._normalize("error", params)
                if event is not None:
                    await self._events.put(event)
                self._clear_rate_limited_turn(jobos_turn_id, provider_turn_id)
                self._rate_limit_completed.discard(provider_turn_id)
                self._rate_limit_settling.discard(provider_turn_id)
                return
            if (
                self._jobos_turn_id != jobos_turn_id
                or self._provider_turn_id != provider_turn_id
            ):
                self._rate_limit_settling.discard(provider_turn_id)
                return
            self._event_sequence += 1
            await self._events.put(
                GatewayEvent(
                    "error",
                    "failed",
                    "Codex rate limit reached; recovery required",
                    {
                        "reason": "transport_lost",
                        "cause": "rate_limited",
                        "retry": False,
                        "recovery_required": True,
                    },
                    jobos_turn_id,
                    f"codex:{self._event_namespace}:{provider_turn_id}:{self._event_sequence}",
                )
            )
            self._clear_rate_limited_turn(jobos_turn_id, provider_turn_id)
            self._rate_limit_settling.discard(provider_turn_id)
            return
        if (
            self._jobos_turn_id != jobos_turn_id
            or self._provider_turn_id != provider_turn_id
        ):
            self._rate_limit_completed.discard(provider_turn_id)
            self._rate_limit_settling.discard(provider_turn_id)
            return
        event = self._normalize("error", params)
        if event is not None:
            await self._events.put(event)
        self._clear_rate_limited_turn(jobos_turn_id, provider_turn_id)
        self._rate_limit_completed.discard(provider_turn_id)
        self._rate_limit_settling.discard(provider_turn_id)

    def _clear_rate_limited_turn(
        self, jobos_turn_id: str, provider_turn_id: str
    ) -> None:
        if (
            self._jobos_turn_id == jobos_turn_id
            and self._provider_turn_id == provider_turn_id
        ):
            self._resolve_pending_review(False)
            self._jobos_turn_id = None
            self._provider_turn_id = None
            self._assistant_pending = ""

    def _normalize(self, method: str, params: dict[str, object]) -> GatewayEvent | None:
        turn_id = self._jobos_turn_id
        provider_turn_id = self._provider_turn_id
        if turn_id is None or provider_turn_id is None:
            return None
        self._event_sequence += 1
        source_id = (
            f"codex:{self._event_namespace}:{provider_turn_id}:{self._event_sequence}"
        )
        if method == "item/agentMessage/delta":
            combined = self._assistant_pending + str(params.get("delta") or "")
            trailing = _STREAM_TOKEN_TAIL.search(combined)
            if trailing is None:
                safe_prefix = combined
                self._assistant_pending = ""
            else:
                safe_prefix = combined[: trailing.start()]
                self._assistant_pending = trailing.group(0)
            if not safe_prefix:
                return None
            delta = sanitize_assistant_text(safe_prefix)
            return GatewayEvent(
                "assistant_message", "streaming", delta, {"text": delta}, turn_id, source_id
            )
        if method in {"item/started", "item/completed", "item/mcpToolCall/progress"}:
            item = params.get("item")
            detail = redact_detail(item if isinstance(item, dict) else params)
            state = "completed" if method == "item/completed" else "working"
            summary = sanitize_text(str(detail.get("name") or "Codex tool activity"))[:500]
            return GatewayEvent("activity", state, summary, detail, turn_id, source_id)
        if method == "error":
            error = params.get("error")
            error_info = error.get("codexErrorInfo") if isinstance(error, dict) else None
            if error_info == "usageLimitExceeded":
                retry_after = _retry_after_seconds(self._rate_limit_snapshot)
                summary = "Codex rate limit reached"
                if retry_after is not None:
                    summary = f"Codex rate limit reached. Try again in {retry_after} seconds"
                return GatewayEvent(
                    "error",
                    "failed",
                    summary,
                    {
                        "reason": "rate_limited",
                        "retry": False,
                        "retry_after_seconds": retry_after,
                    },
                    turn_id,
                    source_id,
                )
            if params.get("willRetry") is True:
                return GatewayEvent(
                    "activity",
                    "working",
                    "Codex is retrying the turn",
                    {"reason": "provider_retry"},
                    turn_id,
                    source_id,
                )
            return GatewayEvent(
                "error",
                "failed",
                "Codex turn failed",
                {"reason": "provider_error"},
                turn_id,
                source_id,
            )
        if method == "turn/completed":
            turn = params.get("turn")
            status = turn.get("status") if isinstance(turn, dict) else None
            if status not in _TERMINAL_STATUSES:
                return None
            state = "interrupted" if status == "interrupted" else status
            summary = "Codex turn completed" if state == "completed" else "Codex turn ended"
            return GatewayEvent("assistant_message", state, summary, {}, turn_id, source_id)
        return None

    async def close(self) -> None:
        self._resolve_pending_review(False)
        for task in tuple(self._rate_limit_tasks):
            task.cancel()
        if self._rate_limit_tasks:
            await asyncio.gather(*self._rate_limit_tasks, return_exceptions=True)
        self._rate_limit_tasks.clear()
        self._closed = True
        self._thread_id = None
        self._jobos_turn_id = None
        self._provider_turn_id = None
        self._unregister(self._conversation_id, self)
        await self._events.put(None)

    def update_rate_limits(self, snapshot: dict[str, object]) -> None:
        self._rate_limit_snapshot = dict(snapshot)


def _retry_after_seconds(snapshot: dict[str, object]) -> int | None:
    resets: list[int] = []
    for name in ("primary", "secondary"):
        window = snapshot.get(name)
        if not isinstance(window, dict):
            continue
        used_percent = window.get("usedPercent")
        if not isinstance(used_percent, int) or isinstance(used_percent, bool):
            continue
        if used_percent < 100:
            continue
        resets_at = window.get("resetsAt")
        if isinstance(resets_at, int) and not isinstance(resets_at, bool):
            resets.append(resets_at)
    now = time.time()
    future_resets = [reset for reset in resets if reset > now]
    if not future_resets:
        return None
    return math.ceil(max(future_resets) - now)


def _merge_rate_limit_snapshot(
    previous: dict[str, object], update: dict[str, object]
) -> dict[str, object]:
    merged = dict(previous)
    for key, value in update.items():
        if value is None:
            continue
        if key in {"primary", "secondary"} and isinstance(value, dict):
            prior_window = merged.get(key)
            window = dict(prior_window) if isinstance(prior_window, dict) else {}
            window.update({name: field for name, field in value.items() if field is not None})
            merged[key] = window
            continue
        merged[key] = value
    return merged

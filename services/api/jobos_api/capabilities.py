from __future__ import annotations

import asyncio
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .browser_policy import BROWSER_URL_LIMIT, safe_browser_url, sanitize_browser_title
from .redaction import sanitize_summary, sanitize_user_text

BrowserCommandName = Literal[
    "tabs.inspect",
    "tab.create",
    "tab.select",
    "tab.associate",
    "tab.close",
    "tabs.reorder",
    "tab.navigate",
    "tab.back",
    "tab.forward",
    "tab.reload",
    "tab.stop",
    "page.snapshot",
    "element.click",
    "element.type",
    "page.scroll",
]

COMMANDS = {
    "tabs.inspect",
    "tab.create",
    "tab.select",
    "tab.associate",
    "tab.close",
    "tabs.reorder",
    "tab.navigate",
    "tab.back",
    "tab.forward",
    "tab.reload",
    "tab.stop",
    "page.snapshot",
    "element.click",
    "element.type",
    "page.scroll",
}
TAB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TARGET_ID = re.compile(r"^t_[A-Za-z0-9_-]{1,64}$")


class BrowserCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: BrowserCommandName
    arguments: dict[str, Any] = Field(default_factory=dict)
    origin: Literal["user", "mcp"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    timeout_ms: int = Field(default=5_000, ge=100, le=10_000)

    @field_validator("command")
    @classmethod
    def supported_command(cls, value: str) -> str:
        if value not in COMMANDS:
            raise ValueError("Unsupported browser command")
        return value

    @field_validator("arguments")
    @classmethod
    def safe_arguments(cls, value: dict[str, Any], info) -> dict[str, Any]:
        # Command-dependent validation also runs at dispatch because Pydantic field
        # ordering is intentionally not relied on as a trust boundary.
        if len(value) > 4:
            raise ValueError("Invalid browser command arguments")
        return value

    def validated_arguments(self) -> dict[str, Any]:
        command = self.command
        args = self.arguments
        allowed: dict[str, set[str]] = {
            "tabs.inspect": set(),
            "tab.create": {"url", "associated_job_id"},
            "tab.select": {"tab_id"},
            "tab.associate": {"tab_id", "job_id"},
            "tab.close": {"tab_id"},
            "tabs.reorder": {"tab_ids"},
            "tab.navigate": {"tab_id", "url"},
            "tab.back": {"tab_id"},
            "tab.forward": {"tab_id"},
            "tab.reload": {"tab_id"},
            "tab.stop": {"tab_id"},
            "page.snapshot": {"tab_id"},
            "element.click": {"tab_id", "target_id"},
            "element.type": {"tab_id", "target_id", "text", "clear"},
            "page.scroll": {"tab_id", "direction", "amount"},
        }
        if set(args) - allowed[command]:
            raise ValueError("Invalid browser command arguments")
        if command not in {"tabs.inspect", "tab.create", "tabs.reorder"} and (
            not isinstance(args.get("tab_id"), str) or not TAB_ID.fullmatch(args["tab_id"])
        ):
            raise ValueError("Invalid browser command arguments")
        if command in {"element.click", "element.type"}:
            target = args.get("target_id")
            if not isinstance(target, str) or not TARGET_ID.fullmatch(target):
                raise ValueError("Invalid browser command arguments")
        if command == "element.type":
            text = args.get("text")
            if (
                not isinstance(text, str)
                or len(text) > 4_000
                or not isinstance(args.get("clear", True), bool)
            ):
                raise ValueError("Invalid browser command arguments")
        if command == "page.scroll":
            if args.get("direction") not in {"up", "down"}:
                raise ValueError("Invalid browser command arguments")
            amount = args.get("amount", 600)
            if not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= 2_000:
                raise ValueError("Invalid browser command arguments")
        if command == "tabs.reorder":
            ids = args.get("tab_ids")
            if (
                not isinstance(ids, list)
                or not ids
                or len(ids) > 50
                or len(set(ids)) != len(ids)
                or any(not isinstance(item, str) or not TAB_ID.fullmatch(item) for item in ids)
            ):
                raise ValueError("Invalid browser command arguments")
        if command in {"tab.create", "tab.navigate"} and "url" in args:
            url = args["url"]
            if (
                not isinstance(url, str)
                or len(url) > BROWSER_URL_LIMIT
                or not safe_browser_url(url, allow_blank=False)
            ):
                raise ValueError("Invalid browser command arguments")
        if command == "tab.navigate" and "url" not in args:
            raise ValueError("Invalid browser command arguments")
        if command == "tab.create" and "associated_job_id" in args:
            job_id = args["associated_job_id"]
            if job_id is not None and (
                not isinstance(job_id, str) or not job_id or len(job_id) > 512
            ):
                raise ValueError("Invalid browser command arguments")
        if command == "tab.associate":
            job_id = args.get("job_id")
            if not isinstance(job_id, str) or not job_id or len(job_id) > 512:
                raise ValueError("Invalid browser command arguments")
        return dict(args)


class BrowserCommandError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: Literal["desktop_unavailable", "tab_not_found", "timeout", "validation", "execution"]
    message: str = Field(max_length=300)


class BrowserCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    command_id: str
    state: Literal["completed", "failed"]
    outcome: str = Field(max_length=120)
    data: dict[str, Any] = Field(default_factory=dict)
    error: BrowserCommandError | None = None

    @model_validator(mode="after")
    def consistent_terminal_state(self):
        if (self.state == "completed") == (self.error is not None):
            raise ValueError("Browser command result state is inconsistent")
        return self


class DesktopCapabilityPresence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool
    device_id: str
    lease_remaining_ms: int = Field(ge=0, le=15_000)


class CapabilitySocket(Protocol):
    async def send_json(self, data: Any) -> None: ...
    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


@dataclass
class _Desktop:
    socket: CapabilitySocket
    device_id: str
    lease_at: float


class DesktopUnavailable(RuntimeError):
    pass


def sanitize_browser_result_data(
    value: Any, *, key: str = "", depth: int = 0
) -> Any:
    """Bound desktop results without stripping safe browser metadata needed by MCP."""
    if depth >= 5:
        return "[bounded]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if key == "url":
            return value if safe_browser_url(value, allow_blank=True) else "about:blank"
        safe = sanitize_user_text(value)
        if key == "title":
            safe = sanitize_browser_title(safe)
        limit = 5_000 if key in {"text", "page_text"} else 1_000
        return safe[:limit]
    if isinstance(value, dict):
        return {
            str(raw_key)[:100]: sanitize_browser_result_data(
                item, key=str(raw_key)[:100], depth=depth + 1
            )
            for raw_key, item in list(value.items())[:120]
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize_browser_result_data(item, key=key, depth=depth + 1)
            for item in list(value)[:100]
        ]
    return sanitize_user_text(str(value))[:1_000]


class CapabilityBroker:
    def __init__(self, *, lease_seconds: float = 15.0) -> None:
        self._lease_seconds = lease_seconds
        self._desktops: dict[str, _Desktop] = {}
        self._pending: dict[
            str, tuple[asyncio.Future[BrowserCommandResponse], CapabilitySocket]
        ] = {}
        self._lock = asyncio.Lock()

    async def register(self, socket: CapabilitySocket, device_id: str) -> bool:
        async with self._lock:
            existing = self._desktops.get(device_id)
            if existing and monotonic() - existing.lease_at <= self._lease_seconds:
                return False
            self._desktops[device_id] = _Desktop(socket, device_id, monotonic())
            return True

    async def presence(self, device_id: str) -> DesktopCapabilityPresence:
        async with self._lock:
            desktop = self._desktops.get(device_id)
            remaining = (
                max(0.0, self._lease_seconds - (monotonic() - desktop.lease_at))
                if desktop
                else 0.0
            )
            return DesktopCapabilityPresence(
                available=remaining > 0,
                device_id=device_id,
                lease_remaining_ms=min(15_000, int(remaining * 1_000)),
            )

    async def heartbeat(self, socket: CapabilitySocket) -> None:
        async with self._lock:
            for desktop in self._desktops.values():
                if desktop.socket is socket:
                    desktop.lease_at = monotonic()
                    return

    async def unregister(self, socket: CapabilitySocket) -> None:
        async with self._lock:
            device_id = next(
                (
                    device_id
                    for device_id, desktop in self._desktops.items()
                    if desktop.socket is socket
                ),
                None,
            )
            if device_id is None:
                return
            self._desktops.pop(device_id, None)
            pending_ids = [
                command_id
                for command_id, (_, pending_socket) in self._pending.items()
                if pending_socket is socket
            ]
            pending = [self._pending.pop(command_id)[0] for command_id in pending_ids]
        for future in pending:
            if not future.done():
                future.set_exception(DesktopUnavailable())

    async def execute(
        self, request: BrowserCommandRequest, *, device_id: str | None = None
    ) -> BrowserCommandResponse:
        arguments = request.validated_arguments()
        command_id = f"cmd_{secrets.token_urlsafe(18)}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BrowserCommandResponse] = loop.create_future()
        async with self._lock:
            now = monotonic()
            expired = [
                candidate_id
                for candidate_id, candidate in self._desktops.items()
                if now - candidate.lease_at > self._lease_seconds
            ]
            for candidate_id in expired:
                self._desktops.pop(candidate_id, None)
            if device_id is not None:
                desktop = self._desktops.get(device_id)
            elif len(self._desktops) == 1:
                desktop = next(iter(self._desktops.values()))
            else:
                desktop = None
            if desktop is None:
                raise DesktopUnavailable()
            self._pending[command_id] = (future, desktop.socket)
            deadline = datetime.now(UTC) + timedelta(milliseconds=request.timeout_ms)
            payload = {
                "type": "command",
                "command_id": command_id,
                "idempotency_key": request.idempotency_key,
                "origin": request.origin,
                "deadline_at": deadline.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "command": request.command,
                "arguments": arguments,
            }
        try:
            try:
                await desktop.socket.send_json(payload)
            except Exception:
                raise DesktopUnavailable() from None
            return await asyncio.wait_for(future, timeout=request.timeout_ms / 1000)
        except TimeoutError:
            return BrowserCommandResponse(
                command_id=command_id,
                state="failed",
                outcome="timeout",
                error=BrowserCommandError(
                    code="timeout",
                    message=(
                        "Desktop command timed out; inspect the current browser state "
                        "before retrying."
                    ),
                ),
            )
        finally:
            self._pending.pop(command_id, None)

    async def resolve(self, socket: CapabilitySocket, payload: dict[str, Any]) -> None:
        async with self._lock:
            command_id = payload.get("command_id")
            pending = self._pending.get(command_id) if isinstance(command_id, str) else None
            if not pending or pending[1] is not socket:
                return
            future = pending[0]
        if future.done():
            return
        try:
            state = payload.get("state")
            if state not in {"completed", "failed"}:
                raise ValueError
            error = payload.get("error")
            if error is not None:
                error = BrowserCommandError.model_validate(error)
            safe_data = sanitize_browser_result_data(
                payload.get("data") if isinstance(payload.get("data"), dict) else {}
            )
            response = BrowserCommandResponse(
                command_id=command_id,
                state=state,
                outcome=sanitize_summary(str(payload.get("outcome", "completed"))),
                data=safe_data,
                error=error,
            )
        except Exception:
            response = BrowserCommandResponse(
                command_id=str(payload.get("command_id", "invalid")),
                state="failed",
                outcome="validation",
                error=BrowserCommandError(
                    code="validation", message="Desktop returned an invalid command result."
                ),
            )
        future.set_result(response)

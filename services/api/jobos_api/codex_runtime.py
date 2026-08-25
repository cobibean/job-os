from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol

CODEX_APP_SERVER_VERSION = "0.144.4"
CODEX_APP_SERVER_RECEIPT_ID = "codex-app-server-rust-v0.144.4-aarch64-apple-darwin"
CODEX_APP_SERVER_SHA256 = "27d324bc906014c77e4e4286edae6b6d093ee60f49bdcf71495e0f57c31dc6fe"
CODEX_CONFIG = 'cli_auth_credentials_store = "keyring"\n'
MAX_PROTOCOL_LINE_BYTES = 4 * 1024 * 1024


class CodexRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CodexRpcError(CodexRuntimeError):
    def __init__(self, rpc_code: int | None, message: str) -> None:
        super().__init__("CODEX_RPC_ERROR", "Codex App Server rejected the request")
        self.rpc_code = rpc_code
        self.safe_message = message[:240]


class CodexRpcClient(Protocol):
    async def start(self) -> None: ...

    async def request(self, method: str, params: object | None = None) -> object: ...

    async def notify(self, method: str, params: object | None = None) -> None: ...

    async def close(self) -> None: ...

    def subscribe(self, callback: Callable[[str, object], Awaitable[None]]) -> None: ...


def prepare_codex_home(codex_home: Path, *, standalone_home: Path | None = None) -> None:
    expanded = codex_home.expanduser()
    if expanded.is_symlink():
        raise CodexRuntimeError("AUTH_VAULT_UNAVAILABLE", "JobOS Codex home is unsafe")
    resolved = expanded.resolve()
    standalone = (standalone_home or Path.home() / ".codex").expanduser().resolve()
    if resolved == standalone or standalone in resolved.parents:
        raise CodexRuntimeError(
            "AUTH_VAULT_UNAVAILABLE", "JobOS Codex home must be isolated from standalone Codex"
        )
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise CodexRuntimeError("AUTH_VAULT_UNAVAILABLE", "JobOS Codex home is unsafe")
    os.chmod(resolved, 0o700)
    auth_json = resolved / "auth.json"
    if auth_json.exists() or auth_json.is_symlink():
        raise CodexRuntimeError(
            "AUTH_VAULT_UNAVAILABLE", "Plaintext Codex credentials are not allowed"
        )
    config_path = resolved / "config.toml"
    if config_path.is_symlink():
        raise CodexRuntimeError("AUTH_VAULT_UNAVAILABLE", "JobOS Codex config is unsafe")
    try:
        if config_path.exists() and config_path.read_text(encoding="utf-8") != CODEX_CONFIG:
            raise CodexRuntimeError(
                "AUTH_VAULT_UNAVAILABLE", "JobOS Codex credential storage is not keyring-only"
            )
        if not config_path.exists():
            descriptor = os.open(
                config_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(CODEX_CONFIG)
                stream.flush()
                os.fsync(stream.fileno())
        os.chmod(config_path, 0o600)
    except OSError as error:
        raise CodexRuntimeError(
            "AUTH_VAULT_UNAVAILABLE", "JobOS Codex keyring configuration is unavailable"
        ) from error


def verify_codex_binary(path: Path) -> None:
    try:
        resolved = path.expanduser().resolve(strict=True)
        mode = resolved.stat().st_mode
        if not resolved.is_file() or not mode & stat.S_IXUSR:
            raise OSError("runtime is not executable")
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise CodexRuntimeError(
            "AGENT_PROVIDER_UNAVAILABLE", "Pinned Codex App Server is unavailable"
        ) from error
    if digest.hexdigest() != CODEX_APP_SERVER_SHA256:
        raise CodexRuntimeError(
            "AGENT_PROVIDER_UNAVAILABLE", "Codex App Server failed integrity verification"
        )


class CodexAppServerProcess:
    """Private JSONL stdio supervisor for the pinned Codex App Server."""

    def __init__(
        self,
        binary_path: Path,
        codex_home: Path,
        *,
        request_timeout: float = 15.0,
        verify_binary: Callable[[Path], None] = verify_codex_binary,
    ) -> None:
        self.binary_path = binary_path
        self.codex_home = codex_home
        self.request_timeout = request_timeout
        self._verify_binary = verify_binary
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[object]] = {}
        self._subscribers: list[Callable[[str, object], Awaitable[None]]] = []
        self._start_lock = asyncio.Lock()

    def subscribe(self, callback: Callable[[str, object], Awaitable[None]]) -> None:
        self._subscribers.append(callback)

    async def start(self) -> None:
        async with self._start_lock:
            if self._process is not None and self._process.returncode is None:
                return
            prepare_codex_home(self.codex_home)
            self._verify_binary(self.binary_path)
            environment = {
                "CODEX_HOME": str(self.codex_home.expanduser().resolve()),
                "HOME": str(Path.home()),
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                "PATH": os.pathsep.join(
                    (
                        str(self.binary_path.expanduser().resolve().parent),
                        "/usr/bin",
                        "/bin",
                        "/usr/sbin",
                        "/sbin",
                    )
                ),
            }
            try:
                self._process = await asyncio.create_subprocess_exec(
                    str(self.binary_path.expanduser().resolve()),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.codex_home.expanduser().resolve()),
                    env=environment,
                )
            except OSError as error:
                raise CodexRuntimeError(
                    "AGENT_PROVIDER_UNAVAILABLE", "Codex App Server could not start"
                ) from error
            self._reader_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._discard_stderr())
            try:
                result = await self.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "jobos",
                            "title": "JobOS",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": False},
                    },
                )
                if not isinstance(result, dict):
                    raise CodexRuntimeError(
                        "AGENT_PROVIDER_UNAVAILABLE", "Codex protocol initialization failed"
                    )
                await self.notify("initialized")
            except BaseException:
                await self.close()
                raise

    async def _write(self, payload: dict[str, object]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise CodexRuntimeError("AGENT_PROVIDER_UNAVAILABLE", "Codex App Server is offline")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        process.stdin.write(encoded)
        try:
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise CodexRuntimeError(
                "AGENT_PROVIDER_UNAVAILABLE", "Codex App Server disconnected"
            ) from error

    async def request(self, method: str, params: object | None = None) -> object:
        if self._process is None:
            await self.start()
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload: dict[str, object] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            await self._write(payload)
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except TimeoutError as error:
            raise CodexRuntimeError(
                "AGENT_PROVIDER_UNAVAILABLE", "Codex App Server timed out"
            ) from error
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: object | None = None) -> None:
        payload: dict[str, object] = {"method": method}
        if params is not None:
            payload["params"] = params
        await self._write(payload)

    async def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while line := await process.stdout.readline():
                if len(line) > MAX_PROTOCOL_LINE_BYTES:
                    raise CodexRuntimeError(
                        "AGENT_PROVIDER_UNAVAILABLE", "Codex protocol response was oversized"
                    )
                try:
                    message = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise CodexRuntimeError(
                        "AGENT_PROVIDER_UNAVAILABLE", "Codex protocol response was invalid"
                    ) from error
                if not isinstance(message, dict):
                    continue
                response_id = message.get("id")
                if isinstance(response_id, int) and response_id in self._pending:
                    future = self._pending[response_id]
                    if "error" in message:
                        error = message["error"]
                        code = error.get("code") if isinstance(error, dict) else None
                        text = (
                            error.get("message")
                            if isinstance(error, dict)
                            else "Request rejected"
                        )
                        future.set_exception(
                            CodexRpcError(code if isinstance(code, int) else None, str(text))
                        )
                    else:
                        future.set_result(message.get("result"))
                    continue
                method = message.get("method")
                if isinstance(method, str):
                    params = message.get("params")
                    for subscriber in tuple(self._subscribers):
                        with suppress(Exception):
                            await subscriber(method, params)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            failure = (
                error
                if isinstance(error, CodexRuntimeError)
                else CodexRuntimeError("AGENT_PROVIDER_UNAVAILABLE", "Codex App Server stopped")
            )
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(failure)

    async def _discard_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while await process.stderr.read(8192):
                pass
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        process, self._process = self._process, None
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                with suppress(asyncio.CancelledError, CodexRuntimeError):
                    await task
        self._reader_task = None
        self._stderr_task = None
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(
                    CodexRuntimeError("AGENT_PROVIDER_UNAVAILABLE", "Codex App Server stopped")
                )
        self._pending.clear()
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()

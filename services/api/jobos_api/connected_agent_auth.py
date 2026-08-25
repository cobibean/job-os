from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .codex_runtime import (
    CODEX_APP_SERVER_RECEIPT_ID,
    CODEX_APP_SERVER_VERSION,
    CODEX_CONFIG,
    CodexRpcClient,
    CodexRpcError,
    CodexRuntimeError,
    prepare_codex_home,
)
from .installation_profiles import (
    ConnectedAgentRecord,
    InstallationProfileConflict,
    InstallationProfileNotFound,
    InstallationProfileRegistry,
)

AUTH_TRANSACTION_TTL = timedelta(minutes=15)
PREFERRED_CODEX_MODEL = "gpt-5.6-sol"
PREFERRED_REASONING_EFFORT = "medium"


class AuthModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StartAuthRequest(AuthModel):
    mode: Literal["connect", "reconnect", "replace"]
    expected_account_fingerprint: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )


class SafeAuthTransaction(AuthModel):
    transaction_id: str = Field(pattern=r"^jauth_[a-f0-9]{32}$")
    agent_id: str = Field(pattern=r"^jagent_[a-f0-9]{32}$")
    method: Literal["device_code", "host_callback"]
    status: Literal[
        "login_pending", "connected", "cancelled", "expired", "failed", "cleanup_required"
    ]
    verification_url: str | None = None
    user_code: str | None = Field(default=None, repr=False)
    expires_at: datetime
    error_code: str | None = None


class VaultStatus(AuthModel):
    available: bool
    authenticated: bool


class IsolationProof(AuthModel):
    isolated: bool
    keyring_only: bool
    plaintext_credentials_absent: bool
    runtime_receipt_id: str


class RemovalProof(AuthModel):
    removed: bool
    verified: bool


class AuthFlowBroker(Protocol):
    async def start_device_code(
        self,
        agent_id: str,
        mode: Literal["connect", "reconnect", "replace"],
        expected_account_fingerprint: str | None,
        *,
        allow_host_callback: bool,
    ) -> SafeAuthTransaction: ...

    async def read(self, transaction_id: str) -> SafeAuthTransaction: ...

    async def cancel(self, transaction_id: str) -> SafeAuthTransaction: ...


class CredentialVault(Protocol):
    async def inspect(self, vault_ref: str) -> VaultStatus: ...

    async def verify_isolation(self, vault_ref: str) -> IsolationProof: ...

    async def remove(self, vault_ref: str) -> RemovalProof: ...


class AuthFlowError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _PendingAuth:
    def __init__(
        self,
        *,
        public: SafeAuthTransaction,
        login_id: str,
        mode: Literal["connect", "reconnect", "replace"],
        runtime_namespace: str,
        had_existing_credentials: bool,
    ) -> None:
        self.public = public
        self.login_id = login_id
        self.mode = mode
        self.runtime_namespace = runtime_namespace
        self.had_existing_credentials = had_existing_credentials
        self.completion: bool | None = None
        self.failure_code: str | None = None


def _safe_url(value: object, *, allow_loopback: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise AuthFlowError("AUTH_SIGN_IN_REQUIRED", "Codex returned an invalid sign-in URL")
    parsed = urlsplit(value)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.username
        or parsed.password
        or parsed.fragment
        or (
            parsed.scheme != "https"
            and not (allow_loopback and loopback and parsed.scheme == "http")
        )
    ):
        raise AuthFlowError("AUTH_SIGN_IN_REQUIRED", "Codex returned an unsafe sign-in URL")
    return value


def _runtime_namespace(agent_id: str) -> str:
    return f"codex-{agent_id.removeprefix('jagent_')}"


def _vault_reference(namespace: str) -> str:
    return f"vault_ref_codex:{namespace}"


def _namespace_from_reference(reference: str) -> str:
    prefix = "vault_ref_codex:"
    if not reference.startswith(prefix):
        raise AuthFlowError("AUTH_VAULT_UNAVAILABLE", "Codex vault reference is invalid")
    namespace = reference.removeprefix(prefix)
    if not namespace or len(namespace) > 64:
        raise AuthFlowError("AUTH_VAULT_UNAVAILABLE", "Codex vault reference is invalid")
    return namespace


class CodexCredentialVault:
    """Verifies Codex-owned Keychain state without reading raw credentials."""

    def __init__(self, client: CodexRpcClient, codex_home: Path) -> None:
        self.client = client
        self.codex_home = codex_home

    async def inspect(self, vault_ref: str) -> VaultStatus:
        _namespace_from_reference(vault_ref)
        try:
            await self.client.start()
            response = await self.client.request("account/read", {"refreshToken": False})
        except CodexRuntimeError:
            return VaultStatus(available=False, authenticated=False)
        account = response.get("account") if isinstance(response, dict) else None
        return VaultStatus(available=True, authenticated=isinstance(account, dict))

    async def verify_isolation(self, vault_ref: str) -> IsolationProof:
        _namespace_from_reference(vault_ref)
        try:
            prepare_codex_home(self.codex_home)
            config = (self.codex_home / "config.toml").read_text(encoding="utf-8")
            plaintext_absent = not (self.codex_home / "auth.json").exists()
        except (OSError, CodexRuntimeError) as error:
            raise AuthFlowError(
                "AUTH_VAULT_UNAVAILABLE", "JobOS Codex Keychain isolation could not be verified"
            ) from error
        proof = IsolationProof(
            isolated=True,
            keyring_only=config == CODEX_CONFIG,
            plaintext_credentials_absent=plaintext_absent,
            runtime_receipt_id=CODEX_APP_SERVER_RECEIPT_ID,
        )
        if not proof.keyring_only or not proof.plaintext_credentials_absent:
            raise AuthFlowError(
                "AUTH_VAULT_UNAVAILABLE", "JobOS Codex Keychain isolation could not be verified"
            )
        return proof

    async def remove(self, vault_ref: str) -> RemovalProof:
        _namespace_from_reference(vault_ref)
        await self.client.start()
        await self.client.request("account/logout")
        status = await self.inspect(vault_ref)
        proof = RemovalProof(removed=not status.authenticated, verified=status.available)
        if not proof.verified or not proof.removed:
            raise AuthFlowError(
                "AUTH_CLEANUP_REQUIRED", "JobOS could not verify Codex credential cleanup"
            )
        return proof


class CodexAuthFlowBroker:
    def __init__(
        self,
        client: CodexRpcClient,
        vault: CredentialVault,
        registry: InstallationProfileRegistry,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.vault = vault
        self.registry = registry
        self.now = now
        self._transactions: dict[str, _PendingAuth] = {}
        self._lock = asyncio.Lock()
        self.client.subscribe(self._notification)

    def _agent(self, agent_id: str) -> ConnectedAgentRecord:
        agent = next(
            (item for item in self.registry.load().connected_agents if item.id == agent_id), None
        )
        if agent is None:
            raise InstallationProfileNotFound("Connected Agent was not found")
        if agent.provider != "codex":
            raise AuthFlowError("AGENT_NOT_CONFIGURED", "Connected Agent does not use Codex")
        return agent

    async def _notification(self, method: str, params: object) -> None:
        if method != "account/login/completed" or not isinstance(params, dict):
            return
        login_id = params.get("loginId")
        for pending in tuple(self._transactions.values()):
            if login_id is None or pending.login_id == login_id:
                pending.completion = params.get("success") is True
                if pending.completion is False:
                    pending.failure_code = "AUTH_SIGN_IN_REQUIRED"

    async def _finish_without_credentials(
        self,
        pending: _PendingAuth,
        *,
        status: Literal["cancelled", "expired", "failed"],
        error_code: str | None,
    ) -> SafeAuthTransaction:
        cleanup_failed = False
        try:
            await self.client.request("account/login/cancel", {"loginId": pending.login_id})
        except (CodexRuntimeError, AuthFlowError):
            cleanup_failed = True
        # A cancelled replacement must not log out the account that was connected
        # before this transaction. Once Codex confirms completion, the credential
        # may have changed and failed validation, so remove it fail-closed.
        credentials_removed = False
        if not pending.had_existing_credentials or pending.completion is True:
            try:
                await self.vault.remove(_vault_reference(pending.runtime_namespace))
                credentials_removed = True
            except (CodexRuntimeError, AuthFlowError):
                cleanup_failed = True
        if credentials_removed and pending.had_existing_credentials:
            # A completed replacement may have overwritten the old credential.
            # If its validation then fails, the safe state is disconnected.
            disconnected = False
            for _attempt in range(2):
                revision = self.registry.load().registry_revision
                try:
                    self.registry.disconnect_connected_agent(
                        pending.public.agent_id,
                        expected_registry_revision=revision,
                        idempotency_key=f"{pending.public.transaction_id}-cleanup",
                        now=self.now(),
                    )
                    disconnected = True
                    break
                except InstallationProfileConflict:
                    continue
            if not disconnected:
                cleanup_failed = True
        pending.public = pending.public.model_copy(
            update={
                "status": "cleanup_required" if cleanup_failed else status,
                "verification_url": None,
                "user_code": None,
                "error_code": "AUTH_CLEANUP_REQUIRED" if cleanup_failed else error_code,
            }
        )
        return pending.public

    async def start_device_code(
        self,
        agent_id: str,
        mode: Literal["connect", "reconnect", "replace"],
        expected_account_fingerprint: str | None,
        *,
        allow_host_callback: bool,
    ) -> SafeAuthTransaction:
        async with self._lock:
            if any(
                pending.public.status == "login_pending"
                for pending in self._transactions.values()
            ):
                raise AuthFlowError(
                    "AGENT_CARDINALITY_CONFLICT",
                    "Another Codex sign-in is already pending",
                )
            agent = self._agent(agent_id)
            if mode == "connect" and agent.credential_reference is not None:
                raise AuthFlowError("AGENT_CARDINALITY_CONFLICT", "Codex is already connected")
            if mode == "reconnect" and agent.account_fingerprint is None:
                raise AuthFlowError(
                    "AUTH_ACCOUNT_REPLACEMENT_REQUIRED",
                    "The original Codex account cannot be verified; confirm account replacement",
                )
            if mode in {"reconnect", "replace"} and (
                expected_account_fingerprint != agent.account_fingerprint
            ):
                raise AuthFlowError(
                    "AUTH_ACCOUNT_REPLACEMENT_REQUIRED",
                    "Codex account replacement requires current account confirmation",
                )
            await self.client.start()
            method: Literal["device_code", "host_callback"] = "device_code"
            try:
                response = await self.client.request(
                    "account/login/start", {"type": "chatgptDeviceCode"}
                )
            except CodexRpcError as error:
                unavailable = error.rpc_code == -32601 or any(
                    marker in error.safe_message.casefold()
                    for marker in ("unavailable", "disabled", "unsupported")
                )
                if not unavailable or not allow_host_callback:
                    raise AuthFlowError(
                        "AUTH_SIGN_IN_REQUIRED", "Device-code sign-in is unavailable"
                    ) from error
                response = await self.client.request(
                    "account/login/start",
                    {
                        "type": "chatgpt",
                        "codexStreamlinedLogin": True,
                        "useHostedLoginSuccessPage": True,
                    },
                )
                method = "host_callback"
            if not isinstance(response, dict) or not isinstance(response.get("loginId"), str):
                raise AuthFlowError("AUTH_SIGN_IN_REQUIRED", "Codex sign-in did not start")
            verification_url = _safe_url(
                response.get("verificationUrl")
                if method == "device_code"
                else response.get("authUrl"),
                allow_loopback=method == "host_callback",
            )
            user_code = response.get("userCode") if method == "device_code" else None
            if method == "device_code" and (
                not isinstance(user_code, str) or not 1 <= len(user_code) <= 128
            ):
                raise AuthFlowError("AUTH_SIGN_IN_REQUIRED", "Codex sign-in code is invalid")
            transaction_id = f"jauth_{uuid4().hex}"
            transaction = SafeAuthTransaction(
                transaction_id=transaction_id,
                agent_id=agent_id,
                method=method,
                status="login_pending",
                verification_url=verification_url,
                user_code=user_code,
                expires_at=self.now() + AUTH_TRANSACTION_TTL,
            )
            self._transactions[transaction_id] = _PendingAuth(
                public=transaction,
                login_id=response["loginId"],
                mode=mode,
                runtime_namespace=_runtime_namespace(agent_id),
                had_existing_credentials=agent.credential_reference is not None,
            )
            return transaction

    async def read(self, transaction_id: str) -> SafeAuthTransaction:
        pending = self._transactions.get(transaction_id)
        if pending is None:
            raise AuthFlowError("AUTH_SIGN_IN_REQUIRED", "Auth transaction was not found")
        if pending.public.status != "login_pending":
            return pending.public
        if self.now() >= pending.public.expires_at:
            return await self._finish_without_credentials(
                pending,
                status="expired",
                error_code="AUTH_SIGN_IN_REQUIRED",
            )
        if pending.completion is False:
            return await self._finish_without_credentials(
                pending,
                status="failed",
                error_code=pending.failure_code or "AUTH_SIGN_IN_REQUIRED",
            )
        if pending.completion is not True:
            return pending.public
        account_response = await self.client.request("account/read", {"refreshToken": True})
        account = account_response.get("account") if isinstance(account_response, dict) else None
        if not isinstance(account, dict):
            return pending.public
        vault_ref = _vault_reference(pending.runtime_namespace)
        try:
            await self.vault.verify_isolation(vault_ref)
        except AuthFlowError as error:
            return await self._finish_without_credentials(
                pending,
                status="failed",
                error_code=error.code,
            )
        models = await self.client.request("model/list", {"includeHidden": False})
        if not isinstance(models, dict) or not isinstance(models.get("data"), list):
            return await self._finish_without_credentials(
                pending,
                status="failed",
                error_code="AGENT_PROVIDER_UNAVAILABLE",
            )
        plan_type = account.get("planType")
        summary = {
            "display_name": "ChatGPT",
            "account_hint": "ChatGPT account",
            **({"plan_name": str(plan_type)[:120]} if plan_type else {}),
        }
        self.registry.complete_connected_agent_auth(
            pending.public.agent_id,
            runtime_namespace=pending.runtime_namespace,
            credential_reference=vault_ref,
            account_summary=summary,
            # Codex v0.144.4 exposes no stable opaque account ID. Never fingerprint email.
            account_fingerprint=None,
            idempotency_key=pending.public.transaction_id,
            now=self.now(),
        )
        pending.public = pending.public.model_copy(
            update={
                "status": "connected",
                "verification_url": None,
                "user_code": None,
                "error_code": None,
            }
        )
        return pending.public

    async def cancel(self, transaction_id: str) -> SafeAuthTransaction:
        pending = self._transactions.get(transaction_id)
        if pending is None:
            raise AuthFlowError("AUTH_SIGN_IN_REQUIRED", "Auth transaction was not found")
        if pending.public.status == "login_pending":
            return await self._finish_without_credentials(
                pending,
                status="cancelled",
                error_code=None,
            )
        return pending.public


class CodexConnectedAgentRuntime:
    """Phase 4 runtime health/models/logout control; chat sessions arrive in Phase 5."""

    def __init__(self, client: CodexRpcClient, vault: CredentialVault) -> None:
        self.client = client
        self.vault = vault

    async def inspect_connection(self, agent: ConnectedAgentRecord) -> dict[str, object]:
        if agent.provider != "codex" or agent.credential_reference is None:
            return {
                "state": "unavailable",
                "label": "Runtime unavailable",
                "provider_available": False,
                "tools_available": False,
                "retry_after_seconds": None,
            }
        status = await self.vault.inspect(agent.credential_reference)
        if not status.available:
            state, label = "unavailable", "Provider unavailable"
        elif not status.authenticated:
            state, label = "sign_in_required", "Sign-in required"
        else:
            state, label = "connected", "Connected"
        return {
            "state": state,
            "label": label,
            "provider_available": status.available and status.authenticated,
            # Phase 5 owns MCP/capability readiness and Codex chat acceptance.
            "tools_available": False,
            "retry_after_seconds": None,
        }

    async def list_models(self, agent: ConnectedAgentRecord) -> dict[str, object]:
        if agent.provider != "codex" or agent.credential_reference is None:
            return {"live": False, "models": []}
        status = await self.vault.inspect(agent.credential_reference)
        if not status.available or not status.authenticated:
            return {"live": False, "models": []}
        response = await self.client.request("model/list", {"includeHidden": False})
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list):
            raise CodexRuntimeError(
                "AGENT_PROVIDER_UNAVAILABLE", "Codex model catalog is unavailable"
            )
        models: list[dict[str, object]] = []
        for item in data:
            if not isinstance(item, dict) or item.get("hidden") is True:
                continue
            model_id = item.get("id")
            display_name = item.get("displayName")
            efforts = item.get("supportedReasoningEfforts")
            if not isinstance(model_id, str) or not isinstance(display_name, str):
                continue
            normalized_efforts = [
                option["reasoningEffort"]
                for option in efforts or []
                if isinstance(option, dict)
                and isinstance(option.get("reasoningEffort"), str)
            ]
            if normalized_efforts:
                models.append(
                    {
                        "model_id": model_id,
                        "display_name": display_name,
                        "reasoning_efforts": normalized_efforts,
                    }
                )
        return {"live": True, "models": models}

    async def disconnect(self, agent: ConnectedAgentRecord) -> dict[str, object]:
        if agent.provider != "codex" or agent.credential_reference is None:
            return {"verified": True}
        proof = await self.vault.remove(agent.credential_reference)
        return {"verified": proof.removed and proof.verified}

    def diagnostics(self) -> dict[str, object]:
        return {
            "provider": "codex",
            "runtime_version": CODEX_APP_SERVER_VERSION,
            "runtime_receipt_id": CODEX_APP_SERVER_RECEIPT_ID,
        }

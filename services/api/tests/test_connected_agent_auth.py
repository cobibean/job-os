from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jobos_api.codex_runtime import CODEX_APP_SERVER_RECEIPT_ID, CodexRpcError
from jobos_api.connected_agent_auth import (
    AuthFlowError,
    CodexAuthFlowBroker,
    CodexConnectedAgentRuntime,
    CodexCredentialVault,
    IsolationProof,
    RemovalProof,
    VaultStatus,
)
from jobos_api.installation_profiles import (
    AnchoredRuntime,
    InstallationProfileRecord,
    InstallationProfileRegistry,
    InstallationProfileRegistryData,
)

PROFILE_ID = "jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AGENT_ID = "jagent_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NOW = datetime(2026, 8, 25, 1, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeCodexClient:
    def __init__(
        self,
        *,
        device_unavailable: bool = False,
        invalid_models: bool = False,
        mcp_ready: bool = True,
    ) -> None:
        self.device_unavailable = device_unavailable
        self.invalid_models = invalid_models
        self.mcp_ready = mcp_ready
        self.calls: list[tuple[str, object | None]] = []
        self.subscribers = []
        self.account: dict[str, object] | None = {
            "type": "chatgpt",
            "email": "must-not-persist@example.test",
            "planType": "plus",
        }

    async def start(self) -> None:
        return None

    def subscribe(self, callback) -> None:
        self.subscribers.append(callback)

    async def notify(self, method: str, params: object | None = None) -> None:
        self.calls.append((method, params))

    async def close(self) -> None:
        return None

    async def request(self, method: str, params: object | None = None) -> object:
        self.calls.append((method, params))
        if method == "account/login/start":
            if params == {"type": "chatgptDeviceCode"}:
                if self.device_unavailable:
                    raise CodexRpcError(-32601, "device code unsupported")
                return {
                    "type": "chatgptDeviceCode",
                    "loginId": "login-1",
                    "userCode": "SAFE-CODE",
                    "verificationUrl": "https://auth.example.test/device",
                }
            return {
                "type": "chatgpt",
                "loginId": "login-2",
                "authUrl": "http://127.0.0.1:1455/callback",
            }
        if method == "account/read":
            return {"account": self.account}
        if method == "account/login/cancel":
            return {}
        if method == "account/logout":
            self.account = None
            return {}
        if method == "model/list":
            if self.invalid_models:
                return {"data": None}
            return {
                "data": [
                    {
                        "id": "gpt-5.6-sol",
                        "displayName": "GPT-5.6",
                        "hidden": False,
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "medium"},
                            {"reasoningEffort": "high"},
                        ],
                    },
                    {
                        "id": "hidden-model",
                        "displayName": "Hidden",
                        "hidden": True,
                        "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
                    },
                ]
            }
        if method == "mcpServerStatus/list":
            resources = [{"uri": "jobos://capability-map"}] if self.mcp_ready else []
            tools = (
                {"jobos.test": {"name": "jobos.test", "inputSchema": {"type": "object"}}}
                if self.mcp_ready
                else {}
            )
            return {"data": [{"name": "jobos", "resources": resources, "tools": tools}]}
        raise AssertionError(f"unexpected method: {method}")

    async def complete_login(self, *, success: bool = True) -> None:
        for subscriber in self.subscribers:
            await subscriber(
                "account/login/completed", {"loginId": "login-1", "success": success}
            )


class FakeVault:
    def __init__(self) -> None:
        self.removed = False
        self.isolation_error: AuthFlowError | None = None
        self.removal_error: AuthFlowError | None = None

    @property
    def tools_configured(self) -> bool:
        return True

    async def inspect(self, vault_ref: str) -> VaultStatus:
        assert vault_ref.startswith("vault_ref_codex:")
        return VaultStatus(available=True, authenticated=not self.removed)

    async def verify_isolation(self, vault_ref: str) -> IsolationProof:
        assert vault_ref.startswith("vault_ref_codex:")
        if self.isolation_error is not None:
            raise self.isolation_error
        return IsolationProof(
            isolated=True,
            keyring_only=True,
            plaintext_credentials_absent=True,
            runtime_receipt_id=CODEX_APP_SERVER_RECEIPT_ID,
        )

    async def remove(self, vault_ref: str) -> RemovalProof:
        assert vault_ref.startswith("vault_ref_codex:")
        if self.removal_error is not None:
            raise self.removal_error
        self.removed = True
        return RemovalProof(removed=True, verified=True)


def registry_with_codex_agent(tmp_path: Path) -> InstallationProfileRegistry:
    registry = InstallationProfileRegistry(tmp_path / "installation-profiles.json")
    runtime = AnchoredRuntime(
        job_provider="sqlite",
        artifact_provider="local",
        state_db_path=(tmp_path / "state" / "jobos.db").absolute(),
        jobs_db_path=(tmp_path / "jobs" / "jobs.db").absolute(),
        local_artifact_root=(tmp_path / "artifacts").absolute(),
    )
    registry.write(
        InstallationProfileRegistryData(
            registry_revision=1,
            active_profile_id=PROFILE_ID,
            profiles=(
                InstallationProfileRecord(
                    profile_id=PROFILE_ID,
                    display_name="Personal",
                    storage_mode="anchored",
                    created_at=NOW,
                    updated_at=NOW,
                    anchored_runtime=runtime,
                ),
            ),
        )
    )
    created = registry.create_connected_agent(
        provider="codex",
        display_name="Codex",
        avatar_id="codex",
        default_model_id="gpt-5.6-sol",
        default_reasoning_effort="medium",
        connection_config=None,
        credential_reference=None,
        expected_registry_revision=1,
        idempotency_key="create-codex",
        agent_id=AGENT_ID,
        now=NOW,
    )
    assert created.lifecycle == "disconnected"
    return registry


@pytest.mark.anyio
async def test_device_code_completes_with_safe_metadata_only(tmp_path: Path) -> None:
    registry = registry_with_codex_agent(tmp_path)
    client = FakeCodexClient()
    vault = FakeVault()
    broker = CodexAuthFlowBroker(client, vault, registry, now=lambda: NOW)

    started = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    assert started.method == "device_code"
    assert started.status == "login_pending"
    assert started.user_code == "SAFE-CODE"

    await client.complete_login()
    completed = await broker.read(started.transaction_id)
    assert completed.status == "connected"
    assert completed.user_code is None
    assert completed.verification_url is None
    persisted = registry.load().connected_agents[0]
    serialized = registry.path.read_text(encoding="utf-8")
    assert persisted.lifecycle == "connected"
    assert persisted.account_summary == {
        "display_name": "ChatGPT",
        "account_hint": "ChatGPT account",
        "plan_name": "plus",
    }
    assert persisted.account_fingerprint is None
    assert "must-not-persist@example.test" not in serialized
    assert "SAFE-CODE" not in serialized


@pytest.mark.anyio
async def test_device_code_cancel_and_expiry_clear_ephemeral_code(tmp_path: Path) -> None:
    registry = registry_with_codex_agent(tmp_path)
    client = FakeCodexClient()
    vault = FakeVault()
    current = NOW
    broker = CodexAuthFlowBroker(client, vault, registry, now=lambda: current)

    started = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    cancelled = await broker.cancel(started.transaction_id)
    assert cancelled.status == "cancelled"
    assert cancelled.user_code is None

    started = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    current = NOW + timedelta(minutes=16)
    expired = await broker.read(started.transaction_id)
    assert expired.status == "expired"
    assert expired.user_code is None


@pytest.mark.anyio
async def test_callback_fallback_is_host_only_and_explicit(tmp_path: Path) -> None:
    registry = registry_with_codex_agent(tmp_path)
    client = FakeCodexClient(device_unavailable=True)
    broker = CodexAuthFlowBroker(client, FakeVault(), registry, now=lambda: NOW)

    with pytest.raises(AuthFlowError, match="unavailable"):
        await broker.start_device_code(
            AGENT_ID, "connect", None, allow_host_callback=False
        )

    started = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=True
    )
    assert started.method == "host_callback"
    assert started.verification_url == "http://127.0.0.1:1455/callback"
    assert started.user_code is None


@pytest.mark.anyio
async def test_isolation_failure_fails_closed_and_requires_cleanup(tmp_path: Path) -> None:
    registry = registry_with_codex_agent(tmp_path)
    vault = FakeVault()
    vault.isolation_error = AuthFlowError(
        "AUTH_VAULT_UNAVAILABLE", "vault could not be verified"
    )
    vault.removal_error = AuthFlowError(
        "AUTH_CLEANUP_REQUIRED", "credential cleanup could not be verified"
    )
    client = FakeCodexClient()
    broker = CodexAuthFlowBroker(client, vault, registry, now=lambda: NOW)

    started = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    await client.complete_login()
    result = await broker.read(started.transaction_id)

    assert result.status == "cleanup_required"
    assert result.error_code == "AUTH_CLEANUP_REQUIRED"
    assert registry.load().connected_agents[0].lifecycle == "disconnected"


@pytest.mark.anyio
async def test_reconnect_requires_explicit_account_replacement(tmp_path: Path) -> None:
    registry = registry_with_codex_agent(tmp_path)
    broker = CodexAuthFlowBroker(FakeCodexClient(), FakeVault(), registry, now=lambda: NOW)

    with pytest.raises(AuthFlowError) as captured:
        await broker.start_device_code(
            AGENT_ID, "reconnect", None, allow_host_callback=False
        )
    assert captured.value.code == "AUTH_ACCOUNT_REPLACEMENT_REQUIRED"


@pytest.mark.anyio
async def test_runtime_models_are_live_and_disconnect_is_vault_verified(tmp_path: Path) -> None:
    registry = registry_with_codex_agent(tmp_path)
    client = FakeCodexClient()
    vault = FakeVault()
    broker = CodexAuthFlowBroker(client, vault, registry, now=lambda: NOW)
    transaction = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    await client.complete_login()
    await broker.read(transaction.transaction_id)
    agent = registry.load().connected_agents[0]
    runtime = CodexConnectedAgentRuntime(client, vault)

    health = await runtime.inspect_connection(agent)
    assert health["provider_available"] is True
    assert health["tools_available"] is True
    client.mcp_ready = False
    unavailable_health = await runtime.inspect_connection(agent)
    assert unavailable_health["provider_available"] is True
    assert unavailable_health["tools_available"] is False
    client.mcp_ready = True
    models = await runtime.list_models(agent)
    assert models == {
        "live": True,
        "models": [
            {
                "model_id": "gpt-5.6-sol",
                "display_name": "GPT-5.6",
                "reasoning_efforts": ["medium", "high"],
            }
        ],
    }
    assert await runtime.disconnect(agent) == {"verified": True}
    assert vault.removed is True


@pytest.mark.anyio
async def test_replace_waits_for_completion_and_cancel_preserves_existing_vault(
    tmp_path: Path,
) -> None:
    registry = registry_with_codex_agent(tmp_path)
    client = FakeCodexClient()
    vault = FakeVault()
    broker = CodexAuthFlowBroker(client, vault, registry, now=lambda: NOW)

    initial = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    await client.complete_login()
    assert (await broker.read(initial.transaction_id)).status == "connected"
    fingerprint = registry.load().connected_agents[0].account_fingerprint

    replacement = await broker.start_device_code(
        AGENT_ID, "replace", fingerprint, allow_host_callback=False
    )
    model_calls_before_poll = sum(
        method == "model/list" for method, _params in client.calls
    )
    assert (await broker.read(replacement.transaction_id)).status == "login_pending"
    assert (
        sum(method == "model/list" for method, _params in client.calls)
        == model_calls_before_poll
    )

    cancelled = await broker.cancel(replacement.transaction_id)
    assert cancelled.status == "cancelled"
    assert vault.removed is False
    assert registry.load().connected_agents[0].lifecycle == "connected"


@pytest.mark.anyio
async def test_successful_replacement_locks_prior_chats_without_fabricating_identity(
    tmp_path: Path,
) -> None:
    registry = registry_with_codex_agent(tmp_path)
    client = FakeCodexClient()
    locked: list[str] = []
    broker = CodexAuthFlowBroker(
        client,
        FakeVault(),
        registry,
        now=lambda: NOW,
        on_account_replaced=locked.append,
    )
    initial = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    await client.complete_login()
    assert (await broker.read(initial.transaction_id)).status == "connected"

    snapshot = registry.load()
    registry.disconnect_connected_agent(
        AGENT_ID,
        expected_registry_revision=snapshot.registry_revision,
        idempotency_key="(FAKE)-disconnect-before-replacement",
        now=NOW,
    )

    replacement = await broker.start_device_code(
        AGENT_ID, "replace", None, allow_host_callback=False
    )
    assert locked == []
    await client.complete_login()
    assert (await broker.read(replacement.transaction_id)).status == "connected"

    assert locked == [AGENT_ID]
    assert registry.load().connected_agents[0].account_fingerprint is None


@pytest.mark.anyio
async def test_completed_replacement_cleanup_disconnects_and_auth_is_single_flight(
    tmp_path: Path,
) -> None:
    registry = registry_with_codex_agent(tmp_path)
    client = FakeCodexClient()
    vault = FakeVault()
    broker = CodexAuthFlowBroker(client, vault, registry, now=lambda: NOW)

    initial = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    with pytest.raises(AuthFlowError) as overlapping:
        await broker.start_device_code(
            AGENT_ID, "connect", None, allow_host_callback=False
        )
    assert overlapping.value.code == "AGENT_CARDINALITY_CONFLICT"

    await client.complete_login()
    assert (await broker.read(initial.transaction_id)).status == "connected"
    fingerprint = registry.load().connected_agents[0].account_fingerprint

    replacement = await broker.start_device_code(
        AGENT_ID, "replace", fingerprint, allow_host_callback=False
    )
    await client.complete_login()
    vault.isolation_error = AuthFlowError(
        "AUTH_VAULT_UNAVAILABLE", "replacement vault could not be verified"
    )
    failed = await broker.read(replacement.transaction_id)

    assert failed.status == "failed"
    assert vault.removed is True
    disconnected = registry.load().connected_agents[0]
    assert disconnected.lifecycle == "disconnected"
    assert disconnected.credential_reference is None


@pytest.mark.anyio
async def test_invalid_model_catalog_after_login_cleans_up_credentials(tmp_path: Path) -> None:
    registry = registry_with_codex_agent(tmp_path)
    client = FakeCodexClient(invalid_models=True)
    vault = FakeVault()
    broker = CodexAuthFlowBroker(client, vault, registry, now=lambda: NOW)

    started = await broker.start_device_code(
        AGENT_ID, "connect", None, allow_host_callback=False
    )
    await client.complete_login()
    failed = await broker.read(started.transaction_id)

    assert failed.status == "failed"
    assert failed.error_code == "AGENT_PROVIDER_UNAVAILABLE"
    assert vault.removed is True
    assert registry.load().connected_agents[0].lifecycle == "disconnected"


@pytest.mark.anyio
async def test_keyring_isolation_accepts_the_trusted_jobos_mcp_config(tmp_path: Path) -> None:
    launcher = tmp_path / "jobos-mcp"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o700)
    vault = CodexCredentialVault(
        FakeCodexClient(),
        tmp_path / "codex-home",
        mcp_command=launcher,
        mcp_args=("--profile", "(FAKE)-profile"),
    )

    first = await vault.verify_isolation("vault_ref_codex:FAKE-jobos-test")
    second = await vault.verify_isolation("vault_ref_codex:FAKE-jobos-test")

    assert first.keyring_only is True
    assert first.plaintext_credentials_absent is True
    assert second == first
    config = (tmp_path / "codex-home" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.jobos]" in config
    assert str(launcher.resolve()) in config

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from jobos_api.agent_gateway import (
    AmbiguousDeliveryError,
    DefinitiveSessionCreationError,
    GatewayEvent,
)
from jobos_api.app import create_app
from jobos_api.connected_agent_auth import SafeAuthTransaction
from jobos_api.connected_agents import (
    HermesConnectedAgentRuntime,
    ProviderConnectedAgentRuntime,
)
from jobos_api.conversation_store import ConversationStore
from jobos_api.installation_profiles import (
    AnchoredRuntime,
    InstallationProfileRecord,
    InstallationProfileRegistry,
    InstallationProfileRegistryData,
    managed_profile_paths,
)
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore

TOKEN = "(FAKE)-connected-agents-device-token"
PROFILE_ID = "jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AGENT_ID = "jagent_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NOW = datetime(2026, 8, 24, 22, tzinfo=UTC)


class ReadyGateway:
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.started = False
        self.session_requests = 0

    @property
    def connection_state(self):
        return "online"

    async def start(self):
        self.started = True

    async def create_or_resume_conversation(self, stored_session_id):
        self.session_requests += 1
        return (
            stored_session_id or f"(FAKE)-session-{self.conversation_id}",
            stored_session_id or f"(FAKE)-session-{self.conversation_id}",
        )

    async def detach_conversation(self):
        return None

    async def submit_turn(self, text, context):
        return None

    async def stream_events(self):
        if False:
            yield GatewayEvent("status", "working", "(FAKE) working")

    async def interrupt_turn(self, turn_id):
        return None

    async def recover_active_turn(self, stored_session_id, turn_id):
        return None

    async def close(self):
        return None


class ReadyGatewayFactory:
    def __init__(self, gateway_type=ReadyGateway) -> None:
        self.gateways: dict[str, ReadyGateway] = {}
        self.gateway_type = gateway_type

    def create(self, conversation_id: str):
        gateway = self.gateway_type(conversation_id)
        self.gateways[conversation_id] = gateway
        return gateway


class DefinitiveFailureGateway(ReadyGateway):
    async def create_or_resume_conversation(self, stored_session_id):
        del stored_session_id
        raise DefinitiveSessionCreationError("(FAKE) provider rejected session creation")


class AmbiguousFailureGateway(ReadyGateway):
    async def create_or_resume_conversation(self, stored_session_id):
        del stored_session_id
        raise AmbiguousDeliveryError("(FAKE) provider acceptance is unknown")


class ReadyRuntimeControl:
    def __init__(self) -> None:
        self.disconnect_calls = 0
        self.model_calls = 0

    async def inspect_connection(self, agent) -> dict[str, object]:
        del agent
        return {
            "state": "connected",
            "label": "Connected",
            "provider_available": True,
            "tools_available": True,
            "retry_after_seconds": None,
        }

    async def list_models(self, agent) -> dict[str, object]:
        del agent
        self.model_calls += 1
        return {
            "live": True,
            "models": [
                {
                    "model_id": "(FAKE)-model-stable",
                    "display_name": "(FAKE) Stable Model",
                    "reasoning_efforts": ["medium", "high"],
                }
            ],
        }

    async def disconnect(self, agent) -> dict[str, object]:
        del agent
        self.disconnect_calls += 1
        return {"verified": True}


class ReadyAuthBroker:
    def __init__(self) -> None:
        self.started = 0
        self.cancelled = 0
        self.transaction = SafeAuthTransaction(
            transaction_id="jauth_" + "c" * 32,
            agent_id=AGENT_ID,
            method="device_code",
            status="login_pending",
            verification_url="https://auth.example.test/device",
            user_code="SAFE-CODE",
            expires_at=NOW,
        )

    async def start_device_code(
        self,
        agent_id,
        mode,
        expected_account_fingerprint,
        *,
        allow_host_callback,
    ):
        del agent_id, mode, expected_account_fingerprint, allow_host_callback
        self.started += 1
        return self.transaction

    async def read(self, transaction_id):
        del transaction_id
        return self.transaction

    async def cancel(self, transaction_id):
        del transaction_id
        self.cancelled += 1
        return self.transaction.model_copy(update={"status": "cancelled", "user_code": None})


def setup_app(
    tmp_path: Path,
    *,
    gateway_type=ReadyGateway,
    runtime_control=None,
    auth_broker=None,
):
    registry_path = tmp_path / "installation-profiles.json"
    state_path = tmp_path / "state" / "jobos.db"
    runtime = AnchoredRuntime(
        job_provider="sqlite",
        artifact_provider="local",
        state_db_path=state_path.absolute(),
        jobs_db_path=(tmp_path / "jobs" / "jobs.db").absolute(),
        local_artifact_root=(tmp_path / "artifacts").absolute(),
    )
    registry = InstallationProfileRegistry(registry_path)
    registry.write(
        InstallationProfileRegistryData(
            registry_revision=1,
            active_profile_id=PROFILE_ID,
            profiles=(
                InstallationProfileRecord(
                    profile_id=PROFILE_ID,
                    display_name="(FAKE) Personal",
                    storage_mode="anchored",
                    created_at=NOW,
                    updated_at=NOW,
                    anchored_runtime=runtime,
                ),
            ),
        )
    )
    registry.create_connected_agent(
        provider="hermes",
        display_name="(FAKE) Hermes",
        avatar_id="hermes",
        default_model_id="(FAKE)-model-stable",
        default_reasoning_effort="medium",
        connection_config={"endpoint_url": "http://127.0.0.1:9220"},
        credential_reference=None,
        expected_registry_revision=1,
        idempotency_key="(FAKE)-create-hermes",
        agent_id=AGENT_ID,
        now=NOW,
    )
    current = registry.load()
    registry.set_profile_default_connected_agent(
        PROFILE_ID,
        AGENT_ID,
        expected_registry_revision=current.registry_revision,
        idempotency_key="(FAKE)-set-default",
        now=NOW,
    )
    state_store = JobOsStateStore(state_path)

    gateway_factory = ReadyGatewayFactory(gateway_type)
    settings = Settings(
        device_token=TOKEN,
        mcp_token="(FAKE)-mcp-token",
        device_id="primary-device",
        state_db_path=state_path,
        jobs_db_path=runtime.jobs_db_path,
        local_artifact_root=runtime.local_artifact_root,
        installation_registry_path=registry_path,
        installation_profile_id=PROFILE_ID,
        installation_profile_name="(FAKE) Personal",
    )
    app = create_app(
        settings,
        state_store=state_store,
        agent_gateway_factory=gateway_factory,
        connected_agent_runtime=runtime_control or ReadyRuntimeControl(),
        connected_agent_auth_broker=auth_broker,
    )
    return app, registry, state_store, gateway_factory, settings


def auth():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-JobOS-Profile-ID": PROFILE_ID,
    }


def chat_payload(revision: int, key: str, **overrides):
    return {
        "selected_job_id": None,
        "connected_agent_id": None,
        "model_id": None,
        "reasoning_effort": None,
        "idempotency_key": key,
        "expected_profile_revision": revision,
        "expected_agent_registry_revision": revision,
        **overrides,
    }


def test_provider_runtime_routes_hermes_to_its_fixed_profile_model(tmp_path):
    _, registry, _, _, _ = setup_app(tmp_path)
    record = registry.load().connected_agents[0]
    runtime = ProviderConnectedAgentRuntime(
        {"hermes": HermesConnectedAgentRuntime(configured=True)}
    )

    health = asyncio.run(runtime.inspect_connection(record))
    models = asyncio.run(runtime.list_models(record))

    assert health["provider_available"] is True
    assert health["tools_available"] is True
    assert models == {
        "live": True,
        "models": [
            {
                "model_id": "(FAKE)-model-stable",
                "display_name": "(FAKE)-model-stable",
                "reasoning_efforts": ["medium"],
            }
        ],
    }


def test_app_startup_repairs_completed_offline_hermes_migration_for_new_chat(tmp_path):
    registry_path = tmp_path / "installation-profiles.json"
    state_path = (tmp_path / "state" / "jobos.db").absolute()
    runtime = AnchoredRuntime(
        job_provider="sqlite",
        artifact_provider="local",
        state_db_path=state_path,
        jobs_db_path=(tmp_path / "jobs" / "jobs.db").absolute(),
        local_artifact_root=(tmp_path / "artifacts").absolute(),
    )
    JobOsStateStore(state_path).initialize(installation_profile_id=PROFILE_ID)
    registry = InstallationProfileRegistry(registry_path)
    registry.write(
        InstallationProfileRegistryData(
            schema_version=1,
            registry_revision=1,
            active_profile_id=PROFILE_ID,
            profiles=(
                InstallationProfileRecord(
                    profile_id=PROFILE_ID,
                    display_name="(FAKE) Personal",
                    storage_mode="anchored",
                    created_at=NOW,
                    updated_at=NOW,
                    anchored_runtime=runtime,
                ),
            ),
        )
    )
    registry.load_or_bootstrap(runtime, now=NOW)
    registry.resume_connected_agent_migration(runtime)
    assert registry.load().connected_agents[0].lifecycle == "disconnected"

    app = create_app(
        Settings(
            device_token=TOKEN,
            mcp_token="(FAKE)-mcp-token",
            device_id="primary-device",
            state_db_path=state_path,
            jobs_db_path=runtime.jobs_db_path,
            local_artifact_root=runtime.local_artifact_root,
            installation_registry_path=registry_path,
            installation_profile_id=PROFILE_ID,
            installation_profile_name="(FAKE) Personal",
            hermes_dashboard_url="ws://127.0.0.1:9120/api/ws",
            hermes_dashboard_token="(FAKE)-hermes-dashboard-token",
            hermes_job_hunter_cwd=tmp_path,
            hermes_default_model_id="gpt-5.6-sol-900k",
            hermes_default_reasoning_effort="medium",
            codex_app_server_path=tmp_path / "(FAKE)-codex-app-server",
            codex_home_path=tmp_path / "(FAKE)-codex-home",
        ),
        agent_gateway_factory=ReadyGatewayFactory(),  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.get("/v1/connected-agents", headers=auth())
        assert response.status_code == 200
        hermes = response.json()["agents"][0]
        assert hermes["provider"] == "hermes"
        assert hermes["lifecycle"] == "connected"
        assert hermes["default_model_id"] == "gpt-5.6-sol-900k"
        assert hermes["default_reasoning_effort"] == "medium"
        assert hermes["health"]["provider_available"] is True
        models = client.get(f"/v1/connected-agents/{hermes['id']}/models", headers=auth())
        assert models.status_code == 200
        assert models.json() == {
            "live": True,
            "models": [
                {
                    "model_id": "gpt-5.6-sol-900k",
                    "display_name": "gpt-5.6-sol-900k",
                    "reasoning_efforts": ["medium"],
                }
            ],
        }


def test_connected_agent_api_resolves_default_and_provisions_immutable_chat(tmp_path):
    app, registry, state_store, gateway_factory, _ = setup_app(tmp_path)

    with TestClient(app) as client:
        agents = client.get("/v1/connected-agents", headers=auth())
        assert agents.status_code == 200
        assert agents.json()["profile_id"] == PROFILE_ID
        assert agents.json()["default_connected_agent_id"] == AGENT_ID
        assert agents.json()["agents"][0]["id"] == AGENT_ID
        assert agents.json()["agents"][0]["health"]["state"] == "connected"

        revision = registry.load().registry_revision
        payload = chat_payload(revision, "(FAKE)-chat-create-1")
        created = client.post("/v1/conversations", headers=auth(), json=payload)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["binding"] == {
            "connected_agent_id": AGENT_ID,
            "provider": "hermes",
            "model_id": "(FAKE)-model-stable",
            "reasoning_effort": "medium",
            "binding_state": "sealed",
        }
        assert body["availability"]["state"] == "ready"
        assert body["agent"]["display_name"] == "(FAKE) Hermes"
        summaries = client.get("/v1/conversations", headers=auth())
        assert summaries.status_code == 200
        summary = next(
            item
            for item in summaries.json()["conversations"]
            if item["conversation_id"] == body["conversation_id"]
        )
        assert summary["binding"] == body["binding"]
        assert summary["availability"] == body["availability"]
        assert state_store.conversation_store(body["conversation_id"]).binding() == {
            "connected_agent_id": AGENT_ID,
            "provider": "hermes",
            "model_id": "(FAKE)-model-stable",
            "reasoning_effort": "medium",
            "binding_state": "sealed",
            "provider_session_id": f"(FAKE)-session-{body['conversation_id']}",
            "connection_account_fingerprint": None,
            "creation_state": "ready",
            "lock_reason": None,
        }
        assert gateway_factory.gateways[body["conversation_id"]].started is True

        replay = client.post("/v1/conversations", headers=auth(), json=payload)
        assert replay.status_code == 200
        assert replay.json()["conversation_id"] == body["conversation_id"]


def test_chat_creation_rejects_stale_revision_and_unsupported_model(tmp_path):
    app, registry, state_store, _, _ = setup_app(tmp_path)
    with TestClient(app) as client:
        revision = registry.load().registry_revision
        stale = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(revision - 1, "(FAKE)-stale-chat"),
        )
        unsupported = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(
                revision,
                "(FAKE)-unsupported-chat",
                model_id="(FAKE)-missing-model",
                reasoning_effort="medium",
            ),
        )

    assert stale.status_code == 409
    assert stale.json()["code"] == "PROFILE_REVISION_CONFLICT"
    assert unsupported.status_code == 409
    assert unsupported.json()["code"] == "MODEL_UNAVAILABLE"
    assert state_store.connected_agent_chat_impact(AGENT_ID)["active_chats"] == 0


def test_definitive_failure_removes_provisional_chat_and_replays_failure(tmp_path):
    app, registry, state_store, _, _ = setup_app(tmp_path, gateway_type=DefinitiveFailureGateway)
    with TestClient(app) as client:
        payload = chat_payload(registry.load().registry_revision, "(FAKE)-definitive-fail")
        failed = client.post("/v1/conversations", headers=auth(), json=payload)
        replay = client.post("/v1/conversations", headers=auth(), json=payload)

    assert failed.status_code == 503
    assert failed.json()["code"] == "AGENT_PROVIDER_UNAVAILABLE"
    assert replay.status_code == 503
    assert replay.json()["code"] == "AGENT_PROVIDER_UNAVAILABLE"
    assert state_store.connected_agent_chat_impact(AGENT_ID) == {
        "active_chats": 0,
        "locked_chats": 0,
    }


def test_ambiguous_failure_retains_locked_recovery_record(tmp_path):
    app, registry, state_store, _, settings = setup_app(
        tmp_path, gateway_type=AmbiguousFailureGateway
    )
    with TestClient(app) as client:
        created = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(registry.load().registry_revision, "(FAKE)-ambiguous-chat"),
        )

    assert created.status_code == 201
    assert created.json()["availability"] == {
        "state": "locked",
        "reason": "RECOVERY_REQUIRED",
    }
    binding = state_store.conversation_store(created.json()["conversation_id"]).binding()
    assert binding["creation_state"] == "locked"
    assert binding["lock_reason"] == "RECOVERY_REQUIRED"
    assert state_store.connected_agent_chat_impact(AGENT_ID)["locked_chats"] == 1

    restarted_factory = ReadyGatewayFactory()
    restarted_app = create_app(
        settings,
        state_store=state_store,
        agent_gateway_factory=restarted_factory,
        connected_agent_runtime=ReadyRuntimeControl(),
    )
    with TestClient(restarted_app) as client:
        readable = client.get(
            f"/v1/conversations/{created.json()['conversation_id']}", headers=auth()
        )
    assert readable.status_code == 200
    assert restarted_factory.gateways[created.json()["conversation_id"]].session_requests == 0


def test_post_acceptance_persistence_failure_keeps_recoverable_chat(tmp_path, monkeypatch):
    app, registry, state_store, _, _ = setup_app(tmp_path)

    def fail_after_acceptance(self, provider_session_id):
        del self, provider_session_id
        raise OSError("(FAKE) persistence failed after provider acceptance")

    monkeypatch.setattr(ConversationStore, "complete_provisioning", fail_after_acceptance)
    with TestClient(app) as client:
        created = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(
                registry.load().registry_revision,
                "(FAKE)-post-acceptance-persistence-failure",
            ),
        )

    assert created.status_code == 201
    assert created.json()["availability"] == {
        "state": "locked",
        "reason": "RECOVERY_REQUIRED",
    }
    assert state_store.connected_agent_chat_impact(AGENT_ID) == {
        "active_chats": 1,
        "locked_chats": 1,
    }


def test_profile_wide_five_chat_limit_and_archive_release(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    with TestClient(app) as client:
        revision = registry.load().registry_revision
        created_ids = []
        for index in range(4):
            created = client.post(
                "/v1/conversations",
                headers=auth(),
                json=chat_payload(revision, f"(FAKE)-limit-chat-{index}"),
            )
            assert created.status_code == 201
            created_ids.append(created.json()["conversation_id"])
        blocked = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(revision, "(FAKE)-limit-chat-blocked"),
        )
        archived = client.delete(f"/v1/conversations/{created_ids[0]}", headers=auth())
        replacement = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(revision, "(FAKE)-limit-chat-replacement"),
        )

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CHAT_LIMIT_REACHED"
    assert archived.status_code == 204
    assert replacement.status_code == 201


def test_account_replacement_durably_locks_prior_agent_chats(tmp_path):
    app, registry, state_store, _, _ = setup_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(
                registry.load().registry_revision,
                "(FAKE)-account-replacement-chat",
            ),
        )
        assert created.status_code == 201, created.text
        assert state_store.lock_connected_agent_chats(
            AGENT_ID, "AUTH_ACCOUNT_REPLACEMENT_REQUIRED"
        ) == 1
        summaries = client.get("/v1/conversations", headers=auth())

    locked = next(
        item
        for item in summaries.json()["conversations"]
        if item["conversation_id"] == created.json()["conversation_id"]
    )
    assert locked["availability"] == {
        "state": "locked",
        "reason": "AUTH_ACCOUNT_REPLACEMENT_REQUIRED",
    }


def test_agent_api_update_models_impact_default_and_disconnect(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    with TestClient(app) as client:
        models = client.get(f"/v1/connected-agents/{AGENT_ID}/models", headers=auth())
        impact = client.get(f"/v1/connected-agents/{AGENT_ID}/disconnect-impact", headers=auth())
        revision = registry.load().registry_revision
        created = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(revision, "(FAKE)-disconnect-chat"),
        )
        assert created.status_code == 201, created.text
        delivered = client.post(
            f"/v1/conversations/{created.json()['conversation_id']}/messages",
            headers=auth(),
            json={
                "text": "(FAKE) ambiguous delivery",
                "idempotency_key": "(FAKE)-ambiguous-send",
            },
        )
        assert delivered.status_code == 201, delivered.text
        updated = client.patch(
            f"/v1/connected-agents/{AGENT_ID}",
            headers=auth(),
            json={
                "display_name": "(FAKE) Renamed Hermes",
                "avatar_id": "hermes-renamed",
                "default_model_id": "(FAKE)-model-stable",
                "default_reasoning_effort": "high",
                "expected_registry_revision": revision,
                "idempotency_key": "(FAKE)-rename-agent",
            },
        )
        revision = registry.load().registry_revision
        cleared = client.put(
            f"/v1/installation-profiles/{PROFILE_ID}/default-agent",
            headers=auth(),
            json={
                "connected_agent_id": None,
                "expected_profile_revision": revision,
                "expected_agent_registry_revision": revision,
                "idempotency_key": "(FAKE)-clear-default",
            },
        )
        revision = registry.load().registry_revision
        disconnected = client.post(
            f"/v1/connected-agents/{AGENT_ID}/disconnect",
            headers=auth(),
            json={
                "confirmation_token": AGENT_ID,
                "expected_registry_revision": revision,
                "idempotency_key": "(FAKE)-disconnect-agent",
            },
        )
        summaries = client.get("/v1/conversations", headers=auth())
        replayed = client.post(
            f"/v1/conversations/{created.json()['conversation_id']}/messages",
            headers=auth(),
            json={
                "text": "(FAKE) ambiguous delivery",
                "idempotency_key": "(FAKE)-ambiguous-send",
            },
        )
        blocked_send = client.post(
            f"/v1/conversations/{created.json()['conversation_id']}/messages",
            headers=auth(),
            json={
                "text": "This must stay read-only",
                "idempotency_key": "(FAKE)-locked-chat-send",
            },
        )

    assert models.status_code == 200
    assert models.json()["models"][0]["model_id"] == "(FAKE)-model-stable"
    assert impact.json()["default_profile_ids"] == [PROFILE_ID]
    assert updated.json()["display_name"] == "(FAKE) Renamed Hermes"
    assert cleared.json()["default_connected_agent_id"] is None
    assert disconnected.json()["lifecycle"] == "disconnected"
    assert replayed.status_code == 201
    assert replayed.json() == {**delivered.json(), "created": False}
    disconnected_chat = next(
        item
        for item in summaries.json()["conversations"]
        if item["conversation_id"] == created.json()["conversation_id"]
    )
    assert disconnected_chat["availability"] == {
        "state": "locked",
        "reason": "AGENT_DISCONNECTED",
    }
    assert blocked_send.status_code == 409
    assert blocked_send.json()["code"] == "AGENT_DISCONNECTED"
    persisted = next(item for item in registry.load().connected_agents if item.id == AGENT_ID)
    assert persisted.connection_config is None
    assert persisted.credential_reference is None


def test_second_codex_identity_returns_cardinality_conflict(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    with TestClient(app) as client:
        revision = registry.load().registry_revision
        first = client.post(
            "/v1/connected-agents",
            headers=auth(),
            json={
                "provider": "codex",
                "display_name": "(FAKE) Codex",
                "avatar_id": "codex",
                "default_model_id": "(FAKE)-model-stable",
                "default_reasoning_effort": "medium",
                "connection_config": {"runtime_namespace": "jobos-test"},
                "expected_registry_revision": revision,
                "idempotency_key": "(FAKE)-create-codex-one",
            },
        )
        revision = registry.load().registry_revision
        second = client.post(
            "/v1/connected-agents",
            headers=auth(),
            json={
                "provider": "codex",
                "display_name": "(FAKE) Second Codex",
                "avatar_id": "codex-two",
                "default_model_id": "(FAKE)-model-stable",
                "default_reasoning_effort": "medium",
                "connection_config": {"runtime_namespace": "jobos-test-two"},
                "expected_registry_revision": revision,
                "idempotency_key": "(FAKE)-create-codex-two",
            },
        )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "AGENT_CARDINALITY_CONFLICT"


def test_chat_creation_rejects_missing_and_disconnected_agents(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    missing_id = "jagent_cccccccccccccccccccccccccccccccc"
    with TestClient(app) as client:
        revision = registry.load().registry_revision
        missing = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(
                revision,
                "(FAKE)-missing-agent-chat",
                connected_agent_id=missing_id,
            ),
        )
        registry.disconnect_connected_agent(
            AGENT_ID,
            expected_registry_revision=revision,
            idempotency_key="(FAKE)-direct-disconnect",
            now=NOW,
        )
        revision = registry.load().registry_revision
        disconnected = client.post(
            "/v1/conversations",
            headers=auth(),
            json=chat_payload(revision, "(FAKE)-disconnected-agent-chat"),
        )

    assert missing.status_code == 409
    assert missing.json()["code"] == "AGENT_NOT_CONFIGURED"
    assert disconnected.status_code == 409
    assert disconnected.json()["code"] == "AGENT_DISCONNECTED"


def test_default_agent_replay_survives_later_registry_revision(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    revision = registry.load().registry_revision
    payload = {
        "connected_agent_id": None,
        "expected_profile_revision": revision,
        "expected_agent_registry_revision": revision,
        "idempotency_key": "(FAKE)-default-replay",
    }
    with TestClient(app) as client:
        first = client.put(
            f"/v1/installation-profiles/{PROFILE_ID}/default-agent",
            headers=auth(),
            json=payload,
        )
        registry.update_connected_agent(
            AGENT_ID,
            display_name="(FAKE) Later Rename",
            avatar_id="hermes",
            default_model_id="(FAKE)-model-stable",
            default_reasoning_effort="medium",
            expected_registry_revision=registry.load().registry_revision,
            idempotency_key="(FAKE)-later-agent-change",
            now=NOW,
        )
        replay = client.put(
            f"/v1/installation-profiles/{PROFILE_ID}/default-agent",
            headers=auth(),
            json=payload,
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["default_connected_agent_id"] is None


def test_stale_disconnect_does_not_touch_provider_runtime(tmp_path):
    runtime_control = ReadyRuntimeControl()
    app, registry, _, _, _ = setup_app(tmp_path, runtime_control=runtime_control)
    stale_revision = registry.load().registry_revision
    registry.update_connected_agent(
        AGENT_ID,
        display_name="(FAKE) Newer Name",
        avatar_id="hermes",
        default_model_id="(FAKE)-model-stable",
        default_reasoning_effort="medium",
        expected_registry_revision=stale_revision,
        idempotency_key="(FAKE)-make-disconnect-stale",
        now=NOW,
    )
    with TestClient(app) as client:
        response = client.post(
            f"/v1/connected-agents/{AGENT_ID}/disconnect",
            headers=auth(),
            json={
                "confirmation_token": AGENT_ID,
                "expected_registry_revision": stale_revision,
                "idempotency_key": "(FAKE)-stale-disconnect",
            },
        )

    assert response.status_code == 409
    assert runtime_control.disconnect_calls == 0
    persisted = next(item for item in registry.load().connected_agents if item.id == AGENT_ID)
    assert persisted.lifecycle == "connected"


def test_invalid_connected_agent_configuration_returns_typed_422(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/v1/connected-agents",
            headers=auth(),
            json={
                "provider": "hermes",
                "display_name": "(FAKE) Invalid Hermes",
                "avatar_id": "invalid-hermes",
                "default_model_id": None,
                "default_reasoning_effort": "medium",
                "connection_config": {"endpoint_url": "http://127.0.0.1:9330"},
                "expected_registry_revision": registry.load().registry_revision,
                "idempotency_key": "(FAKE)-invalid-agent-config",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_failed"


def test_chat_replay_survives_later_registry_revision(tmp_path):
    app, registry, _, gateway_factory, _ = setup_app(tmp_path)
    revision = registry.load().registry_revision
    payload = chat_payload(revision, "(FAKE)-chat-replay-after-revision")
    with TestClient(app) as client:
        first = client.post("/v1/conversations", headers=auth(), json=payload)
        assert first.status_code == 201
        conversation_id = first.json()["conversation_id"]
        registry.update_connected_agent(
            AGENT_ID,
            display_name="(FAKE) Renamed After Chat",
            avatar_id="hermes",
            default_model_id="(FAKE)-model-stable",
            default_reasoning_effort="medium",
            expected_registry_revision=registry.load().registry_revision,
            idempotency_key="(FAKE)-change-after-chat",
            now=NOW,
        )
        replay = client.post("/v1/conversations", headers=auth(), json=payload)

    assert replay.status_code == 200
    assert replay.json()["conversation_id"] == conversation_id
    assert gateway_factory.gateways[conversation_id].session_requests == 1


def test_agent_update_rejects_unavailable_default_model(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    with TestClient(app) as client:
        response = client.patch(
            f"/v1/connected-agents/{AGENT_ID}",
            headers=auth(),
            json={
                "display_name": "(FAKE) Hermes",
                "avatar_id": "hermes",
                "default_model_id": "(FAKE)-missing-model",
                "default_reasoning_effort": "medium",
                "connection_config": {"endpoint_url": "http://127.0.0.1:9220"},
                "expected_registry_revision": registry.load().registry_revision,
                "idempotency_key": "(FAKE)-unsupported-default",
            },
        )

    assert response.status_code == 409
    assert response.json()["code"] == "MODEL_UNAVAILABLE"
    persisted = next(item for item in registry.load().connected_agents if item.id == AGENT_ID)
    assert persisted.default_model_id == "(FAKE)-model-stable"


def test_disconnect_impact_aggregates_all_profile_stores(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    second_profile_id = "jprof_cccccccccccccccccccccccccccccccc"
    second_state_path = managed_profile_paths(
        registry.installation_root, second_profile_id
    ).state_db_path
    current = registry.load()
    registry.write(
        current.model_copy(
            update={
                "profiles": (
                    *current.profiles,
                    InstallationProfileRecord(
                        profile_id=second_profile_id,
                        display_name="(FAKE) Second Profile",
                        storage_mode="managed",
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                )
            }
        )
    )
    second_state_path.parent.mkdir(parents=True, exist_ok=True)
    second_store = JobOsStateStore(second_state_path)
    second_store.initialize(
        owner_device_id="second-device", installation_profile_id=second_profile_id
    )
    second_store.create_conversation(
        actor_id="second-device",
        connected_agent_id=AGENT_ID,
        provider="hermes",
        model_id="(FAKE)-model-stable",
        reasoning_effort="medium",
        idempotency_key="(FAKE)-second-profile-chat",
    )

    with TestClient(app) as client:
        impact = client.get(f"/v1/connected-agents/{AGENT_ID}/disconnect-impact", headers=auth())

    assert impact.status_code == 200
    assert impact.json()["active_chats"] == 1
    assert impact.json()["locked_chats"] == 0


def test_agent_configuration_mutations_require_direct_user_principal(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    revision = registry.load().registry_revision
    headers = {**auth(), "X-JobOS-Agent-Id": AGENT_ID}
    with TestClient(app) as client:
        responses = (
            client.post(
                "/v1/connected-agents",
                headers=headers,
                json={
                    "provider": "hermes",
                    "display_name": "(FAKE) Blocked Agent",
                    "avatar_id": "blocked-agent",
                    "expected_registry_revision": revision,
                    "idempotency_key": "(FAKE)-blocked-create",
                },
            ),
            client.patch(
                f"/v1/connected-agents/{AGENT_ID}",
                headers=headers,
                json={
                    "display_name": "(FAKE) Blocked Rename",
                    "avatar_id": "blocked-agent",
                    "expected_registry_revision": revision,
                    "idempotency_key": "(FAKE)-blocked-update",
                },
            ),
            client.post(
                f"/v1/connected-agents/{AGENT_ID}/disconnect",
                headers=headers,
                json={
                    "confirmation_token": AGENT_ID,
                    "expected_registry_revision": revision,
                    "idempotency_key": "(FAKE)-blocked-disconnect",
                },
            ),
            client.put(
                f"/v1/installation-profiles/{PROFILE_ID}/default-agent",
                headers=headers,
                json={
                    "connected_agent_id": AGENT_ID,
                    "expected_profile_revision": revision,
                    "expected_agent_registry_revision": revision,
                    "idempotency_key": "(FAKE)-blocked-default",
                },
            ),
        )

    assert [response.status_code for response in responses] == [403, 403, 403, 403]
    assert registry.load().registry_revision == revision


def test_disconnected_agent_models_do_not_probe_runtime(tmp_path):
    runtime_control = ReadyRuntimeControl()
    app, registry, _, _, _ = setup_app(tmp_path, runtime_control=runtime_control)
    registry.disconnect_connected_agent(
        AGENT_ID,
        expected_registry_revision=registry.load().registry_revision,
        idempotency_key="(FAKE)-disconnect-before-models",
        now=NOW,
    )

    with TestClient(app) as client:
        response = client.get(f"/v1/connected-agents/{AGENT_ID}/models", headers=auth())

    assert response.status_code == 200
    assert response.json() == {"live": False, "models": []}
    assert runtime_control.model_calls == 0


def test_auth_routes_require_direct_user_and_return_safe_transaction(tmp_path):
    broker = ReadyAuthBroker()
    app, _, _, _, _ = setup_app(tmp_path, auth_broker=broker)
    transaction_id = broker.transaction.transaction_id
    agent_headers = {**auth(), "X-JobOS-Agent-Id": AGENT_ID}

    with TestClient(app) as client:
        unauthenticated = client.post(
            f"/v1/connected-agents/{AGENT_ID}/auth/device-code",
            json={"mode": "connect", "expected_account_fingerprint": None},
        )
        blocked_agent = client.post(
            f"/v1/connected-agents/{AGENT_ID}/auth/device-code",
            headers=agent_headers,
            json={"mode": "connect", "expected_account_fingerprint": None},
        )
        started = client.post(
            f"/v1/connected-agents/{AGENT_ID}/auth/device-code",
            headers=auth(),
            json={"mode": "connect", "expected_account_fingerprint": None},
        )
        read = client.get(f"/v1/connected-agent-auth/{transaction_id}", headers=auth())
        blocked_agent_read = client.get(
            f"/v1/connected-agent-auth/{transaction_id}", headers=agent_headers
        )
        cancel = client.delete(
            f"/v1/connected-agent-auth/{transaction_id}", headers=auth()
        )

    assert unauthenticated.status_code == 401
    assert blocked_agent.status_code == 403
    assert started.status_code == 200
    assert started.json()["verification_url"] == "https://auth.example.test/device"
    assert started.json()["user_code"] == "SAFE-CODE"
    assert read.status_code == 200
    assert blocked_agent_read.status_code == 403
    assert cancel.status_code == 200
    assert cancel.json()["user_code"] is None
    assert broker.started == 1
    assert broker.cancelled == 1

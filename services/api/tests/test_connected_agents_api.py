from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from jobos_api.agent_gateway import (
    AmbiguousDeliveryError,
    DefinitiveSessionCreationError,
    GatewayEvent,
)
from jobos_api.app import create_app
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


def setup_app(tmp_path: Path, *, gateway_type=ReadyGateway, runtime_control=None):
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


def test_connected_agent_api_resolves_default_and_provisions_immutable_chat(tmp_path):
    app, registry, state_store, gateway_factory, _ = setup_app(tmp_path)

    with TestClient(app) as client:
        agents = client.get("/v1/connected-agents", headers=auth())
        assert agents.status_code == 200
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


def test_agent_api_update_models_impact_default_and_disconnect(tmp_path):
    app, registry, _, _, _ = setup_app(tmp_path)
    with TestClient(app) as client:
        models = client.get(f"/v1/connected-agents/{AGENT_ID}/models", headers=auth())
        impact = client.get(f"/v1/connected-agents/{AGENT_ID}/disconnect-impact", headers=auth())
        revision = registry.load().registry_revision
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

    assert models.status_code == 200
    assert models.json()["models"][0]["model_id"] == "(FAKE)-model-stable"
    assert impact.json()["default_profile_ids"] == [PROFILE_ID]
    assert updated.json()["display_name"] == "(FAKE) Renamed Hermes"
    assert cleared.json()["default_connected_agent_id"] is None
    assert disconnected.json()["lifecycle"] == "disconnected"
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

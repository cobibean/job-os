"""Synthetic routing regressions through app composition, registry, router and RPC adapter."""
import asyncio

import pytest
from jobos_api.agent_gateway import AgentContext, AgentRuntimeRouter
from jobos_api.app import create_app
from jobos_api.hermes_adapter import HermesGatewayFactory
from jobos_api.installation_profiles import (
    AnchoredRuntime,
    HermesConnectionConfiguration,
    InstallationProfileRegistry,
)
from jobos_api.settings import Settings
from pydantic import ValidationError
from test_connected_agent_persistence import registry_data
from test_hermes_adapter import FakeConnector, FakeWebSocket, result

PROFILE_ID = "jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AGENT_ID = "jagent_cccccccccccccccccccccccccccccccc"
BINDING = {
    "connected_agent_id": AGENT_ID,
    "provider": "hermes",
    "model_id": "gpt-6-astra",
    "reasoning_effort": "medium",
    "binding_state": "sealed",
}


def composed_runtime(tmp_path, monkeypatch):
    import jobos_api.app as app_module

    routers = []

    def capture_router(*args, **kwargs):
        router = AgentRuntimeRouter(*args, **kwargs)
        routers.append(router)
        return router

    monkeypatch.setattr(app_module, "AgentRuntimeRouter", capture_router)
    old_cwd = tmp_path / "legacy"
    old_cwd.mkdir()
    settings = Settings(
        device_token="(FAKE)-device-token",
        mcp_token="(FAKE)-mcp-token",
        state_db_path=tmp_path / "state.db",
        jobs_db_path=tmp_path / "jobs.db",
        local_artifact_root=tmp_path / "artifacts",
        installation_registry_path=tmp_path / "registry.json",
        installation_profile_id=PROFILE_ID,
        installation_profile_name="(FAKE) Personal",
        hermes_dashboard_url="ws://127.0.0.1:9120/api/ws",
        hermes_dashboard_token="(FAKE)-trusted-dashboard-token",
        hermes_job_hunter_cwd=old_cwd,
        hermes_default_model_id="gpt-6-astra",
        hermes_default_reasoning_effort="medium",
        codex_app_server_path=tmp_path / "(FAKE)-unavailable-codex",
        codex_home_path=tmp_path / "codex",
    )
    registry = InstallationProfileRegistry(settings.installation_registry_path)
    registry.write(registry_data(tmp_path, AnchoredRuntime(
        job_provider="sqlite", artifact_provider="local",
        state_db_path=settings.state_db_path, jobs_db_path=settings.resolved_jobs_db_path(),
        local_artifact_root=settings.resolved_local_artifact_root(),
    )).model_copy(update={"schema_version": 1}))
    create_app(settings)
    return routers[0], registry, settings


def register_agent(registry, config):
    registry.create_connected_agent(
        provider="hermes", display_name="(FAKE) Fresh Hermes", avatar_id="hermes",
        default_model_id="gpt-6-astra", default_reasoning_effort="medium",
        connection_config=config, credential_reference=None,
        expected_registry_revision=registry.load().registry_revision,
        idempotency_key="(FAKE)-register-fresh-runtime", agent_id=AGENT_ID,
    )


def test_app_routes_identity_created_after_startup_and_keeps_legacy_data_roots(
    tmp_path, monkeypatch,
):
    router, registry, settings = composed_runtime(tmp_path, monkeypatch)
    fresh_cwd = tmp_path / "fresh"
    fresh_cwd.mkdir()
    register_agent(registry, {
        # This must never receive the trusted dashboard token.
        "endpoint_url": "https://untrusted.example.test",
        "runtime_profile": "job-hunter-2", "runtime_cwd": str(fresh_cwd),
    })
    routed = router.create("conv_fresh", BINDING)
    gateway = routed._gateway
    assert gateway._profile == "job-hunter-2"
    assert gateway._cwd == fresh_cwd
    assert gateway._url == settings.hermes_dashboard_url
    assert gateway._token == settings.hermes_dashboard_token
    assert settings.hermes_job_hunter_cwd == tmp_path / "legacy"
    assert settings.jobs_db_path == tmp_path / "jobs.db"
    assert settings.local_artifact_root == tmp_path / "artifacts"
    legacy = registry.load().connected_agents[0]
    assert legacy.id != AGENT_ID
    legacy_gateway = router.create("conv_legacy", {**BINDING, "connected_agent_id": legacy.id})
    assert legacy_gateway._gateway._profile == "job-hunter"
    assert legacy_gateway._gateway._cwd == tmp_path / "legacy"

    async def scenario():
        def responder(request):
            if request["method"] in {"session.create", "session.resume"}:
                return [result(request, {
                    "stored_session_id": "fresh-stored", "session_id": "fresh-live",
                    "info": {"profile_name": "job-hunter-2", "cwd": str(fresh_cwd)},
                })]
            if request["method"] == "config.get":
                return [result(request, {"value": "medium"})]
            return [result(request, {"status": "streaming"})]

        socket = FakeWebSocket(responder)
        connector = FakeConnector(socket)
        gateway._connector = connector
        try:
            assert await gateway.create_or_resume_conversation(None) == (
                "fresh-stored", "fresh-live",
            )
            context = AgentContext(
                conversation_id="conv_fresh", turn_id="turn_12345678",
                selected_job_id=None, workspace={},
            )
            await gateway.submit_turn("(FAKE) First turn", context)
            await gateway.submit_turn("(FAKE) Second turn", context)
            await gateway.detach_conversation()
            await gateway.create_or_resume_conversation("fresh-stored")
            await gateway.submit_turn("(FAKE) Resumed turn", context)
            created = socket.requests[0]
            assert created["method"] == "session.create"
            assert created["params"]["profile"] == "job-hunter-2"
            assert created["params"]["cwd"] == str(fresh_cwd)
            assert sum(r["method"] == "prompt.submit" for r in socket.requests) == 3
            assert connector.calls[0][0].startswith(settings.hermes_dashboard_url)
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["profile", "cwd"])
@pytest.mark.parametrize("stored", [None, "old-stored"])
def test_new_profile_rejects_foreign_session_before_prompt(tmp_path, mismatch, stored):
    gateway = HermesGatewayFactory(
        url="ws://127.0.0.1:9120/api/ws", token="(FAKE)-token",
        cwd=tmp_path, profile="job-hunter-2", request_timeout=0.01,
    ).create("conv_fresh")

    async def scenario():
        socket = FakeWebSocket(lambda request: [result(request, {
            "stored_session_id": "old-stored", "session_id": "old-live",
            "info": {
                "profile_name": "job-hunter" if mismatch == "profile" else "job-hunter-2",
                "cwd": str(tmp_path / "other") if mismatch == "cwd" else str(tmp_path),
            },
        })])
        gateway._connector = FakeConnector(socket)
        try:
            with pytest.raises(RuntimeError):
                await gateway.create_or_resume_conversation(stored)
                await gateway.submit_turn("(FAKE) Must not submit", AgentContext(
                    conversation_id="conv_fresh", turn_id="turn_12345678",
                    selected_job_id=None, workspace={},
                ))
            assert not any(r["method"] == "prompt.submit" for r in socket.requests)
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["missing", "disconnected", "missing_config"])
def test_app_runtime_fails_closed_for_unavailable_identity(tmp_path, monkeypatch, state):
    router, registry, _ = composed_runtime(tmp_path, monkeypatch)
    if state != "missing":
        register_agent(registry, None if state == "missing_config" else {
            "endpoint_url": "http://127.0.0.1:9120",
        })
        if state == "disconnected":
            registry.disconnect_connected_agent(
                AGENT_ID, expected_registry_revision=registry.load().registry_revision,
                idempotency_key="(FAKE)-disconnect",
            )
    with pytest.raises(ConnectionError):
        router.create("conv_fresh", BINDING)


@pytest.mark.parametrize("cwd", [None, "relative", "~/workspace", "missing", "file"])
def test_nonlegacy_profile_requires_explicit_existing_absolute_cwd(tmp_path, cwd):
    file = tmp_path / "file"
    file.write_text("(FAKE)")
    value = str(tmp_path / cwd) if cwd in {"missing", "file"} else cwd
    with pytest.raises(ValidationError):
        HermesConnectionConfiguration(
            endpoint_url="http://127.0.0.1:9120", runtime_profile="job-hunter-2",
            runtime_cwd=value,
        )


def test_nonlegacy_profile_cannot_inherit_legacy_cwd_when_field_is_omitted():
    with pytest.raises(ValidationError, match="explicit working directory"):
        HermesConnectionConfiguration(
            endpoint_url="http://127.0.0.1:9120", runtime_profile="job-hunter-2",
        )


def test_removed_runtime_directory_fails_closed(tmp_path, monkeypatch):
    router, registry, _ = composed_runtime(tmp_path, monkeypatch)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    register_agent(registry, {
        "endpoint_url": "http://127.0.0.1:9120",
        "runtime_profile": "job-hunter-2", "runtime_cwd": str(fresh),
    })
    fresh.rmdir()
    with pytest.raises(RuntimeError):
        router.create("conv_fresh", BINDING)


def test_legacy_configuration_and_factory_defaults_remain_compatible(tmp_path):
    config = HermesConnectionConfiguration(endpoint_url="http://127.0.0.1:9120")
    assert config.runtime_profile is None
    assert config.runtime_cwd is None
    gateway = HermesGatewayFactory(
        url="ws://127.0.0.1:9120/api/ws", token="(FAKE)-token", cwd=tmp_path,
    ).create("conv_legacy")
    assert gateway._profile == "job-hunter"
    assert gateway._cwd == tmp_path


@pytest.mark.parametrize("missing", ["cwd", "profile_name"])
def test_new_profile_resume_after_app_restart_refreshes_exact_session_scope(
    tmp_path, monkeypatch, missing,
):
    _, registry, settings = composed_runtime(tmp_path, monkeypatch)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    register_agent(registry, {
        "endpoint_url": "http://127.0.0.1:9120",
        "runtime_profile": "job-hunter-2", "runtime_cwd": str(fresh),
    })
    # Recompose the real app against the persisted registry; no startup snapshot routing.
    import jobos_api.app as app_module

    routers = []

    def capture_router(*args, **kwargs):
        router = AgentRuntimeRouter(*args, **kwargs)
        routers.append(router)
        return router

    monkeypatch.setattr(app_module, "AgentRuntimeRouter", capture_router)
    create_app(settings)
    gateway = routers[0].create("conv_fresh", BINDING)._gateway

    async def scenario():
        def responder(request):
            if request["method"] in {"session.resume", "session.activate"}:
                info = {"profile_name": "job-hunter-2", "cwd": str(fresh)}
                if request["method"] == "session.resume":
                    info.pop(missing)
                    assert request["params"]["profile"] == "job-hunter-2"
                else:
                    assert request["params"]["session_id"] == "fresh-live"
                return [result(request, {
                    "stored_session_id": "fresh-stored", "session_id": "fresh-live",
                    "info": info,
                })]
            if request["method"] == "config.get":
                return [result(request, {"value": "medium"})]
            return [result(request, {"status": "streaming"})]

        socket = FakeWebSocket(responder)
        gateway._connector = FakeConnector(socket)
        try:
            await gateway.create_or_resume_conversation("fresh-stored")
            await gateway.submit_turn("(FAKE) Restart continuation", AgentContext(
                conversation_id="conv_fresh", turn_id="turn_12345678",
                selected_job_id=None, workspace={},
            ))
            assert [r["method"] for r in socket.requests] == [
                "session.resume", "session.activate", "config.get", "prompt.submit",
            ]
        finally:
            await gateway.close()

    asyncio.run(scenario())

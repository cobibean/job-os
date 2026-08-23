from fastapi.testclient import TestClient
from jobos_api.agent_gateway import OfflineAgentGateway
from jobos_api.app import create_app
from jobos_api.installation_profiles import InstallationProfileRegistry
from jobos_api.private_adapters.job_hunter import JobHunterArtifactGateway
from jobos_api.settings import Settings


def registry_backed_settings(tmp_path):
    state_path = tmp_path / "state" / "jobos.db"
    values = {
        "job_provider": "sqlite",
        "artifact_provider": "local",
        "state_db_path": state_path,
        "jobs_db_path": tmp_path / "jobs" / "jobs.db",
        "local_artifact_root": tmp_path / "artifacts",
        "artifact_roots": (),
        "job_hunter_db_path": None,
        "facade_source_path": None,
    }
    registry_path = tmp_path / "installation-profiles.json"
    data = InstallationProfileRegistry(registry_path).load_or_bootstrap(values)
    return Settings(
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        state_db_path=state_path,
        jobs_db_path=tmp_path / "jobs" / "jobs.db",
        local_artifact_root=tmp_path / "artifacts",
        installation_profile_id=data.active_profile_id,
        installation_profile_name=data.profiles[0].display_name,
        installation_registry_path=registry_path,
        profile_registry_revision=data.registry_revision,
    )


class ReadyArtifactFacade:
    def is_available(self):
        return True

    def list_job_artifacts(self, _job_id):
        return []

    def register_artifact(self, _job_id, _artifact_reference):
        return {}

    def publish_document_artifact(self, *_args):
        return {}

    def render_resume(self, *_args):
        return {}


class ExplicitlyUnavailableArtifactFacade(ReadyArtifactFacade):
    def is_available(self):
        return False


class FailingAvailabilityArtifactFacade(ReadyArtifactFacade):
    def is_available(self):
        raise RuntimeError("availability probe failed")


class TruthyAvailabilityArtifactFacade(ReadyArtifactFacade):
    def is_available(self):
        return 1


def test_health_reports_truthful_public_capability_states(tmp_path):
    app = create_app(
        Settings(
            device_token="test-device-token",
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
        )
    )

    with TestClient(app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "jobos-api",
        "version": "0.1.0",
        "state_schema": 31,
        "transport": "local-loopback",
        "agent": "not-configured",
        "artifact_storage": "available",
        "artifact_gateway": "not-configured",
    }


def test_device_session_requires_the_runtime_credential(tmp_path):
    app = create_app(
        Settings(
            device_token="test-device-token",
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
        )
    )

    with TestClient(app) as client:
        rejected = client.get("/v1/device-session")
        accepted = client.get(
            "/v1/device-session",
            headers={"Authorization": "Bearer test-device-token"},
        )

    assert rejected.status_code == 401
    assert rejected.json()["code"] == "http_401"
    assert rejected.json()["message"] == "Device authentication required"
    assert rejected.json()["retryable"] is False
    assert rejected.json()["correlation_id"] == rejected.headers["x-correlation-id"]
    assert accepted.status_code == 200
    assert accepted.json() == {
        "authenticated": True,
        "transport": "local-loopback",
        "desktop": "disconnected",
        "api_version": "0.1.0",
        "installation_profile_id": app.state.installation_profile_id,
        "installation_profile_name": "Personal",
        "profile_registry_revision": 1,
    }


def test_registry_backed_desktop_requests_require_the_exact_profile_header(tmp_path):
    configured = registry_backed_settings(tmp_path)
    app = create_app(configured)
    authorization = {"Authorization": "Bearer test-device-token"}

    with TestClient(app) as client:
        correct = client.get(
            "/v1/jobs",
            headers={
                **authorization,
                "X-JobOS-Profile-Id": configured.installation_profile_id,
            },
        )
        configured.installation_registry_path.unlink()
        missing = client.get("/v1/jobs", headers=authorization)
        stale = client.get(
            "/v1/jobs",
            headers={
                **authorization,
                "X-JobOS-Profile-Id": "jprof_ffffffffffffffffffffffffffffffff",
            },
        )
        health = client.get("/v1/health")
        device_session = client.get("/v1/device-session", headers=authorization)
        mcp = client.get(
            "/v1/conversations",
            headers={
                "Authorization": "Bearer test-mcp-trusted-token",
                "X-JobOS-MCP-Token": "test-mcp-trusted-token",
            },
        )

    assert correct.status_code == 200
    assert missing.status_code == stale.status_code == 409
    assert missing.json()["code"] == stale.json()["code"] == "profile_context_changed"
    assert health.status_code == device_session.status_code == mcp.status_code == 200
    assert "installation_profile_name" not in health.json()


def test_device_session_refreshes_same_process_profile_metadata(tmp_path):
    configured = registry_backed_settings(tmp_path)
    registry = InstallationProfileRegistry(configured.installation_registry_path)
    renamed = registry.rename(
        configured.installation_profile_id,
        "Renamed profile",
        expected_registry_revision=configured.profile_registry_revision,
        idempotency_key="rename-live-profile",
    )
    app = create_app(configured)

    with TestClient(app) as client:
        session = client.get(
            "/v1/device-session",
            headers={"Authorization": "Bearer test-device-token"},
        )

    assert session.status_code == 200
    assert session.json()["installation_profile_id"] == configured.installation_profile_id
    assert session.json()["installation_profile_name"] == "Renamed profile"
    assert session.json()["profile_registry_revision"] == renamed.registry_revision


def test_health_distinguishes_configured_offline_agent_and_private_remote_transport(tmp_path):
    app = create_app(
        Settings(
            device_token="test-device-token",
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
            transport="private-remote",
        ),
        agent_gateway=OfflineAgentGateway(),
    )

    with TestClient(app) as client:
        health = client.get("/v1/health")
        session = client.get(
            "/v1/device-session",
            headers={"Authorization": "Bearer test-device-token"},
        )

    assert health.json()["agent"] == "offline"
    assert health.json()["transport"] == "private-remote"
    assert session.json()["transport"] == "private-remote"
    assert session.json()["desktop"] == "disconnected"


def test_health_distinguishes_local_storage_from_optional_gateway_readiness(tmp_path):
    configured = Settings(
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        state_db_path=tmp_path / "jobos.db",
    )
    unavailable = create_app(configured, artifact_gateway=JobHunterArtifactGateway(object()))
    explicitly_unavailable = create_app(
        configured,
        artifact_gateway=JobHunterArtifactGateway(ExplicitlyUnavailableArtifactFacade()),
    )
    available = create_app(
        configured,
        artifact_gateway=JobHunterArtifactGateway(ReadyArtifactFacade()),
    )
    failed = create_app(
        configured,
        artifact_gateway=JobHunterArtifactGateway(FailingAvailabilityArtifactFacade()),
    )
    non_boolean = create_app(
        configured,
        artifact_gateway=JobHunterArtifactGateway(TruthyAvailabilityArtifactFacade()),
    )

    with TestClient(unavailable) as client:
        unavailable_health = client.get("/v1/health").json()
    with TestClient(explicitly_unavailable) as client:
        explicitly_unavailable_health = client.get("/v1/health").json()
    with TestClient(available) as client:
        available_health = client.get("/v1/health").json()
    with TestClient(failed) as client:
        failed_health = client.get("/v1/health").json()
    with TestClient(non_boolean) as client:
        non_boolean_health = client.get("/v1/health").json()

    assert unavailable_health["artifact_storage"] == "available"
    assert unavailable_health["artifact_gateway"] == "unavailable"
    assert explicitly_unavailable_health["artifact_gateway"] == "unavailable"
    assert available_health["artifact_storage"] == "available"
    assert available_health["artifact_gateway"] == "available"
    assert failed_health["artifact_gateway"] == "unavailable"
    assert non_boolean_health["artifact_gateway"] == "unavailable"


def test_version_and_openapi_describe_the_shared_workspace_contract(tmp_path):
    app = create_app(
        Settings(
            device_token="test-device-token",
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
        )
    )

    with TestClient(app) as client:
        version = client.get("/v1/version")
        openapi = client.get("/openapi.json")

    assert version.status_code == 200
    assert version.json() == {
        "api_version": "0.1.0",
        "contract": "jobos-api-v1",
        "error_schema": "jobos-error-v1",
    }
    assert set(openapi.json()["paths"]) == {
        "/v1/health",
        "/v1/version",
        "/v1/device-session",
        "/v1/installation-profiles",
        "/v1/installation-profiles/{profile_id}",
        "/v1/installation-profiles/{profile_id}/activate",
        "/v1/installation-profiles/switches/{switch_id}",
        "/v1/career-profile",
        "/v1/career-profile/authority",
        "/v1/career-profile/authority/activate",
        "/v1/career-profile/consumer-projection",
        "/v1/career-profile/items",
        "/v1/career-profile/items/{item_id}",
        "/v1/career-profile/items/{item_id}/decision",
        "/v1/career-profile/intent-grants",
        "/v1/career-profile/agents",
        "/v1/career-profile/agents/{agent_id}",
        "/v1/career-profile/agents/{agent_id}/context",
        "/v1/career-profile/agents/{agent_id}/context/preview",
        "/v1/career-profile/agent-edits",
        "/v1/career-profile/agent-edits/batch",
        "/v1/career-profile/agent-search",
        "/v1/career-profile/agent-changes",
        "/v1/career-profile/agent-evidence",
        "/v1/career-profile/agent-evidence/{evidence_id}",
        "/v1/career-profile/proposals",
        "/v1/career-profile/proposals/{proposal_id}/decision",
        "/v1/career-profile/history",
        "/v1/career-profile/history/{revision_id}/undo",
        "/v1/career-profile/evidence",
        "/v1/career-profile/evidence/{evidence_id}",
        "/v1/career-profile/evidence/{evidence_id}/content",
        "/v1/career-profile/evidence/{evidence_id}/erase",
        "/v1/career-profile/reset",
        "/v1/career-profile/export",
        "/v1/career-profile/restore",
        "/v1/career-profile/work-arrangement",
        "/v1/career-profile/work-arrangement/history",
        "/v1/career-profile/work-arrangement/restore",
        "/v1/career-profile/snapshots",
        "/v1/career-profile/snapshots/{snapshot_id}",
        "/v1/jobs",
        "/v1/jobs/order",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/demo",
        "/v1/jobs/{job_id}/description",
        "/v1/jobs/{job_id}/status",
        "/v1/jobs/{job_id}/history",
        "/v1/jobs/{job_id}/artifacts",
        "/v1/jobs/{job_id}/artifacts/refresh",
        "/v1/jobs/{job_id}/artifacts/register",
        "/v1/jobs/{job_id}/artifacts/publish",
        "/v1/jobs/{job_id}/artifacts/{artifact_id}/approve",
        "/v1/artifacts/{artifact_id}/content",
        "/v1/artifacts/{artifact_id}/download",
        "/v1/workspace/jobs",
        "/v1/workspace",
        "/v1/workspace/jobs/selection",
        "/v1/workspace/jobs/sort",
        "/v1/events",
        "/v1/events/stream",
        "/v1/conversations",
        "/v1/conversations/current",
        "/v1/conversations/events/stream",
        "/v1/conversations/{conversation_id}",
        "/v1/conversations/{conversation_id}/workspace/job",
        "/v1/conversations/{conversation_id}/workspace/document",
        "/v1/conversations/{conversation_id}/messages",
        "/v1/conversations/{conversation_id}/turns/{turn_id}/cancel",
        "/v1/conversations/{conversation_id}/turns/{turn_id}/retry",
        "/v1/desktop/capabilities",
        "/v1/browser/commands",
        "/v1/jobs/{job_id}/artifacts/render",
        "/v1/jobs/{job_id}/editable-documents",
        "/v1/jobs/{job_id}/editable-documents/{document_key}",
        "/v1/jobs/{job_id}/editable-document-outlines/{document_key}",
        "/v1/jobs/{job_id}/document-files",
        "/v1/document-files/{document_id}",
        "/v1/editable-documents/{document_id}",
        "/v1/editable-documents/{document_id}/import",
        "/v1/editable-documents/{document_id}/snapshots",
        "/v1/editable-documents/{document_id}/snapshots/{snapshot_id}/restore",
        "/v1/editable-documents/{document_id}/operations",
        "/v1/editable-documents/{document_id}/publish",
        "/v1/activity",
    }
    schemas = openapi.json()["components"]["schemas"]
    assert set(schemas["HealthResponse"]["required"]) == {
        "status",
        "service",
        "version",
        "state_schema",
        "transport",
        "agent",
        "artifact_storage",
        "artifact_gateway",
    }
    assert set(schemas["VersionResponse"]["required"]) == {
        "api_version",
        "contract",
        "error_schema",
    }
    assert set(schemas["DeviceSessionResponse"]["required"]) == {
        "authenticated",
        "transport",
        "desktop",
        "api_version",
        "installation_profile_id",
        "installation_profile_name",
        "profile_registry_revision",
    }
    assert set(schemas["ApiErrorResponse"]["required"]) == {
        "error_schema",
        "code",
        "message",
        "retryable",
        "correlation_id",
    }
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    paths = openapi.json()["paths"]
    assert set(paths["/v1/conversations/current"]) == {"get"}
    assert paths["/v1/conversations/current"]["get"]["deprecated"] is True
    assert not {
        "/v1/conversations/current/reset",
        "/v1/conversations/current/messages",
        "/v1/conversations/current/turns/{turn_id}/cancel",
        "/v1/conversations/current/turns/{turn_id}/retry",
        "/v1/conversations/current/events/stream",
    } & paths.keys()
    assert set(paths["/v1/health"]["get"]["responses"]) == {"200", "500", "503"}
    assert set(paths["/v1/version"]["get"]["responses"]) == {"200", "500"}
    assert set(paths["/v1/device-session"]["get"]["responses"]) == {
        "200",
        "401",
        "422",
        "500",
    }
    assert set(paths["/v1/artifacts/{artifact_id}/content"]["get"]["responses"]) == {
        "200",
        "401",
        "404",
        "409",
        "415",
        "422",
        "500",
        "503",
    }
    documented_errors = 0
    for path_item in openapi.json()["paths"].values():
        for operation in path_item.values():
            if operation.get("security"):
                assert {"401", "500"} <= set(operation["responses"])
            for status, response in operation["responses"].items():
                if int(status) < 400:
                    continue
                documented_errors += 1
                assert response["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ApiErrorResponse"
                }
    assert documented_errors < 450
    assert set(schemas["JobListItem"]["required"]) == {
        "job_id",
        "company",
        "title",
        "status",
        "status_group",
        "canonical_url",
        "discovered_at",
        "last_seen_at",
    }
    assert set(schemas["WorkspaceSnapshotCommand"]["required"]) == {
        "revision",
        "origin",
        "idempotency_key",
        "selected_preset",
        "layouts",
        "selected_job_id",
        "active_center_surface",
    }
    assert "idempotency_key" not in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert "origin" not in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert set(schemas["BrowserTabMetadata"]["required"]) == {"tab_id", "url"}
    assert "browser_tabs" in schemas["WorkspaceSnapshotCommand"]["properties"]
    assert "active_browser_tab_id" in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert "active_artifact_id" in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert "active_artifact_page" in schemas["WorkspaceSnapshotResponse"]["properties"]
    assert "active_artifact_zoom" in schemas["WorkspaceSnapshotResponse"]["properties"]
    for field in (
        "active_top_level_workspace",
        "browse_mode",
        "browse_focus_job_id",
        "browse_query",
        "browse_status_group",
        "browse_sort_mode",
        "browse_rail_width",
    ):
        assert field in schemas["WorkspaceSnapshotCommand"]["properties"]
        assert field in schemas["WorkspaceSnapshotResponse"]["properties"]

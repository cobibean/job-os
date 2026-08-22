import asyncio
import base64
import hashlib
import hmac
import json
import re
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Literal, ParamSpec, TypeVar, cast
from urllib.parse import quote, unquote
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import (
    Path as PathParameter,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jobos_api import __version__
from jobos_api.activity import ActivityReportRequest, ActivityReportResponse
from jobos_api.agent_gateway import (
    AgentGateway,
    AgentGatewayFactory,
    OfflineAgentGateway,
    OfflineAgentGatewayFactory,
)
from jobos_api.artifact_gateway import ArtifactGateway
from jobos_api.artifact_repository import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    ArtifactRepository,
    ArtifactStorageError,
    ArtifactValidationError,
    ArtifactWrite,
    StoredArtifact,
)
from jobos_api.capabilities import (
    BrowserCommandRequest,
    BrowserCommandResponse,
    CapabilityBroker,
    DesktopCapabilityPresence,
    DesktopUnavailable,
)
from jobos_api.career_profile import (
    CareerProfileIdempotencyConflict,
    CareerProfileRevisionConflict,
    CareerProfileRevisionNotFound,
    CareerProfileSnapshot,
    CareerProfileSnapshotForbidden,
    CareerProfileSnapshotNotFound,
    CareerProfileSnapshotRequest,
    CareerProfileStore,
    WorkArrangementCurrent,
    WorkArrangementHistory,
    WorkArrangementMutation,
    WorkArrangementRestore,
    principal_for_device,
)
from jobos_api.career_profile_collaboration import (
    AgentEditResult,
    AgentProfileEditRequest,
    AgentTrustModeUpdate,
    CareerProfileCollaborationConflict,
    CareerProfileCollaborationStore,
    CareerProfileProposalList,
    ConnectedAgent,
    ConnectedAgentAuthorizationError,
    ConnectedAgentList,
    ProfileHistory,
    ProfileUndoRequest,
    ProposalDecisionRequest,
    ProposalDecisionResult,
    ProposalStatus,
)
from jobos_api.career_profile_complete import (
    CareerProfileCompleteCurrent,
    CareerProfileCompleteStore,
    CareerProfileErasureInProgress,
    CareerProfileErasureResult,
    CareerProfileEvidenceIntegrityError,
    CareerProfileEvidenceNotFound,
    CareerProfileEvidencePathError,
    CareerProfileItemNotFound,
    CareerProfileResetRequest,
    CareerProfileValueError,
    CompleteProfileItemId,
    EvidenceErasureRequest,
    EvidenceImportRequest,
    OpaqueEvidenceId,
    ProfileIntentGrant,
    ProfileIntentGrantRequest,
    ProfileItemMutation,
    ProfileItemRemoval,
    ProfileProposalDecision,
)
from jobos_api.composition import create_job_services
from jobos_api.conversation_manager import ConversationListResponse, ConversationManager
from jobos_api.conversations import (
    ConversationDocumentViewRequest,
    ConversationJobContext,
    ConversationJobContextMutation,
    ConversationResponse,
    CreateConversationRequest,
    RetryTurnRequest,
    SendMessageRequest,
    TurnMutationResponse,
    conversation_event_source,
)
from jobos_api.device_auth import DeviceAuthenticator, DeviceIdentity
from jobos_api.document_files import (
    DOCUMENT_FILE_ID,
    DocumentFileList,
    DocumentFileRecord,
    observed_document_file,
)
from jobos_api.document_operations import (
    apply_operations,
    semantic_outline,
    unresolved_suggestion_count,
)
from jobos_api.documents import (
    ARTIFACT_ID_PATTERN,
    ArtifactApprovalRequest,
    ArtifactPublishRequest,
    ArtifactRefreshRequest,
    ArtifactRegistrationRequest,
    ArtifactTrustError,
    JobArtifactsResponse,
    ResumeRenderRequest,
    VerifiedArtifact,
    artifact_record,
    content_headers,
    read_source_artifact,
    verify_facade_artifacts,
    verify_source_artifact,
)
from jobos_api.editable_documents import (
    LABELS,
    ApplyOperationsRequest,
    CreateEditableDocumentRequest,
    CreateExternalImportRequest,
    CreateRegisteredImportRequest,
    CreateSnapshotRequest,
    DocumentDraftOutline,
    DocumentSettings,
    EditableDocument,
    EditableDocumentList,
    EditableDocumentSnapshot,
    EditableDocumentSnapshotList,
    OperationReceipt,
    PublishEditableDocumentRequest,
    ReplaceFromDocxRequest,
    RestoreSnapshotRequest,
    SaveEditableDocumentRequest,
    as_document,
    as_summary,
    blank_content,
    default_settings,
    validate_content,
)
from jobos_api.hermes_adapter import HermesGatewayFactory
from jobos_api.job_repository import (
    Conflict,
    CreateJobCommand,
    JobRepository,
    JobRepositoryError,
    NotFound,
    Unavailable,
    Validation,
)
from jobos_api.jobs import (
    BrowserJobCreateRequest,
    BrowserJobCreateResponse,
    DemoRemovalRequest,
    JobDescriptionUpdateRequest,
    JobDescriptionUpdateResponse,
    JobDetail,
    JobEventsResponse,
    JobListResponse,
    JobMutationResponse,
    JobSelectionRequest,
    JobSortRequest,
    LeadHistoryResponse,
    ManualOrderRequest,
    SortMode,
    StatusChangeRequest,
    StatusChangeResponse,
    WorkspaceJobsResponse,
    list_jobs,
    normalize_job_detail,
)
from jobos_api.local_artifact_repository import LocalArtifactRepository
from jobos_api.redaction import redact_detail, sanitize_text
from jobos_api.responses import (
    ApiErrorResponse,
    DeviceSessionResponse,
    HealthResponse,
    VersionResponse,
)
from jobos_api.settings import Settings
from jobos_api.state_store import (
    ConversationBusy,
    ConversationLimit,
    ConversationNotFound,
    EditableDocumentConflict,
    EditablePublicationConflict,
    IdempotencyConflict,
    JobOsStateStore,
    WorkspaceRevisionConflict,
)
from jobos_api.synthetic_demo import DEMO_JOB_ID
from jobos_api.workspace import WorkspaceSnapshotCommand, WorkspaceSnapshotResponse

ConversationId = Annotated[
    str, PathParameter(pattern=r"^conv_[A-Za-z0-9_-]{1,128}$", max_length=133)
]
TurnId = Annotated[str, PathParameter(pattern=r"^turn_[A-Za-z0-9_-]{1,128}$", max_length=133)]
P = ParamSpec("P")
R = TypeVar("R")
LOCAL_ARTIFACT_STORAGE_UNAVAILABLE = "Local artifact storage is unavailable"
_ERROR_RESPONSE_DESCRIPTIONS = {
    401: "Device authentication required",
    403: "Operation is not permitted",
    404: "Resource not found",
    409: "Resource state conflict",
    415: "Unsupported media type",
    422: "Request validation failed",
    500: "Internal server error",
    503: "Required capability unavailable",
}
_PUBLIC_ROUTE_NAMES = frozenset({"health", "version"})
_ENDPOINT_ERROR_ROUTES = {
    403: frozenset(
        {
            "browser_command",
            "career_profile_complete_get",
            "career_profile_intent_grant_create",
            "career_profile_proposal_decide",
            "career_profile_evidence_content",
            "career_profile_evidence_import",
            "career_profile_evidence_erase",
            "career_profile_evidence_remove",
            "career_profile_item_create",
            "career_profile_item_remove",
            "career_profile_item_update",
            "career_profile_reset",
            "career_profile_snapshot_create",
            "career_profile_snapshot_get",
            "career_profile_work_arrangement_get",
            "career_profile_work_arrangement_history",
            "career_profile_work_arrangement_put",
            "career_profile_work_arrangement_restore",
            "editable_document_outline",
            "editable_operations",
            "editable_snapshot_create",
            "job_create_from_browser",
            "publish_job_artifact",
            "job_remove_demo",
            "job_update_description",
            "job_update_status",
            "jobs_reorder",
            "workspace_put",
            "workspace_select_job",
            "workspace_sort_jobs",
        }
    ),
    404: frozenset(
        {
            "approve_job_artifact",
            "career_profile_complete_get",
            "career_profile_intent_grant_create",
            "career_profile_proposal_decide",
            "career_profile_evidence_content",
            "career_profile_evidence_import",
            "career_profile_evidence_erase",
            "career_profile_evidence_remove",
            "career_profile_item_create",
            "career_profile_item_remove",
            "career_profile_item_update",
            "career_profile_reset",
            "career_profile_snapshot_get",
            "career_profile_work_arrangement_restore",
            "artifact_content",
            "artifact_download",
            "document_file_get",
            "document_files_list",
            "editable_document_create",
            "editable_document_for_job",
            "editable_document_get",
            "editable_document_import",
            "editable_document_outline",
            "editable_document_publish",
            "editable_document_save",
            "editable_documents_list",
            "editable_operations",
            "editable_snapshot_create",
            "editable_snapshot_list",
            "editable_snapshot_restore",
            "job_artifacts",
            "job_history",
            "job_inspect",
            "publish_job_artifact",
            "job_remove_demo",
            "job_update_description",
            "job_update_status",
            "refresh_job_artifacts",
            "register_job_artifact",
            "render_job_artifact",
            "workspace_select_job",
            "conversation_cancel",
            "conversation_get",
            "conversation_archive",
            "conversation_retry",
        }
    ),
    409: frozenset(
        {
            "approve_job_artifact",
            "career_profile_evidence_content",
            "career_profile_evidence_import",
            "career_profile_evidence_erase",
            "career_profile_evidence_remove",
            "career_profile_proposal_decide",
            "career_profile_item_create",
            "career_profile_item_remove",
            "career_profile_item_update",
            "career_profile_reset",
            "career_profile_work_arrangement_put",
            "career_profile_work_arrangement_restore",
            "artifact_content",
            "artifact_download",
            "browser_command",
            "conversation_create",
            "conversation_archive",
            "conversation_retry",
            "conversation_send",
            "editable_document_create",
            "editable_document_import",
            "editable_document_publish",
            "editable_document_save",
            "editable_operations",
            "editable_snapshot_create",
            "editable_snapshot_restore",
            "job_create_from_browser",
            "publish_job_artifact",
            "job_remove_demo",
            "job_update_description",
            "job_update_status",
            "jobs_reorder",
            "refresh_job_artifacts",
            "register_job_artifact",
            "render_job_artifact",
            "report_activity",
            "workspace_put",
            "workspace_select_job",
            "workspace_sort_jobs",
        }
    ),
    415: frozenset({"artifact_content"}),
    503: frozenset(
        {
            "approve_job_artifact",
            "artifact_content",
            "artifact_download",
            "browser_command",
            "document_files_list",
            "editable_document_create",
            "editable_document_import",
            "editable_document_publish",
            "editable_documents_list",
            "editable_document_outline",
            "editable_document_for_job",
            "health",
            "job_artifacts",
            "job_create_from_browser",
            "job_history",
            "job_inspect",
            "publish_job_artifact",
            "job_remove_demo",
            "job_update_description",
            "job_update_status",
            "jobs_list",
            "jobs_reorder",
            "refresh_job_artifacts",
            "register_job_artifact",
            "render_job_artifact",
            "workspace_select_job",
        }
    ),
}


def _configure_openapi_error_responses(app: FastAPI) -> None:
    """Attach precise envelope responses without repeating them on every route."""
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        statuses = {500}
        if route.name not in _PUBLIC_ROUTE_NAMES:
            statuses.add(401)
        statuses.update(
            status
            for status, route_names in _ENDPOINT_ERROR_ROUTES.items()
            if route.name in route_names
        )
        for status in statuses:
            route.responses.setdefault(
                status,
                {
                    "model": ApiErrorResponse,
                    "description": _ERROR_RESPONSE_DESCRIPTIONS[status],
                },
            )

    default_openapi = app.openapi

    def envelope_openapi() -> dict[str, Any]:
        schema = default_openapi()
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                for status, response in operation.get("responses", {}).items():
                    if int(status) < 400:
                        continue
                    if status == "422":
                        response["description"] = _ERROR_RESPONSE_DESCRIPTIONS[422]
                    response["content"] = {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ApiErrorResponse"}
                        }
                    }
        schemas = schema.get("components", {}).get("schemas", {})
        schemas.pop("HTTPValidationError", None)
        schemas.pop("ValidationError", None)
        return schema

    app.openapi = envelope_openapi  # type: ignore[method-assign]


@dataclass(frozen=True, slots=True)
class PreparedEditableImport:
    artifact_id: str | None
    filename: str | None
    sha256: str
    document_key: Literal["resume", "cover_letter", "references"]
    stored: StoredArtifact | None = None


def create_app(
    settings: Settings,
    *,
    job_repository: JobRepository | None = None,
    artifact_gateway: ArtifactGateway | None = None,
    artifact_repository: ArtifactRepository | None = None,
    agent_gateway: AgentGateway | None = None,
    agent_gateway_factory: AgentGatewayFactory | None = None,
    capability_broker: CapabilityBroker | None = None,
    state_store: JobOsStateStore | None = None,
) -> FastAPI:
    state_store = state_store or JobOsStateStore(settings.state_db_path)
    career_profiles = CareerProfileStore(settings.state_db_path)
    complete_career_profile = CareerProfileCompleteStore(
        settings.state_db_path,
        settings.resolved_evidence_vault_root(),
    )
    career_profile_collaboration = CareerProfileCollaborationStore(
        settings.state_db_path,
        complete_career_profile,
    )
    artifact_gateway_configured = (
        artifact_gateway is not None or settings.artifact_provider == "gateway"
    )
    if job_repository is None or artifact_gateway is None:
        composed_jobs, composed_artifacts = create_job_services(settings)
        job_repository = job_repository or composed_jobs
        artifact_gateway = artifact_gateway or composed_artifacts
    jobs = job_repository
    artifacts = artifact_gateway
    local_artifacts = artifact_repository or LocalArtifactRepository(
        settings.resolved_local_artifact_root()
    )

    def artifact_storage_is_available() -> bool:
        try:
            return local_artifacts.is_available() is True
        except (AttributeError, RuntimeError, OSError):
            return False

    def artifact_gateway_is_available() -> bool:
        try:
            return artifacts.is_available() is True
        except (AttributeError, RuntimeError, OSError):
            return False

    trusted_artifact_roots = settings.resolved_artifact_roots()
    device_authenticator = DeviceAuthenticator(settings.device_credential_registry())
    bearer = HTTPBearer(auto_error=False)
    configured_gateway = agent_gateway is not None or agent_gateway_factory is not None
    gateway_factory = agent_gateway_factory
    if gateway_factory is None and agent_gateway is not None:

        class InjectedGatewayFactory:
            claimed = False

            def create(self, conversation_id: str) -> AgentGateway:
                if not self.claimed:
                    self.claimed = True
                    return agent_gateway
                return OfflineAgentGateway()

        gateway_factory = InjectedGatewayFactory()
    if gateway_factory is None and all(
        (
            settings.hermes_dashboard_url,
            settings.hermes_dashboard_token,
            settings.hermes_job_hunter_cwd,
        )
    ):
        gateway_factory = HermesGatewayFactory(
            url=str(settings.hermes_dashboard_url),
            token=str(settings.hermes_dashboard_token),
            cwd=settings.hermes_job_hunter_cwd,  # type: ignore[arg-type]
            request_timeout=settings.hermes_request_timeout,
        )
        configured_gateway = True
    conversation_manager = ConversationManager(
        state_store,
        gateway_factory or OfflineAgentGatewayFactory(),
        career_profile_principal=(
            principal_for_device(settings.device_id) if settings.career_profile_enabled else None
        ),
    )
    browser_capabilities = capability_broker or CapabilityBroker()
    browser_command_locks: dict[tuple[str, str], asyncio.Lock] = {}
    browser_command_lock_references: dict[tuple[str, str], int] = {}
    browser_command_lock_guard = asyncio.Lock()
    mutation_locks: dict[tuple[str, str], Lock] = {}
    mutation_lock_references: dict[tuple[str, str], int] = {}
    mutation_lock_guard = Lock()

    @contextmanager
    def serialize_mutation(key: tuple[str, str]):
        with mutation_lock_guard:
            lock = mutation_locks.setdefault(key, Lock())
            mutation_lock_references[key] = mutation_lock_references.get(key, 0) + 1
        try:
            with lock:
                yield
        finally:
            with mutation_lock_guard:
                remaining = mutation_lock_references[key] - 1
                if remaining:
                    mutation_lock_references[key] = remaining
                else:
                    mutation_lock_references.pop(key, None)
                    if mutation_locks.get(key) is lock:
                        mutation_locks.pop(key, None)

    def serialized_mutation_route(route: Callable[P, R]) -> Callable[P, R]:
        @wraps(route)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            values = cast(dict[str, object], kwargs)
            identity = values.get("identity")
            command = values.get("command")
            device_id = getattr(identity, "device_id", None)
            idempotency_key = getattr(command, "idempotency_key", None)
            if not isinstance(device_id, str) or not isinstance(idempotency_key, str):
                return route(*args, **kwargs)
            with serialize_mutation((device_id, idempotency_key)):
                return route(*args, **kwargs)

        return wrapped

    @asynccontextmanager
    async def serialize_browser_command(key: tuple[str, str]) -> AsyncIterator[None]:
        async with browser_command_lock_guard:
            lock = browser_command_locks.setdefault(key, asyncio.Lock())
            browser_command_lock_references[key] = browser_command_lock_references.get(key, 0) + 1
        acquired = False
        try:
            await lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            async with browser_command_lock_guard:
                remaining = browser_command_lock_references[key] - 1
                if remaining:
                    browser_command_lock_references[key] = remaining
                else:
                    browser_command_lock_references.pop(key, None)
                    if browser_command_locks.get(key) is lock:
                        browser_command_locks.pop(key, None)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        state_store.initialize(owner_device_id=settings.device_id)
        if settings.career_profile_enabled:
            career_profiles.initialize()
            complete_career_profile.initialize()
            career_profile_collaboration.initialize(
                agent_id=settings.career_profile_agent_id,
                display_name=settings.career_profile_agent_display_name,
                token=settings.resolved_career_profile_agent_token(),
            )
        jobs.initialize()
        await conversation_manager.start()
        try:
            yield
        finally:
            await conversation_manager.close()

    app = FastAPI(
        title="JobOS API",
        version=__version__,
        lifespan=lifespan,
    )

    def correlation_id(request: Request) -> str:
        supplied = request.headers.get("x-correlation-id", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", supplied):
            return supplied
        existing = getattr(request.state, "correlation_id", None)
        if isinstance(existing, str):
            return existing
        generated = uuid4().hex
        request.state.correlation_id = generated
        return generated

    def error_response(
        request: Request,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool | None = None,
        detail: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        safe_message = sanitize_text(message)[:500] or "Request failed"
        safe_detail: object | None = None
        if isinstance(detail, str):
            safe_detail = sanitize_text(detail)[:500]
        elif isinstance(detail, dict):
            safe_detail = redact_detail(detail)
        payload = ApiErrorResponse(
            error_schema="jobos-error-v1",
            code=code,
            message=safe_message,
            retryable=(status_code in {408, 425, 429, 502, 503, 504})
            if retryable is None
            else retryable,
            correlation_id=correlation_id(request),
            detail=safe_detail,
        )
        response_headers = dict(headers or {})
        response_headers["X-Correlation-ID"] = payload.correlation_id
        return JSONResponse(
            status_code=status_code,
            content=payload.model_dump(mode="json"),
            headers=response_headers,
        )

    @app.middleware("http")
    async def attach_correlation_id(request: Request, call_next):
        request.state.correlation_id = correlation_id(request)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail
        code = f"http_{error.status_code}"
        message = "Request failed"
        retryable: bool | None = None
        if isinstance(detail, str):
            message = detail
        elif isinstance(detail, dict):
            candidate_code = detail.get("code")
            candidate_message = detail.get("message")
            candidate_retryable = detail.get("retryable")
            if isinstance(candidate_code, str) and re.fullmatch(
                r"[a-z][a-z0-9_]{0,63}", candidate_code
            ):
                code = candidate_code[:64]
            if isinstance(candidate_message, str):
                message = candidate_message
            if isinstance(candidate_retryable, bool):
                retryable = candidate_retryable
        return error_response(
            request,
            status_code=error.status_code,
            code=code,
            message=message,
            retryable=retryable,
            detail=detail,
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, _: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            request,
            status_code=422,
            code="request_validation_failed",
            message="Request validation failed",
            retryable=False,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _: Exception) -> JSONResponse:
        return error_response(
            request,
            status_code=500,
            code="internal_error",
            message="JobOS could not complete the request",
            retryable=False,
        )

    @app.exception_handler(JobRepositoryError)
    async def job_repository_error_handler(
        request: Request, error: JobRepositoryError
    ) -> JSONResponse:
        status_code = 503
        if isinstance(error, NotFound):
            status_code = 404
        elif isinstance(error, Conflict):
            status_code = 409
        elif isinstance(error, Validation):
            status_code = 422
        elif isinstance(error, Unavailable):
            status_code = 503
        if isinstance(error, Unavailable):
            artifact_unavailable = str(error) == "Artifact provider is unavailable"
            renderer_unavailable = str(error) == "Renderer is unavailable"
            return error_response(
                request,
                status_code=status_code,
                code=(
                    "artifact_provider_unavailable"
                    if artifact_unavailable
                    else "renderer_unavailable"
                    if renderer_unavailable
                    else "repository_unavailable"
                ),
                message=(
                    "Artifact provider is unavailable"
                    if artifact_unavailable
                    else "Renderer is unavailable"
                    if renderer_unavailable
                    else "Job data service is unavailable"
                ),
                retryable=True,
            )
        code = (
            "resource_not_found"
            if isinstance(error, NotFound)
            else "resource_conflict"
            if isinstance(error, Conflict)
            else "request_validation_failed"
        )
        return error_response(
            request,
            status_code=status_code,
            code=code,
            message=str(error),
            retryable=False,
            detail=str(error),
        )

    @app.get(
        "/v1/health",
        tags=["system"],
        responses={503: {"model": ApiErrorResponse}},
    )
    def health() -> HealthResponse:
        state_health = state_store.health()
        return HealthResponse(
            status="ready",
            service="jobos-api",
            version=__version__,
            state_schema=state_health.schema_version,
            transport=settings.transport,
            agent=(
                (
                    "online"
                    if any(
                        service.gateway.connection_state == "online"
                        for service in conversation_manager.services
                    )
                    else "connecting"
                    if any(
                        service.gateway.connection_state == "connecting"
                        for service in conversation_manager.services
                    )
                    else "offline"
                )
                if configured_gateway
                else "not-configured"
            ),
            artifact_storage=("available" if artifact_storage_is_available() else "unavailable"),
            artifact_gateway=(
                "available"
                if artifact_gateway_is_available()
                else "unavailable"
                if artifact_gateway_configured
                else "not-configured"
            ),
        )

    @app.get("/v1/version", tags=["system"])
    def version() -> VersionResponse:
        return VersionResponse(
            api_version=__version__, contract="jobos-api-v1", error_schema="jobos-error-v1"
        )

    def authenticated_device(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> DeviceIdentity:
        return device_authenticator.authenticate(credentials)

    def require_career_profile_owner(identity: DeviceIdentity) -> None:
        """Keep the foundation owner-only until explicit collaborator grants exist."""
        if not settings.career_profile_enabled:
            raise HTTPException(status_code=404, detail="Career Profile is not enabled")
        if identity.device_id != settings.device_id:
            raise HTTPException(
                status_code=403,
                detail="This device is not authorized to access the Career Profile",
            )

    def require_trusted_mcp(
        identity: DeviceIdentity,
        origin: str | None,
        mcp_token: str | None,
    ) -> None:
        if origin == "mcp" and (
            identity.device_id != settings.device_id
            or mcp_token is None
            or not hmac.compare_digest(mcp_token, settings.mcp_token)
        ):
            raise HTTPException(
                status_code=403,
                detail="MCP operations require the trusted local MCP credential",
            )

    def require_direct_career_profile_user(
        identity: DeviceIdentity,
        mcp_token: str | None,
    ) -> str:
        require_career_profile_owner(identity)
        if mcp_token is not None:
            raise HTTPException(
                status_code=403,
                detail="This Career Profile decision belongs to the user",
            )
        return principal_for_device(identity.device_id)

    def authenticated_career_profile_agent(
        identity: DeviceIdentity,
        mcp_token: str | None,
        agent_id: str | None,
        agent_token: str | None,
    ) -> ConnectedAgent:
        require_career_profile_owner(identity)
        require_trusted_mcp(identity, "mcp", mcp_token)
        if agent_id is None or agent_token is None:
            raise HTTPException(
                status_code=403,
                detail="Connected-agent identity is required",
            )
        try:
            return career_profile_collaboration.authenticate(
                agent_id=agent_id,
                token=agent_token,
            )
        except ConnectedAgentAuthorizationError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    @app.middleware("http")
    async def enforce_mcp_conversation_job_scope(
        request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
        """Fence conversation-scoped MCP job/document calls at the API boundary."""
        supplied_mcp_token = request.headers.get("x-jobos-mcp-token")
        conversation_id = request.query_params.get("conversation_id")
        if (
            supplied_mcp_token is None
            or not hmac.compare_digest(supplied_mcp_token, settings.mcp_token)
            or conversation_id is None
        ):
            return await call_next(request)

        job_id: str | None = None
        job_match = re.match(
            r"^/v1/jobs/([^/]+)/(?:status|description|artifacts(?:/|$)|"
            r"editable-documents(?:/|$)|editable-document-outlines(?:/|$))",
            request.url.path,
        )
        if job_match is not None:
            job_id = unquote(job_match.group(1))
        else:
            editable_match = re.match(r"^/v1/editable-documents/([^/]+)", request.url.path)
            if editable_match is not None:
                document = state_store.get_editable_document(unquote(editable_match.group(1)))
                if document is not None and isinstance(document.get("job_id"), str):
                    job_id = cast(str, document["job_id"])
            else:
                artifact_match = re.match(r"^/v1/artifacts/([^/]+)", request.url.path)
                if artifact_match is not None:
                    artifact = state_store.get_document_artifact(unquote(artifact_match.group(1)))
                    if artifact is not None and isinstance(artifact.get("job_id"), str):
                        job_id = cast(str, artifact["job_id"])

        if job_id is None:
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        identity = device_authenticator.authenticate(
            HTTPAuthorizationCredentials(scheme=scheme, credentials=token)
        )
        if identity.device_id != settings.device_id:
            return error_response(
                request,
                status_code=403,
                code="mcp_local_device_required",
                message="MCP operations require the trusted local device credential",
                retryable=False,
            )
        try:
            context = conversation_manager.get(conversation_id).store.snapshot()["job_context"]
        except ConversationNotFound:
            return error_response(
                request,
                status_code=404,
                code="conversation_not_found",
                message="Conversation not found",
                retryable=False,
            )
        assert isinstance(context, dict)
        if context.get("selected_job_id") != job_id:
            return error_response(
                request,
                status_code=409,
                code="conversation_job_mismatch",
                message="This agent session is attached to a different job",
                retryable=False,
            )
        return await call_next(request)

    def mutation_hash(command_name: str, payload: dict[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(
                {"command": command_name, **payload},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def mutation_replay(
        *,
        identity: DeviceIdentity,
        target: str,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, object] | None:
        result = state_store.mutation_result(
            actor_id=identity.device_id,
            target_resource=target,
            command_name=command_name,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        return result

    def record_mutation(
        *,
        identity: DeviceIdentity,
        target: str,
        command_name: str,
        origin: str,
        idempotency_key: str,
        request_hash: str,
        result: dict[str, object],
        label: str,
        outcome: str = "completed",
        job_id: str | None = None,
        detail: dict[str, object] | None = None,
        event_type: str | None = None,
        inject_event_id: bool = False,
        reserved_event_id: int | None = None,
    ) -> int:
        event_id = state_store.record_mutation_result(
            event_type=event_type or ("agent_action" if origin == "mcp" else "user_action"),
            origin=origin,
            actor_id=identity.device_id,
            target_resource=target,
            command_name=command_name,
            outcome=outcome,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
            detail={
                "label": label,
                "state": outcome,
                "origin": origin,
                "outcome": outcome,
                **(detail or {}),
            },
            job_id=job_id,
            inject_event_id=inject_event_id,
            reserved_event_id=reserved_event_id,
        )
        return event_id

    def atomic_editable_mutation(
        *,
        identity: DeviceIdentity,
        target: str,
        command_name: str,
        origin: str,
        idempotency_key: str,
        request_hash: str,
        mutation: Callable[[sqlite3.Connection], dict[str, object]],
        label: str,
        job_id: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> dict[str, object]:
        result, _ = state_store.execute_editable_mutation(
            event_type="agent_action" if origin == "mcp" else "user_action",
            origin=origin,
            actor_id=identity.device_id,
            target_resource=target,
            command_name=command_name,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            mutation=mutation,
            detail={
                "label": label,
                "state": "completed",
                "origin": origin,
                "outcome": "completed",
                **(detail or {}),
            },
            job_id=job_id,
        )
        return result

    def record_agent_read(
        *,
        identity: DeviceIdentity,
        origin: Literal["mcp"] | None,
        idempotency_key: str | None,
        command_name: str,
        label: str,
        detail: dict[str, object] | None = None,
    ) -> None:
        if origin != "mcp" or not idempotency_key:
            return
        audit_detail = {"label": label, "origin": "mcp", **(detail or {})}
        request_hash = mutation_hash(command_name, audit_detail)
        try:
            state_store.record_mutation_result(
                event_type="agent_read",
                origin="mcp",
                actor_id=identity.device_id,
                target_resource=f"audit/{command_name}",
                command_name=command_name,
                outcome="completed",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result={},
                detail=audit_detail,
            )
        except IdempotencyConflict:
            # Audit collisions must not turn a successful read into an error.
            return

    @app.websocket("/v1/desktop/capabilities")
    async def desktop_capabilities(socket: WebSocket) -> None:
        await socket.accept()
        registered = False
        try:
            first = await asyncio.wait_for(socket.receive_json(), timeout=3.0)
            if not isinstance(first, dict) or first.get("type") != "authenticate":
                await socket.close(code=4401, reason="Device authentication required")
                return
            token = first.get("token")
            device_id = first.get("device_id")
            if not device_authenticator.matches(token, device_id):
                await socket.close(code=4401, reason="Device authentication required")
                return
            registered = await browser_capabilities.register(socket, device_id)
            if not registered:
                await socket.close(code=4409, reason="Configured desktop already connected")
                return
            await socket.send_json({"type": "ready", "lease_seconds": 15, "heartbeat_seconds": 5})
            while True:
                message = await socket.receive_json()
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "heartbeat":
                    await browser_capabilities.heartbeat(socket)
                    await socket.send_json({"type": "heartbeat_ack"})
                elif message.get("type") == "result":
                    await browser_capabilities.resolve(socket, message)
        except (TimeoutError, WebSocketDisconnect):
            pass
        finally:
            if registered:
                await browser_capabilities.unregister(socket)

    @app.post(
        "/v1/browser/commands",
        tags=["browser"],
        responses={
            403: {"model": ApiErrorResponse, "description": "Trusted MCP credential required"}
        },
    )
    async def browser_command(
        command: BrowserCommandRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> BrowserCommandResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        if command.origin == "mcp" and command.conversation_id is None:
            raise HTTPException(
                status_code=422, detail="MCP browser commands require a conversation ID"
            )
        try:
            arguments = command.validated_arguments()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if command.command == "tab.associate":
            try:
                jobs.get_job(arguments["job_id"])
            except (NotFound, Validation) as error:
                raise HTTPException(
                    status_code=422,
                    detail="Cannot associate a browser tab with an unknown job",
                ) from error
        if command.command.startswith("document."):
            ensure_job(arguments["job_id"])
        request_fields: dict[str, object] = {
            "command": command.command,
            "arguments": arguments,
            "origin": command.origin,
        }
        if command.origin == "mcp":
            request_fields["conversation_id"] = command.conversation_id
        request_hash = hashlib.sha256(
            json.dumps(
                request_fields,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        durable_command = command.command not in {
            "tabs.inspect",
            "page.snapshot",
            "document.inspect",
        }
        if command.origin == "mcp":
            try:
                scoped_service = conversation_manager.get(str(command.conversation_id))
            except ConversationNotFound as error:
                raise HTTPException(status_code=404, detail="Conversation not found") from error
            target_device_id = scoped_service.store.active_turn_origin_device_id()
            if target_device_id is None:
                raise HTTPException(
                    status_code=409,
                    detail="MCP conversation does not have an active turn",
                )
        else:
            target_device_id = identity.device_id
        target = (
            f"document/{arguments['job_id']}/{arguments['document_key']}"
            if command.command.startswith("document.")
            else f"browser/{target_device_id}/{arguments.get('tab_id', 'desktop')}"
        )
        if command.origin == "mcp":
            target = f"conversation/{command.conversation_id}/{target}"

        def observe_document_result(result: BrowserCommandResponse) -> None:
            if not command.command.startswith("document.") or result.state != "completed":
                return
            state_store.observe_document_file(
                observed_document_file(
                    arguments["job_id"],
                    result.data,
                    observed_at=(
                        datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    ),
                    observed_device_id=target_device_id,
                )
            )

        def audit_non_durable_command(result: BrowserCommandResponse) -> None:
            if durable_command or command.origin != "mcp":
                return
            state_store.record_mutation_result(
                event_type=(
                    "document_action"
                    if command.command.startswith("document.")
                    else "browser_action"
                ),
                origin="mcp",
                actor_id=identity.device_id,
                target_resource=target,
                command_name=command.command,
                outcome=result.outcome,
                idempotency_key=f"{command.idempotency_key}:{result.command_id}",
                request_hash=request_hash,
                result={},
                detail={
                    "label": command.command.replace(".", " "),
                    "state": result.state,
                    "origin": "mcp",
                    "outcome": result.outcome,
                    "error": result.error.model_dump() if result.error else None,
                },
            )

        async def execute_or_replay() -> BrowserCommandResponse:
            if durable_command:
                replay = state_store.mutation_result(
                    actor_id=identity.device_id,
                    target_resource=target,
                    command_name=command.command,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    response = BrowserCommandResponse.model_validate(replay)
                    observe_document_result(response)
                    return response
            result = await browser_capabilities.execute(command, device_id=target_device_id)
            observe_document_result(result)
            result_dict = result.model_dump(mode="json")
            if durable_command:
                state_store.record_mutation_result(
                    event_type=(
                        "document_action"
                        if command.command.startswith("document.")
                        else "browser_action"
                    ),
                    origin=command.origin,
                    actor_id=identity.device_id,
                    target_resource=target,
                    command_name=command.command,
                    outcome=result.outcome,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    result=result_dict,
                    detail={
                        "label": command.command.replace(".", " "),
                        "state": result.state,
                        "origin": command.origin,
                        "outcome": result.outcome,
                        "error": result.error.model_dump() if result.error else None,
                    },
                )
            audit_non_durable_command(result)
            return result

        try:
            if durable_command:
                async with serialize_browser_command((target_device_id, command.idempotency_key)):
                    return await execute_or_replay()
            return await execute_or_replay()
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except DesktopUnavailable as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "desktop_unavailable",
                    "message": "Open JobOS on the configured desktop and retry.",
                },
            ) from error

    @app.get("/v1/desktop/capabilities", tags=["desktop"])
    async def desktop_capability_presence(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> DesktopCapabilityPresence:
        return await browser_capabilities.presence(identity.device_id)

    @app.get("/v1/jobs/{job_id}/document-files", tags=["document-files"])
    def document_files_list(
        job_id: str, _: Annotated[DeviceIdentity, Depends(authenticated_device)]
    ) -> DocumentFileList:
        ensure_job(job_id)
        return DocumentFileList(
            documents=[
                DocumentFileRecord.model_validate(value)
                for value in state_store.list_document_files(job_id)
            ]
        )

    @app.get("/v1/document-files/{document_id}", tags=["document-files"])
    def document_file_get(
        document_id: str, _: Annotated[DeviceIdentity, Depends(authenticated_device)]
    ) -> DocumentFileRecord:
        if not DOCUMENT_FILE_ID.fullmatch(document_id):
            raise HTTPException(status_code=422, detail="Invalid document file ID")
        value = state_store.get_document_file(document_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Document file not found")
        return DocumentFileRecord.model_validate(value)

    @app.get(
        "/v1/career-profile",
        tags=["career-profile"],
    )
    def career_profile_complete_get(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
        agent_id: Annotated[str | None, Header(alias="X-JobOS-Agent-Id")] = None,
        agent_token: Annotated[str | None, Header(alias="X-JobOS-Agent-Token")] = None,
    ) -> CareerProfileCompleteCurrent:
        if mcp_token is None:
            require_career_profile_owner(identity)
        else:
            authenticated_career_profile_agent(
                identity,
                mcp_token,
                agent_id,
                agent_token,
            )
        return complete_career_profile.current()

    @app.get(
        "/v1/career-profile/agents",
        tags=["career-profile"],
    )
    def career_profile_agents_list(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ConnectedAgentList:
        require_career_profile_owner(identity)
        return career_profile_collaboration.list_agents()

    @app.patch(
        "/v1/career-profile/agents/{agent_id}",
        tags=["career-profile"],
    )
    def career_profile_agent_update(
        agent_id: str,
        command: AgentTrustModeUpdate,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> ConnectedAgent:
        require_direct_career_profile_user(identity, mcp_token)
        try:
            return career_profile_collaboration.update_trust_mode(
                agent_id=agent_id,
                trust_mode=command.trust_mode,
            )
        except ConnectedAgentAuthorizationError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete(
        "/v1/career-profile/agents/{agent_id}",
        tags=["career-profile"],
    )
    def career_profile_agent_disconnect(
        agent_id: str,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> ConnectedAgent:
        require_direct_career_profile_user(identity, mcp_token)
        try:
            return career_profile_collaboration.disconnect(agent_id=agent_id)
        except ConnectedAgentAuthorizationError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post(
        "/v1/career-profile/agent-edits",
        tags=["career-profile"],
    )
    def career_profile_agent_edit(
        command: AgentProfileEditRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
        agent_id: Annotated[str | None, Header(alias="X-JobOS-Agent-Id")] = None,
        agent_token: Annotated[str | None, Header(alias="X-JobOS-Agent-Token")] = None,
    ) -> AgentEditResult:
        agent = authenticated_career_profile_agent(
            identity,
            mcp_token,
            agent_id,
            agent_token,
        )
        try:
            return career_profile_collaboration.submit_edit(agent=agent, command=command)
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileItemNotFound,
            CareerProfileValueError,
            CareerProfileCollaborationConflict,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.get(
        "/v1/career-profile/proposals",
        tags=["career-profile"],
    )
    def career_profile_proposals_list(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        status: ProposalStatus | None = "pending",
    ) -> CareerProfileProposalList:
        require_career_profile_owner(identity)
        return career_profile_collaboration.list_proposals(status=status)

    @app.post(
        "/v1/career-profile/proposals/{proposal_id}/decision",
        tags=["career-profile"],
    )
    def career_profile_change_proposal_decide(
        proposal_id: str,
        command: ProposalDecisionRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> ProposalDecisionResult:
        principal = require_direct_career_profile_user(identity, mcp_token)
        try:
            return career_profile_collaboration.decide_proposal(
                proposal_id=proposal_id,
                principal=principal,
                command=command,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileValueError,
            CareerProfileCollaborationConflict,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.get(
        "/v1/career-profile/history",
        tags=["career-profile"],
    )
    def career_profile_history(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ProfileHistory:
        require_career_profile_owner(identity)
        return career_profile_collaboration.history()

    @app.post(
        "/v1/career-profile/history/{revision_id}/undo",
        tags=["career-profile"],
    )
    def career_profile_history_undo(
        revision_id: str,
        command: ProfileUndoRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> CareerProfileCompleteCurrent:
        principal = require_direct_career_profile_user(identity, mcp_token)
        try:
            return career_profile_collaboration.undo(
                revision_id=revision_id,
                principal=principal,
                command=command,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileValueError,
            CareerProfileCollaborationConflict,
        ) as error:
            raise complete_profile_conflict(error) from error

    def complete_profile_conflict(error: Exception) -> HTTPException:
        if isinstance(error, CareerProfileEvidenceNotFound):
            return HTTPException(status_code=404, detail="Career Profile Evidence was not found")
        if isinstance(error, CareerProfileItemNotFound):
            return HTTPException(status_code=404, detail="Career Profile item was not found")
        if isinstance(error, CareerProfileValueError):
            return HTTPException(status_code=422, detail=str(error))
        if isinstance(error, CareerProfileErasureInProgress):
            return HTTPException(status_code=409, detail=str(error))
        if isinstance(
            error,
            (CareerProfileEvidenceIntegrityError, CareerProfileEvidencePathError),
        ):
            return HTTPException(
                status_code=409,
                detail="Career Profile Evidence failed its immutable storage check",
            )
        return HTTPException(status_code=409, detail=str(error))

    def career_profile_actor(
        identity: DeviceIdentity,
        mcp_token: str | None,
        intent_grant_id: str | None,
    ) -> tuple[
        str, Literal["direct_user", "agent_inference", "authenticated_user_instruction"], str | None
    ]:
        if mcp_token is None:
            if intent_grant_id is not None:
                raise HTTPException(status_code=403, detail="Agent intent grants require MCP auth")
            return principal_for_device(identity.device_id), "direct_user", None
        require_trusted_mcp(identity, "mcp", mcp_token)
        return (
            "agent:trusted-local-mcp",
            "authenticated_user_instruction" if intent_grant_id else "agent_inference",
            intent_grant_id,
        )

    @app.post(
        "/v1/career-profile/intent-grants",
        tags=["career-profile"],
        status_code=201,
        responses={409: {"description": "Revision or idempotency conflict"}},
    )
    def career_profile_intent_grant_create(
        command: ProfileIntentGrantRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ProfileIntentGrant:
        require_career_profile_owner(identity)
        try:
            return complete_career_profile.create_intent_grant(
                principal=principal_for_device(identity.device_id), command=command
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileValueError,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.post(
        "/v1/career-profile/items",
        tags=["career-profile"],
        status_code=201,
    )
    def career_profile_item_create(
        command: ProfileItemMutation,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
        intent_grant_id: Annotated[str | None, Header(alias="X-JobOS-Intent-Grant")] = None,
    ) -> CareerProfileCompleteCurrent:
        require_career_profile_owner(identity)
        principal, mutation_source, grant_id = career_profile_actor(
            identity, mcp_token, intent_grant_id
        )
        try:
            return complete_career_profile.upsert_item(
                principal=principal,
                command=command,
                mutation_source=mutation_source,
                intent_grant_id=grant_id,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileValueError,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.put(
        "/v1/career-profile/items/{item_id}",
        tags=["career-profile"],
    )
    def career_profile_item_update(
        item_id: CompleteProfileItemId,
        command: ProfileItemMutation,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
        intent_grant_id: Annotated[str | None, Header(alias="X-JobOS-Intent-Grant")] = None,
    ) -> CareerProfileCompleteCurrent:
        require_career_profile_owner(identity)
        principal, mutation_source, grant_id = career_profile_actor(
            identity, mcp_token, intent_grant_id
        )
        try:
            return complete_career_profile.upsert_item(
                principal=principal,
                command=command,
                item_id=item_id,
                mutation_source=mutation_source,
                intent_grant_id=grant_id,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileItemNotFound,
            CareerProfileValueError,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.delete(
        "/v1/career-profile/items/{item_id}",
        tags=["career-profile"],
    )
    def career_profile_item_remove(
        item_id: CompleteProfileItemId,
        command: ProfileItemRemoval,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
        intent_grant_id: Annotated[str | None, Header(alias="X-JobOS-Intent-Grant")] = None,
    ) -> CareerProfileCompleteCurrent:
        require_career_profile_owner(identity)
        principal, mutation_source, grant_id = career_profile_actor(
            identity, mcp_token, intent_grant_id
        )
        try:
            return complete_career_profile.remove_item(
                principal=principal,
                item_id=item_id,
                command=command,
                mutation_source=mutation_source,
                intent_grant_id=grant_id,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileItemNotFound,
            CareerProfileValueError,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.post(
        "/v1/career-profile/items/{item_id}/decision",
        tags=["career-profile"],
    )
    def career_profile_proposal_decide(
        item_id: CompleteProfileItemId,
        command: ProfileProposalDecision,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
        intent_grant_id: Annotated[str | None, Header(alias="X-JobOS-Intent-Grant")] = None,
    ) -> CareerProfileCompleteCurrent:
        require_career_profile_owner(identity)
        principal, mutation_source, grant_id = career_profile_actor(
            identity, mcp_token, intent_grant_id
        )
        try:
            return complete_career_profile.decide_proposal(
                principal=principal,
                item_id=item_id,
                command=command,
                mutation_source=mutation_source,
                intent_grant_id=grant_id,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileItemNotFound,
            CareerProfileValueError,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.post(
        "/v1/career-profile/evidence",
        tags=["career-profile"],
        status_code=201,
    )
    def career_profile_evidence_import(
        command: EvidenceImportRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> CareerProfileCompleteCurrent:
        require_career_profile_owner(identity)
        principal, mutation_source, _ = career_profile_actor(identity, mcp_token, None)
        try:
            return complete_career_profile.import_evidence(
                principal=principal,
                command=command,
                mutation_source=mutation_source,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileValueError,
            CareerProfileEvidencePathError,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.delete(
        "/v1/career-profile/evidence/{evidence_id}",
        tags=["career-profile"],
    )
    def career_profile_evidence_remove(
        evidence_id: OpaqueEvidenceId,
        command: ProfileItemRemoval,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
        intent_grant_id: Annotated[str | None, Header(alias="X-JobOS-Intent-Grant")] = None,
    ) -> CareerProfileCompleteCurrent:
        require_career_profile_owner(identity)
        principal, mutation_source, grant_id = career_profile_actor(
            identity, mcp_token, intent_grant_id
        )
        try:
            return complete_career_profile.remove_evidence(
                principal=principal,
                evidence_id=evidence_id,
                command=command,
                mutation_source=mutation_source,
                intent_grant_id=grant_id,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
            CareerProfileEvidenceNotFound,
            CareerProfileValueError,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.post(
        "/v1/career-profile/evidence/{evidence_id}/erase",
        tags=["career-profile"],
    )
    def career_profile_evidence_erase(
        evidence_id: OpaqueEvidenceId,
        command: EvidenceErasureRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> CareerProfileErasureResult:
        require_career_profile_owner(identity)
        try:
            return complete_career_profile.erase_evidence(
                principal=principal_for_device(identity.device_id),
                evidence_id=evidence_id,
                command=command,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileEvidenceNotFound,
            CareerProfileEvidencePathError,
            CareerProfileErasureInProgress,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.post(
        "/v1/career-profile/reset",
        tags=["career-profile"],
    )
    def career_profile_reset(
        command: CareerProfileResetRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> CareerProfileErasureResult:
        require_career_profile_owner(identity)
        try:
            return complete_career_profile.reset_profile(
                principal=principal_for_device(identity.device_id),
                command=command,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileEvidencePathError,
            CareerProfileErasureInProgress,
        ) as error:
            raise complete_profile_conflict(error) from error

    @app.get(
        "/v1/career-profile/evidence/{evidence_id}/content",
        tags=["career-profile"],
    )
    def career_profile_evidence_content(
        evidence_id: OpaqueEvidenceId,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> Response:
        require_career_profile_owner(identity)
        try:
            metadata = complete_career_profile.evidence_metadata(evidence_id)
            content = complete_career_profile.read_evidence(evidence_id)
        except (
            CareerProfileEvidenceNotFound,
            CareerProfileEvidenceIntegrityError,
            CareerProfileEvidencePathError,
        ) as error:
            raise complete_profile_conflict(error) from error
        filename = re.sub(r'[\x00-\x1f\x7f"\\]', "_", metadata.original_filename)
        fallback_filename = filename.encode("ascii", "replace").decode("ascii")
        return Response(
            content=content,
            media_type=metadata.media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{fallback_filename}"; '
                    f"filename*=UTF-8''{quote(filename)}"
                )
            },
        )

    @app.get(
        "/v1/career-profile/work-arrangement",
        tags=["career-profile"],
    )
    def career_profile_work_arrangement_get(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> WorkArrangementCurrent:
        require_career_profile_owner(identity)
        return career_profiles.current_work_arrangement()

    @app.put(
        "/v1/career-profile/work-arrangement",
        tags=["career-profile"],
    )
    def career_profile_work_arrangement_put(
        command: WorkArrangementMutation,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> WorkArrangementCurrent:
        require_career_profile_owner(identity)
        try:
            return career_profiles.set_work_arrangement(
                principal=principal_for_device(identity.device_id),
                command=command,
            )
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get(
        "/v1/career-profile/work-arrangement/history",
        tags=["career-profile"],
    )
    def career_profile_work_arrangement_history(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> WorkArrangementHistory:
        require_career_profile_owner(identity)
        return career_profiles.work_arrangement_history()

    @app.post(
        "/v1/career-profile/work-arrangement/restore",
        tags=["career-profile"],
    )
    def career_profile_work_arrangement_restore(
        command: WorkArrangementRestore,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> WorkArrangementCurrent:
        require_career_profile_owner(identity)
        try:
            return career_profiles.restore_work_arrangement(
                principal=principal_for_device(identity.device_id),
                command=command,
            )
        except CareerProfileRevisionNotFound as error:
            raise HTTPException(
                status_code=404,
                detail="Career Profile revision not found",
            ) from error
        except (
            CareerProfileRevisionConflict,
            CareerProfileIdempotencyConflict,
            CareerProfileErasureInProgress,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/v1/career-profile/snapshots",
        tags=["career-profile"],
        status_code=201,
    )
    def career_profile_snapshot_create(
        command: CareerProfileSnapshotRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> CareerProfileSnapshot:
        require_career_profile_owner(identity)
        try:
            return career_profiles.create_snapshot(
                principal=principal_for_device(identity.device_id),
                request=command,
            )
        except CareerProfileErasureInProgress as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get(
        "/v1/career-profile/snapshots/{snapshot_id}",
        tags=["career-profile"],
    )
    def career_profile_snapshot_get(
        snapshot_id: str,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> CareerProfileSnapshot:
        require_career_profile_owner(identity)
        try:
            return career_profiles.get_snapshot(
                snapshot_id,
                principal=principal_for_device(identity.device_id),
            )
        except CareerProfileSnapshotNotFound as error:
            raise HTTPException(
                status_code=404,
                detail="Career Profile snapshot not found",
            ) from error
        except CareerProfileSnapshotForbidden as error:
            raise HTTPException(
                status_code=403,
                detail="This device is not authorized to resolve the Career Profile snapshot",
            ) from error

    @app.get("/v1/device-session", tags=["system"])
    async def device_session(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> DeviceSessionResponse:
        presence = await browser_capabilities.presence(identity.device_id)
        return DeviceSessionResponse(
            authenticated=True,
            transport=settings.transport,
            desktop="connected" if presence.available else "disconnected",
            api_version=__version__,
        )

    def conversation_service(conversation_id: str, identity: DeviceIdentity):
        if not re.fullmatch(r"conv_[A-Za-z0-9_-]{1,128}", conversation_id):
            raise HTTPException(status_code=422, detail="Invalid conversation ID")
        try:
            return conversation_manager.get(conversation_id, owner_device_id=identity.device_id)
        except ConversationNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def validated_turn_id(turn_id: str) -> str:
        if not re.fullmatch(r"turn_[A-Za-z0-9_-]{1,128}", turn_id):
            raise HTTPException(status_code=422, detail="Invalid turn ID")
        return turn_id

    @app.get("/v1/conversations", tags=["agent"])
    def conversations_list(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ConversationListResponse:
        return conversation_manager.list(owner_device_id=identity.device_id)

    @app.post("/v1/conversations", tags=["agent"], status_code=201)
    async def conversation_create(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        command: CreateConversationRequest | None = None,
    ) -> ConversationResponse:
        selected_job_id = command.selected_job_id if command else None
        if selected_job_id is not None:
            ensure_job(selected_job_id)
        try:
            return await conversation_manager.create(
                actor_id=identity.device_id, selected_job_id=selected_job_id
            )
        except ConversationLimit as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/v1/conversations/current", tags=["agent"], deprecated=True)
    def conversation_current(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ConversationResponse:
        return conversation_manager.get(
            state_store.first_active_conversation_id(identity.device_id),
            owner_device_id=identity.device_id,
        ).snapshot()

    @app.get("/v1/conversations/{conversation_id}", tags=["agent"])
    def conversation_get(
        conversation_id: ConversationId,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ConversationResponse:
        return conversation_service(conversation_id, identity).snapshot()

    @app.delete("/v1/conversations/{conversation_id}", tags=["agent"], status_code=204)
    async def conversation_archive(
        conversation_id: ConversationId,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> Response:
        conversation_service(conversation_id, identity)
        try:
            await conversation_manager.archive(conversation_id, actor_id=identity.device_id)
        except ConversationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(status_code=204)

    def conversation_context(conversation_id: str, identity: DeviceIdentity) -> dict[str, object]:
        context = state_store.conversation_job_context(conversation_id, identity.device_id)
        selection = context["selected_job_id"]
        workspace = state_store.workspace_snapshot(identity.device_id).snapshot
        selected_job = None
        if selection is not None:
            try:
                job = jobs.get_job(selection)
            except (NotFound, Validation):
                job = None
            if job is not None:
                company = sanitize_text(job.company)[:200]
                title = sanitize_text(job.title)[:200]
                if company and title:
                    selected_job = {
                        "job_id": selection,
                        "company": company,
                        "title": title,
                    }
        return {
            "origin_device_id": identity.device_id,
            "selected_job_id": selection,
            "selected_job": selected_job,
            "workspace": {
                key: workspace.get(key)
                for key in (
                    "selected_preset",
                    "active_center_surface",
                    "active_browser_tab_id",
                )
            }
            | {
                "active_artifact_id": context["active_artifact_id"],
                "active_artifact_page": context["active_artifact_page"],
                "active_artifact_zoom": context["active_artifact_zoom"],
            },
        }

    @app.put("/v1/conversations/{conversation_id}/workspace/job", tags=["workspace"])
    def conversation_select_job(
        conversation_id: ConversationId,
        command: JobSelectionRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ConversationJobContextMutation:
        conversation_service(conversation_id, identity)
        ensure_job(command.job_id)
        context = state_store.select_conversation_job(
            conversation_id, identity.device_id, command.job_id
        )
        return ConversationJobContextMutation(
            event_id=0, job_context=ConversationJobContext.model_validate(context)
        )

    @app.put("/v1/conversations/{conversation_id}/workspace/document", tags=["workspace"])
    def conversation_save_document_view(
        conversation_id: ConversationId,
        command: ConversationDocumentViewRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ConversationJobContextMutation:
        conversation_service(conversation_id, identity)
        try:
            context = state_store.save_conversation_document_view(
                conversation_id,
                identity.device_id,
                artifact_id=command.active_artifact_id,
                page=command.active_artifact_page,
                zoom=command.active_artifact_zoom,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return ConversationJobContextMutation(
            event_id=0, job_context=ConversationJobContext.model_validate(context)
        )

    @app.post(
        "/v1/conversations/{conversation_id}/messages",
        tags=["agent"],
        status_code=201,
    )
    async def conversation_send(
        conversation_id: ConversationId,
        command: SendMessageRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> TurnMutationResponse:
        if settings.career_profile_enabled:
            require_career_profile_owner(identity)
        try:
            return await conversation_service(conversation_id, identity).send(
                command,
                actor_id=identity.device_id,
                context=conversation_context(conversation_id, identity),
            )
        except ConversationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/v1/conversations/{conversation_id}/turns/{turn_id}/cancel", tags=["agent"])
    async def conversation_cancel(
        conversation_id: ConversationId,
        turn_id: TurnId,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> TurnMutationResponse:
        result = await conversation_service(conversation_id, identity).cancel(
            validated_turn_id(turn_id)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Turn not found")
        return result

    @app.post(
        "/v1/conversations/{conversation_id}/turns/{turn_id}/retry",
        tags=["agent"],
        status_code=201,
    )
    async def conversation_retry(
        conversation_id: ConversationId,
        turn_id: TurnId,
        command: RetryTurnRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> TurnMutationResponse:
        if settings.career_profile_enabled:
            require_career_profile_owner(identity)
        try:
            result = await conversation_service(conversation_id, identity).retry(
                validated_turn_id(turn_id), command, actor_id=identity.device_id
            )
        except ConversationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if result is None:
            raise HTTPException(status_code=404, detail="Turn not found")
        return result

    @app.get(
        "/v1/conversations/events/stream",
        tags=["agent"],
        response_class=StreamingResponse,
        responses={
            200: {
                "description": (
                    "SSE frames with a global event ID and a data object containing "
                    "conversation_id, the current recovery_state, and the scoped "
                    "conversation event."
                ),
                "content": {
                    "text/event-stream": {
                        "schema": {"type": "string"},
                        "example": (
                            'id: 42\nevent: conversation_event\ndata: {"conversation_id":'
                            '"conv_example","recovery_state":"ready",'
                            '"event":{"event_id":42}}\n\n'
                        ),
                    }
                },
            }
        },
    )
    async def conversation_stream(
        request: Request,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        after: int | None = None,
        once: bool = False,
    ) -> StreamingResponse:
        header_cursor = request.headers.get("last-event-id")
        try:
            cursor = after if after is not None else int(header_cursor or 0)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid event cursor") from error

        async def event_source() -> AsyncIterator[str]:
            async for frame in conversation_event_source(
                state_store,
                request,
                cursor=cursor,
                once=once,
                owner_device_id=identity.device_id,
            ):
                yield frame

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/jobs", tags=["jobs"])
    def jobs_list(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        sort: SortMode | None = None,
        query: str | None = None,
        status_group: str | None = None,
        origin: Literal["mcp"] | None = None,
        idempotency_key: str | None = None,
    ) -> JobListResponse:
        active_sort = sort or state_store.job_workspace_state().sort_mode
        result = list_jobs(
            jobs,
            sort=active_sort,
            query=query,
            status_group=status_group,
            manual_order=state_store.manual_order(),
        )
        record_agent_read(
            identity=identity,
            origin=origin,
            idempotency_key=idempotency_key,
            command_name="job.list",
            label="Inspected jobs",
            detail={"count": len(result.jobs)},
        )
        return result

    @app.post(
        "/v1/jobs",
        tags=["jobs"],
        responses={
            403: {"model": ApiErrorResponse, "description": "Trusted MCP credential required"}
        },
    )
    @serialized_mutation_route
    def job_create_from_browser(
        command: BrowserJobCreateRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> BrowserJobCreateResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        payload = command.model_dump(mode="json", exclude={"idempotency_key"})
        request_hash = mutation_hash("job.create_from_browser", payload)
        try:
            replay = mutation_replay(
                identity=identity,
                target="jobs",
                command_name="job.create_from_browser",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return BrowserJobCreateResponse.model_validate(replay)

        command_job_id = str(uuid4())
        saved = jobs.create_job(
            CreateJobCommand(
                job_id=command_job_id,
                company_name=command.company_name,
                title=command.title,
                canonical_url=str(command.canonical_url),
                location_text=command.location_text,
                description_text=command.description_text,
                application_url=str(command.application_url),
                full_listing_text=command.full_listing_text,
                analysis_text=command.analysis_text,
                listing_completeness=command.listing_completeness,
                listing_source_url=(
                    str(command.listing_source_url) if command.listing_source_url else None
                ),
                listing_captured_at=command.listing_captured_at,
                listing_verified_at=command.listing_verified_at,
                listing_capture_method=command.listing_capture_method,
                listing_sha256=command.listing_sha256,
                listing_evidence=command.listing_evidence,
            )
        )
        normalized = normalize_job_detail(saved)
        # Saving a job never silently retargets an agent session. Selection is
        # an explicit conversation-scoped command.
        event_id = 0
        result = BrowserJobCreateResponse(
            event_id=event_id,
            created=saved.job_id == command_job_id,
            job=normalized,
        )
        record_mutation(
            identity=identity,
            target="jobs",
            command_name="job.create_from_browser",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(mode="json"),
            label="Saved job from browser" if result.created else "Opened saved browser job",
            job_id=normalized.job_id,
            detail={"created": result.created},
        )
        return result

    @app.put(
        "/v1/jobs/order",
        tags=["jobs"],
        responses={
            403: {"model": ApiErrorResponse, "description": "Trusted MCP credential required"}
        },
    )
    @serialized_mutation_route
    def jobs_reorder(
        command: ManualOrderRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> JobMutationResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        request_hash = mutation_hash(
            "jobs.reorder", {"job_ids": command.job_ids, "origin": command.origin}
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target="jobs/order",
                command_name="jobs.reorder",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobMutationResponse.model_validate(replay)
        known_ids = {job.job_id for job in jobs.list_jobs()}
        supplied_ids = set(command.job_ids)
        if len(supplied_ids) != len(command.job_ids) or supplied_ids != known_ids:
            raise HTTPException(
                status_code=422,
                detail="Manual order must contain every current job exactly once",
            )
        event_id = state_store.save_manual_order(command.job_ids, command.origin)
        result = JobMutationResponse(event_id=event_id)
        record_mutation(
            identity=identity,
            target="jobs/order",
            command_name="jobs.reorder",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(),
            label="Reordered jobs",
        )
        return result

    @app.get("/v1/workspace/jobs", tags=["workspace"])
    def workspace_jobs(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        conversation_id: str | None = None,
    ) -> WorkspaceJobsResponse:
        state = state_store.job_workspace_state()
        selected_job_id = None
        if conversation_id is not None:
            conversation_service(conversation_id, identity)
            selected_job_id = state_store.conversation_job_context(
                conversation_id, identity.device_id
            )["selected_job_id"]
        return WorkspaceJobsResponse(
            selected_job_id=selected_job_id,
            sort_mode=state.sort_mode,
            manual_order=state.manual_order,
        )

    @app.get("/v1/workspace", tags=["workspace"])
    def workspace_get(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        origin: Literal["mcp"] | None = None,
        idempotency_key: str | None = None,
        conversation_id: str | None = None,
    ) -> WorkspaceSnapshotResponse:
        record = state_store.workspace_snapshot(identity.device_id)
        snapshot = dict(record.snapshot)
        if conversation_id is not None:
            conversation_service(conversation_id, identity)
            context = state_store.conversation_job_context(conversation_id, identity.device_id)
            snapshot.update(
                selected_job_id=context["selected_job_id"],
                active_artifact_id=context["active_artifact_id"],
                active_artifact_page=context["active_artifact_page"],
                active_artifact_zoom=context["active_artifact_zoom"],
            )
        result = WorkspaceSnapshotResponse.model_validate(
            {
                "revision": record.revision,
                "repaired_presets": list(record.repaired_presets),
                "repaired_browser": record.repaired_browser,
                "browser_repair_reasons": list(record.browser_repair_reasons),
                **snapshot,
            }
        )
        record_agent_read(
            identity=identity,
            origin=origin,
            idempotency_key=idempotency_key,
            command_name="workspace.inspect",
            label="Inspected workspace",
        )
        return result

    @app.put(
        "/v1/workspace",
        tags=["workspace"],
        responses={
            403: {"model": ApiErrorResponse, "description": "Trusted MCP credential required"}
        },
    )
    def workspace_put(
        command: WorkspaceSnapshotCommand,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> WorkspaceSnapshotResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        # Selected job and document view are conversation-owned. Compatibility
        # fields in this global snapshot are stripped by the state store.
        try:
            record = state_store.save_workspace_snapshot(
                identity.device_id,
                expected_revision=command.revision,
                snapshot=command.model_dump(exclude={"revision", "origin", "idempotency_key"}),
                idempotency_key=command.idempotency_key,
                origin=command.origin,
                actor_id=identity.device_id,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except WorkspaceRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Workspace revision conflict; current revision is {error.current_revision}"
                ),
            ) from error
        return WorkspaceSnapshotResponse(
            revision=record.revision,
            repaired_presets=list(record.repaired_presets),
            repaired_browser=record.repaired_browser,
            browser_repair_reasons=list(record.browser_repair_reasons),
            **record.snapshot,
        )

    @app.put(
        "/v1/workspace/jobs/selection",
        tags=["workspace"],
        deprecated=True,
        responses={
            410: {
                "model": ApiErrorResponse,
                "description": "Selection must target a conversation",
            }
        },
    )
    def workspace_select_job_deprecated(
        command: JobSelectionRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobMutationResponse:
        del command, identity
        raise HTTPException(
            status_code=410,
            detail="Select a job through /v1/conversations/{conversation_id}/workspace/job",
        )

    @app.put(
        "/v1/workspace/jobs/sort",
        tags=["workspace"],
        responses={
            403: {"model": ApiErrorResponse, "description": "Trusted MCP credential required"}
        },
    )
    @serialized_mutation_route
    def workspace_sort_jobs(
        command: JobSortRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> JobMutationResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        request_hash = mutation_hash(
            "jobs.sort", {"sort_mode": command.sort_mode, "origin": command.origin}
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target="workspace/jobs",
                command_name="jobs.sort",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobMutationResponse.model_validate(replay)
        event_id = state_store.save_job_sort(command.sort_mode, command.origin)
        result = JobMutationResponse(event_id=event_id)
        record_mutation(
            identity=identity,
            target="workspace/jobs",
            command_name="jobs.sort",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(),
            label="Changed job ordering",
        )
        return result

    @app.get("/v1/jobs/{job_id}", tags=["jobs"])
    def job_inspect(
        job_id: str,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        origin: Literal["mcp"] | None = None,
        idempotency_key: str | None = None,
    ) -> JobDetail:
        try:
            result = normalize_job_detail(jobs.get_job(job_id))
            record_agent_read(
                identity=identity,
                origin=origin,
                idempotency_key=idempotency_key,
                command_name="job.inspect",
                label="Inspected job",
                detail={"job_id": job_id},
            )
            return result
        except NotFound as error:
            raise HTTPException(status_code=404, detail="Job not found") from error

    @app.delete(
        "/v1/jobs/{job_id}/demo",
        tags=["jobs"],
        responses={
            403: {"model": ApiErrorResponse, "description": "Trusted MCP credential required"},
            404: {"model": ApiErrorResponse, "description": "Demo job not found"},
            409: {
                "model": ApiErrorResponse,
                "description": "The selected job is not the fictional demo",
            },
        },
    )
    @serialized_mutation_route
    def job_remove_demo(
        job_id: str,
        command: DemoRemovalRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> JobMutationResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        request_hash = mutation_hash(
            "job.remove_demo", {"job_id": job_id, "origin": command.origin}
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"jobs/{job_id}/demo",
                command_name="job.remove_demo",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobMutationResponse.model_validate(replay)
        try:
            record = jobs.get_job(job_id)
        except NotFound as error:
            if job_id != DEMO_JOB_ID:
                raise HTTPException(status_code=404, detail="Demo job not found") from error
        else:
            if not record.synthetic_demo:
                raise HTTPException(
                    status_code=409,
                    detail="Only a fictional demo job can be removed",
                )
            jobs.delete_job(job_id)
        state_store.delete_job_documents(job_id)
        state_store.clear_job_from_conversations(job_id)
        result_payload = JobMutationResponse(event_id=0).model_dump(mode="json")
        event_id = record_mutation(
            identity=identity,
            target=f"jobs/{job_id}/demo",
            command_name="job.remove_demo",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result_payload,
            label="Removed fictional demo job",
            job_id=job_id,
            inject_event_id=True,
        )
        return JobMutationResponse(event_id=event_id)

    @app.put(
        "/v1/jobs/{job_id}/description",
        tags=["jobs"],
        responses={
            403: {"model": ApiErrorResponse, "description": "Trusted MCP credential required"}
        },
    )
    @serialized_mutation_route
    def job_update_description(
        job_id: str,
        command: JobDescriptionUpdateRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> JobDescriptionUpdateResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        request_hash = mutation_hash(
            "job.update_description",
            {"job_id": job_id, **command.model_dump(exclude={"idempotency_key"})},
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"jobs/{job_id}",
                command_name="job.update_description",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobDescriptionUpdateResponse.model_validate(replay)
        reservation_event_id = state_store.reserve_mutation(
            origin=command.origin,
            actor_id=identity.device_id,
            target_resource=f"jobs/{job_id}",
            command_name="job.update_description",
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            job_id=job_id,
        )
        try:
            updated = jobs.update_description(
                job_id,
                command.description_text,
                source=command.source,
                provenance=command.provenance,
            )
        except NotFound as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        except Validation as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        normalized = normalize_job_detail(updated)
        result_payload = JobDescriptionUpdateResponse(event_id=0, job=normalized).model_dump(
            mode="json"
        )
        event_id = record_mutation(
            identity=identity,
            target=f"jobs/{job_id}",
            command_name="job.update_description",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result_payload,
            label="Updated full job listing",
            job_id=job_id,
            detail={"source": command.source, "description_length": len(command.description_text)},
            event_type="job_description_updated",
            inject_event_id=True,
            reserved_event_id=reservation_event_id,
        )
        return JobDescriptionUpdateResponse(event_id=event_id, job=normalized)

    @app.put(
        "/v1/jobs/{job_id}/status",
        tags=["jobs"],
        responses={
            403: {"model": ApiErrorResponse, "description": "Trusted MCP credential required"}
        },
    )
    @serialized_mutation_route
    def job_update_status(
        job_id: str,
        command: StatusChangeRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> StatusChangeResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        request_hash = mutation_hash(
            "job.update_status",
            {
                "job_id": job_id,
                "target_status": command.target_status,
                "reason": command.reason,
                "origin": command.origin,
                "record_application": command.record_application,
            },
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"jobs/{job_id}",
                command_name="job.update_status",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return StatusChangeResponse.model_validate(replay)
        try:
            before = jobs.get_job(job_id)
        except NotFound as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        reservation_event_id = state_store.reserve_mutation(
            origin=command.origin,
            actor_id=identity.device_id,
            target_resource=f"jobs/{job_id}",
            command_name="job.update_status",
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            job_id=job_id,
            detail={"from_status": before.status, "to_status": command.target_status},
        )
        reservation_detail = state_store.mutation_reservation_detail(
            event_id=reservation_event_id,
            request_hash=request_hash,
        )
        from_status = str(reservation_detail["from_status"])
        try:
            updated = jobs.update_status(
                job_id,
                command.target_status,
                record_application=command.record_application,
                reason=command.reason,
            )
        except NotFound as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        except Conflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result = StatusChangeResponse(
            event_id=reservation_event_id,
            job=normalize_job_detail(updated),
        )
        record_mutation(
            identity=identity,
            target=f"jobs/{job_id}",
            command_name="job.update_status",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(mode="json"),
            label=f"Updated job status to {command.target_status}",
            job_id=job_id,
            detail={"from_status": from_status, "to_status": updated.status},
            event_type="job_status_changed",
            inject_event_id=True,
            reserved_event_id=reservation_event_id,
        )
        return result

    @app.get(
        "/v1/jobs/{job_id}/history",
        tags=["jobs"],
        response_model_exclude_none=True,
    )
    def job_history(
        job_id: str,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> LeadHistoryResponse:
        try:
            jobs.get_job(job_id)
            return LeadHistoryResponse(
                events=[
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "from_status": event.from_status,
                        "to_status": event.to_status,
                        "occurred_at": event.occurred_at.isoformat(),
                        "reason": event.reason,
                        "source": event.source,
                        "provenance": event.provenance,
                        "from_sha256": event.from_sha256,
                        "to_sha256": event.to_sha256,
                    }
                    for event in jobs.list_history(job_id)
                ]
            )
        except NotFound as error:
            raise HTTPException(status_code=404, detail="Job not found") from error

    def ensure_job(job_id: str) -> None:
        try:
            jobs.get_job(job_id)
        except NotFound as error:
            raise HTTPException(status_code=404, detail="Job not found") from error

    def editable_conflict(error: EditableDocumentConflict) -> HTTPException:
        return HTTPException(
            status_code=409, detail={"message": str(error), "current": error.current}
        )

    def resolve_import_source(
        job_id: str,
        document_id: str,
        source: CreateRegisteredImportRequest | CreateExternalImportRequest,
    ) -> PreparedEditableImport:
        if source.mode == "import_registered_artifact":
            artifact = state_store.editable_import_source(job_id, source.source_artifact_id)
            return PreparedEditableImport(
                artifact_id=source.source_artifact_id,
                filename=str(artifact["filename"]) if artifact.get("filename") else None,
                sha256=str(artifact["sha256"]),
                document_key=source.document_key,
            )
        source_bytes = source.source_bytes()
        stored = local_artifacts.store_import(
            job_id=job_id,
            document_id=document_id,
            artifact=ArtifactWrite(
                filename=source.source_filename,
                media_type=DOCX_MEDIA_TYPE,
                content=source_bytes,
                sha256=source.source_sha256,
            ),
        )
        return PreparedEditableImport(
            artifact_id=None,
            filename=source.source_filename,
            sha256=source.source_sha256,
            document_key=source.document_key,
            stored=stored,
        )

    def register_import_source(
        job_id: str,
        prepared: PreparedEditableImport,
        connection: sqlite3.Connection,
    ) -> tuple[str, str | None, str]:
        if prepared.artifact_id is not None:
            return prepared.artifact_id, prepared.filename, prepared.sha256
        if prepared.stored is None:  # pragma: no cover - dataclass construction is local
            raise ArtifactStorageError("External DOCX was not stored")
        verified = VerifiedArtifact(
            job_id=job_id,
            document_key=prepared.document_key,
            document_label=LABELS[prepared.document_key],
            source_revision=f"external:{prepared.sha256}",
            artifact_revision=prepared.sha256,
            media_type=DOCX_MEDIA_TYPE,
            sha256=prepared.sha256,
            render_status="succeeded",
            render_sequence=state_store.next_document_artifact_sequence(
                job_id, connection=connection
            ),
            canonical_path=str(prepared.stored.canonical_path),
            filename=prepared.filename,
            failure_message=None,
        )
        artifact_id, _ = state_store.register_document_artifacts(
            job_id, [verified], connection=connection
        )
        if artifact_id is None:
            raise ArtifactStorageError("External DOCX was not registered")
        return artifact_id, prepared.filename, prepared.sha256

    @app.get("/v1/jobs/{job_id}/editable-documents", tags=["editable-documents"])
    def editable_documents_list(
        job_id: str, _: Annotated[DeviceIdentity, Depends(authenticated_device)]
    ) -> EditableDocumentList:
        ensure_job(job_id)
        return EditableDocumentList(
            documents=[as_summary(row) for row in state_store.list_editable_documents(job_id)]
        )

    @app.get(
        "/v1/jobs/{job_id}/editable-document-outlines/{document_key}",
        tags=["editable-documents"],
    )
    def editable_document_outline(
        job_id: str,
        document_key: Literal["resume", "cover_letter", "references"],
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        origin: Literal["mcp"] | None = None,
        idempotency_key: str | None = None,
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> DocumentDraftOutline:
        ensure_job(job_id)
        require_trusted_mcp(identity, origin, mcp_token)
        row = state_store.get_job_editable_document(job_id, document_key)
        if row is None:
            raise HTTPException(status_code=404, detail="Editable document not found")
        content = cast(dict[str, object], row["content"])
        result = DocumentDraftOutline.model_validate(
            {
                "document_id": str(row["document_id"]),
                "document_key": document_key,
                "document_label": str(row["document_label"]),
                "revision": row["revision"],
                "settings": row["settings"],
                "outline": semantic_outline(row),
                "unresolved_suggestion_count": unresolved_suggestion_count(content),
                "comment_count": len(cast(list[object], row["comments"])),
            }
        )
        record_agent_read(
            identity=identity,
            origin=origin,
            idempotency_key=idempotency_key,
            command_name="document.draft.read",
            label="Inspected editable document outline",
            detail={"job_id": job_id, "document_key": document_key},
        )
        return result

    @app.get("/v1/jobs/{job_id}/editable-documents/{document_key}", tags=["editable-documents"])
    def editable_document_for_job(
        job_id: str,
        document_key: Literal["resume", "cover_letter", "references"],
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> EditableDocument:
        ensure_job(job_id)
        row = state_store.get_job_editable_document(job_id, document_key)
        if row is None:
            raise HTTPException(status_code=404, detail="Editable document not found")
        return as_document(row)

    @app.post("/v1/jobs/{job_id}/editable-documents", tags=["editable-documents"], status_code=201)
    @serialized_mutation_route
    def editable_document_create(
        job_id: str,
        command: CreateEditableDocumentRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> EditableDocument:
        ensure_job(job_id)
        payload = command.model_dump(mode="json", exclude={"idempotency_key"})
        request_hash = mutation_hash("document.editor.create", {"job_id": job_id, **payload})
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"jobs/{job_id}/editable-documents",
                command_name="document.editor.create",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return EditableDocument.model_validate(replay)
        if command.mode == "blank":
            content = blank_content(command.document_key)
            settings_value = default_settings()
            import_report = {"source_filename": None, "imported_at": None, "issues": []}
            imported = False
            document_id = None
            prepared_import = None
            validate_content(content, DocumentSettings.model_validate(settings_value), [])
        else:
            if state_store.get_job_editable_document(job_id, command.document_key) is not None:
                raise HTTPException(
                    status_code=409,
                    detail="An editable document already exists for this job and type",
                )
            document_id = state_store.new_editable_document_id()
            try:
                prepared_import = resolve_import_source(job_id, document_id, command)
            except (ArtifactStorageError, OSError, sqlite3.OperationalError) as error:
                raise HTTPException(
                    status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
                ) from error
            except (ArtifactTrustError, ValueError) as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            content = command.content
            settings_value = command.settings.model_dump(mode="json")
            import_report = command.import_report.model_dump(mode="json")
            imported = True
        label = (
            f"Created {LABELS[command.document_key]} draft"
            if command.mode == "blank"
            else f"Imported {LABELS[command.document_key]} draft"
        )

        def create_document(connection: sqlite3.Connection) -> dict[str, object]:
            if prepared_import is not None:
                source_artifact_id, source_filename, source_sha256 = register_import_source(
                    job_id, prepared_import, connection
                )
            else:
                source_artifact_id = source_filename = source_sha256 = None
            row = state_store.create_editable_document(
                job_id=job_id,
                document_key=command.document_key,
                document_label=LABELS[command.document_key],
                content=content,
                settings=settings_value,
                comments=[],
                import_report=import_report,
                source_artifact_id=source_artifact_id,
                source_filename=source_filename,
                source_sha256=source_sha256,
                imported=imported,
                document_id=document_id,
                connection=connection,
            )
            return as_document(row).model_dump(mode="json")

        try:
            result = atomic_editable_mutation(
                identity=identity,
                target=f"jobs/{job_id}/editable-documents",
                command_name="document.editor.create",
                origin="user",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                mutation=create_document,
                label=label,
                job_id=job_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (ArtifactStorageError, OSError, sqlite3.OperationalError) as error:
            raise HTTPException(
                status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
            ) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return EditableDocument.model_validate(result)

    @app.get("/v1/editable-documents/{document_id}", tags=["editable-documents"])
    def editable_document_get(
        document_id: str, _: Annotated[DeviceIdentity, Depends(authenticated_device)]
    ) -> EditableDocument:
        row = state_store.get_editable_document(document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Editable document not found")
        return as_document(row)

    @app.put("/v1/editable-documents/{document_id}", tags=["editable-documents"])
    @serialized_mutation_route
    def editable_document_save(
        document_id: str,
        command: SaveEditableDocumentRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> EditableDocument:
        request_hash = mutation_hash(
            "document.editor.save",
            {
                "document_id": document_id,
                **command.model_dump(mode="json", exclude={"idempotency_key"}),
            },
        )

        def save_document(connection: sqlite3.Connection) -> dict[str, object]:
            row = state_store.save_editable_document(
                document_id,
                expected_revision=command.base_revision,
                content=command.content,
                settings=command.settings.model_dump(mode="json"),
                comments=[comment.model_dump(mode="json") for comment in command.comments],
                connection=connection,
            )
            return as_document(row).model_dump(mode="json")

        try:
            result = atomic_editable_mutation(
                identity=identity,
                target=f"editable-documents/{document_id}",
                command_name="document.editor.save",
                origin="user",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                mutation=save_document,
                label="Saved editable document",
            )
        except EditableDocumentConflict as error:
            raise editable_conflict(error) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Editable document not found") from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return EditableDocument.model_validate(result)

    @app.post("/v1/editable-documents/{document_id}/import", tags=["editable-documents"])
    @serialized_mutation_route
    def editable_document_import(
        document_id: str,
        command: ReplaceFromDocxRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> EditableDocument:
        target = f"editable-documents/{document_id}"
        command_name = "document.editor.replace_from_docx"
        request_hash = mutation_hash(
            command_name,
            {
                "document_id": document_id,
                **command.model_dump(mode="json", exclude={"idempotency_key"}),
            },
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=target,
                command_name=command_name,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return EditableDocument.model_validate(replay)
        current = state_store.get_editable_document(document_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Editable document not found")
        if current["revision"] != command.base_revision:
            raise editable_conflict(EditableDocumentConflict(current))
        if current["document_key"] != command.source.document_key:
            raise HTTPException(
                status_code=422,
                detail="Imported document type must match the existing editable document",
            )
        try:
            prepared_import = resolve_import_source(
                str(current["job_id"]), document_id, command.source
            )

            def replace_document(connection: sqlite3.Connection) -> dict[str, object]:
                source_artifact_id, source_filename, source_sha256 = register_import_source(
                    str(current["job_id"]), prepared_import, connection
                )
                row = state_store.replace_editable_document_from_import(
                    document_id,
                    expected_revision=command.base_revision,
                    content=command.source.content,
                    settings=command.source.settings.model_dump(mode="json"),
                    import_report=command.source.import_report.model_dump(mode="json"),
                    source_artifact_id=source_artifact_id,
                    source_filename=source_filename,
                    source_sha256=source_sha256,
                    connection=connection,
                )
                return as_document(row).model_dump(mode="json")

            result = atomic_editable_mutation(
                identity=identity,
                target=target,
                command_name=command_name,
                origin="user",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                mutation=replace_document,
                label=f"Replaced {current['document_label']} from DOCX",
                job_id=str(current["job_id"]),
                detail={"source_sha256": prepared_import.sha256},
            )
        except EditableDocumentConflict as error:
            raise editable_conflict(error) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Editable document not found") from error
        except (ArtifactStorageError, OSError, sqlite3.OperationalError) as error:
            raise HTTPException(
                status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
            ) from error
        except (ArtifactTrustError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return EditableDocument.model_validate(result)

    @app.get("/v1/editable-documents/{document_id}/snapshots", tags=["editable-documents"])
    def editable_snapshot_list(
        document_id: str, _: Annotated[DeviceIdentity, Depends(authenticated_device)]
    ) -> EditableDocumentSnapshotList:
        if state_store.get_editable_document(document_id) is None:
            raise HTTPException(status_code=404, detail="Editable document not found")
        return EditableDocumentSnapshotList(
            snapshots=[
                EditableDocumentSnapshot.model_validate(row)
                for row in state_store.list_editable_snapshots(document_id)
            ]
        )

    @app.post(
        "/v1/editable-documents/{document_id}/snapshots",
        tags=["editable-documents"],
        status_code=201,
    )
    @serialized_mutation_route
    def editable_snapshot_create(
        document_id: str,
        command: CreateSnapshotRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> EditableDocumentSnapshot:
        require_trusted_mcp(identity, command.origin, mcp_token)
        command_name = (
            "document.draft.snapshot" if command.origin == "mcp" else "document.editor.snapshot"
        )
        request_hash = mutation_hash(
            command_name,
            {
                "document_id": document_id,
                **command.model_dump(mode="json", exclude={"idempotency_key"}),
            },
        )
        document = state_store.get_editable_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Editable document not found")

        def create_snapshot(connection: sqlite3.Connection) -> dict[str, object]:
            row = state_store.create_editable_snapshot(
                document_id,
                expected_revision=command.base_revision,
                reason="manual",
                actor="jobhunter" if command.origin == "mcp" else "user",
                label=command.label,
                connection=connection,
            )
            return EditableDocumentSnapshot.model_validate(row).model_dump(mode="json")

        try:
            result = atomic_editable_mutation(
                identity=identity,
                target=f"editable-documents/{document_id}",
                command_name=command_name,
                origin=command.origin,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                mutation=create_snapshot,
                label="Saved document checkpoint",
                job_id=str(document["job_id"]),
            )
        except EditableDocumentConflict as error:
            raise editable_conflict(error) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Editable document not found") from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return EditableDocumentSnapshot.model_validate(result)

    @app.post(
        "/v1/editable-documents/{document_id}/snapshots/{snapshot_id}/restore",
        tags=["editable-documents"],
    )
    @serialized_mutation_route
    def editable_snapshot_restore(
        document_id: str,
        snapshot_id: str,
        command: RestoreSnapshotRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> EditableDocument:
        request_hash = mutation_hash(
            "document.editor.restore",
            {
                "document_id": document_id,
                "snapshot_id": snapshot_id,
                "base_revision": command.base_revision,
            },
        )

        def restore_snapshot(connection: sqlite3.Connection) -> dict[str, object]:
            row = state_store.restore_editable_snapshot(
                document_id,
                snapshot_id,
                expected_revision=command.base_revision,
                connection=connection,
            )
            return as_document(row).model_dump(mode="json")

        try:
            result = atomic_editable_mutation(
                identity=identity,
                target=f"editable-documents/{document_id}",
                command_name="document.editor.restore",
                origin="user",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                mutation=restore_snapshot,
                label="Restored document checkpoint",
            )
        except EditableDocumentConflict as error:
            raise editable_conflict(error) from error
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="Editable document or snapshot not found"
            ) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return EditableDocument.model_validate(result)

    @app.post("/v1/editable-documents/{document_id}/operations", tags=["editable-documents"])
    @serialized_mutation_route
    def editable_operations(
        document_id: str,
        command: ApplyOperationsRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> OperationReceipt:
        require_trusted_mcp(identity, command.origin, mcp_token)
        request_hash = mutation_hash(
            "document.draft.apply",
            {
                "document_id": document_id,
                **command.model_dump(mode="json", exclude={"idempotency_key"}),
            },
        )

        def apply_document_operations(connection: sqlite3.Connection) -> dict[str, object]:
            current = state_store.get_editable_document(document_id, connection=connection)
            if current is None:
                raise KeyError(document_id)
            if current["revision"] != command.base_revision:
                raise EditableDocumentConflict(current)
            content, changed_ids, changes = apply_operations(current, command)
            saved, snapshot_id = state_store.save_agent_document_operation(
                document_id,
                expected_revision=command.base_revision,
                content=content,
                connection=connection,
            )
            return OperationReceipt.model_validate(
                {
                    "document": as_document(saved),
                    "changed_block_ids": changed_ids,
                    "changes": changes,
                    "snapshot_id": snapshot_id,
                }
            ).model_dump(mode="json")

        try:
            result = atomic_editable_mutation(
                identity=identity,
                target=f"editable-documents/{document_id}",
                command_name="document.draft.apply",
                origin=command.origin,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                mutation=apply_document_operations,
                label="Applied MCP document edits",
            )
        except EditableDocumentConflict as error:
            raise editable_conflict(error) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Editable document not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return OperationReceipt.model_validate(result)

    @app.post("/v1/editable-documents/{document_id}/publish", tags=["editable-documents"])
    @serialized_mutation_route
    def editable_document_publish(
        document_id: str,
        command: PublishEditableDocumentRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> EditableDocument:
        request_hash = mutation_hash(
            "document.editor.publish",
            {
                "document_id": document_id,
                **command.model_dump(mode="json", exclude={"idempotency_key"}),
            },
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"editable-documents/{document_id}",
                command_name="document.editor.publish",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return EditableDocument.model_validate(replay)

        current = state_store.get_editable_document(document_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Editable document not found")
        if current["revision"] != command.expected_revision:
            raise editable_conflict(EditableDocumentConflict(current))
        content = cast(dict[str, object], current["content"])
        unresolved = unresolved_suggestion_count(content)
        if unresolved != command.unresolved_suggestion_count:
            raise HTTPException(
                status_code=409,
                detail="Document suggestions changed; review the current revision again",
            )
        if unresolved and not command.confirm_current_state:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Publishing with unresolved JobHunter suggestions requires explicit "
                    "confirmation of the deterministic current state"
                ),
            )
        canonical_bytes = json.dumps(
            {
                "schema_version": current["schema_version"],
                "document_id": document_id,
                "document_revision": current["revision"],
                "content": current["content"],
                "settings": current["settings"],
                "comments": current["comments"],
                "import_report": current["import_report"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        canonical_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
        try:
            docx_bytes = base64.b64decode(command.docx_base64, validate=True)
            pdf_bytes = base64.b64decode(command.pdf_base64, validate=True)
            stored_docx, stored_pdf = local_artifacts.store_publication_pair(
                job_id=str(current["job_id"]),
                document_id=document_id,
                document_revision=command.expected_revision,
                docx=ArtifactWrite(
                    command.docx_filename,
                    DOCX_MEDIA_TYPE,
                    docx_bytes,
                    command.docx_sha256,
                ),
                pdf=ArtifactWrite(
                    command.pdf_filename,
                    PDF_MEDIA_TYPE,
                    pdf_bytes,
                    command.pdf_sha256,
                ),
            )
        except (ArtifactStorageError, OSError, sqlite3.OperationalError) as error:
            raise HTTPException(
                status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
            ) from error
        except (ArtifactValidationError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        def mark_published(connection: sqlite3.Connection) -> dict[str, object]:
            first_sequence = state_store.next_document_artifact_sequence(
                str(current["job_id"]), connection=connection
            )
            document_key = cast(
                Literal["resume", "cover_letter", "references"],
                current["document_key"],
            )
            verified_pair = [
                VerifiedArtifact(
                    job_id=str(current["job_id"]),
                    document_key=document_key,
                    document_label=str(current["document_label"]),
                    source_revision=(
                        f"editable:{document_id}:{command.expected_revision}:{canonical_sha256}"
                    ),
                    artifact_revision=stored.sha256,
                    media_type=stored.media_type,
                    sha256=stored.sha256,
                    render_status="succeeded",
                    render_sequence=first_sequence + offset,
                    canonical_path=str(stored.canonical_path),
                    filename=stored.filename,
                    failure_message=None,
                )
                for offset, stored in enumerate((stored_docx, stored_pdf))
            ]
            state_store.register_editable_publication_pair(
                str(current["job_id"]),
                verified_pair,
                editable_document_id=document_id,
                editable_document_revision=command.expected_revision,
                connection=connection,
            )
            row = state_store.mark_editable_document_published(
                document_id,
                expected_revision=command.expected_revision,
                connection=connection,
            )
            return as_document(row).model_dump(mode="json")

        try:
            result = atomic_editable_mutation(
                identity=identity,
                target=f"editable-documents/{document_id}",
                command_name="document.editor.publish",
                origin="user",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                mutation=mark_published,
                label=f"Published {current['document_label']} revision {command.expected_revision}",
                job_id=str(current["job_id"]),
            )
        except EditableDocumentConflict as error:
            raise editable_conflict(error) from error
        except EditablePublicationConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Editable document not found") from error
        except (OSError, sqlite3.OperationalError) as error:
            raise HTTPException(
                status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
            ) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return EditableDocument.model_validate(result)

    def artifact_list(job_id: str) -> JobArtifactsResponse:
        rows, current_id, last_successful_id, approved_id = state_store.list_document_artifacts(
            job_id
        )
        return JobArtifactsResponse(
            job_id=job_id,
            artifacts=[
                artifact_record(
                    row,
                    current_id=current_id,
                    last_successful_id=last_successful_id,
                    approved_id=approved_id,
                )
                for row in rows
            ],
            current_artifact_id=current_id,
            last_successful_artifact_id=last_successful_id,
            approved_artifact_id=approved_id,
        )

    def registered_artifact_payload(record: dict[str, object]) -> bytes:
        registered_path = Path(str(record["canonical_path"])).expanduser()
        local_root = local_artifacts.root
        if registered_path == local_root or local_root in registered_path.parents:
            try:
                return local_artifacts.read(
                    path=registered_path,
                    media_type=str(record["media_type"]),
                    expected_sha256=str(record["sha256"]),
                )
            except (ArtifactStorageError, OSError) as error:
                raise HTTPException(
                    status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
                ) from error
        try:
            _, payload = read_source_artifact(
                {
                    "job_id": record["job_id"],
                    "document_key": record["document_key"],
                    "document_label": record["document_label"],
                    "source_revision": record["source_revision"],
                    "artifact_revision": record["artifact_revision"],
                    "media_type": record["media_type"],
                    "sha256": record["sha256"],
                    "render_status": record["render_status"],
                    # Registry identity is already fixed; sequence is only used to
                    # select facade current/last-successful pointers during refresh.
                    "render_sequence": 0,
                    "path": record["canonical_path"],
                },
                trusted_artifact_roots,
            )
        except ArtifactStorageError as error:
            raise HTTPException(
                status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
            ) from error
        except (ArtifactTrustError, OSError) as error:
            raise HTTPException(
                status_code=409,
                detail="Registered artifact no longer matches trusted metadata",
            ) from error
        if payload is None:
            raise HTTPException(status_code=409, detail="Artifact render is not available")
        return payload

    @app.get("/v1/jobs/{job_id}/artifacts", tags=["documents"])
    def job_artifacts(
        job_id: str,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        origin: Literal["mcp"] | None = None,
        idempotency_key: str | None = None,
    ) -> JobArtifactsResponse:
        ensure_job(job_id)
        result = artifact_list(job_id)
        record_agent_read(
            identity=identity,
            origin=origin,
            idempotency_key=idempotency_key,
            command_name="document.list",
            label="Inspected resume artifacts",
            detail={"job_id": job_id, "count": len(result.artifacts)},
        )
        return result

    @app.post(
        "/v1/jobs/{job_id}/artifacts/{artifact_id}/approve",
        tags=["documents"],
    )
    @serialized_mutation_route
    def approve_job_artifact(
        job_id: str,
        artifact_id: str,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        command: ArtifactApprovalRequest | None = None,
    ) -> JobArtifactsResponse:
        command = command or ArtifactApprovalRequest()
        if command.origin != "user":
            raise HTTPException(
                status_code=403,
                detail=(
                    "Agents may endorse a document, but only the authenticated user "
                    "can approve it"
                ),
            )
        ensure_job(job_id)
        artifact = state_store.get_document_artifact(artifact_id)
        if (
            artifact is None
            or artifact["job_id"] != job_id
            or artifact["document_key"] not in {"resume", "cover_letter"}
            or artifact["media_type"] not in {PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE}
            or artifact["render_status"] != "succeeded"
            or not artifact["canonical_path"]
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Only a successful resume or cover-letter representation registered "
                    "for this job can be approved"
                ),
            )
        representations = state_store.approval_representation_artifacts(job_id, artifact_id)
        if not representations:
            raise HTTPException(
                status_code=409,
                detail="Document revision has no successful representation",
            )
        for representation in representations:
            registered_artifact_payload(representation)
        request_hash = mutation_hash(
            "document.approve",
            {"job_id": job_id, "artifact_id": artifact_id, "origin": command.origin},
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"jobs/{job_id}/artifacts/{artifact_id}",
                command_name="document.approve",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobArtifactsResponse.model_validate(replay)
        try:
            state_store.approve_document_artifact(job_id, artifact_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result = artifact_list(job_id)
        record_mutation(
            identity=identity,
            target=f"jobs/{job_id}/artifacts/{artifact_id}",
            command_name="document.approve",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(mode="json"),
            label="Approved resume revision",
            job_id=job_id,
            detail={"artifact_id": artifact_id},
        )
        return result

    @app.post("/v1/jobs/{job_id}/artifacts/refresh", tags=["documents"])
    @serialized_mutation_route
    def refresh_job_artifacts(
        job_id: str,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        command: ArtifactRefreshRequest | None = None,
    ) -> JobArtifactsResponse:
        command = command or ArtifactRefreshRequest()
        ensure_job(job_id)
        request_hash = mutation_hash(
            "document.refresh", {"job_id": job_id, "origin": command.origin}
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"jobs/{job_id}/artifacts",
                command_name="document.refresh",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobArtifactsResponse.model_validate(replay)
        try:
            raw_artifacts = artifacts.list_job_artifacts(job_id)
            verified = verify_facade_artifacts(
                raw_artifacts, trusted_artifact_roots, expected_job_id=job_id
            )
            state_store.register_document_artifacts(
                job_id,
                verified.artifacts,
                invalidated_registry_keys=verified.superseded_registry_keys,
            )
        except (ArtifactStorageError, OSError) as error:
            raise HTTPException(
                status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
            ) from error
        except (ArtifactTrustError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        result = artifact_list(job_id)
        record_mutation(
            identity=identity,
            target=f"jobs/{job_id}/artifacts",
            command_name="document.refresh",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(mode="json"),
            label="Refreshed resume artifacts",
            job_id=job_id,
        )
        return result

    @app.post("/v1/jobs/{job_id}/artifacts/render", tags=["documents"])
    @serialized_mutation_route
    def render_job_artifact(
        job_id: str,
        command: ResumeRenderRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobArtifactsResponse:
        ensure_job(job_id)
        request_hash = mutation_hash(
            "document.render",
            {
                "job_id": job_id,
                "source_id": command.source_id,
                "output_format": command.output_format,
                "origin": command.origin,
            },
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"jobs/{job_id}/artifacts",
                command_name="document.render",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobArtifactsResponse.model_validate(replay)
        try:
            raw = artifacts.render_resume(
                job_id, command.source_id, {"format": command.output_format}
            )
            verified = verify_source_artifact(raw, trusted_artifact_roots)
            state_store.register_document_artifacts(job_id, [verified])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Resume source not found") from error
        except (ArtifactStorageError, OSError) as error:
            raise HTTPException(
                status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
            ) from error
        except (ArtifactTrustError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        result = artifact_list(job_id)
        render_succeeded = verified.render_status == "succeeded"
        record_mutation(
            identity=identity,
            target=f"jobs/{job_id}/artifacts",
            command_name="document.render",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(mode="json"),
            label=("Rendered resume artifact" if render_succeeded else "Resume render failed"),
            outcome="completed" if render_succeeded else "failed",
            job_id=job_id,
            detail={"source_id": command.source_id, "artifact_id": result.current_artifact_id},
        )
        return result

    @app.post("/v1/jobs/{job_id}/artifacts/register", tags=["documents"])
    def register_job_artifact(
        job_id: str,
        command: ArtifactRegistrationRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobArtifactsResponse:
        ensure_job(job_id)
        with serialize_mutation((identity.device_id, command.idempotency_key)):
            request_hash = mutation_hash(
                "document.register",
                {
                    "job_id": job_id,
                    "artifact_reference": command.artifact_reference,
                    "origin": command.origin,
                },
            )
            try:
                replay = mutation_replay(
                    identity=identity,
                    target=f"jobs/{job_id}/artifacts",
                    command_name="document.register",
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                )
            except IdempotencyConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if replay is not None:
                return JobArtifactsResponse.model_validate(replay)
            try:
                raw = artifacts.register_artifact(job_id, command.artifact_reference)
                verified = verify_source_artifact(raw, trusted_artifact_roots)
                state_store.register_document_artifacts(job_id, [verified])
            except KeyError as error:
                raise HTTPException(
                    status_code=404, detail="Artifact reference not found"
                ) from error
            except (ArtifactStorageError, OSError) as error:
                raise HTTPException(
                    status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
                ) from error
            except (ArtifactTrustError, ValueError) as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            result = artifact_list(job_id)
            record_mutation(
                identity=identity,
                target=f"jobs/{job_id}/artifacts",
                command_name="document.register",
                origin=command.origin,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                result=result.model_dump(mode="json"),
                label="Registered resume artifact",
                job_id=job_id,
                detail={"artifact_id": result.current_artifact_id},
            )
            return result

    @app.post("/v1/jobs/{job_id}/artifacts/publish", tags=["documents"])
    @serialized_mutation_route
    def publish_job_artifact(
        job_id: str,
        command: ArtifactPublishRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> JobArtifactsResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        ensure_job(job_id)
        source_bytes = command.source_bytes()
        artifact_bytes = command.artifact_bytes()
        source_revision = hashlib.sha256(source_bytes).hexdigest()
        artifact_revision = hashlib.sha256(artifact_bytes).hexdigest()
        request_hash = mutation_hash(
            "document.publish",
            {
                "job_id": job_id,
                "document_key": command.document_key,
                "document_label": command.document_label,
                "source_filename": command.source_filename,
                "source_sha256": source_revision,
                "artifact_filename": command.artifact_filename,
                "artifact_sha256": artifact_revision,
                "origin": command.origin,
            },
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target=f"jobs/{job_id}/artifacts",
                command_name="document.publish",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobArtifactsResponse.model_validate(replay)
        try:
            suffix = Path(command.artifact_filename).suffix.casefold()
            media_type = {
                ".pdf": PDF_MEDIA_TYPE,
                ".docx": DOCX_MEDIA_TYPE,
            }.get(suffix)
            if media_type is None:
                raise ArtifactValidationError("Published artifact must be a PDF or DOCX")
            stored = local_artifacts.store_agent_publication(
                job_id=job_id,
                document_key=command.document_key,
                source_revision=source_revision,
                artifact=ArtifactWrite(
                    filename=command.artifact_filename,
                    media_type=media_type,
                    content=artifact_bytes,
                    sha256=artifact_revision,
                ),
            )
            verified = VerifiedArtifact(
                job_id=job_id,
                document_key=command.document_key,
                document_label=command.document_label,
                source_revision=source_revision,
                artifact_revision=artifact_revision,
                media_type=media_type,
                sha256=artifact_revision,
                render_status="succeeded",
                render_sequence=state_store.next_document_artifact_sequence(job_id),
                canonical_path=str(stored.canonical_path),
                filename=stored.filename,
                failure_message=None,
            )
            state_store.register_document_artifacts(job_id, [verified])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        except (ArtifactStorageError, OSError) as error:
            raise HTTPException(
                status_code=503, detail=LOCAL_ARTIFACT_STORAGE_UNAVAILABLE
            ) from error
        except (ArtifactTrustError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        result = artifact_list(job_id)
        published = next(
            artifact
            for artifact in result.artifacts
            if artifact.artifact_revision == verified.artifact_revision
            and artifact.media_type == verified.media_type
        )
        record_mutation(
            identity=identity,
            target=f"jobs/{job_id}/artifacts",
            command_name="document.publish",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(mode="json"),
            label=f"Published {command.document_label} {suffix[1:].upper()}",
            job_id=job_id,
            detail={"artifact_id": published.artifact_id, "document_key": command.document_key},
        )
        return result

    @app.post("/v1/activity", tags=["activity"])
    @serialized_mutation_route
    def report_activity(
        command: ActivityReportRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ActivityReportResponse:
        request_hash = mutation_hash(
            "activity.report",
            {
                "label": command.label,
                "state": command.state,
                "detail": command.detail,
                "origin": command.origin,
            },
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target="activity",
                command_name="activity.report",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return ActivityReportResponse.model_validate(replay)
        event_id = state_store.record_mutation_result(
            event_type="agent_action",
            origin="mcp",
            actor_id=identity.device_id,
            target_resource="activity",
            command_name="activity.report",
            outcome=command.state,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result={},
            detail={
                **command.detail,
                "label": command.label,
                "state": command.state,
                "origin": "mcp",
                "outcome": command.state,
            },
            inject_event_id=True,
        )
        return ActivityReportResponse(event_id=event_id)

    def registered_artifact_response(artifact_id: str, *, preview: bool) -> Response:
        if not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
            raise HTTPException(status_code=404, detail="Artifact not found")
        record = state_store.get_document_artifact(artifact_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        if record["render_status"] != "succeeded" or not record["canonical_path"]:
            raise HTTPException(status_code=409, detail="Artifact render is not available")
        if preview and record["media_type"] != PDF_MEDIA_TYPE:
            raise HTTPException(
                status_code=415,
                detail="Only authoritative PDF artifacts can be previewed in JobOS",
            )
        payload = registered_artifact_payload(record)
        headers = content_headers(record)
        disposition = "inline" if preview else "attachment"
        filename = quote(headers.filename, safe="")
        return Response(
            content=payload,
            media_type=headers.media_type,
            headers={
                "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
                "Digest": headers.digest,
                "ETag": f'"{headers.sha256}"',
                "X-Artifact-ID": headers.artifact_id,
                "X-Artifact-Revision": headers.artifact_revision,
                "X-Source-Revision": headers.source_revision,
                "X-Content-SHA256": headers.sha256,
            },
        )

    @app.get("/v1/artifacts/{artifact_id}/content", tags=["documents"])
    def artifact_content(
        artifact_id: str,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> Response:
        return registered_artifact_response(artifact_id, preview=True)

    @app.get("/v1/artifacts/{artifact_id}/download", tags=["documents"])
    def artifact_download(
        artifact_id: str,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> Response:
        return registered_artifact_response(artifact_id, preview=False)

    @app.get("/v1/events", tags=["events"])
    def events_list(
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
        after: int = 0,
    ) -> JobEventsResponse:
        return JobEventsResponse(events=state_store.list_job_events(after))

    @app.get("/v1/events/stream", tags=["events"])
    async def events_stream(
        request: Request,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
        after: int = 0,
        once: bool = False,
    ) -> StreamingResponse:
        async def event_source() -> AsyncIterator[str]:
            cursor = after
            while True:
                events = state_store.list_job_events(cursor)
                for event in events:
                    cursor = int(event["event_id"])
                    payload = json.dumps(event, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: jobos\ndata: {payload}\n\n"
                if once or await request.is_disconnected():
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(event_source(), media_type="text/event-stream")

    _configure_openapi_error_responses(app)
    return app

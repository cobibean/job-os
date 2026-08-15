import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from functools import wraps
from threading import Lock
from typing import Annotated, Literal, ParamSpec, TypeVar, cast
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jobos_api import __version__
from jobos_api.activity import ActivityReportRequest, ActivityReportResponse
from jobos_api.agent_gateway import AgentGateway, OfflineAgentGateway
from jobos_api.artifact_gateway import ArtifactGateway
from jobos_api.capabilities import (
    BrowserCommandRequest,
    BrowserCommandResponse,
    CapabilityBroker,
    DesktopCapabilityPresence,
    DesktopUnavailable,
)
from jobos_api.composition import create_job_services
from jobos_api.conversations import (
    ConversationResponse,
    ConversationService,
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
    PDF_MEDIA_TYPE,
    ArtifactApprovalRequest,
    ArtifactPublishRequest,
    ArtifactRefreshRequest,
    ArtifactRegistrationRequest,
    ArtifactTrustError,
    JobArtifactsResponse,
    ResumeRenderRequest,
    artifact_record,
    content_headers,
    materialize_external_import,
    materialize_published_document,
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
from jobos_api.hermes_adapter import HermesWebSocketGateway
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
from jobos_api.redaction import sanitize_text
from jobos_api.responses import DeviceSessionResponse, HealthResponse, VersionResponse
from jobos_api.settings import Settings
from jobos_api.state_store import (
    ConversationBusy,
    EditableDocumentConflict,
    IdempotencyConflict,
    JobOsStateStore,
    WorkspaceRevisionConflict,
)
from jobos_api.workspace import WorkspaceSnapshotCommand, WorkspaceSnapshotResponse

P = ParamSpec("P")
R = TypeVar("R")


def create_app(
    settings: Settings,
    *,
    job_repository: JobRepository | None = None,
    artifact_gateway: ArtifactGateway | None = None,
    agent_gateway: AgentGateway | None = None,
    capability_broker: CapabilityBroker | None = None,
    state_store: JobOsStateStore | None = None,
) -> FastAPI:
    state_store = state_store or JobOsStateStore(settings.state_db_path)
    if job_repository is None or artifact_gateway is None:
        composed_jobs, composed_artifacts = create_job_services(settings)
        job_repository = job_repository or composed_jobs
        artifact_gateway = artifact_gateway or composed_artifacts
    jobs = job_repository
    artifacts = artifact_gateway
    device_authenticator = DeviceAuthenticator(settings.device_credential_registry())
    bearer = HTTPBearer(auto_error=False)
    configured_gateway = agent_gateway
    if configured_gateway is None and all(
        (
            settings.hermes_dashboard_url,
            settings.hermes_dashboard_token,
            settings.hermes_job_hunter_cwd,
        )
    ):
        configured_gateway = HermesWebSocketGateway(
            url=str(settings.hermes_dashboard_url),
            token=str(settings.hermes_dashboard_token),
            cwd=settings.hermes_job_hunter_cwd,  # type: ignore[arg-type]
            request_timeout=settings.hermes_request_timeout,
        )
    conversation_service = ConversationService(
        state_store, configured_gateway or OfflineAgentGateway()
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
        state_store.initialize()
        jobs.initialize()
        await conversation_service.start()
        try:
            yield
        finally:
            await conversation_service.close()

    app = FastAPI(
        title="JobOS API",
        version=__version__,
        lifespan=lifespan,
    )

    @app.exception_handler(JobRepositoryError)
    async def job_repository_error_handler(
        _: Request, error: JobRepositoryError
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
        return JSONResponse(status_code=status_code, content={"detail": str(error)})

    @app.get("/v1/health", tags=["system"])
    def health() -> HealthResponse:
        state_health = state_store.health()
        return HealthResponse(
            status="ready",
            service="jobos-api",
            version=__version__,
            state_schema=state_health.schema_version,
            agent_connection=conversation_service.gateway.connection_state,
        )

    @app.get("/v1/version", tags=["system"])
    def version() -> VersionResponse:
        return VersionResponse(api_version=__version__, contract="jobos-v1-phase7-parity")

    def authenticated_device(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> DeviceIdentity:
        return device_authenticator.authenticate(credentials)

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
        responses={403: {"description": "Trusted MCP credential required"}},
    )
    async def browser_command(
        command: BrowserCommandRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> BrowserCommandResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
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
        request_hash = hashlib.sha256(
            json.dumps(
                {"command": command.command, "arguments": arguments, "origin": command.origin},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        durable_command = command.command not in {
            "tabs.inspect", "page.snapshot", "document.inspect"
        }
        target_device_id = (
            state_store.active_turn_origin_device_id() or identity.device_id
            if command.origin == "mcp"
            else identity.device_id
        )
        target = (
            f"document/{arguments['job_id']}/{arguments['document_key']}"
            if command.command.startswith("document.")
            else f"browser/{target_device_id}/{arguments.get('tab_id', 'desktop')}"
        )

        def observe_document_result(result: BrowserCommandResponse) -> None:
            if not command.command.startswith("document.") or result.state != "completed":
                return
            state_store.observe_document_file(
                observed_document_file(
                    arguments["job_id"],
                    result.data,
                    observed_at=(
                        datetime.now(UTC)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z")
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

    @app.get("/v1/device-session", tags=["system"])
    def device_session(
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> DeviceSessionResponse:
        return DeviceSessionResponse(
            authenticated=True,
            transport="private-tailscale",
            api_version=__version__,
        )

    @app.get("/v1/conversations/current", tags=["agent"])
    def conversation_current(
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ConversationResponse:
        return conversation_service.snapshot()

    @app.post("/v1/conversations/current/reset", tags=["agent"])
    async def conversation_reset(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> ConversationResponse:
        try:
            return await conversation_service.reset(actor_id=identity.device_id)
        except ConversationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    def conversation_context(identity: DeviceIdentity) -> dict[str, object]:
        selection = state_store.job_workspace_state().selected_job_id
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
                    "active_artifact_id",
                    "active_artifact_page",
                    "active_artifact_zoom",
                )
            },
        }

    @app.post(
        "/v1/conversations/current/messages",
        tags=["agent"],
        status_code=201,
    )
    async def conversation_send(
        command: SendMessageRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> TurnMutationResponse:
        try:
            return await conversation_service.send(
                command,
                actor_id=identity.device_id,
                context=conversation_context(identity),
            )
        except ConversationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/v1/conversations/current/turns/{turn_id}/cancel", tags=["agent"])
    async def conversation_cancel(
        turn_id: str,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> TurnMutationResponse:
        result = await conversation_service.cancel(turn_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Turn not found")
        return result

    @app.post(
        "/v1/conversations/current/turns/{turn_id}/retry",
        tags=["agent"],
        status_code=201,
    )
    async def conversation_retry(
        turn_id: str,
        command: RetryTurnRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> TurnMutationResponse:
        try:
            result = await conversation_service.retry(turn_id, command, actor_id=identity.device_id)
        except ConversationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if result is None:
            raise HTTPException(status_code=404, detail="Turn not found")
        return result

    @app.get("/v1/conversations/current/events/stream", tags=["agent"])
    async def conversation_stream(
        request: Request,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
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
                state_store, request, cursor=cursor, once=once
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
        responses={403: {"description": "Trusted MCP credential required"}},
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
        # The canonical job commits first. A retry converges on its unique URL,
        # then the existing workbench idempotency ledger records selection/audit.
        event_id = state_store.save_job_selection(normalized.job_id, command.origin)
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
        responses={403: {"description": "Trusted MCP credential required"}},
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
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> WorkspaceJobsResponse:
        state = state_store.job_workspace_state()
        return WorkspaceJobsResponse(
            selected_job_id=state.selected_job_id,
            sort_mode=state.sort_mode,
            manual_order=state.manual_order,
        )

    @app.get("/v1/workspace", tags=["workspace"])
    def workspace_get(
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        origin: Literal["mcp"] | None = None,
        idempotency_key: str | None = None,
    ) -> WorkspaceSnapshotResponse:
        record = state_store.workspace_snapshot(identity.device_id)
        result = WorkspaceSnapshotResponse(
            revision=record.revision,
            repaired_presets=list(record.repaired_presets),
            repaired_browser=record.repaired_browser,
            browser_repair_reasons=list(record.browser_repair_reasons),
            **record.snapshot,
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
        responses={403: {"description": "Trusted MCP credential required"}},
    )
    def workspace_put(
        command: WorkspaceSnapshotCommand,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> WorkspaceSnapshotResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        if command.active_artifact_id is not None:
            artifact = state_store.get_document_artifact(command.active_artifact_id)
            selected_job_id = state_store.job_workspace_state().selected_job_id
            if (
                artifact is None
                or selected_job_id is None
                or command.selected_job_id != selected_job_id
                or artifact["job_id"] != selected_job_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Active artifact does not belong to the selected job",
                )
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
        responses={403: {"description": "Trusted MCP credential required"}},
    )
    @serialized_mutation_route
    def workspace_select_job(
        command: JobSelectionRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
        mcp_token: Annotated[str | None, Header(alias="X-JobOS-MCP-Token")] = None,
    ) -> JobMutationResponse:
        require_trusted_mcp(identity, command.origin, mcp_token)
        request_hash = mutation_hash(
            "job.select", {"job_id": command.job_id, "origin": command.origin}
        )
        try:
            replay = mutation_replay(
                identity=identity,
                target="workspace/jobs",
                command_name="job.select",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if replay is not None:
            return JobMutationResponse.model_validate(replay)
        known_ids = {job.job_id for job in jobs.list_jobs()}
        if command.job_id not in known_ids:
            raise HTTPException(status_code=404, detail="Job not found")
        event_id = state_store.save_job_selection(command.job_id, command.origin)
        result = JobMutationResponse(event_id=event_id)
        record_mutation(
            identity=identity,
            target="workspace/jobs",
            command_name="job.select",
            origin=command.origin,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(),
            label=f"Selected job {command.job_id}",
            job_id=command.job_id,
        )
        return result

    @app.put(
        "/v1/workspace/jobs/sort",
        tags=["workspace"],
        responses={403: {"description": "Trusted MCP credential required"}},
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

    @app.put(
        "/v1/jobs/{job_id}/description",
        tags=["jobs"],
        responses={403: {"description": "Trusted MCP credential required"}},
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
        responses={403: {"description": "Trusted MCP credential required"}},
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
    ) -> tuple[str, str | None, str]:
        if source.mode == "import_registered_artifact":
            artifact = state_store.editable_import_source(job_id, source.source_artifact_id)
            return (
                source.source_artifact_id,
                str(artifact["filename"]) if artifact.get("filename") else None,
                str(artifact["sha256"]),
            )
        if settings.hermes_job_hunter_cwd is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "External DOCX import requires the configured Job Hunter publication workspace"
                ),
            )
        source_bytes = source.source_bytes()
        manifest_path, artifact_path = materialize_external_import(
            job_id=job_id,
            document_id=document_id,
            document_key=source.document_key,
            source_filename=source.source_filename,
            source_sha256=source.source_sha256,
            source_bytes=source_bytes,
            workspace_root=settings.hermes_job_hunter_cwd,
        )
        raw = artifacts.publish_document_artifact(
            job_id,
            source.document_key,
            LABELS[source.document_key],
            str(manifest_path),
            str(artifact_path),
        )
        verified = verify_source_artifact(raw, settings.artifact_roots)
        if (
            verified.job_id != job_id
            or verified.document_key != source.document_key
            or verified.document_label != LABELS[source.document_key]
            or verified.media_type
            != "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or verified.render_status != "succeeded"
            or verified.sha256 != source.source_sha256
        ):
            raise ArtifactTrustError("Published external DOCX metadata does not match the import")
        artifact_id, _ = state_store.register_document_artifacts(job_id, [verified])
        if artifact_id is None:
            raise ArtifactTrustError("Published external DOCX was not registered")
        return artifact_id, source.source_filename, source.source_sha256

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
            source_artifact_id = source_filename = source_sha256 = None
            imported = False
            document_id = None
            validate_content(content, DocumentSettings.model_validate(settings_value), [])
        else:
            if state_store.get_job_editable_document(job_id, command.document_key) is not None:
                raise HTTPException(
                    status_code=409,
                    detail="An editable document already exists for this job and type",
                )
            document_id = state_store.new_editable_document_id()
            try:
                source_artifact_id, source_filename, source_sha256 = resolve_import_source(
                    job_id, document_id, command
                )
            except (ArtifactTrustError, OSError, ValueError) as error:
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
            source_artifact_id, source_filename, source_sha256 = resolve_import_source(
                str(current["job_id"]), document_id, command.source
            )

            def replace_document(connection: sqlite3.Connection) -> dict[str, object]:
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
                detail={"source_artifact_id": source_artifact_id},
            )
        except EditableDocumentConflict as error:
            raise editable_conflict(error) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Editable document not found") from error
        except (ArtifactTrustError, OSError, ValueError) as error:
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
                label="Applied JobHunter document edits",
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
        if unresolved_suggestion_count(content):
            raise HTTPException(
                status_code=409,
                detail="Resolve all document suggestions before publication",
            )
        if settings.hermes_job_hunter_cwd is None:
            raise HTTPException(status_code=503, detail="Job Hunter workspace is unavailable")

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
        publication_source_bytes = json.dumps(
            {
                "schema_version": current["schema_version"],
                "document_id": document_id,
                "document_revision": current["revision"],
                "canonical_sha256": canonical_sha256,
                "original_source_sha256": current["source_sha256"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        publication_source_base64 = base64.b64encode(publication_source_bytes).decode("ascii")
        publication_source_sha256 = hashlib.sha256(publication_source_bytes).hexdigest()
        try:
            state_store.ensure_editable_publication_snapshot(
                document_id,
                expected_revision=command.expected_revision,
                actor="user",
            )
        except EditableDocumentConflict as error:
            raise editable_conflict(error) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Editable document not found") from error

        publications = (
            (command.docx_filename, command.docx_base64, "docx"),
            (command.pdf_filename, command.pdf_base64, "pdf"),
        )
        try:
            for artifact_filename, artifact_base64, extension in publications:
                publish_command = ArtifactPublishRequest(
                    document_key=cast(
                        Literal["resume", "cover_letter", "references"],
                        current["document_key"],
                    ),
                    document_label=str(current["document_label"]),
                    source_filename="publication-source.json",
                    source_base64=publication_source_base64,
                    artifact_filename=artifact_filename,
                    artifact_base64=artifact_base64,
                    origin="user",
                    idempotency_key=(
                        f"editable-{hashlib.sha256(f'{document_id}:{command.expected_revision}:{extension}'.encode()).hexdigest()[:32]}"
                    ),
                )
                artifact_request_hash = mutation_hash(
                    "document.publish",
                    {
                        "job_id": current["job_id"],
                        "document_key": current["document_key"],
                        "document_label": current["document_label"],
                        "source_filename": "publication-source.json",
                        "source_sha256": publication_source_sha256,
                        "artifact_filename": artifact_filename,
                        "artifact_sha256": (
                            command.docx_sha256 if extension == "docx" else command.pdf_sha256
                        ),
                        "origin": "user",
                    },
                )
                artifact_replay = mutation_replay(
                    identity=identity,
                    target=f"jobs/{current['job_id']}/artifacts",
                    command_name="document.publish",
                    idempotency_key=publish_command.idempotency_key,
                    request_hash=artifact_request_hash,
                )
                if artifact_replay is not None:
                    continue
                source_path, artifact_path = materialize_published_document(
                    publish_command,
                    job_id=str(current["job_id"]),
                    workspace_root=settings.hermes_job_hunter_cwd,
                )
                raw = artifacts.publish_document_artifact(
                    str(current["job_id"]),
                    str(current["document_key"]),
                    str(current["document_label"]),
                    str(source_path),
                    str(artifact_path),
                )
                verified = verify_source_artifact(raw, settings.artifact_roots)
                state_store.register_document_artifacts(
                    str(current["job_id"]),
                    [verified],
                    editable_document_id=document_id,
                    editable_document_revision=command.expected_revision,
                )
                publication_state = artifact_list(str(current["job_id"]))
                record_mutation(
                    identity=identity,
                    target=f"jobs/{current['job_id']}/artifacts",
                    command_name="document.publish",
                    origin="user",
                    idempotency_key=publish_command.idempotency_key,
                    request_hash=artifact_request_hash,
                    result=publication_state.model_dump(mode="json"),
                    label=f"Published {current['document_label']} {extension.upper()}",
                    job_id=str(current["job_id"]),
                    detail={
                        "artifact_id": publication_state.current_artifact_id,
                        "document_key": current["document_key"],
                    },
                )
        except (ArtifactTrustError, OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        def mark_published(connection: sqlite3.Connection) -> dict[str, object]:
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
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Editable document not found") from error
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
                settings.artifact_roots,
            )
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
        ensure_job(job_id)
        artifact = state_store.get_document_artifact(artifact_id)
        if (
            artifact is None
            or artifact["job_id"] != job_id
            or artifact["document_key"] != "resume"
            or artifact["media_type"] != PDF_MEDIA_TYPE
            or artifact["render_status"] != "succeeded"
            or not artifact["canonical_path"]
        ):
            raise HTTPException(
                status_code=409,
                detail="Only a successful artifact registered for this job can be approved",
            )
        registered_artifact_payload(artifact)
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
            verified = verify_facade_artifacts(raw_artifacts, settings.artifact_roots)
            state_store.register_document_artifacts(job_id, verified)
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
            verified = verify_source_artifact(raw, settings.artifact_roots)
            state_store.register_document_artifacts(job_id, [verified])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Resume source not found") from error
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
                verified = verify_source_artifact(raw, settings.artifact_roots)
                state_store.register_document_artifacts(job_id, [verified])
            except KeyError as error:
                raise HTTPException(
                    status_code=404, detail="Artifact reference not found"
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
        if settings.hermes_job_hunter_cwd is None:
            raise HTTPException(status_code=503, detail="Job Hunter workspace is unavailable")
        source_bytes = command.source_bytes()
        artifact_bytes = command.artifact_bytes()
        request_hash = mutation_hash(
            "document.publish",
            {
                "job_id": job_id,
                "document_key": command.document_key,
                "document_label": command.document_label,
                "source_filename": command.source_filename,
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "artifact_filename": command.artifact_filename,
                "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
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
            source_path, artifact_path = materialize_published_document(
                command,
                job_id=job_id,
                workspace_root=settings.hermes_job_hunter_cwd,
            )
            raw = artifacts.publish_document_artifact(
                job_id,
                command.document_key,
                command.document_label,
                str(source_path),
                str(artifact_path),
            )
            verified = verify_source_artifact(raw, settings.artifact_roots)
            state_store.register_document_artifacts(job_id, [verified])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        except (ArtifactTrustError, OSError, ValueError) as error:
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
            label=f"Published {command.document_label} {artifact_path.suffix[1:].upper()}",
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

    return app

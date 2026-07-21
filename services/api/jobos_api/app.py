import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
from threading import Lock
from typing import Annotated, Literal, ParamSpec, TypeVar, cast
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jobos_api import __version__
from jobos_api.activity import ActivityReportRequest, ActivityReportResponse
from jobos_api.adapters import create_job_hunter_adapter
from jobos_api.agent_gateway import AgentGateway, OfflineAgentGateway
from jobos_api.capabilities import (
    BrowserCommandRequest,
    BrowserCommandResponse,
    CapabilityBroker,
    DesktopCapabilityPresence,
    DesktopUnavailable,
)
from jobos_api.conversations import (
    ConversationResponse,
    ConversationService,
    RetryTurnRequest,
    SendMessageRequest,
    TurnMutationResponse,
    conversation_event_source,
)
from jobos_api.device_auth import DeviceAuthenticator, DeviceIdentity
from jobos_api.documents import (
    ARTIFACT_ID_PATTERN,
    PDF_MEDIA_TYPE,
    ArtifactApprovalRequest,
    ArtifactRefreshRequest,
    ArtifactRegistrationRequest,
    ArtifactTrustError,
    JobArtifactsResponse,
    ResumeRenderRequest,
    artifact_record,
    content_headers,
    read_source_artifact,
    verify_facade_artifacts,
    verify_source_artifact,
)
from jobos_api.hermes_adapter import HermesWebSocketGateway
from jobos_api.jobs import (
    BrowserJobCreateRequest,
    BrowserJobCreateResponse,
    JobDetail,
    JobEventsResponse,
    JobFacade,
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
    IdempotencyConflict,
    JobOsStateStore,
    WorkspaceRevisionConflict,
    mutation_activity_source_id,
)
from jobos_api.workspace import WorkspaceSnapshotCommand, WorkspaceSnapshotResponse

P = ParamSpec("P")
R = TypeVar("R")


def create_app(
    settings: Settings,
    *,
    job_facade: JobFacade | None = None,
    agent_gateway: AgentGateway | None = None,
    capability_broker: CapabilityBroker | None = None,
) -> FastAPI:
    state_store = JobOsStateStore(settings.state_db_path)
    jobs = job_facade or create_job_hunter_adapter(
        settings.job_hunter_db_path,
        settings.hermes_job_hunter_cwd,
    )
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
        if result is not None:
            state_store.ensure_mutation_activity(
                actor_id=identity.device_id,
                target_resource=target,
                command_name=command_name,
                idempotency_key=idempotency_key,
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
    ) -> int:
        event_id = state_store.record_mutation_result(
            event_type="agent_action" if origin == "mcp" else "user_action",
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
        )
        if origin == "mcp":
            state_store.ensure_conversation_event(
                turn_id=None,
                event_type="activity",
                state="completed" if outcome == "completed" else "failed",
                summary=label,
                detail={
                    "origin": origin,
                    "command": command_name,
                    "outcome": outcome,
                    **(detail or {}),
                },
                source_event_id=mutation_activity_source_id(
                    actor_id=identity.device_id,
                    target_resource=target,
                    command_name=command_name,
                    idempotency_key=idempotency_key,
                ),
            )
        return event_id

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
        state_store.append_conversation_event(
            turn_id=None,
            event_type="activity",
            state="completed",
            summary=label,
            detail={
                "origin": "mcp",
                "command": command_name,
                "outcome": "completed",
                **(detail or {}),
            },
            source_event_id=f"read:{identity.device_id}:{command_name}:{idempotency_key}",
        )

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

    @app.post("/v1/browser/commands", tags=["browser"])
    async def browser_command(
        command: BrowserCommandRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> BrowserCommandResponse:
        try:
            arguments = command.validated_arguments()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        request_hash = hashlib.sha256(
            json.dumps(
                {"command": command.command, "arguments": arguments, "origin": command.origin},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        target = f"browser/{arguments.get('tab_id', 'desktop')}"
        durable_command = command.command not in {"tabs.inspect", "page.snapshot"}

        def ensure_activity(result: BrowserCommandResponse) -> None:
            if command.origin != "mcp":
                return
            state_store.ensure_conversation_event(
                turn_id=None,
                event_type="activity",
                state="completed" if result.state == "completed" else "failed",
                summary=f"Browser: {command.command.replace('.', ' ')}",
                detail={
                    "origin": "mcp",
                    "command": command.command,
                    "outcome": result.outcome,
                    "error": result.error.model_dump() if result.error else None,
                },
                source_event_id=mutation_activity_source_id(
                    actor_id=identity.device_id,
                    target_resource=target,
                    command_name=f"browser.{command.command}",
                    idempotency_key=command.idempotency_key,
                ),
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
                    ensure_activity(response)
                    return response
            result = await browser_capabilities.execute(command)
            result_dict = result.model_dump(mode="json")
            if durable_command:
                state_store.record_mutation_result(
                    event_type="browser_action",
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
            ensure_activity(result)
            return result

        try:
            if durable_command:
                async with serialize_browser_command(
                    (identity.device_id, command.idempotency_key)
                ):
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
                job = jobs.inspect_job(selection)
            except (KeyError, ValueError):
                job = None
            if job is not None:
                company = sanitize_text(str(job.get("company", "")))[:200]
                title = sanitize_text(str(job.get("title", "")))[:200]
                if company and title:
                    selected_job = {
                        "job_id": selection,
                        "company": company,
                        "title": title,
                    }
        return {
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

    @app.post("/v1/jobs", tags=["jobs"])
    @serialized_mutation_route
    def job_create_from_browser(
        command: BrowserJobCreateRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> BrowserJobCreateResponse:
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

        try:
            saved = jobs.add_job(
                company_name=command.company_name,
                title=command.title,
                canonical_url=str(command.canonical_url),
                location_text=command.location_text,
                description_text=command.description_text,
                application_url=str(command.application_url),
            )
            normalized = normalize_job_detail(saved["job"])
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        event_id = state_store.save_job_selection(normalized.job_id, command.origin)
        result = BrowserJobCreateResponse(
            event_id=event_id,
            created=bool(saved["created"]),
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

    @app.put("/v1/jobs/order", tags=["jobs"])
    @serialized_mutation_route
    def jobs_reorder(
        command: ManualOrderRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobMutationResponse:
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
        known_ids = {str(job["job_id"]) for job in jobs.list_jobs()}
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

    @app.put("/v1/workspace", tags=["workspace"])
    def workspace_put(
        command: WorkspaceSnapshotCommand,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> WorkspaceSnapshotResponse:
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
        if command.origin == "mcp":
            state_store.ensure_conversation_event(
                turn_id=None,
                event_type="activity",
                state="completed",
                summary="Updated workspace",
                detail={
                    "origin": "mcp",
                    "command": "workspace_snapshot.save",
                    "outcome": "completed",
                    "active_center_surface": record.snapshot["active_center_surface"],
                },
                source_event_id=f"workspace:{identity.device_id}:{command.idempotency_key}",
            )
        return WorkspaceSnapshotResponse(
            revision=record.revision,
            repaired_presets=list(record.repaired_presets),
            repaired_browser=record.repaired_browser,
            browser_repair_reasons=list(record.browser_repair_reasons),
            **record.snapshot,
        )

    @app.put("/v1/workspace/jobs/selection", tags=["workspace"])
    @serialized_mutation_route
    def workspace_select_job(
        command: JobSelectionRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobMutationResponse:
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
        known_ids = {str(job["job_id"]) for job in jobs.list_jobs()}
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

    @app.put("/v1/workspace/jobs/sort", tags=["workspace"])
    @serialized_mutation_route
    def workspace_sort_jobs(
        command: JobSortRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobMutationResponse:
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
            result = normalize_job_detail(jobs.inspect_job(job_id))
            record_agent_read(
                identity=identity,
                origin=origin,
                idempotency_key=idempotency_key,
                command_name="job.inspect",
                label="Inspected job",
                detail={"job_id": job_id},
            )
            return result
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error

    @app.put("/v1/jobs/{job_id}/status", tags=["jobs"])
    @serialized_mutation_route
    def job_update_status(
        job_id: str,
        command: StatusChangeRequest,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> StatusChangeResponse:
        request_hash = mutation_hash(
            "job.update_status",
            {
                "job_id": job_id,
                "target_status": command.target_status,
                "reason": command.reason,
                "origin": command.origin,
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
            before = jobs.inspect_job(job_id)
            updated = jobs.update_lead_state(
                job_id,
                command.target_status,
                reason=command.reason,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        event_id = state_store.record_status_event(
            job_id=job_id,
            origin=command.origin,
            from_status=str(before["status"]),
            to_status=str(updated["status"]),
        )
        result = StatusChangeResponse(event_id=event_id, job=normalize_job_detail(updated))
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
            detail={"from_status": str(before["status"]), "to_status": str(updated["status"])},
        )
        return result

    @app.get("/v1/jobs/{job_id}/history", tags=["jobs"])
    def job_history(
        job_id: str,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> LeadHistoryResponse:
        try:
            jobs.inspect_job(job_id)
            return LeadHistoryResponse(events=jobs.get_lead_history(job_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error

    def ensure_job(job_id: str) -> None:
        try:
            jobs.inspect_job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error

    def artifact_list(job_id: str) -> JobArtifactsResponse:
        rows, current_id, last_successful_id, approved_id = (
            state_store.list_document_artifacts(job_id)
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
            raw_artifacts = jobs.list_job_artifacts(job_id)
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
            raw = jobs.render_resume(job_id, command.source_id, {"format": command.output_format})
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
                raw = jobs.register_artifact(job_id, command.artifact_reference)
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
        source_event_id = f"activity:{identity.device_id}:{command.idempotency_key}"
        if replay is not None:
            state_store.ensure_conversation_event(
                turn_id=None,
                event_type="activity",
                state=command.state,
                summary=command.label,
                detail={"origin": "mcp", **command.detail},
                source_event_id=source_event_id,
            )
            return ActivityReportResponse.model_validate(replay)
        cursor = state_store.ensure_conversation_event(
            turn_id=None,
            event_type="activity",
            state=command.state,
            summary=command.label,
            detail={"origin": "mcp", **command.detail},
            source_event_id=source_event_id,
        )
        result = ActivityReportResponse(event_id=cursor)
        state_store.record_mutation_result(
            event_type="agent_action",
            origin="mcp",
            actor_id=identity.device_id,
            target_resource="activity",
            command_name="activity.report",
            outcome=command.state,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
            result=result.model_dump(),
            detail={
                "label": command.label,
                "state": command.state,
                "origin": "mcp",
                "outcome": command.state,
            },
        )
        return result

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

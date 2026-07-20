import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jobos_api import __version__
from jobos_api.adapters import create_job_hunter_adapter
from jobos_api.agent_gateway import AgentGateway, OfflineAgentGateway
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
    ArtifactRegistrationRequest,
    ArtifactTrustError,
    JobArtifactsResponse,
    artifact_record,
    content_headers,
    read_source_artifact,
    verify_facade_artifacts,
    verify_source_artifact,
)
from jobos_api.hermes_adapter import HermesWebSocketGateway
from jobos_api.jobs import (
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
from jobos_api.responses import DeviceSessionResponse, HealthResponse, VersionResponse
from jobos_api.settings import Settings
from jobos_api.state_store import (
    ConversationBusy,
    IdempotencyConflict,
    JobOsStateStore,
    WorkspaceRevisionConflict,
)
from jobos_api.workspace import WorkspaceSnapshotCommand, WorkspaceSnapshotResponse


def create_app(
    settings: Settings,
    *,
    job_facade: JobFacade | None = None,
    agent_gateway: AgentGateway | None = None,
) -> FastAPI:
    state_store = JobOsStateStore(settings.state_db_path)
    jobs = job_facade or create_job_hunter_adapter(settings.job_hunter_db_path)
    device_authenticator = DeviceAuthenticator(settings.device_token, settings.device_id)
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
        return VersionResponse(api_version=__version__, contract="jobos-v1-phase6-backend")

    def authenticated_device(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> DeviceIdentity:
        return device_authenticator.authenticate(credentials)

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

    def conversation_context(identity: DeviceIdentity) -> dict[str, object]:
        selection = state_store.job_workspace_state().selected_job_id
        workspace = state_store.workspace_snapshot(identity.device_id).snapshot
        return {
            "selected_job_id": selection,
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
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
        sort: SortMode | None = None,
        query: str | None = None,
        status_group: str | None = None,
    ) -> JobListResponse:
        active_sort = sort or state_store.job_workspace_state().sort_mode
        return list_jobs(
            jobs,
            sort=active_sort,
            query=query,
            status_group=status_group,
            manual_order=state_store.manual_order(),
        )

    @app.put("/v1/jobs/order", tags=["jobs"])
    def jobs_reorder(
        command: ManualOrderRequest,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobMutationResponse:
        known_ids = {str(job["job_id"]) for job in jobs.list_jobs()}
        supplied_ids = set(command.job_ids)
        if len(supplied_ids) != len(command.job_ids) or supplied_ids != known_ids:
            raise HTTPException(
                status_code=422,
                detail="Manual order must contain every current job exactly once",
            )
        event_id = state_store.save_manual_order(command.job_ids, command.origin)
        return JobMutationResponse(event_id=event_id)

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
    ) -> WorkspaceSnapshotResponse:
        record = state_store.workspace_snapshot(identity.device_id)
        return WorkspaceSnapshotResponse(
            revision=record.revision,
            repaired_presets=list(record.repaired_presets),
            repaired_browser=record.repaired_browser,
            browser_repair_reasons=list(record.browser_repair_reasons),
            **record.snapshot,
        )

    @app.put("/v1/workspace", tags=["workspace"])
    def workspace_put(
        command: WorkspaceSnapshotCommand,
        identity: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> WorkspaceSnapshotResponse:
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

    @app.put("/v1/workspace/jobs/selection", tags=["workspace"])
    def workspace_select_job(
        command: JobSelectionRequest,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobMutationResponse:
        known_ids = {str(job["job_id"]) for job in jobs.list_jobs()}
        if command.job_id not in known_ids:
            raise HTTPException(status_code=404, detail="Job not found")
        event_id = state_store.save_job_selection(command.job_id, command.origin)
        return JobMutationResponse(event_id=event_id)

    @app.put("/v1/workspace/jobs/sort", tags=["workspace"])
    def workspace_sort_jobs(
        command: JobSortRequest,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobMutationResponse:
        event_id = state_store.save_job_sort(command.sort_mode, command.origin)
        return JobMutationResponse(event_id=event_id)

    @app.get("/v1/jobs/{job_id}", tags=["jobs"])
    def job_inspect(
        job_id: str,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobDetail:
        try:
            return normalize_job_detail(jobs.inspect_job(job_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error

    @app.put("/v1/jobs/{job_id}/status", tags=["jobs"])
    def job_update_status(
        job_id: str,
        command: StatusChangeRequest,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> StatusChangeResponse:
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
        return StatusChangeResponse(event_id=event_id, job=normalize_job_detail(updated))

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
        rows, current_id, last_successful_id = state_store.list_document_artifacts(job_id)
        return JobArtifactsResponse(
            job_id=job_id,
            artifacts=[
                artifact_record(
                    row,
                    current_id=current_id,
                    last_successful_id=last_successful_id,
                )
                for row in rows
            ],
            current_artifact_id=current_id,
            last_successful_artifact_id=last_successful_id,
        )

    @app.get("/v1/jobs/{job_id}/artifacts", tags=["documents"])
    def job_artifacts(
        job_id: str,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobArtifactsResponse:
        ensure_job(job_id)
        return artifact_list(job_id)

    @app.post("/v1/jobs/{job_id}/artifacts/refresh", tags=["documents"])
    def refresh_job_artifacts(
        job_id: str,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobArtifactsResponse:
        ensure_job(job_id)
        try:
            raw_artifacts = jobs.list_job_artifacts(job_id)
            verified = verify_facade_artifacts(raw_artifacts, settings.artifact_roots)
            state_store.register_document_artifacts(job_id, verified)
        except (ArtifactTrustError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return artifact_list(job_id)

    @app.post("/v1/jobs/{job_id}/artifacts/register", tags=["documents"])
    def register_job_artifact(
        job_id: str,
        command: ArtifactRegistrationRequest,
        _: Annotated[DeviceIdentity, Depends(authenticated_device)],
    ) -> JobArtifactsResponse:
        ensure_job(job_id)
        try:
            raw = jobs.register_artifact(job_id, command.artifact_reference)
            verified = verify_source_artifact(raw, settings.artifact_roots)
            state_store.register_document_artifacts(job_id, [verified])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Artifact reference not found") from error
        except (ArtifactTrustError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return artifact_list(job_id)

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
        try:
            verified, payload = read_source_artifact(
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

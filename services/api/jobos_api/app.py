import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jobos_api import __version__
from jobos_api.adapters import create_job_hunter_adapter
from jobos_api.device_auth import DeviceAuthenticator, DeviceIdentity
from jobos_api.documents import (
    ARTIFACT_ID_PATTERN,
    PDF_MEDIA_TYPE,
    ArtifactRegistrationRequest,
    ArtifactTrustError,
    JobArtifactsResponse,
    artifact_record,
    content_headers,
    verify_source_artifact,
)
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
    IdempotencyConflict,
    JobOsStateStore,
    WorkspaceRevisionConflict,
)
from jobos_api.workspace import WorkspaceSnapshotCommand, WorkspaceSnapshotResponse


def create_app(settings: Settings, *, job_facade: JobFacade | None = None) -> FastAPI:
    state_store = JobOsStateStore(settings.state_db_path)
    jobs = job_facade or create_job_hunter_adapter(settings.job_hunter_db_path)
    device_authenticator = DeviceAuthenticator(settings.device_token, settings.device_id)
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        state_store.initialize()
        yield

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
        )

    @app.get("/v1/version", tags=["system"])
    def version() -> VersionResponse:
        return VersionResponse(api_version=__version__, contract="jobos-v1-phase5")

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
            verified = [
                verify_source_artifact(raw, settings.artifact_roots) for raw in raw_artifacts
            ]
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
            verified = verify_source_artifact(
                {
                    "job_id": record["job_id"],
                    "source_revision": record["source_revision"],
                    "artifact_revision": record["artifact_revision"],
                    "media_type": record["media_type"],
                    "sha256": record["sha256"],
                    "render_status": record["render_status"],
                    "path": record["canonical_path"],
                },
                settings.artifact_roots,
            )
        except (ArtifactTrustError, OSError) as error:
            raise HTTPException(
                status_code=409,
                detail="Registered artifact no longer matches trusted metadata",
            ) from error
        path = Path(verified.canonical_path or "")
        payload = path.read_bytes()
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

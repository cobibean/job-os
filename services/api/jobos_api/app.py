import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jobos_api import __version__
from jobos_api.adapters import create_job_hunter_adapter
from jobos_api.device_auth import DeviceAuthenticator, DeviceIdentity
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
from jobos_api.state_store import JobOsStateStore, WorkspaceRevisionConflict
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
        return VersionResponse(api_version=__version__, contract="jobos-v1-phase3")

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
                snapshot=command.model_dump(exclude={"revision", "repaired_presets"}),
            )
        except WorkspaceRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Workspace revision conflict; current revision is "
                    f"{error.current_revision}"
                ),
            ) from error
        return WorkspaceSnapshotResponse(
            revision=record.revision,
            repaired_presets=list(record.repaired_presets),
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

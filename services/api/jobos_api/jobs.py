from __future__ import annotations

from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

STATUS_GROUPS = {
    "discovered": "Inbox",
    "scored": "Inbox",
    "reviewed": "Inbox",
    "shortlisted": "Considering",
    "apply_now": "Considering",
    "maybe": "Considering",
    "stretch": "Considering",
    "applied": "Applied",
    "interviewing": "Interviewing",
    "closed": "Closed",
    "skipped": "Inactive",
    "archived": "Inactive",
}
STATUS_GROUP_ORDER = ("Inbox", "Considering", "Applied", "Interviewing", "Closed", "Inactive")
SortMode = Literal["manual", "recent", "alphabetical", "status"]


class JobFacade(Protocol):
    def list_jobs(self) -> list[dict[str, Any]]: ...

    def add_job(
        self,
        *,
        company_name: str,
        title: str,
        canonical_url: str,
        location_text: str,
        description_text: str,
        application_url: str,
    ) -> dict[str, Any]: ...

    def inspect_job(self, job_id: str) -> dict[str, Any]: ...

    def get_lead_history(self, job_id: str) -> list[dict[str, Any]]: ...

    def update_lead_state(
        self,
        job_id: str,
        target_state: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]: ...

    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        """Return the full manifest with a unique non-negative render_sequence per item."""
        ...

    def register_artifact(self, job_id: str, artifact_reference: str) -> dict[str, Any]: ...

    def render_resume(
        self, job_id: str, source_id: str, output_options: dict[str, Any]
    ) -> dict[str, Any]: ...


class EmptyJobFacade:
    def list_jobs(self) -> list[dict[str, Any]]:
        return []

    def add_job(self, **_: str) -> dict[str, Any]:
        raise RuntimeError("Job Hunter is unavailable")

    def inspect_job(self, job_id: str) -> dict[str, Any]:
        raise KeyError(job_id)

    def get_lead_history(self, job_id: str) -> list[dict[str, Any]]:
        raise KeyError(job_id)

    def update_lead_state(
        self,
        job_id: str,
        target_state: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        raise KeyError(job_id)

    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        return []

    def register_artifact(self, job_id: str, artifact_reference: str) -> dict[str, Any]:
        raise KeyError(artifact_reference)

    def render_resume(
        self, job_id: str, source_id: str, output_options: dict[str, Any]
    ) -> dict[str, Any]:
        raise KeyError(job_id)


class JobListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    company: str
    title: str
    status: str
    status_group: str
    canonical_url: HttpUrl
    discovered_at: str
    last_seen_at: str


class JobDetail(JobListItem):
    description: str
    location: str | None


class JobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    jobs: list[JobListItem]


class BrowserJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    company_name: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    canonical_url: HttpUrl
    location_text: str = Field(min_length=1, max_length=1000)
    description_text: str = Field(min_length=1, max_length=100_000)
    application_url: HttpUrl
    origin: Literal["user", "mcp"] = "user"
    idempotency_key: str = Field(
        default_factory=lambda: str(uuid4()), min_length=1, max_length=128
    )

    @field_validator("company_name", "title", "location_text", "description_text")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must not be blank")
        return stripped

    @field_validator("canonical_url", "application_url")
    @classmethod
    def reject_url_credentials(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password:
            raise ValueError("URL credentials are not allowed")
        return value


class BrowserJobCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: int
    created: bool
    job: JobDetail


class ManualOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_ids: list[str] = Field(min_length=1)
    origin: Literal["user", "mcp"]
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class JobMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: int


class WorkspaceJobsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_job_id: str | None
    sort_mode: SortMode
    manual_order: list[str]


class JobSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    origin: Literal["user", "mcp"]
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class JobSortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sort_mode: SortMode
    origin: Literal["user", "mcp"]
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class StatusChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_status: Literal[
        "discovered",
        "scored",
        "reviewed",
        "shortlisted",
        "apply_now",
        "maybe",
        "stretch",
        "skipped",
        "applied",
        "interviewing",
        "closed",
        "archived",
    ]
    origin: Literal["user", "mcp"]
    reason: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class StatusChangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: int
    job: JobDetail


class LeadHistoryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: int
    event_type: str
    from_status: str | None
    to_status: str | None
    occurred_at: str
    reason: str | None


class LeadHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    events: list[LeadHistoryEvent]


class JobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: int
    event_type: str
    job_id: str | None
    origin: Literal["user", "mcp"]
    occurred_at: str
    from_status: str | None = None
    to_status: str | None = None
    selected_job_id: str | None = None
    job_ids: list[str] | None = None
    sort_mode: SortMode | None = None


class JobEventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    events: list[JobEvent]


def list_jobs(
    facade: JobFacade,
    *,
    sort: SortMode = "manual",
    query: str | None = None,
    status_group: str | None = None,
    manual_order: list[str] | None = None,
) -> JobListResponse:
    jobs: list[JobListItem] = []
    for row in facade.list_jobs():
        status = str(row["status"])
        jobs.append(JobListItem(**row, status_group=STATUS_GROUPS[status]))
    if query:
        normalized_query = query.casefold().strip()
        jobs = [job for job in jobs if normalized_query in f"{job.company} {job.title}".casefold()]
    if status_group:
        jobs = [job for job in jobs if job.status_group == status_group]
    if sort == "manual" and manual_order:
        positions = {job_id: index for index, job_id in enumerate(manual_order)}
        jobs.sort(key=lambda job: positions.get(job.job_id, len(positions)))
    elif sort == "recent":
        jobs.sort(key=lambda job: job.last_seen_at, reverse=True)
    elif sort == "alphabetical":
        jobs.sort(key=lambda job: (job.company.casefold(), job.title.casefold()))
    elif sort == "status":
        jobs.sort(
            key=lambda job: (
                STATUS_GROUP_ORDER.index(job.status_group),
                job.company.casefold(),
                job.title.casefold(),
            )
        )
    return JobListResponse(jobs=jobs)


def normalize_job_detail(row: dict[str, Any]) -> JobDetail:
    status = str(row["status"])
    return JobDetail(**row, status_group=STATUS_GROUPS[status])

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from jobos_api.job_repository import (
    JobRecord,
    JobRepository,
    ListingCompleteness,
    mutable_evidence,
)

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
    synthetic_demo: bool = False
    dataset_version: str | None = None


class JobDetail(JobListItem):
    description: str
    location: str | None
    full_listing_text: str | None = None
    analysis_text: str | None = None
    listing_completeness: ListingCompleteness = "unknown"
    listing_source_url: HttpUrl | None = None
    listing_captured_at: datetime | None = None
    listing_verified_at: datetime | None = None
    listing_capture_method: str | None = None
    listing_sha256: str | None = None
    listing_evidence: dict[str, object] = Field(default_factory=dict)


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
    full_listing_text: str | None = Field(default=None, min_length=1, max_length=100_000)
    analysis_text: str | None = Field(default=None, max_length=100_000)
    listing_completeness: ListingCompleteness | None = None
    listing_source_url: HttpUrl | None = None
    listing_captured_at: datetime | None = None
    listing_verified_at: datetime | None = None
    listing_capture_method: str | None = Field(default=None, max_length=100)
    listing_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    listing_evidence: dict[str, object] = Field(default_factory=dict)
    origin: Literal["user", "mcp"] = "user"
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)

    @field_validator(
        "company_name",
        "title",
        "location_text",
        "description_text",
        "full_listing_text",
        "analysis_text",
        "listing_capture_method",
    )
    @classmethod
    def strip_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must not be blank")
        return stripped

    @field_validator("canonical_url", "application_url", "listing_source_url")
    @classmethod
    def reject_url_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return None
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


class DemoRemovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: Literal["user", "mcp"]
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


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
    record_application: bool = False
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class StatusChangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: int
    job: JobDetail


class JobDescriptionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description_text: str = Field(min_length=1, max_length=100_000)
    source: str = Field(min_length=1, max_length=100)
    provenance: str | None = Field(default=None, max_length=500)
    origin: Literal["user", "mcp"]
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)

    @field_validator("description_text", "source", "provenance")
    @classmethod
    def strip_description_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must not be blank")
        return stripped


class JobDescriptionUpdateResponse(BaseModel):
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
    source: str | None = None
    provenance: str | None = None
    from_sha256: str | None = None
    to_sha256: str | None = None


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
    source: str | None = None
    description_length: int | None = None


class JobEventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    events: list[JobEvent]


def list_jobs(
    repository: JobRepository,
    *,
    sort: SortMode = "manual",
    query: str | None = None,
    status_group: str | None = None,
    manual_order: list[str] | None = None,
) -> JobListResponse:
    jobs: list[JobListItem] = []
    for record in repository.list_jobs():
        jobs.append(_list_item(record))
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


def _list_item(record: JobRecord) -> JobListItem:
    return JobListItem(
        job_id=record.job_id,
        company=record.company,
        title=record.title,
        status=record.status,
        status_group=STATUS_GROUPS[record.status],
        canonical_url=record.canonical_url,
        discovered_at=record.discovered_at.isoformat(),
        last_seen_at=record.last_seen_at.isoformat(),
        synthetic_demo=record.synthetic_demo,
        dataset_version=record.dataset_version,
    )


def normalize_job_detail(record: JobRecord) -> JobDetail:
    return JobDetail(
        **_list_item(record).model_dump(),
        description=record.description,
        location=record.location,
        full_listing_text=record.full_listing_text,
        analysis_text=record.analysis_text,
        listing_completeness=record.listing_completeness,
        listing_source_url=record.listing_source_url,
        listing_captured_at=(
            record.listing_captured_at.isoformat() if record.listing_captured_at else None
        ),
        listing_verified_at=(
            record.listing_verified_at.isoformat() if record.listing_verified_at else None
        ),
        listing_capture_method=record.listing_capture_method,
        listing_sha256=record.listing_sha256,
        listing_evidence=mutable_evidence(record.listing_evidence),
    )

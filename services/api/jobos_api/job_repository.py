from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, Protocol, cast

ListingCompleteness = Literal["unknown", "unavailable", "summary_only", "partial", "complete"]
LISTING_COMPLETENESS_VALUES = frozenset(
    {"unknown", "unavailable", "summary_only", "partial", "complete"}
)


class JobRepositoryError(Exception):
    """Base class for stable canonical-job persistence failures."""


class NotFound(JobRepositoryError):
    """The requested canonical job does not exist."""


class Conflict(JobRepositoryError):
    """The requested mutation conflicts with current canonical-job state."""


class Validation(JobRepositoryError):
    """The repository rejected invalid canonical-job input."""


class Unavailable(JobRepositoryError):
    """Canonical-job persistence is temporarily unavailable."""


def normalize_listing_completeness(value: object) -> ListingCompleteness:
    normalized = "unknown" if value is None else value
    if not isinstance(normalized, str) or normalized not in LISTING_COMPLETENESS_VALUES:
        raise Validation("listing_completeness is invalid")
    return cast(ListingCompleteness, normalized)


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def immutable_evidence(value: Mapping[str, object]) -> Mapping[str, object]:
    try:
        copied = json.loads(json.dumps(_thaw_json(value), sort_keys=True))
    except (TypeError, ValueError) as error:
        raise Validation("listing_evidence must contain JSON-compatible values") from error
    frozen = _freeze_json(copied)
    if not isinstance(frozen, Mapping):  # pragma: no cover - copied starts as a dict
        raise Validation("listing_evidence must be an object")
    return frozen


def mutable_evidence(value: Mapping[str, object]) -> dict[str, object]:
    thawed = _thaw_json(value)
    if not isinstance(thawed, dict):  # pragma: no cover - input is a mapping
        raise Validation("listing_evidence must be an object")
    return thawed


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    company: str
    title: str
    status: str
    canonical_url: str
    discovered_at: datetime
    last_seen_at: datetime
    description: str
    location: str | None
    application_url: str | None = None
    full_listing_text: str | None = None
    analysis_text: str | None = None
    listing_completeness: ListingCompleteness = "unknown"
    listing_source_url: str | None = None
    listing_captured_at: datetime | None = None
    listing_verified_at: datetime | None = None
    listing_capture_method: str | None = None
    listing_sha256: str | None = None
    listing_evidence: Mapping[str, object] = field(default_factory=dict)
    synthetic_demo: bool = False
    dataset_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "listing_evidence", immutable_evidence(self.listing_evidence))


@dataclass(frozen=True, slots=True)
class CreateJobCommand:
    job_id: str
    company_name: str
    title: str
    canonical_url: str
    location_text: str
    description_text: str
    application_url: str
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    full_listing_text: str | None = None
    analysis_text: str | None = None
    listing_completeness: ListingCompleteness | None = None
    listing_source_url: str | None = None
    listing_captured_at: datetime | None = None
    listing_verified_at: datetime | None = None
    listing_capture_method: str | None = None
    listing_sha256: str | None = None
    listing_evidence: Mapping[str, object] = field(default_factory=dict)
    synthetic_demo: bool = False
    dataset_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "listing_evidence", immutable_evidence(self.listing_evidence))


@dataclass(frozen=True, slots=True)
class JobHistoryRecord:
    event_id: int
    event_type: str
    from_status: str | None
    to_status: str | None
    occurred_at: datetime
    reason: str | None = None
    source: str | None = None
    provenance: str | None = None
    from_sha256: str | None = None
    to_sha256: str | None = None


class JobRepository(Protocol):
    """Focused persistence seam for canonical jobs and their history."""

    def initialize(self) -> None: ...

    def list_jobs(self) -> Sequence[JobRecord]: ...

    def get_job(self, job_id: str) -> JobRecord: ...

    def create_job(self, command: CreateJobCommand) -> JobRecord:
        """Insert or refresh by canonical URL and return the canonical record."""
        ...

    def update_status(
        self,
        job_id: str,
        target_status: str,
        *,
        reason: str | None = None,
        record_application: bool = False,
    ) -> JobRecord: ...

    def update_description(
        self,
        job_id: str,
        description_text: str,
        *,
        source: str,
        provenance: str | None = None,
    ) -> JobRecord: ...

    def list_history(self, job_id: str) -> Sequence[JobHistoryRecord]: ...

    def delete_job(self, job_id: str) -> None: ...

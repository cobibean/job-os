from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from jobos_api.artifact_gateway import ArtifactGateway
from jobos_api.job_repository import (
    Conflict,
    CreateJobCommand,
    JobHistoryRecord,
    JobRecord,
    NotFound,
    Unavailable,
    Validation,
    mutable_evidence,
    normalize_listing_completeness,
)


def _timestamp(value: object, *, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise Unavailable("JobHunter returned an invalid timestamp") from error
    elif fallback is not None:
        parsed = fallback
    else:
        raise Unavailable("JobHunter omitted a required timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _job_record(row: Mapping[str, Any]) -> JobRecord:
    now = datetime.now(UTC)
    evidence = row.get("listing_evidence") or {}
    if not isinstance(evidence, Mapping):
        raise Unavailable("JobHunter returned invalid listing evidence")
    return JobRecord(
        job_id=str(row["job_id"]),
        company=str(row.get("company", row.get("company_name", ""))),
        title=str(row.get("title", "")),
        status=str(row.get("status", "discovered")),
        canonical_url=str(row.get("canonical_url", "https://invalid.local/unknown")),
        discovered_at=_timestamp(row.get("discovered_at"), fallback=now),
        last_seen_at=_timestamp(row.get("last_seen_at"), fallback=now),
        description=str(row.get("description", row.get("full_listing_text", ""))),
        location=(str(row["location"]) if row.get("location") is not None else None),
        application_url=(
            str(row["application_url"]) if row.get("application_url") is not None else None
        ),
        full_listing_text=(
            str(row["full_listing_text"])
            if row.get("full_listing_text") is not None
            else None
        ),
        analysis_text=(
            str(row["analysis_text"]) if row.get("analysis_text") is not None else None
        ),
        listing_completeness=normalize_listing_completeness(
            row.get("listing_completeness")
        ),
        listing_source_url=(
            str(row["listing_source_url"])
            if row.get("listing_source_url") is not None
            else None
        ),
        listing_captured_at=(
            _timestamp(row["listing_captured_at"])
            if row.get("listing_captured_at") is not None
            else None
        ),
        listing_verified_at=(
            _timestamp(row["listing_verified_at"])
            if row.get("listing_verified_at") is not None
            else None
        ),
        listing_capture_method=(
            str(row["listing_capture_method"])
            if row.get("listing_capture_method") is not None
            else None
        ),
        listing_sha256=(
            str(row["listing_sha256"]) if row.get("listing_sha256") is not None else None
        ),
        listing_evidence=dict(evidence),
    )


def _history_record(row: Mapping[str, Any]) -> JobHistoryRecord:
    event_type = {
        "dedupe_inserted": "job_created",
        "dedupe_updated": "job_refreshed",
        "description_updated": "job_description_updated",
    }.get(str(row["event_type"]), str(row["event_type"]))
    return JobHistoryRecord(
        event_id=int(row["event_id"]),
        event_type=event_type,
        from_status=(
            str(row["from_status"]) if row.get("from_status") is not None else None
        ),
        to_status=str(row["to_status"]) if row.get("to_status") is not None else None,
        occurred_at=_timestamp(row["occurred_at"]),
        reason=str(row["reason"]) if row.get("reason") is not None else None,
        source=str(row["source"]) if row.get("source") is not None else None,
        provenance=(
            str(row["provenance"]) if row.get("provenance") is not None else None
        ),
        from_sha256=(
            str(row["from_sha256"]) if row.get("from_sha256") is not None else None
        ),
        to_sha256=str(row["to_sha256"]) if row.get("to_sha256") is not None else None,
    )


def _translate(error: Exception, *, conflict: bool = False) -> Exception:
    if isinstance(error, KeyError):
        return NotFound(str(error))
    if isinstance(error, ValueError):
        return Conflict(str(error)) if conflict else Validation(str(error))
    if isinstance(error, (ImportError, OSError, RuntimeError, sqlite3.Error)):
        return Unavailable(str(error))
    return Unavailable("JobHunter adapter failed")


class JobHunterJobRepository:
    """Anti-corruption adapter from JobHunter's facade/storage to JobOS records."""

    def __init__(
        self, facade: Any, storage: Any | None = None, model_type: Any | None = None
    ) -> None:
        self._facade = facade
        self._storage = storage
        self._model_type = model_type

    def initialize(self) -> None:
        return None

    def list_jobs(self) -> Sequence[JobRecord]:
        try:
            return tuple(_job_record(row) for row in self._facade.list_jobs())
        except Exception as error:
            raise _translate(error) from error

    def get_job(self, job_id: str) -> JobRecord:
        try:
            return _job_record(self._facade.inspect_job(job_id))
        except Exception as error:
            raise _translate(error) from error

    def create_job(self, command: CreateJobCommand) -> JobRecord:
        try:
            parameters = inspect.signature(self._facade.add_job).parameters
            if "job_id" in parameters:
                result = self._facade.add_job(
                    job_id=command.job_id,
                    company_name=command.company_name,
                    title=command.title,
                    canonical_url=command.canonical_url,
                    location_text=command.location_text,
                    description_text=command.description_text,
                    application_url=command.application_url,
                )
                return _job_record(result["job"])
            if self._storage is None or self._model_type is None:
                result = self._facade.add_job(
                    company_name=command.company_name,
                    title=command.title,
                    canonical_url=command.canonical_url,
                    location_text=command.location_text,
                    description_text=command.description_text,
                    application_url=command.application_url,
                )
                return _job_record(result["job"])

            candidate: dict[str, object] = {
                "job_id": command.job_id,
                "canonical_url": command.canonical_url,
                "source_system": "jobos_browser",
                "source_job_id": None,
                "source_key": "jobos_browser",
                "company_name": command.company_name,
                "title": command.title,
                "job_text": command.full_listing_text or command.description_text,
                "description_text": command.description_text,
                "full_listing_text": command.full_listing_text or command.description_text,
                "analysis_text": command.analysis_text,
                "location_text": command.location_text,
                "application_url": command.application_url,
                "discovered_at": command.observed_at,
                "last_seen_at": command.observed_at,
                "listing_completeness": command.listing_completeness or "unknown",
                "listing_source_url": command.listing_source_url or command.canonical_url,
                "listing_captured_at": command.listing_captured_at or command.observed_at,
                "listing_verified_at": command.listing_verified_at,
                "listing_capture_method": command.listing_capture_method or "jobos_browser",
                "listing_sha256": command.listing_sha256,
                "listing_evidence": mutable_evidence(command.listing_evidence),
                "source_metadata": {"capture_method": "browser"},
            }
            accepted = inspect.signature(self._model_type).parameters
            model = self._model_type(
                **{key: value for key, value in candidate.items() if key in accepted}
            )
            result = self._storage.upsert_job(model, preserve_existing_source=True)
            return self.get_job(str(result["job_id"]))
        except Exception as error:
            raise _translate(error) from error

    def update_status(
        self,
        job_id: str,
        target_status: str,
        *,
        reason: str | None = None,
        record_application: bool = False,
    ) -> JobRecord:
        try:
            return _job_record(
                self._facade.update_lead_state(
                    job_id,
                    target_status,
                    reason=reason,
                    record_application=record_application,
                )
            )
        except Exception as error:
            raise _translate(error, conflict=True) from error

    def update_description(
        self,
        job_id: str,
        description_text: str,
        *,
        source: str,
        provenance: str | None = None,
    ) -> JobRecord:
        try:
            return _job_record(
                self._facade.update_job_description(
                    job_id,
                    description_text,
                    source=source,
                    provenance=provenance,
                )
            )
        except Exception as error:
            raise _translate(error) from error

    def list_history(self, job_id: str) -> Sequence[JobHistoryRecord]:
        try:
            return tuple(
                _history_record(row) for row in self._facade.get_lead_history(job_id)
            )
        except Exception as error:
            raise _translate(error) from error


class JobHunterArtifactGateway:
    def __init__(self, facade: Any) -> None:
        self._facade = facade

    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        return list(self._facade.list_job_artifacts(job_id))

    def register_artifact(self, job_id: str, artifact_reference: str) -> dict[str, Any]:
        return dict(self._facade.register_artifact(job_id, artifact_reference))

    def publish_document_artifact(
        self,
        job_id: str,
        document_key: str,
        document_label: str,
        source_path: str,
        artifact_path: str,
    ) -> dict[str, Any]:
        return dict(
            self._facade.publish_document_artifact(
                job_id, document_key, document_label, source_path, artifact_path
            )
        )

    def render_resume(
        self, job_id: str, source_id: str, output_options: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(self._facade.render_resume(job_id, source_id, output_options))


def adapt_job_hunter_facade(facade: Any) -> tuple[JobHunterJobRepository, ArtifactGateway]:
    """Test/private compatibility entry point without importing JobHunter itself."""
    return JobHunterJobRepository(facade), JobHunterArtifactGateway(facade)


def create_job_hunter_services(
    database_path: Path, workspace_root: Path | None
) -> tuple[JobHunterJobRepository, ArtifactGateway]:
    """Dynamically load JobHunter only after private provider selection."""
    try:
        facade_module = import_module("job_hunter.facade")
        models_module = import_module("job_hunter.models")
        storage_module = import_module("job_hunter.storage")
        storage = storage_module.JobStorage(database_path, initialize=False)
        facade = facade_module.JobHunterFacade(storage, workspace_root=workspace_root)
    except Exception as error:
        raise Unavailable("The selected JobHunter provider is unavailable") from error
    return (
        JobHunterJobRepository(facade, storage, models_module.JobRecord),
        JobHunterArtifactGateway(facade),
    )

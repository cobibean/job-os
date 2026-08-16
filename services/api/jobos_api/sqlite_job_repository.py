from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

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
from jobos_api.job_repository_migrations import (
    MIGRATIONS,
    Migration,
    initialize_job_repository_database,
)

STATUSES = frozenset(
    {
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
    }
)
LISTING_COMPLETENESS_RANK = {
    "unknown": 0,
    "unavailable": 1,
    "summary_only": 2,
    "partial": 3,
    "complete": 4,
}
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "discovered": frozenset({"scored", "reviewed", "shortlisted", "skipped", "archived"}),
    "scored": frozenset(
        {"reviewed", "shortlisted", "apply_now", "maybe", "stretch", "skipped", "archived"}
    ),
    "reviewed": frozenset(
        {"shortlisted", "apply_now", "maybe", "stretch", "skipped", "archived"}
    ),
    "shortlisted": frozenset({"apply_now", "maybe", "stretch", "applied"}),
    "apply_now": frozenset({"applied", "interviewing", "closed"}),
    "maybe": frozenset({"reviewed", "apply_now", "skipped", "archived"}),
    "stretch": frozenset({"reviewed", "apply_now", "skipped", "archived"}),
    "skipped": frozenset({"reviewed", "archived"}),
    "applied": frozenset({"interviewing", "closed", "archived"}),
    "interviewing": frozenset({"closed", "archived"}),
    "closed": frozenset({"archived"}),
    "archived": frozenset(),
}
PRE_APPLICATION_STATUSES = frozenset(
    {"discovered", "scored", "reviewed", "shortlisted", "apply_now", "maybe", "stretch", "skipped"}
)


def canonicalize_url(value: str) -> str:
    candidate = value.strip()
    if candidate != value or any(character.isspace() for character in value):
        raise Validation("canonical_url must be an http(s) URL")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as error:
        raise Validation("canonical_url must be an http(s) URL") from error
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise Validation("canonical_url must be an http(s) URL")
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname.casefold()
    include_port = port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    )
    netloc = f"{hostname}:{port}" if include_port else hostname
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(SplitResult(scheme, netloc, path, parsed.query, ""))


def _validate_http_url(field: str, value: str) -> str:
    try:
        return canonicalize_url(value)
    except Validation as error:
        raise Validation(f"{field} must be an http(s) URL") from error


def _required_text(field: str, value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise Validation(f"{field} must be a nonblank string")
    return stripped


def _optional_text(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise Validation(f"{field} must be omitted or a nonblank string")
    return stripped


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise Validation("timestamps must include a timezone")
    return value.astimezone(UTC)


class SQLiteJobRepository:
    """Canonical local jobs store, intentionally separate from workbench state."""

    def __init__(
        self,
        database_path: Path,
        *,
        initialize: bool = True,
        migrations: Sequence[Migration] = MIGRATIONS,
        backup_directory: Path | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self._migrations = tuple(migrations)
        self._backup_directory = backup_directory
        if initialize:
            self.initialize()

    def initialize(self) -> None:
        initialize_job_repository_database(
            self.database_path,
            migrations=self._migrations,
            backup_directory=self._backup_directory,
        )

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.database_path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except sqlite3.Error as error:
            raise Unavailable(f"Canonical jobs database is unavailable: {error}") from error

    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        try:
            evidence = json.loads(str(row["listing_evidence_json"]))
            if not isinstance(evidence, dict):
                raise ValueError
            return JobRecord(
                job_id=str(row["job_id"]),
                company=str(row["company"]),
                title=str(row["title"]),
                status=str(row["status"]),
                canonical_url=str(row["canonical_url"]),
                discovered_at=datetime.fromisoformat(str(row["discovered_at"])),
                last_seen_at=datetime.fromisoformat(str(row["last_seen_at"])),
                description=str(row["description"]),
                location=str(row["location"]) if row["location"] is not None else None,
                application_url=(
                    str(row["application_url"]) if row["application_url"] is not None else None
                ),
                full_listing_text=(
                    str(row["full_listing_text"])
                    if row["full_listing_text"] is not None
                    else None
                ),
                analysis_text=(
                    str(row["analysis_text"]) if row["analysis_text"] is not None else None
                ),
                listing_completeness=normalize_listing_completeness(
                    row["listing_completeness"]
                ),
                listing_source_url=(
                    str(row["listing_source_url"])
                    if row["listing_source_url"] is not None
                    else None
                ),
                listing_captured_at=(
                    datetime.fromisoformat(str(row["listing_captured_at"]))
                    if row["listing_captured_at"] is not None
                    else None
                ),
                listing_verified_at=(
                    datetime.fromisoformat(str(row["listing_verified_at"]))
                    if row["listing_verified_at"] is not None
                    else None
                ),
                listing_capture_method=(
                    str(row["listing_capture_method"])
                    if row["listing_capture_method"] is not None
                    else None
                ),
                listing_sha256=(
                    str(row["listing_sha256"]) if row["listing_sha256"] is not None else None
                ),
                listing_evidence=evidence,
                synthetic_demo=bool(row["synthetic_demo"]),
                dataset_version=(
                    str(row["dataset_version"]) if row["dataset_version"] is not None else None
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise Unavailable("Canonical jobs database contains an invalid record") from error

    def list_jobs(self) -> Sequence[JobRecord]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM canonical_jobs ORDER BY discovered_at, job_id"
                ).fetchall()
            return tuple(self._record(row) for row in rows)
        except sqlite3.Error as error:
            raise Unavailable(f"Could not list canonical jobs: {error}") from error

    def get_job(self, job_id: str) -> JobRecord:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM canonical_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise Unavailable(f"Could not read canonical job: {error}") from error
        if row is None:
            raise NotFound(f"Unknown job {job_id}")
        return self._record(row)

    def create_job(self, command: CreateJobCommand) -> JobRecord:
        job_id = _required_text("job_id", command.job_id)
        company = _required_text("company_name", command.company_name)
        title = _required_text("title", command.title)
        canonical_url = _validate_http_url("canonical_url", command.canonical_url)
        application_url = _validate_http_url("application_url", command.application_url)
        location = _required_text("location_text", command.location_text)
        description = _required_text("description_text", command.description_text)
        full_listing = _optional_text("full_listing_text", command.full_listing_text) or description
        analysis = _optional_text("analysis_text", command.analysis_text)
        completeness = normalize_listing_completeness(command.listing_completeness)
        source_url = command.listing_source_url or canonical_url
        _validate_http_url("listing_source_url", source_url)
        captured_at = _as_utc(command.listing_captured_at or command.observed_at)
        verified_at = (
            _as_utc(command.listing_verified_at) if command.listing_verified_at else None
        )
        observed_at = _as_utc(command.observed_at)
        capture_method = _optional_text(
            "listing_capture_method", command.listing_capture_method
        )
        listing_digest = command.listing_sha256 or hashlib.sha256(
            full_listing.encode("utf-8")
        ).hexdigest()
        if len(listing_digest) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in listing_digest
        ):
            raise Validation("listing_sha256 must be a hexadecimal SHA-256 digest")
        evidence_json = json.dumps(mutable_evidence(command.listing_evidence), sort_keys=True)
        normalized_url = canonicalize_url(canonical_url)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            id_owner = connection.execute(
                "SELECT normalized_url FROM canonical_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if id_owner is not None and str(id_owner["normalized_url"]) != normalized_url:
                raise Conflict(f"Job ID {job_id} already exists")
            existing = connection.execute(
                "SELECT * FROM canonical_jobs WHERE normalized_url = ?",
                (normalized_url,),
            ).fetchone()
            history_digest: str | None = listing_digest.casefold()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO canonical_jobs(
                        job_id, canonical_url, normalized_url, company, title, status,
                        discovered_at, last_seen_at, description, location, application_url,
                        full_listing_text, analysis_text, listing_completeness,
                        listing_source_url, listing_captured_at, listing_verified_at,
                        listing_capture_method, listing_sha256, listing_evidence_json,
                        synthetic_demo, dataset_version
                    ) VALUES (
                        ?, ?, ?, ?, ?, 'discovered', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        job_id,
                        canonical_url,
                        normalized_url,
                        company,
                        title,
                        observed_at.isoformat(),
                        observed_at.isoformat(),
                        description,
                        location,
                        application_url,
                        full_listing,
                        analysis,
                        completeness,
                        source_url,
                        captured_at.isoformat(),
                        verified_at.isoformat() if verified_at else None,
                        capture_method,
                        listing_digest.casefold(),
                        evidence_json,
                        int(command.synthetic_demo),
                        command.dataset_version,
                    ),
                )
                canonical_job_id = job_id
                event_type = "job_created"
            else:
                canonical_job_id = str(existing["job_id"])
                existing_record = self._record(existing)
                metadata_is_fresh = observed_at >= existing_record.last_seen_at
                merged_last_seen = max(observed_at, existing_record.last_seen_at)
                existing_captured_at = (
                    existing_record.listing_captured_at or existing_record.last_seen_at
                )
                provenance_is_not_downgraded = (
                    existing_record.listing_verified_at is None or verified_at is not None
                ) and (
                    not existing_record.listing_evidence or bool(command.listing_evidence)
                ) and (
                    existing_record.listing_capture_method is None
                    or capture_method is not None
                )
                replace_listing = (
                    captured_at >= existing_captured_at
                    and LISTING_COMPLETENESS_RANK[completeness]
                    >= LISTING_COMPLETENESS_RANK[existing_record.listing_completeness]
                    and provenance_is_not_downgraded
                )
                merged_digest = (
                    listing_digest.casefold()
                    if replace_listing
                    else existing_record.listing_sha256
                )
                merged_evidence_json = (
                    evidence_json
                    if replace_listing
                    else json.dumps(
                        mutable_evidence(existing_record.listing_evidence), sort_keys=True
                    )
                )
                connection.execute(
                    """
                    UPDATE canonical_jobs SET
                        canonical_url = ?, company = ?, title = ?, last_seen_at = ?,
                        description = ?, location = ?, application_url = ?,
                        full_listing_text = ?, analysis_text = ?, listing_completeness = ?,
                        listing_source_url = ?, listing_captured_at = ?, listing_verified_at = ?,
                        listing_capture_method = ?, listing_sha256 = ?, listing_evidence_json = ?
                    WHERE job_id = ?
                    """,
                    (
                        canonical_url if metadata_is_fresh else existing_record.canonical_url,
                        company if metadata_is_fresh else existing_record.company,
                        title if metadata_is_fresh else existing_record.title,
                        merged_last_seen.isoformat(),
                        description if replace_listing else existing_record.description,
                        location if metadata_is_fresh else existing_record.location,
                        application_url if metadata_is_fresh else existing_record.application_url,
                        full_listing if replace_listing else existing_record.full_listing_text,
                        analysis if replace_listing else existing_record.analysis_text,
                        (
                            completeness
                            if replace_listing
                            else existing_record.listing_completeness
                        ),
                        source_url if replace_listing else existing_record.listing_source_url,
                        (
                            captured_at.isoformat()
                            if replace_listing
                            else existing_record.listing_captured_at.isoformat()
                            if existing_record.listing_captured_at
                            else None
                        ),
                        (
                            verified_at.isoformat()
                            if replace_listing and verified_at
                            else existing_record.listing_verified_at.isoformat()
                            if not replace_listing and existing_record.listing_verified_at
                            else None
                        ),
                        (
                            capture_method
                            if replace_listing
                            else existing_record.listing_capture_method
                        ),
                        merged_digest,
                        merged_evidence_json,
                        canonical_job_id,
                    ),
                )
                history_digest = merged_digest
                event_type = "job_refreshed"
            connection.execute(
                """
                INSERT INTO canonical_job_history(
                    job_id, event_type, occurred_at, to_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (canonical_job_id, event_type, observed_at.isoformat(), history_digest),
            )
            row = connection.execute(
                "SELECT * FROM canonical_jobs WHERE job_id = ?", (canonical_job_id,)
            ).fetchone()
            connection.commit()
            if row is None:  # pragma: no cover - transaction invariant
                raise Unavailable("Canonical job disappeared during creation")
            return self._record(row)
        except Conflict:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            connection.rollback()
            if "canonical_jobs.job_id" in str(error):
                raise Conflict(f"Job ID {job_id} already exists") from error
            raise Conflict("Canonical job conflicts with an existing record") from error
        except sqlite3.Error as error:
            connection.rollback()
            raise Unavailable(f"Could not save canonical job: {error}") from error
        finally:
            connection.close()

    def update_status(
        self,
        job_id: str,
        target_status: str,
        *,
        reason: str | None = None,
        record_application: bool = False,
    ) -> JobRecord:
        if target_status not in STATUSES:
            raise Validation(f"Unknown job status {target_status}")
        normalized_reason = _optional_text("reason", reason)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM canonical_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"Unknown job {job_id}")
            current = str(row["status"])
            if current == target_status:
                connection.commit()
                return self._record(row)
            records_application = (
                record_application
                and target_status == "applied"
                and current in PRE_APPLICATION_STATUSES
            )
            if not records_application and target_status not in ALLOWED_TRANSITIONS[current]:
                raise Conflict(f"Invalid lead state transition: {current} -> {target_status}")
            occurred_at = datetime.now(UTC)
            connection.execute(
                "UPDATE canonical_jobs SET status = ? WHERE job_id = ?",
                (target_status, job_id),
            )
            connection.execute(
                """
                INSERT INTO canonical_job_history(
                    job_id, event_type, from_status, to_status, occurred_at, reason
                ) VALUES (?, 'lead_state_changed', ?, ?, ?, ?)
                """,
                (job_id, current, target_status, occurred_at.isoformat(), normalized_reason),
            )
            updated = connection.execute(
                "SELECT * FROM canonical_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._record(updated)
        except (NotFound, Conflict):
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise Unavailable(f"Could not update canonical job status: {error}") from error
        finally:
            connection.close()

    def update_description(
        self,
        job_id: str,
        description_text: str,
        *,
        source: str,
        provenance: str | None = None,
    ) -> JobRecord:
        description = _required_text("description_text", description_text)
        normalized_source = _required_text("source", source)
        normalized_provenance = _optional_text("provenance", provenance)
        digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM canonical_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"Unknown job {job_id}")
            prior_digest = (
                str(row["listing_sha256"])
                if row["listing_sha256"] is not None
                else hashlib.sha256(str(row["description"]).encode("utf-8")).hexdigest()
            )
            occurred_at = datetime.now(UTC)
            connection.execute(
                """
                UPDATE canonical_jobs SET
                    description = ?, full_listing_text = ?, analysis_text = NULL,
                    listing_completeness = 'unknown', listing_sha256 = ?,
                    listing_captured_at = ?, listing_verified_at = NULL,
                    listing_source_url = NULL, listing_capture_method = ?,
                    listing_evidence_json = '{}'
                WHERE job_id = ?
                """,
                (
                    description,
                    description,
                    digest,
                    occurred_at.isoformat(),
                    normalized_source,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO canonical_job_history(
                    job_id, event_type, occurred_at, source, provenance,
                    from_sha256, to_sha256
                ) VALUES (?, 'job_description_updated', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    occurred_at.isoformat(),
                    normalized_source,
                    normalized_provenance,
                    prior_digest,
                    digest,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM canonical_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._record(updated)
        except NotFound:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise Unavailable(f"Could not update canonical job description: {error}") from error
        finally:
            connection.close()

    def list_history(self, job_id: str) -> Sequence[JobHistoryRecord]:
        self.get_job(job_id)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM canonical_job_history
                    WHERE job_id = ? ORDER BY event_id
                    """,
                    (job_id,),
                ).fetchall()
            return tuple(
                JobHistoryRecord(
                    event_id=int(row["event_id"]),
                    event_type=str(row["event_type"]),
                    from_status=(
                        str(row["from_status"]) if row["from_status"] is not None else None
                    ),
                    to_status=str(row["to_status"]) if row["to_status"] is not None else None,
                    occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
                    reason=str(row["reason"]) if row["reason"] is not None else None,
                    source=str(row["source"]) if row["source"] is not None else None,
                    provenance=(
                        str(row["provenance"]) if row["provenance"] is not None else None
                    ),
                    from_sha256=(
                        str(row["from_sha256"]) if row["from_sha256"] is not None else None
                    ),
                    to_sha256=(
                        str(row["to_sha256"]) if row["to_sha256"] is not None else None
                    ),
                )
                for row in rows
            )
        except sqlite3.Error as error:
            raise Unavailable(f"Could not list canonical job history: {error}") from error

    def delete_job(self, job_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT synthetic_demo FROM canonical_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"Unknown job {job_id}")
            if bool(row["synthetic_demo"]):
                occurred_at = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    UPDATE synthetic_demo_ledger
                    SET state = 'deleted', state_changed_at = ?
                    WHERE demo_job_id = ?
                    """,
                    (occurred_at, job_id),
                )
            connection.execute("DELETE FROM canonical_jobs WHERE job_id = ?", (job_id,))
            connection.commit()
        except NotFound:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise Unavailable(f"Could not delete canonical job: {error}") from error
        finally:
            connection.close()

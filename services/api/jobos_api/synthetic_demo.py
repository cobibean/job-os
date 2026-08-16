from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast
from urllib.parse import urlsplit, urlunsplit

from jobos_api.editable_documents import (
    DocumentSettings,
    blank_content,
    default_settings,
    validate_content,
)
from jobos_api.job_repository import (
    Conflict,
    CreateJobCommand,
    JobRecord,
    JobRepository,
    NotFound,
)
from jobos_api.state_store import JobOsStateStore


class DemoFixture(TypedDict):
    job_id: str
    company_name: str
    title: str
    canonical_url: str
    location_text: str
    description_text: str
    application_url: str
    observed_at: str
    dataset_version: str


DEMO_DATASET_ID = "jobos.synthetic-starter"
DEMO_DATASET_VERSION = "jobos-demo-v1"
DEMO_JOB_ID = "jobos-demo-v1"
DEMO_FIXTURE: DemoFixture = {
    "job_id": DEMO_JOB_ID,
    "company_name": "Northstar Kites (Fictional Demo)",
    "title": "Imaginary Kite Systems Tuner — Demo Role",
    "canonical_url": "https://jobs.example.com/jobos-demo-v1",
    "location_text": "Example City · Remote",
    "description_text": (
        "This is fictional sample data for learning JobOS. Tune an imaginary fleet of "
        "sensor-equipped kites, document experiments, and collaborate with a made-up "
        "aerodynamics team. This is not a real vacancy and applications are not accepted."
    ),
    "application_url": "https://jobs.example.com/jobos-demo-v1/apply",
    "observed_at": "2026-08-15T12:00:00+00:00",
    "dataset_version": DEMO_DATASET_VERSION,
}


def _fixture_sha256(value: DemoFixture) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


DEMO_FIXTURE_SHA256 = _fixture_sha256(DEMO_FIXTURE)
DEMO_DOCUMENT_KEY = "resume"


def _starter_document_content() -> dict[str, object]:
    content = blank_content(DEMO_DOCUMENT_KEY)
    lines = (
        "(FAKE) FICTIONAL JOBOS DEMO CANDIDATE — DO NOT APPLY",
        "(FAKE) Synthetic resume for learning JobOS. This is not a real person or application.",
        "(FAKE) Imaginary Kite Systems Workshop — demonstration experience only.",
        "(FAKE) Example Learning Lab — fictional credential; do not verify or submit.",
        "(FAKE) Demo editing, synthetic kite telemetry, and clearly fictional examples.",
    )
    sections = content["content"]
    assert isinstance(sections, list)
    for section, line in zip(sections, lines, strict=True):
        paragraph = section["content"][0]
        paragraph["content"] = [{"type": "text", "text": line}]
    validate_content(content, DocumentSettings.model_validate(default_settings()), [])
    return content


def seed_demo_document_once(state_store: JobOsStateStore) -> str | None:
    existing = state_store.get_job_editable_document(DEMO_JOB_ID, DEMO_DOCUMENT_KEY)
    if existing is not None:
        return None
    row = state_store.create_editable_document(
        job_id=DEMO_JOB_ID,
        document_key=DEMO_DOCUMENT_KEY,
        document_label="Resume",
        content=_starter_document_content(),
        settings=default_settings(),
        comments=[],
        import_report={"source_filename": None, "imported_at": None, "issues": []},
    )
    return str(row["document_id"])


def reset_demo_document(state_store: JobOsStateStore) -> str:
    state_store.delete_job_documents(DEMO_JOB_ID)
    document_id = seed_demo_document_once(state_store)
    assert document_id is not None
    return document_id


def _command(fixture_path: Path | None = None) -> CreateJobCommand:
    value = (
        cast(DemoFixture, json.loads(fixture_path.read_text(encoding="utf-8")))
        if fixture_path is not None
        else DEMO_FIXTURE
    )
    return CreateJobCommand(
        job_id=value["job_id"],
        company_name=value["company_name"],
        title=value["title"],
        canonical_url=value["canonical_url"],
        location_text=value["location_text"],
        description_text=value["description_text"],
        application_url=value["application_url"],
        observed_at=datetime.fromisoformat(value["observed_at"]),
        full_listing_text=value["description_text"],
        listing_completeness="complete",
        listing_source_url=value["canonical_url"],
        listing_capture_method="synthetic_demo_fixture",
        listing_evidence={"synthetic": True, "dataset_version": value["dataset_version"]},
        synthetic_demo=True,
        dataset_version=value["dataset_version"],
    )


def _normalized_fixture_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), "", "")
    )


def _assert_demo_url_available(repository: JobRepository) -> None:
    fixture_url = _normalized_fixture_url(DEMO_FIXTURE["canonical_url"])
    for job in repository.list_jobs():
        if _normalized_fixture_url(job.canonical_url) != fixture_url:
            continue
        if job.job_id != DEMO_JOB_ID or not job.synthetic_demo:
            raise Conflict("The synthetic demo URL is occupied by a non-demo job")


def _existing_demo(repository: JobRepository) -> JobRecord | None:
    try:
        job = repository.get_job(DEMO_JOB_ID)
    except NotFound:
        return None
    if not job.synthetic_demo or job.dataset_version != DEMO_DATASET_VERSION:
        raise Conflict("The synthetic demo ID is occupied by a non-demo job")
    return job


def seed_demo_once(
    repository: JobRepository,
    ledger_path: Path,
    *,
    fixture_path: Path | None = None,
) -> str | None:
    """Seed once per profile; the jobs-database ledger survives intentional deletion."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ledger_path) as connection:
        existing_ledger = connection.execute(
            "SELECT state FROM synthetic_demo_ledger WHERE dataset_id = ?",
            (DEMO_DATASET_ID,),
        ).fetchone()
    if existing_ledger is not None:
        return None

    _assert_demo_url_available(repository)
    job = _existing_demo(repository)
    if job is None:
        job = repository.create_job(_command(fixture_path))

    occurred_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(ledger_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing_ledger = connection.execute(
            "SELECT state FROM synthetic_demo_ledger WHERE dataset_id = ?",
            (DEMO_DATASET_ID,),
        ).fetchone()
        if existing_ledger is not None:
            return None
        connection.execute(
            """
            INSERT INTO synthetic_demo_ledger(
                dataset_id, dataset_version, fixture_sha256, demo_job_id,
                state, first_seeded_at, state_changed_at
            ) VALUES (?, ?, ?, ?, 'seeded', ?, ?)
            """,
            (
                DEMO_DATASET_ID,
                DEMO_DATASET_VERSION,
                DEMO_FIXTURE_SHA256,
                job.job_id,
                occurred_at,
                occurred_at,
            ),
        )
    return job.job_id


def reset_demo(
    repository: JobRepository,
    ledger_path: Path,
    *,
    confirmed: bool,
    fixture_path: Path | None = None,
) -> str:
    """Atomically restore the built-in demo and its ledger in the jobs database."""
    if not confirmed:
        raise ValueError("Demo reset requires explicit confirmation")

    _assert_demo_url_available(repository)
    _existing_demo(repository)
    command = _command(fixture_path)
    occurred_at = datetime.now(UTC).isoformat()
    observed_at = command.observed_at.astimezone(UTC).isoformat()
    listing_text = command.full_listing_text or command.description_text
    listing_digest = hashlib.sha256(listing_text.encode("utf-8")).hexdigest()
    normalized_url = _normalized_fixture_url(command.canonical_url)

    connection = sqlite3.connect(ledger_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        conflicts = connection.execute(
            """
            SELECT job_id, synthetic_demo, dataset_version
            FROM canonical_jobs
            WHERE job_id = ? OR normalized_url = ?
            """,
            (command.job_id, normalized_url),
        ).fetchall()
        if any(
            str(row[0]) != command.job_id or not bool(row[1]) or str(row[2]) != DEMO_DATASET_VERSION
            for row in conflicts
        ):
            raise Conflict("The synthetic demo ID or URL is occupied by a non-demo job")

        connection.execute("DELETE FROM canonical_jobs WHERE job_id = ?", (command.job_id,))
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
                ?, ?, ?, ?, ?, 'discovered', ?, ?, ?, ?, ?, ?, NULL, 'complete',
                ?, ?, NULL, ?, ?, ?, 1, ?
            )
            """,
            (
                command.job_id,
                command.canonical_url,
                normalized_url,
                command.company_name,
                command.title,
                observed_at,
                observed_at,
                command.description_text,
                command.location_text,
                command.application_url,
                listing_text,
                command.listing_source_url,
                observed_at,
                command.listing_capture_method,
                listing_digest,
                json.dumps(dict(command.listing_evidence), sort_keys=True),
                command.dataset_version,
            ),
        )
        connection.execute(
            """
            INSERT INTO canonical_job_history(job_id, event_type, occurred_at, to_sha256)
            VALUES (?, 'synthetic_demo_reset', ?, ?)
            """,
            (command.job_id, occurred_at, listing_digest),
        )
        connection.execute(
            """
            INSERT INTO synthetic_demo_ledger(
                dataset_id, dataset_version, fixture_sha256, demo_job_id,
                state, first_seeded_at, state_changed_at, reset_count, last_reset_at
            ) VALUES (?, ?, ?, ?, 'seeded', ?, ?, 1, ?)
            ON CONFLICT(dataset_id) DO UPDATE SET
                dataset_version = excluded.dataset_version,
                fixture_sha256 = excluded.fixture_sha256,
                demo_job_id = excluded.demo_job_id,
                state = 'seeded',
                state_changed_at = excluded.state_changed_at,
                reset_count = synthetic_demo_ledger.reset_count + 1,
                last_reset_at = excluded.last_reset_at
            """,
            (
                DEMO_DATASET_ID,
                DEMO_DATASET_VERSION,
                DEMO_FIXTURE_SHA256,
                command.job_id,
                occurred_at,
                occurred_at,
                occurred_at,
            ),
        )
        connection.commit()
    except (Conflict, sqlite3.Error):
        connection.rollback()
        raise
    finally:
        connection.close()
    return command.job_id

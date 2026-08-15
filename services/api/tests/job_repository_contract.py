from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobos_api.job_repository import CreateJobCommand, JobRepository


def command(
    job_id: str,
    canonical_url: str = "https://jobs.example.com/roles/product-builder",
    *,
    description: str = "Build useful local software.",
    observed_at: datetime | None = None,
) -> CreateJobCommand:
    observed_at = observed_at or datetime(2026, 8, 15, 12, tzinfo=UTC)
    return CreateJobCommand(
        job_id=job_id,
        company_name="Example Labs",
        title="Product Builder",
        canonical_url=canonical_url,
        location_text="Remote",
        description_text=description,
        application_url="https://jobs.example.com/roles/product-builder/apply",
        observed_at=observed_at,
        full_listing_text=description,
        analysis_text="Strong local-first fit.",
        listing_completeness="complete",
        listing_source_url=canonical_url,
        listing_captured_at=observed_at,
        listing_verified_at=observed_at,
        listing_capture_method="contract_fixture",
        listing_sha256="a" * 64,
        listing_evidence={
            "schema_version": 1,
            "end_of_listing_seen": True,
            "coverage": {"sections": ["description", "requirements"]},
        },
    )


def exercise_repository_contract(repository: JobRepository) -> None:
    repository.initialize()
    created = repository.create_job(command("contract-created"))
    assert created.job_id == "contract-created"
    assert repository.get_job(created.job_id) == created
    assert [job.job_id for job in repository.list_jobs()] == [created.job_id]
    assert created.full_listing_text == "Build useful local software."
    assert created.listing_evidence["end_of_listing_seen"] is True

    duplicate = repository.create_job(
        command(
            "contract-duplicate",
            "https://JOBS.example.com/roles/product-builder/#ignored-fragment",
            description="Refreshed complete listing.",
            observed_at=datetime(2026, 8, 15, 12, tzinfo=UTC) + timedelta(minutes=5),
        )
    )
    assert duplicate.job_id == created.job_id
    assert duplicate.description == "Refreshed complete listing."
    assert len(repository.list_jobs()) == 1

    described = repository.update_description(
        created.job_id,
        "Updated by contract.",
        source="contract",
        provenance="synthetic fixture",
    )
    assert described.description == "Updated by contract."
    applied = repository.update_status(
        created.job_id,
        "applied",
        reason="Submitted",
        record_application=True,
    )
    assert applied.status == "applied"
    history = repository.list_history(created.job_id)
    assert [event.event_type for event in history][-2:] == [
        "job_description_updated",
        "lead_state_changed",
    ]
    assert history[-1].from_status == "discovered"
    assert history[-1].to_status == "applied"

from datetime import UTC, datetime

import pytest
from job_repository_contract import exercise_repository_contract
from jobos_api.private_adapters.job_hunter import create_job_hunter_services

job_hunter_models = pytest.importorskip(
    "job_hunter.models",
    reason="run with the JobHunter src directory on PYTHONPATH for the cross-repo contract gate",
)
job_hunter_storage = pytest.importorskip(
    "job_hunter.storage",
    reason="run with the JobHunter src directory on PYTHONPATH for the cross-repo contract gate",
)

JobRecord = job_hunter_models.JobRecord
JobStorage = job_hunter_storage.JobStorage


def test_real_job_hunter_adapter_satisfies_repository_contract(tmp_path):
    database = tmp_path / "contract-jobs.db"
    JobStorage(database)
    adapter, _ = create_job_hunter_services(database, tmp_path)

    exercise_repository_contract(adapter)


def test_real_job_hunter_adapter_records_direct_application(tmp_path):
    database = tmp_path / "jobs.db"
    storage = JobStorage(database)
    observed_at = datetime(2026, 8, 15, tzinfo=UTC)
    storage.upsert_job(
        JobRecord(
            job_id="job-contract",
            canonical_url="https://example.com/jobs/contract",
            source_system="fixture",
            source_job_id="job-contract",
            company_name="Example Co",
            title="Product Builder",
            description_text="Build and verify useful systems.",
            location_text="Remote",
            discovered_at=observed_at,
            last_seen_at=observed_at,
        )
    )
    adapter, _ = create_job_hunter_services(database, tmp_path)

    updated = adapter.update_status(
        "job-contract",
        "applied",
        record_application=True,
    )
    persisted_storage = JobStorage(database, initialize=False)
    lead = persisted_storage.get_lead("job-contract")
    transitions = [
        event
        for event in persisted_storage.list_history("job-contract")
        if event["event_type"] == "lead_state_changed"
    ]

    assert updated.status == "applied"
    assert lead is not None
    assert lead.applied_at is not None
    assert [(event["from_value"], event["to_value"]) for event in transitions] == [
        ("discovered", "applied")
    ]

from datetime import UTC, datetime

import pytest
from jobos_api.adapters import create_job_hunter_adapter

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
    adapter = create_job_hunter_adapter(database, tmp_path)

    updated = adapter.update_lead_state(
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

    assert updated["status"] == "applied"
    assert lead is not None
    assert lead.applied_at is not None
    assert [(event["from_value"], event["to_value"]) for event in transitions] == [
        ("discovered", "applied")
    ]

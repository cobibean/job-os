from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from job_repository_contract import command
from jobos_api.app import create_app
from jobos_api.editable_documents import plain_text
from jobos_api.initialize import initialize_jobos
from jobos_api.job_repository import Conflict
from jobos_api.local_config import load_credentials, read_config, settings_from_config
from jobos_api.sqlite_job_repository import SQLiteJobRepository
from jobos_api.state_store import JobOsStateStore
from jobos_api.synthetic_demo import (
    DEMO_DATASET_ID,
    DEMO_FIXTURE,
    DEMO_FIXTURE_SHA256,
    DEMO_JOB_ID,
    reset_demo,
)


def _repository(profile):
    return SQLiteJobRepository(profile / "jobs/jobs.db")


def test_public_fixture_matches_the_packaged_demo_definition():
    directory = Path(__file__).resolve().parents[3] / "tests/fixtures/demo"
    fixture = json.loads((directory / "jobos-demo-v1.json").read_text(encoding="utf-8"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert fixture == DEMO_FIXTURE
    assert manifest["dataset_id"] == DEMO_DATASET_ID
    assert manifest["fixture_sha256"] == DEMO_FIXTURE_SHA256


def test_demo_seed_mutation_restart_deletion_and_confirmed_reset(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    initialize_jobos(tmp_path)
    repository = _repository(tmp_path)
    jobs = repository.list_jobs()
    assert len(jobs) == 1
    demo = jobs[0]
    assert demo.job_id == DEMO_JOB_ID
    assert demo.synthetic_demo is True
    assert demo.dataset_version == "jobos-demo-v1"
    assert "Fictional Demo" in demo.company
    assert demo.canonical_url.startswith("https://jobs.example.com/")
    state_store = JobOsStateStore(tmp_path / "state/jobos.db")
    starter = state_store.get_job_editable_document(DEMO_JOB_ID, "resume")
    assert starter is not None
    starter_id = starter["document_id"]
    assert "(FAKE)" in plain_text(starter["content"])
    assert "DO NOT APPLY" in plain_text(starter["content"])

    repository.update_description(demo.job_id, "My edited fictional demo.", source="test")
    initialize_jobos(tmp_path)
    assert _repository(tmp_path).get_job(demo.job_id).description == "My edited fictional demo."
    assert (
        JobOsStateStore(tmp_path / "state/jobos.db").get_job_editable_document(
            DEMO_JOB_ID, "resume"
        )["document_id"]
        == starter_id
    )

    repository.delete_job(demo.job_id)
    initialize_jobos(tmp_path)
    assert _repository(tmp_path).list_jobs() == ()
    with sqlite3.connect(tmp_path / "jobs/jobs.db") as connection:
        state = connection.execute(
            "SELECT state FROM synthetic_demo_ledger"
        ).fetchone()[0]
        assert state == "deleted"

    with pytest.raises(ValueError, match="explicit confirmation"):
        reset_demo(repository, tmp_path / "jobs/jobs.db", confirmed=False)
    reset_demo(repository, tmp_path / "jobs/jobs.db", confirmed=True)
    assert len(_repository(tmp_path).list_jobs()) == 1
    with sqlite3.connect(tmp_path / "jobs/jobs.db") as connection:
        state, reset_count = connection.execute(
            "SELECT state, reset_count FROM synthetic_demo_ledger"
        ).fetchone()
        assert (state, reset_count) == ("seeded", 1)


def test_demo_reset_rolls_back_if_the_ledger_update_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    initialize_jobos(tmp_path)
    repository = _repository(tmp_path)
    repository.update_description(DEMO_JOB_ID, "Preserve this edit.", source="test")
    database = tmp_path / "jobs/jobs.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_demo_ledger_update
            BEFORE UPDATE ON synthetic_demo_ledger
            BEGIN
                SELECT RAISE(ABORT, 'simulated ledger failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="simulated ledger failure"):
        reset_demo(repository, database, confirmed=True)

    assert _repository(tmp_path).get_job(DEMO_JOB_ID).description == "Preserve this edit."


def test_demo_metadata_and_intentional_removal_use_public_api_contract(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    initialize_jobos(tmp_path)
    config = read_config(tmp_path / "config.json")
    device_token, _ = load_credentials(config, tmp_path)
    headers = {"Authorization": f"Bearer {device_token}"}
    state_store = JobOsStateStore(tmp_path / "state/jobos.db")
    starter = state_store.get_job_editable_document(DEMO_JOB_ID, "resume")
    assert starter is not None
    assert isinstance(starter["revision"], int)
    state_store.create_editable_snapshot(
        str(starter["document_id"]),
        expected_revision=starter["revision"],
        reason="manual",
        actor="user",
        label="Contains edited private content",
    )
    settings = settings_from_config(tmp_path / "config.json")
    headers["X-JobOS-Profile-Id"] = settings.installation_profile_id
    app = create_app(settings)
    with TestClient(app) as client:
        listed = client.get("/v1/jobs", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["jobs"] == [
            {
                **listed.json()["jobs"][0],
                "synthetic_demo": True,
                "dataset_version": "jobos-demo-v1",
            }
        ]
        removed = client.request(
            "DELETE",
            f"/v1/jobs/{DEMO_JOB_ID}/demo",
            headers=headers,
            json={"origin": "user", "idempotency_key": "remove-demo-test"},
        )
        assert removed.status_code == 200
        replayed = client.request(
            "DELETE",
            f"/v1/jobs/{DEMO_JOB_ID}/demo",
            headers=headers,
            json={"origin": "user", "idempotency_key": "remove-demo-test"},
        )
        assert replayed.status_code == 200
        assert replayed.json() == removed.json()
        assert client.get("/v1/jobs", headers=headers).json() == {"jobs": []}
        assert (
            JobOsStateStore(tmp_path / "state/jobos.db").get_job_editable_document(
                DEMO_JOB_ID, "resume"
            )
            is None
        )
        with sqlite3.connect(tmp_path / "state/jobos.db") as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM editable_document_snapshots"
            ).fetchone()[0] == 0

    initialize_jobos(tmp_path)
    assert _repository(tmp_path).list_jobs() == ()
    assert (
        JobOsStateStore(tmp_path / "state/jobos.db").get_job_editable_document(
            DEMO_JOB_ID, "resume"
        )
        is None
    )

    initialize_jobos(tmp_path, reset_demo_requested=True, reset_confirmed=True)
    restored = JobOsStateStore(tmp_path / "state/jobos.db").get_job_editable_document(
        DEMO_JOB_ID, "resume"
    )
    assert restored is not None
    assert "fictional" in plain_text(restored["content"]).casefold()
    assert "do not apply" in plain_text(restored["content"]).casefold()


def test_removing_demo_preserves_a_different_workspace_selection(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    initialize_jobos(tmp_path)
    repository = _repository(tmp_path)
    other = repository.create_job(command("other-job"))
    state_store = JobOsStateStore(tmp_path / "state/jobos.db")
    state_store.save_job_selection(other.job_id, "user")
    config = read_config(tmp_path / "config.json")
    device_token, _ = load_credentials(config, tmp_path)

    settings = settings_from_config(tmp_path / "config.json")
    with TestClient(create_app(settings)) as client:
        response = client.request(
            "DELETE",
            f"/v1/jobs/{DEMO_JOB_ID}/demo",
            headers={
                "Authorization": f"Bearer {device_token}",
                "X-JobOS-Profile-Id": settings.installation_profile_id,
            },
            json={"origin": "user", "idempotency_key": "preserve-selection"},
        )

    assert response.status_code == 200
    assert state_store.job_workspace_state().selected_job_id == other.job_id


def test_demo_reset_refuses_to_overwrite_a_non_demo_url_owner(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    initialize_jobos(tmp_path, demo_enabled=False)
    repository = _repository(tmp_path)
    real_job = repository.create_job(
        replace(
            command("real-job", DEMO_FIXTURE["canonical_url"]),
            company_name="Real User Company",
            title="Real User Role",
        )
    )

    with pytest.raises(Conflict, match="URL is occupied by a non-demo job"):
        reset_demo(repository, tmp_path / "jobs/jobs.db", confirmed=True)

    preserved = repository.get_job(real_job.job_id)
    assert preserved.company == "Real User Company"
    assert preserved.synthetic_demo is False


def test_demo_reset_refuses_to_delete_a_non_demo_id_collision(tmp_path, monkeypatch):
    monkeypatch.setattr("jobos_api.local_config.sys.platform", "linux")
    initialize_jobos(tmp_path)
    database = tmp_path / "jobs/jobs.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE canonical_jobs SET synthetic_demo = 0, dataset_version = NULL WHERE job_id = ?",
            (DEMO_JOB_ID,),
        )

    repository = _repository(tmp_path)
    with pytest.raises(Conflict, match="non-demo job"):
        reset_demo(repository, tmp_path / "jobs/jobs.db", confirmed=True)

    assert repository.get_job(DEMO_JOB_ID).synthetic_demo is False

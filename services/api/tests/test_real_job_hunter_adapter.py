from __future__ import annotations

import base64
import hashlib
import importlib.util

import pytest
from conftest import build_minimal_pdf
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings

if importlib.util.find_spec("job_hunter") is None:
    pytest.skip(
        "the optional private JobHunter provider is not installed",
        allow_module_level=True,
    )

from job_hunter.facade import JobHunterFacade
from job_hunter.storage import JobStorage


def test_real_job_hunter_adapter_reports_ready_while_jobos_owns_publication(tmp_path):
    workspace = tmp_path / "job-hunter"
    workspace.mkdir()
    database_path = workspace / "data" / "jobs.db"
    storage = JobStorage(database_path)
    facade = JobHunterFacade(storage, workspace_root=workspace)
    created = facade.add_job(
        company_name="Synthetic Company",
        title="Synthetic Applied AI Builder",
        canonical_url="https://example.com/jobs/synthetic-builder",
        location_text="Remote — United States",
        description_text="Build and operate synthetic agent workflows.",
        application_url="https://example.com/jobs/synthetic-builder/apply",
        coverage_complete=True,
        end_of_listing_seen=True,
    )
    job_id = created["job"]["job_id"]
    configured = Settings(
        device_token="real-adapter-device-token",
        mcp_token="real-adapter-trusted-token",
        state_db_path=tmp_path / "jobos" / "state.db",
        job_provider="job-hunter",
        artifact_provider="gateway",
        job_hunter_db_path=database_path,
        local_artifact_root=tmp_path / "jobos" / "artifacts",
        artifact_roots=(workspace / "resume",),
        hermes_job_hunter_cwd=workspace,
    )
    owner_headers = {"Authorization": "Bearer real-adapter-device-token"}
    mcp_headers = {
        "Authorization": "Bearer real-adapter-trusted-token",
        "X-JobOS-MCP-Token": "real-adapter-trusted-token",
    }
    source = b"# Synthetic resume source\n"
    artifact = build_minimal_pdf("Synthetic resume")
    payload = {
        "document_key": "resume",
        "document_label": "Resume",
        "source_filename": "resume.md",
        "source_base64": base64.b64encode(source).decode("ascii"),
        "artifact_filename": "resume.pdf",
        "artifact_base64": base64.b64encode(artifact).decode("ascii"),
        "origin": "mcp",
        "idempotency_key": "real-adapter-publish",
    }

    with TestClient(create_app(configured)) as client:
        health = client.get("/v1/health")
        published = client.post(
            f"/v1/jobs/{job_id}/artifacts/publish",
            headers=mcp_headers,
            json=payload,
        )
        listed = client.get(
            f"/v1/jobs/{job_id}/artifacts",
            headers=owner_headers,
        )
        artifact_id = listed.json()["artifacts"][0]["artifact_id"]
        downloaded = client.get(
            f"/v1/artifacts/{artifact_id}/download",
            headers=owner_headers,
        )

    assert health.status_code == 200
    assert health.json()["artifact_gateway"] == "available"
    assert published.status_code == 200
    assert listed.status_code == 200
    assert downloaded.status_code == 200
    assert downloaded.content == artifact
    assert published.json()["artifacts"][0]["sha256"] == hashlib.sha256(artifact).hexdigest()
    assert listed.json()["artifacts"][0]["sha256"] == hashlib.sha256(artifact).hexdigest()
    assert [
        (row["document_key"], row["media_type"], row["render_status"])
        for row in listed.json()["artifacts"]
    ] == [("resume", "application/pdf", "succeeded")]

    reopened = JobHunterFacade(
        JobStorage(database_path, initialize=False),
        workspace_root=workspace,
    )
    persisted = reopened.list_job_artifacts(job_id)
    assert persisted == []
    stored = list((tmp_path / "jobos" / "artifacts" / "agent-publications").rglob("*.pdf"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == artifact

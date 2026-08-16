import base64
from hashlib import sha256

from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.artifact_gateway import UnavailableArtifactGateway
from jobos_api.settings import Settings


class FailingRefreshArtifactGateway(UnavailableArtifactGateway):
    def list_job_artifacts(self, job_id: str):
        raise OSError("synthetic artifact read failure")


def test_public_composition_starts_without_private_services(tmp_path):
    """The application core must compose without JobHunter, Hermes, or Tailscale."""
    settings = Settings(
        device_token="local-device-token",
        mcp_token="local-mcp-test-token",
        state_db_path=tmp_path / "jobos.db",
        job_hunter_db_path=None,
        artifact_roots=(),
        hermes_dashboard_url=None,
        hermes_dashboard_token=None,
        hermes_job_hunter_cwd=None,
    )

    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["agent"] == "not-configured"


def test_sqlite_local_editable_lifecycle_survives_restart_and_downloads_pair(
    tmp_path, minimal_docx, minimal_pdf
):
    settings = Settings(
        device_token="local-device-token",
        mcp_token="local-mcp-test-token",
        state_db_path=tmp_path / "application-data/state/jobos.db",
        jobs_db_path=tmp_path / "application-data/jobs/jobs.db",
        local_artifact_root=tmp_path / "application-data/artifacts",
    )
    headers = {"Authorization": "Bearer local-device-token"}
    with TestClient(create_app(settings)) as client:
        job = client.post(
            "/v1/jobs",
            headers=headers,
            json={
                "company_name": "(FAKE) Local Lifecycle Company",
                "title": "(FAKE) Local Lifecycle Role",
                "canonical_url": "https://example.com/(FAKE)-local-lifecycle",
                "location_text": "Example City",
                "description_text": "Synthetic local lifecycle test job.",
                "application_url": "https://example.com/(FAKE)-local-lifecycle/apply",
                "idempotency_key": "local-lifecycle-job",
            },
        ).json()["job"]
        created = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=headers,
            json={
                "mode": "blank",
                "document_key": "resume",
                "idempotency_key": "local-lifecycle-create",
            },
        ).json()
        content = created["content"]
        content["content"][1]["content"][0]["content"] = [
            {"type": "text", "text": "(FAKE) Autosave-like local edit."}
        ]
        saved = client.put(
            f"/v1/editable-documents/{created['document_id']}",
            headers=headers,
            json={
                "base_revision": created["revision"],
                "content": content,
                "settings": created["settings"],
                "comments": [],
                "idempotency_key": "local-lifecycle-save",
            },
        ).json()
        snapshot = client.post(
            f"/v1/editable-documents/{created['document_id']}/snapshots",
            headers=headers,
            json={
                "base_revision": saved["revision"],
                "label": "(FAKE) Before local publication",
                "idempotency_key": "local-lifecycle-snapshot",
            },
        )
        assert snapshot.status_code == 201

    with TestClient(create_app(settings)) as restarted:
        reopened = restarted.get(
            f"/v1/editable-documents/{created['document_id']}", headers=headers
        ).json()
        assert reopened == saved
        imported_docx = minimal_docx("(FAKE) local import")
        imported = restarted.post(
            f"/v1/editable-documents/{created['document_id']}/import",
            headers=headers,
            json={
                "base_revision": reopened["revision"],
                "source": {
                    "mode": "import_external_docx",
                    "document_key": "resume",
                    "source_filename": "(FAKE)-Imported-Resume.docx",
                    "source_base64": base64.b64encode(imported_docx).decode(),
                    "source_sha256": sha256(imported_docx).hexdigest(),
                    "content": reopened["content"],
                    "settings": reopened["settings"],
                    "import_report": {
                        "source_filename": "(FAKE)-Imported-Resume.docx",
                        "imported_at": "2026-08-15T12:00:00Z",
                        "issues": [],
                    },
                    "idempotency_key": "local-lifecycle-import-source",
                },
                "idempotency_key": "local-lifecycle-import",
            },
        )
        assert imported.status_code == 200
        imported_document = imported.json()
        assert imported_document["revision"] == reopened["revision"] + 1
        docx = minimal_docx("(FAKE) local lifecycle DOCX")
        pdf = minimal_pdf("(FAKE) local lifecycle PDF")
        publish = restarted.post(
            f"/v1/editable-documents/{created['document_id']}/publish",
            headers=headers,
            json={
                "expected_revision": imported_document["revision"],
                "docx_filename": "(FAKE)-Local-Resume.docx",
                "docx_base64": base64.b64encode(docx).decode(),
                "docx_sha256": sha256(docx).hexdigest(),
                "pdf_filename": "(FAKE)-Local-Resume.pdf",
                "pdf_base64": base64.b64encode(pdf).decode(),
                "pdf_sha256": sha256(pdf).hexdigest(),
                "idempotency_key": "local-lifecycle-publish",
            },
        )
        assert publish.status_code == 200
        assert publish.json()["published_revision"] == imported_document["revision"]

    with TestClient(create_app(settings)) as restarted_again:
        artifacts = restarted_again.get(
            f"/v1/jobs/{job['job_id']}/artifacts", headers=headers
        ).json()["artifacts"]
        assert len(artifacts) == 3
        expected = {
            sha256(pdf).hexdigest(): pdf,
            sha256(docx).hexdigest(): docx,
            sha256(imported_docx).hexdigest(): imported_docx,
        }
        for artifact in artifacts:
            response = restarted_again.get(
                f"/v1/artifacts/{artifact['artifact_id']}/download", headers=headers
            )
            assert response.status_code == 200
            assert response.content == expected[artifact["sha256"]]
            assert response.headers["x-content-sha256"] == sha256(response.content).hexdigest()


def test_private_artifact_capabilities_report_stable_unconfigured_errors(
    tmp_path, minimal_docx, minimal_pdf
):
    settings = Settings(
        device_token="local-device-token",
        mcp_token="local-mcp-test-token",
        state_db_path=tmp_path / "state/jobos.db",
    )
    headers = {"Authorization": "Bearer local-device-token"}
    with TestClient(create_app(settings)) as client:
        job = client.post(
            "/v1/jobs",
            headers=headers,
            json={
                "company_name": "(FAKE) Capability Company",
                "title": "(FAKE) Capability Role",
                "canonical_url": "https://example.com/(FAKE)-capability",
                "location_text": "Example City",
                "description_text": "Synthetic capability test job.",
                "application_url": "https://example.com/(FAKE)-capability/apply",
                "idempotency_key": "capability-job",
            },
        ).json()["job"]
        refreshed = client.post(f"/v1/jobs/{job['job_id']}/artifacts/refresh", headers=headers)
        rendered = client.post(
            f"/v1/jobs/{job['job_id']}/artifacts/render",
            headers=headers,
            json={
                "source_id": "source-1",
                "origin": "user",
                "idempotency_key": "capability-render",
            },
        )
        registered = client.post(
            f"/v1/jobs/{job['job_id']}/artifacts/register",
            headers=headers,
            json={
                "artifact_reference": "private-artifact-reference",
                "origin": "user",
                "idempotency_key": "capability-register",
            },
        )
        published = client.post(
            f"/v1/jobs/{job['job_id']}/artifacts/publish",
            headers=headers,
            json={
                "document_key": "resume",
                "document_label": "Resume",
                "source_filename": "source.docx",
                "source_base64": base64.b64encode(minimal_docx("source")).decode(),
                "artifact_filename": "resume.pdf",
                "artifact_base64": base64.b64encode(minimal_pdf()).decode(),
                "origin": "user",
                "idempotency_key": "capability-publish",
            },
        )

    for label, response, expected_code, expected_message in (
        (
            "refresh",
            refreshed,
            "artifact_provider_unavailable",
            "Artifact provider is unavailable",
        ),
        ("render", rendered, "renderer_unavailable", "Renderer is unavailable"),
        (
            "register",
            registered,
            "artifact_provider_unavailable",
            "Artifact provider is unavailable",
        ),
        (
            "publish",
            published,
            "artifact_provider_unavailable",
            "Artifact provider is unavailable",
        ),
    ):
        assert response.status_code == 503
        assert response.json()["code"] == expected_code, (label, response.json())
        assert response.json()["message"] == expected_message, label
        assert response.json()["retryable"] is True
        assert response.json()["correlation_id"] == response.headers["x-correlation-id"]


def test_artifact_refresh_maps_filesystem_failures_to_stable_503(tmp_path):
    settings = Settings(
        device_token="local-device-token",
        mcp_token="local-mcp-test-token",
        state_db_path=tmp_path / "state/jobos.db",
    )
    headers = {"Authorization": "Bearer local-device-token"}
    app = create_app(settings, artifact_gateway=FailingRefreshArtifactGateway())
    with TestClient(app) as client:
        job = client.post(
            "/v1/jobs",
            headers=headers,
            json={
                "company_name": "(FAKE) Refresh Failure Company",
                "title": "(FAKE) Refresh Failure Role",
                "canonical_url": "https://example.com/(FAKE)-refresh-failure",
                "location_text": "Example City",
                "description_text": "Synthetic artifact failure test.",
                "application_url": "https://example.com/(FAKE)-refresh-failure/apply",
                "idempotency_key": "refresh-failure-job",
            },
        ).json()["job"]
        response = client.post(
            f"/v1/jobs/{job['job_id']}/artifacts/refresh", headers=headers
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Local artifact storage is unavailable"


def test_local_artifact_download_maps_filesystem_failures_to_stable_503(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch
):
    settings = Settings(
        device_token="local-device-token",
        mcp_token="local-mcp-test-token",
        state_db_path=tmp_path / "state/jobos.db",
        jobs_db_path=tmp_path / "jobs/jobs.db",
        local_artifact_root=tmp_path / "artifacts",
    )
    headers = {"Authorization": "Bearer local-device-token"}
    with TestClient(create_app(settings)) as client:
        job = client.post(
            "/v1/jobs",
            headers=headers,
            json={
                "company_name": "(FAKE) Download Failure Company",
                "title": "(FAKE) Download Failure Role",
                "canonical_url": "https://example.com/(FAKE)-download-failure",
                "location_text": "Example City",
                "description_text": "Synthetic download failure test.",
                "application_url": "https://example.com/(FAKE)-download-failure/apply",
                "idempotency_key": "download-failure-job",
            },
        ).json()["job"]
        document = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=headers,
            json={
                "mode": "blank",
                "document_key": "resume",
                "idempotency_key": "download-failure-document",
            },
        ).json()
        docx = minimal_docx("(FAKE) local download failure")
        pdf = minimal_pdf("(FAKE) local download failure")
        published = client.post(
            f"/v1/editable-documents/{document['document_id']}/publish",
            headers=headers,
            json={
                "expected_revision": document["revision"],
                "docx_filename": "(FAKE)-Resume.docx",
                "docx_base64": base64.b64encode(docx).decode(),
                "docx_sha256": sha256(docx).hexdigest(),
                "pdf_filename": "(FAKE)-Resume.pdf",
                "pdf_base64": base64.b64encode(pdf).decode(),
                "pdf_sha256": sha256(pdf).hexdigest(),
                "idempotency_key": "download-failure-publish",
            },
        )
        assert published.status_code == 200
        artifact_id = client.get(
            f"/v1/jobs/{job['job_id']}/artifacts", headers=headers
        ).json()["artifacts"][0]["artifact_id"]

        def fail_read(*args, **kwargs):
            raise OSError("synthetic local artifact read failure")

        monkeypatch.setattr(
            "jobos_api.local_artifact_repository.LocalArtifactRepository.read", fail_read
        )
        response = client.get(f"/v1/artifacts/{artifact_id}/download", headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "Local artifact storage is unavailable"

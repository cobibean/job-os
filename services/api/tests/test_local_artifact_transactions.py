from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier

import jobos_api.local_artifact_repository as local_repository_module
from fastapi.testclient import TestClient
from jobos_api.app import LOCAL_ARTIFACT_STORAGE_UNAVAILABLE, create_app
from jobos_api.artifact_repository import ArtifactWrite
from jobos_api.editable_documents import blank_content, default_settings
from jobos_api.local_artifact_repository import LocalArtifactRepository
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore

HEADERS = {"Authorization": "Bearer local-device-token"}


def configured_settings(tmp_path: Path) -> Settings:
    return Settings(
        device_token="local-device-token",
        mcp_token="local-mcp-test-token",
        state_db_path=tmp_path / "state/jobos.db",
        jobs_db_path=tmp_path / "jobs/jobs.db",
        local_artifact_root=tmp_path / "artifacts",
    )


def create_job(client: TestClient, suffix: str = "transaction") -> dict[str, object]:
    response = client.post(
        "/v1/jobs",
        headers=HEADERS,
        json={
            "company_name": f"(FAKE) {suffix} Company",
            "title": f"(FAKE) {suffix} Role",
            "canonical_url": f"https://example.com/{suffix}",
            "location_text": "Example City",
            "description_text": "Synthetic transaction coverage.",
            "application_url": f"https://example.com/{suffix}/apply",
            "idempotency_key": f"job-{suffix}",
        },
    )
    assert response.status_code == 200
    return response.json()["job"]


def create_blank_document(client: TestClient, job_id: str) -> dict[str, object]:
    response = client.post(
        f"/v1/jobs/{job_id}/editable-documents",
        headers=HEADERS,
        json={
            "mode": "blank",
            "document_key": "resume",
            "idempotency_key": f"blank-{job_id}",
        },
    )
    assert response.status_code == 201
    return response.json()


def external_import_payload(
    *,
    source: bytes,
    content: dict[str, object],
    settings: dict[str, object],
    idempotency_key: str,
) -> dict[str, object]:
    return {
        "mode": "import_external_docx",
        "document_key": "resume",
        "source_filename": "(FAKE)-Imported.docx",
        "source_base64": base64.b64encode(source).decode(),
        "source_sha256": sha256(source).hexdigest(),
        "content": content,
        "settings": settings,
        "import_report": {
            "source_filename": "(FAKE)-Imported.docx",
            "imported_at": "2026-08-15T12:00:00Z",
            "issues": [],
        },
        "idempotency_key": idempotency_key,
    }


def publication_payload(
    *, docx: bytes, pdf: bytes, revision: int, idempotency_key: str
) -> dict[str, object]:
    return {
        "expected_revision": revision,
        "docx_filename": "(FAKE)-Resume.docx",
        "docx_base64": base64.b64encode(docx).decode(),
        "docx_sha256": sha256(docx).hexdigest(),
        "pdf_filename": "(FAKE)-Resume.pdf",
        "pdf_base64": base64.b64encode(pdf).decode(),
        "pdf_sha256": sha256(pdf).hexdigest(),
        "idempotency_key": idempotency_key,
    }


class CallbackImportRepository(LocalArtifactRepository):
    callback = None

    def store_import(self, *, job_id: str, document_id: str, artifact: ArtifactWrite):
        stored = super().store_import(
            job_id=job_id, document_id=document_id, artifact=artifact
        )
        if self.callback is not None:
            callback, self.callback = self.callback, None
            callback()
        return stored


class FailingArtifactRepository(LocalArtifactRepository):
    def store_import(self, *, job_id: str, document_id: str, artifact: ArtifactWrite):
        raise PermissionError("synthetic storage failure")

    def store_publication_pair(self, **kwargs):
        raise OSError("synthetic fsync failure")


def test_external_replace_conflict_rolls_back_artifact_metadata(tmp_path, minimal_docx):
    settings = configured_settings(tmp_path)
    state_store = JobOsStateStore(settings.state_db_path)
    repository = CallbackImportRepository(settings.resolved_local_artifact_root())
    app = create_app(settings, state_store=state_store, artifact_repository=repository)
    with TestClient(app) as client:
        job = create_job(client, "replace-conflict")
        document = create_blank_document(client, str(job["job_id"]))

        def concurrent_edit() -> None:
            state_store.save_editable_document(
                str(document["document_id"]),
                expected_revision=int(document["revision"]),
                content=document["content"],
                settings=document["settings"],
                comments=[],
            )

        repository.callback = concurrent_edit
        source = minimal_docx("replace conflict")
        response = client.post(
            f"/v1/editable-documents/{document['document_id']}/import",
            headers=HEADERS,
            json={
                "base_revision": document["revision"],
                "source": external_import_payload(
                    source=source,
                    content=document["content"],
                    settings=document["settings"],
                    idempotency_key="replace-conflict-source",
                ),
                "idempotency_key": "replace-conflict",
            },
        )
        artifacts = client.get(
            f"/v1/jobs/{job['job_id']}/artifacts", headers=HEADERS
        ).json()["artifacts"]

    assert response.status_code == 409
    assert artifacts == []


def test_external_create_conflict_rolls_back_artifact_metadata(tmp_path, minimal_docx):
    settings = configured_settings(tmp_path)
    state_store = JobOsStateStore(settings.state_db_path)
    repository = CallbackImportRepository(settings.resolved_local_artifact_root())
    app = create_app(settings, state_store=state_store, artifact_repository=repository)
    with TestClient(app) as client:
        job = create_job(client, "create-conflict")
        content = blank_content("resume")
        settings_value = default_settings()

        def competing_create() -> None:
            state_store.create_editable_document(
                job_id=str(job["job_id"]),
                document_key="resume",
                document_label="Resume",
                content=content,
                settings=settings_value,
                comments=[],
                import_report={"source_filename": None, "imported_at": None, "issues": []},
            )

        repository.callback = competing_create
        source = minimal_docx("create conflict")
        response = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=HEADERS,
            json=external_import_payload(
                source=source,
                content=content,
                settings=settings_value,
                idempotency_key="create-conflict",
            ),
        )
        artifacts = client.get(
            f"/v1/jobs/{job['job_id']}/artifacts", headers=HEADERS
        ).json()["artifacts"]

    assert response.status_code == 409
    assert artifacts == []


def test_import_and_publication_storage_failures_are_stable_503(
    tmp_path, minimal_docx, minimal_pdf
):
    settings = configured_settings(tmp_path)
    repository = FailingArtifactRepository(settings.resolved_local_artifact_root())
    with TestClient(create_app(settings, artifact_repository=repository)) as client:
        job = create_job(client, "storage-errors")
        content = blank_content("resume")
        settings_value = default_settings()
        source = minimal_docx("storage error")
        imported = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=HEADERS,
            json=external_import_payload(
                source=source,
                content=content,
                settings=settings_value,
                idempotency_key="storage-import",
            ),
        )
        document = create_blank_document(client, str(job["job_id"]))
        published = client.post(
            f"/v1/editable-documents/{document['document_id']}/publish",
            headers=HEADERS,
            json=publication_payload(
                docx=source,
                pdf=minimal_pdf(),
                revision=int(document["revision"]),
                idempotency_key="storage-publish",
            ),
        )

    assert (imported.status_code, imported.json()["detail"]) == (
        503,
        LOCAL_ARTIFACT_STORAGE_UNAVAILABLE,
    )
    assert (published.status_code, published.json()["detail"]) == (
        503,
        LOCAL_ARTIFACT_STORAGE_UNAVAILABLE,
    )


def test_import_and_publication_validation_failures_remain_422(tmp_path, minimal_pdf):
    settings = configured_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        job = create_job(client, "validation-errors")
        content = blank_content("resume")
        settings_value = default_settings()
        malformed = b"PK\x03\x04not-a-docx"
        imported = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=HEADERS,
            json=external_import_payload(
                source=malformed,
                content=content,
                settings=settings_value,
                idempotency_key="validation-import",
            ),
        )
        document = create_blank_document(client, str(job["job_id"]))
        published = client.post(
            f"/v1/editable-documents/{document['document_id']}/publish",
            headers=HEADERS,
            json=publication_payload(
                docx=malformed,
                pdf=minimal_pdf(),
                revision=int(document["revision"]),
                idempotency_key="validation-publish",
            ),
        )

    assert imported.status_code == 422
    assert published.status_code == 422


def test_replaced_repository_root_and_destination_mismatch_are_stable_503(
    tmp_path, minimal_docx, minimal_pdf
):
    settings = configured_settings(tmp_path)
    repository = LocalArtifactRepository(settings.resolved_local_artifact_root())
    with TestClient(create_app(settings, artifact_repository=repository)) as client:
        job = create_job(client, "repository-integrity")
        source = minimal_docx("(FAKE) replaced root")
        held_root = tmp_path / "held-artifacts"
        repository.root.rename(held_root)
        outside = tmp_path / "outside-artifacts"
        outside.mkdir()
        repository.root.symlink_to(outside, target_is_directory=True)
        replaced_root = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=HEADERS,
            json=external_import_payload(
                source=source,
                content=blank_content("resume"),
                settings=default_settings(),
                idempotency_key="replaced-root",
            ),
        )

    settings = configured_settings(tmp_path / "destination")
    repository = LocalArtifactRepository(settings.resolved_local_artifact_root())
    with TestClient(create_app(settings, artifact_repository=repository)) as client:
        job = create_job(client, "destination-mismatch")
        document = create_blank_document(client, str(job["job_id"]))
        docx = minimal_docx("(FAKE) destination mismatch")
        pdf = minimal_pdf("(FAKE) destination mismatch")
        docx_hash = sha256(docx).hexdigest()
        pdf_hash = sha256(pdf).hexdigest()
        pair_hash = sha256(
            f"{document['revision']}\0{docx_hash}\0{pdf_hash}".encode("ascii")
        ).hexdigest()
        parent = repository._directory(
            "publications", str(job["job_id"]), str(document["document_id"])
        )
        (parent / f"revision-{document['revision']}-{pair_hash[:20]}").write_bytes(
            b"not a publication directory"
        )
        mismatch = client.post(
            f"/v1/editable-documents/{document['document_id']}/publish",
            headers=HEADERS,
            json=publication_payload(
                docx=docx,
                pdf=pdf,
                revision=int(document["revision"]),
                idempotency_key="destination-mismatch",
            ),
        )

    assert (replaced_root.status_code, replaced_root.json()["detail"]) == (
        503,
        LOCAL_ARTIFACT_STORAGE_UNAVAILABLE,
    )
    assert (mismatch.status_code, mismatch.json()["detail"]) == (
        503,
        LOCAL_ARTIFACT_STORAGE_UNAVAILABLE,
    )


def test_too_long_caller_filename_is_422(tmp_path, minimal_docx):
    settings = configured_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        job = create_job(client, "filename-boundary")
        payload = external_import_payload(
            source=minimal_docx("(FAKE) filename boundary"),
            content=blank_content("resume"),
            settings=default_settings(),
            idempotency_key="filename-boundary",
        )
        payload["source_filename"] = "a" * 230 + ".docx"
        response = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=HEADERS,
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "Artifact filename must be a plain filename"


def test_import_fsync_failure_never_registers_metadata_and_retry_resyncs(
    tmp_path, minimal_docx, monkeypatch
):
    settings = configured_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        job = create_job(client, "fsync-retry")
        source = minimal_docx("fsync retry")
        payload = external_import_payload(
            source=source,
            content=blank_content("resume"),
            settings=default_settings(),
            idempotency_key="fsync-retry",
        )
        real_write_new_file_at = local_repository_module.write_new_file_at

        def fail_after_write(directory_descriptor, filename, content):
            real_write_new_file_at(directory_descriptor, filename, content)
            raise OSError("synthetic directory fsync failure")

        monkeypatch.setattr(
            local_repository_module, "write_new_file_at", fail_after_write
        )
        failed = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=HEADERS,
            json=payload,
        )
        after_failure = client.get(
            f"/v1/jobs/{job['job_id']}/artifacts", headers=HEADERS
        ).json()["artifacts"]
        monkeypatch.setattr(
            local_repository_module, "write_new_file_at", real_write_new_file_at
        )
        retried = client.post(
            f"/v1/jobs/{job['job_id']}/editable-documents",
            headers=HEADERS,
            json=payload,
        )
        after_retry = client.get(
            f"/v1/jobs/{job['job_id']}/artifacts", headers=HEADERS
        ).json()["artifacts"]

    assert (failed.status_code, failed.json()["detail"]) == (
        503,
        LOCAL_ARTIFACT_STORAGE_UNAVAILABLE,
    )
    assert after_failure == []
    assert retried.status_code == 201
    assert len(after_retry) == 1


def test_post_rename_publication_fsync_failure_is_503_without_success_registration(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch
):
    settings = configured_settings(tmp_path)
    state_store = JobOsStateStore(settings.state_db_path)
    repository = LocalArtifactRepository(settings.resolved_local_artifact_root())
    app = create_app(settings, state_store=state_store, artifact_repository=repository)
    with TestClient(app) as client:
        job = create_job(client, "publication-post-rename-fsync")
        document = create_blank_document(client, str(job["job_id"]))
        real_fsync = local_repository_module.os.fsync
        real_rename = local_repository_module.os.rename
        rename_succeeded = False

        def rename_then_mark(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
            nonlocal rename_succeeded
            result = real_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            rename_succeeded = True
            return result

        def fail_after_rename(descriptor):
            if rename_succeeded:
                raise OSError("synthetic post-rename parent fsync failure")
            real_fsync(descriptor)

        monkeypatch.setattr(local_repository_module.os, "rename", rename_then_mark)
        monkeypatch.setattr(local_repository_module.os, "fsync", fail_after_rename)
        failed = client.post(
            f"/v1/editable-documents/{document['document_id']}/publish",
            headers=HEADERS,
            json=publication_payload(
                docx=minimal_docx("post-rename fsync failure"),
                pdf=minimal_pdf("(FAKE) post-rename fsync failure"),
                revision=int(document["revision"]),
                idempotency_key="post-rename-fsync-failure",
            ),
        )

    artifacts, current, last_successful, approved = state_store.list_document_artifacts(
        str(job["job_id"])
    )
    persisted_document = state_store.get_editable_document(str(document["document_id"]))
    assert rename_succeeded
    assert (failed.status_code, failed.json()["detail"]) == (
        503,
        LOCAL_ARTIFACT_STORAGE_UNAVAILABLE,
    )
    assert artifacts == []
    assert (current, last_successful, approved) == (None, None, None)
    assert persisted_document is not None
    assert persisted_document["published_revision"] is None


def test_concurrent_different_publication_pairs_accept_exactly_one(
    tmp_path, minimal_docx, minimal_pdf
):
    settings = configured_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        job = create_job(client, "publication-race")
        document = create_blank_document(client, str(job["job_id"]))
        payloads = [
            publication_payload(
                docx=minimal_docx(f"publication {index}"),
                pdf=minimal_pdf(f"(FAKE) publication {index}"),
                revision=int(document["revision"]),
                idempotency_key=f"publication-race-{index}",
            )
            for index in range(2)
        ]
        barrier = Barrier(2)

        def publish(payload: dict[str, object]):
            barrier.wait()
            return client.post(
                f"/v1/editable-documents/{document['document_id']}/publish",
                headers=HEADERS,
                json=payload,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(publish, payloads))
        artifacts = client.get(
            f"/v1/jobs/{job['job_id']}/artifacts", headers=HEADERS
        ).json()["artifacts"]

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(artifacts) == 2
    successful_index = next(
        index for index, response in enumerate(responses) if response.status_code == 200
    )
    successful = payloads[successful_index]
    assert {artifact["sha256"] for artifact in artifacts} == {
        successful["docx_sha256"],
        successful["pdf_sha256"],
    }


def test_concurrent_identical_publication_pair_is_idempotent(
    tmp_path, minimal_docx, minimal_pdf
):
    settings = configured_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        job = create_job(client, "publication-replay")
        document = create_blank_document(client, str(job["job_id"]))
        docx = minimal_docx("identical publication")
        pdf = minimal_pdf("(FAKE) identical publication")
        payloads = [
            publication_payload(
                docx=docx,
                pdf=pdf,
                revision=int(document["revision"]),
                idempotency_key=f"publication-replay-{index}",
            )
            for index in range(2)
        ]
        barrier = Barrier(2)

        def publish(payload: dict[str, object]):
            barrier.wait()
            return client.post(
                f"/v1/editable-documents/{document['document_id']}/publish",
                headers=HEADERS,
                json=payload,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(publish, payloads))
        artifacts = client.get(
            f"/v1/jobs/{job['job_id']}/artifacts", headers=HEADERS
        ).json()["artifacts"]

    assert [response.status_code for response in responses] == [200, 200]
    assert len(artifacts) == 2

import base64
import os
from hashlib import sha256

import jobos_api.artifact_repository as artifact_repository_module
import pytest
from jobos_api.artifact_repository import ArtifactStorageError
from jobos_api.documents import (
    ArtifactPublishRequest,
    materialize_external_import,
    materialize_published_document,
)

JOB_ID = "job-1"
DOCUMENT_ID = "edoc_abcdefghijklmnopqrstuvwx"


def publish_request(source: bytes, artifact: bytes) -> ArtifactPublishRequest:
    return ArtifactPublishRequest.model_validate(
        {
            "document_key": "resume",
            "document_label": "Resume",
            "source_filename": "resume.docx",
            "source_base64": base64.b64encode(source).decode("ascii"),
            "artifact_filename": "resume.pdf",
            "artifact_base64": base64.b64encode(artifact).decode("ascii"),
            "origin": "mcp",
            "idempotency_key": "materialize-test",
        }
    )


def materialize(flow: str, workspace, source: bytes, artifact: bytes):
    if flow == "published_document":
        return materialize_published_document(
            publish_request(source, artifact),
            job_id=JOB_ID,
            workspace_root=workspace,
        )
    return materialize_external_import(
        job_id=JOB_ID,
        document_id=DOCUMENT_ID,
        document_key="resume",
        source_filename="resume.docx",
        source_sha256=sha256(source).hexdigest(),
        source_bytes=source,
        workspace_root=workspace,
    )


@pytest.mark.parametrize("flow", ["published_document", "external_import"])
@pytest.mark.parametrize("swapped_segment", ["resume", "exports", "job"])
def test_materialization_fails_closed_when_publication_ancestor_is_swapped(
    tmp_path, monkeypatch, flow, swapped_segment
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "private.txt"
    sentinel.write_bytes(b"outside-private")
    source = b"source-docx-bytes"
    artifact = b"artifact-pdf-bytes"
    real_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and flags & os.O_CREAT:
            ancestors = {
                "resume": workspace / "resume",
                "exports": workspace / "resume" / "exports",
                "job": workspace / "resume" / "exports" / "jobos" / JOB_ID,
            }
            ancestor = ancestors[swapped_segment]
            held = ancestor.with_name(f"held-{ancestor.name}")
            ancestor.rename(held)
            ancestor.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(artifact_repository_module.os, "open", racing_open)

    with pytest.raises(ArtifactStorageError, match="directory changed during storage"):
        materialize(flow, workspace, source, artifact)

    assert swapped
    assert sentinel.read_bytes() == b"outside-private"
    assert list(outside.iterdir()) == [sentinel]


@pytest.mark.parametrize("flow", ["published_document", "external_import"])
@pytest.mark.parametrize("swapped_segment", ["resume", "exports", "job", "imports"])
def test_pair_materialization_cleans_first_file_after_between_write_ancestor_move(
    tmp_path, monkeypatch, flow, swapped_segment
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "private.txt"
    sentinel.write_bytes(b"outside-private")
    source = b"source-docx-bytes"
    artifact = b"artifact-pdf-bytes"
    real_materialize = artifact_repository_module._materialize_idempotent_file
    swapped = False
    held = tmp_path / f"held-{swapped_segment}"

    def move_after_first_materialization(directory, filename, content):
        nonlocal swapped
        result = real_materialize(directory, filename, content)
        if not swapped:
            ancestors = {
                "resume": workspace / "resume",
                "exports": workspace / "resume" / "exports",
                "job": workspace / "resume" / "exports" / "jobos" / JOB_ID,
                "imports": workspace
                / "resume"
                / "exports"
                / "jobos"
                / JOB_ID
                / "imports",
            }
            ancestor = ancestors[swapped_segment]
            ancestor.rename(held)
            ancestor.symlink_to(outside, target_is_directory=True)
            swapped = True
        return result

    monkeypatch.setattr(
        artifact_repository_module,
        "_materialize_idempotent_file",
        move_after_first_materialization,
    )

    with pytest.raises(ArtifactStorageError, match="directory changed during storage"):
        materialize(flow, workspace, source, artifact)

    assert swapped
    assert not any(path.is_file() for path in held.rglob("*"))
    assert sentinel.read_bytes() == b"outside-private"
    assert list(outside.iterdir()) == [sentinel]


def test_pair_cleanup_never_claims_or_deletes_post_creation_replacement(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = b"source-docx-bytes"
    artifact = b"artifact-pdf-bytes"
    foreign = b"foreign-replacement"
    real_materialize = artifact_repository_module._materialize_idempotent_file
    calls = 0
    replacement = None

    def replace_after_first_creation(directory, filename, content):
        nonlocal calls, replacement
        calls += 1
        path, created_identity = real_materialize(directory, filename, content)
        if calls == 1:
            held = path.with_name(f"held-{path.name}")
            path.rename(held)
            path.write_bytes(foreign)
            replacement = path
            return path, created_identity
        raise ArtifactStorageError("forced second write failure")

    monkeypatch.setattr(
        artifact_repository_module,
        "_materialize_idempotent_file",
        replace_after_first_creation,
    )

    with pytest.raises(ArtifactStorageError, match="forced second write failure"):
        materialize_published_document(
            publish_request(source, artifact),
            job_id=JOB_ID,
            workspace_root=workspace,
        )

    assert replacement is not None
    assert replacement.read_bytes() == foreign


@pytest.mark.parametrize("flow", ["published_document", "external_import"])
def test_materialization_idempotently_reuses_verified_content_addressed_files(
    tmp_path, flow
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = b"source-docx-bytes"
    artifact = b"artifact-pdf-bytes"

    first = materialize(flow, workspace, source, artifact)
    second = materialize(flow, workspace, source, artifact)

    assert second == first
    if flow == "published_document":
        assert [path.read_bytes() for path in second] == [source, artifact]
    else:
        assert second[1].read_bytes() == source

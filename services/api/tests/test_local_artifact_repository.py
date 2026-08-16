from __future__ import annotations

import os
import stat
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from zipfile import ZIP_DEFLATED, ZipFile

import jobos_api.artifact_repository as artifact_repository_module
import jobos_api.local_artifact_repository as local_repository_module
import pytest
from jobos_api.artifact_repository import (
    DOCX_MEDIA_TYPE,
    MAX_CALLER_FILENAME_BYTES,
    PDF_MEDIA_TYPE,
    ArtifactRepositoryError,
    ArtifactStorageError,
    ArtifactValidationError,
    ArtifactWrite,
    verify_artifact_file,
)
from jobos_api.local_artifact_repository import LocalArtifactRepository


def write(filename: str, media_type: str, content: bytes) -> ArtifactWrite:
    return ArtifactWrite(filename, media_type, content, sha256(content).hexdigest())


def test_local_repository_availability_proves_write_fsync_and_cleanup(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    repository = LocalArtifactRepository(root)
    real_open = os.open

    def reject_probe(path, flags, mode=0o777, *, dir_fd=None):
        if isinstance(path, str) and path.startswith(".availability-"):
            raise PermissionError("read-only artifact directory")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(local_repository_module.os, "open", reject_probe)

    assert repository.is_available() is False
    assert list(root.iterdir()) == []


def test_local_repository_availability_cleans_a_failed_probe(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    repository = LocalArtifactRepository(root)

    def fail_write(_descriptor, _content):
        raise OSError("simulated probe write failure")

    monkeypatch.setattr(local_repository_module.os, "write", fail_write)

    assert repository.is_available() is False
    assert list(root.iterdir()) == []


def test_local_repository_availability_never_unlinks_a_replacement(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    repository = LocalArtifactRepository(root)
    real_open = os.open
    real_write = os.write
    probe_name = None
    replacement = b"user data"

    def capture_probe(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal probe_name
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(".availability-"):
            probe_name = path
        return descriptor

    def replace_after_unlink(descriptor, content):
        assert probe_name is not None
        replacement_path = root / probe_name
        assert not replacement_path.exists()
        replacement_path.write_bytes(replacement)
        return real_write(descriptor, content)

    monkeypatch.setattr(local_repository_module.os, "open", capture_probe)
    monkeypatch.setattr(local_repository_module.os, "write", replace_after_unlink)

    assert repository.is_available() is True
    replacement_path = next(root.glob(".availability-*.tmp"))
    assert replacement_path.read_bytes() == replacement


def test_local_repository_atomically_stores_and_reopens_a_publication_pair(
    tmp_path, minimal_docx, minimal_pdf
):
    repository = LocalArtifactRepository(tmp_path / "application-data/artifacts")
    docx = write("(FAKE)-Resume.docx", DOCX_MEDIA_TYPE, minimal_docx())
    pdf = write("(FAKE)-Resume.pdf", PDF_MEDIA_TYPE, minimal_pdf())

    first = repository.store_publication_pair(
        job_id="job-1",
        document_id="edoc_abcdefghijklmnopqrstuvwx",
        document_revision=3,
        docx=docx,
        pdf=pdf,
    )
    replay = repository.store_publication_pair(
        job_id="job-1",
        document_id="edoc_abcdefghijklmnopqrstuvwx",
        document_revision=3,
        docx=docx,
        pdf=pdf,
    )

    assert replay == first
    assert first[0].canonical_path.parent == first[1].canonical_path.parent
    assert (
        repository.read(
            path=first[0].canonical_path,
            media_type=DOCX_MEDIA_TYPE,
            expected_sha256=docx.sha256,
        )
        == docx.content
    )
    assert (
        repository.read(
            path=first[1].canonical_path,
            media_type=PDF_MEDIA_TYPE,
            expected_sha256=pdf.sha256,
        )
        == pdf.content
    )
    assert all(
        stat.S_ISREG(item.canonical_path.stat().st_mode)
        and stat.S_IMODE(item.canonical_path.stat().st_mode) == 0o600
        for item in first
    )
    assert list(first[0].canonical_path.parent.parent.glob(".publication-*.tmp")) == []


def test_local_repository_rejects_bad_media_hash_paths_and_symlinks(tmp_path, minimal_docx):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    good = write("source.docx", DOCX_MEDIA_TYPE, minimal_docx())
    stored = repository.store_import(
        job_id="job-1",
        document_id="edoc_abcdefghijklmnopqrstuvwx",
        artifact=good,
    )

    with pytest.raises(ArtifactRepositoryError, match="PDF header"):
        repository.store_import(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            artifact=write("wrong.pdf", PDF_MEDIA_TYPE, b"PK\x03\x04not-pdf"),
        )
    with pytest.raises(ArtifactRepositoryError, match="PDF header"):
        repository.store_publication_pair(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            document_revision=1,
            docx=good,
            pdf=write("pair.pdf", PDF_MEDIA_TYPE, b"PK\x03\x04not-pdf"),
        )
    assert not (repository.root / "publications").exists()
    with pytest.raises(ArtifactRepositoryError, match="plain filename"):
        repository.store_import(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            artifact=write("../escape.docx", DOCX_MEDIA_TYPE, minimal_docx("escape")),
        )
    oversized = b"PK" + b"x" * 20_000_000
    with pytest.raises(ArtifactRepositoryError, match="size"):
        repository.store_import(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            artifact=write("oversized.docx", DOCX_MEDIA_TYPE, oversized),
        )
    with pytest.raises(ArtifactStorageError, match="integrity"):
        repository.read(
            path=stored.canonical_path,
            media_type=DOCX_MEDIA_TYPE,
            expected_sha256="0" * 64,
        )
    outside = tmp_path / "outside.docx"
    outside.write_bytes(good.content)
    with pytest.raises(ArtifactRepositoryError, match="outside configured roots"):
        repository.read(
            path=outside,
            media_type=DOCX_MEDIA_TYPE,
            expected_sha256=good.sha256,
        )

    stored.canonical_path.unlink()
    stored.canonical_path.symlink_to(outside)
    with pytest.raises(ArtifactRepositoryError, match="symbolic link"):
        repository.read(
            path=stored.canonical_path,
            media_type=DOCX_MEDIA_TYPE,
            expected_sha256=good.sha256,
        )


@pytest.mark.parametrize(
    "content, message",
    [
        (b"PK\x03\x04not-a-zip", "valid DOCX"),
        (b"not-a-zip", "valid DOCX"),
    ],
)
def test_local_repository_rejects_malformed_docx(tmp_path, content, message):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    with pytest.raises(ArtifactRepositoryError, match=message):
        repository.store_import(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            artifact=write("malformed.docx", DOCX_MEDIA_TYPE, content),
        )


def test_docx_validation_rejects_missing_traversal_and_excessive_expansion(tmp_path):
    repository = LocalArtifactRepository(tmp_path / "artifacts")

    def archive(entries: list[tuple[str, bytes]]) -> bytes:
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as value:
            for name, content in entries:
                value.writestr(name, content)
        return output.getvalue()

    content_types = (
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    )
    document = (
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
        b'wordprocessingml/2006/main"/>'
    )
    invalid = [
        archive([("[Content_Types].xml", content_types)]),
        archive(
            [
                ("[Content_Types].xml", content_types),
                ("word/document.xml", document),
                ("../private.txt", b"outside"),
            ]
        ),
        archive(
            [
                ("[Content_Types].xml", content_types),
                ("word/document.xml", document),
                ("word/highly-compressed.bin", b"x" * 2_000_000),
            ]
        ),
    ]

    for index, content in enumerate(invalid):
        with pytest.raises(ArtifactRepositoryError):
            repository.store_import(
                job_id="job-1",
                document_id="edoc_abcdefghijklmnopqrstuvwx",
                artifact=write(f"invalid-{index}.docx", DOCX_MEDIA_TYPE, content),
            )


def test_docx_validation_requires_a_coherent_word_ooxml_package(tmp_path, minimal_docx):
    repository = LocalArtifactRepository(tmp_path / "artifacts")

    def rewrite(name: str, replacement: bytes | None) -> bytes:
        source = BytesIO(minimal_docx("(FAKE) valid Word package"))
        output = BytesIO()
        with ZipFile(source) as original, ZipFile(
            output, "w", compression=ZIP_DEFLATED
        ) as changed:
            for entry in original.infolist():
                if entry.filename == name:
                    if replacement is not None:
                        changed.writestr(name, replacement)
                else:
                    changed.writestr(entry, original.read(entry.filename))
        return output.getvalue()

    invalid_packages = [
        rewrite("_rels/.rels", None),
        rewrite(
            "_rels/.rels",
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            b'officeDocument/2006/relationships/officeDocument" Target="../outside.xml"/>'
            b"</Relationships>",
        ),
        rewrite(
            "[Content_Types].xml",
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Override PartName="/word/document.xml" ContentType="application/xml"/>'
            b"</Types>",
        ),
        rewrite("word/document.xml", b"<w:document>"),
        rewrite(
            "word/document.xml",
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
            b'wordprocessingml/2006/main"/>',
        ),
        rewrite(
            "word/document.xml",
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
            b'wordprocessingml/2006/main"><w:body/><w:body/></w:document>',
        ),
    ]
    for index, content in enumerate(invalid_packages):
        with pytest.raises(ArtifactValidationError):
            repository.store_import(
                job_id="job-1",
                document_id="edoc_abcdefghijklmnopqrstuvwx",
                artifact=write(f"invalid-ooxml-{index}.docx", DOCX_MEDIA_TYPE, content),
            )


def test_docx_validation_retains_the_existing_fake_word_fixture(tmp_path):
    fixture = (
        Path(__file__).parents[3]
        / "packages/docx-engine/tests/fixtures/(FAKE)-polished-resume.docx"
    )
    repository = LocalArtifactRepository(tmp_path / "artifacts")

    stored = repository.store_import(
        job_id="job-1",
        document_id="edoc_abcdefghijklmnopqrstuvwx",
        artifact=write(fixture.name, DOCX_MEDIA_TYPE, fixture.read_bytes()),
    )

    assert stored.size == fixture.stat().st_size


@pytest.mark.parametrize(
    "content",
    [
        b"prefix%PDF-1.7\nnot a pdf",
        b"%PDF-1.x\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n",
        b"%PDF-1.7\narbitrary bytes\n%%EOF\n",
        b"%PDF-1.7\nstartxref\n999999\n%%EOF\n",
    ],
)
def test_pdf_validation_rejects_prefixes_and_unparseable_structure(tmp_path, content):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    with pytest.raises(ArtifactValidationError):
        repository.store_import(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            artifact=write("invalid.pdf", PDF_MEDIA_TYPE, content),
        )


def test_pdf_validation_rejects_cross_reference_streams(tmp_path):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    content = bytearray(b"%PDF-1.7\n")
    content.extend(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    xref_offset = len(content)
    content.extend(
        b"3 0 obj\n"
        b"<< /Type /XRef /Size 4 /Root 1 0 R /W [1 4 2] /Index [0 4] /Length 1 >>\n"
        b"stream\nX\nendstream\nendobj\n"
    )
    content.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))

    with pytest.raises(ArtifactValidationError, match="streams are not supported"):
        repository.store_import(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            artifact=write("xref-stream.pdf", PDF_MEDIA_TYPE, bytes(content)),
        )


def test_caller_filename_limit_reserves_the_hash_prefix(tmp_path, minimal_docx):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    accepted = "a" * (MAX_CALLER_FILENAME_BYTES - len(".docx")) + ".docx"
    rejected = "a" * (MAX_CALLER_FILENAME_BYTES - len(".docx") + 1) + ".docx"

    stored = repository.store_import(
        job_id="job-1",
        document_id="edoc_abcdefghijklmnopqrstuvwx",
        artifact=write(accepted, DOCX_MEDIA_TYPE, minimal_docx("(FAKE) boundary")),
    )
    assert len(stored.canonical_path.name.encode("utf-8")) == 255
    with pytest.raises(ArtifactValidationError, match="plain filename"):
        repository.store_import(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            artifact=write(rejected, DOCX_MEDIA_TYPE, minimal_docx("(FAKE) too long")),
        )


def test_repository_fsyncs_import_creation_and_publication_rename(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch
):
    fsync_calls: list[tuple[int, int]] = []
    rename_calls: list[tuple[int | None, int | None]] = []
    real_fsync = os.fsync
    real_rename = os.rename

    def record_fsync(descriptor):
        metadata = os.fstat(descriptor)
        fsync_calls.append((metadata.st_dev, metadata.st_ino))
        real_fsync(descriptor)

    def record_rename(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        rename_calls.append((src_dir_fd, dst_dir_fd))
        return real_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(local_repository_module.os, "fsync", record_fsync)
    monkeypatch.setattr(local_repository_module.os, "rename", record_rename)
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    imported = write("source.docx", DOCX_MEDIA_TYPE, minimal_docx("import"))
    repository.store_import(
        job_id="job-1",
        document_id="edoc_abcdefghijklmnopqrstuvwx",
        artifact=imported,
    )
    pair = repository.store_publication_pair(
        job_id="job-1",
        document_id="edoc_abcdefghijklmnopqrstuvwx",
        document_revision=1,
        docx=write("resume.docx", DOCX_MEDIA_TYPE, minimal_docx("publication")),
        pdf=write("resume.pdf", PDF_MEDIA_TYPE, minimal_pdf()),
    )

    assert len(fsync_calls) >= 10
    assert rename_calls and all(
        source is not None and destination is not None for source, destination in rename_calls
    )
    assert pair[0].canonical_path.is_file()
    assert pair[1].canonical_path.is_file()


def test_repository_surfaces_parent_fsync_failure_after_successful_publication_rename(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch
):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    real_fsync = os.fsync
    real_rename = os.rename
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

    with pytest.raises(ArtifactStorageError, match="synchronize published"):
        repository.store_publication_pair(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            document_revision=1,
            docx=write("resume.docx", DOCX_MEDIA_TYPE, minimal_docx("publication")),
            pdf=write("resume.pdf", PDF_MEDIA_TYPE, minimal_pdf()),
        )

    assert rename_succeeded
    destinations = list(
        (repository.root / "publications/job-1/edoc_abcdefghijklmnopqrstuvwx").glob(
            "revision-*"
        )
    )
    assert len(destinations) == 1

    replay_parent = destinations[0].parent
    replay_parent_identity = (replay_parent.stat().st_dev, replay_parent.stat().st_ino)
    replay_parent_synced = False

    def record_replay_fsync(descriptor):
        nonlocal replay_parent_synced
        identity = os.fstat(descriptor)
        if (identity.st_dev, identity.st_ino) == replay_parent_identity:
            replay_parent_synced = True
        real_fsync(descriptor)

    monkeypatch.setattr(local_repository_module.os, "fsync", record_replay_fsync)
    replay = repository.store_publication_pair(
        job_id="job-1",
        document_id="edoc_abcdefghijklmnopqrstuvwx",
        document_revision=1,
        docx=write("resume.docx", DOCX_MEDIA_TYPE, minimal_docx("publication")),
        pdf=write("resume.pdf", PDF_MEDIA_TYPE, minimal_pdf()),
    )

    assert replay_parent_synced
    assert replay[0].canonical_path.parent == destinations[0]


def test_publication_replay_fsyncs_retained_parent_and_surfaces_failure(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch
):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    document_id = "edoc_abcdefghijklmnopqrstuvwx"
    docx = write("resume.docx", DOCX_MEDIA_TYPE, minimal_docx("publication"))
    pdf = write("resume.pdf", PDF_MEDIA_TYPE, minimal_pdf())
    expected = repository.store_publication_pair(
        job_id="job-1",
        document_id=document_id,
        document_revision=1,
        docx=docx,
        pdf=pdf,
    )
    parent = repository.root / "publications" / "job-1" / document_id
    parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
    real_fsync = os.fsync
    real_rename = os.rename
    rename_failed = False
    fsync_failed = False

    def mark_failed_rename(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        nonlocal rename_failed
        try:
            return real_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
        except OSError:
            rename_failed = True
            raise

    def fail_replay_parent_fsync(descriptor):
        nonlocal fsync_failed
        identity = os.fstat(descriptor)
        if (
            rename_failed
            and not fsync_failed
            and (identity.st_dev, identity.st_ino) == parent_identity
        ):
            fsync_failed = True
            raise OSError("synthetic replay parent fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(local_repository_module.os, "rename", mark_failed_rename)
    monkeypatch.setattr(local_repository_module.os, "fsync", fail_replay_parent_fsync)

    with pytest.raises(ArtifactStorageError, match="synchronize published"):
        repository.store_publication_pair(
            job_id="job-1",
            document_id=document_id,
            document_revision=1,
            docx=docx,
            pdf=pdf,
        )

    assert rename_failed
    assert fsync_failed
    assert all(item.canonical_path.is_file() for item in expected)


def test_import_cleanup_is_descriptor_relative_and_identity_bound_after_late_verify_swap(
    tmp_path, minimal_docx, monkeypatch
):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    document_id = "edoc_abcdefghijklmnopqrstuvwx"
    artifact = write("source.docx", DOCX_MEDIA_TYPE, minimal_docx("private import"))
    stored_name = f"{artifact.sha256[:20]}-{artifact.filename}"
    directory = repository.root / "imports" / "job-1" / document_id
    held = tmp_path / "held-import"
    foreign = b"foreign replacement"
    real_read = local_repository_module.read_open_file
    swapped = False

    def move_after_verify_read(descriptor, metadata, *, maximum):
        nonlocal swapped
        content = real_read(descriptor, metadata, maximum=maximum)
        if not swapped:
            directory.rename(held)
            directory.mkdir()
            (directory / stored_name).write_bytes(foreign)
            swapped = True
        return content

    monkeypatch.setattr(local_repository_module, "read_open_file", move_after_verify_read)

    with pytest.raises(ArtifactStorageError, match="directory changed during storage"):
        repository.store_import(
            job_id="job-1",
            document_id=document_id,
            artifact=artifact,
        )

    assert swapped
    assert list(held.iterdir()) == []
    assert (directory / stored_name).read_bytes() == foreign


@pytest.mark.parametrize("operation", ["import", "pair"])
def test_post_open_file_replacement_never_succeeds_or_deletes_foreign_file(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch, operation
):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    document_id = "edoc_abcdefghijklmnopqrstuvwx"
    docx = write("resume.docx", DOCX_MEDIA_TYPE, minimal_docx("opened artifact"))
    stored_name = f"{docx.sha256[:20]}-{docx.filename}"
    foreign = b"foreign replacement"
    held = tmp_path / f"held-{operation}.docx"
    real_read = local_repository_module.read_open_file
    replaced_path: Path | None = None

    def replace_after_open_read(descriptor, metadata, *, maximum):
        nonlocal replaced_path
        content = real_read(descriptor, metadata, maximum=maximum)
        if replaced_path is None:
            if operation == "import":
                target = (
                    repository.root
                    / "imports"
                    / "job-1"
                    / document_id
                    / stored_name
                )
            else:
                targets = list(
                    repository.root.glob(
                        f"publications/job-1/{document_id}/.publication-*.tmp/{stored_name}"
                    )
                )
                assert len(targets) == 1
                target = targets[0]
            target.rename(held)
            target.write_bytes(foreign)
            replaced_path = target
        return content

    monkeypatch.setattr(
        local_repository_module, "read_open_file", replace_after_open_read
    )

    with pytest.raises(ArtifactStorageError):
        if operation == "import":
            repository.store_import(
                job_id="job-1", document_id=document_id, artifact=docx
            )
        else:
            repository.store_publication_pair(
                job_id="job-1",
                document_id=document_id,
                document_revision=1,
                docx=docx,
                pdf=write("resume.pdf", PDF_MEDIA_TYPE, minimal_pdf()),
            )

    assert replaced_path is not None
    assert replaced_path.read_bytes() == foreign
    assert held.read_bytes() == docx.content


def test_publication_cleanup_removes_owned_renamed_pair_after_late_parent_swap(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch
):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    document_id = "edoc_abcdefghijklmnopqrstuvwx"
    docx = write("resume.docx", DOCX_MEDIA_TYPE, minimal_docx("private publication"))
    pdf = write("resume.pdf", PDF_MEDIA_TYPE, minimal_pdf())
    parent = repository.root / "publications" / "job-1" / document_id
    held = tmp_path / "held-publication-parent"
    foreign = b"foreign replacement"
    real_rename = os.rename
    real_read = local_repository_module.read_open_file
    destination_name: str | None = None
    swapped = False

    def mark_publication_rename(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        nonlocal destination_name
        result = real_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if src_dir_fd is not None and dst_dir_fd is not None:
            destination_name = destination
        return result

    def move_parent_after_published_read(descriptor, metadata, *, maximum):
        nonlocal swapped
        content = real_read(descriptor, metadata, maximum=maximum)
        if destination_name is not None and not swapped:
            parent.rename(held)
            replacement = parent / destination_name
            replacement.mkdir(parents=True)
            (replacement / f"{docx.sha256[:20]}-{docx.filename}").write_bytes(foreign)
            swapped = True
        return content

    monkeypatch.setattr(local_repository_module.os, "rename", mark_publication_rename)
    monkeypatch.setattr(
        local_repository_module,
        "read_open_file",
        move_parent_after_published_read,
    )

    with pytest.raises(ArtifactStorageError, match="directory changed during storage"):
        repository.store_publication_pair(
            job_id="job-1",
            document_id=document_id,
            document_revision=1,
            docx=docx,
            pdf=pdf,
        )

    assert swapped
    assert not any(path.is_file() for path in held.rglob("*"))
    replacement = parent / destination_name
    assert (replacement / f"{docx.sha256[:20]}-{docx.filename}").read_bytes() == foreign


def test_repository_initialization_never_deletes_another_process_publication_temp(tmp_path):
    root = tmp_path / "artifacts"
    parent = root / "publications/job-1/edoc_abcdefghijklmnopqrstuvwx"
    orphan = parent / ".publication-0123456789abcdef0123456789abcdef.tmp"
    orphan.mkdir(parents=True)
    (orphan / "partial.docx").write_bytes(b"partial")
    outside = tmp_path / "outside"
    outside.mkdir()
    private = outside / "private.txt"
    private.write_bytes(b"private")
    linked = parent / ".publication-fedcba9876543210fedcba9876543210.tmp"
    linked.symlink_to(outside, target_is_directory=True)

    LocalArtifactRepository(root)

    assert orphan.is_dir()
    assert (orphan / "partial.docx").read_bytes() == b"partial"
    assert linked.is_symlink()
    assert private.read_bytes() == b"private"


def test_repository_construction_during_active_publication_cannot_delete_it(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch
):
    root = tmp_path / "artifacts"
    publisher = LocalArtifactRepository(root)
    entered = Event()
    release = Event()
    real_store = publisher._store_in

    def pause_first_store(directory, artifact):
        if directory.path.name.startswith(".publication-") and not entered.is_set():
            entered.set()
            assert release.wait(timeout=5)
        return real_store(directory, artifact)

    monkeypatch.setattr(publisher, "_store_in", pause_first_store)
    outcome: list[object] = []

    def publish() -> None:
        try:
            outcome.append(
                publisher.store_publication_pair(
                    job_id="job-1",
                    document_id="edoc_abcdefghijklmnopqrstuvwx",
                    document_revision=1,
                    docx=write("resume.docx", DOCX_MEDIA_TYPE, minimal_docx()),
                    pdf=write("resume.pdf", PDF_MEDIA_TYPE, minimal_pdf()),
                )
            )
        except BaseException as error:  # surfaced by the assertion below
            outcome.append(error)

    thread = Thread(target=publish)
    thread.start()
    assert entered.wait(timeout=5)
    active = list(root.glob("publications/*/*/.publication-*.tmp"))
    assert len(active) == 1

    LocalArtifactRepository(root)

    assert active[0].is_dir()
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], tuple)


def test_publication_write_does_not_follow_a_renamed_temp_path_replaced_by_symlink(
    tmp_path, minimal_docx, minimal_pdf, monkeypatch
):
    repository = LocalArtifactRepository(tmp_path / "artifacts")
    outside = tmp_path / "outside"
    outside.mkdir()
    private = outside / "private.txt"
    private.write_bytes(b"private")
    held = tmp_path / "held-publication-temp"
    real_store = repository._store_in
    swapped = False

    def attempt_after_swap(directory, artifact):
        nonlocal swapped
        if not swapped and directory.path.name.startswith(".publication-"):
            swapped = True
            directory.path.rename(held)
            directory.path.symlink_to(outside, target_is_directory=True)
        return real_store(directory, artifact)

    monkeypatch.setattr(repository, "_store_in", attempt_after_swap)
    with pytest.raises(ArtifactStorageError, match="directory changed during storage"):
        repository.store_publication_pair(
            job_id="job-1",
            document_id="edoc_abcdefghijklmnopqrstuvwx",
            document_revision=1,
            docx=write("resume.docx", DOCX_MEDIA_TYPE, minimal_docx()),
            pdf=write("resume.pdf", PDF_MEDIA_TYPE, minimal_pdf()),
        )

    assert swapped
    assert private.read_bytes() == b"private"
    assert held.is_dir()
    assert list(held.iterdir()) == []
    assert list(outside.iterdir()) == [private]


def test_local_repository_rejects_a_symlink_root(tmp_path):
    target = tmp_path / "real-artifacts"
    target.mkdir()
    link = tmp_path / "linked-artifacts"
    os.symlink(target, link)

    with pytest.raises(ArtifactRepositoryError, match="root must not be a symbolic link"):
        LocalArtifactRepository(link)


def test_descriptor_read_does_not_follow_a_post_open_file_swap(
    tmp_path, minimal_docx, monkeypatch
):
    root = tmp_path / "artifacts"
    root.mkdir()
    original = minimal_docx("inside")
    outside_bytes = minimal_docx("outside private bytes")
    target = root / "resume.docx"
    target.write_bytes(original)
    outside = tmp_path / "outside.docx"
    outside.write_bytes(outside_bytes)
    held = root / "held.docx"
    real_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == target.name and dir_fd is not None and not swapped:
            swapped = True
            target.rename(held)
            target.symlink_to(outside)
        return descriptor

    monkeypatch.setattr(artifact_repository_module.os, "open", racing_open)
    _, content = verify_artifact_file(
        target,
        roots=(root,),
        media_type=DOCX_MEDIA_TYPE,
        expected_sha256=sha256(original).hexdigest(),
    )

    assert swapped
    assert content == original
    assert content != outside_bytes


def test_descriptor_read_does_not_follow_a_post_open_ancestor_swap(
    tmp_path, minimal_docx, monkeypatch
):
    root = tmp_path / "artifacts"
    nested = root / "job-1"
    nested.mkdir(parents=True)
    original = minimal_docx("inside ancestor")
    target = nested / "resume.docx"
    target.write_bytes(original)
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    outside_bytes = minimal_docx("outside ancestor bytes")
    (outside_directory / target.name).write_bytes(outside_bytes)
    held = root / "held-job-1"
    real_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == nested.name and dir_fd is not None and not swapped:
            swapped = True
            nested.rename(held)
            nested.symlink_to(outside_directory, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(artifact_repository_module.os, "open", racing_open)
    _, content = verify_artifact_file(
        target,
        roots=(root,),
        media_type=DOCX_MEDIA_TYPE,
        expected_sha256=sha256(original).hexdigest(),
    )

    assert swapped
    assert content == original
    assert content != outside_bytes

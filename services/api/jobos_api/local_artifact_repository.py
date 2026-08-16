from __future__ import annotations

import errno
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from jobos_api.artifact_repository import (
    DOCX_MEDIA_TYPE,
    MAX_ARTIFACT_BYTES,
    MAX_STORED_FILENAME_BYTES,
    PDF_MEDIA_TYPE,
    ArtifactStorageError,
    ArtifactValidationError,
    ArtifactWrite,
    StoredArtifact,
    fsync_directory,
    read_open_file,
    resolve_repository_root,
    validate_artifact_bytes,
    validate_plain_filename,
    validate_safe_segment,
    verify_artifact_file,
    write_new_file_at,
)


@dataclass(slots=True)
class _OpenedDirectory:
    path: Path
    descriptors: list[int]
    segments: tuple[str, ...]

    @property
    def descriptor(self) -> int:
        return self.descriptors[-1]


class LocalArtifactRepository:
    """Content-addressed bytes below one explicitly configured application-data root."""

    def __init__(self, root: Path) -> None:
        supplied = root.expanduser()
        if supplied.is_symlink():
            raise ArtifactStorageError("Artifact repository root must not be a symbolic link")
        self._root = supplied.resolve(strict=False)
        self._ensure_root()

    @property
    def root(self) -> Path:
        return self._root

    def _ensure_root(self) -> None:
        self._root = resolve_repository_root(self._root, create=True)
        fsync_directory(self._root.parent)

    @staticmethod
    def _directory_flags() -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return flags

    @classmethod
    def _open_directory(cls, name: str | Path, *, dir_fd: int | None = None) -> int:
        return os.open(name, cls._directory_flags(), dir_fd=dir_fd)

    @staticmethod
    def _same_directory(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev == second.st_dev
            and first.st_ino == second.st_ino
            and stat.S_ISDIR(first.st_mode)
            and stat.S_ISDIR(second.st_mode)
        )

    @staticmethod
    def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev == second.st_dev
            and first.st_ino == second.st_ino
            and stat.S_IFMT(first.st_mode) == stat.S_IFMT(second.st_mode)
        )

    @staticmethod
    def _close_descriptors(descriptors: list[int]) -> None:
        for descriptor in reversed(descriptors):
            os.close(descriptor)

    def _assert_attached(self, directory: _OpenedDirectory) -> None:
        """Require every retained descriptor to remain at its repository-relative name."""
        try:
            named_root = os.stat(self._root, follow_symlinks=False)
            opened_root = os.fstat(directory.descriptors[0])
            if not self._same_directory(named_root, opened_root):
                raise ArtifactStorageError("Configured artifact root changed during storage")
            for index, segment in enumerate(directory.segments):
                named = os.stat(
                    segment,
                    dir_fd=directory.descriptors[index],
                    follow_symlinks=False,
                )
                opened = os.fstat(directory.descriptors[index + 1])
                if not self._same_directory(named, opened):
                    raise ArtifactStorageError(
                        "Artifact repository directory changed during storage"
                    )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
                raise ArtifactStorageError(
                    "Artifact repository directory changed during storage"
                ) from error
            raise

    def _open_directory_chain(self, *segments: str) -> _OpenedDirectory:
        self._ensure_root()
        safe_segments = tuple(
            validate_safe_segment(segment, "Artifact path segment") for segment in segments
        )
        expected_root = os.stat(self._root, follow_symlinks=False)
        descriptors: list[int] = []
        try:
            root_descriptor = self._open_directory(self._root)
            descriptors.append(root_descriptor)
            if not self._same_directory(expected_root, os.fstat(root_descriptor)):
                raise ArtifactStorageError("Configured artifact root changed during storage")
            current = root_descriptor
            for segment in safe_segments:
                created = False
                try:
                    os.mkdir(segment, mode=0o700, dir_fd=current)
                    created = True
                except FileExistsError:
                    pass
                if created:
                    os.fsync(current)
                descriptor = self._open_directory(segment, dir_fd=current)
                if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    os.close(descriptor)
                    raise ArtifactStorageError("Artifact repository ancestor is not a directory")
                descriptors.append(descriptor)
                current = descriptor
            opened = _OpenedDirectory(
                path=self._root.joinpath(*safe_segments),
                descriptors=descriptors,
                segments=safe_segments,
            )
            self._assert_attached(opened)
            return opened
        except OSError as error:
            self._close_descriptors(descriptors)
            if error.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
                raise ArtifactStorageError(
                    "Artifact repository path contains a symbolic link or missing component"
                ) from error
            raise
        except BaseException:
            self._close_descriptors(descriptors)
            raise

    def _directory(self, *segments: str) -> Path:
        opened = self._open_directory_chain(*segments)
        try:
            return opened.path
        finally:
            self._close_descriptors(opened.descriptors)

    @classmethod
    def _clean_owned_temporary_directory(
        cls,
        parent_descriptor: int,
        name: str,
        descriptor: int,
        identity: os.stat_result,
        owned_files: dict[str, os.stat_result],
    ) -> None:
        """Clean only files and a directory owned by this publication call."""
        for child, child_identity in owned_files.items():
            try:
                metadata = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if cls._same_identity(child_identity, metadata):
                os.unlink(child, dir_fd=descriptor)
        os.fsync(descriptor)
        try:
            named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not cls._same_directory(identity, named):
            return
        try:
            os.rmdir(name, dir_fd=parent_descriptor)
        except OSError as error:
            if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                raise
        os.fsync(parent_descriptor)

    @classmethod
    def _clean_owned_file(
        cls,
        directory_descriptor: int,
        filename: str,
        identity: os.stat_result,
    ) -> None:
        try:
            named = os.stat(
                filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        if cls._same_identity(identity, named):
            os.unlink(filename, dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)

    @staticmethod
    def _validated_write(artifact: ArtifactWrite) -> ArtifactWrite:
        validate_plain_filename(artifact.filename, artifact.media_type)
        validate_artifact_bytes(
            artifact.content,
            media_type=artifact.media_type,
            expected_sha256=artifact.sha256,
        )
        return artifact

    @staticmethod
    def _stored(path: Path, artifact: ArtifactWrite) -> StoredArtifact:
        return StoredArtifact(
            canonical_path=path,
            filename=artifact.filename,
            media_type=artifact.media_type,
            sha256=artifact.sha256,
            size=len(artifact.content),
        )

    def _verify_in(self, directory: _OpenedDirectory, artifact: ArtifactWrite) -> StoredArtifact:
        stored_name = f"{artifact.sha256[:20]}-{artifact.filename}"
        validate_plain_filename(
            stored_name,
            artifact.media_type,
            maximum_bytes=MAX_STORED_FILENAME_BYTES,
        )
        self._assert_attached(directory)
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(stored_name, flags, dir_fd=directory.descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactStorageError("Stored artifact is not a regular file")
            content = read_open_file(descriptor, metadata, maximum=MAX_ARTIFACT_BYTES)
            try:
                validate_artifact_bytes(
                    content,
                    media_type=artifact.media_type,
                    expected_sha256=artifact.sha256,
                )
            except ArtifactValidationError as error:
                raise ArtifactStorageError(
                    "Stored artifact failed integrity validation"
                ) from error
            if content != artifact.content:
                raise ArtifactStorageError("Stored artifact content does not match")
            named = os.stat(
                stored_name,
                dir_fd=directory.descriptor,
                follow_symlinks=False,
            )
            if not self._same_identity(os.fstat(descriptor), named):
                raise ArtifactStorageError("Stored artifact changed during storage")
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
                raise ArtifactStorageError(
                    "Stored artifact path contains a symbolic link or missing component"
                ) from error
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._assert_attached(directory)
        return self._stored(directory.path / stored_name, artifact)

    def _store_in(
        self,
        directory: _OpenedDirectory,
        artifact: ArtifactWrite,
    ) -> tuple[StoredArtifact, os.stat_result | None]:
        artifact = self._validated_write(artifact)
        stored_name = f"{artifact.sha256[:20]}-{artifact.filename}"
        self._assert_attached(directory)
        created_identity: os.stat_result | None = None
        with suppress(FileExistsError):
            created_identity = write_new_file_at(
                directory.descriptor, stored_name, artifact.content
            )
        try:
            # A prior process may have created this content-addressed entry but died
            # before syncing the directory. Re-sync on both creation and replay.
            os.fsync(directory.descriptor)
            self._assert_attached(directory)
            stored = self._verify_in(directory, artifact)
            return stored, created_identity
        except BaseException:
            if created_identity is not None:
                self._clean_owned_file(
                    directory.descriptor,
                    stored_name,
                    created_identity,
                )
            raise

    def store_import(
        self, *, job_id: str, document_id: str, artifact: ArtifactWrite
    ) -> StoredArtifact:
        directory = self._open_directory_chain("imports", job_id, document_id)
        try:
            stored, _ = self._store_in(directory, artifact)
            return stored
        finally:
            self._close_descriptors(directory.descriptors)

    def store_publication_pair(
        self,
        *,
        job_id: str,
        document_id: str,
        document_revision: int,
        docx: ArtifactWrite,
        pdf: ArtifactWrite,
    ) -> tuple[StoredArtifact, StoredArtifact]:
        if document_revision < 1:
            raise ArtifactValidationError("Document revision must be positive")
        docx = self._validated_write(docx)
        pdf = self._validated_write(pdf)
        if docx.media_type != DOCX_MEDIA_TYPE or pdf.media_type != PDF_MEDIA_TYPE:
            raise ArtifactValidationError("Publication requires one DOCX and one PDF")

        parent = self._open_directory_chain("publications", job_id, document_id)
        pair_digest = sha256(
            f"{document_revision}\0{docx.sha256}\0{pdf.sha256}".encode("ascii")
        ).hexdigest()
        destination_name = f"revision-{document_revision}-{pair_digest[:20]}"
        destination_path = parent.path / destination_name
        temporary_name = f".publication-{uuid4().hex}.tmp"
        temporary_descriptor: int | None = None
        temporary_identity: os.stat_result | None = None
        published_descriptor: int | None = None
        owned_files: dict[str, os.stat_result] = {}
        renamed = False
        completed = False
        preserve_renamed_for_replay = False
        try:
            self._assert_attached(parent)
            os.mkdir(temporary_name, mode=0o700, dir_fd=parent.descriptor)
            temporary_descriptor = self._open_directory(
                temporary_name, dir_fd=parent.descriptor
            )
            temporary_identity = os.fstat(temporary_descriptor)
            os.fsync(parent.descriptor)
            temporary = _OpenedDirectory(
                path=parent.path / temporary_name,
                descriptors=[*parent.descriptors, temporary_descriptor],
                segments=(*parent.segments, temporary_name),
            )
            _, docx_identity = self._store_in(temporary, docx)
            if docx_identity is not None:
                docx_name = f"{docx.sha256[:20]}-{docx.filename}"
                owned_files[docx_name] = docx_identity
            _, pdf_identity = self._store_in(temporary, pdf)
            if pdf_identity is not None:
                pdf_name = f"{pdf.sha256[:20]}-{pdf.filename}"
                owned_files[pdf_name] = pdf_identity
            os.fsync(temporary_descriptor)
            self._assert_attached(temporary)
            try:
                os.rename(
                    temporary_name,
                    destination_name,
                    src_dir_fd=parent.descriptor,
                    dst_dir_fd=parent.descriptor,
                )
            except OSError as error:
                try:
                    published_descriptor = self._open_directory(
                        destination_name, dir_fd=parent.descriptor
                    )
                except OSError:
                    raise error from None
                try:
                    os.fsync(parent.descriptor)
                except OSError as fsync_error:
                    raise ArtifactStorageError(
                        "Could not synchronize published artifact directory"
                    ) from fsync_error
            else:
                renamed = True
                published_descriptor = temporary_descriptor
                try:
                    os.fsync(parent.descriptor)
                except OSError as error:
                    preserve_renamed_for_replay = True
                    raise ArtifactStorageError(
                        "Could not synchronize published artifact directory"
                    ) from error

            published = _OpenedDirectory(
                path=destination_path,
                descriptors=[*parent.descriptors, published_descriptor],
                segments=(*parent.segments, destination_name),
            )
            self._assert_attached(published)
            stored_docx = self._verify_in(published, docx)
            stored_pdf = self._verify_in(published, pdf)
            completed = True
            return stored_docx, stored_pdf
        finally:
            try:
                if (
                    temporary_descriptor is not None
                    and temporary_identity is not None
                    and (
                        not renamed
                        or (not completed and not preserve_renamed_for_replay)
                    )
                ):
                    self._clean_owned_temporary_directory(
                        parent.descriptor,
                        destination_name if renamed else temporary_name,
                        temporary_descriptor,
                        temporary_identity,
                        owned_files,
                    )
            finally:
                if (
                    published_descriptor is not None
                    and published_descriptor != temporary_descriptor
                ):
                    os.close(published_descriptor)
                if temporary_descriptor is not None:
                    os.close(temporary_descriptor)
                self._close_descriptors(parent.descriptors)

    def read(self, *, path: Path, media_type: str, expected_sha256: str) -> bytes:
        _, content = verify_artifact_file(
            path,
            roots=(self._root,),
            media_type=media_type,
            expected_sha256=expected_sha256,
        )
        return content

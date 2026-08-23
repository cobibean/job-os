from __future__ import annotations

import errno
import os
import re
import stat
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4
from xml.etree import ElementTree

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ALLOWED_MEDIA_TYPES = frozenset({PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE})
MAX_ARTIFACT_BYTES = 20_000_000
MAX_DOCX_ENTRIES = 2_048
MAX_DOCX_EXPANDED_BYTES = 100_000_000
MAX_DOCX_COMPRESSION_RATIO = 1_000
MAX_DOCX_REQUIRED_XML_BYTES = 5_000_000
MAX_STORED_FILENAME_BYTES = 255
STORED_HASH_PREFIX_BYTES = 21
MAX_CALLER_FILENAME_BYTES = MAX_STORED_FILENAME_BYTES - STORED_HASH_PREFIX_BYTES
MAX_PDF_XREF_ENTRIES = 200_000
MAX_PDF_TRAILER_BYTES = 65_536
_DOCX_REQUIRED_PARTS = {
    "[Content_Types].xml": "{http://schemas.openxmlformats.org/package/2006/content-types}Types",
    "_rels/.rels": "{http://schemas.openxmlformats.org/package/2006/relationships}Relationships",
    "word/document.xml": "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
}
_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_DOCUMENT_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_WORD_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_PDF_HEADER = re.compile(rb"\A%PDF-(?:1\.[0-7]|2\.0)(?:\r\n|\r|\n)")
_PDF_FINAL = re.compile(
    rb"startxref[ \t]*(?:\r\n|\r|\n)[ \t]*([0-9]{1,10})[ \t]*"
    rb"(?:\r\n|\r|\n)[ \t]*%%EOF[ \t]*(?:\r\n|\r|\n)?\Z"
)


class ArtifactRepositoryError(Exception):
    """Base error for the local artifact repository boundary."""


class ArtifactValidationError(ArtifactRepositoryError, ValueError):
    """Caller-supplied artifact metadata or bytes are malformed."""


class ArtifactStorageError(ArtifactRepositoryError, RuntimeError):
    """Configured storage or already-stored bytes are not trustworthy."""


@dataclass(frozen=True, slots=True)
class ArtifactWrite:
    filename: str
    media_type: str
    content: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    canonical_path: Path
    filename: str
    media_type: str
    sha256: str
    size: int


class ArtifactRepository(Protocol):
    """Byte-only artifact storage; metadata remains owned by JobOsStateStore."""

    @property
    def root(self) -> Path: ...

    def is_available(self) -> bool: ...

    def store_import(
        self, *, job_id: str, document_id: str, artifact: ArtifactWrite
    ) -> StoredArtifact: ...

    def store_agent_publication(
        self,
        *,
        job_id: str,
        document_key: str,
        source_revision: str,
        artifact: ArtifactWrite,
    ) -> StoredArtifact: ...

    def store_publication_pair(
        self,
        *,
        job_id: str,
        document_id: str,
        document_revision: int,
        docx: ArtifactWrite,
        pdf: ArtifactWrite,
    ) -> tuple[StoredArtifact, StoredArtifact]: ...

    def read(self, *, path: Path, media_type: str, expected_sha256: str) -> bytes: ...


def validate_safe_segment(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", value) or value in {".", ".."}:
        raise ArtifactValidationError(f"{label} is not safe for artifact storage")
    return value


def validate_plain_filename(
    filename: str,
    media_type: str,
    *,
    maximum_bytes: int = MAX_CALLER_FILENAME_BYTES,
) -> str:
    if (
        not filename
        or filename != filename.strip()
        or filename in {".", ".."}
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
        or len(filename.encode("utf-8")) > maximum_bytes
    ):
        raise ArtifactValidationError("Artifact filename must be a plain filename")
    expected_suffix = media_suffix(media_type)
    if not filename.casefold().endswith(expected_suffix):
        raise ArtifactValidationError("Artifact filename does not match its media type")
    return filename


def media_suffix(media_type: str) -> str:
    if media_type == PDF_MEDIA_TYPE:
        return ".pdf"
    if media_type == DOCX_MEDIA_TYPE:
        return ".docx"
    raise ArtifactValidationError("Artifact media type is not allowlisted")


def validate_artifact_bytes(
    content: bytes,
    *,
    media_type: str,
    expected_sha256: str | None = None,
    maximum: int = MAX_ARTIFACT_BYTES,
) -> str:
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ArtifactValidationError("Artifact media type is not allowlisted")
    if not content or len(content) > maximum:
        raise ArtifactValidationError("Artifact size is invalid")
    if media_type == PDF_MEDIA_TYPE:
        _validate_pdf(content)
    else:
        _validate_docx(content)
    digest = sha256(content).hexdigest()
    if expected_sha256 is not None and (
        not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or digest != expected_sha256
    ):
        raise ArtifactValidationError("Artifact SHA-256 does not match")
    return digest


def _pdf_dictionary(content: bytes, start: int) -> tuple[bytes, int]:
    start = content.find(b"<<", start, min(len(content), start + MAX_PDF_TRAILER_BYTES))
    if start < 0:
        raise ArtifactValidationError("PDF trailer dictionary is missing")
    depth = 0
    position = start
    in_literal = False
    escaped = False
    while position < len(content) and position - start <= MAX_PDF_TRAILER_BYTES:
        byte = content[position]
        if in_literal:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x29:
                in_literal = False
            position += 1
            continue
        if byte == 0x25:
            newline = content.find(b"\n", position + 1)
            position = len(content) if newline < 0 else newline + 1
            continue
        if byte == 0x28:
            in_literal = True
            position += 1
            continue
        if content[position : position + 2] == b"<<":
            depth += 1
            position += 2
            continue
        if content[position : position + 2] == b">>":
            depth -= 1
            position += 2
            if depth == 0:
                return content[start:position], position
            continue
        position += 1
    raise ArtifactValidationError("PDF trailer dictionary is malformed or excessive")


def _pdf_reference(dictionary: bytes, name: bytes) -> tuple[int, int] | None:
    match = re.search(rb"/" + name + rb"\s+([0-9]+)\s+([0-9]+)\s+R\b", dictionary)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _validate_pdf(content: bytes) -> None:
    if _PDF_HEADER.match(content) is None:
        raise ArtifactValidationError("Artifact bytes do not contain a valid PDF header")
    final_window_start = max(0, len(content) - MAX_PDF_TRAILER_BYTES)
    final = _PDF_FINAL.search(content, final_window_start)
    if final is None:
        raise ArtifactValidationError("PDF startxref or final EOF marker is invalid")
    xref_offset = int(final.group(1))
    if xref_offset <= 0 or xref_offset >= final.start():
        raise ArtifactValidationError("PDF startxref offset is out of bounds")

    root_reference: tuple[int, int] | None = None
    xref_entries: dict[tuple[int, int], int] = {}
    if content[xref_offset : xref_offset + 4] != b"xref":
        raise ArtifactValidationError("PDF cross-reference streams are not supported")
    position = xref_offset + 4
    entry_count = 0
    while True:
        whitespace = re.match(rb"[\x00\x09\x0a\x0c\x0d\x20]*", content[position:])
        assert whitespace is not None
        position += whitespace.end()
        if content[position : position + 7] == b"trailer":
            position += 7
            break
        subsection = re.match(rb"([0-9]+)[ \t]+([0-9]+)(?:\r\n|\r|\n)", content[position:])
        if subsection is None:
            raise ArtifactValidationError("PDF cross-reference table is malformed")
        first_object = int(subsection.group(1))
        count = int(subsection.group(2))
        entry_count += count
        if count < 1 or entry_count > MAX_PDF_XREF_ENTRIES:
            raise ArtifactValidationError("PDF cross-reference table is excessive")
        position += subsection.end()
        for index in range(count):
            entry = re.match(
                rb"([0-9]{10})[ \t]([0-9]{5})[ \t]([nf])(?:[ \t]*(?:\r\n|\r|\n))",
                content[position:],
            )
            if entry is None:
                raise ArtifactValidationError("PDF cross-reference entry is malformed")
            if entry.group(3) == b"n":
                offset = int(entry.group(1))
                generation = int(entry.group(2))
                if offset <= 0 or offset >= xref_offset:
                    raise ArtifactValidationError("PDF object offset is out of bounds")
                xref_entries[(first_object + index, generation)] = offset
            position += entry.end()
    trailer, _ = _pdf_dictionary(content, position)
    if re.search(rb"/XRefStm\b", trailer) is not None:
        raise ArtifactValidationError("PDF cross-reference streams are not supported")
    size = re.search(rb"/Size\s+([0-9]+)\b", trailer)
    root_reference = _pdf_reference(trailer, b"Root")
    if size is None or not 1 <= int(size.group(1)) <= MAX_PDF_XREF_ENTRIES:
        raise ArtifactValidationError("PDF trailer size is invalid")
    if root_reference is None:
        raise ArtifactValidationError("PDF trailer does not identify a document catalog")
    for (object_number, generation), offset in xref_entries.items():
        header = re.match(rb"%d\s+%d\s+obj\b" % (object_number, generation), content[offset:])
        if header is None:
            raise ArtifactValidationError("PDF cross-reference does not match its objects")

    root_number, root_generation = root_reference
    root_offset = xref_entries.get(root_reference)
    root_pattern = rb"(?:\A|[\r\n])%d\s+%d\s+obj\b" % (root_number, root_generation)
    if root_offset is not None:
        root_match = re.match(
            rb"%d\s+%d\s+obj\b" % (root_number, root_generation), content[root_offset:]
        )
        root_start = root_offset + (root_match.end() if root_match else 0)
    else:
        located = re.search(root_pattern, content[:xref_offset])
        root_start = located.end() if located else -1
    if root_start < 0:
        raise ArtifactValidationError("PDF document catalog object is missing")
    root_end = content.find(b"endobj", root_start, min(xref_offset, root_start + 1_000_000))
    if root_end < 0:
        raise ArtifactValidationError("PDF document catalog object is malformed")
    root_object = content[root_start:root_end]
    if (
        re.search(rb"/Type\s*/Catalog\b", root_object) is None
        or _pdf_reference(root_object, b"Pages") is None
    ):
        raise ArtifactValidationError("PDF document catalog is invalid")


def _validate_docx(content: bytes) -> None:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_DOCX_ENTRIES:
                raise ArtifactValidationError("DOCX ZIP entry count is invalid")
            names: set[str] = set()
            expanded = 0
            for entry in entries:
                name = entry.filename
                pure = PurePosixPath(name)
                raw_parts = name.split("/")
                if entry.is_dir():
                    raw_parts = raw_parts[:-1]
                unix_mode = entry.external_attr >> 16
                if (
                    not name
                    or "\\" in name
                    or name.startswith("/")
                    or pure.is_absolute()
                    or not raw_parts
                    or any(part in {"", ".", ".."} for part in raw_parts)
                    or name in names
                    or stat.S_ISLNK(unix_mode)
                ):
                    raise ArtifactValidationError("DOCX contains an unsafe ZIP entry")
                names.add(name)
                if entry.flag_bits & 0x1:
                    raise ArtifactValidationError("DOCX contains an encrypted ZIP entry")
                if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise ArtifactValidationError("DOCX uses an unsupported ZIP compression method")
                expanded += entry.file_size
                if expanded > MAX_DOCX_EXPANDED_BYTES:
                    raise ArtifactValidationError("DOCX ZIP expansion is excessive")
                if (entry.file_size and entry.compress_size == 0) or (
                    entry.compress_size
                    and entry.file_size > entry.compress_size * MAX_DOCX_COMPRESSION_RATIO
                ):
                    raise ArtifactValidationError("DOCX ZIP compression ratio is excessive")
            missing = _DOCX_REQUIRED_PARTS.keys() - names
            if missing:
                raise ArtifactValidationError("DOCX is missing required OOXML parts")
            parsed_parts: dict[str, ElementTree.Element] = {}
            for name, expected_tag in _DOCX_REQUIRED_PARTS.items():
                if archive.getinfo(name).file_size > MAX_DOCX_REQUIRED_XML_BYTES:
                    raise ArtifactValidationError("DOCX required OOXML part is excessive")
                try:
                    xml = archive.read(name)
                    if b"<!doctype" in xml.lower() or b"<!entity" in xml.lower():
                        raise ArtifactValidationError(
                            "DOCX required OOXML contains a forbidden declaration"
                        )
                    root = ElementTree.fromstring(xml)
                except ElementTree.ParseError as error:
                    raise ArtifactValidationError("DOCX contains malformed OOXML") from error
                if root.tag != expected_tag:
                    raise ArtifactValidationError("DOCX contains invalid required OOXML parts")
                parsed_parts[name] = root

            content_types = parsed_parts["[Content_Types].xml"]
            document = parsed_parts["word/document.xml"]
            body_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body"
            direct_bodies = document.findall(body_tag)
            all_bodies = list(document.iter(body_tag))
            if len(direct_bodies) != 1 or len(all_bodies) != 1:
                raise ArtifactValidationError("DOCX main document must contain exactly one body")

            word_overrides = [
                entry
                for entry in content_types.findall(f"{{{_CONTENT_TYPES_NAMESPACE}}}Override")
                if entry.get("PartName") == "/word/document.xml"
            ]
            if (
                len(word_overrides) != 1
                or word_overrides[0].get("ContentType") != _WORD_MAIN_CONTENT_TYPE
            ):
                raise ArtifactValidationError(
                    "DOCX main document content type is missing or invalid"
                )

            relationships = parsed_parts["_rels/.rels"]
            office_documents = [
                relationship
                for relationship in relationships.findall(
                    f"{{{_RELATIONSHIPS_NAMESPACE}}}Relationship"
                )
                if relationship.get("Type") == _OFFICE_DOCUMENT_RELATIONSHIP
            ]
            if len(office_documents) != 1:
                raise ArtifactValidationError(
                    "DOCX package must contain one officeDocument relationship"
                )
            office_document = office_documents[0]
            target = office_document.get("Target", "")
            normalized_target = PurePosixPath(target.lstrip("/"))
            if (
                office_document.get("TargetMode", "Internal") != "Internal"
                or not target
                or "\\" in target
                or normalized_target.parts != ("word", "document.xml")
            ):
                raise ArtifactValidationError("DOCX officeDocument relationship target is invalid")
            corrupt = archive.testzip()
            if corrupt is not None:
                raise ArtifactValidationError("DOCX ZIP integrity check failed")
    except ArtifactValidationError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError) as error:
        raise ArtifactValidationError("Artifact bytes are not a valid DOCX") from error


def resolve_repository_root(root: Path, *, create: bool) -> Path:
    supplied = root.expanduser()
    if supplied.is_symlink():
        raise ArtifactStorageError("Artifact repository root must not be a symbolic link")
    if create:
        supplied.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved = supplied.resolve(strict=True)
    metadata = resolved.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ArtifactStorageError("Artifact repository root must be a directory")
    return resolved


def _open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY
    if directory and hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and stat.S_IFMT(first.st_mode) == stat.S_IFMT(second.st_mode)
    )


@dataclass(slots=True)
class OpenedDirectoryChain:
    """A retained no-follow descriptor chain beneath one validated root."""

    root: Path
    path: Path
    descriptors: list[int]
    segments: tuple[str, ...]

    @property
    def descriptor(self) -> int:
        return self.descriptors[-1]

    def assert_attached(self) -> None:
        """Require every descriptor to remain at its original root-relative name."""
        try:
            named_root = os.stat(self.root, follow_symlinks=False)
            if not _same_identity(named_root, os.fstat(self.descriptors[0])):
                raise ArtifactStorageError("Configured artifact root changed during storage")
            for index, segment in enumerate(self.segments):
                named = os.stat(
                    segment,
                    dir_fd=self.descriptors[index],
                    follow_symlinks=False,
                )
                opened = os.fstat(self.descriptors[index + 1])
                if not stat.S_ISDIR(opened.st_mode) or not _same_identity(named, opened):
                    raise ArtifactStorageError(
                        "Artifact repository directory changed during storage"
                    )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
                raise ArtifactStorageError(
                    "Artifact repository directory changed during storage"
                ) from error
            raise

    def close(self) -> None:
        while self.descriptors:
            os.close(self.descriptors.pop())

    def __enter__(self) -> OpenedDirectoryChain:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def open_directory_chain(root: Path, *segments: str) -> OpenedDirectoryChain:
    """Create and open descendants only through descriptors rooted at ``root``."""
    resolved_root = resolve_repository_root(root, create=False)
    safe_segments = tuple(
        validate_safe_segment(segment, "Artifact path segment") for segment in segments
    )
    expected_root = os.stat(resolved_root, follow_symlinks=False)
    descriptors: list[int] = []
    try:
        root_descriptor = os.open(resolved_root, _open_flags(directory=True))
        descriptors.append(root_descriptor)
        opened_root = os.fstat(root_descriptor)
        if not stat.S_ISDIR(opened_root.st_mode) or not _same_identity(expected_root, opened_root):
            raise ArtifactStorageError("Configured artifact root changed during storage")

        current = root_descriptor
        for segment in safe_segments:
            created = False
            try:
                # This is a validated single segment opened relative to a no-follow descriptor.
                # codeql[py/path-injection]
                os.mkdir(segment, mode=0o700, dir_fd=current)
                created = True
            except FileExistsError:
                pass
            if created:
                os.fsync(current)
            # This is a validated single segment opened relative to a no-follow descriptor.
            # codeql[py/path-injection]
            descriptor = os.open(segment, _open_flags(directory=True), dir_fd=current)
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(descriptor)
                raise ArtifactStorageError("Artifact repository ancestor is not a directory")
            descriptors.append(descriptor)
            current = descriptor

        opened = OpenedDirectoryChain(
            root=resolved_root,
            path=resolved_root.joinpath(*safe_segments),
            descriptors=descriptors,
            segments=safe_segments,
        )
        opened.assert_attached()
        return opened
    except OSError as error:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        if error.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise ArtifactStorageError(
                "Artifact repository path contains a symbolic link or missing component"
            ) from error
        raise
    except BaseException:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def open_contained_file(
    path: Path,
    *,
    roots: tuple[Path, ...],
) -> tuple[Path, int, os.stat_result]:
    """Open one regular file beneath a trusted root without following descendant links."""
    supplied = Path(os.path.abspath(path.expanduser()))
    resolved_roots: list[tuple[Path, os.stat_result]] = []
    for root in roots:
        try:
            resolved = resolve_repository_root(root, create=False)
            resolved_roots.append((resolved, os.stat(resolved, follow_symlinks=False)))
        except FileNotFoundError:
            continue

    selected: tuple[Path, tuple[str, ...], os.stat_result] | None = None
    for root, root_metadata in sorted(
        resolved_roots, key=lambda item: len(item[0].parts), reverse=True
    ):
        try:
            relative = supplied.relative_to(root)
        except ValueError:
            continue
        if relative.parts and all(part not in {"", ".", ".."} for part in relative.parts):
            selected = root, relative.parts, root_metadata
            break
    if selected is None:
        raise ArtifactStorageError("Artifact is outside configured roots")

    root, parts, expected_root = selected
    descriptors: list[int] = []
    try:
        root_descriptor = os.open(root, _open_flags(directory=True))
        descriptors.append(root_descriptor)
        opened_root = os.fstat(root_descriptor)
        if not stat.S_ISDIR(opened_root.st_mode) or not _same_identity(expected_root, opened_root):
            raise ArtifactStorageError("Configured artifact root changed during access")
        current = root_descriptor
        for part in parts[:-1]:
            descriptor = os.open(part, _open_flags(directory=True), dir_fd=current)
            descriptors.append(descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise ArtifactStorageError("Artifact ancestor is not a directory")
            current = descriptor
        descriptor = os.open(parts[-1], _open_flags(), dir_fd=current)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise ArtifactStorageError("Artifact is not a regular file")
        return root.joinpath(*parts), descriptor, metadata
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise ArtifactStorageError(
                "Artifact path contains a symbolic link or missing component"
            ) from error
        raise
    finally:
        for opened in reversed(descriptors):
            os.close(opened)


def read_open_file(
    descriptor: int,
    metadata: os.stat_result,
    *,
    maximum: int,
) -> bytes:
    if metadata.st_size <= 0 or metadata.st_size > maximum:
        raise ArtifactStorageError("Artifact size is invalid")
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    after = os.fstat(descriptor)
    if (
        not _same_identity(metadata, after)
        or after.st_size != metadata.st_size
        or len(content) != metadata.st_size
        or len(content) > maximum
    ):
        raise ArtifactStorageError("Artifact changed during access")
    return content


def verify_artifact_file(
    path: Path,
    *,
    roots: tuple[Path, ...],
    media_type: str,
    expected_sha256: str,
    maximum: int = MAX_ARTIFACT_BYTES,
) -> tuple[Path, bytes]:
    candidate, content = read_contained_file(path, roots=roots, maximum=maximum)
    try:
        validate_plain_filename(
            candidate.name,
            media_type,
            maximum_bytes=MAX_STORED_FILENAME_BYTES,
        )
        validate_artifact_bytes(
            content,
            media_type=media_type,
            expected_sha256=expected_sha256,
            maximum=maximum,
        )
    except ArtifactValidationError as error:
        raise ArtifactStorageError("Stored artifact failed integrity validation") from error
    return candidate, content


def read_contained_file(
    path: Path,
    *,
    roots: tuple[Path, ...],
    maximum: int,
) -> tuple[Path, bytes]:
    candidate, descriptor, metadata = open_contained_file(path, roots=roots)
    try:
        content = read_open_file(descriptor, metadata, maximum=maximum)
    finally:
        os.close(descriptor)
    return candidate, content


def write_new_file_at(directory_descriptor: int, filename: str, content: bytes) -> os.stat_result:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(filename, flags, 0o600, dir_fd=directory_descriptor)
    identity = os.fstat(descriptor)
    try:
        written = 0
        while written < len(content):
            count = os.write(descriptor, content[written:])
            if count <= 0:
                raise OSError("Artifact write made no progress")
            written += count
        os.fsync(descriptor)
    except BaseException:
        try:
            named = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
            if _same_identity(identity, named):
                os.unlink(filename, dir_fd=directory_descriptor)
                os.fsync(directory_descriptor)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    os.fsync(directory_descriptor)
    return identity


def read_file_at(directory_descriptor: int, filename: str, *, maximum: int) -> bytes:
    """Read one stable regular file relative to an already-open directory."""
    descriptor: int | None = None
    try:
        descriptor = os.open(filename, _open_flags(), dir_fd=directory_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ArtifactStorageError("Stored artifact is not a regular file")
        content = read_open_file(descriptor, metadata, maximum=maximum)
        named = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if not _same_identity(metadata, named):
            raise ArtifactStorageError("Stored artifact changed during access")
        return content
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise ArtifactStorageError(
                "Stored artifact path contains a symbolic link or missing component"
            ) from error
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _materialize_idempotent_file(
    directory: OpenedDirectoryChain, filename: str, content: bytes
) -> tuple[Path, os.stat_result | None]:
    """Exclusively create or verify a content-addressed file in ``directory``."""
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or len(filename.encode("utf-8")) > MAX_STORED_FILENAME_BYTES
    ):
        raise ArtifactValidationError("Stored artifact filename is unsafe")

    directory.assert_attached()
    created_identity: os.stat_result | None = None
    try:
        with suppress(FileExistsError):
            created_identity = write_new_file_at(directory.descriptor, filename, content)
        # Re-sync idempotent replays in case a prior process exited before the
        # directory entry was durable.
        os.fsync(directory.descriptor)
        directory.assert_attached()
        stored_content = read_file_at(
            directory.descriptor,
            filename,
            maximum=max(len(content), 1),
        )
        if stored_content != content:
            raise ArtifactStorageError("Published document destination is not trustworthy")
        directory.assert_attached()
        return directory.path / filename, created_identity
    except BaseException:
        if created_identity is not None:
            try:
                named = os.stat(
                    filename,
                    dir_fd=directory.descriptor,
                    follow_symlinks=False,
                )
                if _same_identity(created_identity, named):
                    os.unlink(filename, dir_fd=directory.descriptor)
                    os.fsync(directory.descriptor)
            except FileNotFoundError:
                pass
        raise


def materialize_idempotent_file(
    directory: OpenedDirectoryChain, filename: str, content: bytes
) -> Path:
    """Exclusively create or verify one content-addressed file."""
    path, _ = _materialize_idempotent_file(directory, filename, content)
    return path


def _clean_owned_directory(
    parent_descriptor: int,
    directory_name: str,
    directory_descriptor: int,
    directory_identity: os.stat_result,
    owned_files: dict[str, os.stat_result],
) -> None:
    """Remove only entries and a directory created by the current operation."""
    for filename, identity in owned_files.items():
        try:
            named = os.stat(
                filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        if _same_identity(identity, named):
            os.unlink(filename, dir_fd=directory_descriptor)
    os.fsync(directory_descriptor)
    try:
        named_directory = os.stat(
            directory_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if not _same_identity(directory_identity, named_directory):
        return
    try:
        os.rmdir(directory_name, dir_fd=parent_descriptor)
    except OSError as error:
        if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
            raise
    os.fsync(parent_descriptor)


def materialize_idempotent_pair(
    directory: OpenedDirectoryChain,
    files: tuple[tuple[str, bytes], tuple[str, bytes]],
) -> tuple[Path, Path]:
    """Atomically create or verify two files in one deterministic pair directory."""
    filenames = tuple(filename for filename, _ in files)
    if filenames[0] == filenames[1]:
        raise ArtifactValidationError("Published document pair filenames must be distinct")
    pair_material = b"\0".join(
        filename.encode("utf-8") + b"\0" + sha256(content).digest() for filename, content in files
    )
    destination_name = f"pair-{sha256(pair_material).hexdigest()[:20]}"
    temporary_name = f".pair-{uuid4().hex}.tmp"
    temporary_descriptor: int | None = None
    temporary_identity: os.stat_result | None = None
    published_descriptor: int | None = None
    owned_files: dict[str, os.stat_result] = {}
    renamed = False
    completed = False
    try:
        directory.assert_attached()
        os.mkdir(temporary_name, mode=0o700, dir_fd=directory.descriptor)
        temporary_descriptor = os.open(
            temporary_name,
            _open_flags(directory=True),
            dir_fd=directory.descriptor,
        )
        temporary_identity = os.fstat(temporary_descriptor)
        os.fsync(directory.descriptor)
        temporary = OpenedDirectoryChain(
            root=directory.root,
            path=directory.path / temporary_name,
            descriptors=[*directory.descriptors, temporary_descriptor],
            segments=(*directory.segments, temporary_name),
        )
        for filename, content in files:
            _, created_identity = _materialize_idempotent_file(temporary, filename, content)
            if created_identity is not None:
                owned_files[filename] = created_identity
            temporary.assert_attached()
        os.fsync(temporary_descriptor)
        temporary.assert_attached()
        try:
            os.rename(
                temporary_name,
                destination_name,
                src_dir_fd=directory.descriptor,
                dst_dir_fd=directory.descriptor,
            )
        except OSError as error:
            try:
                published_descriptor = os.open(
                    destination_name,
                    _open_flags(directory=True),
                    dir_fd=directory.descriptor,
                )
            except OSError:
                raise error from None
            try:
                os.fsync(directory.descriptor)
            except OSError as fsync_error:
                raise ArtifactStorageError(
                    "Could not synchronize published document pair directory"
                ) from fsync_error
        else:
            renamed = True
            published_descriptor = temporary_descriptor
            try:
                os.fsync(directory.descriptor)
            except OSError as error:
                raise ArtifactStorageError(
                    "Could not synchronize published document pair directory"
                ) from error

        published = OpenedDirectoryChain(
            root=directory.root,
            path=directory.path / destination_name,
            descriptors=[*directory.descriptors, published_descriptor],
            segments=(*directory.segments, destination_name),
        )
        published.assert_attached()
        for filename, content in files:
            if (
                read_file_at(
                    published_descriptor,
                    filename,
                    maximum=max(len(content), 1),
                )
                != content
            ):
                raise ArtifactStorageError("Published document destination is not trustworthy")
            published.assert_attached()
        completed = True
        return published.path / filenames[0], published.path / filenames[1]
    finally:
        try:
            if (
                temporary_descriptor is not None
                and temporary_identity is not None
                and (not renamed or not completed)
            ):
                _clean_owned_directory(
                    directory.descriptor,
                    destination_name if renamed else temporary_name,
                    temporary_descriptor,
                    temporary_identity,
                    owned_files,
                )
        finally:
            if published_descriptor is not None and published_descriptor != temporary_descriptor:
                os.close(published_descriptor)
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)


def write_new_file(path: Path, content: bytes) -> None:
    """Create a file exclusively relative to a no-follow directory descriptor."""
    parent_descriptor = os.open(path.parent, _open_flags(directory=True))
    try:
        write_new_file_at(parent_descriptor, path.name, content)
    finally:
        os.close(parent_descriptor)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, _open_flags(directory=True))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jobos_api.artifact_repository import (
    ALLOWED_MEDIA_TYPES,
    MAX_CALLER_FILENAME_BYTES,
    PDF_MEDIA_TYPE,
    ArtifactRepositoryError,
    ArtifactStorageError,
    ArtifactValidationError,
    OpenedDirectoryChain,
    materialize_idempotent_file,
    materialize_idempotent_pair,
    open_directory_chain,
    verify_artifact_file,
)

ARTIFACT_ID_PATTERN = re.compile(r"^art_[A-Za-z0-9_-]{16,80}$")
DOCUMENT_KEYS = {"resume", "cover_letter", "references"}

ArtifactTrustError = ArtifactValidationError


class ArtifactNotFound(KeyError):
    """The opaque artifact ID is not registered."""


class ArtifactSource(Protocol):
    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class VerifiedArtifact:
    job_id: str
    document_key: Literal["resume", "cover_letter", "references"]
    document_label: str
    source_revision: str
    artifact_revision: str
    media_type: str
    sha256: str
    render_status: Literal["succeeded", "failed", "rendering"]
    render_sequence: int
    canonical_path: str | None
    filename: str | None
    failure_message: str | None

    @property
    def registry_key(self) -> str:
        material = "\0".join(
            (
                self.job_id,
                self.source_revision,
                self.artifact_revision,
                self.media_type,
                self.render_status,
                str(self.render_sequence),
                self.sha256,
                self.failure_message or "",
            )
        )
        return sha256(material.encode("utf-8")).hexdigest()


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    job_id: str
    document_key: Literal["resume", "cover_letter", "references"]
    document_label: str
    render_sequence: int
    source_revision: str
    artifact_revision: str
    media_type: str
    sha256: str | None
    render_status: Literal["succeeded", "failed", "rendering"]
    filename: str | None
    failure_message: str | None
    created_at: str
    is_current: bool
    is_last_successful: bool
    is_approved: bool
    preview_available: bool


class JobArtifactsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    artifacts: list[ArtifactRecord]
    current_artifact_id: str | None
    last_successful_artifact_id: str | None
    approved_artifact_id: str | None


class ArtifactRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_reference: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    origin: Literal["user", "mcp"] = "user"
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class ArtifactPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_key: Literal["resume", "cover_letter", "references"]
    document_label: str = Field(min_length=1, max_length=80)
    source_filename: str = Field(min_length=1, max_length=255)
    source_base64: str = Field(min_length=1, max_length=28_000_000)
    artifact_filename: str = Field(min_length=1, max_length=255)
    artifact_base64: str = Field(min_length=1, max_length=28_000_000)
    origin: Literal["user", "mcp"] = "mcp"
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("document_label")
    @classmethod
    def strip_document_label(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("document label must not be blank")
        return stripped

    @model_validator(mode="after")
    def fixed_document_label(self) -> ArtifactPublishRequest:
        if self.document_key == "references" and self.document_label != "References":
            raise ValueError("document label must be References")
        return self

    @field_validator("source_filename", "artifact_filename")
    @classmethod
    def require_plain_filename(cls, value: str) -> str:
        if value in {".", ".."} or Path(value).name != value or "\0" in value:
            raise ValueError("document filenames must not contain a path")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "document"
        if len(safe_name.encode("utf-8")) > MAX_CALLER_FILENAME_BYTES:
            raise ValueError("document filename is too long for content-addressed storage")
        return value

    def source_bytes(self) -> bytes:
        return _decode_published_bytes(self.source_base64, maximum=20_000_000, label="source")

    def artifact_bytes(self) -> bytes:
        return _decode_published_bytes(self.artifact_base64, maximum=20_000_000, label="artifact")


class ArtifactRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: Literal["user", "mcp"] = "user"
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class ArtifactApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: Literal["user", "mcp"] = "user"
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class ResumeRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    output_format: Literal["pdf"] = "pdf"
    origin: Literal["user", "mcp"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class ArtifactContentHeaders(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    media_type: str
    sha256: str
    source_revision: str
    artifact_revision: str
    filename: str
    digest: str


def _decode_published_bytes(value: str, *, maximum: int, label: str) -> bytes:
    try:
        content = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise ArtifactTrustError(f"Published {label} is not valid base64") from error
    if not content or len(content) > maximum:
        raise ArtifactTrustError(f"Published {label} size is invalid")
    return content


def materialize_published_document(
    command: ArtifactPublishRequest, *, job_id: str, workspace_root: Path
) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", job_id):
        raise ArtifactTrustError("Job ID is not safe for document publication")
    with open_directory_chain(
        workspace_root, "resume", "exports", "jobos", job_id, "imports"
    ) as publication_root:
        source, artifact = materialize_idempotent_pair(
            publication_root,
            (
                _content_addressed_item(command.source_filename, command.source_bytes()),
                _content_addressed_item(command.artifact_filename, command.artifact_bytes()),
            ),
        )
        return source, artifact


def materialize_external_import(
    *,
    job_id: str,
    document_id: str,
    document_key: str,
    source_filename: str,
    source_sha256: str,
    source_bytes: bytes,
    workspace_root: Path,
) -> tuple[Path, Path]:
    """Materialize a manifest source plus the verified original DOCX for facade publication."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", job_id):
        raise ArtifactTrustError("Job ID is not safe for document publication")
    if not re.fullmatch(r"edoc_[A-Za-z0-9_-]{24}", document_id):
        raise ArtifactTrustError("Editable document ID is not safe for publication")
    computed_hash = sha256(source_bytes).hexdigest()
    if computed_hash != source_sha256:
        raise ArtifactTrustError("External DOCX SHA-256 does not match")
    manifest = json.dumps(
        {
            "schema_version": 1,
            "kind": "jobos_editable_document_import",
            "job_id": job_id,
            "document_id": document_id,
            "document_key": document_key,
            "original_filename": source_filename,
            "original_sha256": source_sha256,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    with open_directory_chain(
        workspace_root, "resume", "exports", "jobos", job_id, "imports"
    ) as publication_root:
        manifest_path, artifact_path = materialize_idempotent_pair(
            publication_root,
            (
                _content_addressed_item(f"{document_id}-import-source.json", manifest),
                _content_addressed_item(source_filename, source_bytes),
            ),
        )
        return manifest_path, artifact_path


def _materialize_content_addressed(
    root: OpenedDirectoryChain, filename: str, content: bytes
) -> Path:
    stored_name, stored_content = _content_addressed_item(filename, content)
    return materialize_idempotent_file(root, stored_name, stored_content)


def _content_addressed_item(filename: str, content: bytes) -> tuple[str, bytes]:
    digest = sha256(content).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip(".-") or "document"
    if len(safe_name.encode("utf-8")) > MAX_CALLER_FILENAME_BYTES:
        raise ArtifactTrustError("Document filename is too long for content-addressed storage")
    return f"{digest[:20]}-{safe_name}", content


def read_source_artifact(
    raw: dict[str, Any], roots: tuple[Path, ...]
) -> tuple[VerifiedArtifact, bytes | None]:
    required = ("job_id", "source_revision", "artifact_revision", "media_type", "render_status")
    if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
        raise ArtifactTrustError("Artifact metadata is incomplete")
    status = raw["render_status"]
    if status not in {"succeeded", "failed", "rendering"}:
        raise ArtifactTrustError("Artifact render status is invalid")
    media_type = raw["media_type"]
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ArtifactTrustError("Artifact media type is not allowlisted")
    document_key = raw.get("document_key", "resume")
    document_label = raw.get("document_label", "Resume")
    if document_key not in DOCUMENT_KEYS:
        raise ArtifactTrustError("Artifact document key is invalid")
    if not isinstance(document_label, str) or not 1 <= len(document_label.strip()) <= 80:
        raise ArtifactTrustError("Artifact document label is invalid")
    document_label = document_label.strip()
    render_sequence = raw.get("render_sequence")
    if (
        not isinstance(render_sequence, int)
        or isinstance(render_sequence, bool)
        or render_sequence < 0
    ):
        raise ArtifactTrustError("Artifact render sequence is missing or invalid")

    if status != "succeeded":
        message = raw.get("failure_message")
        return VerifiedArtifact(
            job_id=raw["job_id"],
            document_key=document_key,
            document_label=document_label,
            source_revision=raw["source_revision"],
            artifact_revision=raw["artifact_revision"],
            media_type=media_type,
            sha256="",
            render_status=status,
            render_sequence=render_sequence,
            canonical_path=None,
            filename=None,
            failure_message=str(message)[:500] if message else None,
        ), None

    supplied_path = raw.get("path")
    supplied_hash = raw.get("sha256")
    if not isinstance(supplied_path, (str, Path)) or not isinstance(supplied_hash, str):
        raise ArtifactTrustError("Successful artifact metadata is incomplete")
    try:
        candidate, content = verify_artifact_file(
            Path(supplied_path),
            roots=roots,
            media_type=media_type,
            expected_sha256=supplied_hash,
        )
    except ArtifactStorageError:
        raise
    except ArtifactRepositoryError as error:
        message = str(error)
        if message == "Artifact SHA-256 does not match":
            message = "Artifact SHA-256 does not match registered metadata"
        raise ArtifactTrustError(message) from error
    computed_hash = sha256(content).hexdigest()
    return VerifiedArtifact(
        job_id=raw["job_id"],
        document_key=document_key,
        document_label=document_label,
        source_revision=raw["source_revision"],
        artifact_revision=raw["artifact_revision"],
        media_type=media_type,
        sha256=computed_hash,
        render_status="succeeded",
        render_sequence=render_sequence,
        canonical_path=str(candidate),
        filename=candidate.name,
        failure_message=None,
    ), content


def verify_source_artifact(raw: dict[str, Any], roots: tuple[Path, ...]) -> VerifiedArtifact:
    return read_source_artifact(raw, roots)[0]


def verify_facade_artifacts(
    raw_artifacts: list[dict[str, Any]], roots: tuple[Path, ...]
) -> list[VerifiedArtifact]:
    verified = [verify_source_artifact(raw, roots) for raw in raw_artifacts]
    sequences = [artifact.render_sequence for artifact in verified]
    if len(sequences) != len(set(sequences)):
        raise ArtifactTrustError("Facade artifact render sequences must be unique")
    return verified


def artifact_record(
    row: dict[str, Any],
    *,
    current_id: str | None,
    last_successful_id: str | None,
    approved_id: str | None,
) -> ArtifactRecord:
    artifact_id = str(row["artifact_id"])
    return ArtifactRecord(
        artifact_id=artifact_id,
        job_id=str(row["job_id"]),
        document_key=row["document_key"],
        document_label=str(row["document_label"]),
        render_sequence=int(row["render_sequence"]),
        source_revision=str(row["source_revision"]),
        artifact_revision=str(row["artifact_revision"]),
        media_type=str(row["media_type"]),
        sha256=str(row["sha256"]) if row["sha256"] else None,
        render_status=row["render_status"],
        filename=str(row["filename"]) if row["filename"] else None,
        failure_message=str(row["failure_message"]) if row["failure_message"] else None,
        created_at=str(row["created_at"]),
        is_current=artifact_id == current_id,
        is_last_successful=artifact_id == last_successful_id,
        is_approved=artifact_id == approved_id,
        preview_available=(
            row["render_status"] == "succeeded" and row["media_type"] == PDF_MEDIA_TYPE
        ),
    )


def content_headers(record: dict[str, Any]) -> ArtifactContentHeaders:
    digest = base64.b64encode(bytes.fromhex(record["sha256"])).decode("ascii")
    return ArtifactContentHeaders(
        artifact_id=record["artifact_id"],
        media_type=record["media_type"],
        sha256=record["sha256"],
        source_revision=record["source_revision"],
        artifact_revision=record["artifact_revision"],
        filename=record["filename"],
        digest=f"sha-256={digest}",
    )

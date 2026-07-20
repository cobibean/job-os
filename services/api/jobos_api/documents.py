from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

ARTIFACT_ID_PATTERN = re.compile(r"^art_[A-Za-z0-9_-]{16,80}$")
PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ALLOWED_MEDIA_TYPES = {PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE}


class ArtifactTrustError(ValueError):
    """Artifact metadata or bytes failed the document trust boundary."""


class ArtifactNotFound(KeyError):
    """The opaque artifact ID is not registered."""


class ArtifactSource(Protocol):
    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class VerifiedArtifact:
    job_id: str
    source_revision: str
    artifact_revision: str
    media_type: str
    sha256: str
    render_status: Literal["succeeded", "failed", "rendering"]
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
                self.sha256,
                self.failure_message or "",
            )
        )
        return sha256(material.encode("utf-8")).hexdigest()


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    job_id: str
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
    preview_available: bool


class JobArtifactsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    artifacts: list[ArtifactRecord]
    current_artifact_id: str | None
    last_successful_artifact_id: str | None


class ArtifactRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_reference: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class ArtifactContentHeaders(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    media_type: str
    sha256: str
    source_revision: str
    artifact_revision: str
    filename: str
    digest: str


def verify_source_artifact(raw: dict[str, Any], roots: tuple[Path, ...]) -> VerifiedArtifact:
    required = ("job_id", "source_revision", "artifact_revision", "media_type", "render_status")
    if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
        raise ArtifactTrustError("Artifact metadata is incomplete")
    status = raw["render_status"]
    if status not in {"succeeded", "failed", "rendering"}:
        raise ArtifactTrustError("Artifact render status is invalid")
    media_type = raw["media_type"]
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ArtifactTrustError("Artifact media type is not allowlisted")

    if status != "succeeded":
        message = raw.get("failure_message")
        return VerifiedArtifact(
            job_id=raw["job_id"],
            source_revision=raw["source_revision"],
            artifact_revision=raw["artifact_revision"],
            media_type=media_type,
            sha256="",
            render_status=status,
            canonical_path=None,
            filename=None,
            failure_message=str(message)[:500] if message else None,
        )

    supplied_path = raw.get("path")
    supplied_hash = raw.get("sha256")
    if not isinstance(supplied_path, (str, Path)) or not isinstance(supplied_hash, str):
        raise ArtifactTrustError("Successful artifact metadata is incomplete")
    candidate = Path(supplied_path).expanduser().resolve(strict=True)
    allowed = any(candidate == root or root in candidate.parents for root in roots)
    if not roots or not allowed:
        raise ArtifactTrustError("Artifact resolves outside configured roots")
    if not candidate.is_file():
        raise ArtifactTrustError("Artifact is not a regular file")
    content = candidate.read_bytes()
    computed_hash = sha256(content).hexdigest()
    if not re.fullmatch(r"[a-f0-9]{64}", supplied_hash) or computed_hash != supplied_hash:
        raise ArtifactTrustError("Artifact SHA-256 does not match registered metadata")
    if media_type == PDF_MEDIA_TYPE:
        if candidate.suffix.casefold() != ".pdf" or not content.startswith(b"%PDF-"):
            raise ArtifactTrustError("Artifact bytes do not match PDF metadata")
    elif candidate.suffix.casefold() != ".docx" or not content.startswith(b"PK"):
        raise ArtifactTrustError("Artifact bytes do not match DOCX metadata")
    return VerifiedArtifact(
        job_id=raw["job_id"],
        source_revision=raw["source_revision"],
        artifact_revision=raw["artifact_revision"],
        media_type=media_type,
        sha256=computed_hash,
        render_status="succeeded",
        canonical_path=str(candidate),
        filename=candidate.name,
        failure_message=None,
    )


def artifact_record(
    row: dict[str, Any], *, current_id: str | None, last_successful_id: str | None
) -> ArtifactRecord:
    artifact_id = str(row["artifact_id"])
    return ArtifactRecord(
        artifact_id=artifact_id,
        job_id=str(row["job_id"]),
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

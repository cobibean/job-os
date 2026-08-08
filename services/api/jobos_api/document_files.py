from __future__ import annotations

import re
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocumentKey = Literal["resume", "cover_letter", "references"]
CapabilityMode = Literal["editable", "editable_with_protected_content", "read_only"]
DOCUMENT_FILE_ID = re.compile(r"^dfile_[a-f0-9]{24}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentFileCapabilities(StrictModel):
    mode: CapabilityMode
    protected_block_count: int = Field(ge=0)
    editable_block_count: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("reasons")
    @classmethod
    def bounded_reasons(cls, value: list[str]) -> list[str]:
        if any(not reason or len(reason) > 300 for reason in value):
            raise ValueError("invalid document capability reason")
        return value


class DocumentFileRecord(StrictModel):
    document_id: str = Field(pattern=DOCUMENT_FILE_ID.pattern)
    job_id: str
    document_key: DocumentKey
    document_label: str = Field(min_length=1, max_length=80)
    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=SHA256.pattern)
    observed_revision: int = Field(ge=1)
    observed_device_id: str = Field(min_length=1, max_length=128)
    capabilities: DocumentFileCapabilities
    observed_at: str


class DocumentFileList(StrictModel):
    documents: list[DocumentFileRecord]


def document_file_id(job_id: str, document_key: DocumentKey) -> str:
    digest = sha256(f"{job_id}\0{document_key}".encode()).hexdigest()
    return f"dfile_{digest[:24]}"


def observed_document_file(
    job_id: str, data: dict[str, Any], *, observed_at: str, observed_device_id: str
) -> DocumentFileRecord:
    document_key = data.get("document_key")
    if document_key not in {"resume", "cover_letter", "references"}:
        raise ValueError("desktop returned an invalid document key")
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ValueError("desktop returned invalid document capabilities")
    portable = {
        "document_id": document_file_id(job_id, document_key),
        "job_id": job_id,
        "document_key": document_key,
        "document_label": data.get("document_label"),
        "filename": data.get("filename"),
        "sha256": data.get("sha256"),
        "observed_revision": data.get("revision"),
        "observed_device_id": observed_device_id,
        "capabilities": {
            "mode": capabilities.get("mode"),
            "protected_block_count": capabilities.get("protectedBlockCount"),
            "editable_block_count": capabilities.get("editableBlockCount"),
            "reasons": capabilities.get("reasons", []),
        },
        "observed_at": observed_at,
    }
    return DocumentFileRecord.model_validate(portable)

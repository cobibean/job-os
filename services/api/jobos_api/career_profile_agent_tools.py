from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import Field

from .career_profile_complete import (
    CareerProfileCompleteCurrent,
    ProfileItemRecord,
    SourceEvidenceRecord,
    StrictModel,
)

CareerProfileArea = Literal["my_career", "what_im_looking_for", "my_evidence"]
CareerProfileItemKind = Literal[
    "identity",
    "education",
    "skill",
    "positioning",
    "experience",
    "project",
    "claim",
    "target_roles",
    "compensation",
    "location",
    "work_arrangement",
    "industries",
    "priority",
    "dealbreaker",
    "custom",
]
CareerProfileReviewStatus = Literal["accepted", "proposed", "conflicting"]


class AgentProfileSearchResult(StrictModel):
    profile_revision: int = Field(ge=0)
    items: list[ProfileItemRecord]
    source_evidence: list[SourceEvidenceRecord]
    total_items: int = Field(ge=0)
    total_evidence: int = Field(ge=0)


class AgentEvidenceImportResult(StrictModel):
    profile_revision: int = Field(ge=0)
    evidence: SourceEvidenceRecord


class AgentEvidenceInspectResult(StrictModel):
    evidence: SourceEvidenceRecord
    byte_start: int = Field(ge=0)
    byte_length: int = Field(ge=0)
    total_bytes: int = Field(ge=1)
    next_byte_start: int | None = Field(default=None, ge=0)
    content_base64: str
    text: str | None = None


def search_projection(
    profile: CareerProfileCompleteCurrent,
    *,
    query: str,
    kinds: list[CareerProfileItemKind] | None,
    areas: list[CareerProfileArea] | None,
    review_statuses: list[CareerProfileReviewStatus] | None,
    has_evidence: bool | None,
    limit: int,
) -> AgentProfileSearchResult:
    needle = query.strip().casefold()
    kind_filter = set(kinds or [])
    area_filter = set(areas or [])
    status_filter = set(review_statuses or [])

    items: list[ProfileItemRecord] = []
    for item in profile.items:
        if kind_filter and item.value.kind not in kind_filter:
            continue
        if area_filter and item.area not in area_filter:
            continue
        if status_filter and item.review_status not in status_filter:
            continue
        if has_evidence is not None and bool(item.evidence_ids) != has_evidence:
            continue
        searchable = json.dumps(
            item.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        ).casefold()
        if needle and needle not in searchable:
            continue
        items.append(item)

    evidence: list[SourceEvidenceRecord] = []
    if not area_filter or "my_evidence" in area_filter:
        for source in profile.source_evidence:
            searchable = " ".join(
                (
                    source.original_filename,
                    source.media_type,
                    source.provenance.source_kind,
                    source.provenance.source_label,
                )
            ).casefold()
            if needle and needle not in searchable:
                continue
            evidence.append(source)

    return AgentProfileSearchResult(
        profile_revision=profile.profile_revision,
        items=items[:limit],
        source_evidence=evidence[:limit],
        total_items=len(items),
        total_evidence=len(evidence),
    )


def inspect_evidence_segment(
    evidence: SourceEvidenceRecord,
    content: bytes,
    *,
    byte_start: int,
    byte_length: int,
) -> AgentEvidenceInspectResult:
    segment = (
        b"" if byte_start >= len(content) else content[byte_start : byte_start + byte_length]
    )
    next_start = byte_start + len(segment)
    text: str | None = None
    if evidence.media_type.startswith("text/") or evidence.media_type in {
        "application/json",
        "application/xml",
    }:
        try:
            text = segment.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    return AgentEvidenceInspectResult(
        evidence=evidence,
        byte_start=byte_start,
        byte_length=len(segment),
        total_bytes=len(content),
        next_byte_start=next_start if next_start < len(content) else None,
        content_base64=base64.b64encode(segment).decode(),
        text=text,
    )

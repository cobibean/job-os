from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DocumentKey = Literal["resume", "cover_letter", "references"]
SemanticRole = Literal[
    "contact",
    "summary",
    "experience",
    "experience_achievement",
    "education",
    "skills",
    "reference",
    "cover_letter_body",
    "closing",
    "custom",
]
DocumentActor = Literal["user", "jobhunter", "import", "system"]
SnapshotReason = Literal[
    "import", "before_agent_edit", "manual", "before_publish", "before_restore"
]
NODE_ID = re.compile(
    r"^node_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I
)
SUGGESTION_ID = re.compile(r"^sug_[A-Za-z0-9_-]{1,80}$")
DOC_ID = re.compile(r"^edoc_[A-Za-z0-9_-]{24}$")
SNAP_ID = re.compile(r"^dsnap_[A-Za-z0-9_-]{24}$")
ALLOWED_NODES = {
    "doc",
    "jobosSection",
    "paragraph",
    "heading",
    "bulletList",
    "orderedList",
    "listItem",
    "blockquote",
    "horizontalRule",
    "hardBreak",
    "pageBreak",
    "table",
    "tableRow",
    "tableHeader",
    "tableCell",
    "image",
    "text",
}
ALLOWED_MARKS = {
    "bold",
    "italic",
    "underline",
    "strike",
    "textStyle",
    "link",
    "jobosField",
    "suggestion",
}
BLOCK_NODES = {
    "jobosSection",
    "paragraph",
    "heading",
    "listItem",
    "blockquote",
    "horizontalRule",
    "pageBreak",
    "table",
    "image",
}
BLOCK_CONTENT = {
    "jobosSection",
    "paragraph",
    "heading",
    "bulletList",
    "orderedList",
    "blockquote",
    "horizontalRule",
    "pageBreak",
    "table",
    "image",
}
INLINE_CONTENT = {"text", "hardBreak"}
ROLES = {
    "contact",
    "summary",
    "experience",
    "experience_achievement",
    "education",
    "skills",
    "reference",
    "cover_letter_body",
    "closing",
    "custom",
}
LABELS = {"resume": "Resume", "cover_letter": "Cover Letter", "references": "References"}
MAX_BYTES = 8 * 1024 * 1024
FONTS = {"Arial", "Calibri", "Times New Roman", "Georgia", "Garamond"}
HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$", re.IGNORECASE)
ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
BASE_BLOCK_ATTRS = {
    "jobosId",
    "semanticRole",
    "locked",
    "origin",
    "structuralSuggestion",
}
ATTRIBUTE_KEYS = {
    "doc": set(),
    "jobosSection": BASE_BLOCK_ATTRS | {"label"},
    "paragraph": BASE_BLOCK_ATTRS | {"textAlign"},
    "heading": BASE_BLOCK_ATTRS | {"level", "textAlign"},
    "bulletList": set(),
    "orderedList": {"start"},
    "listItem": BASE_BLOCK_ATTRS,
    "blockquote": BASE_BLOCK_ATTRS,
    "horizontalRule": BASE_BLOCK_ATTRS,
    "hardBreak": set(),
    "pageBreak": BASE_BLOCK_ATTRS,
    "table": BASE_BLOCK_ATTRS,
    "tableRow": set(),
    "tableHeader": {"colspan", "rowspan", "colwidth", "backgroundColor", "align"},
    "tableCell": {"colspan", "rowspan", "colwidth", "backgroundColor", "align"},
    "image": BASE_BLOCK_ATTRS | {"src", "alt", "title", "width", "height"},
    "text": set(),
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Margins(StrictModel):
    top: float = 1
    right: float = 1
    bottom: float = 1
    left: float = 1

    @field_validator("top", "right", "bottom", "left")
    @classmethod
    def valid_margin(cls, value: float) -> float:
        if not 0.25 <= value <= 2 or abs(value * 20 - round(value * 20)) > 1e-6:
            raise ValueError("margins must be 0.25-2.0 inches in 0.05 increments")
        return value


class HeaderFooter(StrictModel):
    left: str = Field(default="", max_length=500)
    center: str = Field(default="", max_length=500)
    right: str = Field(default="", max_length=500)
    first_page_different: bool = False


class DocumentSettings(StrictModel):
    page_size: Literal["letter", "a4"] = "letter"
    orientation: Literal["portrait"] = "portrait"
    margins_inches: Margins = Field(default_factory=Margins)
    default_font_family: Literal["Arial", "Calibri", "Times New Roman", "Georgia", "Garamond"] = (
        "Calibri"
    )
    default_font_size_pt: float = Field(default=11, ge=8, le=72)
    header: HeaderFooter = Field(default_factory=HeaderFooter)
    footer: HeaderFooter = Field(default_factory=HeaderFooter)
    show_page_numbers: bool = False


class DocumentComment(StrictModel):
    comment_id: str = Field(pattern=r"^comment_[A-Za-z0-9_-]{1,80}$")
    block_id: str = Field(pattern=NODE_ID.pattern)
    author: Literal["user", "jobhunter"]
    body: str = Field(min_length=1, max_length=2000)
    created_at: str
    resolved_at: str | None = None

    @field_validator("created_at", "resolved_at")
    @classmethod
    def valid_timestamp(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_iso_timestamp(value)
        return value


class DocumentImportIssue(StrictModel):
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["normalized", "dropped"]
    message: str = Field(min_length=1, max_length=500)
    count: int = Field(ge=1, le=5000)


class DocumentImportReport(StrictModel):
    source_filename: str | None = Field(default=None, max_length=255)
    imported_at: str | None = None
    issues: list[DocumentImportIssue] = Field(default_factory=list, max_length=200)

    @field_validator("imported_at")
    @classmethod
    def valid_import_timestamp(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_iso_timestamp(value)
        return value


def _validate_iso_timestamp(value: str) -> None:
    if not ISO_TIMESTAMP.fullmatch(value):
        raise ValueError("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid timestamp") from error
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")


def _valid_iso_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        _validate_iso_timestamp(value)
    except ValueError:
        return False
    return True


def default_settings() -> dict[str, Any]:
    return DocumentSettings().model_dump(mode="json")


def _attrs(role: str | None, locked: bool, label: str | None = None) -> dict[str, Any]:
    return {
        "jobosId": f"node_{uuid4()}",
        "semanticRole": role,
        "locked": locked,
        "origin": "system",
        "structuralSuggestion": None,
        **({"label": label} if label else {}),
    }


def _paragraph(role: str | None, locked: bool = False) -> dict[str, Any]:
    return {"type": "paragraph", "attrs": _attrs(role, locked), "content": []}


def _section(label: str, role: str, locked: bool, count: int = 1) -> dict[str, Any]:
    return {
        "type": "jobosSection",
        "attrs": _attrs(role, locked, label),
        "content": [_paragraph(role, locked) for _ in range(count)],
    }


def blank_content(key: DocumentKey) -> dict[str, Any]:
    if key == "resume":
        sections = [
            _section("Contact", "contact", True),
            _section("Summary", "summary", False),
            _section("Experience", "experience", False),
            _section("Education", "education", False),
            _section("Skills", "skills", False),
        ]
    elif key == "cover_letter":
        sections = [
            _section("Contact", "contact", True),
            _section("Body", "cover_letter_body", False, 3),
            _section("Closing", "closing", True),
        ]
    else:
        sections = [
            _section("Contact", "contact", True),
            _section("References", "reference", False),
        ]
    return {"type": "doc", "content": sections}


def plain_text(node: dict[str, Any]) -> str:
    if node.get("type") == "text":
        return str(node.get("text", ""))
    return "".join(plain_text(child) for child in node.get("content", []))


def _validate_child_content(node_type: str, children: list[dict[str, Any]]) -> None:
    child_types = [child.get("type") if isinstance(child, dict) else None for child in children]
    for child_type in child_types:
        if child_type not in ALLOWED_NODES:
            raise ValueError(f"unknown node type: {child_type}")
    if node_type == "doc" and (not children or any(kind != "jobosSection" for kind in child_types)):
        raise ValueError("documents must contain one or more JobOS sections")
    if node_type in {"jobosSection", "blockquote", "tableCell", "tableHeader"} and (
        not children or any(kind not in BLOCK_CONTENT for kind in child_types)
    ):
        raise ValueError(f"{node_type} must contain one or more block nodes")
    if node_type in {"paragraph", "heading"} and any(
        kind not in INLINE_CONTENT for kind in child_types
    ):
        raise ValueError(f"{node_type} may contain only inline content")
    if node_type in {"bulletList", "orderedList"} and (
        not children or any(kind != "listItem" for kind in child_types)
    ):
        raise ValueError(f"{node_type} must contain one or more list items")
    if node_type == "listItem" and (
        not children
        or child_types[0] != "paragraph"
        or any(kind not in BLOCK_CONTENT for kind in child_types[1:])
    ):
        raise ValueError("list items must begin with a paragraph and may contain blocks after it")
    if node_type == "table" and (not children or any(kind != "tableRow" for kind in child_types)):
        raise ValueError("tables must contain one or more rows")
    if node_type == "tableRow" and any(
        kind not in {"tableCell", "tableHeader"} for kind in child_types
    ):
        raise ValueError("table rows may contain only cells")
    if node_type in {"horizontalRule", "hardBreak", "pageBreak", "image", "text"} and children:
        raise ValueError(f"{node_type} may not contain child nodes")


def _numeric_value(value: Any, minimum: float, maximum: float, label: str) -> None:
    if value is None:
        return
    if isinstance(value, (bool, str)):
        raise ValueError(f"invalid {label}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"invalid {label}")


def _validate_node_attrs(node_type: str, node: dict[str, Any]) -> dict[str, Any]:
    attrs = node.get("attrs", {})
    if not isinstance(attrs, dict) or set(attrs).difference(ATTRIBUTE_KEYS[node_type]):
        raise ValueError(f"{node_type} contains unknown attributes")
    if node_type in BLOCK_NODES and "attrs" not in node:
        raise ValueError("block attributes are required")
    if (
        node_type in {"paragraph", "heading"}
        and attrs.get("textAlign") is not None
        and attrs["textAlign"] not in {"left", "center", "right", "justify"}
    ):
        raise ValueError("invalid text alignment")
    if node_type == "orderedList" and attrs.get("start") is not None:
        start = attrs["start"]
        if isinstance(start, bool) or not isinstance(start, int) or not 1 <= start <= 1_000_000:
            raise ValueError("invalid ordered-list start")
    if node_type in {"tableCell", "tableHeader"}:
        for field in ("colspan", "rowspan"):
            value = attrs.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20
            ):
                raise ValueError("invalid table span")
        colwidth = attrs.get("colwidth")
        if colwidth is not None and (
            not isinstance(colwidth, list)
            or any(
                isinstance(width, bool) or not isinstance(width, (int, float)) or width <= 0
                for width in colwidth
            )
        ):
            raise ValueError("invalid table column width")
        background = attrs.get("backgroundColor")
        if background is not None and (
            not isinstance(background, str) or not HEX_COLOR.fullmatch(background)
        ):
            raise ValueError("invalid table cell background color")
        alignment = attrs.get("align")
        if alignment is not None and alignment not in {"left", "center", "right", "justify"}:
            raise ValueError("invalid table cell alignment")
    if node_type == "image":
        for field in ("width", "height"):
            _numeric_value(attrs.get(field), 1, 20_000, "image dimension")
        for field in ("alt", "title"):
            value = attrs.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 2_000):
                raise ValueError("invalid image metadata")
    return attrs


def validate_content(
    content: dict[str, Any], settings: DocumentSettings, comments: list[DocumentComment]
) -> dict[str, Any]:
    if not isinstance(content, dict) or content.get("type") != "doc":
        raise ValueError("document root must be doc")
    if (
        len(
            json.dumps(
                {
                    "content": content,
                    "settings": settings.model_dump(mode="json"),
                    "comments": [c.model_dump(mode="json") for c in comments],
                },
                separators=(",", ":"),
            ).encode()
        )
        > MAX_BYTES
    ):
        raise ValueError("canonical document exceeds 8 MB")
    seen: set[str] = set()
    blocks = images = 0

    def walk(node: Any) -> None:
        nonlocal blocks, images
        if not isinstance(node, dict) or node.get("type") not in ALLOWED_NODES:
            raise ValueError("document contains an unknown node")
        node_type = node["type"]
        children = node.get("content", [])
        if not isinstance(children, list):
            raise ValueError("node content must be a list")
        allowed_keys = {"type", "content", "attrs", "marks"}
        if node_type == "text":
            allowed_keys.add("text")
        if set(node).difference(allowed_keys):
            raise ValueError("document node contains unknown fields")
        if node_type == "text":
            if not isinstance(node.get("text"), str) or "attrs" in node or "content" in node:
                raise ValueError("invalid text node")
        elif "text" in node:
            raise ValueError("only text nodes may contain text")
        if node_type != "text" and "marks" in node:
            raise ValueError("only text nodes may contain marks")
        _validate_child_content(node_type, children)
        node_attrs = _validate_node_attrs(node_type, node)
        marks = node.get("marks", [])
        if not isinstance(marks, list):
            raise ValueError("marks must be a list")
        for mark in marks:
            if not isinstance(mark, dict) or mark.get("type") not in ALLOWED_MARKS:
                raise ValueError("document contains an unknown mark")
            attrs = mark.get("attrs", {})
            if not isinstance(attrs, dict) or set(mark).difference({"type", "attrs"}):
                raise ValueError("invalid mark attributes")
            mark_type = mark["type"]
            if mark_type in {"bold", "italic", "underline", "strike"} and attrs:
                raise ValueError("simple marks do not accept attributes")
            if mark_type == "link" and (
                not isinstance(attrs.get("href"), str)
                or not re.match(r"^(https?:|mailto:)", attrs["href"])
                or len(attrs["href"]) > 8_192
                or set(attrs).difference({"href", "target", "rel", "class"})
                or attrs.get("target") not in {None, "_blank"}
                or any(
                    attrs.get(field) is not None and not isinstance(attrs.get(field), str)
                    for field in ("rel", "class")
                )
            ):
                raise ValueError("unsafe link")
            if mark_type == "textStyle":
                if set(attrs).difference(
                    {"fontFamily", "fontSize", "lineHeight", "color", "backgroundColor"}
                ):
                    raise ValueError("text style contains unknown attributes")
                if attrs.get("fontFamily") is not None and attrs["fontFamily"] not in FONTS:
                    raise ValueError("invalid font")
                font_size = attrs.get("fontSize")
                if isinstance(font_size, str):
                    if not re.fullmatch(r"\d+(?:\.\d+)?pt", font_size):
                        raise ValueError("invalid font size")
                    font_size = float(font_size[:-2])
                _numeric_value(font_size, 8, 72, "font size")
                line_height = attrs.get("lineHeight")
                if isinstance(line_height, str) and not re.fullmatch(r"\d+(?:\.\d+)?", line_height):
                    raise ValueError("invalid line height")
                if isinstance(line_height, str):
                    line_height = float(line_height)
                _numeric_value(line_height, 0.8, 3, "line height")
                for field in ("color", "backgroundColor"):
                    value = attrs.get(field)
                    if value is not None and (
                        not isinstance(value, str) or not HEX_COLOR.fullmatch(value)
                    ):
                        raise ValueError("invalid text color")
            if mark_type == "jobosField" and (
                not isinstance(attrs.get("fieldType"), str)
                or not 1 <= len(attrs["fieldType"]) <= 80
                or not isinstance(attrs.get("locked"), bool)
                or set(attrs).difference({"fieldType", "locked"})
            ):
                raise ValueError("invalid JobOS field mark")
            if mark_type == "suggestion" and (
                not SUGGESTION_ID.fullmatch(str(attrs.get("suggestionId", "")))
                or attrs.get("kind") not in {"insert", "delete"}
                or attrs.get("author") != "user"
                or not isinstance(attrs.get("createdAt"), str)
                or not _valid_iso_timestamp(attrs.get("createdAt"))
                or set(attrs).difference({"suggestionId", "kind", "author", "createdAt"})
            ):
                raise ValueError("invalid suggestion")
        if node_type in BLOCK_NODES:
            blocks += 1
            attrs = node_attrs
            block_id = attrs.get("jobosId")
            if not isinstance(block_id, str) or not NODE_ID.fullmatch(block_id) or block_id in seen:
                raise ValueError("block IDs must be present and unique")
            seen.add(block_id)
            if attrs.get("semanticRole") is not None and attrs.get("semanticRole") not in ROLES:
                raise ValueError("invalid semantic role")
            if not isinstance(attrs.get("locked"), bool) or attrs.get("origin") not in {
                "user",
                "jobhunter",
                "import",
                "system",
            }:
                raise ValueError("invalid block provenance")
            if node_type == "jobosSection" and (
                not isinstance(attrs.get("label"), str) or not 1 <= len(attrs["label"]) <= 120
            ):
                raise ValueError("section label is invalid")
            if node_type == "heading" and attrs.get("level") not in {1, 2, 3}:
                raise ValueError("heading level must be 1-3")
            structural = attrs.get("structuralSuggestion")
            if structural is not None and (
                not isinstance(structural, dict)
                or not SUGGESTION_ID.fullmatch(str(structural.get("suggestionId", "")))
                or structural.get("kind") not in {"insert", "delete"}
                or structural.get("author") != "user"
                or not isinstance(structural.get("createdAt"), str)
                or set(structural).difference({"suggestionId", "kind", "author", "createdAt"})
            ):
                raise ValueError("invalid structural suggestion")
        if node_type == "image":
            images += 1
            src = (node.get("attrs") or {}).get("src")
            match = re.fullmatch(
                r"data:image/(png|jpeg|gif);base64,([A-Za-z0-9+/]+={0,2})", str(src)
            )
            if not match or len(match.group(2)) * 3 // 4 > 2 * 1024 * 1024:
                raise ValueError("unsafe or oversized image")
        if node_type == "table" and len(node.get("content", [])) > 50:
            raise ValueError("table row limit exceeded")
        if node_type == "tableRow" and len(node.get("content", [])) > 20:
            raise ValueError("table column limit exceeded")
        for child in node.get("content", []):
            walk(child)

    walk(content)
    if blocks > 5000 or images > 20:
        raise ValueError("document limit exceeded")
    if len(comments) > 200 or any(comment.block_id not in seen for comment in comments):
        raise ValueError("invalid comment target or count")
    return content


def _validate_imported_document(
    content: dict[str, Any], settings: DocumentSettings, report: DocumentImportReport
) -> None:
    validate_content(content, settings, [])
    canonical = json.dumps(
        {
            "content": content,
            "settings": settings.model_dump(mode="json"),
            "comments": [],
            "import_report": report.model_dump(mode="json"),
        },
        separators=(",", ":"),
    ).encode()
    if len(canonical) > MAX_BYTES:
        raise ValueError("canonical document and import report exceed 8 MB")


class CreateBlankRequest(StrictModel):
    mode: Literal["blank"]
    document_key: DocumentKey
    idempotency_key: str = Field(min_length=1, max_length=128)


class CreateRegisteredImportRequest(StrictModel):
    mode: Literal["import_registered_artifact"]
    document_key: DocumentKey
    source_artifact_id: str = Field(pattern=r"^art_[A-Za-z0-9_-]{16,80}$")
    content: dict[str, Any]
    settings: DocumentSettings
    import_report: DocumentImportReport
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_document(self) -> CreateRegisteredImportRequest:
        _validate_imported_document(self.content, self.settings, self.import_report)
        return self


class CreateExternalImportRequest(StrictModel):
    mode: Literal["import_external_docx"]
    document_key: DocumentKey
    source_filename: str = Field(min_length=1, max_length=255)
    source_base64: str = Field(min_length=1, max_length=28_000_000)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: dict[str, Any]
    settings: DocumentSettings
    import_report: DocumentImportReport
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("source_filename")
    @classmethod
    def plain_docx_filename(cls, value: str) -> str:
        if (
            value != value.strip()
            or value in {".", ".."}
            or Path(value).name != value
            or "/" in value
            or "\\" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or len(value.encode("utf-8")) > 255
            or not value.casefold().endswith(".docx")
        ):
            raise ValueError("source filename must be a plain DOCX filename")
        return value

    def source_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.source_base64, validate=True)
        except ValueError as error:
            raise ValueError("source_base64 is not valid base64") from error

    @model_validator(mode="after")
    def validate_import(self) -> CreateExternalImportRequest:
        source = self.source_bytes()
        if not source or len(source) > 20_000_000 or not source.startswith(b"PK"):
            raise ValueError("external DOCX bytes are invalid or exceed 20 MB")
        if sha256(source).hexdigest() != self.source_sha256:
            raise ValueError("external DOCX SHA-256 does not match")
        _validate_imported_document(self.content, self.settings, self.import_report)
        return self


CreateEditableDocumentRequest = Annotated[
    CreateBlankRequest | CreateRegisteredImportRequest | CreateExternalImportRequest,
    Field(discriminator="mode"),
]


class SaveEditableDocumentRequest(StrictModel):
    base_revision: int = Field(ge=1)
    content: dict[str, Any]
    settings: DocumentSettings
    comments: list[DocumentComment] = Field(max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_document(self) -> SaveEditableDocumentRequest:
        validate_content(self.content, self.settings, self.comments)
        return self


class CreateSnapshotRequest(StrictModel):
    base_revision: int = Field(ge=1)
    reason: Literal["manual"] = "manual"
    label: str = Field(min_length=1, max_length=120)
    origin: Literal["user", "mcp"] = "user"
    idempotency_key: str = Field(min_length=1, max_length=128)


ImportSourceRequest = Annotated[
    CreateRegisteredImportRequest | CreateExternalImportRequest,
    Field(discriminator="mode"),
]


class ReplaceFromDocxRequest(StrictModel):
    base_revision: int = Field(ge=1)
    source: ImportSourceRequest
    idempotency_key: str = Field(min_length=1, max_length=128)


class RestoreSnapshotRequest(StrictModel):
    base_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class PublishEditableDocumentRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    docx_filename: str = Field(min_length=1, max_length=255)
    docx_base64: str = Field(min_length=1, max_length=28_000_000)
    docx_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_filename: str = Field(min_length=1, max_length=255)
    pdf_base64: str = Field(min_length=1, max_length=28_000_000)
    pdf_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_payloads(self) -> PublishEditableDocumentRequest:
        for filename, encoded, expected_hash, suffix, magic in (
            (self.docx_filename, self.docx_base64, self.docx_sha256, ".docx", b"PK"),
            (self.pdf_filename, self.pdf_base64, self.pdf_sha256, ".pdf", b"%PDF-"),
        ):
            if Path(filename).name != filename or not filename.casefold().endswith(suffix):
                raise ValueError(f"{suffix} filename must not contain a path")
            try:
                payload = base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise ValueError(f"{suffix} payload is not valid base64") from error
            if not payload or len(payload) > 20_000_000 or not payload.startswith(magic):
                raise ValueError(f"{suffix} payload is invalid or exceeds 20 MB")
            if sha256(payload).hexdigest() != expected_hash:
                raise ValueError(f"{suffix} SHA-256 does not match")
        return self


class ReplaceBlockText(StrictModel):
    type: Literal["replace_block_text"]
    block_id: str = Field(pattern=NODE_ID.pattern)
    expected_text: str = Field(max_length=20000)
    replacement_text: str = Field(max_length=20000)


class InsertBlockAfter(StrictModel):
    type: Literal["insert_block_after"]
    after_block_id: str = Field(pattern=NODE_ID.pattern)
    node_type: Literal["paragraph", "listItem"]
    semantic_role: SemanticRole
    text: str = Field(max_length=20000)


class DeleteBlock(StrictModel):
    type: Literal["delete_block"]
    block_id: str = Field(pattern=NODE_ID.pattern)
    expected_text: str = Field(max_length=20000)


class MoveBlockAfter(StrictModel):
    type: Literal["move_block_after"]
    block_id: str = Field(pattern=NODE_ID.pattern)
    after_block_id: str = Field(pattern=NODE_ID.pattern)


class SetBlockRole(StrictModel):
    type: Literal["set_block_role"]
    block_id: str = Field(pattern=NODE_ID.pattern)
    semantic_role: SemanticRole


JobHunterOperation = Annotated[
    ReplaceBlockText | InsertBlockAfter | DeleteBlock | MoveBlockAfter | SetBlockRole,
    Field(discriminator="type"),
]


class ApplyOperationsRequest(StrictModel):
    base_revision: int = Field(ge=1)
    operations: list[JobHunterOperation] = Field(min_length=1, max_length=50)
    origin: Literal["user", "mcp"] = "user"
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounded_replacements(self) -> ApplyOperationsRequest:
        total = sum(
            len(getattr(operation, "replacement_text", "")) + len(getattr(operation, "text", ""))
            for operation in self.operations
        )
        if total > 20_000:
            raise ValueError("operation text exceeds 20,000 characters")
        return self


class EditableDocument(StrictModel):
    schema_version: Literal[1]
    document_id: str = Field(pattern=DOC_ID.pattern)
    job_id: str
    document_key: DocumentKey
    document_label: Literal["Resume", "Cover Letter", "References"]
    revision: int = Field(ge=1)
    content: dict[str, Any]
    settings: DocumentSettings
    comments: list[DocumentComment]
    source_artifact_id: str | None
    source_filename: str | None
    source_sha256: str | None
    published_revision: int | None
    import_report: DocumentImportReport
    created_at: str
    updated_at: str


class EditableDocumentSummary(StrictModel):
    document_id: str
    job_id: str
    document_key: DocumentKey
    document_label: str
    revision: int
    source_artifact_id: str | None
    published_revision: int | None
    created_at: str
    updated_at: str


class EditableDocumentList(StrictModel):
    documents: list[EditableDocumentSummary]


class EditableDocumentSnapshot(StrictModel):
    snapshot_id: str = Field(pattern=SNAP_ID.pattern)
    document_id: str
    document_revision: int
    reason: SnapshotReason
    actor: DocumentActor
    label: str | None
    created_at: str


class EditableDocumentSnapshotList(StrictModel):
    snapshots: list[EditableDocumentSnapshot]


class OperationChange(StrictModel):
    block_id: str
    before: str
    after: str


class OperationReceipt(StrictModel):
    document: EditableDocument
    changed_block_ids: list[str]
    changes: list[OperationChange]
    snapshot_id: str


class SemanticOutlineBlock(StrictModel):
    block_id: str
    parent_section_id: str | None
    node_type: str
    semantic_role: SemanticRole | None
    locked: bool
    text: str


class DocumentDraftOutline(StrictModel):
    document_id: str
    document_key: DocumentKey
    document_label: str
    revision: int
    settings: DocumentSettings
    outline: list[SemanticOutlineBlock]
    unresolved_suggestion_count: int
    comment_count: int


def as_document(value: dict[str, object]) -> EditableDocument:
    return EditableDocument.model_validate(value)


def as_summary(value: dict[str, object]) -> EditableDocumentSummary:
    return EditableDocumentSummary.model_validate(
        {
            "document_id": value["document_id"],
            "job_id": value["job_id"],
            "document_key": value["document_key"],
            "document_label": value["document_label"],
            "revision": value["revision"],
            "source_artifact_id": value["source_artifact_id"],
            "published_revision": value["published_revision"],
            "created_at": value["created_at"],
            "updated_at": value["updated_at"],
        }
    )

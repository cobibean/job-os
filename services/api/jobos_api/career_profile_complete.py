from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import sqlite3
import stat
from contextlib import suppress
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .career_profile import (
    PROFILE_ID,
    WORK_ARRANGEMENT_NAMESPACE,
    CareerProfileErasureInProgress,
    CareerProfileIdempotencyConflict,
    CareerProfileRevisionConflict,
    IdempotencyKey,
    ensure_no_pending_erasure,
)
from .sqlite_connection import connect_sqlite

OpaqueProfileItemId = Annotated[str, Field(pattern=r"^cp[ir]_[A-Za-z0-9_-]{16,64}$")]
CompleteProfileItemId = Annotated[str, Field(pattern=r"^cpi_[A-Za-z0-9_-]{16,64}$")]
OpaqueEvidenceId = Annotated[str, Field(pattern=r"^cpe_[A-Za-z0-9_-]{16,64}$")]
Sha256Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MediaType = Annotated[
    str,
    Field(
        min_length=3,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$",
    ),
]
DisplayFilename = Annotated[
    str, Field(min_length=1, max_length=500, pattern=r"^[^\x00-\x1f\x7f]+$")
]
ListLabel = Annotated[str, Field(min_length=1, max_length=300)]
ConstraintText = Annotated[str, Field(min_length=1, max_length=2000)]
LinkText = Annotated[str, Field(min_length=1, max_length=2000)]
CustomEnumText = Annotated[str, Field(min_length=1, max_length=100)]
AdditionalContextText = Annotated[str, Field(min_length=1, max_length=1000)]


def _validate_timestamp(value: str) -> str:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must use ISO-8601 format") from error
    return value


def _validate_aware_timestamp(value: str) -> str:
    _validate_timestamp(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


def _validate_career_date(value: str) -> str:
    try:
        if re.fullmatch(r"\d{4}", value):
            datetime.strptime(value, "%Y")
        elif re.fullmatch(r"\d{4}-\d{2}", value):
            datetime.strptime(value, "%Y-%m")
        else:
            date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("career date must use YYYY, YYYY-MM, or YYYY-MM-DD format") from error
    return value


def _validate_capture_time(value: str) -> str:
    if re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return _validate_career_date(value)
    return _validate_aware_timestamp(value)


TimestampText = Annotated[
    str,
    Field(min_length=1, max_length=64),
    AfterValidator(_validate_timestamp),
]
CareerDateText = Annotated[
    str,
    Field(min_length=4, max_length=10, pattern=r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$"),
    AfterValidator(_validate_career_date),
]
CaptureTimeText = Annotated[
    str,
    Field(min_length=4, max_length=64),
    AfterValidator(_validate_capture_time),
]
ReviewStatus = Literal["accepted", "proposed", "conflicting"]
ImportAssessment = Literal["exact", "inferred", "ambiguous", "conflicting"]
MutationSource = Literal[
    "direct_user",
    "authenticated_user_instruction",
    "deterministic_source_mapping",
    "agent_inference",
]
HistoryActorKind = Literal[
    "direct_user",
    "authenticated_user_instruction",
    "deterministic_source_mapping",
    "autonomous_agent",
    "user_proposal_decision",
]


def _history_actor_kind(mutation_source: MutationSource) -> HistoryActorKind:
    if mutation_source == "agent_inference":
        return "autonomous_agent"
    return mutation_source

PreferenceStrength = (
    Literal["requirement", "strong_preference", "preference", "dealbreaker"]
    | CustomEnumText
)
PositivePreferenceStrength = (
    Literal["requirement", "strong_preference", "preference"] | CustomEnumText
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityValue(StrictModel):
    kind: Literal["identity"]
    professional_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    phone: str | None = Field(default=None, min_length=1, max_length=100)
    city: str | None = Field(default=None, min_length=1, max_length=200)
    links: list[LinkText] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> IdentityValue:
        if not any((self.professional_name, self.email, self.phone, self.city, self.links)):
            raise ValueError("identity requires at least one meaningful field")
        return self


class EducationValue(StrictModel):
    kind: Literal["education"]
    institution: str | None = Field(default=None, min_length=1, max_length=300)
    credential: str | None = Field(default=None, min_length=1, max_length=300)
    field_of_study: str | None = Field(default=None, min_length=1, max_length=300)
    started_on: CareerDateText | None = None
    ended_on: CareerDateText | None = None
    details: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> EducationValue:
        if not any(
            (
                self.institution,
                self.credential,
                self.field_of_study,
                self.started_on,
                self.ended_on,
                self.details,
            )
        ):
            raise ValueError("education requires at least one meaningful field")
        return self


class SkillValue(StrictModel):
    kind: Literal["skill"]
    name: str | None = Field(default=None, min_length=1, max_length=200)
    level: (Literal["familiar", "proficient", "advanced", "expert"] | CustomEnumText) | None = None
    note: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> SkillValue:
        if not any((self.name, self.level, self.note)):
            raise ValueError("skill requires at least one meaningful field")
        return self


class PositioningValue(StrictModel):
    kind: Literal["positioning"]
    headline: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> PositioningValue:
        if not any((self.headline, self.summary)):
            raise ValueError("positioning requires at least one meaningful field")
        return self


class ExperienceValue(StrictModel):
    kind: Literal["experience"]
    organization: str | None = Field(default=None, min_length=1, max_length=300)
    role: str | None = Field(default=None, min_length=1, max_length=300)
    location: str | None = Field(default=None, min_length=1, max_length=300)
    started_on: CareerDateText | None = None
    ended_on: CareerDateText | None = None
    current: bool | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> ExperienceValue:
        if not any(
            (
                self.organization,
                self.role,
                self.location,
                self.started_on,
                self.ended_on,
                self.current is not None,
                self.summary,
            )
        ):
            raise ValueError("experience requires at least one meaningful field")
        return self


class ProjectValue(StrictModel):
    kind: Literal["project"]
    name: str | None = Field(default=None, min_length=1, max_length=300)
    role: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = Field(default=None, min_length=1, max_length=4000)
    url: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> ProjectValue:
        if not any((self.name, self.role, self.summary, self.url)):
            raise ValueError("project requires at least one meaningful field")
        return self


class ClaimValue(StrictModel):
    kind: Literal["claim"]
    statement: str | None = Field(default=None, min_length=1, max_length=2000)
    qualifiers: list[ConstraintText] = Field(default_factory=list, max_length=20)
    forbidden_uses: list[ConstraintText] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> ClaimValue:
        if not any((self.statement, self.qualifiers, self.forbidden_uses)):
            raise ValueError("claim requires at least one meaningful field")
        return self


class TargetRolesValue(StrictModel):
    kind: Literal["target_roles"]
    roles: list[ListLabel] = Field(default_factory=list, max_length=50)
    strength: PositivePreferenceStrength | None = None

    @model_validator(mode="after")
    def require_meaningful_field(self) -> TargetRolesValue:
        if not self.roles:
            raise ValueError("target roles requires at least one role")
        return self


class CompensationValue(StrictModel):
    kind: Literal["compensation"]
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    minimum: int | None = Field(default=None, ge=0)
    target: int | None = Field(default=None, ge=0)
    period: (Literal["hour", "year"] | CustomEnumText) | None = None
    note: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> CompensationValue:
        if not any(
            (
                self.currency,
                self.minimum is not None,
                self.target is not None,
                self.period,
                self.note,
            )
        ):
            raise ValueError("compensation requires at least one meaningful field")
        return self


class LocationPreferenceValue(StrictModel):
    kind: Literal["location"]
    locations: list[ListLabel] = Field(default_factory=list, max_length=50)
    relocation: (Literal["yes", "no", "consider"] | CustomEnumText) | None = None
    strength: PreferenceStrength | None = None

    @model_validator(mode="after")
    def require_meaningful_field(self) -> LocationPreferenceValue:
        if not any((self.locations, self.relocation)):
            raise ValueError("location requires a location or relocation preference")
        return self


class WorkArrangementProfileValue(StrictModel):
    kind: Literal["work_arrangement"]
    mode: Literal["remote", "hybrid", "onsite", "flexible"] | CustomEnumText
    strength: PreferenceStrength | None = None
    note: AdditionalContextText | None = None


class IndustryPreferencesValue(StrictModel):
    kind: Literal["industries"]
    industries: list[ListLabel] = Field(default_factory=list, max_length=50)
    strength: PreferenceStrength | None = None

    @model_validator(mode="after")
    def require_meaningful_field(self) -> IndustryPreferencesValue:
        if not self.industries:
            raise ValueError("industries requires at least one industry")
        return self


class PriorityValue(StrictModel):
    kind: Literal["priority"]
    label: str | None = Field(default=None, min_length=1, max_length=300)
    explanation: str | None = Field(default=None, min_length=1, max_length=2000)
    strength: PositivePreferenceStrength | None = None

    @model_validator(mode="after")
    def require_meaningful_field(self) -> PriorityValue:
        if not any((self.label, self.explanation)):
            raise ValueError("priority requires a label or explanation")
        return self


class DealbreakerValue(StrictModel):
    kind: Literal["dealbreaker"]
    label: str | None = Field(default=None, min_length=1, max_length=300)
    explanation: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_meaningful_field(self) -> DealbreakerValue:
        if not any((self.label, self.explanation)):
            raise ValueError("dealbreaker requires a label or explanation")
        return self


class CustomValue(StrictModel):
    kind: Literal["custom"]
    label: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=4000)


ProfileValue = Annotated[
    IdentityValue
    | EducationValue
    | SkillValue
    | PositioningValue
    | ExperienceValue
    | ProjectValue
    | ClaimValue
    | TargetRolesValue
    | CompensationValue
    | LocationPreferenceValue
    | WorkArrangementProfileValue
    | IndustryPreferencesValue
    | PriorityValue
    | DealbreakerValue
    | CustomValue,
    Field(discriminator="kind"),
]


class ItemProvenance(StrictModel):
    method: Literal[
        "user_entered",
        "agent_generated",
        "agent_edit",
        "evidence_import",
        "evidence_erased",
        "tracer_compatibility",
    ]
    source_label: str | None = Field(default=None, max_length=500)
    imported_at: TimestampText | None = None
    mutation_source: MutationSource


class ProfileItemRecord(StrictModel):
    item_id: OpaqueProfileItemId
    area: Literal["my_career", "what_im_looking_for", "my_evidence"]
    value: ProfileValue
    review_status: ReviewStatus
    evidence_ids: list[OpaqueEvidenceId] = Field(default_factory=list, max_length=100)
    provenance: ItemProvenance
    item_revision: int = Field(ge=1)
    actor_principal: str
    created_at: TimestampText
    updated_at: TimestampText


class EvidenceProvenance(StrictModel):
    source_kind: Literal["resume", "portfolio", "supporting_document", "citation"]
    source_label: str = Field(min_length=1, max_length=500)
    method: Literal["user_import", "agent_import", "migration_import"]


class SourceEvidenceRecord(StrictModel):
    evidence_id: OpaqueEvidenceId
    original_filename: DisplayFilename
    media_type: MediaType
    sha256: Sha256Digest
    byte_count: int = Field(ge=1, le=10 * 1024 * 1024)
    captured_at: CaptureTimeText | None = None
    imported_at: TimestampText
    provenance: EvidenceProvenance
    active: bool


class CareerProfileCompleteCurrent(StrictModel):
    profile_revision: int = Field(ge=0)
    authority_epoch: int = Field(ge=0)
    items: list[ProfileItemRecord]
    source_evidence: list[SourceEvidenceRecord]


class ProfileItemMutation(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    value: ProfileValue
    evidence_ids: list[OpaqueEvidenceId] = Field(default_factory=list, max_length=100)


class ProfileItemRemoval(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey


class ProfileProposalDecision(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    proposal_sha256: Sha256Digest
    decision: Literal["accept", "reject"]


class ProfileIntentGrantRequest(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    expected_authority_epoch: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    operation: Literal[
        "item.create",
        "item.update",
        "item.remove",
        "evidence.remove",
        "proposal.accept",
        "proposal.reject",
    ]
    target_id: str | None = Field(default=None, max_length=80)
    payload: dict[str, object]

    @field_validator("payload")
    @classmethod
    def bound_payload(cls, value: dict[str, object]) -> dict[str, object]:
        if len(_canonical_json(value).encode()) > 64 * 1024:
            raise ValueError("intent grant payload must not exceed 64 KiB")
        return value

    @model_validator(mode="after")
    def target_matches_operation(self) -> Self:
        if self.operation == "item.create":
            if self.target_id is not None:
                raise ValueError("item.create grants cannot name an existing target")
        elif self.operation == "evidence.remove":
            if self.target_id is None or not re.fullmatch(
                r"cpe_[A-Za-z0-9_-]{16,64}", self.target_id
            ):
                raise ValueError("evidence.remove grants require an exact Evidence ID")
        elif self.target_id is None or not re.fullmatch(
            r"cpi_[A-Za-z0-9_-]{16,64}", self.target_id
        ):
            raise ValueError(f"{self.operation} grants require an exact item ID")
        return self


class ProfileIntentGrant(StrictModel):
    grant_id: Annotated[str, Field(pattern=r"^cpg_[A-Za-z0-9_-]{16,64}$")]
    operation: str
    target_id: str | None
    payload_sha256: Sha256Digest
    created_at: TimestampText
class EvidenceErasureRequest(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    confirmation: Literal["ERASE_EVIDENCE_PERMANENTLY"]


class CareerProfileResetRequest(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    confirmation: Literal["RESET_CAREER_PROFILE_PERMANENTLY"]


class CareerProfileErasureResult(StrictModel):
    operation: Literal["evidence_erased", "career_profile_reset"]
    completed: Literal[True] = True


class EvidenceExtraction(StrictModel):
    assessment: ImportAssessment
    value: ProfileValue


class EvidenceImportRequest(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    original_filename: DisplayFilename
    media_type: MediaType
    captured_at: CaptureTimeText | None = None
    provenance: EvidenceProvenance
    content_base64: str = Field(min_length=1, max_length=14 * 1024 * 1024)
    extractions: list[EvidenceExtraction] = Field(default_factory=list, max_length=100)

    @field_validator("content_base64")
    @classmethod
    def validate_content(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("content_base64 must be valid base64") from error
        if not decoded or len(decoded) > 10 * 1024 * 1024:
            raise ValueError("evidence content must be between 1 byte and 10 MiB")
        return value


class CareerProfileEvidencePathError(RuntimeError):
    """Evidence storage escaped its managed regular-file boundary."""


class CareerProfileEvidenceIntegrityError(RuntimeError):
    """Evidence bytes no longer match the immutable imported hash."""


class CareerProfileEvidenceNotFound(RuntimeError):
    """Evidence metadata or bytes do not exist."""


class CareerProfileItemNotFound(RuntimeError):
    """A requested complete-model item does not exist."""


class CareerProfileValueError(RuntimeError):
    """A complete-model mutation is invalid for the current profile."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _opaque_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(18)}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _request_hash(command: str, payload: object) -> str:
    return sha256(_canonical_json({"command": command, "payload": payload}).encode()).hexdigest()


def _actor_bound_request_hash(
    command: str,
    payload: object,
    mutation_source: MutationSource,
    intent_grant_id: str | None,
) -> str:
    """Bind idempotent replay to the authority used for the original call."""
    return _request_hash(
        command,
        {
            "mutation_source": mutation_source,
            "intent_grant_id": intent_grant_id,
            "payload": payload,
        },
    )


def proposal_sha256(item: ProfileItemRecord) -> str:
    """Bind a decision to every immutable byte of the currently stored proposal."""
    return sha256(_canonical_json(item.model_dump(mode="json")).encode()).hexdigest()


def _area_for_kind(kind: str) -> Literal["my_career", "what_im_looking_for", "my_evidence"]:
    if kind in {
        "identity",
        "education",
        "skill",
        "positioning",
        "experience",
        "project",
        "claim",
        "custom",
    }:
        return "my_career"
    return "what_im_looking_for"


class EvidenceVault:
    def __init__(self, root: Path) -> None:
        self.root = root

    def initialize(self) -> None:
        with suppress(FileExistsError):
            self.root.mkdir(parents=True, mode=0o700)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(self.root, flags)
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise CareerProfileEvidencePathError("Evidence vault must be a regular directory")
            os.fchmod(descriptor, 0o700)
        except OSError as error:
            raise CareerProfileEvidencePathError(
                "Evidence vault could not be opened without following links"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def storage_name(evidence_id: str) -> str:
        if not re.fullmatch(r"cpe_[A-Za-z0-9_-]{16,64}", evidence_id):
            raise CareerProfileEvidencePathError("Evidence identifier is invalid")
        return f"{evidence_id}.bin"

    @staticmethod
    def _validate_storage_name(storage_name: str) -> None:
        if not re.fullmatch(r"cpe_[A-Za-z0-9_-]{16,64}\.bin", storage_name):
            raise CareerProfileEvidencePathError("Evidence storage name is invalid")

    def _open_root(self) -> int:
        self.initialize()
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            return os.open(self.root, flags)
        except OSError as error:
            raise CareerProfileEvidencePathError(
                "Evidence vault could not be opened without following links"
            ) from error

    def write(self, evidence_id: str, content: bytes) -> str:
        storage_name = self.storage_name(evidence_id)
        root_descriptor = self._open_root()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(storage_name, flags, 0o600, dir_fd=root_descriptor)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(storage_name, 0o600, dir_fd=root_descriptor, follow_symlinks=False)
            os.fsync(root_descriptor)
        except Exception as error:
            if descriptor is not None:
                os.close(descriptor)
            with suppress(OSError):
                os.unlink(storage_name, dir_fd=root_descriptor)
            if isinstance(error, OSError):
                raise CareerProfileEvidencePathError(
                    "Evidence could not be written inside its managed vault"
                ) from error
            raise
        finally:
            os.close(root_descriptor)
        return storage_name

    def read(self, storage_name: str, expected_hash: str) -> bytes:
        self._validate_storage_name(storage_name)
        root_descriptor = self._open_root()
        flags = os.O_RDONLY
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            metadata = os.stat(storage_name, dir_fd=root_descriptor, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise CareerProfileEvidencePathError("Evidence must be a regular file")
            descriptor = os.open(storage_name, flags, dir_fd=root_descriptor)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise CareerProfileEvidencePathError("Evidence must be a regular file")
            with os.fdopen(descriptor, "rb") as source:
                descriptor = None
                content = source.read(10 * 1024 * 1024 + 1)
        except FileNotFoundError as error:
            raise CareerProfileEvidenceNotFound from error
        except OSError as error:
            raise CareerProfileEvidencePathError(
                "Evidence could not be opened without following links"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(root_descriptor)
        if len(content) > 10 * 1024 * 1024 or not secrets.compare_digest(
            sha256(content).hexdigest(), expected_hash
        ):
            raise CareerProfileEvidenceIntegrityError
        return content

    def discard_uncommitted(self, storage_name: str) -> None:
        with suppress(CareerProfileEvidencePathError, OSError):
            self._validate_storage_name(storage_name)
            root_descriptor = self._open_root()
            try:
                os.unlink(storage_name, dir_fd=root_descriptor)
            finally:
                os.close(root_descriptor)

    def erase(self, storage_name: str) -> None:
        """Permanently unlink one managed regular file and durably sync the vault."""
        self._validate_storage_name(storage_name)
        root_descriptor = self._open_root()
        try:
            try:
                metadata = os.stat(storage_name, dir_fd=root_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISREG(metadata.st_mode):
                raise CareerProfileEvidencePathError("Evidence to erase must be a regular file")
            os.unlink(storage_name, dir_fd=root_descriptor)
            os.fsync(root_descriptor)
        except OSError as error:
            raise CareerProfileEvidencePathError(
                "Evidence could not be permanently erased from its managed vault"
            ) from error
        finally:
            os.close(root_descriptor)


class CareerProfileCompleteStore:
    def __init__(self, database: Path, evidence_root: Path) -> None:
        self.database = database
        self.vault = EvidenceVault(evidence_root)

    def initialize(self) -> None:
        self.vault.initialize()
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO career_profiles(profile_id) VALUES (?)", (PROFILE_ID,)
            )
            connection.commit()
        self.recover_pending_erasures()

    def current(self) -> CareerProfileCompleteCurrent:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            return self._current_in_connection(connection)

    def create_intent_grant(
        self, *, principal: str, command: ProfileIntentGrantRequest
    ) -> ProfileIntentGrant:
        if command.operation in {"item.create", "item.update"}:
            normalized_payload = ProfileItemMutation.model_validate(command.payload).model_dump(
                mode="json"
            )
        elif command.operation in {"item.remove", "evidence.remove"}:
            normalized_payload = ProfileItemRemoval.model_validate(command.payload).model_dump(
                mode="json"
            )
        else:
            normalized_payload = ProfileProposalDecision.model_validate(command.payload).model_dump(
                mode="json"
            )
        if normalized_payload["expected_profile_revision"] != command.expected_profile_revision:
            raise CareerProfileValueError(
                "Intent grant payload revision must match the grant request revision"
            )
        payload_sha = sha256(_canonical_json(normalized_payload).encode()).hexdigest()
        request_hash = _request_hash(
            "intent_grant.create",
            command.model_dump(mode="json"),
        )
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            ensure_no_pending_erasure(connection)
            replay = connection.execute(
                "SELECT request_hash, result_json FROM career_profile_complete_idempotency "
                "WHERE actor_principal = ? AND idempotency_key = ?",
                (principal, command.idempotency_key),
            ).fetchone()
            if replay is not None:
                if not secrets.compare_digest(str(replay[0]), request_hash):
                    connection.rollback()
                    raise CareerProfileIdempotencyConflict
                result = ProfileIntentGrant.model_validate_json(str(replay[1]))
                connection.rollback()
                return result
            profile_state = connection.execute(
                "SELECT authority_epoch FROM career_profiles WHERE profile_id = ?",
                (PROFILE_ID,),
            ).fetchone()
            if profile_state is None:
                raise RuntimeError("Career Profile storage is not initialized")
            if int(profile_state[0]) != command.expected_authority_epoch:
                connection.rollback()
                raise CareerProfileValueError("Career Profile authority epoch has changed")
            self._check_head(connection, command.expected_profile_revision)
            grant_id = _opaque_id("cpg_")
            created_at = _now()
            connection.execute(
                "INSERT INTO career_profile_intent_grants("
                "grant_id, created_by_principal, operation, target_id, payload_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    grant_id,
                    principal,
                    command.operation,
                    command.target_id,
                    payload_sha,
                    created_at,
                ),
            )
            result = ProfileIntentGrant(
                grant_id=grant_id,
                operation=command.operation,
                target_id=command.target_id,
                payload_sha256=payload_sha,
                created_at=created_at,
            )
            connection.execute(
                "INSERT INTO career_profile_complete_idempotency("
                "actor_principal, idempotency_key, request_hash, result_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    principal,
                    command.idempotency_key,
                    request_hash,
                    _canonical_json(result.model_dump(mode="json")),
                ),
            )
            connection.commit()
        return result

    def upsert_item(
        self,
        *,
        principal: str,
        command: ProfileItemMutation,
        item_id: str | None = None,
        mutation_source: Literal[
            "direct_user", "agent_inference", "authenticated_user_instruction"
        ] = "direct_user",
        intent_grant_id: str | None = None,
        allow_agent_direct: bool = False,
        reason: str | None = None,
    ) -> CareerProfileCompleteCurrent:
        if item_id is not None and not re.fullmatch(r"cpi_[A-Za-z0-9_-]{16,64}", item_id):
            raise CareerProfileValueError(
                "Compatibility tracer items must use their dedicated staging endpoint"
            )
        if command.value.kind == "work_arrangement":
            raise CareerProfileValueError(
                "Work arrangement remains on the staging tracer endpoint until consumer cutover"
            )
        payload = command.model_dump(mode="json")
        if item_id is not None:
            payload["item_id"] = item_id
        resolved_item_id = item_id or _opaque_id("cpi_")
        return self._mutate_item(
            principal=principal,
            idempotency_key=command.idempotency_key,
            request_hash=_actor_bound_request_hash(
                "item.upsert", payload, mutation_source, intent_grant_id
            ),
            expected_revision=command.expected_profile_revision,
            item_id=resolved_item_id,
            value=command.value,
            evidence_ids=command.evidence_ids,
            require_existing=item_id is not None,
            mutation_source=mutation_source,
            intent_grant_id=intent_grant_id,
            operation="item.update" if item_id is not None else "item.create",
            grant_payload=command.model_dump(mode="json"),
            allow_agent_direct=allow_agent_direct,
            reason=reason,
        )

    def remove_item(
        self,
        *,
        principal: str,
        item_id: str,
        command: ProfileItemRemoval,
        mutation_source: Literal[
            "direct_user", "agent_inference", "authenticated_user_instruction"
        ] = "direct_user",
        intent_grant_id: str | None = None,
    ) -> CareerProfileCompleteCurrent:
        if not re.fullmatch(r"cpi_[A-Za-z0-9_-]{16,64}", item_id):
            raise CareerProfileValueError(
                "Compatibility tracer items must use their dedicated staging endpoint"
            )
        request_hash = _actor_bound_request_hash(
            "item.remove",
            command.model_dump(mode="json") | {"item_id": item_id},
            mutation_source,
            intent_grant_id,
        )
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay(connection, principal, command.idempotency_key, request_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                head = self._check_head(connection, command.expected_profile_revision)
                self._authorize_actor(
                    connection,
                    principal=principal,
                    mutation_source=mutation_source,
                    intent_grant_id=intent_grant_id,
                    operation="item.remove",
                    target_id=item_id,
                    payload=command.model_dump(mode="json"),
                )
                row = connection.execute(
                    "SELECT value_json, provenance_json, review_status, evidence_ids_json, "
                    "item_revision, actor_principal, created_at, updated_at "
                    "FROM career_profile_items WHERE item_id = ? AND active = 1",
                    (item_id,),
                ).fetchone()
                if row is None:
                    raise CareerProfileItemNotFound
                before = self._item_from_row((item_id, *row))
                revision = head + 1
                connection.execute(
                    "UPDATE career_profile_items SET active = 0, "
                    "item_revision = item_revision + 1, "
                    "actor_principal = ?, updated_at = ? WHERE item_id = ?",
                    (principal, _now(), item_id),
                )
                self._record_revision(
                    connection,
                    revision=revision,
                    base_revision=head,
                    principal=principal,
                    actor_kind=_history_actor_kind(mutation_source),
                    operation="item.remove",
                    item_id=item_id,
                    evidence_id=None,
                    before=before.model_dump(mode="json"),
                    after=None,
                    affected=[f"items.{item_id}"],
                )
                result = self._finish(connection, principal, command.idempotency_key, request_hash)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def import_evidence(
        self,
        *,
        principal: str,
        command: EvidenceImportRequest,
        mutation_source: Literal[
            "direct_user", "agent_inference", "deterministic_source_mapping"
        ] = "direct_user",
    ) -> CareerProfileCompleteCurrent:
        request_hash = _actor_bound_request_hash(
            "evidence.import",
            command.model_dump(mode="json", exclude={"content_base64"})
            | {"content_sha256": sha256(base64.b64decode(command.content_base64)).hexdigest()},
            mutation_source,
            None,
        )
        content = base64.b64decode(command.content_base64, validate=True)
        storage_name: str | None = None
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay(connection, principal, command.idempotency_key, request_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                head = self._check_head(connection, command.expected_profile_revision)
                if any(item.value.kind == "work_arrangement" for item in command.extractions):
                    raise CareerProfileValueError(
                        "Imported work arrangement stays proposed for the later migration candidate"
                    )
                evidence_id = _opaque_id("cpe_")
                storage_name = self.vault.write(evidence_id, content)
                imported_at = _now()
                content_hash = sha256(content).hexdigest()
                provenance_method = (
                    "agent_import"
                    if mutation_source == "agent_inference"
                    else "user_import"
                    if mutation_source == "direct_user"
                    else command.provenance.method
                )
                evidence_provenance = command.provenance.model_copy(
                    update={"method": provenance_method}
                )
                connection.execute(
                    "INSERT INTO career_profile_evidence("
                    "evidence_id, original_filename, media_type, "
                    "content_sha256, byte_count, captured_at, imported_at, provenance_json, "
                    "storage_name, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (
                        evidence_id,
                        command.original_filename,
                        command.media_type,
                        content_hash,
                        len(content),
                        command.captured_at,
                        imported_at,
                        _canonical_json(evidence_provenance.model_dump(mode="json")),
                        storage_name,
                    ),
                )
                created_items: list[ProfileItemRecord] = []
                for extraction in command.extractions:
                    item_id = _opaque_id("cpi_")
                    review_status: ReviewStatus = (
                        "accepted"
                        if mutation_source == "deterministic_source_mapping"
                        and extraction.assessment == "exact"
                        else "conflicting"
                        if extraction.assessment == "conflicting"
                        else "proposed"
                    )
                    provenance = ItemProvenance(
                        method="evidence_import",
                        source_label=evidence_provenance.source_label,
                        imported_at=imported_at,
                        mutation_source=mutation_source,
                    )
                    connection.execute(
                        "INSERT INTO career_profile_items(item_id, value_json, provenance_json, "
                        "review_status, evidence_ids_json, item_revision, actor_principal, active, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, 1, ?, ?)",
                        (
                            item_id,
                            _canonical_json(extraction.value.model_dump(mode="json")),
                            _canonical_json(provenance.model_dump(mode="json")),
                            review_status,
                            _canonical_json([evidence_id]),
                            principal,
                            imported_at,
                            imported_at,
                        ),
                    )
                    created_row = connection.execute(
                        "SELECT value_json, provenance_json, review_status, evidence_ids_json, "
                        "item_revision, actor_principal, created_at, updated_at "
                        "FROM career_profile_items WHERE item_id = ?",
                        (item_id,),
                    ).fetchone()
                    assert created_row is not None
                    created_items.append(self._item_from_row((item_id, *created_row)))
                revision = head + 1
                evidence_row = connection.execute(
                    "SELECT original_filename, media_type, content_sha256, byte_count, "
                    "captured_at, "
                    "imported_at, provenance_json, active FROM career_profile_evidence "
                    "WHERE evidence_id = ?",
                    (evidence_id,),
                ).fetchone()
                assert evidence_row is not None
                imported_evidence = self._evidence_from_row((evidence_id, *evidence_row))
                self._record_revision(
                    connection,
                    revision=revision,
                    base_revision=head,
                    principal=principal,
                    actor_kind=_history_actor_kind(mutation_source),
                    operation="evidence.import",
                    item_id=None,
                    evidence_id=evidence_id,
                    before=None,
                    after={
                        "source_evidence": imported_evidence.model_dump(mode="json"),
                        "items": [item.model_dump(mode="json") for item in created_items],
                    },
                    affected=[
                        "source_evidence",
                        *[f"items.{item.item_id}" for item in created_items],
                    ],
                )
                result = self._finish(connection, principal, command.idempotency_key, request_hash)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                if storage_name is not None:
                    self.vault.discard_uncommitted(storage_name)
                raise

    def remove_evidence(
        self,
        *,
        principal: str,
        evidence_id: str,
        command: ProfileItemRemoval,
        mutation_source: Literal[
            "direct_user", "agent_inference", "authenticated_user_instruction"
        ] = "direct_user",
        intent_grant_id: str | None = None,
    ) -> CareerProfileCompleteCurrent:
        request_hash = _actor_bound_request_hash(
            "evidence.remove",
            command.model_dump(mode="json") | {"evidence_id": evidence_id},
            mutation_source,
            intent_grant_id,
        )
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay(connection, principal, command.idempotency_key, request_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                head = self._check_head(connection, command.expected_profile_revision)
                self._authorize_actor(
                    connection,
                    principal=principal,
                    mutation_source=mutation_source,
                    intent_grant_id=intent_grant_id,
                    operation="evidence.remove",
                    target_id=evidence_id,
                    payload=command.model_dump(mode="json"),
                )
                row = connection.execute(
                    "SELECT original_filename, media_type, content_sha256, "
                    "byte_count, captured_at, "
                    "imported_at, provenance_json, active FROM career_profile_evidence "
                    "WHERE evidence_id = ?",
                    (evidence_id,),
                ).fetchone()
                if row is None or not bool(row[7]):
                    raise CareerProfileEvidenceNotFound
                before = self._evidence_from_row((evidence_id, *row))
                connection.execute(
                    "UPDATE career_profile_evidence SET active = 0 WHERE evidence_id = ?",
                    (evidence_id,),
                )
                linked = connection.execute(
                    "SELECT item_id, value_json, provenance_json, review_status, "
                    "evidence_ids_json, "
                    "item_revision, actor_principal, created_at, updated_at "
                    "FROM career_profile_items WHERE active = 1"
                ).fetchall()
                affected = ["source_evidence"]
                linked_before: list[ProfileItemRecord] = []
                linked_after: list[ProfileItemRecord] = []
                for linked_row in linked:
                    item_before = self._item_from_row(linked_row)
                    if evidence_id not in item_before.evidence_ids:
                        continue
                    linked_before.append(item_before)
                    # Evidence supports provenance; it does not control whether a user-owned
                    # Career Profile entry remains accepted or active.
                    linked_after.append(item_before)
                revision = head + 1
                inactive_evidence = before.model_copy(update={"active": False})
                self._record_revision(
                    connection,
                    revision=revision,
                    base_revision=head,
                    principal=principal,
                    actor_kind=_history_actor_kind(mutation_source),
                    operation="evidence.remove",
                    item_id=None,
                    evidence_id=evidence_id,
                    before={
                        "source_evidence": before.model_dump(mode="json"),
                        "linked_items": [item.model_dump(mode="json") for item in linked_before],
                    },
                    after={
                        "source_evidence": inactive_evidence.model_dump(mode="json"),
                        "linked_items": [item.model_dump(mode="json") for item in linked_after],
                    },
                    affected=affected,
                )
                result = self._finish(connection, principal, command.idempotency_key, request_hash)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def read_evidence(self, evidence_id: str) -> bytes:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT storage_name, content_sha256 FROM career_profile_evidence "
                "WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            raise CareerProfileEvidenceNotFound
        return self.vault.read(str(row[0]), str(row[1]))

    def evidence_metadata(self, evidence_id: str) -> SourceEvidenceRecord:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT original_filename, media_type, content_sha256, byte_count, captured_at, "
                "imported_at, provenance_json, active FROM career_profile_evidence "
                "WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            raise CareerProfileEvidenceNotFound
        return self._evidence_from_row((evidence_id, *row))

    def decide_proposal(
        self,
        *,
        principal: str,
        item_id: str,
        command: ProfileProposalDecision,
        mutation_source: Literal[
            "direct_user", "agent_inference", "authenticated_user_instruction"
        ] = "direct_user",
        intent_grant_id: str | None = None,
    ) -> CareerProfileCompleteCurrent:
        operation = f"proposal.{command.decision}"
        payload = command.model_dump(mode="json")
        request_hash = _actor_bound_request_hash(
            operation, payload | {"item_id": item_id}, mutation_source, intent_grant_id
        )
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay(connection, principal, command.idempotency_key, request_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                head = self._check_head(connection, command.expected_profile_revision)
                self._authorize_actor(
                    connection,
                    principal=principal,
                    mutation_source=mutation_source,
                    intent_grant_id=intent_grant_id,
                    operation=operation,
                    target_id=item_id,
                    payload=payload,
                )
                row = connection.execute(
                    "SELECT value_json, provenance_json, review_status, evidence_ids_json, "
                    "item_revision, actor_principal, created_at, updated_at "
                    "FROM career_profile_items WHERE item_id = ? AND active = 1",
                    (item_id,),
                ).fetchone()
                if row is None:
                    raise CareerProfileItemNotFound
                before = self._item_from_row((item_id, *row))
                if before.review_status not in {"proposed", "conflicting"}:
                    raise CareerProfileValueError("Only an exact pending proposal can be decided")
                if not secrets.compare_digest(proposal_sha256(before), command.proposal_sha256):
                    raise CareerProfileValueError(
                        "Proposal payload changed; regenerate the decision"
                    )
                timestamp = _now()
                if command.decision == "accept":
                    connection.execute(
                        "UPDATE career_profile_items SET review_status = 'accepted', "
                        "item_revision = item_revision + 1, updated_at = ? WHERE item_id = ?",
                        (timestamp, item_id),
                    )
                    decided_row = connection.execute(
                        "SELECT value_json, provenance_json, review_status, evidence_ids_json, "
                        "item_revision, actor_principal, created_at, updated_at "
                        "FROM career_profile_items WHERE item_id = ?",
                        (item_id,),
                    ).fetchone()
                    assert decided_row is not None
                    after: object | None = self._item_from_row((item_id, *decided_row)).model_dump(
                        mode="json"
                    )
                    revision_operation = "item.upsert"
                else:
                    connection.execute(
                        "UPDATE career_profile_items SET active = 0, "
                        "item_revision = item_revision + 1, updated_at = ? WHERE item_id = ?",
                        (timestamp, item_id),
                    )
                    after = None
                    revision_operation = "item.remove"
                revision = head + 1
                self._record_revision(
                    connection,
                    revision=revision,
                    base_revision=head,
                    principal=principal,
                    actor_kind="user_proposal_decision",
                    operation=revision_operation,
                    item_id=item_id,
                    evidence_id=None,
                    before=before.model_dump(mode="json"),
                    after=after,
                    affected=[f"items.{item_id}"],
                )
                result = self._finish(connection, principal, command.idempotency_key, request_hash)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
    def erase_evidence(
        self,
        *,
        principal: str,
        evidence_id: str,
        command: EvidenceErasureRequest,
    ) -> CareerProfileErasureResult:
        request_hash = _request_hash(
            "evidence.erase",
            command.model_dump(mode="json") | {"evidence_id": evidence_id},
        )
        replay = self._erasure_receipt(principal, command.idempotency_key, request_hash)
        if replay is not None:
            return replay
        operation_id: str
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                pending = connection.execute(
                    "SELECT operation_id, actor_principal, idempotency_key, request_hash "
                    "FROM career_profile_erasure_journal LIMIT 1"
                ).fetchone()
                if pending is not None:
                    if (
                        str(pending[1]) != principal
                        or str(pending[2]) != command.idempotency_key
                        or not secrets.compare_digest(str(pending[3]), request_hash)
                    ):
                        raise CareerProfileErasureInProgress(
                            "A Career Profile erasure is already being recovered"
                        )
                    operation_id = str(pending[0])
                else:
                    self._check_head(connection, command.expected_profile_revision)
                    evidence = connection.execute(
                        "SELECT storage_name FROM career_profile_evidence WHERE evidence_id = ?",
                        (evidence_id,),
                    ).fetchone()
                    if evidence is None:
                        raise CareerProfileEvidenceNotFound
                    operation_id = _opaque_id("cpx_")
                    connection.execute(
                        "INSERT INTO career_profile_erasure_journal("
                        "operation_id, operation, actor_principal, idempotency_key, request_hash, "
                        "target_evidence_id, storage_names_json) "
                        "VALUES (?, 'evidence.erase', ?, ?, ?, ?, ?)",
                        (
                            operation_id,
                            principal,
                            command.idempotency_key,
                            request_hash,
                            evidence_id,
                            _canonical_json([str(evidence[0])]),
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._complete_erasure(operation_id)

    def reset_profile(
        self,
        *,
        principal: str,
        command: CareerProfileResetRequest,
    ) -> CareerProfileErasureResult:
        request_hash = _request_hash("profile.reset", command.model_dump(mode="json"))
        replay = self._erasure_receipt(principal, command.idempotency_key, request_hash)
        if replay is not None:
            return replay
        operation_id: str
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                pending = connection.execute(
                    "SELECT operation_id, actor_principal, idempotency_key, request_hash "
                    "FROM career_profile_erasure_journal LIMIT 1"
                ).fetchone()
                if pending is not None:
                    if (
                        str(pending[1]) != principal
                        or str(pending[2]) != command.idempotency_key
                        or not secrets.compare_digest(str(pending[3]), request_hash)
                    ):
                        raise CareerProfileErasureInProgress(
                            "A Career Profile erasure is already being recovered"
                        )
                    operation_id = str(pending[0])
                else:
                    self._check_head(connection, command.expected_profile_revision)
                    storage_names = [
                        str(row[0])
                        for row in connection.execute(
                            "SELECT storage_name FROM career_profile_evidence ORDER BY evidence_id"
                        ).fetchall()
                    ]
                    operation_id = _opaque_id("cpx_")
                    connection.execute(
                        "INSERT INTO career_profile_erasure_journal("
                        "operation_id, operation, actor_principal, idempotency_key, request_hash, "
                        "target_evidence_id, storage_names_json) "
                        "VALUES (?, 'profile.reset', ?, ?, ?, NULL, ?)",
                        (
                            operation_id,
                            principal,
                            command.idempotency_key,
                            request_hash,
                            _canonical_json(storage_names),
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._complete_erasure(operation_id)

    def recover_pending_erasures(self) -> None:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT operation_id FROM career_profile_erasure_journal ORDER BY created_at"
            ).fetchall()
        for row in rows:
            self._complete_erasure(str(row[0]))

    def _erasure_receipt(
        self, principal: str, idempotency_key: str, request_hash: str
    ) -> CareerProfileErasureResult | None:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT request_hash, result_json FROM career_profile_erasure_receipts "
                "WHERE actor_principal = ? AND idempotency_key = ?",
                (principal, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        if not secrets.compare_digest(str(row[0]), request_hash):
            raise CareerProfileIdempotencyConflict
        return CareerProfileErasureResult.model_validate_json(str(row[1]))

    def _complete_erasure(self, operation_id: str) -> CareerProfileErasureResult:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT operation, actor_principal, idempotency_key, request_hash, "
                "target_evidence_id, storage_names_json, phase "
                "FROM career_profile_erasure_journal WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Career Profile erasure journal entry was not found")
        operation = str(row[0])
        principal = str(row[1])
        idempotency_key = str(row[2])
        request_hash = str(row[3])
        evidence_id = str(row[4]) if row[4] is not None else None
        storage_names = [str(value) for value in json.loads(str(row[5]))]
        phase = str(row[6])
        if operation not in {"evidence.erase", "profile.reset"}:
            raise RuntimeError("Career Profile erasure journal operation is invalid")
        result = CareerProfileErasureResult(
            operation="evidence_erased" if operation == "evidence.erase" else "career_profile_reset"
        )

        if phase == "prepared":
            for storage_name in storage_names:
                self.vault.erase(storage_name)

            with connect_sqlite(self.database) as connection:
                connection.execute("PRAGMA secure_delete = ON")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if operation == "evidence.erase":
                        if evidence_id is None:
                            raise RuntimeError("Evidence erasure journal lost its target")
                        self._purge_evidence_database_scope(connection, evidence_id)
                    elif operation == "profile.reset":
                        self._purge_complete_profile_database_scope(connection)
                    else:
                        raise RuntimeError("Career Profile erasure journal operation is invalid")
                    # Scrub source identifiers before the durable hardening phase. The
                    # remaining marker is sufficient to retry compaction after a crash.
                    connection.execute(
                        "UPDATE career_profile_erasure_journal SET phase = 'purged', "
                        "target_evidence_id = NULL, storage_names_json = '[]' "
                        "WHERE operation_id = ?",
                        (operation_id,),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        elif phase != "purged":
            raise RuntimeError("Career Profile erasure journal phase is invalid")

        self._harden_database()
        with connect_sqlite(self.database) as connection:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR REPLACE INTO career_profile_erasure_receipts("
                "actor_principal, idempotency_key, request_hash, result_json) VALUES (?, ?, ?, ?)",
                (
                    principal,
                    idempotency_key,
                    request_hash,
                    _canonical_json(result.model_dump(mode="json")),
                ),
            )
            connection.execute(
                "DELETE FROM career_profile_erasure_journal WHERE operation_id = ?",
                (operation_id,),
            )
            connection.commit()
        return result

    def _purge_evidence_database_scope(
        self, connection: sqlite3.Connection, evidence_id: str
    ) -> None:
        removed_revision_ids: list[str] = []
        for row in connection.execute(
            "SELECT revision_id, evidence_id, before_json, after_json "
            "FROM career_profile_complete_revisions"
        ).fetchall():
            if str(row[1]) == evidence_id or any(
                self._json_contains_reference(value, evidence_id) for value in row[2:]
            ):
                removed_revision_ids.append(str(row[0]))
        if removed_revision_ids:
            placeholders = ",".join("?" for _ in removed_revision_ids)
            connection.execute(
                f"DELETE FROM career_profile_audit_events WHERE revision_id IN ({placeholders})",
                removed_revision_ids,
            )
            connection.execute(
                f"DELETE FROM career_profile_complete_revisions "
                f"WHERE revision_id IN ({placeholders})",
                removed_revision_ids,
            )

        for row in connection.execute(
            "SELECT item_id, provenance_json, review_status, evidence_ids_json, active "
            "FROM career_profile_items"
        ).fetchall():
            evidence_ids = json.loads(str(row[3]))
            if evidence_id not in evidence_ids:
                continue
            provenance = json.loads(str(row[1]))
            source_derived = provenance.get("method") == "evidence_import"
            if source_derived and (not bool(row[4]) or str(row[2]) != "accepted"):
                connection.execute("DELETE FROM career_profile_items WHERE item_id = ?", (row[0],))
                continue
            if source_derived:
                provenance = {
                    "method": "evidence_erased",
                    "mutation_source": provenance["mutation_source"],
                }
            connection.execute(
                "UPDATE career_profile_items SET evidence_ids_json = ?, provenance_json = ? "
                "WHERE item_id = ?",
                (
                    _canonical_json([value for value in evidence_ids if value != evidence_id]),
                    _canonical_json(provenance),
                    row[0],
                ),
            )

        for row in connection.execute(
            "SELECT actor_principal, idempotency_key, result_json "
            "FROM career_profile_complete_idempotency"
        ).fetchall():
            if self._json_contains_reference(row[2], evidence_id):
                connection.execute(
                    "DELETE FROM career_profile_complete_idempotency "
                    "WHERE actor_principal = ? AND idempotency_key = ?",
                    (row[0], row[1]),
                )
        connection.execute(
            "DELETE FROM career_profile_intent_grants WHERE target_id = ?", (evidence_id,)
        )
        connection.execute(
            "DELETE FROM career_profile_evidence WHERE evidence_id = ?", (evidence_id,)
        )

    @staticmethod
    def _json_contains_reference(value: object, reference: str) -> bool:
        if value is None:
            return False
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError):
            return False

        def contains(candidate: object) -> bool:
            if isinstance(candidate, str):
                return secrets.compare_digest(candidate, reference)
            if isinstance(candidate, list):
                return any(contains(item) for item in candidate)
            if isinstance(candidate, dict):
                return any(contains(item) for item in candidate.values())
            return False

        return contains(parsed)

    @staticmethod
    def _purge_complete_profile_database_scope(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE conversation_turns SET career_profile_snapshot_id = NULL, "
            "career_profile_revision = NULL, career_profile_content_hash = NULL "
            "WHERE career_profile_snapshot_id IS NOT NULL"
        )
        for table in (
            "career_profile_intent_grants",
            "career_profile_collaboration_idempotency",
            "career_profile_change_proposals",
            "career_profile_complete_idempotency",
            "career_profile_complete_revisions",
            "career_profile_items",
            "career_profile_evidence",
            "career_profile_idempotency",
            "career_profile_revisions",
            "career_profile_records",
            "career_profile_snapshots",
            "career_profile_audit_events",
            "career_profile_erasure_receipts",
        ):
            connection.execute(f"DELETE FROM {table}")
        connection.execute(
            "UPDATE career_profiles SET head_revision = 0, authority_epoch = authority_epoch + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE profile_id = ?",
            (PROFILE_ID,),
        )

    def _harden_database(self) -> None:
        with connect_sqlite(self.database) as connection:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def _mutate_item(
        self,
        *,
        principal: str,
        idempotency_key: str,
        request_hash: str,
        expected_revision: int,
        item_id: str,
        value: ProfileValue,
        evidence_ids: list[str],
        require_existing: bool,
        mutation_source: Literal[
            "direct_user", "agent_inference", "authenticated_user_instruction"
        ],
        intent_grant_id: str | None,
        operation: str,
        grant_payload: dict[str, object],
        allow_agent_direct: bool,
        reason: str | None,
    ) -> CareerProfileCompleteCurrent:
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay(connection, principal, idempotency_key, request_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                head = self._check_head(connection, expected_revision)
                self._authorize_actor(
                    connection,
                    principal=principal,
                    mutation_source=mutation_source,
                    intent_grant_id=intent_grant_id,
                    operation=operation,
                    target_id=item_id if require_existing else None,
                    payload=grant_payload,
                    allow_agent_direct=allow_agent_direct,
                )
                previous_row = connection.execute(
                    "SELECT value_json, provenance_json, review_status, evidence_ids_json, "
                    "item_revision, actor_principal, created_at, updated_at "
                    "FROM career_profile_items WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                previous = (
                    self._item_from_row((item_id, *previous_row))
                    if previous_row is not None
                    else None
                )
                if require_existing and previous is None:
                    raise CareerProfileItemNotFound
                if (
                    mutation_source == "agent_inference"
                    and previous is not None
                    and not allow_agent_direct
                ):
                    raise CareerProfileValueError(
                        "Autonomous agent updates must be submitted as a separate proposal"
                    )
                historical_evidence_ids = set(previous.evidence_ids if previous else [])
                for evidence_id in evidence_ids:
                    row = connection.execute(
                        "SELECT active FROM career_profile_evidence WHERE evidence_id = ?",
                        (evidence_id,),
                    ).fetchone()
                    if row is None or (
                        not bool(row[0]) and evidence_id not in historical_evidence_ids
                    ):
                        raise CareerProfileValueError("New Evidence links must exist and be active")
                item_revision = previous.item_revision + 1 if previous else 1
                timestamp = _now()
                review_status: ReviewStatus = (
                    "proposed"
                    if mutation_source == "agent_inference" and not allow_agent_direct
                    else "accepted"
                )
                provenance = ItemProvenance(
                    method=(
                        "agent_generated"
                        if mutation_source == "agent_inference"
                        else "agent_edit"
                        if mutation_source == "authenticated_user_instruction"
                        else "user_entered"
                    ),
                    mutation_source=(
                        "agent_inference"
                        if mutation_source == "agent_inference"
                        else "authenticated_user_instruction"
                        if mutation_source == "authenticated_user_instruction"
                        else "direct_user"
                    ),
                )
                connection.execute(
                    "INSERT INTO career_profile_items(item_id, value_json, provenance_json, "
                    "review_status, evidence_ids_json, item_revision, actor_principal, active, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
                    "ON CONFLICT(item_id) DO UPDATE SET value_json = excluded.value_json, "
                    "provenance_json = excluded.provenance_json, "
                    "review_status = excluded.review_status, "
                    "evidence_ids_json = excluded.evidence_ids_json, "
                    "item_revision = excluded.item_revision, "
                    "actor_principal = excluded.actor_principal, active = 1, "
                    "updated_at = excluded.updated_at",
                    (
                        item_id,
                        _canonical_json(value.model_dump(mode="json")),
                        _canonical_json(provenance.model_dump(mode="json")),
                        review_status,
                        _canonical_json(evidence_ids),
                        item_revision,
                        principal,
                        previous.created_at if previous else timestamp,
                        timestamp,
                    ),
                )
                current_row = connection.execute(
                    "SELECT value_json, provenance_json, review_status, evidence_ids_json, "
                    "item_revision, actor_principal, created_at, updated_at "
                    "FROM career_profile_items WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                assert current_row is not None
                current = self._item_from_row((item_id, *current_row))
                revision = head + 1
                self._record_revision(
                    connection,
                    revision=revision,
                    base_revision=head,
                    principal=principal,
                    actor_kind=_history_actor_kind(mutation_source),
                    operation="item.upsert",
                    item_id=item_id,
                    evidence_id=None,
                    before=previous.model_dump(mode="json") if previous else None,
                    after=current.model_dump(mode="json"),
                    affected=[f"items.{item_id}"],
                    reason=reason,
                )
                result = self._finish(connection, principal, idempotency_key, request_hash)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _authorize_actor(
        connection: sqlite3.Connection,
        *,
        principal: str,
        mutation_source: Literal[
            "direct_user", "agent_inference", "authenticated_user_instruction"
        ],
        intent_grant_id: str | None,
        operation: str,
        target_id: str | None,
        payload: dict[str, object],
        allow_agent_direct: bool = False,
    ) -> None:
        if mutation_source == "direct_user":
            if intent_grant_id is not None:
                raise CareerProfileValueError("Direct user actions do not consume agent grants")
            return
        if mutation_source == "agent_inference":
            if operation != "item.create" and not allow_agent_direct:
                raise CareerProfileValueError(
                    "Autonomous agent destructive or replacement actions require exact user intent"
                )
            return
        if intent_grant_id is None:
            raise CareerProfileValueError("Authenticated exact-payload user intent is required")
        row = connection.execute(
            "SELECT operation, target_id, payload_sha256, consumed_at "
            "FROM career_profile_intent_grants WHERE grant_id = ?",
            (intent_grant_id,),
        ).fetchone()
        payload_sha = sha256(_canonical_json(payload).encode()).hexdigest()
        if (
            row is None
            or row[3] is not None
            or str(row[0]) != operation
            or row[1] != target_id
            or not secrets.compare_digest(str(row[2]), payload_sha)
        ):
            raise CareerProfileValueError(
                "Intent grant is missing, consumed, or payload-mismatched"
            )
        updated = connection.execute(
            "UPDATE career_profile_intent_grants SET consumed_at = ?, "
            "consumed_by_principal = ? WHERE grant_id = ? AND consumed_at IS NULL",
            (_now(), principal, intent_grant_id),
        )
        if updated.rowcount != 1:
            raise CareerProfileValueError("Intent grant was already consumed")

    @staticmethod
    def _head(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT head_revision FROM career_profiles WHERE profile_id = ?", (PROFILE_ID,)
        ).fetchone()
        if row is None:
            raise RuntimeError("Career Profile storage is not initialized")
        return int(row[0])

    def _check_head(self, connection: sqlite3.Connection, expected: int) -> int:
        ensure_no_pending_erasure(connection)
        head = self._head(connection)
        if head != expected:
            raise CareerProfileRevisionConflict(head)
        return head

    def _finish(
        self,
        connection: sqlite3.Connection,
        principal: str,
        idempotency_key: str,
        request_hash: str,
    ) -> CareerProfileCompleteCurrent:
        result = self._current_in_connection(connection)
        connection.execute(
            "INSERT INTO career_profile_complete_idempotency(actor_principal, idempotency_key, "
            "request_hash, result_json) VALUES (?, ?, ?, ?)",
            (
                principal,
                idempotency_key,
                request_hash,
                _canonical_json(result.model_dump(mode="json")),
            ),
        )
        return result

    @staticmethod
    def _replay(
        connection: sqlite3.Connection,
        principal: str,
        idempotency_key: str,
        request_hash: str,
    ) -> CareerProfileCompleteCurrent | None:
        ensure_no_pending_erasure(connection)
        row = connection.execute(
            "SELECT request_hash, result_json FROM career_profile_complete_idempotency "
            "WHERE actor_principal = ? AND idempotency_key = ?",
            (principal, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if not secrets.compare_digest(str(row[0]), request_hash):
            raise CareerProfileIdempotencyConflict
        return CareerProfileCompleteCurrent.model_validate_json(str(row[1]))

    def _record_revision(
        self,
        connection: sqlite3.Connection,
        *,
        revision: int,
        base_revision: int,
        principal: str,
        actor_kind: HistoryActorKind,
        operation: str,
        item_id: str | None,
        evidence_id: str | None,
        before: object | None,
        after: object | None,
        affected: list[str],
        reason: str | None = None,
        proposal_id: str | None = None,
        undo_of_revision_id: str | None = None,
    ) -> None:
        revision_id = _opaque_id("cpv_")
        connection.execute(
            "INSERT INTO career_profile_complete_revisions(revision_id, profile_revision, "
            "base_profile_revision, actor_principal, actor_kind, operation, item_id, "
            "evidence_id, before_json, "
            "after_json, affected_fields_json, reason, proposal_id, undo_of_revision_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                revision,
                base_revision,
                principal,
                actor_kind,
                operation,
                item_id,
                evidence_id,
                _canonical_json(before) if before is not None else None,
                _canonical_json(after) if after is not None else None,
                _canonical_json(affected),
                reason,
                proposal_id,
                undo_of_revision_id,
            ),
        )
        connection.execute(
            "UPDATE career_profiles SET head_revision = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE profile_id = ?",
            (revision, PROFILE_ID),
        )
        connection.execute(
            "INSERT INTO career_profile_audit_events(actor_principal, action, profile_revision, "
            "base_profile_revision, affected_fields_json, revision_id) VALUES (?, ?, ?, ?, ?, ?)",
            (
                principal,
                f"career_profile.{operation}",
                revision,
                base_revision,
                _canonical_json(affected),
                revision_id,
            ),
        )

    def _current_in_connection(
        self, connection: sqlite3.Connection
    ) -> CareerProfileCompleteCurrent:
        profile_state = connection.execute(
            "SELECT head_revision, authority_epoch FROM career_profiles WHERE profile_id = ?",
            (PROFILE_ID,),
        ).fetchone()
        if profile_state is None:
            raise RuntimeError("Career Profile storage is not initialized")
        head = int(profile_state[0])
        authority_epoch = int(profile_state[1])
        rows = connection.execute(
            "SELECT item_id, value_json, provenance_json, review_status, evidence_ids_json, "
            "item_revision, actor_principal, created_at, updated_at FROM career_profile_items "
            "WHERE active = 1 ORDER BY created_at, item_id"
        ).fetchall()
        items = [self._item_from_row(row) for row in rows]
        tracer = connection.execute(
            "SELECT record_id, value_json, item_revision, actor_principal, created_at, updated_at "
            "FROM career_profile_records WHERE profile_id = ? AND namespace = ?",
            (PROFILE_ID, WORK_ARRANGEMENT_NAMESPACE),
        ).fetchone()
        if tracer is not None and not any(item.value.kind == "work_arrangement" for item in items):
            value = json.loads(str(tracer[1]))
            items.append(
                ProfileItemRecord(
                    item_id=str(tracer[0]),
                    area="what_im_looking_for",
                    value=WorkArrangementProfileValue(kind="work_arrangement", **value),
                    review_status="accepted",
                    evidence_ids=[],
                    provenance=ItemProvenance(
                        method="tracer_compatibility", mutation_source="direct_user"
                    ),
                    item_revision=int(tracer[2]),
                    actor_principal=str(tracer[3]),
                    created_at=str(tracer[4]),
                    updated_at=str(tracer[5]),
                )
            )
        evidence_rows = connection.execute(
            "SELECT evidence_id, original_filename, media_type, content_sha256, byte_count, "
            "captured_at, imported_at, provenance_json, active FROM career_profile_evidence "
            "ORDER BY imported_at, evidence_id"
        ).fetchall()
        return CareerProfileCompleteCurrent(
            profile_revision=head,
            authority_epoch=authority_epoch,
            items=items,
            source_evidence=[self._evidence_from_row(row) for row in evidence_rows],
        )

    @staticmethod
    def _item_from_row(row: sqlite3.Row | tuple[object, ...]) -> ProfileItemRecord:
        value = ProfileItemRecord.model_validate(
            {
                "item_id": str(row[0]),
                "area": _area_for_kind(json.loads(str(row[1]))["kind"]),
                "value": json.loads(str(row[1])),
                "provenance": json.loads(str(row[2])),
                "review_status": str(row[3]),
                "evidence_ids": json.loads(str(row[4])),
                "item_revision": int(row[5]),
                "actor_principal": str(row[6]),
                "created_at": str(row[7]),
                "updated_at": str(row[8]),
            }
        )
        return value

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row | tuple[object, ...]) -> SourceEvidenceRecord:
        return SourceEvidenceRecord(
            evidence_id=str(row[0]),
            original_filename=str(row[1]),
            media_type=str(row[2]),
            sha256=str(row[3]),
            byte_count=int(row[4]),
            captured_at=str(row[5]) if row[5] is not None else None,
            imported_at=str(row[6]),
            provenance=EvidenceProvenance.model_validate_json(str(row[7])),
            active=bool(row[8]),
        )

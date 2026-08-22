from __future__ import annotations

import base64
import binascii
import json
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .career_profile import PROFILE_ID
from .career_profile_complete import (
    CareerProfileCompleteStore,
    CareerProfileLegacyWriterFenced,
    EvidenceProvenance,
    ItemProvenance,
    ProfileItemMutation,
    ProfileValue,
)
from .sqlite_connection import connect_sqlite

MIGRATION_PRINCIPAL = "migration:career-profile"
MIGRATION_REVIEW_AGENT_ID = "career-profile-migration"
MappingDisposition = Literal["deterministic", "inference", "conflict"]

# This registry, not input labels, owns whether an imported shape can become accepted.
MAPPING_POLICY: dict[str, MappingDisposition] = {
    "canonical.identity": "deterministic",
    "canonical.education": "deterministic",
    "canonical.skill": "deterministic",
    "canonical.experience": "deterministic",
    "canonical.project": "deterministic",
    "canonical.claim": "deterministic",
    "canonical.positioning": "inference",
    "search.target_roles": "deterministic",
    "search.location": "deterministic",
    "search.compensation": "deterministic",
    "search.work_arrangement": "deterministic",
    "search.industries": "deterministic",
    "search.priority": "deterministic",
    "search.dealbreaker": "deterministic",
    "source.extracted": "inference",
    "source.ambiguous": "inference",
    "source.conflict": "conflict",
}

MAPPING_VALUE_KINDS = {
    "canonical.identity": "identity",
    "canonical.education": "education",
    "canonical.skill": "skill",
    "canonical.experience": "experience",
    "canonical.project": "project",
    "canonical.claim": "claim",
    "canonical.positioning": "positioning",
    "search.target_roles": "target_roles",
    "search.location": "location",
    "search.compensation": "compensation",
    "search.work_arrangement": "work_arrangement",
    "search.industries": "industries",
    "search.priority": "priority",
    "search.dealbreaker": "dealbreaker",
}

# These source shapes describe one profile-wide field. Repeatable rows such as
# skills, education, experience, projects, priorities, and claims are not
# conflicts merely because their values differ.
SINGLETON_DETERMINISTIC_MAPPINGS = {
    "canonical.identity",
    "search.target_roles",
    "search.location",
    "search.compensation",
    "search.work_arrangement",
    "search.industries",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MigrationEvidenceInput(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    original_filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=200)
    captured_at: str | None = None
    source_kind: Literal["resume", "portfolio", "supporting_document", "citation"]
    source_label: str = Field(min_length=1, max_length=500)
    content_base64: str = Field(min_length=1, max_length=14 * 1024 * 1024)

    @field_validator("content_base64")
    @classmethod
    def valid_content(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("content_base64 must be valid base64") from error
        if not decoded or len(decoded) > 10 * 1024 * 1024:
            raise ValueError("migration Evidence must be between 1 byte and 10 MiB")
        return value


class MigrationFactInput(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    mapping: str = Field(min_length=1, max_length=100)
    value: dict[str, object] | None = None
    evidence_keys: list[str] = Field(default_factory=list, max_length=100)
    source_label: str = Field(min_length=1, max_length=500)


class CareerProfileMigrationBundle(StrictModel):
    schema_version: Literal[1]
    bundle_label: str = Field(min_length=1, max_length=200)
    evidence: list[MigrationEvidenceInput] = Field(default_factory=list, max_length=100)
    facts: list[MigrationFactInput] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def references_are_exact(self) -> CareerProfileMigrationBundle:
        evidence_keys = [item.key for item in self.evidence]
        fact_keys = [item.key for item in self.facts]
        if len(set(evidence_keys)) != len(evidence_keys) or len(set(fact_keys)) != len(fact_keys):
            raise ValueError("migration bundle keys must be unique")
        known = set(evidence_keys)
        if any(key not in known for fact in self.facts for key in fact.evidence_keys):
            raise ValueError("migration fact references unknown Evidence")
        unknown_mappings = sorted({fact.mapping for fact in self.facts} - MAPPING_POLICY.keys())
        if unknown_mappings:
            raise ValueError(f"migration mapping is not code-owned: {unknown_mappings[0]}")
        for fact in self.facts:
            expected_kind = MAPPING_VALUE_KINDS.get(fact.mapping)
            if (
                fact.value is not None
                and expected_kind is not None
                and fact.value.get("kind") != expected_kind
            ):
                raise ValueError(
                    f"migration mapping {fact.mapping} requires value kind {expected_kind}"
                )
        return self


class MigrationAreaCounts(StrictModel):
    accepted: int = Field(ge=0)
    proposed: int = Field(ge=0)
    conflicting: int = Field(ge=0)
    skipped_unknown: int = Field(ge=0)


class CareerProfileMigrationReport(StrictModel):
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bundle_label: str
    completed: Literal[True] = True
    profile_revision: int = Field(ge=0)
    authority_state: Literal["staging"] = "staging"
    my_career: MigrationAreaCounts
    what_im_looking_for: MigrationAreaCounts
    my_evidence: MigrationAreaCounts
    evidence_objects: int = Field(ge=0)
    evidence_hashes: list[str]
    accepted_content_hashes: list[str]
    proposed_content_hashes: list[str]


class CareerProfileMigrationError(RuntimeError):
    """The migration candidate is incomplete or violates its one-shot boundary."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _bundle_hash(bundle: CareerProfileMigrationBundle) -> str:
    return sha256(_canonical_json(bundle.model_dump(mode="json")).encode()).hexdigest()


def _opaque(prefix: str, bundle_hash: str, key: str) -> str:
    return f"{prefix}{sha256(f'{bundle_hash}:{key}'.encode()).hexdigest()[:32]}"


def _area(kind: str) -> Literal["my_career", "what_im_looking_for"]:
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


class CareerProfileMigrationService:
    """One-shot, resumable migration into a staging Career Profile candidate."""

    def __init__(self, database: Path, evidence_root: Path) -> None:
        self.database = database
        self.evidence_root = evidence_root
        self.complete = CareerProfileCompleteStore(database, evidence_root)

    def initialize(self) -> None:
        """Fail closed at startup when a prior candidate stopped before completion."""
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            pending = connection.execute(
                "SELECT bundle_sha256, phase FROM career_profile_migration_journal "
                "WHERE phase != 'complete' LIMIT 1"
            ).fetchone()
        if pending is not None:
            raise CareerProfileMigrationError(
                "Career Profile migration requires explicit same-bundle recovery before startup"
            )

    def run(self, bundle: CareerProfileMigrationBundle) -> CareerProfileMigrationReport:
        bundle_hash = _bundle_hash(bundle)
        request_json = _canonical_json(bundle.model_dump(mode="json"))
        current = self.complete.current()
        if current.authority_state != "staging":
            raise CareerProfileLegacyWriterFenced(CareerProfileLegacyWriterFenced.code)
        receipt = self._receipt(bundle_hash)
        if receipt is not None:
            return receipt
        self._prepare(bundle_hash, request_json)
        evidence = self._write_evidence(bundle_hash, bundle)
        self._set_phase(bundle_hash, "vault_written")
        try:
            return self._commit(bundle_hash, bundle, evidence)
        except Exception:
            # The durable journal and deterministic IDs make the same bundle safely resumable.
            raise

    def _receipt(self, bundle_hash: str) -> CareerProfileMigrationReport | None:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT report_json FROM career_profile_migration_receipts WHERE bundle_sha256 = ?",
                (bundle_hash,),
            ).fetchone()
        return CareerProfileMigrationReport.model_validate_json(str(row[0])) if row else None

    def _prepare(self, bundle_hash: str, request_json: str) -> None:
        timestamp = _now()
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_json FROM career_profile_migration_journal WHERE bundle_sha256 = ?",
                (bundle_hash,),
            ).fetchone()
            if row is not None and not secrets.compare_digest(str(row[0]), request_json):
                raise CareerProfileMigrationError("migration journal request hash mismatch")
            if row is None:
                occupied = connection.execute(
                    "SELECT bundle_sha256 FROM career_profile_migration_journal LIMIT 1"
                ).fetchone()
                if occupied is not None:
                    raise CareerProfileMigrationError(
                        "Career Profile migration is a one-shot operation"
                    )
                profile = connection.execute(
                    "SELECT head_revision, authority_state FROM career_profiles "
                    "WHERE profile_id = ?",
                    (PROFILE_ID,),
                ).fetchone()
                if profile is None or int(profile[0]) != 0 or str(profile[1]) != "staging":
                    raise CareerProfileMigrationError(
                        "migration requires a fresh staging Career Profile candidate"
                    )
                connection.execute(
                    "INSERT INTO career_profile_migration_journal("
                    "bundle_sha256, actor_principal, phase, request_json, created_at, updated_at) "
                    "VALUES (?, ?, 'prepared', ?, ?, ?)",
                    (bundle_hash, MIGRATION_PRINCIPAL, request_json, timestamp, timestamp),
                )
            connection.commit()

    def _write_evidence(
        self, bundle_hash: str, bundle: CareerProfileMigrationBundle
    ) -> dict[str, tuple[str, str, int, str]]:
        self.complete.vault.initialize()
        result: dict[str, tuple[str, str, int, str]] = {}
        for source in bundle.evidence:
            content = base64.b64decode(source.content_base64, validate=True)
            digest = sha256(content).hexdigest()
            evidence_id = _opaque("cpe_", bundle_hash, source.key)
            storage_name = self.complete.vault.write_idempotent(evidence_id, content)
            result[source.key] = (evidence_id, digest, len(content), storage_name)
        return result

    def _set_phase(self, bundle_hash: str, phase: Literal["vault_written"]) -> None:
        with connect_sqlite(self.database) as connection:
            connection.execute(
                "UPDATE career_profile_migration_journal SET phase = ?, updated_at = ? "
                "WHERE bundle_sha256 = ?",
                (phase, _now(), bundle_hash),
            )
            connection.commit()

    def _commit(
        self,
        bundle_hash: str,
        bundle: CareerProfileMigrationBundle,
        evidence: dict[str, tuple[str, str, int, str]],
    ) -> CareerProfileMigrationReport:
        timestamp = _now()
        counts = {
            "my_career": {"accepted": 0, "proposed": 0, "conflicting": 0, "skipped_unknown": 0},
            "what_im_looking_for": {
                "accepted": 0,
                "proposed": 0,
                "conflicting": 0,
                "skipped_unknown": 0,
            },
            "my_evidence": {"accepted": 0, "proposed": 0, "conflicting": 0, "skipped_unknown": 0},
        }
        accepted_hashes: list[str] = []
        proposed_hashes: list[str] = []
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT head_revision, authority_state FROM career_profiles WHERE profile_id = ?",
                (PROFILE_ID,),
            ).fetchone()
            if row is None or int(row[0]) != 0 or str(row[1]) != "staging":
                raise CareerProfileMigrationError(
                    "migration lost its fresh staging authority boundary"
                )
            for source in bundle.evidence:
                evidence_id, digest, byte_count, storage_name = evidence[source.key]
                provenance = EvidenceProvenance(
                    source_kind=source.source_kind,
                    source_label=source.source_label,
                    method="migration_import",
                )
                connection.execute(
                    "INSERT INTO career_profile_evidence("
                    "evidence_id, original_filename, media_type, content_sha256, byte_count, "
                    "captured_at, imported_at, provenance_json, storage_name, active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (
                        evidence_id,
                        source.original_filename,
                        source.media_type,
                        digest,
                        byte_count,
                        source.captured_at,
                        timestamp,
                        _canonical_json(provenance.model_dump(mode="json")),
                        storage_name,
                    ),
                )
            counts["my_evidence"]["accepted"] = len(bundle.evidence)
            affected: list[str] = ["source_evidence"] if bundle.evidence else []
            if any(fact.value is not None for fact in bundle.facts):
                connection.execute(
                    "INSERT OR IGNORE INTO career_profile_connected_agents("
                    "agent_id, display_name, principal, token_sha256, trust_mode, active, "
                    "connected_at, updated_at) VALUES (?, ?, ?, ?, 'review', 1, ?, ?)",
                    (
                        MIGRATION_REVIEW_AGENT_ID,
                        "Migration reviewer",
                        MIGRATION_PRINCIPAL,
                        sha256(b"disabled-internal-migration-review-principal").hexdigest(),
                        timestamp,
                        timestamp,
                    ),
                )
            deterministic_values: dict[str, set[str]] = {}
            for fact in bundle.facts:
                if fact.mapping in SINGLETON_DETERMINISTIC_MAPPINGS and fact.value is not None:
                    deterministic_values.setdefault(fact.mapping, set()).add(
                        _canonical_json(fact.value)
                    )
            conflicting_mappings = {
                mapping for mapping, values in deterministic_values.items() if len(values) > 1
            }
            for fact in bundle.facts:
                disposition = (
                    "conflict"
                    if fact.mapping in conflicting_mappings
                    else MAPPING_POLICY[fact.mapping]
                )
                if fact.value is None:
                    target_area = (
                        "my_career"
                        if fact.mapping.startswith("canonical.")
                        else "what_im_looking_for"
                    )
                    counts[target_area]["skipped_unknown"] += 1
                    continue
                value: ProfileValue = ProfileItemMutation.model_validate(
                    {
                        "expected_profile_revision": 0,
                        "idempotency_key": f"migration-{fact.key}-0001",
                        "value": fact.value,
                    }
                ).value
                target_area = _area(value.kind)
                review_status = (
                    "accepted"
                    if disposition == "deterministic"
                    else "conflicting"
                    if disposition == "conflict"
                    else "proposed"
                )
                counts[target_area][
                    "accepted" if review_status == "accepted" else review_status
                ] += 1
                item_id = _opaque("cpi_", bundle_hash, fact.key)
                evidence_ids = [evidence[key][0] for key in fact.evidence_keys]
                mutation_source = (
                    "deterministic_source_mapping"
                    if disposition == "deterministic"
                    else "agent_inference"
                )
                provenance = ItemProvenance(
                    method="migration_import",
                    source_label=fact.source_label,
                    imported_at=timestamp,
                    mutation_source=mutation_source,
                )
                value_payload = value.model_dump(mode="json", exclude_none=True)
                value_json = _canonical_json(value_payload)
                content_hash = sha256(value_json.encode()).hexdigest()
                (accepted_hashes if review_status == "accepted" else proposed_hashes).append(
                    content_hash
                )
                if review_status == "accepted":
                    connection.execute(
                        "INSERT INTO career_profile_items("
                        "item_id, value_json, provenance_json, review_status, evidence_ids_json, "
                        "item_revision, actor_principal, active, created_at, updated_at) "
                        "VALUES (?, ?, ?, 'accepted', ?, 1, ?, 1, ?, ?)",
                        (
                            item_id,
                            value_json,
                            _canonical_json(provenance.model_dump(mode="json")),
                            _canonical_json(evidence_ids),
                            MIGRATION_PRINCIPAL,
                            timestamp,
                            timestamp,
                        ),
                    )
                    affected.append(f"items.{item_id}")
                else:
                    proposal_id = _opaque("cpp_", bundle_hash, fact.key)
                    after = {
                        "item_id": item_id,
                        "area": target_area,
                        "value": value.model_dump(mode="json"),
                        "review_status": review_status,
                        "evidence_ids": evidence_ids,
                        "provenance": provenance.model_dump(mode="json"),
                        "item_revision": 1,
                        "actor_principal": MIGRATION_PRINCIPAL,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                    review_reason = (
                        "Conflicting legacy sources require user review"
                        if review_status == "conflicting"
                        else "Legacy content requires inference and user review"
                    )
                    proposal_payload = {
                        "proposal_id": proposal_id,
                        "agent_id": MIGRATION_REVIEW_AGENT_ID,
                        "reason": "One-time Career Profile migration candidate",
                        "review_reason": review_reason,
                        "base_profile_revision": 1,
                        "operation": "item.create",
                        "target_id": item_id,
                        "before": None,
                        "after": after,
                        "evidence_ids": evidence_ids,
                    }
                    connection.execute(
                        "INSERT INTO career_profile_change_proposals("
                        "proposal_id, agent_id, reason, review_reason, base_profile_revision, "
                        "operation, target_id, before_json, after_json, evidence_ids_json, "
                        "payload_sha256, status, created_at) "
                        "VALUES (?, ?, ?, ?, 1, 'item.create', ?, NULL, ?, ?, ?, 'pending', ?)",
                        (
                            proposal_id,
                            MIGRATION_REVIEW_AGENT_ID,
                            proposal_payload["reason"],
                            review_reason,
                            item_id,
                            _canonical_json(after),
                            _canonical_json(evidence_ids),
                            sha256(_canonical_json(proposal_payload).encode()).hexdigest(),
                            timestamp,
                        ),
                    )
                    affected.append(f"proposals.{proposal_id}")
            revision = (
                1 if bundle.evidence or any(fact.value is not None for fact in bundle.facts) else 0
            )
            if revision:
                revision_id = _opaque("cpv_", bundle_hash, "revision")
                connection.execute(
                    "INSERT INTO career_profile_complete_revisions("
                    "revision_id, profile_revision, base_profile_revision, actor_principal, "
                    "operation, affected_fields_json, reason, actor_kind) "
                    "VALUES (?, 1, 0, ?, 'item.upsert', ?, ?, ?)",
                    (
                        revision_id,
                        MIGRATION_PRINCIPAL,
                        _canonical_json(affected),
                        "One-time Career Profile migration candidate",
                        "deterministic_source_mapping",
                    ),
                )
                connection.execute(
                    "UPDATE career_profiles SET head_revision = 1, updated_at = ? "
                    "WHERE profile_id = ?",
                    (timestamp, PROFILE_ID),
                )
            report = CareerProfileMigrationReport(
                bundle_sha256=bundle_hash,
                bundle_label=bundle.bundle_label,
                profile_revision=revision,
                my_career=MigrationAreaCounts(**counts["my_career"]),
                what_im_looking_for=MigrationAreaCounts(**counts["what_im_looking_for"]),
                my_evidence=MigrationAreaCounts(**counts["my_evidence"]),
                evidence_objects=len(evidence),
                evidence_hashes=sorted(value[1] for value in evidence.values()),
                accepted_content_hashes=sorted(accepted_hashes),
                proposed_content_hashes=sorted(proposed_hashes),
            )
            report_json = _canonical_json(report.model_dump(mode="json"))
            connection.execute(
                "INSERT INTO career_profile_migration_receipts("
                "bundle_sha256, report_json, created_at) "
                "VALUES (?, ?, ?)",
                (bundle_hash, report_json, timestamp),
            )
            connection.execute(
                "UPDATE career_profile_migration_journal SET phase = 'complete', report_json = ?, "
                "updated_at = ? WHERE bundle_sha256 = ?",
                (report_json, timestamp, bundle_hash),
            )
            connection.execute(
                "INSERT INTO career_profile_audit_events("
                "actor_principal, action, profile_revision, affected_fields_json) "
                "VALUES (?, 'migration.complete', ?, ?)",
                (MIGRATION_PRINCIPAL, revision, _canonical_json(affected)),
            )
            connection.commit()
        return report

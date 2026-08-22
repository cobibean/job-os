from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import shutil
import zipfile
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .career_profile import (
    PROFILE_ID,
    WORK_ARRANGEMENT_NAMESPACE,
    CareerProfileIdempotencyConflict,
    CareerProfileRevisionConflict,
    CareerProfileStore,
    IdempotencyKey,
    WorkArrangementRecord,
    ensure_no_active_conversation_turn,
    ensure_no_pending_erasure_operation,
    ensure_no_pending_profile_operation,
)
from .career_profile_complete import (
    CareerProfileCompleteCurrent,
    CareerProfileCompleteStore,
    EvidenceVault,
    ProfileItemRecord,
    SourceEvidenceRecord,
)
from .sqlite_connection import connect_sqlite

EvidenceMode = Literal["profile_only", "selected", "all"]
RESTORE_CONFIRMATION = "RESTORE_CAREER_PROFILE_BASELINE"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 1_001
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CareerProfileExportRequest(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    evidence_mode: EvidenceMode
    selected_evidence_ids: list[str] = Field(default_factory=list, max_length=1_000)

    @model_validator(mode="after")
    def explicit_evidence_choice(self) -> Self:
        if len(set(self.selected_evidence_ids)) != len(self.selected_evidence_ids):
            raise ValueError("selected Evidence IDs must be unique")
        if any(
            not re.fullmatch(r"cpe_[A-Za-z0-9_-]{16,64}", evidence_id)
            for evidence_id in self.selected_evidence_ids
        ):
            raise ValueError("selected Evidence ID is invalid")
        if self.evidence_mode == "selected" and not self.selected_evidence_ids:
            raise ValueError("selected Evidence export requires at least one source")
        if self.evidence_mode != "selected" and self.selected_evidence_ids:
            raise ValueError(f"{self.evidence_mode} export cannot include selected Evidence IDs")
        return self


class EvidenceInclusionManifest(StrictModel):
    mode: EvidenceMode
    included_evidence_ids: list[str] = Field(max_length=1_000)
    omitted_evidence_ids: list[str] = Field(max_length=1_000)


class CareerProfileArchiveManifest(StrictModel):
    schema_version: Literal[1]
    exported_at: str
    source_profile_revision: int = Field(ge=0)
    source_authority_epoch: int = Field(ge=0)
    items: list[ProfileItemRecord] = Field(max_length=2_000)
    source_evidence: list[SourceEvidenceRecord] = Field(max_length=1_000)
    work_arrangement: WorkArrangementRecord | None
    evidence_inclusion: EvidenceInclusionManifest

    @model_validator(mode="after")
    def internally_consistent(self) -> Self:
        item_ids = [item.item_id for item in self.items]
        evidence_ids = [source.evidence_id for source in self.source_evidence]
        included = self.evidence_inclusion.included_evidence_ids
        omitted = self.evidence_inclusion.omitted_evidence_ids
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("Career Profile archive contains duplicate item IDs")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("Career Profile archive contains duplicate Evidence IDs")
        if len(set(included)) != len(included) or len(set(omitted)) != len(omitted):
            raise ValueError("Career Profile archive Evidence lists contain duplicates")
        if set(included) & set(omitted):
            raise ValueError("Career Profile archive Evidence lists overlap")
        if set(included) | set(omitted) != set(evidence_ids):
            raise ValueError("Career Profile archive Evidence inventory is incomplete")
        evidence_by_id = {source.evidence_id: source for source in self.source_evidence}
        if any(not evidence_by_id[evidence_id].active for evidence_id in included):
            raise ValueError("Career Profile archive cannot include inactive Evidence bytes")
        if any(
            evidence_id not in evidence_by_id
            for item in self.items
            for evidence_id in item.evidence_ids
        ):
            raise ValueError("Career Profile item references unknown Evidence")
        if any(item.value.kind == "work_arrangement" for item in self.items):
            raise ValueError("Work arrangement must use its compatibility field")
        mode = self.evidence_inclusion.mode
        active_ids = {source.evidence_id for source in self.source_evidence if source.active}
        if mode == "profile_only" and included:
            raise ValueError("Profile-only archives cannot include Evidence bytes")
        if mode == "selected" and not included:
            raise ValueError("Selected-Evidence archives must include a source")
        if mode == "all" and set(included) != active_ids:
            raise ValueError("All-Evidence archives must include every active source")
        return self


class CareerProfileExportResult(StrictModel):
    filename: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(ge=1, le=MAX_ARCHIVE_BYTES)
    content_base64: str
    included_evidence_ids: list[str]
    omitted_evidence_ids: list[str]


class CareerProfileRestoreRequest(StrictModel):
    expected_profile_revision: int = Field(ge=0)
    idempotency_key: IdempotencyKey
    confirmation: Literal["RESTORE_CAREER_PROFILE_BASELINE"]
    archive_base64: str = Field(min_length=1, max_length=140 * 1024 * 1024)

    @field_validator("archive_base64")
    @classmethod
    def bounded_archive(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("archive_base64 must be valid base64") from error
        if not decoded or len(decoded) > MAX_ARCHIVE_BYTES:
            raise ValueError("Career Profile archive must be between 1 byte and 100 MiB")
        return value


class CareerProfileRestoreResult(StrictModel):
    profile: CareerProfileCompleteCurrent
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    restored_evidence_ids: list[str]
    unavailable_evidence_ids: list[str]
    baseline_created: Literal[True] = True


class CareerProfilePortabilityError(RuntimeError):
    """A portable archive failed bounded validation or safe baseline restore."""


class CareerProfileRestoreBusy(RuntimeError):
    """A baseline restore cannot race an active agent turn."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _opaque_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(18)}"


def _archive_member(name: str, content: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    member = zipfile.ZipInfo(name)
    member.compress_type = zipfile.ZIP_DEFLATED
    member.external_attr = 0o600 << 16
    return member, content


class CareerProfilePortabilityService:
    """Explicit current-state export and crash-recoverable new-baseline restore."""

    def __init__(self, database: Path, evidence_root: Path) -> None:
        self.database = database
        self.evidence_root = evidence_root
        self.complete_profile = CareerProfileCompleteStore(database, evidence_root)

    def export_archive(self, command: CareerProfileExportRequest) -> CareerProfileExportResult:
        # A write-intent read transaction serializes metadata + vault reads with
        # restore preparation. Once this guard passes, restore cannot journal or
        # swap the vault until the export has captured one coherent baseline.
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                ensure_no_pending_profile_operation(connection)
                profile = self.complete_profile._current_in_connection(  # noqa: SLF001
                    connection
                )
                if profile.profile_revision != command.expected_profile_revision:
                    raise CareerProfileRevisionConflict(profile.profile_revision)
                tracer_row = connection.execute(
                    "SELECT record_id, value_json, item_revision, actor_principal, updated_at "
                    "FROM career_profile_records WHERE profile_id = ? AND namespace = ?",
                    (PROFILE_ID, WORK_ARRANGEMENT_NAMESPACE),
                ).fetchone()
                work_arrangement = (
                    CareerProfileStore._record_from_row(tracer_row, profile.profile_revision)
                    if tracer_row is not None
                    else None
                )
                evidence_by_id = {source.evidence_id: source for source in profile.source_evidence}
                active_ids = [
                    source.evidence_id for source in profile.source_evidence if source.active
                ]
                if command.evidence_mode == "profile_only":
                    included_ids: list[str] = []
                elif command.evidence_mode == "all":
                    included_ids = active_ids
                else:
                    unavailable = [
                        evidence_id
                        for evidence_id in command.selected_evidence_ids
                        if evidence_id not in evidence_by_id
                        or not evidence_by_id[evidence_id].active
                    ]
                    if unavailable:
                        raise CareerProfilePortabilityError(
                            "Selected Evidence is unavailable; reload the export choices"
                        )
                    included_ids = list(command.selected_evidence_ids)
                included_set = set(included_ids)
                omitted_ids = [
                    source.evidence_id
                    for source in profile.source_evidence
                    if source.evidence_id not in included_set
                ]
                evidence_bytes = {
                    evidence_id: self.complete_profile.vault.read(
                        EvidenceVault.storage_name(evidence_id),
                        evidence_by_id[evidence_id].sha256,
                    )
                    for evidence_id in included_ids
                }
                manifest = CareerProfileArchiveManifest(
                    schema_version=1,
                    exported_at=_now(),
                    source_profile_revision=profile.profile_revision,
                    source_authority_epoch=profile.authority_epoch,
                    items=[item for item in profile.items if item.value.kind != "work_arrangement"],
                    source_evidence=profile.source_evidence,
                    work_arrangement=work_arrangement,
                    evidence_inclusion=EvidenceInclusionManifest(
                        mode=command.evidence_mode,
                        included_evidence_ids=included_ids,
                        omitted_evidence_ids=omitted_ids,
                    ),
                )
                manifest_bytes = _canonical_json(manifest.model_dump(mode="json")).encode()
                if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                    raise CareerProfilePortabilityError(
                        "Career Profile export manifest exceeds its portable limit"
                    )
                uncompressed_size = len(manifest_bytes) + sum(
                    len(content) for content in evidence_bytes.values()
                )
                if uncompressed_size > MAX_ARCHIVE_BYTES:
                    raise CareerProfilePortabilityError(
                        "Career Profile export uncompressed content exceeds the "
                        "100 MiB aggregate limit"
                    )
                connection.rollback()
            except Exception:
                connection.rollback()
                raise

        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr(*_archive_member("manifest.json", manifest_bytes))
            for evidence_id in included_ids:
                archive.writestr(
                    *_archive_member(f"evidence/{evidence_id}.bin", evidence_bytes[evidence_id])
                )
        archive_bytes = archive_buffer.getvalue()
        if len(archive_bytes) > MAX_ARCHIVE_BYTES:
            raise CareerProfilePortabilityError(
                "Career Profile export exceeds the 100 MiB portable archive limit"
            )
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return CareerProfileExportResult(
            filename=f"JobOS-Career-Profile-{timestamp}.zip",
            sha256=sha256(archive_bytes).hexdigest(),
            byte_count=len(archive_bytes),
            content_base64=base64.b64encode(archive_bytes).decode(),
            included_evidence_ids=included_ids,
            omitted_evidence_ids=omitted_ids,
        )

    def restore_archive(
        self,
        *,
        principal: str,
        command: CareerProfileRestoreRequest,
    ) -> CareerProfileRestoreResult:
        archive_bytes = base64.b64decode(command.archive_base64, validate=True)
        archive_hash = sha256(archive_bytes).hexdigest()
        request_hash = sha256(
            _canonical_json(
                {
                    "command": "career_profile.restore",
                    "principal": principal,
                    "expected_profile_revision": command.expected_profile_revision,
                    "confirmation": command.confirmation,
                    "archive_sha256": archive_hash,
                }
            ).encode()
        ).hexdigest()
        replay = self._restore_receipt(
            principal=principal,
            idempotency_key=command.idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay

        manifest, included_bytes = self._parse_archive(archive_bytes)
        operation_id = _opaque_id("cprt_")
        staging_root, backup_root = self._operation_paths(operation_id)
        self._remove_path(staging_root)
        self._remove_path(backup_root)
        had_live_vault = self.evidence_root.exists()

        # Journal ownership before writing any restored bytes. A process death
        # during staging now leaves enough durable intent for startup recovery to
        # remove the partial vault rather than orphaning sensitive archive data.
        self._prepare_restore(
            operation_id=operation_id,
            principal=principal,
            command=command,
            request_hash=request_hash,
            had_live_vault=had_live_vault,
        )
        staging_vault = EvidenceVault(staging_root)
        try:
            staging_vault.initialize()
            for evidence_id, content in included_bytes.items():
                staging_vault.write(evidence_id, content)
        except Exception:
            self._remove_path(staging_root)
            self._sync_parent()
            self._delete_journal(operation_id)
            raise

        live_vault_moved = False
        swapped = False
        database_committed = False
        result: CareerProfileRestoreResult | None = None
        try:
            if had_live_vault:
                os.replace(self.evidence_root, backup_root)
                live_vault_moved = True
            os.replace(staging_root, self.evidence_root)
            swapped = True
            self._sync_parent()

            with connect_sqlite(self.database) as connection:
                connection.execute("PRAGMA secure_delete = ON")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    # The restore's own journal is expected here. Only an owner
                    # erasure or a missing/replaced restore intent blocks commit.
                    ensure_no_pending_erasure_operation(connection)
                    self._require_restore_journal(
                        connection,
                        operation_id=operation_id,
                        request_hash=request_hash,
                        expected_phase="swap_pending",
                    )
                    self._ensure_no_active_turn(connection)
                    row = connection.execute(
                        "SELECT head_revision, authority_epoch FROM career_profiles "
                        "WHERE profile_id = ?",
                        (PROFILE_ID,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("Career Profile storage is not initialized")
                    current_revision = int(row[0])
                    if current_revision != command.expected_profile_revision:
                        raise CareerProfileRevisionConflict(current_revision)
                    baseline_revision = current_revision + 1
                    self._replace_database_baseline(
                        connection,
                        principal=principal,
                        baseline_revision=baseline_revision,
                        base_revision=current_revision,
                        authority_epoch=int(row[1]) + 1,
                        manifest=manifest,
                    )
                    profile = self.complete_profile._current_in_connection(  # noqa: SLF001
                        connection
                    )
                    result = CareerProfileRestoreResult(
                        profile=profile,
                        archive_sha256=archive_hash,
                        restored_evidence_ids=list(included_bytes),
                        unavailable_evidence_ids=list(
                            manifest.evidence_inclusion.omitted_evidence_ids
                        ),
                    )
                    connection.execute(
                        "INSERT INTO career_profile_restore_receipts("
                        "actor_principal, idempotency_key, request_hash, result_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            principal,
                            command.idempotency_key,
                            request_hash,
                            _canonical_json(result.model_dump(mode="json")),
                        ),
                    )
                    updated = connection.execute(
                        "UPDATE career_profile_restore_journal SET phase = 'db_committed' "
                        "WHERE operation_id = ? AND phase = 'swap_pending'",
                        (operation_id,),
                    )
                    if updated.rowcount != 1:
                        raise CareerProfilePortabilityError(
                            "Career Profile restore lost its durable journal ownership"
                        )
                    connection.commit()
                    database_committed = True
                except Exception:
                    connection.rollback()
                    raise
        except Exception:
            if not database_committed:
                # sqlite commit may report an I/O failure after the transaction
                # became durable. Trust the journal phase, not the Python line
                # reached, before deciding whether the vault may be rolled back.
                database_committed = self._restore_journal_phase(operation_id) == "db_committed"
            if not database_committed:
                if swapped or live_vault_moved:
                    self._rollback_vault_swap(
                        staging_root=staging_root,
                        backup_root=backup_root,
                        had_live_vault=had_live_vault,
                    )
                else:
                    self._remove_path(staging_root)
                    self._sync_parent()
                self._delete_journal(operation_id)
            raise

        assert result is not None
        # Cleanup and database remanence hardening are part of restore success,
        # not best-effort work. A failure leaves the db_committed journal so an
        # explicit retry or startup recovery must finish before success returns.
        self._finalize_committed_restore(
            operation_id=operation_id,
            staging_root=staging_root,
            backup_root=backup_root,
        )
        return result

    def recover_pending_restores(self) -> None:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            try:
                rows = connection.execute(
                    "SELECT operation_id, phase, had_live_vault "
                    "FROM career_profile_restore_journal ORDER BY created_at"
                ).fetchall()
            except Exception as error:
                if "no such table" in str(error).lower():
                    return
                raise
        for operation_id_value, phase_value, had_live_value in rows:
            operation_id = str(operation_id_value)
            staging_root, backup_root = self._operation_paths(operation_id)
            phase = str(phase_value)
            if phase == "db_committed":
                self._finalize_committed_restore(
                    operation_id=operation_id,
                    staging_root=staging_root,
                    backup_root=backup_root,
                )
                continue
            if phase != "swap_pending":
                raise CareerProfilePortabilityError(
                    "Career Profile restore journal has an invalid recovery phase"
                )
            self._rollback_vault_swap(
                staging_root=staging_root,
                backup_root=backup_root,
                had_live_vault=bool(had_live_value),
            )
            self._delete_journal(operation_id)

    def _prepare_restore(
        self,
        *,
        operation_id: str,
        principal: str,
        command: CareerProfileRestoreRequest,
        request_hash: str,
        had_live_vault: bool,
    ) -> None:
        with connect_sqlite(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                ensure_no_pending_profile_operation(connection)
                self._ensure_no_active_turn(connection)
                replay = connection.execute(
                    "SELECT request_hash, result_json FROM career_profile_restore_receipts "
                    "WHERE actor_principal = ? AND idempotency_key = ?",
                    (principal, command.idempotency_key),
                ).fetchone()
                if replay is not None:
                    if not secrets.compare_digest(str(replay[0]), request_hash):
                        raise CareerProfileIdempotencyConflict
                    if replay[1] is None:
                        raise CareerProfilePortabilityError(
                            "Career Profile restore replay was invalidated by permanent erasure"
                        )
                    raise CareerProfilePortabilityError(
                        "Career Profile restore completed while this request was preparing"
                    )
                row = connection.execute(
                    "SELECT head_revision FROM career_profiles WHERE profile_id = ?",
                    (PROFILE_ID,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Career Profile storage is not initialized")
                current_revision = int(row[0])
                if current_revision != command.expected_profile_revision:
                    raise CareerProfileRevisionConflict(current_revision)
                if connection.execute(
                    "SELECT 1 FROM career_profile_restore_journal LIMIT 1"
                ).fetchone():
                    raise CareerProfileRestoreBusy(
                        "Another Career Profile restore is being recovered"
                    )
                connection.execute(
                    "INSERT INTO career_profile_restore_journal("
                    "operation_id, actor_principal, idempotency_key, request_hash, phase, "
                    "had_live_vault) VALUES (?, ?, ?, ?, 'swap_pending', ?)",
                    (
                        operation_id,
                        principal,
                        command.idempotency_key,
                        request_hash,
                        int(had_live_vault),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _ensure_no_active_turn(connection) -> None:
        ensure_no_active_conversation_turn(
            connection,
            conflict_type=CareerProfileRestoreBusy,
            message="Finish or stop active agent work before restoring the Career Profile",
        )

    @staticmethod
    def _require_restore_journal(
        connection,
        *,
        operation_id: str,
        request_hash: str,
        expected_phase: str,
    ) -> None:
        row = connection.execute(
            "SELECT request_hash, phase FROM career_profile_restore_journal WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if (
            row is None
            or not secrets.compare_digest(str(row[0]), request_hash)
            or str(row[1]) != expected_phase
        ):
            raise CareerProfilePortabilityError(
                "Career Profile restore journal ownership or phase is invalid"
            )

    def _restore_journal_phase(self, operation_id: str) -> str | None:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT phase FROM career_profile_restore_journal WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def _replace_database_baseline(
        self,
        connection,
        *,
        principal: str,
        baseline_revision: int,
        base_revision: int,
        authority_epoch: int,
        manifest: CareerProfileArchiveManifest,
    ) -> None:
        timestamp = _now()
        # Non-erasure restore replaces mutable profile state, not immutable turn
        # context. Keep exactly the snapshots that completed turns still bind so
        # retry/continuation can resolve their original bytes and hashes.
        connection.execute(
            "DELETE FROM career_profile_snapshots AS snapshot WHERE NOT EXISTS ("
            "SELECT 1 FROM conversation_turns AS turn "
            "WHERE turn.career_profile_snapshot_id = snapshot.snapshot_id)"
        )
        connection.execute(
            "DELETE FROM career_profile_context_snapshots AS snapshot WHERE NOT EXISTS ("
            "SELECT 1 FROM conversation_turns AS turn "
            "WHERE turn.career_profile_context_snapshot_id = snapshot.snapshot_id)"
        )
        for table in (
            "career_profile_audit_events",
            "career_profile_change_proposals",
            "career_profile_collaboration_idempotency",
            "career_profile_context_idempotency",
            "career_profile_complete_idempotency",
            "career_profile_intent_grants",
            "career_profile_complete_revisions",
            "career_profile_idempotency",
            "career_profile_revisions",
            "career_profile_items",
            "career_profile_evidence",
            "career_profile_records",
        ):
            connection.execute(f"DELETE FROM {table}")
        connection.execute(
            "UPDATE career_profile_context_grants SET mode = 'none', "
            "selected_item_ids_json = '[]', selected_areas_json = '[]', updated_at = ?",
            (timestamp,),
        )
        connection.execute(
            "UPDATE career_profiles SET head_revision = ?, authority_epoch = ?, updated_at = ? "
            "WHERE profile_id = ?",
            (baseline_revision, authority_epoch, timestamp, PROFILE_ID),
        )

        for item in manifest.items:
            connection.execute(
                "INSERT INTO career_profile_items("
                "item_id, value_json, provenance_json, review_status, evidence_ids_json, "
                "item_revision, actor_principal, active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, 1, ?, ?)",
                (
                    item.item_id,
                    _canonical_json(item.value.model_dump(mode="json")),
                    _canonical_json(item.provenance.model_dump(mode="json")),
                    item.review_status,
                    _canonical_json(item.evidence_ids),
                    item.actor_principal,
                    item.created_at,
                    timestamp,
                ),
            )

        included = set(manifest.evidence_inclusion.included_evidence_ids)
        for evidence in manifest.source_evidence:
            connection.execute(
                "INSERT INTO career_profile_evidence("
                "evidence_id, original_filename, media_type, content_sha256, byte_count, "
                "captured_at, imported_at, provenance_json, storage_name, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence.evidence_id,
                    evidence.original_filename,
                    evidence.media_type,
                    evidence.sha256,
                    evidence.byte_count,
                    evidence.captured_at,
                    evidence.imported_at,
                    _canonical_json(evidence.provenance.model_dump(mode="json")),
                    EvidenceVault.storage_name(evidence.evidence_id),
                    int(evidence.evidence_id in included),
                ),
            )

        if manifest.work_arrangement is not None:
            work = manifest.work_arrangement
            work_value = work.value.model_dump(mode="json")
            connection.execute(
                "INSERT INTO career_profile_records("
                "record_id, profile_id, namespace, item_revision, value_json, actor_principal, "
                "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
                (
                    work.record_id,
                    PROFILE_ID,
                    WORK_ARRANGEMENT_NAMESPACE,
                    _canonical_json(work_value),
                    work.actor_principal,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "INSERT INTO career_profile_revisions("
                "revision_id, profile_revision, profile_id, record_id, namespace, item_revision, "
                "actor_principal, base_profile_revision, operation, previous_value_json, "
                "resulting_value_json, changed_fields_json, restored_from_profile_revision) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'set', NULL, ?, ?, NULL)",
                (
                    _opaque_id("cpv_"),
                    baseline_revision,
                    PROFILE_ID,
                    work.record_id,
                    WORK_ARRANGEMENT_NAMESPACE,
                    principal,
                    base_revision,
                    _canonical_json(work_value),
                    _canonical_json(["mode", "strength", "note"]),
                ),
            )

        baseline_revision_id = _opaque_id("cpv_")
        connection.execute(
            "INSERT INTO career_profile_complete_revisions("
            "revision_id, profile_revision, base_profile_revision, actor_principal, actor_kind, "
            "operation, item_id, evidence_id, before_json, after_json, affected_fields_json, "
            "reason, proposal_id, undo_of_revision_id) "
            "VALUES (?, ?, ?, ?, 'direct_user', 'item.upsert', NULL, NULL, NULL, NULL, ?, ?, "
            "NULL, NULL)",
            (
                baseline_revision_id,
                baseline_revision,
                base_revision,
                principal,
                _canonical_json(
                    ["items", "source_evidence", "search_preferences.work_arrangement"]
                ),
                "Portable Career Profile restored as a new baseline",
            ),
        )
        connection.execute(
            "INSERT INTO career_profile_audit_events("
            "actor_principal, action, profile_revision, base_profile_revision, "
            "affected_fields_json, revision_id) VALUES (?, 'career_profile.baseline.restore', "
            "?, ?, ?, ?)",
            (
                principal,
                baseline_revision,
                base_revision,
                _canonical_json(
                    ["items", "source_evidence", "search_preferences.work_arrangement"]
                ),
                baseline_revision_id,
            ),
        )

    def _parse_archive(
        self, archive_bytes: bytes
    ) -> tuple[CareerProfileArchiveManifest, dict[str, bytes]]:
        try:
            archive = zipfile.ZipFile(BytesIO(archive_bytes))
        except (zipfile.BadZipFile, OSError) as error:
            raise CareerProfilePortabilityError(
                "Career Profile archive is not a valid ZIP file"
            ) from error
        with archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise CareerProfilePortabilityError(
                    "Career Profile archive contains too many files"
                )
            if len(names) != len(set(names)):
                raise CareerProfilePortabilityError(
                    "Career Profile archive contains duplicate file names"
                )
            if any(member.is_dir() or member.flag_bits & 0x1 for member in members):
                raise CareerProfilePortabilityError(
                    "Career Profile archive contains unsupported files"
                )
            if any(
                name != "manifest.json"
                and not re.fullmatch(r"evidence/cpe_[A-Za-z0-9_-]{16,64}\.bin", name)
                for name in names
            ):
                raise CareerProfilePortabilityError(
                    "Career Profile archive contains an unexpected file"
                )
            if "manifest.json" not in names:
                raise CareerProfilePortabilityError(
                    "Career Profile archive is missing manifest.json"
                )
            if sum(member.file_size for member in members) > MAX_ARCHIVE_BYTES:
                raise CareerProfilePortabilityError(
                    "Career Profile archive uncompressed content expands beyond the "
                    "100 MiB aggregate limit"
                )
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise CareerProfilePortabilityError("Career Profile archive manifest is too large")
            try:
                manifest = CareerProfileArchiveManifest.model_validate_json(
                    archive.read(manifest_info)
                )
            except (ValidationError, ValueError, UnicodeDecodeError) as error:
                raise CareerProfilePortabilityError(
                    "Career Profile archive manifest is invalid"
                ) from error
            expected_names = {
                "manifest.json",
                *(
                    f"evidence/{evidence_id}.bin"
                    for evidence_id in manifest.evidence_inclusion.included_evidence_ids
                ),
            }
            if set(names) != expected_names:
                raise CareerProfilePortabilityError(
                    "Career Profile archive contains an unexpected or missing Evidence file"
                )
            evidence_by_id = {source.evidence_id: source for source in manifest.source_evidence}
            contents: dict[str, bytes] = {}
            for evidence_id in manifest.evidence_inclusion.included_evidence_ids:
                info = archive.getinfo(f"evidence/{evidence_id}.bin")
                if info.file_size <= 0 or info.file_size > MAX_EVIDENCE_BYTES:
                    raise CareerProfilePortabilityError(
                        "Career Profile archive contains an invalid Evidence file size"
                    )
                content = archive.read(info)
                metadata = evidence_by_id[evidence_id]
                if len(content) != metadata.byte_count or not secrets.compare_digest(
                    sha256(content).hexdigest(), metadata.sha256
                ):
                    raise CareerProfilePortabilityError(
                        "Career Profile archive Evidence failed its integrity check"
                    )
                contents[evidence_id] = content
            return manifest, contents

    def _restore_receipt(
        self, *, principal: str, idempotency_key: str, request_hash: str
    ) -> CareerProfileRestoreResult | None:
        # Read receipt + journal from one SQLite snapshot. Receipt insertion and
        # the db_committed phase transition are atomic, so a replay can never
        # return the receipt while mandatory cleanup remains undiscovered.
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT request_hash, result_json FROM career_profile_restore_receipts "
                "WHERE actor_principal = ? AND idempotency_key = ?",
                (principal, idempotency_key),
            ).fetchone()
            pending = connection.execute(
                "SELECT operation_id, phase FROM career_profile_restore_journal "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
            connection.rollback()

        if pending is not None:
            operation_id = str(pending[0])
            phase = str(pending[1])
            staging_root, backup_root = self._operation_paths(operation_id)
            if phase == "db_committed":
                self._finalize_committed_restore(
                    operation_id=operation_id,
                    staging_root=staging_root,
                    backup_root=backup_root,
                )
                return self._restore_receipt(
                    principal=principal,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
            if phase == "swap_pending":
                raise CareerProfileRestoreBusy("Another Career Profile restore is being recovered")
            raise CareerProfilePortabilityError(
                "Career Profile restore journal has an invalid recovery phase"
            )

        if row is None:
            return None
        if not secrets.compare_digest(str(row[0]), request_hash):
            raise CareerProfileIdempotencyConflict
        if row[1] is None:
            raise CareerProfilePortabilityError(
                "Career Profile restore replay was invalidated by permanent erasure"
            )
        return CareerProfileRestoreResult.model_validate_json(str(row[1]))

    def _operation_paths(self, operation_id: str) -> tuple[Path, Path]:
        if not re.fullmatch(r"cprt_[A-Za-z0-9_-]{16,64}", operation_id):
            raise CareerProfilePortabilityError(
                "Career Profile restore journal identifier is invalid"
            )
        parent = self.evidence_root.parent
        staging = parent / f".{self.evidence_root.name}.{operation_id}.staging"
        backup = parent / f".{self.evidence_root.name}.{operation_id}.backup"
        return staging, backup

    def _rollback_vault_swap(
        self,
        *,
        staging_root: Path,
        backup_root: Path,
        had_live_vault: bool,
    ) -> None:
        if backup_root.exists():
            self._remove_path(self.evidence_root)
            os.replace(backup_root, self.evidence_root)
        elif not had_live_vault:
            self._remove_path(self.evidence_root)
        self._remove_path(staging_root)
        self._sync_parent()

    def _finalize_committed_restore(
        self,
        *,
        operation_id: str,
        staging_root: Path,
        backup_root: Path,
    ) -> None:
        with connect_sqlite(f"file:{self.database}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT phase FROM career_profile_restore_journal WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None or str(row[0]) != "db_committed":
            raise CareerProfilePortabilityError(
                "Committed Career Profile restore journal was not found"
            )
        if not self.evidence_root.exists():
            raise CareerProfilePortabilityError(
                "Committed Career Profile restore lost its Evidence vault"
            )
        self._remove_path(staging_root)
        self._remove_path(backup_root)
        self._sync_parent()
        self._harden_database()
        self._delete_journal(operation_id)

    def _harden_database(self) -> None:
        """Scrub deleted baseline pages and truncate rollback/WAL remnants."""
        with connect_sqlite(self.database) as connection:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def _delete_journal(self, operation_id: str) -> None:
        with connect_sqlite(self.database) as connection:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM career_profile_restore_journal WHERE operation_id = ?",
                (operation_id,),
            )
            connection.commit()

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    def _sync_parent(self) -> None:
        self.evidence_root.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(self.evidence_root.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

from __future__ import annotations

import base64
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

import jobos_api.career_profile_portability as portability_module
import pytest
from jobos_api.career_profile import (
    CareerProfileErasureInProgress,
    CareerProfileRevisionConflict,
    CareerProfileSnapshotRequest,
    CareerProfileStore,
    WorkArrangementMutation,
)
from jobos_api.career_profile_collaboration import CareerProfileCollaborationStore
from jobos_api.career_profile_complete import (
    CareerProfileCompleteStore,
    CareerProfileResetRequest,
    EvidenceErasureRequest,
    EvidenceImportRequest,
    EvidenceVault,
    ProfileItemMutation,
    ProfileItemRemoval,
)
from jobos_api.career_profile_context import (
    CareerProfileContextScopeUpdate,
    CareerProfileContextStore,
)
from jobos_api.career_profile_portability import (
    CareerProfileExportRequest,
    CareerProfilePortabilityError,
    CareerProfilePortabilityService,
    CareerProfileRestoreRequest,
)
from jobos_api.state_store import JobOsStateStore

DEVICE_PRINCIPAL = "device:primary-device"
AGENT_ID = "job-hunter"
AGENT_TOKEN = "synthetic-agent-token"


def _initialize(root: Path, *, connected: bool = False):
    database = root / "state" / "jobos.db"
    evidence_root = root / "evidence"
    state = JobOsStateStore(database)
    state.initialize(owner_device_id="primary-device")
    complete = CareerProfileCompleteStore(database, evidence_root)
    complete.initialize()
    tracer = CareerProfileStore(database)
    tracer.initialize()
    context: CareerProfileContextStore | None = None
    if connected:
        collaboration = CareerProfileCollaborationStore(database, complete)
        collaboration.initialize(
            agent_id=AGENT_ID,
            display_name="Job Hunter",
            token=AGENT_TOKEN,
        )
        context = CareerProfileContextStore(database, complete)
        context.initialize()
    portability = CareerProfilePortabilityService(database, evidence_root)
    return state, database, evidence_root, complete, tracer, context, portability


def _import_evidence(
    complete: CareerProfileCompleteStore,
    *,
    expected_revision: int,
    key: str,
    content: bytes,
    filename: str = "(FAKE)-source.txt",
):
    return complete.import_evidence(
        principal=DEVICE_PRINCIPAL,
        command=EvidenceImportRequest.model_validate(
            {
                "expected_profile_revision": expected_revision,
                "idempotency_key": key,
                "original_filename": filename,
                "media_type": "text/plain",
                "provenance": {
                    "source_kind": "supporting_document",
                    "source_label": filename,
                    "method": "user_import",
                },
                "content_base64": base64.b64encode(content).decode(),
                "extractions": [],
            }
        ),
    )


def _source_archive(
    root: Path,
    *,
    evidence_mode: str = "profile_only",
    content: bytes = b"(FAKE) portable Evidence\n",
):
    _, _, _, complete, _, _, portability = _initialize(root)
    profile = complete.upsert_item(
        principal=DEVICE_PRINCIPAL,
        command=ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": 0,
                "idempotency_key": "source-skill-0001",
                "value": {
                    "kind": "skill",
                    "name": "(FAKE) portable lifecycle skill",
                },
            }
        ),
    )
    profile = _import_evidence(
        complete,
        expected_revision=profile.profile_revision,
        key="source-evidence-0001",
        content=content,
    )
    evidence_id = profile.source_evidence[0].evidence_id
    selected = [evidence_id] if evidence_mode == "selected" else []
    exported = portability.export_archive(
        CareerProfileExportRequest(
            expected_profile_revision=profile.profile_revision,
            evidence_mode=evidence_mode,
            selected_evidence_ids=selected,
        )
    )
    return exported, evidence_id


def _restore_command(exported, *, expected_revision: int, key: str):
    return CareerProfileRestoreRequest(
        expected_profile_revision=expected_revision,
        idempotency_key=key,
        confirmation="RESTORE_CAREER_PROFILE_BASELINE",
        archive_base64=exported.content_base64,
    )


def _bind_completed_turn(
    state: JobOsStateStore,
    database: Path,
    complete: CareerProfileCompleteStore,
    context: CareerProfileContextStore,
    *,
    key: str,
):
    current = complete.current()
    context.update_scope(
        principal=DEVICE_PRINCIPAL,
        agent_id=AGENT_ID,
        command=CareerProfileContextScopeUpdate(
            expected_profile_revision=current.profile_revision,
            expected_authority_epoch=current.authority_epoch,
            idempotency_key=f"{key}-scope",
            mode="broader",
        ),
    )
    conversation = state.conversation_store(state.first_active_conversation_id())
    created = conversation.create_turn(
        text="(FAKE) preserve this exact Career Profile binding",
        context={},
        idempotency_key=f"{key}-turn",
        actor_id="primary-device",
        career_profile_principal=DEVICE_PRINCIPAL,
        career_profile_context=context,
        career_profile_agent_id=AGENT_ID,
    )
    turn_id = str(created["turn_id"])
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE conversation_turns SET status = 'completed' WHERE turn_id = ?",
            (turn_id,),
        )
        binding = connection.execute(
            "SELECT career_profile_snapshot_id, career_profile_revision, "
            "career_profile_content_hash, career_profile_context_snapshot_id, "
            "career_profile_context_agent_id, career_profile_context_revision, "
            "career_profile_context_authority_epoch, career_profile_context_content_hash "
            "FROM conversation_turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
    assert binding is not None
    assert all(value is not None for value in binding)
    return turn_id, binding


def test_pending_restore_blocks_profile_evidence_export_and_legacy_snapshot_operations(
    tmp_path: Path,
):
    _, database, _, complete, tracer, _, portability = _initialize(tmp_path)
    profile = _import_evidence(
        complete,
        expected_revision=0,
        key="pending-restore-existing-evidence",
        content=b"(FAKE) pending restore Evidence",
    )
    evidence_id = profile.source_evidence[0].evidence_id
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO career_profile_restore_journal("
            "operation_id, actor_principal, idempotency_key, request_hash, phase, "
            "had_live_vault) VALUES (?, ?, ?, ?, 'swap_pending', 1)",
            (
                "cprt_pendingrestore01",
                DEVICE_PRINCIPAL,
                "pending-restore-0001",
                "a" * 64,
            ),
        )

    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        complete.read_evidence(evidence_id)
    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        complete.evidence_metadata(evidence_id)
    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        portability.export_archive(
            CareerProfileExportRequest(
                expected_profile_revision=profile.profile_revision,
                evidence_mode="profile_only",
            )
        )
    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        complete.remove_evidence(
            principal=DEVICE_PRINCIPAL,
            evidence_id=evidence_id,
            command=ProfileItemRemoval(
                expected_profile_revision=profile.profile_revision,
                idempotency_key="blocked-evidence-remove-0001",
            ),
        )
    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        _import_evidence(
            complete,
            expected_revision=profile.profile_revision,
            key="blocked-evidence-import-0001",
            content=b"(FAKE) blocked import",
        )
    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        complete.upsert_item(
            principal=DEVICE_PRINCIPAL,
            command=ProfileItemMutation.model_validate(
                {
                    "expected_profile_revision": profile.profile_revision,
                    "idempotency_key": "blocked-profile-mutation-0001",
                    "value": {"kind": "skill", "name": "(FAKE) blocked"},
                }
            ),
        )
    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        tracer.set_work_arrangement(
            principal=DEVICE_PRINCIPAL,
            command=WorkArrangementMutation.model_validate(
                {
                    "expected_profile_revision": profile.profile_revision,
                    "idempotency_key": "blocked-tracer-mutation-0001",
                    "value": {"mode": "remote", "strength": "preference"},
                }
            ),
        )
    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        tracer.create_snapshot(
            principal=DEVICE_PRINCIPAL,
            request=CareerProfileSnapshotRequest(),
        )


def test_restore_baselines_advance_the_global_revision_and_reject_delayed_commands(
    tmp_path: Path,
):
    exported, _ = _source_archive(tmp_path / "source")
    _, _, _, complete, _, _, portability = _initialize(tmp_path / "target")
    original = complete.upsert_item(
        principal=DEVICE_PRINCIPAL,
        command=ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": 0,
                "idempotency_key": "target-before-restore-0001",
                "value": {"kind": "skill", "name": "(FAKE) old baseline"},
            }
        ),
    )
    delayed = ProfileItemMutation.model_validate(
        {
            "expected_profile_revision": original.profile_revision,
            "idempotency_key": "delayed-before-restore-0001",
            "value": {"kind": "skill", "name": "(FAKE) delayed stale write"},
        }
    )

    first = portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=_restore_command(
            exported,
            expected_revision=original.profile_revision,
            key="monotonic-restore-0001",
        ),
    )
    assert first.profile.profile_revision == original.profile_revision + 1
    with pytest.raises(CareerProfileRevisionConflict) as conflict:
        complete.upsert_item(principal=DEVICE_PRINCIPAL, command=delayed)
    assert conflict.value.current_revision == first.profile.profile_revision

    second = portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=_restore_command(
            exported,
            expected_revision=first.profile.profile_revision,
            key="monotonic-restore-0002",
        ),
    )
    assert second.profile.profile_revision == first.profile.profile_revision + 1


def test_non_erasure_restore_preserves_turn_referenced_legacy_and_complete_snapshots(
    tmp_path: Path,
):
    exported, _ = _source_archive(tmp_path / "source")
    state, database, _, complete, tracer, context, portability = _initialize(
        tmp_path / "target", connected=True
    )
    assert context is not None
    current = complete.upsert_item(
        principal=DEVICE_PRINCIPAL,
        command=ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": 0,
                "idempotency_key": "snapshot-target-skill-0001",
                "value": {"kind": "skill", "name": "(FAKE) bound old skill"},
            }
        ),
    )
    turn_id, binding = _bind_completed_turn(
        state,
        database,
        complete,
        context,
        key="preserved-snapshot",
    )
    legacy_snapshot_id = str(binding[0])
    context_snapshot_id = str(binding[3])

    restored = portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=_restore_command(
            exported,
            expected_revision=current.profile_revision,
            key="preserve-bound-snapshots-0001",
        ),
    )
    assert restored.profile.profile_revision == current.profile_revision + 1

    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT career_profile_snapshot_id, career_profile_revision, "
            "career_profile_content_hash, career_profile_context_snapshot_id, "
            "career_profile_context_agent_id, career_profile_context_revision, "
            "career_profile_context_authority_epoch, career_profile_context_content_hash "
            "FROM conversation_turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        assert after == binding
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_snapshots WHERE snapshot_id = ?",
            (legacy_snapshot_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_context_snapshots WHERE snapshot_id = ?",
            (context_snapshot_id,),
        ).fetchone() == (1,)
    assert tracer.get_snapshot(legacy_snapshot_id, principal=DEVICE_PRINCIPAL).snapshot_id == (
        legacy_snapshot_id
    )
    assert context.get_snapshot(context_snapshot_id, agent_id=AGENT_ID).snapshot_id == (
        context_snapshot_id
    )


def test_restore_finalization_failure_is_reported_and_replay_finishes_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    exported, _ = _source_archive(tmp_path / "source", evidence_mode="selected")
    _, database, evidence_root, _, _, _, portability = _initialize(tmp_path / "target")
    command = _restore_command(
        exported,
        expected_revision=0,
        key="finalization-retry-restore-0001",
    )
    real_finalize = portability._finalize_committed_restore  # noqa: SLF001
    calls = 0

    def fail_once(**kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic restore finalization failure")
        real_finalize(**kwargs)

    monkeypatch.setattr(portability, "_finalize_committed_restore", fail_once)
    with pytest.raises(OSError, match="synthetic restore finalization failure"):
        portability.restore_archive(principal=DEVICE_PRINCIPAL, command=command)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT phase FROM career_profile_restore_journal"
        ).fetchone() == ("db_committed",)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_restore_receipts"
        ).fetchone() == (1,)

    replay = portability.restore_archive(principal=DEVICE_PRINCIPAL, command=command)
    assert replay.baseline_created is True
    assert calls == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_restore_journal"
        ).fetchone() == (0,)
    assert not list(evidence_root.parent.glob(f".{evidence_root.name}.cprt_*.*"))


def test_restore_requires_secure_delete_checkpoint_and_vacuum_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    exported, _ = _source_archive(tmp_path / "source")
    _, database, _, _, _, _, portability = _initialize(tmp_path / "target")
    command = _restore_command(
        exported,
        expected_revision=0,
        key="hardening-retry-restore-0001",
    )
    real_harden = getattr(portability, "_harden_database", None)
    calls = 0

    def fail_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic restore hardening failure")
        assert callable(real_harden)
        real_harden()

    monkeypatch.setattr(portability, "_harden_database", fail_once, raising=False)
    with pytest.raises(OSError, match="synthetic restore hardening failure"):
        portability.restore_archive(principal=DEVICE_PRINCIPAL, command=command)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT phase FROM career_profile_restore_journal"
        ).fetchone() == ("db_committed",)

    replay = portability.restore_archive(principal=DEVICE_PRINCIPAL, command=command)
    assert replay.baseline_created is True
    assert calls == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_restore_journal"
        ).fetchone() == (0,)


def test_export_and_restore_share_the_uncompressed_aggregate_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    exported, _ = _source_archive(
        tmp_path / "source",
        evidence_mode="all",
        content=b"A" * 8192,
    )
    archive_bytes = base64.b64decode(exported.content_base64)
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        uncompressed_size = sum(member.file_size for member in archive.infolist())
    compressed_size = len(archive_bytes)
    assert compressed_size < uncompressed_size
    synthetic_limit = compressed_size + (uncompressed_size - compressed_size) // 2
    monkeypatch.setattr(portability_module, "MAX_ARCHIVE_BYTES", synthetic_limit)

    _, _, _, source_complete, _, _, source_portability = _initialize(tmp_path / "source")
    with pytest.raises(CareerProfilePortabilityError, match="uncompressed"):
        source_portability.export_archive(
            CareerProfileExportRequest(
                expected_profile_revision=source_complete.current().profile_revision,
                evidence_mode="all",
            )
        )

    _, _, _, _, _, _, target_portability = _initialize(tmp_path / "target")
    with pytest.raises(CareerProfilePortabilityError, match="expands|uncompressed"):
        target_portability.restore_archive(
            principal=DEVICE_PRINCIPAL,
            command=_restore_command(
                exported,
                expected_revision=0,
                key="aggregate-limit-restore-0001",
            ),
        )


def test_restore_rolls_back_when_staging_install_fails_after_live_vault_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    exported, _ = _source_archive(tmp_path / "source", evidence_mode="selected")
    _, database, evidence_root, complete, _, _, portability = _initialize(tmp_path / "target")
    target = _import_evidence(
        complete,
        expected_revision=0,
        key="pre-install-failure-live-evidence-0001",
        content=b"(FAKE) readable old live Evidence",
    )
    old_evidence_id = target.source_evidence[0].evidence_id
    real_replace = portability_module.os.replace
    replace_calls = 0
    observed_between_renames: list[tuple[bool, bool]] = []

    def fail_staging_install(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            backup_roots = list(evidence_root.parent.glob(f".{evidence_root.name}.cprt_*.backup"))
            observed_between_renames.append((evidence_root.exists(), len(backup_roots) == 1))
            raise OSError("synthetic staging install failure")
        real_replace(source, destination)

    monkeypatch.setattr(portability_module.os, "replace", fail_staging_install)
    with pytest.raises(OSError, match="synthetic staging install failure"):
        portability.restore_archive(
            principal=DEVICE_PRINCIPAL,
            command=_restore_command(
                exported,
                expected_revision=target.profile_revision,
                key="install-failure-restore-0001",
            ),
        )

    assert observed_between_renames == [(False, True)]
    assert replace_calls == 3
    assert complete.read_evidence(old_evidence_id) == b"(FAKE) readable old live Evidence"
    assert complete.current().profile_revision == target.profile_revision
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_restore_journal"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_restore_receipts"
        ).fetchone() == (0,)
    assert not list(evidence_root.parent.glob(f".{evidence_root.name}.cprt_*.*"))


def test_restore_journals_before_staging_so_crash_recovery_removes_partial_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    exported, _ = _source_archive(tmp_path / "source", evidence_mode="selected")
    _, database, evidence_root, complete, _, _, portability = _initialize(tmp_path / "target")
    target = _import_evidence(
        complete,
        expected_revision=0,
        key="pre-crash-live-evidence-0001",
        content=b"(FAKE) old live Evidence",
    )
    old_evidence_id = target.source_evidence[0].evidence_id
    real_write = EvidenceVault.write
    observed_journal_counts: list[int] = []

    class SimulatedProcessCrash(BaseException):
        pass

    def crash_after_staging_write(self, evidence_id: str, content: bytes) -> str:
        real_write(self, evidence_id, content)
        with sqlite3.connect(database) as connection:
            observed_journal_counts.append(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM career_profile_restore_journal"
                    ).fetchone()[0]
                )
            )
        raise SimulatedProcessCrash

    monkeypatch.setattr(EvidenceVault, "write", crash_after_staging_write)
    with pytest.raises(SimulatedProcessCrash):
        portability.restore_archive(
            principal=DEVICE_PRINCIPAL,
            command=_restore_command(
                exported,
                expected_revision=target.profile_revision,
                key="crash-during-staging-restore-0001",
            ),
        )

    assert observed_journal_counts == [1]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT phase FROM career_profile_restore_journal"
        ).fetchone() == ("swap_pending",)
    assert list(evidence_root.parent.glob(f".{evidence_root.name}.cprt_*.staging"))
    with pytest.raises(CareerProfileErasureInProgress, match="restore"):
        complete.read_evidence(old_evidence_id)

    portability.recover_pending_restores()
    assert complete.read_evidence(old_evidence_id) == b"(FAKE) old live Evidence"
    assert not list(evidence_root.parent.glob(f".{evidence_root.name}.cprt_*.staging"))
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_restore_journal"
        ).fetchone() == (0,)


def test_evidence_erasure_scrubs_context_snapshots_turn_binding_and_restore_payload(
    tmp_path: Path,
):
    exported, evidence_id = _source_archive(tmp_path / "source", evidence_mode="selected")
    state, database, _, complete, _, context, portability = _initialize(
        tmp_path / "target", connected=True
    )
    assert context is not None
    restore = _restore_command(
        exported,
        expected_revision=0,
        key="erase-restored-evidence-0001",
    )
    restored = portability.restore_archive(principal=DEVICE_PRINCIPAL, command=restore)
    turn_id, binding = _bind_completed_turn(
        state,
        database,
        complete,
        context,
        key="erase-evidence-binding",
    )
    legacy_snapshot_id = str(binding[0])
    context_snapshot_id = str(binding[3])

    complete.erase_evidence(
        principal=DEVICE_PRINCIPAL,
        evidence_id=evidence_id,
        command=EvidenceErasureRequest(
            expected_profile_revision=restored.profile.profile_revision,
            idempotency_key="permanent-restored-evidence-erase-0001",
            confirmation="ERASE_EVIDENCE_PERMANENTLY",
        ),
    )

    with sqlite3.connect(database) as connection:
        turn_binding = connection.execute(
            "SELECT career_profile_snapshot_id, career_profile_context_snapshot_id, "
            "career_profile_context_agent_id, career_profile_context_revision, "
            "career_profile_context_authority_epoch, career_profile_context_content_hash "
            "FROM conversation_turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        assert turn_binding == (legacy_snapshot_id, None, None, None, None, None)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_context_snapshots WHERE snapshot_id = ?",
            (context_snapshot_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_snapshots WHERE snapshot_id = ?",
            (legacy_snapshot_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT result_json FROM career_profile_restore_receipts WHERE idempotency_key = ?",
            (restore.idempotency_key,),
        ).fetchone() == (None,)
        database_dump = "\n".join(connection.iterdump())
    assert evidence_id not in database_dump
    with pytest.raises(CareerProfilePortabilityError, match="erasure|invalidated"):
        portability.restore_archive(principal=DEVICE_PRINCIPAL, command=restore)


def test_full_reset_scrubs_all_context_restore_payloads_bindings_and_grants(
    tmp_path: Path,
):
    exported, evidence_id = _source_archive(tmp_path / "source", evidence_mode="selected")
    state, database, _, complete, _, context, portability = _initialize(
        tmp_path / "target", connected=True
    )
    assert context is not None
    restore = _restore_command(
        exported,
        expected_revision=0,
        key="reset-restored-profile-0001",
    )
    restored = portability.restore_archive(principal=DEVICE_PRINCIPAL, command=restore)
    item_id = restored.profile.items[0].item_id
    turn_id, binding = _bind_completed_turn(
        state,
        database,
        complete,
        context,
        key="reset-profile-binding",
    )
    legacy_snapshot_id = str(binding[0])
    context_snapshot_id = str(binding[3])

    complete.reset_profile(
        principal=DEVICE_PRINCIPAL,
        command=CareerProfileResetRequest(
            expected_profile_revision=restored.profile.profile_revision,
            idempotency_key="permanent-complete-profile-reset-0001",
            confirmation="RESET_CAREER_PROFILE_PERMANENTLY",
        ),
    )

    with sqlite3.connect(database) as connection:
        binding_after = connection.execute(
            "SELECT career_profile_snapshot_id, career_profile_revision, "
            "career_profile_content_hash, career_profile_context_snapshot_id, "
            "career_profile_context_agent_id, career_profile_context_revision, "
            "career_profile_context_authority_epoch, career_profile_context_content_hash "
            "FROM conversation_turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        assert binding_after == (None,) * 8
        assert connection.execute("SELECT COUNT(*) FROM career_profile_snapshots").fetchone() == (
            0,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_context_snapshots"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_context_idempotency"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT mode, selected_item_ids_json, selected_areas_json "
            "FROM career_profile_context_grants WHERE agent_id = ?",
            (AGENT_ID,),
        ).fetchone() == ("none", "[]", "[]")
        assert connection.execute(
            "SELECT result_json FROM career_profile_restore_receipts WHERE idempotency_key = ?",
            (restore.idempotency_key,),
        ).fetchone() == (None,)
        database_dump = "\n".join(connection.iterdump())
    for sentinel in (
        evidence_id,
        item_id,
        "(FAKE) portable lifecycle skill",
        legacy_snapshot_id,
        context_snapshot_id,
    ):
        assert sentinel not in database_dump
    with pytest.raises(CareerProfilePortabilityError, match="erasure|invalidated"):
        portability.restore_archive(principal=DEVICE_PRINCIPAL, command=restore)

from __future__ import annotations

import base64
import json
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from jobos_api.career_profile import CareerProfileStore, WorkArrangementMutation
from jobos_api.career_profile_collaboration import CareerProfileCollaborationStore
from jobos_api.career_profile_complete import (
    CareerProfileCompleteStore,
    CareerProfileEvidenceNotFound,
    EvidenceImportRequest,
    ProfileItemMutation,
    ProfileItemRemoval,
)
from jobos_api.career_profile_context import (
    CareerProfileContextScopeUpdate,
    CareerProfileContextSelectionError,
    CareerProfileContextStore,
)
from jobos_api.career_profile_portability import (
    CareerProfileExportRequest,
    CareerProfilePortabilityError,
    CareerProfilePortabilityService,
    CareerProfileRestoreBusy,
    CareerProfileRestoreRequest,
)
from jobos_api.state_store import JobOsStateStore

DEVICE_PRINCIPAL = "device:primary-device"
AGENT_ID = "job-hunter"
AGENT_TOKEN = "synthetic-agent-token"
ACTIVE_EVIDENCE = b"(FAKE) active resume evidence\n"
REMOVED_EVIDENCE = b"(FAKE) removed portfolio evidence\n"


def initialize(tmp_path: Path):
    database = tmp_path / "state/jobos.db"
    evidence_root = tmp_path / "evidence"
    JobOsStateStore(database).initialize(owner_device_id="primary-device")
    complete = CareerProfileCompleteStore(database, evidence_root)
    complete.initialize()
    tracer = CareerProfileStore(database)
    tracer.initialize()
    collaboration = CareerProfileCollaborationStore(database, complete)
    collaboration.initialize(
        agent_id=AGENT_ID,
        display_name="Job Hunter",
        token=AGENT_TOKEN,
    )
    context = CareerProfileContextStore(database, complete)
    context.initialize()
    portability = CareerProfilePortabilityService(database, evidence_root)
    return database, evidence_root, complete, tracer, context, portability


def import_evidence(
    complete: CareerProfileCompleteStore,
    *,
    expected_revision: int,
    key: str,
    filename: str,
    content: bytes,
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
                    "source_kind": "resume",
                    "source_label": filename,
                    "method": "user_import",
                },
                "content_base64": base64.b64encode(content).decode(),
                "extractions": [],
            }
        ),
    )


def seed_profile(tmp_path: Path):
    database, evidence_root, complete, tracer, context, portability = initialize(tmp_path)
    profile = complete.upsert_item(
        principal=DEVICE_PRINCIPAL,
        command=ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": 0,
                "idempotency_key": "seed-skill-0001",
                "value": {"kind": "skill", "name": "(FAKE) TypeScript", "level": "advanced"},
            }
        ),
    )
    skill_id = profile.items[0].item_id
    profile = complete.upsert_item(
        principal=DEVICE_PRINCIPAL,
        command=ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": profile.profile_revision,
                "idempotency_key": "seed-claim-0001",
                "value": {
                    "kind": "claim",
                    "statement": "(FAKE) Increased launch conversion by 42%.",
                    "qualifiers": ["Use only for the synthetic launch project"],
                },
            }
        ),
    )
    active = import_evidence(
        complete,
        expected_revision=profile.profile_revision,
        key="seed-active-evidence-0001",
        filename="(FAKE)-resume.txt",
        content=ACTIVE_EVIDENCE,
    )
    active_id = next(item.evidence_id for item in active.source_evidence if item.active)
    removed = import_evidence(
        complete,
        expected_revision=active.profile_revision,
        key="seed-removed-evidence-0001",
        filename="(FAKE)-portfolio.txt",
        content=REMOVED_EVIDENCE,
    )
    removed_id = next(
        item.evidence_id for item in removed.source_evidence if item.evidence_id != active_id
    )
    profile = complete.remove_evidence(
        principal=DEVICE_PRINCIPAL,
        evidence_id=removed_id,
        command=ProfileItemRemoval(
            expected_profile_revision=removed.profile_revision,
            idempotency_key="remove-evidence-0001",
        ),
    )
    tracer.set_work_arrangement(
        principal=DEVICE_PRINCIPAL,
        command=WorkArrangementMutation.model_validate(
            {
                "expected_profile_revision": profile.profile_revision,
                "idempotency_key": "seed-work-arrangement-0001",
                "value": {
                    "mode": "remote",
                    "strength": "strong_preference",
                    "note": "(FAKE) Hybrid is okay for the right role.",
                },
            }
        ),
    )
    return {
        "database": database,
        "evidence_root": evidence_root,
        "complete": complete,
        "tracer": tracer,
        "context": context,
        "portability": portability,
        "skill_id": skill_id,
        "active_evidence_id": active_id,
        "removed_evidence_id": removed_id,
    }


def archive_manifest(content_base64: str) -> tuple[dict[str, object], set[str]]:
    with zipfile.ZipFile(BytesIO(base64.b64decode(content_base64))) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
    return manifest, names


def test_export_requires_an_explicit_evidence_choice_and_bundles_only_the_selected_source(
    tmp_path: Path,
):
    seeded = seed_profile(tmp_path)
    service: CareerProfilePortabilityService = seeded["portability"]
    revision = seeded["complete"].current().profile_revision

    with pytest.raises(ValueError):
        CareerProfileExportRequest.model_validate({"expected_profile_revision": revision})
    with pytest.raises(ValueError, match="selected Evidence"):
        CareerProfileExportRequest.model_validate(
            {
                "expected_profile_revision": revision,
                "evidence_mode": "selected",
                "selected_evidence_ids": [],
            }
        )

    exported = service.export_archive(
        CareerProfileExportRequest(
            expected_profile_revision=revision,
            evidence_mode="selected",
            selected_evidence_ids=[seeded["active_evidence_id"]],
        )
    )
    manifest, names = archive_manifest(exported.content_base64)

    assert exported.included_evidence_ids == [seeded["active_evidence_id"]]
    assert exported.omitted_evidence_ids == [seeded["removed_evidence_id"]]
    assert names == {
        "manifest.json",
        f"evidence/{seeded['active_evidence_id']}.bin",
    }
    assert manifest["evidence_inclusion"] == {
        "mode": "selected",
        "included_evidence_ids": [seeded["active_evidence_id"]],
        "omitted_evidence_ids": [seeded["removed_evidence_id"]],
    }
    assert "revision_history" not in manifest
    assert "agent_settings" not in manifest


def test_profile_only_restore_creates_one_new_baseline_and_preserves_unavailable_provenance(
    tmp_path: Path,
):
    seeded = seed_profile(tmp_path / "source")
    source_profile = seeded["complete"].current()
    exported = seeded["portability"].export_archive(
        CareerProfileExportRequest(
            expected_profile_revision=source_profile.profile_revision,
            evidence_mode="profile_only",
        )
    )

    _, _, target_complete, target_tracer, _, target_portability = initialize(tmp_path / "target")
    restored = target_portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=CareerProfileRestoreRequest(
            expected_profile_revision=0,
            idempotency_key="restore-profile-only-0001",
            confirmation="RESTORE_CAREER_PROFILE_BASELINE",
            archive_base64=exported.content_base64,
        ),
    )

    current = target_complete.current()
    assert restored.profile.profile_revision == 1
    assert current == restored.profile
    assert {item.value.kind for item in current.items} == {"skill", "claim", "work_arrangement"}
    assert all(not evidence.active for evidence in current.source_evidence)
    assert target_tracer.current_work_arrangement().record is not None
    with pytest.raises(CareerProfileEvidenceNotFound):
        target_complete.read_evidence(seeded["active_evidence_id"])
    assert target_portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=CareerProfileRestoreRequest(
            expected_profile_revision=0,
            idempotency_key="restore-profile-only-0001",
            confirmation="RESTORE_CAREER_PROFILE_BASELINE",
            archive_base64=exported.content_base64,
        ),
    ) == restored


def test_selected_evidence_restore_verifies_and_reactivates_only_bundled_bytes(tmp_path: Path):
    seeded = seed_profile(tmp_path / "source")
    source_profile = seeded["complete"].current()
    exported = seeded["portability"].export_archive(
        CareerProfileExportRequest(
            expected_profile_revision=source_profile.profile_revision,
            evidence_mode="selected",
            selected_evidence_ids=[seeded["active_evidence_id"]],
        )
    )

    _, _, target_complete, _, _, target_portability = initialize(tmp_path / "target")
    restored = target_portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=CareerProfileRestoreRequest(
            expected_profile_revision=0,
            idempotency_key="restore-selected-evidence-0001",
            confirmation="RESTORE_CAREER_PROFILE_BASELINE",
            archive_base64=exported.content_base64,
        ),
    )

    evidence = {item.evidence_id: item for item in restored.profile.source_evidence}
    assert evidence[seeded["active_evidence_id"]].active is True
    assert evidence[seeded["removed_evidence_id"]].active is False
    assert target_complete.read_evidence(seeded["active_evidence_id"]) == ACTIVE_EVIDENCE
    with pytest.raises(CareerProfileEvidenceNotFound):
        target_complete.read_evidence(seeded["removed_evidence_id"])


def test_restore_rejects_unexpected_archive_members_without_mutating_the_profile(tmp_path: Path):
    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("../outside.txt", "not allowed")

    _, _, target_complete, _, _, target_portability = initialize(tmp_path / "target")
    with pytest.raises(CareerProfilePortabilityError, match="unexpected"):
        target_portability.restore_archive(
            principal=DEVICE_PRINCIPAL,
            command=CareerProfileRestoreRequest(
                expected_profile_revision=0,
                idempotency_key="restore-malicious-archive-0001",
                confirmation="RESTORE_CAREER_PROFILE_BASELINE",
                archive_base64=base64.b64encode(archive_bytes.getvalue()).decode(),
            ),
        )
    assert target_complete.current().profile_revision == 0


def test_restore_rejects_tampered_evidence_before_mutating_the_profile(tmp_path: Path):
    seeded = seed_profile(tmp_path / "source")
    source_profile = seeded["complete"].current()
    exported = seeded["portability"].export_archive(
        CareerProfileExportRequest(
            expected_profile_revision=source_profile.profile_revision,
            evidence_mode="selected",
            selected_evidence_ids=[seeded["active_evidence_id"]],
        )
    )
    original_bytes = base64.b64decode(exported.content_base64)
    tampered_bytes = BytesIO()
    with (
        zipfile.ZipFile(BytesIO(original_bytes)) as original,
        zipfile.ZipFile(tampered_bytes, "w", zipfile.ZIP_DEFLATED) as tampered,
    ):
        for name in original.namelist():
            content = original.read(name)
            if name.startswith("evidence/"):
                content = b"tampered evidence"
            tampered.writestr(name, content)

    _, _, target_complete, _, _, target_portability = initialize(tmp_path / "target")
    with pytest.raises(CareerProfilePortabilityError, match="integrity"):
        target_portability.restore_archive(
            principal=DEVICE_PRINCIPAL,
            command=CareerProfileRestoreRequest(
                expected_profile_revision=0,
                idempotency_key="restore-tampered-evidence-0001",
                confirmation="RESTORE_CAREER_PROFILE_BASELINE",
                archive_base64=base64.b64encode(tampered_bytes.getvalue()).decode(),
            ),
        )
    assert target_complete.current().profile_revision == 0


def test_restore_keeps_old_receipts_so_a_delayed_retry_cannot_replace_a_newer_baseline(
    tmp_path: Path,
):
    def archive_with_skill(root: Path, name: str, key: str):
        _, _, complete, _, _, portability = initialize(root)
        profile = complete.upsert_item(
            principal=DEVICE_PRINCIPAL,
            command=ProfileItemMutation.model_validate(
                {
                    "expected_profile_revision": 0,
                    "idempotency_key": key,
                    "value": {"kind": "skill", "name": name},
                }
            ),
        )
        return portability.export_archive(
            CareerProfileExportRequest(
                expected_profile_revision=profile.profile_revision,
                evidence_mode="profile_only",
            )
        )

    archive_a = archive_with_skill(
        tmp_path / "source-a", "(FAKE) Skill A", "source-a-skill"
    )
    archive_b = archive_with_skill(
        tmp_path / "source-b", "(FAKE) Skill B", "source-b-skill"
    )
    _, _, target_complete, _, _, target_portability = initialize(tmp_path / "target")
    target_complete.upsert_item(
        principal=DEVICE_PRINCIPAL,
        command=ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": 0,
                "idempotency_key": "target-placeholder-skill",
                "value": {"kind": "skill", "name": "(FAKE) Placeholder"},
            }
        ),
    )
    restore_a = CareerProfileRestoreRequest(
        expected_profile_revision=1,
        idempotency_key="restore-source-a",
        confirmation="RESTORE_CAREER_PROFILE_BASELINE",
        archive_base64=archive_a.content_base64,
    )
    result_a = target_portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=restore_a,
    )
    target_portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=CareerProfileRestoreRequest(
            expected_profile_revision=result_a.profile.profile_revision,
            idempotency_key="restore-source-b",
            confirmation="RESTORE_CAREER_PROFILE_BASELINE",
            archive_base64=archive_b.content_base64,
        ),
    )

    replay = target_portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=restore_a,
    )

    assert replay == result_a
    assert [item.value.model_dump()["name"] for item in target_complete.current().items] == [
        "(FAKE) Skill B"
    ]


def test_restore_blocks_active_agent_work_without_swapping_or_journaling(tmp_path: Path):
    source = seed_profile(tmp_path / "source")
    source_profile = source["complete"].current()
    exported = source["portability"].export_archive(
        CareerProfileExportRequest(
            expected_profile_revision=source_profile.profile_revision,
            evidence_mode="profile_only",
        )
    )
    database, evidence_root, target_complete, _, _, target_portability = initialize(
        tmp_path / "target"
    )
    state = JobOsStateStore(database)
    conversation = state.conversation_store(state.first_active_conversation_id())
    conversation.create_turn(
        text="(FAKE) Active Career Profile work",
        context={},
        idempotency_key="active-turn-before-restore",
        actor_id="primary-device",
    )
    vault_before = sorted(path.name for path in evidence_root.iterdir())

    with pytest.raises(CareerProfileRestoreBusy, match="active agent work"):
        target_portability.restore_archive(
            principal=DEVICE_PRINCIPAL,
            command=CareerProfileRestoreRequest(
                expected_profile_revision=0,
                idempotency_key="restore-during-active-turn",
                confirmation="RESTORE_CAREER_PROFILE_BASELINE",
                archive_base64=exported.content_base64,
            ),
        )

    assert target_complete.current().profile_revision == 0
    assert sorted(path.name for path in evidence_root.iterdir()) == vault_before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_restore_journal"
        ).fetchone() == (0,)


def test_restore_recovery_rolls_back_uncommitted_swap_and_finalizes_committed_swap(
    tmp_path: Path,
):
    database, evidence_root, _, _, _, portability = initialize(tmp_path)
    evidence_root.joinpath("old-sentinel.txt").write_text("old", encoding="utf-8")

    rollback_id = "cprt_aaaaaaaaaaaaaaaa"
    rollback_staging, rollback_backup = portability._operation_paths(  # noqa: SLF001
        rollback_id
    )
    rollback_staging.mkdir()
    rollback_staging.joinpath("new-sentinel.txt").write_text("new", encoding="utf-8")
    evidence_root.replace(rollback_backup)
    rollback_staging.replace(evidence_root)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO career_profile_restore_journal("
            "operation_id, actor_principal, idempotency_key, request_hash, phase, had_live_vault) "
            "VALUES (?, ?, ?, ?, 'swap_pending', 1)",
            (rollback_id, DEVICE_PRINCIPAL, "rollback-recovery", "a" * 64),
        )

    portability.recover_pending_restores()

    assert evidence_root.joinpath("old-sentinel.txt").read_text(encoding="utf-8") == "old"
    assert not evidence_root.joinpath("new-sentinel.txt").exists()
    assert not rollback_backup.exists()

    finalize_id = "cprt_bbbbbbbbbbbbbbbb"
    finalize_staging, finalize_backup = portability._operation_paths(  # noqa: SLF001
        finalize_id
    )
    finalize_staging.mkdir()
    finalize_staging.joinpath("committed-sentinel.txt").write_text(
        "committed", encoding="utf-8"
    )
    evidence_root.replace(finalize_backup)
    finalize_staging.replace(evidence_root)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO career_profile_restore_journal("
            "operation_id, actor_principal, idempotency_key, request_hash, phase, had_live_vault) "
            "VALUES (?, ?, ?, ?, 'db_committed', 1)",
            (finalize_id, DEVICE_PRINCIPAL, "finalize-recovery", "b" * 64),
        )

    portability.recover_pending_restores()

    assert evidence_root.joinpath("committed-sentinel.txt").read_text(
        encoding="utf-8"
    ) == "committed"
    assert not evidence_root.joinpath("old-sentinel.txt").exists()
    assert not finalize_backup.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_restore_journal"
        ).fetchone() == (0,)


def test_zero_evidence_profile_exports_and_restores_without_inventing_sources(tmp_path: Path):
    _, _, source_complete, _, _, source_portability = initialize(tmp_path / "source")
    source_profile = source_complete.upsert_item(
        principal=DEVICE_PRINCIPAL,
        command=ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": 0,
                "idempotency_key": "zero-evidence-skill",
                "value": {"kind": "skill", "name": "(FAKE) Evidence-free skill"},
            }
        ),
    )
    exported = source_portability.export_archive(
        CareerProfileExportRequest(
            expected_profile_revision=source_profile.profile_revision,
            evidence_mode="all",
        )
    )
    manifest, names = archive_manifest(exported.content_base64)
    assert names == {"manifest.json"}
    assert manifest["source_evidence"] == []
    assert exported.included_evidence_ids == []
    assert exported.omitted_evidence_ids == []

    _, _, target_complete, _, _, target_portability = initialize(tmp_path / "target")
    restored = target_portability.restore_archive(
        principal=DEVICE_PRINCIPAL,
        command=CareerProfileRestoreRequest(
            expected_profile_revision=0,
            idempotency_key="restore-zero-evidence-profile",
            confirmation="RESTORE_CAREER_PROFILE_BASELINE",
            archive_base64=exported.content_base64,
        ),
    )
    assert restored.profile.source_evidence == []
    assert [
        item.value.model_dump()["name"] for item in target_complete.current().items
    ] == ["(FAKE) Evidence-free skill"]


def test_context_scope_snapshot_is_exact_accepted_only_and_immutable(tmp_path: Path):
    seeded = seed_profile(tmp_path)
    complete: CareerProfileCompleteStore = seeded["complete"]
    context: CareerProfileContextStore = seeded["context"]
    profile = complete.current()
    proposed = complete.import_evidence(
        principal="agent:job-hunter",
        mutation_source="agent_inference",
        command=EvidenceImportRequest.model_validate(
            {
                "expected_profile_revision": profile.profile_revision,
                "idempotency_key": "seed-proposed-item-0001",
                "original_filename": "(FAKE)-agent-source.txt",
                "media_type": "text/plain",
                "provenance": {
                    "source_kind": "supporting_document",
                    "source_label": "(FAKE) agent source",
                    "method": "agent_import",
                },
                "content_base64": base64.b64encode(b"(FAKE) proposed source").decode(),
                "extractions": [
                    {
                        "assessment": "inferred",
                        "value": {"kind": "skill", "name": "(FAKE) Rust"},
                    }
                ],
            }
        ),
    )
    proposed_id = next(item.item_id for item in proposed.items if item.review_status == "proposed")

    with pytest.raises(CareerProfileContextSelectionError, match="accepted"):
        context.update_scope(
            principal=DEVICE_PRINCIPAL,
            agent_id=AGENT_ID,
            command=CareerProfileContextScopeUpdate(
                expected_profile_revision=proposed.profile_revision,
                expected_authority_epoch=proposed.authority_epoch,
                idempotency_key="select-proposed-context-0001",
                mode="selected",
                selected_item_ids=[proposed_id],
            ),
        )

    grant = context.update_scope(
        principal=DEVICE_PRINCIPAL,
        agent_id=AGENT_ID,
        command=CareerProfileContextScopeUpdate(
            expected_profile_revision=proposed.profile_revision,
            expected_authority_epoch=proposed.authority_epoch,
            idempotency_key="select-exact-context-0001",
            mode="selected",
            selected_item_ids=[seeded["skill_id"]],
        ),
    )
    assert grant.mode == "selected"
    with sqlite3.connect(seeded["database"]) as connection:
        connection.execute("BEGIN IMMEDIATE")
        snapshot = context.create_snapshot_in_transaction(connection, agent_id=AGENT_ID)
        connection.commit()
    assert [item.item_id for item in snapshot.projection.items] == [seeded["skill_id"]]
    assert snapshot.projection.source_evidence == []

    complete.upsert_item(
        principal=DEVICE_PRINCIPAL,
        command=ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": proposed.profile_revision,
                "idempotency_key": "add-after-snapshot-0001",
                "value": {"kind": "project", "name": "(FAKE) Later project"},
            }
        ),
    )
    assert context.get_snapshot(snapshot.snapshot_id, agent_id=AGENT_ID) == snapshot


def test_none_and_broader_context_scopes_require_separate_user_updates(tmp_path: Path):
    seeded = seed_profile(tmp_path)
    complete: CareerProfileCompleteStore = seeded["complete"]
    context: CareerProfileContextStore = seeded["context"]
    profile = complete.current()

    empty = context.preview(agent_id=AGENT_ID)
    assert empty.scope.mode == "none"
    assert empty.projection.items == []
    assert empty.projection.source_evidence == []

    broader = context.update_scope(
        principal=DEVICE_PRINCIPAL,
        agent_id=AGENT_ID,
        command=CareerProfileContextScopeUpdate(
            expected_profile_revision=profile.profile_revision,
            expected_authority_epoch=profile.authority_epoch,
            idempotency_key="grant-broader-context-0001",
            mode="broader",
        ),
    )
    assert broader.mode == "broader"
    full = context.preview(agent_id=AGENT_ID)
    assert {item.value.kind for item in full.projection.items} == {
        "skill",
        "claim",
        "work_arrangement",
    }
    assert all(item.review_status == "accepted" for item in full.projection.items)

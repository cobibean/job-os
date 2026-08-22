from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.career_profile import CareerProfileRevisionConflict
from jobos_api.career_profile_collaboration import (
    CareerProfileCollaborationStore,
    ProposalDecisionRequest,
)
from jobos_api.career_profile_complete import (
    CareerProfileAuthorityActivationRequest,
    CareerProfileCompleteStore,
    CareerProfileLegacyWriterFenced,
    CareerProfileValueError,
)
from jobos_api.career_profile_migration import (
    CareerProfileMigrationBundle,
    CareerProfileMigrationError,
    CareerProfileMigrationService,
)
from jobos_api.career_profile_portability import (
    CareerProfileExportRequest,
    CareerProfilePortabilityService,
    CareerProfileRestoreRequest,
)
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore

FIXTURES = Path(__file__).parent / "fixtures"
DEVICE_TOKEN = "migration-device-token"
MCP_TOKEN = "migration-mcp-token"
AGENT_TOKEN = "migration-agent-token"


def load_bundle(name: str) -> CareerProfileMigrationBundle:
    return CareerProfileMigrationBundle.model_validate_json(
        (FIXTURES / f"career-profile-migration-{name}.json").read_text()
    )


def initialized(tmp_path: Path) -> tuple[CareerProfileMigrationService, CareerProfileCompleteStore]:
    database = tmp_path / "jobos.db"
    JobOsStateStore(database).initialize(owner_device_id="primary-device")
    complete = CareerProfileCompleteStore(database, tmp_path / "career-profile-evidence")
    complete.initialize()
    return (
        CareerProfileMigrationService(database, tmp_path / "career-profile-evidence"),
        complete,
    )


def settings(database: Path) -> Settings:
    return Settings(
        device_id="primary-device",
        device_token=DEVICE_TOKEN,
        mcp_token=MCP_TOKEN,
        state_db_path=database,
        career_profile_enabled=True,
        career_profile_agent_id="job-hunter",
        career_profile_agent_display_name="(FAKE) Job Hunter",
        career_profile_agent_token=AGENT_TOKEN,
    )


def user_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEVICE_TOKEN}"}


def agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {MCP_TOKEN}",
        "X-JobOS-MCP-Token": MCP_TOKEN,
        "X-JobOS-Agent-Id": "job-hunter",
        "X-JobOS-Agent-Token": AGENT_TOKEN,
    }


def test_full_bundle_is_journaled_idempotent_and_preserves_exact_provenance(tmp_path: Path):
    migration, complete = initialized(tmp_path)
    bundle = load_bundle("full")

    report = migration.run(bundle)
    replay = migration.run(bundle)

    assert replay == report
    assert report.authority_state == "staging"
    assert report.profile_revision == 1
    assert report.evidence_objects == 2
    assert report.my_career.accepted == 2
    assert report.my_career.proposed == 2
    assert report.what_im_looking_for.accepted == 2
    current = complete.current()
    assert current.authority_state == "staging"
    assert {item.value.kind for item in current.items} == {
        "identity",
        "skill",
        "target_roles",
        "work_arrangement",
    }
    assert all(item.provenance.method == "migration_import" for item in current.items)
    assert all(
        item.provenance.mutation_source == "deterministic_source_mapping" for item in current.items
    )
    for evidence in current.source_evidence:
        content = complete.read_evidence(evidence.evidence_id)
        assert sha256(content).hexdigest() == evidence.sha256
        assert evidence.provenance.method == "migration_import"

    proposals = (
        CareerProfileCollaborationStore(
            migration.database,
            complete,
        )
        .list_proposals()
        .proposals
    )
    assert {proposal.after.value.kind for proposal in proposals if proposal.after} == {
        "positioning",
        "claim",
    }
    assert all(proposal.after is not None for proposal in proposals)
    assert all(
        proposal.after.provenance.method == "migration_import"
        and proposal.after.provenance.mutation_source == "agent_inference"
        for proposal in proposals
        if proposal.after
    )
    assert any(len(proposal.evidence_ids) == 2 for proposal in proposals)
    assert {proposal.agent_display_name for proposal in proposals} == {"Migration reviewer"}

    collaboration = CareerProfileCollaborationStore(migration.database, complete)
    head = report.profile_revision
    for index, proposal in enumerate(proposals, start=1):
        decided = collaboration.decide_proposal(
            proposal_id=proposal.proposal_id,
            principal="device:primary-device",
            command=ProposalDecisionRequest(
                expected_profile_revision=head,
                idempotency_key=f"accept-migration-proposal-{index:04d}",
                proposal_sha256=proposal.proposal_sha256,
                decision="accept",
            ),
        )
        head = decided.profile.profile_revision

    accepted_imports = [
        item
        for item in complete.current().items
        if item.provenance.mutation_source == "agent_inference"
    ]
    assert len(accepted_imports) == 2
    assert all(item.review_status == "accepted" for item in accepted_imports)
    assert all(item.provenance.method == "migration_import" for item in accepted_imports)
    assert any(len(item.evidence_ids) == 2 for item in accepted_imports)

    with sqlite3.connect(migration.database) as connection:
        assert connection.execute(
            "SELECT phase FROM career_profile_migration_journal"
        ).fetchone() == ("complete",)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_migration_receipts"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_complete_revisions "
            "WHERE actor_principal = 'migration:career-profile'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM career_profile_complete_revisions"
        ).fetchone() == (3,)


@pytest.mark.parametrize(
    ("fixture", "accepted", "skipped", "evidence"),
    [("sparse", 1, 3, 0), ("zero-evidence", 2, 0, 0)],
)
def test_sparse_and_zero_evidence_are_first_class_candidates(
    tmp_path: Path,
    fixture: str,
    accepted: int,
    skipped: int,
    evidence: int,
):
    migration, complete = initialized(tmp_path)
    report = migration.run(load_bundle(fixture))

    assert report.evidence_objects == evidence
    assert report.my_career.accepted + report.what_im_looking_for.accepted == accepted
    assert report.my_career.skipped_unknown + report.what_im_looking_for.skipped_unknown == skipped
    assert complete.current().authority_state == "staging"
    with sqlite3.connect(migration.database) as connection:
        values = [
            json.loads(row[0])
            for row in connection.execute("SELECT value_json FROM career_profile_items")
        ]
    assert all(
        "currency" not in value and "current" not in value and "strength" not in value
        for value in values
    )
    if fixture == "zero-evidence":
        claim = next(item for item in complete.current().items if item.value.kind == "claim")
        assert claim.review_status == "accepted"
        assert claim.evidence_ids == []


def test_conflicting_deterministic_assertions_are_code_forced_to_review(tmp_path: Path):
    migration, complete = initialized(tmp_path)

    report = migration.run(load_bundle("conflict"))

    assert report.my_career.accepted == 0
    assert report.my_career.conflicting == 2
    assert complete.current().items == []
    proposals = (
        CareerProfileCollaborationStore(migration.database, complete).list_proposals().proposals
    )
    assert len(proposals) == 2
    assert all(
        proposal.after and proposal.after.review_status == "conflicting" for proposal in proposals
    )
    assert {proposal.evidence_ids[0] for proposal in proposals} == {
        source.evidence_id for source in complete.current().source_evidence
    }


def test_repeatable_deterministic_rows_do_not_conflict_merely_for_differing(tmp_path: Path):
    migration, complete = initialized(tmp_path)
    payload = load_bundle("zero-evidence").model_dump(mode="json")
    payload["facts"] = [
        {
            "key": "skill-one",
            "mapping": "canonical.skill",
            "value": {"kind": "skill", "name": "(FAKE) Python"},
            "evidence_keys": [],
            "source_label": "(FAKE) accepted skills",
        },
        {
            "key": "skill-two",
            "mapping": "canonical.skill",
            "value": {"kind": "skill", "name": "(FAKE) TypeScript"},
            "evidence_keys": [],
            "source_label": "(FAKE) accepted skills",
        },
    ]

    report = migration.run(CareerProfileMigrationBundle.model_validate(payload))

    assert report.my_career.accepted == 2
    assert report.my_career.conflicting == 0
    names = {
        item.value.model_dump(mode="json").get("name") for item in complete.current().items
    }
    assert names == {
        "(FAKE) Python",
        "(FAKE) TypeScript",
    }


def test_unknown_mapping_cannot_self_authorize_acceptance():
    payload = load_bundle("zero-evidence").model_dump(mode="json")
    payload["facts"][0]["mapping"] = "caller.claims_this_is_exact"
    with pytest.raises(ValueError, match="mapping is not code-owned"):
        CareerProfileMigrationBundle.model_validate(payload)


def test_known_mapping_cannot_authorize_the_wrong_value_kind():
    payload = load_bundle("zero-evidence").model_dump(mode="json")
    payload["facts"][0]["mapping"] = "canonical.identity"
    with pytest.raises(ValueError, match="requires value kind identity"):
        CareerProfileMigrationBundle.model_validate(payload)


def test_partial_failure_is_journaled_startup_fails_closed_and_same_bundle_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    migration, complete = initialized(tmp_path)
    bundle = load_bundle("full")
    original_commit = migration._commit

    def fail_after_vault(*args, **kwargs):
        raise RuntimeError("injected migration commit failure")

    monkeypatch.setattr(migration, "_commit", fail_after_vault)
    with pytest.raises(RuntimeError, match="injected"):
        migration.run(bundle)
    with pytest.raises(CareerProfileMigrationError, match="explicit same-bundle recovery"):
        CareerProfileMigrationService(
            migration.database,
            tmp_path / "career-profile-evidence",
        ).initialize()
    assert complete.current().profile_revision == 0

    monkeypatch.setattr(migration, "_commit", original_commit)
    recovered = migration.run(bundle)
    assert recovered.completed is True
    CareerProfileMigrationService(
        migration.database,
        tmp_path / "career-profile-evidence",
    ).initialize()


def test_authority_activation_is_persisted_exact_fresh_and_fences_legacy_writers(tmp_path: Path):
    migration, complete = initialized(tmp_path)
    report = migration.run(load_bundle("zero-evidence"))

    invalid = {
        "expected_profile_revision": report.profile_revision,
        "expected_authority_epoch": 0,
        "idempotency_key": "activate-authority-0001",
        "confirmation": "CUTOVER",
    }
    with pytest.raises(ValueError):
        CareerProfileAuthorityActivationRequest.model_validate(invalid)
    with pytest.raises(CareerProfileRevisionConflict):
        complete.activate_authority(
            principal="device:primary-device",
            command=CareerProfileAuthorityActivationRequest(
                expected_profile_revision=0,
                expected_authority_epoch=0,
                idempotency_key="activate-stale-authority-0001",
                confirmation="CUT OVER CAREER PROFILE AUTHORITY",
            ),
        )

    command = CareerProfileAuthorityActivationRequest(
        expected_profile_revision=report.profile_revision,
        expected_authority_epoch=0,
        idempotency_key="activate-authority-0001",
        confirmation="CUT OVER CAREER PROFILE AUTHORITY",
    )
    activated = complete.activate_authority(
        principal="device:primary-device",
        command=command,
    )
    assert (
        complete.activate_authority(principal="device:primary-device", command=command) == activated
    )
    assert activated.authority_state == "cutover"
    assert (
        CareerProfileCompleteStore(
            migration.database,
            tmp_path / "career-profile-evidence",
        ).authority()
        == activated
    )
    with pytest.raises(CareerProfileLegacyWriterFenced) as fenced:
        complete.assert_legacy_writer_allowed()
    assert str(fenced.value) == "career_profile_legacy_writer_fenced"
    with pytest.raises(CareerProfileLegacyWriterFenced) as migration_fenced:
        CareerProfileMigrationService(
            migration.database,
            tmp_path / "career-profile-evidence",
        ).run(load_bundle("zero-evidence"))
    assert str(migration_fenced.value) == "career_profile_legacy_writer_fenced"


def test_authority_activation_requires_completed_candidate_and_no_active_turn(tmp_path: Path):
    migration, complete = initialized(tmp_path)
    command = CareerProfileAuthorityActivationRequest(
        expected_profile_revision=0,
        expected_authority_epoch=0,
        idempotency_key="activate-without-candidate-0001",
        confirmation="CUT OVER CAREER PROFILE AUTHORITY",
    )
    with pytest.raises(CareerProfileValueError, match="completed migration candidate"):
        complete.activate_authority(principal="device:primary-device", command=command)

    report = migration.run(load_bundle("zero-evidence"))
    with sqlite3.connect(migration.database) as connection:
        connection.execute(
            "INSERT INTO conversation_turns("
            "turn_id, conversation_id, message_id, text, context_json, status"
            ") SELECT 'turn_cutover_active', conversation_id, 'msg_cutover_active', "
            "'(FAKE) active turn', '{}', 'running' FROM conversations WHERE archived_at IS NULL"
        )
    with pytest.raises(CareerProfileValueError, match="active agent work"):
        complete.activate_authority(
            principal="device:primary-device",
            command=CareerProfileAuthorityActivationRequest(
                expected_profile_revision=report.profile_revision,
                expected_authority_epoch=0,
                idempotency_key="activate-during-turn-0001",
                confirmation="CUT OVER CAREER PROFILE AUTHORITY",
            ),
        )


def test_completed_empty_migration_consumes_the_one_shot_boundary(tmp_path: Path):
    migration, _ = initialized(tmp_path)
    first_payload = load_bundle("sparse").model_dump(mode="json")
    first_payload["facts"] = []
    first = CareerProfileMigrationBundle.model_validate(first_payload)
    report = migration.run(first)
    assert report.profile_revision == 0

    second_payload = first.model_dump(mode="json")
    second_payload["bundle_label"] = "(FAKE) second empty migration"
    second = CareerProfileMigrationBundle.model_validate(second_payload)
    with pytest.raises(CareerProfileMigrationError, match="one-shot operation"):
        migration.run(second)


def test_authenticated_api_projection_is_dormant_until_exact_owner_cutover(tmp_path: Path):
    database = tmp_path / "jobos.db"
    migration, complete = initialized(tmp_path)
    report = migration.run(load_bundle("zero-evidence"))
    item_id = next(item.item_id for item in complete.current().items if item.value.kind == "claim")
    app = create_app(settings(database))
    with TestClient(app) as client:
        selected = client.put(
            "/v1/career-profile/agents/job-hunter/context",
            headers=user_headers(),
            json={
                "expected_profile_revision": report.profile_revision,
                "expected_authority_epoch": 0,
                "idempotency_key": "projection-scope-0001",
                "mode": "selected",
                "selected_item_ids": [item_id],
                "selected_areas": [],
            },
        )
        assert selected.status_code == 200, selected.text
        dormant = client.get(
            "/v1/career-profile/consumer-projection",
            headers=agent_headers(),
        )
        assert dormant.status_code == 422

        wrong = client.post(
            "/v1/career-profile/authority/activate",
            headers=user_headers(),
            json={
                "expected_profile_revision": report.profile_revision,
                "expected_authority_epoch": 0,
                "idempotency_key": "api-activate-authority-0001",
                "confirmation": "CUTOVER",
            },
        )
        assert wrong.status_code == 422
        activated = client.post(
            "/v1/career-profile/authority/activate",
            headers=user_headers(),
            json={
                "expected_profile_revision": report.profile_revision,
                "expected_authority_epoch": 0,
                "idempotency_key": "api-activate-authority-0001",
                "confirmation": "CUT OVER CAREER PROFILE AUTHORITY",
            },
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["authority_state"] == "cutover"
        projection = client.get(
            "/v1/career-profile/consumer-projection",
            headers=agent_headers(),
        )
        assert projection.status_code == 200, projection.text
        assert [item["item_id"] for item in projection.json()["projection"]["items"]] == [item_id]
        assert projection.json()["projection"]["authority_state"] == "cutover"

        none_scope = client.put(
            "/v1/career-profile/agents/job-hunter/context",
            headers=user_headers(),
            json={
                "expected_profile_revision": report.profile_revision,
                "expected_authority_epoch": 1,
                "idempotency_key": "projection-scope-none-0001",
                "mode": "none",
                "selected_item_ids": [],
                "selected_areas": [],
            },
        )
        assert none_scope.status_code == 200, none_scope.text
        none_projection = client.get(
            "/v1/career-profile/consumer-projection",
            headers=agent_headers(),
        )
        assert none_projection.status_code == 200, none_projection.text
        assert none_projection.json()["projection"]["items"] == []
        assert none_projection.json()["projection"]["source_evidence"] == []

        broader_scope = client.put(
            "/v1/career-profile/agents/job-hunter/context",
            headers=user_headers(),
            json={
                "expected_profile_revision": report.profile_revision,
                "expected_authority_epoch": 1,
                "idempotency_key": "projection-scope-broader-0001",
                "mode": "broader",
                "selected_item_ids": [],
                "selected_areas": [],
            },
        )
        assert broader_scope.status_code == 200, broader_scope.text
        broader_projection = client.get(
            "/v1/career-profile/consumer-projection",
            headers=agent_headers(),
        )
        assert broader_projection.status_code == 200, broader_projection.text
        assert {item["item_id"] for item in broader_projection.json()["projection"]["items"]} == {
            item.item_id for item in complete.current().items
        }


def test_context_projection_guard_stays_fail_closed_in_staging(tmp_path: Path):
    _, complete = initialized(tmp_path)
    with pytest.raises(CareerProfileValueError, match="remain dormant"):
        complete.require_consumer_projection()


def test_portable_restore_never_imports_cutover_authority(tmp_path: Path):
    source_migration, source = initialized(tmp_path / "source")
    report = source_migration.run(load_bundle("zero-evidence"))
    source.activate_authority(
        principal="device:primary-device",
        command=CareerProfileAuthorityActivationRequest(
            expected_profile_revision=report.profile_revision,
            expected_authority_epoch=0,
            idempotency_key="source-activate-authority-0001",
            confirmation="CUT OVER CAREER PROFILE AUTHORITY",
        ),
    )
    exported = CareerProfilePortabilityService(
        source_migration.database,
        tmp_path / "source/career-profile-evidence",
    ).export_archive(
        CareerProfileExportRequest(
            expected_profile_revision=report.profile_revision,
            evidence_mode="profile_only",
        )
    )

    target_migration, target = initialized(tmp_path / "target")
    restored = CareerProfilePortabilityService(
        target_migration.database,
        tmp_path / "target/career-profile-evidence",
    ).restore_archive(
        principal="device:primary-device",
        command=CareerProfileRestoreRequest(
            expected_profile_revision=0,
            idempotency_key="target-restore-cutover-archive-0001",
            confirmation="RESTORE_CAREER_PROFILE_BASELINE",
            archive_base64=exported.content_base64,
        ),
    )

    assert restored.profile.authority_state == "staging"
    assert target.authority().authority_state == "staging"

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.career_profile_collaboration import (
    AgentProfileEditRequest,
    CareerProfileCollaborationConflict,
    CareerProfileCollaborationStore,
    ConnectedAgentAuthorizationError,
    ProfileUndoRequest,
    ProposalDecisionRequest,
)
from jobos_api.career_profile_complete import (
    CareerProfileCompleteStore,
    EvidenceImportRequest,
    ProfileIntentGrantRequest,
    ProfileItemMutation,
    ProfileItemRemoval,
)
from jobos_api.settings import Settings

DEVICE_TOKEN = "collaboration-device-token"
MCP_TOKEN = "collaboration-mcp-token"
AGENT_TOKEN = "collaboration-agent-token"
AGENT_ID = "job-hunter"


def configured_settings(database: Path) -> Settings:
    return Settings(
        device_id="primary-device",
        device_token=DEVICE_TOKEN,
        mcp_token=MCP_TOKEN,
        state_db_path=database,
        career_profile_enabled=True,
        career_profile_agent_id=AGENT_ID,
        career_profile_agent_display_name="Job Hunter",
        career_profile_agent_token=AGENT_TOKEN,
    )


def user_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEVICE_TOKEN}"}


def agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {MCP_TOKEN}",
        "X-JobOS-MCP-Token": MCP_TOKEN,
        "X-JobOS-Agent-Id": AGENT_ID,
        "X-JobOS-Agent-Token": AGENT_TOKEN,
    }


def agent_edit(
    *,
    revision: int,
    key: str,
    value: dict[str, object] | None,
    reason: str,
    operation: str = "item.create",
    target_id: str | None = None,
    evidence_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "expected_profile_revision": revision,
        "idempotency_key": key,
        "operation": operation,
        "target_id": target_id,
        "reason": reason,
        "value": value,
        "evidence_ids": evidence_ids or [],
    }


def set_direct_mode(client: TestClient) -> None:
    changed = client.patch(
        f"/v1/career-profile/agents/{AGENT_ID}",
        headers=user_headers(),
        json={"trust_mode": "direct"},
    )
    assert changed.status_code == 200, changed.text


def test_connected_agent_defaults_to_review_and_only_user_changes_mode(tmp_path: Path):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        listed = client.get("/v1/career-profile/agents", headers=user_headers())
        assert listed.status_code == 200, listed.text
        assert listed.json() == {
            "agents": [
                {
                    "agent_id": AGENT_ID,
                    "display_name": "Job Hunter",
                    "principal": f"agent:{AGENT_ID}",
                    "trust_mode": "review",
                    "active": True,
                    "connected_at": listed.json()["agents"][0]["connected_at"],
                    "updated_at": listed.json()["agents"][0]["updated_at"],
                    "disconnected_at": None,
                }
            ]
        }

        self_escalation = client.patch(
            f"/v1/career-profile/agents/{AGENT_ID}",
            headers=agent_headers(),
            json={"trust_mode": "direct"},
        )
        assert self_escalation.status_code == 403

        changed = client.patch(
            f"/v1/career-profile/agents/{AGENT_ID}",
            headers=user_headers(),
            json={"trust_mode": "direct"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["trust_mode"] == "direct"


def test_review_mode_proposal_is_exact_evidence_optional_and_stale_safe(tmp_path: Path):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        proposed = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=0,
                key="review-skill-proposal-0001",
                value={"kind": "skill", "name": "TypeScript"},
                reason="The user asked me to remember this skill",
            ),
        )
        assert proposed.status_code == 200, proposed.text
        body = proposed.json()
        assert body["outcome"] == "proposal"
        assert body["profile"]["profile_revision"] == 0
        proposal = body["proposal"]
        assert proposal["agent_id"] == AGENT_ID
        assert proposal["agent_display_name"] == "Job Hunter"
        assert proposal["reason"] == "The user asked me to remember this skill"
        assert proposal["base_profile_revision"] == 0
        assert proposal["before"] is None
        assert proposal["after"]["value"] == {
            "kind": "skill",
            "name": "TypeScript",
            "level": None,
            "note": None,
        }
        assert proposal["evidence_ids"] == []

        changed_payload = client.post(
            f"/v1/career-profile/proposals/{proposal['proposal_id']}/decision",
            headers=user_headers(),
            json={
                "expected_profile_revision": 0,
                "idempotency_key": "reject-changed-proposal-payload-0001",
                "proposal_sha256": "0" * 64,
                "decision": "accept",
            },
        )
        assert changed_payload.status_code == 409

        competing_edit = client.post(
            "/v1/career-profile/items",
            headers=user_headers(),
            json={
                "expected_profile_revision": 0,
                "idempotency_key": "user-competing-profile-edit-0001",
                "value": {"kind": "custom", "label": "Note", "text": "Newer value"},
                "evidence_ids": [],
            },
        )
        assert competing_edit.status_code == 201, competing_edit.text

        stale_accept = client.post(
            f"/v1/career-profile/proposals/{proposal['proposal_id']}/decision",
            headers=user_headers(),
            json={
                "expected_profile_revision": 1,
                "idempotency_key": "reject-stale-proposal-accept-0001",
                "proposal_sha256": proposal["proposal_sha256"],
                "decision": "accept",
            },
        )
        assert stale_accept.status_code == 409
        current = client.get("/v1/career-profile", headers=user_headers()).json()
        assert current["profile_revision"] == 1


def test_review_mode_accepts_exact_zero_evidence_payload_atomically(tmp_path: Path):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        proposed = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=0,
                key="zero-evidence-proposal-0001",
                value={"kind": "skill", "name": "Product strategy"},
                reason="Remember a user-authored skill",
            ),
        ).json()["proposal"]

        accepted = client.post(
            f"/v1/career-profile/proposals/{proposed['proposal_id']}/decision",
            headers=user_headers(),
            json={
                "expected_profile_revision": 0,
                "idempotency_key": "accept-zero-evidence-proposal-0001",
                "proposal_sha256": proposed["proposal_sha256"],
                "decision": "accept",
            },
        )
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body["proposal"]["status"] == "accepted"
        assert body["profile"]["profile_revision"] == 1
        assert body["profile"]["items"][0]["evidence_ids"] == []

        history = client.get("/v1/career-profile/history", headers=user_headers()).json()
        assert history["revisions"][0]["actor_kind"] == "user_proposal_decision"
        assert history["revisions"][0]["proposal_id"] == proposed["proposal_id"]


def test_direct_ordinary_edit_has_history_and_compensating_undo(tmp_path: Path):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        set_direct_mode(client)
        applied = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=0,
                key="direct-skill-edit-0001",
                value={"kind": "skill", "name": "React"},
                reason="Added a skill from the current conversation",
            ),
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["outcome"] == "applied"
        assert applied.json()["profile"]["profile_revision"] == 1
        assert applied.json()["profile"]["items"][0]["review_status"] == "accepted"
        assert applied.json()["profile"]["items"][0]["actor_principal"] == (
            f"agent:{AGENT_ID}"
        )

        replayed = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=0,
                key="direct-skill-edit-0001",
                value={"kind": "skill", "name": "React"},
                reason="Added a skill from the current conversation",
            ),
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json() == applied.json()

        mismatched_retry = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=0,
                key="direct-skill-edit-0001",
                value={"kind": "skill", "name": "React"},
                reason="A different request must not reuse the receipt",
            ),
        )
        assert mismatched_retry.status_code == 409

        history = client.get("/v1/career-profile/history", headers=user_headers())
        assert history.status_code == 200, history.text
        revision = history.json()["revisions"][0]
        assert revision["actor_kind"] == "autonomous_agent"
        assert revision["reason"] == "Added a skill from the current conversation"
        assert revision["undoable"] is True

        undone = client.post(
            f"/v1/career-profile/history/{revision['revision_id']}/undo",
            headers=user_headers(),
            json={
                "expected_profile_revision": 1,
                "idempotency_key": "undo-direct-skill-edit-0001",
            },
        )
        assert undone.status_code == 200, undone.text
        assert undone.json()["profile_revision"] == 2
        assert undone.json()["items"] == []
        latest = client.get(
            "/v1/career-profile/history", headers=user_headers()
        ).json()["revisions"][0]
        assert latest["undo_of_revision_id"] == revision["revision_id"]
        assert latest["actor_kind"] == "direct_user"


def test_evidence_removal_is_reversible_through_history_undo(tmp_path: Path):
    settings = configured_settings(tmp_path / "jobos.db")
    app = create_app(settings)
    evidence_bytes = b"(FAKE) retained Evidence bytes for Undo"

    with TestClient(app):
        complete = CareerProfileCompleteStore(
            settings.state_db_path,
            settings.resolved_evidence_vault_root(),
        )
        collaboration = CareerProfileCollaborationStore(settings.state_db_path, complete)
        imported = complete.import_evidence(
            principal="device:primary-device",
            command=EvidenceImportRequest.model_validate(
                {
                    "expected_profile_revision": 0,
                    "idempotency_key": "import-evidence-before-undo-0001",
                    "original_filename": "(FAKE)-evidence-for-undo.txt",
                    "media_type": "text/plain",
                    "captured_at": None,
                    "provenance": {
                        "source_kind": "supporting_document",
                        "source_label": "(FAKE) Evidence for Undo",
                        "method": "user_import",
                    },
                    "content_base64": base64.b64encode(evidence_bytes).decode(),
                    "extractions": [],
                }
            ),
        )
        evidence_id = imported.source_evidence[0].evidence_id
        complete.remove_evidence(
            principal="device:primary-device",
            evidence_id=evidence_id,
            command=ProfileItemRemoval(
                expected_profile_revision=1,
                idempotency_key="remove-evidence-before-undo-0001",
            ),
        )

        removed_revision = collaboration.history().revisions[0]
        assert removed_revision.operation == "evidence.remove"
        assert removed_revision.evidence_id == evidence_id
        assert removed_revision.undoable is True

        command = ProfileUndoRequest(
            expected_profile_revision=2,
            idempotency_key="undo-evidence-removal-0001",
        )
        restored = collaboration.undo(
            revision_id=removed_revision.revision_id,
            principal="device:primary-device",
            command=command,
        )
        assert restored.profile_revision == 3
        assert restored.source_evidence[0].active is True
        assert complete.read_evidence(evidence_id) == evidence_bytes
        assert collaboration.undo(
            revision_id=removed_revision.revision_id,
            principal="device:primary-device",
            command=command,
        ) == restored

        restoration_revision = collaboration.history().revisions[0]
        assert restoration_revision.operation == "evidence.import"
        assert restoration_revision.evidence_id == evidence_id
        assert restoration_revision.undo_of_revision_id == removed_revision.revision_id
        assert restoration_revision.reason == (
            "Restored the previously removed Evidence source"
        )
        assert restoration_revision.undoable is False


def test_direct_edit_and_retry_receipt_roll_back_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = configured_settings(tmp_path / "jobos.db")
    app = create_app(settings)

    with TestClient(app):
        complete = CareerProfileCompleteStore(
            settings.state_db_path,
            settings.resolved_evidence_vault_root(),
        )
        collaboration = CareerProfileCollaborationStore(settings.state_db_path, complete)
        collaboration.update_trust_mode(agent_id=AGENT_ID, trust_mode="direct")
        agent = collaboration.get_agent(AGENT_ID)
        command = AgentProfileEditRequest.model_validate(
            agent_edit(
                revision=0,
                key="atomic-direct-edit-receipt-0001",
                value={"kind": "skill", "name": "React"},
                reason="Remember a user-authored skill",
            )
        )

        def fail_to_record_result(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("synthetic retry receipt failure")

        monkeypatch.setattr(
            collaboration,
            "_record_result_in_connection",
            fail_to_record_result,
        )
        with pytest.raises(RuntimeError, match="synthetic retry receipt failure"):
            collaboration.submit_edit(agent=agent, command=command)

        current = complete.current()
        assert current.profile_revision == 0
        assert current.items == []


def test_undo_restores_value_as_a_new_attributed_item_revision(tmp_path: Path):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        set_direct_mode(client)
        created = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=0,
                key="create-skill-before-update-0001",
                value={"kind": "skill", "name": "React"},
                reason="Remember this skill",
            ),
        ).json()["profile"]["items"][0]

        updated = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=1,
                key="update-skill-before-undo-0001",
                operation="item.update",
                target_id=created["item_id"],
                value={"kind": "skill", "name": "TypeScript"},
                reason="Correct the skill name",
            ),
        )
        assert updated.status_code == 200, updated.text
        updated_item = updated.json()["profile"]["items"][0]
        assert updated_item["item_revision"] == 2

        changed_revision = client.get(
            "/v1/career-profile/history", headers=user_headers()
        ).json()["revisions"][0]
        undone = client.post(
            f"/v1/career-profile/history/{changed_revision['revision_id']}/undo",
            headers=user_headers(),
            json={
                "expected_profile_revision": 2,
                "idempotency_key": "undo-updated-skill-0001",
            },
        )
        assert undone.status_code == 200, undone.text
        restored = undone.json()["items"][0]
        assert restored["value"]["name"] == "React"
        assert restored["item_revision"] == 3
        assert restored["actor_principal"] == "device:primary-device"
        assert restored["created_at"] == created["created_at"]
        assert restored["updated_at"] != created["updated_at"]


def test_direct_mode_still_proposes_identity_evidence_removal_and_loosened_claims(
    tmp_path: Path,
):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        imported = client.post(
            "/v1/career-profile/evidence",
            headers=user_headers(),
            json={
                "expected_profile_revision": 0,
                "idempotency_key": "import-boundary-evidence-0001",
                "original_filename": "synthetic.txt",
                "media_type": "text/plain",
                "provenance": {
                    "source_kind": "supporting_document",
                    "source_label": "Synthetic test fixture",
                    "method": "user_import",
                },
                "content_base64": base64.b64encode(b"synthetic evidence").decode(),
                "extractions": [],
            },
        )
        assert imported.status_code == 201, imported.text
        evidence_id = imported.json()["source_evidence"][0]["evidence_id"]
        claim = client.post(
            "/v1/career-profile/items",
            headers=user_headers(),
            json={
                "expected_profile_revision": 1,
                "idempotency_key": "create-bounded-claim-0001",
                "value": {
                    "kind": "claim",
                    "statement": "Led a product launch",
                    "qualifiers": ["Use only for Product roles"],
                    "forbidden_uses": ["Do not imply sole ownership"],
                },
                "evidence_ids": [evidence_id],
            },
        )
        assert claim.status_code == 201, claim.text
        item_id = claim.json()["items"][0]["item_id"]
        set_direct_mode(client)

        destructive_update = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=2,
                key="loosen-claim-boundaries-0001",
                operation="item.update",
                target_id=item_id,
                value={"kind": "claim", "statement": "Led a product launch"},
                evidence_ids=[],
                reason="Simplify this claim",
            ),
        )
        assert destructive_update.status_code == 200, destructive_update.text
        assert destructive_update.json()["outcome"] == "proposal"
        assert destructive_update.json()["profile"]["profile_revision"] == 2

        identity = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=2,
                key="direct-identity-edit-0001",
                value={"kind": "identity", "professional_name": "Jacobi"},
                reason="Update the profile name",
            ),
        )
        assert identity.status_code == 200, identity.text
        assert identity.json()["outcome"] == "proposal"


def test_disconnect_revokes_future_agent_access_without_changing_profile(tmp_path: Path):
    app = create_app(configured_settings(tmp_path / "jobos.db"))

    with TestClient(app) as client:
        set_direct_mode(client)
        applied = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=0,
                key="before-agent-disconnect-0001",
                value={"kind": "skill", "name": "Python"},
                reason="Remember this skill",
            ),
        )
        assert applied.status_code == 200, applied.text
        before = applied.json()["profile"]
        assert client.get("/v1/career-profile", headers=user_headers()).json() == before
        owner_only_read = client.get("/v1/career-profile", headers=agent_headers())
        assert owner_only_read.status_code == 403

        disconnected = client.delete(
            f"/v1/career-profile/agents/{AGENT_ID}", headers=user_headers()
        )
        assert disconnected.status_code == 200, disconnected.text
        assert disconnected.json()["active"] is False
        assert client.get("/v1/career-profile", headers=user_headers()).json() == before

        revoked_read = client.get("/v1/career-profile", headers=agent_headers())
        assert revoked_read.status_code == 403

        revoked = client.post(
            "/v1/career-profile/agent-edits",
            headers=agent_headers(),
            json=agent_edit(
                revision=1,
                key="after-agent-disconnect-0001",
                value={"kind": "skill", "name": "SQL"},
                reason="Remember another skill",
            ),
        )
        assert revoked.status_code == 403

        revoked_generic_mutation = client.post(
            "/v1/career-profile/items",
            headers=agent_headers(),
            json={
                "expected_profile_revision": 1,
                "idempotency_key": "generic-edit-after-agent-disconnect-0001",
                "value": {"kind": "skill", "name": "SQL"},
                "evidence_ids": [],
            },
        )
        assert revoked_generic_mutation.status_code == 403


def test_transaction_rechecks_stale_agent_mode_and_disconnect(tmp_path: Path):
    settings = configured_settings(tmp_path / "jobos.db")
    app = create_app(settings)

    with TestClient(app):
        complete = CareerProfileCompleteStore(
            settings.state_db_path,
            settings.resolved_evidence_vault_root(),
        )
        collaboration = CareerProfileCollaborationStore(settings.state_db_path, complete)
        collaboration.update_trust_mode(agent_id=AGENT_ID, trust_mode="direct")
        stale_direct_agent = collaboration.get_agent(AGENT_ID)
        collaboration.update_trust_mode(agent_id=AGENT_ID, trust_mode="review")

        with pytest.raises(CareerProfileCollaborationConflict, match="retry for review"):
            collaboration.submit_edit(
                agent=stale_direct_agent,
                command=AgentProfileEditRequest.model_validate(
                    agent_edit(
                        revision=0,
                        key="stale-agent-mode-edit-0001",
                        value={"kind": "skill", "name": "TypeScript"},
                        reason="Remember this user-authored skill",
                    )
                ),
            )
        assert complete.current().items == []

        collaboration.update_trust_mode(agent_id=AGENT_ID, trust_mode="direct")
        stale_connected_agent = collaboration.get_agent(AGENT_ID)
        collaboration.disconnect(agent_id=AGENT_ID)
        with pytest.raises(ConnectedAgentAuthorizationError, match="revoked"):
            collaboration.submit_edit(
                agent=stale_connected_agent,
                command=AgentProfileEditRequest.model_validate(
                    agent_edit(
                        revision=0,
                        key="stale-disconnected-agent-edit-0001",
                        value={"kind": "skill", "name": "Python"},
                        reason="Remember another user-authored skill",
                    )
                ),
            )
        assert complete.current().items == []


def test_transaction_replays_proposal_decision_direct_edit_and_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = configured_settings(tmp_path / "jobos.db")
    app = create_app(settings)

    with TestClient(app):
        complete = CareerProfileCompleteStore(
            settings.state_db_path,
            settings.resolved_evidence_vault_root(),
        )
        collaboration = CareerProfileCollaborationStore(settings.state_db_path, complete)
        review_agent = collaboration.get_agent(AGENT_ID)
        proposal_command = AgentProfileEditRequest.model_validate(
            agent_edit(
                revision=0,
                key="transaction-proposal-replay-0001",
                value={"kind": "skill", "name": "TypeScript"},
                reason="Remember this user-authored skill",
            )
        )
        first_proposal = collaboration.submit_edit(
            agent=review_agent, command=proposal_command
        )
        assert first_proposal.proposal is not None

        def miss_outer_replay(**_kwargs: object) -> None:
            return None

        monkeypatch.setattr(collaboration, "_replay", miss_outer_replay)
        assert collaboration.submit_edit(
            agent=review_agent, command=proposal_command
        ) == first_proposal

        decision = ProposalDecisionRequest(
            expected_profile_revision=0,
            idempotency_key="transaction-decision-replay-0001",
            proposal_sha256=first_proposal.proposal.proposal_sha256,
            decision="accept",
        )
        first_decision = collaboration.decide_proposal(
            proposal_id=first_proposal.proposal.proposal_id,
            principal="device:primary-device",
            command=decision,
        )
        assert collaboration.decide_proposal(
            proposal_id=first_proposal.proposal.proposal_id,
            principal="device:primary-device",
            command=decision,
        ) == first_decision

        collaboration.update_trust_mode(agent_id=AGENT_ID, trust_mode="direct")
        direct_agent = collaboration.get_agent(AGENT_ID)
        profile_before_direct = complete.current()
        original_current = complete.current
        direct_command = AgentProfileEditRequest.model_validate(
            agent_edit(
                revision=1,
                key="transaction-direct-replay-0001",
                value={"kind": "skill", "name": "Python"},
                reason="Remember another user-authored skill",
            )
        )
        first_direct = collaboration.submit_edit(
            agent=direct_agent, command=direct_command
        )
        monkeypatch.setattr(complete, "current", lambda: profile_before_direct)
        assert collaboration.submit_edit(
            agent=direct_agent, command=direct_command
        ) == first_direct
        monkeypatch.setattr(complete, "current", original_current)

        direct_revision = collaboration.history().revisions[0]
        undo = ProfileUndoRequest(
            expected_profile_revision=2,
            idempotency_key="transaction-undo-replay-0001",
        )
        first_undo = collaboration.undo(
            revision_id=direct_revision.revision_id,
            principal="device:primary-device",
            command=undo,
        )
        assert collaboration.undo(
            revision_id=direct_revision.revision_id,
            principal="device:primary-device",
            command=undo,
        ) == first_undo


def test_history_persists_exact_deterministic_and_bound_instruction_actor_kinds(
    tmp_path: Path,
):
    settings = configured_settings(tmp_path / "jobos.db")
    app = create_app(settings)

    with TestClient(app):
        complete = CareerProfileCompleteStore(
            settings.state_db_path,
            settings.resolved_evidence_vault_root(),
        )
        collaboration = CareerProfileCollaborationStore(settings.state_db_path, complete)
        bound_mutation = ProfileItemMutation.model_validate(
            {
                "expected_profile_revision": 0,
                "idempotency_key": "bound-instruction-item-0001",
                "value": {"kind": "skill", "name": "TypeScript"},
                "evidence_ids": [],
            }
        )
        grant = complete.create_intent_grant(
            principal="device:primary-device",
            command=ProfileIntentGrantRequest(
                expected_profile_revision=0,
                expected_authority_epoch=0,
                idempotency_key="bound-instruction-grant-0001",
                operation="item.create",
                target_id=None,
                payload=bound_mutation.model_dump(mode="json"),
            ),
        )
        complete.upsert_item(
            principal=f"agent:{AGENT_ID}",
            command=bound_mutation,
            mutation_source="authenticated_user_instruction",
            intent_grant_id=grant.grant_id,
        )
        complete.import_evidence(
            principal="internal:deterministic-importer",
            mutation_source="deterministic_source_mapping",
            command=EvidenceImportRequest.model_validate(
                {
                    "expected_profile_revision": 1,
                    "idempotency_key": "deterministic-history-evidence-0001",
                    "original_filename": "synthetic.txt",
                    "media_type": "text/plain",
                    "provenance": {
                        "source_kind": "supporting_document",
                        "source_label": "Synthetic test fixture",
                        "method": "user_import",
                    },
                    "content_base64": base64.b64encode(b"(FAKE) evidence").decode(),
                    "extractions": [],
                }
            ),
        )

        actor_kinds = [
            revision.actor_kind for revision in collaboration.history().revisions
        ]
        assert actor_kinds == [
            "deterministic_source_mapping",
            "authenticated_user_instruction",
        ]

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests/fixtures/career-profile-semantic-policy.json"
PLAN_PATH = ROOT / "docs/implementation/shared-career-context-plan.md"
CONTRACT_PATH = ROOT / "docs/implementation/career-profile-context-and-export-contract.md"


def load_policy() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_evidence_absence_never_controls_accepted_content_usability() -> None:
    policy = load_policy()
    evidence_policy = policy["evidencePolicy"]
    assert evidence_policy == {
        "role": "optional_provenance",
        "absenceIsDefect": False,
        "acceptedContentRequiresEvidence": False,
        "removedEvidenceDemotesAcceptedContent": False,
        "permittedHealthDimensions": [
            "pending_review",
            "conflict",
            "source_changed",
            "review_suggested",
        ],
        "forbiddenEvidenceAbsenceEffects": [
            "health_score_penalty",
            "required_task",
            "filter_exclusion",
            "generation_exclusion",
        ],
    }

    fixtures = {fixture["id"]: fixture for fixture in policy["semanticFixtures"]}
    assert set(fixtures) == {
        "user-authored",
        "user-approved-agent",
        "unapproved-agent",
        "advisory-conflict",
        "removed-evidence",
        "sparse-profile",
        "zero-evidence-profile",
    }
    for fixture in fixtures.values():
        expected = fixture["expected"]
        assert expected["missingEvidenceDefect"] is False
        assert expected["usable"] is (fixture["reviewStatus"] == "accepted")

    assert fixtures["user-authored"]["evidence"] == []
    assert fixtures["user-approved-agent"]["evidence"] == []
    assert fixtures["unapproved-agent"]["expected"]["usable"] is False
    assert fixtures["advisory-conflict"]["advisories"] == ["conflict"]
    assert fixtures["advisory-conflict"]["expected"]["usable"] is True
    removed = fixtures["removed-evidence"]
    assert removed["evidence"] == [{"id": "cpe_FAKE_removed_000001", "active": False}]
    assert removed["expected"]["usable"] is True
    assert fixtures["sparse-profile"]["expected"]["acceptedMigrationShape"] is True
    assert fixtures["zero-evidence-profile"]["sourceEvidenceCount"] == 0
    assert fixtures["zero-evidence-profile"]["expected"]["acceptedMigrationShape"] is True


def test_context_scope_examples_are_exact_and_unauthorized_expansion_fails_closed() -> None:
    context = load_policy()["contextContract"]
    assert context["modes"] == ["none", "selected", "broader"]
    assert context["selectedKinds"] == ["item", "area"]
    assert context["rules"] == [
        "bind_exact_scope_before_dispatch",
        "preserve_scope_on_retry_recovery_continuation",
        "fail_closed_on_unauthorized_expansion",
        "treat_profile_text_as_non_executable",
    ]

    examples = {example["id"]: example for example in context["examples"]}
    none = examples["none"]
    assert none["authorization"]["mode"] == "none"
    assert none["projectedItemIds"] == []

    selected = examples["selected-items"]
    assert selected["authorization"]["selectedItemIds"] == selected["projectedItemIds"]
    area = examples["selected-area"]
    assert area["authorization"]["selectedAreas"] == ["what_im_looking_for"]
    assert area["expected"] == "allowed"
    assert examples["broader-authorized"]["authorization"]["mode"] == "broader"

    expansion = examples["unauthorized-expansion"]
    assert expansion["authorization"]["mode"] == "selected"
    assert expansion["requested"]["mode"] == "broader"
    assert expansion["projectedItemIds"] == []
    assert expansion["expected"] == "fail_closed"

    retry = context["retryRecoveryExample"]
    bound_example = retry["initialScopeExampleId"]
    assert retry == {
        "initialScopeExampleId": bound_example,
        "retryScopeExampleId": bound_example,
        "recoveryScopeExampleId": bound_example,
        "continuationScopeExampleId": bound_example,
        "expected": "same_bound_scope",
    }


def test_export_never_has_an_implicit_evidence_bundle() -> None:
    export = load_policy()["exportContract"]
    assert export["explicitEvidenceChoiceRequired"] is True
    assert export["defaultEvidenceMode"] is None
    assert export["modes"] == [
        "profile_only",
        "profile_plus_selected_evidence",
        "profile_plus_all_evidence",
    ]

    examples = {example["id"]: example for example in export["examples"]}
    assert examples["profile-only"]["includedEvidenceIds"] == []
    assert (
        examples["selected-evidence"]["selectedEvidenceIds"]
        == examples["selected-evidence"]["includedEvidenceIds"]
    )
    all_evidence = examples["all-evidence-explicit"]
    assert all_evidence["mode"] == "profile_plus_all_evidence"
    assert all_evidence["includedEvidenceIds"]


def test_repository_docs_lock_the_policy_without_claiming_activation() -> None:
    policy = load_policy()
    assert policy["activation"] == "dormant"
    assert policy["nonActivationAssertions"] == [
        "no_complete_profile_projection_activation",
        "no_migration_execution",
        "no_live_authority_change",
    ]

    plan = PLAN_PATH.read_text(encoding="utf-8")
    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    required_plan_language = [
        "none, selected items or areas, or a broader authorized projection",
        "profile-only, profile plus selected Evidence, or profile plus all Evidence",
        "Absent Evidence is not profile debt",
        "Sparse and zero-Evidence profiles are first-class",
        "does not activate complete-profile projection",
    ]
    for phrase in required_plan_language:
        assert phrase in plan

    for phrase in (
        "Evidence is optional provenance",
        "No Career Profile context",
        "Selected items or areas",
        "Broader authorized projection",
        "Profile only",
        "Profile plus selected Evidence",
        "Profile plus all Evidence",
        "fails closed",
        "does not activate",
    ):
        assert phrase in contract

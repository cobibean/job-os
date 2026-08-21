from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_review_brief_uses_user_approved_context_without_requiring_evidence():
    ideas = (REPOSITORY_ROOT / "docs/ideas.md").read_text(encoding="utf-8")

    required_contract = (
        "accepted user-authored or user-approved Career Profile information",
        "provenance `user stated`",
        "never require Evidence for a match",
        "absent Evidence as a qualification gap or blocker",
        "Agent-inferred unsupported matches remain proposals until the user approves them",
    )
    assert all(statement in ideas for statement in required_contract)

    obsolete_terms = (
        "trusted facts",
        "exact applicant evidence",
        "evidence-backed decision artifact",
        "requirement-to-evidence",
    )
    normalized_ideas = ideas.casefold()
    assert all(term not in normalized_ideas for term in obsolete_terms)

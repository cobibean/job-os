from __future__ import annotations

import json
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "docs/acceptance/connected-agents/evidence-index.json"


def expected_ids() -> set[str]:
    ranges = {
        "DOM": 5,
        "MIG": 4,
        "API": 4,
        "RTR": 3,
        "EVT": 3,
        "CDX": 1,
        "AUTH": 5,
        "MOD": 2,
        "HOST": 1,
        "CAP": 5,
        "UX": 8,
        "A11Y": 1,
        "VIS": 1,
        "CON": 2,
        "ISO": 2,
        "REC": 3,
        "SEC": 3,
        "RATE": 1,
        "PKG": 5,
        "INST": 2,
        "REG": 1,
    }
    return {
        f"{prefix}-{number:02d}"
        for prefix, count in ranges.items()
        for number in range(1, count + 1)
    }


def test_connected_agents_evidence_index_is_complete_unique_and_honest():
    value = json.loads(INDEX.read_text(encoding="utf-8"))
    entries = value["entries"]
    identifiers = [entry["id"] for entry in entries]

    assert value["schema_version"] == 1
    assert value["acceptance_count"] == 62
    assert len(identifiers) == len(set(identifiers)) == 62
    assert set(identifiers) == expected_ids()

    for entry in entries:
        assert entry["status"] in {"verified", "partial", "approval_blocked", "not_run"}
        assert 0 <= entry["owner_phase"] <= 8
        if entry["status"] == "verified":
            assert entry["source_commit"]
            assert len(entry["source_commit"]) == 40
            assert entry["proof_refs"]
            assert entry["remaining_proof"] is None
        else:
            assert entry["remaining_proof"]

    unresolved = {entry["id"] for entry in entries if entry["status"] != "verified"}
    assert unresolved == {
        "CAP-04",
        "A11Y-01",
        "VIS-01",
        "INST-02",
        "REG-01",
    }

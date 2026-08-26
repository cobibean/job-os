from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "docs/acceptance/connected-agents/evidence-index.json"
GITHUB_PR = re.compile(r"https://github\.com/cobibean/job-os/pull/[1-9][0-9]*$")


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
    assert value["evidence_scope"] == "historical_phase_acceptance"
    assert "Each entry proves only its source_commit" in value["current_commit_policy"]
    assert len(identifiers) == len(set(identifiers)) == 62
    assert set(identifiers) == expected_ids()

    for entry in entries:
        assert entry["status"] in {"verified", "partial", "approval_blocked", "not_run"}
        assert 0 <= entry["owner_phase"] <= 8
        if entry["status"] == "verified":
            assert entry["source_commit"]
            assert re.fullmatch(r"[0-9a-f]{40}", entry["source_commit"])
            assert entry["proof_refs"]
            assert entry["remaining_proof"] is None
            commit = subprocess.run(
                ["git", "cat-file", "-e", f'{entry["source_commit"]}^{{commit}}'],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            assert commit.returncode == 0, f'{entry["id"]} references an unreachable commit'
            reachable = subprocess.run(
                ["git", "merge-base", "--is-ancestor", entry["source_commit"], "HEAD"],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            assert reachable.returncode == 0, (
                f'{entry["id"]} source commit is not reachable from HEAD'
            )
            for proof_ref in entry["proof_refs"]:
                if proof_ref.startswith("https://"):
                    assert GITHUB_PR.fullmatch(proof_ref), (
                        f'{entry["id"]} has an unsupported proof URL'
                    )
                else:
                    proof_path = proof_ref.split("::", 1)[0]
                    resolved_root = ROOT.resolve()
                    resolved_proof = (ROOT / proof_path).resolve()
                    assert resolved_proof.is_relative_to(resolved_root), (
                        f'{entry["id"]} proof path escapes the repository'
                    )
                    assert resolved_proof.is_file(), (
                        f'{entry["id"]} references a missing proof file'
                    )
        else:
            assert entry["remaining_proof"]

    unresolved = {entry["id"] for entry in entries if entry["status"] != "verified"}
    assert not unresolved

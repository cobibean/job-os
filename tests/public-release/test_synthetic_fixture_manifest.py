from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).with_name("synthetic-fixtures.json")
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
LOG_SUFFIXES = {".log"}
CONTROLLED_BINARY_SUFFIXES = {
    ".docx",
    ".gif",
    ".icns",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".wav",
    ".webp",
    ".zip",
}
PROHIBITED_DIRECTORY_NAMES = {
    ".hermes": "agent run state",
    "backups": "backup data",
    "credentials": "credential material",
    "exports": "exported private data",
    "private": "private material",
    "secrets": "secret material",
}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def prohibited_reason(path: str) -> str | None:
    candidate = Path(path)
    lowercase_parts = tuple(part.casefold() for part in candidate.parts)
    name = candidate.name.casefold()
    if lowercase_parts[:2] == ("docs", "memory"):
        return "private session memory"
    for directory, reason in PROHIBITED_DIRECTORY_NAMES.items():
        if directory in lowercase_parts[:-1]:
            return reason
    if name == ".ds_store":
        return "operating-system metadata"
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file"
    if candidate.suffix.casefold() in DATABASE_SUFFIXES:
        return "database"
    if candidate.suffix.casefold() in LOG_SUFFIXES:
        return "log"
    return None


@pytest.mark.xfail(
    strict=True,
    reason="Phase 0 red gate: private memory and OS metadata remain in the tracked tree",
)
def test_public_tree_excludes_prohibited_path_classes():
    violations = {
        path: reason
        for path in tracked_files()
        if (reason := prohibited_reason(path)) is not None
    }

    assert violations == {}


def test_synthetic_fixture_manifest_is_tracked():
    manifest_path = str(MANIFEST_PATH.relative_to(REPOSITORY_ROOT))
    assert manifest_path in tracked_files()


def load_manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def tracked_controlled_binary_assets() -> set[str]:
    assets: set[str] = set()
    for relative_path in tracked_files():
        if prohibited_reason(relative_path) is not None:
            continue
        path = REPOSITORY_ROOT / relative_path
        if not path.is_file():
            continue
        contents = path.read_bytes()
        suffix_is_controlled = path.suffix.casefold() in CONTROLLED_BINARY_SUFFIXES
        try:
            contents.decode("utf-8")
            is_non_utf8_binary = False
        except UnicodeDecodeError:
            is_non_utf8_binary = True
        if suffix_is_controlled or is_non_utf8_binary:
            assets.add(relative_path)
    return assets


def test_every_tracked_binary_asset_has_a_publication_manifest_entry():
    manifest = load_manifest()
    assert manifest["schemaVersion"] == 1
    entries = manifest["assets"]
    assert isinstance(entries, list)

    declared_paths = {entry["path"] for entry in entries}
    assert len(declared_paths) == len(entries)
    assert declared_paths == tracked_controlled_binary_assets()
    assert all(entry["classification"] in {"project-asset", "synthetic"} for entry in entries)
    assert all(entry["publication"] == "keep" for entry in entries)
    assert all(entry["purpose"].strip() for entry in entries)
    assert all(entry["source"].strip() for entry in entries)

    synthetic_entries = [entry for entry in entries if entry["classification"] == "synthetic"]
    assert synthetic_entries
    assert all("/fixtures/" in entry["path"].casefold() for entry in synthetic_entries)
    tracked = set(tracked_files())
    for entry in synthetic_entries:
        provenance = entry["provenance"]
        assert provenance["trackedSource"] in tracked
        assert (REPOSITORY_ROOT / provenance["trackedSource"]).is_file()
        assert provenance["method"].strip()


def test_publication_manifest_checksums_match_tracked_bytes():
    entries = load_manifest()["assets"]
    assert isinstance(entries, list)

    mismatches: list[str] = []
    for entry in entries:
        asset_path = REPOSITORY_ROOT / entry["path"]
        actual = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            mismatches.append(entry["path"])

    assert mismatches == []

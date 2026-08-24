#!/usr/bin/env python3
"""Verify the publication manifest for tracked binary assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

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
REGISTERED_TEXT_FIXTURE_ROOTS = (
    "services/api/tests/fixtures/",
    "tests/connected_agents/fixtures/",
)
REQUIRED_TEXT_FIXTURE_ROOTS = ("tests/connected_agents/fixtures/",)


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def controlled_assets(root: Path, tracked: list[str]) -> set[str]:
    assets: set[str] = set()
    for relative_path in tracked:
        path = root / relative_path
        if path.is_symlink() or not path.is_file():
            continue
        contents = path.read_bytes()
        try:
            contents.decode("utf-8")
            binary = False
        except UnicodeDecodeError:
            binary = True
        if binary or path.suffix.casefold() in CONTROLLED_BINARY_SUFFIXES:
            assets.add(relative_path)
    return assets


def required_text_fixtures(tracked: list[str]) -> set[str]:
    return {
        relative_path
        for relative_path in tracked
        if relative_path.startswith(REQUIRED_TEXT_FIXTURE_ROOTS)
    }


def verify(root: Path, manifest_path: Path) -> list[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("schemaVersion") != 1:
        errors.append("manifest schemaVersion must equal 1")
    entries = manifest.get("assets")
    if not isinstance(entries, list):
        return [*errors, "manifest assets must be a list"]

    tracked = tracked_files(root)
    for relative_path in tracked:
        if (root / relative_path).is_symlink():
            errors.append(f"tracked symbolic link is not allowed: {relative_path}")
    paths = [entry.get("path") for entry in entries if isinstance(entry, dict)]
    if len(paths) != len(set(paths)):
        errors.append("manifest contains duplicate paths")
    declared = {path for path in paths if isinstance(path, str)}
    actual = controlled_assets(root, tracked) | required_text_fixtures(tracked)
    tracked_set = set(tracked)
    entry_by_path = {
        entry["path"]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    for path in sorted(actual - declared):
        errors.append(f"missing manifest entry: {path}")
    for path in sorted(declared - actual):
        entry = entry_by_path[path]
        is_registered_text_fixture = (
            path in tracked_set
            and entry.get("classification") == "synthetic"
            and path.startswith(REGISTERED_TEXT_FIXTURE_ROOTS)
            and (root / path).is_file()
        )
        if not is_registered_text_fixture:
            errors.append(f"stale manifest entry: {path}")

    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append("every asset entry must be an object with a path")
            continue
        relative_path = entry["path"]
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            errors.append(f"invalid asset path: {relative_path}")
            continue
        asset_path = root / relative_path
        if entry.get("classification") not in {"project-asset", "synthetic"}:
            errors.append(f"invalid classification: {relative_path}")
        if entry.get("publication") != "keep":
            errors.append(f"asset is not approved for publication: {relative_path}")
        if not str(entry.get("purpose", "")).strip() or not str(entry.get("source", "")).strip():
            errors.append(f"missing purpose/source: {relative_path}")
        if asset_path.is_file():
            actual_sha = hashlib.sha256(asset_path.read_bytes()).hexdigest()
            if entry.get("sha256") != actual_sha:
                errors.append(f"checksum mismatch: {relative_path}")
        if entry.get("classification") == "synthetic":
            provenance = entry.get("provenance")
            if not isinstance(provenance, dict):
                errors.append(f"missing synthetic provenance: {relative_path}")
                continue
            source = provenance.get("trackedSource")
            if source not in tracked_set or not str(provenance.get("method", "")).strip():
                errors.append(f"invalid synthetic provenance: {relative_path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = args.manifest or root / "tests/public-release/synthetic-fixtures.json"
    errors = verify(root, manifest)
    if errors:
        print("Fixture-manifest verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Fixture-manifest verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

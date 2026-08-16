#!/usr/bin/env python3
"""Fail when a tracked tree contains private release material."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROHIBITED_DIRECTORY_NAMES = {
    ".agent": "agent run state",
    ".hermes": "agent run state",
    "backups": "backup material",
    "credentials": "credential material",
    "exports": "exported data",
    "logs": "runtime logs",
    "private": "private material",
    "runtime-config": "runtime configuration",
    "secrets": "secret material",
    "support-bundles": "support bundle",
}
PROHIBITED_FILE_NAMES = {
    ".ds_store": "operating-system metadata",
    "desktop.ini": "operating-system metadata",
    "thumbs.db": "operating-system metadata",
}
PROHIBITED_SUFFIXES = {
    ".backup": "backup",
    ".bak": "backup",
    ".cer": "certificate",
    ".crt": "certificate",
    ".db": "database",
    ".key": "private key",
    ".log": "log",
    ".p12": "credential archive",
    ".pem": "key or certificate",
    ".pfx": "credential archive",
    ".sqlite": "database",
    ".sqlite3": "database",
}
TEXT_SUFFIXES = {
    "",
    ".bash",
    ".cjs",
    ".conf",
    ".css",
    ".cts",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".mts",
    ".plist",
    ".py",
    ".sh",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
CONTENT_PATTERNS = {  # public-tree: allow-pattern-fixture
    # Existing bundle and Keychain identifiers remain stable for installed-user
    # migration compatibility. They are public identifiers, not credentials.
    "operator workstation role": re.compile(
        r"Mac Mini",
        re.IGNORECASE,  # public-tree: allow-pattern-fixture
    ),  # public-tree: allow-pattern-fixture
    "operator display name": re.compile(
        r"Jacobi\s+Lange",
        re.IGNORECASE,  # public-tree: allow-pattern-fixture
    ),  # public-tree: allow-pattern-fixture
    "operator home path": re.compile(
        r"/Users/"  # public-tree: allow-pattern-fixture
        r"(?!example(?:/|$)|you(?:/|$)|username(?:/|$))"
    ),  # public-tree: allow-pattern-fixture
    "tailnet IPv4 identity": re.compile(
        r"(?<![0-9])100(?:[.]\d{1,3}){3}(?![0-9])"
    ),  # public-tree: allow-pattern-fixture
    "tailnet hostname": re.compile(
        r"\b[a-z0-9-]+[.]ts[.]net\b", re.IGNORECASE
    ),  # public-tree: allow-pattern-fixture
}
PATTERN_FIXTURE_PATHS = {
    "scripts/public-release/verify-public-tree.py",
    "tests/public-release/test_public_tree_boundary.py",
    "tests/public-release/test_verify_public_tree.py",
}


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def path_violation(relative_path: str) -> str | None:
    path = Path(relative_path)
    parts = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()

    if parts[:2] == ("docs", "memory"):
        return "private project memory"
    if name.startswith("._"):
        return "operating-system metadata"
    if name in PROHIBITED_FILE_NAMES:
        return PROHIBITED_FILE_NAMES[name]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file"
    for directory, reason in PROHIBITED_DIRECTORY_NAMES.items():
        if directory in parts[:-1]:
            return reason
    return PROHIBITED_SUFFIXES.get(path.suffix.casefold())


def content_violations(root: Path, relative_path: str) -> list[Violation]:
    path = root / relative_path
    if path.is_symlink():
        return [Violation(relative_path, "tracked symbolic link")]
    if not path.is_file() or path.suffix.casefold() not in TEXT_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    violations: list[Violation] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        fixture_window = " ".join(lines[index : index + 3])
        if (
            relative_path in PATTERN_FIXTURE_PATHS
            and "public-tree: allow-pattern-fixture" in fixture_window
        ):
            continue
        violations.extend(
            Violation(relative_path, reason)
            for reason, pattern in CONTENT_PATTERNS.items()
            if pattern.search(line)
        )
    return violations


def verify(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relative_path in tracked_files(root):
        if reason := path_violation(relative_path):
            violations.append(Violation(relative_path, reason))
            continue
        violations.extend(content_violations(root, relative_path))
    return sorted(set(violations), key=lambda item: (item.path, item.reason))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.root.resolve()
    violations = verify(root)
    if violations:
        print("Public-tree verification failed:")
        for violation in violations:
            print(f"- {violation.path}: {violation.reason}")
        return 1
    print("Public-tree verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

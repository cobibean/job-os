from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEXT_SOURCE_SUFFIXES = {
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
    ".mjs",
    ".mts",
    ".plist",
    ".py",
    ".sh",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
SCRIPT_IMPORT_SUFFIXES = {".cjs", ".cts", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx"}
SCRIPT_PRIVATE_IMPORT = re.compile(
    r"(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*(?:\(\s*)?)"
    r"['\"]job_hunter(?:[./][^'\"]*)?['\"]"
)
EXCLUDED_SOURCE_PARTS = {".hermes", "__pycache__", "docs", "fixtures", "tests"}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def public_runtime_files() -> list[Path]:
    paths: list[Path] = []
    for relative_path in tracked_files():
        candidate = Path(relative_path)
        if EXCLUDED_SOURCE_PARTS.intersection(part.casefold() for part in candidate.parts):
            continue
        if ".test." in candidate.name or ".spec." in candidate.name:
            continue
        if candidate.suffix.casefold() not in TEXT_SOURCE_SUFFIXES:
            continue
        paths.append(REPOSITORY_ROOT / candidate)
    return paths


def imported_python_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.xfail(
    strict=True,
    reason="Phase 0 red gate: the private JobHunter import moves behind a private adapter later",
)
def test_public_runtime_does_not_import_private_job_hunter_package():
    violations: list[str] = []
    runtime_files = public_runtime_files()
    for path in (candidate for candidate in runtime_files if candidate.suffix == ".py"):
        private_imports = sorted(
            module
            for module in imported_python_modules(path)
            if module == "job_hunter" or module.startswith("job_hunter.")
        )
        violations.extend(
            f"{path.relative_to(REPOSITORY_ROOT)} imports {module}" for module in private_imports
        )

    for path in runtime_files:
        if path.suffix.casefold() in SCRIPT_IMPORT_SUFFIXES and SCRIPT_PRIVATE_IMPORT.search(
            path.read_text(encoding="utf-8")
        ):
            violations.append(f"{path.relative_to(REPOSITORY_ROOT)} imports job_hunter")

    assert violations == []


def test_script_private_import_detection_covers_supported_forms():
    prohibited_imports = (
        'import "job_hunter"',
        'import adapter from "job_hunter/facade"',
        'export * from "job_hunter/storage"',
        'const adapter = require("job_hunter")',
        'const adapter = import("job_hunter/facade")',
    )
    assert all(SCRIPT_PRIVATE_IMPORT.search(statement) for statement in prohibited_imports)


@pytest.mark.xfail(
    strict=True,
    reason="Phase 0 red gate: public defaults still describe Cobi's private installation",
)
def test_public_defaults_contain_no_operator_or_private_network_identity():
    prohibited_patterns = {
        "operator bundle/service identifier": re.compile(r"com[.]cobibean", re.IGNORECASE),
        "operator workstation role": re.compile(r"Mac Mini", re.IGNORECASE),
        "absolute user home": re.compile(r"/Users/(?!example(?:/|$)|you(?:/|$)|username(?:/|$))"),
        "tailnet IPv4 identity": re.compile(r"\b100(?:[.]\d{1,3}){3}\b"),
        "private-network default": re.compile(r"private-tailscale", re.IGNORECASE),
    }
    violations: list[str] = []

    for path in public_runtime_files():
        text = path.read_text(encoding="utf-8")
        for label, pattern in prohibited_patterns.items():
            if pattern.search(text):
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)}: {label}")

    assert violations == []


def test_public_release_tests_are_part_of_default_pytest_collection():
    config = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"tests/public-release"' in config

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_URL = "https://github.com/cobibean/job-os.git"
LICENSE_INVENTORY_SPEC = importlib.util.spec_from_file_location(
    "verify_license_inventory",
    REPOSITORY_ROOT / "scripts/verify_license_inventory.py",
)
assert LICENSE_INVENTORY_SPEC is not None and LICENSE_INVENTORY_SPEC.loader is not None
license_inventory = importlib.util.module_from_spec(LICENSE_INVENTORY_SPEC)
LICENSE_INVENTORY_SPEC.loader.exec_module(license_inventory)
PUBLIC_ENTRY_POINTS = {
    "LICENSE",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/pull_request_template.md",
    "docs/public/architecture.md",
    "docs/public/data-privacy.md",
    "docs/public/troubleshooting.md",
    "docs/public/release-process.md",
}
NODE_METADATA_PATHS = {
    "package.json",
    "apps/desktop/package.json",
    "packages/contracts/package.json",
}
PYTHON_METADATA_PATHS = {
    "services/api/pyproject.toml",
    "services/mcp/pyproject.toml",
}
REQUIRED_README_PHRASES = {
    "source-first",
    "pre-release alpha",
    "local-first",
    "pnpm install --frozen-lockfile",
    "uv sync --all-packages --frozen",
    "pnpm check",
    "pnpm contracts:check",
    "synthetic demo job",
    "private adapter",
    '"private": true',
    "Apache License 2.0",
}


def read_json(relative_path: str) -> dict[str, object]:
    return json.loads((REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"))


def test_required_public_entry_points_exist():
    missing = sorted(
        relative_path
        for relative_path in PUBLIC_ENTRY_POINTS
        if not (REPOSITORY_ROOT / relative_path).is_file()
    )
    assert missing == []


def test_public_package_metadata_is_licensed_but_registry_private():
    for relative_path in NODE_METADATA_PATHS:
        package = read_json(relative_path)
        assert package["private"] is True
        assert package["license"] == "Apache-2.0"
        assert package["repository"] == {"type": "git", "url": REPOSITORY_URL}

    for relative_path in PYTHON_METADATA_PATHS:
        project = tomllib.loads(
            (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        )["project"]
        assert project["license"] == "Apache-2.0"
        assert project["urls"]["Repository"] == REPOSITORY_URL


def test_root_license_notice_and_upstream_provenance_are_preserved():
    license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")
    notice_text = (REPOSITORY_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "Copyright 2026 Cobi" in license_text
    assert "Copyright 2026 Cobi" in notice_text
    assert "Mainfunc, Inc." in notice_text

    for package in ("docx-engine", "docx-editor-core"):
        package_root = REPOSITORY_ROOT / "packages" / package
        assert {"LICENSE", "NOTICE", "UPSTREAM.md"}.issubset(
            {path.name for path in package_root.iterdir()}
        )
        upstream = (package_root / "UPSTREAM.md").read_text(encoding="utf-8")
        assert "d8305ff2dc152593a1ec5639d77e6860c6a512bd" in upstream
        assert "Apache-2.0" in upstream


def test_readme_states_the_source_alpha_contract_honestly():
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    normalized_readme = " ".join(readme.replace(">", " ").split())
    missing = sorted(phrase for phrase in REQUIRED_README_PHRASES if phrase not in readme)
    assert missing == []
    assert "The source-first clean-clone path is accepted" in normalized_readme
    assert "There is no supported public JobOS binary yet" in normalized_readme
    assert "not an announcement of public distribution" in normalized_readme


def test_public_markdown_relative_links_resolve():
    markdown_paths = [
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / "SECURITY.md",
        REPOSITORY_ROOT / "CONTRIBUTING.md",
        REPOSITORY_ROOT / "CODE_OF_CONDUCT.md",
        *sorted((REPOSITORY_ROOT / "docs/public").glob("*.md")),
    ]
    missing: list[str] = []
    for markdown_path in markdown_paths:
        text = markdown_path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            relative_target = target.split("#", 1)[0]
            if not (markdown_path.parent / relative_target).resolve().exists():
                missing.append(f"{markdown_path.relative_to(REPOSITORY_ROOT)} -> {target}")
    assert missing == []


def test_license_inventory_discovers_new_node_workspaces(tmp_path, monkeypatch):
    (tmp_path / "apps/new-package").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"root-dependency": "1.0.0"}}),
        encoding="utf-8",
    )
    (tmp_path / "apps/new-package/package.json").write_text(
        json.dumps({"dependencies": {"new-dependency": "1.0.0"}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    monkeypatch.setattr(license_inventory, "ROOT", tmp_path)

    assert license_inventory.node_manifest_paths() == [
        "apps/new-package/package.json",
        "package.json",
    ]
    assert license_inventory.direct_node_dependencies() == {
        "new-dependency",
        "root-dependency",
    }


def test_license_inventory_discovers_new_python_workspace_members(tmp_path, monkeypatch):
    (tmp_path / "services/new-service").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["services/*"]\n',
        encoding="utf-8",
    )
    (tmp_path / "services/new-service/pyproject.toml").write_text(
        '[project]\nname = "new-service"\nversion = "0.1.0"\ndependencies = ["new-dependency"]\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(license_inventory, "ROOT", tmp_path)

    assert license_inventory.python_manifest_paths() == [
        "services/new-service/pyproject.toml"
    ]
    assert license_inventory.direct_python_dependencies() == {"new-dependency"}


def test_packaged_legal_resource_verifier_checks_real_output(tmp_path):
    for _, destination in license_inventory.REQUIRED_PACKAGED_NOTICES:
        destination_path = tmp_path / destination
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_text("legal material\n", encoding="utf-8")

    license_inventory.verify_packaged_legal_resources(tmp_path)

    (tmp_path / "NOTICE").unlink()
    with pytest.raises(AssertionError, match="NOTICE"):
        license_inventory.verify_packaged_legal_resources(tmp_path)

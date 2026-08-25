from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_NODE_PACKAGES = (
    "package.json",
    "apps/desktop/package.json",
    "packages/contracts/package.json",
)
REPOSITORY_URL = "https://github.com/cobibean/job-os.git"
UNKNOWN_LICENSES = {"", "unknown", "unlicensed", "none", "null"}
REQUIRED_PACKAGED_NOTICES = {
    ("../../THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
    ("../../LICENSE", "LICENSE"),
    ("../../NOTICE", "NOTICE"),
    ("../../packages/docx-engine/LICENSE", "licenses/docx-engine/LICENSE"),
    ("../../packages/docx-engine/NOTICE", "licenses/docx-engine/NOTICE"),
    ("../../packages/docx-engine/UPSTREAM.md", "licenses/docx-engine/UPSTREAM.md"),
    ("../../packages/docx-editor-core/LICENSE", "licenses/docx-editor-core/LICENSE"),
    ("../../packages/docx-editor-core/NOTICE", "licenses/docx-editor-core/NOTICE"),
    (
        "../../packages/docx-editor-core/UPSTREAM.md",
        "licenses/docx-editor-core/UPSTREAM.md",
    ),
    (
        "../../tests/connected_agents/receipts/codex-0.144.4/LICENSE",
        "licenses/codex/LICENSE",
    ),
    (
        "../../tests/connected_agents/receipts/codex-0.144.4/NOTICE",
        "licenses/codex/NOTICE",
    ),
}
ADAPTED_SOURCE_ROOTS = (
    "packages/docx-engine/scripts/",
    "packages/docx-engine/src/",
    "packages/docx-engine/tests/",
    "packages/docx-editor-core/src/",
)
ADAPTED_TEXT_SUFFIXES = {".cjs", ".css", ".js", ".jsx", ".mjs", ".ts", ".tsx"}
GENOFFICE_CHANGE_NOTICE = "part of JobOS's modified GenOffice-derived package"


def load_json(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def node_manifest_paths() -> list[str]:
    return sorted(path for path in tracked_files() if Path(path).name == "package.json")


def python_manifest_paths() -> list[str]:
    workspace = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    members = workspace["tool"]["uv"]["workspace"]["members"]
    paths: list[str] = []
    for member_pattern in members:
        for member in ROOT.glob(member_pattern):
            manifest = member / "pyproject.toml"
            if manifest.is_file():
                paths.append(str(manifest.relative_to(ROOT)))
    return sorted(paths)


def direct_node_dependencies() -> set[str]:
    dependencies: set[str] = set()
    for path in node_manifest_paths():
        for name, version in load_json(path).get("dependencies", {}).items():
            if not version.startswith("workspace:"):
                dependencies.add(name)
    return dependencies


def installed_node_licenses() -> tuple[dict[str, set[str]], set[str]]:
    result = subprocess.run(
        ["pnpm", "licenses", "list", "--json", "--prod"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    inventory = json.loads(result.stdout)
    licenses_by_name: dict[str, set[str]] = {}
    unknown_categories: set[str] = set()
    for license_name, packages in inventory.items():
        if license_name.strip().casefold() in UNKNOWN_LICENSES:
            unknown_categories.add(license_name)
        for package in packages:
            licenses_by_name.setdefault(package["name"], set()).add(license_name)
    return licenses_by_name, unknown_categories


def direct_python_dependencies() -> set[str]:
    dependencies: set[str] = set()
    for path in python_manifest_paths():
        project = tomllib.loads((ROOT / path).read_text(encoding="utf-8"))["project"]
        for requirement in project["dependencies"]:
            match = re.match(r"[A-Za-z0-9_.-]+", requirement)
            if not match:
                raise AssertionError(f"Cannot parse dependency in {path}: {requirement}")
            dependencies.add(match.group(0))
    return dependencies


def python_license(name: str) -> str:
    try:
        package_metadata: Any = metadata(name)
    except PackageNotFoundError as error:
        raise AssertionError(
            f"Python dependency {name!r} is not installed; run uv sync --all-packages --frozen"
        ) from error
    license_name = package_metadata.get("License-Expression") or package_metadata.get("License")
    if license_name and license_name.strip().casefold() not in UNKNOWN_LICENSES:
        return license_name.strip()
    classifiers = [
        classifier.removeprefix("License :: ")
        for classifier in package_metadata.get_all("Classifier", [])
        if classifier.startswith("License :: ")
    ]
    if classifiers:
        return "; ".join(classifiers)
    raise AssertionError(f"Python dependency {name!r} has no declared license metadata")


def verify_project_metadata() -> None:
    for path in PUBLIC_NODE_PACKAGES:
        package = load_json(path)
        assert package["private"] is True, f"{path} must remain registry-private"
        assert package["license"] == "Apache-2.0", f"{path} has wrong license"
        assert package["repository"]["url"] == REPOSITORY_URL, f"{path} has wrong repository"

    for path in python_manifest_paths():
        project = tomllib.loads((ROOT / path).read_text(encoding="utf-8"))["project"]
        assert project["license"] == "Apache-2.0", f"{path} has wrong license"
        assert project["urls"]["Repository"] == REPOSITORY_URL, f"{path} has wrong repository"


def verify_notices_and_provenance() -> None:
    root_license = (ROOT / "LICENSE").read_text(encoding="utf-8")
    root_notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Apache License" in root_license and "Version 2.0" in root_license
    assert "Copyright 2026 Cobi" in root_license
    assert "Copyright 2026 Cobi" in root_notice
    assert "Mainfunc, Inc." in root_notice

    for package in ("docx-engine", "docx-editor-core"):
        package_root = ROOT / "packages" / package
        for filename in ("LICENSE", "NOTICE", "UPSTREAM.md"):
            assert (package_root / filename).is_file(), f"missing {package}/{filename}"
        upstream = (package_root / "UPSTREAM.md").read_text(encoding="utf-8")
        assert "d8305ff2dc152593a1ec5639d77e6860c6a512bd" in upstream
        assert "Apache-2.0" in upstream

    missing_change_notices: list[str] = []
    for relative_path in tracked_files():
        path = Path(relative_path)
        if not any(relative_path.startswith(root) for root in ADAPTED_SOURCE_ROOTS):
            continue
        if path.suffix.casefold() not in ADAPTED_TEXT_SUFFIXES:
            continue
        first_lines = "\n".join((ROOT / path).read_text(encoding="utf-8").splitlines()[:3])
        if GENOFFICE_CHANGE_NOTICE not in first_lines:
            missing_change_notices.append(relative_path)
    assert not missing_change_notices, (
        "GenOffice-derived files missing prominent JobOS change notices: "
        f"{missing_change_notices}"
    )

    resources = {
        (entry["from"], entry["to"])
        for entry in load_json("apps/desktop/package.json")["build"]["extraResources"]
    }
    missing = REQUIRED_PACKAGED_NOTICES - resources
    assert not missing, f"desktop package is missing legal resources: {sorted(missing)}"


def verify_packaged_legal_resources(resources_root: Path) -> None:
    missing = sorted(
        destination
        for _, destination in REQUIRED_PACKAGED_NOTICES
        if not (resources_root / destination).is_file()
    )
    assert not missing, f"packaged application is missing legal resources: {missing}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--packaged-resources",
        type=Path,
        help="Verify legal files in an unpacked application's resources directory.",
    )
    args = parser.parse_args()

    verify_project_metadata()
    verify_notices_and_provenance()
    if args.packaged_resources:
        verify_packaged_legal_resources(args.packaged_resources)

    node_licenses, unknown_categories = installed_node_licenses()
    assert not unknown_categories, f"unknown Node license categories: {sorted(unknown_categories)}"
    missing_node = sorted(direct_node_dependencies() - node_licenses.keys())
    assert not missing_node, f"direct Node dependencies missing license metadata: {missing_node}"

    python_licenses = {
        name: python_license(name) for name in sorted(direct_python_dependencies())
    }

    node_categories = sorted(
        {license_name for names in node_licenses.values() for license_name in names}
    )
    print(
        json.dumps(
            {
                "directNodeDependencies": len(direct_node_dependencies()),
                "nodeLicenseCategories": node_categories,
                "directPythonDependencies": python_licenses,
                "packagedLegalResources": len(REQUIRED_PACKAGED_NOTICES),
                "packagedOutputVerified": bool(args.packaged_resources),
                "status": "ok",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

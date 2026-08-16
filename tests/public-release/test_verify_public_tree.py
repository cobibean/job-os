from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFY_TREE = REPOSITORY_ROOT / "scripts/public-release/verify-public-tree.py"


def run_verifier(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(VERIFY_TREE), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def initialize_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


def track(root: Path, relative_path: str, contents: str = "fixture\n") -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    subprocess.run(["git", "add", relative_path], cwd=root, check=True)


@pytest.mark.parametrize(
    "relative_path",
    [
        "docs/memory/session.md",
        "docs/.DS_Store",
        "docs/._notes.md",
        ".env.local",
        "credentials/token.json",
        "data/jobs.sqlite3",
        "logs/jobos.log",
        "exports/jobs.json",
        "private/operator.md",
        "certs/signing.key",
        ".hermes/session.json",
        ".agent/runs/goal.html",
    ],
)
def test_verifier_rejects_prohibited_tracked_path_classes(tmp_path: Path, relative_path: str):
    initialize_repo(tmp_path)
    track(tmp_path, relative_path)

    result = run_verifier(tmp_path)

    assert result.returncode == 1
    assert relative_path in result.stdout


@pytest.mark.parametrize(
    ("relative_path", "contents"),
    [
        (".env.example", "JOBOS_DEVICE_TOKEN=replace-me\n"),
        ("src/config.ts", 'export const home = "/Users/example/JobOS";\n'),
        ("fixtures/demo.json", '{"company":"Example Labs (Synthetic Demo)"}\n'),
    ],
)
def test_verifier_allows_public_examples(tmp_path: Path, relative_path: str, contents: str):
    initialize_repo(tmp_path)
    track(tmp_path, relative_path, contents)

    result = run_verifier(tmp_path)

    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "contents",
    [
        'const root = "/Users/real-person/JobOS";\n',  # public-tree: allow-pattern-fixture
        'const host = "private-machine.ts.net";\n',  # public-tree: allow-pattern-fixture
        'const host = "100.64.0.7";\n',  # public-tree: allow-pattern-fixture
        'const role = "Mac Mini";\n',  # public-tree: allow-pattern-fixture
        'const displayName = "Jacobi Lange";\n',  # public-tree: allow-pattern-fixture
    ],
)
def test_verifier_rejects_operator_identity_content(tmp_path: Path, contents: str):
    initialize_repo(tmp_path)
    track(tmp_path, "src/config.ts", contents)

    result = run_verifier(tmp_path)

    assert result.returncode == 1
    assert "src/config.ts" in result.stdout


def test_current_tree_passes_public_verifier():
    result = run_verifier(REPOSITORY_ROOT)
    assert result.returncode == 0, result.stdout


def test_verifier_rejects_tracked_symlink(tmp_path: Path):
    initialize_repo(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("public text\n", encoding="utf-8")
    link = tmp_path / "src/config.ts"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    subprocess.run(["git", "add", "src/config.ts"], cwd=tmp_path, check=True)

    result = run_verifier(tmp_path)

    assert result.returncode == 1
    assert "tracked symbolic link" in result.stdout


def test_verifier_scans_public_release_directories(tmp_path: Path):
    initialize_repo(tmp_path)
    track(
        tmp_path, "tests/public-release/leak.md", "/Users/real-person/private\n"
    )  # public-tree: allow-pattern-fixture

    result = run_verifier(tmp_path)

    assert result.returncode == 1
    assert "tests/public-release/leak.md" in result.stdout

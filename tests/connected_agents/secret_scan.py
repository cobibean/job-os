"""Fail-closed recursive secret-canary scanning for acceptance evidence."""

from __future__ import annotations

import gzip
import hashlib
import io
import sqlite3
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_DEPTH = 4
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 64 * 1024 * 1024
SQLITE_MAGIC = b"SQLite format 3\x00"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
ZIP_SUFFIXES = (".zip",)
TAR_GZIP_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".gz")


@dataclass(frozen=True)
class SecretCanary:
    label: str
    value: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.value.encode()).hexdigest()


@dataclass(frozen=True)
class SecretFinding:
    path: str
    container: str
    canary_label: str


@dataclass(frozen=True)
class SecretScanIssue:
    path: str
    container: str
    reason: str


class SecretCanaryDetected(AssertionError):
    def __init__(self, findings: tuple[SecretFinding, ...]) -> None:
        self.findings = findings
        summary = ",".join(
            f"{finding.path}:{finding.container}:{finding.canary_label}"
            for finding in findings
        )
        super().__init__(f"secret_canary_detected:{summary}")


class SecretScanIncomplete(AssertionError):
    """An evidence surface could not be inspected completely."""

    def __init__(self, issues: tuple[SecretScanIssue, ...]) -> None:
        self.issues = issues
        summary = ",".join(
            f"{issue.path}:{issue.container}:{issue.reason}" for issue in issues
        )
        super().__init__(f"secret_scan_incomplete:{summary}")


def phase0_canaries() -> tuple[SecretCanary, ...]:
    return (
        SecretCanary("device-token", "(FAKE)-JOBOS-DEVICE-TOKEN-canary-111"),
        SecretCanary("oauth-token", "(FAKE)-OAUTH-ACCESS-TOKEN-canary-111"),
        SecretCanary("device-code", "(FAKE)-DEVICE-CODE-canary-111"),
    )


def _safe_member_name(name: str) -> str:
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        return "(unsafe-archive-member)"
    return str(candidate)


def _raw_findings(
    data: bytes,
    *,
    display_path: str,
    container: str,
    canaries: tuple[SecretCanary, ...],
) -> list[SecretFinding]:
    return [
        SecretFinding(display_path, container, canary.label)
        for canary in canaries
        if canary.value.encode() in data
    ]


def _looks_like_archive(data: bytes) -> bool:
    return data.startswith(ZIP_MAGIC) or data.startswith(b"\x1f\x8b") or data[257:262] == b"ustar"


def _scan_bytes(
    data: bytes,
    *,
    display_path: str,
    canaries: tuple[SecretCanary, ...],
    depth: int,
    issues: list[SecretScanIssue],
) -> list[SecretFinding]:
    findings = _raw_findings(
        data, display_path=display_path, container="raw", canaries=canaries
    )
    member_name = display_path.rsplit("!", maxsplit=1)[-1].casefold()
    if member_name.endswith(ZIP_SUFFIXES) and not data.startswith(ZIP_MAGIC):
        issues.append(SecretScanIssue(display_path, "zip", "malformed_archive"))
        return findings
    if member_name.endswith(TAR_GZIP_SUFFIXES) and not (
        data.startswith(b"\x1f\x8b") or data[257:262] == b"ustar"
    ):
        issues.append(SecretScanIssue(display_path, "archive", "malformed_archive"))
        return findings
    if depth >= MAX_ARCHIVE_DEPTH:
        if _looks_like_archive(data):
            issues.append(SecretScanIssue(display_path, "archive", "archive_depth_limit"))
        return findings

    if data.startswith(ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                total = 0
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    name = _safe_member_name(member.filename)
                    member_path = f"{display_path}!{name}"
                    total += member.file_size
                    if member.file_size > MAX_MEMBER_BYTES:
                        issues.append(
                            SecretScanIssue(member_path, "zip", "archive_member_too_large")
                        )
                        continue
                    if total > MAX_ARCHIVE_TOTAL_BYTES:
                        issues.append(
                            SecretScanIssue(display_path, "zip", "archive_expansion_limit")
                        )
                        break
                    try:
                        member_data = archive.read(member)
                    except (OSError, RuntimeError, zipfile.BadZipFile):
                        issues.append(
                            SecretScanIssue(member_path, "zip", "archive_member_unreadable")
                        )
                        continue
                    findings.extend(
                        _scan_bytes(
                            member_data,
                            display_path=member_path,
                            canaries=canaries,
                            depth=depth + 1,
                            issues=issues,
                        )
                    )
        except (OSError, EOFError, zipfile.BadZipFile):
            issues.append(SecretScanIssue(display_path, "zip", "malformed_archive"))
        return findings

    if data.startswith(b"\x1f\x8b") or data[257:262] == b"ustar":
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                total = 0
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    name = _safe_member_name(member.name)
                    member_path = f"{display_path}!{name}"
                    total += member.size
                    if member.size > MAX_MEMBER_BYTES:
                        issues.append(
                            SecretScanIssue(member_path, "tar", "archive_member_too_large")
                        )
                        continue
                    if total > MAX_ARCHIVE_TOTAL_BYTES:
                        issues.append(
                            SecretScanIssue(display_path, "tar", "archive_expansion_limit")
                        )
                        break
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        issues.append(
                            SecretScanIssue(member_path, "tar", "archive_member_unreadable")
                        )
                        continue
                    findings.extend(
                        _scan_bytes(
                            extracted.read(),
                            display_path=member_path,
                            canaries=canaries,
                            depth=depth + 1,
                            issues=issues,
                        )
                    )
            return findings
        except (OSError, EOFError, tarfile.TarError):
            if not data.startswith(b"\x1f\x8b"):
                issues.append(SecretScanIssue(display_path, "tar", "malformed_archive"))
                return findings
        try:
            expanded = gzip.decompress(data)
        except (OSError, EOFError, gzip.BadGzipFile):
            issues.append(SecretScanIssue(display_path, "gzip", "malformed_archive"))
            return findings
        if len(expanded) > MAX_MEMBER_BYTES:
            issues.append(SecretScanIssue(display_path, "gzip", "archive_member_too_large"))
            return findings
        findings.extend(
            _scan_bytes(
                expanded,
                display_path=f"{display_path}!gzip",
                canaries=canaries,
                depth=depth + 1,
                issues=issues,
            )
        )
    return findings


def _scan_sqlite(
    path: Path,
    *,
    display_path: str,
    canaries: tuple[SecretCanary, ...],
    issues: list[SecretScanIssue],
) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table_name,) in tables:
            quoted = '"' + str(table_name).replace('"', '""') + '"'
            for row in connection.execute(f"SELECT * FROM {quoted}"):
                encoded = "\0".join(
                    value.hex() if isinstance(value, bytes) else str(value) for value in row
                ).encode()
                findings.extend(
                    _raw_findings(
                        encoded,
                        display_path=display_path,
                        container=f"sqlite:{table_name}",
                        canaries=canaries,
                    )
                )
    except sqlite3.Error:
        issues.append(SecretScanIssue(display_path, "sqlite", "database_unreadable"))
    finally:
        if connection is not None:
            connection.close()
    return findings


def _scan_paths(
    root: Path, canaries: tuple[SecretCanary, ...]
) -> tuple[tuple[SecretFinding, ...], tuple[SecretScanIssue, ...]]:
    if not root.exists():
        return (), (SecretScanIssue(root.name or ".", "root", "evidence_root_missing"),)
    paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
    findings: list[SecretFinding] = []
    issues: list[SecretScanIssue] = []
    for path in paths:
        display_path = path.name if root.is_file() else str(path.relative_to(root))
        try:
            data = path.read_bytes()
        except OSError:
            issues.append(SecretScanIssue(display_path, "raw", "file_unreadable"))
            continue
        findings.extend(
            _scan_bytes(
                data,
                display_path=display_path,
                canaries=canaries,
                depth=0,
                issues=issues,
            )
        )
        sqlite_expected = path.name.casefold().endswith(SQLITE_SUFFIXES)
        if sqlite_expected and not data.startswith(SQLITE_MAGIC):
            issues.append(SecretScanIssue(display_path, "sqlite", "database_unreadable"))
        elif data.startswith(SQLITE_MAGIC):
            findings.extend(
                _scan_sqlite(
                    path,
                    display_path=display_path,
                    canaries=canaries,
                    issues=issues,
                )
            )
    unique_findings = {
        (finding.path, finding.container, finding.canary_label): finding for finding in findings
    }
    unique_issues = {(issue.path, issue.container, issue.reason): issue for issue in issues}
    return (
        tuple(unique_findings[key] for key in sorted(unique_findings)),
        tuple(unique_issues[key] for key in sorted(unique_issues)),
    )


def scan_secret_canaries(
    root: Path, *, canaries: tuple[SecretCanary, ...] | None = None
) -> tuple[SecretFinding, ...]:
    findings, issues = _scan_paths(root, canaries or phase0_canaries())
    if issues:
        raise SecretScanIncomplete(issues)
    return findings


def assert_no_secret_canaries(
    root: Path, *, canaries: tuple[SecretCanary, ...] | None = None
) -> None:
    findings, issues = _scan_paths(root, canaries or phase0_canaries())
    if findings:
        raise SecretCanaryDetected(findings)
    if issues:
        raise SecretScanIncomplete(issues)

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path

import pytest
from jobos_api import state_store
from jobos_api.installation_profiles import InstallationProfileRegistryData

from . import packaged_host, secret_scan
from .build_profile_v31_fixture import build
from .events import (
    SUPPORTED_KINDS,
    EventTrace,
    EventTraceViolation,
    NormalizedEvent,
    TraceExpectation,
    assert_trace_isolation,
)
from .fakes import DeterministicFakeCredentialVault, DeterministicFakeProvider, FakeBinding
from .faults import ConcurrencyCoordinator, DeterministicFaultInjector, InjectedFault
from .packaged_host import (
    MAX_CAPTURE_BYTES,
    MIN_LISTENER_AUDIT_SECONDS,
    PackagedHostError,
    isolated_host_environment,
    run_packaged_host,
)
from .remote_device import (
    RemoteAuthorizationError,
    RemoteDeviceFixture,
    authorize_remote_device,
    load_remote_devices,
)
from .run_phase0_baseline import (
    checkout_is_clean,
    code_snapshot_sha256,
    installed_hermes_smoke,
    isolated_environment,
    loopback_base_url,
    run_command,
)
from .secret_scan import (
    SecretCanaryDetected,
    SecretScanIncomplete,
    assert_no_secret_canaries,
    phase0_canaries,
    scan_secret_canaries,
)
from .sqlite_proof import (
    SQLiteSnapshotMismatch,
    assert_exact_snapshot,
    restore_sql_fixture,
    snapshot_sqlite,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
BASELINE = ROOT / "docs/acceptance/connected-agents/phase-0/baseline-manifest.json"
REDISTRIBUTION = (
    ROOT / "docs/acceptance/connected-agents/phase-0/codex-redistribution-candidate.json"
)
VERIFICATION_RESULTS = (
    ROOT / "docs/acceptance/connected-agents/phase-0/verification-results.json"
)
MAX_ARCHIVE_TEST_BYTES = 17 * 1024 * 1024


def event(sequence: int, *, source: str | None = None, chat: str = "(FAKE)-chat-a"):
    kinds = {1: "turn_started", 2: "assistant_text_delta", 3: "turn_completed"}
    return NormalizedEvent(
        sequence=sequence,
        source_event_id=source or f"(FAKE)-source-{sequence}",
        timestamp=f"2026-08-01T12:00:{sequence:02d}Z",
        profile_id="jprof_11111111111111111111111111111111",
        conversation_id=chat,
        turn_id="(FAKE)-turn-a",
        kind=kinds[sequence],  # type: ignore[arg-type]
        payload={"text": "(FAKE) synthetic"} if sequence == 2 else {},
    )


def test_real_registry_v1_fixture_validates_against_pre_feature_model():
    fixture_receipt = json.loads(
        (FIXTURES / "fixture-receipt.json").read_text(encoding="utf-8")
    )
    registry_path = FIXTURES / "(FAKE)-installation-registry-v1.json"
    assert hashlib.sha256(registry_path.read_bytes()).hexdigest() == fixture_receipt[
        "installation_registry_v1"
    ]["sha256"]
    payload = json.loads(
        registry_path.read_text(encoding="utf-8")
    )

    validated = InstallationProfileRegistryData.model_validate(payload)

    assert validated.schema_version == 1
    assert validated.registry_revision == 7
    assert validated.active_profile_id == "jprof_11111111111111111111111111111111"
    assert validated.profiles[0].display_name == "(FAKE) Existing Profile"


def test_profile_v31_sql_is_reproducible_and_has_exact_history(tmp_path: Path):
    fixture = FIXTURES / "(FAKE)-profile-v31.sql"
    if state_store.SCHEMA_VERSION == 31:
        regenerated = tmp_path / "regenerated.sql"
        build(regenerated)
        assert regenerated.read_bytes() == fixture.read_bytes()

    database = tmp_path / "profile.db"
    snapshot = restore_sql_fixture(fixture, database)
    fixture_receipt = json.loads(
        (FIXTURES / "fixture-receipt.json").read_text(encoding="utf-8")
    )
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == fixture_receipt[
        "profile_sqlite_v31"
    ]["sha256"]
    assert snapshot.schema_version == 31
    assert snapshot.integrity == ("ok",)
    assert snapshot.sha256() == fixture_receipt["profile_sqlite_v31"][
        "canonical_snapshot_sha256"
    ]
    conversations = next(rows for name, _, rows in snapshot.rows if name == "conversations")
    turns = next(rows for name, _, rows in snapshot.rows if name == "conversation_turns")
    events = next(rows for name, _, rows in snapshot.rows if name == "conversation_events")
    assert conversations == (
        (
            "conv_current",
            1,
            "(FAKE) Existing Hermes Chat",
            "(FAKE)-opaque-hermes-session-1",
            None,
            None,
            None,
            None,
            None,
            None,
            "2026-08-01T12:00:00Z",
            "2026-08-01T12:00:00Z",
            "(FAKE)-authorized-macbook",
            "(FAKE)-job-legacy-1",
            None,
            1,
            1.0,
        ),
    )
    assert turns[0][:9] == (
        "(FAKE)-turn-legacy-1",
        "conv_current",
        "(FAKE)-message-legacy-1",
        None,
        "(FAKE) Summarize the synthetic role.",
        '{"selected_job_id":"(FAKE)-job-legacy-1"}',
        "completed",
        0,
        "2026-08-01T12:00:00Z",
    )
    assert tuple(row[7] for row in events) == (
        "(FAKE)-source-event-1",
        "(FAKE)-source-event-2",
    )


def test_profile_v31_sql_restores_with_foreign_keys_enabled(tmp_path: Path):
    database = tmp_path / "foreign-key-checked.db"
    fixture = FIXTURES / "(FAKE)-profile-v31.sql"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(fixture.read_text(encoding="utf-8"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_exact_sqlite_snapshot_detects_deliberate_migration_corruption(tmp_path: Path):
    database = tmp_path / "profile.db"
    before = restore_sql_fixture(FIXTURES / "(FAKE)-profile-v31.sql", database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE conversations SET stored_session_id = ? WHERE conversation_id = ?",
            ("(FAKE)-corrupted-session", "conv_current"),
        )
    after = snapshot_sqlite(database)

    with pytest.raises(SQLiteSnapshotMismatch) as caught:
        assert_exact_snapshot(before, after)

    assert caught.value.coordinates == ("rows:conversations",)
    assert before.sha256() != after.sha256()


def test_event_trace_detects_duplicate_out_of_order_and_cross_chat_leakage():
    assert {"connection_changed", "recovery_required"} <= SUPPORTED_KINDS
    duplicate = EventTrace(
        profile_id=event(1).profile_id,
        conversation_id=event(1).conversation_id,
        turn_id=event(1).turn_id,
    )
    duplicate.append(event(1, source="(FAKE)-duplicate"))
    with pytest.raises(EventTraceViolation, match="duplicate_source_event"):
        duplicate.append(event(2, source="(FAKE)-duplicate"))

    out_of_order = EventTrace(
        profile_id=event(1).profile_id,
        conversation_id=event(1).conversation_id,
        turn_id=event(1).turn_id,
    )
    with pytest.raises(EventTraceViolation, match="event_sequence_out_of_order"):
        out_of_order.append(event(2))

    scoped = EventTrace(
        profile_id=event(1).profile_id,
        conversation_id=event(1).conversation_id,
        turn_id=event(1).turn_id,
    )
    with pytest.raises(EventTraceViolation, match="event_scope_mismatch"):
        scoped.append(event(1, chat="(FAKE)-chat-b"))

    unknown = EventTrace(
        profile_id=event(1).profile_id,
        conversation_id=event(1).conversation_id,
        turn_id=event(1).turn_id,
    )
    unsupported = event(1)
    object.__setattr__(unsupported, "kind", "(FAKE)-future-event")
    with pytest.raises(EventTraceViolation, match="unsupported_event_kind"):
        unknown.append(unsupported)

    invalid_timestamp = EventTrace(
        profile_id=event(1).profile_id,
        conversation_id=event(1).conversation_id,
        turn_id=event(1).turn_id,
    )
    malformed = event(1)
    object.__setattr__(malformed, "timestamp", "(FAKE)-not-a-timestamp")
    with pytest.raises(EventTraceViolation, match="event_timestamp_invalid"):
        invalid_timestamp.append(malformed)


def test_event_trace_requires_one_terminal_and_isolates_source_ids():
    provider = DeterministicFakeProvider()
    first_binding = FakeBinding(
        profile_id="jprof_11111111111111111111111111111111",
        chat_id="(FAKE)-chat-a",
        turn_id="(FAKE)-turn-a",
        agent_id="(FAKE)-agent-a",
    )
    second_binding = FakeBinding(
        profile_id="jprof_22222222222222222222222222222222",
        chat_id="(FAKE)-chat-b",
        turn_id="(FAKE)-turn-b",
        agent_id="(FAKE)-agent-b",
    )
    first = provider.complete_turn(first_binding)
    second = provider.complete_turn(second_binding)
    first_expected = TraceExpectation(
        profile_id=first_binding.profile_id,
        conversation_id=first_binding.chat_id,
        turn_id=first_binding.turn_id,
        agent_id=first_binding.agent_id,
        session_id=provider.session_id(first_binding),
        payload_canary=first_binding.profile_id,
        forbidden_payload_canaries=(second_binding.profile_id,),
    )
    second_expected = TraceExpectation(
        profile_id=second_binding.profile_id,
        conversation_id=second_binding.chat_id,
        turn_id=second_binding.turn_id,
        agent_id=second_binding.agent_id,
        session_id=provider.session_id(second_binding),
        payload_canary=second_binding.profile_id,
        forbidden_payload_canaries=(first_binding.profile_id,),
    )
    assert_trace_isolation(((first, first_expected), (second, second_expected)))
    reused_session = TraceExpectation(
        profile_id=second_expected.profile_id,
        conversation_id=second_expected.conversation_id,
        turn_id=second_expected.turn_id,
        agent_id=second_expected.agent_id,
        session_id=first_expected.session_id,
        payload_canary=second_expected.payload_canary,
        forbidden_payload_canaries=second_expected.forbidden_payload_canaries,
    )
    reused_session_trace = EventTrace(
        profile_id=second_binding.profile_id,
        conversation_id=second_binding.chat_id,
        turn_id=second_binding.turn_id,
    )
    for event in second.events:
        payload = dict(event.payload)
        if event.kind == "turn_started":
            payload["session_id"] = first_expected.session_id
        reused_session_trace.append(
            NormalizedEvent(
                sequence=event.sequence,
                source_event_id=f"{event.source_event_id}-shared-session",
                timestamp=event.timestamp,
                profile_id=event.profile_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                kind=event.kind,
                payload=payload,
            )
        )
    with pytest.raises(EventTraceViolation, match="cross_trace_session_reuse"):
        assert_trace_isolation(((first, first_expected), (reused_session_trace, reused_session)))
    with pytest.raises(EventTraceViolation, match="cross_trace_binding_mismatch"):
        assert_trace_isolation(((first, second_expected), (second, first_expected)))
    leaked = provider.complete_turn(first_binding, text=f"leaked:{second_binding.profile_id}")
    with pytest.raises(EventTraceViolation, match="cross_trace_payload"):
        assert_trace_isolation(((leaked, first_expected),))
    with pytest.raises(EventTraceViolation, match="event_after_terminal"):
        first.append(
            NormalizedEvent(
                sequence=4,
                source_event_id="(FAKE)-late-event",
                timestamp="2026-08-01T12:00:04Z",
                profile_id=first.events[0].profile_id,
                conversation_id=first.events[0].conversation_id,
                turn_id=first.events[0].turn_id,
                kind="turn_failed",
                payload={},
            )
        )


def test_fake_vault_is_deterministic_and_does_not_retain_raw_credential():
    credential = "(FAKE)-vault-secret-material"
    vault = DeterministicFakeCredentialVault()
    reference = vault.store("jobos-codex-test", credential)
    assert reference == vault.store("jobos-codex-test", credential)
    assert vault.verify(reference, credential, namespace="jobos-codex-test")
    assert credential not in repr(vault.__dict__)
    assert vault.remove(reference)
    assert not vault.verify(reference, credential, namespace="jobos-codex-test")


def _write_canary_archive(path: Path, name: str, value: str) -> None:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(name, value)
        return
    with tarfile.open(path, "w:gz") as archive:
        payload = value.encode()
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_recursive_secret_scanner_detects_text_json_sqlite_journal_and_archives(
    tmp_path: Path,
):
    canaries = phase0_canaries()
    (tmp_path / "capture.txt").write_text(canaries[0].value, encoding="utf-8")
    (tmp_path / "capture.json").write_text(
        json.dumps({"authorization": canaries[1].value}), encoding="utf-8"
    )
    database = tmp_path / "capture.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES (?)", (canaries[2].value,))
    (tmp_path / "journal-evidence.sqlite-journal").write_bytes(canaries[0].value.encode())
    (tmp_path / "wal-evidence.sqlite-wal").write_bytes(canaries[2].value.encode())
    _write_canary_archive(tmp_path / "capture.zip", "nested/evidence.json", canaries[1].value)
    _write_canary_archive(tmp_path / "capture.tar.gz", "nested/evidence.txt", canaries[2].value)

    with pytest.raises(SecretCanaryDetected) as caught:
        assert_no_secret_canaries(tmp_path)

    labels = {finding.canary_label for finding in caught.value.findings}
    assert labels == {"device-token", "oauth-token", "device-code"}
    assert all(canary.value not in str(caught.value) for canary in canaries)
    coordinates = {
        (finding.path, finding.container, finding.canary_label)
        for finding in caught.value.findings
    }
    assert ("capture.sqlite", "sqlite:evidence", "device-code") in coordinates
    assert ("journal-evidence.sqlite-journal", "raw", "device-token") in coordinates
    assert ("wal-evidence.sqlite-wal", "raw", "device-code") in coordinates
    assert ("capture.zip!nested/evidence.json", "raw", "oauth-token") in coordinates
    assert ("capture.tar.gz!nested/evidence.txt", "raw", "device-code") in coordinates


def test_secret_scanner_fails_closed_on_oversized_and_malformed_archives(tmp_path: Path):
    canary = phase0_canaries()[1].value
    oversized = tmp_path / "oversized.zip"
    payload = (canary + "x").encode() * (MAX_ARCHIVE_TEST_BYTES // (len(canary) + 1) + 1)
    with zipfile.ZipFile(oversized, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("evidence.log", payload)

    with pytest.raises(SecretScanIncomplete) as oversized_error:
        scan_secret_canaries(oversized)
    assert {issue.reason for issue in oversized_error.value.issues} == {
        "archive_member_too_large"
    }

    oversized_gzip = tmp_path / "oversized.gz"
    with gzip.open(oversized_gzip, "wb") as archive:
        archive.write(b"x" * (MAX_ARCHIVE_TEST_BYTES + 1))
    with pytest.raises(SecretScanIncomplete) as oversized_gzip_error:
        scan_secret_canaries(oversized_gzip)
    assert {issue.reason for issue in oversized_gzip_error.value.issues} == {
        "archive_member_too_large"
    }

    malformed = tmp_path / "malformed.zip"
    malformed.write_bytes(b"PK\x03\x04(FAKE)-truncated")
    with pytest.raises(SecretScanIncomplete) as malformed_error:
        scan_secret_canaries(malformed)
    assert {issue.reason for issue in malformed_error.value.issues} == {"malformed_archive"}

    wrong_magic = tmp_path / "wrong-magic.zip"
    wrong_magic.write_bytes(b"(FAKE)-not-a-zip")
    with pytest.raises(SecretScanIncomplete, match="malformed_archive"):
        scan_secret_canaries(wrong_magic)

    malformed_database = tmp_path / "malformed.sqlite"
    malformed_database.write_bytes(b"(FAKE)-not-a-database")
    with pytest.raises(SecretScanIncomplete, match="database_unreadable"):
        scan_secret_canaries(malformed_database)

    with pytest.raises(SecretScanIncomplete, match="evidence_root_missing"):
        scan_secret_canaries(tmp_path / "missing-evidence")


def test_secret_scanner_rejects_symbolic_links(tmp_path: Path):
    outside = tmp_path.parent / "(FAKE)-outside-secret.txt"
    outside.write_text(phase0_canaries()[0].value, encoding="utf-8")
    linked_file = tmp_path / "linked-evidence.txt"
    linked_file.symlink_to(outside)

    with pytest.raises(SecretScanIncomplete, match="symbolic_link_rejected"):
        scan_secret_canaries(tmp_path)
    with pytest.raises(SecretScanIncomplete, match="symbolic_link_rejected"):
        scan_secret_canaries(linked_file)


def test_secret_scanner_shares_archive_expansion_budget_across_nested_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(secret_scan, "MAX_ARCHIVE_TOTAL_BYTES", 1_000)
    monkeypatch.setattr(secret_scan, "MAX_MEMBER_BYTES", 900)
    nested = tmp_path / "nested-budget.zip"
    with zipfile.ZipFile(nested, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("first.gz", "second.gz"):
            payload = io.BytesIO()
            with gzip.GzipFile(fileobj=payload, mode="wb") as compressed:
                compressed.write(b"x" * 700)
            archive.writestr(name, payload.getvalue())

    with pytest.raises(SecretScanIncomplete) as caught:
        scan_secret_canaries(nested)
    assert "archive_expansion_limit" in {issue.reason for issue in caught.value.issues}


def test_secret_scanner_bounds_top_level_files_and_shared_archive_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"x" * 11)
    monkeypatch.setattr(secret_scan, "MAX_EVIDENCE_FILE_BYTES", 10)
    with pytest.raises(SecretScanIncomplete, match="evidence_file_too_large"):
        scan_secret_canaries(oversized)

    evidence = tmp_path / "collection"
    evidence.mkdir()
    monkeypatch.setattr(secret_scan, "MAX_EVIDENCE_FILE_BYTES", 10_000)
    monkeypatch.setattr(secret_scan, "MAX_EVIDENCE_TOTAL_BYTES", 15)
    (evidence / "first.txt").write_bytes(b"a" * 10)
    (evidence / "second.txt").write_bytes(b"b" * 10)
    with pytest.raises(SecretScanIncomplete, match="evidence_total_limit"):
        scan_secret_canaries(evidence)

    monkeypatch.setattr(secret_scan, "MAX_EVIDENCE_FILES", 1)
    with pytest.raises(SecretScanIncomplete, match="evidence_file_count_limit"):
        scan_secret_canaries(evidence)
    monkeypatch.setattr(secret_scan, "MAX_EVIDENCE_FILES", 10_000)

    archive_collection = tmp_path / "archive-collection"
    archive_collection.mkdir()
    monkeypatch.setattr(secret_scan, "MAX_EVIDENCE_TOTAL_BYTES", 100_000)
    monkeypatch.setattr(secret_scan, "MAX_ARCHIVE_TOTAL_BYTES", 1_000)
    monkeypatch.setattr(secret_scan, "MAX_MEMBER_BYTES", 900)
    for name in ("first.zip", "second.zip"):
        with zipfile.ZipFile(archive_collection / name, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("payload.txt", b"x" * 700)
    with pytest.raises(SecretScanIncomplete, match="archive_expansion_limit"):
        scan_secret_canaries(archive_collection)

    entry_limited = tmp_path / "entry-limited.zip"
    with zipfile.ZipFile(entry_limited, "w") as archive:
        for index in range(3):
            archive.writestr(f"empty-{index}.txt", b"")
    monkeypatch.setattr(secret_scan, "MAX_EVIDENCE_FILES", 3)
    with pytest.raises(SecretScanIncomplete, match="evidence_file_count_limit"):
        scan_secret_canaries(entry_limited)

    concatenated_gzip = tmp_path / "entry-limited.gz"
    concatenated_gzip.write_bytes(b"".join(gzip.compress(b"") for _ in range(3)))
    with pytest.raises(SecretScanIncomplete, match="evidence_file_count_limit"):
        scan_secret_canaries(concatenated_gzip)

    monkeypatch.setattr(secret_scan, "MAX_EVIDENCE_FILES", 10)
    ambiguous_gzip = tmp_path / "concatenated-canary.gz"
    ambiguous_gzip.write_bytes(
        gzip.compress(b"(FAKE)-safe-first-member")
        + gzip.compress(phase0_canaries()[0].value.encode())
    )
    with pytest.raises(SecretScanIncomplete, match="concatenated_gzip_rejected"):
        scan_secret_canaries(ambiguous_gzip)


def test_installed_smoke_rejects_non_loopback_and_never_bootstraps_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assert loopback_base_url({"host": "127.0.0.1", "port": 8766}) == "http://127.0.0.1:8766"
    assert loopback_base_url({"host": "::1", "port": 8766}) == "http://[::1]:8766"
    with pytest.raises(ValueError, match="installed_runtime_not_loopback"):
        loopback_base_url({"host": "203.0.113.10", "port": 8766})

    from jobos_api import local_config

    credential_path = tmp_path / "credentials.json"
    credential_path.write_text(
        json.dumps({"deviceToken": "(FAKE)-device", "mcpToken": "(FAKE)-mcp"}),
        encoding="utf-8",
    )
    credential_path.chmod(0o600)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "deviceId": "(FAKE)-device",
                "credentialStore": {"provider": "file", "path": "credentials.json"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "service").mkdir()
    (tmp_path / "service/runtime.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 8766}), encoding="utf-8"
    )
    monkeypatch.setattr(local_config, "default_data_dir", lambda: tmp_path)

    result = installed_hermes_smoke()
    assert result["result"] == "failed"
    assert result["mutation_performed"] is False
    assert not (tmp_path / "installation-profiles.json").exists()
    assert not (tmp_path / "installation-profiles.lock").exists()


def test_phase0_receipt_fails_closed_for_dirty_checkout():
    empty = hashlib.sha256(b"").hexdigest()
    clean = {
        "staged_diff_sha256": empty,
        "working_diff_sha256": empty,
        "untracked_paths": [],
    }
    assert checkout_is_clean(clean)
    for dirty in (
        {**clean, "staged_diff_sha256": hashlib.sha256(b"staged").hexdigest()},
        {**clean, "working_diff_sha256": hashlib.sha256(b"working").hexdigest()},
        {**clean, "untracked_paths": ["(FAKE)-untracked"]},
    ):
        assert not checkout_is_clean(dirty)


def test_baseline_command_timeout_is_bounded_and_fail_closed(tmp_path: Path):
    child_pid = tmp_path / "child.pid"
    script = tmp_path / "spawn-child.py"
    script.write_text(
        "import pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    result = run_command(
        f"{sys.executable} {script}",
        isolated_environment(),
        timeout_seconds=0.2,
    )
    assert result["exit_code"] == 124
    assert result["result"] == "failed"
    pid = int(child_pid.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        pytest.fail("timed-out baseline command left a descendant process alive")


def test_fault_injector_and_concurrency_coordinator_are_deterministic():
    injector = DeterministicFaultInjector((("after_registry_write", 2),))
    injector.checkpoint("after_registry_write")
    with pytest.raises(InjectedFault, match="after_registry_write"):
        injector.checkpoint("after_registry_write")
    assert injector.count("after_registry_write") == 2

    coordinator = ConcurrencyCoordinator()
    completed: list[str] = []

    def worker() -> None:
        coordinator.arrive_and_wait("both-turns-started")
        completed.append("released")

    thread = threading.Thread(target=worker)
    thread.start()
    coordinator.wait_until_arrived("both-turns-started")
    assert completed == []
    coordinator.release("both-turns-started")
    thread.join(timeout=2)
    assert completed == ["released"]


@pytest.mark.skipif(
    sys.platform != "darwin"
    or shutil.which("lsof") is None
    or shutil.which("sandbox-exec") is None,
    reason="macOS packaged-host proof needs lsof and sandbox-exec",
)
def test_packaged_host_environment_isolated_and_inherited_auth_stripped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "(FAKE)-inherited-api-key")
    monkeypatch.setenv("JOBOS_DEVICE_TOKEN", "(FAKE)-inherited-device-token")
    environment = isolated_host_environment(tmp_path / "environment")
    assert "OPENAI_API_KEY" not in environment
    assert "JOBOS_DEVICE_TOKEN" not in environment
    assert len({environment["HOME"], environment["TMPDIR"], environment["CODEX_HOME"]}) == 3

    script = (
        "import json,os; "
        "import time; "
        "print(json.dumps({k:os.environ[k] for k in ('HOME','TMPDIR','CODEX_HOME')})); "
        "time.sleep(0.1)"
    )
    result = run_packaged_host(
        [sys.executable, "-c", script], root=tmp_path / "runner", timeout=2
    )
    observed = json.loads(result.stdout)
    assert result.returncode == 0
    assert observed == {
        "HOME": str(result.home),
        "TMPDIR": str(result.tmpdir),
        "CODEX_HOME": str(result.codex_home),
    }
    assert result.listeners == ()
    assert result.listener_audit_count >= 2
    assert result.listener_audit_seconds >= MIN_LISTENER_AUDIT_SECONDS


@pytest.mark.skipif(
    sys.platform != "darwin"
    or shutil.which("lsof") is None
    or shutil.which("sandbox-exec") is None,
    reason="macOS packaged-host proof needs lsof and sandbox-exec",
)
def test_packaged_host_captures_output_larger_than_pipe_capacity(tmp_path: Path):
    expected_bytes = 256 * 1024
    script = f"import sys,time; sys.stdout.write('x'*{expected_bytes}); time.sleep(0.1)"
    result = run_packaged_host(
        [sys.executable, "-c", script], root=tmp_path / "large-output", timeout=2
    )
    assert result.returncode == 0
    assert len(result.stdout) == expected_bytes


@pytest.mark.skipif(
    sys.platform != "darwin"
    or shutil.which("lsof") is None
    or shutil.which("sandbox-exec") is None,
    reason="macOS packaged-host proof needs lsof and sandbox-exec",
)
def test_packaged_host_rejects_unbounded_output(tmp_path: Path):
    script = f"import sys,time; sys.stdout.write('x'*{MAX_CAPTURE_BYTES + 1}); time.sleep(0.1)"
    with pytest.raises(PackagedHostError, match="packaged_host_output_limit"):
        run_packaged_host(
            [sys.executable, "-c", script], root=tmp_path / "output-limit", timeout=2
        )


def test_packaged_host_runner_fails_closed_without_network_confinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(packaged_host.shutil, "which", lambda _: None)
    with pytest.raises(PackagedHostError, match="network_confinement_unavailable"):
        run_packaged_host([sys.executable, "-c", "pass"], root=tmp_path, timeout=2)


@pytest.mark.skipif(
    sys.platform != "darwin"
    or shutil.which("lsof") is None
    or shutil.which("sandbox-exec") is None,
    reason="macOS packaged-host network confinement needs lsof and sandbox-exec",
)
def test_packaged_host_runner_continuously_blocks_transient_listener(tmp_path: Path):
    ready = tmp_path / "listener-ready"
    script = (
        "import pathlib,socket,time; "
        "listener=socket.socket(); "
        "status='escaped'; "
        "\ntry: listener.bind(('127.0.0.1',0)); listener.listen()"
        "\nexcept PermissionError: status='blocked'"
        f"\npathlib.Path({str(ready)!r}).write_text(status); "
        "time.sleep(0.1)"
    )
    result = run_packaged_host([sys.executable, "-c", script], root=tmp_path, timeout=2)
    assert ready.read_text(encoding="utf-8") == "blocked"
    assert result.network_isolation == "sandbox-exec-deny-network"
    assert result.listeners == ()


@pytest.mark.skipif(
    sys.platform != "darwin"
    or shutil.which("lsof") is None
    or shutil.which("sandbox-exec") is None,
    reason="macOS packaged-host proof needs lsof and sandbox-exec",
)
def test_packaged_host_runner_fails_distinctly_when_child_cannot_start(tmp_path: Path):
    script = "import sys; sys.exit(7)"
    with pytest.raises(PackagedHostError, match="packaged_host_failed"):
        run_packaged_host([sys.executable, "-c", script], root=tmp_path, timeout=2)


def test_remote_device_fixture_detects_reachable_but_unauthorized_access():
    fixture_receipt = json.loads(
        (FIXTURES / "fixture-receipt.json").read_text(encoding="utf-8")
    )
    remote_fixture_path = FIXTURES / "(FAKE)-remote-devices.json"
    assert hashlib.sha256(remote_fixture_path.read_bytes()).hexdigest() == fixture_receipt[
        "remote_devices"
    ]["sha256"]
    fixtures = load_remote_devices(remote_fixture_path)
    authorized, unauthorized = fixtures
    accepted = authorize_remote_device(
        authorized.headers,
        expected_profile_id=authorized.profile_id,
        fixtures=fixtures,
    )
    assert accepted.device_id == "(FAKE)-authorized-macbook"
    with pytest.raises(RemoteAuthorizationError, match="device_authentication_required"):
        authorize_remote_device(
            unauthorized.headers,
            expected_profile_id=authorized.profile_id,
            fixtures=fixtures,
        )
    with pytest.raises(RemoteAuthorizationError, match="device_authentication_required"):
        authorize_remote_device(
            {**authorized.headers, "X-JobOS-Profile-Id": "jprof_22222222222222222222222222222222"},
            expected_profile_id=authorized.profile_id,
            fixtures=fixtures,
        )
    cross_profile_authorized = RemoteDeviceFixture(
        label="(FAKE) Other Profile MacBook",
        device_id="(FAKE)-other-profile-macbook",
        profile_id="jprof_22222222222222222222222222222222",
        token="(FAKE)-other-profile-authorized-token",
        authorized=True,
    )
    with pytest.raises(RemoteAuthorizationError, match="device_authentication_required"):
        authorize_remote_device(
            {
                **cross_profile_authorized.headers,
                "X-JobOS-Profile-Id": authorized.profile_id,
            },
            expected_profile_id=authorized.profile_id,
            fixtures=(*fixtures, cross_profile_authorized),
        )


def test_machine_readable_receipts_pin_exact_baselines_without_unrun_claims():
    if os.environ.get("JOBOS_PHASE0_RECORDING") == "1":
        pytest.skip("receipt is being regenerated by the Phase 0 runner")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    receipt = json.loads(REDISTRIBUTION.read_text(encoding="utf-8"))
    verification = json.loads(VERIFICATION_RESULTS.read_text(encoding="utf-8"))
    assert baseline["source"]["commit"] == "ee664f0fc59d61d67c2ba567657af93047133049"
    assert baseline["schemas"]["installation_registry"]["version"] == 1
    assert baseline["schemas"]["profile_sqlite"]["version"] == 31
    assert all(item["execution"] != "passed" for item in baseline["reg_01_command_map"])
    assert baseline["claims"] == {
        "production_behavior_changed": False,
        "installed_acceptance_run": False,
        "full_regression_run": False,
        "later_phase_acceptance_claimed": False,
    }
    expected_commands = [item["command"] for item in baseline["reg_01_command_map"]]
    executed_commands = [item["command"] for item in verification["commands"]]
    assert executed_commands == expected_commands
    assert all(
        item["exit_code"] == 0 and item["result"] == "passed"
        for item in verification["commands"]
    )
    assert verification["all_passed"] is True
    assert verification["credentials_recorded"] is False
    assert verification["raw_command_output_recorded"] is False
    empty_digest = hashlib.sha256(b"").hexdigest()
    assert verification["checkout"]["code_snapshot_sha256"] == code_snapshot_sha256()
    assert verification["checkout"]["staged_diff_sha256"] == empty_digest
    assert verification["checkout"]["working_diff_sha256"] == empty_digest
    assert verification["checkout"]["untracked_paths"] == []
    assert verification["installed_hermes_control"] == {
        "active_turn_count": 0,
        "agent_status": "online",
        "all_recovery_ready": True,
        "conversation_count": 2,
        "conversations_status": 200,
        "health_status": 200,
        "installed_app_running": True,
        "mode": "authenticated_read_only",
        "mutation_performed": False,
        "result": "passed",
        "service_status": "ready",
        "state_schema": 31,
        "transport": "local-loopback",
    }
    assert receipt["candidate"]["version"] == "0.144.4"
    assert receipt["candidate"]["source_commit"] == "8c68d4c87dc54d38861f5114e920c3de2efa5876"
    assert receipt["package"]["sha256"] == (
        "70772846e663a1bc7cbc0417de1306344e1516b9420925b4d67efd788dd3c88e"
    )
    assert receipt["app_server_binary"]["sha256"] == (
        "27d324bc906014c77e4e4286edae6b6d093ee60f49bdcf71495e0f57c31dc6fe"
    )
    assert receipt["app_server_binary"]["codesign_strict"]["result"] == "passed"


@pytest.mark.parametrize(
    ("relative_path", "sha256"),
    (
        (
            "contracts/codex_app_server_protocol.schemas.json",
            "5c40798d0ea83e14988a6f73e854f905f35df8b8c41c4ac61afb67f8698a4c4f",
        ),
        (
            "contracts/codex_app_server_protocol.v2.schemas.json",
            "007e12d25541eb0a50bc778dfcff9e6ab88b3124c9425c4e8f79391d3538bec0",
        ),
        (
            "receipts/codex-0.144.4/LICENSE",
            "d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc",
        ),
        (
            "receipts/codex-0.144.4/NOTICE",
            "9d71575ecfd9a843fc1677b0efb08053c6ba9fd686a0de1a6f5382fd3c220915",
        ),
    ),
)
def test_upstream_contract_and_legal_receipt_hashes(relative_path: str, sha256: str):
    path = Path(__file__).parent / relative_path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    if path.suffix == ".json":
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)

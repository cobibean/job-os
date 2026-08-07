import json
import sqlite3
from pathlib import Path

import pytest
from jobos_api.browser_policy import (
    browser_title_contains_credentials,
    safe_browser_url,
    sanitize_browser_title,
)
from jobos_api.state_store import (
    MIGRATIONS,
    SCHEMA_VERSION,
    IncompatibleSchemaError,
    JobOsStateStore,
    Migration,
)

BROWSER_URL_POLICY_FIXTURES = json.loads(
    (Path(__file__).parents[3] / "tests/fixtures/browser-url-policy.json").read_text()
)
BROWSER_TITLE_POLICY_FIXTURES = json.loads(
    (Path(__file__).parents[3] / "tests/fixtures/browser-title-policy.json").read_text()
)


def applied_versions(path):
    with sqlite3.connect(path) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]


@pytest.mark.parametrize(
    "value",
    ["https://[::1", "http://[", "https://example.com:bad", "https://example.com:70000"],
)
def test_browser_url_policy_rejects_parser_and_deferred_property_errors(value):
    assert safe_browser_url(value, allow_blank=False) is False


def test_browser_url_policy_accepts_electron_compatible_ipv6_and_ordinary_hosts():
    assert safe_browser_url("https://[::1]:443/jobs?view=safe", allow_blank=False)
    assert safe_browser_url("https://jobs.example.com:8443/roles/7", allow_blank=False)


@pytest.mark.parametrize(
    ("url", "expected"),
    [(fixture["url"], fixture["api_safe"]) for fixture in BROWSER_URL_POLICY_FIXTURES],
)
def test_browser_url_policy_matches_shared_credential_host_and_port_fixtures(url, expected):
    assert safe_browser_url(url, allow_blank=False) is expected


@pytest.mark.parametrize(
    ("title", "expected", "unsafe"),
    [
        (fixture["title"], fixture["expected"], fixture["unsafe"])
        for fixture in BROWSER_TITLE_POLICY_FIXTURES
    ],
)
def test_browser_title_policy_matches_shared_conservative_redaction_fixtures(
    title, expected, unsafe
):
    assert browser_title_contains_credentials(title) is unsafe
    assert sanitize_browser_title(title) == expected


def metadata_columns(path):
    with sqlite3.connect(path) as connection:
        return {
            row[1] for row in connection.execute("PRAGMA table_info(jobos_metadata)").fetchall()
        }


def test_initialization_applies_every_migration_once(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)

    first = store.initialize()
    second = store.initialize()

    assert first.schema_version == SCHEMA_VERSION == 12
    assert second.schema_version == SCHEMA_VERSION
    assert applied_versions(database) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert metadata_columns(database) == {"key", "value", "updated_at"}


def test_initialization_upgrades_a_behind_database(tmp_path):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")

    result = JobOsStateStore(database).initialize()

    assert result.schema_version == SCHEMA_VERSION
    assert applied_versions(database) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert metadata_columns(database) == {"key", "value", "updated_at"}


def test_document_identity_migration_clears_legacy_docx_approval(tmp_path):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS[:10]:
            JobOsStateStore._apply_migration(connection, migration)
        connection.execute(
            """
            INSERT INTO document_artifacts(
                artifact_id, registry_key, job_id, source_revision,
                artifact_revision, media_type, sha256, render_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "art_legacy_docx_123456",
                "legacy-registry-key",
                "job-legacy",
                "source-1",
                "render-1",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "0" * 64,
                "succeeded",
            ),
        )
        connection.execute(
            """
            INSERT INTO job_document_state(
                job_id, current_artifact_id, last_successful_artifact_id,
                approved_artifact_id
            ) VALUES (?, ?, ?, ?)
            """,
            (
                "job-legacy",
                "art_legacy_docx_123456",
                "art_legacy_docx_123456",
                "art_legacy_docx_123456",
            ),
        )

    JobOsStateStore(database).initialize()

    with sqlite3.connect(database) as connection:
        approved = connection.execute(
            "SELECT approved_artifact_id FROM job_document_state WHERE job_id = ?",
            ("job-legacy",),
        ).fetchone()
        identity = connection.execute(
            "SELECT document_key, document_label, render_sequence "
            "FROM document_artifacts WHERE artifact_id = ?",
            ("art_legacy_docx_123456",),
        ).fetchone()

    assert approved == (None,)
    assert identity == ("resume", "Resume", 1)


@pytest.mark.parametrize("versions", ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], [2]))
def test_initialization_rejects_ahead_or_incompatible_history(tmp_path, versions):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            [(version,) for version in versions],
        )

    with pytest.raises(IncompatibleSchemaError):
        JobOsStateStore(database).initialize()

    assert applied_versions(database) == versions


def test_a_failed_migration_rolls_back_its_schema_and_ledger_entry(tmp_path):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        migration = Migration(
            version=1,
            statements=("CREATE TABLE partial_change (id INTEGER)", "INVALID SQL"),
        )

        with pytest.raises(sqlite3.OperationalError):
            JobOsStateStore._apply_migration(connection, migration)

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "partial_change" not in tables
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (0,)


def test_conversation_message_turn_and_context_are_atomic_and_idempotent(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    store.save_job_selection("job-7", "user")
    context = {
        "selected_job_id": "job-7",
        "workspace": {"selected_preset": "review", "active_artifact_id": "art-safe"},
    }

    first = store.create_conversation_turn(
        text="Review this role",
        context=context,
        idempotency_key="conversation-message-1",
        actor_id="device-a",
    )
    replay = store.create_conversation_turn(
        text="Review this role",
        context=context,
        idempotency_key="conversation-message-1",
        actor_id="device-a",
    )

    assert first == replay
    snapshot = store.conversation_snapshot()
    assert snapshot["latest_event_id"] == 2
    assert [entry["type"] for entry in snapshot["entries"]] == ["user_message", "turn"]
    assert snapshot["entries"][1]["context"] == context
    assert len(store.list_mutation_audit()) == 1


def test_conversation_events_restore_in_monotonic_order_from_fresh_store(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    created = store.create_conversation_turn(
        text="Start",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="conversation-message-2",
        actor_id="device-a",
    )
    store.append_conversation_event(
        turn_id=created["turn_id"],
        event_type="activity",
        state="working",
        summary="Running command",
        detail={"activity_id": "tool-1"},
    )
    store.update_turn_status(created["turn_id"], "completed")

    restored = JobOsStateStore(database).conversation_snapshot()

    ids = [entry["event_id"] for entry in restored["entries"]]
    assert ids == sorted(ids) == [1, 2, 3]
    assert restored["active_turn"] is None
    assert restored["entries"][1]["context"]["selected_job_id"] is None


def test_conversation_persistence_never_stores_raw_secret_fields(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    created = store.create_conversation_turn(
        text="Safe user text",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="conversation-message-3",
        actor_id="device-a",
    )
    store.append_conversation_event(
        turn_id=created["turn_id"],
        event_type="error",
        state="failed",
        summary="Agent connection unavailable",
        detail={"authorization": "Bearer raw-secret", "safe": "retry"},
    )

    database_bytes = database.read_bytes().lower()
    assert b"raw-secret" not in database_bytes
    assert b"authorization" not in database_bytes


def test_conversation_user_text_is_sanitized_before_any_persistence_or_snapshot(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    raw_secret = "sk-live-never-persist-this-value"

    created = store.create_conversation_turn(
        text=f"Draft a normal follow-up; api_key={raw_secret} and keep the prose.",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="conversation-message-secret-text",
        actor_id="device-a",
    )

    record = store.turn_record(str(created["turn_id"]))
    snapshot_json = json.dumps(store.conversation_snapshot())
    assert record["text"] == "Draft a normal follow-up; [redacted] and keep the prose."
    assert raw_secret not in database.read_bytes().decode(errors="ignore")
    assert raw_secret not in snapshot_json
    assert "Draft a normal follow-up" in snapshot_json


@pytest.mark.parametrize("stale_status", ["queued", "running", "waiting"])
def test_startup_recovery_interrupts_stale_active_turn_once_without_deleting_history(
    tmp_path, stale_status
):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    created = store.create_conversation_turn(
        text="Preserve this transcript",
        context={"selected_job_id": "job-7", "workspace": {}},
        idempotency_key=f"stale-{stale_status}-turn",
        actor_id="device-a",
    )
    store.append_conversation_event(
        turn_id=str(created["turn_id"]),
        event_type="activity",
        state="working",
        summary="Existing activity",
        detail={"activity_id": "existing-action"},
    )
    store.update_turn_status(str(created["turn_id"]), stale_status)
    before = store.conversation_snapshot()["entries"]

    assert store.recover_active_conversation_turns() == 1
    assert store.recover_active_conversation_turns() == 0

    after = store.conversation_snapshot()
    assert after["active_turn"] is None
    assert after["entries"][: len(before)] == before
    recovery = [
        entry
        for entry in after["entries"]
        if entry["type"] == "status" and entry["state"] == "interrupted"
    ]
    assert len(recovery) == 1
    assert recovery[0]["turn_id"] == created["turn_id"]
    assert recovery[0]["detail"]["actionable"] is True
    assert recovery[0]["detail"]["retry"] is True


def test_workspace_snapshot_is_atomic_revisioned_and_preserves_job_selection(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    store.save_job_selection("job-7", "user")

    initial = store.workspace_snapshot("device-a")
    saved = store.save_workspace_snapshot(
        "device-a",
        expected_revision=0,
        snapshot={
            **initial.snapshot,
            "selected_preset": "research",
            "selected_job_id": "job-7",
            "active_center_surface": "browser",
        },
        idempotency_key="workspace-save-atomic-1",
        origin="user",
        actor_id="device-a",
    )

    assert saved.revision == 1
    assert store.workspace_snapshot("device-a").snapshot["selected_job_id"] == "job-7"

    with pytest.raises(Exception, match="revision conflict"):
        store.save_workspace_snapshot(
            "device-a",
            expected_revision=0,
            snapshot=initial.snapshot,
            idempotency_key="workspace-save-stale-1",
            origin="user",
            actor_id="device-a",
        )

    assert store.workspace_snapshot("device-a").revision == 1


def test_stale_layout_snapshot_never_rolls_back_newer_job_selection(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    store.save_job_selection("job-1", "user")
    stale_layout = store.workspace_snapshot("device-a")

    store.save_job_selection("job-2", "mcp")
    saved = store.save_workspace_snapshot(
        "device-a",
        expected_revision=stale_layout.revision,
        snapshot={
            **stale_layout.snapshot,
            "selected_preset": "research",
        },
        idempotency_key="workspace-selection-race-1",
        origin="mcp",
        actor_id="device-a",
    )

    assert saved.snapshot["selected_job_id"] == "job-2"
    assert store.job_workspace_state().selected_job_id == "job-2"
    assert store.workspace_snapshot("device-a").snapshot["selected_job_id"] == "job-2"


def test_workspace_snapshot_retry_is_idempotent_and_records_one_safe_audit(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    initial = store.workspace_snapshot("device-a")
    command = {
        **initial.snapshot,
        "selected_preset": "research",
        "active_center_surface": "browser",
    }

    first = store.save_workspace_snapshot(
        "device-a",
        expected_revision=0,
        snapshot=command,
        idempotency_key="workspace-save-1",
        origin="user",
        actor_id="device-a",
    )
    retry = store.save_workspace_snapshot(
        "device-a",
        expected_revision=0,
        snapshot=command,
        idempotency_key="workspace-save-1",
        origin="user",
        actor_id="device-a",
    )
    audit = store.list_mutation_audit()

    assert first == retry
    assert store.workspace_snapshot("device-a").revision == 1
    assert len(audit) == 1
    assert audit[0] == {
        "origin": "user",
        "actor_id": "device-a",
        "target_resource": "workspace/device-a",
        "command_name": "workspace_snapshot.save",
        "outcome": "succeeded",
        "occurred_at": audit[0]["occurred_at"],
        "detail": {
            "revision": 1,
            "selected_preset": "research",
            "active_center_surface": "browser",
            "repaired_presets": [],
        },
    }
    assert "selected_job_id" not in audit[0]["detail"]


def test_corrupt_layout_repairs_only_the_affected_preset(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    store.save_job_selection("job-9", "user")
    initial = store.workspace_snapshot("device-a")
    corrupt = {
        **initial.snapshot,
        "selected_preset": "review",
        "layouts": {
            **initial.snapshot["layouts"],
            "review": {"order": ["unknown"], "widths": {"agent": -1}},
        },
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO workspace_snapshots(device_id, revision, snapshot_json) VALUES (?, ?, ?)",
            ("device-a", 4, json.dumps(corrupt)),
        )

    restored = store.workspace_snapshot("device-a")

    assert restored.revision == 4
    assert restored.repaired_presets == ("review",)
    assert restored.snapshot["layouts"]["review"] == initial.snapshot["layouts"]["review"]
    assert restored.snapshot["layouts"]["research"] == initial.snapshot["layouts"]["research"]
    assert restored.snapshot["selected_job_id"] == "job-9"


def test_agent_focus_defaults_to_centered_chat_and_upgrades_only_the_legacy_stock_layout(
    tmp_path,
):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    initial = store.workspace_snapshot("new-device")

    assert initial.snapshot["layouts"]["agent-focus"]["order"] == [
        "jobs",
        "agent",
        "center",
    ]

    legacy = {
        **initial.snapshot,
        "layouts": {
            **initial.snapshot["layouts"],
            "agent-focus": {
                "order": ["jobs", "center", "agent"],
                "widths": {"jobs": 220, "center": 420, "agent": 650},
                "collapsed": ["center"],
            },
        },
    }
    customized = {
        **legacy,
        "layouts": {
            **legacy["layouts"],
            "agent-focus": {
                **legacy["layouts"]["agent-focus"],
                "order": ["center", "agent", "jobs"],
                "widths": {"jobs": 220, "center": 420, "agent": 651},
                "collapsed": ["jobs"],
            },
        },
    }
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO workspace_snapshots(device_id, revision, snapshot_json) VALUES (?, ?, ?)",
            (
                ("legacy-device", 3, json.dumps(legacy)),
                ("custom-device", 4, json.dumps(customized)),
            ),
        )

    restored_legacy = store.workspace_snapshot("legacy-device")
    restored_custom = store.workspace_snapshot("custom-device")

    assert restored_legacy.snapshot["layouts"]["agent-focus"]["order"] == [
        "jobs",
        "agent",
        "center",
    ]
    assert restored_legacy.snapshot["layouts"]["agent-focus"]["collapsed"] == ["center"]
    assert restored_custom.snapshot["layouts"]["agent-focus"] == customized["layouts"][
        "agent-focus"
    ]


def test_browser_metadata_round_trips_without_credentials_or_session_material(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    initial = store.workspace_snapshot("device-a")
    saved = store.save_workspace_snapshot(
        "device-a",
        expected_revision=0,
        snapshot={
            **initial.snapshot,
            "browser_tabs": [
                {
                    "tab_id": "gmail",
                    "url": "https://mail.google.com/mail/u/0/",
                    "title": "Inbox",
                    "favicon_url": "https://mail.google.com/favicon.ico",
                    "associated_job_id": None,
                },
                {
                    "tab_id": "listing",
                    "url": "https://jobs.example.com/roles/7",
                    "title": "Staff Product Manager",
                    "favicon_url": None,
                    "associated_job_id": "job-7",
                },
            ],
            "active_browser_tab_id": "gmail",
        },
        idempotency_key="browser-tabs-1",
        origin="user",
        actor_id="device-a",
    )

    restored = store.workspace_snapshot("device-a")
    persisted_json = json.dumps(restored.snapshot)

    assert saved.snapshot["active_browser_tab_id"] == "gmail"
    assert restored.snapshot["browser_tabs"] == saved.snapshot["browser_tabs"]
    assert "cookie" not in persisted_json.lower()
    assert "credential" not in persisted_json.lower()
    assert "authorization" not in persisted_json.lower()
    assert "browser_tabs" not in json.dumps(store.list_mutation_audit())


def test_mixed_browser_restore_repairs_entries_and_active_tab_without_resetting_workspace(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    store.save_job_selection("job-9", "user")
    initial = store.workspace_snapshot("device-a")
    corrupt = {
        **initial.snapshot,
        "selected_preset": "research",
        "browser_tabs": [
            {
                "tab_id": "gmail",
                "url": "https://mail.google.com/mail/u/0/?view=inbox",
                "title": "Inbox",
                "favicon_url": None,
                "associated_job_id": None,
            },
            {
                "tab_id": "unsafe-title",
                "url": "https://example.com/account?view=safe",
                "title": (
                    "authorization_code=title-secret PHPSESSID=session-secret SAMLart=saml-secret"
                ),
                "favicon_url": None,
                "associated_job_id": None,
            },
            {"tab_id": "bad", "url": "file:///etc/passwd", "title": "Bad"},
            {"tab_id": "bad-ipv6", "url": "https://[::1", "title": "Bad IPv6"},
            {
                "tab_id": "bad-favicon",
                "url": "https://valid.example.com/",
                "title": "Bad favicon",
                "favicon_url": "http://[",
                "associated_job_id": None,
            },
            {
                "tab_id": "gmail",
                "url": "https://duplicate.example.com/",
                "title": "Duplicate",
                "favicon_url": None,
                "associated_job_id": None,
            },
            {
                "tab_id": "listing",
                "url": "https://jobs.example.com/roles/7?page=2",
                "title": "Listing",
                "favicon_url": None,
                "associated_job_id": "job-9",
            },
        ],
        "active_browser_tab_id": "unsafe-title",
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO workspace_snapshots(device_id, revision, snapshot_json) VALUES (?, ?, ?)",
            ("device-a", 6, json.dumps(corrupt)),
        )

    restored = store.workspace_snapshot("device-a")

    assert restored.revision == 6
    assert restored.repaired_browser is True
    assert restored.browser_repair_reasons == ("protected_title", "dropped_tabs")
    assert [tab["tab_id"] for tab in restored.snapshot["browser_tabs"]] == [
        "gmail",
        "unsafe-title",
        "listing",
    ]
    assert restored.snapshot["active_browser_tab_id"] == "unsafe-title"
    assert restored.snapshot["browser_tabs"][1]["title"] == "Protected page"
    assert "title-secret" not in json.dumps(restored.snapshot)
    assert restored.snapshot["selected_preset"] == "research"
    assert restored.snapshot["layouts"] == initial.snapshot["layouts"]
    assert restored.snapshot["selected_job_id"] == "job-9"


@pytest.mark.parametrize(
    ("tabs", "active_tab_id", "expected_reasons", "expected_active"),
    [
        (
            [
                {
                    "tab_id": "safe",
                    "url": "https://example.com/",
                    "title": "%ZZAWS%5FSECRET%5FACCESS%5FKEY%3Dexample-value",
                }
            ],
            "safe",
            ("protected_title",),
            "safe",
        ),
        (
            [
                {"tab_id": "safe", "url": "https://example.com/", "title": "Safe"},
                {"tab_id": "bad", "url": "file:///bad", "title": "Bad"},
            ],
            "safe",
            ("dropped_tabs",),
            "safe",
        ),
        (
            [{"tab_id": "safe", "url": "https://example.com/", "title": "Safe"}],
            "missing",
            ("reselected_active_tab",),
            "safe",
        ),
        (
            [
                {
                    "tab_id": "safe",
                    "url": "https://example.com/",
                    "title": "PRIVATE KEY: example-value",
                },
                {"tab_id": "bad", "url": "file:///bad", "title": "Bad"},
            ],
            "bad",
            ("protected_title", "dropped_tabs", "reselected_active_tab"),
            "safe",
        ),
    ],
)
def test_browser_repair_reports_exact_bounded_reasons(
    tmp_path, tabs, active_tab_id, expected_reasons, expected_active
):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    initial = store.workspace_snapshot("device-a")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO workspace_snapshots(device_id, revision, snapshot_json) VALUES (?, ?, ?)",
            (
                "device-a",
                1,
                json.dumps(
                    {
                        **initial.snapshot,
                        "browser_tabs": tabs,
                        "active_browser_tab_id": active_tab_id,
                    }
                ),
            ),
        )

    restored = store.workspace_snapshot("device-a")

    assert restored.browser_repair_reasons == expected_reasons
    assert restored.snapshot["active_browser_tab_id"] == expected_active
    assert len(restored.snapshot["browser_tabs"]) == 1
    if "protected_title" in expected_reasons:
        assert restored.snapshot["browser_tabs"][0]["title"] == "Protected page"


def test_browser_repair_keeps_first_fifty_valid_tabs_in_stable_order(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    initial = store.workspace_snapshot("device-a")
    valid_tabs = [
        {
            "tab_id": f"tab-{index}",
            "url": f"https://example.com/{index}?view=safe",
            "title": f"Tab {index}",
            "favicon_url": None,
            "associated_job_id": None,
        }
        for index in range(52)
    ]
    tabs = [
        {"tab_id": "invalid", "url": "file:///etc/passwd", "title": "Invalid"},
        valid_tabs[0],
        {**valid_tabs[0], "url": "https://duplicate.example.com/"},
        {"tab_id": "malformed", "url": "https://[::1", "title": "Malformed"},
        {
            "tab_id": "bad-favicon",
            "url": "https://valid.example.com/",
            "title": "Bad favicon",
            "favicon_url": "http://[",
            "associated_job_id": None,
        },
        *valid_tabs[1:],
    ]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO workspace_snapshots(device_id, revision, snapshot_json) VALUES (?, ?, ?)",
            (
                "device-a",
                3,
                json.dumps(
                    {
                        **initial.snapshot,
                        "browser_tabs": tabs,
                        "active_browser_tab_id": "tab-49",
                    }
                ),
            ),
        )

    restored = store.workspace_snapshot("device-a")

    assert restored.repaired_browser is True
    assert [tab["tab_id"] for tab in restored.snapshot["browser_tabs"]] == [
        f"tab-{index}" for index in range(50)
    ]
    assert restored.snapshot["active_browser_tab_id"] == "tab-49"

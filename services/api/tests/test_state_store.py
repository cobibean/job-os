import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from jobos_api.browser_policy import (
    browser_title_contains_credentials,
    safe_browser_url,
    sanitize_browser_title,
)
from jobos_api.documents import VerifiedArtifact
from jobos_api.state_store import (
    MIGRATIONS,
    SCHEMA_VERSION,
    ConversationBusy,
    ConversationLimit,
    ConversationNotFound,
    IncompatibleSchemaError,
    JobOsStateStore,
    Migration,
    canonical_workspace_snapshot,
    normalize_workspace_snapshot,
)

BROWSER_URL_POLICY_FIXTURES = json.loads(
    (Path(__file__).parents[3] / "tests/fixtures/browser-url-policy.json").read_text()
)
BROWSER_TITLE_POLICY_FIXTURES = json.loads(
    (Path(__file__).parents[3] / "tests/fixtures/browser-title-policy.json").read_text()
)


def test_browse_workspace_defaults_and_malformed_fields_repair_independently():
    old_snapshot = canonical_workspace_snapshot("job-1")
    for key in list(old_snapshot):
        if key.startswith("browse_") or key == "active_top_level_workspace":
            old_snapshot.pop(key)
    old_snapshot["layouts"]["research"]["widths"]["jobs"] = 333
    old_snapshot["browser_tabs"] = [
        {
            "tab_id": "listing",
            "url": "https://example.com/job",
            "title": "Listing",
            "favicon_url": None,
            "associated_job_id": "job-1",
        }
    ]
    old_snapshot["active_browser_tab_id"] = "listing"
    restored, repaired = normalize_workspace_snapshot(old_snapshot, "job-1")

    assert repaired == ()
    assert restored["active_top_level_workspace"] == "review"
    assert restored["browse_mode"] == "list"
    assert restored["browse_focus_job_id"] is None
    assert restored["browse_query"] == ""
    assert restored["browse_status_group"] == ""
    assert restored["browse_sort_mode"] == "manual"
    assert restored["browse_rail_width"] == 292
    assert restored["layouts"]["research"]["widths"]["jobs"] == 333
    assert restored["active_browser_tab_id"] == "listing"

    malformed = {**restored}
    malformed.update(
        active_top_level_workspace="unknown",
        browse_mode="cards",
        browse_focus_job_id={"bad": True},
        browse_query=["bad"],
        browse_status_group="Imaginary",
        browse_sort_mode="salary",
        browse_rail_width=900,
    )
    repaired_browse, repaired_presets = normalize_workspace_snapshot(malformed, "job-1")
    assert repaired_presets == ()
    assert repaired_browse["active_top_level_workspace"] == "review"
    assert repaired_browse["browse_mode"] == "list"
    assert repaired_browse["browse_focus_job_id"] is None
    assert repaired_browse["browse_query"] == ""
    assert repaired_browse["browse_status_group"] == ""
    assert repaired_browse["browse_sort_mode"] == "manual"
    assert repaired_browse["browse_rail_width"] == 292
    assert repaired_browse["layouts"] == restored["layouts"]
    assert repaired_browse["browser_tabs"] == restored["browser_tabs"]


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

    assert first.schema_version == SCHEMA_VERSION == 20
    assert second.schema_version == SCHEMA_VERSION
    assert applied_versions(database) == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
    ]
    assert metadata_columns(database) == {"key", "value", "updated_at"}


def test_concurrent_initialization_applies_pending_migrations_once(tmp_path):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS[:14]:
            JobOsStateStore._apply_migration(connection, migration)

    ready = Barrier(2)

    def initialize() -> int:
        ready.wait(timeout=5)
        return JobOsStateStore(database).initialize().schema_version

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: initialize(), range(2)))

    assert results == [SCHEMA_VERSION, SCHEMA_VERSION]
    assert applied_versions(database) == list(range(1, SCHEMA_VERSION + 1))


def test_initialization_upgrades_a_behind_database(tmp_path):
    database = tmp_path / "jobos.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")

    result = JobOsStateStore(database).initialize()

    assert result.schema_version == SCHEMA_VERSION
    assert applied_versions(database) == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
    ]
    assert metadata_columns(database) == {"key", "value", "updated_at"}


def test_migration_15_reconciles_dirty_v14_publications_deterministically(tmp_path):
    database = tmp_path / "jobos.db"
    document_id = "edoc_abcdefghijklmnopqrstuvwx"
    docx_media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS[:14]:
            JobOsStateStore._apply_migration(connection, migration)
        connection.execute(
            """
            INSERT INTO editable_documents(
                document_id, job_id, document_key, document_label, schema_version,
                revision, content_json, settings_json, comments_json, import_report_json,
                published_revision
            ) VALUES (?, 'job-dirty', 'resume', 'Resume', 1, 3, '{}', '{}', '[]', '{}', 3)
            """,
            (document_id,),
        )
        rows = [
            ("art_old_docx_123456", "old-docx", 10, "source-shared", docx_media, "1" * 64),
            ("art_old_pdf_1234567", "old-pdf", 11, "source-shared", "application/pdf", "2" * 64),
            ("art_new_docx_123456", "new-docx", 20, "source-shared", docx_media, "3" * 64),
            ("art_new_pdf_1234567", "new-pdf", 21, "source-shared", "application/pdf", "4" * 64),
        ]
        connection.executemany(
            """
            INSERT INTO document_artifacts(
                artifact_id, registry_key, job_id, document_key, document_label,
                render_sequence, source_revision, artifact_revision, media_type,
                sha256, render_status, canonical_path, filename,
                editable_document_id, editable_document_revision
            ) VALUES (?, ?, 'job-dirty', 'resume', 'Resume', ?, ?, ?, ?, ?, 'succeeded',
                '/synthetic/path', 'synthetic', ?, 3)
            """,
            [
                (
                    artifact_id,
                    registry_key,
                    sequence,
                    source_revision,
                    digest,
                    media_type,
                    digest,
                    document_id,
                )
                for artifact_id, registry_key, sequence, source_revision, media_type, digest in rows
            ],
        )
        connection.execute(
            """
            INSERT INTO job_document_state(
                job_id, current_artifact_id, last_successful_artifact_id,
                approved_artifact_id, approved_at
            ) VALUES ('job-dirty', 'art_new_pdf_1234567', 'art_new_pdf_1234567',
                'art_old_pdf_1234567', CURRENT_TIMESTAMP)
            """
        )

    health = JobOsStateStore(database).initialize()

    with sqlite3.connect(database) as connection:
        associated = connection.execute(
            """
            SELECT artifact_id, media_type, source_revision
            FROM document_artifacts
            WHERE editable_document_id = ? AND editable_document_revision = 3
            ORDER BY media_type
            """,
            (document_id,),
        ).fetchall()
        detached = connection.execute(
            """
            SELECT artifact_id
            FROM document_artifacts
            WHERE artifact_id LIKE 'art_new_%'
                AND editable_document_id IS NULL
                AND editable_document_revision IS NULL
            ORDER BY artifact_id
            """
        ).fetchall()
        state = connection.execute(
            """
            SELECT current_artifact_id, last_successful_artifact_id, approved_artifact_id
            FROM job_document_state WHERE job_id = 'job-dirty'
            """
        ).fetchone()
        published = connection.execute(
            "SELECT published_revision FROM editable_documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    assert health.schema_version == 20
    assert associated == [
        ("art_old_pdf_1234567", "application/pdf", "source-shared"),
        ("art_old_docx_123456", docx_media, "source-shared"),
    ]
    assert detached == [("art_new_docx_123456",), ("art_new_pdf_1234567",)]
    assert state == (
        "art_old_pdf_1234567",
        "art_old_pdf_1234567",
        "art_old_pdf_1234567",
    )
    assert published == (3,)


def test_migration_15_preserves_valid_owner_and_clears_mixed_wrong_owner_pointers(
    tmp_path,
):
    database = tmp_path / "jobos.db"
    document_id = "edoc_abcdefghijklmnopqrstuvwx"
    docx_media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS[:14]:
            JobOsStateStore._apply_migration(connection, migration)
        connection.execute(
            """
            INSERT INTO editable_documents(
                document_id, job_id, document_key, document_label, schema_version,
                revision, content_json, settings_json, comments_json, import_report_json,
                published_revision
            ) VALUES (?, 'job-owner', 'resume', 'Resume', 1, 3, '{}', '{}', '[]', '{}', 3)
            """,
            (document_id,),
        )
        rows = [
            ("art_owner_docx_12345", "job-owner", 10, docx_media, "1" * 64),
            ("art_owner_pdf_123456", "job-owner", 11, "application/pdf", "2" * 64),
            ("art_wrong_docx_12345", "job-wrong", 20, docx_media, "3" * 64),
            ("art_wrong_pdf_123456", "job-wrong", 21, "application/pdf", "4" * 64),
        ]
        connection.executemany(
            """
            INSERT INTO document_artifacts(
                artifact_id, registry_key, job_id, document_key, document_label,
                render_sequence, source_revision, artifact_revision, media_type,
                sha256, render_status, canonical_path, filename,
                editable_document_id, editable_document_revision
            ) VALUES (?, 'legacy-' || ?, ?, 'resume', 'Resume', ?, 'source-shared', ?, ?, ?,
                'succeeded', '/synthetic/path', 'synthetic', ?, 3)
            """,
            [
                (
                    artifact_id,
                    artifact_id,
                    job_id,
                    sequence,
                    digest,
                    media_type,
                    digest,
                    document_id,
                )
                for artifact_id, job_id, sequence, media_type, digest in rows
            ],
        )
        connection.executemany(
            """
            INSERT INTO job_document_state(
                job_id, current_artifact_id, last_successful_artifact_id,
                approved_artifact_id, approved_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                (
                    "job-owner",
                    "art_owner_pdf_123456",
                    "art_owner_pdf_123456",
                    "art_owner_pdf_123456",
                ),
                (
                    "job-wrong",
                    "art_wrong_pdf_123456",
                    "art_owner_docx_12345",
                    "art_owner_pdf_123456",
                ),
            ],
        )

    health = JobOsStateStore(database).initialize()

    with sqlite3.connect(database) as connection:
        owner_state = connection.execute(
            """
            SELECT current_artifact_id, last_successful_artifact_id, approved_artifact_id
            FROM job_document_state WHERE job_id = 'job-owner'
            """
        ).fetchone()
        wrong_state = connection.execute(
            """
            SELECT current_artifact_id, last_successful_artifact_id,
                approved_artifact_id, approved_at
            FROM job_document_state WHERE job_id = 'job-wrong'
            """
        ).fetchone()
        associated = connection.execute(
            """
            SELECT artifact_id FROM document_artifacts
            WHERE editable_document_id = ?
            ORDER BY artifact_id
            """,
            (document_id,),
        ).fetchall()

    assert health.schema_version == 20
    assert owner_state == (
        "art_owner_pdf_123456",
        "art_owner_pdf_123456",
        "art_owner_pdf_123456",
    )
    assert wrong_state == (None, None, None, None)
    assert associated == [("art_owner_docx_12345",), ("art_owner_pdf_123456",)]


@pytest.mark.parametrize(
    ("legacy_rows", "artifact_job_id", "artifact_document_keys"),
    [
        (
            [("art_only_docx_123456", 10, "source-a", "docx")],
            "job-malformed",
            ("resume",),
        ),
        (
            [("art_only_pdf_1234567", 10, "source-a", "pdf")],
            "job-malformed",
            ("resume",),
        ),
        (
            [
                ("art_mismatch_docx_1", 10, "source-a", "docx"),
                ("art_mismatch_pdf_12", 11, "source-b", "pdf"),
            ],
            "job-malformed",
            ("resume", "resume"),
        ),
        (
            [
                ("art_gap_docx_1234567", 10, "source-a", "docx"),
                ("art_gap_pdf_12345678", 12, "source-a", "pdf"),
            ],
            "job-malformed",
            ("resume", "resume"),
        ),
        (
            [
                ("art_wrong_job_docx_1", 10, "source-a", "docx"),
                ("art_wrong_job_pdf_12", 11, "source-a", "pdf"),
            ],
            "job-wrong-owner",
            ("resume", "resume"),
        ),
        (
            [
                ("art_wrong_key_docx_1", 10, "source-a", "docx"),
                ("art_wrong_key_pdf_12", 11, "source-a", "pdf"),
            ],
            "job-malformed",
            ("resume", "cover_letter"),
        ),
    ],
    ids=(
        "docx-only",
        "pdf-only",
        "mismatched-source",
        "nonadjacent-sequences",
        "owner-job-mismatch",
        "owner-document-key-mismatch",
    ),
)
def test_migration_15_detaches_every_malformed_v14_publication_and_allows_republish(
    tmp_path, legacy_rows, artifact_job_id, artifact_document_keys
):
    database = tmp_path / "jobos.db"
    document_id = "edoc_abcdefghijklmnopqrstuvwx"
    docx_media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    media_types = {"docx": docx_media, "pdf": "application/pdf"}
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS[:14]:
            JobOsStateStore._apply_migration(connection, migration)
        connection.execute(
            """
            INSERT INTO editable_documents(
                document_id, job_id, document_key, document_label, schema_version,
                revision, content_json, settings_json, comments_json, import_report_json,
                published_revision
            ) VALUES (?, 'job-malformed', 'resume', 'Resume', 1, 3, '{}', '{}', '[]', '{}', 3)
            """,
            (document_id,),
        )
        for index, ((artifact_id, sequence, source_revision, media), document_key) in enumerate(
            zip(legacy_rows, artifact_document_keys, strict=True)
        ):
            digest = str(index + 1) * 64
            connection.execute(
                """
                INSERT INTO document_artifacts(
                    artifact_id, registry_key, job_id, document_key, document_label,
                    render_sequence, source_revision, artifact_revision, media_type,
                    sha256, render_status, canonical_path, filename,
                    editable_document_id, editable_document_revision
                ) VALUES (?, ?, ?, ?, 'Resume', ?, ?, ?, ?, ?,
                    'succeeded', '/synthetic/path', 'synthetic', ?, 3)
                """,
                (
                    artifact_id,
                    f"legacy-{index}",
                    artifact_job_id,
                    document_key,
                    sequence,
                    source_revision,
                    digest,
                    media_types[media],
                    digest,
                    document_id,
                ),
            )
        pointed_artifact = legacy_rows[0][0]
        connection.execute(
            """
            INSERT INTO job_document_state(
                job_id, current_artifact_id, last_successful_artifact_id,
                approved_artifact_id, approved_at
            ) VALUES ('job-malformed', ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (pointed_artifact, pointed_artifact, pointed_artifact),
        )

    store = JobOsStateStore(database)
    health = store.initialize()

    with sqlite3.connect(database) as connection:
        detached = connection.execute(
            """
            SELECT artifact_id
            FROM document_artifacts
            WHERE registry_key LIKE 'legacy-%'
                AND editable_document_id IS NULL
                AND editable_document_revision IS NULL
            ORDER BY artifact_id
            """
        ).fetchall()
        state = connection.execute(
            """
            SELECT current_artifact_id, last_successful_artifact_id,
                approved_artifact_id, approved_at
            FROM job_document_state WHERE job_id = 'job-malformed'
            """
        ).fetchone()
        published = connection.execute(
            "SELECT published_revision FROM editable_documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        republished = store.register_editable_publication_pair(
            "job-malformed",
            [
                VerifiedArtifact(
                    job_id="job-malformed",
                    document_key="resume",
                    document_label="Resume",
                    source_revision="source-republish",
                    artifact_revision="republish-docx",
                    media_type=docx_media,
                    sha256="a" * 64,
                    render_status="succeeded",
                    render_sequence=30,
                    canonical_path="/synthetic/republish.docx",
                    filename="republish.docx",
                    failure_message=None,
                ),
                VerifiedArtifact(
                    job_id="job-malformed",
                    document_key="resume",
                    document_label="Resume",
                    source_revision="source-republish",
                    artifact_revision="republish-pdf",
                    media_type="application/pdf",
                    sha256="b" * 64,
                    render_status="succeeded",
                    render_sequence=31,
                    canonical_path="/synthetic/republish.pdf",
                    filename="republish.pdf",
                    failure_message=None,
                ),
            ],
            editable_document_id=document_id,
            editable_document_revision=3,
            connection=connection,
        )

    assert health.schema_version == 20
    assert detached == sorted((row[0],) for row in legacy_rows)
    assert state == (None, None, None, None)
    assert published == (None,)
    assert republished is True
    assert len(store.editable_publication_artifacts(document_id, 3)) == 2


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


@pytest.mark.parametrize(
    "versions",
    (
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
        [2],
    ),
)
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

    assert first["created"] is True
    assert replay["created"] is False
    assert {key: value for key, value in first.items() if key != "created"} == {
        key: value for key, value in replay.items() if key != "created"
    }
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


def test_terminal_agent_continuation_does_not_replace_active_user_turn(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    active = store.create_conversation_turn(
        text="A newer follow-up",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="active-user-before-continuation",
        actor_id="device-a",
    )
    conversation = store.conversation_store(store.first_active_conversation_id())

    assert conversation.record_agent_continuation(
        turn_id="turn_agent_continuation_1234",
        status="completed",
        event_type="assistant_message",
        summary="Background work finished",
        detail={
            "type": "message.complete",
            "text": "Background work finished",
            "agent_continuation": True,
        },
    )
    snapshot = conversation.conversation_snapshot()

    entries = snapshot["entries"]
    assert isinstance(entries, list)
    continuation_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry["turn_id"] == "turn_agent_continuation_1234"
    ]
    assert [entry["type"] for entry in continuation_entries] == [
        "turn",
        "assistant_message",
    ]
    assert not any(entry["type"] == "user_message" for entry in continuation_entries)
    active_turn = snapshot["active_turn"]
    assert isinstance(active_turn, dict)
    assert active_turn["turn_id"] == active["turn_id"]


def test_completed_assistant_text_uses_transcript_bound_while_summary_stays_concise(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    created = store.create_conversation_turn(
        text="Write a detailed response",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="conversation-long-assistant-text",
        actor_id="device-a",
    )
    text = ("x" * 1_500) + " api_key=raw-secret tail"
    safe_text = ("x" * 1_500) + " [redacted] tail"

    assert store.settle_active_turn(
        str(created["turn_id"]),
        "completed",
        event_type="assistant_message",
        summary=text,
        detail={"type": "message.complete", "text": text},
    )

    entries = store.conversation_snapshot()["entries"]
    assert isinstance(entries, list)
    completed = entries[-1]
    assert isinstance(completed, dict)
    detail = completed["detail"]
    assert isinstance(detail, dict)
    assert completed["summary"] == text[:500] + "…"
    assert detail["text"] == safe_text
    assert b"raw-secret" not in database.read_bytes()


def test_completed_assistant_text_has_a_hard_transcript_bound(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    created = store.create_conversation_turn(
        text="Write a huge response",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="conversation-huge-assistant-text",
        actor_id="device-a",
    )
    text = "x" * 100_050

    assert store.settle_active_turn(
        str(created["turn_id"]),
        "completed",
        event_type="assistant_message",
        summary=text,
        detail={"type": "message.complete", "text": text},
    )

    entries = store.conversation_snapshot()["entries"]
    assert isinstance(entries, list)
    completed = entries[-1]
    assert isinstance(completed, dict)
    detail = completed["detail"]
    assert isinstance(detail, dict)
    assert detail["text"] == text[:100_000] + "…"


def test_streaming_assistant_delta_keeps_generic_event_detail_bound(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    text = "x" * 2_000

    store.append_conversation_event(
        turn_id=None,
        event_type="assistant_message",
        state="working",
        summary=text,
        detail={"type": "message.delta", "text": text},
    )

    entries = store.conversation_snapshot()["entries"]
    assert isinstance(entries, list)
    detail = entries[-1]["detail"]
    assert isinstance(detail, dict)
    assert detail["text"] == text[:1_000] + "…"


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


def test_workspace_snapshot_is_atomic_revisioned_and_strips_conversation_projection(tmp_path):
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
    assert store.workspace_snapshot("device-a").snapshot["selected_job_id"] is None

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


def test_layout_snapshot_is_independent_from_legacy_job_selection(tmp_path):
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

    assert saved.snapshot["selected_job_id"] is None
    assert store.job_workspace_state().selected_job_id == "job-2"
    assert store.workspace_snapshot("device-a").snapshot["selected_job_id"] is None


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
    assert restored.snapshot["selected_job_id"] is None


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
    assert (
        restored_custom.snapshot["layouts"]["agent-focus"] == customized["layouts"]["agent-focus"]
    )


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
    assert restored.snapshot["selected_job_id"] is None


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


def test_migration_16_preserves_legacy_conversation_and_recovery_state(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS[:15]:
            JobOsStateStore._apply_migration(connection, migration)
        connection.execute("""
            UPDATE conversations SET conversation_id='conv_legacy',
                stored_session_id='stored-legacy', isolated_turn_id='turn_legacy',
                isolated_previous_session_id='stored-ordinary',
                isolated_agent_session_id='stored-isolated',
                ignored_agent_session_id='stored-ignored' WHERE singleton_id=1
            """)
        connection.execute("""
            INSERT INTO conversation_turns(turn_id,message_id,text,context_json,status)
            VALUES ('turn_legacy','msg_legacy','Legacy prompt','{}','waiting')
            """)
        connection.execute("""
            INSERT INTO conversation_events(
                event_id,turn_id,event_type,state,summary,detail_json,source_event_id
            ) VALUES (41,'turn_legacy','status','waiting','Legacy wait','{}','legacy-event')
            """)
        connection.execute("""
            INSERT INTO jobos_metadata(key,value,updated_at)
            VALUES ('agent_recovery_turn_id','turn_legacy',CURRENT_TIMESTAMP)
            """)
    store = JobOsStateStore(database)
    store.initialize()
    scoped = store.conversation_store("conv_legacy")
    snapshot = scoped.snapshot()
    assert (snapshot["conversation_id"], snapshot["position"], snapshot["title"]) == (
        "conv_legacy",
        1,
        "Session 1",
    )
    assert snapshot["entries"][0]["event_id"] == 41
    assert scoped.turn_record("turn_legacy")["text"] == "Legacy prompt"
    assert scoped.stored_session_id() == "stored-legacy"
    assert scoped.recovery_turn_id() == "turn_legacy"
    assert scoped.restore_isolated_agent_session("turn_legacy") is True
    assert scoped.stored_session_id() == "stored-ordinary"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("retain_earlier_event", [False, True])
def test_migration_16_preserves_deleted_autoincrement_high_water_mark(
    tmp_path, retain_earlier_event
):
    database = tmp_path / f"legacy-high-water-{retain_earlier_event}.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS[:15]:
            JobOsStateStore._apply_migration(connection, migration)
        connection.execute(
            "UPDATE conversations SET conversation_id = 'conv_legacy' WHERE singleton_id = 1"
        )
        connection.execute(
            """
            INSERT INTO conversation_turns(turn_id,message_id,text,context_json,status)
            VALUES ('turn_legacy','msg_legacy','Legacy prompt','{}','running')
            """
        )
        if retain_earlier_event:
            connection.execute(
                """
                INSERT INTO conversation_events(
                    event_id,turn_id,event_type,state,summary,detail_json
                ) VALUES (41,'turn_legacy','status','working','Earlier event','{}')
                """
            )
        connection.execute(
            """
            INSERT INTO conversation_events(
                event_id,turn_id,event_type,state,summary,detail_json
            ) VALUES (100,'turn_legacy','status','working','Deleted tail','{}')
            """
        )
        connection.execute("DELETE FROM conversation_events WHERE event_id = 100")
        assert (
            connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'conversation_events'"
            ).fetchone()[0]
            == 100
        )

    store = JobOsStateStore(database)
    store.initialize()
    event = store.conversation_store("conv_legacy").append_event(
        turn_id="turn_legacy",
        event_type="status",
        state="working",
        summary="First post-migration event",
    )

    assert event is not None and event > 100
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_conversation_foreign_keys_enforce_same_conversation_scope(tmp_path):
    database = tmp_path / "scoped-foreign-keys.db"
    store = JobOsStateStore(database)
    store.initialize()
    first_id = store.first_active_conversation_id()
    second_id = str(store.create_conversation(actor_id="device-a")["conversation_id"])
    first_turn = store.conversation_store(first_id).create_turn(
        text="First", context={}, idempotency_key="fk-first-turn-1", actor_id="device-a"
    )

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO conversation_events(
                    conversation_id,turn_id,event_type,state,summary,detail_json
                ) VALUES (?,?,'status','working','Wrong scope','{}')
                """,
                (second_id, first_turn["turn_id"]),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO conversation_turns(
                    turn_id,conversation_id,message_id,source_turn_id,text,context_json,status
                ) VALUES ('turn_cross_scope',?,'msg_cross_scope',?,'Retry','{}','running')
                """,
                (second_id, first_turn["turn_id"]),
            )


def test_migration_statement_failure_rolls_back_schema_and_ledger(tmp_path):
    database = tmp_path / "migration-rollback.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        broken = Migration(
            version=999,
            statements=(
                "CREATE TABLE migration_should_rollback(value TEXT)",
                "INSERT INTO table_that_does_not_exist(value) VALUES ('fail')",
            ),
        )
        with pytest.raises(sqlite3.OperationalError):
            JobOsStateStore._apply_migration(connection, broken)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'migration_should_rollback'"
            ).fetchone()
            is None
        )
        assert (
            connection.execute("SELECT 1 FROM schema_migrations WHERE version = 999").fetchone()
            is None
        )


def test_conversation_scope_isolates_busy_events_idempotency_and_sessions(tmp_path):
    store = JobOsStateStore(tmp_path / "scoped.db")
    store.initialize()
    first = store.conversation_store(store.first_active_conversation_id())
    second_id = str(store.create_conversation(actor_id="device-a")["conversation_id"])
    second = store.conversation_store(second_id)
    first_turn = first.create_turn(
        text="First", context={}, idempotency_key="same-key-0001", actor_id="device-a"
    )
    second_turn = second.create_turn(
        text="Second", context={}, idempotency_key="same-key-0001", actor_id="device-a"
    )
    assert first_turn["turn_id"] != second_turn["turn_id"]
    with pytest.raises(ConversationBusy):
        first.create_turn(
            text="Blocked", context={}, idempotency_key="another-key-0001", actor_id="device-a"
        )
    first.append_event(
        turn_id=str(first_turn["turn_id"]),
        event_type="activity",
        state="working",
        summary="Only first",
        source_event_id="shared-source",
    )
    second.append_event(
        turn_id=str(second_turn["turn_id"]),
        event_type="activity",
        state="working",
        summary="Only second",
        source_event_id="shared-source",
    )
    assert all(entry["summary"] != "Only second" for entry in first.events_after(0))
    assert all(entry["summary"] != "Only first" for entry in second.events_after(0))
    first.save_stored_session_id("ordinary-first")
    first.begin_isolated_agent_session(str(first_turn["turn_id"]))
    assert first.stored_session_id() is None
    assert second.stored_session_id() is None
    first.restore_isolated_agent_session(str(first_turn["turn_id"]))
    assert first.stored_session_id() == "ordinary-first"


def test_conversation_job_selections_are_independent_and_restore_after_restart(tmp_path):
    database = tmp_path / "conversation-jobs.db"
    store = JobOsStateStore(database)
    store.initialize(owner_device_id="device-a")
    first_id = store.first_active_conversation_id("device-a")
    second_id = str(
        store.create_conversation(actor_id="device-a", selected_job_id="job-b")[
            "conversation_id"
        ]
    )

    first = store.select_conversation_job(first_id, "device-a", "job-a")
    second = store.conversation_job_context(second_id, "device-a")

    assert first == {
        "selected_job_id": "job-a",
        "active_artifact_id": None,
        "active_artifact_page": 1,
        "active_artifact_zoom": 1.0,
    }
    assert second == {
        "selected_job_id": "job-b",
        "active_artifact_id": None,
        "active_artifact_page": 1,
        "active_artifact_zoom": 1.0,
    }

    restored = JobOsStateStore(database)
    assert restored.conversation_job_context(first_id, "device-a") == first
    assert restored.conversation_job_context(second_id, "device-a") == second
    with pytest.raises(ConversationNotFound, match="not found"):
        restored.conversation_job_context(first_id, "device-b")


def test_migration_claims_legacy_conversations_for_configured_device(tmp_path):
    database = tmp_path / "owner-migration.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS[:16]:
            JobOsStateStore._apply_migration(connection, migration)

    store = JobOsStateStore(database)
    store.initialize(owner_device_id="configured-device")

    owned = store.list_active_conversations(owner_device_id="configured-device")
    assert len(owned) == 1
    assert owned[0]["owner_device_id"] == "configured-device"
    assert store.list_active_conversations(owner_device_id="other-device") == []


def test_conversation_event_collection_filters_by_durable_owner(tmp_path):
    store = JobOsStateStore(tmp_path / "owner-events.db")
    store.initialize(owner_device_id="device-a")
    first_id = store.first_active_conversation_id("device-a")
    second_id = str(store.create_conversation(actor_id="device-b")["conversation_id"])
    store.conversation_store(first_id).append_event(
        turn_id=None, event_type="status", state="working", summary="Only A"
    )
    store.conversation_store(second_id).append_event(
        turn_id=None, event_type="status", state="working", summary="Only B"
    )

    a_events = store.all_conversation_events_after(0, owner_device_id="device-a")
    b_events = store.all_conversation_events_after(0, owner_device_id="device-b")
    assert [entry["event"]["summary"] for entry in a_events] == ["Only A"]
    assert [entry["event"]["summary"] for entry in b_events] == ["Only B"]


def test_two_owners_have_independent_positions_caps_compaction_and_final_guards(tmp_path):
    store = JobOsStateStore(tmp_path / "cap.db")
    store.initialize(owner_device_id="device-a")
    a_created = [store.create_conversation(actor_id="device-a") for _ in range(4)]
    b_created = [store.create_conversation(actor_id="device-b") for _ in range(5)]

    assert [
        item["position"] for item in store.list_active_conversations(owner_device_id="device-a")
    ] == [1, 2, 3, 4, 5]
    assert [
        item["position"] for item in store.list_active_conversations(owner_device_id="device-b")
    ] == [1, 2, 3, 4, 5]
    assert store.first_active_conversation_id("device-a") != store.first_active_conversation_id(
        "device-b"
    )
    with pytest.raises(ConversationLimit, match="Maximum 5 sessions"):
        store.create_conversation(actor_id="device-a")
    with pytest.raises(ConversationLimit, match="Maximum 5 sessions"):
        store.create_conversation(actor_id="device-b")

    with pytest.raises(ConversationNotFound, match="not found"):
        store.archive_conversation(str(b_created[0]["conversation_id"]), actor_id="device-a")
    store.archive_conversation(str(a_created[1]["conversation_id"]), actor_id="device-a")
    assert [
        item["position"] for item in store.list_active_conversations(owner_device_id="device-a")
    ] == [1, 2, 3, 4]
    assert [
        item["title"] for item in store.list_active_conversations(owner_device_id="device-a")
    ] == [
        "Session 1",
        "Session 2",
        "Session 3",
        "Session 4",
    ]
    assert store.create_conversation(actor_id="device-a")["position"] == 5
    for item in list(store.list_active_conversations(owner_device_id="device-a"))[1:]:
        store.archive_conversation(str(item["conversation_id"]), actor_id="device-a")
    with pytest.raises(ConversationBusy, match="final session"):
        store.archive_conversation(
            store.first_active_conversation_id("device-a"), actor_id="device-a"
        )
    assert len(store.list_active_conversations(owner_device_id="device-b")) == 5

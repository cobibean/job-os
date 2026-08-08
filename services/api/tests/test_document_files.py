import sqlite3
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.capabilities import BrowserCommandResponse
from jobos_api.document_files import DocumentFileCapabilities, DocumentFileRecord, document_file_id
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore

TOKEN = "document-files-device-token"


def headers():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-JobOS-MCP-Token": "document-files-mcp-token",
    }


class FakeJobs:
    def inspect_job(self, job_id):
        if job_id != "(FAKE)-job-7":
            raise KeyError(job_id)
        return {"job_id": job_id}


class DocumentBroker:
    def __init__(self):
        self.commands = []

    async def execute(self, command, *, device_id=None):
        self.commands.append((command, device_id))
        revision = 8 if command.command == "document.apply_operations" else 7
        return BrowserCommandResponse(
            command_id="cmd_document_file_123456",
            state="completed",
            outcome=command.command,
            data={
                "document_key": "resume",
                "document_label": "Resume",
                "filename": "(FAKE)-resume.docx",
                "sha256": ("b" if revision == 8 else "a") * 64,
                "revision": revision,
                "capabilities": {
                    "mode": "editable_with_protected_content",
                    "protectedBlockCount": 2,
                    "editableBlockCount": 14,
                    "reasons": ["2 complex item(s) are retained but not directly editable"],
                },
                "context": {
                    "revision": "context-7",
                    "blocks": [
                        {
                            "id": "docx:0",
                            "index": 0,
                            "type": "docParagraph",
                            "text": "(FAKE) Alex Morgan",
                            "protected": False,
                        }
                    ],
                },
                "recovery_id": "recovery-7" if revision == 8 else None,
            },
        )


def make_app(tmp_path, broker):
    return create_app(
        Settings(
            device_token=TOKEN,
            mcp_token="document-files-mcp-token",
            state_db_path=tmp_path / "jobos.db",
        ),
        capability_broker=broker,
        job_facade=cast(Any, FakeJobs()),
    )


def test_document_inspect_persists_portable_metadata_without_a_local_path(tmp_path):
    broker = DocumentBroker()
    app = make_app(tmp_path, broker)
    command = {
        "command": "document.inspect",
        "arguments": {"job_id": "(FAKE)-job-7", "document_key": "resume"},
        "origin": "mcp",
        "idempotency_key": "(FAKE)-inspect-7",
        "timeout_ms": 10_000,
    }

    with TestClient(app) as client:
        inspected = client.post("/v1/browser/commands", headers=headers(), json=command)
        listing = client.get("/v1/jobs/(FAKE)-job-7/document-files", headers=headers())
        document = listing.json()["documents"][0]
        fetched = client.get(f"/v1/document-files/{document['document_id']}", headers=headers())

    assert inspected.status_code == 200
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert document == fetched.json()
    assert document["filename"] == "(FAKE)-resume.docx"
    assert document["sha256"] == "a" * 64
    assert document["observed_revision"] == 7
    assert document["observed_device_id"]
    assert document["capabilities"]["protected_block_count"] == 2
    assert "path" not in str(document).lower()
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        observation_count = connection.execute(
            "SELECT COUNT(*) FROM document_file_observations"
        ).fetchone()
        assert observation_count == (1,)


def test_document_apply_requires_a_current_sha_and_updates_one_observation_per_revision(tmp_path):
    broker = DocumentBroker()
    app = make_app(tmp_path, broker)
    invalid = {
        "command": "document.apply_operations",
        "arguments": {
            "job_id": "(FAKE)-job-7",
            "document_key": "resume",
            "expected_sha256": "stale",
            "operations": [{"type": "replace_block_text"}],
        },
        "origin": "mcp",
        "idempotency_key": "(FAKE)-invalid-7",
    }
    valid = {
        **invalid,
        "arguments": {
            **invalid["arguments"],
            "expected_sha256": "a" * 64,
            "operations": [
                {
                    "type": "replace_block_text",
                    "blockId": "docx:0",
                    "expectedCurrentText": "(FAKE) Alex Morgan",
                    "text": "(FAKE) Alex Morgan — edited",
                }
            ],
        },
        "idempotency_key": "(FAKE)-apply-7",
    }

    with TestClient(app) as client:
        rejected = client.post("/v1/browser/commands", headers=headers(), json=invalid)
        applied = client.post("/v1/browser/commands", headers=headers(), json=valid)
        replayed = client.post("/v1/browser/commands", headers=headers(), json=valid)
        listing = client.get("/v1/jobs/(FAKE)-job-7/document-files", headers=headers())

    assert rejected.status_code == 422
    assert applied.status_code == 200
    assert replayed.status_code == 200
    assert listing.json()["documents"][0]["observed_revision"] == 8
    assert len(broker.commands) == 1
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        observation_count = connection.execute(
            "SELECT COUNT(*) FROM document_file_observations"
        ).fetchone()
        assert observation_count == (1,)


def test_equal_document_revision_is_idempotent_or_a_visible_conflict(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    capabilities = DocumentFileCapabilities(
        mode="editable",
        protected_block_count=0,
        editable_block_count=3,
        reasons=[],
    )
    observed = DocumentFileRecord(
        document_id=document_file_id("(FAKE)-job-7", "resume"),
        job_id="(FAKE)-job-7",
        document_key="resume",
        document_label="Resume",
        filename="(FAKE)-resume.docx",
        sha256="a" * 64,
        observed_revision=7,
        observed_device_id="(FAKE)-desktop-a",
        capabilities=capabilities,
        observed_at="2026-08-08T01:00:00Z",
    )

    store.observe_document_file(observed)
    store.observe_document_file(observed.model_copy(update={"observed_at": "2026-08-08T01:01:00Z"}))
    with pytest.raises(ValueError, match="Conflicting document file observation"):
        store.observe_document_file(observed.model_copy(update={"sha256": "b" * 64}))

    current = store.get_document_file(observed.document_id)
    assert current is not None
    assert current["sha256"] == "a" * 64
    with sqlite3.connect(database) as connection:
        history = connection.execute(
            "SELECT observed_device_id, observed_revision, sha256 FROM document_file_observations"
        ).fetchall()
    assert history == [("(FAKE)-desktop-a", 7, "a" * 64)]


def test_document_revisions_are_ordered_per_device_without_cross_device_conflicts(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    capabilities = DocumentFileCapabilities(
        mode="editable",
        protected_block_count=0,
        editable_block_count=3,
        reasons=[],
    )
    desktop_a = DocumentFileRecord(
        document_id=document_file_id("(FAKE)-job-7", "resume"),
        job_id="(FAKE)-job-7",
        document_key="resume",
        document_label="Resume",
        filename="(FAKE)-resume-a.docx",
        sha256="a" * 64,
        observed_revision=7,
        observed_device_id="(FAKE)-desktop-a",
        capabilities=capabilities,
        observed_at="2026-08-08T01:00:00Z",
    )
    desktop_b = desktop_a.model_copy(
        update={
            "filename": "(FAKE)-resume-b.docx",
            "sha256": "b" * 64,
            "observed_revision": 1,
            "observed_device_id": "(FAKE)-desktop-b",
            "observed_at": "2026-08-08T01:01:00Z",
        }
    )

    store.observe_document_file(desktop_a)
    store.observe_document_file(desktop_b)

    current = store.get_document_file(desktop_a.document_id)
    assert current is not None
    assert current["observed_device_id"] == "(FAKE)-desktop-b"
    assert current["observed_revision"] == 1
    assert current["sha256"] == "b" * 64
    with sqlite3.connect(database) as connection:
        history = connection.execute(
            """
            SELECT observed_device_id, observed_revision, sha256
            FROM document_file_observations
            ORDER BY observation_id
            """
        ).fetchall()
    assert history == [
        ("(FAKE)-desktop-a", 7, "a" * 64),
        ("(FAKE)-desktop-b", 1, "b" * 64),
    ]

import base64
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import jobos_api.artifact_repository as artifact_repository_module
import pytest
from conftest import build_minimal_pdf
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.documents import ArtifactPublishRequest
from jobos_api.editable_documents import blank_content, default_settings
from jobos_api.private_adapters.job_hunter import adapt_job_hunter_facade
from jobos_api.settings import DeviceCredential, Settings
from jobos_api.state_store import JobOsStateStore, mutation_activity_source_id

TITLE_POLICY_FIXTURES = json.loads(
    (Path(__file__).parents[3] / "tests/fixtures/browser-title-policy.json").read_text()
)


def test_publish_request_preserves_custom_existing_labels_and_fixes_references_label():
    common = {
        "source_filename": "source.docx",
        "source_base64": base64.b64encode(b"source").decode(),
        "artifact_filename": "artifact.pdf",
        "artifact_base64": base64.b64encode(b"artifact").decode(),
        "origin": "mcp",
        "idempotency_key": "publish-label-contract",
    }
    request = ArtifactPublishRequest.model_validate(
        {"document_key": "resume", "document_label": "Tailored Resume", **common}
    )
    assert request.document_label == "Tailored Resume"
    with pytest.raises(ValueError, match="References"):
        ArtifactPublishRequest.model_validate(
            {"document_key": "references", "document_label": "Reference Sheet", **common}
        )


def test_publish_request_reserves_content_addressed_filename_prefix():
    common = {
        "document_key": "resume",
        "document_label": "Tailored Resume",
        "source_base64": base64.b64encode(b"source").decode(),
        "artifact_filename": "artifact.pdf",
        "artifact_base64": base64.b64encode(b"artifact").decode(),
        "origin": "mcp",
        "idempotency_key": "publish-filename-contract",
    }
    accepted = ArtifactPublishRequest.model_validate(
        {"source_filename": f"{'a' * 229}.docx", **common}
    )
    assert len(accepted.source_filename.encode("utf-8")) == 234

    with pytest.raises(ValueError, match="too long for content-addressed storage"):
        ArtifactPublishRequest.model_validate(
            {"source_filename": f"{'a' * 230}.docx", **common}
        )


STATUSES = (
    "discovered",
    "scored",
    "reviewed",
    "shortlisted",
    "apply_now",
    "maybe",
    "stretch",
    "skipped",
    "applied",
    "interviewing",
    "closed",
    "archived",
)

EXPECTED_GROUPS = {
    "discovered": "Inbox",
    "scored": "Inbox",
    "reviewed": "Inbox",
    "shortlisted": "Considering",
    "apply_now": "Considering",
    "maybe": "Considering",
    "stretch": "Considering",
    "applied": "Applied",
    "interviewing": "Interviewing",
    "closed": "Closed",
    "skipped": "Inactive",
    "archived": "Inactive",
}


class FakeJobHunterFacade:
    def __init__(self):
        self.status_update_calls = []
        self.jobs = [
            {
                "job_id": f"job-{index}",
                "company": f"Company {index:02d}",
                "title": f"Role {index:02d}",
                "status": status,
                "canonical_url": f"https://example.com/jobs/{index}",
                "discovered_at": f"2026-07-{index + 1:02d}T00:00:00+00:00",
                "last_seen_at": f"2026-07-{index + 1:02d}T01:00:00+00:00",
            }
            for index, status in enumerate(STATUSES)
        ]
        self.history = []
        self.artifacts = {}
        self.add_job_calls = 0
        self.description_update_calls = 0
        self.descriptions = {}
        self.locations = {}
        self.publish_calls = []

    def is_available(self):
        return True

    def list_jobs(self):
        return list(self.jobs)

    def add_job(
        self,
        *,
        job_id,
        company_name,
        title,
        canonical_url,
        location_text,
        description_text,
        application_url,
    ):
        self.add_job_calls += 1
        existing = next(
            (job for job in self.jobs if job["canonical_url"] == canonical_url),
            None,
        )
        if existing is not None:
            self.descriptions[existing["job_id"]] = description_text
            self.locations[existing["job_id"]] = location_text
            return {"created": False, "job": self.inspect_job(existing["job_id"])}
        self.jobs.append(
            {
                "job_id": job_id,
                "company": company_name,
                "title": title,
                "status": "discovered",
                "canonical_url": canonical_url,
                "discovered_at": "2026-07-21T16:00:00+00:00",
                "last_seen_at": "2026-07-21T16:00:00+00:00",
            }
        )
        self.descriptions[job_id] = description_text
        self.locations[job_id] = location_text
        return {"created": True, "job": self.inspect_job(job_id)}

    def inspect_job(self, job_id):
        job = next((job for job in self.jobs if job["job_id"] == job_id), None)
        if job is None:
            raise KeyError(job_id)
        return {
            **job,
            "description": self.descriptions.get(job_id, "A job description"),
            "location": self.locations.get(job_id, "Remote"),
        }

    def update_job_description(
        self, job_id, description_text, *, source, provenance=None
    ):
        self.inspect_job(job_id)
        self.description_update_calls += 1
        self.descriptions[job_id] = description_text
        return self.inspect_job(job_id)

    def get_lead_history(self, job_id):
        self.inspect_job(job_id)
        return list(self.history)

    def update_lead_state(
        self, job_id, target_state, *, reason=None, record_application=False
    ):
        job = self.inspect_job(job_id)
        self.status_update_calls.append(
            (job["status"], target_state, record_application)
        )
        if job["status"] == "discovered" and target_state == "interviewing":
            raise ValueError("Invalid lead state transition: discovered -> interviewing")
        stored = next(job for job in self.jobs if job["job_id"] == job_id)
        stored["status"] = target_state
        return self.inspect_job(job_id)

    def list_job_artifacts(self, job_id):
        self.inspect_job(job_id)
        return list(self.artifacts.get(job_id, []))

    def register_artifact(self, job_id, artifact_reference):
        return next(
            artifact
            for artifact in self.list_job_artifacts(job_id)
            if artifact.get("artifact_reference") == artifact_reference
        )

    def render_resume(self, job_id, source_id, _output_options):
        return self.register_artifact(job_id, source_id)

    def publish_document_artifact(
        self, job_id, document_key, document_label, source_path, artifact_path
    ):
        self.inspect_job(job_id)
        source = Path(source_path)
        artifact = Path(artifact_path)
        self.publish_calls.append(
            {
                "job_id": job_id,
                "document_key": document_key,
                "source_path": source_path,
                "artifact_path": artifact_path,
            }
        )
        content = artifact.read_bytes()
        rows = self.artifacts.setdefault(job_id, [])
        published = {
            "job_id": job_id,
            "document_key": document_key,
            "document_label": document_label,
            "source_revision": sha256(source.read_bytes()).hexdigest(),
            "artifact_revision": sha256(content).hexdigest(),
            "media_type": (
                "application/pdf"
                if artifact.suffix.casefold() == ".pdf"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            "sha256": sha256(content).hexdigest(),
            "render_status": "succeeded",
            "render_sequence": len(rows) + 1,
            "path": str(artifact),
        }
        rows.append(published)
        return published


class FailOnceStatusSettlementStore(JobOsStateStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.fail_status_settlement = True

    def record_mutation_result(self, **kwargs):
        if (
            self.fail_status_settlement
            and kwargs.get("command_name") == "job.update_status"
            and kwargs.get("reserved_event_id") is not None
        ):
            self.fail_status_settlement = False
            raise sqlite3.OperationalError("synthetic status settlement failure")
        return super().record_mutation_result(**kwargs)


def make_client(tmp_path, facade=None, state_store=None):
    repository, artifact_gateway = adapt_job_hunter_facade(
        facade or FakeJobHunterFacade()
    )
    app = create_app(
        Settings(
            device_token="test-device-token",
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
            artifact_roots=(tmp_path,),
            hermes_job_hunter_cwd=tmp_path,
        ),
        job_repository=repository,
        artifact_gateway=artifact_gateway,
        state_store=state_store,
    )
    return TestClient(app)


def test_record_application_advances_through_required_internal_states(tmp_path):
    facade = FakeJobHunterFacade()
    client = make_client(tmp_path, facade)

    with client:
        changed = client.put(
            "/v1/jobs/job-0/status",
            headers=auth_headers(),
            json={
                "target_status": "applied",
                "origin": "user",
                "record_application": True,
            },
        )

    assert changed.status_code == 200
    assert changed.json()["job"]["status"] == "applied"
    assert facade.status_update_calls == [
        ("discovered", "applied", True),
    ]


def test_status_retry_preserves_original_transition_after_settlement_failure(tmp_path):
    facade = FakeJobHunterFacade()
    state_path = tmp_path / "jobos.db"
    state_store = FailOnceStatusSettlementStore(state_path)
    client = make_client(tmp_path, facade, state_store)
    payload = {
        "target_status": "reviewed",
        "origin": "user",
        "idempotency_key": "status-settlement-retry",
    }

    with client:
        with pytest.raises(sqlite3.OperationalError, match="synthetic status settlement failure"):
            client.put(
                "/v1/jobs/job-0/status",
                headers=auth_headers(),
                json=payload,
            )
        retry = client.put(
            "/v1/jobs/job-0/status",
            headers=auth_headers(),
            json=payload,
        )

    assert retry.status_code == 200
    assert retry.json()["job"]["status"] == "reviewed"
    assert facade.status_update_calls == [
        ("discovered", "reviewed", False),
        ("reviewed", "reviewed", False),
    ]
    with sqlite3.connect(state_path) as connection:
        rows = connection.execute(
            """
            SELECT event_type, payload_json, result_json
            FROM job_events
            WHERE command_name = 'job.update_status'
              AND idempotency_key = 'status-settlement-retry'
            """
        ).fetchall()
    assert len(rows) == 1
    event_type, payload_json, result_json = rows[0]
    assert event_type == "job_status_changed"
    assert json.loads(payload_json)["from_status"] == "discovered"
    assert json.loads(payload_json)["to_status"] == "reviewed"
    assert json.loads(result_json)["job"]["status"] == "reviewed"


def auth_headers():
    return {
        "Authorization": "Bearer test-device-token",
        "X-JobOS-MCP-Token": "test-mcp-trusted-token",
    }


def browser_job_payload(**overrides):
    return {
        "company_name": "Northstar Labs",
        "title": "Applied AI Product Builder",
        "canonical_url": "https://jobs.example.com/northstar/applied-ai-builder",
        "location_text": "United States · Remote",
        "description_text": "Build useful agent workflows with operators and customers.",
        "application_url": "https://jobs.example.com/northstar/applied-ai-builder/apply",
        "origin": "user",
        "idempotency_key": "browser-save-1",
        **overrides,
    }


def long_browser_description():
    return "\n\n".join(
        [
            "FULL-DESCRIPTION-START\n## Role overview\n"
            + "Build reliable agent workflows with customers. " * 70,
            "FULL-DESCRIPTION-MIDDLE\n## Responsibilities\n"
            + "Own discovery, delivery, measurement, and iteration. " * 70,
            "## Qualifications\n" + "Translate complex systems into clear product decisions. " * 70,
            "## Benefits and compensation\n"
            + "Competitive salary, healthcare, and flexible work. " * 70,
            "## Equal opportunity\nWe welcome qualified applicants.\nFULL-DESCRIPTION-END",
        ]
    )


def test_browser_save_persists_complete_long_description_for_new_and_existing_job(tmp_path):
    facade = FakeJobHunterFacade()
    description = long_browser_description()
    refreshed_description = f"{description}\n\nFULL-DESCRIPTION-REFRESH"

    with make_client(tmp_path, facade) as client:
        first = client.post(
            "/v1/jobs",
            headers=auth_headers(),
            json=browser_job_payload(description_text=description),
        )
        replay = client.post(
            "/v1/jobs",
            headers=auth_headers(),
            json=browser_job_payload(description_text=description),
        )
        refreshed = client.post(
            "/v1/jobs",
            headers=auth_headers(),
            json=browser_job_payload(
                description_text=refreshed_description,
                idempotency_key="browser-save-long-description-2",
            ),
        )
        inspected = client.get(
            f"/v1/jobs/{first.json()['job']['job_id']}", headers=auth_headers()
        )

    assert len(description) > 5_000
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["job"]["description"] == description
    assert replay.json() == first.json()
    assert refreshed.status_code == 200
    assert refreshed.json()["created"] is False
    assert refreshed.json()["job"]["job_id"] == first.json()["job"]["job_id"]
    assert refreshed.json()["job"]["description"] == refreshed_description
    assert inspected.json()["description"] == refreshed_description
    assert facade.add_job_calls == 2


def test_browser_save_creates_and_lists_without_retargeting_an_agent_session(tmp_path):
    facade = FakeJobHunterFacade()

    with make_client(tmp_path, facade) as client:
        response = client.post("/v1/jobs", headers=auth_headers(), json=browser_job_payload())
        jobs = client.get("/v1/jobs", headers=auth_headers())
        workspace = client.get("/v1/workspace/jobs", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["job"]["company"] == "Northstar Labs"
    assert body["job"]["title"] == "Applied AI Product Builder"
    assert body["job"]["location"] == "United States · Remote"
    assert body["job"]["description"].startswith("Build useful agent workflows")
    assert jobs.json()["jobs"][-1]["job_id"] == body["job"]["job_id"]
    assert workspace.json()["selected_job_id"] is None


def test_mcp_job_create_requires_the_separate_trusted_credential(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/jobs",
            headers={"Authorization": "Bearer test-device-token"},
            json=browser_job_payload(origin="mcp", idempotency_key="forged-agent-save-1"),
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "MCP operations require the trusted local MCP credential"


def test_agent_job_create_stays_in_audit_without_injecting_ownerless_chat_activity(tmp_path):
    facade = FakeJobHunterFacade()

    with make_client(tmp_path, facade) as client:
        response = client.post(
            "/v1/jobs",
            headers=auth_headers(),
            json=browser_job_payload(origin="mcp", idempotency_key="agent-save-1"),
        )
        conversation = client.get("/v1/conversations/current", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["job"]["title"] == "Applied AI Product Builder"
    assert not any(entry["type"] == "activity" for entry in conversation.json()["entries"])


def test_agent_read_stays_in_audit_without_injecting_ownerless_chat_activity(tmp_path):
    with make_client(tmp_path) as client:
        response = client.get(
            "/v1/jobs",
            headers=auth_headers(),
            params={"origin": "mcp", "idempotency_key": "agent-read-audit-1"},
        )
        conversation = client.get("/v1/conversations/current", headers=auth_headers())

    assert response.status_code == 200
    assert not any(entry["type"] == "activity" for entry in conversation.json()["entries"])
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        audit = connection.execute(
            "SELECT event_type, command_name FROM job_events WHERE idempotency_key = ?",
            ("agent-read-audit-1",),
        ).fetchone()
    assert audit == ("agent_read", "job.list")


def test_agent_updates_full_description_with_idempotent_recorded_mutation(tmp_path):
    facade = FakeJobHunterFacade()
    payload = {
        "description_text": (
            "Complete listing with responsibilities, qualifications, and compensation."
        ),
        "source": "jobhunter_agent",
        "provenance": "User supplied the complete posting",
        "origin": "mcp",
        "idempotency_key": "description-update-1",
    }

    with make_client(tmp_path, facade) as client:
        first = client.put(
            "/v1/jobs/job-0/description", headers=auth_headers(), json=payload
        )
        replay = client.put(
            "/v1/jobs/job-0/description", headers=auth_headers(), json=payload
        )
        inspected = client.get("/v1/jobs/job-0", headers=auth_headers())
        events = client.get("/v1/events?after=0", headers=auth_headers())
        conversation = client.get("/v1/conversations/current", headers=auth_headers())

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert facade.description_update_calls == 1
    assert inspected.json()["description"] == payload["description_text"]
    description_events = [
        event
        for event in events.json()["events"]
        if event["event_type"] == "job_description_updated"
    ]
    assert description_events == [
        {
            "event_id": first.json()["event_id"],
            "event_type": "job_description_updated",
            "job_id": "job-0",
            "origin": "mcp",
            "occurred_at": description_events[0]["occurred_at"],
            "from_status": None,
            "to_status": None,
            "selected_job_id": None,
            "job_ids": None,
            "sort_mode": None,
            "source": "jobhunter_agent",
            "description_length": len(payload["description_text"]),
        }
    ]
    assert not any(entry["type"] == "activity" for entry in conversation.json()["entries"])


def test_description_update_requires_trusted_mcp_credential_and_valid_job(tmp_path):
    payload = {
        "description_text": "Complete listing",
        "source": "jobhunter_agent",
        "origin": "mcp",
        "idempotency_key": "description-update-2",
    }
    with make_client(tmp_path) as client:
        forbidden = client.put(
            "/v1/jobs/job-0/description",
            headers={"Authorization": "Bearer test-device-token"},
            json=payload,
        )
        missing = client.put(
            "/v1/jobs/missing/description", headers=auth_headers(), json=payload
        )
        invalid = client.put(
            "/v1/jobs/job-0/description",
            headers=auth_headers(),
            json={**payload, "description_text": "   "},
        )

    assert forbidden.status_code == 403
    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_browser_save_replays_the_same_idempotent_result_without_a_second_ingest(tmp_path):
    facade = FakeJobHunterFacade()
    payload = browser_job_payload()

    with make_client(tmp_path, facade) as client:
        first = client.post("/v1/jobs", headers=auth_headers(), json=payload)
        second = client.post("/v1/jobs", headers=auth_headers(), json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert facade.add_job_calls == 1


def test_browser_save_reuses_the_existing_job_when_the_listing_url_is_already_saved(tmp_path):
    facade = FakeJobHunterFacade()
    payload = browser_job_payload()

    with make_client(tmp_path, facade) as client:
        first = client.post("/v1/jobs", headers=auth_headers(), json=payload)
        second = client.post(
            "/v1/jobs",
            headers=auth_headers(),
            json={**payload, "idempotency_key": "browser-save-2"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["job"]["job_id"] == first.json()["job"]["job_id"]
    assert len(facade.jobs) == len(STATUSES) + 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_name", ""),
        ("title", "  "),
        ("canonical_url", ""),
        ("location_text", ""),
        ("description_text", ""),
        ("application_url", ""),
    ],
)
def test_browser_save_requires_every_listing_field(tmp_path, field, value):
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/jobs",
            headers=auth_headers(),
            json=browser_job_payload(**{field: value}),
        )

    assert response.status_code == 422


@pytest.mark.parametrize("field", ["canonical_url", "application_url"])
def test_browser_save_rejects_credential_bearing_urls(tmp_path, field):
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/jobs",
            headers=auth_headers(),
            json=browser_job_payload(
                **{field: "https://user:secret@jobs.example.com/northstar"}
            ),
        )

    assert response.status_code == 422


def artifact_metadata(
    path,
    *,
    job_id="job-0",
    source="source-1",
    revision="render-1",
    sequence=1,
):
    content = path.read_bytes()
    return {
        "job_id": job_id,
        "source_revision": source,
        "artifact_revision": revision,
        "media_type": "application/pdf",
        "sha256": sha256(content).hexdigest(),
        "render_status": "succeeded",
        "render_sequence": sequence,
        "path": str(path),
    }


def test_every_canonical_status_maps_to_exactly_one_approved_group(tmp_path):
    with make_client(tmp_path) as client:
        response = client.get("/v1/jobs", headers=auth_headers())

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert {job["status"]: job["status_group"] for job in jobs} == EXPECTED_GROUPS
    assert len(jobs) == len(STATUSES)


def test_list_filters_then_applies_the_requested_calculated_order(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = [facade.jobs[8], facade.jobs[0], facade.jobs[4]]
    facade.jobs[0]["company"] = "Zulu Labs"
    facade.jobs[1]["company"] = "Alpha Systems"
    facade.jobs[2]["company"] = "Middle Works"

    with make_client(tmp_path, facade) as client:
        recent = client.get("/v1/jobs?sort=recent", headers=auth_headers()).json()["jobs"]
        alphabetical = client.get("/v1/jobs?sort=alphabetical", headers=auth_headers()).json()[
            "jobs"
        ]
        status = client.get("/v1/jobs?sort=status", headers=auth_headers()).json()["jobs"]
        filtered = client.get(
            "/v1/jobs?query=alpha&status_group=Inbox",
            headers=auth_headers(),
        ).json()["jobs"]

    assert [job["job_id"] for job in recent] == ["job-8", "job-4", "job-0"]
    assert [job["company"] for job in alphabetical] == [
        "Alpha Systems",
        "Middle Works",
        "Zulu Labs",
    ]
    assert [job["status_group"] for job in status] == ["Inbox", "Considering", "Applied"]
    assert [job["job_id"] for job in filtered] == ["job-0"]


def test_manual_order_survives_switching_to_calculated_sort_modes(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:3]

    with make_client(tmp_path, facade) as client:
        reordered = client.put(
            "/v1/jobs/order",
            headers=auth_headers(),
            json={"job_ids": ["job-2", "job-0", "job-1"], "origin": "user"},
        )
        alphabetical = client.get("/v1/jobs?sort=alphabetical", headers=auth_headers()).json()[
            "jobs"
        ]
        manual = client.get("/v1/jobs?sort=manual", headers=auth_headers()).json()["jobs"]

    assert reordered.status_code == 200
    assert [job["job_id"] for job in alphabetical] == ["job-0", "job-1", "job-2"]
    assert [job["job_id"] for job in manual] == ["job-2", "job-0", "job-1"]


def test_user_and_mcp_status_changes_share_one_command_and_event_path(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]

    with make_client(tmp_path, facade) as client:
        user_change = client.put(
            "/v1/jobs/job-0/status",
            headers=auth_headers(),
            json={"target_status": "reviewed", "origin": "user", "reason": "Worth review"},
        )
        visible_to_mcp = client.get("/v1/jobs/job-0", headers=auth_headers())
        mcp_change = client.put(
            "/v1/jobs/job-0/status",
            headers=auth_headers(),
            json={"target_status": "shortlisted", "origin": "mcp"},
        )
        events = client.get("/v1/events?after=0", headers=auth_headers())

    assert user_change.status_code == 200
    assert user_change.json()["job"]["status"] == "reviewed"
    assert visible_to_mcp.json()["status"] == "reviewed"
    assert mcp_change.status_code == 200
    assert mcp_change.json()["job"]["status"] == "shortlisted"
    assert [(event["origin"], event["to_status"]) for event in events.json()["events"]] == [
        ("user", "reviewed"),
        ("mcp", "shortlisted"),
    ]


def test_invalid_transition_returns_clear_feedback_without_an_event_or_partial_change(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]

    with make_client(tmp_path, facade) as client:
        rejected = client.put(
            "/v1/jobs/job-0/status",
            headers=auth_headers(),
            json={"target_status": "interviewing", "origin": "user"},
        )
        current = client.get("/v1/jobs/job-0", headers=auth_headers())
        events = client.get("/v1/events?after=0", headers=auth_headers())

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == (
        "Invalid lead state transition: discovered -> interviewing"
    )
    assert current.json()["status"] == "discovered"
    assert events.json() == {"events": []}


def test_selection_is_durable_and_uses_the_same_conversation_path_for_user_and_mcp(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]

    with make_client(tmp_path, facade) as client:
        conversation_id = client.get(
            "/v1/conversations/current", headers=auth_headers()
        ).json()["conversation_id"]
        user_selection = client.put(
            f"/v1/conversations/{conversation_id}/workspace/job",
            headers=auth_headers(),
            json={"job_id": "job-1", "origin": "user"},
        )
        state = client.get(
            f"/v1/workspace/jobs?conversation_id={conversation_id}", headers=auth_headers()
        )
        mcp_selection = client.put(
            f"/v1/conversations/{conversation_id}/workspace/job",
            headers=auth_headers(),
            json={"job_id": "job-0", "origin": "mcp"},
        )
        restored = client.get(
            f"/v1/workspace/jobs?conversation_id={conversation_id}", headers=auth_headers()
        )

    assert user_selection.status_code == 200
    assert state.json()["selected_job_id"] == "job-1"
    assert mcp_selection.status_code == 200
    assert restored.json()["selected_job_id"] == "job-0"


def test_mcp_job_and_artifact_calls_fail_closed_when_the_conversation_owns_another_job(
    tmp_path,
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]

    with make_client(tmp_path, facade) as client:
        conversation_id = client.get(
            "/v1/conversations/current", headers=auth_headers()
        ).json()["conversation_id"]
        selected = client.put(
            f"/v1/conversations/{conversation_id}/workspace/job",
            headers=auth_headers(),
            json={"job_id": "job-0", "origin": "user"},
        )
        mismatched_read = client.get(
            f"/v1/jobs/job-1/artifacts?conversation_id={conversation_id}",
            headers=auth_headers(),
        )
        mismatched_mutation = client.put(
            f"/v1/jobs/job-1/status?conversation_id={conversation_id}",
            headers=auth_headers(),
            json={
                "target_status": "reviewed",
                "origin": "mcp",
                "idempotency_key": "wrong-session-job",
            },
        )
        owned_read = client.get(
            f"/v1/jobs/job-0/artifacts?conversation_id={conversation_id}",
            headers=auth_headers(),
        )

    assert selected.status_code == 200
    assert mismatched_read.status_code == 409
    assert mismatched_read.json()["code"] == "conversation_job_mismatch"
    assert mismatched_mutation.status_code == 409
    assert owned_read.status_code == 200


def test_trusted_local_mcp_scopes_publication_to_a_remote_device_conversation(
    tmp_path, minimal_docx
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    repository, artifact_gateway = adapt_job_hunter_facade(facade)
    app = create_app(
        Settings(
            device_id="primary-device",
            device_token="test-device-token",
            mcp_token="test-mcp-trusted-token",
            device_credentials=(
                DeviceCredential(
                    device_id="remote-device",
                    token="remote-device-token",
                ),
            ),
            state_db_path=tmp_path / "jobos.db",
            artifact_roots=(tmp_path,),
            hermes_job_hunter_cwd=tmp_path,
        ),
        job_repository=repository,
        artifact_gateway=artifact_gateway,
    )
    remote_headers = {"Authorization": "Bearer remote-device-token"}
    source = b"# Remote-device resume"
    payload = {
        "document_key": "resume",
        "document_label": "Resume",
        "source_filename": "resume.md",
        "source_base64": base64.b64encode(source).decode("ascii"),
        "artifact_filename": "resume.docx",
        "artifact_base64": base64.b64encode(minimal_docx("remote resume")).decode("ascii"),
        "origin": "mcp",
        "idempotency_key": "remote-conversation-publish",
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        conversation = client.post("/v1/conversations", headers=remote_headers)
        conversation_id = conversation.json()["conversation_id"]
        selected = client.put(
            f"/v1/conversations/{conversation_id}/workspace/job",
            headers=remote_headers,
            json={"job_id": "job-0", "origin": "user"},
        )
        published = client.post(
            f"/v1/jobs/job-0/artifacts/publish?conversation_id={conversation_id}",
            headers=auth_headers(),
            json=payload,
        )
        listed = client.get(
            f"/v1/jobs/job-0/artifacts?conversation_id={conversation_id}",
            headers=auth_headers(),
        )
        missing = client.get(
            "/v1/jobs/job-0/artifacts?conversation_id=conv_missing",
            headers=auth_headers(),
        )
        remote_mcp_attempt = client.get(
            f"/v1/jobs/job-0/artifacts?conversation_id={conversation_id}",
            headers={**remote_headers, "X-JobOS-MCP-Token": "test-mcp-trusted-token"},
        )

    assert conversation.status_code == 201
    assert selected.status_code == 200
    assert published.status_code == 200
    assert published.json()["artifacts"][0]["job_id"] == "job-0"
    assert listed.status_code == 200
    assert len(listed.json()["artifacts"]) == 1
    assert missing.status_code == 404
    assert missing.json()["code"] == "conversation_not_found"
    assert remote_mcp_attempt.status_code == 403
    assert remote_mcp_attempt.json()["code"] == "mcp_local_device_required"


def test_inspect_and_history_expose_normalized_facade_records(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    facade.history = [
        {
            "event_id": 9,
            "event_type": "lead_state_changed",
            "from_status": "discovered",
            "to_status": "reviewed",
            "occurred_at": "2026-07-20T02:00:00+00:00",
            "reason": "Reviewed in JobOS",
        }
    ]

    with make_client(tmp_path, facade) as client:
        inspected = client.get("/v1/jobs/job-0", headers=auth_headers())
        history = client.get("/v1/jobs/job-0/history", headers=auth_headers())

    assert inspected.status_code == 200
    assert inspected.json()["description"] == "A job description"
    assert history.status_code == 200
    assert history.json() == {"events": facade.history}


def test_event_stream_emits_ordered_resumable_status_events(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]

    with make_client(tmp_path, facade) as client:
        changed = client.put(
            "/v1/jobs/job-0/status",
            headers=auth_headers(),
            json={"target_status": "reviewed", "origin": "mcp"},
        )
        streamed = client.get(
            "/v1/events/stream?after=0&once=true",
            headers=auth_headers(),
        )

    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert f"id: {changed.json()['event_id']}" in streamed.text
    assert '"origin":"mcp"' in streamed.text
    assert '"to_status":"reviewed"' in streamed.text


def test_sort_mode_persists_without_rewriting_manual_order(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:3]
    facade.jobs[0]["status"] = "applied"

    with make_client(tmp_path, facade) as client:
        client.put(
            "/v1/jobs/order",
            headers=auth_headers(),
            json={"job_ids": ["job-2", "job-0", "job-1"], "origin": "user"},
        )
        changed = client.put(
            "/v1/workspace/jobs/sort",
            headers=auth_headers(),
            json={"sort_mode": "status", "origin": "user"},
        )
        state = client.get("/v1/workspace/jobs", headers=auth_headers())
        default_list = client.get("/v1/jobs", headers=auth_headers())

    assert changed.status_code == 200
    assert state.json() == {
        "selected_job_id": None,
        "sort_mode": "status",
        "manual_order": ["job-2", "job-0", "job-1"],
    }
    assert [job["status_group"] for job in default_list.json()["jobs"]] == [
        "Inbox",
        "Inbox",
        "Applied",
    ]


def test_workspace_snapshot_round_trip_and_revision_conflict(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]

    with make_client(tmp_path, facade) as client:
        client.put(
            "/v1/workspace/jobs/selection",
            headers=auth_headers(),
            json={"job_id": "job-1", "origin": "user"},
        )
        initial = client.get("/v1/workspace", headers=auth_headers())
        body = initial.json()
        body.pop("repaired_presets")
        body.pop("repaired_browser")
        body.pop("browser_repair_reasons")
        body.update({"origin": "user", "idempotency_key": "workspace-round-trip-1"})
        body["selected_preset"] = "agent-focus"
        body.update(
            active_top_level_workspace="browse",
            browse_mode="swipe",
            browse_focus_job_id="job-0",
            browse_query="builder",
            browse_status_group="Considering",
            browse_sort_mode="recent",
            browse_rail_width=300,
        )
        saved = client.put("/v1/workspace", headers=auth_headers(), json=body)
        body["idempotency_key"] = "workspace-stale-write-1"
        stale = client.put("/v1/workspace", headers=auth_headers(), json=body)
        restored = client.get("/v1/workspace", headers=auth_headers())

    assert initial.status_code == 200
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Workspace revision conflict; current revision is 1"
    assert restored.json()["selected_preset"] == "agent-focus"
    assert restored.json()["selected_job_id"] is None
    assert restored.json()["active_top_level_workspace"] == "browse"
    assert restored.json()["browse_mode"] == "swipe"
    assert restored.json()["browse_focus_job_id"] == "job-0"
    assert restored.json()["browse_query"] == "builder"
    assert restored.json()["browse_status_group"] == "Considering"
    assert restored.json()["browse_sort_mode"] == "recent"
    assert restored.json()["browse_rail_width"] == 300


def test_workspace_snapshot_idempotent_retry_returns_original_revision(tmp_path):
    facade = FakeJobHunterFacade()

    with make_client(tmp_path, facade) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        body.pop("repaired_presets")
        body.pop("repaired_browser")
        body.pop("browser_repair_reasons")
        body.update(
            {
                "selected_preset": "research",
                "active_center_surface": "browser",
                "origin": "user",
                "idempotency_key": "workspace-api-save-1",
            }
        )
        first = client.put("/v1/workspace", headers=auth_headers(), json=body)
        retry = client.put("/v1/workspace", headers=auth_headers(), json=body)
        changed = client.put(
            "/v1/workspace",
            headers=auth_headers(),
            json={**body, "selected_preset": "review"},
        )
        restored = client.get("/v1/workspace", headers=auth_headers())

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert changed.status_code == 409
    assert changed.json()["detail"] == (
        "Idempotency key was already used for a different workspace command"
    )
    assert restored.json()["revision"] == 1


def test_workspace_old_client_preserves_selected_preset_as_top_level_workspace(tmp_path):
    facade = FakeJobHunterFacade()

    with make_client(tmp_path, facade) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        for field in (
            "repaired_presets",
            "repaired_browser",
            "browser_repair_reasons",
            "active_top_level_workspace",
        ):
            body.pop(field)
        body.update(
            selected_preset="research",
            active_center_surface="browser",
            origin="user",
            idempotency_key="workspace-old-client-research-1",
        )
        saved = client.put("/v1/workspace", headers=auth_headers(), json=body)
        restored = client.get("/v1/workspace", headers=auth_headers())

    assert saved.status_code == 200
    assert saved.json()["selected_preset"] == "research"
    assert saved.json()["active_top_level_workspace"] == "research"
    assert restored.json()["active_top_level_workspace"] == "research"


def test_workspace_rejects_credential_bearing_browser_metadata(tmp_path):
    facade = FakeJobHunterFacade()

    with make_client(tmp_path, facade) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        body.pop("repaired_presets")
        body.pop("repaired_browser")
        body.pop("browser_repair_reasons")
        body.update(
            {
                "origin": "user",
                "idempotency_key": "workspace-browser-secret-1",
                "browser_tabs": [
                    {
                        "tab_id": "oauth",
                        "url": "https://example.com/callback?code=must-not-persist",
                        "title": "Callback",
                    }
                ],
                "active_browser_tab_id": "oauth",
            }
        )
        response = client.put("/v1/workspace", headers=auth_headers(), json=body)

    assert response.status_code == 422


def test_workspace_get_drops_malformed_url_and_favicon_without_losing_valid_tabs(tmp_path):
    with make_client(tmp_path) as client:
        initial = client.get("/v1/workspace", headers=auth_headers()).json()
        snapshot = {
            key: value
            for key, value in initial.items()
            if key
            not in {"revision", "repaired_presets", "repaired_browser", "browser_repair_reasons"}
        }
        snapshot["browser_tabs"] = [
            {
                "tab_id": "before",
                "url": "https://example.com/before",
                "title": "Before",
                "favicon_url": None,
                "associated_job_id": None,
            },
            {"tab_id": "bad-url", "url": "https://[::1", "title": "Bad URL"},
            {
                "tab_id": "bad-favicon",
                "url": "https://example.com/valid-page",
                "title": "Bad favicon",
                "favicon_url": "http://[",
                "associated_job_id": None,
            },
            {
                "tab_id": "after",
                "url": "https://example.com/after",
                "title": "After",
                "favicon_url": None,
                "associated_job_id": None,
            },
        ]
        snapshot["active_browser_tab_id"] = "after"
        with sqlite3.connect(tmp_path / "jobos.db") as connection:
            connection.execute(
                """
                INSERT INTO workspace_snapshots(device_id, revision, snapshot_json)
                VALUES (?, ?, ?)
                """,
                ("primary-device", 9, json.dumps(snapshot)),
            )

        response = client.get("/v1/workspace", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["repaired_browser"] is True
    assert body["browser_repair_reasons"] == ["dropped_tabs"]
    assert [tab["tab_id"] for tab in body["browser_tabs"]] == ["before", "after"]
    assert body["active_browser_tab_id"] == "after"


@pytest.mark.parametrize(
    ("tabs", "active_tab_id", "expected_reasons"),
    [
        (
            [
                {
                    "tab_id": "safe",
                    "url": "https://example.com/",
                    "title": "%ZZPRIVATE%5FKEY%3Aexample-value",
                }
            ],
            "safe",
            ["protected_title"],
        ),
        (
            [
                {"tab_id": "safe", "url": "https://example.com/", "title": "Safe"},
                {"tab_id": "bad", "url": "file:///bad", "title": "Bad"},
            ],
            "safe",
            ["dropped_tabs"],
        ),
        (
            [{"tab_id": "safe", "url": "https://example.com/", "title": "Safe"}],
            "missing",
            ["reselected_active_tab"],
        ),
        (
            [
                {
                    "tab_id": "safe",
                    "url": "https://example.com/",
                    "title": "AWS_SECRET_ACCESS_KEY=example-value",
                },
                {"tab_id": "bad", "url": "file:///bad", "title": "Bad"},
            ],
            "bad",
            ["protected_title", "dropped_tabs", "reselected_active_tab"],
        ),
    ],
)
def test_workspace_get_reports_exact_browser_repair_reasons(
    tmp_path, tabs, active_tab_id, expected_reasons
):
    with make_client(tmp_path) as client:
        initial = client.get("/v1/workspace", headers=auth_headers()).json()
        snapshot = {
            key: value
            for key, value in initial.items()
            if key
            not in {"revision", "repaired_presets", "repaired_browser", "browser_repair_reasons"}
        }
        snapshot["browser_tabs"] = tabs
        snapshot["active_browser_tab_id"] = active_tab_id
        with sqlite3.connect(tmp_path / "jobos.db") as connection:
            connection.execute(
                "INSERT INTO workspace_snapshots(device_id, revision, snapshot_json) "
                "VALUES (?, ?, ?)",
                ("primary-device", 3, json.dumps(snapshot)),
            )

        response = client.get("/v1/workspace", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["browser_repair_reasons"] == expected_reasons
    assert body["repaired_browser"] is True
    assert len(body["browser_tabs"]) == 1
    if "protected_title" in expected_reasons:
        assert body["browser_tabs"][0]["title"] == "Protected page"


def test_workspace_rejects_capability_session_and_signed_url_variants(tmp_path):
    facade = FakeJobHunterFacade()
    sensitive_parameters = [
        "ticket",
        "assertion",
        "sig",
        "sessionid",
        "oauth_verifier",
        "X-Amz-Credential",
        "X-Amz-Signature",
        "X-Goog-Signature",
    ]

    with make_client(tmp_path, facade) as client:
        for index, parameter in enumerate(sensitive_parameters):
            body = client.get("/v1/workspace", headers=auth_headers()).json()
            body.pop("repaired_presets")
            body.pop("repaired_browser")
            body.pop("browser_repair_reasons")
            body.update(
                {
                    "origin": "user",
                    "idempotency_key": f"workspace-sensitive-variant-{index}",
                    "browser_tabs": [
                        {
                            "tab_id": "unsafe",
                            "url": f"https://example.com/download?view=safe&{parameter}=secret",
                            "title": "Unsafe",
                        }
                    ],
                    "active_browser_tab_id": "unsafe",
                }
            )
            response = client.put("/v1/workspace", headers=auth_headers(), json=body)
            assert response.status_code == 422, parameter


@pytest.mark.parametrize("metadata_field", ["url", "favicon_url"])
@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://example.com/jobs?api_key=secret&view=safe",
        "https://example.com/jobs?SAMLart=secret&view=safe",
        "https://example.com/jobs?authorization-code=secret&view=safe",
        "https://example.com/jobs?code_verifier=secret&view=safe",
        "https://example.com/jobs?PHPSESSID=secret&view=safe",
        "https://example.com/jobs?api%5Fkey=one&api%5Fkey=two&view=safe",
        "https://example.com/jobs;jsessionid=secret/opening?view=safe",
        "https://example.com/jobs%3BJSESSIONID%3Dsecret/opening?view=safe",
        "https://example.com/jobs%253Bjsessionid%253Dsecret/opening?view=safe",
    ],
)
def test_workspace_rejects_remaining_browser_credential_carriers(
    tmp_path, metadata_field, unsafe_url
):
    facade = FakeJobHunterFacade()
    with make_client(tmp_path, facade) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        body.pop("repaired_presets")
        body.pop("repaired_browser")
        body.pop("browser_repair_reasons")
        tab = {
            "tab_id": "unsafe",
            "url": "https://example.com/jobs?view=safe",
            "title": "Unsafe",
        }
        tab[metadata_field] = unsafe_url
        body.update(
            {
                "origin": "user",
                "idempotency_key": (
                    f"workspace-final-carrier-{metadata_field}-{abs(hash(unsafe_url))}"
                ),
                "browser_tabs": [tab],
                "active_browser_tab_id": "unsafe",
            }
        )
        response = client.put("/v1/workspace", headers=auth_headers(), json=body)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "unsafe_title",
    [fixture["title"] for fixture in TITLE_POLICY_FIXTURES if fixture["unsafe"]],
)
def test_workspace_rejects_credential_bearing_remote_page_titles(tmp_path, unsafe_title):
    facade = FakeJobHunterFacade()
    with make_client(tmp_path, facade) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        body.pop("repaired_presets")
        body.pop("repaired_browser")
        body.pop("browser_repair_reasons")
        body.update(
            {
                "origin": "user",
                "idempotency_key": "workspace-unsafe-title",
                "browser_tabs": [
                    {
                        "tab_id": "unsafe-title",
                        "url": "https://example.com/account?view=safe",
                        "title": unsafe_title,
                    }
                ],
                "active_browser_tab_id": "unsafe-title",
            }
        )
        response = client.put("/v1/workspace", headers=auth_headers(), json=body)

    assert response.status_code == 422


def test_workspace_preserves_safe_ordinary_remote_page_titles(tmp_path):
    facade = FakeJobHunterFacade()
    with make_client(tmp_path, facade) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        body.pop("repaired_presets")
        body.pop("repaired_browser")
        body.pop("browser_repair_reasons")
        body.update(
            {
                "origin": "user",
                "idempotency_key": "workspace-safe-title",
                "browser_tabs": [
                    {
                        "tab_id": "safe-title",
                        "url": "https://example.com/jobs?view=safe",
                        "title": "Planning Session: Q3",
                    }
                ],
                "active_browser_tab_id": "safe-title",
            }
        )
        response = client.put("/v1/workspace", headers=auth_headers(), json=body)

    assert response.status_code == 200
    assert response.json()["browser_tabs"][0]["title"] == "Planning Session: Q3"


def test_workspace_preserves_ordinary_browser_query_parameters(tmp_path):
    facade = FakeJobHunterFacade()
    with make_client(tmp_path, facade) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        body.pop("repaired_presets")
        body.pop("repaired_browser")
        body.pop("browser_repair_reasons")
        body.update(
            {
                "origin": "user",
                "idempotency_key": "workspace-safe-query-1",
                "browser_tabs": [
                    {
                        "tab_id": "safe",
                        "url": "https://example.com/jobs?page=2&view=compact&utm_source=jobos",
                        "title": "Safe",
                    }
                ],
                "active_browser_tab_id": "safe",
            }
        )
        response = client.put("/v1/workspace", headers=auth_headers(), json=body)

    assert response.status_code == 200
    assert response.json()["browser_tabs"][0]["url"].endswith(
        "page=2&view=compact&utm_source=jobos"
    )


def test_layout_save_cannot_overwrite_a_newer_user_or_mcp_selection(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]

    with make_client(tmp_path, facade) as client:
        conversation_id = client.get(
            "/v1/conversations/current", headers=auth_headers()
        ).json()["conversation_id"]
        client.put(
            f"/v1/conversations/{conversation_id}/workspace/job",
            headers=auth_headers(),
            json={"job_id": "job-0", "origin": "user"},
        )
        stale_layout = client.get("/v1/workspace", headers=auth_headers()).json()
        stale_layout.pop("repaired_presets")
        stale_layout.pop("repaired_browser")
        stale_layout.pop("browser_repair_reasons")
        stale_layout.update({"origin": "user", "idempotency_key": "workspace-selection-race-1"})
        client.put(
            f"/v1/conversations/{conversation_id}/workspace/job",
            headers=auth_headers(),
            json={"job_id": "job-1", "origin": "mcp"},
        )
        stale_layout["selected_preset"] = "research"
        saved = client.put("/v1/workspace", headers=auth_headers(), json=stale_layout)
        job_state = client.get(
            f"/v1/workspace/jobs?conversation_id={conversation_id}", headers=auth_headers()
        )

    assert saved.status_code == 200
    assert saved.json()["selected_job_id"] is None
    assert job_state.json()["selected_job_id"] == "job-1"


def test_workspace_get_repairs_non_scalar_layout_values_without_losing_valid_state(
    tmp_path,
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]

    with make_client(tmp_path, facade) as client:
        snapshot = client.get("/v1/workspace", headers=auth_headers()).json()
        snapshot.pop("revision")
        snapshot.pop("repaired_presets")
        snapshot["selected_preset"] = {"bad": "value"}
        snapshot["active_center_surface"] = "browser"
        snapshot["layouts"]["research"] = {
            "order": ["center", "jobs", "agent"],
            "widths": {"jobs": 333, "center": 811, "agent": 377},
            "collapsed": ["agent"],
        }
        snapshot["layouts"]["review"]["collapsed"] = [{"bad": "value"}]
        snapshot["layouts"]["agent-focus"] = {
            "order": ["agent", "center", "jobs"],
            "widths": {"jobs": 245, "center": 465, "agent": 721},
            "collapsed": ["jobs"],
        }
        with sqlite3.connect(tmp_path / "jobos.db") as connection:
            connection.execute(
                """
                INSERT INTO workspace_snapshots(device_id, revision, snapshot_json)
                VALUES ('primary-device', 7, ?)
                """,
                (json.dumps(snapshot),),
            )

        restored = client.get("/v1/workspace", headers=auth_headers())

    assert restored.status_code == 200
    body = restored.json()
    assert body["revision"] == 7
    assert body["repaired_presets"] == ["review"]
    assert body["selected_preset"] == "review"
    assert body["selected_job_id"] is None
    assert body["active_center_surface"] == "browser"
    assert body["layouts"]["research"] == snapshot["layouts"]["research"]
    assert body["layouts"]["agent-focus"] == snapshot["layouts"]["agent-focus"]
    assert body["layouts"]["review"] == {
        "order": ["jobs", "center", "agent"],
        "widths": {"jobs": 280, "center": 700, "agent": 380},
        "collapsed": [],
    }


def test_registered_pdf_is_discoverable_and_streamed_with_trust_metadata(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) trusted resume fixture"))
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        refreshed = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        )
        artifact = refreshed.json()["artifacts"][0]
        streamed = client.get(
            f"/v1/artifacts/{artifact['artifact_id']}/content", headers=auth_headers()
        )

    assert refreshed.status_code == 200
    assert artifact["job_id"] == "job-0"
    assert artifact["is_current"] is True
    assert artifact["is_last_successful"] is True
    assert artifact["preview_available"] is True
    assert streamed.status_code == 200
    assert streamed.content == pdf.read_bytes()
    assert streamed.headers["content-type"] == "application/pdf"
    assert streamed.headers["x-artifact-revision"] == "render-1"
    assert streamed.headers["x-source-revision"] == "source-1"
    assert streamed.headers["x-content-sha256"] == sha256(pdf.read_bytes()).hexdigest()
    assert streamed.headers["content-disposition"].startswith("inline;")


def test_trusted_mcp_can_publish_paired_pdf_and_docx_into_one_logical_revision(
    tmp_path, minimal_docx
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    source = b"# Tailored cover letter\n\nDear hiring team"

    def payload(filename, content, key):
        return {
            "document_key": "cover_letter",
            "document_label": "Cover Letter",
            "source_filename": "cover-letter.md",
            "source_base64": base64.b64encode(source).decode("ascii"),
            "artifact_filename": filename,
            "artifact_base64": base64.b64encode(content).decode("ascii"),
            "origin": "mcp",
            "idempotency_key": key,
        }

    with make_client(tmp_path, facade) as client:
        pdf = client.post(
            "/v1/jobs/job-0/artifacts/publish",
            headers=auth_headers(),
            json=payload(
                "cover-letter.pdf",
                build_minimal_pdf("(FAKE) letter"),
                "publish-pdf",
            ),
        )
        docx_payload = payload(
            "cover-letter.docx", minimal_docx("cover letter"), "publish-docx"
        )
        docx = client.post(
            "/v1/jobs/job-0/artifacts/publish",
            headers=auth_headers(),
            json=docx_payload,
        )
        replay = client.post(
            "/v1/jobs/job-0/artifacts/publish",
            headers=auth_headers(),
            json=docx_payload,
        )
        untrusted = client.post(
            "/v1/jobs/job-0/artifacts/publish",
            headers={"Authorization": "Bearer test-device-token"},
            json=payload("blocked.docx", minimal_docx("blocked"), "publish-blocked"),
        )

    assert pdf.status_code == 200
    assert docx.status_code == 200
    assert replay.status_code == 200
    assert untrusted.status_code == 403
    artifacts = docx.json()["artifacts"]
    assert len(artifacts) == 2
    assert {artifact["media_type"] for artifact in artifacts} == {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert {artifact["source_revision"] for artifact in artifacts} == {sha256(source).hexdigest()}
    assert replay.json() == docx.json()
    assert facade.publish_calls == []
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        stored_paths = [
            Path(row[0])
            for row in connection.execute(
                "SELECT canonical_path FROM document_artifacts ORDER BY render_sequence"
            ).fetchall()
        ]
    assert all(path.is_relative_to(tmp_path / "artifacts") for path in stored_paths)
    assert all("agent-publications" in path.parts for path in stored_paths)


def test_editable_document_publish_pairs_docx_pdf_marks_revision_and_replays(
    tmp_path, minimal_docx
):
    facade = FakeJobHunterFacade()
    client = make_client(tmp_path, facade)
    client.__enter__()
    created = client.post(
        "/v1/jobs/job-0/editable-documents",
        headers=auth_headers(),
        json={
            "mode": "blank",
            "document_key": "resume",
            "idempotency_key": "create-publishable-resume",
        },
    )
    assert created.status_code == 201
    document = created.json()
    docx = minimal_docx("JobOS editable publication")
    pdf = build_minimal_pdf("(FAKE) JobOS PDF")
    payload = {
        "expected_revision": document["revision"],
        "docx_filename": "Resume-r1.docx",
        "docx_base64": base64.b64encode(docx).decode(),
        "docx_sha256": sha256(docx).hexdigest(),
        "pdf_filename": "Resume-r1.pdf",
        "pdf_base64": base64.b64encode(pdf).decode(),
        "pdf_sha256": sha256(pdf).hexdigest(),
        "idempotency_key": "publish-editable-r1",
    }

    published = client.post(
        f"/v1/editable-documents/{document['document_id']}/publish",
        headers=auth_headers(),
        json=payload,
    )
    assert published.status_code == 200, published.text
    assert published.json()["published_revision"] == 1
    assert facade.publish_calls == []
    listed = client.get("/v1/jobs/job-0/artifacts", headers=auth_headers()).json()
    assert {row["media_type"] for row in listed["artifacts"]} == {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert {row["sha256"] for row in listed["artifacts"]} == {
        sha256(docx).hexdigest(),
        sha256(pdf).hexdigest(),
    }
    downloaded = {
        row["media_type"]: client.get(
            f"/v1/artifacts/{row['artifact_id']}/download", headers=auth_headers()
        )
        for row in listed["artifacts"]
    }
    assert downloaded["application/pdf"].content == pdf
    assert (
        downloaded[
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ].content
        == docx
    )
    assert all(
        response.headers["x-content-sha256"] == sha256(response.content).hexdigest()
        for response in downloaded.values()
    )
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        linked_rows = connection.execute(
            """
            SELECT editable_document_id, editable_document_revision, canonical_path
            FROM document_artifacts
            """
        ).fetchall()
    assert [(row[0], row[1]) for row in linked_rows] == [
        (document["document_id"], document["revision"])
    ] * 2
    assert all(Path(row[2]).is_relative_to(tmp_path / "artifacts") for row in linked_rows)
    snapshots = client.get(
        f"/v1/editable-documents/{document['document_id']}/snapshots",
        headers=auth_headers(),
    )
    assert snapshots.status_code == 200
    assert snapshots.json()["snapshots"][0]["reason"] == "before_publish"

    advanced = client.put(
        f"/v1/editable-documents/{document['document_id']}",
        headers=auth_headers(),
        json={
            "base_revision": published.json()["revision"],
            "content": published.json()["content"],
            "settings": published.json()["settings"],
            "comments": published.json()["comments"],
            "idempotency_key": "advance-after-publication",
        },
    )
    assert advanced.status_code == 200
    assert advanced.json()["revision"] == published.json()["revision"] + 1

    replay = client.post(
        f"/v1/editable-documents/{document['document_id']}/publish",
        headers=auth_headers(),
        json=payload,
    )
    assert replay.status_code == 200
    assert replay.json() == published.json()
    assert facade.publish_calls == []
    client.__exit__(None, None, None)


def test_authenticated_desktop_can_publish_without_an_mcp_token(tmp_path, minimal_docx):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    source = minimal_docx("canonical DOCX")
    payload = {
        "document_key": "resume",
        "document_label": "Resume",
        "source_filename": "resume-r7.docx",
        "source_base64": base64.b64encode(source).decode("ascii"),
        "artifact_filename": "resume-r7.pdf",
        "artifact_base64": base64.b64encode(
            build_minimal_pdf("(FAKE) revision seven")
        ).decode("ascii"),
        "origin": "user",
        "idempotency_key": "desktop-publish-resume-r7-pdf",
    }

    with make_client(tmp_path, facade) as client:
        response = client.post(
            "/v1/jobs/job-0/artifacts/publish",
            headers={"Authorization": "Bearer test-device-token"},
            json=payload,
        )

    assert response.status_code == 200
    assert response.json()["artifacts"][0]["source_revision"] == sha256(source).hexdigest()


def test_newer_success_and_failed_render_preserve_last_successful_preview(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    first = tmp_path / "resume-1.pdf"
    second = tmp_path / "resume-2.pdf"
    first.write_bytes(build_minimal_pdf("(FAKE) revision one"))
    second.write_bytes(build_minimal_pdf("(FAKE) revision two"))
    facade.artifacts["job-0"] = [artifact_metadata(first)]

    with make_client(tmp_path, facade) as client:
        initial = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        facade.artifacts["job-0"] = [
            artifact_metadata(first, sequence=1),
            artifact_metadata(
                second, source="source-2", revision="render-2", sequence=2
            ),
        ]
        newer = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        facade.artifacts["job-0"].append(
            {
                "job_id": "job-0",
                "source_revision": "source-3",
                "artifact_revision": "render-3",
                "media_type": "application/pdf",
                "render_status": "failed",
                "render_sequence": 3,
                "failure_message": "PDF render failed in fixture",
            }
        )
        failed = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()

    assert newer["current_artifact_id"] != initial["current_artifact_id"]
    assert newer["current_artifact_id"] == newer["last_successful_artifact_id"]
    assert failed["current_artifact_id"] != failed["last_successful_artifact_id"]
    current = next(
        item for item in failed["artifacts"] if item["is_current"]
    )
    retained = next(
        item for item in failed["artifacts"] if item["is_last_successful"]
    )
    assert current["render_status"] == "failed"
    assert current["failure_message"] == "PDF render failed in fixture"
    assert retained["artifact_revision"] == "render-2"
    assert retained["preview_available"] is True


def test_approval_persists_the_exact_successful_artifact_for_the_job(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) approved resume"))
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        registered = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        artifact_id = registered["last_successful_artifact_id"]
        approved = client.post(
            f"/v1/jobs/job-0/artifacts/{artifact_id}/approve",
            headers=auth_headers(),
            json={
                "origin": "user",
                "idempotency_key": "approve-artifact-1",
            },
        )
        agent_approval = client.post(
            f"/v1/jobs/job-0/artifacts/{artifact_id}/approve",
            headers=auth_headers(),
            json={
                "origin": "mcp",
                "idempotency_key": "agent-cannot-self-approve-1",
            },
        )

    with make_client(tmp_path, facade) as restarted:
        restored = restarted.get(
            "/v1/jobs/job-0/artifacts", headers=auth_headers()
        ).json()

    assert approved.status_code == 200
    assert agent_approval.status_code == 403
    assert "only the authenticated user" in agent_approval.json()["detail"]
    assert approved.json()["approved_artifact_id"] == artifact_id
    approved_artifact = next(
        item for item in approved.json()["artifacts"] if item["artifact_id"] == artifact_id
    )
    assert approved_artifact["is_approved"] is True
    assert restored["approved_artifact_id"] == artifact_id


@pytest.mark.parametrize("damage", ["tampered", "deleted"])
def test_approval_rejects_artifact_bytes_that_no_longer_match_registration(
    tmp_path, damage
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) trusted resume"))
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        registered = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        artifact_id = registered["last_successful_artifact_id"]
        if damage == "tampered":
            pdf.write_bytes(build_minimal_pdf("(FAKE) tampered resume"))
        else:
            pdf.unlink()
        rejected = client.post(
            f"/v1/jobs/job-0/artifacts/{artifact_id}/approve",
            headers=auth_headers(),
            json={
                "origin": "user",
                "idempotency_key": f"approve-{damage}-artifact",
            },
        )
        restored = client.get(
            "/v1/jobs/job-0/artifacts", headers=auth_headers()
        ).json()

    assert rejected.status_code == 503
    assert rejected.json()["detail"] == "Local artifact storage is unavailable"
    assert restored["approved_artifact_id"] is None


def test_failed_or_cross_job_artifact_cannot_be_approved(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]
    failed = {
        "job_id": "job-0",
        "source_revision": "source-failed",
        "artifact_revision": "render-failed",
        "media_type": "application/pdf",
        "render_status": "failed",
        "render_sequence": 1,
        "failure_message": "fixture failed",
    }
    facade.artifacts["job-0"] = [failed]
    pdf = tmp_path / "other.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) other job"))
    facade.artifacts["job-1"] = [artifact_metadata(pdf, job_id="job-1")]

    with make_client(tmp_path, facade) as client:
        failed_id = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()["current_artifact_id"]
        other_id = client.post(
            "/v1/jobs/job-1/artifacts/refresh", headers=auth_headers()
        ).json()["current_artifact_id"]
        failed_approval = client.post(
            f"/v1/jobs/job-0/artifacts/{failed_id}/approve",
            headers=auth_headers(),
            json={"origin": "user", "idempotency_key": "approve-failed"},
        )
        cross_job = client.post(
            f"/v1/jobs/job-0/artifacts/{other_id}/approve",
            headers=auth_headers(),
            json={"origin": "user", "idempotency_key": "approve-cross-job"},
        )

    assert failed_approval.status_code == 409
    assert cross_job.status_code == 409


def test_refresh_uses_latest_checksum_when_provider_reuses_an_artifact_path(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) pre-fix resume"))
    stale = artifact_metadata(pdf, source="source-old", revision="render-old", sequence=1)
    pdf.write_bytes(build_minimal_pdf("(FAKE) corrected resume"))
    corrected = artifact_metadata(
        pdf, source="source-corrected", revision="render-corrected", sequence=2
    )
    facade.artifacts["job-0"] = [stale, corrected]

    with make_client(tmp_path, facade) as client:
        response = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["artifacts"]) == 1
    assert body["artifacts"][0]["artifact_revision"] == "render-corrected"
    assert body["artifacts"][0]["source_revision"] == "source-corrected"
    assert body["artifacts"][0]["is_current"] is True


def test_refresh_clears_approval_for_superseded_mutable_path_bytes(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) approved pre-fix resume"))
    stale = artifact_metadata(pdf, source="source-old", revision="render-old", sequence=1)
    facade.artifacts["job-0"] = [stale]

    with make_client(tmp_path, facade) as client:
        initial = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        approved_id = initial["last_successful_artifact_id"]
        approved = client.post(
            f"/v1/jobs/job-0/artifacts/{approved_id}/approve",
            headers=auth_headers(),
            json={"origin": "user", "idempotency_key": "approve-pre-fix-resume"},
        )
        pdf.write_bytes(build_minimal_pdf("(FAKE) corrected resume"))
        facade.artifacts["job-0"].append(
            artifact_metadata(
                pdf,
                source="source-corrected",
                revision="render-corrected",
                sequence=2,
            )
        )
        refreshed = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        )

    assert approved.status_code == 200
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["approved_artifact_id"] is None
    old = next(item for item in body["artifacts"] if item["artifact_id"] == approved_id)
    corrected = next(
        item for item in body["artifacts"] if item["artifact_revision"] == "render-corrected"
    )
    assert old["is_approved"] is False
    assert corrected["is_current"] is True


@pytest.mark.parametrize("hidden_damage", ["malformed", "cross-job"])
def test_refresh_does_not_hide_invalid_metadata_behind_path_supersession(
    tmp_path, hidden_damage
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) pre-fix resume"))
    stale = artifact_metadata(pdf, source="source-old", revision="render-old", sequence=1)
    if hidden_damage == "malformed":
        stale["render_sequence"] = -1
    else:
        stale["job_id"] = "job-other"
    pdf.write_bytes(build_minimal_pdf("(FAKE) corrected resume"))
    corrected = artifact_metadata(
        pdf, source="source-corrected", revision="render-corrected", sequence=2
    )
    facade.artifacts["job-0"] = [stale, corrected]

    with make_client(tmp_path, facade) as client:
        response = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        )
        listed = client.get("/v1/jobs/job-0/artifacts", headers=auth_headers())

    assert response.status_code == 422
    assert listed.json()["artifacts"] == []


def test_refresh_still_rejects_a_lone_artifact_with_stale_checksum(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) registered resume"))
    stale = artifact_metadata(pdf)
    pdf.write_bytes(build_minimal_pdf("(FAKE) unregistered replacement"))
    facade.artifacts["job-0"] = [stale]

    with make_client(tmp_path, facade) as client:
        response = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        )

    assert (response.status_code, response.json()["detail"]) == (
        503,
        "Local artifact storage is unavailable",
    )


def test_failed_render_stays_in_audit_without_injecting_chat_activity(tmp_path):
    class FailedRenderFacade(FakeJobHunterFacade):
        def render_resume(self, job_id, source_id, output_options):
            return {
                "job_id": job_id,
                "source_revision": "source-failed",
                "artifact_revision": "render-failed",
                "media_type": "application/pdf",
                "render_status": "failed",
                "render_sequence": 1,
                "failure_message": "fixture render failed",
            }

    with make_client(tmp_path, FailedRenderFacade()) as client:
        response = client.post(
            "/v1/jobs/job-0/artifacts/render",
            headers=auth_headers(),
            json={
                "source_id": "job-0-tailored",
                "output_format": "pdf",
                "origin": "mcp",
                "idempotency_key": "failed-render-activity",
            },
        )

    assert response.status_code == 200
    assert response.json()["artifacts"][0]["render_status"] == "failed"
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        chat_count = connection.execute(
            "SELECT COUNT(*) FROM conversation_events WHERE event_type = 'activity'"
        ).fetchone()[0]
        outcome = connection.execute(
            "SELECT outcome FROM job_events WHERE command_name = 'document.render'"
        ).fetchone()[0]
    assert chat_count == 0
    assert outcome == "failed"


@pytest.mark.parametrize("route", ["render", "register"])
@pytest.mark.parametrize("damage", ["disappeared", "symlink", "replaced"])
def test_gateway_artifact_storage_races_are_stable_503(tmp_path, route, damage):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) gateway artifact"))
    raw = {**artifact_metadata(pdf), "artifact_reference": "resume-ref"}

    class DamagingFacade(FakeJobHunterFacade):
        def successful_artifact(self):
            held = tmp_path / f"held-{route}-{damage}.pdf"
            if damage == "disappeared":
                pdf.unlink()
            elif damage == "symlink":
                pdf.rename(held)
                pdf.symlink_to(held)
            else:
                pdf.rename(held)
                pdf.write_bytes(build_minimal_pdf("(FAKE) foreign replacement"))
            return raw

        def render_resume(self, job_id, source_id, output_options):
            return self.successful_artifact()

        def register_artifact(self, job_id, artifact_reference):
            return self.successful_artifact()

    facade = DamagingFacade()
    facade.jobs = facade.jobs[:1]
    if route == "render":
        endpoint = "/v1/jobs/job-0/artifacts/render"
        payload = {
            "source_id": "job-0-tailored",
            "output_format": "pdf",
            "origin": "mcp",
            "idempotency_key": f"storage-race-{route}-{damage}",
        }
    else:
        endpoint = "/v1/jobs/job-0/artifacts/register"
        payload = {
            "artifact_reference": "resume-ref",
            "origin": "mcp",
            "idempotency_key": f"storage-race-{route}-{damage}",
        }

    with make_client(tmp_path, facade) as client:
        response = client.post(endpoint, headers=auth_headers(), json=payload)
        listed = client.get("/v1/jobs/job-0/artifacts", headers=auth_headers())

    assert (response.status_code, response.json()["detail"]) == (
        503,
        "Local artifact storage is unavailable",
    )
    assert listed.json()["artifacts"] == []


@pytest.mark.parametrize("route", ["render", "register"])
def test_gateway_artifact_trust_validation_remains_422(tmp_path, route):
    class InvalidMetadataFacade(FakeJobHunterFacade):
        def invalid_artifact(self, job_id):
            return {
                "job_id": job_id,
                "source_revision": "source-invalid",
                "artifact_revision": "render-invalid",
                "media_type": "text/plain",
                "render_status": "succeeded",
                "render_sequence": 1,
            }

        def render_resume(self, job_id, source_id, output_options):
            return self.invalid_artifact(job_id)

        def register_artifact(self, job_id, artifact_reference):
            return self.invalid_artifact(job_id)

    if route == "render":
        endpoint = "/v1/jobs/job-0/artifacts/render"
        payload = {
            "source_id": "job-0-tailored",
            "output_format": "pdf",
            "origin": "mcp",
            "idempotency_key": f"trust-validation-{route}",
        }
    else:
        endpoint = "/v1/jobs/job-0/artifacts/register"
        payload = {
            "artifact_reference": "resume-ref",
            "origin": "mcp",
            "idempotency_key": f"trust-validation-{route}",
        }

    with make_client(tmp_path, InvalidMetadataFacade()) as client:
        response = client.post(endpoint, headers=auth_headers(), json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("manifest_order", ["oldest-first", "newest-first"])
def test_facade_render_sequence_determines_current_and_last_successful(
    tmp_path, manifest_order, minimal_docx
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    older_pdf = tmp_path / "resume-old.pdf"
    last_good_docx = tmp_path / "resume-current.docx"
    older_pdf.write_bytes(build_minimal_pdf("(FAKE) older PDF"))
    last_good_docx.write_bytes(minimal_docx("last successful DOCX"))
    artifacts = [
        artifact_metadata(
            older_pdf, source="source-1", revision="render-1", sequence=1
        ),
        {
            **artifact_metadata(
                last_good_docx,
                source="source-2",
                revision="render-2",
                sequence=2,
            ),
            "media_type": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        },
        {
            "job_id": "job-0",
            "source_revision": "source-3",
            "artifact_revision": "render-3",
            "render_sequence": 3,
            "media_type": "application/pdf",
            "render_status": "failed",
            "failure_message": "newest render failed",
        },
    ]
    facade.artifacts["job-0"] = (
        artifacts if manifest_order == "oldest-first" else list(reversed(artifacts))
    )

    with make_client(tmp_path, facade) as client:
        response = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        )

    assert response.status_code == 200
    body = response.json()
    current = next(item for item in body["artifacts"] if item["is_current"])
    last_successful = next(
        item for item in body["artifacts"] if item["is_last_successful"]
    )
    assert current["artifact_revision"] == "render-3"
    assert current["render_status"] == "failed"
    assert last_successful["artifact_revision"] == "render-2"
    assert last_successful["media_type"].endswith(
        "openxmlformats-officedocument.wordprocessingml.document"
    )
    assert last_successful["preview_available"] is False


def test_duplicate_facade_render_sequences_are_rejected(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    first = tmp_path / "resume-1.pdf"
    second = tmp_path / "resume-2.pdf"
    first.write_bytes(build_minimal_pdf("(FAKE) first"))
    second.write_bytes(build_minimal_pdf("(FAKE) second"))
    facade.artifacts["job-0"] = [
        artifact_metadata(first, revision="render-1", sequence=7),
        artifact_metadata(second, revision="render-2", sequence=7),
    ]

    with make_client(tmp_path, facade) as client:
        response = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        )

    assert response.status_code == 422
    assert "sequences must be unique" in response.json()["detail"]


def test_explicit_resume_identity_does_not_duplicate_a_legacy_registered_artifact(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) legacy resume"))
    legacy = artifact_metadata(pdf)
    facade.artifacts["job-0"] = [legacy]

    with make_client(tmp_path, facade) as client:
        first = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        facade.artifacts["job-0"] = [
            {**legacy, "document_key": "resume", "document_label": "Resume"}
        ]
        second = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        artifact_id = second["artifacts"][0]["artifact_id"]
        approved = client.post(
            f"/v1/jobs/job-0/artifacts/{artifact_id}/approve",
            headers=auth_headers(),
            json={"origin": "user", "idempotency_key": "approve-legacy"},
        )
        assert approved.status_code == 200

        facade.artifacts["job-0"] = [
            {**legacy, "document_key": "cover_letter", "document_label": "Cover Letter"}
        ]
        reclassified = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()

    assert len(first["artifacts"]) == len(second["artifacts"]) == 1
    assert first["artifacts"][0]["artifact_id"] == artifact_id
    assert second["artifacts"][0]["render_sequence"] == legacy["render_sequence"]
    assert reclassified["artifacts"][0]["artifact_id"] == artifact_id
    assert reclassified["artifacts"][0]["document_key"] == "cover_letter"
    assert reclassified["artifacts"][0]["is_approved"] is False
    assert reclassified["approved_artifact_id"] is None


def test_artifact_response_hashes_and_serves_one_byte_snapshot(tmp_path, monkeypatch):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    original_bytes = build_minimal_pdf("(FAKE) trusted snapshot")
    replacement_bytes = build_minimal_pdf("(FAKE) replacement snapshot")
    pdf.write_bytes(original_bytes)
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        artifact = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()["artifacts"][0]
        original_read = artifact_repository_module.os.read
        calls = 0
        held = tmp_path / "held-resume.pdf"

        def replacing_read(descriptor, maximum):
            nonlocal calls
            calls += 1
            content = original_read(descriptor, maximum)
            if calls == 1:
                pdf.rename(held)
                pdf.write_bytes(replacement_bytes)
            return content

        monkeypatch.setattr(artifact_repository_module.os, "read", replacing_read)
        streamed = client.get(
            f"/v1/artifacts/{artifact['artifact_id']}/content",
            headers=auth_headers(),
        )

    assert calls == 2
    assert streamed.status_code == 200
    assert streamed.content == original_bytes
    assert streamed.headers["x-content-sha256"] == sha256(original_bytes).hexdigest()
    assert streamed.headers["x-artifact-revision"] == "render-1"


@pytest.mark.parametrize("attack", ["root_escape", "wrong_media", "hash_mismatch"])
def test_artifact_refresh_rejects_root_media_and_metadata_mismatches(tmp_path, attack):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    root_pdf = tmp_path / "resume.pdf"
    root_pdf.write_bytes(build_minimal_pdf("(FAKE) trusted"))
    raw = artifact_metadata(root_pdf)
    if attack == "root_escape":
        outside = tmp_path.parent / "outside-jobos-artifact.pdf"
        outside.write_bytes(build_minimal_pdf("(FAKE) outside"))
        raw = artifact_metadata(outside)
    elif attack == "wrong_media":
        raw["media_type"] = "text/plain"
    else:
        raw["sha256"] = "0" * 64
    facade.artifacts["job-0"] = [raw]

    with make_client(tmp_path, facade) as client:
        response = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        )
        listed = client.get("/v1/jobs/job-0/artifacts", headers=auth_headers())

    assert response.status_code == (422 if attack == "wrong_media" else 503)
    assert listed.json()["artifacts"] == []
    if attack == "root_escape":
        outside.unlink(missing_ok=True)


def test_unregistered_ids_paths_and_docx_preview_are_rejected(tmp_path, minimal_docx):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    docx = tmp_path / "resume.docx"
    docx.write_bytes(minimal_docx("preview fixture DOCX"))
    facade.artifacts["job-0"] = [
        {
            **artifact_metadata(docx),
            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    ]

    with make_client(tmp_path, facade) as client:
        artifact = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()["artifacts"][0]
        docx_preview = client.get(
            f"/v1/artifacts/{artifact['artifact_id']}/content", headers=auth_headers()
        )
        docx_download = client.get(
            f"/v1/artifacts/{artifact['artifact_id']}/download", headers=auth_headers()
        )
        unregistered = client.get(
            "/v1/artifacts/art_AAAAAAAAAAAAAAAA/content", headers=auth_headers()
        )
        arbitrary_path = client.get(
            "/v1/artifacts/..%2F..%2Fetc%2Fpasswd/content", headers=auth_headers()
        )

    assert artifact["preview_available"] is False
    assert docx_preview.status_code == 415
    assert docx_download.status_code == 200
    assert unregistered.status_code == 404
    assert arbitrary_path.status_code in {404, 422}


def test_multiple_document_formats_keep_identity_and_logical_document_approval(
    tmp_path, minimal_docx
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    resume_pdf = tmp_path / "resume.pdf"
    resume_docx = tmp_path / "resume.docx"
    cover_pdf = tmp_path / "cover-letter.pdf"
    cover_docx = tmp_path / "cover-letter.docx"
    resume_pdf.write_bytes(build_minimal_pdf("(FAKE) resume"))
    resume_docx.write_bytes(minimal_docx("resume DOCX"))
    cover_pdf.write_bytes(build_minimal_pdf("(FAKE) cover letter"))
    cover_docx.write_bytes(minimal_docx("cover letter DOCX"))
    docx_media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    facade.artifacts["job-0"] = [
        {**artifact_metadata(resume_pdf, source="resume-source", sequence=1),
         "document_key": "resume", "document_label": "Resume"},
        {
            **artifact_metadata(
                resume_docx, source="resume-source", revision="resume-docx", sequence=2
            ),
            "document_key": "resume",
            "document_label": "Resume",
            "media_type": docx_media,
        },
        {**artifact_metadata(cover_pdf, source="cover-source", revision="cover-pdf", sequence=3),
         "document_key": "cover_letter", "document_label": "Cover Letter"},
        {
            **artifact_metadata(
                cover_docx, source="cover-source", revision="cover-docx", sequence=4
            ),
            "document_key": "cover_letter",
            "document_label": "Cover Letter",
            "media_type": docx_media,
        },
    ]

    with make_client(tmp_path, facade) as client:
        body = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        resume = [item for item in body["artifacts"] if item["document_key"] == "resume"]
        cover = [item for item in body["artifacts"] if item["document_key"] == "cover_letter"]
        resume_pdf_artifact = next(
            item for item in resume if item["media_type"] == "application/pdf"
        )
        resume_approval = client.post(
            f"/v1/jobs/job-0/artifacts/{resume_pdf_artifact['artifact_id']}/approve",
            headers=auth_headers(),
        )
        cover_approval = client.post(
            f"/v1/jobs/job-0/artifacts/{cover[0]['artifact_id']}/approve",
            headers=auth_headers(),
        )

    assert {item["media_type"] for item in resume} == {"application/pdf", docx_media}
    assert {item["media_type"] for item in cover} == {"application/pdf", docx_media}
    assert {item["source_revision"] for item in resume} == {"resume-source"}
    assert {item["source_revision"] for item in cover} == {"cover-source"}
    assert {item["document_label"] for item in cover} == {"Cover Letter"}
    assert {item["render_sequence"] for item in body["artifacts"]} == {1, 2, 3, 4}
    assert resume_approval.status_code == 200
    assert cover_approval.status_code == 200
    assert all(item["is_approved"] for item in cover_approval.json()["artifacts"] if (
        item["document_key"] == "cover_letter" and item["source_revision"] == "cover-source"
    ))


def test_workspace_restores_only_an_active_artifact_owned_by_the_selected_job(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) owned resume"))
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        conversation_id = client.get(
            "/v1/conversations/current", headers=auth_headers()
        ).json()["conversation_id"]
        client.put(
            f"/v1/conversations/{conversation_id}/workspace/job",
            headers=auth_headers(),
            json={"job_id": "job-0", "origin": "user"},
        )
        artifact_id = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()["current_artifact_id"]
        saved = client.put(
            f"/v1/conversations/{conversation_id}/workspace/document",
            headers=auth_headers(),
            json={
                "origin": "user",
                "idempotency_key": "document-view-restore-1",
                "active_artifact_id": artifact_id,
                "active_artifact_page": 2,
                "active_artifact_zoom": 1.4,
            },
        )
        restored = client.get(
            f"/v1/workspace?conversation_id={conversation_id}", headers=auth_headers()
        )

    assert saved.status_code == 200
    assert restored.json()["active_artifact_id"] == artifact_id
    assert restored.json()["active_artifact_page"] == 2
    assert restored.json()["active_artifact_zoom"] == 1.4


def test_workspace_rejects_cross_job_stale_and_unselected_active_artifacts(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) job zero resume"))
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        conversation_id = client.get(
            "/v1/conversations/current", headers=auth_headers()
        ).json()["conversation_id"]
        artifact_id = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()["current_artifact_id"]
        unselected = client.put(
            f"/v1/conversations/{conversation_id}/workspace/document",
            headers=auth_headers(),
            json={
                "origin": "user",
                "idempotency_key": "unselected-artifact",
                "active_artifact_id": artifact_id,
            },
        )
        client.put(
            f"/v1/conversations/{conversation_id}/workspace/job",
            headers=auth_headers(),
            json={"job_id": "job-1", "origin": "user"},
        )
        mismatch = client.put(
            f"/v1/conversations/{conversation_id}/workspace/document",
            headers=auth_headers(),
            json={
                "origin": "user",
                "idempotency_key": "cross-job-artifact",
                "active_artifact_id": artifact_id,
            },
        )
        stale = client.put(
            f"/v1/conversations/{conversation_id}/workspace/document",
            headers=auth_headers(),
            json={
                "origin": "user",
                "idempotency_key": "stale-artifact",
                "active_artifact_id": "art_AAAAAAAAAAAAAAAA",
            },
        )

    assert unselected.status_code in {409, 422}
    assert mismatch.status_code in {409, 422}
    assert stale.status_code in {409, 422}


@pytest.mark.parametrize(
    ("method", "endpoint", "payload", "source_event_id"),
    [
        (
            "put",
            "/v1/jobs/job-0/status",
            {"target_status": "reviewed", "origin": "mcp", "idempotency_key": "repair-status"},
            mutation_activity_source_id(
                actor_id="primary-device",
                target_resource="jobs/job-0",
                command_name="job.update_status",
                idempotency_key="repair-status",
            ),
        ),
        (
            "post",
            "/v1/jobs/job-0/artifacts/register",
            {
                "artifact_reference": "resume-ref",
                "origin": "mcp",
                "idempotency_key": "repair-register",
            },
            mutation_activity_source_id(
                actor_id="primary-device",
                target_resource="jobs/job-0/artifacts",
                command_name="document.register",
                idempotency_key="repair-register",
            ),
        ),
    ],
)
def test_mutation_replay_does_not_recreate_agent_chat_activity(
    tmp_path, method, endpoint, payload, source_event_id
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) chronology"))
    facade.artifacts["job-0"] = [
        {**artifact_metadata(pdf), "artifact_reference": "resume-ref"}
    ]

    with make_client(tmp_path, facade) as client:
        request = getattr(client, method)
        first = request(endpoint, headers=auth_headers(), json=payload)
        with sqlite3.connect(tmp_path / "jobos.db") as connection:
            connection.execute(
                "DELETE FROM conversation_events WHERE source_event_id = ?",
                (source_event_id,),
            )
        replay = request(endpoint, headers=auth_headers(), json=payload)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM conversation_events WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0]
    assert count == 0


def test_workspace_save_replay_does_not_recreate_agent_chat_activity(tmp_path):
    with make_client(tmp_path) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        for key in ("repaired_presets", "repaired_browser", "browser_repair_reasons"):
            body.pop(key)
        body.update({"origin": "mcp", "idempotency_key": "repair-workspace"})
        first = client.put("/v1/workspace", headers=auth_headers(), json=body)
        source_event_id = "workspace:primary-device:repair-workspace"
        with sqlite3.connect(tmp_path / "jobos.db") as connection:
            connection.execute(
                "DELETE FROM conversation_events WHERE source_event_id = ?",
                (source_event_id,),
            )
        replay = client.put("/v1/workspace", headers=auth_headers(), json=body)

    assert replay.json() == first.json()
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_events WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0] == 0


def test_concurrent_identical_artifact_registration_executes_the_facade_once(tmp_path):
    class BlockingFacade(FakeJobHunterFacade):
        def __init__(self):
            super().__init__()
            self.side_effect_started = threading.Event()
            self.second_request_started = threading.Event()
            self.release_side_effect = threading.Event()
            self.register_calls = 0

        def inspect_job(self, job_id):
            if self.side_effect_started.is_set() and not self.release_side_effect.is_set():
                self.second_request_started.set()
            return super().inspect_job(job_id)

        def register_artifact(self, job_id, artifact_reference):
            self.register_calls += 1
            self.side_effect_started.set()
            assert self.release_side_effect.wait(timeout=2)
            return super().register_artifact(job_id, artifact_reference)

    facade = BlockingFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(build_minimal_pdf("(FAKE) concurrent"))
    facade.artifacts["job-0"] = [
        {**artifact_metadata(pdf), "artifact_reference": "resume-ref"}
    ]
    payload = {
        "artifact_reference": "resume-ref",
        "origin": "mcp",
        "idempotency_key": "concurrent-register",
    }

    with make_client(tmp_path, facade) as client, ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            client.post,
            "/v1/jobs/job-0/artifacts/register",
            headers=auth_headers(),
            json=payload,
        )
        assert facade.side_effect_started.wait(timeout=2)
        second = executor.submit(
            client.post,
            "/v1/jobs/job-0/artifacts/register",
            headers=auth_headers(),
            json=payload,
        )
        assert facade.second_request_started.wait(timeout=2)
        facade.release_side_effect.set()
        responses = [first.result(), second.result()]

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert facade.register_calls == 1



def test_editable_document_routes_cover_crud_conflict_snapshots_operations_and_stubs(tmp_path):
    with make_client(tmp_path) as client:
        created = client.post(
            "/v1/jobs/job-0/editable-documents",
            headers=auth_headers(),
            json={
                "mode": "blank",
                "document_key": "references",
                "idempotency_key": "draft-create-1",
            },
        )
        replay = client.post(
            "/v1/jobs/job-0/editable-documents",
            headers=auth_headers(),
            json={
                "mode": "blank",
                "document_key": "references",
                "idempotency_key": "draft-create-1",
            },
        )
        assert created.status_code == replay.status_code == 201
        document = created.json()
        assert replay.json() == document
        assert document["document_label"] == "References"
        document_id = document["document_id"]

        listing = client.get(
            "/v1/jobs/job-0/editable-documents", headers=auth_headers()
        )
        outline = client.get(
            "/v1/jobs/job-0/editable-document-outlines/references",
            headers=auth_headers(),
            params={"origin": "mcp", "idempotency_key": "draft-read-1"},
        )
        assert listing.status_code == outline.status_code == 200
        assert "content" not in listing.json()["documents"][0]
        assert "content" not in outline.json()
        assert outline.json()["outline"]

        saved = client.put(
            f"/v1/editable-documents/{document_id}",
            headers=auth_headers(),
            json={
                "base_revision": 1,
                "content": document["content"],
                "settings": document["settings"],
                "comments": [],
                "idempotency_key": "draft-save-1",
            },
        )
        stale = client.put(
            f"/v1/editable-documents/{document_id}",
            headers=auth_headers(),
            json={
                "base_revision": 1,
                "content": document["content"],
                "settings": document["settings"],
                "comments": [],
                "idempotency_key": "draft-save-stale",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 2
        assert stale.status_code == 409
        assert stale.json()["detail"]["current"]["revision"] == 2

        snapshot = client.post(
            f"/v1/editable-documents/{document_id}/snapshots",
            headers=auth_headers(),
            json={
                "base_revision": 2,
                "reason": "manual",
                "label": "Before agent",
                "origin": "mcp",
                "idempotency_key": "draft-snapshot-1",
            },
        )
        assert snapshot.status_code == 201
        assert snapshot.json()["actor"] == "jobhunter"

        target = saved.json()["content"]["content"][1]["content"][0]
        applied = client.post(
            f"/v1/editable-documents/{document_id}/operations",
            headers=auth_headers(),
            json={
                "base_revision": 2,
                "origin": "mcp",
                "idempotency_key": "draft-apply-1",
                "operations": [
                    {
                        "type": "replace_block_text",
                        "block_id": target["attrs"]["jobosId"],
                        "expected_text": "",
                        "replacement_text": "Alex Example — alex@example.com",
                    }
                ],
            },
        )
        assert applied.status_code == 200
        assert applied.json()["document"]["revision"] == 3
        assert applied.json()["snapshot_id"].startswith("dsnap_")

        restored = client.post(
            f"/v1/editable-documents/{document_id}/snapshots/{snapshot.json()['snapshot_id']}/restore",
            headers=auth_headers(),
            json={"base_revision": 3, "idempotency_key": "draft-restore-1"},
        )
        assert restored.status_code == 200
        assert restored.json()["revision"] == 4

        import_source = {
            "mode": "import_registered_artifact",
            "document_key": "references",
            "source_artifact_id": "art_1234567890abcdef",
            "content": document["content"],
            "settings": document["settings"],
            "import_report": {
                "source_filename": "references.docx",
                "imported_at": "2026-08-07T00:00:00Z",
                "issues": [],
            },
            "idempotency_key": "nested-source-key",
        }
        stale_import = client.post(
            f"/v1/editable-documents/{document_id}/import",
            headers=auth_headers(),
            json={
                "base_revision": 3,
                "source": import_source,
                "idempotency_key": "draft-import-stale",
            },
        )
        deferred_import = client.post(
            f"/v1/editable-documents/{document_id}/import",
            headers=auth_headers(),
            json={
                "base_revision": 4,
                "source": import_source,
                "idempotency_key": "draft-import-deferred",
            },
        )
        assert stale_import.status_code == 409
        assert deferred_import.status_code == 422
        assert client.get(
            f"/v1/editable-documents/{document_id}", headers=auth_headers()
        ).json()["revision"] == 4


def imported_document_payload(document_key, source_artifact_id, *, key="import-create-1"):
    return {
        "mode": "import_registered_artifact",
        "document_key": document_key,
        "source_artifact_id": source_artifact_id,
        "content": blank_content(document_key),
        "settings": default_settings(),
        "import_report": {
            "source_filename": f"{document_key}.docx",
            "imported_at": "2026-08-07T20:00:00Z",
            "issues": [
                {
                    "code": "font_normalized",
                    "severity": "normalized",
                    "message": "Unsupported font normalized to Calibri",
                    "count": 1,
                }
            ],
        },
        "idempotency_key": key,
    }


def external_document_payload(document_key, content, *, key="external-create-1", digest=None):
    return {
        "mode": "import_external_docx",
        "document_key": document_key,
        "source_filename": f"{document_key}.docx",
        "source_base64": base64.b64encode(content).decode("ascii"),
        "source_sha256": digest or sha256(content).hexdigest(),
        "content": blank_content(document_key),
        "settings": default_settings(),
        "import_report": {
            "source_filename": f"{document_key}.docx",
            "imported_at": "2026-08-07T20:00:00Z",
            "issues": [],
        },
        "idempotency_key": key,
    }


def test_registered_import_requires_successful_same_job_docx_and_persists_snapshot(
    tmp_path, minimal_docx
):
    facade = FakeJobHunterFacade()
    pdf = tmp_path / "wrong-media.pdf"
    good = tmp_path / "references.docx"
    other = tmp_path / "other-job.docx"
    pdf.write_bytes(build_minimal_pdf("(FAKE) fixture"))
    good.write_bytes(minimal_docx("registered references"))
    other.write_bytes(minimal_docx("other job"))
    docx_media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    facade.artifacts["job-0"] = [
        {
            **artifact_metadata(pdf, revision="wrong-media", sequence=1),
            "document_key": "references",
            "document_label": "References",
        },
        {
            "job_id": "job-0",
            "document_key": "references",
            "document_label": "References",
            "source_revision": "failed-source",
            "artifact_revision": "failed-docx",
            "media_type": docx_media,
            "render_status": "failed",
            "render_sequence": 2,
            "failure_message": "fixture failure",
        },
        {
            **artifact_metadata(good, revision="good-docx", sequence=3),
            "document_key": "references",
            "document_label": "References",
            "media_type": docx_media,
        },
    ]
    facade.artifacts["job-1"] = [
        {
            **artifact_metadata(other, job_id="job-1", revision="other-docx", sequence=1),
            "document_key": "references",
            "document_label": "References",
            "media_type": docx_media,
        }
    ]

    with make_client(tmp_path, facade) as client:
        job_zero = client.post(
            "/v1/jobs/job-0/artifacts/refresh",
            headers=auth_headers(),
            json={"origin": "user", "idempotency_key": "refresh-import-job-0"},
        ).json()["artifacts"]
        job_one = client.post(
            "/v1/jobs/job-1/artifacts/refresh",
            headers=auth_headers(),
            json={"origin": "user", "idempotency_key": "refresh-import-job-1"},
        ).json()["artifacts"]
        ids = {artifact["artifact_revision"]: artifact["artifact_id"] for artifact in job_zero}
        other_id = job_one[0]["artifact_id"]

        for index, artifact_id in enumerate(
            (ids["wrong-media"], ids["failed-docx"], other_id), start=1
        ):
            rejected = client.post(
                "/v1/jobs/job-0/editable-documents",
                headers=auth_headers(),
                json=imported_document_payload(
                    "references", artifact_id, key=f"rejected-import-{index}"
                ),
            )
            assert rejected.status_code == 422

        created = client.post(
            "/v1/jobs/job-0/editable-documents",
            headers=auth_headers(),
            json=imported_document_payload("references", ids["good-docx"]),
        )
        assert created.status_code == 201
        document = created.json()
        assert document["source_artifact_id"] == ids["good-docx"]
        assert document["source_sha256"] == sha256(good.read_bytes()).hexdigest()
        assert document["import_report"]["issues"][0]["code"] == "font_normalized"
        snapshots = client.get(
            f"/v1/editable-documents/{document['document_id']}/snapshots",
            headers=auth_headers(),
        ).json()["snapshots"]
        assert [(item["reason"], item["actor"]) for item in snapshots] == [
            ("import", "import")
        ]


def test_external_import_uses_local_repository_replays_and_preserves_approval(
    tmp_path, minimal_docx
):
    facade = FakeJobHunterFacade()
    approved_pdf = tmp_path / "approved-resume.pdf"
    approved_pdf.write_bytes(build_minimal_pdf("(FAKE) approved"))
    facade.artifacts["job-0"] = [artifact_metadata(approved_pdf, revision="approved-pdf")]
    source = minimal_docx("external cover letter")
    payload = external_document_payload("cover_letter", source)

    with make_client(tmp_path, facade) as client:
        artifacts = client.post(
            "/v1/jobs/job-0/artifacts/refresh",
            headers=auth_headers(),
            json={"origin": "user", "idempotency_key": "refresh-approved"},
        ).json()["artifacts"]
        approved_id = artifacts[0]["artifact_id"]
        assert (
            client.post(
                f"/v1/jobs/job-0/artifacts/{approved_id}/approve",
                headers=auth_headers(),
                json={"origin": "user", "idempotency_key": "approve-before-import"},
            ).status_code
            == 200
        )

        created = client.post(
            "/v1/jobs/job-0/editable-documents", headers=auth_headers(), json=payload
        )
        replay = client.post(
            "/v1/jobs/job-0/editable-documents", headers=auth_headers(), json=payload
        )
        assert created.status_code == replay.status_code == 201
        assert replay.json() == created.json()
        document = created.json()
        assert document["source_filename"] == "cover_letter.docx"
        assert document["source_sha256"] == sha256(source).hexdigest()
        listed = client.get("/v1/jobs/job-0/artifacts", headers=auth_headers()).json()
        assert listed["approved_artifact_id"] == approved_id
        published_docx = [
            row for row in listed["artifacts"] if row["media_type"].endswith("document")
        ]
        assert len(published_docx) == 1
        imported = client.get(
            f"/v1/artifacts/{published_docx[0]['artifact_id']}/download",
            headers=auth_headers(),
        )
        assert imported.content == source
        assert imported.headers["x-content-sha256"] == sha256(source).hexdigest()
        assert facade.publish_calls == []

        bad_checksum = client.post(
            "/v1/jobs/job-0/editable-documents",
            headers=auth_headers(),
            json=external_document_payload("references", source, digest="0" * 64),
        )
        assert bad_checksum.status_code == 422
        before_duplicate = len(listed["artifacts"])
        duplicate = client.post(
            "/v1/jobs/job-0/editable-documents",
            headers=auth_headers(),
            json=external_document_payload("cover_letter", source, key="external-duplicate"),
        )
        assert duplicate.status_code == 409
        assert (
            len(client.get("/v1/jobs/job-0/artifacts", headers=auth_headers()).json()["artifacts"])
            == before_duplicate
        )

    with make_client(tmp_path, facade) as restarted:
        restarted_replay = restarted.post(
            "/v1/jobs/job-0/editable-documents", headers=auth_headers(), json=payload
        )
        persisted = restarted.get(
            f"/v1/editable-documents/{document['document_id']}", headers=auth_headers()
        )
        assert restarted_replay.status_code == 201
        assert restarted_replay.json() == document
        assert facade.publish_calls == []
        assert persisted.status_code == 200
        assert persisted.json()["source_sha256"] == sha256(source).hexdigest()
        assert persisted.json()["import_report"] == document["import_report"]


def test_replace_from_docx_snapshots_once_conflicts_and_protects_source_metadata(
    tmp_path, minimal_docx
):
    facade = FakeJobHunterFacade()
    replacement = tmp_path / "replacement.docx"
    replacement.write_bytes(minimal_docx("replacement references"))
    facade.artifacts["job-0"] = [
        {
            **artifact_metadata(replacement, revision="replacement-docx"),
            "document_key": "references",
            "document_label": "References",
            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    ]
    with make_client(tmp_path, facade) as client:
        artifact = client.post(
            "/v1/jobs/job-0/artifacts/refresh",
            headers=auth_headers(),
            json={"origin": "user", "idempotency_key": "refresh-replacement"},
        ).json()["artifacts"][0]
        created = client.post(
            "/v1/jobs/job-0/editable-documents",
            headers=auth_headers(),
            json={
                "mode": "blank",
                "document_key": "references",
                "idempotency_key": "blank-before-replace",
            },
        ).json()
        source_payload = imported_document_payload(
            "references", artifact["artifact_id"], key="nested-replacement"
        )
        command = {
            "base_revision": 1,
            "source": source_payload,
            "idempotency_key": "replace-registered-1",
        }
        replaced = client.post(
            f"/v1/editable-documents/{created['document_id']}/import",
            headers=auth_headers(),
            json=command,
        )
        replay = client.post(
            f"/v1/editable-documents/{created['document_id']}/import",
            headers=auth_headers(),
            json=command,
        )
        assert replaced.status_code == replay.status_code == 200
        assert replay.json() == replaced.json()
        assert replaced.json()["revision"] == 2
        assert replaced.json()["source_artifact_id"] == artifact["artifact_id"]
        snapshots = client.get(
            f"/v1/editable-documents/{created['document_id']}/snapshots",
            headers=auth_headers(),
        ).json()["snapshots"]
        assert [item["reason"] for item in snapshots] == ["before_restore"]

        stale = client.post(
            f"/v1/editable-documents/{created['document_id']}/import",
            headers=auth_headers(),
            json={**command, "idempotency_key": "replace-stale"},
        )
        protected = client.put(
            f"/v1/editable-documents/{created['document_id']}",
            headers=auth_headers(),
            json={
                "base_revision": 2,
                "content": replaced.json()["content"],
                "settings": replaced.json()["settings"],
                "comments": [],
                "source_artifact_id": "art_1234567890abcdef",
                "idempotency_key": "save-protected-source",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["current"]["revision"] == 2
        assert protected.status_code == 422

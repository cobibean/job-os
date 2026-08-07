import base64
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings
from jobos_api.state_store import mutation_activity_source_id

TITLE_POLICY_FIXTURES = json.loads(
    (Path(__file__).parents[3] / "tests/fixtures/browser-title-policy.json").read_text()
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

    def list_jobs(self):
        return list(self.jobs)

    def add_job(
        self,
        *,
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
        job_id = f"browser-job-{len(self.jobs)}"
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

    def update_lead_state(self, job_id, target_state, *, reason=None):
        job = self.inspect_job(job_id)
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

    def publish_document_artifact(
        self, job_id, document_key, document_label, source_path, artifact_path
    ):
        self.inspect_job(job_id)
        source = Path(source_path)
        artifact = Path(artifact_path)
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


def make_client(tmp_path, facade=None):
    app = create_app(
        Settings(
            device_token="test-device-token",
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
            artifact_roots=(tmp_path,),
            hermes_job_hunter_cwd=tmp_path,
        ),
        job_facade=facade or FakeJobHunterFacade(),
    )
    return TestClient(app)


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


def test_browser_save_creates_selects_and_immediately_lists_the_canonical_job(tmp_path):
    facade = FakeJobHunterFacade()

    with make_client(tmp_path, facade) as client:
        response = client.post("/v1/jobs", headers=auth_headers(), json=browser_job_payload())
        jobs = client.get("/v1/jobs", headers=auth_headers())
        workspace = client.get("/v1/workspace/jobs", headers=auth_headers())
        events = client.get("/v1/events?after=0", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["job"]["company"] == "Northstar Labs"
    assert body["job"]["title"] == "Applied AI Product Builder"
    assert body["job"]["location"] == "United States · Remote"
    assert body["job"]["description"].startswith("Build useful agent workflows")
    assert jobs.json()["jobs"][-1]["job_id"] == body["job"]["job_id"]
    assert workspace.json()["selected_job_id"] == body["job"]["job_id"]
    assert events.json()["events"][-1]["event_type"] == "job_selected"
    assert events.json()["events"][-1]["job_id"] == body["job"]["job_id"]


def test_mcp_job_create_requires_the_separate_trusted_credential(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/jobs",
            headers={"Authorization": "Bearer test-device-token"},
            json=browser_job_payload(origin="mcp", idempotency_key="forged-agent-save-1"),
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "MCP operations require the trusted local MCP credential"


def test_agent_can_create_a_job_through_the_same_canonical_ingest_seam(tmp_path):
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
    assert any(
        entry["type"] == "activity"
        and entry["summary"] == "Saved job from browser"
        and entry["detail"]["origin"] == "mcp"
        for entry in conversation.json()["entries"]
    )


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
    assert any(
        entry["summary"] == "Updated full job listing"
        and entry["detail"]["description_length"] == len(payload["description_text"])
        for entry in conversation.json()["entries"]
    )


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


def test_selection_is_durable_and_uses_the_same_event_path_for_user_and_mcp(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]

    with make_client(tmp_path, facade) as client:
        user_selection = client.put(
            "/v1/workspace/jobs/selection",
            headers=auth_headers(),
            json={"job_id": "job-1", "origin": "user"},
        )
        state = client.get("/v1/workspace/jobs", headers=auth_headers())
        mcp_selection = client.put(
            "/v1/workspace/jobs/selection",
            headers=auth_headers(),
            json={"job_id": "job-0", "origin": "mcp"},
        )
        events = client.get("/v1/events?after=0", headers=auth_headers()).json()["events"]

    assert user_selection.status_code == 200
    assert state.json()["selected_job_id"] == "job-1"
    assert mcp_selection.status_code == 200
    assert events[-1]["event_type"] == "job_selected"
    assert events[-1]["origin"] == "mcp"
    assert events[-1]["selected_job_id"] == "job-0"


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
    assert restored.json()["selected_job_id"] == "job-1"


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
        client.put(
            "/v1/workspace/jobs/selection",
            headers=auth_headers(),
            json={"job_id": "job-0", "origin": "user"},
        )
        stale_layout = client.get("/v1/workspace", headers=auth_headers()).json()
        stale_layout.pop("repaired_presets")
        stale_layout.pop("repaired_browser")
        stale_layout.pop("browser_repair_reasons")
        stale_layout.update({"origin": "user", "idempotency_key": "workspace-selection-race-1"})
        client.put(
            "/v1/workspace/jobs/selection",
            headers=auth_headers(),
            json={"job_id": "job-1", "origin": "mcp"},
        )
        stale_layout["selected_preset"] = "research"
        saved = client.put("/v1/workspace", headers=auth_headers(), json=stale_layout)
        job_state = client.get("/v1/workspace/jobs", headers=auth_headers())

    assert saved.status_code == 200
    assert saved.json()["selected_job_id"] == "job-1"
    assert job_state.json()["selected_job_id"] == "job-1"


def test_workspace_get_repairs_non_scalar_layout_values_without_losing_valid_state(
    tmp_path,
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]

    with make_client(tmp_path, facade) as client:
        client.put(
            "/v1/workspace/jobs/selection",
            headers=auth_headers(),
            json={"job_id": "job-1", "origin": "user"},
        )
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
    assert body["selected_job_id"] == "job-1"
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
    pdf.write_bytes(b"%PDF-1.7\ntrusted resume fixture\n%%EOF\n")
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


def test_trusted_mcp_can_publish_paired_pdf_and_docx_into_one_logical_revision(tmp_path):
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
            json=payload("cover-letter.pdf", b"%PDF-1.7\nletter\n%%EOF\n", "publish-pdf"),
        )
        docx_payload = payload("cover-letter.docx", b"PK\x03\x04docx-fixture", "publish-docx")
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
            json=payload("blocked.docx", b"PK\x03\x04blocked", "publish-blocked"),
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
    assert len(facade.artifacts["job-0"]) == 2
    assert all(
        Path(artifact["path"]).is_relative_to(tmp_path)
        for artifact in facade.artifacts["job-0"]
    )


def test_newer_success_and_failed_render_preserve_last_successful_preview(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    first = tmp_path / "resume-1.pdf"
    second = tmp_path / "resume-2.pdf"
    first.write_bytes(b"%PDF-1.7\nrevision one\n%%EOF\n")
    second.write_bytes(b"%PDF-1.7\nrevision two\n%%EOF\n")
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
    pdf.write_bytes(b"%PDF-1.7\napproved resume\n%%EOF\n")
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

    with make_client(tmp_path, facade) as restarted:
        restored = restarted.get(
            "/v1/jobs/job-0/artifacts", headers=auth_headers()
        ).json()

    assert approved.status_code == 200
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
    pdf.write_bytes(b"%PDF-1.7\ntrusted resume\n%%EOF\n")
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        registered = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()
        artifact_id = registered["last_successful_artifact_id"]
        if damage == "tampered":
            pdf.write_bytes(b"%PDF-1.7\ntampered resume\n%%EOF\n")
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

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == (
        "Registered artifact no longer matches trusted metadata"
    )
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
    pdf.write_bytes(b"%PDF-1.7\nother job\n%%EOF\n")
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


def test_failed_render_records_failed_agent_activity(tmp_path):
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
        state, detail_json = connection.execute(
            "SELECT state, detail_json FROM conversation_events "
            "WHERE source_event_id = ?",
            (
                mutation_activity_source_id(
                    actor_id="primary-device",
                    target_resource="jobs/job-0/artifacts",
                    command_name="document.render",
                    idempotency_key="failed-render-activity",
                ),
            ),
        ).fetchone()
    assert state == "failed"
    assert json.loads(detail_json)["outcome"] == "failed"


@pytest.mark.parametrize("manifest_order", ["oldest-first", "newest-first"])
def test_facade_render_sequence_determines_current_and_last_successful(
    tmp_path, manifest_order
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    older_pdf = tmp_path / "resume-old.pdf"
    last_good_docx = tmp_path / "resume-current.docx"
    older_pdf.write_bytes(b"%PDF-1.7\nolder PDF\n%%EOF\n")
    last_good_docx.write_bytes(b"PK\x03\x04last successful DOCX")
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
    first.write_bytes(b"%PDF-1.7\nfirst\n%%EOF\n")
    second.write_bytes(b"%PDF-1.7\nsecond\n%%EOF\n")
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
    pdf.write_bytes(b"%PDF-1.7\nlegacy resume\n%%EOF\n")
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
    original_bytes = b"%PDF-1.7\ntrusted snapshot\n%%EOF\n"
    replacement_bytes = b"%PDF-1.7\nreplacement snapshot\n%%EOF\n"
    pdf.write_bytes(original_bytes)
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        artifact = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()["artifacts"][0]
        original_read_bytes = Path.read_bytes
        calls = 0

        def replacing_read_bytes(path):
            nonlocal calls
            calls += 1
            return original_bytes if calls == 1 else replacement_bytes

        monkeypatch.setattr(Path, "read_bytes", replacing_read_bytes)
        streamed = client.get(
            f"/v1/artifacts/{artifact['artifact_id']}/content",
            headers=auth_headers(),
        )
        monkeypatch.setattr(Path, "read_bytes", original_read_bytes)

    assert calls == 1
    assert streamed.status_code == 200
    assert streamed.content == original_bytes
    assert streamed.headers["x-content-sha256"] == sha256(original_bytes).hexdigest()
    assert streamed.headers["x-artifact-revision"] == "render-1"


@pytest.mark.parametrize("attack", ["root_escape", "wrong_media", "hash_mismatch"])
def test_artifact_refresh_rejects_root_media_and_metadata_mismatches(tmp_path, attack):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    root_pdf = tmp_path / "resume.pdf"
    root_pdf.write_bytes(b"%PDF-1.7\ntrusted\n%%EOF\n")
    raw = artifact_metadata(root_pdf)
    if attack == "root_escape":
        outside = tmp_path.parent / "outside-jobos-artifact.pdf"
        outside.write_bytes(b"%PDF-1.7\noutside\n%%EOF\n")
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

    assert response.status_code == 422
    assert listed.json()["artifacts"] == []
    if attack == "root_escape":
        outside.unlink(missing_ok=True)


def test_unregistered_ids_paths_and_docx_preview_are_rejected(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    docx = tmp_path / "resume.docx"
    docx.write_bytes(b"PK\x03\x04fixture docx")
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


def test_multiple_document_formats_keep_identity_and_resume_only_approval(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    resume_pdf = tmp_path / "resume.pdf"
    resume_docx = tmp_path / "resume.docx"
    cover_pdf = tmp_path / "cover-letter.pdf"
    cover_docx = tmp_path / "cover-letter.docx"
    resume_pdf.write_bytes(b"%PDF-1.7\nresume\n%%EOF\n")
    resume_docx.write_bytes(b"PK\x03\x04resume docx")
    cover_pdf.write_bytes(b"%PDF-1.7\ncover letter\n%%EOF\n")
    cover_docx.write_bytes(b"PK\x03\x04cover letter docx")
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
    assert cover_approval.status_code == 409


def test_workspace_restores_only_an_active_artifact_owned_by_the_selected_job(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.7\nowned resume\n%%EOF\n")
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        client.put(
            "/v1/workspace/jobs/selection",
            headers=auth_headers(),
            json={"job_id": "job-0", "origin": "user"},
        )
        artifact_id = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()["current_artifact_id"]
        initial = client.get("/v1/workspace", headers=auth_headers()).json()
        command = {
            key: value
            for key, value in initial.items()
            if key
            not in {
                "repaired_presets",
                "repaired_browser",
                "browser_repair_reasons",
            }
        }
        command.update(
            {
                "origin": "user",
                "idempotency_key": "document-view-restore-1",
                "active_artifact_id": artifact_id,
                "active_artifact_page": 2,
                "active_artifact_zoom": 1.4,
            }
        )
        saved = client.put("/v1/workspace", headers=auth_headers(), json=command)
        restored = client.get("/v1/workspace", headers=auth_headers())

    assert saved.status_code == 200
    assert restored.json()["active_artifact_id"] == artifact_id
    assert restored.json()["active_artifact_page"] == 2
    assert restored.json()["active_artifact_zoom"] == 1.4


def test_workspace_rejects_cross_job_stale_and_unselected_active_artifacts(tmp_path):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:2]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.7\njob zero resume\n%%EOF\n")
    facade.artifacts["job-0"] = [artifact_metadata(pdf)]

    with make_client(tmp_path, facade) as client:
        artifact_id = client.post(
            "/v1/jobs/job-0/artifacts/refresh", headers=auth_headers()
        ).json()["current_artifact_id"]
        initial = client.get("/v1/workspace", headers=auth_headers()).json()
        command = {
            key: value
            for key, value in initial.items()
            if key not in {"repaired_presets", "repaired_browser", "browser_repair_reasons"}
        }
        unselected = client.put(
            "/v1/workspace",
            headers=auth_headers(),
            json={
                **command,
                "origin": "user",
                "idempotency_key": "unselected-artifact",
                "active_artifact_id": artifact_id,
            },
        )
        client.put(
            "/v1/workspace/jobs/selection",
            headers=auth_headers(),
            json={"job_id": "job-1", "origin": "user"},
        )
        selected = client.get("/v1/workspace", headers=auth_headers()).json()
        selected = {
            key: value
            for key, value in selected.items()
            if key not in {"repaired_presets", "repaired_browser", "browser_repair_reasons"}
        }
        mismatch = client.put(
            "/v1/workspace",
            headers=auth_headers(),
            json={
                **selected,
                "origin": "user",
                "idempotency_key": "cross-job-artifact",
                "active_artifact_id": artifact_id,
            },
        )
        stale = client.put(
            "/v1/workspace",
            headers=auth_headers(),
            json={
                **selected,
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
            "/v1/workspace/jobs/selection",
            {"job_id": "job-0", "origin": "mcp", "idempotency_key": "repair-select"},
            mutation_activity_source_id(
                actor_id="primary-device",
                target_resource="workspace/jobs",
                command_name="job.select",
                idempotency_key="repair-select",
            ),
        ),
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
def test_mutation_replay_repairs_a_missing_activity_row(
    tmp_path, method, endpoint, payload, source_event_id
):
    facade = FakeJobHunterFacade()
    facade.jobs = facade.jobs[:1]
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.7\nchronology\n%%EOF\n")
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
    assert count == 1


def test_workspace_save_replay_repairs_a_missing_activity_row(tmp_path):
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
        ).fetchone()[0] == 1


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
    pdf.write_bytes(b"%PDF-1.7\nconcurrent\n%%EOF\n")
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

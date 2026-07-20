import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings

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

    def list_jobs(self):
        return list(self.jobs)

    def inspect_job(self, job_id):
        job = next((job for job in self.jobs if job["job_id"] == job_id), None)
        if job is None:
            raise KeyError(job_id)
        return {**job, "description": "A job description", "location": "Remote"}

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


def make_client(tmp_path, facade=None):
    app = create_app(
        Settings(device_token="test-device-token", state_db_path=tmp_path / "jobos.db"),
        job_facade=facade or FakeJobHunterFacade(),
    )
    return TestClient(app)


def auth_headers():
    return {"Authorization": "Bearer test-device-token"}


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
        alphabetical = client.get(
            "/v1/jobs?sort=alphabetical", headers=auth_headers()
        ).json()["jobs"]
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
        alphabetical = client.get(
            "/v1/jobs?sort=alphabetical", headers=auth_headers()
        ).json()["jobs"]
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
            if key not in {"revision", "repaired_presets", "repaired_browser"}
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
    assert [tab["tab_id"] for tab in body["browser_tabs"]] == ["before", "after"]
    assert body["active_browser_tab_id"] == "after"


def test_workspace_rejects_capability_session_and_signed_url_variants(tmp_path):
    facade = FakeJobHunterFacade()
    sensitive_parameters = [
        "ticket", "assertion", "sig", "sessionid", "oauth_verifier",
        "X-Amz-Credential", "X-Amz-Signature", "X-Goog-Signature",
    ]

    with make_client(tmp_path, facade) as client:
        for index, parameter in enumerate(sensitive_parameters):
            body = client.get("/v1/workspace", headers=auth_headers()).json()
            body.pop("repaired_presets")
            body.pop("repaired_browser")
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
                    f"workspace-final-carrier-{metadata_field}-"
                    f"{abs(hash(unsafe_url))}"
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
def test_workspace_rejects_credential_bearing_remote_page_titles(
    tmp_path, unsafe_title
):
    facade = FakeJobHunterFacade()
    with make_client(tmp_path, facade) as client:
        body = client.get("/v1/workspace", headers=auth_headers()).json()
        body.pop("repaired_presets")
        body.pop("repaired_browser")
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
        stale_layout.update(
            {"origin": "user", "idempotency_key": "workspace-selection-race-1"}
        )
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

"""Delivery regressions use only the publication-approved fictional demo dataset."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings
from jobos_api.sqlite_job_repository import SQLiteJobRepository
from jobos_api.state_store import MIGRATIONS, IdempotencyConflict, JobOsStateStore
from jobos_api.synthetic_demo import DEMO_FIXTURE, DEMO_JOB_ID, seed_demo_once
from starlette.requests import Request

HEADERS = {"Authorization": "Bearer event-test-device"}


def event_settings(tmp_path):
    return Settings(
        device_token="event-test-device",
        mcp_token="event-test-mcp-token",
        state_db_path=tmp_path / "state.db",
        jobs_db_path=tmp_path / "jobs.db",
        artifact_roots=(tmp_path,),
    )


def demo_payload(**overrides):
    return {
        key: value
        for key, value in {
            **DEMO_FIXTURE,
            "origin": "user",
            "idempotency_key": "create-fictional-demo",
            **overrides,
        }.items()
        if key not in {"job_id", "observed_at", "dataset_version"}
    }


def sse_events(response):
    assert response.status_code == 200, response.text
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


@pytest.mark.parametrize("ingestion_source", ["browser", "external"])
def test_created_and_refreshed_jobs_deliver_once_to_another_client(tmp_path, ingestion_source):
    app = create_app(event_settings(tmp_path))
    with TestClient(app) as writer, TestClient(app) as observer:
        assert observer.get("/v1/events", headers=HEADERS).json() == {"events": []}
        payload = demo_payload(ingestion_source=ingestion_source)
        created = writer.post("/v1/jobs", headers=HEADERS, json=payload)
        replayed = writer.post("/v1/jobs", headers=HEADERS, json=payload)
        refreshed_payload = demo_payload(
            ingestion_source=ingestion_source,
            idempotency_key="refresh-fictional-demo",
            description_text=f"{DEMO_FIXTURE['description_text']} Fictional refresh.",
        )
        refreshed = writer.post("/v1/jobs", headers=HEADERS, json=refreshed_payload)
        refreshed_replay = writer.post("/v1/jobs", headers=HEADERS, json=refreshed_payload)
        conflicting = writer.post(
            "/v1/jobs",
            headers=HEADERS,
            json={**payload, "description_text": "Conflicting fictional listing"},
        )
        assert conflicting.status_code == 409
        assert created.status_code == refreshed.status_code == 200
        assert replayed.json() == created.json()
        assert refreshed_replay.json() == refreshed.json()
        events = sse_events(observer.get("/v1/events/stream?once=true", headers=HEADERS))
        assert [event["event_type"] for event in events] == ["job_created", "job_refreshed"]
        assert [event["event_id"] for event in events] == [
            created.json()["event_id"],
            refreshed.json()["event_id"],
        ]
        assert events[0]["event_id"] > 0
        assert all(event["job_id"] == created.json()["job"]["job_id"] for event in events)
        assert all(
            set(event) == {"event_id", "event_type", "job_id", "origin", "occurred_at"}
            for event in events
        )
        cursor = events[-1]["event_id"]
        assert (
            sse_events(observer.get(f"/v1/events/stream?after={cursor}&once=true", headers=HEADERS))
            == []
        )


def test_demo_removal_delivers_once_and_replays_after_restart(tmp_path):
    configured = event_settings(tmp_path)
    repository = SQLiteJobRepository(configured.jobs_db_path)
    repository.initialize()
    seed_demo_once(repository, configured.jobs_db_path)
    payload = {"origin": "user", "idempotency_key": "remove-fictional-demo"}
    with TestClient(create_app(configured)) as writer:
        removed = writer.request(
            "DELETE", f"/v1/jobs/{DEMO_JOB_ID}/demo", headers=HEADERS, json=payload
        )
        assert removed.status_code == 200
    with TestClient(create_app(configured)) as observer:
        replayed = observer.request(
            "DELETE", f"/v1/jobs/{DEMO_JOB_ID}/demo", headers=HEADERS, json=payload
        )
        assert replayed.json() == removed.json()
        events = sse_events(observer.get("/v1/events/stream?once=true", headers=HEADERS))
        assert [(event["event_id"], event["event_type"], event["job_id"]) for event in events] == [
            (removed.json()["event_id"], "job_removed", DEMO_JOB_ID)
        ]


class BlockedMutationRepository(SQLiteJobRepository):
    def __init__(self, path):
        super().__init__(path)
        self.entered = Event()
        self.release = Event()
        self.mutation_calls = 0

    def wait_for_release(self):
        self.mutation_calls += 1
        self.entered.set()
        assert self.release.wait(timeout=10), "Test did not release the blocked provider"

    def update_status(self, job_id, target_status, **kwargs):
        self.wait_for_release()
        return super().update_status(job_id, target_status, **kwargs)

    def update_description(self, job_id, description_text, **kwargs):
        self.wait_for_release()
        return super().update_description(job_id, description_text, **kwargs)


class WatchingEventStore(JobOsStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.waiting = Event()
        self.watch_cursor = None

    def list_job_events(self, after=0):
        events = super().list_job_events(after)
        if after == self.watch_cursor and not events:
            self.waiting.set()
        return events


@pytest.mark.parametrize("mutation_kind", ["status", "description"])
def test_late_completion_reaches_waiting_stream_and_reconnect_after_overtaking(
    tmp_path, mutation_kind
):
    configured = event_settings(tmp_path)
    repository = BlockedMutationRepository(configured.jobs_db_path)
    repository.initialize()
    seed_demo_once(repository, configured.jobs_db_path)
    store = WatchingEventStore(configured.state_db_path)
    app = create_app(configured, job_repository=repository, state_store=store)
    detail = (
        {"target_status": "reviewed"}
        if mutation_kind == "status"
        else {"description_text": DEMO_FIXTURE["description_text"], "source": "user"}
    )
    payload = {**detail, "origin": "user", "idempotency_key": f"late-{mutation_kind}"}
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/events/stream")

    async def live_delivery(cursor):
        async def receive():
            return {"type": "http.request", "body": b""}

        response = await endpoint(
            Request({"type": "http"}, receive), None, after=cursor, once=False
        )
        iterator = response.body_iterator
        try:
            return await asyncio.wait_for(anext(iterator), timeout=5)
        finally:
            await iterator.aclose()

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(
            client.put, f"/v1/jobs/{DEMO_JOB_ID}/{mutation_kind}", headers=HEADERS, json=payload
        )
        try:
            assert repository.entered.wait(timeout=5)
            overtaking = client.put(
                "/v1/workspace/jobs/sort",
                headers=HEADERS,
                json={
                    "sort_mode": "alphabetical",
                    "origin": "user",
                    "idempotency_key": "overtaking-sort",
                },
            )
            assert overtaking.status_code == 200, overtaking.text
            cursor = overtaking.json()["event_id"]
            assert [
                event["event_type"]
                for event in sse_events(client.get("/v1/events/stream?once=true", headers=HEADERS))
            ] == ["job_sort_changed"]
            store.watch_cursor = cursor
            live = pool.submit(lambda: asyncio.run(live_delivery(cursor)))
            assert store.waiting.wait(timeout=5), "Live stream did not begin waiting"
        finally:
            repository.release.set()
        completed = pending.result(timeout=5)
        assert completed.status_code == 200, completed.text
        replayed = client.put(
            f"/v1/jobs/{DEMO_JOB_ID}/{mutation_kind}", headers=HEADERS, json=payload
        )
        assert replayed.json() == completed.json()
        assert repository.mutation_calls == 1
        reconnected = sse_events(
            client.get(f"/v1/events/stream?after={cursor}&once=true", headers=HEADERS)
        )
        expected_type = (
            "job_status_changed" if mutation_kind == "status" else "job_description_updated"
        )
        assert [(event["event_type"], event["event_id"]) for event in reconnected] == [
            (expected_type, completed.json()["event_id"])
        ]
        assert completed.json()["event_id"] > cursor
        assert f"id: {completed.json()['event_id']}\n" in live.result(timeout=5)
    with TestClient(create_app(configured)) as restarted:
        assert (
            sse_events(
                restarted.get(f"/v1/events/stream?after={cursor}&once=true", headers=HEADERS)
            )
            == reconnected
        )


def test_old_completed_event_cursors_and_pending_reservations_survive_upgrade(tmp_path):
    database = tmp_path / "state.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for migration in MIGRATIONS:
            if migration.version <= 32:
                JobOsStateStore._apply_migration(connection, migration)
        connection.execute(
            """INSERT INTO job_events(event_type, job_id, origin, actor_id,
            target_resource, command_name, idempotency_key, request_hash, outcome,
            payload_json, result_json) VALUES ('job_status_changed', ?, 'user', 'device',
            'jobs/demo', 'job.update_status', 'legacy-completed', 'hash', 'completed',
            '{"from_status":"discovered","to_status":"reviewed"}', '{"event_id":1}')""",
            (DEMO_JOB_ID,),
        )
        connection.execute(
            """INSERT INTO job_events(event_type, job_id, origin, actor_id,
            target_resource, command_name, idempotency_key, request_hash, outcome)
            VALUES ('mutation_pending', ?, 'user', 'device', 'jobs/demo',
            'job.update_description', 'legacy-pending', 'hash-pending', 'pending')""",
            (DEMO_JOB_ID,),
        )
    store = JobOsStateStore(database)
    store.initialize()
    assert [event["event_id"] for event in store.list_job_events()] == [1]
    cursor = store.save_job_sort("alphabetical", "user")
    published = store.record_mutation_result(
        event_type="job_description_updated",
        origin="user",
        actor_id="device",
        target_resource="jobs/demo",
        command_name="job.update_description",
        outcome="completed",
        idempotency_key="legacy-pending",
        request_hash="hash-pending",
        result={"event_id": 0},
        detail={"source": "user", "description_length": 10},
        job_id=DEMO_JOB_ID,
        inject_event_id=True,
        reserved_event_id=2,
    )
    assert published > cursor
    assert [event["event_id"] for event in store.list_job_events(cursor)] == [published]
    assert [event["event_id"] for event in store.list_job_events()] == [1, cursor, published]


def test_rejected_reservation_does_not_block_later_delivery(tmp_path):
    store = JobOsStateStore(tmp_path / "state.db")
    store.initialize()
    store.reserve_mutation(
        origin="user",
        actor_id="device",
        target_resource="jobs/demo",
        command_name="job.update_status",
        idempotency_key="rejected",
        request_hash="rejected-hash",
        job_id=DEMO_JOB_ID,
    )
    cursor = store.save_job_sort("alphabetical", "user")
    assert [event["event_id"] for event in store.list_job_events()] == [cursor]


def test_settlement_failure_rolls_back_delivery_and_retry_publishes_once(tmp_path):
    database = tmp_path / "state.db"
    store = JobOsStateStore(database)
    store.initialize()
    reservation = store.reserve_mutation(
        origin="user",
        actor_id="device",
        target_resource="jobs/demo",
        command_name="job.update_status",
        idempotency_key="atomic-publication",
        request_hash="atomic-hash",
        job_id=DEMO_JOB_ID,
    )
    arguments = {
        "event_type": "job_status_changed",
        "origin": "user",
        "actor_id": "device",
        "target_resource": "jobs/demo",
        "command_name": "job.update_status",
        "outcome": "completed",
        "idempotency_key": "atomic-publication",
        "request_hash": "atomic-hash",
        "result": {"event_id": 0},
        "detail": {
            "from_status": "discovered",
            "to_status": "reviewed",
            "label": "private audit label",
            "unrelated": "hidden",
        },
        "job_id": DEMO_JOB_ID,
        "inject_event_id": True,
        "reserved_event_id": reservation,
    }
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TRIGGER fail_settlement BEFORE UPDATE ON job_events
            WHEN OLD.event_type = 'job_status_changed' AND OLD.result_json IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'synthetic settlement failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic settlement failure"):
        store.record_mutation_result(**arguments)
    assert store.list_job_events() == []
    replay_arguments = {
        key: arguments[key]
        for key in (
            "actor_id",
            "target_resource",
            "command_name",
            "idempotency_key",
            "request_hash",
        )
    }
    assert store.mutation_result(**replay_arguments) is None
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_settlement")
    published = store.record_mutation_result(**arguments)
    assert published > reservation
    assert store.mutation_result(**replay_arguments) == {"event_id": published}
    events = store.list_job_events()
    assert len(events) == 1
    assert events[0]["event_id"] == published
    assert events[0]["from_status"] == "discovered"
    assert not {"label", "unrelated", "actor_id", "command_name"} & events[0].keys()


@pytest.mark.parametrize(
    "changed_field",
    ["actor_id", "target_resource", "command_name", "idempotency_key", "request_hash"],
)
def test_stale_or_mismatched_reservation_cannot_publish(tmp_path, changed_field):
    store = JobOsStateStore(tmp_path / "state.db")
    store.initialize()
    identity = {
        "actor_id": "device",
        "target_resource": "jobs/demo",
        "command_name": "job.update_status",
        "idempotency_key": "owned-reservation",
        "request_hash": "owned-hash",
    }
    reservation = store.reserve_mutation(origin="user", job_id=DEMO_JOB_ID, **identity)
    arguments = {
        **identity,
        "event_type": "job_status_changed",
        "origin": "user",
        "outcome": "completed",
        "result": {"event_id": 0},
        "detail": {"from_status": "discovered", "to_status": "reviewed"},
        "job_id": DEMO_JOB_ID,
        "inject_event_id": True,
        "reserved_event_id": reservation,
    }
    with pytest.raises(IdempotencyConflict):
        store.record_mutation_result(**{**arguments, changed_field: "different-command"})
    assert store.list_job_events() == []
    published = store.record_mutation_result(**arguments)
    with pytest.raises(IdempotencyConflict):
        store.record_mutation_result(**arguments)
    assert [event["event_id"] for event in store.list_job_events()] == [published]
    assert store.mutation_result(**identity) == {"event_id": published}


@pytest.mark.parametrize("outcome", ["rejected", "failed"])
def test_failed_settlement_does_not_publish_a_success_event(tmp_path, outcome):
    store = JobOsStateStore(tmp_path / "state.db")
    store.initialize()
    identity = {
        "actor_id": "device",
        "target_resource": "jobs/demo",
        "command_name": "job.update_status",
        "idempotency_key": "rejected-reservation",
        "request_hash": "rejected-hash",
    }
    reservation = store.reserve_mutation(origin="user", job_id=DEMO_JOB_ID, **identity)
    store.record_mutation_result(
        **identity,
        event_type="job_status_changed",
        origin="user",
        outcome=outcome,
        result={"error": "synthetic rejection"},
        detail={},
        job_id=DEMO_JOB_ID,
        reserved_event_id=reservation,
    )
    assert store.list_job_events() == []
    assert store.mutation_result(**identity) == {"error": "synthetic rejection"}

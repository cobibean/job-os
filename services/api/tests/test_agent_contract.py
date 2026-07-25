import asyncio
import json
import sqlite3
import time

from fastapi.testclient import TestClient
from jobos_api.agent_gateway import AgentContext, GatewayEvent
from jobos_api.app import create_app
from jobos_api.conversations import (
    ConversationService,
    RetryTurnRequest,
    SendMessageRequest,
    conversation_event_source,
)
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore

TOKEN = "phase-six-device-token"


class FakeJobFacade:
    def __init__(self) -> None:
        self.jobs = [
            {
                "job_id": "job-1",
                "company": "Northstar",
                "title": "Product Engineer",
                "status": "shortlisted",
            },
            {
                "job_id": "job-2",
                "company": "Daybreak",
                "title": "Platform Engineer",
                "status": "reviewed",
            },
        ]

    def list_jobs(self):
        return self.jobs

    def inspect_job(self, job_id):
        for job in self.jobs:
            if job["job_id"] == job_id:
                return job
        raise KeyError(job_id)


class FakeGateway:
    def __init__(self, *, online=True) -> None:
        self.online = online
        self.submissions: list[tuple[str, AgentContext]] = []
        self.session_requests: list[str | None] = []
        self.interruptions: list[str] = []
        self._events: list[GatewayEvent] = []
        self.detaches = 0
        self.started = False
        self.closed = False

    @property
    def connection_state(self):
        return "online" if self.online else "offline"

    async def start(self):
        self.started = True

    async def create_or_resume_conversation(self, stored_session_id):
        self.session_requests.append(stored_session_id)
        if not self.online:
            raise ConnectionError("dashboard unavailable with Authorization: secret-value")
        return "stored-session", "live-session"

    async def submit_turn(self, text, context):
        if not self.online:
            raise ConnectionError("dashboard unavailable with token=secret-value")
        self.submissions.append((text, context))

    async def detach_conversation(self):
        self.detaches += 1
        self._events.clear()

    async def stream_events(self):
        for event in self._events:
            yield event

    async def interrupt_turn(self, turn_id):
        self.interruptions.append(turn_id)

    async def recover_active_turn(self, stored_session_id, turn_id):
        self.interruptions.append(turn_id)

    async def close(self):
        self.closed = True


class ReconnectingGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__(online=False)
        self.events = asyncio.Queue()

    async def start(self):
        self.started = True
        if not self.online:
            raise ConnectionError("initial dashboard connection failed")

    async def create_or_resume_conversation(self, stored_session_id):
        self.online = True
        return "stored-session", "live-session"

    async def submit_turn(self, text, context):
        await super().submit_turn(text, context)
        await self.events.put(
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="Recovered and completed",
                turn_id=context.turn_id,
                source_event_id="reconnected-completion",
            )
        )

    async def stream_events(self):
        while True:
            yield await self.events.get()


class DelayedStartGateway(ReconnectingGateway):
    def __init__(self, failures=2) -> None:
        super().__init__()
        self.failures = failures
        self.start_attempts = 0

    async def start(self):
        self.started = True
        self.start_attempts += 1
        if self.start_attempts <= self.failures:
            raise ConnectionError("dashboard not ready")
        self.online = True


class RestartingStreamGateway(ReconnectingGateway):
    def __init__(self) -> None:
        super().__init__()
        self.online = True
        self.stream_calls = 0

    async def stream_events(self):
        self.stream_calls += 1
        if self.stream_calls == 1:
            raise RuntimeError("event stream ended unexpectedly")
        while True:
            yield await self.events.get()


class InterruptFailureGateway(FakeGateway):
    async def interrupt_turn(self, turn_id):
        self.interruptions.append(turn_id)
        raise ConnectionError("Authorization: Bearer cancellation-secret")


class RotatingSessionGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self._events = [
            GatewayEvent(
                event_type="reconciliation",
                state="idle",
                summary="",
                detail={
                    "running": False,
                    "stored_session_id": "stored-rotated",
                },
            )
        ]


class ConnectedRequest:
    async def is_disconnected(self):
        return False


class FifteenActionGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.events = asyncio.Queue()

    async def submit_turn(self, text, context):
        await super().submit_turn(text, context)
        for index in range(15):
            for phase, state in (
                ("start", "working"),
                ("progress", "working"),
                ("complete", "completed"),
            ):
                await self.events.put(
                    GatewayEvent(
                        event_type="activity",
                        state=state,
                        summary=f"Action {index + 1}",
                        detail={"phase": phase},
                        turn_id=context.turn_id,
                        source_event_id=f"event-{index}-{phase}",
                        activity_id=f"tool-{index}",
                    )
                )
        await self.events.put(
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="All done",
                turn_id=context.turn_id,
                source_event_id="event-complete",
            )
        )
        await asyncio.sleep(0)

    async def stream_events(self):
        while True:
            yield await self.events.get()


def headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def make_client(tmp_path, gateway=None):
    app = create_app(
        Settings(
            device_token=TOKEN,
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
        ),
        job_facade=FakeJobFacade(),
        agent_gateway=gateway or FakeGateway(),
    )
    return TestClient(app)


def send_message(client, text="Help me plan the next step", key="message-key-0001"):
    return client.post(
        "/v1/conversations/current/messages",
        headers=headers(),
        json={"text": text, "idempotency_key": key},
    )


def test_browser_save_turn_starts_with_fresh_model_context(tmp_path):
    """Large browser snapshots must not bury required save tools in old agent history."""

    async def scenario():
        gateway = FakeGateway()
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        store.save_stored_session_id("stored-long-running-session")
        service = ConversationService(store, gateway)
        await service.start()
        try:
            created = await service.send(
                SendMessageRequest(
                    text="Save the job in this browser tab",
                    idempotency_key="browser-save-regression-0001",
                ),
                actor_id="device-mini",
                context={"workspace": {"active_browser_tab_id": "tab-1"}},
            )
            assert store.stored_session_id() == "stored-session"
            gateway._events.append(
                GatewayEvent(
                    event_type="assistant_message",
                    state="completed",
                    summary="JOBOS_EXTRACT_RESULT:{}",
                    turn_id=created.turn_id,
                    source_event_id="browser-save-complete",
                )
            )
            await service._consume_gateway_events()
        finally:
            await service.close()
        assert gateway.session_requests == [None]
        assert gateway.submissions[0][0] == "Save the job in this browser tab"
        assert store.stored_session_id() == "stored-long-running-session"
        snapshot = store.conversation_snapshot()
        entries = snapshot["entries"]
        assert isinstance(entries, list)
        turn_entry = next(entry for entry in entries if entry["type"] == "turn")
        assert "_fresh_agent_session" not in turn_entry["context"]

    asyncio.run(scenario())


def test_browser_save_session_restores_after_api_restart_and_ignores_late_terminal(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        store.save_stored_session_id("ordinary-session")
        first = ConversationService(store, FakeGateway())
        await first.start()
        created = await first.send(
            SendMessageRequest(
                text="Save the current browser job",
                idempotency_key="browser-save-restart-regression",
            ),
            actor_id="device-mini",
            context={"workspace": {"active_browser_tab_id": "tab-1"}},
        )
        await first.close()
        assert store.stored_session_id() == "stored-session"

        recovered_gateway = FakeGateway()
        recovered = ConversationService(store, recovered_gateway)
        await recovered.start()
        assert recovered_gateway.interruptions == [created.turn_id]
        assert store.stored_session_id() == "ordinary-session"

        recovered_gateway._events.append(GatewayEvent(
            event_type="reconciliation",
            state="completed",
            summary="late isolated-session reconciliation",
            turn_id=None,
            source_event_id="late-isolated-reconciliation",
            detail={"stored_session_id": "stored-session"},
        ))
        await recovered._consume_gateway_events()
        assert store.stored_session_id() == "ordinary-session"
        recovered_gateway._events.clear()

        store.save_stored_session_id("newer-conversation-session")
        recovered_gateway._events.append(GatewayEvent(
            event_type="assistant_message",
            state="completed",
            summary="late duplicate",
            turn_id=created.turn_id,
            source_event_id="late-browser-save-terminal",
        ))
        await recovered._consume_gateway_events()
        assert store.stored_session_id() == "newer-conversation-session"
        await recovered.close()

    asyncio.run(scenario())


def test_empty_current_conversation_is_authenticated_and_stable(tmp_path):
    gateway = FakeGateway()
    with make_client(tmp_path, gateway) as client:
        unauthorized = client.get("/v1/conversations/current")
        response = client.get("/v1/conversations/current", headers=headers())

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": response.json()["conversation_id"],
        "entries": [],
        "active_turn": None,
        "connection": {"state": "online"},
        "latest_event_id": 0,
    }
    assert gateway.started and gateway.closed


def test_new_session_rejects_active_work_then_rotates_and_clears_the_conversation(tmp_path):
    gateway = FakeGateway()
    with make_client(tmp_path, gateway) as client:
        before = client.get("/v1/conversations/current", headers=headers()).json()
        created = send_message(client).json()

        blocked = client.post("/v1/conversations/current/reset", headers=headers())
        client.post(
            f"/v1/conversations/current/turns/{created['turn_id']}/cancel",
            headers=headers(),
        )
        reset = client.post("/v1/conversations/current/reset", headers=headers())
        restored = client.get("/v1/conversations/current", headers=headers())
        fresh_turn = send_message(client, text="Fresh context, same delivery key")

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == (
        "Finish or stop the active turn before starting a new session"
    )
    assert reset.status_code == 200
    assert restored.json() == reset.json()
    assert reset.json() == {
        "conversation_id": reset.json()["conversation_id"],
        "entries": [],
        "active_turn": None,
        "connection": {"state": "online"},
        "latest_event_id": reset.json()["latest_event_id"],
    }
    assert reset.json()["latest_event_id"] > 0
    assert reset.json()["conversation_id"] != before["conversation_id"]
    assert fresh_turn.status_code == 201
    assert fresh_turn.json()["turn_id"] != created["turn_id"]
    assert gateway.detaches == 1
    assert gateway.session_requests == [None, None]


def test_message_validation_idempotency_and_running_turn_serialization(tmp_path):
    gateway = FakeGateway()
    with make_client(tmp_path, gateway) as client:
        assert send_message(client, text=" ").status_code == 422
        first = send_message(client)
        replay = send_message(client)
        blocked = send_message(client, key="message-key-0002")

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert blocked.status_code == 409
    assert len(gateway.submissions) == 1


def test_user_text_is_sanitized_before_database_snapshot_and_gateway_submission(tmp_path):
    gateway = FakeGateway()
    raw_secret = "sk-live-never-cross-this-boundary"
    database = tmp_path / "jobos.db"
    with make_client(tmp_path, gateway) as client:
        response = send_message(
            client,
            text=f"Keep this useful prose but password={raw_secret} please.",
            key="message-key-secret-boundary",
        )
        snapshot_json = json.dumps(
            client.get("/v1/conversations/current", headers=headers()).json()
        )

    assert response.status_code == 201
    assert gateway.submissions[0][0] == "Keep this useful prose but [redacted] please."
    assert raw_secret not in database.read_bytes().decode(errors="ignore")
    assert raw_secret not in snapshot_json
    assert raw_secret not in json.dumps([item[0] for item in gateway.submissions])


def test_cookie_and_authorization_headers_inside_plain_text_are_redacted_everywhere(tmp_path):
    gateway = FakeGateway()
    database = tmp_path / "jobos.db"
    raw = (
        "Cookie: sessionid=cookie-secret; csrftoken=csrf-secret\nAuthorization: Basic dXNlcjpwYXNz"
    )
    with make_client(tmp_path, gateway) as client:
        response = send_message(
            client, text=f"Inspect this request:\n{raw}", key="header-secret-boundary"
        )
        snapshot = client.get("/v1/conversations/current", headers=headers()).json()

    assert response.status_code == 201
    serialized = json.dumps(
        {"snapshot": snapshot, "submitted": [item[0] for item in gateway.submissions]}
    )
    persisted = database.read_bytes().decode(errors="ignore")
    for secret in ("cookie-secret", "csrf-secret", "dXNlcjpwYXNz"):
        assert secret not in serialized
        assert secret not in persisted
    assert "Inspect this request:" in gateway.submissions[0][0]


def test_long_valid_user_prompt_keeps_its_user_bound_after_redaction(tmp_path):
    gateway = FakeGateway()
    secret = "Basic dXNlcjpwYXNz"
    raw = f"{secret}:{'x' * (12_000 - len(secret) - 1)}"
    expected = f"[redacted]:{'x' * (12_000 - len(secret) - 1)}"

    with make_client(tmp_path, gateway) as client:
        response = send_message(client, text=raw, key="long-redacted-user-message")
        snapshot = client.get("/v1/conversations/current", headers=headers()).json()

    assert response.status_code == 201
    assert gateway.submissions[0][0] == expected
    user_entry = next(entry for entry in snapshot["entries"] if entry["type"] == "user_message")
    assert user_entry["text"] == expected
    assert len(user_entry["text"]) == len(expected) > 11_000
    assert "dXNlcjpwYXNz" not in json.dumps(snapshot)


def test_turn_snapshots_selected_job_and_device_workspace_without_new_conversation(tmp_path):
    gateway = FakeGateway()
    with make_client(tmp_path, gateway) as client:
        before = client.get("/v1/conversations/current", headers=headers()).json()
        selected = client.put(
            "/v1/workspace/jobs/selection",
            headers=headers(),
            json={"job_id": "job-1", "origin": "user"},
        )
        sent = send_message(client)
        after = client.get("/v1/conversations/current", headers=headers()).json()

    assert selected.status_code == 200
    assert sent.status_code == 201
    assert after["conversation_id"] == before["conversation_id"]
    assert gateway.submissions[0][1].selected_job_id == "job-1"
    assert gateway.submissions[0][1].selected_job == {
        "job_id": "job-1",
        "company": "Northstar",
        "title": "Product Engineer",
    }
    assert gateway.submissions[0][1].workspace["selected_preset"] == "review"
    turn = next(entry for entry in after["entries"] if entry["type"] == "turn")
    assert turn["context"]["selected_job_id"] == "job-1"
    assert turn["context"]["selected_job"] == {
        "job_id": "job-1",
        "company": "Northstar",
        "title": "Product Engineer",
    }


def test_cancel_is_idempotent_and_retry_appends_linked_turn(tmp_path):
    gateway = FakeGateway()
    with make_client(tmp_path, gateway) as client:
        created = send_message(client).json()
        turn_id = created["turn_id"]
        first_cancel = client.post(
            f"/v1/conversations/current/turns/{turn_id}/cancel", headers=headers()
        )
        second_cancel = client.post(
            f"/v1/conversations/current/turns/{turn_id}/cancel", headers=headers()
        )
        retry = client.post(
            f"/v1/conversations/current/turns/{turn_id}/retry",
            headers=headers(),
            json={"idempotency_key": "retry-key-0001"},
        )

    assert first_cancel.status_code == second_cancel.status_code == 200
    assert first_cancel.json()["status"] == "interrupted"
    assert gateway.interruptions == [turn_id]
    assert retry.status_code == 201
    assert retry.json()["source_turn_id"] == turn_id


def test_cancel_settles_locally_when_interrupt_transport_fails_and_remains_idempotent(
    tmp_path,
):
    gateway = InterruptFailureGateway()
    with make_client(tmp_path, gateway) as client:
        turn_id = send_message(client).json()["turn_id"]
        first = client.post(f"/v1/conversations/current/turns/{turn_id}/cancel", headers=headers())
        second = client.post(f"/v1/conversations/current/turns/{turn_id}/cancel", headers=headers())
        snapshot = client.get("/v1/conversations/current", headers=headers()).json()

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "interrupted"
    assert gateway.interruptions == [turn_id]
    assert snapshot["active_turn"] is None
    interruption = [
        entry
        for entry in snapshot["entries"]
        if entry["turn_id"] == turn_id and entry["state"] == "interrupted"
    ]
    assert len(interruption) == 1
    assert interruption[0]["detail"]["actionable"] is True
    serialized = json.dumps({"responses": [first.json(), second.json()], "snapshot": snapshot})
    assert "cancellation-secret" not in serialized
    assert "authorization" not in serialized.lower()


def test_late_gateway_completion_cannot_overwrite_local_interruption(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        gateway = InterruptFailureGateway()
        service = ConversationService(store, gateway)
        created = await service.send(
            SendMessageRequest(
                text="Start cancellable work", idempotency_key="cancel-late-completion"
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        await service.cancel(created.turn_id)
        gateway._events = [
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="Remote completion arrived late",
                turn_id=created.turn_id,
                source_event_id="late-completion",
            )
        ]
        await service._consume_gateway_events()
        return store.turn_record(created.turn_id)

    record = asyncio.run(scenario())
    assert record["status"] == "interrupted"


def test_late_gateway_waiting_status_cannot_reopen_local_interruption(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        gateway = InterruptFailureGateway()
        service = ConversationService(store, gateway)
        created = await service.send(
            SendMessageRequest(
                text="Start cancellable work", idempotency_key="cancel-late-waiting"
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        await service.cancel(created.turn_id)
        gateway._events = [
            GatewayEvent(
                event_type="status",
                state="waiting",
                summary="Remote waiting arrived late",
                turn_id=created.turn_id,
                source_event_id="late-waiting",
            )
        ]
        await service._consume_gateway_events()
        return store.turn_record(created.turn_id)

    record = asyncio.run(scenario())
    assert record["status"] == "interrupted"


def test_sse_event_ids_are_ordered_and_resume_after_cursor(tmp_path):
    with make_client(tmp_path) as client:
        created = send_message(client).json()
        snapshot = client.get("/v1/conversations/current", headers=headers()).json()
        first_id = snapshot["entries"][0]["event_id"]
        response = client.get(
            "/v1/conversations/current/events/stream?once=true",
            headers={**headers(), "Last-Event-ID": str(first_id)},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame.startswith("id:")]
    ids = [int(frame.splitlines()[0].removeprefix("id: ")) for frame in frames]
    assert ids == sorted(ids)
    assert ids and all(event_id > first_id for event_id in ids)
    assert created["turn_id"] in response.text


def test_sse_polls_promptly_for_new_events_and_heartbeats_on_separate_cadence(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        source = conversation_event_source(
            store,
            ConnectedRequest(),
            cursor=0,
            poll_interval=0.02,
            heartbeat_interval=0.12,
        )
        assert await anext(source) == "retry: 2000\n\n"

        started = time.monotonic()
        heartbeat = await asyncio.wait_for(anext(source), timeout=0.5)
        heartbeat_elapsed = time.monotonic() - started
        assert heartbeat == ": heartbeat\n\n"
        assert 0.08 <= heartbeat_elapsed <= 0.5

        cursor = int(store.conversation_snapshot()["latest_event_id"])
        await source.aclose()
        prompt_source = conversation_event_source(
            store,
            ConnectedRequest(),
            cursor=cursor,
            poll_interval=0.02,
            heartbeat_interval=15,
        )
        assert await anext(prompt_source) == "retry: 2000\n\n"

        async def append_later():
            await asyncio.sleep(0.05)
            store.append_conversation_event(
                turn_id=None,
                event_type="status",
                state="working",
                summary="New local event",
            )

        append_task = asyncio.create_task(append_later())
        started = time.monotonic()
        frame = await asyncio.wait_for(anext(prompt_source), timeout=0.5)
        elapsed = time.monotonic() - started
        await append_task
        await prompt_source.aclose()
        assert "event: conversation" in frame
        assert "New local event" in frame
        assert elapsed <= 0.5

    asyncio.run(scenario())


def test_relaunch_restores_entries_and_offline_submit_remains_actionable(tmp_path):
    offline = FakeGateway(online=False)
    with make_client(tmp_path, offline) as client:
        sent = send_message(client)
        snapshot = client.get("/v1/conversations/current", headers=headers()).json()

    assert sent.status_code == 201
    assert snapshot["active_turn"] is None
    assert any(entry["state"] == "failed" for entry in snapshot["entries"])
    serialized = json.dumps(snapshot).lower()
    assert "secret-value" not in serialized
    assert "authorization" not in serialized

    with make_client(tmp_path, FakeGateway(online=False)) as relaunched:
        restored = relaunched.get("/v1/conversations/current", headers=headers()).json()

    assert restored["conversation_id"] == snapshot["conversation_id"]
    assert restored["entries"] == snapshot["entries"]


def test_offline_start_keeps_consumer_alive_for_reconnect_and_completion(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        gateway = ReconnectingGateway()
        service = ConversationService(store, gateway)
        await service.start()
        result = await service.send(
            SendMessageRequest(
                text="Reconnect and complete", idempotency_key="offline-reconnect-turn"
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        for _ in range(50):
            if store.turn_record(result.turn_id)["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        await service.close()
        return store.turn_record(result.turn_id), store.conversation_snapshot(), gateway

    record, snapshot, gateway = asyncio.run(scenario())
    assert gateway.started is True
    assert len(gateway.submissions) == 1
    assert record["status"] == "completed"
    assert snapshot["active_turn"] is None
    assert any(entry["summary"] == "Recovered and completed" for entry in snapshot["entries"])


def test_gateway_connectivity_and_mid_turn_transport_loss_are_durable_and_ordered(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    created = store.create_conversation_turn(
        text="Keep this durable",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="durable-disconnect-turn",
        actor_id="device-a",
    )
    turn_id = str(created["turn_id"])
    store.save_stored_session_id("stored-disconnected")
    gateway = FakeGateway()
    gateway._events = [
        GatewayEvent(
            event_type="connection",
            state="working",
            summary="",
            detail={"agent_connection": "online"},
        ),
        GatewayEvent(
            event_type="error",
            state="failed",
            summary="Agent connection unavailable. Retry when the agent is online.",
            detail={"actionable": True, "reason": "transport_lost", "retry": True},
            turn_id=turn_id,
        ),
        GatewayEvent(
            event_type="connection",
            state="working",
            summary="",
            detail={"agent_connection": "offline"},
        ),
    ]

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())
    snapshot = store.conversation_snapshot()
    connection_events = [
        entry["detail"]["agent_connection"]
        for entry in snapshot["entries"]
        if "agent_connection" in entry["detail"]
    ]
    terminal = [
        entry
        for entry in snapshot["entries"]
        if entry["turn_id"] == turn_id and entry["type"] == "error"
    ]

    assert connection_events == ["online", "offline"]
    assert store.turn_record(turn_id)["status"] == "failed"
    assert snapshot["active_turn"] is None
    assert len(terminal) == 1
    assert terminal[0]["detail"]["reason"] == "transport_lost"
    assert terminal[0]["detail"]["retry"] is True

    retried = asyncio.run(
        ConversationService(store, gateway).retry(
            turn_id,
            RetryTurnRequest(idempotency_key="disconnect-recovery-retry"),
            actor_id="device-a",
        )
    )
    assert gateway.interruptions == [turn_id]
    assert retried is not None
    assert retried.source_turn_id == turn_id


def test_new_durable_session_is_saved_before_prompt_submission_can_begin(tmp_path):
    async def scenario(*, acknowledge):
        store = JobOsStateStore(tmp_path / ("accepted.db" if acknowledge else "rejected.db"))
        store.initialize()

        class OrderingGateway(FakeGateway):
            async def create_or_resume_conversation(self, stored_session_id):
                assert stored_session_id is None
                return "new-durable-id", "new-live-id"

            async def submit_turn(self, text, context):
                assert store.stored_session_id() == "new-durable-id"
                if not acknowledge:
                    raise RuntimeError("prompt was not acknowledged")
                await super().submit_turn(text, context)

        gateway = OrderingGateway()
        service = ConversationService(store, gateway)
        result = await service.send(
            SendMessageRequest(
                text="Start safely",
                idempotency_key=f"persist-after-ack-{acknowledge}",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        return store.stored_session_id(), store.turn_record(result.turn_id)

    accepted_id, accepted_turn = asyncio.run(scenario(acknowledge=True))
    rejected_id, rejected_turn = asyncio.run(scenario(acknowledge=False))

    assert accepted_id == "new-durable-id"
    assert accepted_turn["status"] == "running"
    assert rejected_id == "new-durable-id"
    assert rejected_turn["status"] == "failed"
    assert JobOsStateStore(tmp_path / "rejected.db").recovery_turn_id() == rejected_turn["turn_id"]


def test_cancel_during_session_attachment_never_submits_the_settled_turn(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        attachment_entered = asyncio.Event()
        release_attachment = asyncio.Event()

        class AttachmentBarrierGateway(FakeGateway):
            async def create_or_resume_conversation(self, stored_session_id):
                attachment_entered.set()
                await release_attachment.wait()
                return "stored-attached", "live-attached"

        gateway = AttachmentBarrierGateway()
        service = ConversationService(store, gateway)
        send_task = asyncio.create_task(
            service.send(
                SendMessageRequest(
                    text="Do not submit after cancellation",
                    idempotency_key="cancel-attachment-race",
                ),
                actor_id="device-a",
                context={"selected_job_id": None, "workspace": {}},
            )
        )
        await attachment_entered.wait()
        turn_id = str(store.conversation_snapshot()["active_turn"]["turn_id"])
        cancelled = await service.cancel(turn_id)
        release_attachment.set()
        await send_task
        return cancelled, gateway, store.turn_record(turn_id), store.conversation_snapshot()

    cancelled, gateway, record, snapshot = asyncio.run(scenario())

    assert cancelled.status == "interrupted"
    assert gateway.submissions == []
    assert record["status"] == "interrupted"
    terminal = [
        entry
        for entry in snapshot["entries"]
        if entry["turn_id"] == record["turn_id"]
        and entry["state"] in {"completed", "failed", "interrupted"}
        and entry["type"] in {"assistant_message", "error", "status"}
    ]
    assert [(entry["type"], entry["state"]) for entry in terminal] == [("status", "interrupted")]


def test_attachment_failure_before_submit_terminalizes_without_recovery_quarantine(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()

        class AttachmentFailureGateway(FakeGateway):
            async def create_or_resume_conversation(self, stored_session_id):
                raise ConnectionError("transport lost before attachment")

        gateway = AttachmentFailureGateway()
        result = await ConversationService(store, gateway).send(
            SendMessageRequest(
                text="Fail before submission",
                idempotency_key="pre-submit-transport-failure",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        return gateway, store.turn_record(result.turn_id), store.recovery_turn_id()

    gateway, record, recovery_turn_id = asyncio.run(scenario())

    assert gateway.submissions == []
    assert record["status"] == "failed"
    assert recovery_turn_id is None


def test_isolated_submission_failure_recovers_with_the_isolated_session_id(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        store.save_stored_session_id("ordinary-session")

        class SubmissionFailureGateway(FakeGateway):
            def __init__(self):
                super().__init__()
                self.recoveries = []

            async def submit_turn(self, text, context):
                raise ConnectionError("transport lost during submission")

            async def recover_active_turn(self, stored_session_id, turn_id):
                self.recoveries.append((stored_session_id, turn_id))

        gateway = SubmissionFailureGateway()
        service = ConversationService(store, gateway)
        result = await service.send(
            SendMessageRequest(
                text="Save the active browser listing",
                idempotency_key="browser-save-submission-failure",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        assert store.stored_session_id() == "ordinary-session"
        assert store.recovery_turn_id() == result.turn_id

        await service._ensure_recovery_clear()
        return result.turn_id, gateway.recoveries, store.recovery_turn_id()

    turn_id, recoveries, recovery_turn_id = asyncio.run(scenario())

    assert recoveries == [("stored-session", turn_id)]
    assert recovery_turn_id is None


def test_rotated_durable_id_reconciles_without_transcript_or_raw_metadata(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    store.save_stored_session_id("stored-original")
    gateway = RotatingSessionGateway()

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())

    assert store.stored_session_id() == "stored-rotated"
    snapshot = store.conversation_snapshot()
    assert snapshot["entries"] == []
    serialized = json.dumps(snapshot)
    assert "stored-rotated" not in serialized
    assert "running" not in serialized


def test_renderer_event_detail_excludes_session_routing_and_raw_transport_metadata(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    gateway = FakeGateway()
    gateway._events = [
        GatewayEvent(
            event_type="activity",
            state="working",
            summary="Safe activity",
            detail={
                "session_id": "live-never-render",
                "stored_session_id": "stored-never-render",
                "profile": "private-profile",
                "cwd": "/private/workspace",
                "transport_metadata": {"url": "ws://private.invalid"},
                "operation": "Useful safe operation",
            },
            turn_id=None,
            source_event_id="private-routing-boundary",
        )
    ]

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())

    snapshot = store.conversation_snapshot()
    serialized = json.dumps(snapshot)
    assert snapshot["entries"][0]["detail"]["operation"] == "Useful safe operation"
    for private_value in (
        "live-never-render",
        "stored-never-render",
        "private-profile",
        "/private/workspace",
        "ws://private.invalid",
    ):
        assert private_value not in serialized
        assert private_value not in (tmp_path / "jobos.db").read_bytes().decode(errors="ignore")


def test_prompt_ack_cannot_overwrite_a_concurrently_rotated_durable_id(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        store.save_stored_session_id("stored-original")

        class RotationDuringAckGateway(FakeGateway):
            async def create_or_resume_conversation(self, stored_session_id):
                assert stored_session_id == "stored-original"
                return "stored-resumed", "live-session"

            async def submit_turn(self, text, context):
                store.save_stored_session_id("stored-rotated")
                await super().submit_turn(text, context)

        service = ConversationService(store, RotationDuringAckGateway())
        await service.send(
            SendMessageRequest(
                text="Continue safely",
                idempotency_key="rotation-during-prompt-ack",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        return store.stored_session_id()

    assert asyncio.run(scenario()) == "stored-rotated"


def test_api_restart_recovers_stale_turn_once_and_allows_linked_retry(tmp_path):
    first_gateway = FakeGateway()
    with make_client(tmp_path, first_gateway) as client:
        stale_turn_id = send_message(client).json()["turn_id"]
        before_restart = client.get("/v1/conversations/current", headers=headers()).json()

    retry_gateway = FakeGateway()
    with make_client(tmp_path, retry_gateway) as restarted:
        recovered = restarted.get("/v1/conversations/current", headers=headers()).json()
        retry = restarted.post(
            f"/v1/conversations/current/turns/{stale_turn_id}/retry",
            headers=headers(),
            json={"idempotency_key": "restart-retry-key-0001"},
        )

    assert recovered["conversation_id"] == before_restart["conversation_id"]
    assert recovered["entries"][: len(before_restart["entries"])] == before_restart["entries"]
    recovery_events = [
        entry
        for entry in recovered["entries"]
        if entry["turn_id"] == stale_turn_id and entry["state"] == "interrupted"
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0]["detail"]["retry"] is True
    assert retry.status_code == 201
    assert retry.json()["source_turn_id"] == stale_turn_id

    with make_client(tmp_path, FakeGateway()) as restarted_again:
        repeated = restarted_again.get("/v1/conversations/current", headers=headers()).json()
    repeated_recovery_events = [
        entry
        for entry in repeated["entries"]
        if entry["turn_id"] == stale_turn_id and entry["state"] == "interrupted"
    ]
    assert len(repeated_recovery_events) == 1


def test_restart_reattaches_and_interrupts_remote_before_terminalizing_or_retrying(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    created = store.create_conversation_turn(
        text="Still running remotely",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="restart-remote-active",
        actor_id="device-a",
    )
    store.save_stored_session_id("stored-active")
    gateway = FakeGateway()

    with make_client(tmp_path, gateway) as client:
        snapshot = client.get("/v1/conversations/current", headers=headers()).json()
        retry = client.post(
            f"/v1/conversations/current/turns/{created['turn_id']}/retry",
            headers=headers(),
            json={"idempotency_key": "restart-confirmed-retry"},
        )

    assert gateway.interruptions[0] == created["turn_id"]
    assert snapshot["active_turn"] is None
    assert retry.status_code == 201


def test_unconfirmed_restart_cleanup_quarantines_overlap_and_stop_retries_recovery(tmp_path):
    database = tmp_path / "jobos.db"
    store = JobOsStateStore(database)
    store.initialize()
    created = store.create_conversation_turn(
        text="Potential remote overlap",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="restart-quarantine-active",
        actor_id="device-a",
    )
    store.save_stored_session_id("stored-quarantine")

    class RetryableRecoveryGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.recovery_attempts = 0

        async def recover_active_turn(self, stored_session_id, turn_id):
            assert stored_session_id == "stored-quarantine"
            self.recovery_attempts += 1
            if self.recovery_attempts == 1:
                raise ConnectionError("Cookie: sessionid=restart-secret")
            self.interruptions.append(turn_id)

    gateway = RetryableRecoveryGateway()
    with make_client(tmp_path, gateway) as client:
        quarantined = client.get("/v1/conversations/current", headers=headers()).json()
        overlap = send_message(client, key="must-not-overlap-quarantine")
        recovered = client.post(
            f"/v1/conversations/current/turns/{created['turn_id']}/cancel", headers=headers()
        )
        after = client.get("/v1/conversations/current", headers=headers()).json()

    assert quarantined["active_turn"]["turn_id"] == created["turn_id"]
    assert any(entry["detail"].get("recovery_pending") is True for entry in quarantined["entries"])
    assert overlap.status_code == 409
    assert recovered.json()["status"] == "interrupted"
    assert after["active_turn"] is None
    assert gateway.recovery_attempts == 2
    assert "restart-secret" not in json.dumps({"quarantined": quarantined, "after": after})


def test_cancel_completion_race_has_one_winning_terminal_event(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        entered_interrupt = asyncio.Event()
        release_interrupt = asyncio.Event()

        class BarrierGateway(FakeGateway):
            async def interrupt_turn(self, turn_id):
                entered_interrupt.set()
                await release_interrupt.wait()

        gateway = BarrierGateway()
        service = ConversationService(store, gateway)
        created = await service.send(
            SendMessageRequest(text="Race safely", idempotency_key="cancel-complete-race"),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        cancel_task = asyncio.create_task(service.cancel(created.turn_id))
        await entered_interrupt.wait()
        gateway._events = [
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="Completed first",
                turn_id=created.turn_id,
                source_event_id="race-complete",
            )
        ]
        await service._consume_gateway_events()
        release_interrupt.set()
        result = await cancel_task
        return result, store.turn_record(created.turn_id), store.conversation_snapshot()

    result, record, snapshot = asyncio.run(scenario())
    terminal = [
        entry
        for entry in snapshot["entries"]
        if entry["turn_id"] == record["turn_id"]
        and entry["type"] in {"assistant_message", "error", "status"}
        and entry["state"] in {"completed", "failed", "interrupted"}
    ]
    assert result.status == "completed"
    assert record["status"] == "completed"
    assert [(entry["type"], entry["state"]) for entry in terminal] == [
        ("assistant_message", "completed")
    ]


def test_dispatch_exception_cannot_overwrite_concurrent_interrupt(tmp_path):
    async def scenario():
        cancel_requested = asyncio.Event()

        class BarrierStore(JobOsStateStore):
            def request_turn_cancel(self, turn_id):
                result = super().request_turn_cancel(turn_id)
                cancel_requested.set()
                return result

        store = BarrierStore(tmp_path / "jobos.db")
        store.initialize()
        entered_submit = asyncio.Event()
        release_submit = asyncio.Event()

        class BarrierGateway(FakeGateway):
            async def submit_turn(self, text, context):
                entered_submit.set()
                await release_submit.wait()
                raise ConnectionError("Authorization: Basic dispatch-secret")

        gateway = BarrierGateway()
        service = ConversationService(store, gateway)
        send_task = asyncio.create_task(
            service.send(
                SendMessageRequest(text="Dispatch safely", idempotency_key="dispatch-cancel-race"),
                actor_id="device-a",
                context={"selected_job_id": None, "workspace": {}},
            )
        )
        await entered_submit.wait()
        turn_id = str(store.conversation_snapshot()["active_turn"]["turn_id"])
        cancel_task = asyncio.create_task(service.cancel(turn_id))
        await cancel_requested.wait()
        release_submit.set()
        await send_task
        cancelled = await cancel_task
        return cancelled, store.turn_record(turn_id), store.conversation_snapshot()

    cancelled, record, snapshot = asyncio.run(scenario())
    terminal = [
        entry
        for entry in snapshot["entries"]
        if entry["turn_id"] == record["turn_id"]
        and entry["type"] in {"assistant_message", "error", "status"}
        and entry["state"] in {"completed", "failed", "interrupted"}
    ]
    assert cancelled.status == "interrupted"
    assert record["status"] == "interrupted"
    assert [(entry["type"], entry["state"]) for entry in terminal] == [("status", "interrupted")]
    assert "dispatch-secret" not in json.dumps(snapshot)


def test_fifteen_gateway_actions_project_to_fifteen_ordered_activity_identities(
    tmp_path,
):
    gateway = FifteenActionGateway()
    with make_client(tmp_path, gateway) as client:
        assert send_message(client).status_code == 201
        snapshot = client.get("/v1/conversations/current", headers=headers()).json()

    activities = [entry for entry in snapshot["entries"] if entry["type"] == "activity"]
    assert len(activities) == 45
    projected = {entry["detail"]["activity_id"]: entry for entry in activities}
    assert list(projected) == [f"tool-{index}" for index in range(15)]
    assert all(entry["state"] == "completed" for entry in projected.values())
    assert [entry["event_id"] for entry in activities] == sorted(
        entry["event_id"] for entry in activities
    )
    assert snapshot["active_turn"] is None


def test_only_new_terminal_message_event_transitions_turn_status_once(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    created = store.create_conversation_turn(
        text="Start",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="terminal-transition-1",
        actor_id="device-a",
    )
    turn_id = str(created["turn_id"])
    gateway = FakeGateway()
    gateway._events = [
        GatewayEvent(
            event_type="activity",
            state="completed",
            summary="Tool done",
            turn_id=turn_id,
            source_event_id="tool-complete",
            activity_id="tool-1",
        ),
        GatewayEvent(
            event_type="assistant_message",
            state="completed",
            summary="Done",
            turn_id=turn_id,
            source_event_id="message-complete",
        ),
        GatewayEvent(
            event_type="assistant_message",
            state="completed",
            summary="Done",
            turn_id=turn_id,
            source_event_id="message-complete",
        ),
    ]
    transitions = []
    original_settle = store.settle_active_turn

    def record_transition(changed_turn_id, status, **kwargs):
        changed = original_settle(changed_turn_id, status, **kwargs)
        if changed:
            transitions.append((changed_turn_id, status))
        return changed

    store.settle_active_turn = record_transition

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())

    assert transitions == [(turn_id, "completed")]


def test_only_new_error_event_transitions_turn_to_failed_once(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    created = store.create_conversation_turn(
        text="Start",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="error-transition-1",
        actor_id="device-a",
    )
    turn_id = str(created["turn_id"])
    gateway = FakeGateway()
    gateway._events = [
        GatewayEvent(
            event_type="error",
            state="failed",
            summary="Agent unavailable",
            turn_id=turn_id,
            source_event_id="gateway-error",
        ),
        GatewayEvent(
            event_type="error",
            state="failed",
            summary="Agent unavailable",
            turn_id=turn_id,
            source_event_id="gateway-error",
        ),
    ]
    transitions = []
    original_settle = store.settle_active_turn

    def record_transition(changed_turn_id, status, **kwargs):
        changed = original_settle(changed_turn_id, status, **kwargs)
        if changed:
            transitions.append((changed_turn_id, status))
        return changed

    store.settle_active_turn = record_transition

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())

    assert transitions == [(turn_id, "failed")]


def test_working_status_moves_waiting_turn_back_to_running_without_settling(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    created = store.create_conversation_turn(
        text="Start",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="waiting-to-running-1",
        actor_id="device-a",
    )
    turn_id = str(created["turn_id"])
    store.update_turn_status(turn_id, "waiting")
    gateway = FakeGateway()
    gateway._events = [
        GatewayEvent(
            event_type="status",
            state="working",
            summary="Resuming work",
            turn_id=turn_id,
            source_event_id="status-working-again",
        )
    ]

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())

    assert store.turn_record(turn_id)["status"] == "running"
    snapshot = store.conversation_snapshot()
    assert snapshot["active_turn"]["status"] == "running"
    assert snapshot["entries"][-1]["state"] == "working"


def test_service_reconnects_when_gateway_becomes_available_without_a_new_session(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        gateway = DelayedStartGateway(failures=2)
        service = ConversationService(store, gateway)
        await service.start()
        try:
            for _ in range(50):
                if gateway.connection_state == "online":
                    break
                await asyncio.sleep(0.02)
            return gateway.connection_state, gateway.start_attempts
        finally:
            await service.close()

    state, attempts = asyncio.run(scenario())

    assert state == "online"
    assert attempts == 3


def test_gateway_event_persistence_retries_the_same_event_after_sqlite_failure(
    tmp_path, monkeypatch
):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        gateway = ReconnectingGateway()
        gateway.online = True
        service = ConversationService(store, gateway)
        real_append = store.append_conversation_event
        attempts = 0

        def flaky_append(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise sqlite3.OperationalError("transient database failure")
            return real_append(*args, **kwargs)

        monkeypatch.setattr(store, "append_conversation_event", flaky_append)
        await service.start()
        try:
            await gateway.events.put(
                GatewayEvent(
                    event_type="connection",
                    state="working",
                    summary="",
                    detail={"agent_connection": "online"},
                )
            )
            for _ in range(50):
                snapshot = store.conversation_snapshot()
                entries = snapshot["entries"]
                assert isinstance(entries, list)
                if any(entry["summary"] == "Agent online" for entry in entries):
                    event_task = service._event_task
                    assert event_task is not None
                    return attempts, event_task.done()
                await asyncio.sleep(0.02)
            raise AssertionError("retried event was not persisted")
        finally:
            await service.close()

    attempts, consumer_done = asyncio.run(scenario())

    assert attempts == 2
    assert consumer_done is False


def test_gateway_event_supervisor_restarts_an_ended_stream(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        gateway = RestartingStreamGateway()
        service = ConversationService(store, gateway)
        await service.start()
        try:
            await gateway.events.put(
                GatewayEvent(
                    event_type="connection",
                    state="working",
                    summary="",
                    detail={"agent_connection": "online"},
                )
            )
            for _ in range(50):
                entries = store.conversation_snapshot()["entries"]
                assert isinstance(entries, list)
                if any(entry["summary"] == "Agent online" for entry in entries):
                    return gateway.stream_calls
                await asyncio.sleep(0.02)
            raise AssertionError("restarted event stream did not persist its event")
        finally:
            await service.close()

    assert asyncio.run(scenario()) == 2


def test_terminal_retry_restores_isolated_session_before_settling_turn(tmp_path, monkeypatch):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    store.save_stored_session_id("stored-original")
    created = store.create_conversation_turn(
        text="Create documents",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="terminal-restore-retry",
        actor_id="device-a",
    )
    turn_id = str(created["turn_id"])
    store.begin_isolated_agent_session(turn_id)
    store.record_isolated_agent_session(turn_id, "stored-isolated")
    real_restore = store.restore_isolated_agent_session
    restore_attempts = 0

    def flaky_restore(changed_turn_id):
        nonlocal restore_attempts
        restore_attempts += 1
        if restore_attempts == 1:
            raise sqlite3.OperationalError("transient restore failure")
        return real_restore(changed_turn_id)

    monkeypatch.setattr(store, "restore_isolated_agent_session", flaky_restore)
    gateway = FakeGateway()
    gateway._events = [
        GatewayEvent(
            event_type="assistant_message",
            state="completed",
            summary="Done",
            turn_id=turn_id,
            source_event_id="terminal-restore-complete",
        )
    ]

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())

    assert restore_attempts == 2
    turn = store.turn_record(turn_id)
    assert turn is not None
    assert turn["status"] == "completed"
    assert store.stored_session_id() == "stored-original"
    entries = store.conversation_snapshot()["entries"]
    assert isinstance(entries, list)
    terminal_entries = [
        entry
        for entry in entries
        if entry["summary"] == "Done"
    ]
    assert len(terminal_entries) == 1


def test_status_retry_transitions_before_appending_exactly_one_event(tmp_path, monkeypatch):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    created = store.create_conversation_turn(
        text="Wait",
        context={"selected_job_id": None, "workspace": {}},
        idempotency_key="status-append-retry",
        actor_id="device-a",
    )
    turn_id = str(created["turn_id"])
    real_append = store.append_conversation_event
    append_attempts = 0

    def flaky_append(*args, **kwargs):
        nonlocal append_attempts
        append_attempts += 1
        if append_attempts == 1:
            raise sqlite3.OperationalError("transient append failure")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(store, "append_conversation_event", flaky_append)
    gateway = FakeGateway()
    gateway._events = [
        GatewayEvent(
            event_type="status",
            state="waiting",
            summary="Choose one",
            turn_id=turn_id,
            source_event_id="status-append-waiting",
        )
    ]

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())

    assert append_attempts == 2
    turn = store.turn_record(turn_id)
    assert turn is not None
    assert turn["status"] == "waiting"
    entries = store.conversation_snapshot()["entries"]
    assert isinstance(entries, list)
    matching_entries = [
        entry for entry in entries if entry["summary"] == "Choose one"
    ]
    assert len(matching_entries) == 1

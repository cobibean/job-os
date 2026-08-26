import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from jobos_api.agent_gateway import (
    AgentContext,
    AmbiguousDeliveryError,
    DefinitivePreSubmitError,
    GatewayEvent,
)
from jobos_api.app import create_app
from jobos_api.conversation_manager import ConversationManager
from jobos_api.conversations import (
    ConversationService,
    RetryTurnRequest,
    SendMessageRequest,
    conversation_event_source,
)
from jobos_api.hermes_adapter import HermesGatewayFactory
from jobos_api.private_adapters.job_hunter import adapt_job_hunter_facade
from jobos_api.settings import DeviceCredential, Settings
from jobos_api.state_store import ConversationNotFound, JobOsStateStore

TOKEN = "agent-contract-device-token"


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

    def is_available(self):
        return True

    def list_jobs(self):
        return self.jobs

    def inspect_job(self, job_id):
        for job in self.jobs:
            if job["job_id"] == job_id:
                return job
        raise KeyError(job_id)


class FakeGateway:
    def __init__(self, *, online=True, session_scope="") -> None:
        self.online = online
        self.session_scope = session_scope
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
            raise ConnectionError("dashboard unavailable with Authorization: ***")
        return f"stored-session{self.session_scope}", f"live-session{self.session_scope}"

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

    async def respond_to_review(self, turn_id, approval_id, *, approved) -> None:
        raise ValueError("No tool review is pending")

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


class IdleReconnectingGateway(ReconnectingGateway):
    def __init__(self) -> None:
        super().__init__()
        self.start_attempts = 0

    async def start(self):
        self.start_attempts += 1
        self.started = True
        if self.start_attempts == 1:
            raise ConnectionError("initial dashboard connection failed")
        self.online = True


class InterruptFailureGateway(FakeGateway):
    async def interrupt_turn(self, turn_id):
        self.interruptions.append(turn_id)
        raise ConnectionError("Authorization: Bearer cancellation-secret")


class AmbiguousAttachmentGateway(FakeGateway):
    async def create_or_resume_conversation(self, stored_session_id):
        self.session_requests.append(stored_session_id)
        raise AmbiguousDeliveryError("Provider attachment outcome is unknown")


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


class ToolReviewGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.events: asyncio.Queue[GatewayEvent] = asyncio.Queue()
        self.pending: tuple[str, str] | None = None
        self.reviews: list[tuple[str, str, bool]] = []

    async def submit_turn(self, text, context):
        await super().submit_turn(text, context)
        approval_id = "approval_aB3dE5gH7jK9mN2pQ4rS6tUv"
        self.pending = (context.turn_id, approval_id)
        await self.events.put(
            GatewayEvent(
                event_type="status",
                state="waiting",
                summary="Allow the JobOS tool to inspect this job?",
                detail={
                    "actionable": True,
                    "approval_id": approval_id,
                    "tool_name": "job_inspect",
                },
                turn_id=context.turn_id,
                source_event_id="review-request-1",
            )
        )

    async def stream_events(self):
        while True:
            yield await self.events.get()

    async def respond_to_review(self, turn_id, approval_id, *, approved):
        if self.pending != (turn_id, approval_id):
            raise ValueError("Tool review does not match the active turn")
        self.reviews.append((turn_id, approval_id, approved))
        self.pending = None


def headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def make_client(tmp_path, gateway=None, gateway_factory=None):
    repository, artifact_gateway = adapt_job_hunter_facade(FakeJobFacade())
    app = create_app(
        Settings(
            device_token=TOKEN,
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
        ),
        job_repository=repository,
        artifact_gateway=artifact_gateway,
        agent_gateway=gateway or FakeGateway(),
        agent_gateway_factory=gateway_factory,
    )
    return TestClient(app)


def send_message(client, text="Help me plan the next step", key="message-key-0001"):
    conversation_id = current_id(client)
    return client.post(
        f"/v1/conversations/{conversation_id}/messages",
        headers=headers(),
        json={"text": text, "idempotency_key": key},
    )


def current_id(client):
    return client.get("/v1/conversations", headers=headers()).json()["conversations"][0][
        "conversation_id"
    ]


def turn_url(client, turn_id, action):
    return f"/v1/conversations/{current_id(client)}/turns/{turn_id}/{action}"


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

        recovered_gateway._events.append(
            GatewayEvent(
                event_type="reconciliation",
                state="completed",
                summary="late isolated-session reconciliation",
                turn_id=None,
                source_event_id="late-isolated-reconciliation",
                detail={"stored_session_id": "stored-session"},
            )
        )
        await recovered._consume_gateway_events()
        assert store.stored_session_id() == "ordinary-session"
        recovered_gateway._events.clear()

        store.save_stored_session_id("newer-conversation-session")
        recovered_gateway._events.append(
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="late duplicate",
                turn_id=created.turn_id,
                source_event_id="late-browser-save-terminal",
            )
        )
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
        "title": "Session 1",
        "position": 1,
        "created_at": response.json()["created_at"],
        "entries": [],
        "active_turn": None,
        "connection": {"state": "online"},
        "recovery_state": "ready",
        "latest_event_id": 0,
        "job_context": {
            "selected_job_id": None,
            "active_artifact_id": None,
            "active_artifact_page": 1,
            "active_artifact_zoom": 1.0,
        },
    }
    assert gateway.started and gateway.closed


def test_new_session_is_additive_and_archive_rejects_active_work(tmp_path):
    gateway = FakeGateway()
    with make_client(tmp_path, gateway) as client:
        before = client.get("/v1/conversations/current", headers=headers()).json()
        created = send_message(client).json()

        blocked = client.delete(f"/v1/conversations/{before['conversation_id']}", headers=headers())
        added = client.post("/v1/conversations", headers=headers())
        client.post(turn_url(client, created["turn_id"], "cancel"), headers=headers())
        archived = client.delete(
            f"/v1/conversations/{before['conversation_id']}", headers=headers()
        )
        restored = client.get(
            f"/v1/conversations/{added.json()['conversation_id']}", headers=headers()
        )

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "The final session cannot be archived"
    assert added.status_code == 201
    assert archived.status_code == 204
    assert restored.status_code == 200
    assert restored.json()["conversation_id"] == added.json()["conversation_id"]
    assert restored.json()["entries"] == []


def test_message_validation_idempotency_and_running_turn_serialization(tmp_path):
    gateway = FakeGateway()
    with make_client(tmp_path, gateway) as client:
        assert send_message(client, text=" ").status_code == 422
        first = send_message(client)
        replay = send_message(client)
        blocked = send_message(client, key="message-key-0002")

    assert first.status_code == 201
    assert replay.status_code == 201
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    assert {key: value for key, value in replay.json().items() if key != "created"} == {
        key: value for key, value in first.json().items() if key != "created"
    }
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
            f"/v1/conversations/{before['conversation_id']}/workspace/job",
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


def test_tool_review_endpoint_is_authenticated_and_scoped_to_exact_waiting_turn(tmp_path):
    gateway = ToolReviewGateway()
    with make_client(tmp_path, gateway) as client:
        created = send_message(client).json()
        conversation_id = current_id(client)
        turn_id = created["turn_id"]
        review_url = f"/v1/conversations/{conversation_id}/turns/{turn_id}/review"

        for _ in range(100):
            current = client.get(
                f"/v1/conversations/{conversation_id}", headers=headers()
            ).json()
            if current["active_turn"] and current["active_turn"]["status"] == "waiting":
                break
            time.sleep(0.01)
        else:
            pytest.fail("turn did not enter the waiting review state")

        current = client.get(
            f"/v1/conversations/{conversation_id}", headers=headers()
        ).json()
        waiting_event = next(
            entry
            for entry in reversed(current["entries"])
            if entry["type"] == "status" and entry["state"] == "waiting"
        )
        assert waiting_event["detail"]["approval_id"] == (
            "approval_aB3dE5gH7jK9mN2pQ4rS6tUv"
        )

        unauthorized = client.post(
            review_url,
            json={"approval_id": "approval_aB3dE5gH7jK9mN2pQ4rS6tUv", "approved": True},
        )
        wrong_request = client.post(
            review_url,
            headers=headers(),
            json={"approval_id": "approval_wrongwrongwrong1", "approved": True},
        )
        accepted = client.post(
            review_url,
            headers=headers(),
            json={"approval_id": "approval_aB3dE5gH7jK9mN2pQ4rS6tUv", "approved": True},
        )
        stale = client.post(
            review_url,
            headers=headers(),
            json={"approval_id": "approval_aB3dE5gH7jK9mN2pQ4rS6tUv", "approved": False},
        )

    assert unauthorized.status_code == 401
    assert wrong_request.status_code == 409
    assert accepted.status_code == 200
    assert accepted.json()["turn_id"] == turn_id
    assert accepted.json()["status"] == "waiting"
    assert stale.status_code == 409
    assert gateway.reviews == [(turn_id, "approval_aB3dE5gH7jK9mN2pQ4rS6tUv", True)]


def test_cancel_is_idempotent_and_retry_appends_linked_turn(tmp_path):
    gateway = FakeGateway()
    with make_client(tmp_path, gateway) as client:
        created = send_message(client).json()
        turn_id = created["turn_id"]
        first_cancel = client.post(turn_url(client, turn_id, "cancel"), headers=headers())
        second_cancel = client.post(turn_url(client, turn_id, "cancel"), headers=headers())
        retry = client.post(
            turn_url(client, turn_id, "retry"),
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
        first = client.post(turn_url(client, turn_id, "cancel"), headers=headers())
        second = client.post(turn_url(client, turn_id, "cancel"), headers=headers())
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
            "/v1/conversations/events/stream?once=true",
            headers={**headers(), "Last-Event-ID": str(first_id)},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame.startswith("id:")]
    ids = [int(frame.splitlines()[0].removeprefix("id: ")) for frame in frames]
    assert ids == sorted(ids)
    assert ids and all(event_id > first_id for event_id in ids)
    assert created["turn_id"] in response.text


def test_scoped_stream_envelope_tracks_runtime_quarantine_and_return_to_ready(tmp_path):
    store = JobOsStateStore(tmp_path / "recovery-stream.db")
    store.initialize(owner_device_id="device-a")
    conversation_id = store.first_active_conversation_id("device-a")
    scoped = store.conversation_store(conversation_id)
    created = scoped.create_turn(
        text="Run remote work",
        context={},
        idempotency_key="recovery-stream-key",
        actor_id="device-a",
    )
    turn_id = str(created["turn_id"])
    assert scoped.settle_active_turn(
        turn_id,
        "failed",
        event_type="error",
        summary="Transport lost",
        detail={"reason": "transport_lost", "retry": True},
        quarantine=True,
    )

    quarantined = store.all_conversation_events_after(0, owner_device_id="device-a")[-1]
    assert quarantined["conversation_id"] == conversation_id
    assert quarantined["recovery_state"] == "quarantined"
    quarantine_event_id = int(quarantined["event"]["event_id"])

    assert scoped.clear_recovery_turn_if_current(turn_id)
    scoped.append_event(
        turn_id=turn_id,
        event_type="status",
        state="interrupted",
        summary="Remote recovery confirmed",
        detail={"recovery_confirmed": True},
    )
    ready = store.all_conversation_events_after(quarantine_event_id, owner_device_id="device-a")
    assert len(ready) == 1
    assert ready[0]["recovery_state"] == "ready"


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


def test_offline_start_reconnects_while_idle(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        gateway = IdleReconnectingGateway()
        service = ConversationService(store, gateway)
        await service.start()
        for _ in range(60):
            if gateway.connection_state == "online":
                break
            await asyncio.sleep(0.05)
        assert gateway.connection_state == "online"
        assert gateway.start_attempts >= 2
        await service.close()

    asyncio.run(scenario())


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
        return (
            store.stored_session_id(),
            store.turn_record(result.turn_id),
            service.snapshot().recovery_state,
        )

    accepted_id, accepted_turn, accepted_recovery = asyncio.run(scenario(acknowledge=True))
    rejected_id, rejected_turn, rejected_recovery = asyncio.run(scenario(acknowledge=False))

    assert accepted_id == "new-durable-id"
    assert accepted_turn["status"] == "running"
    assert accepted_recovery == "ready"
    assert rejected_id == "new-durable-id"
    assert rejected_turn["status"] == "failed"
    assert rejected_recovery == "quarantined"
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


def test_definitive_pre_submit_failure_can_retry_without_remote_cleanup(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()

        class PreSubmitFailureGateway(FakeGateway):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            async def submit_turn(self, text, context):
                self.attempts += 1
                if self.attempts <= 2:
                    raise DefinitivePreSubmitError(
                        "Hermes session isolation could not be verified"
                    )
                await super().submit_turn(text, context)

        gateway = PreSubmitFailureGateway()
        service = ConversationService(store, gateway)
        failed = await service.send(
            SendMessageRequest(
                text="Explain why the browser tool was unavailable",
                idempotency_key="definitive-pre-submit-failure",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        failed_record = store.turn_record(failed.turn_id)
        assert failed_record is not None
        recovery_after_failure = store.recovery_turn_id()
        retried = await service.retry(
            failed.turn_id,
            RetryTurnRequest(idempotency_key="definitive-pre-submit-retry"),
            actor_id="device-a",
        )
        return failed_record, recovery_after_failure, retried, gateway

    failed_record, recovery_after_failure, retried, gateway = asyncio.run(scenario())

    assert failed_record["status"] == "failed"
    assert recovery_after_failure is None
    assert retried is not None
    assert retried.source_turn_id == failed_record["turn_id"]
    assert gateway.attempts == 3
    assert len(gateway.submissions) == 1


def test_definitive_pre_submit_attachment_loss_is_repaired_before_failing_turn(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        store.save_stored_session_id("ordinary-session")

        class LostAttachmentGateway(FakeGateway):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            async def submit_turn(self, text, context):
                self.attempts += 1
                if self.attempts == 1:
                    raise DefinitivePreSubmitError("Hermes session is not attached")
                await super().submit_turn(text, context)

        gateway = LostAttachmentGateway()
        service = ConversationService(store, gateway)
        result = await service.send(
            SendMessageRequest(
                text="Explain the failed Save Job turn",
                idempotency_key="lost-attachment-follow-up",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        return result, gateway, store

    result, gateway, store = asyncio.run(scenario())

    record = store.turn_record(result.turn_id)
    assert record is not None
    assert record["status"] == "running"
    assert gateway.attempts == 2
    assert gateway.session_requests == ["ordinary-session", "stored-session"]
    assert len(gateway.submissions) == 1
    assert store.recovery_turn_id() is None


def test_rotated_durable_id_reconciles_without_transcript_or_raw_metadata(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    store.save_stored_session_id("stored-original")
    gateway = RotatingSessionGateway()

    asyncio.run(ConversationService(store, gateway)._consume_gateway_events())

    assert store.stored_session_id() == "stored-rotated"
    snapshot = store.conversation_snapshot()
    assert len(snapshot["entries"]) == 1
    assert snapshot["entries"][0]["summary"] == "Agent session reconciled"
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
            turn_url(restarted, stale_turn_id, "retry"),
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
            turn_url(client, created["turn_id"], "retry"),
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
        recovered = client.post(turn_url(client, created["turn_id"], "cancel"), headers=headers())
        after = client.get("/v1/conversations/current", headers=headers()).json()

    assert quarantined["active_turn"]["turn_id"] == created["turn_id"]
    assert quarantined["recovery_state"] == "recovering"
    assert any(entry["detail"].get("recovery_pending") is True for entry in quarantined["entries"])
    assert overlap.status_code == 409
    assert recovered.json()["status"] == "interrupted"
    assert after["active_turn"] is None
    assert after["recovery_state"] == "ready"
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
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        scoped_store = store.conversation_store(store.first_active_conversation_id())
        original_request_cancel = scoped_store.request_turn_cancel

        def request_turn_cancel(turn_id):
            result = original_request_cancel(turn_id)
            cancel_requested.set()
            return result

        scoped_store.request_turn_cancel = request_turn_cancel
        entered_submit = asyncio.Event()
        release_submit = asyncio.Event()

        class BarrierGateway(FakeGateway):
            async def submit_turn(self, text, context):
                entered_submit.set()
                await release_submit.wait()
                raise ConnectionError("Authorization: Basic dispatch-secret")

        gateway = BarrierGateway()
        service = ConversationService(scoped_store, gateway)
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
    scoped_store = store.conversation_store(store.first_active_conversation_id())
    original_settle = scoped_store.settle_active_turn

    def record_transition(changed_turn_id, status, **kwargs):
        changed = original_settle(changed_turn_id, status, **kwargs)
        if changed:
            transitions.append((changed_turn_id, status))
        return changed

    scoped_store.settle_active_turn = record_transition

    asyncio.run(ConversationService(scoped_store, gateway)._consume_gateway_events())

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
    scoped_store = store.conversation_store(store.first_active_conversation_id())
    original_settle = scoped_store.settle_active_turn

    def record_transition(changed_turn_id, status, **kwargs):
        changed = original_settle(changed_turn_id, status, **kwargs)
        if changed:
            transitions.append((changed_turn_id, status))
        return changed

    scoped_store.settle_active_turn = record_transition

    asyncio.run(ConversationService(scoped_store, gateway)._consume_gateway_events())

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


class RecordingGatewayFactory:
    def __init__(self):
        self.gateways = {}

    def create(self, conversation_id):
        gateway = FakeGateway(session_scope=f"-{conversation_id}")
        self.gateways[conversation_id] = gateway
        return gateway


def test_manager_starts_all_services_concurrently_with_a_barrier(tmp_path):
    async def scenario():
        entered = 0
        both_entered = asyncio.Event()
        release = asyncio.Event()

        class BarrierStartGateway(FakeGateway):
            async def start(self):
                nonlocal entered
                entered += 1
                if entered == 2:
                    both_entered.set()
                await release.wait()
                self.started = True

        class Factory:
            def create(self, conversation_id):
                return BarrierStartGateway()

        store = JobOsStateStore(tmp_path / "manager-start-barrier.db")
        store.initialize()
        store.create_conversation(actor_id="device-a")
        manager = ConversationManager(store, Factory())
        startup = asyncio.create_task(manager.start())
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        assert entered == 2
        release.set()
        await asyncio.wait_for(startup, timeout=1)
        await manager.close()

    asyncio.run(scenario())


def test_manager_list_deduplicates_and_parallelizes_binding_health_probes(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "manager-list-probes.db")
        store.initialize(owner_device_id="device-a")
        first = store.create_conversation(
            actor_id="device-a",
            connected_agent_id=f"jagent_{'a' * 32}",
            provider="hermes",
            model_id="(FAKE)-shared-model",
            reasoning_effort="medium",
        )
        duplicate = store.create_conversation(
            actor_id="device-a",
            connected_agent_id=f"jagent_{'a' * 32}",
            provider="hermes",
            model_id="(FAKE)-shared-model",
            reasoning_effort="medium",
        )
        unique = store.create_conversation(
            actor_id="device-a",
            connected_agent_id=f"jagent_{'b' * 32}",
            provider="codex",
            model_id="(FAKE)-unique-model",
            reasoning_effort="high",
        )
        for value in (first, duplicate, unique):
            store.conversation_store(str(value["conversation_id"])).complete_provisioning(
                f"(FAKE)-session-{value['conversation_id']}"
            )

        calls: list[tuple[str, str, str, str, str | None]] = []
        both_entered = asyncio.Event()
        release = asyncio.Event()

        async def probe(agent_id, provider, model_id, effort, fingerprint):
            calls.append((agent_id, provider, model_id, effort, fingerprint))
            if len(calls) == 2:
                both_entered.set()
            await release.wait()
            return None

        class Factory:
            def create(self, conversation_id):
                del conversation_id
                return FakeGateway()

        manager = ConversationManager(
            store,
            Factory(),
            connected_agent_binding_unavailability=probe,
        )
        listing = asyncio.create_task(manager.list(owner_device_id="device-a"))
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        assert len(calls) == 2
        release.set()
        summaries = (await asyncio.wait_for(listing, timeout=1)).conversations
        assert len(summaries) == 4
        assert [item.availability.state for item in summaries[1:]] == ["ready"] * 3

    asyncio.run(scenario())


def test_manager_list_isolates_failed_health_probe_and_keeps_unbound_chat_ready(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "manager-list-probe-failure.db")
        store.initialize(owner_device_id="device-a")
        bound = store.create_conversation(
            actor_id="device-a",
            connected_agent_id=f"jagent_{'c' * 32}",
            provider="codex",
            model_id="(FAKE)-model",
            reasoning_effort="medium",
        )
        store.conversation_store(str(bound["conversation_id"])).complete_provisioning(
            "(FAKE)-session-bound"
        )

        async def probe(*_args):
            raise RuntimeError("controlled provider probe failure")

        class Factory:
            def create(self, conversation_id):
                del conversation_id
                return FakeGateway()

        manager = ConversationManager(
            store,
            Factory(),
            connected_agent_binding_unavailability=probe,
        )
        unbound = await manager.create(actor_id="device-a")
        summaries = (await manager.list(owner_device_id="device-a")).conversations
        assert len(summaries) == 3
        unbound_summary = next(
            item for item in summaries if item.conversation_id == unbound.conversation_id
        )
        bound_summary = next(
            item
            for item in summaries
            if item.conversation_id == str(bound["conversation_id"])
        )
        assert unbound_summary.binding is None
        assert unbound_summary.availability.state == "ready"
        assert unbound_summary.availability.reason is None
        assert bound_summary.availability.state == "locked"
        assert bound_summary.availability.reason == "AGENT_PROVIDER_UNAVAILABLE"

    asyncio.run(scenario())


def test_replay_uses_transaction_flag_when_other_conversation_advances_cursor(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "explicit-replay.db")
        store.initialize()
        first_id = store.first_active_conversation_id("primary-device")
        second_id = str(store.create_conversation(actor_id="primary-device")["conversation_id"])
        gateway = FakeGateway()
        service = ConversationService(store.conversation_store(first_id), gateway, first_id)
        command = SendMessageRequest(text="Exactly once", idempotency_key="exact-replay-key")
        first = await service.send(command, actor_id="primary-device", context={})
        store.conversation_store(second_id).append_event(
            turn_id=None,
            event_type="status",
            state="working",
            summary="Unrelated global event",
        )
        replay = await service.send(command, actor_id="primary-device", context={})
        assert first.created is True
        assert replay.created is False
        assert len(gateway.submissions) == 1

    asyncio.run(scenario())


def test_failed_gateway_construction_archives_row_and_releases_cap(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "construction-rollback.db")
        store.initialize()

        class Factory:
            fail = True

            def create(self, conversation_id):
                if self.fail:
                    self.fail = False
                    raise RuntimeError("controlled construction failure")
                return FakeGateway()

        factory = Factory()
        manager = ConversationManager(store, factory)
        # Preserve the migrated initial service and fail only the requested addition.
        factory.fail = False
        await manager.start()
        factory.fail = True
        with pytest.raises(RuntimeError, match="controlled construction failure"):
            await manager.create(actor_id="primary-device")
        assert len(store.list_active_conversations()) == 1
        recovered = await manager.create(actor_id="primary-device")
        assert recovered.position == 2
        assert len((await manager.list(owner_device_id="primary-device")).conversations) == 2
        await manager.close()

    asyncio.run(scenario())


def test_startup_factory_failure_preserves_durable_row_and_starts_sibling(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "startup-construction.db")
        store.initialize(owner_device_id="device-a")
        broken_id = store.first_active_conversation_id("device-a")
        sibling_id = str(store.create_conversation(actor_id="device-a")["conversation_id"])

        class Factory:
            def create(self, conversation_id):
                if conversation_id == broken_id:
                    raise RuntimeError("controlled startup factory failure")
                return FakeGateway()

        manager = ConversationManager(store, Factory())
        await manager.start()
        assert [
            item["conversation_id"]
            for item in store.list_active_conversations(owner_device_id="device-a")
        ] == [broken_id, sibling_id]
        assert manager.get(sibling_id, owner_device_id="device-a").gateway.started is True
        listed = (await manager.list(owner_device_id="device-a")).conversations
        assert [item.conversation_id for item in listed] == [broken_id, sibling_id]
        assert listed[0].connection.state == "offline"
        assert listed[1].connection.state == "online"
        with pytest.raises(ConversationNotFound, match="Conversation not found"):
            manager.get(broken_id, owner_device_id="device-a")
        await manager.close()

    asyncio.run(scenario())


def test_startup_hermes_cwd_construction_failure_preserves_row_and_starts_sibling(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "startup-cwd.db")
        store.initialize(owner_device_id="device-a")
        broken_id = store.first_active_conversation_id("device-a")
        sibling_id = str(store.create_conversation(actor_id="device-a")["conversation_id"])
        hermes = HermesGatewayFactory(
            url="ws://127.0.0.1:9119/api/ws",
            token="synthetic-token",
            cwd=tmp_path / "missing-hermes-cwd",
        )

        class Factory:
            def create(self, conversation_id):
                return (
                    hermes.create(conversation_id)
                    if conversation_id == broken_id
                    else FakeGateway()
                )

        manager = ConversationManager(store, Factory())
        await manager.start()
        assert [
            item["conversation_id"]
            for item in store.list_active_conversations(owner_device_id="device-a")
        ] == [broken_id, sibling_id]
        assert manager.get(sibling_id, owner_device_id="device-a").gateway.started is True
        await manager.close()

    asyncio.run(scenario())


def test_event_consumer_failure_is_quarantined_and_restarted(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "consumer-restart.db")
        store.initialize()

        class FailingOnceGateway(FakeGateway):
            def __init__(self):
                super().__init__()
                self.stream_attempts = 0
                self.restarted = asyncio.Event()

            async def stream_events(self):
                self.stream_attempts += 1
                if self.stream_attempts == 1:
                    raise RuntimeError("controlled normalization failure")
                self.restarted.set()
                while True:
                    await asyncio.sleep(10)
                    if False:
                        yield GatewayEvent(event_type="status", state="working", summary="")

        gateway = FailingOnceGateway()
        conversation_id = store.first_active_conversation_id()
        service = ConversationService(
            store.conversation_store(conversation_id), gateway, conversation_id
        )
        service._event_consumer_restart_delay = 0
        task = asyncio.create_task(service._supervise_gateway_events())
        await asyncio.wait_for(gateway.restarted.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        snapshot = service.snapshot()
        assert gateway.stream_attempts == 2
        assert snapshot.entries[-1]["detail"]["reason"] == "event_consumer_failure"

    asyncio.run(scenario())


def test_manager_start_failure_keeps_sibling_available_and_reconnect_alive(tmp_path):
    async def scenario():
        class Factory:
            def __init__(self):
                self.created = []

            def create(self, conversation_id):
                gateway = IdleReconnectingGateway() if not self.created else FakeGateway()
                self.created.append(gateway)
                return gateway

        store = JobOsStateStore(tmp_path / "manager-start-failure.db")
        store.initialize()
        second_id = str(store.create_conversation(actor_id="device-a")["conversation_id"])
        factory = Factory()
        manager = ConversationManager(store, factory)
        await manager.start()
        await asyncio.sleep(0.05)
        assert manager.get(second_id).gateway.connection_state == "online"
        assert factory.created[0].start_attempts >= 2
        assert factory.created[0].connection_state == "online"
        await manager.close()

    asyncio.run(scenario())


def test_manager_submissions_overlap_at_the_gateway_boundary(tmp_path):
    async def scenario():
        entered = 0
        both_entered = asyncio.Event()
        release = asyncio.Event()

        class BarrierSubmitGateway(FakeGateway):
            async def submit_turn(self, text, context):
                nonlocal entered
                entered += 1
                if entered == 2:
                    both_entered.set()
                await release.wait()
                await super().submit_turn(text, context)

        class Factory:
            def __init__(self):
                self.gateways = {}

            def create(self, conversation_id):
                gateway = BarrierSubmitGateway()
                self.gateways[conversation_id] = gateway
                return gateway

        store = JobOsStateStore(tmp_path / "manager-submit-barrier.db")
        store.initialize()
        second_id = str(store.create_conversation(actor_id="device-a")["conversation_id"])
        first_id = store.first_active_conversation_id("primary-device")
        manager = ConversationManager(store, Factory())
        await manager.start()
        submissions = asyncio.gather(
            manager.get(first_id).send(
                SendMessageRequest(text="First", idempotency_key="barrier-first-1"),
                actor_id="device-a",
                context={},
            ),
            manager.get(second_id).send(
                SendMessageRequest(text="Second", idempotency_key="barrier-second-1"),
                actor_id="device-b",
                context={},
            ),
        )
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        assert entered == 2
        release.set()
        await asyncio.wait_for(submissions, timeout=1)
        await manager.close()

    asyncio.run(scenario())


def test_manager_close_attempts_every_service_once_when_one_raises(tmp_path):
    async def scenario():
        close_counts: dict[str, int] = {}

        class CloseGateway(FakeGateway):
            def __init__(self, conversation_id):
                super().__init__()
                self.conversation_id = conversation_id

            async def close(self):
                close_counts[self.conversation_id] = close_counts.get(self.conversation_id, 0) + 1
                if len(close_counts) == 1:
                    raise RuntimeError("controlled close failure")

        class Factory:
            def create(self, conversation_id):
                return CloseGateway(conversation_id)

        store = JobOsStateStore(tmp_path / "manager-close-isolation.db")
        store.initialize()
        store.create_conversation(actor_id="device-a")
        manager = ConversationManager(store, Factory())
        await manager.start()
        with pytest.raises(RuntimeError, match="controlled close failure"):
            await manager.close()
        assert sorted(close_counts.values()) == [1, 1]
        assert manager.services == ()

    asyncio.run(scenario())


def test_conversation_openapi_constrains_paths_and_documents_scoped_sse(tmp_path):
    with make_client(tmp_path) as client:
        schema = client.app.openapi()

    conversation = schema["paths"]["/v1/conversations/{conversation_id}"]["get"]["parameters"][0][
        "schema"
    ]
    cancel_parameters = schema["paths"][
        "/v1/conversations/{conversation_id}/turns/{turn_id}/cancel"
    ]["post"]["parameters"]
    stream = schema["paths"]["/v1/conversations/events/stream"]["get"]["responses"]["200"]

    assert conversation["pattern"] == "^conv_[A-Za-z0-9_-]{1,128}$"
    assert conversation["maxLength"] == 133
    assert cancel_parameters[1]["schema"]["pattern"] == "^turn_[A-Za-z0-9_-]{1,128}$"
    assert cancel_parameters[1]["schema"]["maxLength"] == 133
    assert set(stream["content"]) == {"text/event-stream"}
    assert stream["content"]["text/event-stream"]["schema"] == {"type": "string"}
    assert "conversation_id" in stream["content"]["text/event-stream"]["example"]
    assert "recovery_state" in stream["content"]["text/event-stream"]["example"]
    collection_errors = schema["paths"]["/v1/conversations"]["post"]["responses"]
    item = schema["paths"]["/v1/conversations/{conversation_id}"]
    assert "409" in collection_errors
    assert "404" in item["get"]["responses"]
    assert {"404", "409"}.issubset(item["delete"]["responses"])


def test_manager_runs_conversations_concurrently_and_shuts_each_gateway(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "manager.db")
        store.initialize()
        second = store.create_conversation(actor_id="device-a")
        factory = RecordingGatewayFactory()
        manager = ConversationManager(store, factory)
        await manager.start()
        first_id = store.first_active_conversation_id("primary-device")
        second_id = str(second["conversation_id"])
        first, second_service = manager.get(first_id), manager.get(second_id)
        first_turn, second_turn = await asyncio.gather(
            first.send(
                SendMessageRequest(text="First concurrent", idempotency_key="parallel-first-01"),
                actor_id="device-a",
                context={},
            ),
            second_service.send(
                SendMessageRequest(text="Second concurrent", idempotency_key="parallel-second-1"),
                actor_id="device-a",
                context={},
            ),
        )
        assert factory.gateways[first_id].submissions[0][1].turn_id == first_turn.turn_id
        assert factory.gateways[second_id].submissions[0][1].turn_id == second_turn.turn_id
        factory.gateways[first_id]._events = [
            GatewayEvent(
                event_type="activity",
                state="working",
                summary="First-only event",
                turn_id=first_turn.turn_id,
                source_event_id="interleaved-first",
            )
        ]
        factory.gateways[second_id]._events = [
            GatewayEvent(
                event_type="activity",
                state="working",
                summary="Second-only event",
                turn_id=second_turn.turn_id,
                source_event_id="interleaved-second",
            )
        ]
        await asyncio.gather(
            first._consume_gateway_events(), second_service._consume_gateway_events()
        )
        assert "Second-only event" not in json.dumps(first.snapshot().entries)
        assert "First-only event" not in json.dumps(second_service.snapshot().entries)
        await first.cancel(first_turn.turn_id)
        assert factory.gateways[first_id].interruptions == [first_turn.turn_id]
        assert factory.gateways[second_id].interruptions == []
        assert second_service.snapshot().active_turn["turn_id"] == second_turn.turn_id
        await manager.close()
        return tuple(factory.gateways.values())

    gateways = asyncio.run(scenario())
    assert all(gateway.closed for gateway in gateways)


def test_manager_detaches_active_agent_turn_before_account_replacement(tmp_path):
    async def scenario():
        agent_id = f"jagent_{'d' * 32}"
        store = JobOsStateStore(tmp_path / "manager-account-replacement.db")
        store.initialize(owner_device_id="device-a")
        created = store.create_conversation(
            actor_id="device-a",
            connected_agent_id=agent_id,
            provider="codex",
            model_id="(FAKE)-replacement-model",
            reasoning_effort="medium",
        )
        conversation_id = str(created["conversation_id"])
        scoped = store.conversation_store(conversation_id)
        scoped.complete_provisioning("(FAKE)-replacement-provider-session")
        class CancellationFailureFactory(RecordingGatewayFactory):
            def create(self, conversation_id):
                gateway = InterruptFailureGateway(
                    session_scope=f"-{conversation_id}"
                )
                self.gateways[conversation_id] = gateway
                return gateway

        factory = CancellationFailureFactory()
        manager = ConversationManager(store, factory)
        await manager.start()
        turn = scoped.create_turn(
            text="Stop before replacing credentials",
            context={},
            idempotency_key="(FAKE)-replacement-active-turn",
            actor_id="device-a",
        )

        original_gateway = factory.gateways[conversation_id]
        assert store.lock_connected_agent_chats(
            agent_id, "AUTH_ACCOUNT_REPLACEMENT_REQUIRED"
        ) == 1
        await manager.detach_connected_agent(agent_id)
        replay = await manager.replay_bound(
            conversation_id=conversation_id,
            actor_id="device-a",
            agent_summary={
                "id": agent_id,
                "provider": "codex",
                "display_name": "Codex",
                "avatar_id": "spark",
            },
        )

        assert original_gateway.interruptions == [turn["turn_id"]]
        assert original_gateway.closed is True
        assert replay.availability.state == "locked"
        assert manager.get(conversation_id).snapshot().conversation_id == conversation_id

    asyncio.run(scenario())


def test_manager_does_not_start_locked_provider_sessions_but_keeps_history_readable(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "manager-locked-recovery.db")
        store.initialize(owner_device_id="device-a")
        created = store.create_conversation(
            actor_id="device-a",
            connected_agent_id=f"jagent_{'c' * 32}",
            provider="codex",
            model_id="(FAKE)-locked-model",
            reasoning_effort="medium",
        )
        conversation_id = str(created["conversation_id"])
        scoped = store.conversation_store(conversation_id)
        scoped.complete_provisioning("(FAKE)-locked-provider-session")
        turn = scoped.create_turn(
            text="Do not recover under replacement credentials",
            context={},
            idempotency_key="(FAKE)-locked-recovery-turn",
            actor_id="device-a",
        )
        scoped.save_stored_session_id("(FAKE)-locked-provider-session")
        assert store.lock_connected_agent_chats(
            f"jagent_{'c' * 32}", "AUTH_ACCOUNT_REPLACEMENT_REQUIRED"
        ) == 1

        factory = RecordingGatewayFactory()
        manager = ConversationManager(store, factory)
        await manager.start()
        try:
            service = manager.get(conversation_id, owner_device_id="device-a")
            active_turn = service.snapshot().active_turn
            assert active_turn is not None
            assert active_turn["turn_id"] == turn["turn_id"]
            assert factory.gateways[conversation_id].started is False
            assert factory.gateways[conversation_id].interruptions == []
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_manager_recovers_each_persisted_active_turn_independently(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "manager-recovery.db")
        store.initialize()
        first_id = store.first_active_conversation_id("primary-device")
        second_id = str(store.create_conversation(actor_id="device-a")["conversation_id"])
        turn_ids = []
        for conversation_id in (first_id, second_id):
            scoped = store.conversation_store(conversation_id)
            turn = scoped.create_turn(
                text=f"Recover {conversation_id}",
                context={},
                idempotency_key=f"recover-{conversation_id}",
                actor_id="device-a",
            )
            scoped.save_stored_session_id(f"stored-{conversation_id}")
            turn_ids.append(str(turn["turn_id"]))
        factory = RecordingGatewayFactory()
        manager = ConversationManager(store, factory)
        await manager.start()
        try:
            assert factory.gateways[first_id].interruptions == [turn_ids[0]]
            assert factory.gateways[second_id].interruptions == [turn_ids[1]]
            assert manager.get(first_id).snapshot().active_turn is None
            assert manager.get(second_id).snapshot().active_turn is None
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_api_collection_cap_archive_and_global_sse_envelopes(tmp_path):
    factory = RecordingGatewayFactory()
    with make_client(tmp_path, gateway_factory=factory) as client:
        first_id = current_id(client)
        second = client.post("/v1/conversations", headers=headers())
        second_id = second.json()["conversation_id"]
        first_turn = client.post(
            f"/v1/conversations/{first_id}/messages",
            headers=headers(),
            json={"text": "First tab", "idempotency_key": "api-first-tab-01"},
        )
        second_turn = client.post(
            f"/v1/conversations/{second_id}/messages",
            headers=headers(),
            json={"text": "Second tab", "idempotency_key": "api-second-tab-1"},
        )
        stream = client.get("/v1/conversations/events/stream?once=true", headers=headers())
        for _ in range(3):
            assert client.post("/v1/conversations", headers=headers()).status_code == 201
        capped = client.post("/v1/conversations", headers=headers())
        blocked_archive = client.delete(f"/v1/conversations/{second_id}", headers=headers())
        client.post(
            f"/v1/conversations/{second_id}/turns/{second_turn.json()['turn_id']}/cancel",
            headers=headers(),
        )
        archived = client.delete(f"/v1/conversations/{second_id}", headers=headers())
        listed = client.get("/v1/conversations", headers=headers()).json()["conversations"]

    assert first_turn.status_code == second_turn.status_code == 201
    assert f'"conversation_id":"{first_id}"' in stream.text
    assert f'"conversation_id":"{second_id}"' in stream.text
    assert capped.status_code == 409
    assert blocked_archive.status_code == 409
    assert archived.status_code == 204
    assert [item["position"] for item in listed] == [1, 2, 3, 4]


def test_conversation_routes_share_profile_authority_across_authenticated_devices(tmp_path):
    remote_token = "remote-device-token-value"
    repository, artifact_gateway = adapt_job_hunter_facade(FakeJobFacade())
    app = create_app(
        Settings(
            device_token=TOKEN,
            mcp_token="test-mcp-trusted-token",
            device_id="primary-device",
            device_credentials=(DeviceCredential(device_id="remote-device", token=remote_token),),
            state_db_path=tmp_path / "owned-conversations.db",
        ),
        job_repository=repository,
        artifact_gateway=artifact_gateway,
        agent_gateway_factory=RecordingGatewayFactory(),
    )
    remote_headers = {"Authorization": f"Bearer {remote_token}"}
    with TestClient(app) as client:
        primary_id = current_id(client)
        initial_remote = client.get("/v1/conversations", headers=remote_headers).json()[
            "conversations"
        ]
        remote = client.post("/v1/conversations", headers=remote_headers)
        primary_turn = client.post(
            f"/v1/conversations/{primary_id}/messages",
            headers=headers(),
            json={"text": "Primary-owned", "idempotency_key": "primary-owner-key"},
        )
        cross_get = client.get(f"/v1/conversations/{primary_id}", headers=remote_headers)
        cross_cancel = client.post(
            f"/v1/conversations/{primary_id}/turns/{primary_turn.json()['turn_id']}/cancel",
            headers=remote_headers,
        )
        for index in range(3):
            request_headers = headers() if index % 2 == 0 else remote_headers
            assert client.post("/v1/conversations", headers=request_headers).status_code == 201
        primary_list = client.get("/v1/conversations", headers=headers()).json()["conversations"]
        remote_list = client.get("/v1/conversations", headers=remote_headers).json()[
            "conversations"
        ]
        primary_cap = client.post("/v1/conversations", headers=headers())
        remote_cap = client.post("/v1/conversations", headers=remote_headers)
        stream = client.get("/v1/conversations/events/stream?once=true", headers=remote_headers)

    assert [item["conversation_id"] for item in initial_remote] == [primary_id]
    assert remote.status_code == primary_turn.status_code == 201
    assert cross_get.status_code == 200
    assert cross_cancel.status_code == 200
    assert [item["conversation_id"] for item in primary_list] == [
        item["conversation_id"] for item in remote_list
    ]
    assert [item["position"] for item in primary_list] == [1, 2, 3, 4, 5]
    assert [item["title"] for item in primary_list] == [f"Session {value}" for value in range(1, 6)]
    assert primary_cap.status_code == remote_cap.status_code == 409
    assert f'"conversation_id":"{primary_id}"' in stream.text


def test_ambiguous_attachment_survives_restart_without_blind_retry(tmp_path):
    async def scenario():
        path = tmp_path / "jobos.db"
        store = JobOsStateStore(path)
        store.initialize()
        store.save_stored_session_id("previous-session")
        gateway = AmbiguousAttachmentGateway()
        service = ConversationService(store, gateway)
        created = await service.send(
            SendMessageRequest(
                text="Do not submit this twice",
                idempotency_key="browser-save-ambiguous-attachment-turn",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )

        turn = store.turn_record(created.turn_id)
        assert turn is not None
        assert turn["status"] == "waiting"
        assert store.recovery_turn_id() == created.turn_id
        assert store.stored_session_id() == "previous-session"
        assert gateway.submissions == []

        restarted_store = JobOsStateStore(path)
        restarted_store.initialize()
        restarted_gateway = FakeGateway()
        restarted = ConversationService(restarted_store, restarted_gateway)
        cancelled = await restarted.cancel(created.turn_id)
        assert cancelled is not None
        assert cancelled.status == "interrupted"
        assert restarted_gateway.submissions == []
        assert restarted_gateway.interruptions == [created.turn_id]
        entries = restarted_store.conversation_snapshot()["entries"]
        assert sum(entry["type"] == "turn" for entry in entries) == 1

    asyncio.run(scenario())


def test_authorized_turn_scope_finishes_before_cancellation_settles(tmp_path):
    async def scenario():
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        gateway = FakeGateway()
        service = ConversationService(store, gateway)
        created = await service.send(
            SendMessageRequest(
                text="Run one scoped operation",
                idempotency_key="turn-scope-cancellation",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def authorized_operation():
            async with service.turn_scope_lease():
                entered.set()
                await release.wait()

        operation = asyncio.create_task(authorized_operation())
        await entered.wait()
        cancellation = asyncio.create_task(service.cancel(created.turn_id))
        await asyncio.sleep(0)
        assert not cancellation.done()
        release.set()
        await operation
        cancelled = await cancellation
        assert cancelled is not None
        assert cancelled.status == "interrupted"

    asyncio.run(scenario())


def test_normalized_event_envelope_is_durable_across_store_restart(tmp_path):
    async def scenario():
        path = tmp_path / "jobos.db"
        store = JobOsStateStore(path)
        store.initialize()
        gateway = FakeGateway()
        service = ConversationService(store, gateway, profile_id="jprof_test")
        created = await service.send(
            SendMessageRequest(
                text="Persist normalized events",
                idempotency_key="normalized-event-replay",
            ),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        gateway._events = [
            GatewayEvent(
                event_type="status",
                state="working",
                summary="Agent turn started",
                turn_id=created.turn_id,
                source_event_id="normalized-start",
                normalized_kind="turn_started",
                profile_id="jprof_test",
                conversation_id=service.conversation_id,
                sequence=1,
                timestamp="2026-08-24T20:00:00Z",
            ),
            GatewayEvent(
                event_type="assistant_message",
                state="completed",
                summary="Done",
                turn_id=created.turn_id,
                source_event_id="normalized-complete",
                normalized_kind="turn_completed",
                profile_id="jprof_test",
                conversation_id=service.conversation_id,
                sequence=2,
                timestamp="2026-08-24T20:00:01Z",
            ),
        ]
        await service._consume_gateway_events()

        restarted = JobOsStateStore(path)
        restarted.initialize()
        entries = restarted.conversation_snapshot()["entries"]
        normalized = [entry for entry in entries if entry.get("normalized_kind")]
        assert [entry["normalized_kind"] for entry in normalized] == [
            "turn_started",
            "turn_completed",
        ]
        assert [entry["sequence"] for entry in normalized] == [1, 2]
        assert all(entry["profile_id"] == "jprof_test" for entry in normalized)
        assert all(
            entry["conversation_id"] == service.conversation_id for entry in normalized
        )
        assert [entry["timestamp"] for entry in normalized] == [
            "2026-08-24T20:00:00Z",
            "2026-08-24T20:00:01Z",
        ]

    asyncio.run(scenario())

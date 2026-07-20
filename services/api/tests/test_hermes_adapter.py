import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from jobos_api.agent_gateway import AgentContext
from jobos_api.conversations import ConversationService, SendMessageRequest
from jobos_api.hermes_adapter import HermesWebSocketGateway
from jobos_api.state_store import JobOsStateStore

TOKEN = "protected-dashboard-token-value"


class FakeWebSocket:
    def __init__(self, responder, *, ready=True) -> None:
        self.responder = responder
        self.incoming = asyncio.Queue()
        self.requests = []
        self.closed = False
        if ready:
            self.incoming.put_nowait(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "gateway.ready",
                            "payload": {"version": "0.18.2"},
                        },
                    }
                )
            )

    async def send(self, raw):
        request = json.loads(raw)
        self.requests.append(request)
        for frame in self.responder(request):
            self.incoming.put_nowait(json.dumps(frame))

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def close(self):
        self.closed = True
        self.incoming.put_nowait(None)


class FakeConnector:
    def __init__(self, socket):
        self.socket = socket
        self.calls = []

    async def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.socket


def result(request, value):
    return {"jsonrpc": "2.0", "id": request["id"], "result": value}


def rpc_error(request, code, message="sensitive server detail"):
    return {
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {"code": code, "message": message},
    }


def event(event_type, session_id, payload=None):
    params = {"type": event_type, "session_id": session_id}
    if payload is not None:
        params["payload"] = payload
    return {"jsonrpc": "2.0", "method": "event", "params": params}


async def next_non_connection(stream):
    while True:
        item = await asyncio.wait_for(anext(stream), 1)
        if item.event_type != "connection":
            return item


def test_adapter_scopes_create_submit_events_and_interrupt_to_job_hunter(tmp_path):
    async def scenario():
        def responder(request):
            method = request["method"]
            if method == "session.create":
                return [
                    result(
                        request,
                        {
                            "stored_session_id": "stored-1",
                            "session_id": "live-1",
                            "info": {
                                "profile_name": "job-hunter",
                                "cwd": str(tmp_path),
                            },
                        },
                    )
                ]
            if method == "prompt.submit":
                return [
                    result(request, {"status": "streaming"}),
                    event(
                        "tool.start",
                        "live-1",
                        {"tool_id": "tool-1", "name": "shell"},
                    ),
                    event(
                        "message.complete",
                        "live-1",
                        {"status": "complete", "text": "Done safely"},
                    ),
                ]
            return [result(request, {"status": "interrupted"})]

        socket = FakeWebSocket(responder)
        connector = FakeConnector(socket)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=connector,
        )
        await gateway.start()
        stored, live = await gateway.create_or_resume_conversation(None)
        await gateway.submit_turn(
            "A harmless test",
            AgentContext("turn-1", "job-1", {"selected_preset": "review"}),
        )
        stream = gateway.stream_events()
        activity = await next_non_connection(stream)
        complete = await next_non_connection(stream)
        await gateway.interrupt_turn("turn-1")
        await gateway.close()

        connected_url, connect_options = connector.calls[0]
        assert urlsplit(connected_url).path == "/api/ws"
        assert parse_qs(urlsplit(connected_url).query) == {"token": [TOKEN]}
        assert "additional_headers" not in connect_options
        assert (stored, live) == ("stored-1", "live-1")
        assert socket.requests[0]["method"] == "session.create"
        assert socket.requests[0]["params"] == {
            "profile": "job-hunter",
            "source": "jobos",
            "cwd": str(tmp_path),
            "close_on_disconnect": False,
        }
        assert socket.requests[1]["method"] == "prompt.submit"
        assert socket.requests[1]["params"]["session_id"] == "live-1"
        assert socket.requests[1]["params"]["text"].startswith("A harmless test")
        assert len(socket.requests) == 2
        assert activity.activity_id == "tool-1"
        assert complete.state == "completed"
        assert complete.summary == "Done safely"

    asyncio.run(scenario())


def test_two_serialized_service_prompts_reuse_one_verified_live_attachment(tmp_path):
    async def scenario():
        prompt_count = 0

        def responder(request):
            nonlocal prompt_count
            if request["method"] == "session.create":
                return [
                    result(
                        request,
                        {
                            "stored_session_id": "stored-1",
                            "session_id": "live-1",
                            "info": {
                                "lazy": True,
                                "profile_name": "default",
                                "cwd": str(tmp_path),
                            },
                        },
                    ),
                    event(
                        "session.info",
                        "live-1",
                        {
                            "profile_name": "job-hunter",
                            "cwd": str(tmp_path),
                            "token": "raw-runtime-secret",
                            "tools": {"unsafe": "raw-tool-metadata"},
                        },
                    ),
                ]
            if request["method"] == "prompt.submit":
                prompt_count += 1
                frames = [
                    result(request, {"status": "streaming"}),
                    event(
                        "message.complete",
                        "live-1",
                        {"status": "complete", "text": f"Answer {prompt_count}"},
                    ),
                ]
                if prompt_count == 1:
                    frames.append(
                        event(
                            "session.info",
                            "live-1",
                            {
                                "running": False,
                                "profile_name": "default",
                                "cwd": str(tmp_path),
                            },
                        )
                    )
                return frames
            raise AssertionError(f"unexpected RPC: {request['method']}")

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        service = ConversationService(store, gateway)
        await service.start()
        try:
            first = await service.send(
                SendMessageRequest(text="First prompt", idempotency_key="first-prompt-key"),
                actor_id="device-a",
                context={"selected_job_id": None, "workspace": {}},
            )
            for _ in range(50):
                if store.turn_record(first.turn_id)["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            assert store.turn_record(first.turn_id)["status"] == "completed"
            await asyncio.sleep(0.01)

            second = await service.send(
                SendMessageRequest(text="Second prompt", idempotency_key="second-prompt-key"),
                actor_id="device-a",
                context={"selected_job_id": None, "workspace": {}},
            )
            for _ in range(50):
                if store.turn_record(second.turn_id)["status"] == "completed":
                    break
                await asyncio.sleep(0.01)

            snapshot = store.conversation_snapshot()
        finally:
            await service.close()

        methods = [request["method"] for request in socket.requests]
        assert methods == ["session.create", "prompt.submit", "prompt.submit"]
        assert store.turn_record(first.turn_id)["status"] == "completed"
        assert store.turn_record(second.turn_id)["status"] == "completed"
        serialized = json.dumps(snapshot)
        assert "Answer 1" in serialized
        assert "Answer 2" in serialized
        assert TOKEN not in serialized
        assert "raw-runtime-secret" not in serialized
        assert "raw-tool-metadata" not in serialized
        assert TOKEN not in repr(gateway)

    asyncio.run(scenario())


def test_verified_live_attachment_fast_path_preserves_state_and_rotated_id(tmp_path):
    async def scenario():
        socket = FakeWebSocket(
            lambda request: [
                result(
                    request,
                    {
                        "stored_session_id": "stored-1",
                        "session_id": "live-1",
                        "info": {
                            "profile_name": "job-hunter",
                            "cwd": str(tmp_path),
                        },
                    },
                )
            ]
        )
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation(None)
        reconciliation = gateway.normalize_frame(
            event(
                "session.info",
                "live-1",
                {
                    "stored_session_id": "stored-rotated",
                    "profile_name": "job-hunter",
                    "cwd": str(tmp_path),
                },
            )
        )
        gateway._active_turn_id = "turn-in-flight"
        gateway._pending_session_info["candidate"] = (True, None)
        isolation_event = gateway._session_isolation_event

        attached = await gateway.create_or_resume_conversation("stored-rotated")

        assert reconciliation is not None
        assert attached == ("stored-rotated", "live-1")
        assert [request["method"] for request in socket.requests] == ["session.create"]
        assert gateway._active_turn_id == "turn-in-flight"
        assert gateway._session_isolation_state == "verified"
        assert gateway._session_isolation_event is isolation_event
        assert gateway._pending_session_info == {"candidate": (True, None)}
        await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("isolation_state", ["unverified", "failed"])
def test_unverified_or_failed_isolation_cannot_use_live_attachment_fast_path(
    tmp_path, isolation_state
):
    async def scenario():
        def responder(request):
            if request["method"] == "session.create":
                return [
                    result(
                        request,
                        {
                            "stored_session_id": "stored-1",
                            "session_id": "live-1",
                            "info": {
                                "lazy": True,
                                "profile_name": "default",
                                "cwd": str(tmp_path),
                            },
                        },
                    )
                ]
            return [
                result(
                    request,
                    {
                        "session_key": "stored-1",
                        "session_id": "live-2",
                        "info": {
                            "profile_name": "job-hunter",
                            "cwd": str(tmp_path),
                        },
                    },
                )
            ]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation(None)
        if isolation_state == "failed":
            gateway.normalize_frame(
                event(
                    "session.info",
                    "live-1",
                    {"profile_name": "wrong-profile", "cwd": str(tmp_path)},
                )
            )

        attached = await gateway.create_or_resume_conversation("stored-1")
        await gateway.close()

        assert attached == ("stored-1", "live-2")
        assert [request["method"] for request in socket.requests] == [
            "session.create",
            "session.resume",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("stored_session_id", [None, "stored-different"])
def test_missing_or_different_stored_id_does_not_reuse_live_attachment(tmp_path, stored_session_id):
    async def scenario():
        create_count = 0

        def responder(request):
            nonlocal create_count
            if request["method"] == "session.create":
                create_count += 1
                stored = f"stored-{create_count}"
            else:
                stored = str(request["params"]["session_id"])
            return [
                result(
                    request,
                    {
                        "stored_session_id": stored,
                        "session_id": f"live-{len(socket.requests)}",
                        "info": {
                            "profile_name": "job-hunter",
                            "cwd": str(tmp_path),
                        },
                    },
                )
            ]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation(None)
        await gateway.create_or_resume_conversation(stored_session_id)
        await gateway.close()

        expected = "session.create" if stored_session_id is None else "session.resume"
        assert [request["method"] for request in socket.requests] == [
            "session.create",
            expected,
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("durable_field", ["session_key", "resumed"])
@pytest.mark.parametrize("live_id", ["live-1", "live-2"])
def test_adapter_resumes_stored_identity_after_fresh_transport(tmp_path, durable_field, live_id):
    async def scenario():
        socket = FakeWebSocket(
            lambda request: [result(request, {durable_field: "stored-1", "session_id": live_id})]
        )
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        resumed = await gateway.create_or_resume_conversation("stored-1")
        await gateway.close()

        assert resumed == ("stored-1", live_id)
        assert socket.requests[0]["method"] == "session.resume"
        assert socket.requests[0]["params"] == {
            "session_id": "stored-1",
            "profile": "job-hunter",
            "source": "jobos",
            "close_on_disconnect": False,
        }

    asyncio.run(scenario())


def test_adapter_reconnects_and_resumes_after_transport_loss(tmp_path):
    async def scenario():
        first = FakeWebSocket(
            lambda request: [
                result(request, {"stored_session_id": "stored-1", "session_id": "live-1"})
            ]
        )
        second = FakeWebSocket(
            lambda request: [result(request, {"session_key": "stored-1", "session_id": "live-2"})]
        )

        class ReconnectingConnector:
            def __init__(self):
                self.sockets = iter((first, second))
                self.calls = 0

            async def __call__(self, url, **kwargs):
                self.calls += 1
                return next(self.sockets)

        connector = ReconnectingConnector()
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=connector,
        )
        await gateway.start()
        await gateway.create_or_resume_conversation(None)
        first.incoming.put_nowait(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        resumed = await gateway.create_or_resume_conversation("stored-1")
        await gateway.close()

        assert connector.calls == 2
        assert resumed == ("stored-1", "live-2")
        assert second.requests[0]["method"] == "session.resume"

    asyncio.run(scenario())


def test_gateway_streams_connectivity_transitions_and_mid_turn_disconnect_terminal(tmp_path):
    async def scenario():
        def responder(request):
            if request["method"] == "session.create":
                return [
                    result(
                        request,
                        {
                            "stored_session_id": "stored-1",
                            "session_id": "live-1",
                            "info": {"profile_name": "job-hunter", "cwd": str(tmp_path)},
                        },
                    )
                ]
            return [result(request, {"status": "streaming"})]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation(None)
        await gateway.submit_turn("safe", AgentContext("turn-1", None, {}))
        socket.incoming.put_nowait(None)
        stream = gateway.stream_events()
        observed = [await asyncio.wait_for(anext(stream), 1) for _ in range(4)]
        await gateway.close()
        return observed

    observed = asyncio.run(scenario())
    connections = [
        item.detail.get("agent_connection") for item in observed if item.event_type == "connection"
    ]
    terminal = [item for item in observed if item.turn_id == "turn-1" and item.state == "failed"]
    assert connections == ["connecting", "online", "offline"]
    assert len(terminal) == 1
    assert terminal[0].event_type == "error"
    assert terminal[0].detail == {"actionable": True, "reason": "transport_lost", "retry": True}


def test_recovery_resume_interrupts_without_requiring_in_memory_active_turn(tmp_path):
    async def scenario():
        def responder(request):
            if request["method"] == "session.resume":
                return [
                    result(
                        request,
                        {
                            "session_key": "stored-1",
                            "session_id": "live-recovered",
                            "info": {"profile_name": "job-hunter", "cwd": str(tmp_path)},
                        },
                    )
                ]
            return [result(request, {"status": "interrupted"})]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.recover_active_turn("stored-1", "turn-persisted")
        await gateway.close()
        return socket.requests

    requests = asyncio.run(scenario())
    assert [request["method"] for request in requests] == ["session.resume", "session.interrupt"]
    assert requests[1]["params"] == {"session_id": "live-recovered"}


def test_reconnect_resets_session_isolation_verification(tmp_path):
    async def scenario():
        first = FakeWebSocket(
            lambda request: [
                result(
                    request,
                    {
                        "session_key": "stored-1",
                        "session_id": "live-1",
                        "info": {
                            "profile_name": "job-hunter",
                            "cwd": str(tmp_path),
                        },
                    },
                )
            ]
        )
        second = FakeWebSocket(
            lambda request: [result(request, {"session_key": "stored-1", "session_id": "live-2"})]
        )

        class ReconnectingConnector:
            def __init__(self):
                self.sockets = iter((first, second))

            async def __call__(self, url, **kwargs):
                return next(self.sockets)

        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=0.01,
            connector=ReconnectingConnector(),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation("stored-1")
        first.incoming.put_nowait(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        await gateway.create_or_resume_conversation("stored-1")
        with pytest.raises(RuntimeError) as caught:
            await gateway.submit_turn("must not submit", AgentContext("turn-1", None, {}))
        await gateway.close()

        assert [request["method"] for request in second.requests] == ["session.resume"]
        assert str(caught.value) == "Hermes session isolation could not be verified"

    asyncio.run(scenario())


def test_adapter_does_not_require_hermes_event_ids_sequences_or_replay(tmp_path):
    gateway = HermesWebSocketGateway(
        url="ws://127.0.0.1:9119/api/ws",
        token=TOKEN,
        cwd=tmp_path,
        request_timeout=0.01,
    )
    gateway._active_turn_id = "turn-1"

    first = gateway.normalize_frame(
        {"type": "message.delta", "event_id": "one", "sequence": 1, "delta": "hello"}
    )
    same_optional_metadata = gateway.normalize_frame(
        {"type": "message.delta", "event_id": "one", "sequence": 1, "delta": "again"}
    )

    assert first is not None
    assert same_optional_metadata is not None
    assert same_optional_metadata.summary == "again"
    assert gateway.normalize_frame({"type": "message.delta", "sequence": 0}) is not None
    assert gateway.normalize_frame({"unexpected": "raw-secret", "sequence": 99}) is None
    valid_after_malformed = gateway.normalize_frame(
        {"type": "status.update", "event_id": "two", "sequence": 2, "message": "Working"}
    )
    assert valid_after_malformed is not None
    assert TOKEN not in repr(gateway)


def test_real_envelopes_unwrap_payload_and_reject_another_live_session(tmp_path):
    gateway = HermesWebSocketGateway(url="ws://127.0.0.1:9119/api/ws", token=TOKEN, cwd=tmp_path)
    gateway._live_session_id = "live-1"
    gateway._active_turn_id = "turn-1"

    accepted = gateway.normalize_frame(event("message.delta", "live-1", {"text": "hello"}))
    rejected = gateway.normalize_frame(
        event("message.delta", "live-2", {"text": "not this conversation"})
    )

    assert accepted is not None
    assert accepted.summary == "hello"
    assert accepted.detail == {
        "text": "hello",
        "type": "message.delta",
        "redacted": True,
    }
    assert accepted.turn_id == "turn-1"
    assert rejected is None


def test_real_events_without_synthetic_ids_or_sequences_are_not_dropped(tmp_path):
    gateway = HermesWebSocketGateway(url="ws://127.0.0.1:9119/api/ws", token=TOKEN, cwd=tmp_path)
    gateway._live_session_id = "live-1"
    gateway._active_turn_id = "turn-1"

    first = gateway.normalize_frame(event("message.delta", "live-1", {"text": "one"}))
    second = gateway.normalize_frame(event("message.delta", "live-1", {"text": "two"}))

    assert first is not None and first.summary == "one"
    assert second is not None and second.summary == "two"


def test_real_status_and_error_fields_are_read_from_payload(tmp_path):
    gateway = HermesWebSocketGateway(url="ws://127.0.0.1:9119/api/ws", token=TOKEN, cwd=tmp_path)
    gateway._live_session_id = "live-1"
    gateway._active_turn_id = "turn-1"

    status = gateway.normalize_frame(
        event("status.update", "live-1", {"kind": "process", "text": "Working"})
    )
    error = gateway.normalize_frame(event("error", "live-1", {"message": "Provider unavailable"}))
    repeated_error = gateway.normalize_frame(
        event("error", "live-1", {"message": "Provider unavailable"})
    )

    assert status is not None
    assert status.summary == "Working"
    assert status.state == "working"
    assert error is not None
    assert error.detail["message"] == "Provider unavailable"
    assert error.turn_id == "turn-1"
    assert gateway._active_turn_id is None
    assert repeated_error is None


@pytest.mark.parametrize(
    ("event_type", "payload", "summary"),
    [
        ("clarify.request", {"question": "Choose one", "choices": ["A", "B"]}, "Choose one"),
        (
            "approval.request",
            {"message": "Approve command?", "choices": ["once", "deny"]},
            "Approve command?",
        ),
        (
            "sudo.request",
            {"prompt": "Administrator access required"},
            "Administrator access required",
        ),
        ("secret.request", {"prompt": "Credential required"}, "Credential required"),
    ],
)
def test_real_waiting_events_use_exact_names_and_payload_fields(
    tmp_path, event_type, payload, summary
):
    gateway = HermesWebSocketGateway(url="ws://127.0.0.1:9119/api/ws", token=TOKEN, cwd=tmp_path)
    gateway._live_session_id = "live-1"
    gateway._active_turn_id = "turn-1"

    normalized = gateway.normalize_frame(event(event_type, "live-1", payload))

    assert normalized is not None
    assert normalized.state == "waiting"
    assert normalized.summary == summary
    assert normalized.detail["type"] == event_type
    assert "session_id" not in normalized.detail
    assert "stored_session_id" not in normalized.detail
    assert "profile" not in normalized.detail
    assert "cwd" not in normalized.detail


def test_gateway_ready_envelope_is_metadata_not_transcript(tmp_path):
    gateway = HermesWebSocketGateway(url="ws://127.0.0.1:9119/api/ws", token=TOKEN, cwd=tmp_path)

    assert gateway.normalize_frame(event("gateway.ready", "", {"version": "0.18.2"})) is None


def test_message_complete_keeps_turn_id_then_clears_association_exactly_once(tmp_path):
    gateway = HermesWebSocketGateway(url="ws://127.0.0.1:9119/api/ws", token=TOKEN, cwd=tmp_path)
    gateway._live_session_id = "live-1"
    gateway._active_turn_id = "turn-1"
    complete = event("message.complete", "live-1", {"status": "complete", "text": "Done"})

    normalized = gateway.normalize_frame(complete)
    repeated = gateway.normalize_frame(complete)

    assert normalized is not None
    assert normalized.turn_id == "turn-1"
    assert normalized.state == "completed"
    assert gateway._active_turn_id is None
    assert repeated is None


def test_connection_url_preserves_query_and_never_exposes_token_in_repr_or_errors(tmp_path):
    class FailingConnector:
        def __init__(self):
            self.url = None
            self.kwargs = None

        async def __call__(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs
            raise OSError(f"could not connect to {url}")

    async def scenario():
        connector = FailingConnector()
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws?profile=job-hunter&token=stale",
            token=TOKEN,
            cwd=tmp_path,
            connector=connector,
        )

        with pytest.raises(ConnectionError) as caught:
            await gateway.start()

        parsed = urlsplit(connector.url)
        assert parse_qs(parsed.query) == {
            "profile": ["job-hunter"],
            "token": [TOKEN],
        }
        assert connector.kwargs is not None
        assert "additional_headers" not in connector.kwargs
        assert TOKEN not in repr(gateway)
        assert TOKEN not in str(caught.value)
        assert TOKEN not in repr(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    asyncio.run(scenario())


def test_adapter_normalizes_waiting_file_render_errors_and_redacts_output(tmp_path):
    gateway = HermesWebSocketGateway(url="ws://127.0.0.1:9119/api/ws", token=TOKEN, cwd=tmp_path)
    gateway._active_turn_id = "turn-1"

    gateway._live_session_id = "live-1"
    waiting = gateway.normalize_frame(
        event("clarify.request", "live-1", {"question": "Choose one"})
    )
    changed = gateway.normalize_frame(
        {"type": "file.changed", "event_id": "b", "path": "resume/source.md"}
    )
    rendered = gateway.normalize_frame(
        {"type": "render.complete", "event_id": "c", "artifact": "resume.pdf"}
    )
    error = gateway.normalize_frame({"error": {"authorization": "Bearer raw-secret"}})
    gateway._active_turn_id = "turn-1"
    message = gateway.normalize_frame(
        {
            "type": "message.complete",
            "event_id": "d",
            "status": "complete",
            "text": "token=raw-secret",
        }
    )

    assert waiting.state == "waiting"
    assert changed.summary == "Updated file"
    assert rendered.summary == "Rendered artifact"
    assert error.state == "failed"
    assert "raw-secret" not in str(error)
    assert "raw-secret" not in str(message)


def test_adapter_timeout_is_safe_and_does_not_disclose_credentials(tmp_path):
    async def scenario():
        socket = FakeWebSocket(lambda request: [])
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=0.01,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        try:
            await gateway.create_or_resume_conversation(None)
        except TimeoutError as error:
            assert TOKEN not in str(error)
        else:
            raise AssertionError("expected timeout")
        await gateway.close()

    asyncio.run(scenario())


def test_lazy_create_waits_for_matching_session_info_before_prompt_submit(tmp_path):
    async def scenario():
        def responder(request):
            if request["method"] == "session.create":
                return [
                    result(
                        request,
                        {
                            "stored_session_id": "stored-1",
                            "session_id": "live-1",
                            "info": {
                                "lazy": True,
                                "profile_name": "default",
                                "cwd": str(tmp_path),
                            },
                        },
                    ),
                    event(
                        "session.info",
                        "live-1",
                        {
                            "running": False,
                            "profile_name": "job-hunter",
                            "cwd": str(tmp_path),
                            "token": "raw-secret",
                            "tools": {"danger": "raw-metadata"},
                        },
                    ),
                ]
            return [result(request, {"status": "streaming"})]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        assert await gateway.create_or_resume_conversation(None) == ("stored-1", "live-1")
        await gateway.submit_turn(
            "A harmless test",
            AgentContext("turn-1", None, {}),
        )
        reconciled = await next_non_connection(gateway.stream_events())
        await gateway.close()

        assert [request["method"] for request in socket.requests] == [
            "session.create",
            "prompt.submit",
        ]
        assert reconciled.event_type == "reconciliation"
        assert reconciled.summary == ""
        assert reconciled.detail == {"running": False}
        assert "job-hunter" not in str(reconciled)
        assert str(tmp_path) not in str(reconciled)
        assert "raw-secret" not in str(reconciled)
        assert "raw-metadata" not in str(reconciled)

    asyncio.run(scenario())


def test_lazy_create_still_rejects_wrong_immediate_cwd(tmp_path):
    async def scenario():
        socket = FakeWebSocket(
            lambda request: [
                result(
                    request,
                    {
                        "stored_session_id": "stored-unsafe",
                        "session_id": "live-unsafe",
                        "info": {
                            "lazy": True,
                            "profile_name": "default",
                            "cwd": str(tmp_path / "wrong-cwd"),
                        },
                    },
                )
            ]
        )
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        with pytest.raises(RuntimeError) as caught:
            await gateway.create_or_resume_conversation(None)
        await gateway.close()

        assert str(caught.value) == "Hermes returned an unsafe session response"
        assert str(tmp_path) not in str(caught.value)
        assert "wrong-cwd" not in str(caught.value)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("info_payload", "unsafe_value"),
    [
        ({"profile_name": "wrong-profile", "cwd": "approved"}, "wrong-profile"),
        ({"profile_name": "job-hunter", "cwd": "wrong"}, "wrong-cwd"),
        ({"profile_name": "job-hunter"}, "missing-cwd"),
        ({"cwd": "approved"}, "missing-profile"),
        (None, "missing-event"),
    ],
)
def test_lazy_create_missing_or_wrong_session_info_prevents_submit(
    tmp_path, info_payload, unsafe_value
):
    async def scenario():
        def responder(request):
            if request["method"] != "session.create":
                return [result(request, {"status": "streaming"})]
            frames = [
                result(
                    request,
                    {
                        "stored_session_id": "stored-unsafe",
                        "session_id": "live-unsafe",
                        "info": {
                            "lazy": True,
                            "profile_name": "default-launch-profile",
                            "cwd": str(tmp_path),
                        },
                    },
                )
            ]
            if info_payload is not None:
                payload = dict(info_payload)
                if payload.get("cwd") == "approved":
                    payload["cwd"] = str(tmp_path)
                elif payload.get("cwd") == "wrong":
                    payload["cwd"] = str(tmp_path / "wrong-cwd")
                frames.append(event("session.info", "live-unsafe", payload))
            return frames

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=0.01,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation(None)
        with pytest.raises(RuntimeError) as caught:
            await gateway.submit_turn("must not submit", AgentContext("turn-1", None, {}))
        await gateway.close()

        assert [request["method"] for request in socket.requests] == ["session.create"]
        message = str(caught.value)
        assert message == "Hermes session isolation could not be verified"
        assert TOKEN not in message
        assert str(tmp_path) not in message
        assert unsafe_value not in message
        assert "stored-unsafe" not in message
        assert "live-unsafe" not in message

    asyncio.run(scenario())


def test_lazy_create_ignores_wrong_session_info_and_accepts_matching_event(tmp_path):
    async def scenario():
        def responder(request):
            if request["method"] == "session.create":
                return [
                    result(
                        request,
                        {
                            "stored_session_id": "stored-1",
                            "session_id": "live-1",
                            "info": {
                                "lazy": True,
                                "profile_name": "default",
                                "cwd": str(tmp_path),
                            },
                        },
                    ),
                    event(
                        "session.info",
                        "another-live-session",
                        {"profile_name": "wrong-profile", "cwd": "/private/unsafe"},
                    ),
                    event(
                        "session.info",
                        "live-1",
                        {"profile_name": "job-hunter", "cwd": str(tmp_path)},
                    ),
                ]
            return [result(request, {"status": "streaming"})]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation(None)
        await gateway.submit_turn("safe", AgentContext("turn-1", None, {}))
        await gateway.close()

        assert [request["method"] for request in socket.requests] == [
            "session.create",
            "prompt.submit",
        ]

    asyncio.run(scenario())


def test_create_nonmatching_immediate_profile_stays_unverified(tmp_path):
    async def scenario():
        socket = FakeWebSocket(
            lambda request: [
                result(
                    request,
                    {
                        "stored_session_id": "stored-unsafe",
                        "session_id": "live-unsafe",
                        "info": {
                            "profile_name": "dashboard-default",
                            "cwd": str(tmp_path),
                        },
                    },
                )
            ]
        )
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=0.01,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation(None)
        with pytest.raises(RuntimeError) as caught:
            await gateway.submit_turn("must not submit", AgentContext("turn-1", None, {}))
        await gateway.close()

        assert [request["method"] for request in socket.requests] == ["session.create"]
        assert str(caught.value) == "Hermes session isolation could not be verified"
        assert "dashboard-default" not in str(caught.value)
        assert "stored-unsafe" not in str(caught.value)
        assert "live-unsafe" not in str(caught.value)

    asyncio.run(scenario())


def test_create_fails_closed_on_wrong_immediate_cwd(tmp_path):
    async def scenario():
        socket = FakeWebSocket(
            lambda request: [
                result(
                    request,
                    {
                        "stored_session_id": "stored-unsafe",
                        "session_id": "live-unsafe",
                        "info": {"cwd": "/tmp/hermes-silent-fallback"},
                    },
                )
            ]
        )
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        with pytest.raises(RuntimeError) as caught:
            await gateway.create_or_resume_conversation(None)
        await gateway.close()

        assert str(caught.value) == "Hermes returned an unsafe session response"
        assert "/tmp/hermes-silent-fallback" not in str(caught.value)
        assert "stored-unsafe" not in str(caught.value)
        assert "live-unsafe" not in str(caught.value)

    asyncio.run(scenario())


def test_resume_fails_closed_on_wrong_profile_or_cwd(tmp_path):
    async def scenario():
        socket = FakeWebSocket(
            lambda request: [
                result(
                    request,
                    {
                        "session_key": "stored-1",
                        "session_id": "live-1",
                        "info": {
                            "lazy": True,
                            "profile_name": "wrong-profile",
                            "cwd": str(tmp_path / "wrong-cwd"),
                        },
                    },
                )
            ]
        )
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        with pytest.raises(RuntimeError) as caught:
            await gateway.create_or_resume_conversation("stored-1")
        await gateway.close()
        assert "wrong-profile" not in str(caught.value)
        assert "wrong-cwd" not in str(caught.value)

    asyncio.run(scenario())


def test_resume_with_verified_profile_and_cwd_submits_without_session_info(tmp_path):
    async def scenario():
        def responder(request):
            if request["method"] == "session.resume":
                return [
                    result(
                        request,
                        {
                            "session_key": "stored-1",
                            "session_id": "live-1",
                            "info": {
                                "profile_name": "job-hunter",
                                "cwd": str(tmp_path),
                            },
                        },
                    )
                ]
            return [result(request, {"status": "streaming"})]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation("stored-1")
        await gateway.submit_turn("safe", AgentContext("turn-1", None, {}))
        await gateway.close()

        assert [request["method"] for request in socket.requests] == [
            "session.resume",
            "prompt.submit",
        ]

    asyncio.run(scenario())


def test_persisted_resume_waits_for_job_hunter_info_then_submits_and_completes(tmp_path):
    async def scenario():
        def responder(request):
            if request["method"] == "session.resume":
                return [
                    result(
                        request,
                        {
                            "session_key": "stored-1",
                            "session_id": "live-resumed",
                            "info": {
                                "profile_name": "default",
                                "cwd": str(tmp_path),
                            },
                        },
                    ),
                    event(
                        "session.info",
                        "live-resumed",
                        {
                            "running": False,
                            "profile_name": "job-hunter",
                            "cwd": str(tmp_path),
                        },
                    ),
                ]
            if request["method"] == "prompt.submit":
                return [
                    result(request, {"status": "streaming"}),
                    event(
                        "message.complete",
                        "live-resumed",
                        {"status": "complete", "text": "Resume completed"},
                    ),
                ]
            raise AssertionError(f"unexpected RPC: {request['method']}")

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        store = JobOsStateStore(tmp_path / "jobos.db")
        store.initialize()
        store.save_stored_session_id("stored-1")
        service = ConversationService(store, gateway)
        await service.start()
        try:
            sent = await service.send(
                SendMessageRequest(text="Continue", idempotency_key="resume-after-relaunch"),
                actor_id="device-a",
                context={"selected_job_id": None, "workspace": {}},
            )
            for _ in range(50):
                if store.turn_record(sent.turn_id)["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            snapshot = store.conversation_snapshot()
        finally:
            await service.close()

        assert [request["method"] for request in socket.requests] == [
            "session.resume",
            "prompt.submit",
        ]
        assert socket.requests[0]["params"] == {
            "session_id": "stored-1",
            "profile": "job-hunter",
            "source": "jobos",
            "close_on_disconnect": False,
        }
        assert socket.requests[1]["params"]["session_id"] == "live-resumed"
        assert store.turn_record(sent.turn_id)["status"] == "completed"
        serialized = json.dumps(snapshot)
        assert "Resume completed" in serialized
        assert "job-hunter" not in serialized
        assert str(tmp_path) not in serialized

    asyncio.run(scenario())


def test_resume_still_rejects_wrong_immediate_cwd(tmp_path):
    async def scenario():
        socket = FakeWebSocket(
            lambda request: [
                result(
                    request,
                    {
                        "session_key": "stored-1",
                        "session_id": "live-unsafe",
                        "info": {
                            "profile_name": "default",
                            "cwd": str(tmp_path / "wrong-cwd"),
                        },
                    },
                )
            ]
        )
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        with pytest.raises(RuntimeError) as caught:
            await gateway.create_or_resume_conversation("stored-1")
        await gateway.close()

        assert str(caught.value) == "Hermes returned an unsafe session response"
        assert [request["method"] for request in socket.requests] == ["session.resume"]
        assert str(tmp_path) not in str(caught.value)
        assert "wrong-cwd" not in str(caught.value)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "deferred_payload",
    [
        None,
        {"profile_name": "wrong-profile", "cwd": "approved"},
        {"profile_name": "job-hunter"},
        {"profile_name": "job-hunter", "cwd": "wrong"},
    ],
)
def test_resume_wrong_or_missing_deferred_info_blocks_prompt(tmp_path, deferred_payload):
    async def scenario():
        def responder(request):
            if request["method"] != "session.resume":
                return [result(request, {"status": "streaming"})]
            frames = [
                result(
                    request,
                    {
                        "session_key": "stored-1",
                        "session_id": "live-resumed",
                        "info": {
                            "profile_name": "default",
                            "cwd": str(tmp_path),
                        },
                    },
                )
            ]
            if deferred_payload is not None:
                payload = dict(deferred_payload)
                if payload.get("cwd") == "approved":
                    payload["cwd"] = str(tmp_path)
                elif payload.get("cwd") == "wrong":
                    payload["cwd"] = str(tmp_path / "wrong-cwd")
                frames.append(event("session.info", "live-resumed", payload))
            return frames

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=0.01,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation("stored-1")
        with pytest.raises(RuntimeError) as caught:
            await gateway.submit_turn("must not submit", AgentContext("turn-1", None, {}))
        await gateway.close()

        assert str(caught.value) == "Hermes session isolation could not be verified"
        assert [request["method"] for request in socket.requests] == ["session.resume"]

    asyncio.run(scenario())


def test_resume_ignores_wrong_session_info_before_matching_verification(tmp_path):
    async def scenario():
        def responder(request):
            if request["method"] == "session.resume":
                return [
                    result(
                        request,
                        {
                            "session_key": "stored-1",
                            "session_id": "live-resumed",
                            "info": {
                                "profile_name": "default",
                                "cwd": str(tmp_path),
                            },
                        },
                    ),
                    event(
                        "session.info",
                        "another-live-session",
                        {"profile_name": "wrong-profile", "cwd": "/private/unsafe"},
                    ),
                    event(
                        "session.info",
                        "live-resumed",
                        {"profile_name": "job-hunter", "cwd": str(tmp_path)},
                    ),
                ]
            return [result(request, {"status": "streaming"})]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation("stored-1")
        await gateway.submit_turn("safe", AgentContext("turn-1", None, {}))
        await gateway.close()

        assert [request["method"] for request in socket.requests] == [
            "session.resume",
            "prompt.submit",
        ]

    asyncio.run(scenario())


def test_launch_context_session_info_cannot_revoke_verified_live_session(tmp_path):
    async def scenario():
        def responder(request):
            if request["method"] == "prompt.submit":
                return [result(request, {"status": "streaming"})]
            return [
                result(
                    request,
                    {
                        "session_key": "stored-1",
                        "session_id": "live-1",
                        "info": {
                            "profile_name": "job-hunter",
                            "cwd": str(tmp_path),
                        },
                    },
                ),
                event(
                    "session.info",
                    "live-1",
                    {
                        "running": False,
                        "stored_session_id": "stored-rotated",
                        "profile_name": "default",
                        "cwd": str(tmp_path),
                    },
                ),
            ]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        await gateway.create_or_resume_conversation("stored-1")
        await gateway.submit_turn("safe", AgentContext("turn-1", None, {}))
        await gateway.close()

        assert [request["method"] for request in socket.requests] == [
            "session.resume",
            "prompt.submit",
        ]
        assert gateway._stored_session_id == "stored-rotated"

    asyncio.run(scenario())


def test_resume_4007_creates_replacement_but_other_rpc_errors_do_not(tmp_path):
    async def replacement_scenario():
        def responder(request):
            if request["method"] == "session.resume":
                return [rpc_error(request, 4007, "missing token=raw-secret")]
            return [
                result(
                    request,
                    {"stored_session_id": "stored-2", "session_id": "live-2"},
                )
            ]

        socket = FakeWebSocket(responder)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        attached = await gateway.create_or_resume_conversation("stored-missing")
        await gateway.close()
        assert attached == ("stored-2", "live-2")
        assert [request["method"] for request in socket.requests] == [
            "session.resume",
            "session.create",
        ]

    async def other_error_scenario():
        socket = FakeWebSocket(
            lambda request: [rpc_error(request, 5000, "Authorization: raw-secret")]
        )
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=1,
            connector=FakeConnector(socket),
        )
        await gateway.start()
        with pytest.raises(RuntimeError) as caught:
            await gateway.create_or_resume_conversation("stored-1")
        await gateway.close()
        assert len(socket.requests) == 1
        assert "raw-secret" not in str(caught.value)
        assert "authorization" not in str(caught.value).lower()

    asyncio.run(replacement_scenario())
    asyncio.run(other_error_scenario())


def test_session_info_is_allowlisted_reconciliation_not_transcript(tmp_path):
    gateway = HermesWebSocketGateway(url="ws://127.0.0.1:9119/api/ws", token=TOKEN, cwd=tmp_path)
    gateway._live_session_id = "live-1"
    reconciled = gateway.normalize_frame(
        event(
            "session.info",
            "live-1",
            {
                "running": False,
                "stored_session_id": "stored-rotated",
                "profile_name": "job-hunter",
                "cwd": str(tmp_path),
                "token": "raw-secret",
                "tools": {"danger": "raw-payload"},
            },
        )
    )

    assert reconciled is not None
    assert reconciled.event_type == "reconciliation"
    assert reconciled.detail == {
        "running": False,
        "stored_session_id": "stored-rotated",
    }
    assert "raw-secret" not in str(reconciled)
    assert "raw-payload" not in str(reconciled)


def test_gateway_waits_for_ready_before_rpc_and_times_out_safely(tmp_path):
    async def scenario():
        socket = FakeWebSocket(lambda request: [], ready=False)
        gateway = HermesWebSocketGateway(
            url="ws://127.0.0.1:9119/api/ws",
            token=TOKEN,
            cwd=tmp_path,
            request_timeout=0.01,
            connector=FakeConnector(socket),
        )
        with pytest.raises(TimeoutError) as caught:
            await gateway.start()
        assert socket.requests == []
        assert gateway.connection_state == "offline"
        assert TOKEN not in str(caught.value)
        await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url",
    [
        "ws://example.com:9119/api/ws",
        "wss://192.0.2.10/api/ws?token=stale-secret",
    ],
)
def test_stable_token_query_rejects_non_loopback_urls_without_disclosure(tmp_path, url):
    with pytest.raises(ValueError) as caught:
        HermesWebSocketGateway(url=url, token=TOKEN, cwd=tmp_path)
    assert TOKEN not in str(caught.value)
    assert "stale-secret" not in str(caught.value)

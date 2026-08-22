import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.capabilities import (
    BrowserCommandRequest,
    BrowserCommandResponse,
    CapabilityBroker,
)
from jobos_api.private_adapters.job_hunter import adapt_job_hunter_facade
from jobos_api.settings import DeviceCredential, Settings

TOKEN = "browser-device-token"
REMOTE_TOKEN = "remote-browser-device-token"


class ActiveTurnGateway:
    connection_state = "online"

    async def start(self):
        return None

    async def create_or_resume_conversation(self, stored_session_id):
        return "stored-browser-test", "live-browser-test"

    async def submit_turn(self, text, context):
        return None

    async def detach_conversation(self):
        return None

    async def stream_events(self):
        if False:
            yield None

    async def interrupt_turn(self, turn_id):
        return None

    async def recover_active_turn(self, stored_session_id, turn_id):
        return None

    async def close(self):
        return None


class ActiveTurnGatewayFactory:
    def create(self, conversation_id):
        return ActiveTurnGateway()


def make_app(
    tmp_path, *, broker=None, job_facade=None, gateway=None, gateway_factory=None, settings=None
):
    repository = None
    artifact_gateway = None
    if job_facade is not None:
        repository, artifact_gateway = adapt_job_hunter_facade(job_facade)
    if gateway is None and gateway_factory is None:
        gateway_factory = ActiveTurnGatewayFactory()
    return create_app(
        settings
        or Settings(
            device_token=TOKEN,
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
        ),
        capability_broker=broker,
        job_repository=repository,
        artifact_gateway=artifact_gateway,
        agent_gateway=gateway,
        agent_gateway_factory=gateway_factory,
    )


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def mcp_auth():
    return {
        "Authorization": "Bearer test-mcp-trusted-token",
        "X-JobOS-MCP-Token": "test-mcp-trusted-token",
    }


def begin_active_conversation(client, *, token=TOKEN, key="browser-active-turn-1"):
    conversation_id = client.get("/v1/conversations", headers=auth()).json()["conversations"][0][
        "conversation_id"
    ]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Keep browser correlation active", "idempotency_key": key},
    )
    assert response.status_code == 201
    return conversation_id


def test_tab_association_requires_a_bounded_job_id():
    request = BrowserCommandRequest(
        command="tab.associate",
        arguments={"tab_id": "tab-1", "job_id": "job-42"},
        origin="mcp",
        idempotency_key="associate-1",
    )

    assert request.validated_arguments() == {"tab_id": "tab-1", "job_id": "job-42"}
    with pytest.raises(ValueError, match="Invalid browser command arguments"):
        request.model_copy(
            update={"arguments": {"tab_id": "tab-1", "job_id": ""}}
        ).validated_arguments()


def test_tab_create_accepts_only_an_explicit_boolean_activation_policy():
    request = BrowserCommandRequest(
        command="tab.create",
        arguments={
            "url": "https://jobs.example.com/role",
            "associated_job_id": None,
            "activate": False,
        },
        origin="mcp",
        idempotency_key="create-background-1",
    )

    assert request.validated_arguments() == request.arguments
    with pytest.raises(ValueError, match="Invalid browser command arguments"):
        request.model_copy(update={"arguments": {"activate": "false"}}).validated_arguments()


def test_snapshot_accepts_only_bounded_explicit_text_pagination():
    request = BrowserCommandRequest(
        command="page.snapshot",
        arguments={
            "tab_id": "tab-1",
            "text_start": 12_000,
            "text_length": 12_000,
            "include_targets": False,
        },
        origin="mcp",
        idempotency_key="snapshot-page-1",
    )

    assert request.validated_arguments() == request.arguments
    for invalid in (-1, 12_001, True):
        with pytest.raises(ValueError, match="Invalid browser command arguments"):
            request.model_copy(
                update={
                    "arguments": {
                        "tab_id": "tab-1",
                        "text_start": 0,
                        "text_length": invalid,
                        "include_targets": False,
                    }
                }
            ).validated_arguments()


def test_tab_association_rejects_unknown_jobs_before_desktop_dispatch(tmp_path):
    class InspectOnlyJobs:
        def inspect_job(self, job_id):
            if job_id != "job-known":
                raise KeyError(job_id)
            return {"job_id": job_id}

    class CountingBroker:
        def __init__(self):
            self.executions = 0

        async def execute(self, command, *, device_id=None):
            self.executions += 1
            return BrowserCommandResponse(
                command_id="cmd_associate1", state="completed", outcome="tab.associate", data={}
            )

    broker = CountingBroker()
    app = make_app(tmp_path, broker=broker, job_facade=InspectOnlyJobs())
    body = {
        "command": "tab.associate",
        "arguments": {"tab_id": "tab-1", "job_id": "job-missing"},
        "origin": "mcp",
        "idempotency_key": "associate-missing-1",
    }
    with TestClient(app) as client:
        body["conversation_id"] = begin_active_conversation(client)
        missing = client.post("/v1/browser/commands", headers=mcp_auth(), json=body)
        body["arguments"]["job_id"] = "job-known"
        body["idempotency_key"] = "associate-known-1"
        known = client.post("/v1/browser/commands", headers=mcp_auth(), json=body)

    assert missing.status_code == 422
    assert missing.json()["detail"] == "Cannot associate a browser tab with an unknown job"
    assert known.status_code == 200
    assert broker.executions == 1


def make_remote_app(tmp_path):
    return create_app(
        Settings(
            device_token=TOKEN,
            mcp_token="test-mcp-trusted-token",
            device_credentials=(DeviceCredential(device_id="example-macbook", token=REMOTE_TOKEN),),
            state_db_path=tmp_path / "jobos.db",
        )
    )


def test_capability_broker_routes_commands_to_the_originating_desktop():
    class Socket:
        def __init__(self):
            self.sent = []

        async def send_json(self, data):
            self.sent.append(data)

        async def close(self, code=1000, reason=None):
            return None

    async def scenario():
        broker = CapabilityBroker()
        mini = Socket()
        macbook = Socket()
        assert await broker.register(mini, "primary-device") is True
        assert await broker.register(macbook, "example-macbook") is True

        execution = asyncio.create_task(
            broker.execute(
                BrowserCommandRequest(
                    command="page.snapshot",
                    arguments={"tab_id": "macbook-tab"},
                    origin="mcp",
                    idempotency_key="macbook-snapshot-1",
                ),
                device_id="example-macbook",
            )
        )
        await asyncio.sleep(0)
        assert mini.sent == []
        assert len(macbook.sent) == 1
        command_id = macbook.sent[0]["command_id"]
        await broker.resolve(
            macbook,
            {
                "type": "result",
                "command_id": command_id,
                "state": "completed",
                "outcome": "snapshot",
                "data": {"tab_id": "macbook-tab"},
            },
        )
        response = await execution
        assert response.state == "completed"
        assert response.data["tab_id"] == "macbook-tab"

    asyncio.run(scenario())


def test_mcp_browser_command_targets_the_desktop_that_started_the_turn(tmp_path):
    class Gateway:
        connection_state = "online"

        async def start(self):
            return None

        async def create_or_resume_conversation(self, stored_session_id):
            return "stored-session", "live-session"

        async def submit_turn(self, text, context):
            return None

        async def detach_conversation(self):
            return None

        async def stream_events(self):
            if False:
                yield None

        async def interrupt_turn(self, turn_id):
            return None

        async def recover_active_turn(self, stored_session_id, turn_id):
            return None

        async def close(self):
            return None

    class CapturingBroker:
        def __init__(self):
            self.device_id = None

        async def execute(self, command, *, device_id=None):
            self.device_id = device_id
            return BrowserCommandResponse(
                command_id="cmd_macbook", state="completed", outcome="snapshot", data={}
            )

    broker = CapturingBroker()
    settings = Settings(
        device_token=TOKEN,
        mcp_token="test-mcp-trusted-token",
        device_credentials=(DeviceCredential(device_id="example-macbook", token=REMOTE_TOKEN),),
        state_db_path=tmp_path / "jobos.db",
    )

    class Factory:
        def create(self, conversation_id):
            return Gateway()

    app = make_app(tmp_path, broker=broker, gateway_factory=Factory(), settings=settings)
    with TestClient(app) as client:
        conversation_id = client.post(
            "/v1/conversations", headers={"Authorization": f"Bearer {REMOTE_TOKEN}"}
        ).json()["conversation_id"]
        turn = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {REMOTE_TOKEN}"},
            json={"text": "Save this browser job", "idempotency_key": "macbook-turn-1"},
        )
        result = client.post(
            "/v1/browser/commands",
            headers=mcp_auth(),
            json={
                "command": "page.snapshot",
                "arguments": {"tab_id": "macbook-tab"},
                "origin": "mcp",
                "conversation_id": conversation_id,
                "idempotency_key": "macbook-snapshot-2",
            },
        )

    assert turn.status_code == 201
    assert result.status_code == 200
    assert broker.device_id == "example-macbook"


def test_mcp_browser_commands_are_correlated_to_one_active_conversation(tmp_path):
    class Gateway:
        connection_state = "online"

        async def start(self):
            return None

        async def create_or_resume_conversation(self, stored_session_id):
            return "stored-session", "live-session"

        async def submit_turn(self, text, context):
            return None

        async def detach_conversation(self):
            return None

        async def stream_events(self):
            if False:
                yield None

        async def interrupt_turn(self, turn_id):
            return None

        async def recover_active_turn(self, stored_session_id, turn_id):
            return None

        async def close(self):
            return None

    class Factory:
        def create(self, conversation_id):
            return Gateway()

    class CapturingBroker:
        def __init__(self):
            self.device_ids = []

        async def execute(self, command, *, device_id=None):
            self.device_ids.append(device_id)
            return BrowserCommandResponse(
                command_id=f"cmd_{len(self.device_ids)}",
                state="completed",
                outcome="snapshot",
                data={},
            )

    broker = CapturingBroker()
    settings = Settings(
        device_token=TOKEN,
        mcp_token="test-mcp-trusted-token",
        device_credentials=(DeviceCredential(device_id="example-macbook", token=REMOTE_TOKEN),),
        state_db_path=tmp_path / "jobos.db",
    )
    with TestClient(
        make_app(tmp_path, broker=broker, gateway_factory=Factory(), settings=settings)
    ) as client:
        first_id = client.get("/v1/conversations", headers=auth()).json()["conversations"][0][
            "conversation_id"
        ]
        second_id = client.post(
            "/v1/conversations", headers={"Authorization": f"Bearer {REMOTE_TOKEN}"}
        ).json()["conversation_id"]
        idle_id = client.post("/v1/conversations", headers=auth()).json()["conversation_id"]
        assert (
            client.post(
                f"/v1/conversations/{first_id}/messages",
                headers=auth(),
                json={"text": "First", "idempotency_key": "correlation-first-1"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/v1/conversations/{second_id}/messages",
                headers={"Authorization": f"Bearer {REMOTE_TOKEN}"},
                json={"text": "Second", "idempotency_key": "correlation-second-1"},
            ).status_code
            == 201
        )

        def command(conversation_id=None, key="correlation-command-1"):
            body = {
                "command": "tabs.inspect",
                "arguments": {},
                "origin": "mcp",
                "idempotency_key": key,
            }
            if conversation_id is not None:
                body["conversation_id"] = conversation_id
            return client.post("/v1/browser/commands", headers=mcp_auth(), json=body)

        assert command(second_id, "correlation-second-command").status_code == 200
        assert command(first_id, "correlation-first-command").status_code == 200
        assert command(None, "correlation-missing-command").status_code == 422
        assert command("conv_unknown", "correlation-wrong-command").status_code == 404
        assert command(idle_id, "correlation-idle-command").status_code == 409
        assert client.delete(f"/v1/conversations/{idle_id}", headers=auth()).status_code == 204
        assert command(idle_id, "correlation-archived-command").status_code == 404

    assert broker.device_ids == ["example-macbook", "primary-device"]


def test_remote_desktop_credential_routes_direct_commands_to_that_device(tmp_path):
    with (
        TestClient(make_remote_app(tmp_path)) as client,
        client.websocket_connect("/v1/desktop/capabilities") as socket,
    ):
        socket.send_json(
            {
                "type": "authenticate",
                "token": REMOTE_TOKEN,
                "device_id": "example-macbook",
            }
        )
        assert socket.receive_json()["type"] == "ready"

        with ThreadPoolExecutor(max_workers=1) as executor:
            task = executor.submit(
                client.post,
                "/v1/browser/commands",
                headers={"Authorization": f"Bearer {REMOTE_TOKEN}"},
                json={
                    "command": "tabs.inspect",
                    "arguments": {},
                    "origin": "user",
                    "idempotency_key": "remote-inspect-1",
                    "timeout_ms": 1000,
                },
            )
            command = socket.receive_json()
            socket.send_json(
                {
                    "type": "result",
                    "command_id": command["command_id"],
                    "state": "completed",
                    "outcome": "tabs.inspect",
                    "data": {"tabs": [], "active_tab_id": None},
                }
            )
            response = task.result(timeout=2)

    assert response.status_code == 200
    assert response.json()["state"] == "completed"


def test_remote_desktop_cannot_impersonate_the_trusted_mcp_client(tmp_path):
    with TestClient(make_remote_app(tmp_path)) as client:
        response = client.post(
            "/v1/browser/commands",
            headers={"Authorization": f"Bearer {REMOTE_TOKEN}"},
            json={
                "command": "tabs.inspect",
                "arguments": {},
                "origin": "mcp",
                "idempotency_key": "remote-mcp-forbidden-1",
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "MCP operations require the trusted local MCP credential"


def test_browser_command_fails_immediately_without_a_desktop(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        conversation_id = begin_active_conversation(client)
        presence = client.get("/v1/desktop/capabilities", headers=auth())
        response = client.post(
            "/v1/browser/commands",
            headers=mcp_auth(),
            json={
                "command": "tabs.inspect",
                "arguments": {},
                "origin": "mcp",
                "conversation_id": conversation_id,
                "idempotency_key": "inspect-offline-1",
                "timeout_ms": 500,
            },
        )

    assert presence.json() == {
        "available": False,
        "device_id": "primary-device",
        "lease_remaining_ms": 0,
    }
    assert response.status_code == 503
    assert response.json()["code"] == "desktop_unavailable"
    assert response.json()["message"] == "Open JobOS on the configured desktop and retry."
    assert response.json()["retryable"] is True
    assert response.json()["correlation_id"] == response.headers["x-correlation-id"]


def test_authenticated_desktop_receives_correlated_command_and_replay_is_idempotent(tmp_path):
    with (
        TestClient(make_app(tmp_path)) as client,
        client.websocket_connect("/v1/desktop/capabilities") as socket,
    ):
        socket.send_json({"type": "authenticate", "token": TOKEN, "device_id": "primary-device"})
        accepted = socket.receive_json()
        assert accepted["type"] == "ready"
        assert "token" not in str(accepted).lower()
        presence = client.get("/v1/desktop/capabilities", headers=auth()).json()
        assert presence["available"] is True
        assert 0 < presence["lease_remaining_ms"] <= 15_000
        conversation_id = begin_active_conversation(client)
        chronology_before = client.get("/v1/conversations/current", headers=auth()).json()[
            "entries"
        ]

        with ThreadPoolExecutor(max_workers=1) as executor:
            task = executor.submit(
                client.post,
                "/v1/browser/commands",
                headers=mcp_auth(),
                json={
                    "command": "tab.navigate",
                    "arguments": {"tab_id": "tab-1", "url": "https://example.com/jobs/1"},
                    "origin": "mcp",
                    "conversation_id": conversation_id,
                    "idempotency_key": "navigate-1",
                    "timeout_ms": 1000,
                },
            )
            command = socket.receive_json()
            assert command["type"] == "command"
            assert command["command"] == "tab.navigate"
            assert command["origin"] == "mcp"
            assert command["command_id"]
            assert command["deadline_at"].endswith("Z")
            assert "token" not in str(command).lower()
            socket.send_json(
                {
                    "type": "result",
                    "command_id": command["command_id"],
                    "state": "completed",
                    "outcome": "navigated",
                    "data": {
                        "tab_id": "tab-1",
                        "url": "https://example.com/jobs/1",
                        "page_text": "Authorization: Bearer phase7-secret-value",
                    },
                }
            )
            first = task.result(timeout=2)

            replay = client.post(
                "/v1/browser/commands",
                headers=mcp_auth(),
                json={
                    "command": "tab.navigate",
                    "arguments": {"tab_id": "tab-1", "url": "https://example.com/jobs/1"},
                    "origin": "mcp",
                    "conversation_id": conversation_id,
                    "idempotency_key": "navigate-1",
                    "timeout_ms": 1000,
                },
            )
            chronology = client.get("/v1/conversations/current", headers=auth()).json()["entries"]

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["data"]["url"] == "https://example.com/jobs/1"
    assert "phase7-secret-value" not in first.text
    assert "phase7-secret-value" not in (tmp_path / "jobos.db").read_bytes().decode(
        "utf-8", errors="ignore"
    )
    assert chronology == chronology_before


def test_concurrent_durable_browser_retries_execute_the_desktop_once(tmp_path):
    class CountingBroker:
        def __init__(self):
            self.executions = 0

        async def execute(self, command, *, device_id=None):
            self.executions += 1
            await asyncio.sleep(0.1)
            return BrowserCommandResponse(
                command_id="cmd_concurrent1", state="completed", outcome="selected", data={}
            )

    broker = CountingBroker()
    body = {
        "command": "tab.select",
        "arguments": {"tab_id": "tab-1"},
        "origin": "mcp",
        "idempotency_key": "concurrent-select-1",
        "timeout_ms": 1000,
    }
    with (
        TestClient(make_app(tmp_path, broker=broker)) as client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        body["conversation_id"] = begin_active_conversation(client)
        futures = [
            executor.submit(client.post, "/v1/browser/commands", headers=mcp_auth(), json=body)
            for _ in range(2)
        ]
        responses = [future.result(timeout=2) for future in futures]

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert broker.executions == 1


def test_browser_replay_does_not_inject_activity_into_agent_chat(tmp_path):
    class Broker:
        async def execute(self, command, *, device_id=None):
            return BrowserCommandResponse(
                command_id="cmd_repair123", state="completed", outcome="selected", data={}
            )

    body = {
        "command": "tab.select",
        "arguments": {"tab_id": "tab-1"},
        "origin": "mcp",
        "idempotency_key": "repair-select-1",
        "timeout_ms": 1000,
    }
    database = tmp_path / "jobos.db"
    with TestClient(make_app(tmp_path, broker=Broker())) as client:
        body["conversation_id"] = begin_active_conversation(client)
        entries_before = client.get("/v1/conversations/current", headers=auth()).json()["entries"]
        first = client.post("/v1/browser/commands", headers=mcp_auth(), json=body)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "DELETE FROM conversation_events WHERE summary = ?",
                ("Browser: tab select",),
            )
        replay = client.post("/v1/browser/commands", headers=mcp_auth(), json=body)
        entries = client.get("/v1/conversations/current", headers=auth()).json()["entries"]

    assert replay.json() == first.json()
    assert entries == entries_before


def test_distinct_browser_actions_may_reuse_a_key_without_injecting_chat_activity(tmp_path):
    class Broker:
        def __init__(self):
            self.executions = 0

        async def execute(self, command, *, device_id=None):
            self.executions += 1
            return BrowserCommandResponse(
                command_id=f"cmd_shared{self.executions}",
                state="completed",
                outcome="succeeded",
                data={},
            )

    with TestClient(make_app(tmp_path, broker=Broker())) as client:
        conversation_id = begin_active_conversation(client)
        entries_before = client.get("/v1/conversations/current", headers=auth()).json()["entries"]
        inspect = client.post(
            "/v1/browser/commands",
            headers=mcp_auth(),
            json={
                "command": "tabs.inspect",
                "arguments": {},
                "origin": "mcp",
                "conversation_id": conversation_id,
                "idempotency_key": "shared-key",
                "timeout_ms": 1000,
            },
        )
        select = client.post(
            "/v1/browser/commands",
            headers=mcp_auth(),
            json={
                "command": "tab.select",
                "arguments": {"tab_id": "tab-1"},
                "origin": "mcp",
                "idempotency_key": "shared-key",
                "timeout_ms": 1000,
                "conversation_id": conversation_id,
            },
        )
        entries = client.get("/v1/conversations/current", headers=auth()).json()["entries"]

    assert inspect.status_code == 200
    assert select.status_code == 200
    assert entries == entries_before
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        commands = {
            row[0]
            for row in connection.execute(
                "SELECT command_name FROM job_events WHERE event_type = 'browser_action'"
            )
        }
    assert commands == {"tabs.inspect", "tab.select"}


def test_activity_report_is_idempotent_without_injecting_agent_chat_activity(tmp_path):
    body = {
        "label": "Reviewed listing",
        "state": "completed",
        "detail": {},
        "origin": "mcp",
        "idempotency_key": "activity-repair-1",
    }
    with TestClient(make_app(tmp_path)) as client:
        first = client.post("/v1/activity", headers=mcp_auth(), json=body)
        replay = client.post("/v1/activity", headers=mcp_auth(), json=body)
        entries = client.get("/v1/conversations/current", headers=auth()).json()["entries"]

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert entries == []
    with sqlite3.connect(tmp_path / "jobos.db") as connection:
        detail = json.loads(
            connection.execute(
                "SELECT payload_json FROM job_events WHERE command_name = 'activity.report'"
            ).fetchone()[0]
        )
    assert detail["label"] == "Reviewed listing"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"command": "script.execute", "arguments": {}}, "Request validation failed"),
        (
            {
                "command": "element.click",
                "arguments": {"tab_id": "tab-1", "target_id": "document.querySelector('button')"},
            },
            "Invalid browser command arguments",
        ),
    ],
)
def test_browser_command_validation_rejects_scripts_and_selector_like_targets(
    tmp_path, payload, message
):
    body = {
        **payload,
        "origin": "mcp",
        "idempotency_key": "invalid-1",
        "timeout_ms": 500,
    }
    with TestClient(make_app(tmp_path)) as client:
        body["conversation_id"] = begin_active_conversation(client)
        response = client.post("/v1/browser/commands", headers=mcp_auth(), json=body)
    assert response.status_code == 422
    assert message in str(response.json())


def test_websocket_rejects_missing_or_invalid_auth_without_echoing_secret(tmp_path):
    with (
        TestClient(make_app(tmp_path)) as client,
        pytest.raises(Exception) as failure,
        client.websocket_connect("/v1/desktop/capabilities") as socket,
    ):
        socket.send_json(
            {
                "type": "authenticate",
                "token": "wrong-secret-value",
                "device_id": "primary-device",
            }
        )
        socket.receive_json()
    assert "wrong-secret-value" not in str(failure.value)

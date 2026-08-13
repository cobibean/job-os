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
from jobos_api.settings import DeviceCredential, Settings

TOKEN = "phase-seven-device-token"
REMOTE_TOKEN = "phase-seven-remote-device-token"


def make_app(tmp_path, *, broker=None, job_facade=None, gateway=None, settings=None):
    return create_app(
        settings
        or Settings(
            device_token=TOKEN,
            mcp_token="test-mcp-trusted-token",
            state_db_path=tmp_path / "jobos.db",
        ),
        capability_broker=broker,
        job_facade=job_facade,
        agent_gateway=gateway,
    )


def auth():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-JobOS-MCP-Token": "test-mcp-trusted-token",
    }


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
        missing = client.post("/v1/browser/commands", headers=auth(), json=body)
        body["arguments"]["job_id"] = "job-known"
        body["idempotency_key"] = "associate-known-1"
        known = client.post("/v1/browser/commands", headers=auth(), json=body)

    assert missing.status_code == 422
    assert missing.json()["detail"] == "Cannot associate a browser tab with an unknown job"
    assert known.status_code == 200
    assert broker.executions == 1


def make_remote_app(tmp_path):
    return create_app(
        Settings(
            device_token=TOKEN,
            mcp_token="test-mcp-trusted-token",
            device_credentials=(
                DeviceCredential(device_id="cobi-macbook", token=REMOTE_TOKEN),
            ),
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
        assert await broker.register(macbook, "cobi-macbook") is True

        execution = asyncio.create_task(
            broker.execute(
                BrowserCommandRequest(
                    command="page.snapshot",
                    arguments={"tab_id": "macbook-tab"},
                    origin="mcp",
                    idempotency_key="macbook-snapshot-1",
                ),
                device_id="cobi-macbook",
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
        device_credentials=(DeviceCredential(device_id="cobi-macbook", token=REMOTE_TOKEN),),
        state_db_path=tmp_path / "jobos.db",
    )
    app = make_app(tmp_path, broker=broker, gateway=Gateway(), settings=settings)
    with TestClient(app) as client:
        turn = client.post(
            "/v1/conversations/current/messages",
            headers={"Authorization": f"Bearer {REMOTE_TOKEN}"},
            json={"text": "Save this browser job", "idempotency_key": "macbook-turn-1"},
        )
        result = client.post(
            "/v1/browser/commands",
            headers=auth(),
            json={
                "command": "page.snapshot",
                "arguments": {"tab_id": "macbook-tab"},
                "origin": "mcp",
                "idempotency_key": "macbook-snapshot-2",
            },
        )

    assert turn.status_code == 201
    assert result.status_code == 200
    assert broker.device_id == "cobi-macbook"


def test_remote_desktop_credential_routes_direct_commands_to_that_device(tmp_path):
    with (
        TestClient(make_remote_app(tmp_path)) as client,
        client.websocket_connect("/v1/desktop/capabilities") as socket,
    ):
        socket.send_json(
            {
                "type": "authenticate",
                "token": REMOTE_TOKEN,
                "device_id": "cobi-macbook",
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
        presence = client.get("/v1/desktop/capabilities", headers=auth())
        response = client.post(
            "/v1/browser/commands",
            headers=auth(),
            json={
                "command": "tabs.inspect",
                "arguments": {},
                "origin": "mcp",
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
    assert response.json()["detail"] == {
        "code": "desktop_unavailable",
        "message": "Open JobOS on the configured desktop and retry.",
    }


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

        with ThreadPoolExecutor(max_workers=1) as executor:
            task = executor.submit(
                client.post,
                "/v1/browser/commands",
                headers=auth(),
                json={
                    "command": "tab.navigate",
                    "arguments": {"tab_id": "tab-1", "url": "https://example.com/jobs/1"},
                    "origin": "mcp",
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
                headers=auth(),
                json={
                    "command": "tab.navigate",
                    "arguments": {"tab_id": "tab-1", "url": "https://example.com/jobs/1"},
                    "origin": "mcp",
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
    assert chronology == []


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
        "command": "tab.select", "arguments": {"tab_id": "tab-1"},
        "origin": "mcp", "idempotency_key": "concurrent-select-1", "timeout_ms": 1000,
    }
    with (
        TestClient(make_app(tmp_path, broker=broker)) as client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [
            executor.submit(
                client.post, "/v1/browser/commands", headers=auth(), json=body
            )
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
        "command": "tab.select", "arguments": {"tab_id": "tab-1"},
        "origin": "mcp", "idempotency_key": "repair-select-1", "timeout_ms": 1000,
    }
    database = tmp_path / "jobos.db"
    with TestClient(make_app(tmp_path, broker=Broker())) as client:
        first = client.post("/v1/browser/commands", headers=auth(), json=body)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "DELETE FROM conversation_events WHERE summary = ?",
                ("Browser: tab select",),
            )
        replay = client.post("/v1/browser/commands", headers=auth(), json=body)
        entries = client.get("/v1/conversations/current", headers=auth()).json()["entries"]

    assert replay.json() == first.json()
    assert entries == []


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
        inspect = client.post(
            "/v1/browser/commands",
            headers=auth(),
            json={
                "command": "tabs.inspect", "arguments": {}, "origin": "mcp",
                "idempotency_key": "shared-key", "timeout_ms": 1000,
            },
        )
        select = client.post(
            "/v1/browser/commands",
            headers=auth(),
            json={
                "command": "tab.select", "arguments": {"tab_id": "tab-1"},
                "origin": "mcp", "idempotency_key": "shared-key", "timeout_ms": 1000,
            },
        )
        entries = client.get("/v1/conversations/current", headers=auth()).json()["entries"]

    assert inspect.status_code == 200
    assert select.status_code == 200
    assert entries == []
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
        "label": "Reviewed listing", "state": "completed", "detail": {},
        "origin": "mcp", "idempotency_key": "activity-repair-1",
    }
    with TestClient(make_app(tmp_path)) as client:
        first = client.post("/v1/activity", headers=auth(), json=body)
        replay = client.post("/v1/activity", headers=auth(), json=body)
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
        ({"command": "script.execute", "arguments": {}}, "Input should be"),
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
        response = client.post("/v1/browser/commands", headers=auth(), json=body)
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

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.capabilities import BrowserCommandResponse
from jobos_api.settings import DeviceCredential, Settings

TOKEN = "phase-seven-device-token"
REMOTE_TOKEN = "phase-seven-remote-device-token"


def make_app(tmp_path, *, broker=None):
    return create_app(
        Settings(device_token=TOKEN, state_db_path=tmp_path / "jobos.db"),
        capability_broker=broker,
    )


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def make_remote_app(tmp_path):
    return create_app(
        Settings(
            device_token=TOKEN,
            device_credentials=(
                DeviceCredential(device_id="cobi-macbook", token=REMOTE_TOKEN),
            ),
            state_db_path=tmp_path / "jobos.db",
        )
    )


def test_remote_desktop_credential_registers_for_primary_mcp_commands(tmp_path):
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
                headers=auth(),
                json={
                    "command": "tabs.inspect",
                    "arguments": {},
                    "origin": "mcp",
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
    browser_events = [entry for entry in chronology if entry["summary"] == "Browser: tab navigate"]
    assert len(browser_events) == 1
    assert browser_events[0]["detail"] == {
        "command": "tab.navigate",
        "error": None,
        "origin": "mcp",
        "outcome": "navigated",
    }


def test_concurrent_durable_browser_retries_execute_the_desktop_once(tmp_path):
    class CountingBroker:
        def __init__(self):
            self.executions = 0

        async def execute(self, command):
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


def test_browser_replay_repairs_a_missing_deterministic_activity_row(tmp_path):
    class Broker:
        async def execute(self, command):
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
    assert [entry["summary"] for entry in entries].count("Browser: tab select") == 1


def test_distinct_browser_actions_may_reuse_a_key_without_collapsing_activity(tmp_path):
    class Broker:
        def __init__(self):
            self.executions = 0

        async def execute(self, command):
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
    assert [entry["summary"] for entry in entries].count("Browser: tabs inspect") == 1
    assert [entry["summary"] for entry in entries].count("Browser: tab select") == 1


def test_activity_report_recovers_when_activity_precedes_its_mutation_ledger(tmp_path):
    database = tmp_path / "jobos.db"
    body = {
        "label": "Reviewed listing", "state": "completed", "detail": {},
        "origin": "mcp", "idempotency_key": "activity-repair-1",
    }
    with TestClient(make_app(tmp_path)) as client:
        with sqlite3.connect(database) as connection:
            cursor = connection.execute(
                "INSERT INTO conversation_events("
                "event_type, state, summary, detail_json, source_event_id) "
                "VALUES ('activity', 'completed', 'Reviewed listing', '{}', ?)",
                ("activity:primary-device:activity-repair-1",),
            )
            existing_event_id = cursor.lastrowid
        repaired = client.post("/v1/activity", headers=auth(), json=body)
        replay = client.post("/v1/activity", headers=auth(), json=body)

    assert repaired.status_code == 200
    assert repaired.json() == {"event_id": existing_event_id}
    assert replay.json() == repaired.json()


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

"""Synthetic catalog and credential-persistence upgrade proofs; no live connector claims."""

import asyncio
import copy
import logging
import re

import pytest
from jobos_mcp.catalog import DiscoveryLogging, catalog_metadata, observe_catalog
from jobos_mcp.remote_auth import OwnerOAuthProvider
from jobos_mcp.remote_http import create_http_app
from mcp import types
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient
from test_remote_http import BASE, PASSWORD, authorize, exchange


def upgraded_app(tmp_path, *, upgraded=False):
    server = FastMCP("synthetic-catalog", instructions="Synthetic discovery instructions")
    if upgraded:

        @server.tool(name="ping")
        def ping_v2(message: str) -> str:
            """Updated synthetic signature."""
            return message

        @server.tool()
        def added() -> str:
            return "synthetic"
    else:

        @server.tool()
        def ping() -> str:
            return "pong"

    return create_http_app(server, OwnerOAuthProvider(tmp_path / "oauth.db", BASE, PASSWORD))


def discover(client, token):
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json, text/event-stream",
    }
    initialized = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "synthetic-upgrade-client", "version": "1"},
            },
        },
    )
    assert initialized.status_code == 200
    initialization = initialized.json()["result"]
    assert initialization["capabilities"]["tools"].get("listChanged") is not True
    headers["MCP-Protocol-Version"] = initialization["protocolVersion"]
    notified = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert notified.status_code == 202
    listed = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200
    result = listed.json()["result"]
    assert not result.get("nextCursor")
    expected = catalog_metadata(
        [types.Tool.model_validate(tool) for tool in result["tools"]],
        initialization["instructions"],
    )
    health = client.get("/healthz").json()
    assert {key: health[key] for key in expected} == expected
    assert initialization["serverInfo"]["version"] == "catalog-" + expected["catalog_revision"]
    return expected, result["tools"]


def test_registered_client_discovers_upgrade_without_reregistration(tmp_path):
    with TestClient(upgraded_app(tmp_path), base_url=BASE) as client:
        info, code = authorize(client)
        tokens = exchange(client, info, code).json()
        before, old_tools = discover(client, tokens["access_token"])
        assert before["tool_count"] == 1
        assert old_tools[0]["inputSchema"]["properties"] == {}
    # New app and provider objects, same on-disk OAuth store, same registration/token.
    with TestClient(upgraded_app(tmp_path, upgraded=True), base_url=BASE) as client:
        after, new_tools = discover(client, tokens["access_token"])
        assert after["tool_count"] == 2
        assert after["catalog_revision"] != before["catalog_revision"]
        tools = {tool["name"]: tool for tool in new_tools}
        assert set(tools) == {"ping", "added"}
        assert tools["ping"]["inputSchema"]["required"] == ["message"]
        refreshed = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": info["client_id"],
                "client_secret": info["client_secret"],
            },
        )
        assert refreshed.status_code == 200
        rotated = refreshed.json()
        assert discover(client, rotated["access_token"])[0] == after
    with TestClient(upgraded_app(tmp_path, upgraded=True), base_url=BASE) as client:
        assert discover(client, rotated["access_token"])[0] == after


def test_revision_covers_complete_descriptors_and_instructions():
    descriptor = {
        "name": "synthetic",
        "title": "Synthetic tool",
        "description": "Synthetic description",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
        "icons": [{"src": "https://example.com/icon.png"}],
        "_meta": {"synthetic": "one"},
    }
    tool = types.Tool.model_validate(descriptor)
    other = types.Tool(name="other", inputSchema={"type": "object"})
    original = catalog_metadata([tool, other], "instructions")
    assert original == catalog_metadata([other, tool], "instructions")
    reordered = types.Tool.model_validate(dict(reversed(list(descriptor.items()))))
    assert original == catalog_metadata([reordered, other], "instructions")
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", original["catalog_revision"])
    assert original["tool_count"] == 2
    changes = {
        "name": "renamed",
        "title": "Changed",
        "description": "Changed",
        "inputSchema": {"type": "object", "required": ["text"]},
        "outputSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "annotations": {"readOnlyHint": False},
        "icons": [{"src": "https://example.com/changed.png"}],
        "_meta": {"synthetic": "two"},
    }
    for field, value in changes.items():
        changed = copy.deepcopy(descriptor)
        changed[field] = value
        assert (
            catalog_metadata([types.Tool.model_validate(changed), other], "instructions")
            != original
        )
    assert catalog_metadata([tool, other], "changed instructions") != original
    assert catalog_metadata([tool], "instructions") != original


def test_discovery_logs_only_allowlisted_fields(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="jobos_mcp.discovery")
    with TestClient(upgraded_app(tmp_path), base_url=BASE) as client:
        info, code = authorize(client)
        tokens = exchange(client, info, code).json()
        metadata, _ = discover(client, tokens["access_token"])
        caplog.clear()
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "Accept": "application/json, text/event-stream",
            "X-Request-ID": "synthetic-private-correlation",
            "User-Agent": "synthetic-private-user",
            "Cookie": "secret=synthetic-cookie",
        }
        for index in range(2):
            response = client.post(
                "/mcp?private=synthetic-query",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": "synthetic-private-rpc-id",
                    "method": "tools/list",
                    "params": {"_meta": {"private": "synthetic-private-job-data"}},
                },
            )
            assert response.status_code == 200
            correlation = response.headers["x-jobos-discovery-id"]
            assert re.fullmatch(r"[0-9a-f]{32}", correlation)
            records = [r.getMessage() for r in caplog.records if r.name == "jobos_mcp.discovery"]
            assert len(records) == index + 1
            assert records[-1] == (
                "mcp_discovery method=tools/list status=200 "
                f"catalog_revision={metadata['catalog_revision']} "
                f"tool_count=1 discovery_id={correlation}"
            )
        assert records[0] != records[1]
        # Tool payloads, rejected auth, malformed RPC and health checks are not discovery events.
        client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            },
        )
        client.post("/mcp", json={"method": "tools/list"})
        client.post("/mcp", headers=headers, json={})
        client.get("/healthz")
        assert [
            r.getMessage() for r in caplog.records if r.name == "jobos_mcp.discovery"
        ] == records
        text = "\n".join(records)
        for private in [
            tokens["access_token"],
            info["client_secret"],
            "synthetic-private",
            "synthetic-query",
            "synthetic-cookie",
            "pong",
        ]:
            assert private not in text


def test_failed_send_does_not_log_completed_discovery(caplog):
    caplog.set_level(logging.INFO, logger="jobos_mcp.discovery")
    server = FastMCP("synthetic")
    observe_catalog(server)

    async def app(scope, receive, send):
        await server._mcp_server.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"synthetic"})

    async def failed_send(message):
        if message["type"] == "http.response.body":
            raise OSError("synthetic-private-error")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(OSError):
        asyncio.run(DiscoveryLogging(app)({"type": "http"}, receive, failed_send))
    assert not [r for r in caplog.records if r.name == "jobos_mcp.discovery"]

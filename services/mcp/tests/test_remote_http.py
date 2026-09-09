"""OAuth and MCP are tested through their actual HTTP endpoints."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jobos_mcp.remote_auth import OwnerOAuthProvider
from jobos_mcp.remote_http import create_http_app
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

BASE = "https://jobos.example"
PASSWORD = "synthetic-owner-secret-" + "x" * 32
CALLBACK = "https://chatgpt.com/connector/oauth/synthetic"
VERIFIER = "synthetic-verifier-" + "a" * 48


def app_for(tmp_path: Path):
    server = FastMCP("test", stateless_http=True, json_response=True)

    @server.tool()
    def ping() -> str:
        return "pong"

    provider = OwnerOAuthProvider(tmp_path / "oauth.db", BASE, PASSWORD)
    return create_http_app(server, provider)


def authorize(client: TestClient):
    registered = client.post(
        "/register",
        json={
            "redirect_uris": [CALLBACK],
            "client_name": "ChatGPT",
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "client_secret_post",
            "scope": "jobos",
        },
    )
    assert registered.status_code == 201, registered.text
    info = registered.json()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
    )
    response = client.get(
        "/authorize",
        params={
            "client_id": info["client_id"],
            "redirect_uri": CALLBACK,
            "response_type": "code",
            "scope": "jobos",
            "state": "owner-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": BASE + "/mcp",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    login = client.get(response.headers["location"])
    assert login.status_code == 200
    assert login.headers["referrer-policy"] == "same-origin"
    # Chromium applies form-action to the OAuth redirect after the password POST.
    directives = {
        parts[0]: parts[1:]
        for directive in login.headers["content-security-policy"].split(";")
        if (parts := directive.split())
    }
    assert directives["form-action"] == ["'self'", "https://chatgpt.com"]
    request_id = parse_qs(urlparse(str(login.url)).query)["request"][0]
    denied = client.post(
        "/connect", data={"request": request_id, "password": "wrong"}, follow_redirects=False
    )
    assert denied.status_code == 403
    for origin in ("null", "https://other.example"):
        blocked = client.post(
            "/connect",
            data={"request": request_id, "password": PASSWORD},
            headers={"Origin": origin},
            follow_redirects=False,
        )
        assert blocked.status_code == 403
    # Privacy settings and parallel OAuth windows may drop/replace browser cookies.
    # This password-authenticated flow must not depend on ambient cookie state.
    client.cookies.clear()
    accepted = client.post(
        "/connect",
        data={"request": request_id, "password": PASSWORD},
        headers={"Origin": BASE},
        follow_redirects=False,
    )
    assert accepted.status_code == 303, accepted.text
    query = parse_qs(urlparse(accepted.headers["location"]).query)
    assert query["state"] == ["owner-state"]
    return info, query["code"][0]


def exchange(client, info, code, verifier=VERIFIER):
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": info["client_id"],
            "client_secret": info["client_secret"],
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
            "resource": BASE + "/mcp",
        },
    )


def test_oauth_login_pkce_refresh_and_real_mcp(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        denied = client.post("/mcp", json={})
        assert denied.status_code == 401
        assert "oauth-protected-resource" in denied.headers["www-authenticate"]
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert "S256" in metadata["code_challenge_methods_supported"]
        resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert resource["resource"] == BASE + "/mcp"
        info, code = authorize(client)
        assert exchange(client, info, code, "incorrect" * 8).status_code == 400
        tokens = exchange(client, info, code)
        assert tokens.status_code == 200, tokens.text
        tokens = tokens.json()
        assert exchange(client, info, code).status_code == 400
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "Accept": "application/json, text/event-stream",
        }
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            },
        )
        assert response.status_code == 200, response.text
        assert "pong" in response.text
        refresh = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": info["client_id"],
                "client_secret": info["client_secret"],
            },
        )
        assert refresh.status_code == 200, refresh.text
        assert refresh.json()["access_token"] != tokens["access_token"]
        assert client.post("/mcp", headers=headers, json={}).status_code == 401
    # Persistent tokens survive daemon restart.
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        headers["Authorization"] = "Bearer " + refresh.json()["access_token"]
        assert (
            client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            ).status_code
            == 200
        )


def test_external_redirects_cannot_register(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        response = client.post(
            "/register", json={"redirect_uris": ["https://evil.example/callback"]}
        )
        assert response.status_code == 400


def test_owner_login_requires_real_request_and_tokens_can_be_revoked(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        assert (
            client.post("/connect", data={"request": "fake", "password": PASSWORD}).status_code
            == 400
        )
        info, code = authorize(client)
        tokens = exchange(client, info, code).json()
        revoked = client.post(
            "/revoke",
            data={
                "client_id": info["client_id"],
                "client_secret": info["client_secret"],
                "token": tokens["access_token"],
                "token_type_hint": "access_token",
            },
        )
        assert revoked.status_code == 200
        assert (
            client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer " + tokens["access_token"],
                },
                json={},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/token",
                data={
                    "client_id": info["client_id"],
                    "client_secret": info["client_secret"],
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                },
            ).status_code
            == 400
        )

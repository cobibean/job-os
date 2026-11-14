"""OAuth and MCP are tested through their actual HTTP endpoints."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from jobos_mcp.remote_auth import OwnerOAuthProvider
from jobos_mcp.remote_http import create_http_app
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

BASE = "https://jobos.example"
PASSWORD = "synthetic-owner-secret-" + "x" * 32
CALLBACK = "https://chatgpt.com/connector/oauth/synthetic"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "synthetic-verifier-" + "a" * 48
OAUTH_CLIENTS = [
    pytest.param(CALLBACK, "client_secret_post", id="chatgpt"),
    pytest.param(
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "client_secret_post",
        id="chatgpt-platform",
    ),
    pytest.param(CLAUDE_CALLBACK, "none", id="claude-public"),
    pytest.param(CLAUDE_CALLBACK, "client_secret_post", id="claude-confidential"),
]


def app_for(tmp_path: Path):
    server = FastMCP("test", stateless_http=True, json_response=True)

    @server.tool()
    def ping() -> str:
        return "pong"

    provider = OwnerOAuthProvider(tmp_path / "oauth.db", BASE, PASSWORD)
    return create_http_app(server, provider)


def authorize(client: TestClient, callback=CALLBACK, auth_method="client_secret_post"):
    registered = client.post(
        "/register",
        json={
            "redirect_uris": [callback],
            "client_name": "Synthetic OAuth client",
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": auth_method,
            "scope": "jobos",
        },
    )
    assert registered.status_code == 201, registered.text
    info = registered.json()
    if auth_method == "none":
        assert not info.get("client_secret")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
    )
    response = client.get(
        "/authorize",
        params={
            "client_id": info["client_id"],
            "redirect_uri": callback,
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
    client_name = "Claude" if callback == CLAUDE_CALLBACK else "ChatGPT"
    assert f"Connect {client_name} to JobOS" in login.text
    assert f"return to {urlparse(callback).hostname}" in login.text
    assert login.headers["referrer-policy"] == "same-origin"
    # Chromium applies form-action to the OAuth redirect after the password POST.
    directives = {
        parts[0]: parts[1:]
        for directive in login.headers["content-security-policy"].split(";")
        if (parts := directive.split())
    }
    assert directives["form-action"] == ["'self'", "https://chatgpt.com", "https://claude.ai"]
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
    assert accepted.headers["location"].split("?")[0] == callback
    assert accepted.headers["content-security-policy"] == login.headers["content-security-policy"]
    query = parse_qs(urlparse(accepted.headers["location"]).query)
    assert query["state"] == ["owner-state"]
    return info, query["code"][0]


def credentials(info):
    return {key: info[key] for key in ("client_id", "client_secret") if info.get(key)}


def exchange(client, info, code, verifier=VERIFIER):
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            **credentials(info),
            "redirect_uri": info["redirect_uris"][0],
            "code_verifier": verifier,
            "resource": BASE + "/mcp",
        },
    )


@pytest.mark.parametrize(("callback", "auth_method"), OAUTH_CLIENTS)
def test_oauth_login_pkce_refresh_and_real_mcp(tmp_path, callback, auth_method):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        denied = client.post("/mcp", json={})
        assert denied.status_code == 401
        assert "oauth-protected-resource" in denied.headers["www-authenticate"]
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert "S256" in metadata["code_challenge_methods_supported"]
        assert "none" in metadata["token_endpoint_auth_methods_supported"]
        assert "none" in metadata["revocation_endpoint_auth_methods_supported"]
        resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert resource["resource"] == BASE + "/mcp"
        info, code = authorize(client, callback, auth_method)
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
                **credentials(info),
            },
        )
        assert refresh.status_code == 200, refresh.text
        assert refresh.json()["access_token"] != tokens["access_token"]
        assert client.post("/mcp", headers=headers, json={}).status_code == 401
        replay = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                **credentials(info),
            },
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"
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


@pytest.mark.parametrize(
    "callback",
    [
        "https://evil.example/callback",
        "http://claude.ai/api/mcp/auth_callback",
        "https://claude.ai.evil.example/api/mcp/auth_callback",
        "https://claude.ai@evil.example/api/mcp/auth_callback",
        "https://user@claude.ai/api/mcp/auth_callback",
        "https://claude.ai:8443/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback/extra",
        "https://claude.ai/api/mcp/auth_callback?next=https://evil.example",
        "https://claude.ai/api/mcp/auth_callback#fragment",
        "http://localhost:3118/callback",
    ],
)
def test_external_redirects_cannot_register(tmp_path, callback):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        response = client.post("/register", json={"redirect_uris": [callback]})
        assert response.status_code == 400


@pytest.mark.parametrize(("callback", "auth_method"), OAUTH_CLIENTS)
def test_owner_login_requires_real_request_and_tokens_can_be_revoked(
    tmp_path, callback, auth_method
):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        assert (
            client.post("/connect", data={"request": "fake", "password": PASSWORD}).status_code
            == 400
        )
        info, code = authorize(client, callback, auth_method)
        tokens = exchange(client, info, code).json()
        revoked = client.post(
            "/revoke",
            data={
                **credentials(info),
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
                    **credentials(info),
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                },
            ).status_code
            == 400
        )


@pytest.mark.parametrize("redirects", [[], [CALLBACK], [CLAUDE_CALLBACK, CALLBACK]])
def test_public_clients_require_only_the_claude_callback(tmp_path, redirects):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        response = client.post(
            "/register",
            json={"redirect_uris": redirects, "token_endpoint_auth_method": "none"},
        )
        assert response.status_code == 400


def test_claude_public_tokens_are_bound_to_the_client(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        first, code = authorize(client, CLAUDE_CALLBACK, "none")
        other, _ = authorize(client, CLAUDE_CALLBACK, "none")
        assert exchange(client, other, code).status_code == 400
        wrong_redirect = {**first, "redirect_uris": [CALLBACK]}
        assert exchange(client, wrong_redirect, code).status_code == 400
        tokens = exchange(client, first, code).json()
        response = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                **credentials(other),
            },
        )
        assert response.status_code == 400
        response = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                **credentials(first),
            },
        )
        assert response.status_code == 200


def test_public_revocation_preserves_credentials_validation_and_token_ownership(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        confidential, code = authorize(client)
        protected = exchange(client, confidential, code).json()
        assert (
            client.post(
                "/revoke",
                data={"client_id": confidential["client_id"], "token": protected["access_token"]},
            ).status_code
            == 401
        )
        public, code = authorize(client, CLAUDE_CALLBACK, "none")
        tokens = exchange(client, public, code).json()
        assert client.post("/revoke", data=credentials(public)).status_code == 400
        assert (
            client.post(
                "/revoke",
                data={**credentials(public), "token": protected["access_token"]},
            ).status_code
            == 200
        )
        # A public client's successful no-op must not revoke another client's token.
        assert (
            client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer " + protected["access_token"],
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/revoke",
                data={
                    **credentials(public),
                    "token": tokens["refresh_token"],
                    "token_type_hint": "refresh_token",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/mcp",
                json={},
                headers={"Authorization": "Bearer " + tokens["access_token"]},
            ).status_code
            == 401
        )

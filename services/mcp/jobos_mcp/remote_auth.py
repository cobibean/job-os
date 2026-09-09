"""Single-owner OAuth storage; protocol validation/PKCE are owned by the MCP SDK."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response


class OwnerOAuthProvider:
    """One owner password, standard OAuth clients, persistent rotating bearer tokens."""

    def __init__(self, database: Path, base_url: str, owner_secret: str):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("Remote MCP public URL must be an HTTPS origin")
        if len(owner_secret) < 32:
            raise ValueError("Remote MCP owner secret must contain at least 32 characters")
        self.base_url = base_url.rstrip("/")
        self.resource = self.base_url + "/mcp"
        self.owner_hash = hashlib.sha256(owner_secret.encode()).digest()
        database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(database, 0o600)
        self.database = database
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS oauth (kind TEXT, key TEXT, value TEXT, "
                "expires REAL, PRIMARY KEY(kind, key))"
            )

    def _db(self):
        return sqlite3.connect(self.database)

    @staticmethod
    def _key(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _put(self, kind, key, value, expires):
        with self._db() as db:
            db.execute("DELETE FROM oauth WHERE expires < ?", (time.time(),))
            db.execute(
                "INSERT OR REPLACE INTO oauth VALUES (?, ?, ?, ?)",
                (kind, self._key(key), json.dumps(value), expires),
            )

    def _get(self, kind, key, *, consume=False):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM oauth WHERE kind=? AND key=? AND expires>?",
                (kind, self._key(key), time.time()),
            ).fetchone()
            if consume:
                db.execute("DELETE FROM oauth WHERE kind=? AND key=?", (kind, self._key(key)))
        return json.loads(row[0]) if row else None

    async def get_client(self, client_id):
        data = self._get("client", client_id) or self._get("unconnected_client", client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info):
        # This private integration only accepts ChatGPT redirects; no open redirect service.
        for redirect in client_info.redirect_uris or []:
            uri = urlsplit(str(redirect))
            if (
                uri.scheme != "https"
                or uri.netloc != "chatgpt.com"
                or uri.fragment
                or not (
                    uri.path.startswith("/connector/oauth/")
                    or uri.path == "/connector_platform_oauth_redirect"
                )
            ):
                raise RegistrationError("invalid_redirect_uri", "Use the ChatGPT callback URL")
        if client_info.token_endpoint_auth_method not in (
            "client_secret_post",
            "client_secret_basic",
        ):
            raise RegistrationError(
                "invalid_client_metadata", "A confidential OAuth client is required"
            )
        # Abandoned public registrations are temporary and reclaimable. Only owner
        # login promotes a client to durable storage; flooding cannot fill its slots.
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM oauth WHERE kind='unconnected_client' AND key IN "
                "(SELECT key FROM oauth WHERE kind='unconnected_client' "
                "ORDER BY expires DESC LIMIT -1 OFFSET 99)"
            )
            db.execute(
                "INSERT OR REPLACE INTO oauth VALUES (?, ?, ?, ?)",
                (
                    "unconnected_client",
                    self._key(client_info.client_id),
                    client_info.model_dump_json(),
                    time.time() + 600,
                ),
            )

    async def authorize(self, client, params: AuthorizationParams):
        if params.resource != self.resource:
            raise AuthorizeError("invalid_request", "Use this server's MCP resource URL")
        if set(params.scopes or []) - {"jobos"}:
            raise AuthorizeError("invalid_scope", "Only the jobos scope is available")
        request_id = secrets.token_urlsafe(32)
        self._put(
            "pending",
            request_id,
            {
                "client_id": client.client_id,
                "client": client.model_dump(mode="json"),
                "params": params.model_dump(mode="json"),
            },
            time.time() + 600,
        )
        return self.base_url + "/connect?" + urlencode({"request": request_id})

    async def connect(self, request: Request) -> Response:
        headers = {
            "Cache-Control": "no-store",
            # no-referrer makes browsers submit this form with Origin: null.
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
        }
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            if not self._get("pending", request_id):
                return Response("Connection request expired. Start again in ChatGPT.", 400)
            safe = html.escape(request_id, quote=True)
            response = HTMLResponse(
                '<!doctype html><meta name="viewport" content="width=device-width">'
                "<title>Connect JobOS</title><style>body{font:18px system-ui;max-width:480px;"
                "margin:12vh auto;padding:24px;background:#10141c;color:#eee}"
                "input,button{font:inherit;padding:12px;box-sizing:border-box;width:100%;"
                "margin-top:16px}button{cursor:pointer}</style><h1>Connect ChatGPT to JobOS</h1>"
                "<p>Enter your JobOS connection password to connect all JobOS tools.</p>"
                '<form method="post" action="/connect">'
                f'<input type="hidden" name="request" value="{safe}">'
                '<label>Connection password<input type="password" name="password" '
                'autocomplete="current-password" required autofocus></label>'
                '<button type="submit">Connect JobOS</button></form>',
                headers=headers,
            )
            response.set_cookie(
                "jobos_connect",
                self._key(request_id),
                max_age=600,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/connect",
            )
            return response
        if len(await request.body()) > 4096:
            return Response("Request too large", 413)
        origin = request.headers.get("origin")
        if origin is not None and origin != self.base_url:
            return Response("Invalid request origin", 403)
        form = await request.form()
        request_id, password = str(form.get("request", "")), str(form.get("password", ""))
        if not hmac.compare_digest(request.cookies.get("jobos_connect", ""), self._key(request_id)):
            return Response("Restart the connection in ChatGPT.", 403)
        if not hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self.owner_hash):
            return Response(
                "Incorrect connection password. Go back and try again.", 403, headers=headers
            )
        pending = self._get("pending", request_id, consume=True)
        if not pending:
            return Response("Connection request expired. Start again in ChatGPT.", 400)
        self._put("client", pending["client_id"], pending["client"], time.time() + 365 * 86400)
        self._get("unconnected_client", pending["client_id"], consume=True)
        params = AuthorizationParams.model_validate(pending["params"])
        code = AuthorizationCode(
            code=secrets.token_urlsafe(32),
            scopes=params.scopes or ["jobos"],
            expires_at=time.time() + 120,
            client_id=pending["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource,
            subject="owner",
        )
        self._put("code", code.code, code.model_dump(mode="json"), code.expires_at)
        target = urlsplit(str(params.redirect_uri))
        query = parse_qsl(target.query) + [("code", code.code)]
        if params.state is not None:
            query.append(("state", params.state))
        response = RedirectResponse(
            urlunsplit(target._replace(query=urlencode(query))), status_code=303, headers=headers
        )
        response.delete_cookie("jobos_connect", path="/connect")
        return response

    async def load_authorization_code(self, client, authorization_code):
        data = self._get("code", authorization_code)
        return (
            AuthorizationCode.model_validate(data)
            if data and data["client_id"] == client.client_id
            else None
        )

    def _issue(self, client_id, scopes):
        expires = int(time.time()) + 3600
        refresh_expires = int(time.time()) + 90 * 86400
        access = AccessToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=expires,
            resource=self.resource,
            subject="owner",
        )
        refresh = RefreshToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=refresh_expires,
            subject="owner",
        )
        self._put("access", access.token, access.model_dump(mode="json"), expires)
        refresh_data = refresh.model_dump(mode="json") | {"access_token": access.token}
        self._put("refresh", refresh.token, refresh_data, refresh_expires)
        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=refresh.token,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(self, client, authorization_code):
        if not self._get("code", authorization_code.code, consume=True):
            raise TokenError("invalid_grant", "Code has expired or was already used")
        return self._issue(client.client_id, authorization_code.scopes)

    async def load_refresh_token(self, client, refresh_token):
        data = self._get("refresh", refresh_token)
        return (
            RefreshToken.model_validate(data)
            if data and data["client_id"] == client.client_id
            else None
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        data = self._get("refresh", refresh_token.token, consume=True)
        if not data:
            raise TokenError("invalid_grant", "Refresh token has expired or was already used")
        self._get("access", data["access_token"], consume=True)
        return self._issue(client.client_id, scopes)

    async def load_access_token(self, token):
        data = self._get("access", token)
        return (
            AccessToken.model_validate(data) if data and data["resource"] == self.resource else None
        )

    async def revoke_token(self, token):
        if isinstance(token, RefreshToken):
            data = self._get("refresh", token.token, consume=True)
            if data:
                self._get("access", data["access_token"], consume=True)
        else:
            self._get("access", token.token, consume=True)
            with self._db() as db:
                db.execute(
                    "DELETE FROM oauth WHERE kind='refresh' "
                    "AND json_extract(value, '$.access_token')=?",
                    (token.token,),
                )

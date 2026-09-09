"""Opt-in, OAuth-protected Streamable HTTP entrypoint for remote JobOS clients."""

from __future__ import annotations

import argparse
import asyncio
import json
import stat
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import (
    build_resource_metadata_url,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from jobos_mcp.remote_auth import OwnerOAuthProvider


def create_http_app(server: FastMCP, provider: OwnerOAuthProvider) -> Starlette:
    """Reuse the complete tool registry; let the SDK own OAuth and MCP protocols."""
    origin = AnyHttpUrl(provider.base_url)
    resource = AnyHttpUrl(provider.resource)
    server.settings.stateless_http = True
    server.settings.json_response = True
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            origin.host + (f":{origin.port}" if origin.port != 443 else ""),
            "127.0.0.1:*",
            "localhost:*",
        ],
        allowed_origins=[provider.base_url],
    )
    mcp_app = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    async def health(request: Request):
        return JSONResponse({"status": "ready", "service": "jobos-remote-mcp"})

    routes = create_auth_routes(
        provider,
        origin,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["jobos"], default_scopes=["jobos"]
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    routes += create_protected_resource_routes(
        resource_url=resource, authorization_servers=[origin], scopes_supported=["jobos"]
    )
    routes += [
        Route("/connect", provider.connect, methods=["GET", "POST"]),
        Route("/healthz", health),
        Route(
            "/mcp", RequireAuthMiddleware(mcp_app, ["jobos"], build_resource_metadata_url(resource))
        ),
    ]
    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(
                AuthenticationMiddleware, backend=BearerAuthBackend(ProviderTokenVerifier(provider))
            ),
            Middleware(AuthContextMiddleware),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the private JobOS remote MCP connection")
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    path = arguments.config.expanduser().resolve(strict=True)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        parser.error("Remote MCP config must be readable only by its owner (chmod 600)")
    config = json.loads(path.read_text())
    if not isinstance(config.get("agent_token"), str) or len(config["agent_token"]) < 16:
        parser.error("Configure the existing Career Profile agent_token for all-tools access")
    from jobos_mcp.external import create_external_server
    from jobos_mcp.jobs import JobOsMcpClient

    provider = OwnerOAuthProvider(
        path.parent / "oauth.sqlite3", config["public_url"], config["owner_secret"]
    )
    api_token = Path(config["api_token_file"]).expanduser().read_text().strip()
    client = JobOsMcpClient(
        base_url=config.get("api_base_url", "http://127.0.0.1:8766"),
        device_token="unused",
        mcp_token=api_token,
        external=True,
        agent_id=config.get("agent_id", "trusted-local-mcp"),
        agent_token=config.get("agent_token"),
    )
    server = create_external_server(client, artifact_root=Path(config["artifact_root"]))
    app = create_http_app(server, provider)
    try:
        # TLS terminates at the forwarding service. Never bind this listener publicly.
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(config.get("port", 8770)),
            access_log=False,
            log_level="warning",
        )
    finally:
        asyncio.run(client.aclose())


if __name__ == "__main__":
    main()

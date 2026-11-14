"""Opt-in, OAuth-protected Streamable HTTP entrypoint for remote JobOS clients."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import (
    build_metadata,
    build_resource_metadata_url,
    cors_middleware,
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

from jobos_mcp.catalog import DiscoveryLogging, catalog_metadata, logger, observe_catalog
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
    observe_catalog(server)

    @asynccontextmanager
    async def lifespan(app):
        # Tool registration is complete before startup. Version is diagnostic metadata,
        # not a promise that an external client will refresh its saved catalog.
        metadata = catalog_metadata(await server.list_tools(), server.instructions)
        server._mcp_server.version = "catalog-" + str(metadata["catalog_revision"])
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    async def health(request: Request):
        return JSONResponse(
            {
                "status": "ready",
                "service": "jobos-remote-mcp",
                **catalog_metadata(await server.list_tools(), server.instructions),
            }
        )

    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=["jobos"], default_scopes=["jobos"]
    )
    revocation = RevocationOptions(enabled=True)
    routes = create_auth_routes(
        provider,
        origin,
        client_registration_options=registration,
        revocation_options=revocation,
    )
    # The SDK supports public clients, but its default discovery document only
    # advertises confidential methods. Publish the methods our provider accepts.
    metadata = build_metadata(origin, None, registration, revocation)
    metadata.token_endpoint_auth_methods_supported = [
        "client_secret_post",
        "client_secret_basic",
        "none",
    ]
    metadata.revocation_endpoint_auth_methods_supported = (
        metadata.token_endpoint_auth_methods_supported.copy()
    )
    metadata_path = "/.well-known/oauth-authorization-server"
    routes = [route for route in routes if route.path not in (metadata_path, "/revoke")]
    routes.append(
        Route(
            metadata_path,
            endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
    )
    revocation_handler = RevocationHandler(provider, ClientAuthenticator(provider))

    async def revoke(request: Request):
        # MCP SDK 1.28.1's revocation schema requires the client_secret field even
        # for public clients. Normalize only the missing field; the SDK still
        # authenticates confidential clients and enforces token ownership.
        if request.headers.get("content-type", "").split(";")[0] == (
            "application/x-www-form-urlencoded"
        ):
            form = await request.form()
            if "client_secret" not in form:
                body = urlencode([*form.multi_items(), ("client_secret", "")]).encode()
                headers = [
                    (key, value)
                    for key, value in request.scope["headers"]
                    if key.lower() != b"content-length"
                ]
                headers.append((b"content-length", str(len(body)).encode()))

                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request({**request.scope, "headers": headers}, receive)
        return await revocation_handler.handle(request)

    routes.append(
        Route(
            "/revoke",
            endpoint=cors_middleware(revoke, ["POST", "OPTIONS"]),
            methods=["POST", "OPTIONS"],
        )
    )
    routes += create_protected_resource_routes(
        resource_url=resource, authorization_servers=[origin], scopes_supported=["jobos"]
    )
    routes += [
        Route("/connect", provider.connect, methods=["GET", "POST"]),
        Route("/healthz", health),
        Route(
            "/mcp",
            RequireAuthMiddleware(
                DiscoveryLogging(mcp_app), ["jobos"], build_resource_metadata_url(resource)
            ),
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
    # Enable only the bounded discovery logger, not SDK/debug or HTTP access logs.
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)
    logger.propagate = False
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

"""Catalog fingerprints and allowlisted discovery diagnostics, not cache invalidation."""

from __future__ import annotations

import hashlib
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

from mcp import types
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("jobos_mcp.discovery")


def catalog_metadata(tools: list[types.Tool], instructions: str | None) -> dict[str, str | int]:
    """Hash every served descriptor field; ignore only tool/object-key ordering."""
    descriptors = [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools]
    canonical = json.dumps(
        {"tools": sorted(descriptors, key=lambda tool: tool["name"]), "instructions": instructions},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return {
        "catalog_revision": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "tool_count": len(tools),
    }


@dataclass
class Discovery:
    metadata: dict[str, str | int] | None = None


_discovery: ContextVar[Discovery | None] = ContextVar("jobos_discovery", default=None)


def observe_catalog(server: FastMCP) -> None:
    """Observe the SDK's actual list result without replacing its registry/cache behavior."""
    low_level = server._mcp_server
    original = low_level.request_handlers[types.ListToolsRequest]

    async def list_tools(request: types.ListToolsRequest) -> types.ServerResult:
        result = await original(request)
        discovery = _discovery.get()
        if discovery is not None and isinstance(result.root, types.ListToolsResult):
            # FastMCP serves its complete registry without pagination.
            discovery.metadata = catalog_metadata(result.root.tools, low_level.instructions)
        return result

    low_level.request_handlers[types.ListToolsRequest] = list_tools


class DiscoveryLogging:
    """Log only completed discovery responses; never inspect or buffer HTTP bodies."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        discovery = Discovery()
        token = _discovery.set(discovery)
        correlation = uuid4().hex  # Never trust incoming request IDs or headers for logging.
        status = None

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                if discovery.metadata is not None:
                    message = {
                        **message,
                        "headers": [
                            *message.get("headers", []),
                            (b"x-jobos-discovery-id", correlation.encode("ascii")),
                        ],
                    }
            await send(message)
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and discovery.metadata is not None
            ):
                logger.info(
                    "mcp_discovery method=tools/list status=%s catalog_revision=%s "
                    "tool_count=%s discovery_id=%s",
                    status,
                    discovery.metadata["catalog_revision"],
                    discovery.metadata["tool_count"],
                    correlation,
                )

        try:
            await self.app(scope, receive, observed_send)
        finally:
            _discovery.reset(token)

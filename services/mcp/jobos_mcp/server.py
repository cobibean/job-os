from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from jobos_mcp.jobs import JobOsMcpClient


def create_server(client: JobOsMcpClient) -> FastMCP:
    server = FastMCP(
        "JobOS Jobs",
        instructions="Operate JobOS jobs only through the shared authenticated application API.",
    )

    @server.tool(name="job_list", structured_output=True)
    async def job_list(
        sort: str = "manual",
        query: str | None = None,
        status_group: str | None = None,
    ) -> dict[str, Any]:
        """List jobs using JobOS filtering and ordering."""
        return await client.list_jobs(sort=sort, query=query, status_group=status_group)

    @server.tool(name="job_inspect", structured_output=True)
    async def job_inspect(job_id: str) -> dict[str, Any]:
        """Inspect one normalized JobOS job record."""
        return await client.inspect_job(job_id)

    @server.tool(name="job_select", structured_output=True)
    async def job_select(job_id: str) -> dict[str, Any]:
        """Select the active JobOS job context."""
        return await client.select_job(job_id)

    @server.tool(name="job_reorder", structured_output=True)
    async def job_reorder(job_ids: list[str]) -> dict[str, Any]:
        """Replace the complete manual JobOS job order."""
        return await client.reorder_jobs(job_ids)

    @server.tool(name="job_update_status", structured_output=True)
    async def job_update_status(
        job_id: str,
        target_status: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Change a job status through the shared JobOS transition command."""
        return await client.update_status(job_id, target_status, reason=reason)

    return server


def main() -> None:
    base_url = os.environ.get("JOBOS_API_BASE_URL", "http://127.0.0.1:8766")
    device_token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
    if not device_token:
        raise RuntimeError("JOBOS_DEVICE_TOKEN is required")
    client = JobOsMcpClient(base_url=base_url, device_token=device_token)
    try:
        create_server(client).run(transport="stdio")
    finally:
        asyncio.run(client.aclose())

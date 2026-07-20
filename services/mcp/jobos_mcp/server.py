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
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """List jobs using JobOS filtering and ordering."""
        return await client.list_jobs(
            sort=sort,
            query=query,
            status_group=status_group,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="job_inspect", structured_output=True)
    async def job_inspect(job_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Inspect one normalized JobOS job record."""
        return await client.inspect_job(job_id, idempotency_key=idempotency_key)

    @server.tool(name="job_select", structured_output=True)
    async def job_select(job_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Select the active JobOS job context."""
        return await client.select_job(job_id, idempotency_key=idempotency_key)

    @server.tool(name="job_reorder", structured_output=True)
    async def job_reorder(job_ids: list[str], idempotency_key: str | None = None) -> dict[str, Any]:
        """Replace the complete manual JobOS job order."""
        return await client.reorder_jobs(job_ids, idempotency_key=idempotency_key)

    @server.tool(name="job_update_status", structured_output=True)
    async def job_update_status(
        job_id: str,
        target_status: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Change a job status through the shared JobOS transition command."""
        return await client.update_status(
            job_id, target_status, reason=reason, idempotency_key=idempotency_key
        )

    @server.tool(name="workspace_inspect", structured_output=True)
    async def workspace_inspect(idempotency_key: str | None = None) -> dict[str, Any]:
        """Inspect the current shared JobOS workspace snapshot."""
        return await client.inspect_workspace(idempotency_key=idempotency_key)

    @server.tool(name="workspace_update", structured_output=True)
    async def workspace_update(
        snapshot: dict[str, Any], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Replace the atomic shared workspace snapshot."""
        return await client.update_workspace(snapshot, idempotency_key=idempotency_key)

    @server.tool(name="document_list", structured_output=True)
    async def document_list(job_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """List trusted registered artifacts for a job."""
        return await client.list_documents(job_id, idempotency_key=idempotency_key)

    @server.tool(name="document_refresh", structured_output=True)
    async def document_refresh(job_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Refresh a job's trusted artifact manifest."""
        return await client.refresh_documents(job_id, idempotency_key=idempotency_key)

    @server.tool(name="document_render", structured_output=True)
    async def document_render(
        job_id: str, source_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Start the fixed PDF resume render command for a job source."""
        return await client.render_document(job_id, source_id, idempotency_key=idempotency_key)

    @server.tool(name="document_register", structured_output=True)
    async def document_register(
        job_id: str, artifact_reference: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Register an opaque facade artifact reference through JobOS."""
        return await client.register_document(
            job_id, artifact_reference, idempotency_key=idempotency_key
        )

    @server.tool(name="document_approve", structured_output=True)
    async def document_approve(
        job_id: str, artifact_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Approve one exact successful resume artifact for its job."""
        return await client.approve_document(
            job_id, artifact_id, idempotency_key=idempotency_key
        )

    @server.tool(name="document_select", structured_output=True)
    async def document_select(
        artifact_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Select a registered artifact in the shared document workspace."""
        return await client.select_document(artifact_id, idempotency_key=idempotency_key)

    def browser(name: str, arguments: dict[str, Any], key: str | None, timeout_ms: int = 5_000):
        return client.browser_command(name, arguments, idempotency_key=key, timeout_ms=timeout_ms)

    @server.tool(name="browser_tabs_inspect", structured_output=True)
    async def browser_tabs_inspect(timeout_ms: int = 5_000) -> dict[str, Any]:
        """Inspect bounded metadata for live desktop browser tabs."""
        return await browser("tabs.inspect", {}, None, timeout_ms)

    @server.tool(name="browser_tab_create", structured_output=True)
    async def browser_tab_create(
        url: str, associated_job_id: str | None = None, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Create a live browser tab for an ordinary HTTP(S) URL."""
        return await browser(
            "tab.create", {"url": url, "associated_job_id": associated_job_id}, idempotency_key
        )

    @server.tool(name="browser_tab_select", structured_output=True)
    async def browser_tab_select(tab_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Select a live browser tab."""
        return await browser("tab.select", {"tab_id": tab_id}, idempotency_key)

    @server.tool(name="browser_tab_close", structured_output=True)
    async def browser_tab_close(tab_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Close a live browser tab."""
        return await browser("tab.close", {"tab_id": tab_id}, idempotency_key)

    @server.tool(name="browser_tabs_reorder", structured_output=True)
    async def browser_tabs_reorder(
        tab_ids: list[str], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Replace the complete live browser tab order."""
        return await browser("tabs.reorder", {"tab_ids": tab_ids}, idempotency_key)

    async def tab_command(name: str, tab_id: str, key: str | None = None):
        return await browser(name, {"tab_id": tab_id}, key)

    @server.tool(name="browser_navigate", structured_output=True)
    async def browser_navigate(
        tab_id: str, url: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Navigate a live tab to an ordinary HTTP(S) URL."""
        return await browser("tab.navigate", {"tab_id": tab_id, "url": url}, idempotency_key)

    @server.tool(name="browser_back", structured_output=True)
    async def browser_back(tab_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Go back in a live tab."""
        return await tab_command("tab.back", tab_id, idempotency_key)

    @server.tool(name="browser_forward", structured_output=True)
    async def browser_forward(tab_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Go forward in a live tab."""
        return await tab_command("tab.forward", tab_id, idempotency_key)

    @server.tool(name="browser_reload", structured_output=True)
    async def browser_reload(tab_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Reload a live tab."""
        return await tab_command("tab.reload", tab_id, idempotency_key)

    @server.tool(name="browser_stop", structured_output=True)
    async def browser_stop(tab_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Stop loading a live tab."""
        return await tab_command("tab.stop", tab_id, idempotency_key)

    @server.tool(name="browser_snapshot", structured_output=True)
    async def browser_snapshot(tab_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Capture a bounded semantic snapshot with opaque target IDs."""
        return await tab_command("page.snapshot", tab_id, idempotency_key)

    @server.tool(name="browser_click", structured_output=True)
    async def browser_click(
        tab_id: str, target_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Click an opaque target from the latest semantic snapshot."""
        return await browser(
            "element.click", {"tab_id": tab_id, "target_id": target_id}, idempotency_key
        )

    @server.tool(name="browser_type", structured_output=True)
    async def browser_type(
        tab_id: str,
        target_id: str,
        text: str,
        clear: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Type bounded text into an opaque snapshot target."""
        return await browser(
            "element.type",
            {"tab_id": tab_id, "target_id": target_id, "text": text, "clear": clear},
            idempotency_key,
        )

    @server.tool(name="browser_scroll", structured_output=True)
    async def browser_scroll(
        tab_id: str, direction: str, amount: int = 600, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Scroll a live tab by a bounded amount."""
        return await browser(
            "page.scroll",
            {"tab_id": tab_id, "direction": direction, "amount": amount},
            idempotency_key,
        )

    @server.tool(name="activity_report", structured_output=True)
    async def activity_report(
        label: str,
        state: str,
        detail: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append one concise agent-origin action to JobOS chronology."""
        return await client.report_activity(
            label, state, detail=detail, idempotency_key=idempotency_key
        )

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

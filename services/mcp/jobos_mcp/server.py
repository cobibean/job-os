from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from jobos_mcp.jobs import JobOsMcpClient


def local_mcp_token() -> str:
    configured = os.environ.get("JOBOS_MCP_TOKEN", "")
    if configured:
        return configured
    if sys.platform == "darwin":
        account = os.environ.get("JOBOS_DEVICE_ID", "primary-device")
        for arguments in (
            ["-s", "com.cobibean.jobos.mcp-token", "-a", account],
            ["-s", "com.cobibean.jobos.mcp-token"],
        ):
            result = subprocess.run(
                ["security", "find-generic-password", "-w", *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            token = result.stdout.strip()
            if result.returncode == 0 and token:
                return token
    raise RuntimeError("JOBOS_MCP_TOKEN is required")


def _document_import_roots() -> tuple[Path, ...]:
    configured_roots = tuple(
        Path(value).expanduser()
        for value in os.environ.get("JOBOS_DOCUMENT_ROOTS", "").split(os.pathsep)
        if value
    )
    if configured_roots:
        roots = configured_roots
    else:
        configured_path = os.environ.get("JOBOS_CONFIG_PATH")
        if configured_path:
            path = Path(configured_path).expanduser()
        else:
            data_dir = os.environ.get("JOBOS_DATA_DIR")
            if data_dir:
                path = Path(data_dir).expanduser() / "config.json"
            elif sys.platform == "darwin":
                path = Path.home() / "Library/Application Support/JobOS/config.json"
            else:
                xdg_data = os.environ.get("XDG_DATA_HOME")
                base = Path(xdg_data).expanduser() if xdg_data else Path.home() / ".local/share"
                path = base / "JobOS/config.json"
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(config, dict)
                or config.get("schemaVersion") != 1
                or not isinstance(config.get("paths"), dict)
            ):
                raise ValueError
            paths = config["paths"]
            raw_artifacts = paths.get("artifacts")
            if not isinstance(raw_artifacts, str) or not raw_artifacts:
                raise ValueError
        except (
            FileNotFoundError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise RuntimeError(
                "Document publication requires JOBOS_DOCUMENT_ROOTS or a valid JobOS local config"
            ) from error
        artifact_root = Path(raw_artifacts).expanduser()
        roots = (artifact_root if artifact_root.is_absolute() else path.parent / artifact_root,)

    resolved: list[Path] = []
    for root in roots:
        if not root.is_absolute():
            raise RuntimeError("Configured document roots must be absolute paths")
        if root.is_symlink():
            raise RuntimeError("Configured document roots must not be symbolic links")
        candidate = root.resolve(strict=True)
        if not candidate.is_dir():
            raise RuntimeError("Configured document roots must be directories")
        resolved.append(candidate)
    return tuple(dict.fromkeys(resolved))


def _read_document_input(
    raw_path: str,
    *,
    roots: tuple[Path, ...],
    maximum: int,
    suffixes: set[str] | None = None,
) -> tuple[str, bytes]:
    def stable_metadata(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            stat.S_IFMT(value.st_mode),
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def descriptor_bytes(descriptor: int) -> bytes:
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    requested = Path(raw_path)
    if not requested.is_absolute():
        raise ValueError("Document input path must be absolute")
    supplied = Path(os.path.abspath(requested))
    selected: tuple[Path, tuple[str, ...], os.stat_result] | None = None
    for root in sorted(roots, key=lambda value: len(value.parts), reverse=True):
        expanded = root.expanduser()
        if expanded.is_symlink():
            raise ValueError("Configured document roots must not be symbolic links")
        resolved = expanded.resolve(strict=True)
        try:
            relative = supplied.relative_to(resolved)
        except ValueError:
            continue
        if relative.parts and all(part not in {"", ".", ".."} for part in relative.parts):
            selected = resolved, relative.parts, os.stat(resolved, follow_symlinks=False)
            break
    if selected is None:
        raise ValueError("Document input is outside configured document roots")
    root, parts, expected_root = selected
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    descriptor: int | None = None
    try:
        current = os.open(root, directory_flags | nofollow)
        descriptors.append(current)
        opened_root = os.fstat(current)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or opened_root.st_dev != expected_root.st_dev
            or opened_root.st_ino != expected_root.st_ino
        ):
            raise ValueError("Configured document root changed during access")
        for part in parts[:-1]:
            current = os.open(part, directory_flags | nofollow, dir_fd=current)
            descriptors.append(current)
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                raise ValueError("Document input ancestor must be a directory")
        descriptor = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Document input must be a regular file")
        if before.st_size <= 0 or before.st_size > maximum:
            raise ValueError("Document input size is invalid")
        content = descriptor_bytes(descriptor)
        after = os.fstat(descriptor)
        if (
            stable_metadata(before) != stable_metadata(after)
            or len(content) != before.st_size
            or len(content) > maximum
        ):
            raise ValueError("Document input changed during access")
        os.lseek(descriptor, 0, os.SEEK_SET)
        confirmation = descriptor_bytes(descriptor)
        confirmed = os.fstat(descriptor)
        if (
            stable_metadata(after) != stable_metadata(confirmed)
            or len(confirmation) != before.st_size
            or sha256(content).digest() != sha256(confirmation).digest()
        ):
            raise ValueError("Document input changed during access")
    except OSError as error:
        raise ValueError(
            "Document input contains a symbolic link or inaccessible component"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for opened in reversed(descriptors):
            os.close(opened)
    candidate = root.joinpath(*parts)
    if suffixes is not None and candidate.suffix.casefold() not in suffixes:
        raise ValueError("Published artifact must be a PDF or DOCX")
    return candidate.name, content


def create_server(
    client: JobOsMcpClient, *, document_roots: tuple[Path, ...] | None = None
) -> FastMCP:
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

    @server.tool(name="job_create_from_browser", structured_output=True)
    async def job_create_from_browser(
        company_name: str,
        title: str,
        canonical_url: str,
        location_text: str,
        description_text: str,
        application_url: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Save one listing inspected from the live JobOS browser through canonical ingest."""
        return await client.create_job(
            company_name=company_name,
            title=title,
            canonical_url=canonical_url,
            location_text=location_text,
            description_text=description_text,
            application_url=application_url,
            idempotency_key=idempotency_key,
        )

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

    @server.tool(name="job_update_description", structured_output=True)
    async def job_update_description(
        job_id: str,
        description_text: str,
        source_note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Replace a saved job's canonical full listing and refresh its durable packet."""
        return await client.update_description(
            job_id,
            description_text,
            source_note=source_note,
            idempotency_key=idempotency_key,
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

    @server.tool(name="document_draft_get", structured_output=True)
    async def document_draft_get(
        job_id: str, document_key: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Read a bounded semantic outline for one editable job document."""
        return await client.get_document_draft(
            job_id, document_key, idempotency_key=idempotency_key  # gitleaks:allow
        )

    @server.tool(name="document_draft_apply", structured_output=True)
    async def document_draft_apply(
        job_id: str,
        document_id: str,
        base_revision: int,
        operations: list[dict[str, Any]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically apply only the five allowlisted editable-document operations."""
        return await client.apply_document_draft(
            job_id,
            document_id,
            base_revision,
            operations,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="document_draft_snapshot", structured_output=True)
    async def document_draft_snapshot(
        job_id: str,
        document_id: str,
        label: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a durable manual checkpoint for one job-owned editable document."""
        return await client.snapshot_document_draft(
            job_id, document_id, label, idempotency_key=idempotency_key
        )

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

    @server.tool(name="document_publish", structured_output=True)
    async def document_publish(
        job_id: str,
        document_key: str,
        document_label: str,
        source_path: str,
        artifact_path: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Publish one finished PDF/DOCX into JobOS.

        Call once per promised format, use the same source file for paired PDF/DOCX,
        then confirm every format with document_list before claiming completion.
        """
        roots = document_roots or _document_import_roots()
        source_filename, source_bytes = _read_document_input(
            source_path, roots=roots, maximum=2_000_000
        )
        artifact_filename, artifact_bytes = _read_document_input(
            artifact_path,
            roots=roots,
            maximum=20_000_000,
            suffixes={".pdf", ".docx"},
        )
        return await client.publish_document(
            job_id,
            document_key,
            document_label,
            source_filename,
            source_bytes,
            artifact_filename,
            artifact_bytes,
            idempotency_key=idempotency_key,
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

    @server.tool(name="document_file_inspect", structured_output=True)
    async def document_file_inspect(
        job_id: str,
        document_key: str,
        timeout_ms: int = 10_000,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Inspect the current canonical DOCX hash, capabilities, and bounded block context."""
        return await client.inspect_document_file(
            job_id,
            document_key,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="document_file_apply", structured_output=True)
    async def document_file_apply(
        job_id: str,
        document_key: str,
        expected_sha256: str,
        operations: list[dict[str, Any]],
        timeout_ms: int = 10_000,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply typed operations to the canonical DOCX with an expected-hash conflict check."""
        return await client.apply_document_file_operations(
            job_id,
            document_key,
            expected_sha256,
            operations,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
        )

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

    @server.tool(name="browser_tab_associate", structured_output=True)
    async def browser_tab_associate(
        tab_id: str, job_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Link a live browser tab to the canonical JobOS job created from it."""
        return await browser(
            "tab.associate", {"tab_id": tab_id, "job_id": job_id}, idempotency_key
        )

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
    mcp_token = local_mcp_token()
    client = JobOsMcpClient(base_url=base_url, device_token=device_token, mcp_token=mcp_token)
    try:
        create_server(client).run(transport="stdio")
    finally:
        asyncio.run(client.aclose())

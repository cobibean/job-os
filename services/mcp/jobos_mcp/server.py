from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from jobos_mcp.jobs import JobOsMcpClient

ConversationId = Annotated[
    str, Field(pattern=r"^conv_[A-Za-z0-9_-]{1,128}$", max_length=133)
]


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


def _local_config_path() -> Path:
    configured_path = os.environ.get("JOBOS_CONFIG_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    data_dir = os.environ.get("JOBOS_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/JobOS/config.json"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data).expanduser() if xdg_data else Path.home() / ".local/share"
    return base / "JobOS/config.json"


def _document_artifact_root() -> Path:
    path = _local_config_path()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(config, dict)
            or config.get("schemaVersion") != 1
            or not isinstance(config.get("paths"), dict)
        ):
            raise ValueError
        raw_artifacts = config["paths"].get("artifacts")
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
            "Document publication requires a valid JobOS local config"
        ) from error

    artifact_root = Path(raw_artifacts).expanduser()
    root = artifact_root if artifact_root.is_absolute() else path.parent / artifact_root
    if root.is_symlink():
        raise RuntimeError("The JobOS artifact root must not use symbolic links")
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("The JobOS artifact root is unavailable") from error
    if not resolved.is_dir():
        raise RuntimeError("The JobOS artifact root must be a directory")
    return resolved


def _publication_workspace_path(conversation_id: str, job_id: str, artifact_root: Path) -> Path:
    if not conversation_id or len(conversation_id) > 133:
        raise ValueError("Invalid conversation ID")
    if not job_id or len(job_id) > 256:
        raise ValueError("Invalid job ID")
    conversation_key = sha256(conversation_id.encode("utf-8")).hexdigest()[:24]
    job_key = sha256(job_id.encode("utf-8")).hexdigest()[:24]
    return artifact_root / "publication-inbox" / conversation_key / job_key


def _prepare_private_directory_chain(root: Path, parts: tuple[str, ...]) -> None:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=current)
                os.fsync(current)
            except FileExistsError:
                pass
            child = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(child)
            metadata = os.fstat(child)
            named = os.stat(part, dir_fd=current, follow_symlinks=False)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_dev != named.st_dev
                or metadata.st_ino != named.st_ino
            ):
                raise RuntimeError("The JobOS publication inbox changed during preparation")
            os.fchmod(child, 0o700)
            current = child
    except OSError as error:
        raise RuntimeError(
            "JobOS publication inbox directories must not use symbolic links"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _prepare_document_publication_workspace(
    conversation_id: str, job_id: str, *, artifact_root: Path | None = None
) -> Path:
    root = artifact_root or _document_artifact_root()
    if root.is_symlink():
        raise RuntimeError("The JobOS artifact root must not use symbolic links")
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise RuntimeError("The JobOS artifact root must be a directory")
    workspace = _publication_workspace_path(conversation_id, job_id, resolved_root)
    _prepare_private_directory_chain(
        resolved_root, workspace.relative_to(resolved_root).parts
    )
    return workspace


def _existing_document_publication_workspace(
    conversation_id: str, job_id: str, *, artifact_root: Path | None = None
) -> Path:
    root = artifact_root or _document_artifact_root()
    if root.is_symlink():
        raise RuntimeError("The JobOS artifact root must not use symbolic links")
    resolved_root = root.resolve(strict=True)
    workspace = _publication_workspace_path(conversation_id, job_id, resolved_root)
    current = resolved_root
    for part in workspace.relative_to(resolved_root).parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(
                "JobOS publication inbox directories must not use symbolic links"
            )
        try:
            metadata = os.stat(current, follow_symlinks=False)
        except OSError as error:
            raise ValueError(
                "Publication inbox is not prepared. Call document_publication_prepare "
                "before creating or publishing documents."
            ) from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("The JobOS publication inbox must contain only directories")
    return workspace


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


def _read_publication_input(
    raw_path: str,
    *,
    conversation_id: str,
    job_id: str,
    artifact_root: Path,
    maximum: int,
    suffixes: set[str] | None = None,
) -> tuple[str, bytes]:
    workspace = _existing_document_publication_workspace(
        conversation_id, job_id, artifact_root=artifact_root
    )
    requested = Path(raw_path)
    if not requested.is_absolute():
        raise ValueError("Document input path must be absolute")
    supplied = Path(os.path.abspath(requested))
    try:
        relative = supplied.relative_to(workspace)
    except ValueError as error:
        raise ValueError("Document input is outside the prepared publication inbox") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Document input is outside the prepared publication inbox")
    # Read from a descriptor chain anchored at the app-owned artifact root, not from
    # the previously checked workspace path. A renamed or symlink-swapped inbox can
    # therefore never redirect this read outside JobOS storage.
    return _read_document_input(
        str(supplied), roots=(artifact_root,), maximum=maximum, suffixes=suffixes
    )


def create_server(
    client: JobOsMcpClient, *, artifact_root: Path | None = None
) -> FastMCP:
    server = FastMCP(
        "JobOS Jobs",
        instructions=(
            "Operate JobOS only through the authenticated application API. "
            "Before multi-step JobOS work, read the jobos://capability-map resource for "
            "workflow sequencing, verification, and authority boundaries. Build Career Profiles "
            "from plain-language user conversation with career_profile_get/search and "
            "career_profile_edit/edit_batch; supporting Evidence is optional. Before creating "
            "resume or cover-letter files, call document_publication_prepare and write every "
            "source/PDF/DOCX into its returned publication_directory. Publish each promised "
            "format, then verify with document_list."
        ),
    )

    @server.resource(
        "jobos://capability-map",
        name="JobOS MCP capability map",
        description=(
            "Workflow recipes, operating rules, authority boundaries, and the complete JobOS "
            "MCP tool catalog for connected agents."
        ),
        mime_type="text/markdown",
    )
    def capability_map() -> str:
        """Read the agent operating manual shipped with this JobOS source revision."""
        path = Path(__file__).resolve().parents[3] / "docs/public/mcp-capability-map.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError("The JobOS MCP capability map is unavailable") from error

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
    async def job_select(
        conversation_id: ConversationId, job_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Select this conversation's active JobOS job context."""
        client.scope_conversation(conversation_id)
        return await client.select_job(job_id, idempotency_key=idempotency_key)

    @server.tool(name="job_reorder", structured_output=True)
    async def job_reorder(job_ids: list[str], idempotency_key: str | None = None) -> dict[str, Any]:
        """Replace the complete manual JobOS job order."""
        return await client.reorder_jobs(job_ids, idempotency_key=idempotency_key)

    @server.tool(name="job_update_status", structured_output=True)
    async def job_update_status(
        conversation_id: ConversationId,
        job_id: str,
        target_status: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Change a job status through the shared JobOS transition command."""
        client.scope_conversation(conversation_id)
        return await client.update_status(
            job_id, target_status, reason=reason, idempotency_key=idempotency_key
        )

    @server.tool(name="job_update_description", structured_output=True)
    async def job_update_description(
        conversation_id: ConversationId,
        job_id: str,
        description_text: str,
        source_note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Replace a saved job's canonical full listing and refresh its durable packet."""
        client.scope_conversation(conversation_id)
        return await client.update_description(
            job_id,
            description_text,
            source_note=source_note,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="career_profile_edit", structured_output=True)
    async def career_profile_edit(
        expected_profile_revision: int,
        operation: Literal["item.create", "item.update", "item.remove"],
        reason: str,
        value: dict[str, Any] | None = None,
        target_id: str | None = None,
        evidence_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit one exact Career Profile edit under the user's selected review mode.

        Ordinary edits may apply immediately when the user allows direct edits. Review-mode
        edits, identity changes, removals, Evidence removal, and loosened claim boundaries
        become proposals for the user. Evidence is optional.
        """
        return await client.edit_career_profile(
            expected_profile_revision=expected_profile_revision,
            operation=operation,
            reason=reason,
            value=value,
            target_id=target_id,
            evidence_ids=evidence_ids,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="career_profile_get", structured_output=True)
    async def career_profile_get() -> dict[str, Any]:
        """Read the exact user-authorized post-cutover Career Profile projection."""
        return await client.get_career_profile_projection()

    @server.tool(name="career_profile_search", structured_output=True)
    async def career_profile_search(
        query: str,
        kinds: list[str] | None = None,
        areas: list[str] | None = None,
        review_statuses: list[str] | None = None,
        has_evidence: bool | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search only the Career Profile items and Evidence authorized for this agent."""
        return await client.search_career_profile(
            query=query,
            kinds=kinds,
            areas=areas,
            review_statuses=review_statuses,
            has_evidence=has_evidence,
            limit=limit,
        )

    @server.tool(name="career_profile_edit_batch", structured_output=True)
    async def career_profile_edit_batch(
        expected_profile_revision: int,
        edits: list[dict[str, Any]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically apply or propose several related Career Profile edits.

        Each edit uses the same item.create, item.update, or item.remove shape as
        career_profile_edit. Evidence IDs remain optional. If any edit is invalid,
        none of the batch is saved.
        """
        return await client.edit_career_profile_batch(
            expected_profile_revision=expected_profile_revision,
            edits=edits,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="career_profile_changes_list", structured_output=True)
    async def career_profile_changes_list(
        status: Literal["pending", "accepted", "rejected", "all"] = "pending",
        limit: int = 25,
    ) -> dict[str, Any]:
        """List this agent's proposals and directly applied Career Profile revisions."""
        return await client.list_career_profile_changes(status=status, limit=limit)

    @server.tool(name="career_profile_evidence_import", structured_output=True)
    async def career_profile_evidence_import(
        expected_profile_revision: int,
        original_filename: str,
        media_type: str,
        source_kind: Literal["resume", "portfolio", "supporting_document", "citation"],
        source_label: str,
        content_base64: str,
        captured_at: str | None = None,
        extractions: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Optionally import immutable source Evidence into the JobOS-owned vault.

        Evidence is never required to create or edit profile items. Any supplied
        extractions remain subject to JobOS review and truthfulness rules.
        """
        return await client.import_career_profile_evidence(
            expected_profile_revision=expected_profile_revision,
            original_filename=original_filename,
            media_type=media_type,
            source_kind=source_kind,
            source_label=source_label,
            content_base64=content_base64,
            captured_at=captured_at,
            extractions=extractions,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="career_profile_evidence_inspect", structured_output=True)
    async def career_profile_evidence_inspect(
        evidence_id: str,
        byte_start: int = 0,
        byte_length: int = 65_536,
    ) -> dict[str, Any]:
        """Read a bounded segment of Evidence already authorized for this agent."""
        return await client.inspect_career_profile_evidence(
            evidence_id,
            byte_start=byte_start,
            byte_length=byte_length,
        )

    @server.tool(name="workspace_inspect", structured_output=True)
    async def workspace_inspect(
        conversation_id: ConversationId, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Inspect global layout merged with this conversation's job context."""
        client.scope_conversation(conversation_id)
        return await client.inspect_workspace(idempotency_key=idempotency_key)

    @server.tool(name="workspace_update", structured_output=True)
    async def workspace_update(
        conversation_id: ConversationId,
        snapshot: dict[str, Any], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Update global layout and this conversation's document projection."""
        client.scope_conversation(conversation_id)
        return await client.update_workspace(snapshot, idempotency_key=idempotency_key)

    @server.tool(name="document_list", structured_output=True)
    async def document_list(
        conversation_id: ConversationId, job_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """List trusted registered artifacts for a job."""
        client.scope_conversation(conversation_id)
        return await client.list_documents(job_id, idempotency_key=idempotency_key)

    @server.tool(name="document_draft_get", structured_output=True)
    async def document_draft_get(
        conversation_id: ConversationId,
        job_id: str,
        document_key: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Read a bounded semantic outline for one editable job document."""
        client.scope_conversation(conversation_id)
        return await client.get_document_draft(
            job_id, document_key, idempotency_key=idempotency_key  # gitleaks:allow
        )

    @server.tool(name="document_draft_apply", structured_output=True)
    async def document_draft_apply(
        conversation_id: ConversationId,
        job_id: str,
        document_id: str,
        base_revision: int,
        operations: list[dict[str, Any]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically apply only the five allowlisted editable-document operations."""
        client.scope_conversation(conversation_id)
        return await client.apply_document_draft(
            job_id,
            document_id,
            base_revision,
            operations,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="document_draft_snapshot", structured_output=True)
    async def document_draft_snapshot(
        conversation_id: ConversationId,
        job_id: str,
        document_id: str,
        label: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a durable manual checkpoint for one job-owned editable document."""
        client.scope_conversation(conversation_id)
        return await client.snapshot_document_draft(
            job_id, document_id, label, idempotency_key=idempotency_key
        )

    @server.tool(name="document_refresh", structured_output=True)
    async def document_refresh(
        conversation_id: ConversationId, job_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Refresh a job's trusted artifact manifest."""
        client.scope_conversation(conversation_id)
        return await client.refresh_documents(job_id, idempotency_key=idempotency_key)

    @server.tool(name="document_render", structured_output=True)
    async def document_render(
        conversation_id: ConversationId,
        job_id: str,
        source_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start the fixed PDF resume render command for a job source."""
        client.scope_conversation(conversation_id)
        return await client.render_document(job_id, source_id, idempotency_key=idempotency_key)

    @server.tool(name="document_register", structured_output=True)
    async def document_register(
        conversation_id: ConversationId,
        job_id: str,
        artifact_reference: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Register an opaque facade artifact reference through JobOS."""
        client.scope_conversation(conversation_id)
        return await client.register_document(
            job_id, artifact_reference, idempotency_key=idempotency_key
        )

    @server.tool(name="document_publication_prepare", structured_output=True)
    async def document_publication_prepare(
        conversation_id: ConversationId,
        job_id: str,
    ) -> dict[str, Any]:
        """Prepare JobOS's only supported publication inbox for this session and job.

        Call this before generating a resume or cover letter. Write the source file and
        every promised PDF/DOCX directly into publication_directory. Do not use a
        workspace, repository, temporary folder, or agent-profile cache for publication.
        """
        client.scope_conversation(conversation_id)
        workspace = _prepare_document_publication_workspace(
            conversation_id, job_id, artifact_root=artifact_root
        )
        return {
            "ready": True,
            "job_id": job_id,
            "publication_directory": str(workspace),
            "accepted_artifact_formats": ["pdf", "docx"],
            "source_maximum_bytes": 2_000_000,
            "artifact_maximum_bytes": 20_000_000,
            "next_step": (
                "Write the source and finished artifacts into publication_directory, "
                "call document_publish once per format, then confirm them with document_list."
            ),
        }

    @server.tool(name="document_publish", structured_output=True)
    async def document_publish(
        conversation_id: ConversationId,
        job_id: str,
        document_key: str,
        document_label: str,
        source_path: str,
        artifact_path: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Publish one finished PDF/DOCX from JobOS's prepared publication inbox.

        First call document_publication_prepare. Call this once per promised format,
        use the same source file for paired PDF/DOCX, then confirm every format with
        document_list before claiming completion. Other filesystem paths are never read.
        """
        client.scope_conversation(conversation_id)
        resolved_artifact_root = artifact_root or _document_artifact_root()
        try:
            source_filename, source_bytes = _read_publication_input(
                source_path,
                conversation_id=conversation_id,
                job_id=job_id,
                artifact_root=resolved_artifact_root,
                maximum=2_000_000,
            )
            artifact_filename, artifact_bytes = _read_publication_input(
                artifact_path,
                conversation_id=conversation_id,
                job_id=job_id,
                artifact_root=resolved_artifact_root,
                maximum=20_000_000,
                suffixes={".pdf", ".docx"},
            )
        except ValueError as error:
            if "outside" in str(error):
                raise ValueError(
                    "Document is outside this job's JobOS publication inbox. Call "
                    "document_publication_prepare, write the source and artifact into its "
                    "publication_directory, and retry."
                ) from error
            raise
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

    @server.tool(name="document_select", structured_output=True)
    async def document_select(
        conversation_id: ConversationId,
        artifact_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Select a registered artifact in the shared document workspace."""
        client.scope_conversation(conversation_id)
        return await client.select_document(artifact_id, idempotency_key=idempotency_key)

    @server.tool(name="document_file_inspect", structured_output=True)
    async def document_file_inspect(
        conversation_id: ConversationId,
        job_id: str,
        document_key: str,
        timeout_ms: int = 10_000,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Inspect the current canonical DOCX hash, capabilities, and bounded block context."""
        client.scope_conversation(conversation_id)
        return await client.inspect_document_file(
            conversation_id,
            job_id,
            document_key,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
        )

    @server.tool(name="document_file_apply", structured_output=True)
    async def document_file_apply(
        conversation_id: ConversationId,
        job_id: str,
        document_key: str,
        expected_sha256: str,
        operations: list[dict[str, Any]],
        timeout_ms: int = 10_000,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply typed operations to the canonical DOCX with an expected-hash conflict check."""
        client.scope_conversation(conversation_id)
        return await client.apply_document_file_operations(
            conversation_id,
            job_id,
            document_key,
            expected_sha256,
            operations,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
        )

    def browser(
        conversation_id: str,
        name: str,
        arguments: dict[str, Any],
        key: str | None,
        timeout_ms: int = 5_000,
    ):
        return client.browser_command(
            conversation_id, name, arguments, idempotency_key=key, timeout_ms=timeout_ms
        )

    @server.tool(name="browser_tabs_inspect", structured_output=True)
    async def browser_tabs_inspect(
        conversation_id: ConversationId, timeout_ms: int = 5_000
    ) -> dict[str, Any]:
        """Inspect bounded metadata for live desktop browser tabs."""
        return await browser(conversation_id, "tabs.inspect", {}, None, timeout_ms)

    @server.tool(name="browser_tab_create", structured_output=True)
    async def browser_tab_create(
        conversation_id: ConversationId,
        url: str,
        associated_job_id: str | None = None,
        activate: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a live browser tab for an ordinary HTTP(S) URL."""
        return await browser(
            conversation_id,
            "tab.create",
            {"url": url, "associated_job_id": associated_job_id, "activate": activate},
            idempotency_key,
        )

    @server.tool(name="browser_tab_select", structured_output=True)
    async def browser_tab_select(
        conversation_id: ConversationId, tab_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Select a live browser tab."""
        return await browser(conversation_id, "tab.select", {"tab_id": tab_id}, idempotency_key)

    @server.tool(name="browser_tab_associate", structured_output=True)
    async def browser_tab_associate(
        conversation_id: ConversationId,
        tab_id: str,
        job_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Link a live browser tab to the canonical JobOS job created from it."""
        return await browser(
            conversation_id,
            "tab.associate", {"tab_id": tab_id, "job_id": job_id}, idempotency_key
        )

    @server.tool(name="browser_tab_close", structured_output=True)
    async def browser_tab_close(
        conversation_id: ConversationId, tab_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Close a live browser tab."""
        return await browser(conversation_id, "tab.close", {"tab_id": tab_id}, idempotency_key)

    @server.tool(name="browser_tabs_reorder", structured_output=True)
    async def browser_tabs_reorder(
        conversation_id: ConversationId,
        tab_ids: list[str],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Replace the complete live browser tab order."""
        return await browser(
            conversation_id, "tabs.reorder", {"tab_ids": tab_ids}, idempotency_key
        )

    async def tab_command(
        conversation_id: str, name: str, tab_id: str, key: str | None = None
    ):
        return await browser(conversation_id, name, {"tab_id": tab_id}, key)

    @server.tool(name="browser_navigate", structured_output=True)
    async def browser_navigate(
        conversation_id: ConversationId,
        tab_id: str,
        url: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Navigate a live tab to an ordinary HTTP(S) URL."""
        return await browser(
            conversation_id, "tab.navigate", {"tab_id": tab_id, "url": url}, idempotency_key
        )

    @server.tool(name="browser_back", structured_output=True)
    async def browser_back(
        conversation_id: ConversationId, tab_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Go back in a live tab."""
        return await tab_command(conversation_id, "tab.back", tab_id, idempotency_key)

    @server.tool(name="browser_forward", structured_output=True)
    async def browser_forward(
        conversation_id: ConversationId, tab_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Go forward in a live tab."""
        return await tab_command(conversation_id, "tab.forward", tab_id, idempotency_key)

    @server.tool(name="browser_reload", structured_output=True)
    async def browser_reload(
        conversation_id: ConversationId, tab_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Reload a live tab."""
        return await tab_command(conversation_id, "tab.reload", tab_id, idempotency_key)

    @server.tool(name="browser_stop", structured_output=True)
    async def browser_stop(
        conversation_id: ConversationId, tab_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Stop loading a live tab."""
        return await tab_command(conversation_id, "tab.stop", tab_id, idempotency_key)

    @server.tool(name="browser_snapshot", structured_output=True)
    async def browser_snapshot(
        conversation_id: ConversationId,
        tab_id: str,
        text_start: int = 0,
        text_length: int = 12_000,
        include_targets: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Read one bounded page-text segment.

        Pagination defaults to the first 12,000-character segment with targets.
        For later segments, pass every pagination argument and use the returned
        next_text_start.
        """
        return await browser(
            conversation_id,
            "page.snapshot",
            {
                "tab_id": tab_id,
                "text_start": text_start,
                "text_length": text_length,
                "include_targets": include_targets,
            },
            idempotency_key,
        )

    @server.tool(name="browser_click", structured_output=True)
    async def browser_click(
        conversation_id: ConversationId,
        tab_id: str,
        target_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Click an opaque target from the latest semantic snapshot."""
        return await browser(
            conversation_id,
            "element.click", {"tab_id": tab_id, "target_id": target_id}, idempotency_key
        )

    @server.tool(name="browser_type", structured_output=True)
    async def browser_type(
        conversation_id: ConversationId,
        tab_id: str,
        target_id: str,
        text: str,
        clear: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Type bounded text into an opaque snapshot target."""
        return await browser(
            conversation_id,
            "element.type",
            {"tab_id": tab_id, "target_id": target_id, "text": text, "clear": clear},
            idempotency_key,
        )

    @server.tool(name="browser_scroll", structured_output=True)
    async def browser_scroll(
        conversation_id: ConversationId,
        tab_id: str,
        direction: str,
        amount: int = 600,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Scroll a live tab by a bounded amount."""
        return await browser(
            conversation_id,
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
    client = JobOsMcpClient(
        base_url=base_url,
        device_token=device_token,
        mcp_token=mcp_token,
        agent_id=os.environ.get("JOBOS_CAREER_PROFILE_AGENT_ID", "trusted-local-mcp"),
        agent_token=os.environ.get("JOBOS_CAREER_PROFILE_AGENT_TOKEN"),
    )
    try:
        create_server(client).run(transport="stdio")
    finally:
        asyncio.run(client.aclose())

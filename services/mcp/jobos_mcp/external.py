"""The complete JobOS tool catalog for separately authenticated external clients."""

from __future__ import annotations

import base64
import inspect
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, get_type_hints

from mcp.server.fastmcp import FastMCP

from jobos_mcp.jobs import JobOsMcpClient
from jobos_mcp.remote_files import MAX_TRANSFER, decode_upload, generate_pair, put_file, read_file
from jobos_mcp.server import _document_artifact_root, create_server

# These operations actually need persistent selection/browser/inbox context.
_CONTEXT_TOOLS = {
    "job_select",
    "workspace_inspect",
    "workspace_update",
    "document_select",
    "document_file_inspect",
    "document_file_apply",
}


def create_external_server(client: JobOsMcpClient, *, artifact_root: Path | None = None) -> FastMCP:
    if not client.external:
        raise ValueError("External server requires JobOsMcpClient(external=True)")
    server = create_server(client, artifact_root=artifact_root)
    server._mcp_server.instructions = (
        "Operate JobOS through its authenticated API. Read jobos://capability-map first. "
        "No active JobOS turn is required. Context-dependent tools transparently resolve one "
        "shared external conversation and return conversation_id. No internal agent starts. "
        "Use document_generate with plain text/markdown to create and publish matched PDF/DOCX. "
        "Use file_upload/file_read and document_publish for existing files, not local paths. "
        "Browser and document_file tools still need the configured desktop online."
    )

    async def context(conversation_id: str | None) -> str:
        client.scope_turn(None, None)
        if conversation_id is None:
            result = await client._request("POST", "/v1/external-mcp/session")
            conversation_id = str(result["conversation_id"])
        else:
            # Validate explicit context at the API before any local file access.
            client.scope_turn(conversation_id, None)
            await client._request("POST", "/v1/external-mcp/session")
        client.scope_turn(conversation_id, None)
        return conversation_id

    def adapt(tool):
        fn = tool.fn
        hints = get_type_hints(fn, include_extras=True)
        signature = inspect.signature(fn)
        needs_context = tool.name in _CONTEXT_TOOLS or tool.name.startswith("browser_")
        parameters = [
            parameter.replace(annotation=hints.get(name, parameter.annotation))
            for name, parameter in signature.parameters.items()
            if name not in {"conversation_id", "turn_id"}
        ]
        parameters.append(
            inspect.Parameter(
                "conversation_id",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=str | None,
            )
        )

        async def call(**kwargs: Any) -> dict[str, Any]:
            conversation_id = kwargs.pop("conversation_id", None)
            client.scope_turn(None, None)
            if needs_context or conversation_id is not None:
                conversation_id = await context(conversation_id)
            result = await fn(conversation_id=conversation_id, turn_id=None, **kwargs)
            if conversation_id is not None:
                result = {**result, "conversation_id": conversation_id}
            return result

        call.__name__ = tool.name
        call.__doc__ = tool.description
        call.__signature__ = signature.replace(
            parameters=parameters, return_annotation=dict[str, Any]
        )
        return call

    # Reuse the authoritative registrations; do not maintain a second divergent catalog.
    for tool in server._tool_manager.list_tools():
        server.remove_tool(tool.name)
        if tool.name not in {"document_publish", "document_publication_prepare"}:
            server.add_tool(adapt(tool), name=tool.name, structured_output=True)

    def root() -> Path:
        value = artifact_root or _document_artifact_root()
        if value.is_symlink():
            raise ValueError("Artifact root must not be a symbolic link")
        return value.resolve(strict=True)

    @server.tool(structured_output=True)
    async def external_session() -> dict[str, Any]:
        """Inspect/create the shared external context, without launching an agent or active turn."""
        client.scope_turn(None, None)
        return await client._request("POST", "/v1/external-mcp/session")

    @server.tool(structured_output=True)
    async def document_publication_prepare(
        job_id: str, conversation_id: str | None = None
    ) -> dict[str, Any]:
        """Prepare a remote publication context. No filesystem access is required by the caller."""
        conversation_id = await context(conversation_id)
        await client.inspect_job(job_id)
        return {
            "ready": True,
            "job_id": job_id,
            "conversation_id": conversation_id,
            "accepted_artifact_formats": ["pdf", "docx"],
            "maximum_transfer_bytes": MAX_TRANSFER,
            "next_step": "Call document_generate, or file_upload then document_publish.",
        }

    @server.tool(structured_output=True)
    async def file_upload(
        job_id: str,
        content: str,
        format: Literal["txt", "md", "json", "pdf", "docx"],
        encoding: Literal["text", "base64"] = "text",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload up to 2 MB into this job's inbox; return a file ID, never a local path."""
        data = decode_upload(content, encoding)
        conversation_id = await context(conversation_id)
        await client.inspect_job(job_id)
        return {
            **put_file(root(), conversation_id, job_id, format, data),
            "conversation_id": conversation_id,
            "job_id": job_id,
        }

    @server.tool(structured_output=True)
    async def file_read(
        job_id: str,
        file_id: str,
        byte_start: int = 0,
        byte_length: int = 65_536,
        encoding: Literal["text", "base64"] = "base64",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Read an inbox file chunk. Text chunks must align UTF-8 byte boundaries."""
        if byte_start < 0 or not 1 <= byte_length <= 65_536:
            raise ValueError("Invalid byte range (maximum 65536 bytes)")
        conversation_id = await context(conversation_id)
        await client.inspect_job(job_id)
        data = read_file(root(), conversation_id, job_id, file_id)
        chunk = data[byte_start : byte_start + byte_length]
        return {
            "file_id": file_id,
            "conversation_id": conversation_id,
            "size_bytes": len(data),
            "sha256": sha256(data).hexdigest(),
            "byte_start": byte_start,
            "byte_length": len(chunk),
            "has_more": byte_start + len(chunk) < len(data),
            "encoding": encoding,
            "content": chunk.decode("utf-8")
            if encoding == "text"
            else base64.b64encode(chunk).decode("ascii"),
        }

    @server.tool(structured_output=True)
    async def artifact_read(
        artifact_id: str, byte_start: int = 0, byte_length: int = 65_536
    ) -> dict[str, Any]:
        """Read a bounded base64 chunk of a registered artifact, rechecking its trusted checksum."""
        client.scope_turn(None, None)
        return await client._request(
            "GET",
            f"/v1/external-mcp/artifacts/{client._segment(artifact_id, 'artifact ID')}",
            params={"byte_start": byte_start, "byte_length": byte_length},
        )

    @server.tool(structured_output=True)
    async def document_publish(
        job_id: str,
        document_key: str,
        document_label: str,
        source_file_id: str,
        artifact_file_id: str,
        idempotency_key: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Publish a PDF or DOCX from file IDs; use the same source ID for both formats."""
        if not artifact_file_id.endswith((".pdf", ".docx")):
            raise ValueError("Published artifacts must be PDF or DOCX")
        conversation_id = await context(conversation_id)
        source = read_file(root(), conversation_id, job_id, source_file_id)
        artifact = read_file(root(), conversation_id, job_id, artifact_file_id)
        result = await client.publish_document(
            job_id,
            document_key,
            document_label,
            source_file_id,
            source,
            artifact_file_id,
            artifact,
            idempotency_key=idempotency_key,
        )
        return {**result, "conversation_id": conversation_id}

    @server.tool(structured_output=True)
    async def document_generate(
        job_id: str,
        document_key: Literal["resume", "cover_letter", "references"],
        document_label: str,
        markdown: str,
        publish: bool = True,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate matched text-based PDF/DOCX, optionally publish both (default true).

        Supports plain lines, #/##/### headings and - bullets, not HTML, images or rich Markdown.
        Maximum 100 KB text; unsupported font characters fail explicitly. Reuse identical text,
        key and label to retry interrupted paired publication. Never invent career claims.
        """
        conversation_id = await context(conversation_id)
        await client.inspect_job(job_id)
        pdf, docx = generate_pair(markdown)
        files = {
            suffix: put_file(root(), conversation_id, job_id, suffix, data)
            for suffix, data in (("md", markdown.encode("utf-8")), ("pdf", pdf), ("docx", docx))
        }
        publications = []
        if publish:
            for suffix in ("pdf", "docx"):
                key = sha256(
                    (
                        job_id
                        + document_key
                        + document_label
                        + files["md"]["file_id"]
                        + files[suffix]["file_id"]
                    ).encode()
                ).hexdigest()
                publications.append(
                    await document_publish(
                        job_id,
                        document_key,
                        document_label,
                        files["md"]["file_id"],
                        files[suffix]["file_id"],
                        idempotency_key=f"remote-generate-{key}",
                        conversation_id=conversation_id,
                    )
                )
        verified = await client.list_documents(job_id) if publish else None
        if publish:
            artifacts = verified.get("artifacts", [])
            matched = [
                artifact
                for artifact in artifacts
                if artifact.get("document_key") == document_key
                and artifact.get("sha256") in {files["pdf"]["sha256"], files["docx"]["sha256"]}
                and artifact.get("render_status") == "succeeded"
            ]
            if {artifact.get("sha256") for artifact in matched} != {
                files["pdf"]["sha256"],
                files["docx"]["sha256"],
            } or len({artifact.get("source_revision") for artifact in matched}) != 1:
                raise ValueError("Publication verification failed; re-read document_list")
        return {
            "conversation_id": conversation_id,
            "job_id": job_id,
            "files": files,
            "published": publish,
            "publications": publications,
            "documents": verified,
        }

    return server

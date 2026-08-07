from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx


class JobOsMcpClient:
    """Thin agent Adapter over the authenticated JobOS application Interface."""

    def __init__(
        self,
        *,
        base_url: str,
        device_token: str,
        mcp_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {device_token}",
                "X-JobOS-MCP-Token": mcp_token,
            },
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_jobs(
        self,
        *,
        sort: str = "manual",
        query: str | None = None,
        status_group: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        params = {"sort": sort, "origin": "mcp", "idempotency_key": self._key(idempotency_key)}
        if query:
            params["query"] = query
        if status_group:
            params["status_group"] = status_group
        return await self._request("GET", "/v1/jobs", params=params)

    async def inspect_job(
        self, job_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/jobs/{self._segment(job_id, 'job ID')}",
            params={"origin": "mcp", "idempotency_key": self._key(idempotency_key)},
        )

    async def create_job(
        self,
        *,
        company_name: str,
        title: str,
        canonical_url: str,
        location_text: str,
        description_text: str,
        application_url: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/jobs",
            json={
                "company_name": company_name,
                "title": title,
                "canonical_url": canonical_url,
                "location_text": location_text,
                "description_text": description_text,
                "application_url": application_url,
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    @staticmethod
    def _key(value: str | None) -> str:
        return value or str(uuid4())

    @staticmethod
    def _segment(value: str, label: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value
        ):
            raise ValueError(f"Invalid {label}")
        return quote(value, safe="")

    @staticmethod
    def _document_key(value: str) -> str:
        if value not in {"resume", "cover_letter", "references"}:
            raise ValueError("Invalid document key")
        return quote(value, safe="")

    async def select_job(
        self, job_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            "/v1/workspace/jobs/selection",
            json={"job_id": job_id, "origin": "mcp", "idempotency_key": self._key(idempotency_key)},
        )

    async def reorder_jobs(
        self, job_ids: list[str], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            "/v1/jobs/order",
            json={
                "job_ids": job_ids,
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def update_status(
        self,
        job_id: str,
        target_status: str,
        *,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "target_status": target_status,
            "origin": "mcp",
            "idempotency_key": self._key(idempotency_key),
        }
        if reason is not None:
            payload["reason"] = reason
        return await self._request(
            "PUT", f"/v1/jobs/{self._segment(job_id, 'job ID')}/status", json=payload
        )

    async def update_description(
        self,
        job_id: str,
        description_text: str,
        *,
        source_note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/v1/jobs/{self._segment(job_id, 'job ID')}/description",
            json={
                "description_text": description_text,
                "source": "jobhunter_agent",
                "provenance": source_note,
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def inspect_workspace(self, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/v1/workspace",
            params={"origin": "mcp", "idempotency_key": self._key(idempotency_key)},
        )

    async def update_workspace(
        self, snapshot: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            "/v1/workspace",
            json={**snapshot, "origin": "mcp", "idempotency_key": self._key(idempotency_key)},
        )

    async def list_documents(
        self, job_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/jobs/{self._segment(job_id, 'job ID')}/artifacts",
            params={"origin": "mcp", "idempotency_key": self._key(idempotency_key)},
        )

    async def get_document_draft(
        self,
        job_id: str,
        document_key: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        job_segment = self._segment(job_id, "job ID")
        key_segment = self._document_key(document_key)
        return await self._request(
            "GET",
            f"/v1/jobs/{job_segment}/editable-document-outlines/{key_segment}",
            params={"origin": "mcp", "idempotency_key": self._key(idempotency_key)},
        )

    async def _owned_editable_document(
        self, job_id: str, document_id: str
    ) -> dict[str, Any]:
        listing = await self._request(
            "GET", f"/v1/jobs/{self._segment(job_id, 'job ID')}/editable-documents"
        )
        documents = listing.get("documents")
        if not isinstance(documents, list):
            raise ValueError("JobOS returned an invalid editable-document list")
        document = next(
            (
                item
                for item in documents
                if isinstance(item, dict) and item.get("document_id") == document_id
            ),
            None,
        )
        if document is None:
            raise ValueError("Editable document is not owned by the supplied job")
        return document

    async def apply_document_draft(
        self,
        job_id: str,
        document_id: str,
        base_revision: int,
        operations: list[dict[str, Any]],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        await self._owned_editable_document(job_id, document_id)
        return await self._request(
            "POST",
            f"/v1/editable-documents/{self._segment(document_id, 'document ID')}/operations",
            json={
                "base_revision": base_revision,
                "operations": operations,
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def snapshot_document_draft(
        self,
        job_id: str,
        document_id: str,
        label: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        document = await self._owned_editable_document(job_id, document_id)
        revision = document.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("JobOS returned an invalid editable-document revision")
        return await self._request(
            "POST",
            f"/v1/editable-documents/{self._segment(document_id, 'document ID')}/snapshots",
            json={
                "base_revision": revision,
                "reason": "manual",
                "label": label,
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def refresh_documents(
        self, job_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/jobs/{self._segment(job_id, 'job ID')}/artifacts/refresh",
            json={"origin": "mcp", "idempotency_key": self._key(idempotency_key)},
        )

    async def render_document(
        self, job_id: str, source_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/jobs/{self._segment(job_id, 'job ID')}/artifacts/render",
            json={
                "source_id": source_id,
                "output_format": "pdf",
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def register_document(
        self, job_id: str, artifact_reference: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/jobs/{self._segment(job_id, 'job ID')}/artifacts/register",
            json={
                "artifact_reference": artifact_reference,
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def publish_document(
        self,
        job_id: str,
        document_key: str,
        document_label: str,
        source_filename: str,
        source_bytes: bytes,
        artifact_filename: str,
        artifact_bytes: bytes,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/jobs/{self._segment(job_id, 'job ID')}/artifacts/publish",
            json={
                "document_key": document_key,
                "document_label": document_label,
                "source_filename": source_filename,
                "source_base64": base64.b64encode(source_bytes).decode("ascii"),
                "artifact_filename": artifact_filename,
                "artifact_base64": base64.b64encode(artifact_bytes).decode("ascii"),
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def approve_document(
        self, job_id: str, artifact_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        job_segment = self._segment(job_id, "job ID")
        artifact_segment = self._segment(artifact_id, "artifact ID")
        return await self._request(
            "POST",
            f"/v1/jobs/{job_segment}/artifacts/{artifact_segment}/approve",
            json={"origin": "mcp", "idempotency_key": self._key(idempotency_key)},
        )

    async def select_document(
        self, artifact_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        # This prerequisite read is internal to one document-selection action;
        # omit MCP read metadata so the visible chronology receives one mutation row.
        workspace = await self._request("GET", "/v1/workspace")
        selected_job_id = workspace.get("selected_job_id")
        if not isinstance(selected_job_id, str) or not selected_job_id:
            raise ValueError("Select a job before selecting one of its documents")
        documents = await self._request(
            "GET", f"/v1/jobs/{self._segment(selected_job_id, 'job ID')}/artifacts"
        )
        artifacts = documents.get("artifacts")
        if not isinstance(artifacts, list) or not any(
            isinstance(artifact, dict)
            and artifact.get("artifact_id") == artifact_id
            and artifact.get("job_id") == selected_job_id
            for artifact in artifacts
        ):
            raise ValueError("Artifact is not registered for the selected job")
        request_fields = {
            "revision", "selected_preset", "layouts", "selected_job_id",
            "active_center_surface", "browser_tabs", "active_browser_tab_id",
            "active_artifact_id", "active_artifact_page", "active_artifact_zoom",
        }
        command = {key: workspace[key] for key in request_fields if key in workspace}
        command.update({"active_artifact_id": artifact_id, "active_center_surface": "document"})
        return await self.update_workspace(command, idempotency_key=idempotency_key)

    async def browser_command(
        self,
        command: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        timeout_ms: int = 5_000,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/browser/commands",
            json={
                "command": command,
                "arguments": arguments,
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
                "timeout_ms": timeout_ms,
            },
        )

    async def report_activity(
        self,
        label: str,
        state: str,
        *,
        detail: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/activity",
            json={
                "label": label,
                "state": state,
                "detail": detail or {},
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return dict(response.json())

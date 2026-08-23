from __future__ import annotations

import base64
import re
from contextvars import ContextVar
from math import log2
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

_ABSOLUTE_PATH = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9_:/])/(?!/)(?:"
    r"[^\r\n,;:()\[\]{}<>\"']*?\.[A-Za-z0-9]{1,16}(?=\s|[,;:()\[\]{}<>\"']|$)"
    r"|[^\r\n,;:()\[\]{}<>\"']+"
    r")"
    r"|[A-Za-z]:\\(?:[^\r\n,;:]*?\.[A-Za-z0-9]{1,16}(?=\s|[,;:]|$)|[^\r\n,;:]+)"
    r")"
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,253}\."
    r"[A-Za-z0-9_-]{5,2048}\.[A-Za-z0-9_-]{16,512}(?![A-Za-z0-9_-])"
)
_OPAQUE_CREDENTIAL_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_+=-])[A-Za-z0-9_+=-]{32,256}(?![A-Za-z0-9_+=-])"
)
_SENSITIVE_VALUE = re.compile(
    r"(?:"
    r"(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+"
    r"|bearer\s+\S+"
    r"|(?:token|api[_-]?key|password|secret|credential|authorization[_-]?code)"
    r"\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)
_STANDALONE_CREDENTIAL = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"Basic\s+[A-Za-z0-9+/]{4,}={0,2}"
    r"|sk-(?:proj-)?[A-Za-z0-9_-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{16,}"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SIGNED_URL = re.compile(
    r"[?&](?:x-amz-signature|signature|signed|sig|token|api[_-]?key)=[^&#]+",
    re.IGNORECASE,
)
_CREDENTIAL_PATH = re.compile(r"(?:^|/)(?:\.hermes|\.ssh|mcp-tokens|auth\.json|\.env)(?:/|$)")


def _is_opaque_credential(value: str) -> bool:
    if re.fullmatch(r"[a-fA-F0-9]{32,256}", value):
        return False
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
        value,
    ):
        return False
    classes = sum(
        bool(re.search(pattern, value))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[_+=-]")
    )
    if classes < 3:
        return False
    counts = {character: value.count(character) for character in set(value)}
    entropy = -sum((count / len(value)) * log2(count / len(value)) for count in counts.values())
    return entropy >= 4.25


def _redact_opaque_credentials(value: str) -> str:
    return _OPAQUE_CREDENTIAL_CANDIDATE.sub(
        lambda match: "[redacted]"
        if _is_opaque_credential(match.group(0))
        else match.group(0),
        value,
    )


def _safe_error_message(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        return "JobOS API request failed"
    sanitized = _SENSITIVE_VALUE.sub("[redacted]", value)
    sanitized = _STANDALONE_CREDENTIAL.sub("[redacted]", sanitized)
    sanitized = _JWT.sub("[redacted]", sanitized)
    sanitized = _redact_opaque_credentials(sanitized)
    sanitized = _ABSOLUTE_PATH.sub("[protected path]", sanitized)
    if _SIGNED_URL.search(sanitized):
        sanitized = "[protected signed URL]"
    if _CREDENTIAL_PATH.search(sanitized):
        sanitized = "[protected path]"
    return sanitized if sanitized.strip() else "JobOS API request failed"


class JobOsMcpError(RuntimeError):
    """Bounded public API failure suitable for MCP tool output."""

    def __init__(
        self, *, code: str, message: str, retryable: bool, correlation_id: str
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.correlation_id = correlation_id
        super().__init__(
            f"{code}: {message} (retryable={str(retryable).lower()}, "
            f"correlation_id={correlation_id})"
        )


class JobOsMcpClient:
    """Thin agent Adapter over the authenticated JobOS application Interface."""

    def __init__(
        self,
        *,
        base_url: str,
        device_token: str,
        mcp_token: str,
        agent_id: str = "trusted-local-mcp",
        agent_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", agent_id):
            raise ValueError("Invalid Career Profile agent ID")
        _ = device_token  # Kept for construction compatibility; MCP uses a distinct principal.
        self._conversation_scope: ContextVar[str | None] = ContextVar(
            "jobos_mcp_conversation_id", default=None
        )
        self._career_profile_agent_headers = {
            "X-JobOS-Agent-Id": agent_id,
            "X-JobOS-Agent-Token": agent_token or mcp_token,
        }
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {mcp_token}",
                "X-JobOS-MCP-Token": mcp_token,
            },
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def scope_conversation(self, conversation_id: str) -> None:
        if not re.fullmatch(r"conv_[A-Za-z0-9_-]{1,128}", conversation_id):
            raise ValueError("Invalid conversation ID")
        self._conversation_scope.set(conversation_id)

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

    async def edit_career_profile(
        self,
        *,
        expected_profile_revision: int,
        operation: str,
        reason: str,
        value: dict[str, Any] | None = None,
        target_id: str | None = None,
        evidence_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply or propose one Career Profile edit under the user's selected mode."""
        return await self._request(
            "POST",
            "/v1/career-profile/agent-edits",
            headers=self._career_profile_agent_headers,
            json={
                "expected_profile_revision": expected_profile_revision,
                "idempotency_key": self._key(idempotency_key),
                "operation": operation,
                "target_id": target_id,
                "reason": reason,
                "value": value,
                "evidence_ids": evidence_ids or [],
            },
        )

    async def get_career_profile_projection(self) -> dict[str, Any]:
        """Read only the exact authorized post-cutover projection."""
        return await self._request(
            "GET",
            "/v1/career-profile/consumer-projection",
            headers=self._career_profile_agent_headers,
        )

    async def search_career_profile(
        self,
        *,
        query: str,
        kinds: list[str] | None = None,
        areas: list[str] | None = None,
        review_statuses: list[str] | None = None,
        has_evidence: bool | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query, "limit": limit}
        if kinds:
            params["kinds"] = kinds
        if areas:
            params["areas"] = areas
        if review_statuses:
            params["review_statuses"] = review_statuses
        if has_evidence is not None:
            params["has_evidence"] = has_evidence
        return await self._request(
            "GET",
            "/v1/career-profile/agent-search",
            headers=self._career_profile_agent_headers,
            params=params,
        )

    async def edit_career_profile_batch(
        self,
        *,
        expected_profile_revision: int,
        edits: list[dict[str, Any]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/career-profile/agent-edits/batch",
            headers=self._career_profile_agent_headers,
            json={
                "expected_profile_revision": expected_profile_revision,
                "idempotency_key": self._key(idempotency_key),
                "edits": edits,
            },
        )

    async def list_career_profile_changes(
        self, *, status: str = "pending", limit: int = 25
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/v1/career-profile/agent-changes",
            headers=self._career_profile_agent_headers,
            params={"status": status, "limit": limit},
        )

    async def import_career_profile_evidence(
        self,
        *,
        expected_profile_revision: int,
        original_filename: str,
        media_type: str,
        source_kind: str,
        source_label: str,
        content_base64: str,
        captured_at: str | None = None,
        extractions: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/career-profile/agent-evidence",
            headers=self._career_profile_agent_headers,
            json={
                "expected_profile_revision": expected_profile_revision,
                "idempotency_key": self._key(idempotency_key),
                "original_filename": original_filename,
                "media_type": media_type,
                "captured_at": captured_at,
                "provenance": {
                    "source_kind": source_kind,
                    "source_label": source_label,
                    "method": "agent_import",
                },
                "content_base64": content_base64,
                "extractions": extractions or [],
            },
        )

    async def inspect_career_profile_evidence(
        self,
        evidence_id: str,
        *,
        byte_start: int = 0,
        byte_length: int = 65_536,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/career-profile/agent-evidence/{self._segment(evidence_id, 'Evidence ID')}",
            headers=self._career_profile_agent_headers,
            params={"byte_start": byte_start, "byte_length": byte_length},
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
    def _job_id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[^/\s]{1,128}", value):
            raise ValueError("Invalid job ID")
        return value

    @staticmethod
    def _document_key(value: str) -> str:
        if value not in {"resume", "cover_letter", "references"}:
            raise ValueError("Invalid document key")
        return quote(value, safe="")

    async def select_job(
        self, job_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        conversation_id = self._conversation_scope.get()
        if conversation_id is None:
            raise ValueError("Conversation ID is required")
        return await self._request(
            "PUT",
            f"/v1/conversations/{conversation_id}/workspace/job",
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
                "source": "mcp_agent",
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
        conversation_id = self._conversation_scope.get()
        if conversation_id is None:
            raise ValueError("Conversation ID is required")
        # These prerequisite reads are internal to one document-selection action;
        # omit MCP read metadata so chronology receives one visible mutation.
        workspace_jobs = await self._request("GET", "/v1/workspace/jobs")
        selected_job_id = workspace_jobs.get("selected_job_id")
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
        return await self._request(
            "PUT",
            f"/v1/conversations/{conversation_id}/workspace/document",
            json={
                "active_artifact_id": artifact_id,
                "active_artifact_page": 1,
                "active_artifact_zoom": 1.0,
                "origin": "mcp",
                "idempotency_key": self._key(idempotency_key),
            },
        )

    async def inspect_document_file(
        self,
        conversation_id: str,
        job_id: str,
        document_key: str,
        *,
        idempotency_key: str | None = None,
        timeout_ms: int = 10_000,
    ) -> dict[str, Any]:
        return await self.browser_command(
            conversation_id,
            "document.inspect",
            {
                "job_id": self._job_id(job_id),
                "document_key": self._document_key(document_key),
            },
            idempotency_key=idempotency_key,
            timeout_ms=timeout_ms,
        )

    async def apply_document_file_operations(
        self,
        conversation_id: str,
        job_id: str,
        document_key: str,
        expected_sha256: str,
        operations: list[dict[str, Any]],
        *,
        idempotency_key: str | None = None,
        timeout_ms: int = 10_000,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
            raise ValueError("Invalid expected DOCX SHA-256")
        if not 1 <= len(operations) <= 100 or any(
            not isinstance(operation, dict) for operation in operations
        ):
            raise ValueError("Invalid DOCX operation list")
        return await self.browser_command(
            conversation_id,
            "document.apply_operations",
            {
                "job_id": self._job_id(job_id),
                "document_key": self._document_key(document_key),
                "expected_sha256": expected_sha256,
                "operations": operations,
            },
            idempotency_key=idempotency_key,
            timeout_ms=timeout_ms,
        )

    async def browser_command(
        self,
        conversation_id: str,
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
                "conversation_id": self._conversation_id(conversation_id),
                "idempotency_key": self._key(idempotency_key),
                "timeout_ms": timeout_ms,
            },
        )

    @staticmethod
    def _conversation_id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            r"conv_[A-Za-z0-9_-]{1,128}", value
        ):
            raise ValueError("Invalid conversation ID")
        return value

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
        conversation_id = self._conversation_scope.get()
        if conversation_id is not None:
            params = dict(kwargs.pop("params", {}) or {})
            params["conversation_id"] = conversation_id
            kwargs["params"] = params
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise JobOsMcpError(
                code="api_unreachable",
                message="JobOS API is unavailable",
                retryable=True,
                correlation_id="unavailable",
            ) from error
        if response.is_error:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            valid_envelope = (
                isinstance(payload, dict) and payload.get("error_schema") == "jobos-error-v1"
            )
            code = payload.get("code") if valid_envelope else None
            message = payload.get("message") if valid_envelope else None
            retryable = payload.get("retryable") if valid_envelope else None
            correlation_id = payload.get("correlation_id") if valid_envelope else None
            if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
                code = f"http_{response.status_code}"
            message = _safe_error_message(message)
            if not isinstance(retryable, bool):
                retryable = response.status_code in {408, 425, 429, 502, 503, 504}
            if (
                not isinstance(correlation_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", correlation_id)
            ):
                header_id = response.headers.get("x-correlation-id", "")
                correlation_id = (
                    header_id
                    if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", header_id)
                    else "unavailable"
                )
            raise JobOsMcpError(
                code=code,
                message=message,
                retryable=retryable,
                correlation_id=correlation_id,
            )
        return dict(response.json())

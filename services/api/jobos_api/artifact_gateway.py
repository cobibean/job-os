from __future__ import annotations

from typing import Any, Protocol

from jobos_api.job_repository import Unavailable


class ArtifactGateway(Protocol):
    """Private-capability seam for artifact publication and rendering."""

    def is_available(self) -> bool: ...

    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]: ...

    def register_artifact(self, job_id: str, artifact_reference: str) -> dict[str, Any]: ...

    def publish_document_artifact(
        self,
        job_id: str,
        document_key: str,
        document_label: str,
        source_path: str,
        artifact_path: str,
    ) -> dict[str, Any]: ...

    def render_resume(
        self, job_id: str, source_id: str, output_options: dict[str, Any]
    ) -> dict[str, Any]: ...


class UnavailableArtifactGateway:
    """Stable errors for optional private publication and rendering capabilities."""

    def is_available(self) -> bool:
        return False

    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        raise Unavailable("Artifact provider is unavailable")

    def register_artifact(self, job_id: str, artifact_reference: str) -> dict[str, Any]:
        raise Unavailable("Artifact provider is unavailable")

    def publish_document_artifact(
        self,
        job_id: str,
        document_key: str,
        document_label: str,
        source_path: str,
        artifact_path: str,
    ) -> dict[str, Any]:
        raise Unavailable("Artifact provider is unavailable")

    def render_resume(
        self, job_id: str, source_id: str, output_options: dict[str, Any]
    ) -> dict[str, Any]:
        raise Unavailable("Renderer is unavailable")

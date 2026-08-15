from __future__ import annotations

from typing import Any, Protocol

from jobos_api.job_repository import NotFound, Unavailable


class ArtifactGateway(Protocol):
    """Private-capability seam for artifact publication and rendering."""

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
    """Truthful public default until local artifact ownership lands."""

    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        return []

    def register_artifact(self, job_id: str, artifact_reference: str) -> dict[str, Any]:
        raise NotFound(f"Unknown artifact {artifact_reference}")

    def publish_document_artifact(
        self,
        job_id: str,
        document_key: str,
        document_label: str,
        source_path: str,
        artifact_path: str,
    ) -> dict[str, Any]:
        raise Unavailable("Artifact publication is unavailable")

    def render_resume(
        self, job_id: str, source_id: str, output_options: dict[str, Any]
    ) -> dict[str, Any]:
        raise Unavailable("Artifact rendering is unavailable")

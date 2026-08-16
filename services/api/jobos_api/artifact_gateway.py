from __future__ import annotations

from typing import Any, Protocol

from jobos_api.job_repository import Unavailable


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
    """Stable errors for optional private publication and rendering capabilities."""

    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        raise Unavailable("JobHunter artifact refresh is unconfigured")

    def register_artifact(self, job_id: str, artifact_reference: str) -> dict[str, Any]:
        raise Unavailable("JobHunter artifact registration is unconfigured")

    def publish_document_artifact(
        self,
        job_id: str,
        document_key: str,
        document_label: str,
        source_path: str,
        artifact_path: str,
    ) -> dict[str, Any]:
        raise Unavailable("JobHunter artifact publication is unconfigured")

    def render_resume(
        self, job_id: str, source_id: str, output_options: dict[str, Any]
    ) -> dict[str, Any]:
        raise Unavailable("JobHunter artifact rendering is unconfigured")

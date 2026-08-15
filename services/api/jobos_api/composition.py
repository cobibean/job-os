from __future__ import annotations

from importlib import import_module

from jobos_api.artifact_gateway import ArtifactGateway, UnavailableArtifactGateway
from jobos_api.job_repository import JobRepository, Unavailable
from jobos_api.settings import Settings
from jobos_api.sqlite_job_repository import SQLiteJobRepository


def create_job_services(settings: Settings) -> tuple[JobRepository, ArtifactGateway]:
    if settings.job_provider == "sqlite":
        return (
            SQLiteJobRepository(settings.resolved_jobs_db_path(), initialize=False),
            UnavailableArtifactGateway(),
        )
    if settings.job_provider == "job-hunter":
        if settings.job_hunter_db_path is None:
            raise Unavailable("The JobHunter provider requires JOBOS_JOB_HUNTER_DB_PATH")
        module = import_module("jobos_api.private_adapters.job_hunter")
        return module.create_job_hunter_services(
            settings.job_hunter_db_path, settings.hermes_job_hunter_cwd
        )
    raise Unavailable(f"Unsupported job provider {settings.job_provider}")

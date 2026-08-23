from __future__ import annotations

from importlib import import_module

from jobos_api.artifact_gateway import ArtifactGateway, UnavailableArtifactGateway
from jobos_api.job_repository import JobRepository, Unavailable
from jobos_api.settings import Settings
from jobos_api.sqlite_job_repository import SQLiteJobRepository


def create_job_services(settings: Settings) -> tuple[JobRepository, ArtifactGateway]:
    if settings.job_provider == "sqlite":
        job_repository: JobRepository = SQLiteJobRepository(
            settings.resolved_jobs_db_path(),
            initialize=False,
            installation_profile_id=settings.installation_profile_id,
        )
    elif settings.job_provider == "job-hunter":
        if settings.job_hunter_db_path is None:
            raise Unavailable("The JobHunter provider requires JOBOS_JOB_HUNTER_DB_PATH")
        module = import_module("jobos_api.private_adapters.job_hunter")
        job_repository = module.create_job_hunter_job_repository(
            settings.job_hunter_db_path,
            settings.hermes_job_hunter_cwd,
        )
    else:
        raise Unavailable(f"Unsupported job provider {settings.job_provider}")

    if settings.artifact_provider == "local":
        artifact_gateway: ArtifactGateway = UnavailableArtifactGateway()
    elif settings.artifact_provider == "gateway":
        if settings.job_hunter_db_path is None:
            raise Unavailable("The JobHunter artifact gateway requires JOBOS_JOB_HUNTER_DB_PATH")
        module = import_module("jobos_api.private_adapters.job_hunter")
        artifact_gateway = module.create_job_hunter_artifact_gateway(
            settings.job_hunter_db_path,
            settings.hermes_job_hunter_cwd,
        )
    else:
        raise Unavailable(f"Unsupported artifact provider {settings.artifact_provider}")
    return job_repository, artifact_gateway

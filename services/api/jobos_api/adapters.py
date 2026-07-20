from __future__ import annotations

from pathlib import Path

from jobos_api.jobs import EmptyJobFacade, JobFacade


def create_job_hunter_adapter(database_path: Path | None) -> JobFacade:
    """Compose the reviewed job-hunter Facade without exposing its storage to JobOS."""
    if database_path is None:
        return EmptyJobFacade()

    from job_hunter.facade import JobHunterFacade
    from job_hunter.storage import JobStorage

    return JobHunterFacade(JobStorage(database_path, initialize=False))

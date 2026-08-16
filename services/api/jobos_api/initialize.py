from __future__ import annotations

import fcntl
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jobos_api.job_repository import NotFound
from jobos_api.local_config import (
    LocalConfigError,
    config_path,
    load_credentials,
    read_config,
    store_credentials,
)
from jobos_api.sqlite_job_repository import SQLiteJobRepository
from jobos_api.state_store import JobOsStateStore
from jobos_api.synthetic_demo import (
    DEMO_JOB_ID,
    reset_demo,
    reset_demo_document,
    seed_demo_document_once,
    seed_demo_once,
)


@dataclass(frozen=True, slots=True)
class InitializationResult:
    created: bool
    demo_seeded: bool
    credential_provider: str
    status: str = "ready"

    def safe_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "created": self.created,
            "demoSeeded": self.demo_seeded,
            "credentialProvider": self.credential_provider,
        }


def _write_config(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


@contextmanager
def _initialization_lock(data_dir: Path) -> Iterator[None]:
    lock_path = data_dir / ".initialize.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _initialize_jobos_locked(
    data_dir: Path,
    *,
    config_path_override: Path | None = None,
    demo_enabled: bool = True,
    reset_demo_requested: bool = False,
    reset_confirmed: bool = False,
    state_db_path: Path | None = None,
    jobs_db_path: Path | None = None,
    artifacts_path: Path | None = None,
    logs_path: Path | None = None,
    credentials_path: Path | None = None,
) -> InitializationResult:
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(data_dir, 0o700)
    configured_state = state_db_path or Path("state/jobos.db")
    configured_jobs = jobs_db_path or Path("jobs/jobs.db")
    configured_artifacts = artifacts_path or Path("artifacts")
    configured_logs = logs_path or Path("logs")
    configured_credentials = credentials_path or Path("credentials")
    target = (config_path_override or config_path(data_dir)).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def resolve(value: Path) -> Path:
        return value if value.is_absolute() else data_dir / value

    def persisted_path(value: Path) -> str:
        return str(value) if target.parent == data_dir else str(resolve(value))

    def resolve_from_config(value: Path) -> Path:
        return value if value.is_absolute() else target.parent / value

    for directory in (
        resolve(configured_state).parent,
        resolve(configured_jobs).parent,
        resolve(configured_artifacts),
        resolve(configured_logs),
    ):
        existed = directory.exists()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_relative_to(data_dir) or not existed:
            os.chmod(directory, 0o700)
    created = not target.exists()
    if created:
        device_id = f"local-{uuid4().hex[:12]}"
        credential_store = store_credentials(
            data_dir=data_dir,
            device_id=device_id,
            device_token=secrets.token_urlsafe(32),
            mcp_token=secrets.token_urlsafe(32),
            credentials_dir=resolve(configured_credentials),
        )
        if credential_store.get("provider") == "file" and target.parent != data_dir:
            credential_store["path"] = str(resolve(configured_credentials) / "local.json")
        config: dict[str, object] = {
            "schemaVersion": 1,
            "mode": "local-service",
            "apiBaseUrl": "http://127.0.0.1:8766",
            "deviceId": device_id,
            "credentialStore": credential_store,
            "paths": {
                "stateDatabase": persisted_path(configured_state),
                "jobsDatabase": persisted_path(configured_jobs),
                "artifacts": persisted_path(configured_artifacts),
                "logs": persisted_path(configured_logs),
            },
            "jobProvider": "sqlite",
            "artifactProvider": "local",
            "agentProvider": "offline",
            "demoEnabled": demo_enabled,
        }
        _write_config(target, config)
    else:
        config = read_config(target)
        try:
            load_credentials(config, target.parent)
        except LocalConfigError:
            device_id = config.get("deviceId")
            if not isinstance(device_id, str) or not device_id:
                raise LocalConfigError("JobOS device configuration is invalid.") from None
            configured_store = config.get("credentialStore")
            repair_credentials_dir = resolve(configured_credentials)
            if isinstance(configured_store, dict) and configured_store.get("provider") == "file":
                configured_path = configured_store.get("path")
                if isinstance(configured_path, str) and configured_path:
                    repair_credentials_dir = resolve_from_config(Path(configured_path)).parent
            config["credentialStore"] = store_credentials(
                data_dir=data_dir,
                device_id=device_id,
                device_token=secrets.token_urlsafe(32),
                mcp_token=secrets.token_urlsafe(32),
                credentials_dir=repair_credentials_dir,
            )
            _write_config(target, config)

    paths = config["paths"]
    if not isinstance(paths, dict):
        raise RuntimeError("JobOS path configuration is invalid")
    state_path = resolve_from_config(Path(str(paths["stateDatabase"])))
    jobs_path = resolve_from_config(Path(str(paths["jobsDatabase"])))
    state_store = JobOsStateStore(state_path)
    repository = SQLiteJobRepository(jobs_path)
    state_store.initialize()
    os.chmod(state_path, 0o600)
    os.chmod(jobs_path, 0o600)
    ledger_path = jobs_path
    seeded_job_id: str | None = None
    if reset_demo_requested:
        seeded_job_id = reset_demo(repository, ledger_path, confirmed=reset_confirmed)
        reset_demo_document(state_store)
    elif bool(config.get("demoEnabled", False)):
        seeded_job_id = seed_demo_once(repository, ledger_path)
        try:
            demo = repository.get_job(DEMO_JOB_ID)
        except NotFound:
            pass
        else:
            if demo.synthetic_demo:
                seed_demo_document_once(state_store)
    if ledger_path.exists():
        os.chmod(ledger_path, 0o600)
    if seeded_job_id is not None:
        state_store.save_job_selection(seeded_job_id, "user")
    provider = config.get("credentialStore", {})
    provider_name = provider.get("provider", "unknown") if isinstance(provider, dict) else "unknown"
    return InitializationResult(
        created=created,
        demo_seeded=seeded_job_id is not None,
        credential_provider=str(provider_name),
    )


def initialize_jobos(
    data_dir: Path,
    *,
    config_path_override: Path | None = None,
    demo_enabled: bool = True,
    reset_demo_requested: bool = False,
    reset_confirmed: bool = False,
    state_db_path: Path | None = None,
    jobs_db_path: Path | None = None,
    artifacts_path: Path | None = None,
    logs_path: Path | None = None,
    credentials_path: Path | None = None,
) -> InitializationResult:
    resolved_data_dir = data_dir.expanduser().resolve()
    resolved_data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(resolved_data_dir, 0o700)
    with _initialization_lock(resolved_data_dir):
        return _initialize_jobos_locked(
            resolved_data_dir,
            config_path_override=config_path_override,
            demo_enabled=demo_enabled,
            reset_demo_requested=reset_demo_requested,
            reset_confirmed=reset_confirmed,
            state_db_path=state_db_path,
            jobs_db_path=jobs_db_path,
            artifacts_path=artifacts_path,
            logs_path=logs_path,
            credentials_path=credentials_path,
        )

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jobos_api.artifact_repository import ArtifactStorageError, open_directory_chain

PROFILE_ID_PATTERN = re.compile(r"^jprof_[a-f0-9]{32}$")
SWITCH_ID_PATTERN = re.compile(r"^jpswitch_[a-f0-9]{32}$")
MAX_PROFILES = 32
MAX_REGISTRY_BYTES = 1024 * 1024
MAX_IDEMPOTENCY_REPLAYS = 256


class InstallationProfileError(RuntimeError):
    code = "installation_profile_error"


class InstallationProfileRegistryError(InstallationProfileError):
    code = "profile_registry_invalid"


class InstallationProfileConflict(InstallationProfileError):
    code = "profile_registry_conflict"


class InstallationProfileNotFound(InstallationProfileError):
    code = "installation_profile_not_found"


class InstallationProfileLimitReached(InstallationProfileError):
    code = "installation_profile_limit_reached"


class InstallationProfileStorageError(InstallationProfileError):
    code = "profile_storage_unavailable"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_profile_id() -> str:
    return f"jprof_{secrets.token_hex(16)}"


def new_switch_id() -> str:
    return f"jpswitch_{secrets.token_hex(16)}"


def validate_profile_id(value: str) -> str:
    if not PROFILE_ID_PATTERN.fullmatch(value):
        raise ValueError("JobOS Profile identifier is invalid")
    return value


def normalized_display_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 64:
        raise ValueError("JobOS Profile name must contain 1 to 64 characters")
    if any(
        unicodedata.category(character).startswith("C") or character in {"/", "\\"}
        for character in name
    ):
        raise ValueError("JobOS Profile name contains unsupported characters")
    return name


def display_name_key(value: str) -> str:
    return unicodedata.normalize("NFKC", normalized_display_name(value)).casefold()


class AnchoredRuntime(StrictModel):
    job_provider: Literal["sqlite", "job-hunter"]
    artifact_provider: Literal["local", "gateway"]
    state_db_path: Path
    jobs_db_path: Path
    local_artifact_root: Path
    job_hunter_db_path: Path | None = None
    facade_source_path: Path | None = None
    artifact_roots: tuple[Path, ...] = ()

    @model_validator(mode="after")
    def validate_paths(self) -> AnchoredRuntime:
        paths = (
            self.state_db_path,
            self.jobs_db_path,
            self.local_artifact_root,
            *self.artifact_roots,
            *([self.job_hunter_db_path] if self.job_hunter_db_path else []),
            *([self.facade_source_path] if self.facade_source_path else []),
        )
        if any(not path.is_absolute() for path in paths):
            raise ValueError("anchored runtime paths must be absolute")
        if (self.job_provider == "job-hunter" or self.artifact_provider == "gateway") and (
            self.job_hunter_db_path is None or self.facade_source_path is None
        ):
            raise ValueError("private providers require their anchored adapter paths")
        return self

    @classmethod
    def from_runtime(cls, runtime: object) -> AnchoredRuntime:
        def value(name: str, fallback: object = None) -> object:
            if isinstance(runtime, dict):
                return runtime.get(name, fallback)
            return getattr(runtime, name, fallback)

        state_db_path = Path(value("state_db_path"))
        jobs_value = value("jobs_db_path")
        local_artifact_value = value("local_artifact_root")
        return cls(
            job_provider=str(value("job_provider", "sqlite")),
            artifact_provider=str(value("artifact_provider", "local")),
            state_db_path=state_db_path,
            jobs_db_path=(
                Path(jobs_value) if jobs_value is not None else state_db_path.parent / "jobs.db"
            ),
            local_artifact_root=(
                Path(local_artifact_value)
                if local_artifact_value is not None
                else state_db_path.parent / "artifacts"
            ),
            job_hunter_db_path=(
                Path(value("job_hunter_db_path"))
                if value("job_hunter_db_path") is not None
                else None
            ),
            facade_source_path=(
                Path(value("facade_source_path"))
                if value("facade_source_path") is not None
                else None
            ),
            artifact_roots=tuple(Path(item) for item in value("artifact_roots", ()) or ()),
        )


class InstallationProfileRecord(StrictModel):
    profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    display_name: str
    storage_mode: Literal["anchored", "managed"]
    created_at: datetime
    updated_at: datetime
    anchored_runtime: AnchoredRuntime | None = None

    @model_validator(mode="after")
    def validate_record(self) -> InstallationProfileRecord:
        normalized_display_name(self.display_name)
        if (self.storage_mode == "anchored") != (self.anchored_runtime is not None):
            raise ValueError("only an anchored JobOS Profile may contain runtime paths")
        return self


class PendingProfileSwitch(StrictModel):
    switch_id: str = Field(pattern=r"^jpswitch_[a-f0-9]{32}$")
    from_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    target_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    status: Literal["pending", "activating"]
    created_at: datetime


class CompletedProfileSwitch(StrictModel):
    switch_id: str = Field(pattern=r"^jpswitch_[a-f0-9]{32}$")
    from_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    target_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    status: Literal["succeeded", "rolled_back"]
    active_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    completed_at: datetime
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def validate_completion(self) -> CompletedProfileSwitch:
        expected_active = (
            self.target_profile_id if self.status == "succeeded" else self.from_profile_id
        )
        if self.active_profile_id != expected_active:
            raise ValueError("completed JobOS Profile switch is inconsistent")
        if (self.status == "rolled_back") != (self.error_code is not None):
            raise ValueError("completed JobOS Profile switch error is inconsistent")
        return self


class PersistentIdempotencyReplay(StrictModel):
    operation: Literal["create", "rename", "activate"]
    key_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    response_json: str = Field(max_length=MAX_REGISTRY_BYTES)
    result_profile_id: str | None = Field(
        default=None,
        pattern=r"^jprof_[a-f0-9]{32}$",
    )


class InstallationProfileRegistryData(StrictModel):
    schema_version: Literal[1] = 1
    registry_revision: int = Field(ge=1)
    active_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    profiles: tuple[InstallationProfileRecord, ...]
    pending_switch: PendingProfileSwitch | None = None
    last_switch: CompletedProfileSwitch | None = None
    idempotency_replays: tuple[PersistentIdempotencyReplay, ...] = ()

    @model_validator(mode="after")
    def validate_registry(self) -> InstallationProfileRegistryData:
        if not self.profiles or len(self.profiles) > MAX_PROFILES:
            raise ValueError("registry must contain between 1 and 32 JobOS Profiles")
        ids = [profile.profile_id for profile in self.profiles]
        if len(ids) != len(set(ids)) or self.active_profile_id not in ids:
            raise ValueError("registry JobOS Profile identifiers are inconsistent")
        names = [display_name_key(profile.display_name) for profile in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("JobOS Profile names must be unique")
        anchored = [profile for profile in self.profiles if profile.storage_mode == "anchored"]
        if len(anchored) != 1:
            raise ValueError("registry must contain exactly one anchored JobOS Profile")
        if len(self.idempotency_replays) > MAX_IDEMPOTENCY_REPLAYS:
            raise ValueError("registry contains too many idempotency records")
        replay_keys = [
            (replay.operation, replay.key_hash) for replay in self.idempotency_replays
        ]
        if len(replay_keys) != len(set(replay_keys)):
            raise ValueError("registry idempotency records are inconsistent")
        if self.pending_switch is not None:
            if (
                self.pending_switch.from_profile_id not in ids
                or self.pending_switch.target_profile_id not in ids
                or self.pending_switch.target_profile_id == self.pending_switch.from_profile_id
            ):
                raise ValueError("pending JobOS Profile switch is inconsistent")
            expected_active = (
                self.pending_switch.from_profile_id
                if self.pending_switch.status == "pending"
                else self.pending_switch.target_profile_id
            )
            if self.active_profile_id != expected_active:
                raise ValueError("pending JobOS Profile switch is inconsistent")
        if self.last_switch is not None:
            if not {
                self.last_switch.from_profile_id,
                self.last_switch.target_profile_id,
                self.last_switch.active_profile_id,
            }.issubset(ids):
                raise ValueError("completed JobOS Profile switch is inconsistent")
            if (
                self.pending_switch is None
                and self.active_profile_id != self.last_switch.active_profile_id
            ):
                raise ValueError("completed JobOS Profile switch is inconsistent")
        return self


class JobOsProfileSummary(StrictModel):
    profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    display_name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class JobOsProfileList(StrictModel):
    registry_revision: int = Field(ge=1)
    active_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    anchored_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    profiles: tuple[JobOsProfileSummary, ...]


class CreateJobOsProfileRequest(StrictModel):
    display_name: str
    idempotency_key: str = Field(min_length=1, max_length=128)


class RenameJobOsProfileRequest(StrictModel):
    display_name: str
    expected_registry_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ActivateJobOsProfileRequest(StrictModel):
    expected_registry_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class JobOsProfileSwitchAccepted(StrictModel):
    switch_id: str = Field(pattern=r"^jpswitch_[a-f0-9]{32}$")
    from_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    to_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    status: Literal["accepted"] = "accepted"


class JobOsProfileSwitchStatus(StrictModel):
    switch_id: str = Field(pattern=r"^jpswitch_[a-f0-9]{32}$")
    target_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    status: Literal["pending", "activating", "succeeded", "rolled_back"]
    active_profile_id: str = Field(pattern=r"^jprof_[a-f0-9]{32}$")
    error_code: str | None = None


class ManagedProfilePaths(StrictModel):
    root: Path
    state_db_path: Path
    jobs_db_path: Path
    local_artifact_root: Path
    evidence_root: Path


def public_profile_list(data: InstallationProfileRegistryData) -> JobOsProfileList:
    profiles = sorted(
        data.profiles,
        key=lambda item: (
            item.profile_id != data.active_profile_id,
            item.created_at,
            item.profile_id,
        ),
    )
    return JobOsProfileList(
        registry_revision=data.registry_revision,
        active_profile_id=data.active_profile_id,
        anchored_profile_id=next(
            profile.profile_id for profile in data.profiles if profile.storage_mode == "anchored"
        ),
        profiles=tuple(
            JobOsProfileSummary(
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                active=profile.profile_id == data.active_profile_id,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )
            for profile in profiles
        ),
    )


def _assert_safe_managed_root(installation_root: Path, profile_id: str) -> Path:
    validate_profile_id(profile_id)
    installation_root = installation_root.absolute()
    profiles_root = installation_root / "profiles"
    root = profiles_root / profile_id
    try:
        root.relative_to(profiles_root)
    except ValueError as error:
        raise InstallationProfileStorageError("Managed JobOS Profile root is invalid") from error
    current = installation_root.anchor and Path(installation_root.anchor) or Path("/")
    for part in installation_root.parts[1:]:
        current /= part
        if current.exists() and stat.S_ISLNK(current.lstat().st_mode):
            raise InstallationProfileStorageError("Managed JobOS Profile root is unavailable")
    current = installation_root
    for part in ("profiles", profile_id):
        current /= part
        if current.exists() and stat.S_ISLNK(current.lstat().st_mode):
            raise InstallationProfileStorageError("Managed JobOS Profile root is unavailable")
    return root


def _assert_no_symlink_ancestors(path: Path, message: str) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        except OSError as error:
            raise InstallationProfileStorageError(message) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise InstallationProfileStorageError(message)


def managed_profile_paths(installation_root: Path, profile_id: str) -> ManagedProfilePaths:
    root = _assert_safe_managed_root(installation_root, profile_id)
    return ManagedProfilePaths(
        root=root,
        state_db_path=root / "state/jobos.db",
        jobs_db_path=root / "jobs/jobs.db",
        local_artifact_root=root / "artifacts",
        evidence_root=root / "state/career-profile-evidence",
    )


def ensure_managed_profile_storage(installation_root: Path, profile_id: str) -> ManagedProfilePaths:
    paths = managed_profile_paths(installation_root, profile_id)
    _assert_no_symlink_ancestors(
        installation_root,
        "Managed JobOS Profile root is unavailable",
    )
    installation_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_no_symlink_ancestors(
        installation_root,
        "Managed JobOS Profile root is unavailable",
    )
    try:
        with open_directory_chain(installation_root, "profiles", profile_id) as root:
            os.fchmod(root.descriptor, 0o700)
            for segments in (
                ("profiles", profile_id, "state"),
                ("profiles", profile_id, "jobs"),
                ("profiles", profile_id, "artifacts"),
                ("profiles", profile_id, "state", "career-profile-evidence"),
            ):
                with open_directory_chain(installation_root, *segments) as directory:
                    os.fchmod(directory.descriptor, 0o700)
            probe_name = f".writable-{os.getpid()}-{secrets.token_hex(4)}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(probe_name, flags, 0o600, dir_fd=root.descriptor)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.unlink(probe_name, dir_fd=root.descriptor)
            os.fsync(root.descriptor)
    except (ArtifactStorageError, OSError) as error:
        raise InstallationProfileStorageError(
            "Managed JobOS Profile root is not writable"
        ) from error
    _assert_safe_managed_root(installation_root, profile_id)
    return paths


def effective_profile_runtime(
    base_runtime: object,
    profile: InstallationProfileRecord,
    installation_root: Path,
) -> dict[str, object]:
    if hasattr(base_runtime, "to_mapping"):
        values = dict(base_runtime.to_mapping())
    elif isinstance(base_runtime, BaseModel):
        values = base_runtime.model_dump()
    elif isinstance(base_runtime, dict):
        values = dict(base_runtime)
    else:
        values = dict(vars(base_runtime))
    if profile.storage_mode == "anchored":
        assert profile.anchored_runtime is not None
        values.update(profile.anchored_runtime.model_dump())
    else:
        paths = managed_profile_paths(installation_root, profile.profile_id)
        values.update(
            {
                "job_provider": "sqlite",
                "artifact_provider": "local",
                "facade_source_path": None,
                "state_db_path": paths.state_db_path,
                "jobs_db_path": paths.jobs_db_path,
                "local_artifact_root": paths.local_artifact_root,
                "job_hunter_db_path": None,
                "artifact_roots": (),
            }
        )
    return values


_PROCESS_LOCKS: dict[Path, Lock] = {}
_PROCESS_LOCKS_GUARD = Lock()


class InstallationProfileRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(f"{path.stem}.lock")
        self.installation_root = path.parent

    @contextmanager
    def locked(self):
        _assert_no_symlink_ancestors(
            self.path.parent,
            "JobOS Profile registry parent is unavailable",
        )
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _assert_no_symlink_ancestors(
            self.path.parent,
            "JobOS Profile registry parent is unavailable",
        )
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(self.lock_path, Lock())
        with process_lock:
            flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(self.lock_path, flags, 0o600)
            except OSError as error:
                raise InstallationProfileRegistryError(
                    "JobOS Profile registry lock is unavailable"
                ) from error
            with os.fdopen(descriptor, "a+b") as handle:
                os.fchmod(handle.fileno(), 0o600)
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self) -> InstallationProfileRegistryData:
        try:
            metadata = self.path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or self.path.is_symlink():
                raise InstallationProfileRegistryError("JobOS Profile registry is invalid")
            if metadata.st_size > MAX_REGISTRY_BYTES:
                raise InstallationProfileRegistryError("JobOS Profile registry is too large")
            raw = self.path.read_bytes()
            if len(raw) > MAX_REGISTRY_BYTES:
                raise InstallationProfileRegistryError("JobOS Profile registry is too large")
            return InstallationProfileRegistryData.model_validate_json(raw)
        except InstallationProfileRegistryError:
            raise
        except FileNotFoundError as error:
            raise InstallationProfileRegistryError("JobOS Profile registry is missing") from error
        except (OSError, ValidationError, ValueError) as error:
            raise InstallationProfileRegistryError("JobOS Profile registry is invalid") from error

    def load_or_bootstrap(
        self,
        runtime: object,
        *,
        display_name: str = "Personal",
        now: datetime | None = None,
    ) -> InstallationProfileRegistryData:
        with self.locked():
            if self.path.exists():
                return self.load()
            timestamp = now or utc_now()
            profile_id = new_profile_id()
            data = InstallationProfileRegistryData(
                registry_revision=1,
                active_profile_id=profile_id,
                profiles=(
                    InstallationProfileRecord(
                        profile_id=profile_id,
                        display_name=normalized_display_name(display_name),
                        storage_mode="anchored",
                        created_at=timestamp,
                        updated_at=timestamp,
                        anchored_runtime=AnchoredRuntime.from_runtime(runtime),
                    ),
                ),
            )
            self._write_unlocked(data)
            return data

    def _write_unlocked(self, data: InstallationProfileRegistryData) -> None:
        payload = data.model_dump_json(indent=2).encode("utf-8") + b"\n"
        if len(payload) > MAX_REGISTRY_BYTES:
            raise InstallationProfileRegistryError("JobOS Profile registry is too large")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
            parent = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except OSError as error:
            raise InstallationProfileRegistryError(
                "JobOS Profile registry could not be saved"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

    def write(self, data: InstallationProfileRegistryData) -> None:
        with self.locked():
            self._write_unlocked(data)

    def list_public(self) -> JobOsProfileList:
        with self.locked():
            return public_profile_list(self.load())

    @staticmethod
    def _request_hash(payload: dict[str, object]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _key_hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _replay(
        self,
        data: InstallationProfileRegistryData,
        operation: Literal["create", "rename", "activate"],
        key: str,
        request_hash: str,
    ) -> JobOsProfileList | JobOsProfileSwitchAccepted | None:
        key_hash = self._key_hash(key)
        record = next(
            (
                item
                for item in data.idempotency_replays
                if item.operation == operation and item.key_hash == key_hash
            ),
            None,
        )
        if record is None:
            return None
        if record.request_hash != request_hash:
            raise InstallationProfileConflict("Idempotency key was already used")
        model = JobOsProfileSwitchAccepted if operation == "activate" else JobOsProfileList
        try:
            return model.model_validate_json(record.response_json)
        except ValidationError as error:
            raise InstallationProfileRegistryError(
                "JobOS Profile registry idempotency record is invalid"
            ) from error

    def _with_replay(
        self,
        data: InstallationProfileRegistryData,
        operation: Literal["create", "rename", "activate"],
        key: str,
        request_hash: str,
        response: JobOsProfileList | JobOsProfileSwitchAccepted,
        result_profile_id: str | None = None,
    ) -> InstallationProfileRegistryData:
        replay = PersistentIdempotencyReplay(
            operation=operation,
            key_hash=self._key_hash(key),
            request_hash=request_hash,
            response_json=response.model_dump_json(),
            result_profile_id=result_profile_id,
        )
        retained = tuple(
            item
            for item in data.idempotency_replays
            if (item.operation, item.key_hash) != (replay.operation, replay.key_hash)
        )[-(MAX_IDEMPOTENCY_REPLAYS - 1) :]
        return data.model_copy(update={"idempotency_replays": (*retained, replay)})

    def create(
        self,
        display_name: str,
        *,
        idempotency_key: str,
        now: datetime | None = None,
        profile_id: str | None = None,
    ) -> JobOsProfileList:
        result, _profile_id = self.create_with_identity(
            display_name,
            idempotency_key=idempotency_key,
            now=now,
            profile_id=profile_id,
        )
        return result

    def create_with_identity(
        self,
        display_name: str,
        *,
        idempotency_key: str,
        now: datetime | None = None,
        profile_id: str | None = None,
    ) -> tuple[JobOsProfileList, str]:
        name = normalized_display_name(display_name)
        request_hash = self._request_hash({"display_name": name})
        with self.locked():
            data = self.load()
            replay = self._replay(data, "create", idempotency_key, request_hash)
            if replay is not None:
                assert isinstance(replay, JobOsProfileList)
                key_hash = self._key_hash(idempotency_key)
                record = next(
                    item
                    for item in data.idempotency_replays
                    if item.operation == "create" and item.key_hash == key_hash
                )
                if (
                    record.result_profile_id is None
                    or not any(
                        item.profile_id == record.result_profile_id
                        for item in replay.profiles
                    )
                ):
                    raise InstallationProfileRegistryError(
                        "JobOS Profile registry create replay is invalid"
                    )
                return replay, record.result_profile_id
            if len(data.profiles) >= MAX_PROFILES:
                raise InstallationProfileLimitReached("This installation already has 32 profiles")
            if display_name_key(name) in {
                display_name_key(item.display_name) for item in data.profiles
            }:
                raise InstallationProfileConflict("A JobOS Profile with this name already exists")
            identifier = profile_id or new_profile_id()
            validate_profile_id(identifier)
            if identifier in {item.profile_id for item in data.profiles}:
                raise InstallationProfileConflict("JobOS Profile identifier already exists")
            ensure_managed_profile_storage(self.installation_root, identifier)
            timestamp = now or utc_now()
            profile = InstallationProfileRecord(
                profile_id=identifier,
                display_name=name,
                storage_mode="managed",
                created_at=timestamp,
                updated_at=timestamp,
            )
            updated = data.model_copy(
                update={
                    "registry_revision": data.registry_revision + 1,
                    "profiles": (*data.profiles, profile),
                }
            )
            result = public_profile_list(updated)
            updated = self._with_replay(
                updated,
                "create",
                idempotency_key,
                request_hash,
                result,
                result_profile_id=identifier,
            )
            self._write_unlocked(updated)
        return result, identifier

    def rename(
        self,
        profile_id: str,
        display_name: str,
        *,
        expected_registry_revision: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> JobOsProfileList:
        validate_profile_id(profile_id)
        name = normalized_display_name(display_name)
        request_hash = self._request_hash(
            {
                "profile_id": profile_id,
                "display_name": name,
                "expected_registry_revision": expected_registry_revision,
            }
        )
        with self.locked():
            data = self.load()
            replay = self._replay(data, "rename", idempotency_key, request_hash)
            if replay is not None:
                assert isinstance(replay, JobOsProfileList)
                return replay
            if data.registry_revision != expected_registry_revision:
                raise InstallationProfileConflict("JobOS Profile registry changed")
            target = next((item for item in data.profiles if item.profile_id == profile_id), None)
            if target is None:
                raise InstallationProfileNotFound("JobOS Profile was not found")
            if display_name_key(name) in {
                display_name_key(item.display_name)
                for item in data.profiles
                if item.profile_id != profile_id
            }:
                raise InstallationProfileConflict("A JobOS Profile with this name already exists")
            timestamp = now or utc_now()
            updated_profiles = tuple(
                item.model_copy(update={"display_name": name, "updated_at": timestamp})
                if item.profile_id == profile_id
                else item
                for item in data.profiles
            )
            updated = data.model_copy(
                update={
                    "registry_revision": data.registry_revision + 1,
                    "profiles": updated_profiles,
                }
            )
            result = public_profile_list(updated)
            updated = self._with_replay(
                updated, "rename", idempotency_key, request_hash, result
            )
            self._write_unlocked(updated)
        return result

    def active_profile(self) -> tuple[InstallationProfileRegistryData, InstallationProfileRecord]:
        with self.locked():
            data = self.load()
            return data, next(
                profile for profile in data.profiles if profile.profile_id == data.active_profile_id
            )

    def activate(
        self,
        profile_id: str,
        *,
        expected_registry_revision: int,
        idempotency_key: str,
        driver: Literal["launchd", "desktop"],
        now: datetime | None = None,
        switch_id: str | None = None,
    ) -> JobOsProfileSwitchAccepted:
        validate_profile_id(profile_id)
        request_hash = self._request_hash(
            {
                "profile_id": profile_id,
                "expected_registry_revision": expected_registry_revision,
            }
        )
        with self.locked():
            data = self.load()
            replay = self._replay(data, "activate", idempotency_key, request_hash)
            if replay is not None:
                assert isinstance(replay, JobOsProfileSwitchAccepted)
                return replay
            if data.registry_revision != expected_registry_revision:
                raise InstallationProfileConflict("JobOS Profile registry changed")
            target = next((item for item in data.profiles if item.profile_id == profile_id), None)
            if target is None:
                raise InstallationProfileNotFound("JobOS Profile was not found")
            if data.pending_switch is not None:
                raise InstallationProfileConflict("Another JobOS Profile switch is in progress")
            identifier = switch_id or new_switch_id()
            timestamp = now or utc_now()
            accepted = JobOsProfileSwitchAccepted(
                switch_id=identifier,
                from_profile_id=data.active_profile_id,
                to_profile_id=profile_id,
            )
            if profile_id == data.active_profile_id:
                completed = CompletedProfileSwitch(
                    switch_id=identifier,
                    from_profile_id=profile_id,
                    target_profile_id=profile_id,
                    status="succeeded",
                    active_profile_id=profile_id,
                    completed_at=timestamp,
                )
                updated = data.model_copy(
                    update={
                        "registry_revision": data.registry_revision + 1,
                        "last_switch": completed,
                    }
                )
            elif driver == "desktop":
                if target.storage_mode == "managed":
                    ensure_managed_profile_storage(self.installation_root, profile_id)
                completed = CompletedProfileSwitch(
                    switch_id=identifier,
                    from_profile_id=data.active_profile_id,
                    target_profile_id=profile_id,
                    status="succeeded",
                    active_profile_id=profile_id,
                    completed_at=timestamp,
                )
                updated = data.model_copy(
                    update={
                        "registry_revision": data.registry_revision + 1,
                        "active_profile_id": profile_id,
                        "pending_switch": None,
                        "last_switch": completed,
                    }
                )
            else:
                if target.storage_mode == "managed":
                    ensure_managed_profile_storage(self.installation_root, profile_id)
                pending = PendingProfileSwitch(
                    switch_id=identifier,
                    from_profile_id=data.active_profile_id,
                    target_profile_id=profile_id,
                    status="pending",
                    created_at=timestamp,
                )
                updated = data.model_copy(
                    update={
                        "registry_revision": data.registry_revision + 1,
                        "pending_switch": pending,
                    }
                )
            updated = self._with_replay(
                updated, "activate", idempotency_key, request_hash, accepted
            )
            self._write_unlocked(updated)
        return accepted

    def switch_status(self, switch_id: str) -> JobOsProfileSwitchStatus:
        if not SWITCH_ID_PATTERN.fullmatch(switch_id):
            raise InstallationProfileNotFound("JobOS Profile switch was not found")
        with self.locked():
            data = self.load()
            if data.pending_switch is not None and data.pending_switch.switch_id == switch_id:
                pending = data.pending_switch
                return JobOsProfileSwitchStatus(
                    switch_id=switch_id,
                    target_profile_id=pending.target_profile_id,
                    status=pending.status,
                    active_profile_id=data.active_profile_id,
                    error_code=None,
                )
            if data.last_switch is not None and data.last_switch.switch_id == switch_id:
                completed = data.last_switch
                return JobOsProfileSwitchStatus(
                    switch_id=switch_id,
                    target_profile_id=completed.target_profile_id,
                    status=completed.status,
                    active_profile_id=completed.active_profile_id,
                    error_code=completed.error_code,
                )
        raise InstallationProfileNotFound("JobOS Profile switch was not found")

    def claim_switch(self, switch_id: str, target_profile_id: str) -> PendingProfileSwitch:
        validate_profile_id(target_profile_id)
        with self.locked():
            data = self.load()
            pending = data.pending_switch
            if (
                pending is None
                or pending.switch_id != switch_id
                or pending.target_profile_id != target_profile_id
                or pending.status != "pending"
                or data.active_profile_id != pending.from_profile_id
            ):
                raise InstallationProfileConflict("JobOS Profile switch is no longer pending")
            claimed = pending.model_copy(update={"status": "activating"})
            updated = data.model_copy(
                update={
                    "registry_revision": data.registry_revision + 1,
                    "active_profile_id": target_profile_id,
                    "pending_switch": claimed,
                }
            )
            self._write_unlocked(updated)
            return claimed

    def complete_switch(self, switch_id: str, target_profile_id: str) -> None:
        with self.locked():
            data = self.load()
            pending = data.pending_switch
            if (
                pending is None
                or pending.switch_id != switch_id
                or pending.target_profile_id != target_profile_id
                or pending.status != "activating"
                or data.active_profile_id != target_profile_id
            ):
                raise InstallationProfileConflict("JobOS Profile switch claim changed")
            completed = CompletedProfileSwitch(
                switch_id=switch_id,
                from_profile_id=pending.from_profile_id,
                target_profile_id=target_profile_id,
                status="succeeded",
                active_profile_id=target_profile_id,
                completed_at=utc_now(),
            )
            self._write_unlocked(
                data.model_copy(
                    update={
                        "registry_revision": data.registry_revision + 1,
                        "pending_switch": None,
                        "last_switch": completed,
                    }
                )
            )

    def rollback_switch(self, switch_id: str, error_code: str) -> str:
        with self.locked():
            data = self.load()
            pending = data.pending_switch
            if pending is None or pending.switch_id != switch_id:
                raise InstallationProfileConflict("JobOS Profile switch claim changed")
            completed = CompletedProfileSwitch(
                switch_id=switch_id,
                from_profile_id=pending.from_profile_id,
                target_profile_id=pending.target_profile_id,
                status="rolled_back",
                active_profile_id=pending.from_profile_id,
                completed_at=utc_now(),
                error_code=error_code,
            )
            self._write_unlocked(
                data.model_copy(
                    update={
                        "registry_revision": data.registry_revision + 1,
                        "active_profile_id": pending.from_profile_id,
                        "pending_switch": None,
                        "last_switch": completed,
                    }
                )
            )
            return pending.from_profile_id

    def rollback_completed_source_switch(self, switch_id: str, error_code: str) -> str:
        """Roll back a desktop-driven switch whose target API failed to start."""
        with self.locked():
            data = self.load()
            completed = data.last_switch
            if (
                data.pending_switch is not None
                or completed is None
                or completed.switch_id != switch_id
                or completed.status != "succeeded"
                or completed.from_profile_id == completed.target_profile_id
                or data.active_profile_id != completed.target_profile_id
            ):
                raise InstallationProfileConflict("JobOS Profile source switch changed")
            rolled_back = CompletedProfileSwitch(
                switch_id=switch_id,
                from_profile_id=completed.from_profile_id,
                target_profile_id=completed.target_profile_id,
                status="rolled_back",
                active_profile_id=completed.from_profile_id,
                completed_at=utc_now(),
                error_code=error_code,
            )
            self._write_unlocked(
                data.model_copy(
                    update={
                        "registry_revision": data.registry_revision + 1,
                        "active_profile_id": completed.from_profile_id,
                        "last_switch": rolled_back,
                    }
                )
            )
            return completed.from_profile_id

    def fail_pending_switch(self, switch_id: str, error_code: str) -> None:
        """Record a helper startup failure only while the exact switch is unclaimed."""
        with self.locked():
            data = self.load()
            pending = data.pending_switch
            if (
                pending is None
                or pending.switch_id != switch_id
                or pending.status != "pending"
                or data.active_profile_id != pending.from_profile_id
            ):
                raise InstallationProfileConflict("JobOS Profile switch is no longer pending")
            completed = CompletedProfileSwitch(
                switch_id=switch_id,
                from_profile_id=pending.from_profile_id,
                target_profile_id=pending.target_profile_id,
                status="rolled_back",
                active_profile_id=pending.from_profile_id,
                completed_at=utc_now(),
                error_code=error_code,
            )
            self._write_unlocked(
                data.model_copy(
                    update={
                        "registry_revision": data.registry_revision + 1,
                        "pending_switch": None,
                        "last_switch": completed,
                    }
                )
            )

    def replace_last_switch_error(self, switch_id: str, error_code: str) -> None:
        with self.locked():
            data = self.load()
            last = data.last_switch
            if last is None or last.switch_id != switch_id or last.status != "rolled_back":
                raise InstallationProfileConflict("JobOS Profile switch result changed")
            self._write_unlocked(
                data.model_copy(
                    update={
                        "registry_revision": data.registry_revision + 1,
                        "last_switch": last.model_copy(update={"error_code": error_code}),
                    }
                )
            )

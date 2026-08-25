from __future__ import annotations

import asyncio
from typing import Literal, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .installation_profiles import (
    ConnectedAgentCardinalityConflict,
    ConnectedAgentRecord,
    InstallationProfileNotFound,
    InstallationProfileRegistry,
    managed_profile_paths,
    new_connected_agent_id,
    utc_now,
)
from .state_store import JobOsStateStore


class ConnectedAgentRuntimeControl(Protocol):
    async def inspect_connection(self, agent: ConnectedAgentRecord) -> dict[str, object]: ...

    async def list_models(self, agent: ConnectedAgentRecord) -> dict[str, object]: ...

    async def disconnect(self, agent: ConnectedAgentRecord) -> dict[str, object]: ...


class UnavailableConnectedAgentRuntime:
    async def inspect_connection(self, agent: ConnectedAgentRecord) -> dict[str, object]:
        del agent
        return {
            "state": "unavailable",
            "label": "Runtime unavailable",
            "provider_available": False,
            "tools_available": False,
            "retry_after_seconds": None,
        }

    async def list_models(self, agent: ConnectedAgentRecord) -> dict[str, object]:
        del agent
        return {"live": False, "models": []}

    async def disconnect(self, agent: ConnectedAgentRecord) -> dict[str, object]:
        del agent
        return {"verified": False}


class ConnectedAgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectedAgentHealth(ConnectedAgentModel):
    state: str
    label: str
    provider_available: bool
    tools_available: bool
    retry_after_seconds: int | None = None


class ConnectedAgentPublic(ConnectedAgentModel):
    id: str
    provider: Literal["hermes", "codex"]
    display_name: str
    avatar_id: str
    default_model_id: str | None
    default_reasoning_effort: str | None
    lifecycle: Literal["connected", "disconnected"]
    account_summary: dict[str, str] | None
    account_fingerprint: str | None
    created_at: str
    updated_at: str
    disconnected_at: str | None
    health: ConnectedAgentHealth
    impact: dict[str, object]


class ConnectedAgentListResponse(ConnectedAgentModel):
    registry_revision: int
    agents: list[ConnectedAgentPublic]


class ConnectedAgentModelOption(ConnectedAgentModel):
    model_id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    reasoning_efforts: list[str]


class ConnectedAgentModelsResponse(ConnectedAgentModel):
    live: bool
    models: list[ConnectedAgentModelOption]


class CreateConnectedAgentRequest(ConnectedAgentModel):
    provider: Literal["hermes", "codex"]
    display_name: str = Field(min_length=1, max_length=120)
    avatar_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    default_model_id: str | None = Field(default=None, min_length=1, max_length=256)
    default_reasoning_effort: str | None = Field(default=None, min_length=1, max_length=64)
    connection_config: dict[str, str] | None = None
    expected_registry_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def validate_defaults(self) -> CreateConnectedAgentRequest:
        if (self.default_model_id is None) != (self.default_reasoning_effort is None):
            raise ValueError("Connected Agent defaults require model and reasoning effort")
        return self


class UpdateConnectedAgentRequest(ConnectedAgentModel):
    display_name: str = Field(min_length=1, max_length=120)
    avatar_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    default_model_id: str | None = Field(default=None, min_length=1, max_length=256)
    default_reasoning_effort: str | None = Field(default=None, min_length=1, max_length=64)
    connection_config: dict[str, str] | None = None
    expected_registry_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def validate_defaults(self) -> UpdateConnectedAgentRequest:
        if (self.default_model_id is None) != (self.default_reasoning_effort is None):
            raise ValueError("Connected Agent defaults require model and reasoning effort")
        return self


class DisconnectConnectedAgentRequest(ConnectedAgentModel):
    confirmation_token: str = Field(min_length=1, max_length=128)
    expected_registry_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=200)


class SetDefaultConnectedAgentRequest(ConnectedAgentModel):
    connected_agent_id: str | None = Field(default=None, pattern=r"^jagent_[a-f0-9]{32}$")
    expected_profile_revision: int = Field(ge=1)
    expected_agent_registry_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=200)


class SetDefaultConnectedAgentResponse(ConnectedAgentModel):
    profile_id: str
    default_connected_agent_id: str | None
    registry_revision: int


class ConnectedAgentImpactResponse(ConnectedAgentModel):
    agent_id: str
    default_profile_ids: list[str]
    active_chats: int
    locked_chats: int


class ResolvedAgent(TypedDict):
    record: ConnectedAgentRecord
    model_id: str
    reasoning_effort: str


class ConnectedAgentConflict(RuntimeError):
    pass


class ConnectedAgentService:
    def __init__(
        self,
        registry: InstallationProfileRegistry,
        runtime: ConnectedAgentRuntimeControl,
        *,
        profile_id: str,
        state_store: JobOsStateStore,
    ) -> None:
        self.registry = registry
        self.runtime = runtime
        self.profile_id = profile_id
        self.state_store = state_store
        self._mutation_lock = asyncio.Lock()

    def _record(self, agent_id: str) -> ConnectedAgentRecord:
        record = next(
            (item for item in self.registry.load().connected_agents if item.id == agent_id), None
        )
        if record is None:
            raise InstallationProfileNotFound("Connected Agent was not found")
        return record

    async def public(self, record: ConnectedAgentRecord) -> ConnectedAgentPublic:
        health = (
            {
                "state": "disconnected",
                "label": "Disconnected",
                "provider_available": False,
                "tools_available": False,
                "retry_after_seconds": None,
            }
            if record.lifecycle == "disconnected"
            else await self.runtime.inspect_connection(record)
        )
        return ConnectedAgentPublic(
            id=record.id,
            provider=record.provider,
            display_name=record.display_name,
            avatar_id=record.avatar_id,
            default_model_id=record.default_model_id,
            default_reasoning_effort=record.default_reasoning_effort,
            lifecycle=record.lifecycle,
            account_summary=record.account_summary,
            account_fingerprint=record.account_fingerprint,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
            disconnected_at=record.disconnected_at.isoformat() if record.disconnected_at else None,
            health=ConnectedAgentHealth.model_validate(health),
            impact=self.impact(record.id).model_dump(),
        )

    async def list(self) -> ConnectedAgentListResponse:
        data = self.registry.load()
        return ConnectedAgentListResponse(
            registry_revision=data.registry_revision,
            agents=[await self.public(record) for record in data.connected_agents],
        )

    async def get(self, agent_id: str) -> ConnectedAgentPublic:
        return await self.public(self._record(agent_id))

    async def models(self, agent_id: str) -> ConnectedAgentModelsResponse:
        record = self._record(agent_id)
        if record.lifecycle == "disconnected":
            return ConnectedAgentModelsResponse(live=False, models=[])
        return ConnectedAgentModelsResponse.model_validate(await self.runtime.list_models(record))

    async def _validate_defaults(
        self,
        record: ConnectedAgentRecord,
        model_id: str | None,
        reasoning_effort: str | None,
    ) -> None:
        if model_id is None or reasoning_effort is None:
            return
        catalog = ConnectedAgentModelsResponse.model_validate(
            await self.runtime.list_models(record)
        )
        option = next((item for item in catalog.models if item.model_id == model_id), None)
        if not catalog.live or option is None or reasoning_effort not in option.reasoning_efforts:
            raise ConnectedAgentConflict("MODEL_UNAVAILABLE")

    async def create(self, command: CreateConnectedAgentRequest) -> ConnectedAgentPublic:
        async with self._mutation_lock:
            data = self.registry.load()
            if data.registry_revision == command.expected_registry_revision:
                timestamp = utc_now()
                candidate = ConnectedAgentRecord.model_validate(
                    {
                        "id": new_connected_agent_id(),
                        "provider": command.provider,
                        "display_name": command.display_name,
                        "avatar_id": command.avatar_id,
                        "default_model_id": command.default_model_id,
                        "default_reasoning_effort": command.default_reasoning_effort,
                        "lifecycle": "connected",
                        "connection_config": command.connection_config,
                        "credential_reference": None,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                )
                await self._validate_defaults(
                    candidate,
                    command.default_model_id,
                    command.default_reasoning_effort,
                )
            try:
                record = self.registry.create_connected_agent(
                    provider=command.provider,
                    display_name=command.display_name,
                    avatar_id=command.avatar_id,
                    default_model_id=command.default_model_id,
                    default_reasoning_effort=command.default_reasoning_effort,
                    connection_config=command.connection_config,
                    credential_reference=None,
                    expected_registry_revision=command.expected_registry_revision,
                    idempotency_key=command.idempotency_key,
                )
            except ConnectedAgentCardinalityConflict as error:
                raise ConnectedAgentConflict("AGENT_CARDINALITY_CONFLICT") from error
        return await self.public(self._record(record.id))

    async def update(
        self, agent_id: str, command: UpdateConnectedAgentRequest
    ) -> ConnectedAgentPublic:
        async with self._mutation_lock:
            data = self.registry.load()
            if data.registry_revision == command.expected_registry_revision:
                current = self._record(agent_id)
                candidate = ConnectedAgentRecord.model_validate(
                    {
                        **current.model_dump(mode="python"),
                        "display_name": command.display_name,
                        "avatar_id": command.avatar_id,
                        "default_model_id": command.default_model_id,
                        "default_reasoning_effort": command.default_reasoning_effort,
                        "connection_config": command.connection_config
                        if command.connection_config is not None
                        else current.connection_config,
                        "updated_at": utc_now(),
                    }
                )
                await self._validate_defaults(
                    candidate,
                    command.default_model_id,
                    command.default_reasoning_effort,
                )
            record = self.registry.update_connected_agent(
                agent_id,
                display_name=command.display_name,
                avatar_id=command.avatar_id,
                default_model_id=command.default_model_id,
                default_reasoning_effort=command.default_reasoning_effort,
                connection_config=command.connection_config,
                expected_registry_revision=command.expected_registry_revision,
                idempotency_key=command.idempotency_key,
            )
        return await self.public(self._record(record.id))

    def impact(self, agent_id: str) -> ConnectedAgentImpactResponse:
        data = self.registry.load()
        if not any(item.id == agent_id for item in data.connected_agents):
            raise InstallationProfileNotFound("Connected Agent was not found")
        totals = {"active_chats": 0, "locked_chats": 0}
        for profile in data.profiles:
            path = (
                profile.anchored_runtime.state_db_path
                if profile.anchored_runtime is not None
                else managed_profile_paths(
                    self.registry.installation_root, profile.profile_id
                ).state_db_path
            )
            profile_impact = JobOsStateStore.connected_agent_chat_impact_at(path, agent_id)
            totals["active_chats"] += profile_impact["active_chats"]
            totals["locked_chats"] += profile_impact["locked_chats"]
        return ConnectedAgentImpactResponse(
            agent_id=agent_id,
            default_profile_ids=[
                profile.profile_id
                for profile in data.profiles
                if profile.default_connected_agent_id == agent_id
            ],
            **totals,
        )

    async def disconnect(
        self, agent_id: str, command: DisconnectConnectedAgentRequest
    ) -> ConnectedAgentPublic:
        if command.confirmation_token != agent_id:
            raise ConnectedAgentConflict("DISCONNECT_CONFIRMATION_REQUIRED")
        async with self._mutation_lock:
            record = self._record(agent_id)
            if record.lifecycle == "disconnected":
                self.registry.disconnect_connected_agent(
                    agent_id,
                    expected_registry_revision=command.expected_registry_revision,
                    idempotency_key=command.idempotency_key,
                )
            else:
                if self.registry.load().registry_revision != command.expected_registry_revision:
                    raise ConnectedAgentConflict("PROFILE_REVISION_CONFLICT")
                result = await self.runtime.disconnect(record)
                if result.get("verified") is not True:
                    raise ConnectedAgentConflict("AUTH_CLEANUP_REQUIRED")
                self.registry.disconnect_connected_agent(
                    agent_id,
                    expected_registry_revision=command.expected_registry_revision,
                    idempotency_key=command.idempotency_key,
                )
        return await self.public(self._record(agent_id))

    async def set_default(
        self, profile_id: str, command: SetDefaultConnectedAgentRequest
    ) -> SetDefaultConnectedAgentResponse:
        if command.expected_profile_revision != command.expected_agent_registry_revision:
            raise ConnectedAgentConflict("PROFILE_REVISION_CONFLICT")
        async with self._mutation_lock:
            profile = self.registry.set_profile_default_connected_agent(
                profile_id,
                command.connected_agent_id,
                expected_registry_revision=command.expected_agent_registry_revision,
                idempotency_key=command.idempotency_key,
            )
            registry_revision = self.registry.load().registry_revision
        return SetDefaultConnectedAgentResponse(
            profile_id=profile.profile_id,
            default_connected_agent_id=profile.default_connected_agent_id,
            registry_revision=registry_revision,
        )

    def presentation(self, agent_id: str) -> dict[str, object]:
        record = self._record(agent_id)
        return {
            "id": record.id,
            "provider": record.provider,
            "display_name": record.display_name,
            "avatar_id": record.avatar_id,
        }

    async def resolve_for_chat(
        self,
        *,
        connected_agent_id: str | None,
        model_id: str | None,
        reasoning_effort: str | None,
        expected_profile_revision: int,
        expected_agent_registry_revision: int,
    ) -> ResolvedAgent:
        data = self.registry.load()
        if (
            data.active_profile_id != self.profile_id
            or data.registry_revision != expected_profile_revision
            or data.registry_revision != expected_agent_registry_revision
        ):
            raise ConnectedAgentConflict("PROFILE_REVISION_CONFLICT")
        profile = next(item for item in data.profiles if item.profile_id == self.profile_id)
        selected_id = connected_agent_id or profile.default_connected_agent_id
        if selected_id is None:
            raise ConnectedAgentConflict("AGENT_NOT_CONFIGURED")
        record = next((item for item in data.connected_agents if item.id == selected_id), None)
        if record is None:
            raise ConnectedAgentConflict("AGENT_NOT_CONFIGURED")
        if record.lifecycle != "connected":
            raise ConnectedAgentConflict("AGENT_DISCONNECTED")
        health = ConnectedAgentHealth.model_validate(await self.runtime.inspect_connection(record))
        if not health.provider_available:
            raise ConnectedAgentConflict("AGENT_PROVIDER_UNAVAILABLE")
        if not health.tools_available:
            raise ConnectedAgentConflict("AGENT_TOOLS_UNAVAILABLE")
        selected_model = model_id or record.default_model_id
        selected_effort = reasoning_effort or record.default_reasoning_effort
        if selected_model is None or selected_effort is None:
            raise ConnectedAgentConflict("MODEL_SELECTION_REQUIRED")
        catalog = ConnectedAgentModelsResponse.model_validate(
            await self.runtime.list_models(record)
        )
        option = next((item for item in catalog.models if item.model_id == selected_model), None)
        if not catalog.live or option is None or selected_effort not in option.reasoning_efforts:
            raise ConnectedAgentConflict("MODEL_UNAVAILABLE")
        return {"record": record, "model_id": selected_model, "reasoning_effort": selected_effort}

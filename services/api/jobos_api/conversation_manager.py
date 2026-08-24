import asyncio
from typing import Literal

from pydantic import Field

from .agent_gateway import (
    AgentGatewayFactory,
    AgentRuntimeRouter,
    AmbiguousDeliveryError,
    DefinitiveSessionCreationError,
)
from .career_profile_context import CareerProfileContextStore
from .conversations import (
    BoundConversationResponse,
    ConnectionResponse,
    ConversationJobContext,
    ConversationModel,
    ConversationResponse,
    ConversationService,
)
from .state_store import (
    ConversationNotFound,
    ConversationProvisioningFailed,
    JobOsStateStore,
)


class ConversationSummary(ConversationModel):
    conversation_id: str
    title: str
    position: int
    active_turn: dict[str, object] | None
    connection: ConnectionResponse
    recovery_state: Literal["ready", "recovering", "quarantined"]
    latest_event_id: int
    created_at: str
    job_context: ConversationJobContext


class ConversationListResponse(ConversationModel):
    conversations: list[ConversationSummary] = Field(max_length=5)


class ConversationManager:
    def __init__(
        self,
        store: JobOsStateStore,
        gateway_factory: AgentGatewayFactory | AgentRuntimeRouter,
        *,
        profile_id: str | None = None,
        career_profile_principal: str | None = None,
        career_profile_context: CareerProfileContextStore | None = None,
        career_profile_agent_id: str | None = None,
    ) -> None:
        self.store = store
        self.gateway_factory = gateway_factory
        self.profile_id = profile_id
        self.career_profile_principal = career_profile_principal
        self.career_profile_context = career_profile_context
        self.career_profile_agent_id = career_profile_agent_id
        self._services: dict[str, ConversationService] = {}
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            pending: list[tuple[dict[str, object], ConversationService]] = []
            for summary in self.store.list_active_conversations():
                conversation_id = str(summary["conversation_id"])
                try:
                    service = self._make_service(conversation_id)
                except Exception:
                    continue
                self._services[conversation_id] = service
                pending.append((summary, service))
        if pending:
            results = await asyncio.gather(
                *(service.start() for _, service in pending), return_exceptions=True
            )
            async with self._lifecycle_lock:
                for (summary, service), result in zip(pending, results, strict=True):
                    if not isinstance(result, BaseException):
                        continue
                    conversation_id = str(summary["conversation_id"])
                    self._services.pop(conversation_id, None)
                    await asyncio.gather(service.close(), return_exceptions=True)

    async def close(self) -> None:
        async with self._lifecycle_lock:
            services = list(self._services.values())
            self._services.clear()
        if services:
            results = await asyncio.gather(
                *(service.close() for service in services), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    def _make_service(self, conversation_id: str) -> ConversationService:
        scoped_store = self.store.conversation_store(conversation_id)
        binding = scoped_store.binding()
        binding["_normalized_event_sequences"] = scoped_store.normalized_event_sequences()
        gateway = (
            self.gateway_factory.create(conversation_id, binding)
            if isinstance(self.gateway_factory, AgentRuntimeRouter)
            else self.gateway_factory.create(conversation_id)
        )
        return ConversationService(
            scoped_store,
            gateway,
            conversation_id,
            profile_id=self.profile_id,
            career_profile_principal=self.career_profile_principal,
            career_profile_context=self.career_profile_context,
            career_profile_agent_id=self.career_profile_agent_id,
        )

    def list(self, *, owner_device_id: str) -> ConversationListResponse:
        conversations: list[ConversationSummary] = []
        for value in self.store.list_active_conversations(owner_device_id=owner_device_id):
            conversation_id = str(value["conversation_id"])
            service = self._services.get(conversation_id)
            if service is None:
                scoped = self.store.conversation_store(conversation_id)
                durable = scoped.conversation_snapshot()
                recovery_turn_id = scoped.recovery_turn_id()
                active_turn = durable.get("active_turn")
                conversations.append(
                    ConversationSummary(
                        conversation_id=conversation_id,
                        title=str(durable["title"]),
                        position=int(durable["position"]),
                        active_turn=active_turn if isinstance(active_turn, dict) else None,
                        connection=ConnectionResponse(state="offline"),
                        recovery_state=(
                            "recovering"
                            if recovery_turn_id and isinstance(active_turn, dict)
                            else "quarantined"
                            if recovery_turn_id
                            else "ready"
                        ),
                        latest_event_id=int(durable["latest_event_id"]),
                        created_at=str(durable["created_at"]),
                        job_context=ConversationJobContext.model_validate(durable["job_context"]),
                    )
                )
                continue
            snapshot = service.snapshot()
            conversations.append(
                ConversationSummary(
                    conversation_id=snapshot.conversation_id,
                    title=snapshot.title,
                    position=snapshot.position,
                    active_turn=snapshot.active_turn,
                    connection=snapshot.connection,
                    recovery_state=snapshot.recovery_state,
                    latest_event_id=snapshot.latest_event_id,
                    created_at=snapshot.created_at,
                    job_context=snapshot.job_context,
                )
            )
        return ConversationListResponse(conversations=conversations)

    async def create(
        self, *, actor_id: str, selected_job_id: str | None = None
    ) -> ConversationResponse:
        async with self._lifecycle_lock:
            summary = self.store.create_conversation(
                actor_id=actor_id, selected_job_id=selected_job_id
            )
            conversation_id = str(summary["conversation_id"])
            service: ConversationService | None = None
            try:
                service = self._make_service(conversation_id)
                self._services[conversation_id] = service
                await service.start()
            except BaseException:
                self._services.pop(conversation_id, None)
                if service is not None:
                    await asyncio.gather(service.close(), return_exceptions=True)
                self.store.discard_failed_conversation(conversation_id, actor_id=actor_id)
                raise
        return service.snapshot()

    async def create_bound(
        self,
        *,
        actor_id: str,
        selected_job_id: str | None,
        connected_agent_id: str,
        provider: str,
        model_id: str,
        reasoning_effort: str,
        connection_account_fingerprint: str | None,
        idempotency_key: str,
        client_request_hash: str,
        agent_summary: dict[str, object],
    ) -> tuple[BoundConversationResponse, bool]:
        async with self._lifecycle_lock:
            summary = self.store.create_conversation(
                actor_id=actor_id,
                selected_job_id=selected_job_id,
                connected_agent_id=connected_agent_id,
                provider=provider,
                model_id=model_id,
                reasoning_effort=reasoning_effort,
                connection_account_fingerprint=connection_account_fingerprint,
                idempotency_key=idempotency_key,
                client_request_hash=client_request_hash,
            )
            conversation_id = str(summary["conversation_id"])
            created = summary["created"] is True
            service = self._services.get(conversation_id)
            if not created:
                if service is None:
                    service = self._make_service(conversation_id)
                    self._services[conversation_id] = service
                    await service.start()
                return self._bound_snapshot(service, agent_summary), False
            try:
                service = self._make_service(conversation_id)
                self._services[conversation_id] = service
                await service.start()
                provider_session_id, _ = await service.gateway.create_or_resume_conversation(None)
                service.store.complete_provisioning(provider_session_id)
            except AmbiguousDeliveryError:
                if service is None:
                    raise
                service.store.lock_provisioning()
            except DefinitiveSessionCreationError as error:
                self._services.pop(conversation_id, None)
                if service is not None:
                    await asyncio.gather(service.close(), return_exceptions=True)
                self.store.discard_provisioning_conversation(
                    conversation_id,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                    failure_code="AGENT_PROVIDER_UNAVAILABLE",
                )
                raise ConversationProvisioningFailed("AGENT_PROVIDER_UNAVAILABLE") from error
            except asyncio.CancelledError:
                if service is not None:
                    service.store.lock_provisioning()
                raise
            except BaseException:
                if service is None:
                    raise
                service.store.lock_provisioning()
        return self._bound_snapshot(service, agent_summary), True

    async def replay_bound(
        self,
        *,
        conversation_id: str,
        actor_id: str,
        agent_summary: dict[str, object],
    ) -> BoundConversationResponse:
        async with self._lifecycle_lock:
            if not any(
                item["conversation_id"] == conversation_id
                for item in self.store.list_active_conversations(owner_device_id=actor_id)
            ):
                raise ConversationNotFound("Conversation not found")
            service = self._services.get(conversation_id)
            if service is None:
                service = self._make_service(conversation_id)
                self._services[conversation_id] = service
                await service.start()
        return self._bound_snapshot(service, agent_summary)

    @staticmethod
    def _bound_snapshot(
        service: ConversationService, agent_summary: dict[str, object]
    ) -> BoundConversationResponse:
        binding = service.store.binding()
        return BoundConversationResponse.model_validate(
            {
                **service.snapshot().model_dump(),
                "binding": {
                    key: binding[key]
                    for key in (
                        "connected_agent_id",
                        "provider",
                        "model_id",
                        "reasoning_effort",
                        "binding_state",
                    )
                },
                "availability": {
                    "state": "ready" if binding["creation_state"] == "ready" else "locked",
                    "reason": binding["lock_reason"],
                },
                "agent": agent_summary,
            }
        )

    async def archive(self, conversation_id: str, *, actor_id: str) -> None:
        async with self._lifecycle_lock:
            service = self.get(conversation_id, owner_device_id=actor_id)
            self.store.archive_conversation(conversation_id, actor_id=actor_id)
            self._services.pop(conversation_id, None)
            await service.close()

    def get(
        self, conversation_id: str, *, owner_device_id: str | None = None
    ) -> ConversationService:
        service = self._services.get(conversation_id)
        if service is None or (
            owner_device_id is not None
            and not any(
                value["conversation_id"] == conversation_id
                for value in self.store.list_active_conversations(owner_device_id=owner_device_id)
            )
        ):
            raise ConversationNotFound("Conversation not found")
        return service

    @property
    def services(self) -> tuple[ConversationService, ...]:
        return tuple(self._services.values())

import asyncio
from typing import Literal

from pydantic import Field

from .agent_gateway import AgentGatewayFactory, AgentRuntimeRouter
from .career_profile_context import CareerProfileContextStore
from .conversations import (
    ConnectionResponse,
    ConversationJobContext,
    ConversationModel,
    ConversationResponse,
    ConversationService,
)
from .state_store import ConversationNotFound, JobOsStateStore


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

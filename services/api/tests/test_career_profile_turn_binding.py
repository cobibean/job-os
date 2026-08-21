import asyncio
import json
import sqlite3

import pytest
from jobos_api.agent_gateway import AgentContext
from jobos_api.career_profile import (
    CareerProfileSnapshotForbidden,
    CareerProfileSnapshotIntegrityError,
    CareerProfileStore,
    WorkArrangementMutation,
    WorkArrangementValue,
    principal_for_device,
)
from jobos_api.conversations import (
    ConversationService,
    RetryTurnRequest,
    SendMessageRequest,
)
from jobos_api.state_store import JobOsStateStore


class CapturingGateway:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, AgentContext]] = []

    @property
    def connection_state(self):
        return "online"

    async def start(self):
        return None

    async def create_or_resume_conversation(self, stored_session_id):
        return "stored-session", "live-session"

    async def submit_turn(self, text, context):
        self.submissions.append((text, context))

    async def stream_events(self):
        if False:
            yield

    async def detach_conversation(self):
        return None

    async def interrupt_turn(self, turn_id):
        return None

    async def recover_active_turn(self, stored_session_id, turn_id):
        return None

    async def close(self):
        return None


def configured_service(tmp_path):
    database = tmp_path / "jobos.db"
    state = JobOsStateStore(database)
    state.initialize(owner_device_id="device-a")
    profile = CareerProfileStore(database)
    profile.initialize()
    principal = principal_for_device("device-a")
    gateway = CapturingGateway()
    conversation_id = state.first_active_conversation_id("device-a")
    service = ConversationService(
        state.conversation_store(conversation_id),
        gateway,
        conversation_id,
        career_profile_principal=principal,
    )
    return database, profile, service, gateway, principal


def set_arrangement(profile, principal, *, revision, mode, key):
    return profile.set_work_arrangement(
        principal=principal,
        command=WorkArrangementMutation(
            expected_profile_revision=revision,
            idempotency_key=key,
            value=WorkArrangementValue(
                mode=mode, strength="strong_preference", note=f"Prefer {mode}"
            ),
        ),
    )


def test_new_turn_binds_latest_snapshot_and_retry_keeps_original(tmp_path):
    async def scenario():
        database, profile, service, gateway, principal = configured_service(tmp_path)
        set_arrangement(profile, principal, revision=0, mode="remote", key="set-remote-0001")

        first = await service.send(
            SendMessageRequest(text="Find roles", idempotency_key="send-profile-0001"),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        first_context = gateway.submissions[-1][1].career_profile
        assert first_context is not None
        assert first_context["profile_revision"] == 1
        first_projection = first_context["projection"]
        assert isinstance(first_projection, dict)
        first_arrangement = first_projection["work_arrangement"]
        assert isinstance(first_arrangement, dict)
        assert first_arrangement["mode"] == "remote"

        service.store.update_turn_status(first.turn_id, "failed")
        set_arrangement(profile, principal, revision=1, mode="hybrid", key="set-hybrid-0002")
        retry = await service.retry(
            first.turn_id,
            RetryTurnRequest(idempotency_key="retry-profile-0001"),
            actor_id="device-a",
        )
        assert retry is not None
        retry_context = gateway.submissions[-1][1].career_profile
        assert retry_context == first_context

        service.store.update_turn_status(retry.turn_id, "completed")
        await service.send(
            SendMessageRequest(text="Find more", idempotency_key="send-profile-0002"),
            actor_id="device-a",
            context={"selected_job_id": None, "workspace": {}},
        )
        latest_context = gateway.submissions[-1][1].career_profile
        assert latest_context is not None
        assert latest_context["profile_revision"] == 2
        latest_projection = latest_context["projection"]
        assert isinstance(latest_projection, dict)
        latest_arrangement = latest_projection["work_arrangement"]
        assert isinstance(latest_arrangement, dict)
        assert latest_arrangement["mode"] == "hybrid"

        with sqlite3.connect(database) as connection:
            turn_payloads = "\n".join(
                row[0] for row in connection.execute("SELECT detail_json FROM conversation_events")
            )
            audit_payloads = "\n".join(
                row[0]
                for row in connection.execute(
                    "SELECT affected_fields_json FROM career_profile_audit_events"
                )
            )
        assert "Prefer remote" not in turn_payloads
        assert "Prefer hybrid" not in turn_payloads
        assert "Prefer remote" not in audit_payloads
        assert "Prefer hybrid" not in audit_payloads

    asyncio.run(scenario())


def test_missing_or_tampered_bound_snapshot_fails_closed(tmp_path):
    async def scenario():
        database, profile, service, gateway, principal = configured_service(tmp_path)
        set_arrangement(profile, principal, revision=0, mode="remote", key="set-remote-0003")
        created = service.store.create_turn(
            text="Do not dispatch stale context",
            context={},
            idempotency_key="tampered-profile-turn",
            actor_id="device-a",
            career_profile_principal=principal,
        )
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                UPDATE career_profile_snapshots SET projection_json = ?
                WHERE snapshot_id = (
                    SELECT career_profile_snapshot_id FROM conversation_turns WHERE turn_id = ?
                )
                """,
                (json.dumps({"work_arrangement": None}), created["turn_id"]),
            )
        with pytest.raises(CareerProfileSnapshotIntegrityError):
            service.store.bound_career_profile_snapshot(
                str(created["turn_id"]), principal=principal
            )
        turn = service.store.turn_record(str(created["turn_id"]))
        assert turn is not None
        await service._dispatch(turn)
        assert gateway.submissions == []
        settled = service.store.turn_record(str(created["turn_id"]))
        assert settled is not None
        assert settled["status"] == "failed"

    asyncio.run(scenario())


def test_background_continuation_keeps_spawning_turn_binding(tmp_path):
    database, profile, service, _gateway, principal = configured_service(tmp_path)
    set_arrangement(profile, principal, revision=0, mode="remote", key="set-remote-0004")
    first = service.store.create_turn(
        text="Spawn background work",
        context={},
        idempotency_key="continuation-source-a",
        actor_id="device-a",
        career_profile_principal=principal,
    )
    service.store.append_conversation_event(
        turn_id=str(first["turn_id"]),
        event_type="activity",
        state="working",
        summary="Delegated background work",
        detail={"activity_id": "delegate-activity-0001"},
        source_event_id="source-delegate-activity-0001",
        continuation_ids=("delegation-binding-0001",),
    )
    service.store.update_turn_status(str(first["turn_id"]), "completed")

    set_arrangement(profile, principal, revision=1, mode="hybrid", key="set-hybrid-0004")
    second = service.store.create_turn(
        text="A genuinely new turn",
        context={},
        idempotency_key="continuation-source-b",
        actor_id="device-a",
        career_profile_principal=principal,
    )
    service.store.update_turn_status(str(second["turn_id"]), "completed")
    reopened = ConversationService(
        service.store,
        service.gateway,
        service.conversation_id,
        career_profile_principal=principal,
    )
    assert reopened.store.record_agent_continuation(
        turn_id="turn_continuation_binding_0001",
        status="completed",
        event_type="assistant_message",
        summary="Background work finished",
        detail={
            "agent_continuation": True,
            "continuation_id": "delegation-binding-0001",
        },
        career_profile_principal=principal,
    )

    with sqlite3.connect(database) as connection:
        source_binding = connection.execute(
            """SELECT career_profile_snapshot_id, career_profile_revision,
                      career_profile_content_hash
               FROM conversation_turns WHERE turn_id = ?""",
            (first["turn_id"],),
        ).fetchone()
        continuation_binding = connection.execute(
            """SELECT career_profile_snapshot_id, career_profile_revision,
                      career_profile_content_hash
               FROM conversation_turns WHERE turn_id = 'turn_continuation_binding_0001'"""
        ).fetchone()
    assert continuation_binding == source_binding
    assert continuation_binding is not None
    assert continuation_binding[1] == 1


def test_background_continuation_without_durable_binding_fails_closed(tmp_path):
    database, profile, service, _gateway, principal = configured_service(tmp_path)
    set_arrangement(profile, principal, revision=0, mode="remote", key="set-remote-0005")

    assert not service.store.record_agent_continuation(
        turn_id="turn_unbound_continuation_0001",
        status="completed",
        event_type="assistant_message",
        summary="Untrusted background result",
        detail={
            "agent_continuation": True,
            "continuation_id": "unknown-continuation-0001",
        },
        career_profile_principal=principal,
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM conversation_turns WHERE turn_id = ?",
            ("turn_unbound_continuation_0001",),
        ).fetchone() is None


def test_background_continuation_rejects_unauthorized_source_snapshot(tmp_path):
    database, profile, service, _gateway, principal = configured_service(tmp_path)
    set_arrangement(profile, principal, revision=0, mode="remote", key="set-remote-0007")
    source = service.store.create_turn(
        text="Owner-only background work",
        context={},
        idempotency_key="continuation-owner-source",
        actor_id="device-a",
        career_profile_principal=principal,
    )
    service.store.append_conversation_event(
        turn_id=str(source["turn_id"]),
        event_type="activity",
        state="working",
        summary="Delegated background work",
        detail={"activity_id": "delegate-activity-owner"},
        continuation_ids=("delegation-owner-binding",),
    )

    with pytest.raises(CareerProfileSnapshotForbidden):
        service.store.record_agent_continuation(
            turn_id="turn_unauthorized_continuation_0001",
            status="completed",
            event_type="assistant_message",
            summary="Background result",
            detail={
                "agent_continuation": True,
                "continuation_id": "delegation-owner-binding",
            },
            career_profile_principal=principal_for_device("device-b"),
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM conversation_turns WHERE turn_id = ?",
            ("turn_unauthorized_continuation_0001",),
        ).fetchone() is None


def test_retry_after_service_restart_keeps_original_and_fresh_session_gets_latest(tmp_path):
    async def scenario():
        _database, profile, service, gateway, principal = configured_service(tmp_path)
        set_arrangement(profile, principal, revision=0, mode="remote", key="set-remote-0006")
        original = await service.send(
            SendMessageRequest(text="Long-running work", idempotency_key="restart-source-0001"),
            actor_id="device-a",
            context={},
        )
        original_context = gateway.submissions[-1][1].career_profile
        service.store.update_turn_status(original.turn_id, "interrupted")
        set_arrangement(profile, principal, revision=1, mode="hybrid", key="set-hybrid-0006")

        restarted_gateway = CapturingGateway()
        restarted = ConversationService(
            service.store,
            restarted_gateway,
            service.conversation_id,
            career_profile_principal=principal,
        )
        retry = await restarted.retry(
            original.turn_id,
            RetryTurnRequest(idempotency_key="restart-retry-0001"),
            actor_id="device-a",
        )
        assert retry is not None
        assert restarted_gateway.submissions[-1][1].career_profile == original_context

        restarted.store.update_turn_status(retry.turn_id, "completed")
        await restarted.send(
            SendMessageRequest(
                text="Fresh isolated work",
                idempotency_key="browser-save-fresh-profile-0001",
            ),
            actor_id="device-a",
            context={},
        )
        fresh_context = restarted_gateway.submissions[-1][1].career_profile
        assert fresh_context is not None
        assert fresh_context["profile_revision"] == 2
        fresh_projection = fresh_context["projection"]
        assert isinstance(fresh_projection, dict)
        fresh_arrangement = fresh_projection["work_arrangement"]
        assert isinstance(fresh_arrangement, dict)
        assert fresh_arrangement["mode"] == "hybrid"

    asyncio.run(scenario())


def test_idempotent_replay_reuses_turn_and_snapshot(tmp_path):
    async def scenario():
        database, profile, service, gateway, principal = configured_service(tmp_path)
        set_arrangement(profile, principal, revision=0, mode="onsite", key="set-onsite-0001")
        command = SendMessageRequest(text="One turn", idempotency_key="same-turn-profile")
        first = await service.send(command, actor_id="device-a", context={})
        replay = await service.send(command, actor_id="device-a", context={})
        assert replay.turn_id == first.turn_id
        with sqlite3.connect(database) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM career_profile_snapshots"
            ).fetchone()[0]
        assert count == 1

    asyncio.run(scenario())
"""Provider-neutral event trace validation used by adapter acceptance tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

EventKind = Literal[
    "turn_started",
    "assistant_text_delta",
    "reasoning_activity",
    "tool_started",
    "tool_progress",
    "tool_review_required",
    "tool_completed",
    "turn_completed",
    "turn_cancelled",
    "turn_failed",
    "connection_state",
    "recovery_state",
]
TERMINAL_KINDS = frozenset({"turn_completed", "turn_cancelled", "turn_failed"})


class EventTraceViolation(AssertionError):
    """Stable, content-free rejection raised for an invalid normalized trace."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NormalizedEvent:
    sequence: int
    source_event_id: str
    profile_id: str
    chat_id: str
    turn_id: str
    kind: EventKind
    payload: dict[str, object]


@dataclass(frozen=True)
class TraceExpectation:
    profile_id: str
    chat_id: str
    turn_id: str
    agent_id: str
    session_id: str
    payload_canary: str
    forbidden_payload_canaries: tuple[str, ...] = ()


class EventTrace:
    """Collect one scoped turn while enforcing ordering and terminal integrity."""

    def __init__(self, *, profile_id: str, chat_id: str, turn_id: str) -> None:
        self._scope = (profile_id, chat_id, turn_id)
        self._events: list[NormalizedEvent] = []
        self._source_ids: set[str] = set()
        self._terminal = False

    @property
    def events(self) -> tuple[NormalizedEvent, ...]:
        return tuple(self._events)

    def append(self, event: NormalizedEvent) -> None:
        if (event.profile_id, event.chat_id, event.turn_id) != self._scope:
            raise EventTraceViolation("event_scope_mismatch")
        if event.source_event_id in self._source_ids:
            raise EventTraceViolation("duplicate_source_event")
        if event.sequence != len(self._events) + 1:
            raise EventTraceViolation("event_sequence_out_of_order")
        if self._terminal:
            raise EventTraceViolation("event_after_terminal")
        if not self._events and event.kind != "turn_started":
            raise EventTraceViolation("turn_start_missing")
        self._events.append(event)
        self._source_ids.add(event.source_event_id)
        self._terminal = event.kind in TERMINAL_KINDS

    def assert_complete(self) -> None:
        if not self._events or not self._terminal:
            raise EventTraceViolation("terminal_event_missing")
        terminal_count = sum(event.kind in TERMINAL_KINDS for event in self._events)
        if terminal_count != 1:
            raise EventTraceViolation("terminal_event_count_invalid")


def assert_trace_isolation(
    traces: tuple[tuple[EventTrace, TraceExpectation], ...],
) -> None:
    """Prove bindings, payload canaries, and provider event IDs stay isolated."""

    seen_source_ids: set[str] = set()
    for trace, expected in traces:
        trace.assert_complete()
        for event in trace.events:
            if (event.profile_id, event.chat_id, event.turn_id) != (
                expected.profile_id,
                expected.chat_id,
                expected.turn_id,
            ):
                raise EventTraceViolation("cross_trace_binding_mismatch")
        started = trace.events[0]
        if started.payload.get("agent_id") != expected.agent_id:
            raise EventTraceViolation("cross_trace_agent_mismatch")
        if started.payload.get("session_id") != expected.session_id:
            raise EventTraceViolation("cross_trace_session_mismatch")
        payload = json.dumps(
            [event.payload for event in trace.events], sort_keys=True, separators=(",", ":")
        )
        if expected.payload_canary not in payload:
            raise EventTraceViolation("cross_trace_payload_canary_missing")
        if any(canary in payload for canary in expected.forbidden_payload_canaries):
            raise EventTraceViolation("cross_trace_payload_leakage")
        source_ids = {event.source_event_id for event in trace.events}
        if seen_source_ids.intersection(source_ids):
            raise EventTraceViolation("cross_trace_source_event_reuse")
        seen_source_ids.update(source_ids)

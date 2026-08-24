"""Deterministic provider and credential-vault controls for later phases."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .events import EventTrace, NormalizedEvent


@dataclass(frozen=True)
class FakeBinding:
    profile_id: str
    chat_id: str
    turn_id: str
    agent_id: str
    model_id: str = "(FAKE)-model-stable"
    reasoning_effort: str = "medium"


class DeterministicFakeProvider:
    """A provider control with stable sessions and normalized output only."""

    provider = "fake"

    def session_id(self, binding: FakeBinding) -> str:
        material = "\0".join((binding.profile_id, binding.chat_id, binding.agent_id))
        return f"(FAKE)-session-{hashlib.sha256(material.encode()).hexdigest()[:16]}"

    def complete_turn(self, binding: FakeBinding, text: str | None = None) -> EventTrace:
        trace = EventTrace(
            profile_id=binding.profile_id,
            chat_id=binding.chat_id,
            turn_id=binding.turn_id,
        )
        kinds_and_payloads = (
            (
                "turn_started",
                {
                    "agent_id": binding.agent_id,
                    "session_id": self.session_id(binding),
                    "model_id": binding.model_id,
                    "reasoning_effort": binding.reasoning_effort,
                },
            ),
            (
                "assistant_text_delta",
                {"text": text or f"(FAKE) response for {binding.profile_id}"},
            ),
            ("turn_completed", {"finish_reason": "completed"}),
        )
        for sequence, (kind, payload) in enumerate(kinds_and_payloads, start=1):
            trace.append(
                NormalizedEvent(
                    sequence=sequence,
                    source_event_id=f"{self.session_id(binding)}:{binding.turn_id}:{sequence}",
                    profile_id=binding.profile_id,
                    chat_id=binding.chat_id,
                    turn_id=binding.turn_id,
                    kind=kind,  # type: ignore[arg-type]
                    payload=payload,
                )
            )
        trace.assert_complete()
        return trace


class DeterministicFakeCredentialVault:
    """In-memory fake that stores only a one-way test fingerprint."""

    def __init__(self) -> None:
        self._fingerprints: dict[str, str] = {}

    def store(self, namespace: str, credential: str) -> str:
        digest = hashlib.sha256(f"{namespace}\0{credential}".encode()).hexdigest()
        reference = f"(FAKE)-vault-ref-{hashlib.sha256(namespace.encode()).hexdigest()[:16]}"
        self._fingerprints[reference] = digest
        return reference

    def verify(self, reference: str, credential: str, *, namespace: str) -> bool:
        expected = hashlib.sha256(f"{namespace}\0{credential}".encode()).hexdigest()
        return self._fingerprints.get(reference) == expected

    def remove(self, reference: str) -> bool:
        return self._fingerprints.pop(reference, None) is not None

    def references(self) -> tuple[str, ...]:
        return tuple(sorted(self._fingerprints))

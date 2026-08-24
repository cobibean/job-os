"""Deterministic fault injection and concurrency coordination primitives."""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Iterable


class InjectedFault(RuntimeError):
    def __init__(self, checkpoint: str) -> None:
        self.checkpoint = checkpoint
        super().__init__(f"injected_fault:{checkpoint}")


class DeterministicFaultInjector:
    """Raise at named, one-indexed checkpoint occurrences."""

    def __init__(self, scheduled: Iterable[tuple[str, int]] = ()) -> None:
        self._scheduled = set(scheduled)
        self._counts: Counter[str] = Counter()

    def checkpoint(self, name: str) -> None:
        self._counts[name] += 1
        if (name, self._counts[name]) in self._scheduled:
            raise InjectedFault(name)

    def count(self, name: str) -> int:
        return self._counts[name]


class ConcurrencyCoordinator:
    """Explicit named gates make thread interleavings repeatable in tests."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._released: set[str] = set()
        self._arrived: set[str] = set()

    def arrive_and_wait(self, gate: str, *, timeout: float = 2.0) -> None:
        with self._condition:
            self._arrived.add(gate)
            self._condition.notify_all()
            if not self._condition.wait_for(lambda: gate in self._released, timeout=timeout):
                raise TimeoutError(f"coordination gate timed out: {gate}")

    def wait_until_arrived(self, gate: str, *, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while gate not in self._arrived:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._condition.wait(timeout=remaining):
                    raise TimeoutError(f"coordination arrival timed out: {gate}")

    def release(self, gate: str) -> None:
        with self._condition:
            self._released.add(gate)
            self._condition.notify_all()

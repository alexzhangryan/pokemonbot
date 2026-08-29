"""Deadline watchdog: an anytime wrapper around decision making.

A decision maintains a current best action and returns it when the deadline
arrives, whether or not it has finished searching. This is a correctness
requirement rather than performance work: Showdown's `VGC Timer` rule auto-loses
inactive players, so a live game is clock enforced regardless of whether we are
optimizing for the clock yet. See docs/09-m0-tasks.md T0.7 and DECISIONS.md D7.

The decision function receives an `AnytimeDecision` handle and calls `propose()`
each time it finds a better action. If the deadline arrives first, the search
task is cancelled and the last proposal is returned with `finished=False`.

Caveat: cancellation is cooperative, as everywhere in asyncio. A decision that
blocks the event loop in a tight CPU-bound loop without awaiting cannot be
interrupted, and will overrun its deadline. Search code must await periodically
(`propose()` is the natural place to do so) to stay interruptible.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from champions.trace.schema import EventType
from champions.trace.writer import Trace


class AnytimeDecision[A]:
    """Handle a decision function uses to publish improving candidate actions."""

    def __init__(self, fallback: A) -> None:
        self._best: A = fallback
        self._value: float | None = None
        self._proposals = 0

    def propose(self, action: A, value: float | None = None) -> None:
        self._best = action
        self._value = value
        self._proposals += 1

    @property
    def best(self) -> A:
        return self._best

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def proposals(self) -> int:
        return self._proposals


@dataclass(frozen=True)
class DecisionResult[A]:
    action: A
    finished: bool
    watchdog_fired: bool
    elapsed_ms: float
    proposals: int
    value: float | None = None


async def decide_with_deadline[A](
    decision_fn: Callable[[AnytimeDecision[A]], Awaitable[Any]],
    fallback: A,
    deadline_s: float,
    trace: Trace | None = None,
    trace_payload: dict[str, Any] | None = None,
) -> DecisionResult[A]:
    """Run `decision_fn` under a deadline, returning the best action found by then.

    `fallback` is the action returned if the deadline arrives before the decision
    proposes anything at all, so this never returns without a legal action.
    """
    decision: AnytimeDecision[A] = AnytimeDecision(fallback)
    start = time.perf_counter()
    watchdog_fired = False

    task = asyncio.ensure_future(decision_fn(decision))
    try:
        await asyncio.wait_for(task, timeout=deadline_s)
        finished = True
    except TimeoutError:
        watchdog_fired = True
        finished = False

    elapsed_ms = (time.perf_counter() - start) * 1000

    result = DecisionResult(
        action=decision.best,
        finished=finished,
        watchdog_fired=watchdog_fired,
        elapsed_ms=elapsed_ms,
        proposals=decision.proposals,
        value=decision.value,
    )

    if trace is not None:
        trace.emit(
            EventType.TIMING,
            {
                **(trace_payload or {}),
                "total_ms": elapsed_ms,
                "deadline_s": deadline_s,
                "watchdog_fired": watchdog_fired,
                "finished": finished,
                "proposals": decision.proposals,
                "exceeded_45s": elapsed_ms > 45_000,
            },
        )

    return result

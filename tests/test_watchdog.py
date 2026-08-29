"""T0.7: a deliberately slow mock decision returns within its deadline, and the
trace records that the watchdog fired and that the result was unfinished."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from champions.search.watchdog import AnytimeDecision, decide_with_deadline
from champions.trace.writer import Trace, read_events


async def test_slow_decision_returns_within_deadline(tmp_path: Path) -> None:
    async def far_too_slow(decision: AnytimeDecision[str]) -> None:
        decision.propose("depth-1")
        await asyncio.sleep(10.0)
        decision.propose("depth-2-never-reached")

    deadline = 0.2
    start = time.perf_counter()
    result = await decide_with_deadline(far_too_slow, fallback="protect", deadline_s=deadline)
    elapsed = time.perf_counter() - start

    assert result.action == "depth-1", "should return the best action found so far"
    assert result.watchdog_fired is True
    assert result.finished is False
    assert elapsed < deadline + 0.5, f"overran its deadline: {elapsed:.3f}s"


async def test_watchdog_firing_is_recorded_in_the_trace(tmp_path: Path) -> None:
    trace = Trace("battle-watchdog", trace_dir=tmp_path)

    async def far_too_slow(decision: AnytimeDecision[str]) -> None:
        decision.propose("best-so-far")
        await asyncio.sleep(10.0)

    await decide_with_deadline(
        far_too_slow,
        fallback="protect",
        deadline_s=0.2,
        trace=trace,
        trace_payload={"turn": 3},
    )
    await trace.close()

    timing_events = [e for e in read_events(trace.path) if e.type == "timing"]
    assert len(timing_events) == 1
    payload = timing_events[0].payload
    assert payload["watchdog_fired"] is True
    assert payload["finished"] is False
    assert payload["turn"] == 3


async def test_fast_decision_finishes_and_does_not_fire_the_watchdog(tmp_path: Path) -> None:
    async def quick(decision: AnytimeDecision[str]) -> None:
        decision.propose("first-guess")
        await asyncio.sleep(0)
        decision.propose("refined", value=0.71)

    trace = Trace("battle-fast", trace_dir=tmp_path)
    result = await decide_with_deadline(quick, fallback="protect", deadline_s=5.0, trace=trace)
    await trace.close()

    assert result.action == "refined"
    assert result.value == 0.71
    assert result.finished is True
    assert result.watchdog_fired is False
    assert result.proposals == 2

    payload = [e for e in read_events(trace.path) if e.type == "timing"][0].payload
    assert payload["watchdog_fired"] is False
    assert payload["finished"] is True


async def test_fallback_is_returned_when_nothing_is_proposed_in_time() -> None:
    async def proposes_nothing(decision: AnytimeDecision[str]) -> None:
        await asyncio.sleep(10.0)
        decision.propose("too-late")

    result = await decide_with_deadline(
        proposes_nothing, fallback="default-legal-action", deadline_s=0.2
    )

    assert result.action == "default-legal-action"
    assert result.proposals == 0
    assert result.watchdog_fired is True


async def test_search_keeps_improving_until_the_deadline() -> None:
    """The point of anytime search: a deeper answer if there's time, a valid one regardless."""

    async def iterative_deepening(decision: AnytimeDecision[int]) -> None:
        for depth in range(1, 100):
            await asyncio.sleep(0.02)
            decision.propose(depth)

    result = await decide_with_deadline(iterative_deepening, fallback=0, deadline_s=0.3)

    assert result.watchdog_fired is True
    assert result.action > 1, "should have improved past its first proposal"
    assert result.action < 100, "should not have run to completion"

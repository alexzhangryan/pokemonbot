"""M11: the one ply agent under an allocated budget, escalating when close.

`docs/04-decision-engine.md` section 7 states the intended rule: run a cheap
first pass; if the equilibrium is near pure and the gap between the best and
second-best action exceeds a threshold, commit immediately; otherwise escalate
until the gap resolves or the slice is spent, tracking cumulative usage
against the 7 minute total. `champions/search/clock.py` is the arithmetic and
this is the agent that applies it.

Three things change from `TwoPlyAgent`, which already has the two-stage
structure:

1. **The deadline is allocated, not fixed.** `_deadline_s` reads the battle's
   clock state and hands `decide_with_deadline` a per-turn budget that is a
   share of what is left, never more than the turn limit.
2. **The second stage runs only when the first is close.** A decisive one-ply
   solve is played as is; a mixed or narrow one is re-solved one ply deeper
   inside whatever budget remains. `is_decisive` is the test and the trace
   records `escalated`, `decisive` and the gap.
3. **The budget state goes on the timing event.** Remaining player clock,
   the budget offered, and what the turn actually cost, so the harness and the
   viewer can see the allocation working rather than infer it.

Win rate is not the claim. M8 measured the extra ply as not apart from the
incumbent (D71), so escalating to it is a structural choice about where time
goes, and the ladder table is what says whether it costs anything.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np
from poke_env.battle import AbstractBattle
from poke_env.player.battle_order import BattleOrder

from champions.agents.twoply import TwoPlyAgent
from champions.search.clock import (
    DECISIVE_GAP,
    DEFAULT_EXPECTED_TURNS,
    DEFAULT_RESERVE_S,
    is_decisive,
    turn_budget,
)
from champions.search.kinds import solve_columns
from champions.search.payoff import payoff_matrix
from champions.search.watchdog import AnytimeDecision


class AdaptiveAgent(TwoPlyAgent):
    """One ply when the answer is clear, two when it is not, on a budget."""

    strategy = "adaptive-equilibrium"
    payoff_model = "analytic-adaptive"

    def __init__(
        self,
        *args: Any,
        gap: float = DECISIVE_GAP,
        reserve_s: float = DEFAULT_RESERVE_S,
        expected_turns: int = DEFAULT_EXPECTED_TURNS,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._gap = gap
        self._reserve_s = reserve_s
        self._expected_turns = expected_turns

    def _deadline_s(self, battle: AbstractBattle) -> float:
        clock = self.clock_for(battle)
        return turn_budget(
            clock.spent_s,
            battle.turn,
            expected_turns=self._expected_turns,
            reserve_s=self._reserve_s,
            turn_limit_s=self._decision_deadline_s,
        )

    async def _estimate(
        self,
        battle: AbstractBattle,
        snapshot: dict[str, Any],
        ours: list[dict[str, Any]],
        theirs: list[dict[str, Any]],
        decision: AnytimeDecision[BattleOrder],
        by_message: dict[str, BattleOrder],
        timings: dict[str, float],
    ) -> tuple[np.ndarray, dict[str, Any]]:
        started = time.perf_counter()
        shallow = payoff_matrix(snapshot, ours, theirs, self._turn_model(battle))
        first, _ = solve_columns(shallow, theirs, battle.turn, self._kind_prior, self._prior_weight)
        timings["payoff_one_ply_s"] = time.perf_counter() - started
        chosen = ours[self._sample(first.row, battle)]
        decision.propose(by_message[chosen["message"]], value=float(first.value))
        await asyncio.sleep(0)

        decisive, margin = is_decisive(first.row, shallow @ first.column, self._gap)
        record: dict[str, Any] = {
            "decisive": decisive,
            "gap": float(margin) if np.isfinite(margin) else None,
            "gap_threshold": self._gap,
            "game_value_one_ply": float(first.value),
        }
        if decisive:
            return shallow, {**record, "escalated": False}

        started = time.perf_counter()
        model = self._two_ply_model(battle)
        model.reset()
        rows: list[list[float]] = []
        for our_action in ours:
            rows.append(model.row(snapshot, our_action, theirs))
            await asyncio.sleep(0)
        matrix = np.array(rows, dtype=float).reshape(len(ours), len(theirs))
        timings["payoff_two_ply_s"] = time.perf_counter() - started
        return matrix, {
            **record,
            "escalated": True,
            "k2": self._k2,
            "payoff_one_ply": shallow.tolist(),
            **model.stats.as_dict(),
        }

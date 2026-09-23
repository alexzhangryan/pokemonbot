"""The depth arm of the M8 engine gate: the one ply agent, one ply deeper.

Everything structural is `OnePlyAgent`'s -- prune from the request, solve the
matrix game, sample the equilibrium. What changes is what a cell is worth: the
one-ply model scores the position one turn's resolution leaves behind, and
this agent scores the *game* that position starts, by pruning and solving it
too (`champions/search/twoply.py`). `docs/specs/2026-09-13-engine-gate.md`
section 5.1 specifies it and section 4 fixes how it is judged.

## Anytime, in two stages

A two-ply decision costs on the order of a hundred one-ply decisions, so the
one-ply answer is computed first and proposed, and the two-ply matrix is built
row by row with an await between rows. If the watchdog fires during the second
stage the agent plays the one-ply equilibrium draw, which is exactly the
incumbent's answer rather than a degraded one. Both matrices go on the trace,
so a reviewer can see what the extra ply changed.

## The seams it inherits

`_turn_model` and `_believed_moves` are the two places a belief or an oracle
plugs into `OnePlyAgent`, and the two-ply model is built from whatever they
return for the battle. That is what lets `twoply-oracle` be a subclass with no
search code of its own (`champions/agents/oracle.py`).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np
from poke_env.battle import AbstractBattle
from poke_env.player.battle_order import BattleOrder

from champions.agents.oneply import OnePlyAgent
from champions.search.kinds import solve_columns
from champions.search.payoff import payoff_matrix
from champions.search.twoply import DEFAULT_K2, TwoPlyModel
from champions.search.watchdog import AnytimeDecision


class TwoPlyAgent(OnePlyAgent):
    """Two plies on the analytic model; otherwise `OnePlyAgent`."""

    strategy = "two-ply-equilibrium"
    payoff_model = "analytic-two-ply"

    def __init__(self, *args: Any, k2: int = DEFAULT_K2, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._k2 = k2
        self._two_ply: dict[str, TwoPlyModel] = {}

    def _two_ply_model(self, battle: AbstractBattle) -> TwoPlyModel:
        """One model per battle, built on that battle's hypothesis and effects.

        Per battle for the reason `BeliefAgent._turn_model` is: the ladder runs
        games concurrently through one player, and a belief or oracle is per
        battle. Reset per decision by the caller, since the memo is per position.
        """
        tag = battle.battle_tag
        if tag not in self._two_ply:
            turn = self._turn_model(battle)
            self._two_ply[tag] = TwoPlyModel(
                self.dex,
                hypothesis=turn.hypothesis,
                effects=turn.effects,
                policy=self._policy,
                k2=self._k2,
                believed_moves=self._believed_moves(battle),
            )
        return self._two_ply[tag]

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
        # Stage one: the incumbent's answer, proposed so the deadline has
        # something as good as `OnePlyAgent` would have played.
        started = time.perf_counter()
        shallow = payoff_matrix(snapshot, ours, theirs, self._turn_model(battle))
        first, _ = solve_columns(shallow, theirs, battle.turn, self._kind_prior, self._prior_weight)
        timings["payoff_one_ply_s"] = time.perf_counter() - started
        chosen = ours[self._sample(first.row, battle)]
        decision.propose(by_message[chosen["message"]], value=float(first.value))
        await asyncio.sleep(0)

        # Stage two: the same matrix one ply deeper, a row at a time.
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
            "k2": self._k2,
            "payoff_one_ply": shallow.tolist(),
            "game_value_one_ply": float(first.value),
            **model.stats.as_dict(),
        }

    def _battle_finished_callback(self, battle: AbstractBattle) -> None:
        self._two_ply.pop(battle.battle_tag, None)
        super()._battle_finished_callback(battle)

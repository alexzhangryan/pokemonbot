"""The one ply agent: prune, estimate, solve, sample.

The M2 deliverable. It plugs into `TracingPlayer._search`, so it inherits the
whole observability surface and adds the four things the decision engine is
made of (`docs/04-decision-engine.md`):

1. Enumerate the joint action set from the request, which `TracingPlayer`
   already does because the request is the only correct source of legality.
2. Prune both sides to `k` candidates with a `PolicyProvider`.
3. Estimate each cell with the analytic turn model.
4. Solve the matrix game by LP and sample the resulting mixed strategy.

## Why it samples rather than taking the mode

Playing the argmax of an equilibrium is not playing the equilibrium. Protect,
Fake Out and redirection are prediction interactions, and an opponent who learns
the agent's deterministic reply beats it from then on. So the action is drawn
from the mixed strategy.

That makes the agent stochastic, which is why the seed is on the trace and why
the draw uses a per-decision generator derived from `(seed, battle, turn)`
rather than global randomness: the same battle replayed with the same seed makes
the same choices, so a trace can be re-derived rather than merely read.

## Anytime by construction

The search proposes twice: the policy layer's top-scoring action as soon as
pruning is done, then the equilibrium draw once the matrix is solved. If the
watchdog fires between them the agent plays the heuristic pick, which is the
`MaxBasePowerAgent`-grade fallback rather than a random one. It awaits between
phases so cancellation can actually land, which `watchdog.py` documents as the
condition for the deadline to be honoured.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import numpy as np
from poke_env.battle import AbstractBattle
from poke_env.player.battle_order import BattleOrder

from champions.dex.loader import Dex, DexNotBuiltError
from champions.protocol import actions as action_describe
from champions.protocol import state as state_snapshot
from champions.search.matrix import solve_both
from champions.search.payoff import OpponentHypothesis, TurnModel, payoff_matrix
from champions.search.policy import DEFAULT_K, HeuristicPolicy, opponent_candidates
from champions.search.watchdog import AnytimeDecision
from champions.trace.schema import EventType

from .baseline import TracingPlayer


class OnePlyAgent(TracingPlayer):
    """Prunes, solves a matrix game, and samples the equilibrium."""

    strategy = "one-ply-equilibrium"

    def __init__(
        self,
        *args: Any,
        dex: Dex | None = None,
        k: int = DEFAULT_K,
        hypothesis: OpponentHypothesis | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, dex=dex, **kwargs)
        if self._dex is None:
            raise DexNotBuiltError(
                f"{type(self).__name__} computes every number it uses from the Champions "
                f"dex, which is not built for {self.format!r}. poke-env's mainline values "
                f"would be silently wrong rather than absent. Build it with:\n"
                f"    python scripts/build_dex.py {self.format}"
            )
        self._k = k
        self._policy = HeuristicPolicy(self._dex)
        self._model = TurnModel(self._dex, hypothesis)

    async def _search(
        self,
        battle: AbstractBattle,
        orders: list[BattleOrder],
        decision: AnytimeDecision[BattleOrder],
    ) -> None:
        if not orders:
            return

        trace = self.trace_for(battle)
        timings: dict[str, float] = {}

        # -- prune ------------------------------------------------------
        started = time.perf_counter()
        described = [action_describe.describe(order, self._dex) for order in orders]
        by_message = {d["message"]: order for d, order in zip(described, orders, strict=True)}
        scored = self._policy.scored(described, self._k)
        timings["candidates_s"] = time.perf_counter() - started

        if not scored:
            return

        # The best heuristic action is a usable answer on its own, so it is
        # proposed before the expensive phase starts rather than after it fails.
        #
        # Proposed without a value on purpose. The trace's `value` field is a win
        # probability everywhere else, and a policy score is not one; putting a
        # 7.3 where the coach expects a 0.73 would be worse than an honest null.
        decision.propose(by_message[scored[0].action["message"]], value=None)
        await asyncio.sleep(0)

        # -- estimate ---------------------------------------------------
        started = time.perf_counter()
        snapshot = state_snapshot.snapshot(battle, self._dex)
        theirs = opponent_candidates(snapshot, self._dex, self._k)
        ours = [s.action for s in scored]
        matrix = payoff_matrix(snapshot, ours, theirs, self._model)
        timings["payoff_s"] = time.perf_counter() - started
        await asyncio.sleep(0)

        # -- solve ------------------------------------------------------
        started = time.perf_counter()
        equilibrium = solve_both(matrix)
        timings["solve_s"] = time.perf_counter() - started

        index = self._sample(equilibrium.row, battle)
        chosen = ours[index]
        decision.propose(by_message[chosen["message"]], value=float(equilibrium.value))

        trace.emit(
            EventType.CANDIDATES,
            {
                "turn": battle.turn,
                "phase": "pruned",
                "pruned": True,
                "k": self._k,
                "n_legal_joint_actions": len(orders),
                "joint": [
                    {
                        **s.action,
                        "policy_score": s.score,
                        "policy_reasons": list(s.reasons),
                        "policy_provider": "heuristic",
                        "equilibrium_probability": float(equilibrium.row[i]),
                    }
                    for i, s in enumerate(scored)
                ],
                "opponent_joint": theirs,
                "opponent_equilibrium": [float(p) for p in equilibrium.column],
                "payoff": matrix.tolist(),
                "game_value": float(equilibrium.value),
                "is_pure": equilibrium.is_pure,
                "support": equilibrium.support,
                "chosen_index": index,
                "timings": timings,
                # What the matrix is actually built on, so a reader never
                # mistakes this for a simulator-backed number.
                "model": "analytic-one-turn",
                "opponent_model": "revealed-moves-only",
            },
        )

    def _sample(self, strategy: np.ndarray, battle: AbstractBattle) -> int:
        """Draw an action index from the mixed strategy.

        Seeded per decision rather than from a shared generator, so that
        replaying a battle reproduces its choices even if the agent played other
        battles concurrently -- which it does, since the ladder runs games in
        parallel.
        """
        # hashlib, not the builtin hash(): Python randomises string hashing per
        # process unless PYTHONHASHSEED is set, so the builtin would give the
        # same battle a different draw on every rerun -- which is exactly the
        # irreproducibility the seed exists to prevent.
        key = f"{self._seed}:{battle.battle_tag}:{battle.turn}".encode()
        seed = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        return int(rng.choice(len(strategy), p=strategy))

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
from collections.abc import Callable
from typing import Any

import numpy as np
from poke_env.battle import AbstractBattle
from poke_env.player.battle_order import BattleOrder

from champions.dex.loader import Dex, DexNotBuiltError
from champions.protocol import actions as action_describe
from champions.protocol import state as state_snapshot
from champions.search.kinds import PRIOR_WEIGHT, KindPrior, load_kind_prior, solve_columns
from champions.search.lead import PREVIEW_BUDGET_S, LeadChoice, lead_sweep
from champions.search.payoff import OpponentHypothesis, TurnModel, payoff_matrix
from champions.search.policy import (
    DEFAULT_COLUMN_K,
    DISQUALIFIED,
    HeuristicPolicy,
    PolicyProvider,
    opponent_candidates,
    widen_by_kind,
)
from champions.search.watchdog import AnytimeDecision
from champions.trace.schema import EventType

from .baseline import TracingPlayer


class OnePlyAgent(TracingPlayer):
    """Prunes, solves a matrix game, and samples the equilibrium."""

    strategy = "one-ply-equilibrium"

    #: How many of our joint actions the equilibrium is solved over. None is
    #: every legal one (D92): the one-ply payoff loop costs a few tenths of a
    #: second a turn against a 45 s budget, and the pruning guard measured the
    #: heuristic's twelve as dropping the unpruned equilibrium's mass on a
    #: third of positions. The two-ply agents keep the budget, because their
    #: second ply multiplies it.
    row_budget: int | None = None
    #: With a numeric `row_budget`, how many rows of each kind of turn the
    #: budget leaves out are added on top of it (`policy.widen_by_kind`);
    #: None adds none. Meaningless with `row_budget` None.
    extra_per_kind: int | None = None

    def __init__(
        self,
        *args: Any,
        dex: Dex | None = None,
        k: int | None | str = "class",
        extra_per_kind: int | None | str = "class",
        column_k: int = DEFAULT_COLUMN_K,
        hypothesis: OpponentHypothesis | None = None,
        policy: PolicyProvider | None = None,
        kind_prior: KindPrior | None | str = "format",
        prior_weight: float = PRIOR_WEIGHT,
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
        # Narrowed once here rather than at every use. The constructor has
        # just refused to build without it, so `self._dex` is not optional from
        # this point on and the type checker should know it.
        self.dex: Dex = self._dex
        self._k: int | None = self.row_budget if k == "class" else k  # type: ignore[assignment]
        self._extra_per_kind: int | None = (
            self.extra_per_kind if extra_per_kind == "class" else extra_per_kind  # type: ignore[assignment]
        )
        self._column_k = column_k
        # The candidate provider is swappable so that implementation B (the
        # learned prior) and C (the language model) can play through the same
        # search as A does. Defaults to the specified A, so every existing caller
        # -- and the whole M2-M6 measurement record -- is unchanged.
        self._policy = policy if policy is not None else HeuristicPolicy(self.dex)
        # The incoming Pokemon is placed on a switch (D86): the opponent's
        # moves then resolve against what is actually on the field, which is
        # what made a switch worth considering at all.
        self._model = TurnModel(self.dex, hypothesis, place_incoming=True)
        # The prior on what kind of turn the opponent plays (D91): the column
        # player's kind marginals are pinned to the corpus's rates with
        # `prior_weight`, so the equilibrium stops expecting a free Protect.
        # "format" loads the format's file (or its lineage's); None plays the
        # plain equilibrium, which is every number measured before D91.
        self._kind_prior: KindPrior | None = (
            load_kind_prior(self.dex.format_id) if kind_prior == "format" else kind_prior
        )
        self._prior_weight = prior_weight

    # -- preview --------------------------------------------------------

    #: Wall-clock budget for the lead sweep at team preview.
    preview_budget_s = PREVIEW_BUDGET_S

    def teampreview(self, battle: AbstractBattle) -> str:
        """Four and a lead from the one-turn model's own opening values (D86).

        Synchronous, as poke-env requires, and bounded by `preview_budget_s`.
        Falls back to the random preview if the sweep cannot run.
        """
        self._emit_battle_start_once(battle)
        choice = self._lead_choice(battle)
        if choice is None:
            return super().teampreview(battle)
        team = list(battle.team.values())
        for index in choice.order:
            team[index - 1]._selected_in_teampreview = True
        order = choice.as_message()
        self.trace_for(battle).emit(
            EventType.PREVIEW_DECISION,
            {
                "order": order,
                "selected": [p.species for p in battle.team.values() if p._selected_in_teampreview],
                "policy": "one-ply-lead-sweep",
                "lead": [team[i].species for i in choice.lead],
                "pair_values": {
                    f"{team[a].species}+{team[b].species}": round(v, 4)
                    for (a, b), v in choice.scores.items()
                    if v == v
                },
                "single_values": {
                    team[i].species: round(v, 4) for i, v in choice.singles.items() if v == v
                },
                "rounds": choice.rounds,
                "of_rounds": choice.of_rounds,
                "evaluated": choice.evaluated,
                "elapsed_s": round(choice.elapsed_s, 3),
                "budget_s": self.preview_budget_s,
                "model": self.payoff_model,
                "opponent_model": self.opponent_model,
                # A bring-4 model is still not what this is (D39, D56).
                "pending": ["subset_distribution", "payoff_matrix", "equilibrium_weights"],
            },
        )
        return order

    def _lead_choice(self, battle: AbstractBattle) -> LeadChoice | None:
        ours = [state_snapshot._pokemon(p, self._dex, known=True) for p in battle.team.values()]
        theirs = [
            state_snapshot._pokemon(p, self._dex, known=False)
            for p in battle.teampreview_opponent_team
        ]
        if len(ours) < 2 or len(theirs) < 2:
            return None
        key = f"{self._seed}:{battle.battle_tag}:preview".encode()
        seed = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % (2**32)
        return lead_sweep(
            ours,
            theirs,
            self.dex,
            self._turn_model(battle),
            self._policy,
            self._believed_moves(battle),
            believed_ability=self._believed_ability(battle),
            seed=seed,
            budget_s=self.preview_budget_s,
            kind_prior=self._kind_prior,
            prior_weight=self._prior_weight,
        )

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
        #
        # The snapshot is taken before pruning rather than after it, because the
        # policy needs it. Section 3's heuristic is four questions about the
        # position -- does this knock a target out, is this slot threatened,
        # does this flip a race, is this the turn Fake Out works -- and none of
        # them can be answered from the action list alone.
        started = time.perf_counter()
        snapshot = state_snapshot.snapshot(battle, self._dex)
        self._annotate_belief(battle, snapshot)
        self._annotate_unseen(battle, snapshot)
        described = [action_describe.describe(order, self._dex) for order in orders]
        by_message = {d["message"]: order for d, order in zip(described, orders, strict=True)}
        budget = self._k if self._k is not None else len(described)
        scored = self._policy.scored(described, budget, snapshot)
        # A disqualified row (friendly fire, a status move at our own partner)
        # is never worth a cell; with the whole legal set as the budget it
        # would otherwise reach the matrix.
        legal = [s for s in scored if s.score != DISQUALIFIED]
        scored = legal or scored
        if self._k is not None and self._extra_per_kind:
            ranking = self._policy.scored(described, len(described), snapshot)
            scored = widen_by_kind(ranking, scored, self._extra_per_kind)
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
        theirs = self._opponent_candidates(battle, snapshot)
        ours = [s.action for s in scored]
        matrix, estimate = await self._estimate(
            battle, snapshot, ours, theirs, decision, by_message, timings
        )
        timings["payoff_s"] = time.perf_counter() - started
        await asyncio.sleep(0)

        # -- solve ------------------------------------------------------
        started = time.perf_counter()
        equilibrium, column_prior = solve_columns(
            matrix, theirs, battle.turn, self._kind_prior, self._prior_weight
        )
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
                "row_budget": "all"
                if self._k is None
                else (
                    f"{self._k}+{self._extra_per_kind}/kind" if self._extra_per_kind else self._k
                ),
                "n_legal_joint_actions": len(orders),
                "joint": [
                    {
                        **s.action,
                        "policy_score": s.score,
                        "policy_reasons": list(s.reasons),
                        "policy_provider": self._policy.name,
                        "equilibrium_probability": float(equilibrium.row[i]),
                    }
                    for i, s in enumerate(scored)
                ],
                "opponent_joint": theirs,
                "opponent_equilibrium": [float(p) for p in equilibrium.column],
                "column_prior": column_prior,
                "payoff": matrix.tolist(),
                "game_value": float(equilibrium.value),
                "is_pure": equilibrium.is_pure,
                "support": equilibrium.support,
                "chosen_index": index,
                "timings": timings,
                # What the matrix is actually built on, so a reader never
                # mistakes this for a simulator-backed number.
                "model": self.payoff_model,
                "opponent_model": self.opponent_model,
                **estimate,
            },
        )

    # -- the payoff seam -------------------------------------------------
    #
    # The M8 arms (`champions.agents.twoply`, `champions.agents.oracle`) differ
    # from this agent in how a cell is valued and in nothing else, so that is
    # the one method they override. `docs/specs/2026-09-13-engine-gate.md`.

    #: Recorded on the trace beside `opponent_model`, so a reader can tell an
    #: analytic estimate from a two-ply or simulator-backed one.
    payoff_model = "analytic-one-turn"

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
        """The payoff matrix for this decision, plus anything the trace should
        record about how it was built. May propose to `decision` on the way, if
        it has an intermediate answer worth returning at the deadline."""
        return payoff_matrix(snapshot, ours, theirs, self._turn_model(battle)), {}

    # -- the two seams a belief plugs into -------------------------------
    #
    # Overridden by `champions.agents.belief_agent.BeliefAgent` and by the M8
    # oracle. Kept as methods rather than as constructor arguments because a
    # belief is per battle and this agent is per run: the ladder plays several
    # games concurrently through one player object.

    #: Recorded on the trace so a reader can tell which of the two produced a
    #: matrix without inferring it from the column count.
    opponent_model = "revealed-moves-only"

    def _turn_model(self, battle: AbstractBattle) -> TurnModel:
        return self._model

    def _opponent_candidates(
        self,
        battle: AbstractBattle,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return opponent_candidates(
            snapshot, self.dex, self._column_k, believed_moves=self._believed_moves(battle)
        )

    def _annotate_unseen(self, battle: AbstractBattle, snapshot: dict[str, Any]) -> None:
        """The opponent's previewed, not yet seen Pokemon go on their bench, so
        the switch columns have somewhere to switch to (D91). The trace's own
        snapshot was emitted before this and records what was observed."""
        preview = [p.species for p in getattr(battle, "teampreview_opponent_team", None) or []]
        if not preview:
            return
        state_snapshot.annotate_unseen(
            snapshot,
            preview,
            self._dex,
            believed_ability=self._believed_ability(battle),
            believed_moves=self._believed_moves(battle),
        )

    def _annotate_belief(self, battle: AbstractBattle, snapshot: dict[str, Any]) -> None:
        """Write the moves each foe is believed to have onto the search snapshot.

        The candidate policy's threat model and the column generator both read
        `believed_moves` off the view, so a move the posterior puts mass on can
        threaten a slot and appear as a column without either knowing what a
        belief is. The trace's own snapshot was emitted before this, so what
        is recorded is what was observed, not what was believed.
        """
        believed = self._believed_moves(battle)
        if believed is None:
            return
        for view in snapshot["theirs"]["active"]:
            if view is not None:
                view["believed_moves"] = list(believed(view.get("species") or ""))

    def _believed_moves(self, battle: AbstractBattle) -> Callable[[str], list[str]] | None:
        """Moves the opponent is believed to have beyond the revealed ones, as a
        callable from species, or None for the revealed-only model. Also what
        the two-ply child uses for its columns, which is why it is a seam of
        its own rather than folded into `_opponent_candidates`."""
        return None

    def _believed_ability(self, battle: AbstractBattle) -> Callable[[str], str | None] | None:
        """The ability the opponent's Pokemon is believed to carry, for the
        entry effects the lead sweep prices (a Surge terrain, Intimidate)."""
        return None

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

"""The oracle opponent, and the M8 arms that are handed it.

`docs/specs/2026-09-13-engine-gate.md` section 3 explains why the gate's
non-incumbent arms know the opponent's six registered sets: the simulator
needs a complete team to step, the belief prior is not built on this machine,
and a belief-fed arm would confound particle error with model fidelity. The
oracle is the ceiling of what any belief could supply, so a fidelity result
against it is the most fidelity can be worth, and a negative one is decisive.

What the oracle does not know is the bring and its order. Revealed Pokemon are
certain; the rest of the four are drawn, seeded per decision, from the species
not yet seen (`champions.search.rollout.team_order`). That is the same
information limit `evaluate.alive()` works under.

## Three questions, answered from a file

`BeliefHypothesis`, `BeliefEffects` and `opponent_candidates` read exactly
three things off `BattleBelief`: a point-estimate spread, a set, and the moves
a species is believed to carry. `TeamOracle` answers the same three from the
team export (`champions.belief.evaluate.truth_from_team_file`), so the oracle
arms are the belief arms with the belief swapped for the truth and no search
code of their own.

## The arms

- `OraclePlyAgent`: the one-ply agent with the oracle in both seams. The
  information control for the two headline comparisons.
- `TwoPlyOracleAgent`: `TwoPlyAgent` with the oracle. Depth, under the same
  information as the fidelity arm.
- `SimOracleAgent`: the one-ply agent whose cells are stepped by the
  simulator (`champions.search.rollout.RolloutModel`), with the oracle's team
  as the opponent. Fidelity, under the same information as the depth arm.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from poke_env.battle import AbstractBattle
from poke_env.player.battle_order import BattleOrder

from champions.agents.oneply import OnePlyAgent
from champions.agents.twoply import TwoPlyAgent
from champions.belief.evaluate import TruthSet, truth_from_team_file
from champions.belief.hypothesis import BeliefEffects, BeliefHypothesis
from champions.belief.priors import SetHypothesis
from champions.dex.loader import Dex, to_id
from champions.dex.stats import StatSpread, stats_for_species
from champions.search.oracle import SimServer
from champions.search.payoff import TurnModel
from champions.search.rollout import DEFAULT_REPLICATES, RolloutModel, export_sets
from champions.search.watchdog import AnytimeDecision


class TeamOracle:
    """A team export, answering what the belief answers: stats, set, moves."""

    def __init__(self, team: str, dex: Dex) -> None:
        self._dex = dex
        self._truth = truth_from_team_file(team)
        self._sets = export_sets(team)

    def _truth_for(self, species: str) -> TruthSet | None:
        species_id = to_id(species)
        truth = self._truth.get(species_id)
        if truth is not None:
            return truth
        entry = self._dex.species.get(species_id)
        if entry is None:
            return None
        return self._truth.get(to_id(entry.get("baseSpecies")))

    def stats_for(self, species: str) -> dict[str, int] | None:
        """Exact stats for the species as it currently stands -- a forme keeps
        its own base stats and the registered points and nature."""
        truth = self._truth_for(species)
        if truth is None or truth.points is None:
            return None
        species_id = to_id(species)
        if species_id not in self._dex.species:
            species_id = truth.species
        spread = StatSpread(points=dict(truth.points), nature=truth.nature or "hardy")
        return stats_for_species(species_id, spread, self._dex)

    def set_for(self, species: str) -> SetHypothesis | None:
        truth = self._truth_for(species)
        if truth is None:
            return None
        return SetHypothesis(
            species=truth.species,
            item=truth.item,
            ability=truth.ability,
            moves=frozenset(truth.moves),
            nature=truth.nature or "hardy",
        )

    def believed_moves(self, species: str, threshold: float = 0.0) -> list[str]:
        truth = self._truth_for(species)
        return sorted(truth.moves) if truth is not None else []

    def items(self) -> dict[str, str | None]:
        """Registered item by nickname, which is how the rollout tells a
        missing item from a consumed one."""
        out: dict[str, str | None] = {}
        for registered in self._sets:
            truth = self._truth_for(registered.species)
            out[registered.nickname] = truth.item if truth is not None else None
        return out


class OraclePlyAgent(OnePlyAgent):
    """The one ply agent, handed the opponent's registered sets."""

    strategy = "one-ply-oracle"
    opponent_model = "oracle-sets"

    def __init__(self, *args: Any, opponent_team: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._opponent_team = opponent_team
        self._oracle = TeamOracle(opponent_team, self.dex)
        self._oracle_model = TurnModel(
            self.dex,
            hypothesis=BeliefHypothesis(belief=self._oracle),
            effects=BeliefEffects(self._oracle),
        )

    @property
    def oracle(self) -> TeamOracle:
        return self._oracle

    def _turn_model(self, battle: AbstractBattle) -> TurnModel:
        return self._oracle_model

    def _believed_moves(self, battle: AbstractBattle) -> Callable[[str], list[str]] | None:
        oracle = self._oracle
        return lambda species: oracle.believed_moves(species)


class TwoPlyOracleAgent(TwoPlyAgent, OraclePlyAgent):
    """Two plies on the analytic model, with the oracle in both seams."""

    strategy = "two-ply-oracle"


class SimOracleAgent(OraclePlyAgent):
    """One ply, every cell stepped by the simulator against the oracle's team.

    One simulator process per agent, started on first use and stopped by
    `shutdown`, which the ladder calls when a matchup ends. The analytic oracle
    model is the fallback for what the simulator cannot score, and the count of
    such cells goes on the trace.
    """

    strategy = "sim-oracle"
    payoff_model = "simulator-one-turn"

    def __init__(
        self,
        *args: Any,
        opponent_team: str,
        our_team: str,
        replicates: int = DEFAULT_REPLICATES,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, opponent_team=opponent_team, **kwargs)
        self._our_team = our_team
        self._replicates = replicates
        self._sim: SimServer | None = None
        self._rollouts: dict[str, RolloutModel] = {}
        self._our_items = TeamOracle(our_team, self.dex).items()
        self._their_items = self._oracle.items()

    def _simulator(self) -> SimServer:
        if self._sim is None:
            self._sim = SimServer()
            self._sim.start()
        return self._sim

    def _rollout(self, battle: AbstractBattle) -> RolloutModel:
        tag = battle.battle_tag
        if tag not in self._rollouts:
            self._rollouts[tag] = RolloutModel(
                self._simulator(),
                self.dex,
                self.format,
                our_team=self._our_team,
                their_team=self._opponent_team,
                fallback=self._oracle_model,
                seed=self._seed or 0,
                replicates=self._replicates,
                our_items=self._our_items,
                their_items=self._their_items,
            )
        return self._rollouts[tag]

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
        model = self._rollout(battle)
        started = time.perf_counter()
        model.begin(snapshot, battle.battle_tag)
        try:
            rows: list[list[float]] = []
            for our_action in ours:
                rows.append(model.row(snapshot, our_action, theirs))
                await asyncio.sleep(0)
        finally:
            model.end()
        timings["rollout_s"] = time.perf_counter() - started
        matrix = np.array(rows, dtype=float).reshape(len(ours), len(theirs))
        return matrix, {"replicates": self._replicates, **model.stats.as_dict()}

    def _battle_finished_callback(self, battle: AbstractBattle) -> None:
        self._rollouts.pop(battle.battle_tag, None)
        super()._battle_finished_callback(battle)

    async def shutdown(self) -> None:
        if self._sim is not None:
            self._sim.stop()
            self._sim = None
        await super().shutdown()

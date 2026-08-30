"""The M5 agent: the one ply search, playing against a belief rather than a blank.

Everything structural is `OnePlyAgent`'s -- prune, estimate, solve, sample. What
changes is what the estimator is handed, at exactly the two seams
`champions/search/payoff.py` and `champions/search/policy.py` left open:

1. **Opponent stats** come from the particle filter's spread intervals instead
   of from `ASSUMED_POINTS = 32` everywhere, which describes a Pokemon that
   cannot legally exist (32 per stat against a 66 point budget) and was chosen
   only because being uniformly pessimistic is a safe bias.

2. **Items and abilities** enter the damage calculation at all. M2 modelled
   neither and measured the cost: 82% against max-base-power on a team with
   inert everything, 56% on a real one (D30). That gap is the single largest
   piece of evidence in the project about where win rate lives.

3. **The opponent's candidate columns** come from the posterior over their
   moves, not only from what they have already shown. On turn one the old model
   had a single "no action" column, so the equilibrium was an argmax against an
   opponent doing nothing -- and the game value it reported, typically 0.997,
   was unusable for the coach. `docs/STATUS.md` carries that as an open
   question with the note that inventing moves is not the fix and the belief
   filter is.

Being a subclass rather than a flag is deliberate. The two agents have to be
runnable against each other on the same team, on the same seed, in the same
process, because that head-to-head is the only measurement that says whether M5
was worth building.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import AbstractBattle

from champions.agents.oneply import OnePlyAgent
from champions.belief.hypothesis import BeliefEffects, BeliefHypothesis
from champions.belief.priors import PriorNotBuiltError
from champions.search.payoff import TurnModel
from champions.search.policy import opponent_candidates

#: Posterior mass a move needs before it becomes a column of the matrix. Low,
#: because a column costs one payoff evaluation and a missing column costs the
#: equilibrium the ability to see the action at all -- the asymmetry that made
#: the revealed-moves-only model degenerate.
MOVE_THRESHOLD = 0.15


class BeliefAgent(OnePlyAgent):
    """One ply equilibrium against the belief filter's posterior."""

    strategy = "one-ply-belief"
    opponent_model = "belief-particles"

    def __init__(self, *args: Any, move_threshold: float = MOVE_THRESHOLD, **kwargs: Any) -> None:
        kwargs.setdefault("belief", True)
        super().__init__(*args, **kwargs)
        if not self._belief_enabled:
            raise PriorNotBuiltError(
                f"{type(self).__name__} plays against the belief filter, which needs the "
                f"set prior built from the replay corpus. Build it with:\n"
                f"    python scripts/build_priors.py\n"
                f"(and `make scrape` first if there is no corpus yet)."
            )
        self._move_threshold = move_threshold
        self._models: dict[str, TurnModel] = {}

    def _turn_model(self, battle: AbstractBattle) -> TurnModel:
        """One model per battle, holding that battle's belief.

        Cached per battle rather than rebuilt per decision because the model is
        stateless with respect to the turn -- it reads the belief object, which
        updates in place -- and because the ladder runs games concurrently
        through one player, so a single shared model would be reading another
        game's opponent.
        """
        belief = self.belief_for(battle)
        if belief is None:
            return self._model
        tag = battle.battle_tag
        if tag not in self._models:
            self._models[tag] = TurnModel(
                self.dex,
                hypothesis=BeliefHypothesis(belief=belief),
                effects=BeliefEffects(belief),
            )
        return self._models[tag]

    def _opponent_candidates(
        self,
        battle: AbstractBattle,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        belief = self.belief_for(battle)
        return opponent_candidates(
            snapshot,
            self.dex,
            self._k,
            believed_moves=None
            if belief is None
            else (lambda species: belief.believed_moves(species, self._move_threshold)),
        )

    def _battle_finished_callback(self, battle: AbstractBattle) -> None:
        self._models.pop(battle.battle_tag, None)
        super()._battle_finished_callback(battle)


class BeliefStatsOnly(BeliefAgent):
    """Ablation: believed stats and effects, but the M2 opponent action model.

    M5 changes two things at once, and one head-to-head cannot say which one
    moved the win rate. This arm keeps the belief's stats, items and abilities
    and reverts the columns to revealed moves only.
    """

    strategy = "one-ply-belief-stats"
    opponent_model = "belief-stats-revealed-moves"

    def _opponent_candidates(
        self,
        battle: AbstractBattle,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return opponent_candidates(snapshot, self.dex, self._k)


class BeliefMovesOnly(BeliefAgent):
    """Ablation: believed action columns, but the M2 constant opponent stats.

    The other half. Together with `BeliefStatsOnly` and the M2 agent these three
    numbers decompose the difference the belief makes.
    """

    strategy = "one-ply-belief-moves"
    opponent_model = "belief-moves-constant-stats"

    def _turn_model(self, battle: AbstractBattle) -> TurnModel:
        return self._model

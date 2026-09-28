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

from collections.abc import Callable
from typing import Any

from poke_env.battle import AbstractBattle

from champions.agents.adaptive import AdaptiveAgent
from champions.agents.oneply import OnePlyAgent
from champions.belief.filter import MINIMUM_MOVES
from champions.belief.hypothesis import BeliefEffects, BeliefHypothesis
from champions.belief.priors import PriorNotBuiltError
from champions.search.payoff import TurnModel
from champions.search.policy import DEFAULT_K

#: Posterior mass a move needs before it becomes a column of the matrix. Low,
#: because a column costs one payoff evaluation and a missing column costs the
#: equilibrium the ability to see the action at all -- the asymmetry that made
#: the revealed-moves-only model degenerate.
#: Probability a move needs before the columns offer it. A probability that the
#: move is in the opponent's set, which is what the marginal reports since D99;
#: before that it reported that quantity divided by four, so 0.15 silently meant
#: 0.6 and the columns were offered a single move in 47% of live positions.
#: Measured on 5,994 ladder belief snapshots against the moves those Pokemon
#: turned out to use: 0.6 offers 3.3 moves at 85% recall, 0.4 offers 4.2 at 90%,
#: 0.2 offers 5.3 at 93%, 0.08 offers 6.5 at 95%. 0.2 is the knee, and
#: `filter.MINIMUM_MOVES` floors it at four.
MOVE_THRESHOLD = 0.20


class BeliefAgent(OnePlyAgent):
    """One ply equilibrium against the belief filter's posterior."""

    strategy = "one-ply-belief"
    opponent_model = "belief-particles"

    #: Moves offered per opponent Pokemon even when none clears the threshold.
    #: `BeliefNarrowAgent` sets it to 0 to reproduce the pre-D99 behaviour.
    move_minimum: int = MINIMUM_MOVES

    def __init__(
        self,
        *args: Any,
        move_threshold: float = MOVE_THRESHOLD,
        move_minimum: int | None = None,
        **kwargs: Any,
    ) -> None:
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
        self._move_minimum = self.move_minimum if move_minimum is None else move_minimum
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
                place_incoming=True,
            )
        return self._models[tag]

    def _believed_ability(self, battle: AbstractBattle) -> Callable[[str], str | None] | None:
        belief = self.belief_for(battle)
        if belief is None:
            return None

        def ability(species: str) -> str | None:
            hypothesis = belief.set_for(species)
            return hypothesis.ability if hypothesis is not None else None

        return ability

    def _believed_moves(self, battle: AbstractBattle) -> Callable[[str], list[str]] | None:
        belief = self.belief_for(battle)
        if belief is None:
            return None
        return lambda species: belief.believed_moves(
            species, self._move_threshold, self._move_minimum
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

    def _believed_moves(self, battle: AbstractBattle) -> Callable[[str], list[str]] | None:
        return None


class BeliefMovesOnly(BeliefAgent):
    """Ablation: believed action columns, but the M2 constant opponent stats.

    The other half. Together with `BeliefStatsOnly` and the M2 agent these three
    numbers decompose the difference the belief makes.
    """

    strategy = "one-ply-belief-moves"
    opponent_model = "belief-moves-constant-stats"

    def _turn_model(self, battle: AbstractBattle) -> TurnModel:
        return self._model


class BeliefNarrowAgent(BeliefAgent):
    """The pre-D99 move rule, kept as the ablation arm.

    Before D99 the move marginal reported P(move in set) / 4, so the shipped
    threshold of 0.15 meant P >= 0.60, and there was no floor. Against
    open-sheet ground truth that offered 3.34 moves per species at 73.0% move
    recall, with the opponent's whole four-move set available to the column
    generator in 26.2% of positions; the corrected rule offers 5.23 at 90.0%
    and 66.0%. This arm reproduces the old behaviour so the difference can be
    played rather than argued.
    """

    strategy = "one-ply-belief-narrow"
    move_minimum = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("move_threshold", 0.60)
        super().__init__(*args, **kwargs)


class WideBeliefAgent(BeliefAgent):
    """The belief agent on the heuristic's twelve rows plus the best two rows of
    every kind of turn the twelve leave out (`policy.widen_by_kind`): the
    bounded alternative to D92's every-legal-row, for the A/B D94 asked for."""

    strategy = "one-ply-belief-wide"
    row_budget: int | None = DEFAULT_K
    extra_per_kind: int | None = 2


class AdaptiveBeliefAgent(BeliefAgent, AdaptiveAgent):
    """The clock-allocated, escalating agent (M11) on the belief's seams (D87).

    `BeliefAgent` supplies `_turn_model`, `_believed_moves` and
    `_believed_ability`; `AdaptiveAgent` supplies the budget and the second
    ply on close positions, and builds its two-ply model from those same
    seams. Nothing here but the order of the bases: the belief's overrides
    have to win, and the adaptive agent's `_estimate` has to be the one that
    runs. M8 measured the extra ply as not apart under a model that gave a
    status move no effect; this is the arm that re-measures it under D85's.
    """

    strategy = "adaptive-belief"
    payoff_model = "analytic-adaptive"
    opponent_model = "belief-particles"

    def _battle_finished_callback(self, battle: AbstractBattle) -> None:
        self._two_ply.pop(battle.battle_tag, None)
        super()._battle_finished_callback(battle)

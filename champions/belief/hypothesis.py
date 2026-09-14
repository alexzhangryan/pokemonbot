"""The belief, plugged into the search layer's two seams.

`champions/search/payoff.py` was written with the swap in mind: `M5 swaps the
estimator for particles over hypothesised teams without the surrounding search
changing. OpponentHypothesis is the seam that swap happens at.` This module is
the other side of that seam, plus the second one M5 added -- `EffectsProvider`,
which supplies the item and ability multipliers M2 measured the absence of.

Both read the belief and nothing else. The search layer has no import of
`champions.belief` in either direction, so the M2 agent still runs with no
corpus, no prior artifact and no particles, and the difference between the two
agents is what is passed into `TurnModel` rather than which code path runs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from champions.belief import effects as effect_table
from champions.belief.priors import SetHypothesis
from champions.dex.damage import TypeChart
from champions.dex.loader import to_id
from champions.search.payoff import ASSUMED_POINTS, OpponentHypothesis


class SetSource(Protocol):
    """What the two seams below read: a point-estimate spread and a set per
    species. `champions.belief.filter.BattleBelief` is the designed source; the
    M8 oracle (`champions.agents.oracle.TeamOracle`) answers the same two
    questions from the registered team, which is how the gate hands the
    payoff model the truth without the belief in the loop."""

    def stats_for(self, species: str) -> dict[str, int] | None: ...  # pragma: no cover

    def set_for(self, species: str) -> SetHypothesis | None: ...  # pragma: no cover


@dataclass(frozen=True, eq=False)
class BeliefHypothesis(OpponentHypothesis):
    """Opponent stats from the particle filter, falling back to the constant.

    The fallback is not decoration. A species the filter has no particle for --
    one that appeared without ever being previewed, or one whose every
    hypothesis was eliminated -- must still produce six numbers, and the
    pessimistic constant is the right thing to produce: it assumes the opponent
    is fast, strong and bulky at once, which is a safe bias for a search and a
    much better failure mode than a plausible-looking invented spread.
    """

    belief: SetSource | None = None
    points: int = ASSUMED_POINTS

    def stats_for(self, view: dict[str, Any]) -> dict[str, int]:
        if self.belief is not None:
            stats = self.belief.stats_for(to_id(view.get("species")))
            if stats is not None:
                return stats
        return super().stats_for(view)


class BeliefEffects:
    """Item and ability multipliers for both sides of a hit.

    Ours are read from the snapshot, which knows them exactly. Theirs are the
    highest-weight particle's hypothesis, which is a guess -- but a guess drawn
    from 50,000 registered sets and filtered by everything the battle has
    revealed, which is a different thing from the 1.0 the M2 model used.

    The modal particle rather than an expectation over particles, on purpose.
    Averaging Life Orb and Focus Sash produces a set nobody registered, and the
    payoff model is already an expectation over damage rolls -- stacking a
    second expectation inside a cell would blur exactly the discontinuities
    (a knockout, a Sash survival) that the cell exists to distinguish.
    """

    def __init__(self, belief: SetSource | None) -> None:
        self._belief = belief

    def attacker(
        self,
        view: dict[str, Any],
        move: dict[str, Any],
        defender_types: list[str],
        chart: TypeChart,
    ) -> effect_table.SetEffects:
        hypothesis = self._set_for(view)
        return effect_table.attacker_effects(
            hypothesis,
            {**move, "_attacker_types": view.get("types") or []},
            defender_types,
            chart,
            statused=bool(view.get("status")),
        )

    def defender(
        self,
        view: dict[str, Any],
        move: dict[str, Any],
        defender_types: list[str],
        chart: TypeChart,
    ) -> effect_table.SetEffects:
        hypothesis = self._set_for(view)
        return effect_table.defender_effects(
            hypothesis,
            move,
            defender_types,
            chart,
            at_full_hp=float(view.get("hp_pct") or 0.0) >= 100.0,
        )

    def _set_for(self, view: dict[str, Any]) -> SetHypothesis | None:
        if view.get("known"):
            return SetHypothesis(
                species=to_id(view.get("species")),
                item=to_id(view.get("item")) or None,
                ability=to_id(view.get("ability")) or None,
                moves=frozenset(to_id(m.get("id")) for m in view.get("moves") or []),
                nature="hardy",
            )
        hypothesis = (
            self._belief.set_for(to_id(view.get("species"))) if self._belief is not None else None
        )
        # What the battle has shown outranks what the prior guessed: a Mega
        # Gardevoir's Pixilate is on the view from the turn it evolves, while
        # the particle still carries the base forme's Trace (D87).
        ability = to_id(view.get("ability")) or None
        item = to_id(view.get("item")) or None
        if hypothesis is None:
            if ability or item:
                return SetHypothesis(
                    species=to_id(view.get("species")),
                    item=item,
                    ability=ability,
                    moves=frozenset(),
                    nature="hardy",
                )
            return None
        if (ability and ability != hypothesis.ability) or (item and item != hypothesis.item):
            return replace(
                hypothesis, ability=ability or hypothesis.ability, item=item or hypothesis.item
            )
        return hypothesis

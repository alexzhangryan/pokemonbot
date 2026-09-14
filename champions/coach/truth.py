"""A `SetSource` over a truth table, for the coach's information states.

`docs/specs/2026-09-13-coach.md` section 2. The analysis needs the opponent's
hidden sets in two situations: after the fact, when a mirror match's team file
says what the opponent registered, and before the fact on the Bo3 ladder,
where Open Team Sheets showed the human the opposing sets and so they were
knowable. Both arrive as `dict[str, TruthSet]` -- `truth_from_team_file` and
`truth_from_replay` respectively -- and this is the one object that turns
either into what the payoff model's two seams read.

`champions.agents.oracle.TeamOracle` does the same over a team export; this
does it over the truth table itself, so an open sheet (which carries no stat
points, D33) is the same kind of thing to the analysis as a team file (which
does). Without points `stats_for` returns None and `BeliefHypothesis` falls
back to its pessimistic constant, which is that seam's documented behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping

from champions.belief.evaluate import TruthSet
from champions.belief.priors import SetHypothesis, SetPrior
from champions.dex.loader import Dex, to_id
from champions.dex.stats import StatSpread, stats_for_species


class TruthOracle:
    """Stats, set and moves for a species, from a table of registered sets."""

    def __init__(self, truths: Mapping[str, TruthSet], dex: Dex) -> None:
        self._dex = dex
        self._truths = {to_id(k): v for k, v in truths.items()}

    def __len__(self) -> int:
        return len(self._truths)

    def _truth_for(self, species: str) -> TruthSet | None:
        species_id = to_id(species)
        truth = self._truths.get(species_id)
        if truth is not None:
            return truth
        # A forme (Mega, Stance Change) is registered under its base name.
        entry = self._dex.species.get(species_id)
        if entry is None:
            return None
        return self._truths.get(to_id(entry.get("baseSpecies")))

    def stats_for(self, species: str) -> dict[str, int] | None:
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


#: Posterior-free mass a move needs in the prior before the coach treats it as
#: a column: the belief agent's own threshold (`BeliefAgent.MOVE_THRESHOLD`).
PRIOR_MOVE_THRESHOLD = 0.15
#: Moves per species the prior contributes, most common first.
PRIOR_MOVES = 6


class PriorSource:
    """A `SetSource` over the corpus prior alone: what a reader who knew the
    metagame but nothing about this opponent would assume (D85).

    Between the two states the spec names -- revealed moves only, and the
    open sheet -- there is the one the belief agent actually plays under, and
    a review that solves against "nothing" on turn one cannot say whether
    the agent's turn-one choice was right. No filtering: the prior's most
    common registered set for the species and its move frequencies, with
    revealed moves supplied by the caller as they always were. Stats come
    back None, so `BeliefHypothesis` falls back to its constant.
    """

    def __init__(self, prior: SetPrior, dex: Dex) -> None:
        self._prior = prior
        self._dex = dex

    def _species_id(self, species: str) -> str:
        species_id = to_id(species)
        if self._prior.knows(species_id):
            return species_id
        entry = self._dex.species.get(species_id)
        base = to_id((entry or {}).get("baseSpecies"))
        return base if base and self._prior.knows(base) else species_id

    def stats_for(self, species: str) -> dict[str, int] | None:
        return None

    def set_for(self, species: str) -> SetHypothesis | None:
        observed = self._prior.observed_sets(self._species_id(species))
        return observed[0][0] if observed else None

    def believed_moves(self, species: str, threshold: float = PRIOR_MOVE_THRESHOLD) -> list[str]:
        species_id = self._species_id(species)
        entry = self._prior.species.get(species_id)
        if entry is None or not entry.count:
            return []
        ranked = sorted(entry.moves.items(), key=lambda kv: (-kv[1], kv[0]))
        return [m for m, n in ranked if n / entry.count >= threshold][:PRIOR_MOVES]

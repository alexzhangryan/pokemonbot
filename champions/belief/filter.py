"""The belief filter as one object a battle can hold.

`spreads.py` maintains intervals, `particles.py` maintains hypotheses,
`evidence.py` turns protocol into inequalities, `priors.py` says what is likely
before anything is seen. This is the seam that assembles them and the only thing
an agent needs to know about.

Two things it deliberately does not do.

It does not parse. `champions/protocol/parser.py` already produces the
observation stream, live and offline, from the same code (D32), and the agent
already runs it to emit `turn_result`. The filter consumes that stream rather
than re-reading the log, which is what stops the offline evaluation and the live
filter from drifting apart.

And it does not touch poke-env. It reads the trace snapshot -- the same plain
JSON the viewer renders and the search layer scores -- so an evaluation replaying
a stored battle supplies exactly what a live battle does. That is the shape
`champions/search/evaluate.py`, `payoff.py` and `policy.py` already have, and
`docs/STATUS.md` flags confirming it as the intended architecture rather than an
accident; this module is another vote for it being intended.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np

from champions.belief.evidence import EvidenceBuilder
from champions.belief.particles import (
    DEFAULT_PARTICLES,
    BeliefContext,
    ParticleFilter,
)
from champions.belief.priors import SetHypothesis, SetPrior
from champions.dex.loader import Dex, to_id
from champions.dex.stats import STAT_IDS
from champions.protocol.parser import Observation

SLOT_LETTERS = ("a", "b")


class BattleBelief:
    """One battle's belief about the opponent's six.

    Constructed at team preview, when Champions has revealed six species and
    nothing else, and updated once per decision from the observations the agent
    already parses.
    """

    def __init__(
        self,
        dex: Dex,
        prior: SetPrior,
        opponent_species: Sequence[str],
        player_role: str,
        n_particles: int = DEFAULT_PARTICLES,
        seed: int | None = None,
    ) -> None:
        self.dex = dex
        self.player_role = player_role or "p1"
        self.opponent_side = "p2" if self.player_role == "p1" else "p1"
        self.evidence = EvidenceBuilder(dex)
        self.particles = ParticleFilter(
            dex=dex,
            prior=prior,
            species=list(opponent_species),
            n_particles=n_particles,
            rng=np.random.default_rng(seed if seed is not None else 0),
        )
        self.turns_observed = 0
        self.last_evidence: list[dict[str, Any]] = []

    # -- updating -------------------------------------------------------

    def update(self, observations: Iterable[Observation], snapshot: Mapping[str, Any]) -> None:
        """Fold one decision point's observations into the belief."""
        evidence = self.evidence.feed(observations)
        context = self._context(snapshot)
        self.particles.observe(evidence, context)
        self.turns_observed += 1
        self.last_evidence = [e.as_dict() for e in evidence]

    def add_species(self, species: Iterable[str]) -> None:
        """Register species that appear after preview.

        Preview shows all six in this format, so this is defensive rather than
        load-bearing -- but a species the filter has never heard of would
        otherwise have no particle entry at all, and the payoff model would
        silently fall back to the constant hypothesis for it.
        """
        added = [to_id(s) for s in species if to_id(s) and to_id(s) not in self.particles.species]
        if not added:
            return
        self.particles.species.extend(added)
        for species_id in added:
            self.particles.constraints.note_species(species_id)
        self.particles.reseed()

    # -- reading --------------------------------------------------------

    def stats_for(self, species: str) -> dict[str, int] | None:
        """Point-estimate stats for one opponent Pokemon.

        The midpoints of the highest-weight particle's interval box, which is a
        jointly plausible spread rather than six independently plausible stats.
        Returns None when the species is not in the belief at all, so the caller
        can fall back rather than be handed invented numbers.
        """
        spread = self.particles.expected_spread(species)
        return spread.stats() if spread is not None else None

    def set_for(self, species: str) -> SetHypothesis | None:
        return self.particles.most_likely(species)

    def believed_moves(self, species: str, threshold: float = 0.15) -> list[str]:
        """Moves carrying at least `threshold` posterior mass.

        This is what replaces "their revealed moves, and nothing if they have
        revealed none". `docs/STATUS.md` records that degeneracy as an open
        question: on turn one the opponent's candidate set is empty, the matrix
        has a single column, and the equilibrium collapses to an argmax against
        an opponent doing nothing. The answer was never to invent moves -- it
        was to have a prior over them, which is this.
        """
        marginals = self.particles.marginals(species)
        return [entry["value"] for entry in marginals["moves"] if entry["probability"] >= threshold]

    def summary(self) -> dict[str, Any]:
        """The `belief` trace event payload."""
        return {
            **self.particles.summary(),
            "turns_observed": self.turns_observed,
            "evidence": self.last_evidence,
            "opponent_side": self.opponent_side,
        }

    # -- our own side ---------------------------------------------------

    def _context(self, snapshot: Mapping[str, Any]) -> BeliefContext:
        """Our half of the battle, as `particles.py` needs to read it.

        Every number here is exact. That asymmetry is the whole reason the
        filter works: an ordering or a damage figure is only an inequality about
        their stats because our side of it is known.
        """
        ours_by_slot: dict[str, dict[str, Any]] = {}
        our_sets: dict[str, SetHypothesis] = {}
        active = (snapshot.get("ours") or {}).get("active") or []
        for index, pokemon in enumerate(active):
            if pokemon is None or index >= len(SLOT_LETTERS):
                continue
            slot = f"{self.player_role}{SLOT_LETTERS[index]}"
            ours_by_slot[slot] = dict(pokemon)
            our_sets[slot] = _our_set(pokemon)

        tailwind = set()
        if "TAILWIND" in (snapshot.get("side_conditions") or {}):
            tailwind.add(self.player_role)
        if "TAILWIND" in (snapshot.get("opponent_side_conditions") or {}):
            tailwind.add(self.opponent_side)

        return BeliefContext(
            opponent_side=self.opponent_side,
            ours_by_slot=ours_by_slot,
            our_sets=our_sets,
            boosts={slot: dict(p.get("boosts") or {}) for slot, p in ours_by_slot.items()},
            tailwind_sides=frozenset(tailwind),
        )


def _our_set(pokemon: Mapping[str, Any]) -> SetHypothesis:
    """One of our own Pokemon as a `SetHypothesis`, so the effects table can read it.

    The nature is not in the snapshot and is not needed: our stats are exact, so
    nothing here has to derive them. It is filled with the neutral nature rather
    than left out, because `SetHypothesis` is also the prior's currency and a
    None there would read as "unknown" instead of "irrelevant".
    """
    return SetHypothesis(
        species=to_id(pokemon.get("species")),
        item=to_id(pokemon.get("item")) or None,
        ability=to_id(pokemon.get("ability")) or None,
        moves=frozenset(to_id(m.get("id")) for m in pokemon.get("moves") or []),
        nature="hardy",
    )


def true_points(stats: Mapping[str, int], base_stats: Mapping[str, int]) -> dict[str, int]:
    """Recover a point allocation from exact stats and base stats.

    Only correct for a neutral nature, which is why it is used solely by the
    coverage measurement in `champions/belief/evaluate.py` against teams whose
    files we hold. Here rather than in `evaluate.py` because `spreads.py`'s
    inverse belongs beside the forward direction it inverts.
    """
    points: dict[str, int] = {}
    for stat in STAT_IDS:
        base = int(base_stats[stat])
        offset = 75 if stat == "hp" else 20
        points[stat] = max(0, int(stats[stat]) - base - offset)
    return points

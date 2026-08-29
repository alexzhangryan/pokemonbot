"""Features for the preview models, computed from species names and the dex.

The hard constraint is stated in `dataset.py` and repeated here because this is
where it would be violated: at team preview in Champions the agent knows six
names per side and nothing else. Every feature below is a function of those
twelve names and the resolved dex. Nothing reads an item, an ability, a move or
a nature, even though the corpus has all four for open-sheet games.

Two feature families, for different reasons.

Species identity is a one-hot over the species seen often enough in training to
estimate anything about. It carries the marginal that dominates: Pelipper is
brought 81% of the time and Scizor 39%, and no matchup reasoning is needed to
know that.

Matchup features are dense and shared across species, so they keep working for
the long tail that the one-hot cannot reach. They are deliberately few. With a
few thousand examples and 228 species, species-by-species interactions are not
estimable, and pretending otherwise would fit noise.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from champions.dex.damage import TypeChart
from champions.dex.loader import Dex

#: Dense feature names, in the order they are emitted. Named so a fitted model
#: can be read rather than merely used.
DENSE_FEATURES = (
    "bias",
    "offense_vs_their_team",
    "defense_vs_their_team",
    "speed_rank_in_matchup",
    "speed_rank_on_own_team",
    "base_stat_total",
    "is_mega_capable",
)


@dataclass(frozen=True)
class FeatureSpace:
    """Species vocabulary plus the dex lookups the dense features need.

    The vocabulary is built from training data only. A species below the count
    threshold, or absent entirely, gets no indicator and is described by its
    dense features alone -- which is the correct behaviour at play time too,
    where an unfamiliar lead is exactly the case that must not crash.
    """

    dex: Dex
    chart: TypeChart
    vocabulary: tuple[str, ...]
    index: dict[str, int]

    @classmethod
    def build(cls, dex: Dex, species_counts: dict[str, int], min_count: int = 25) -> FeatureSpace:
        vocabulary = tuple(sorted(s for s, n in species_counts.items() if n >= min_count))
        return cls(
            dex=dex,
            chart=TypeChart.from_dex(dex),
            vocabulary=vocabulary,
            index={s: i for i, s in enumerate(vocabulary)},
        )

    @property
    def width(self) -> int:
        return len(DENSE_FEATURES) + len(self.vocabulary)

    def names(self) -> tuple[str, ...]:
        return DENSE_FEATURES + tuple(f"species:{s}" for s in self.vocabulary)

    # -- dex lookups ------------------------------------------------------

    def _entry(self, species: str) -> dict:
        return self.dex.species.get(species, {})

    def types(self, species: str) -> list[str]:
        return list(self._entry(species).get("types", []))

    def base_stats(self, species: str) -> dict[str, int]:
        return dict(self._entry(species).get("baseStats", {}))

    def speed(self, species: str) -> int:
        return int(self.base_stats(species).get("spe", 0))

    def stat_total(self, species: str) -> int:
        return sum(int(v) for v in self.base_stats(species).values())

    def has_mega(self, species: str) -> bool:
        """Whether a Mega forme exists for this species.

        Mega Evolution is back in Champions and the slot is a real constraint --
        one per team -- so carrying a Mega-capable Pokemon changes what else can
        be brought alongside it.
        """
        entry = self._entry(species)
        base = (entry.get("baseSpecies") or entry.get("name") or species).lower().replace(" ", "")
        return any(key.startswith(base) and "mega" in key[len(base) :] for key in self.dex.species)

    # -- feature construction ---------------------------------------------

    def _offense(self, species: str, opponents: Sequence[str]) -> float:
        """Mean over the opposing six of this Pokemon's best type matchup into it.

        Stages rather than multipliers, matching `TypeChart`: +1 per super
        effective type, -1 per resistance. Best-of rather than mean-of over our
        own types, because an attacker picks its move.
        """
        if not opponents:
            return 0.0
        mine = self.types(species)
        if not mine:
            return 0.0
        scores = []
        for other in opponents:
            defending = self.types(other)
            if not defending:
                continue
            scores.append(max(self.chart.effectiveness(t, defending) for t in mine))
        return float(np.mean(scores)) if scores else 0.0

    def _defense(self, species: str, opponents: Sequence[str]) -> float:
        """Mean over the opposing six of their best type matchup into this one.

        Negated so that larger is better for us, keeping every dense feature
        oriented the same way -- a sign flip buried in one feature is the kind
        of thing that reads as a model failing to learn.
        """
        if not opponents:
            return 0.0
        defending = self.types(species)
        if not defending:
            return 0.0
        scores = []
        for other in opponents:
            attacking = self.types(other)
            if not attacking:
                continue
            scores.append(max(self.chart.effectiveness(t, defending) for t in attacking))
        return -float(np.mean(scores)) if scores else 0.0

    @staticmethod
    def _rank(value: float, population: Sequence[float]) -> float:
        """Fraction of the population this value beats, in [0, 1]."""
        if not population:
            return 0.5
        return float(sum(1 for other in population if value > other) / len(population))

    def row(self, species: str, team: Sequence[str], opponents: Sequence[str]) -> np.ndarray:
        """The feature vector for one Pokemon in one matchup."""
        vector = np.zeros(self.width, dtype=float)
        speeds_all = [self.speed(s) for s in list(team) + list(opponents)]
        speeds_own = [self.speed(s) for s in team]
        vector[0] = 1.0
        vector[1] = self._offense(species, opponents)
        vector[2] = self._defense(species, opponents)
        vector[3] = self._rank(self.speed(species), speeds_all)
        vector[4] = self._rank(self.speed(species), speeds_own)
        vector[5] = (self.stat_total(species) - 500.0) / 100.0
        vector[6] = 1.0 if self.has_mega(species) else 0.0
        slot = self.index.get(species)
        if slot is not None:
            vector[len(DENSE_FEATURES) + slot] = 1.0
        return vector

    def matrix(self, team: Sequence[str], opponents: Sequence[str]) -> np.ndarray:
        """One row per Pokemon on `team`, in team order."""
        return np.stack([self.row(s, team, opponents) for s in team])

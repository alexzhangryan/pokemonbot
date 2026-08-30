"""The spread half of the belief filter: exact interval propagation over points.

`docs/03-belief-filter.md` section 2. The naive representation -- a distribution
over spreads -- is forbidden by the space: integer solutions to `sum(p) <= 66`
with `0 <= p_i <= 32` over six stats number 136,663,185, and times 25 natures
that is 2.5e8 hypotheses for a single Pokemon.

We never need the spread. We need the six derived stats, and in Champions those
are affine in the points:

    HP   = base + p + 75
    stat = (base + p + 20) * nature

so every observation lands as a linear inequality in `p`, and the feasible
region is a box intersected with one resource constraint. For a single resource
constraint over box-bounded variables the tightened upper bound is exact in
closed form:

    u_i <- min(u_i, 32, 66 - sum_{j != i} l_j)

which is a handful of integer operations. No linear program, no approximation.

## Why the inversion is a scan and not algebra

`compute_stat` is monotone non-decreasing in the points, and the domain is 33
integers. So "the smallest point value whose stat reaches v" is found by walking
the 33 values through the same function the simulator agrees with, rather than
by rearranging the formula by hand. Rearranging would reintroduce exactly the
16-bit truncation step (`trunc(trunc(stat * 110, 16) / 100)`) that M1 exists to
have transcribed once and checked -- a second, inverted transcription of it is a
second thing to be silently wrong about.

## Bounds are soft on purpose

Opponent HP arrives quantized to percent, so a damage observation carries about
+/- 0.5% of maximum HP of error and it compounds across chained inferences
(`CLAUDE.md` constraint 5). Every observation here therefore takes an explicit
tolerance, and the callers that derive bounds from damage widen before they
narrow. `docs/03` section 5 names interval coverage as the metric that catches
this being wrong; `champions/belief/evaluate.py` measures it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from champions.dex.stats import (
    MAX_POINTS_PER_STAT,
    MAX_POINTS_TOTAL,
    STAT_IDS,
    compute_stat,
)

#: Every legal point value for one stat. Small enough that inverting a monotone
#: function over it by scanning is both exact and free.
POINT_VALUES = tuple(range(MAX_POINTS_PER_STAT + 1))


@dataclass
class SpreadBelief:
    """Bounds on one Pokemon's six point allocations, given a nature.

    The nature is fixed rather than marginalised because it is carried by the
    particle that owns this belief. D33: open team sheets label the nature on
    every set in the corpus, so a nature is a *learnable* attribute drawn from
    the prior alongside item, ability and moves, and interval propagation is
    left with stat points alone -- which is a strictly smaller and better
    conditioned problem than `docs/03` assumed when it proposed 25 nature
    hypotheses each carrying their own interval set.
    """

    base_stats: Mapping[str, int]
    nature: str
    nature_entry: Mapping[str, Any]
    lower: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STAT_IDS, 0))
    upper: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(STAT_IDS, MAX_POINTS_PER_STAT)
    )
    #: False once an observation has emptied the feasible region. A flag rather
    #: than an exception, because the caller's response is to down-weight the
    #: particle, not to abort the battle.
    feasible: bool = True

    def __post_init__(self) -> None:
        self.lower = {s: int(self.lower.get(s, 0)) for s in STAT_IDS}
        self.upper = {s: int(self.upper.get(s, MAX_POINTS_PER_STAT)) for s in STAT_IDS}
        self.tighten()

    # -- construction ---------------------------------------------------

    @classmethod
    def unconstrained(
        cls,
        base_stats: Mapping[str, int],
        nature: str,
        nature_entry: Mapping[str, Any],
    ) -> SpreadBelief:
        return cls(base_stats=dict(base_stats), nature=nature, nature_entry=dict(nature_entry))

    def copy(self) -> SpreadBelief:
        clone = SpreadBelief.__new__(SpreadBelief)
        clone.base_stats = self.base_stats
        clone.nature = self.nature
        clone.nature_entry = self.nature_entry
        clone.lower = dict(self.lower)
        clone.upper = dict(self.upper)
        clone.feasible = self.feasible
        return clone

    # -- the closed-form propagation ------------------------------------

    def tighten(self) -> bool:
        """Apply the resource constraint. Returns whether the region is non-empty.

        The only coupling between the six stats is `sum(p) <= 66`, so spending
        the minimum everywhere else caps what any one stat can hold. Exact for a
        single resource constraint over a box; not a relaxation.
        """
        total_lower = sum(self.lower.values())
        if total_lower > MAX_POINTS_TOTAL:
            self.feasible = False
            return False
        for stat_id in STAT_IDS:
            slack = MAX_POINTS_TOTAL - (total_lower - self.lower[stat_id])
            capped = min(self.upper[stat_id], MAX_POINTS_PER_STAT, slack)
            if capped < self.lower[stat_id]:
                self.feasible = False
                return False
            self.upper[stat_id] = capped
        return True

    def constrain_points(
        self,
        stat_id: str,
        low: int | None = None,
        high: int | None = None,
    ) -> bool:
        """Intersect one stat's point interval with `[low, high]`."""
        if low is not None:
            self.lower[stat_id] = max(self.lower[stat_id], int(low))
        if high is not None:
            self.upper[stat_id] = min(self.upper[stat_id], int(high))
        if self.lower[stat_id] > self.upper[stat_id]:
            self.feasible = False
            return False
        return self.tighten()

    # -- reading it back ------------------------------------------------

    def stat_at(
        self,
        stat_id: str,
        points: int,
        base_stats: Mapping[str, int] | None = None,
    ) -> int:
        """One derived stat at one point value.

        `base_stats` overrides the species' own, which is how a Mega Evolution
        is handled: the points belong to the set and do not change, the base
        stats they are added to do, and the belief keeps one interval box per
        Pokemon rather than one per forme it might take.
        """
        table = base_stats if base_stats is not None else self.base_stats
        return compute_stat(stat_id, int(table[stat_id]), int(points), self.nature_entry)

    def stat_bounds(self, stat_id: str) -> tuple[int, int]:
        """The interval the derived stat can lie in, given the point interval.

        Monotone in the points, so the endpoints map to the endpoints.
        """
        return (
            self.stat_at(stat_id, self.lower[stat_id]),
            self.stat_at(stat_id, self.upper[stat_id]),
        )

    def point_estimate(self, stat_id: str) -> int:
        """The midpoint of one stat's interval, read in isolation.

        Correct for one stat and wrong for six: six independent midpoints can
        sum well past 66, which is a Pokemon that cannot exist. Use
        `allocation()` for anything that needs a whole spread.
        """
        return (self.lower[stat_id] + self.upper[stat_id]) // 2

    def allocation(self) -> dict[str, int]:
        """One legal point allocation inside the box. The whole-spread estimate.

        Six independent midpoints are not a spread: unconstrained, each is 16
        and the six sum to 96 against a budget of 66. Handing that to the search
        would describe an opponent half again stronger than any legal set, which
        is exactly the failure `ASSUMED_POINTS = 32` chose deliberately and this
        layer exists to replace.

        So the allocation starts at the lower bounds, which the resource
        constraint guarantees are affordable together, and spends whatever is
        left in proportion to each stat's remaining slack. Unconstrained that is
        11 in every stat -- the mean of a uniform allocation, which is the right
        thing to believe about a spread nothing has been learned about -- and as
        evidence narrows a stat, the budget it frees moves to the others.
        """
        allocation = dict(self.lower)
        budget = MAX_POINTS_TOTAL - sum(allocation.values())
        if budget <= 0:
            return allocation

        slack = {s: self.upper[s] - self.lower[s] for s in STAT_IDS}
        total_slack = sum(slack.values())
        if total_slack <= 0:
            return allocation

        for stat_id in STAT_IDS:
            share = min(slack[stat_id], int(budget * slack[stat_id] / total_slack))
            allocation[stat_id] += share
        return allocation

    def stats(self) -> dict[str, int]:
        """All six derived stats, from one legal allocation."""
        allocation = self.allocation()
        return {s: self.stat_at(s, allocation[s]) for s in STAT_IDS}

    def width(self, stat_id: str) -> int:
        return self.upper[stat_id] - self.lower[stat_id]

    def total_width(self) -> int:
        return sum(self.width(s) for s in STAT_IDS)

    def contains(self, points: Mapping[str, int]) -> bool:
        """Whether a true allocation lies inside the maintained box.

        The coverage metric in `docs/03` section 5 is the fraction of the time
        this is True on held-out truth. Below the nominal level means the
        quantization tolerance is too small and the filter is eliminating the
        truth.
        """
        return self.feasible and all(
            self.lower[s] <= int(points.get(s, 0)) <= self.upper[s] for s in STAT_IDS
        )

    def as_dict(self) -> dict[str, Any]:
        """The trace payload: point bounds and the derived stat bounds together,
        because a reader thinks in stats and the filter thinks in points."""
        return {
            "nature": self.nature,
            "feasible": self.feasible,
            "points": {s: [self.lower[s], self.upper[s]] for s in STAT_IDS},
            "stats": {s: list(self.stat_bounds(s)) for s in STAT_IDS},
        }

    # -- observations ---------------------------------------------------

    def observe_stat_at_least(self, stat_id: str, value: float, tolerance: float = 0.0) -> bool:
        """The derived stat is at least `value`, give or take `tolerance`.

        Widen before narrowing: the tolerance is subtracted from the observed
        floor, so a noisy observation produces a weaker bound rather than a
        wrong one.
        """
        threshold = value - tolerance
        for points in POINT_VALUES:
            if self.stat_at(stat_id, points) >= threshold:
                return self.constrain_points(stat_id, low=points)
        self.feasible = False
        return False

    def observe_stat_at_most(self, stat_id: str, value: float, tolerance: float = 0.0) -> bool:
        """The derived stat is at most `value`, give or take `tolerance`."""
        threshold = value + tolerance
        best: int | None = None
        for points in POINT_VALUES:
            if self.stat_at(stat_id, points) <= threshold:
                best = points
        if best is None:
            self.feasible = False
            return False
        return self.constrain_points(stat_id, high=best)

    def feasible_points(self, stat_id: str) -> list[int]:
        return [p for p in POINT_VALUES if self.lower[stat_id] <= p <= self.upper[stat_id]]

    def restrict_points(self, stat_id: str, allowed: list[int]) -> bool:
        """Keep only `allowed` point values for one stat, as an interval.

        The representation is a box, so a non-contiguous feasible set is stored
        as its convex hull. That is a relaxation and it is the deliberate one:
        the alternative is a per-stat bitmask, which buys very little here --
        damage is monotone in the stat, so the feasible sets damage observations
        produce are contiguous anyway -- and costs the closed-form propagation
        above.
        """
        if not allowed:
            self.feasible = False
            return False
        return self.constrain_points(stat_id, low=min(allowed), high=max(allowed))


def joint_restrict(
    belief: SpreadBelief,
    first: str,
    second: str,
    allowed: list[tuple[int, int]],
) -> bool:
    """Intersect a two-stat feasible set, projected onto each axis.

    A single damage observation against an opponent constrains their bulk stat
    and their HP *jointly*, because the reported figure is a percentage of a
    maximum HP we do not know. `docs/03` section 2 says exactly this. The pair
    is swept exactly (33 x 33 = 1089 cells, which is nothing) and then projected
    back onto the box, which is where the information is lost -- and the loss is
    real: the projection keeps "high HP is possible" and "high Defence is
    possible" without keeping "not both at once".

    Projecting rather than storing the joint set is the same trade the box
    representation makes everywhere else, and it errs in the safe direction: the
    projection is a superset, so it never eliminates the truth.
    """
    if not allowed:
        belief.feasible = False
        return False
    return belief.restrict_points(first, [a for a, _ in allowed]) and belief.restrict_points(
        second, [b for _, b in allowed]
    )

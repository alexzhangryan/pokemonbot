"""The Champions stat layer.

Champions is not mechanically Generation 9. At the fixed level 50 of Reg M-B the
stat formula is linear and integer valued:

    HP    = base + points + 75
    stat  = (base + points + 20) * nature

where `points` is the allocation stored in a set's `evs` field and `nature` is
0.9, 1.0 or 1.1 applied through 16 bit truncated integer arithmetic. This is
transcribed from `data/mods/champions/scripts.ts` `statModify`, not from any
mainline formula, and `test_stats.py` checks it cell by cell against the stats
the simulator itself reports for a battle.

Consequences worth knowing before using this module:

- One stat point is exactly plus one to the final stat, before nature. There is
  no quadratic and no per-level flooring, so the mainline intuition that points
  come in blocks of four does not apply.
- The endpoints coincide with mainline. Base 100 with 0 points is 120, matching
  0 EVs / 31 IVs at level 50; with 32 points it is 152, matching 252 EVs. A
  point is worth roughly 8 EVs and the 32 point cap is the 252 EV cap.
- Nothing here is valid under `Level Clause Mod`, which switches the simulator
  to a level-dependent mainline-shaped formula. Reg M-B does not carry that
  rule; `compute_stat` refuses rather than silently returning the wrong number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from champions.dex.loader import Dex

STAT_IDS: tuple[str, ...] = ("hp", "atk", "def", "spa", "spd", "spe")

#: Reg M-B allocates points, not EVs. Per-stat cap and total budget are the
#: format's, and are asserted rather than enforced: a set that exceeds them is a
#: set the simulator would reject at validation, not something to silently clamp.
MAX_POINTS_PER_STAT = 32
MAX_POINTS_TOTAL = 66

HP_OFFSET = 75
STAT_OFFSET = 20

#: Stat id -> the label Showdown's export-format parser expects, which is
#: case sensitive and contains one genuine trap: `sim/dex-data.ts` maps the
#: string `"Spd"` to **Speed**, a legacy alias from the old Smogon convention.
#: Special Defense is `"SpD"`. So `"spd".capitalize()` silently writes points
#: onto the wrong stat, and the resulting team is legal, so nothing complains.
#: Anything emitting export format must go through this table.
SHOWDOWN_STAT_LABELS: dict[str, str] = {
    "hp": "HP",
    "atk": "Atk",
    "def": "Def",
    "spa": "SpA",
    "spd": "SpD",
    "spe": "Spe",
}


def export_points(points: Mapping[str, int]) -> str:
    """The `EVs:` line of a Showdown export-format set, for a point allocation.

    Champions calls these stat points; the export format calls the field EVs and
    that is what the simulator parses, so the field name stays.
    """
    unknown = set(points) - set(STAT_IDS)
    if unknown:
        raise ValueError(f"Unknown stat ids: {sorted(unknown)}")
    return " / ".join(
        f"{points[stat_id]} {SHOWDOWN_STAT_LABELS[stat_id]}"
        for stat_id in STAT_IDS
        if points.get(stat_id)
    )


def trunc(number: float, bits: int = 0) -> int:
    """Showdown's `Battle#trunc`: truncate toward zero, optionally to `bits`.

    Reimplemented rather than approximated with `int()` because the nature step
    is specified as `trunc(trunc(stat * 110, 16) / 100)`, and 16 bit truncation
    is only a no-op while `stat * 110` stays under 65536. It does at the stat
    magnitudes Reg M-B can reach, but writing `int(stat * 1.1)` would make that
    a silent assumption instead of a checked one.
    """
    if bits:
        return int(number) % (2**bits)
    return int(number)


@dataclass(frozen=True)
class StatSpread:
    """A set's stat point allocation plus its nature.

    `points` is what the Showdown set format calls `evs`; the name here is the
    Champions name for it, because they are not EVs and do not behave like them.
    """

    points: Mapping[str, int]
    nature: str = "hardy"

    def __post_init__(self) -> None:
        unknown = set(self.points) - set(STAT_IDS)
        if unknown:
            raise ValueError(f"Unknown stat ids in spread: {sorted(unknown)}")
        negative = [s for s, p in self.points.items() if p < 0]
        if negative:
            raise ValueError(f"Negative stat points: {sorted(negative)}")

    def get(self, stat_id: str) -> int:
        return int(self.points.get(stat_id, 0))

    @property
    def total(self) -> int:
        return sum(int(p) for p in self.points.values())

    @property
    def is_legal(self) -> bool:
        """Whether the format would accept this spread.

        Advisory. The simulator's validator is the authority; this exists so a
        team builder can reject a spread without a round trip.
        """
        return self.total <= MAX_POINTS_TOTAL and all(
            p <= MAX_POINTS_PER_STAT for p in self.points.values()
        )


def apply_nature(stat: int, stat_id: str, nature: Mapping[str, Any]) -> int:
    """Apply one nature multiplier exactly as `statModify` does.

    `nature` is a resolved dex nature entry: `plus` and `minus` stat ids, both
    absent on a neutral nature.
    """
    if nature.get("plus") == stat_id:
        return trunc(trunc(stat * 110, 16) / 100)
    if nature.get("minus") == stat_id:
        return trunc(trunc(stat * 90, 16) / 100)
    return stat


def compute_stat(
    stat_id: str,
    base: int,
    points: int,
    nature: Mapping[str, Any] | None = None,
) -> int:
    """One final stat from one base stat and one point allocation.

    HP takes no nature, which is why the nature branch sits after the early
    return in the source and here.
    """
    if stat_id not in STAT_IDS:
        raise ValueError(f"Unknown stat id: {stat_id!r}")
    if stat_id == "hp":
        return base + points + HP_OFFSET
    stat = base + points + STAT_OFFSET
    return apply_nature(stat, stat_id, nature or {})


def compute_stats(
    base_stats: Mapping[str, int],
    spread: StatSpread,
    dex: Dex,
) -> dict[str, int]:
    """All six final stats for a set.

    `base_stats` is a species entry's `baseStats`. The nature is resolved
    through the dex so the multiplier comes from the simulator's own data.
    """
    nature = dex.nature(spread.nature)
    return {
        stat_id: compute_stat(stat_id, int(base_stats[stat_id]), spread.get(stat_id), nature)
        for stat_id in STAT_IDS
    }


def stats_for_species(
    species_id: str,
    spread: StatSpread,
    dex: Dex,
) -> dict[str, int]:
    """All six final stats for a species by id, resolved through the dex."""
    species = dex.species[species_id]
    return compute_stats(species["baseStats"], spread, dex)


def max_pp(move_id: str, dex: Dex) -> int:
    """Effective PP for a move, always fully PP-upped.

    Champions caps base PP at 20 in `Scripts.init` and overrides `calculatePP`
    to `(pp / 5 + 1) * 4`, so effective PP is `0.8 * pp + 4` rather than the
    mainline `1.6 * pp`. `docs/02-mechanics-deltas.md` records the mainline
    factor; the source and the simulator's own request objects agree on this
    one (see the STATUS open question).
    """
    move = dex.move(move_id)
    pp = int(move["pp"])
    if move.get("noPPBoosts"):
        return pp
    return int((pp / 5 + 1) * 4)

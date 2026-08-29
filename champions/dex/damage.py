"""The Champions damage layer.

The load-bearing finding of M1, established by diffing the mod against the
simulator's own source rather than by assumption:

    Champions does not change the shape of the damage calculation.

`data/mods/champions/scripts.ts` overrides `modifyDamage`, but the override is
numerically identical to `sim/battle-actions.ts`. The only difference in the
whole function is that `-supereffective` and `-resisted` gained a
`Math.min(typeMod, 2)` argument, which is a protocol message, not a number.
`getDamage`, which computes the base damage, is not overridden at all.

What Champions changes is every *input* to that calculation:

- Stats, via the linear stat formula (`champions.dex.stats`).
- Base power, on roughly 300 of the moves.
- Item and ability behaviour, on roughly 250 items and 8 abilities.
- Terastallization, which is disabled, removing the Tera STAB and Tera type
  branches entirely.

So `CLAUDE.md`'s rule stands unchanged and for a sharper reason than "the
formula differs": `@smogon/calc` would feed mainline stats, mainline base
powers, mainline item effects and a live Tera type into a skeleton that happens
to match. Sharing the skeleton is exactly what makes that failure quiet.

Scope. This module is the deterministic core: base damage, the spread modifier,
weather, crit, the sixteen damage rolls, STAB, type effectiveness, burn, and the
final 16 bit truncation. Item and ability multipliers enter through
`DamageContext.final_modifiers` rather than being enumerated here, because there
are roughly 250 of them and enumerating them by hand is precisely the transcription
error this project keeps avoiding. `tests/test_damage.py` compares this against
the simulator cell by cell.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from champions.dex.loader import Dex
from champions.dex.stats import trunc

#: The sixteen equally likely damage rolls, as percentages of the pre-roll
#: damage. Showdown's `randomizer` is `trunc(trunc(d * (100 - random(16))) / 100)`
#: so the multipliers are 100 down to 85, and every roll is equally likely.
DAMAGE_ROLLS: tuple[int, ...] = tuple(range(100, 84, -1))

#: `sim/pokemon.ts` getStat. Positive stages multiply, negative stages divide,
#: both with a floor. Not the 2/(2+n) form some calculators use.
BOOST_TABLE: tuple[float, ...] = (1, 1.5, 2, 2.5, 3, 3.5, 4)

SPREAD_MODIFIER_DOUBLES = 0.75
SPREAD_MODIFIER_FREE_FOR_ALL = 0.5
CRIT_MODIFIER = 1.5
BURN_MODIFIER = 0.5
STAB_MODIFIER = 1.5


def modify(value: int, numerator: float, denominator: int = 1) -> int:
    """Showdown's `Battle#modify`: a 4096ths fixed point multiply.

    `trunc((trunc(value * trunc(numerator * 4096 / denominator)) + 2047) / 4096)`.
    The rounding is round-half-up at 4096ths, not a float multiply, and chained
    modifiers each round independently. Doing this with floats drifts by one HP
    on a meaningful fraction of rolls, which is the difference between a
    guaranteed KO and a probable one.
    """
    modifier = trunc(numerator * 4096 / denominator)
    return trunc((trunc(value * modifier) + 2048 - 1) / 4096)


def boosted(stat: int, stage: int) -> int:
    """A stat with its boost stage applied, floored as the simulator floors it."""
    stage = max(-6, min(6, stage))
    if stage >= 0:
        return int(stat * BOOST_TABLE[stage])
    return int(stat / BOOST_TABLE[-stage])


@dataclass(frozen=True)
class TypeChart:
    """Type effectiveness, resolved from the dex dump.

    Showdown stores this as `damageTaken` on the *defending* type: 1 is super
    effective, 2 is resisted, 3 is immune, anything else is neutral. Immunity is
    a separate question from effectiveness in the source and stays separate
    here, because abilities and field effects can lift an immunity without
    touching the multiplier.
    """

    _types: Mapping[str, Any]

    @classmethod
    def from_dex(cls, dex: Dex) -> TypeChart:
        return cls(_types=dex.types)

    def is_immune(self, attacking: str, defending: Sequence[str]) -> bool:
        return any(
            self._types[t.lower()]["damageTaken"].get(attacking) == 3
            for t in defending
            if t.lower() in self._types
        )

    def effectiveness(self, attacking: str, defending: Sequence[str]) -> int:
        """The summed type modifier, as a stage in [-6, 6], not a multiplier.

        Returned as a stage because that is what the simulator carries, and
        because the doubling loop in `modifyDamage` is exact integer doubling on
        the way up and truncated halving on the way down -- the two are not
        inverse, so collapsing to a float multiplier first loses a HP.
        """
        total = 0
        for t in defending:
            entry = self._types.get(t.lower())
            if not entry:
                continue
            taken = entry["damageTaken"].get(attacking)
            if taken == 1:
                total += 1
            elif taken == 2:
                total -= 1
        return max(-6, min(6, total))


@dataclass(frozen=True)
class DamageContext:
    """Everything the deterministic core needs for one attacker/move/target cell.

    Deliberately plain: no Pokemon objects, no battle handle. The belief filter
    needs to run this backwards over a range of hypothesised spreads, and the
    search needs to run it thousands of times per turn, so it takes numbers.
    """

    base_power: int
    attack: int
    defense: int
    move_type: str
    attacker_types: Sequence[str]
    defender_types: Sequence[str]
    level: int = 50

    is_spread: bool = False
    is_crit: bool = False
    attacker_burned: bool = False
    #: Guts ignores burn, and Facade is exempt from gen 6 on.
    ignore_burn: bool = False
    game_type: str = "doubles"

    #: Weather and the roughly 250 item/ability effects enter here rather than
    #: being enumerated. Each is applied as a separate `modify` call in order,
    #: because chained modifiers round independently.
    weather_modifiers: Sequence[float] = field(default_factory=tuple)
    final_modifiers: Sequence[float] = field(default_factory=tuple)
    stab_override: float | None = None

    @property
    def has_stab(self) -> bool:
        if self.stab_override is not None:
            return False
        return any(t.lower() == self.move_type.lower() for t in self.attacker_types)


def base_damage(context: DamageContext) -> int:
    """`trunc(trunc(trunc(trunc(2L/5 + 2) * power * atk) / def) / 50)`.

    Four separate truncations, in that order. Collapsing them into one
    expression changes the answer.
    """
    level_term = trunc(2 * context.level / 5 + 2)
    step = trunc(level_term * context.base_power * context.attack)
    step = trunc(step / context.defense)
    return trunc(step / 50)


def damage_for_roll(context: DamageContext, chart: TypeChart, roll: int) -> int:
    """Final damage for one of the sixteen rolls.

    `roll` is the percentage from `DAMAGE_ROLLS`, 100 down to 85. The order of
    operations is `modifyDamage`'s and is not rearrangeable: crit before the
    roll, the roll before STAB, STAB before type effectiveness, burn after type
    effectiveness, and the 16 bit truncation last of all.
    """
    if chart.is_immune(context.move_type, context.defender_types):
        return 0

    damage = base_damage(context) + 2

    if context.is_spread:
        spread = (
            SPREAD_MODIFIER_FREE_FOR_ALL
            if context.game_type == "freeforall"
            else SPREAD_MODIFIER_DOUBLES
        )
        damage = modify(damage, spread)

    for weather in context.weather_modifiers:
        damage = modify(damage, weather)

    if context.is_crit:
        damage = trunc(damage * CRIT_MODIFIER)

    # The random factor is "not a modifier": a plain truncated percentage, not a
    # 4096ths multiply.
    damage = trunc(trunc(damage * roll) / 100)

    stab = context.stab_override
    if stab is None:
        stab = STAB_MODIFIER if context.has_stab else 1.0
    if stab != 1.0:
        damage = modify(damage, stab)

    type_mod = chart.effectiveness(context.move_type, context.defender_types)
    for _ in range(type_mod):
        damage *= 2
    for _ in range(-type_mod):
        damage = trunc(damage / 2)

    if context.attacker_burned and not context.ignore_burn:
        damage = modify(damage, BURN_MODIFIER)

    for final in context.final_modifiers:
        damage = modify(damage, final)

    if not damage:
        return 1
    return trunc(damage, 16)


def damage_roll_distribution(context: DamageContext, chart: TypeChart) -> list[int]:
    """All sixteen possible damage values, low roll first.

    Equally likely, so this doubles as the distribution. Returned as a list
    rather than a summary because the search buckets it and the coach reports
    the extremes, and both want the individual values.
    """
    return sorted(damage_for_roll(context, chart, roll) for roll in DAMAGE_ROLLS)


def ko_probability(context: DamageContext, chart: TypeChart, remaining_hp: int) -> float:
    """Fraction of the sixteen rolls that would knock the target out.

    The single number the search actually wants out of the damage layer. Exact,
    not sampled: sixteen rolls is cheap enough to enumerate, and sampling here
    would add variance to a quantity that has none.
    """
    rolls = damage_roll_distribution(context, chart)
    return sum(1 for d in rolls if d >= remaining_hp) / len(rolls)

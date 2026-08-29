"""M1: the Champions damage layer, validated cell by cell against the simulator.

The method is a probe battle with everything else held still. Two clean
attackers (Clear Body, Illuminate) hit two clean defenders (Keen Eye, Inner
Focus) who spend the turn using Agility, which is the only move they know and
which changes nothing the damage formula reads. No items, no weather, no
switching, no boosts on either attack or defence.

Every hit the simulator reports is then checked against
`damage_roll_distribution`: the observed damage must be one of the sixteen
values this module predicts. Run over many seeds the observed values sweep most
of the roll range, so this is a check on the whole distribution rather than on a
single number, and `test_the_probe_actually_sampled_the_range` fails if the
sweep degenerates.

Coverage across the cells: physical and special, STAB and not, spread and
single target, friendly fire, immunity, and effectiveness at 0.5x, 1x, 2x and
4x. Crits are not forced -- they are detected from the log and fed back in, so
whenever one happens it is checked too.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from champions.dex.damage import (
    DAMAGE_ROLLS,
    DamageContext,
    TypeChart,
    boosted,
    damage_roll_distribution,
    ko_probability,
    modify,
)
from champions.dex.loader import Dex
from champions.dex.stats import StatSpread, stats_for_species
from champions.search.oracle import SimServer

FORMAT_ID = "gen9championsvgc2026regmb"

# Ability choices matter as much as species here: Intimidate, Multiscale,
# Sturdy or Thick Fat on any of the four would move the numbers and the probe
# would be measuring the ability instead of the formula.
ATTACKERS = (
    ("Metagross", "Clear Body", "Adamant", {"atk": 32, "spa": 20}),
    ("Starmie", "Illuminate", "Modest", {"spa": 32, "spe": 20}),
)
DEFENDERS = (
    ("Skarmory", "Keen Eye", "Bold", {"hp": 32, "def": 20}),
    ("Dragonite", "Inner Focus", "Calm", {"hp": 32, "spd": 12}),
)
FILLER = (
    ("Pelipper", "Keen Eye", "Hardy", {}),
    ("Talonflame", "Flame Body", "Hardy", {}),
    ("Corviknight", "Pressure", "Hardy", {}),
    ("Arcanine", "Flash Fire", "Hardy", {}),
)

ATTACKER_MOVES = {
    "Metagross": ["Iron Head", "Earthquake", "Bullet Punch", "Zen Headbutt"],
    "Starmie": ["Thunderbolt", "Ice Beam", "Surf", "Psychic"],
}

# One entry per turn-1 choice, chosen so that between them they exercise every
# branch of `damage_for_roll`.
CHOICES = (
    "move ironhead 1, move icebeam 2",  # STAB resisted; non-STAB 4x
    "move bulletpunch 2, move thunderbolt 1",  # STAB neutral; non-STAB 2x
    "move ironhead 2, move psychic 1",  # STAB neutral, physical and special
    "move earthquake, move icebeam 1",  # spread into a double immunity, plus friendly fire
    "move bulletpunch 1, move surf",  # STAB spread hitting three targets at three multipliers
)

SPREAD_MOVES = {"earthquake", "surf"}


def _points_line(points: dict[str, int]) -> str:
    from champions.dex.stats import export_points

    return f"EVs: {export_points(points)}\n" if points else ""


def _team(
    front: tuple[tuple[str, str, str, dict[str, int]], ...],
    moves: dict[str, list[str]] | None,
) -> str:
    blocks = []
    for species, ability, nature, points in front + FILLER:
        move_list = (moves or {}).get(species, ["Agility"])
        blocks.append(
            f"{species}\nAbility: {ability}\nLevel: 50\n"
            + _points_line(points)
            + f"{nature} Nature\n"
            + "\n".join(f"- {m}" for m in move_list)
        )
    return "\n\n".join(blocks) + "\n"


@dataclass(frozen=True)
class Hit:
    """One damage event, read off the protocol log."""

    attacker: str
    move: str
    target: str
    damage: int
    hp_before: int
    max_hp: int
    crit: bool
    spread: bool


IDENT = re.compile(r"^p(\d)([a-c]): (.+)$")


def _slot_species(ident: str) -> str:
    match = IDENT.match(ident)
    assert match, ident
    return match.group(3)


def _parse_hits(log: list[str]) -> list[Hit]:
    """Extract every damage event with the move that caused it.

    The simulator emits each HP change twice behind a `|split|` -- the exact
    value for the side that owns the Pokemon, then the percentage everyone else
    sees. The exact line comes first, which is the one worth reading, and is
    also the reason `docs/03-belief-filter.md` treats opponent HP as quantized:
    in a real battle only the second line is available.
    """
    hits: list[Hit] = []
    hp: dict[str, tuple[int, int]] = {}
    move: str | None = None
    attacker: str | None = None
    crit_pending: set[str] = set()
    spread_pending = False
    expect_exact = False

    for line in log:
        parts = line.split("|")
        if len(parts) < 2:
            continue
        tag = parts[1]

        if tag == "split":
            expect_exact = True
            continue

        if tag == "switch" and len(parts) > 4 and "/" in parts[4]:
            if expect_exact:
                cur, mx = parts[4].split("/")
                hp[parts[2]] = (int(cur), int(mx.split()[0]))
            expect_exact = False
            continue

        if tag == "move":
            attacker = parts[2]
            move = parts[3].lower().replace(" ", "").replace("-", "")
            crit_pending.clear()
            spread_pending = move in SPREAD_MOVES
            continue

        if tag == "-crit":
            crit_pending.add(parts[2])
            continue

        if tag in {"-damage", "-heal"} and expect_exact:
            expect_exact = False
            ident = parts[2]
            condition = parts[3]
            before = hp.get(ident)
            if condition.startswith("0 fnt"):
                after = 0
                max_hp = before[1] if before else 0
            else:
                cur, mx = condition.split("/")
                after, max_hp = int(cur), int(mx.split()[0])
            if before and tag == "-damage" and move and attacker:
                hits.append(
                    Hit(
                        attacker=_slot_species(attacker),
                        move=move,
                        target=_slot_species(ident),
                        damage=before[0] - after,
                        hp_before=before[0],
                        max_hp=max_hp,
                        crit=ident in crit_pending,
                        spread=spread_pending,
                    )
                )
            hp[ident] = (after, max_hp)
            continue

        expect_exact = False
    return hits


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


@pytest.fixture(scope="module")
def chart(dex: Dex) -> TypeChart:
    return TypeChart.from_dex(dex)


@pytest.fixture(scope="module")
def sim() -> Iterator[SimServer]:
    with SimServer() as server:
        yield server


@pytest.fixture(scope="module")
def profile(dex: Dex) -> dict[str, dict]:
    """species -> its types and its six computed stats, from the stat layer.

    These are the numbers the damage layer is fed. They come from
    `champions.dex.stats`, which `test_stats.py` has already checked against the
    simulator, so a failure here is a damage failure and not a stat one.
    """
    out = {}
    for species, _ability, nature, points in ATTACKERS + DEFENDERS:
        species_id = species.lower()
        out[species] = {
            "types": dex.species[species_id]["types"],
            "stats": stats_for_species(species_id, StatSpread(points=points, nature=nature), dex),
        }
    return out


@pytest.fixture(scope="module")
def observed(sim: SimServer) -> list[Hit]:
    """Every hit from turn 1 of a fresh battle, across choices and seeds.

    Turn 1 only, and a new battle each time, so nothing carries over: no
    residual damage, no boosts, no fainted slots, and every target at full HP.
    """
    attackers = _team(ATTACKERS, ATTACKER_MOVES)
    defenders = _team(DEFENDERS, None)

    hits: list[Hit] = []
    for seed in range(40):
        for choice in CHOICES:
            handle = int(
                sim.create(
                    FORMAT_ID, attackers, defenders, seed=[seed, seed + 1, seed + 2, seed + 3]
                )["handle"]
            )
            sim.step(handle, "team 1234", "team 1234")
            state = sim.step(handle, choice, "move agility, move agility")
            hits.extend(_parse_hits(state["log"]))
            sim.destroy(handle)
    return hits


def _context(hit: Hit, dex: Dex, profile: dict[str, dict]) -> DamageContext:
    move = dex.move(hit.move)
    attacker = profile[hit.attacker]
    defender = profile[hit.target]
    physical = move["category"] == "Physical"
    return DamageContext(
        base_power=int(move["basePower"]),
        attack=attacker["stats"]["atk" if physical else "spa"],
        defense=defender["stats"]["def" if physical else "spd"],
        move_type=move["type"],
        attacker_types=attacker["types"],
        defender_types=defender["types"],
        is_spread=hit.spread,
        is_crit=hit.crit,
    )


def test_the_probe_produced_hits(observed: list[Hit]) -> None:
    assert len(observed) > 200, f"only {len(observed)} hits, the probe told us little"


def test_every_observed_damage_is_a_predicted_roll(
    observed: list[Hit], dex: Dex, profile: dict[str, dict], chart: TypeChart
) -> None:
    """The claim M1 exists to support, checked on every cell the probe produced."""
    failures = []
    for hit in observed:
        predicted = damage_roll_distribution(_context(hit, dex, profile), chart)
        # The simulator reports the HP actually lost, so a roll that would
        # overkill is reported as the target's remaining HP. Predicting more
        # damage than the target has left is correct, not a divergence.
        clamped = {min(d, hit.hp_before) for d in predicted}
        if hit.damage not in clamped:
            failures.append(
                f"{hit.attacker} {hit.move} -> {hit.target}"
                f"{' (crit)' if hit.crit else ''}{' (spread)' if hit.spread else ''}: "
                f"simulator dealt {hit.damage}, predicted {predicted[0]}..{predicted[-1]}"
            )
    assert not failures, "\n".join(sorted(set(failures)))


def test_the_probe_actually_sampled_the_range(
    observed: list[Hit], dex: Dex, profile: dict[str, dict], chart: TypeChart
) -> None:
    """Guards the test above from passing on a degenerate sample.

    If every cell only ever produced one damage value, membership in a sixteen
    element set would be a weak claim. This requires that at least one cell was
    seen across most of its roll range.
    """
    seen: dict[tuple[str, str, str], set[int]] = {}
    for hit in observed:
        seen.setdefault((hit.attacker, hit.move, hit.target), set()).add(hit.damage)

    best = max(seen.values(), key=len)
    assert len(best) >= 8, f"the widest cell only showed {len(best)} distinct damage values"


def test_the_probe_covered_every_effectiveness_branch(
    observed: list[Hit], dex: Dex, profile: dict[str, dict], chart: TypeChart
) -> None:
    """Asserts what the probe is actually exercising, so a later edit that
    quietly drops a cell is a failure rather than a silent loss of coverage."""
    branches = set()
    for hit in observed:
        move = dex.move(hit.move)
        branches.add(chart.effectiveness(move["type"], profile[hit.target]["types"]))
    assert {-1, 0, 1, 2} <= branches, f"missing effectiveness branches, saw {sorted(branches)}"

    assert any(h.spread for h in observed), "no spread move was exercised"
    assert any(not h.spread for h in observed), "no single-target move was exercised"
    assert any(dex.move(h.move)["category"] == "Physical" for h in observed)
    assert any(dex.move(h.move)["category"] == "Special" for h in observed)
    # Friendly fire: a spread move hitting the attacker's own partner.
    assert any(h.target in {a[0] for a in ATTACKERS} for h in observed)


def test_ground_moves_are_predicted_immune_against_flying(
    dex: Dex, profile: dict[str, dict], chart: TypeChart
) -> None:
    """Earthquake into two Flying types is in the probe precisely so that a
    missing immunity check would show up as predicted damage where the
    simulator reported none."""
    for defender in ("Skarmory", "Dragonite"):
        assert chart.is_immune("Ground", profile[defender]["types"])
        context = DamageContext(
            base_power=100,
            attack=200,
            defense=100,
            move_type="Ground",
            attacker_types=["Steel", "Psychic"],
            defender_types=profile[defender]["types"],
        )
        assert damage_roll_distribution(context, chart) == [0] * 16


def test_no_ground_damage_was_ever_observed_on_a_flying_target(observed: list[Hit]) -> None:
    flying = ("Skarmory", "Dragonite")
    ground = [h for h in observed if h.move == "earthquake" and h.target in flying]
    assert not ground, f"the simulator dealt Ground damage to a Flying type: {ground[:1]}"


# -- the primitives, checked against the source rather than against each other --


def test_modify_is_a_4096ths_multiply_not_a_float_multiply() -> None:
    """`modify(value, n)` is `trunc((trunc(value * trunc(n * 4096)) + 2047) / 4096)`.

    The cases below are ones where a plain float multiply disagrees, which is
    the whole reason this is not written as `int(value * n)`.
    """
    assert modify(100, 1.5) == 150
    # The two cases below are where a float multiply disagrees: it would floor
    # them to 0 and 3. Both are spread-modifier territory, so this is not
    # hypothetical -- it is one HP on every spread move against a weak roll.
    assert modify(1, 0.75) == 1
    assert modify(5, 0.75) == 4
    assert modify(3, 0.75) == 2
    assert modify(5, 0.75) == 4
    assert modify(38, 1.5) == 57


def test_boost_table_matches_the_simulator() -> None:
    """Positive stages multiply and negative stages divide, both floored."""
    assert boosted(100, 0) == 100
    assert boosted(100, 1) == 150
    assert boosted(100, 6) == 400
    assert boosted(100, -1) == 66
    assert boosted(100, -6) == 25
    assert boosted(100, 99) == boosted(100, 6)
    assert boosted(100, -99) == boosted(100, -6)


def test_there_are_sixteen_equally_likely_rolls() -> None:
    assert len(DAMAGE_ROLLS) == 16
    assert DAMAGE_ROLLS[0] == 100
    assert DAMAGE_ROLLS[-1] == 85


def test_ko_probability_is_exact_not_sampled(chart: TypeChart) -> None:
    context = DamageContext(
        base_power=80,
        attack=200,
        defense=100,
        move_type="Normal",
        attacker_types=["Normal"],
        defender_types=["Normal"],
    )
    rolls = damage_roll_distribution(context, chart)
    assert ko_probability(context, chart, rolls[0]) == 1.0
    assert ko_probability(context, chart, rolls[-1] + 1) == 0.0
    # A threshold between the low and high roll is a genuine fraction of 16.
    midpoint = rolls[8]
    probability = ko_probability(context, chart, midpoint)
    assert 0.0 < probability < 1.0
    assert probability == sum(1 for d in rolls if d >= midpoint) / 16

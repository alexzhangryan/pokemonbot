"""M1: the Champions stat formula, checked against the simulator rather than
against the transcription.

`docs/09-m0-tasks.md` flags this as the thing to verify first, because every
number downstream -- damage, KO thresholds, speed order, the belief filter's
spread intervals -- is built on it, and a transcription error would be silent.

The method is a probe team: species with deliberately chosen point allocations
and natures, run through the real simulator, and every stat it reports compared
against `champions.dex.stats`. The simulator is the authority; this module is
the thing under test.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from champions.dex.loader import Dex
from champions.dex.stats import (
    MAX_POINTS_PER_STAT,
    STAT_IDS,
    StatSpread,
    compute_stat,
    export_points,
    max_pp,
    stats_for_species,
    trunc,
)
from champions.search.oracle import SimServer

FORMAT_ID = "gen9championsvgc2026regmb"

# Chosen to separate the terms of the formula rather than to be a good team.
# Between them these cover: zero points, the per-stat cap, a plus nature, a
# minus nature, a neutral nature, and plus/minus landing on stats that also
# carry points -- so a missing nature step and a missing points term cannot both
# be hidden by the same passing cell.
PROBE: tuple[tuple[str, str, str, dict[str, int]], ...] = (
    ("Incineroar", "Blaze", "Adamant", {"hp": 32, "atk": 32}),
    ("Aegislash", "Stance Change", "Modest", {"spa": 32, "spd": 32}),
    ("Corviknight", "Pressure", "Timid", {"spe": 32, "def": 32}),
    ("Garchomp", "Rough Skin", "Jolly", {"atk": 20, "spe": 20, "hp": 26}),
    ("Pelipper", "Drizzle", "Hardy", {}),
    ("Talonflame", "Gale Wings", "Relaxed", {"hp": 32, "def": 32}),
)


def _species_id(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "")


def _export(entries: tuple[tuple[str, str, str, dict[str, int]], ...], moves: str) -> str:
    blocks = []
    for species, ability, nature, points in entries:
        lines = [species, f"Ability: {ability}", "Level: 50"]
        if points:
            # Showdown's export format calls the allocation EVs. In Champions
            # these are stat points and one is worth one, not four.
            lines.append(f"EVs: {export_points(points)}")
        lines.append(f"{nature} Nature")
        lines.append(moves)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


@pytest.fixture(scope="module")
def sim() -> Iterator[SimServer]:
    with SimServer() as server:
        yield server


@pytest.fixture(scope="module")
def reported(sim: SimServer) -> dict[str, dict[str, int]]:
    """species id -> the six stats the simulator reports for the probe set.

    Read off the team preview request, which carries every Pokemon on the side
    with its computed stats and its `condition` as `cur/max` HP.
    """
    team = _export(PROBE, "- Protect")
    handle = int(sim.create(FORMAT_ID, team, team, seed=[1, 2, 3, 4])["handle"])
    request = sim.request(handle)["p1"]

    out: dict[str, dict[str, int]] = {}
    for mon in request["side"]["pokemon"]:
        stats = dict(mon["stats"])
        stats["hp"] = int(mon["condition"].split("/")[1].split()[0])
        out[_species_id(mon["details"].split(",")[0])] = stats
    sim.destroy(handle)
    return out


def test_the_probe_team_is_what_we_think_it_is(reported: dict[str, dict[str, int]]) -> None:
    assert len(reported) == len(PROBE)
    for stats in reported.values():
        assert set(stats) == set(STAT_IDS)


@pytest.mark.parametrize("entry", PROBE, ids=[e[0] for e in PROBE])
@pytest.mark.parametrize("stat_id", STAT_IDS)
def test_computed_stat_matches_the_simulator(
    entry: tuple[str, str, str, dict[str, int]],
    stat_id: str,
    reported: dict[str, dict[str, int]],
    dex: Dex,
) -> None:
    """Cell by cell: six stats times six sets, each compared independently."""
    species, _ability, nature, points = entry
    species_id = _species_id(species)

    computed = stats_for_species(species_id, StatSpread(points=points, nature=nature), dex)

    assert computed[stat_id] == reported[species_id][stat_id], (
        f"{species} {stat_id}: computed {computed[stat_id]}, "
        f"simulator says {reported[species_id][stat_id]}"
    )


def test_a_point_is_worth_exactly_one(dex: Dex) -> None:
    """The formula is linear, unlike mainline's quadratic with flooring.

    This is the property the spread-interval half of the belief filter depends
    on, so it is asserted directly rather than left implied.
    """
    neutral = dex.nature("hardy")
    for points in range(MAX_POINTS_PER_STAT + 1):
        assert compute_stat("atk", 100, points, neutral) == 100 + points + 20
        assert compute_stat("hp", 100, points, neutral) == 100 + points + 75


def test_endpoints_coincide_with_mainline_level_50(dex: Dex) -> None:
    """Base 100 with 0 points is 120 (0 EVs, 31 IVs); with 32 it is 152 (252 EVs).

    The correspondence is what makes existing spread intuition partly portable,
    and it is the cheapest available check that the offsets are not off by one.
    """
    neutral = dex.nature("hardy")
    assert compute_stat("atk", 100, 0, neutral) == 120
    assert compute_stat("atk", 100, MAX_POINTS_PER_STAT, neutral) == 152


def test_nature_uses_truncated_integer_arithmetic(dex: Dex) -> None:
    """Not `int(stat * 1.1)`.

    The two agree across Reg M-B's range; the point is that the source says
    `trunc(trunc(stat * 110, 16) / 100)` and this module says the same thing, so
    a future mod that raises stats into the 16 bit overflow finds this still
    correct rather than quietly diverged.
    """
    adamant = dex.nature("adamant")
    timid = dex.nature("timid")

    assert compute_stat("atk", 100, 0, adamant) == trunc(trunc(120 * 110, 16) / 100)
    assert compute_stat("atk", 100, 0, timid) == trunc(trunc(120 * 90, 16) / 100)
    # HP takes no nature, in the source and here.
    assert compute_stat("hp", 100, 0, adamant) == 175


def test_hp_is_never_nature_modified(reported: dict[str, dict[str, int]], dex: Dex) -> None:
    for species, _ability, nature, points in PROBE:
        species_id = _species_id(species)
        base = dex.species[species_id]["baseStats"]["hp"]
        assert reported[species_id]["hp"] == base + points.get("hp", 0) + 75, (
            f"{species} with a {nature} nature"
        )


def test_effective_pp_matches_the_simulator(sim: SimServer, dex: Dex) -> None:
    """Champions caps base PP at 20 and computes `(pp / 5 + 1) * 4`.

    `docs/02-mechanics-deltas.md` records this as `1.6 * min(pp, 20)`, which is
    the mainline factor and disagrees. The simulator's own request object is
    what settles it. Raised as an open question in STATUS.
    """
    team = _export(PROBE, "- Protect\n- Rest\n- Substitute\n- Sleep Talk")
    handle = int(sim.create(FORMAT_ID, team, team, seed=[1, 2, 3, 4])["handle"])
    sim.step(handle, "team 1234", "team 1234")
    request = sim.request(handle)["p1"]

    checked = 0
    for active in request.get("active", []):
        for move in active["moves"]:
            assert move["maxpp"] == max_pp(move["id"], dex), move["id"]
            checked += 1
    sim.destroy(handle)
    assert checked, "no moves were checked, the probe told us nothing"


def test_spread_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="Unknown stat"):
        StatSpread(points={"attack": 4})
    with pytest.raises(ValueError, match="Negative"):
        StatSpread(points={"atk": -1})


def test_spread_legality_is_advisory_but_correct() -> None:
    assert StatSpread(points={"atk": 32, "spe": 32}).is_legal
    assert not StatSpread(points={"atk": 33}).is_legal
    assert not StatSpread(points={"hp": 32, "atk": 32, "spe": 32}).is_legal

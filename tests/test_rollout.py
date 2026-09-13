"""M8, the fidelity arm: a battle materialised at an observed position.

`js/sim_server.js` `materialize` is the first code in the project that
*constructs* a simulator state rather than observing one, and a constructed
state that is subtly wrong would make every simulator-backed number wrong in
a way no win rate reveals. So the load-bearing test here does not assert a
value. It plays real seeded battles, rebuilds each turn's position from the
battle's own log the way the agent sees it, materialises that, copies the real
battle's PRNG seed across, steps both with the same choices, and requires the
two logs to agree line for line.

Positions carrying something an observed position cannot represent -- PP,
Choice locks, Encore, volatile durations, sleep and toxic counters -- are
skipped and counted, and the exclusion list is `UNREPRESENTABLE` below, by
name. The test reports how many turns it skipped so a change that widens the
gap is visible.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from champions.corpus.replay_state import Observer
from champions.dex.loader import Dex
from champions.search.oracle import SimServer
from champions.search.payoff import TurnModel, payoff_matrix
from champions.search.rollout import (
    RolloutModel,
    export_sets,
    our_choice,
    pokemon_name,
    position_payload,
    replicate_seed,
    sim_snapshot,
    slot_leads,
    team_order,
    their_choice,
)
from champions.teams import ALPHA, BETA, load_team

FORMAT_ID = "gen9championsvgc2026regmb"

#: What an observed position cannot carry into the simulator. A real position
#: showing any of these is skipped by the equivalence test rather than
#: compared, and the skip is counted.
UNREPRESENTABLE_VOLATILES = {
    "choicelock",
    "encore",
    "taunt",
    "disable",
    "torment",
    "substitute",
    "perishsong",
    "lockedmove",
    "twoturnmove",
    "mustrecharge",
    "confusion",
    "leechseed",
    "partiallytrapped",
    "attract",
    "yawn",
    "curse",
    "nightmare",
    "embargo",
    "healblock",
    "throatchop",
    "focusenergy",
    "laserfocus",
    "charge",
    "magnetrise",
    "telekinesis",
    "gastroacid",
    "smackdown",
    "dynamax",
}
REPRESENTABLE_VOLATILES = {"stall"}


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


@pytest.fixture(scope="module")
def sim() -> Iterator[SimServer]:
    with SimServer() as server:
        yield server


# -- helpers -----------------------------------------------------------------------


def spectator(lines: list[str], private_for: set[str]) -> list[str]:
    """The log as a viewer with `private_for`'s exact HP would receive it.

    The simulator writes `|split|pN` followed by the private and the public
    version of the next line; a client keeps the private one for its own side.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|split|"):
            side = line.split("|")[2]
            private, public = lines[i + 1], lines[i + 2]
            out.append(private if side in private_for else public)
            i += 3
            continue
        out.append(line)
        i += 1
    return out


def stripped(lines: list[str]) -> list[str]:
    return [line for line in lines if not line.startswith("|t:|")]


def view_from(
    lines: list[str], dex: Dex, side: str, brought: set[str] | None = None
) -> dict[str, Any]:
    """The observer's view, with `selected` set the way the live snapshot sets
    it: the observer previews all six and cannot know the bring, poke-env can."""
    observer = Observer(dex)
    for line in lines:
        observer.feed(line)
    view = observer.view(side)
    if brought is not None:
        for p in [p for p in view["ours"]["active"] if p] + view["ours"]["bench"]:
            p["selected"] = p["name"] in brought
    return view


def bring_of(state: dict[str, Any], side_index: int) -> set[str]:
    return {pokemon_name(p) for p in state["sides"][side_index]["pokemon"]}


def with_formes(view: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """The live snapshot reports a Pokemon's *current* forme as its species
    (poke-env follows `-formechange`); the observer keeps the base species and
    folds the forme into types and base stats. Materialising needs the forme,
    so the test supplies it from the real state, as play would."""
    formes: dict[str, str] = {}
    for side in state["sides"]:
        for p in side["pokemon"]:
            species = str(p.get("species") or "")
            formes[pokemon_name(p)] = species[species.index(":") + 1 : -1]
    for side in ("ours", "theirs"):
        for p in [p for p in view[side]["active"] if p] + view[side]["bench"]:
            if p["name"] in formes:
                p["species"] = formes[p["name"]]
    return view


def order_from(state: dict[str, Any], side_index: int, team: str) -> str:
    """The team preview choice that reproduces `side.pokemon`'s current order,
    which the simulator keeps with the active Pokemon first."""
    names = [pokemon_name(p) for p in state["sides"][side_index]["pokemon"]]
    order = team_order(export_sets(team), names, [], np.random.default_rng(0))
    return ",".join(map(str, order))


def condition_hp(condition: str) -> tuple[int, int]:
    head = condition.split(" ")[0]
    if "/" not in head:
        return 0, 1
    current, _, maximum = head.partition("/")
    return int(re.sub(r"\D", "", current) or 0), int(re.sub(r"\D", "", maximum) or 1)


def unrepresentable(state: dict[str, Any], requests: dict[str, Any]) -> str | None:
    """Why this real position is outside what `materialize` can reproduce, or None."""
    for side in state["sides"]:
        for p in side["pokemon"]:
            for volatile in p.get("volatiles") or {}:
                if volatile not in REPRESENTABLE_VOLATILES:
                    return f"volatile {volatile}"
            status_state = p.get("statusState") or {}
            if p.get("status") in ("slp", "tox") and status_state:
                return f"{p['status']} counter"
            for slot in p.get("moveSlots") or []:
                if slot.get("pp") == 0:
                    return "a move at 0 PP"
        for cid, data in (side.get("sideConditions") or {}).items():
            if cid in ("spikes", "toxicspikes"):
                return f"layered {cid}"
            if data.get("duration") == 1:
                return f"{cid} ending this turn"
    field = state.get("field") or {}
    if field.get("weather") and (field.get("weatherState") or {}).get("duration") == 1:
        return "weather ending this turn"
    if field.get("terrain") and (field.get("terrainState") or {}).get("duration") == 1:
        return "terrain ending this turn"
    for pid, data in (field.get("pseudoWeather") or {}).items():
        if data.get("duration") == 1:
            return f"{pid} ending this turn"
    for request in requests.values():
        for active in (request or {}).get("active") or []:
            for move in active.get("moves") or []:
                if move.get("disabled"):
                    return f"disabled {move.get('id')}"
    return None


def play(sim: SimServer, seed: list[int], turns: int) -> Iterator[dict[str, Any]]:
    """Yield each move-request turn's real state, log so far, and the random
    choices about to be made, then make them. Forced switches are answered at
    random without yielding, since they are not positions the agent decides
    a turn from."""
    created = sim.create(FORMAT_ID, load_team(ALPHA), load_team(BETA), seed=seed)
    handle = int(created["handle"])
    preview = sim.call("randomChoice", handle=handle, seed=seed)["choices"]
    summary = sim.step(handle, preview.get("p1"), preview.get("p2"))
    yielded = 0
    steps = 0
    while not summary["ended"] and yielded < turns and steps < 4 * turns:
        steps += 1
        choices = sim.call("randomChoice", handle=handle, seed=[*seed[:3], steps])["choices"]
        if summary["requestState"] == "move":
            yield {
                "handle": handle,
                "state": sim.serialize(handle),
                "requests": sim.request(handle),
                "log": list(summary["log"]),
                "choices": choices,
            }
            yielded += 1
        summary = sim.step(handle, choices.get("p1"), choices.get("p2"))
    sim.destroy(handle)


# -- the team text and the choices --------------------------------------------------


def test_export_sets_read_both_checked_in_teams() -> None:
    alpha = export_sets(load_team(ALPHA))
    assert [s.species for s in alpha] == [
        "Incineroar",
        "Aegislash",
        "Corviknight",
        "Clefable",
        "Conkeldurr",
        "Charizard",
    ]
    assert [s.index for s in alpha] == [1, 2, 3, 4, 5, 6]
    assert all(s.nickname == s.species for s in alpha)


def test_export_sets_split_nicknames_and_genders() -> None:
    sets = export_sets(
        "Blade (Aegislash) (M) @ Leftovers\nAbility: Stance Change\n\nClefable (F)\n"
    )
    assert (sets[0].nickname, sets[0].species) == ("Blade", "Aegislash")
    assert (sets[1].nickname, sets[1].species) == ("Clefable", "Clefable")


def test_team_order_leads_first_then_a_seeded_draw() -> None:
    sets = export_sets(load_team(BETA))
    rng = np.random.default_rng(3)
    order = team_order(sets, ["Garchomp", "Milotic"], ["Tyranitar"], rng)
    assert order[:3] == [4, 6, 2]
    assert len(order) == 4 and len(set(order)) == 4
    again = team_order(sets, ["Garchomp", "Milotic"], ["Tyranitar"], np.random.default_rng(3))
    assert again == order, "the draw must be reproducible from its seed"


def test_our_choice_is_the_wire_form_without_the_prefix() -> None:
    assert our_choice({"message": "/choose move flareblitz 1, switch Clefable"}) == (
        "move flareblitz 1, switch Clefable"
    )
    assert our_choice({"message": "move protect, pass"}) == "move protect, pass"


def test_their_choice_targets_a_living_slot_and_passes_a_fainted_one(dex: Dex) -> None:
    column = {"slots": [{"kind": "move", "move": "icebeam", "target": 1}, {"kind": "none"}]}
    assert their_choice(column, dex, [False, True], [True, True]) == "move icebeam 2, default"
    assert their_choice(column, dex, [True, True], [True, False]) == "move icebeam 1, pass"
    spread = {"slots": [{"kind": "move", "move": "earthquake", "target": 1}]}
    assert their_choice(spread, dex, [True, True], [True, True]) == "move earthquake, default"


def test_slot_leads_hold_empty_slots_with_the_fainted() -> None:
    active = [None, {"name": "Milotic"}]
    bench = [{"name": "Gyarados", "fainted": True}, {"name": "Dragonite", "fainted": False}]
    assert slot_leads(active, bench) == ["Gyarados", "Milotic"]
    assert slot_leads([None, None], bench) is None
    assert slot_leads([{"name": "A"}, {"name": "B"}], []) == ["A", "B"]


def test_replicate_seeds_are_four_words_and_stable() -> None:
    a = replicate_seed(0, "battle-x-1", 3, 0)
    assert len(a) == 4 and all(0 <= w < 2**16 for w in a)
    assert a == replicate_seed(0, "battle-x-1", 3, 0)
    assert a != replicate_seed(0, "battle-x-1", 3, 1)


# -- the position payload ---------------------------------------------------------------


def test_position_payload_dates_conditions_and_names_sides(dex: Dex) -> None:
    snapshot = {
        "turn": 6,
        "player_role": "p2",
        "weather": {"RAINDANCE": 4},
        "fields": {"TRICK_ROOM": 5, "GRASSY_TERRAIN": 3},
        "side_conditions": {"TAILWIND": 5, "SPIKES": 2},
        "opponent_side_conditions": {},
        "ours": {
            "active": [
                {
                    "known": True,
                    "name": "Chomp",
                    "species": "garchomp",
                    "hp": 90,
                    "hp_pct": 49.2,
                    "status": "PAR",
                    "boosts": {"atk": 2},
                    "item": None,
                    "first_turn": True,
                    "protect_counter": 1,
                },
                None,
            ],
            "bench": [],
        },
        "theirs": {
            "active": [{"known": False, "name": "Milotic", "species": "milotic", "hp_pct": 70.0}],
            "bench": [],
        },
    }
    payload = position_payload(snapshot, {"Chomp": "lifeorb"}, {})

    assert payload["turn"] == 6
    assert payload["weather"] == {"id": "raindance", "remaining": None}
    assert payload["pseudo_weather"] == {"trickroom": {"remaining": 4}}
    assert payload["terrain"] == {"id": "grassyterrain", "remaining": 2}
    ours = payload["sides"]["p2"]
    assert ours["conditions"] == {"tailwind": {"remaining": 3}, "spikes": {"layers": 2}}
    chomp = ours["pokemon"][0]
    assert chomp["hp"] == 90 and chomp["status"] == "PAR" and chomp["boosts"] == {"atk": 2}
    assert chomp["item_consumed"] is True, "the set registers an item and we hold none"
    assert chomp["first_turn"] is True and chomp["protect_counter"] == 1
    theirs = payload["sides"]["p1"]
    assert theirs["pokemon"][0]["hp_pct"] == 70.0 and "hp" not in theirs["pokemon"][0]


# -- the equivalence test --------------------------------------------------------------


def test_materialize_reproduces_real_positions(sim: SimServer, dex: Dex) -> None:
    """Rebuild every turn of three random games from its own log, and require
    the materialised battle to answer the same request and, under the real
    battle's seed, produce the same log for the same choices."""
    compared = skipped = 0
    reasons: dict[str, int] = {}

    for game in range(4):
        seed = [11 + game, 22, 33, 44]
        for turn in play(sim, seed, turns=15):
            state = turn["state"]
            why = unrepresentable(state, turn["requests"])
            if why:
                skipped += 1
                reasons[why] = reasons.get(why, 0) + 1
                continue

            # Both sides' exact HP, so what is being tested is the machinery
            # and not the percent quantisation CLAUDE.md constraint 5 already
            # names as a known error on the opponent's side.
            lines = spectator(turn["log"], private_for={"p1", "p2"})
            view = with_formes(view_from(lines, dex, "p1", bring_of(state, 0)), state)
            payload = position_payload(view, {}, {})

            made = sim.call(
                "materialize",
                formatId=FORMAT_ID,
                seed=seed,
                p1={
                    "name": "p1",
                    "team": load_team(ALPHA),
                    "order": order_from(state, 0, load_team(ALPHA)),
                },
                p2={
                    "name": "p2",
                    "team": load_team(BETA),
                    "order": order_from(state, 1, load_team(BETA)),
                },
                position=payload,
            )
            assert made["problems"] == [], made["problems"]
            handle = int(made["handle"])
            try:
                # The request before the step: same HP, status, and move sets.
                real = turn["requests"]
                mine = sim.request(handle)
                for side in ("p1", "p2"):
                    real_mons = real[side]["side"]["pokemon"]
                    mine_mons = mine[side]["side"]["pokemon"]
                    assert [m["ident"] for m in mine_mons] == [m["ident"] for m in real_mons]
                    for a, b in zip(real_mons, mine_mons, strict=True):
                        assert a["condition"] == b["condition"], (side, a["ident"], state["turn"])
                        assert a["active"] == b["active"]
                    real_moves = [[m["id"] for m in a["moves"]] for a in real[side]["active"]]
                    mine_moves = [[m["id"] for m in a["moves"]] for a in mine[side]["active"]]
                    assert real_moves == mine_moves

                # The step, under the real battle's PRNG state.
                before = len(made["log"])
                stepped = sim.call(
                    "step",
                    handle=handle,
                    choices=turn["choices"],
                    seed=state["prng"],
                )
                assert all(stepped["accepted"].values()), stepped["accepted"]
                real_after = sim.serialize(turn["handle"])
                # `play` has not stepped the real battle yet at this point; the
                # real log for this turn is read on the next iteration. Compare
                # instead against a clone of the real battle stepped now.
                real_clone = int(sim.clone(turn["handle"])["handle"])
                try:
                    real_stepped = sim.step(
                        real_clone, turn["choices"].get("p1"), turn["choices"].get("p2")
                    )
                finally:
                    sim.destroy(real_clone)
                real_lines = stripped(real_stepped["log"][len(turn["log"]) :])
                mine_lines = stripped(stepped["log"][before:])
                assert mine_lines == real_lines, (state["turn"], game)
                del real_after
            finally:
                sim.destroy(handle)
            compared += 1

    print(f"materialize equivalence: {compared} turns compared, {skipped} skipped {reasons}")
    assert compared >= 12, f"too few comparable turns ({compared}); skipped {reasons}"


def test_the_observed_view_lands_within_a_percent_of_the_truth(sim: SimServer, dex: Dex) -> None:
    """The agent's real view has the opponent's HP in percent. Materialising
    from that view must land within the quantisation, not somewhere else."""
    checked = 0
    for turn in play(sim, [5, 6, 7, 8], turns=8):
        state = turn["state"]
        view = view_from(spectator(turn["log"], private_for={"p1"}), dex, "p1", bring_of(state, 0))
        view = with_formes(view, state)
        made = sim.call(
            "materialize",
            formatId=FORMAT_ID,
            seed=[1, 2, 3, 4],
            p1={
                "name": "p1",
                "team": load_team(ALPHA),
                "order": order_from(state, 0, load_team(ALPHA)),
            },
            p2={
                "name": "p2",
                "team": load_team(BETA),
                "order": order_from(state, 1, load_team(BETA)),
            },
            position=position_payload(view, {}, {}),
        )
        handle = int(made["handle"])
        try:
            mine = sim.request(handle)
            for a, b in zip(
                turn["requests"]["p2"]["side"]["pokemon"],
                mine["p2"]["side"]["pokemon"],
                strict=True,
            ):
                real_hp, max_hp = condition_hp(a["condition"])
                mine_hp, _ = condition_hp(b["condition"])
                assert abs(real_hp - mine_hp) <= max(1, round(max_hp / 100)), (a, b)
            checked += 1
        finally:
            sim.destroy(handle)
    assert checked > 0


# -- reading the simulator back --------------------------------------------------------


def test_sim_snapshot_agrees_with_the_observer(sim: SimServer, dex: Dex) -> None:
    checked = 0
    for turn in play(sim, [21, 22, 23, 24], turns=10):
        state = turn["state"]
        brought = bring_of(state, 0)
        view = with_formes(
            view_from(spectator(turn["log"], private_for={"p1"}), dex, "p1", brought), state
        )
        revealed = {p["name"] for p in view["theirs"]["active"] if p} | {
            p["name"] for p in view["theirs"]["bench"]
        }
        mine = sim_snapshot(state, "p1", dex, brought, revealed)

        assert mine["turn"] == view["turn"]
        assert set(mine["side_conditions"]) == set(view["side_conditions"])
        assert set(mine["opponent_side_conditions"]) == set(view["opponent_side_conditions"])
        assert set(mine["fields"]) == set(view["fields"])
        assert set(mine["weather"]) == set(view["weather"])

        for side, tolerance in (("ours", 0.05), ("theirs", 1.0)):
            actives = list(zip(mine[side]["active"], view[side]["active"], strict=True))
            for a, b in actives:
                assert (a is None) == (b is None)
                if a is None or b is None:
                    continue
                assert a["species"] == b["species"]
                assert a["fainted"] == b["fainted"]
                assert a["status"] == b["status"], (side, a["name"])
                assert a["boosts"] == b["boosts"], (side, a["name"])
                assert abs(a["hp_pct"] - b["hp_pct"]) <= tolerance, (side, a["name"])
            mine_bench = {p["name"]: p for p in mine[side]["bench"]}
            view_bench = {p["name"]: p for p in view[side]["bench"]}
            if side == "theirs":
                assert set(mine_bench) == set(view_bench)
            for name, b in view_bench.items():
                if name in mine_bench:
                    a = mine_bench[name]
                    assert a["fainted"] == b["fainted"]
                    assert abs(a["hp_pct"] - b["hp_pct"]) <= tolerance, (side, name)
        assert mine["theirs"]["revealed"] == view["theirs"]["revealed"]
        checked += 1
    assert checked > 0


# -- the model ------------------------------------------------------------------------------


def _position(sim: SimServer, dex: Dex) -> dict[str, Any]:
    """A real mid-game position from our side, with our moves filled in from
    the team so the choices below are legal."""
    turns = list(play(sim, [31, 32, 33, 34], turns=4))
    view = view_from(
        spectator(turns[-1]["log"], private_for={"p1"}), dex, "p1", bring_of(turns[-1]["state"], 0)
    )
    request = turns[-1]["requests"]["p1"]
    for slot, active in zip(view["ours"]["active"], request["active"], strict=False):
        if slot is not None:
            slot["moves"] = [{"id": m["id"], "pp": m["pp"]} for m in active["moves"]]
    return view


def _model(sim: SimServer, dex: Dex, replicates: int = 2) -> RolloutModel:
    return RolloutModel(
        sim,
        dex,
        FORMAT_ID,
        our_team=load_team(ALPHA),
        their_team=load_team(BETA),
        fallback=TurnModel(dex),
        seed=7,
        replicates=replicates,
    )


def test_the_rollout_matrix_is_a_deterministic_probability(sim: SimServer, dex: Dex) -> None:
    position = _position(sim, dex)
    ours: list[dict[str, Any]] = [
        {
            "message": f"/choose move {position['ours']['active'][0]['moves'][0]['id']}, move "
            f"{position['ours']['active'][1]['moves'][0]['id']}"
        },
        {"message": "/choose default"},
    ]
    theirs: list[dict[str, Any]] = [
        {"slots": [{"kind": "none"}, {"kind": "none"}]},
        {"slots": [{"kind": "move", "move": "protect", "target": 1}, {"kind": "none"}]},
    ]

    first = _model(sim, dex)
    assert first.begin(position, "battle-test-1")
    matrix = payoff_matrix(position, ours, theirs, first)
    first.end()

    second = _model(sim, dex)
    assert second.begin(position, "battle-test-1")
    again = payoff_matrix(position, ours, theirs, second)
    second.end()

    assert matrix.shape == (2, 2)
    assert np.all(matrix >= 0.0) and np.all(matrix <= 1.0)
    assert np.array_equal(matrix, again), "common random numbers must make this reproducible"
    assert first.stats.cells == 4 and first.stats.fallback_cells == 0
    assert first.stats.materialized


def test_a_rejected_choice_falls_back_and_is_counted(sim: SimServer, dex: Dex) -> None:
    position = _position(sim, dex)
    model = _model(sim, dex, replicates=1)
    assert model.begin(position, "battle-test-2")
    bogus = {"message": "/choose move nosuchmove 1, move nosuchmove 1"}
    column = {"slots": [{"kind": "none"}, {"kind": "none"}]}

    value = model.value(position, bogus, column)
    model.end()

    assert value == TurnModel(dex).value(position, bogus, column)
    assert model.stats.fallback_cells == 1 and model.stats.rejected == 1


def test_a_fainted_slot_with_no_bench_is_materialised_and_passes(sim: SimServer, dex: Dex) -> None:
    position = _position(sim, dex)
    position["ours"]["active"][1] = {
        **position["ours"]["active"][1],
        "fainted": True,
        "hp_pct": 0.0,
    }
    position["ours"]["bench"] = []
    model = _model(sim, dex, replicates=1)
    assert model.begin(position, "battle-test-4") is True
    first = next(
        m["id"]
        for m in position["ours"]["active"][0]["moves"]
        if dex.moves[m["id"]]["target"] == "normal"
    )
    value = model.value(
        position, {"message": f"/choose move {first} 1, pass"}, {"slots": [{"kind": "none"}]}
    )
    model.end()
    assert 0.0 <= value <= 1.0 and model.stats.fallback_cells == 0, model.stats.refusals


def test_an_empty_slot_is_held_by_a_fainted_pokemon(sim: SimServer, dex: Dex) -> None:
    """A side down to one Pokemon: the survivor stays in its slot, the other
    slot passes, and a target aimed at the survivor's slot lands on it."""
    position = _position(sim, dex)
    survivor = position["ours"]["active"][1]
    position["ours"]["active"] = [None, survivor]
    position["ours"]["bench"] = [
        {**p, "fainted": True, "hp_pct": 0.0, "hp": 0} for p in position["ours"]["bench"]
    ]
    model = _model(sim, dex, replicates=1)
    assert model.begin(position, "battle-test-3") is True, model.stats
    first = next(m["id"] for m in survivor["moves"] if dex.moves[m["id"]]["target"] == "normal")
    value = model.value(
        position,
        {"message": f"/choose pass, move {first} 1"},
        {"slots": [{"kind": "move", "move": "protect", "target": 1}, {"kind": "none"}]},
    )
    model.end()
    assert 0.0 <= value <= 1.0 and model.stats.fallback_cells == 0, model.stats.refusals


def test_an_empty_slot_with_nothing_fainted_is_not_materialised(sim: SimServer, dex: Dex) -> None:
    position = _position(sim, dex)
    position["ours"]["active"][1] = None
    position["ours"]["bench"] = [
        {**p, "fainted": False, "hp_pct": 100.0} for p in position["ours"]["bench"]
    ]
    model = _model(sim, dex)
    assert model.begin(position, "battle-test-5") is False
    assert model.stats.skipped == "empty slot with no fainted Pokemon to hold it"
    value = model.value(position, {"message": "/choose default"}, {"slots": []})
    assert 0.0 <= value <= 1.0 and model.stats.fallback_cells == 1

"""M8, the depth arm: one more ply on the analytic model.

The one-ply model's value is not checkable against ground truth and its
ordering is (`tests/test_payoff.py`). The same holds one ply down, with one
ordering that is the whole reason the ply exists: a switch the one-ply model
scores as giving up the turn, and the two-ply model scores as what the
incoming Pokemon does next turn. That is the switch bias `docs/STATUS.md`
names, as a test rather than a sentence.

The rest is plumbing that has to be right for the ordering to mean anything:
interior enumeration yields only actions the position allows, in the shape
the policy and the turn model already consume; the position between plies is
prepared the way the module docstring says; children are memoised; and the
agent completes real games with both matrices on the trace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from champions.dex.loader import Dex
from champions.harness.ladder import run_matchup
from champions.search.evaluate import win_prob
from champions.search.payoff import TurnModel, payoff_matrix
from champions.search.twoply import TwoPlyModel, enumerate_joint, is_terminal, next_turn
from champions.teams import ALPHA
from scripts.run_ladder import build_arm

FORMAT_ID = "gen9championsvgc2026regmb"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


def _mon(
    dex: Dex,
    species: str,
    hp_pct: float = 100.0,
    known: bool = True,
    fainted: bool = False,
    moves: list[str] | None = None,
    revealed_moves: list[str] | None = None,
) -> dict[str, Any]:
    entry = dex.species[species.lower().replace("-", "")]
    view: dict[str, Any] = {
        "species": entry["name"],
        "name": entry["name"],
        "types": entry["types"],
        "base_stats": entry["baseStats"],
        "hp_pct": 0.0 if fainted else hp_pct,
        "fainted": fainted,
        "status": None,
        "boosts": {},
        "active": True,
        "known": known,
        "first_turn": False,
        "protect_counter": 0,
    }
    if known:
        view["stats"] = {k: v + 32 + 20 for k, v in entry["baseStats"].items() if k != "hp"}
        view["max_hp"] = entry["baseStats"]["hp"] + 32 + 75
        view["hp"] = round(view["max_hp"] * view["hp_pct"] / 100)
        view["selected"] = True
        view["moves"] = [{"id": m, "pp": 20} for m in (moves or [])]
    else:
        view["revealed_moves"] = [{"id": m} for m in (revealed_moves or [])]
    return view


def _side(active: list[Any], bench: list[Any]) -> dict[str, Any]:
    seen = [p for p in active if p] + bench
    return {
        "active": active,
        "bench": bench,
        "remaining": sum(1 for p in seen if not p["fainted"]),
        "revealed": len(seen),
    }


def _state(ours: dict[str, Any], theirs: dict[str, Any], turn: int = 3) -> dict[str, Any]:
    return {
        "turn": turn,
        "weather": {},
        "fields": {},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": ours,
        "theirs": theirs,
    }


def _move(dex: Dex, move_id: str, target: int) -> dict[str, Any]:
    entry = dex.move(move_id)
    return {
        "kind": "move",
        "move": move_id,
        "name": entry["name"],
        "type": entry["type"],
        "category": entry["category"],
        "base_power": entry["basePower"],
        "priority": entry.get("priority", 0),
        "target": target,
        "label": f"{entry['name']} -> {target}",
    }


def _switch(species: str) -> dict[str, Any]:
    return {"kind": "switch", "species": species, "name": species, "label": f"switch to {species}"}


def _act(*slots: dict[str, Any]) -> dict[str, Any]:
    return {"message": "|".join(s.get("label", "?") for s in slots), "slots": list(slots)}


NOTHING = _act({"kind": "none", "label": "unrevealed"}, {"kind": "none", "label": "unrevealed"})


@pytest.fixture
def position(dex: Dex) -> dict[str, Any]:
    """Garchomp and Metagross out, Empoleon and a fainted Pelipper behind;
    Dragonite and Skarmory across, nothing revealed."""
    return _state(
        _side(
            [
                _mon(dex, "Garchomp", moves=["earthquake", "dragonclaw", "protect"]),
                _mon(dex, "Metagross", moves=["ironhead", "protect"]),
            ],
            [_mon(dex, "Empoleon", moves=["icebeam"]), _mon(dex, "Pelipper", fainted=True)],
        ),
        _side(
            [_mon(dex, "Dragonite", known=False), _mon(dex, "Skarmory", known=False)],
            [],
        ),
    )


# -- interior enumeration ----------------------------------------------------


def test_enumeration_yields_only_what_the_position_allows(dex: Dex, position: Any) -> None:
    joint = enumerate_joint(position, dex)

    # Slot 1: Earthquake (no target choice), Dragon Claw at each foe, Protect,
    # switch to Empoleon. Slot 2: Iron Head at each foe, Protect, the switch.
    # 5 x 4 minus the pair that switches both into Empoleon.
    assert len(joint) == 19
    assert len({j["message"] for j in joint}) == len(joint), "messages must be unique"

    for action in joint:
        assert set(action) >= {"message", "slots", "label", "kinds"}
        assert action["message"].startswith("/choose ")
        for slot in action["slots"]:
            assert slot["kind"] in {"move", "switch"}
            if slot["kind"] == "switch":
                assert slot["species"] == "Empoleon", "the fainted Pelipper must not be offered"

    first_slot = {(s["slots"][0].get("move"), s["slots"][0].get("target")) for s in joint}
    assert ("earthquake", 0) in first_slot
    assert ("dragonclaw", 1) in first_slot and ("dragonclaw", 2) in first_slot
    assert ("protect", 0) in first_slot
    assert not any(
        a["slots"][0]["kind"] == "switch" and a["slots"][1]["kind"] == "switch" for a in joint
    ), "both slots cannot switch into the one bench Pokemon"


def test_a_fainted_partner_passes(dex: Dex, position: Any) -> None:
    position["ours"]["active"][1] = _mon(dex, "Metagross", fainted=True)
    joint = enumerate_joint(position, dex)

    assert len(joint) == 5
    assert all(a["slots"][1]["kind"] == "pass" for a in joint)


def test_a_fainted_foe_is_not_a_target(dex: Dex, position: Any) -> None:
    position["theirs"]["active"][1] = _mon(dex, "Skarmory", known=False, fainted=True)
    joint = enumerate_joint(position, dex)

    targets = {s["target"] for a in joint for s in a["slots"] if s["kind"] == "move"}
    assert 2 not in targets


# -- between the plies ---------------------------------------------------------


def test_next_turn_refills_our_fainted_slot_and_marks_it_fresh(dex: Dex, position: Any) -> None:
    position["ours"]["active"][0] = _mon(dex, "Garchomp", fainted=True)

    child = next_turn(position)

    assert child["turn"] == position["turn"] + 1
    assert child["ours"]["active"][0]["species"] == "Empoleon"
    assert child["ours"]["active"][0]["first_turn"] is True
    assert child["ours"]["active"][1]["first_turn"] is False
    assert any(p["species"] == "Garchomp" for p in child["ours"]["bench"])
    assert position["ours"]["active"][0]["fainted"], "the input must not be modified"


def test_next_turn_counts_protect_and_clears_the_flags(dex: Dex, position: Any) -> None:
    position["ours"]["active"][0] = {**position["ours"]["active"][0], "_protected": True}
    position["ours"]["active"][1] = {**position["ours"]["active"][1], "protect_counter": 2}

    child = next_turn(position)

    assert child["ours"]["active"][0]["protect_counter"] == 1
    assert child["ours"]["active"][1]["protect_counter"] == 0
    assert "_protected" not in child["ours"]["active"][0]


def test_their_fainted_slot_stays_empty(dex: Dex, position: Any) -> None:
    position["theirs"]["active"][0] = _mon(dex, "Dragonite", known=False, fainted=True)
    child = next_turn(position)
    assert child["theirs"]["active"][0]["fainted"]


def test_terminal_when_a_side_is_wiped(dex: Dex, position: Any) -> None:
    position["theirs"]["active"] = [
        _mon(dex, "Dragonite", known=False, fainted=True),
        _mon(dex, "Skarmory", known=False, fainted=True),
    ]
    position["theirs"]["bench"] = [
        _mon(dex, "Pelipper", known=False, fainted=True),
        _mon(dex, "Milotic", known=False, fainted=True),
    ]
    assert is_terminal(position, dex.picked_team_size)

    model = TwoPlyModel(dex)
    assert model.child_value(position) == win_prob(next_turn(position))
    assert model.stats.children_solved == 0


# -- the switch is placed at depth -------------------------------------------


def test_the_one_ply_model_leaves_the_slot_empty_and_the_deep_one_fills_it(
    dex: Dex, position: Any
) -> None:
    switching = _act(_switch("Empoleon"), _move(dex, "ironhead", 1))

    shallow = TurnModel(dex).outcomes(position, switching, NOTHING)[0].snapshot
    assert shallow["ours"]["active"][0] is None

    deep = TurnModel(dex, place_incoming=True).outcomes(position, switching, NOTHING)[0].snapshot
    assert deep["ours"]["active"][0]["species"] == "Empoleon"
    assert deep["ours"]["active"][0]["_placed"] is True
    assert any(p["species"] == "Garchomp" for p in deep["ours"]["bench"])
    assert not any(p["species"] == "Empoleon" for p in deep["ours"]["bench"])


def test_depth_sees_the_switch_the_one_ply_model_cannot(dex: Dex) -> None:
    """The switch bias, measured rather than described.

    Garchomp has Pound and Metagross has Protect; Dragonite is at 60%.
    Empoleon, behind, knocks Dragonite out with Ice Beam from there. One ply
    scores the switch as a lost turn and prefers the chip. Two plies see
    Empoleon's Ice Beam, and prefer the switch.
    """
    state = _state(
        _side(
            [_mon(dex, "Garchomp", moves=["pound"]), _mon(dex, "Metagross", moves=["protect"])],
            [_mon(dex, "Empoleon", moves=["icebeam"])],
        ),
        _side([_mon(dex, "Dragonite", 60.0, known=False), _mon(dex, "Skarmory", known=False)], []),
    )
    attack = _act(_move(dex, "pound", 1), _move(dex, "protect", 0))
    switch = _act(_switch("Empoleon"), _move(dex, "protect", 0))

    shallow = TurnModel(dex)
    assert shallow.value(state, attack, NOTHING) > shallow.value(state, switch, NOTHING)

    deep = TwoPlyModel(dex)
    assert deep.value(state, switch, NOTHING) > deep.value(state, attack, NOTHING)


# -- the matrix ------------------------------------------------------------------


def test_the_matrix_is_deterministic_and_a_probability(dex: Dex, position: Any) -> None:
    ours = enumerate_joint(position, dex)[:4]
    theirs = [NOTHING, _act(_move(dex, "icebeam", 1), {"kind": "none", "label": "-"})]

    first = TwoPlyModel(dex).matrix(position, ours, theirs)
    second = TwoPlyModel(dex).matrix(position, ours, theirs)

    assert first.shape == (4, 2)
    assert np.array_equal(first, second)
    assert np.all(first >= 0.0) and np.all(first <= 1.0)


def test_identical_children_hit_the_memo(dex: Dex, position: Any) -> None:
    ours = enumerate_joint(position, dex)[:3]
    model = TwoPlyModel(dex)
    model.matrix(position, ours, [NOTHING, NOTHING])

    assert model.stats.memo_hits > 0
    assert model.stats.children_solved > 0


def test_payoff_matrix_accepts_the_deep_model(dex: Dex, position: Any) -> None:
    """`payoff_matrix` is the seam `discard.MatrixFn` and the agents share."""
    ours = enumerate_joint(position, dex)[:2]
    matrix = payoff_matrix(position, ours, [NOTHING], TwoPlyModel(dex))
    assert matrix.shape == (2, 1)


# -- the agent, against a real simulator --------------------------------------


async def test_the_agent_completes_games_with_both_matrices_on_the_trace(
    showdown_server: int, tmp_path: Path
) -> None:
    n_games = 2
    results = await run_matchup(
        build_arm("twoply", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        n_games,
        tmp_path,
        seed=13,
    )
    assert sum(r.games for r in results) == 2 * n_games

    traces = sorted(tmp_path.glob("*.twoply13.jsonl"))
    assert traces, "no two-ply traces written"

    solved = 0
    for path in traces:
        for line in path.open(encoding="utf-8"):
            event = json.loads(line)
            payload = event.get("payload", {})
            if event["type"] != "candidates" or payload.get("phase") != "pruned":
                continue
            solved += 1
            assert payload["model"] == "analytic-two-ply"
            assert payload["k2"] > 0
            assert len(payload["payoff_one_ply"]) == len(payload["payoff"])
            assert 0.0 <= payload["game_value_one_ply"] <= 1.0
            assert payload["children_solved"] + payload["leaves"] > 0
            assert {"payoff_one_ply_s", "payoff_two_ply_s"} <= set(payload["timings"])
    assert solved > 0

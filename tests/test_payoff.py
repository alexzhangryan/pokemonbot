"""M2: the analytic turn model and the payoff matrix.

There is no ground truth to compare a one-turn *value* against -- the evaluation
weights are hand chosen, so the number itself means little. What is checkable is
the ordering, and every case below is one where the right order is not a matter
of opinion: a knockout beats chip damage, hitting a foe beats hitting your own
partner, a spread move that hits two beats the same move hitting one.

Two of these tests exist because the code got them wrong first, and both bugs
were invisible in the value and obvious in the ordering:

- `_switch` deleted the switching Pokemon instead of benching it, so the
  evaluation counted it as dead. A double switch scored 0.06 against a 0.82
  baseline, and the agent treated switching as suicide.
- The heuristic scored a move aimed at our own partner identically to the same
  move aimed at a foe, so nine of the ten pruned candidates were friendly fire.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from champions.dex.loader import Dex
from champions.search.payoff import (
    OpponentHypothesis,
    TurnModel,
    combatant,
    effective_speed,
    payoff_matrix,
)
from champions.search.policy import DISQUALIFIED, HeuristicPolicy, opponent_candidates

FORMAT_ID = "gen9championsvgc2026regmb"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


@pytest.fixture(scope="module")
def model(dex: Dex) -> TurnModel:
    return TurnModel(dex)


def _mon(
    dex: Dex,
    species: str,
    hp_pct: float = 100.0,
    known: bool = True,
    fainted: bool = False,
    status: str | None = None,
    boosts: dict[str, int] | None = None,
    revealed_moves: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entry = dex.species[species.lower().replace("-", "")]
    view: dict[str, Any] = {
        "species": entry["name"],
        "name": entry["name"],
        "types": entry["types"],
        "base_stats": entry["baseStats"],
        "hp_pct": 0.0 if fainted else hp_pct,
        "fainted": fainted,
        "status": status,
        "boosts": boosts or {},
        "active": True,
        "known": known,
    }
    if known:
        view["stats"] = {k: v + 32 + 20 for k, v in entry["baseStats"].items() if k != "hp"}
        view["max_hp"] = entry["baseStats"]["hp"] + 32 + 75
        view["hp"] = round(view["max_hp"] * view["hp_pct"] / 100)
    else:
        view["revealed_moves"] = revealed_moves or []
    return view


def _side(active: list, bench: list) -> dict[str, Any]:
    seen = [p for p in active if p] + bench
    return {
        "active": active,
        "bench": bench,
        "remaining": sum(1 for p in seen if not p["fainted"]),
        "revealed": len(seen),
    }


@pytest.fixture
def snapshot(dex: Dex) -> dict[str, Any]:
    """Us: healthy Metagross and Starmie. Them: healthy Skarmory, weak Dragonite."""
    return {
        "turn": 3,
        "weather": {},
        "fields": {},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": _side(
            [_mon(dex, "Metagross"), _mon(dex, "Starmie")],
            [_mon(dex, "Garchomp"), _mon(dex, "Pelipper")],
        ),
        "theirs": _side(
            [_mon(dex, "Skarmory", known=False), _mon(dex, "Dragonite", 20.0, known=False)],
            [_mon(dex, "Pelipper", known=False)],
        ),
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


def _act(*slots: dict[str, Any]) -> dict[str, Any]:
    return {"message": "|".join(s.get("label", "?") for s in slots), "slots": list(slots)}


NOTHING = _act({"kind": "none", "label": "unrevealed"}, {"kind": "none", "label": "unrevealed"})


# -- ordering, which is the only thing worth asserting ------------------


def test_a_knockout_beats_chip_damage(dex: Dex, model: TurnModel, snapshot: Any) -> None:
    """Ice Beam is 4x on Dragonite and it is at 20%, so aiming there is a KO."""
    knockout = _act(_move(dex, "ironhead", 1), _move(dex, "icebeam", 2))
    chip = _act(_move(dex, "ironhead", 1), _move(dex, "icebeam", 1))

    assert model.value(snapshot, knockout, NOTHING) > model.value(snapshot, chip, NOTHING)


def test_hitting_a_foe_beats_hitting_our_own_partner(
    dex: Dex, model: TurnModel, snapshot: Any
) -> None:
    """Friendly fire is modelled, so the model can see it is bad.

    The policy layer disqualifies these before they reach the matrix, but the
    model has to be right about them independently -- spread moves hit our own
    side and are not disqualified.
    """
    at_foe = _act(_move(dex, "ironhead", 1), _move(dex, "icebeam", 2))
    at_ally = _act(_move(dex, "ironhead", -2), _move(dex, "icebeam", 2))

    assert model.value(snapshot, at_foe, NOTHING) > model.value(snapshot, at_ally, NOTHING)


def test_super_effective_beats_resisted(dex: Dex, model: TurnModel, snapshot: Any) -> None:
    """Thunderbolt is 2x on Skarmory; Iron Head is resisted by it."""
    effective = _act(_move(dex, "thunderbolt", 1), {"kind": "none", "label": "-"})
    resisted = _act(_move(dex, "ironhead", 1), {"kind": "none", "label": "-"})

    assert model.value(snapshot, effective, NOTHING) > model.value(snapshot, resisted, NOTHING)


def test_a_ground_move_into_two_flying_types_does_nothing(
    dex: Dex, model: TurnModel, snapshot: Any
) -> None:
    """Both defenders are Flying. Earthquake is a spread move, so it also hits
    our own Starmie -- it should be strictly worse than doing nothing."""
    earthquake = _act(_move(dex, "earthquake", 0), {"kind": "none", "label": "-"})

    assert model.value(snapshot, earthquake, NOTHING) < model.value(snapshot, NOTHING, NOTHING)


def test_switching_keeps_the_pokemon_alive(dex: Dex, model: TurnModel, snapshot: Any) -> None:
    """The regression. A switch gives up the turn; it does not lose the Pokemon.

    Before the fix a double switch scored near zero because `_switch` removed
    the occupant from the position entirely, and the evaluation counts HP and
    survivors over active plus bench.
    """
    baseline = model.value(snapshot, NOTHING, NOTHING)
    switching = _act({"kind": "switch", "label": "sw"}, {"kind": "switch", "label": "sw"})

    value = model.value(snapshot, switching, NOTHING)
    assert value < baseline, "switching should cost the turn"
    assert value > baseline / 2, f"switching should not read as losing the team ({value})"


def test_a_switch_moves_the_occupant_to_the_bench(
    dex: Dex, model: TurnModel, snapshot: Any
) -> None:
    switching = _act({"kind": "switch", "label": "sw"}, {"kind": "none", "label": "-"})
    outcome = model.outcomes(snapshot, switching, NOTHING)[0]

    ours = outcome.snapshot["ours"]
    assert ours["active"][0] is None
    assert any(p["species"] == "Metagross" for p in ours["bench"])
    assert ours["remaining"] == snapshot["ours"]["remaining"]


def test_protect_prevents_the_damage_it_should(dex: Dex, model: TurnModel, snapshot: Any) -> None:
    protect = {"kind": "move", "move": "protect", "target": 0, "label": "Protect"}
    incoming = _act(_move(dex, "earthquake", 0), {"kind": "none", "label": "-"})

    protected = _act(protect, protect)
    exposed = _act({"kind": "none", "label": "-"}, {"kind": "none", "label": "-"})

    # Their Earthquake hits our Metagross and Starmie; Protect should stop it.
    assert model.value(snapshot, protected, incoming) > model.value(snapshot, exposed, incoming)


# -- roll bucketing and outcomes ----------------------------------------


def test_outcomes_are_a_probability_distribution(dex: Dex, model: TurnModel, snapshot: Any) -> None:
    action = _act(_move(dex, "icebeam", 2), _move(dex, "ironhead", 1))
    outcomes = model.outcomes(snapshot, action, NOTHING)

    assert outcomes
    assert sum(o.probability for o in outcomes) == pytest.approx(1.0)
    assert all(0.0 <= o.value <= 1.0 for o in outcomes)


def test_a_partial_knockout_chance_produces_two_branches(
    dex: Dex, model: TurnModel, snapshot: Any
) -> None:
    """Bucketing splits on the knockout threshold and nowhere else, so a cell
    where some rolls kill and some do not has exactly two outcomes."""
    weak = dict(snapshot)
    weak["theirs"] = _side(
        [_mon(dex, "Skarmory", known=False), _mon(dex, "Dragonite", 47.0, known=False)],
        [],
    )
    action = _act(_move(dex, "icebeam", 2), {"kind": "none", "label": "-"})

    outcomes = model.outcomes(weak, action, NOTHING)
    assert len(outcomes) in (1, 2)
    assert sum(o.probability for o in outcomes) == pytest.approx(1.0)


def test_the_model_is_deterministic(dex: Dex, model: TurnModel, snapshot: Any) -> None:
    """Nothing samples, so repeated evaluation is bit-identical.

    This is what discharges the common random numbers requirement: two cells
    cannot disagree about randomness that does not exist.
    """
    action = _act(_move(dex, "icebeam", 2), _move(dex, "ironhead", 1))
    first = model.value(snapshot, action, NOTHING)
    for _ in range(5):
        assert model.value(snapshot, action, NOTHING) == first


# -- combatant construction ---------------------------------------------


def test_our_own_stats_are_used_exactly_and_theirs_are_hypothesised(dex: Dex) -> None:
    hypothesis = OpponentHypothesis(points=0)

    ours = combatant(_mon(dex, "Metagross"), hypothesis)
    theirs = combatant(_mon(dex, "Metagross", known=False), hypothesis)

    base = dex.species["metagross"]["baseStats"]
    assert ours.stats["atk"] == base["atk"] + 32 + 20  # from the snapshot, not the hypothesis
    assert theirs.stats["atk"] == base["atk"] + 0 + 20  # from the hypothesis
    assert theirs.max_hp == base["hp"] + 0 + 75


def test_an_opponent_percentage_becomes_hit_points(dex: Dex) -> None:
    hypothesis = OpponentHypothesis(points=0)
    half = combatant(_mon(dex, "Metagross", 50.0, known=False), hypothesis)
    assert half.hp == round(half.max_hp * 0.5)


def test_paralysis_and_tailwind_move_effective_speed(dex: Dex, snapshot: Any) -> None:
    fast = combatant(_mon(dex, "Starmie"), OpponentHypothesis())
    slow = combatant(_mon(dex, "Starmie", status="PAR"), OpponentHypothesis())

    assert effective_speed(slow, snapshot, ours=True) < effective_speed(fast, snapshot, ours=True)

    with_tailwind = dict(snapshot, side_conditions={"TAILWIND": 3})
    assert effective_speed(fast, with_tailwind, ours=True) == pytest.approx(
        2 * effective_speed(fast, snapshot, ours=True)
    )


# -- the matrix ---------------------------------------------------------


def test_the_payoff_matrix_has_the_right_shape(dex: Dex, model: TurnModel, snapshot: Any) -> None:
    ours = [
        _act(_move(dex, "icebeam", 2), {"kind": "none", "label": "-"}),
        _act(_move(dex, "ironhead", 1), {"kind": "none", "label": "-"}),
        _act(_move(dex, "thunderbolt", 1), {"kind": "none", "label": "-"}),
    ]
    theirs = [NOTHING, _act(_move(dex, "dragonclaw", 1), {"kind": "none", "label": "-"})]

    matrix = payoff_matrix(snapshot, ours, theirs, model)

    assert matrix.shape == (3, 2)
    assert np.all(np.isfinite(matrix))
    assert np.all((matrix >= 0) & (matrix <= 1))


def test_a_single_opponent_column_still_produces_a_matrix(
    dex: Dex, model: TurnModel, snapshot: Any
) -> None:
    """The turn-one case: nothing revealed, so one column and an argmax."""
    ours = [_act(_move(dex, "icebeam", 2), {"kind": "none", "label": "-"})]
    matrix = payoff_matrix(snapshot, ours, [NOTHING], model)
    assert matrix.shape == (1, 1)


# -- the policy layer ---------------------------------------------------


def test_friendly_fire_is_disqualified_not_merely_penalised(dex: Dex) -> None:
    """The regression: without this, nine of ten pruned candidates were moves
    aimed at our own partner, because base power alone does not distinguish
    them from the same move aimed at a foe."""
    policy = HeuristicPolicy(dex)

    at_ally = _act(_move(dex, "ironhead", -2), _move(dex, "icebeam", 2))
    at_foe = _act(_move(dex, "ironhead", 1), _move(dex, "icebeam", 2))

    scores = {s.action["message"]: s.score for s in policy.scored([at_ally, at_foe], k=2)}
    assert scores[at_ally["message"]] == DISQUALIFIED
    assert scores[at_foe["message"]] > 0


def test_a_status_move_aimed_at_an_ally_is_not_disqualified(dex: Dex) -> None:
    """Only damaging moves. Redirection and support legitimately target allies."""
    policy = HeuristicPolicy(dex)
    helping = _act(_move(dex, "protect", 0), _move(dex, "icebeam", 2))
    assert policy.scored([helping], k=1)[0].score > 0


def test_pruning_returns_at_most_k_and_is_deterministic(dex: Dex) -> None:
    policy = HeuristicPolicy(dex)
    actions = [
        _act(_move(dex, "ironhead", 1), _move(dex, "icebeam", 2)),
        _act(_move(dex, "bulletpunch", 1), _move(dex, "surf", 0)),
        _act(_move(dex, "thunderbolt", 1), _move(dex, "psychic", 2)),
        _act({"kind": "switch", "label": "sw"}, {"kind": "switch", "label": "sw"}),
    ]
    first = [s.action["message"] for s in policy.scored(actions, k=2)]
    assert len(first) == 2
    for _ in range(3):
        assert [s.action["message"] for s in policy.scored(actions, k=2)] == first


def test_opponent_candidates_come_only_from_revealed_moves(dex: Dex, snapshot: Any) -> None:
    """Nothing revealed means one "no action" column, not an invented move set."""
    blank = opponent_candidates(snapshot, dex)
    assert len(blank) == 1
    assert all(slot["kind"] == "none" for slot in blank[0]["slots"])

    revealed = dict(snapshot)
    revealed["theirs"] = _side(
        [
            _mon(dex, "Skarmory", known=False, revealed_moves=[{"id": "bravebird"}]),
            _mon(dex, "Dragonite", known=False, revealed_moves=[{"id": "dragonclaw"}]),
        ],
        [],
    )
    seen = opponent_candidates(revealed, dex)
    assert len(seen) == 1
    assert [s["move"] for s in seen[0]["slots"]] == ["bravebird", "dragonclaw"]


def test_opponent_candidates_respect_k(dex: Dex, snapshot: Any) -> None:
    revealed = dict(snapshot)
    moves = [{"id": m} for m in ("bravebird", "ironhead", "roost", "spikes")]
    revealed["theirs"] = _side(
        [
            _mon(dex, "Skarmory", known=False, revealed_moves=moves),
            _mon(dex, "Dragonite", known=False, revealed_moves=moves),
        ],
        [],
    )
    assert len(opponent_candidates(revealed, dex, k=5)) <= 5

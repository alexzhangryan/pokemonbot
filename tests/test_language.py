"""Implementation C: the language-model candidate provider's glue.

The model itself is stubbed here -- a `FakeClient` returns a scripted ordering,
so these tests need the dex (to compute the briefs' numbers) but no network. What
they pin is everything around the model: that the shortlist is A's, that the
model's order is honoured, that the briefs carry the computed damage the spec
says the model must be shown, that a dead backend falls back to A rather than
raising, and that the budget still slices the result to `k`.

`champions.search.llm`'s own parse/cache/transport are tested in
`tests/test_llm.py`, dex-free.
"""

from __future__ import annotations

from typing import Any

import pytest

from champions.dex.loader import Dex
from champions.search.language import LanguagePolicy
from champions.search.llm import LLMError
from champions.search.policy import HeuristicPolicy

FORMAT_ID = "gen9championsvgc2026regmb"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


class RecordingClient:
    """Returns a fixed reply and remembers the prompt it was handed."""

    name = "fake:recording"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompt = ""

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return self.reply


class RaisingClient:
    name = "fake:raises"

    def complete(self, prompt: str) -> str:
        raise LLMError("server down")


# -- snapshot helpers, matching tests/test_policy.py -------------------------


def _mon(
    dex: Dex,
    species: str,
    hp_pct: float = 100.0,
    known: bool = True,
    fainted: bool = False,
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
        "protect_counter": 0,
        "first_turn": False,
    }
    if known:
        view["stats"] = {k: v + 32 + 20 for k, v in entry["baseStats"].items() if k != "hp"}
        view["max_hp"] = entry["baseStats"]["hp"] + 32 + 75
        view["hp"] = round(view["max_hp"] * view["hp_pct"] / 100)
    else:
        view["revealed_moves"] = [
            {
                "id": move_id,
                "name": dex.move(move_id)["name"],
                "type": dex.move(move_id)["type"],
                "category": dex.move(move_id)["category"],
                "base_power": dex.move(move_id)["basePower"],
                "priority": dex.move(move_id).get("priority", 0),
                "target": dex.move(move_id).get("target"),
            }
            for move_id in (revealed_moves or [])
        ]
    return view


def _side(active: list, bench: list) -> dict[str, Any]:
    seen = [p for p in active if p] + bench
    return {
        "active": active,
        "bench": bench,
        "remaining": sum(1 for p in seen if not p["fainted"]),
        "revealed": len(seen),
    }


def _state(ours: list, theirs: list, turn: int = 3) -> dict[str, Any]:
    return {
        "turn": turn,
        "weather": {},
        "fields": {},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": _side(ours, []),
        "theirs": _side(theirs, []),
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


PASS: dict[str, Any] = {"kind": "pass", "label": "pass"}


def _act(*slots: dict[str, Any]) -> dict[str, Any]:
    return {"message": "|".join(s.get("label", "?") for s in slots), "slots": list(slots)}


@pytest.fixture
def scenario(dex: Dex) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A small legal set and a state, with three distinguishable actions."""
    state = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Skarmory", known=False), _mon(dex, "Dragonite", 15.0, known=False)],
    )
    actions = [
        _act(_move(dex, "iceshard", 2), PASS),
        _act(_move(dex, "ironhead", 1), PASS),
        _act(_move(dex, "bulletpunch", 1), PASS),
    ]
    return state, actions


def _heuristic_order(dex: Dex, state: dict[str, Any], actions: list[dict[str, Any]]) -> list[str]:
    scored = HeuristicPolicy(dex).scored(actions, k=len(actions), state=state)
    return [s.action["message"] for s in scored]


# -- the glue ----------------------------------------------------------------


def test_the_model_order_is_honoured(dex: Dex, scenario: Any) -> None:
    state, actions = scenario
    heuristic = _heuristic_order(dex, state, actions)

    # The model reverses A's shortlist. If C honours the model, the result is
    # A's order reversed -- which it would never produce on its own.
    reply = "[" + ", ".join(str(i) for i in reversed(range(len(actions)))) + "]"
    policy = LanguagePolicy(dex, client=RecordingClient(reply), shortlist=len(actions))

    got = [a["message"] for a in policy.candidates(actions, state, None, k=len(actions))]
    assert got == list(reversed(heuristic))


def test_the_briefs_carry_computed_numbers(dex: Dex, scenario: Any) -> None:
    state, actions = scenario
    client = RecordingClient("[0, 1, 2]")
    LanguagePolicy(dex, client=client, shortlist=len(actions)).candidates(
        actions, state, None, k=len(actions)
    )

    # The prompt the model saw names candidates by index and shows a damage
    # percentage -- the "engine computed the numbers" property the spec requires.
    assert "0:" in client.prompt and "1:" in client.prompt
    assert "%" in client.prompt
    assert "Ice Shard" in client.prompt


def test_a_dead_backend_falls_back_to_the_heuristic(dex: Dex, scenario: Any) -> None:
    state, actions = scenario
    heuristic = _heuristic_order(dex, state, actions)

    policy = LanguagePolicy(dex, client=RaisingClient(), shortlist=len(actions))
    got = [a["message"] for a in policy.candidates(actions, state, None, k=len(actions))]
    assert got == heuristic


def test_no_state_is_the_heuristic_order(dex: Dex, scenario: Any) -> None:
    _, actions = scenario
    client = RecordingClient("[2, 1, 0]")
    policy = LanguagePolicy(dex, client=client, shortlist=len(actions))

    kept = policy.candidates(actions, state=None, belief=None, k=len(actions))
    got = [a["message"] for a in kept]
    # Without a board there is nothing to put in a brief, so the model is never
    # called and the ordering is A's state-free one.
    assert client.prompt == ""
    assert got == [a["message"] for a in HeuristicPolicy(dex).scored(actions, k=len(actions))]


def test_the_budget_slices_the_result(dex: Dex, scenario: Any) -> None:
    state, actions = scenario
    policy = LanguagePolicy(dex, client=RecordingClient("[2, 0, 1]"), shortlist=len(actions))

    kept = policy.candidates(actions, state, None, k=2)
    assert len(kept) == 2
    # The model put index 2 first, so it leads the kept set.
    assert kept[0]["message"] == actions[2]["message"]


def test_it_is_deterministic_given_a_deterministic_client(dex: Dex, scenario: Any) -> None:
    state, actions = scenario
    policy = LanguagePolicy(dex, client=RecordingClient("[1, 2, 0]"), shortlist=len(actions))

    first = [a["message"] for a in policy.candidates(actions, state, None, k=len(actions))]
    second = [a["message"] for a in policy.candidates(actions, state, None, k=len(actions))]
    assert first == second

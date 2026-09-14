"""The lead sweep at team preview (D86)."""

from __future__ import annotations

from typing import Any

import pytest

from champions.dex.loader import Dex
from champions.search.lead import LeadChoice, lead_sweep, opening
from champions.search.payoff import TurnModel
from champions.search.policy import HeuristicPolicy
from tests.test_turn_effects import _mon

FORMAT_ID = "gen9championsvgc2026regmc"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


def _ours(dex: Dex, *species: str) -> list[dict[str, Any]]:
    views = []
    for name in species:
        view = _mon(dex, name)
        view["moves"] = [
            {"id": m, "pp": 16}
            for m in {
                "sneasler": ["closecombat", "direclaw", "fakeout", "protect"],
                "kingambit": ["kowtowcleave", "ironhead", "suckerpunch", "protect"],
                "garchomp": ["dragonclaw", "earthquake", "rockslide", "protect"],
                "dragonite": ["dragonpulse", "heatwave", "extremespeed", "protect"],
            }[name.lower()]
        ]
        view["selected"] = False
        views.append(view)
    return views


def _theirs(dex: Dex, *species: str) -> list[dict[str, Any]]:
    return [_mon(dex, name, known=False) for name in species]


BELIEVED = {
    "indeedeef": ["expandingforce", "followme", "trickroom", "helpinghand"],
    "gardevoir": ["expandingforce", "moonblast", "trickroom", "protect"],
    "incineroar": ["fakeout", "flareblitz", "knockoff", "partingshot"],
    "rillaboom": ["fakeout", "grassyglide", "woodhammer", "uturn"],
}
ABILITIES = {"indeedeef": "psychicsurge", "incineroar": "intimidate"}


def test_the_opening_applies_entry_abilities(dex: Dex) -> None:
    ours = _ours(dex, "Sneasler", "Kingambit", "Garchomp")
    theirs = _theirs(dex, "Indeedee-F", "Incineroar", "Gardevoir")
    snapshot = opening(ours, theirs, (0, 1), (0, 1), dex, ABILITIES.get)
    assert snapshot["fields"] == {"PSYCHIC_TERRAIN": 0}
    assert snapshot["ours"]["active"][0]["boosts"] == {"atk": -1}, "Intimidate on the way in"
    assert all(v["selected"] for v in snapshot["ours"]["active"] + snapshot["ours"]["bench"])
    assert len(snapshot["ours"]["bench"]) == 1
    assert [v["species"] for v in snapshot["theirs"]["active"]] == ["indeedeef", "incineroar"]


def test_the_sweep_keeps_the_four_times_weak_lead_out_of_the_terrain(dex: Dex) -> None:
    """Sneasler is four times weak to Psychic; on Indeedee's terrain it is not
    the lead, and Kingambit, immune to it, is."""
    ours = _ours(dex, "Sneasler", "Kingambit", "Garchomp", "Dragonite")
    theirs = _theirs(dex, "Indeedee-F", "Gardevoir", "Incineroar", "Rillaboom")
    choice = lead_sweep(
        ours,
        theirs,
        dex,
        TurnModel(dex, place_incoming=True),
        HeuristicPolicy(dex),
        lambda s: BELIEVED.get(s, []),
        believed_ability=ABILITIES.get,
        seed=3,
        budget_s=60.0,
    )
    assert isinstance(choice, LeadChoice)
    assert choice.rounds == choice.of_rounds == 6, "the whole sweep fits the budget"
    assert sorted(choice.order) == sorted(set(choice.order)) and len(choice.order) == 4
    assert choice.as_message().startswith("/team ")
    # The mean over their six leads decides; the Indeedee opening itself is
    # what has to price Sneasler out and Kingambit in.
    from champions.search.lead import _value

    model, policy = TurnModel(dex, place_incoming=True), HeuristicPolicy(dex)
    believed = BELIEVED.get
    exposed = opening(ours, theirs, (0, 3), (0, 1), dex, ABILITIES.get)
    immune = opening(ours, theirs, (1, 2), (0, 1), dex, ABILITIES.get)
    weak = _value(exposed, dex, model, policy, lambda s: believed(s, []), 8, 12)
    strong = _value(immune, dex, model, policy, lambda s: believed(s, []), 8, 12)
    assert weak < strong - 0.2


def test_the_sweep_is_anytime_and_deterministic(dex: Dex) -> None:
    ours = _ours(dex, "Sneasler", "Kingambit", "Garchomp", "Dragonite")
    theirs = _theirs(dex, "Indeedee-F", "Gardevoir", "Incineroar", "Rillaboom")
    args = (dex, TurnModel(dex), HeuristicPolicy(dex), lambda s: BELIEVED.get(s, []))
    quick = lead_sweep(ours, theirs, *args, seed=7, budget_s=0.4)
    again = lead_sweep(ours, theirs, *args, seed=7, budget_s=0.4)
    assert quick.rounds < quick.of_rounds
    assert quick.evaluated >= 1
    assert quick.order == again.order, "same seed, same opponents visited, same choice"
    none = lead_sweep(ours, theirs, *args, seed=7, budget_s=0.0)
    assert none.order[:2] == [1, 2] and none.evaluated == 0

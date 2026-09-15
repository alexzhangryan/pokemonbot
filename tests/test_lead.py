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
                "rillaboom": ["fakeout", "grassyglide", "woodhammer", "uturn"],
                "incineroar": ["fakeout", "flareblitz", "partingshot", "throatchop"],
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


def test_the_opening_prices_our_own_entry_effects_and_the_seed(dex: Dex) -> None:
    """Rillaboom and Sneasler lead: our Grassy Surge stands over Indeedee's
    Psychic Surge (Rillaboom is slower, so it fires last), the Grassy Seed
    pops on the terrain, and our Incineroar's Intimidate lands on them.
    Before D90 only the opponent's entry effects fired at preview."""
    ours = _ours(dex, "Rillaboom", "Sneasler", "Incineroar", "Kingambit")
    ours[0]["ability"] = "grassysurge"
    # Slower than Indeedee, so Grassy Terrain goes up second and stands.
    ours[0]["stats"]["spe"] = 90
    ours[1]["ability"], ours[1]["item"] = "unburden", "grassyseed"
    ours[2]["ability"] = "intimidate"
    theirs = _theirs(dex, "Indeedee-F", "Incineroar", "Gardevoir")
    snapshot = opening(ours, theirs, (0, 1), (0, 1), dex, ABILITIES.get)
    assert snapshot["fields"] == {"GRASSY_TERRAIN": 0}
    sneasler = snapshot["ours"]["active"][1]
    assert sneasler["boosts"] == {"def": 1, "atk": -1}, "the seed popped, under Intimidate"
    assert sneasler["item"] is None
    assert ours[1]["item"] == "grassyseed", "the previewed views are not mutated"
    assert snapshot["theirs"]["active"][0]["ability"] == "psychicsurge"
    swept = opening(ours, theirs, (0, 2), (0, 1), dex, ABILITIES.get)
    assert all(v["boosts"] == {"atk": -1} for v in swept["theirs"]["active"])


def test_the_opening_puts_everyone_at_full_health(dex: Dex) -> None:
    """poke-env reports 0% HP for a Pokemon it has never seen in battle, which
    is every Pokemon at team preview, and the sweep must not price that as a
    side with no Pokemon left (D90)."""
    ours = _ours(dex, "Sneasler", "Kingambit", "Garchomp", "Dragonite")
    theirs = _theirs(dex, "Indeedee-F", "Gardevoir", "Incineroar", "Rillaboom")
    for view in theirs:
        view["hp_pct"] = 0.0
    snapshot = opening(ours, theirs, (0, 1), (0, 1), dex)
    seen = snapshot["theirs"]["active"] + snapshot["theirs"]["bench"]
    assert all(v["hp_pct"] == 100.0 and not v["fainted"] for v in seen)
    assert theirs[0]["hp_pct"] == 0.0, "the previewed views are not mutated"
    from champions.search import evaluate

    assert evaluate.features(snapshot)["hp_advantage"] == 0.0
    assert 0.3 < evaluate.win_prob(snapshot) < 0.7, "a dead-even preview is near a coin flip"


def test_the_preview_budget_finishes_the_sweep_inside_the_timer() -> None:
    """Showdown's VGC Timer gives 90 s at preview; the full 15 rounds took
    about 12 s here, and 8 s never finished them live (D90)."""
    from champions.search.lead import PREVIEW_BUDGET_S

    assert 15.0 <= PREVIEW_BUDGET_S <= 45.0


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
    # Cut by rounds rather than by the clock: the choice is a function of the
    # seed and the completed round count (D90), and a test that cuts on
    # wall-clock is reproducible from neither.
    quick = lead_sweep(ours, theirs, *args, seed=7, max_rounds=2)
    again = lead_sweep(ours, theirs, *args, seed=7, max_rounds=2)
    assert quick.rounds == 2 < quick.of_rounds
    assert quick.evaluated == 2 * 6
    assert quick.order == again.order, "same seed, same opponents visited, same choice"
    assert lead_sweep(ours, theirs, *args, seed=8, max_rounds=2).evaluated == quick.evaluated

    # The clock cuts at the same granularity: a budget that expires inside a
    # round throws that round away rather than scoring some of our pairs
    # against an opponent the others never faced.
    partial = lead_sweep(ours, theirs, *args, seed=7, budget_s=0.4)
    assert partial.rounds < partial.of_rounds
    assert partial.evaluated >= 1

    none = lead_sweep(ours, theirs, *args, seed=7, budget_s=0.0)
    assert none.order[:2] == [1, 2] and none.evaluated == 0 and none.rounds == 0

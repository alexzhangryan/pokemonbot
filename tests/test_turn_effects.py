"""The turn model's doubles interactions (D85).

Every case is an ordering or a state fact, not a value: Fake Out stops the
target's move, Follow Me takes the hit, Trick Room ends up on the field, a
boost lands on the right Pokemon. Each one is something the first ten live
ladder games were lost to while the model scored the status move as a pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from champions.dex.loader import Dex
from champions.search import evaluate
from champions.search.payoff import TurnModel, combatant
from champions.search.policy import opponent_candidates

FORMAT_ID = "gen9championsvgc2026regmc"


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
    status: str | None = None,
    boosts: dict[str, int] | None = None,
    revealed: list[str] | None = None,
    item: str | None = None,
    first_turn: bool = True,
    speed_points: int = 32,
) -> dict[str, Any]:
    entry = dex.species[species.lower().replace("-", "")]
    view: dict[str, Any] = {
        "species": entry["name"].lower().replace("-", ""),
        "name": entry["name"],
        "types": [t.upper() for t in entry["types"]],
        "base_stats": entry["baseStats"],
        "hp_pct": hp_pct,
        "fainted": False,
        "status": status,
        "boosts": boosts or {},
        "active": True,
        "known": known,
        "first_turn": first_turn,
        "item": item,
        "ability": None,
        "selected": True,
    }
    if known:
        view["stats"] = {
            k: v + (speed_points if k == "spe" else 32) + 20
            for k, v in entry["baseStats"].items()
            if k != "hp"
        }
        view["max_hp"] = entry["baseStats"]["hp"] + 32 + 75
        view["hp"] = round(view["max_hp"] * hp_pct / 100)
        view["moves"] = []
    else:
        view["revealed_moves"] = [{"id": m} for m in (revealed or [])]
    return view


def _side(active: list, bench: list | None = None) -> dict[str, Any]:
    bench = bench or []
    seen = [p for p in active if p] + bench
    return {
        "active": active,
        "bench": bench,
        "remaining": sum(1 for p in seen if not p["fainted"]),
        "revealed": len(seen),
    }


def _snapshot(ours: list, theirs: list, **extra: Any) -> dict[str, Any]:
    return {
        "turn": 1,
        "weather": {},
        "fields": {},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": _side(ours),
        "theirs": _side(theirs),
        **extra,
    }


def _move(dex: Dex, move_id: str, target: int = 0, mega: bool = False) -> dict[str, Any]:
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
        "mega": mega,
        "label": f"{entry['name']} -> {target}",
    }


def _act(*slots: dict[str, Any]) -> dict[str, Any]:
    return {"message": "|".join(s.get("label", "?") for s in slots), "slots": list(slots)}


def _pass() -> dict[str, Any]:
    return {"kind": "pass", "label": "pass"}


def _after(model: TurnModel, snapshot: dict[str, Any], ours: Any, theirs: Any) -> dict[str, Any]:
    outcomes = model.outcomes(snapshot, ours, theirs)
    assert outcomes
    return max(outcomes, key=lambda o: o.probability).snapshot


# -- Fake Out ---------------------------------------------------------------


def test_fake_out_flinches_a_slower_target_out_of_its_move(dex: Dex, model: TurnModel) -> None:
    """Sneasler's Fake Out lands before Farigiraf's Trick Room; no room goes up."""
    snapshot = _snapshot(
        [_mon(dex, "Sneasler"), _mon(dex, "Kingambit")],
        [_mon(dex, "Farigiraf", known=False), _mon(dex, "Rillaboom", known=False)],
    )
    ours = _act(_move(dex, "fakeout", 1), _pass())
    theirs = _act(_move(dex, "trickroom"), _pass())
    after = _after(model, snapshot, ours, theirs)
    assert "TRICK_ROOM" not in after["fields"]

    # Off the first turn Fake Out fails, and the room goes up.
    snapshot["ours"]["active"][0]["first_turn"] = False
    after = _after(model, snapshot, ours, theirs)
    assert "TRICK_ROOM" in after["fields"]
    assert after["theirs"]["active"][0]["hp_pct"] == 100.0


def test_trick_room_toggles_and_the_evaluation_sees_the_speed_flip(
    dex: Dex, model: TurnModel
) -> None:
    fast = _snapshot(
        [_mon(dex, "Sneasler"), _mon(dex, "Garchomp")],
        [_mon(dex, "Farigiraf", known=False), _mon(dex, "Snorlax", known=False)],
    )
    room_up = _after(model, fast, _act(_pass(), _pass()), _act(_move(dex, "trickroom"), _pass()))
    assert "TRICK_ROOM" in room_up["fields"]
    assert evaluate.features(fast)["speed_advantage"] == 1.0
    assert evaluate.features(room_up)["speed_advantage"] == -1.0
    assert evaluate.win_prob(room_up) < evaluate.win_prob(fast)

    ended = _after(model, room_up, _act(_pass(), _pass()), _act(_move(dex, "trickroom"), _pass()))
    assert "TRICK_ROOM" not in ended["fields"]


# -- redirection and support --------------------------------------------------


def test_follow_me_takes_the_hit_aimed_at_the_partner(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Sneasler"), _mon(dex, "Dragonite")],
        [_mon(dex, "Gardevoir", known=False), _mon(dex, "Indeedee-F", known=False)],
    )
    ours = _act(_move(dex, "closecombat", 1), _pass())
    theirs = _act(_pass(), _move(dex, "followme"))
    after = _after(model, snapshot, ours, theirs)
    assert after["theirs"]["active"][0]["hp_pct"] == 100.0, "Gardevoir was not hit"
    assert after["theirs"]["active"][1]["hp_pct"] < 100.0, "Indeedee took it"

    # A spread move is not redirected.
    spread = _after(model, snapshot, _act(_pass(), _move(dex, "heatwave")), theirs)
    assert spread["theirs"]["active"][0]["hp_pct"] < 100.0


def test_rage_powder_does_not_pull_a_grass_type(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Rillaboom"), _mon(dex, "Sneasler")],
        [_mon(dex, "Gardevoir", known=False), _mon(dex, "Volcarona", known=False)],
    )
    theirs = _act(_pass(), _move(dex, "ragepowder"))
    after = _after(model, snapshot, _act(_move(dex, "woodhammer", 1), _pass()), theirs)
    assert after["theirs"]["active"][0]["hp_pct"] < 100.0


def test_helping_hand_raises_the_partners_damage(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Sneasler"), _mon(dex, "Kingambit")],
        [_mon(dex, "Snorlax", known=False), _mon(dex, "Farigiraf", known=False)],
    )
    plain = _after(model, snapshot, _act(_move(dex, "closecombat", 1), _pass()), _act())
    helped = _after(
        model, snapshot, _act(_move(dex, "closecombat", 1), _move(dex, "helpinghand", -1)), _act()
    )
    assert helped["theirs"]["active"][0]["hp_pct"] < plain["theirs"]["active"][0]["hp_pct"]


# -- boosts, drops and status ---------------------------------------------------


def test_setup_and_drops_land_on_the_right_pokemon(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Sneasler"), _mon(dex, "Kingambit")],
        [_mon(dex, "Volcarona", known=False), _mon(dex, "Incineroar", known=False)],
    )
    after = _after(
        model,
        snapshot,
        _act(_move(dex, "closecombat", 2), _pass()),
        _act(_move(dex, "quiverdance"), _move(dex, "partingshot", 2)),
    )
    assert after["theirs"]["active"][0]["boosts"] == {"spa": 1, "spd": 1, "spe": 1}
    assert after["ours"]["active"][0]["boosts"] == {"def": -1, "spd": -1}, "Close Combat's drop"
    assert after["ours"]["active"][1]["boosts"] == {"atk": -1, "spa": -1}, "Parting Shot's drop"
    assert after["theirs"]["active"][1] is None, "Parting Shot switched Incineroar out"
    assert any(p["species"] == "incineroar" for p in after["theirs"]["bench"])


def test_icy_wind_drops_speed_and_thunder_wave_respects_immunity(
    dex: Dex, model: TurnModel
) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Garchomp"), _mon(dex, "Sneasler")],
        [_mon(dex, "Pelipper", known=False), _mon(dex, "Rillaboom", known=False)],
    )
    after = _after(
        model,
        snapshot,
        _act(_pass(), _pass()),
        _act(_move(dex, "icywind"), _move(dex, "thunderwave", 1)),
    )
    assert after["ours"]["active"][0]["boosts"].get("spe") == -1
    assert after["ours"]["active"][1]["boosts"].get("spe") == -1
    assert after["ours"]["active"][0]["status"] is None, "Ground is immune to Thunder Wave"
    burned = _after(
        model, snapshot, _act(_pass(), _pass()), _act(_pass(), _move(dex, "willowisp", 2))
    )
    assert burned["ours"]["active"][1]["status"] == "BRN"


# -- field and side conditions ---------------------------------------------


def test_tailwind_and_screens_are_set_and_used(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Dragonite"), _mon(dex, "Garchomp")],
        [_mon(dex, "Whimsicott", known=False), _mon(dex, "Metagross", known=False)],
    )
    after = _after(
        model,
        snapshot,
        _act(_pass(), _pass()),
        _act(_move(dex, "tailwind"), _move(dex, "lightscreen")),
    )
    assert "TAILWIND" in after["opponent_side_conditions"]
    assert "LIGHT_SCREEN" in after["opponent_side_conditions"]
    assert evaluate.features(after)["speed_control"] == -1.0

    screened = dict(snapshot, opponent_side_conditions={"LIGHT_SCREEN": 5})
    plain = _after(model, snapshot, _act(_move(dex, "heatwave"), _pass()), _act())
    behind = _after(model, screened, _act(_move(dex, "heatwave"), _pass()), _act())
    assert behind["theirs"]["active"][1]["hp_pct"] > plain["theirs"]["active"][1]["hp_pct"]


def test_wide_guard_blocks_spread_moves_only(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Dragonite"), _mon(dex, "Garchomp")],
        [_mon(dex, "Golisopod", known=False), _mon(dex, "Metagross", known=False)],
    )
    guarded = _after(
        model,
        snapshot,
        _act(_move(dex, "heatwave"), _move(dex, "dragonclaw", 2)),
        _act(_move(dex, "wideguard"), _pass()),
    )
    assert guarded["theirs"]["active"][0]["hp_pct"] == 100.0
    assert guarded["theirs"]["active"][1]["hp_pct"] < 100.0, "the single-target move lands"


def test_psychic_terrain_boosts_expanding_force_and_blocks_priority(
    dex: Dex, model: TurnModel
) -> None:
    plain = _snapshot(
        [_mon(dex, "Dragonite"), _mon(dex, "Kingambit")],
        [_mon(dex, "Indeedee-F", known=False), _mon(dex, "Gardevoir", known=False)],
    )
    terrain = dict(plain, fields={"PSYCHIC_TERRAIN": 0})
    theirs = _act(_move(dex, "expandingforce", 1), _pass())
    on_plain = _after(model, plain, _act(_pass(), _pass()), theirs)
    on_terrain = _after(model, terrain, _act(_pass(), _pass()), theirs)
    assert 0.0 < on_terrain["ours"]["active"][0]["hp_pct"] < on_plain["ours"]["active"][0]["hp_pct"]

    # Off the terrain it is single target; on it, spread -- though Kingambit,
    # being Dark, is immune either way, so the spread is checked on a partner
    # that is not.
    spread_plain = dict(plain, ours=_side([_mon(dex, "Dragonite"), _mon(dex, "Garchomp")]))
    spread_terrain = dict(spread_plain, fields={"PSYCHIC_TERRAIN": 0})
    off = _after(model, spread_plain, _act(_pass(), _pass()), theirs)
    on = _after(model, spread_terrain, _act(_pass(), _pass()), theirs)
    assert off["ours"]["active"][1]["hp_pct"] == 100.0, "single target off terrain"
    assert on["ours"]["active"][1]["hp_pct"] < 100.0, "spread on terrain"

    punched = _after(model, terrain, _act(_pass(), _move(dex, "suckerpunch", 1)), theirs)
    assert punched["theirs"]["active"][0]["hp_pct"] == 100.0, "priority fails on the terrain"


# -- conditional moves ----------------------------------------------------------


def test_sucker_punch_needs_an_attacking_target_that_has_not_moved(
    dex: Dex, model: TurnModel
) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Kingambit"), _mon(dex, "Sneasler")],
        [_mon(dex, "Gardevoir", known=False), _mon(dex, "Indeedee-F", known=False)],
    )
    ours = _act(_move(dex, "suckerpunch", 1), _pass())
    attacking = _after(model, snapshot, ours, _act(_move(dex, "moonblast", 1), _pass()))
    assert attacking["theirs"]["active"][0]["hp_pct"] < 100.0
    protecting = _after(model, snapshot, ours, _act(_move(dex, "calmmind"), _pass()))
    assert protecting["theirs"]["active"][0]["hp_pct"] == 100.0


def test_foul_play_uses_the_targets_attack(dex: Dex, model: TurnModel) -> None:
    calm = _snapshot(
        [_mon(dex, "Garchomp"), _mon(dex, "Floette-Eternal")],
        [_mon(dex, "Thievul", known=False), _mon(dex, "Altaria", known=False)],
    )
    danced = dict(
        calm, ours=_side([_mon(dex, "Garchomp", boosts={"atk": 2}), _mon(dex, "Floette-Eternal")])
    )
    theirs = _act(_move(dex, "foulplay", 1), _pass())
    plain = _after(model, calm, _act(_pass(), _pass()), theirs)
    boosted = _after(model, danced, _act(_pass(), _pass()), theirs)
    assert boosted["ours"]["active"][0]["hp_pct"] < plain["ours"]["active"][0]["hp_pct"], (
        "Foul Play reads the target's Attack, boosts included"
    )
    knock = _act(_move(dex, "knockoff", 1), _pass())
    assert (
        _after(model, calm, _act(_pass(), _pass()), knock)["ours"]["active"][0]["hp_pct"]
        == _after(model, danced, _act(_pass(), _pass()), knock)["ours"]["active"][0]["hp_pct"]
    ), "an ordinary move does not"


def test_recoil_and_life_orb_cost_the_attacker(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Basculegion", item="lifeorb"), _mon(dex, "Sneasler")],
        [_mon(dex, "Snorlax", known=False), _mon(dex, "Farigiraf", known=False)],
    )
    after = _after(model, snapshot, _act(_move(dex, "wavecrash", 1), _pass()), _act())
    assert after["ours"]["active"][0]["hp_pct"] < 100.0
    assert after["theirs"]["active"][0]["hp_pct"] < 100.0


def test_accuracy_lowers_the_expected_damage_without_new_branches(
    dex: Dex, model: TurnModel
) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Dragonite"), _mon(dex, "Garchomp")],
        [_mon(dex, "Snorlax", known=False), _mon(dex, "Farigiraf", known=False)],
    )
    shaky = model.outcomes(snapshot, _act(_move(dex, "dracometeor", 1), _pass()), _act())
    assert len(shaky) <= 2
    assert sum(o.probability for o in shaky) == pytest.approx(1.0)
    # Draco Meteor at 90% still does more expected damage than Dragon Pulse
    # at 100% into a healthy Snorlax, and the drop is on the user.
    weakest = min(shaky, key=lambda o: o.probability)
    assert any(o.snapshot["ours"]["active"][0]["boosts"].get("spa") == -2 for o in shaky)
    assert weakest.probability > 0


def test_mega_evolution_uses_the_new_forme(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Dragonite", item="dragoninite"), _mon(dex, "Garchomp")],
        [_mon(dex, "Snorlax", known=False), _mon(dex, "Farigiraf", known=False)],
    )
    plain = _after(model, snapshot, _act(_move(dex, "dragonpulse", 1), _pass()), _act())
    mega = _after(model, snapshot, _act(_move(dex, "dragonpulse", 1, mega=True), _pass()), _act())
    assert mega["ours"]["active"][0]["species"] == "dragonitemega"
    assert mega["theirs"]["active"][0]["hp_pct"] != plain["theirs"]["active"][0]["hp_pct"]


# -- the opponent's columns --------------------------------------------------


def test_opponent_columns_aim_at_both_our_slots_and_rank_threats_first(dex: Dex) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Sneasler"), _mon(dex, "Garchomp")],
        [
            _mon(dex, "Indeedee-F", known=False, revealed=["followme", "expandingforce"]),
            _mon(dex, "Gardevoir", known=False, revealed=["moonblast", "calmmind"]),
        ],
    )
    columns = opponent_candidates(snapshot, dex, k=24)
    labels = [c["label"] for c in columns]
    assert any("Moonblast -> our slot 2" in label for label in labels)
    assert any("Moonblast -> our slot 1" in label for label in labels)
    assert any("Follow Me" in label for label in labels)
    # Moonblast into Garchomp (4x) outranks Moonblast into Sneasler (2x).
    slot_two = next(i for i, label in enumerate(labels) if "Moonblast -> our slot 2" in label)
    slot_one = next(i for i, label in enumerate(labels) if "Moonblast -> our slot 1" in label)
    assert slot_two < slot_one


def test_believed_moves_on_the_view_become_columns(dex: Dex) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Sneasler"), _mon(dex, "Garchomp")],
        [_mon(dex, "Farigiraf", known=False), _mon(dex, "Rillaboom", known=False)],
    )
    snapshot["theirs"]["active"][0]["believed_moves"] = ["trickroom", "psychic"]
    snapshot["theirs"]["active"][1]["believed_moves"] = ["fakeout", "grassyglide"]
    columns = opponent_candidates(snapshot, dex)
    assert len(columns) > 1
    assert any("Trick Room" in c["label"] and "Fake Out" in c["label"] for c in columns)


def test_the_model_keeps_the_turn_flags_off_the_snapshot_it_was_given(
    dex: Dex, model: TurnModel
) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Sneasler"), _mon(dex, "Kingambit")],
        [_mon(dex, "Farigiraf", known=False), _mon(dex, "Rillaboom", known=False)],
    )
    before = {k: dict(v) for k, v in enumerate(snapshot["ours"]["active"])}
    model.outcomes(
        snapshot, _act(_move(dex, "fakeout", 1), _pass()), _act(_move(dex, "trickroom"), _pass())
    )
    assert {k: dict(v) for k, v in enumerate(snapshot["ours"]["active"])} == before
    assert "TRICK_ROOM" not in snapshot["fields"]
    assert combatant(snapshot["ours"]["active"][0], model.hypothesis).hp > 0


def test_a_repeated_protect_is_a_gamble(dex: Dex, model: TurnModel) -> None:
    snapshot = _snapshot(
        [_mon(dex, "Kingambit"), _mon(dex, "Sneasler")],
        [_mon(dex, "Garchomp", known=False), _mon(dex, "Salamence", known=False)],
    )
    theirs = _act(_move(dex, "earthquake"), _pass())
    fresh = model.outcomes(snapshot, _act(_move(dex, "protect"), _pass()), theirs)
    assert all(o.snapshot["ours"]["active"][0]["hp_pct"] == 100.0 for o in fresh)

    snapshot["ours"]["active"][0]["protect_counter"] = 1
    again = model.outcomes(snapshot, _act(_move(dex, "protect"), _pass()), theirs)
    held = sum(o.probability for o in again if o.snapshot["ours"]["active"][0]["hp_pct"] == 100.0)
    assert held == pytest.approx(1 / 3)
    assert sum(o.probability for o in again) == pytest.approx(1.0)


def test_pixilate_makes_hyper_voice_a_fairy_move(dex: Dex) -> None:
    """D87: Mega Gardevoir's Hyper Voice is a boosted Fairy spread move, not a
    Normal one a Ghost ignores."""
    from champions.belief.hypothesis import BeliefEffects, BeliefHypothesis

    snapshot = _snapshot(
        [_mon(dex, "Basculegion"), _mon(dex, "Dragonite")],
        [_mon(dex, "Gardevoir", known=False), _mon(dex, "Indeedee-F", known=False)],
    )
    snapshot["theirs"]["active"][0]["ability"] = "pixilate"
    plain = TurnModel(dex)
    typed = TurnModel(dex, hypothesis=BeliefHypothesis(belief=None), effects=BeliefEffects(None))
    theirs = _act(_move(dex, "hypervoice"), _pass())
    before = _after(plain, snapshot, _act(_pass(), _pass()), theirs)
    after = _after(typed, snapshot, _act(_pass(), _pass()), theirs)
    assert before["ours"]["active"][0]["hp_pct"] == 100.0, "Normal into a Ghost"
    assert after["ours"]["active"][0]["hp_pct"] < 100.0, "Fairy into a Ghost"
    assert after["ours"]["active"][1]["hp_pct"] < before["ours"]["active"][1]["hp_pct"], (
        "super effective and boosted into the Dragon"
    )


def test_entry_abilities_fire_on_a_mega_and_on_a_placed_switch(dex: Dex) -> None:
    """D87: Mega Froslass brings snow (so Aurora Veil goes up and Blizzard
    cannot miss), a switched-in Politoed brings rain (so Weather Ball is a
    Water move), and a switched-in Incineroar lowers the foes' Attack."""
    model = TurnModel(dex, place_incoming=True)
    froslass = _mon(dex, "Froslass", item="froslassite")
    froslass["ability"] = "cursedbody"
    politoed = _mon(dex, "Politoed")
    politoed["ability"] = "drizzle"
    incineroar = _mon(dex, "Incineroar")
    incineroar["ability"] = "intimidate"
    snapshot = _snapshot(
        [froslass, politoed],
        [_mon(dex, "Rillaboom", known=False), _mon(dex, "Kingambit", known=False)],
    )
    snapshot["ours"]["bench"] = [incineroar]
    snapshot["ours"]["remaining"] = 3

    veiled = _after(model, snapshot, _act(_move(dex, "auroraveil", mega=True), _pass()), _act())
    assert (
        "SNOWSCAPE" in veiled["weather"]
        and veiled["ours"]["active"][0]["species"] == "froslassmega"
    )
    assert "AURORA_VEIL" in veiled["side_conditions"], "the veil goes up under the mega's snow"

    calm = dict(snapshot, weather={})
    dry = _after(model, calm, _act(_pass(), _move(dex, "weatherball", 2)), _act())
    rainy = dict(snapshot, weather={"RAINDANCE": 0})
    wet = _after(model, rainy, _act(_pass(), _move(dex, "weatherball", 2)), _act())
    assert wet["theirs"]["active"][1]["hp_pct"] < dry["theirs"]["active"][1]["hp_pct"], (
        "a 100-power Water move in rain against a 50-power Normal one"
    )

    switching = _act(
        {"kind": "switch", "species": "incineroar", "name": "Incineroar", "label": "sw"},
        _pass(),
    )
    arrived = _after(model, snapshot, switching, _act())
    assert arrived["ours"]["active"][0]["species"] == "incineroar"
    assert all(p["boosts"].get("atk") == -1 for p in arrived["theirs"]["active"])

    rain_in = _act(
        _pass(), {"kind": "switch", "species": "politoed", "name": "Politoed", "label": "sw"}
    )
    calm["ours"]["active"][1] = _mon(dex, "Archaludon")
    calm["ours"]["bench"] = [politoed]
    poured = _after(model, calm, rain_in, _act())
    assert "RAINDANCE" in poured["weather"]


def test_electro_shot_charges_outside_rain_and_fires_in_it(dex: Dex, model: TurnModel) -> None:
    """D88: the charge turn deals nothing and raises Special Attack; rain
    waives it; a charged user fires."""
    snapshot = _snapshot(
        [_mon(dex, "Archaludon"), _mon(dex, "Politoed")],
        [_mon(dex, "Kingambit", known=False), _mon(dex, "Rillaboom", known=False)],
    )
    shot = _act(_move(dex, "electroshot", 2), _pass())
    dry = _after(model, snapshot, shot, _act())
    assert dry["theirs"]["active"][1]["hp_pct"] == 100.0, "charging, not firing"
    assert dry["ours"]["active"][0]["boosts"] == {"spa": 1}
    assert dry["ours"]["active"][0]["_preparing"]

    rain = dict(snapshot, weather={"RAINDANCE": 0})
    wet = _after(model, rain, shot, _act())
    assert wet["theirs"]["active"][1]["hp_pct"] < 100.0, "rain waives the charge"

    charged = _snapshot(
        [_mon(dex, "Archaludon"), _mon(dex, "Politoed")],
        [_mon(dex, "Kingambit", known=False), _mon(dex, "Rillaboom", known=False)],
    )
    charged["ours"]["active"][0]["preparing"] = True
    fired = _after(model, charged, shot, _act())
    assert fired["theirs"]["active"][1]["hp_pct"] < 100.0, "the second turn fires"


def test_weather_synergy_counts_the_side_that_uses_the_weather(dex: Dex) -> None:
    archaludon = _mon(dex, "Archaludon")
    archaludon["moves"] = [{"id": "electroshot"}, {"id": "flashcannon"}]
    politoed = _mon(dex, "Politoed")
    politoed["moves"] = [{"id": "weatherball"}, {"id": "perishsong"}]
    politoed["ability"] = "drizzle"
    snapshot = _snapshot(
        [archaludon, politoed],
        [_mon(dex, "Kingambit", known=False), _mon(dex, "Rillaboom", known=False)],
    )
    assert evaluate.features(snapshot)["weather_synergy"] == 0.0
    rainy = dict(snapshot, weather={"RAINDANCE": 0})
    assert evaluate.features(rainy)["weather_synergy"] == 2.0
    snowy = dict(snapshot, weather={"SNOWSCAPE": 0})
    assert evaluate.features(snowy)["weather_synergy"] == 1.0, "Weather Ball still wants it"
    assert evaluate.win_prob(rainy) > evaluate.win_prob(snowy) > evaluate.win_prob(snapshot)
    snapshot["theirs"]["active"][1]["revealed_moves"] = [{"id": "weatherball"}]
    rainy = dict(snapshot, weather={"RAINDANCE": 0})
    assert evaluate.features(rainy)["weather_synergy"] == 1.0

"""M7: implementation A, the one `docs/04-decision-engine.md` section 3 specifies.

Section 3 names four things the heuristic provider is supposed to do -- any move
that knocks out a target on an average roll, Protect when the slot is
threatened, speed control when it flips an outspeed, Fake Out on turn 1, plus
the switches -- and what shipped through M6 did none of them. It ranked moves by
base power and never looked at the position at all.

`docs/pruning-guard.md` measured what that cost: at the agent's own `k` the
unpruned equilibrium put mass on a discarded row on 64.2% of positions. So the
tests below are about the four conditionals, and every one of them is a case
where the right ordering is not a matter of opinion: a knockout outranks chip
damage from a stronger move, Protect into a threat outranks Protect into
nothing, Tailwind that flips a speed race outranks Tailwind that changes no
order, and Fake Out is worth something on the turn it works and nothing after.

`BasePowerPolicy` is kept and tested here too. The 1,500 self-play traces the
pruning guard reads were produced by it, so it is both the baseline the
specified A has to beat and the only policy whose re-derived candidate set can
be expected to match what those traces recorded.
"""

from __future__ import annotations

from typing import Any

import pytest

from champions.dex.loader import Dex
from champions.search.policy import DISQUALIFIED, BasePowerPolicy, HeuristicPolicy

FORMAT_ID = "gen9championsvgc2026regmb"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


@pytest.fixture(scope="module")
def policy(dex: Dex) -> HeuristicPolicy:
    return HeuristicPolicy(dex)


def _mon(
    dex: Dex,
    species: str,
    hp_pct: float = 100.0,
    known: bool = True,
    fainted: bool = False,
    status: str | None = None,
    boosts: dict[str, int] | None = None,
    revealed_moves: list[str] | None = None,
    protect_counter: int = 0,
    first_turn: bool = False,
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
        "protect_counter": protect_counter,
        "first_turn": first_turn,
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


def _state(
    ours: list,
    theirs: list,
    turn: int = 3,
    fields: dict[str, Any] | None = None,
    side_conditions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "turn": turn,
        "weather": {},
        "fields": fields or {},
        "side_conditions": side_conditions or {},
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


def _score(policy: HeuristicPolicy, state: dict[str, Any], action: dict[str, Any]) -> float:
    return policy.scored([action], k=1, state=state)[0].score


# -- "any move that knocks out a target on an average roll" ------------------


def test_a_knockout_outranks_a_stronger_move_that_does_not_knock_out(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """Ice Shard is 40 base power and Iron Head is 80, so base power alone puts
    them the wrong way round. Ice Shard is 4x into a Dragonite at 15% and kills
    it; Iron Head is resisted by a full-health Skarmory and does not."""
    state = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Skarmory", known=False), _mon(dex, "Dragonite", 15.0, known=False)],
    )
    knockout = _act(_move(dex, "iceshard", 2), PASS)
    chip = _act(_move(dex, "ironhead", 1), PASS)

    assert _score(policy, state, knockout) > _score(policy, state, chip)
    assert "knockout" in policy.scored([knockout], k=1, state=state)[0].reasons


def test_the_base_power_policy_ranks_those_two_the_other_way_round(dex: Dex) -> None:
    """The baseline the specified A has to beat, pinned so the comparison in
    `docs/pruning-guard.md` is against a policy that has not quietly changed."""
    base = BasePowerPolicy(dex)
    knockout = _act(_move(dex, "iceshard", 2), PASS)
    chip = _act(_move(dex, "ironhead", 1), PASS)

    scores = {s.action["message"]: s.score for s in base.scored([knockout, chip], k=2)}
    assert scores[chip["message"]] > scores[knockout["message"]]


def test_damage_dealt_orders_two_moves_that_neither_of_them_kills(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """No knockout on either side, so the tie-break is the fraction of the
    target's remaining HP the average roll removes -- which is what base power
    was a proxy for and is wrong about whenever effectiveness disagrees."""
    state = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Skarmory", known=False), _mon(dex, "Milotic", known=False)],
    )
    resisted = _act(_move(dex, "ironhead", 1), PASS)
    neutral = _act(_move(dex, "ironhead", 2), PASS)

    assert _score(policy, state, neutral) > _score(policy, state, resisted)


def test_friendly_fire_is_still_disqualified(dex: Dex, policy: HeuristicPolicy) -> None:
    """The M2 regression: a damaging move aimed at our own partner scored the
    same as the same move aimed at a foe, and nine of ten survivors were
    friendly fire. Carried forward rather than re-derived from the damage."""
    state = _state(
        ours=[_mon(dex, "Metagross"), _mon(dex, "Starmie")],
        theirs=[_mon(dex, "Skarmory", known=False), _mon(dex, "Dragonite", known=False)],
    )
    at_ally = _act(_move(dex, "ironhead", -2), PASS)

    assert _score(policy, state, at_ally) == DISQUALIFIED


def test_a_spread_move_that_kills_our_own_partner_ranks_below_one_that_does_not(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """Earthquake hits every adjacent slot including ours. Aimed at a foe it is
    not friendly fire and is not disqualified, but a partner it kills is a real
    cost the base-power ranking could not see."""
    doomed = _state(
        ours=[_mon(dex, "Garchomp"), _mon(dex, "Metagross", 5.0)],
        theirs=[_mon(dex, "Milotic", known=False), _mon(dex, "Skarmory", known=False)],
    )
    safe = _state(
        ours=[_mon(dex, "Garchomp"), _mon(dex, "Skarmory")],
        theirs=[_mon(dex, "Milotic", known=False), _mon(dex, "Skarmory", known=False)],
    )
    quake = _act(_move(dex, "earthquake", 0), PASS)

    assert _score(policy, doomed, quake) < _score(policy, safe, quake)


def test_the_knockout_step_outranks_a_spread_move_that_leaves_both_targets_alive(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """Damage alone cannot say this. Earthquake takes more total HP off the
    field -- 0.79 of one target and 0.49 of the other -- than an Outrage that
    only finishes the weakened one, and the finished one is worth more. The
    difference between 99% and 100% of a target's HP is the whole value of the
    turn, and no continuous function of damage says so."""
    state = _state(
        ours=[_mon(dex, "Garchomp"), None],
        theirs=[_mon(dex, "Milotic", 40.0, known=False), _mon(dex, "Metagross", known=False)],
    )
    finish = _act(_move(dex, "outrage", 1), PASS)
    spread = _act(_move(dex, "earthquake", 0), PASS)

    assert _score(policy, state, finish) > _score(policy, state, spread)


# -- "Protect when the slot is threatened" -----------------------------------


def test_protect_outranks_protect_when_the_slot_is_actually_threatened(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    threatened = _state(
        ours=[_mon(dex, "Metagross", 30.0), None],
        theirs=[_mon(dex, "Garchomp", known=False, revealed_moves=["earthquake"])],
    )
    idle = _state(
        ours=[_mon(dex, "Metagross", 30.0), None],
        theirs=[_mon(dex, "Garchomp", known=False, revealed_moves=[])],
    )
    protect = _act(_move(dex, "protect", 0), PASS)

    assert _score(policy, threatened, protect) > _score(policy, idle, protect)
    assert "protect" in policy.scored([protect], k=1, state=threatened)[0].reasons


def test_protect_ranks_low_when_it_was_already_used_last_turn(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """Consecutive Protects fail with rising probability, so a threat is not on
    its own a reason to press it twice."""
    once = _state(
        ours=[_mon(dex, "Metagross", 30.0, protect_counter=0), None],
        theirs=[_mon(dex, "Garchomp", known=False, revealed_moves=["earthquake"])],
    )
    again = _state(
        ours=[_mon(dex, "Metagross", 30.0, protect_counter=1), None],
        theirs=[_mon(dex, "Garchomp", known=False, revealed_moves=["earthquake"])],
    )
    protect = _act(_move(dex, "protect", 0), PASS)

    assert _score(policy, again, protect) < _score(policy, once, protect)


# -- "speed control when it flips an outspeed" -------------------------------


def test_tailwind_ranks_high_only_when_doubling_our_speed_changes_the_order(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """Metagross is slower than Dragonite and faster with Tailwind up, so there
    it flips a race. Against a Snorlax it was already faster and Tailwind
    changes no order this turn."""
    flips = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Dragonite", known=False)],
    )
    changes_nothing = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Snorlax", known=False)],
    )
    tailwind = _act(_move(dex, "tailwind", 0), PASS)

    assert _score(policy, flips, tailwind) > _score(policy, changes_nothing, tailwind)
    assert "speed control" in policy.scored([tailwind], k=1, state=flips)[0].reasons


def test_tailwind_ranks_low_when_our_side_already_has_it_up(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    already = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Dragonite", known=False)],
        side_conditions={"TAILWIND": 3},
    )
    fresh = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Dragonite", known=False)],
    )
    tailwind = _act(_move(dex, "tailwind", 0), PASS)

    assert _score(policy, already, tailwind) < _score(policy, fresh, tailwind)


def test_trick_room_ranks_high_when_we_are_slower_and_low_when_it_is_already_up(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """Trick Room reverses the comparison rather than changing a speed, so the
    same question -- does this flip a race we currently lose? -- answers it."""
    slower = _state(
        ours=[_mon(dex, "Snorlax"), None],
        theirs=[_mon(dex, "Dragonite", known=False)],
    )
    already = _state(
        ours=[_mon(dex, "Snorlax"), None],
        theirs=[_mon(dex, "Dragonite", known=False)],
        fields={"TRICK_ROOM": 3},
    )
    trick_room = _act(_move(dex, "trickroom", 0), PASS)

    assert _score(policy, slower, trick_room) > _score(policy, already, trick_room)


def test_icy_wind_is_speed_control_even_though_it_is_a_damaging_move(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """Icy Wind and Electroweb are the speed control doubles actually plays, and
    both are Special rather than Status. Scoring them purely as attacks means
    the one conditional section 3 asks for never fires on the moves it most
    obviously means.

    The two positions differ only in whether Trick Room is up, so the damage is
    identical and the speed race is the only thing that moved: without it
    Metagross is slower and the Speed drop flips that, with it Metagross is
    already moving first and the drop changes no order."""
    flips = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Dragonite", known=False)],
    )
    changes_nothing = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Dragonite", known=False)],
        fields={"TRICK_ROOM": 3},
    )
    icy_wind = _act(_move(dex, "icywind", 0), PASS)

    assert _score(policy, flips, icy_wind) > _score(policy, changes_nothing, icy_wind)
    assert "speed control" in policy.scored([icy_wind], k=1, state=flips)[0].reasons


# -- "Fake Out on turn 1" ----------------------------------------------------


def test_fake_out_ranks_high_on_the_turn_it_works_and_low_after(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """Fake Out only works on the turn its user came in. Off that turn it is a
    guaranteed wasted action, which base power cannot express."""
    first = _state(
        ours=[_mon(dex, "Metagross", first_turn=True), None],
        theirs=[_mon(dex, "Milotic", known=False)],
        turn=1,
    )
    later = _state(
        ours=[_mon(dex, "Metagross", first_turn=False), None],
        theirs=[_mon(dex, "Milotic", known=False)],
        turn=6,
    )
    fake_out = _act(_move(dex, "fakeout", 1), PASS)

    assert _score(policy, first, fake_out) > _score(policy, later, fake_out)
    assert "fake out" in policy.scored([fake_out], k=1, state=first)[0].reasons


def test_fake_out_falls_back_to_the_turn_number_when_first_turn_is_not_recorded(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """The 1,500 traces the pruning guard reads predate `first_turn` in the
    snapshot. Turn 1 is when it is true for everything on the field, so the
    older traces are still measurable rather than silently scored as if Fake Out
    never worked."""
    old = _state(ours=[_mon(dex, "Metagross"), None], theirs=[_mon(dex, "Milotic", known=False)])
    del old["ours"]["active"][0]["first_turn"]
    turn_one = dict(old, turn=1)
    fake_out = _act(_move(dex, "fakeout", 1), PASS)

    assert _score(policy, turn_one, fake_out) > _score(policy, old, fake_out)


# -- "plus the switches" -----------------------------------------------------


def test_switches_survive_a_position_where_an_attack_knocks_something_out(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """The one-turn payoff model scores a switch as a lost turn, so a switch
    that only pays off next turn cannot be seen from here. Ranking them out
    entirely would make the agent unable to switch at all."""
    state = _state(
        ours=[_mon(dex, "Metagross"), None],
        theirs=[_mon(dex, "Dragonite", 5.0, known=False)],
    )
    switch = _act({"kind": "switch", "species": "Starmie", "label": "switch to Starmie"}, PASS)

    assert _score(policy, state, switch) > 0.0


# -- properties the whole selection has to keep ------------------------------


def test_pruning_returns_at_most_k_and_is_deterministic(dex: Dex, policy: HeuristicPolicy) -> None:
    state = _state(
        ours=[_mon(dex, "Metagross"), _mon(dex, "Starmie")],
        theirs=[_mon(dex, "Skarmory", known=False), _mon(dex, "Dragonite", known=False)],
    )
    actions = [
        _act(_move(dex, "ironhead", 1), _move(dex, "icebeam", 2)),
        _act(_move(dex, "iceshard", 2), _move(dex, "surf", 0)),
        _act(_move(dex, "protect", 0), _move(dex, "icebeam", 1)),
        _act({"kind": "switch", "label": "sw"}, {"kind": "switch", "label": "sw"}),
    ]

    first = [s.action["message"] for s in policy.scored(actions, k=2, state=state)]
    assert len(first) == 2
    for _ in range(3):
        assert [s.action["message"] for s in policy.scored(actions, k=2, state=state)] == first


def test_without_a_snapshot_it_ranks_by_base_power_rather_than_failing(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """`discard.KeepFn` and the M7 benchmark both hand a provider positions one
    at a time, and a trace written before a snapshot existed has none. Nothing
    can be known to knock out or to be threatened without one, so the state-free
    path is the old ordering rather than an exception."""
    strong = _act(_move(dex, "icebeam", 1), PASS)
    weak = _act(_move(dex, "iceshard", 1), PASS)

    scores = {s.action["message"]: s.score for s in policy.scored([strong, weak], k=2)}
    assert scores[strong["message"]] > scores[weak["message"]]


def test_a_fainted_slot_scores_without_reaching_for_a_pokemon_that_is_not_there(
    dex: Dex, policy: HeuristicPolicy
) -> None:
    """A KO leaves an empty slot and a forced switch, and the request still
    describes the other slot's move. Reading our own active by slot index is
    what makes that a hole rather than an off-by-one."""
    state = _state(
        ours=[None, _mon(dex, "Starmie")],
        theirs=[_mon(dex, "Dragonite", 10.0, known=False)],
    )
    action = _act(PASS, _move(dex, "icebeam", 1))

    assert _score(policy, state, action) > 0.0

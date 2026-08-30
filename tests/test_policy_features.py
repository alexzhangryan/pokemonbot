"""The features a candidate provider is scored on, and the one path that makes them.

`docs/specs/2026-08-29-learned-policy-provider.md` section 3.2 asks for one
function serving both the trainer and the live agent. The reason is the failure
it prevents rather than tidiness: two paths to the same features is how a model
quietly stops being served the inputs it was fit on, and the symptom is a good
offline number and a bad live one, with nothing in between to read.

So the test this file exists for is
`test_the_same_position_gives_the_same_vector_from_a_log_and_from_play`. The
rest check that individual features mean what their names say.

The vectors have to agree in a specific and slightly uncomfortable way. A live
snapshot carries our exact spread; a replay carries a percentage and nothing
else, because a spectator stream does not contain stat points. Agreement is
therefore only possible on the *hypothesis* -- the assumed spread both sides
already use for the opponent -- so the feature path drops the real numbers it
has. That is a deliberate loss, and this file is where it is visible.
"""

from __future__ import annotations

import glob
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from champions.corpus.replay_state import Observer
from champions.dex.loader import Dex
from champions.search.policy_features import (
    FEATURE_NAMES,
    board_for,
    option_features,
)

FORMAT_ID = "gen9championsvgc2026regmb"

#: How many traces the agreement test replays. Each is a whole battle and every
#: legal option of every turn is vectorised twice, so this is the slowest test
#: here by an order of magnitude. The property does not get truer with more
#: files; the full sweep belongs in the fit script's report, not in the suite.
AGREEMENT_TRACES = 6

#: Features whose live half depends on a snapshot field that did not exist when
#: a trace was written. Exempted per turn, and only when the field is genuinely
#: missing from that turn's snapshot, so a trace new enough to carry it is held
#: to the same equality as everything else.
AGE_DEPENDENT = {"actor_first_turn": "first_turn"}


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


PREVIEW = [
    "|gametype|doubles",
    "|player|p1|alice|1|",
    "|player|p2|bob|266|",
    "|clearpoke",
    "|poke|p1|Metagross, L50, M|",
    "|poke|p1|Corviknight, L50, F|",
    "|poke|p1|Milotic, L50, M|",
    "|poke|p2|Starmie, L50|",
    "|poke|p2|Dragonite, L50, F|",
    "|teampreview",
]

LEAD = [
    "|start",
    "|switch|p1a: Metagross|Metagross, L50, M|100/100",
    "|switch|p1b: Corviknight|Corviknight, L50, F|100/100",
    "|switch|p2a: Starmie|Starmie, L50|100/100",
    "|switch|p2b: Dragonite|Dragonite, L50, F|100/100",
    "|turn|1",
]


def view(dex: Dex, lines: list[str], side: str = "p1") -> dict[str, Any]:
    observer = Observer(dex)
    for line in lines:
        observer.feed(line)
    return observer.view(side)


def move_option(dex: Dex, move_id: str, target: int, mega: bool = False) -> dict[str, Any]:
    """One slot's move, in the shape `champions.protocol.actions.describe` emits.

    Built from the dex rather than typed out, so that a move whose Champions
    numbers differ from mainline cannot be described here with the mainline ones
    -- the exact confusion `CLAUDE.md` constraint 1 is about.
    """
    entry = dex.moves[move_id]
    return {
        "kind": "move",
        "move": move_id,
        "name": entry["name"],
        "type": entry["type"],
        "category": entry["category"],
        "base_power": entry["basePower"],
        "priority": entry.get("priority", 0),
        "move_target": entry["target"],
        "target": target,
        "target_label": f"slot {target}",
        "mega": mega,
        "label": entry["name"],
    }


def vector(dex: Dex, snapshot: dict[str, Any], slot: int, option: dict[str, Any]) -> np.ndarray:
    return option_features(snapshot, slot, option, board_for(snapshot, dex))


def named(
    dex: Dex, snapshot: dict[str, Any], slot: int, option: dict[str, Any]
) -> dict[str, float]:
    return dict(zip(FEATURE_NAMES, vector(dex, snapshot, slot, option), strict=True))


# -- the shape ---------------------------------------------------------------


def test_the_vector_has_one_entry_per_name(dex: Dex) -> None:
    """`FEATURE_NAMES` is the schema. A model is a weight per position in this
    vector, so a length that disagrees with the names is a silent relabelling of
    every weight rather than a crash."""
    snapshot = view(dex, PREVIEW + LEAD)
    assert len(vector(dex, snapshot, 0, move_option(dex, "ironhead", 1))) == len(FEATURE_NAMES)
    assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)


def test_no_feature_is_infinite_or_missing_over_a_whole_legal_set(dex: Dex) -> None:
    """A single non-finite entry poisons a softmax over the whole slot, so this
    is checked over every legal option of a real turn rather than over one."""
    paths = sorted(glob.glob("runs/m6-selfplay/*.jsonl"))[:1]
    if not paths:
        pytest.skip("no self-play traces to read options from")

    events = [json.loads(line) for line in Path(paths[0]).read_text("utf-8").splitlines() if line]
    snapshots = {
        e["payload"]["turn"]: e["payload"]["state"] for e in events if e["type"] == "turn_start"
    }
    checked = 0
    for event in (e for e in events if e["type"] == "candidates"):
        snapshot = snapshots.get(event["payload"]["turn"])
        if snapshot is None:
            continue
        board = board_for(snapshot, dex)
        for action in event["payload"]["joint"]:
            for index, option in enumerate(action["slots"]):
                for value in option_features(snapshot, index, option, board):
                    assert math.isfinite(value)
                checked += 1
    assert checked > 0


# -- what the features mean --------------------------------------------------


def test_a_move_that_kills_on_the_average_roll_is_marked_and_a_resisted_one_is_not(
    dex: Dex,
) -> None:
    """`target_ko` is the step the spec's damage feature cannot express on its
    own: the difference between 99% and 100% of a target's remaining HP is the
    whole value of the turn, and no continuous function of damage says so."""
    weakened = view(dex, PREVIEW + LEAD + ["|-damage|p2a: Starmie|5/100", "|turn|2"])

    finisher = named(dex, weakened, 0, move_option(dex, "ironhead", 1))
    assert finisher["target_ko"] == 1.0
    assert finisher["damage_fraction"] == 1.0

    # Corviknight into Dragonite: Steel is resisted and Dragonite is untouched.
    chip = named(dex, weakened, 1, move_option(dex, "ironhead", 2))
    assert chip["target_ko"] == 0.0
    assert 0.0 < chip["damage_fraction"] < 1.0


def test_type_effectiveness_is_read_against_the_resolved_target(dex: Dex) -> None:
    """Effectiveness is a fact about the pair, not about the move, so it is
    computed against whichever target the option actually names."""
    snapshot = view(dex, PREVIEW + LEAD)

    # Metagross's Ice Punch: Starmie's Water half resists it, and Dragonite is
    # Dragon and Flying, both of which it hits for double. Stages, not
    # multipliers, so those are -1 and +2 rather than 0.5x and 4x.
    resisted = named(dex, snapshot, 0, move_option(dex, "icepunch", 1))
    doubled = named(dex, snapshot, 0, move_option(dex, "icepunch", 2))
    assert resisted["effectiveness"] == -1.0
    assert doubled["effectiveness"] == 2.0


def test_a_move_aimed_at_our_own_slot_is_marked_as_such(dex: Dex) -> None:
    """Friendly fire has to be visible in the vector. `HeuristicPolicy`
    disqualifies it outright; a learned prior has to be able to learn that, and
    it cannot if the target's side is not a feature."""
    snapshot = view(dex, PREVIEW + LEAD)

    at_a_foe = named(dex, snapshot, 0, move_option(dex, "ironhead", 1))
    at_our_partner = named(dex, snapshot, 0, move_option(dex, "ironhead", -2))

    assert at_a_foe["target_is_ally"] == 0.0
    assert at_our_partner["target_is_ally"] == 1.0
    assert at_our_partner["ally_damage_fraction"] > 0.0
    assert at_a_foe["ally_damage_fraction"] == 0.0


def test_a_switch_is_marked_and_carries_the_incoming_pokemons_health(dex: Dex) -> None:
    """Without something about the Pokemon coming in, every switch out of a slot
    scores identically and the provider picks between them arbitrarily. This is
    thin on purpose -- it is one number, not a matchup model -- and it is the
    first thing to extend if B loses to A on positions that wanted a switch."""
    hurt_bench = view(
        dex,
        PREVIEW
        + LEAD
        + [
            "|-damage|p1b: Corviknight|40/100",
            "|switch|p1b: Milotic|Milotic, L50, M|100/100",
            "|turn|2",
        ],
    )
    option = {"kind": "switch", "species": "corviknight", "name": "Corviknight"}
    switch = named(dex, hurt_bench, 0, option)

    assert switch["is_switch"] == 1.0
    assert switch["is_move"] == 0.0
    assert switch["damage_fraction"] == 0.0
    assert switch["target_ko"] == 0.0
    assert switch["switch_incoming_hp"] == pytest.approx(0.4)


def test_the_board_features_read_the_field_and_both_sides_counts(dex: Dex) -> None:
    boosted = view(
        dex,
        PREVIEW
        + LEAD
        + [
            "|-fieldstart|move: Trick Room",
            "|-sidestart|p1: alice|move: Tailwind",
            "|-weather|SunnyDay",
            "|faint|p2b: Dragonite",
            "|turn|2",
        ],
    )
    board = named(dex, boosted, 0, move_option(dex, "ironhead", 1))

    assert board["trick_room"] == 1.0
    assert board["our_tailwind"] == 1.0
    assert board["their_tailwind"] == 0.0
    assert board["weather_sun"] == 1.0
    assert board["weather_rain"] == 0.0
    # Both sides are counted from announced faints against the four a side
    # brings, which is the only count a replay can also produce.
    assert board["our_remaining"] == pytest.approx(1.0)
    assert board["their_remaining"] == pytest.approx(0.75)


def test_speed_order_is_reported_against_each_opposing_slot(dex: Dex) -> None:
    """Metagross is slower than Starmie and faster than nothing here; Tailwind
    flips the first. Read per opposing slot rather than as one number, because
    which of the two we outspeed is what decides whether a move lands first."""
    snapshot = view(dex, PREVIEW + LEAD)
    before = named(dex, snapshot, 0, move_option(dex, "ironhead", 1))
    assert before["outspeeds_foe_a"] == 0.0

    with_tailwind = view(dex, PREVIEW + LEAD + ["|-sidestart|p1: alice|move: Tailwind", "|turn|2"])
    after = named(dex, with_tailwind, 0, move_option(dex, "ironhead", 1))
    assert after["outspeeds_foe_a"] == 1.0


def test_the_actor_is_the_slot_that_acts_not_the_one_that_is_aimed_at(dex: Dex) -> None:
    hurt = view(dex, PREVIEW + LEAD + ["|-damage|p1b: Corviknight|30/100", "|turn|2"])
    healthy_slot = named(dex, hurt, 0, move_option(dex, "ironhead", 1))
    hurt_slot = named(dex, hurt, 1, move_option(dex, "ironhead", 1))

    assert healthy_slot["actor_hp"] == pytest.approx(1.0)
    assert hurt_slot["actor_hp"] == pytest.approx(0.3)


# -- the one that matters ----------------------------------------------------


def test_the_same_position_gives_the_same_vector_from_a_log_and_from_play(
    dex: Dex,
) -> None:
    """The test this module exists to pass.

    `turn_start` carries the snapshot the agent built from poke-env *and* that
    turn's protocol lines, and `candidates` carries every legal joint action of
    the same turn. So both halves of the spec's one-path claim are checkable on
    traces that already exist: rebuild the board from the log, vectorise every
    legal option against both boards, and require the numbers to be equal.

    Not "close". Equal, to floating point, because the two are meant to be the
    same computation over the same inputs and a tolerance here would hide
    exactly the drift this is looking for.

    One feature is exempt and the exemption is asserted rather than assumed.
    `first_turn` reached the snapshot with the specified implementation A, after
    these traces were written, so the live side of every one of them falls back
    to "turn 1" while the reconstruction tracks it properly. That is the trace's
    age, not a disagreement, and `AGE_DEPENDENT` names it so the day the traces
    are regenerated the exemption fails loudly instead of hiding a real one.
    """
    paths = sorted(glob.glob("runs/m6-selfplay/*.jsonl"))[:AGREEMENT_TRACES]
    if not paths:
        pytest.skip("no self-play traces to check against")

    mismatches: list[str] = []
    compared = 0

    for path in paths:
        events = [json.loads(line) for line in Path(path).read_text("utf-8").splitlines() if line]
        role = next(
            (e["payload"].get("player_role") for e in events if e["type"] == "battle_start"),
            None,
        )
        if role is None:
            continue

        legal = {
            e["payload"]["turn"]: e["payload"]["joint"] for e in events if e["type"] == "candidates"
        }
        observer = Observer(dex)

        for event in (e for e in events if e["type"] == "turn_start"):
            payload = event["payload"]
            for line in payload["log"]:
                observer.feed(line)

            live, rebuilt = payload["state"], observer.view(role)
            live_board = board_for(live, dex)
            rebuilt_board = board_for(rebuilt, dex)
            where = f"{path.rsplit('/', 1)[-1]} turn {payload['turn']}"

            exempt = {
                name
                for name, field in AGE_DEPENDENT.items()
                if any(p is not None and field not in p for p in live["ours"]["active"])
            }

            for action in legal.get(payload["turn"], []):
                for index, option in enumerate(action["slots"]):
                    from_play = option_features(live, index, option, live_board)
                    from_log = option_features(rebuilt, index, option, rebuilt_board)
                    compared += 1
                    for name, a, b in zip(FEATURE_NAMES, from_play, from_log, strict=True):
                        if a != b and name not in exempt:
                            mismatches.append(
                                f"{where} {action['message']!r} slot {index} {name}: {a} != {b}"
                            )

    assert compared > 0, "no options compared"
    detail = "\n".join(sorted(set(mismatches))[:20])
    assert not mismatches, f"{len(mismatches)} of {compared} option vectors disagree:\n{detail}"

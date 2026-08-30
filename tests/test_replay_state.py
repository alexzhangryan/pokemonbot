"""Rebuilding what each player could see, from a protocol log.

The corpus holds 697,582 labelled human decisions and no states to go with
them. `champions/protocol/parser.py` turns a log into *observations* -- facts
learned, one per line -- which is what M3 needed and is not a board. Fitting a
policy prior needs the board: the thing the player was looking at when they
chose.

Two properties matter more than any single field, and both are what these tests
are about.

**It has to be the observer's view, not the log's.** A replay log is a
spectator stream and shows both teams. Our agent declines Open Team Sheets
(`CLAUDE.md` constraint 2), so a model fit on what a spectator knows is fit on
information the agent will never have. Every test below that touches the
opponent asserts an absence.

**It has to agree with the live snapshot.** `champions/protocol/state.py` builds
the same thing from a poke-env battle during play, and if the two disagree the
model is served different inputs from the ones it was fit on -- silently, and
only in production. `turn_start` carries both the snapshot the agent saw and
that turn's protocol lines, so the agreement is checkable on 1,500 existing
traces rather than asserted.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any

import pytest

from champions.corpus.replay_state import Observer, turn_states
from champions.dex.loader import Dex

FORMAT_ID = "gen9championsvgc2026regmb"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


PREVIEW = [
    "|gametype|doubles",
    "|player|p1|alice|1|",
    "|player|p2|bob|266|",
    "|clearpoke",
    "|poke|p1|Metagross, L50, M|",
    "|poke|p1|Starmie, L50|",
    "|poke|p2|Dragonite, L50, F|",
    "|poke|p2|Milotic, L50, M|",
    "|teampreview",
]

LEAD = [
    "|start",
    "|switch|p1a: Metagross|Metagross, L50, M|100/100",
    "|switch|p1b: Starmie|Starmie, L50|100/100",
    "|switch|p2a: Dragonite|Dragonite, L50, F|100/100",
    "|switch|p2b: Milotic|Milotic, L50, M|100/100",
    "|turn|1",
]


def feed(dex: Dex, lines: list[str]) -> Observer:
    observer = Observer(dex)
    for line in lines:
        observer.feed(line)
    return observer


def active_species(view: dict[str, Any]) -> list[str | None]:
    return [None if p is None else p["species"] for p in view["ours"]["active"]]


# -- the board ---------------------------------------------------------------


def test_the_leads_are_on_the_field_and_the_rest_are_not(dex: Dex) -> None:
    view = feed(dex, PREVIEW + LEAD).view("p1")

    assert active_species(view) == ["metagross", "starmie"]
    assert view["turn"] == 1


def test_damage_moves_hp_and_a_faint_empties_the_slot(dex: Dex) -> None:
    lines = (
        PREVIEW
        + LEAD
        + [
            "|move|p2a: Dragonite|Extreme Speed|p1a: Metagross",
            "|-damage|p1a: Metagross|54/100",
            "|move|p2b: Milotic|Surf|p1a: Metagross",
            "|-damage|p1a: Metagross|0 fnt",
            "|faint|p1a: Metagross",
            "|turn|2",
        ]
    )
    view = feed(dex, lines).view("p1")

    # Empty, not occupied by a corpse: poke-env's `active_pokemon` holds None
    # for a slot awaiting its replacement, and the agent gets a turn in that
    # state. `state.snapshot()` is the authority on this and it records None.
    assert view["ours"]["active"][0] is None
    benched = {p["species"]: p for p in view["ours"]["bench"]}
    assert benched["metagross"]["fainted"] is True
    assert benched["metagross"]["hp_pct"] == 0.0
    assert view["ours"]["remaining"] == 1


def test_a_switch_puts_the_incoming_pokemon_in_the_slot(dex: Dex) -> None:
    lines = (
        PREVIEW
        + LEAD
        + [
            "|switch|p1a: Garchomp|Garchomp, L50, M|100/100",
            "|turn|2",
        ]
    )
    view = feed(dex, lines).view("p1")

    assert active_species(view) == ["garchomp", "starmie"]
    assert "metagross" in [p["species"] for p in view["ours"]["bench"]]


def test_boosts_status_weather_and_side_conditions_are_tracked(dex: Dex) -> None:
    lines = (
        PREVIEW
        + LEAD
        + [
            "|-boost|p1a: Metagross|atk|2",
            "|-status|p1b: Starmie|par",
            "|-weather|RainDance",
            "|-sidestart|p1: alice|move: Tailwind",
            "|turn|2",
        ]
    )
    view = feed(dex, lines).view("p1")

    assert view["ours"]["active"][0]["boosts"]["atk"] == 2
    assert view["ours"]["active"][1]["status"] == "PAR"
    assert "RAINDANCE" in view["weather"]
    assert "TAILWIND" in view["side_conditions"]


def test_an_hp_bar_colour_code_does_not_stop_the_damage_landing(dex: Dex) -> None:
    """Showdown appends `g`, `y` or `r` to the denominator when the bar colour
    differs from what the client would compute: `50/100g`. Parsed naively that
    raises, and a parser that swallows the error keeps the *previous* HP -- the
    one failure mode worse than crashing, because the state stays plausible."""
    lines = (
        PREVIEW
        + LEAD
        + [
            "|-damage|p2a: Dragonite|50/100g",
            "|-damage|p2b: Milotic|20/100r",
            "|turn|2",
        ]
    )
    view = feed(dex, lines).view("p1")

    assert view["theirs"]["active"][0]["hp_pct"] == 50.0
    assert view["theirs"]["active"][1]["hp_pct"] == 20.0


def test_the_protect_counter_rises_as_the_protect_resolves(dex: Dex) -> None:
    """Not at the next `|turn|`. A knockout gives the other player a forced
    switch, which is a decision point *inside* the turn -- the agent is asked to
    choose with the Protect already resolved, and poke-env has already counted
    it. Deferring to the turn boundary reports a stale counter at exactly the
    position a policy is being asked about."""
    lines = (
        PREVIEW
        + LEAD
        + [
            "|move|p1a: Metagross|Protect|p1a: Metagross",
            "|-singleturn|p1a: Metagross|Protect",
            "|move|p2a: Dragonite|Earthquake|p1b: Starmie",
            "|-damage|p1b: Starmie|0 fnt",
            "|faint|p1b: Starmie",
        ]
    )

    view = feed(dex, lines).view("p1")

    assert view["ours"]["active"][0]["protect_counter"] == 1


def test_a_protect_that_failed_does_not_count_towards_the_counter(dex: Dex) -> None:
    """Consecutive Protects fail with rising probability, which is what the
    counter is for. Counting the attempt rather than the success inverts it:
    the move that failed is exactly the one that resets it."""
    lines = (
        PREVIEW
        + LEAD
        + [
            "|move|p1a: Metagross|Protect|p1a: Metagross",
            "|-singleturn|p1a: Metagross|Protect",
            "|turn|2",
            "|move|p1a: Metagross|Protect||[still]",
            "|-fail|p1a: Metagross",
            "|turn|3",
        ]
    )
    view = feed(dex, lines).view("p1")

    assert view["ours"]["active"][0]["protect_counter"] == 0


# -- the observer's view, not the log's --------------------------------------


def test_the_opponents_hp_is_a_percentage_and_their_stats_are_unknown(dex: Dex) -> None:
    """The knowledge asymmetry `state.snapshot` encodes, from the other end. A
    model fit on exact opponent numbers is fit on information no agent has."""
    view = feed(dex, PREVIEW + LEAD).view("p1")

    theirs = view["theirs"]["active"][0]
    assert theirs["known"] is False
    assert theirs["stats"] is None
    assert theirs["hp"] is None and theirs["max_hp"] is None
    assert theirs["hp_pct"] == 100.0


def test_an_opponents_move_is_known_only_once_it_has_been_used(dex: Dex) -> None:
    before = feed(dex, PREVIEW + LEAD).view("p1")
    assert before["theirs"]["active"][0]["revealed_moves"] == []

    after = feed(
        dex,
        PREVIEW + LEAD + ["|move|p2a: Dragonite|Extreme Speed|p1a: Metagross", "|turn|2"],
    ).view("p1")
    assert [m["id"] for m in after["theirs"]["active"][0]["revealed_moves"]] == ["extremespeed"]


def test_open_team_sheets_do_not_leak_into_either_view(dex: Dex) -> None:
    """The corpus is open-sheet play and the agent always declines open sheets.
    A `|showteam|` in the log is information the agent will never hold, so it
    must not reach the opponent's half of anyone's view."""
    sheet = (
        "|showteam|p2|Dragonite||ChoiceBand|Multiscale|"
        "ExtremeSpeed,Outrage,Earthquake,Protect|Adamant|||||50|"
    )
    view = feed(dex, PREVIEW + [sheet] + LEAD).view("p1")

    theirs = view["theirs"]["active"][0]
    assert theirs["revealed_moves"] == []
    assert theirs["item"] is None
    assert theirs["ability"] is None


def test_each_side_sees_its_own_team_and_only_the_other_side_as_revealed(dex: Dex) -> None:
    """Two views out of one log, mirrored. Without this the corpus yields one
    training row per turn instead of two, and the p2 half is unusable."""
    observer = feed(dex, PREVIEW + LEAD)

    assert active_species(observer.view("p1")) == ["metagross", "starmie"]
    assert active_species(observer.view("p2")) == ["dragonite", "milotic"]
    assert observer.view("p2")["theirs"]["active"][0]["species"] == "metagross"
    assert observer.view("p2")["theirs"]["active"][0]["known"] is False


# -- one state per decision --------------------------------------------------


def test_a_state_is_yielded_for_each_side_at_the_start_of_each_turn(dex: Dex) -> None:
    """The decision points. A turn is where a player chose, so that is where a
    training row comes from -- the state at `|turn|n`, before the moves."""
    lines = (
        PREVIEW
        + LEAD
        + [
            "|move|p2a: Dragonite|Extreme Speed|p1a: Metagross",
            "|-damage|p1a: Metagross|54/100",
            "|turn|2",
        ]
    )

    states = list(turn_states(lines, dex))

    assert [turn for turn, _ in states] == [1, 2]
    assert states[0][1]["p1"]["ours"]["active"][0]["hp_pct"] == 100.0
    assert states[1][1]["p1"]["ours"]["active"][0]["hp_pct"] == 54.0


# -- agreement with the live snapshot ----------------------------------------


#: Enough traces to catch a systematic error, few enough to keep the suite fast.
#: Every field below was wrong at some point during this module's development
#: and each was found by this comparison rather than by reasoning.
EQUIVALENCE_TRACES = 40

#: Fields a replay observer genuinely cannot know, and which are therefore not
#: compared and not used as features: a spectator stream reports percentages for
#: both sides, so exact HP, stat spreads and PP are absent by construction.
NOT_IN_A_REPLAY = ("hp", "max_hp", "stats", "moves")


def test_the_reconstruction_matches_what_the_agent_actually_saw(dex: Dex) -> None:
    """The test this module exists to pass.

    `turn_start` carries the snapshot the agent built from poke-env *and* that
    turn's protocol lines. Rebuilding the second must produce the first, or the
    model is served different inputs from the ones it was fit on -- silently,
    and only once it is playing.

    At the last full run this held over all 1,500 traces: 11,774 turns and
    47,096 slot comparisons, zero mismatches. The sample here is smaller so the
    suite stays fast; the assertion is the same one.
    """
    paths = sorted(glob.glob("runs/m6-selfplay/*.jsonl"))[:EQUIVALENCE_TRACES]
    if not paths:
        pytest.skip("no self-play traces to check against")

    mismatches: list[str] = []
    turns_checked = 0

    for path in paths:
        events = [json.loads(line) for line in Path(path).read_text("utf-8").splitlines() if line]
        role = next(
            (e["payload"].get("player_role") for e in events if e["type"] == "battle_start"),
            None,
        )
        if role is None:
            continue

        # The per-turn log is incremental, not cumulative: turn 2 carries the
        # lines since turn 1. One observer fed in order is the only reading of
        # it that reproduces the battle.
        observer = Observer(dex)
        for event in (e for e in events if e["type"] == "turn_start"):
            payload = event["payload"]
            for line in payload["log"]:
                observer.feed(line)
            recorded, rebuilt = payload["state"], observer.view(role)
            turns_checked += 1
            where = f"{path.rsplit('/', 1)[-1]} turn {payload['turn']}"

            for side in ("ours", "theirs"):
                for field in ("remaining", "revealed"):
                    if recorded[side][field] != rebuilt[side][field]:
                        mismatches.append(
                            f"{where} {side}.{field}: "
                            f"{recorded[side][field]} != {rebuilt[side][field]}"
                        )
                for mine, theirs in zip(
                    rebuilt[side]["active"], recorded[side]["active"], strict=True
                ):
                    if (mine is None) != (theirs is None):
                        mismatches.append(f"{where} {side} slot occupancy")
                        continue
                    if mine is None:
                        continue
                    for field in ("species", "fainted", "status", "boosts", "protect_counter"):
                        # Guarded: traces written before a field existed do not
                        # carry it, and comparing against a missing key would
                        # report the trace's age as a reconstruction bug.
                        if field in theirs and mine[field] != theirs[field]:
                            mismatches.append(
                                f"{where} {side}.{field}: {theirs[field]} != {mine[field]}"
                            )
                    if abs(mine["hp_pct"] - theirs["hp_pct"]) > 0.6:
                        mismatches.append(
                            f"{where} {side}.hp_pct: {theirs['hp_pct']} != {mine['hp_pct']}"
                        )
                    if side == "theirs":
                        seen = sorted(m["id"] for m in mine["revealed_moves"])
                        known = sorted(m["id"] for m in theirs["revealed_moves"])
                        if seen != known:
                            mismatches.append(f"{where} theirs.revealed_moves: {known} != {seen}")

            for field in ("weather", "fields", "side_conditions", "opponent_side_conditions"):
                if set(recorded[field]) != set(rebuilt[field]):
                    mismatches.append(f"{where} {field}")

    assert turns_checked > 0, "no turns compared"
    detail = "\n".join(mismatches[:20])
    assert not mismatches, f"{len(mismatches)} of {turns_checked} turns disagree:\n{detail}"


def test_the_fields_a_replay_cannot_carry_are_absent_rather_than_invented(dex: Dex) -> None:
    """A spectator stream reports percentages for both sides, so our own exact
    HP, spread and PP are not in it. Emitted as None rather than guessed: a
    number that looks computed and is invented is worse than a missing one, and
    the feature set is built on fractions for this reason."""
    view = feed(dex, PREVIEW + LEAD).view("p1")

    ours = view["ours"]["active"][0]
    for field in NOT_IN_A_REPLAY:
        assert ours[field] in (None, []), field

"""The training rows a replay contributes: a choice set, a label, and vectors.

`docs/specs/2026-08-29-learned-policy-provider.md` section 3.4 fits a policy
prior to the corpus. A corpus row is not a state and a label -- it is a state, a
*choice set*, and which member of it the human picked -- and the choice set is
the part a replay does not contain. This file pins down how it is reconstructed
and what that reconstruction is allowed to use.

The rule these tests enforce: the choice set may use anything the acting player
knew, and nothing else. Their own four moves and their own bring-4 are theirs,
and both are recoverable from the log. The opponent's sheet is not theirs, and
`champions.corpus.replay_state` already drops it before the state is built.
"""

from __future__ import annotations

import numpy as np
import pytest

from champions.corpus.replay import parse_replay
from champions.dex.loader import Dex
from champions.search.policy_data import Decision, decisions_from_log, decisions_from_record
from champions.search.policy_features import (
    FEATURE_INDEX,
    FEATURE_NAMES,
    board_for,
    option_features,
)

FORMAT_ID = "gen9championsvgc2026regmb"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


P1_TEAM = (
    "Metagross||leftovers|clearbody|ironhead,psychicfangs,protect,bulletpunch|Jolly|||||50|"
    "]Corviknight||rockyhelmet|pressure|bravebird,tailwind,protect,uturn|Impish|||||50|"
    "]Milotic||sitrusberry|competitive|scald,icywind,recover,protect|Calm|||||50|"
    "]Sinistcha||occaberry|hospitality|matchagotcha,ragepowder,lifedew,trickroom|Calm|||||50|"
)
P2_TEAM = (
    "Starmie||lifeorb|naturalcure|hydropump,icebeam,thunderbolt,protect|Timid|||||50|"
    "]Dragonite||choiceband|multiscale|extremespeed,outrage,firepunch,earthquake|Adamant|||||50|"
    "]Incineroar||safetygoggles|intimidate|flareblitz,knockoff,fakeout,partingshot|Adamant|||||50|"
    "]Torkoal||charcoal|drought|eruption,heatwave,protect,bodypress|Quiet|||||50|"
)

HEADER = [
    "|player|p1|alice|1|1500",
    "|player|p2|bob|266|1500",
    "|teamsize|p1|4",
    "|teamsize|p2|4",
    "|gametype|doubles",
    "|rated",
    "|clearpoke",
    "|poke|p1|Metagross, L50, M|",
    "|poke|p1|Corviknight, L50, F|",
    "|poke|p1|Milotic, L50, M|",
    "|poke|p1|Sinistcha, L50|",
    "|poke|p2|Starmie, L50|",
    "|poke|p2|Dragonite, L50, F|",
    "|poke|p2|Incineroar, L50, M|",
    "|poke|p2|Torkoal, L50, F|",
    f"|showteam|p1|{P1_TEAM}",
    f"|showteam|p2|{P2_TEAM}",
    "|teampreview",
    "|start",
    "|switch|p1a: Metagross|Metagross, L50, M|100/100",
    "|switch|p1b: Corviknight|Corviknight, L50, F|100/100",
    "|switch|p2a: Starmie|Starmie, L50|100/100",
    "|switch|p2b: Dragonite|Dragonite, L50, F|100/100",
]

LOG = "\n".join(
    [
        *HEADER,
        "|turn|1",
        "|move|p1a: Metagross|Iron Head|p2a: Starmie",
        "|-damage|p2a: Starmie|40/100",
        "|move|p1b: Corviknight|Tailwind|p1b: Corviknight",
        "|-sidestart|p1: alice|move: Tailwind",
        "|move|p2a: Starmie|Ice Beam|p1b: Corviknight",
        "|-damage|p1b: Corviknight|55/100",
        "|switch|p2b: Incineroar|Incineroar, L50, M|100/100",
        "|turn|2",
        "|switch|p1a: Milotic|Milotic, L50, M|100/100",
        "|move|p1b: Corviknight|Brave Bird|p2b: Incineroar",
        "|-damage|p2b: Incineroar|30/100",
        "|move|p2a: Starmie|Hydro Pump|p1a: Milotic",
        "|-damage|p1a: Milotic|70/100",
        "|move|p2b: Incineroar|Fake Out|p1b: Corviknight",
        "|-damage|p1b: Corviknight|48/100",
        "|upkeep",
        "|turn|3",
        # The fourth takes the field, so all four are observed as brought and
        # the switch half of every earlier choice set is complete.
        "|switch|p1b: Sinistcha|Sinistcha, L50|100/100",
        "|move|p1a: Milotic|Scald|p2a: Starmie",
        "|-damage|p2a: Starmie|10/100",
        "|upkeep",
        "|turn|4",
        "|win|alice",
    ]
)

#: The same game, with our own Metagross knocked out during turn 1 and replaced
#: before turn 2. The replacement is not a turn-start choice, and the Pokemon it
#: replaced is not a switch option afterwards. `|upkeep|` is what separates the
#: two -- it is how the protocol says the choice phase is over, and a switch
#: after it is forced.
FAINT_LOG = "\n".join(
    [
        *HEADER,
        "|turn|1",
        "|move|p2a: Starmie|Hydro Pump|p1a: Metagross",
        "|-damage|p1a: Metagross|0 fnt",
        "|faint|p1a: Metagross",
        "|move|p1b: Corviknight|Tailwind|p1b: Corviknight",
        "|-sidestart|p1: alice|move: Tailwind",
        "|upkeep",
        "|switch|p1a: Milotic|Milotic, L50, M|100/100",
        "|turn|2",
        "|switch|p1b: Sinistcha|Sinistcha, L50|100/100",
        "|move|p1a: Milotic|Icy Wind|p2a: Starmie",
        "|-damage|p2a: Starmie|80/100",
        "|upkeep",
        "|turn|3",
        "|move|p1a: Milotic|Scald|p2a: Starmie",
        "|-damage|p2a: Starmie|20/100",
        "|upkeep",
        "|turn|4",
        "|win|alice",
    ]
)


CHARGE_P1_TEAM = (
    "Archaludon||assaultvest|stamina|electroshot,flashcannon,dracometeor,bodypress|Modest|||||50|"
    "]Torkoal||charcoal|drought|solarbeam,eruption,heatwave,protect|Quiet|||||50|"
    "]Corviknight||rockyhelmet|pressure|bravebird,tailwind,protect,uturn|Impish|||||50|"
    "]Milotic||sitrusberry|competitive|scald,icywind,recover,protect|Calm|||||50|"
)

#: Two-turn moves, which the protocol reports in three ways and only one of them
#: is the ordinary one.
#:
#: Turn 1: Torkoal has Drought, so Solar Beam charges and fires in the same
#: turn. The `|move|` line prints no target and the `|-anim|` line does.
#:
#: Turn 2 and 3: Electro Shot charges with no rain up, so the release is a turn
#: later. The `|move|` line prints no target, the release does, and the release
#: is tagged `[from] lockedmove` because the slot had no choice about it.
CHARGE_LOG = "\n".join(
    [
        "|player|p1|alice|1|1500",
        "|player|p2|bob|266|1500",
        "|teamsize|p1|4",
        "|teamsize|p2|4",
        "|gametype|doubles",
        "|rated",
        "|clearpoke",
        "|poke|p1|Archaludon, L50, M|",
        "|poke|p1|Torkoal, L50, F|",
        "|poke|p1|Corviknight, L50, F|",
        "|poke|p1|Milotic, L50, M|",
        "|poke|p2|Starmie, L50|",
        "|poke|p2|Dragonite, L50, F|",
        "|poke|p2|Incineroar, L50, M|",
        "|poke|p2|Torkoal, L50, F|",
        f"|showteam|p1|{CHARGE_P1_TEAM}",
        f"|showteam|p2|{P2_TEAM}",
        "|teampreview",
        "|start",
        "|switch|p1a: Archaludon|Archaludon, L50, M|100/100",
        "|switch|p1b: Torkoal|Torkoal, L50, F|100/100",
        "|switch|p2a: Starmie|Starmie, L50|100/100",
        "|switch|p2b: Dragonite|Dragonite, L50, F|100/100",
        "|-weather|SunnyDay|[from] ability: Drought|[of] p1b: Torkoal",
        "|turn|1",
        "|move|p1b: Torkoal|Solar Beam||[still]",
        "|-prepare|p1b: Torkoal|Solar Beam",
        "|-anim|p1b: Torkoal|Solar Beam|p2b: Dragonite",
        "|-damage|p2b: Dragonite|60/100",
        "|move|p1a: Archaludon|Body Press|p2a: Starmie",
        "|-damage|p2a: Starmie|70/100",
        "|upkeep",
        "|turn|2",
        "|move|p1a: Archaludon|Electro Shot||[still]",
        "|-prepare|p1a: Archaludon|Electro Shot",
        "|-boost|p1a: Archaludon|spa|1",
        "|move|p1b: Torkoal|Protect|p1b: Torkoal",
        "|-singleturn|p1b: Torkoal|Protect",
        "|upkeep",
        "|turn|3",
        "|move|p1a: Archaludon|Electro Shot|p2a: Starmie|[from] lockedmove",
        "|-damage|p2a: Starmie|10/100",
        "|move|p1b: Torkoal|Heat Wave|p2a: Starmie|[spread] p2a,p2b",
        "|-damage|p2b: Dragonite|40/100",
        "|upkeep",
        "|turn|4",
        "|win|alice",
    ]
)


#: A Mega that evolves, pivots out and comes back. The switch back in is
#: announced with the Mega's own details, so from turn 4 the field and the sheet
#: are calling the same Pokemon two different names.
MEGA_LOG = "\n".join(
    [
        *HEADER,
        "|turn|1",
        "|-mega|p1a: Metagross|Metagross|Metagrossite",
        "|detailschange|p1a: Metagross|Metagross-Mega, L50, M",
        "|move|p1a: Metagross|Iron Head|p2a: Starmie",
        "|-damage|p2a: Starmie|40/100",
        "|move|p1b: Corviknight|Tailwind|p1b: Corviknight",
        "|-sidestart|p1: alice|move: Tailwind",
        "|upkeep",
        "|turn|2",
        "|switch|p1a: Milotic|Milotic, L50, M|100/100",
        "|move|p1b: Corviknight|Brave Bird|p2a: Starmie",
        "|-damage|p2a: Starmie|20/100",
        "|upkeep",
        "|turn|3",
        "|switch|p1a: Metagross|Metagross-Mega, L50, M|100/100",
        "|move|p1b: Corviknight|Brave Bird|p2a: Starmie",
        "|-damage|p2a: Starmie|10/100",
        "|upkeep",
        "|turn|4",
        "|move|p1a: Metagross|Bullet Punch|p2a: Starmie",
        "|-damage|p2a: Starmie|5/100",
        "|upkeep",
        "|turn|5",
        "|win|alice",
    ]
)


@pytest.fixture(scope="module")
def decisions(dex: Dex) -> list[Decision]:
    return list(decisions_from_log(LOG, "test-1", dex))


def one(rows: list[Decision], turn: int, side: str, slot: int) -> Decision:
    found = [d for d in rows if d.turn == turn and d.side == side and d.slot == slot]
    assert len(found) == 1, f"expected exactly one decision at turn {turn}, {side}, slot {slot}"
    return found[0]


def signature(rows: list[Decision]) -> list[tuple]:
    """A Decision reduced to what can be compared with `==`.

    Not `__eq__` on the dataclass: it carries a numpy array, and comparing two
    of those returns an array rather than a bool.
    """
    return [
        (d.battle_id, d.player, d.side, d.turn, d.slot, d.chosen, tuple(d.features.ravel()))
        for d in rows
    ]


# -- the choice set ----------------------------------------------------------


def test_a_slots_choice_set_is_its_own_sheet_moves_times_targets_plus_switches(
    decisions: list,
) -> None:
    """The acting player's own four moves, each at every legal target, plus a
    switch to each living bench member of the four they brought.

    Metagross on turn 1 holds Iron Head, Psychic Fangs and Bullet Punch -- all
    `normal`, so an ally and two foes, three targets each -- and Protect, which
    is `self` and takes no target choice. Two of the four brought are on the
    bench, so two switches.
    """
    decision = one(decisions, 1, "p1", 0)
    moves = [o for o in decision.options if o["kind"] == "move"]
    switches = [o for o in decision.options if o["kind"] == "switch"]

    assert sorted({o["move"] for o in moves}) == [
        "bulletpunch",
        "ironhead",
        "protect",
        "psychicfangs",
    ]
    assert len(moves) == 3 * 3 + 1
    assert sorted(o["species"] for o in switches) == ["milotic", "sinistcha"]


def test_the_chosen_option_is_the_one_the_human_played_with_its_target(
    decisions: list,
) -> None:
    decision = one(decisions, 1, "p1", 0)
    chosen = decision.options[decision.chosen]
    assert chosen["kind"] == "move"
    assert chosen["move"] == "ironhead"
    # Starmie is the opponent's first slot, which is +1 from our side.
    assert chosen["target"] == 1


def test_a_move_aimed_at_our_own_side_is_labelled_with_a_negative_target(
    decisions: list,
) -> None:
    """Tailwind is `allySide`, which takes no target choice, so the label is the
    no-target option rather than an ally slot. The signed encoding is still what
    the option carries, and getting the sign backwards would silently relabel
    every ally-targeting row."""
    decision = one(decisions, 1, "p1", 1)
    chosen = decision.options[decision.chosen]
    assert chosen["move"] == "tailwind"
    assert chosen["target"] == 0


def test_a_voluntary_switch_is_a_decision_and_names_what_came_in(
    decisions: list,
) -> None:
    decision = one(decisions, 2, "p1", 0)
    chosen = decision.options[decision.chosen]
    assert chosen["kind"] == "switch"
    assert chosen["species"] == "milotic"


def test_a_replacement_switch_is_not_a_decision(dex: Dex) -> None:
    """A Pokemon sent in after a faint was not chosen at the start of a turn, so
    it is not a turn-start decision and must not become a training row."""
    rows = list(decisions_from_log(FAINT_LOG, "test-2", dex))
    assert not [d for d in rows if d.turn == 1 and d.side == "p1" and d.slot == 0]
    # The slot the faint emptied still decides on the following turn.
    assert one(rows, 2, "p1", 0).options[0] is not None


def test_a_fainted_pokemon_is_not_a_switch_option(dex: Dex) -> None:
    """Metagross fainted on turn 1, so turn 2's bench is Sinistcha alone even
    though three of the four are off the field."""
    rows = list(decisions_from_log(FAINT_LOG, "test-3", dex))
    decision = one(rows, 2, "p1", 1)
    assert sorted(o["species"] for o in decision.options if o["kind"] == "switch") == ["sinistcha"]


def test_a_move_the_slot_was_locked_into_is_not_a_decision(dex: Dex) -> None:
    """Showdown tags the second turn of a two-turn move `[from] lockedmove`.
    Counting it as a choice trains the model on a row claiming the player
    picked, out of four moves and every switch, the one move they had no choice
    about."""
    rows = list(decisions_from_log(CHARGE_LOG, "test-locked", dex))
    assert not [d for d in rows if d.turn == 3 and d.side == "p1" and d.slot == 0]
    # The partner slot chose freely on the same turn and still yields a row.
    assert one(rows, 3, "p1", 1).options


def test_a_charge_that_fires_the_same_turn_takes_its_target_from_the_animation(
    dex: Dex,
) -> None:
    """Solar Beam under Torkoal's own sun is a one-turn move, and the `|move|`
    line still prints no target. `|-anim|` is the only line that names it, and
    without reading it the whole decision is dropped for being unresolvable."""
    decision = one(list(decisions_from_log(CHARGE_LOG, "test-anim", dex)), 1, "p1", 1)
    chosen = decision.options[decision.chosen]
    assert chosen["move"] == "solarbeam"
    assert chosen["target"] == 2


def test_a_charge_that_fires_next_turn_takes_its_target_from_the_release(dex: Dex) -> None:
    """Electro Shot with no rain up charges on turn 2 and fires on turn 3. The
    choice was made on turn 2 and the only line that names the target is on turn
    3, so the row for turn 2 is built from both."""
    decision = one(list(decisions_from_log(CHARGE_LOG, "test-locked-2", dex)), 2, "p1", 0)
    chosen = decision.options[decision.chosen]
    assert chosen["move"] == "electroshot"
    assert chosen["target"] == 1


def test_a_move_that_failed_and_named_no_target_yields_no_row(dex: Dex) -> None:
    """A Sucker Punch that fails prints `[still]` and `|-fail|` and never says
    what it was aimed at. There is no line to recover it from, so the decision
    is dropped -- a label guessed out of the choice set is worse than none."""
    log = CHARGE_LOG.replace(
        "|move|p1a: Archaludon|Body Press|p2a: Starmie\n|-damage|p2a: Starmie|70/100",
        "|move|p1a: Archaludon|Flash Cannon||[still]\n|-fail|p1a: Archaludon",
    )
    rows = list(decisions_from_log(log, "test-fail", dex))
    assert not [d for d in rows if d.turn == 1 and d.side == "p1" and d.slot == 0]


def test_a_switch_is_only_offered_to_the_ones_that_were_brought(dex: Dex) -> None:
    """Six are registered and four are played. A choice set built from preview
    would offer two switches that were never legal."""
    log = LOG.replace(
        "|poke|p1|Sinistcha, L50|",
        "|poke|p1|Sinistcha, L50|\n|poke|p1|Tsareena, L50, F|\n|poke|p1|Gholdengo, L50|",
    )
    decision = one(list(decisions_from_log(log, "test-4", dex)), 1, "p1", 0)
    assert "tsareena" not in {o.get("species") for o in decision.options}


def test_a_mega_forme_still_finds_its_own_move_set(dex: Dex) -> None:
    """The sheet registers Metagross and a Mega that switches back in is
    announced as Metagross-Mega. Without the base-species fallback, every
    decision it makes from then on finds no move set and is dropped -- which
    would thin the corpus of the format's most distinctive Pokemon in proportion
    to how often they are worth using. Champions has 75 legal stones, and 170 of
    3,900 slots in a 120-replay sample were lost to exactly this."""
    decision = one(list(decisions_from_log(MEGA_LOG, "test-mega", dex)), 4, "p1", 0)
    assert decision.snapshot["ours"]["active"][0]["species"] == "metagrossmega"
    assert {o["move"] for o in decision.options if o["kind"] == "move"} == {
        "ironhead",
        "psychicfangs",
        "protect",
        "bulletpunch",
    }


def test_a_forme_the_sheet_names_directly_is_not_collapsed_to_its_base(dex: Dex) -> None:
    """Rotom-Wash and Ninetales-Alola are on the sheet under their own names and
    both have a different `baseSpecies`, so a lookup that collapsed to the base
    unconditionally would break the ones that were already right. Exact first,
    base second."""
    from champions.search.policy_data import _sheet_moves

    movesets = {("p1", "rotomwash"): ("hydropump",), ("p1", "rotom"): ("thunderbolt",)}
    assert _sheet_moves("Rotom-Wash", "p1", movesets, dex) == ("hydropump",)
    assert _sheet_moves("Rotom-Heat", "p1", movesets, dex) == ("thunderbolt",)


def test_each_sides_options_come_from_its_own_sheet(decisions: list) -> None:
    """p1's rows are built from p1's own sheet and p2's from p2's. The state
    each is read off is already one-sided; this is the other half of the same
    property, on the option list rather than on the board."""
    ours = one(decisions, 1, "p1", 0)
    theirs = one(decisions, 1, "p2", 0)
    assert {o["move"] for o in ours.options if o["kind"] == "move"} == {
        "ironhead",
        "psychicfangs",
        "protect",
        "bulletpunch",
    }
    assert {o["move"] for o in theirs.options if o["kind"] == "move"} == {
        "hydropump",
        "icebeam",
        "thunderbolt",
        "protect",
    }


def test_a_slot_whose_choice_the_log_does_not_show_yields_no_row(dex: Dex) -> None:
    """A move that was chosen and prevented shows up as the prevention. There is
    then no label, and a row with a guessed label is worse than no row."""
    log = LOG.replace("|move|p1a: Metagross|Iron Head|p2a: Starmie\n", "")
    rows = list(decisions_from_log(log, "test-5", dex))
    assert not [d for d in rows if d.turn == 1 and d.side == "p1" and d.slot == 0]
    assert [d for d in rows if d.turn == 1 and d.side == "p1" and d.slot == 1]


def test_every_target_the_dex_uses_is_in_the_table_the_live_enumeration_reads(
    dex: Dex,
) -> None:
    """`policy_data.TARGET_SLOTS` is poke-env's own table rather than a copy,
    because a copy is how the reconstructed choice set and the served one stop
    matching. It is keyed in upper snake and the dex spells targets in camel, so
    the conversion is what actually has to hold."""
    from champions.search.policy_data import _CAMEL, TARGET_SLOTS

    missing = {
        str(entry.get("target"))
        for entry in dex.moves.values()
        if _CAMEL.sub("_", str(entry.get("target") or "normal")).upper() not in TARGET_SLOTS
    }
    assert not missing


# -- the vectors -------------------------------------------------------------


def test_every_option_carries_a_finite_vector_of_the_right_shape(decisions: list) -> None:
    assert decisions
    for decision in decisions:
        assert decision.features.shape == (len(decision.options), len(FEATURE_NAMES))
        assert np.isfinite(decision.features).all()


def test_the_vectors_are_the_shared_feature_path_and_not_a_second_one(
    decisions: list, dex: Dex
) -> None:
    """`policy_features.option_features` is the one path (spec section 3.2).
    This checks the dataset calls it rather than growing its own copy, which is
    the failure the one-path rule exists to prevent."""
    decision = one(decisions, 1, "p1", 0)
    board = board_for(decision.snapshot, dex)
    for option, row in zip(decision.options, decision.features, strict=True):
        expected = option_features(decision.snapshot, decision.slot, option, board)
        assert np.array_equal(row, expected)


def test_a_switch_option_is_marked_as_one_in_its_vector(decisions: list) -> None:
    decision = one(decisions, 1, "p1", 0)
    for option, row in zip(decision.options, decision.features, strict=True):
        assert row[FEATURE_INDEX["is_switch"]] == float(option["kind"] == "switch")


# -- what the rows carry for the split ---------------------------------------


def test_a_row_names_the_player_that_made_it_so_the_split_can_hold_them_out(
    decisions: list,
) -> None:
    """Section 3.4 splits by player, not by replay: M4 found a random split hid
    a model that did not transfer to unseen players."""
    assert {d.player for d in decisions} == {"alice", "bob"}
    assert all(d.battle_id == "test-1" for d in decisions)


def test_open_sheet_play_is_flagged_on_every_row(decisions: list) -> None:
    """The corpus is open-sheet and the agent is not (spec section 2). The flag
    is what lets the closed-sheet slice be reported as its own line."""
    assert all(d.sheets_revealed for d in decisions)


def test_a_closed_sheet_replay_yields_nothing_unless_the_caller_asks(dex: Dex) -> None:
    """Without `|showteam|` there is no sheet to build a move set from, so the
    choice set can only be the moves that were revealed. That is a smaller and
    differently-biased set, so it takes an explicit flag rather than arriving by
    default."""
    log = "\n".join(line for line in LOG.split("\n") if not line.startswith("|showteam|"))
    assert list(decisions_from_log(log, "test-6", dex)) == []

    rows = list(decisions_from_log(log, "test-6", dex, revealed_moves_fallback=True))
    decision = one(rows, 1, "p1", 0)
    assert not decision.sheets_revealed
    # Metagross only ever revealed Iron Head, so that is the whole move half.
    assert {o["move"] for o in decision.options if o["kind"] == "move"} == {"ironhead"}


def test_the_record_can_be_passed_in_so_a_caller_parses_the_log_once(dex: Dex) -> None:
    record = parse_replay("test-7", LOG)
    assert signature(list(decisions_from_record(record, LOG, dex))) == signature(
        list(decisions_from_log(LOG, "test-7", dex))
    )

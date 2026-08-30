"""M6: turning games into labelled positions.

These tests exist because the two sources have to agree. A model fit on corpus
positions and applied to live ones is only meaningful if "the same position"
means the same thing in both, and almost every bug found while building this was
a way for them to differ silently: the opponent uncensored on one side, our own
bring counted differently, a position labelled with the turn after the one it
faced.

So most of what is asserted here is a property that must hold in both, checked
against a hand-built protocol log short enough to reason about.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from champions.search import positions

# Two Pokemon each on the field, two each in the back, and nothing has happened.
# Written out rather than fixtured because every assertion below is about what
# one of these lines does.
LOG = """|player|p1|Alice|lance|
|player|p2|Bob|allister|
|teamsize|p1|6
|teamsize|p2|6
|gametype|doubles
|tier|[Gen 9 Champions] VGC 2026 Reg M-B
|start
|switch|p1a: Milotic|Milotic, L50, F|100/100
|switch|p1b: Gengar|Gengar, L50, M|100/100
|switch|p2a: Incineroar|Incineroar, L50, M|100/100
|switch|p2b: Garchomp|Garchomp, L50, M|100/100
|turn|1
|move|p1a: Milotic|Scald|p2a: Incineroar
|-damage|p2a: Incineroar|60/100
|-status|p2a: Incineroar|brn
|turn|2
|switch|p1a: Vanilluxe|Vanilluxe, L50, F|100/100
|move|p2b: Garchomp|Earthquake|p1a: Vanilluxe
|-damage|p1a: Vanilluxe|20/100
|-sidestart|p2: Tailwind|move: Tailwind
|turn|3
|move|p2b: Garchomp|Dragon Claw|p1a: Vanilluxe
|-damage|p1a: Vanilluxe|0 fnt
|faint|p1a: Vanilluxe
|turn|4
|move|p1b: Gengar|Shadow Ball|p2a: Incineroar
|-damage|p2a: Incineroar|0 fnt
|faint|p2a: Incineroar
|win|Alice
"""


def rows_for(side: str) -> list[positions.Position]:
    return [p for p in positions.from_replay_log(LOG, "test-1") if p.battle_id.endswith(side)]


# -- reading the result ------------------------------------------------------


def test_the_winner_is_resolved_from_the_player_lines() -> None:
    """`|win|` names a player, not a side."""
    assert positions.winner_side(LOG) == "p1"


def test_a_tier_line_is_not_a_tie() -> None:
    """`|tier|` starts with `|tie`. Matching on a prefix silently made every
    replay in the corpus unusable and reported nothing at all."""
    assert positions.winner_side(LOG) == "p1"
    assert positions.winner_side(LOG.replace("|win|Alice", "|tie")) is None


def test_both_viewpoints_are_emitted_with_opposite_labels() -> None:
    """A model fit on one side would learn whatever asymmetry the log's choice
    of `p1` carries."""
    p1, p2 = rows_for("p1"), rows_for("p2")
    assert len(p1) == len(p2)
    assert {r.label for r in p1} == {1}
    assert {r.label for r in p2} == {0}


# -- the properties both sources have to share -------------------------------


def test_the_opening_position_is_even_from_both_sides() -> None:
    """Antisymmetry at turn 1, which is the sharpest available check that the
    two sides are being counted by the same rule.

    It failed three different ways while this was built: our own bring counted
    as six against an opponent counted as four, our side counted only as what had
    walked on so far, and a side that played three Pokemon counted as three
    against an opponent always counted as four.
    """
    first = {r.battle_id[-2:]: r for r in positions.from_replay_log(LOG, "t") if r.turn == 1}
    for name, value in first["p1"].features.items():
        assert value + first["p2"].features[name] == pytest.approx(0.0), name


def test_a_position_is_scored_before_the_turn_it_faces() -> None:
    """Turn 1's row is the board the player chose from, not the wreckage after.

    Scored the other way, every row carries the consequences of the decision it
    is supposed to be evaluating, which is a label leak dressed as an off-by-one.
    """
    p1 = {r.turn: r for r in rows_for("p1")}
    # Milotic's Scald lands during turn 1, so its damage must not be in turn 1.
    assert p1[1].features["hp_advantage"] == pytest.approx(0.0)
    assert p1[2].features["hp_advantage"] > 0.0


def test_the_opponent_is_censored_to_what_has_been_revealed() -> None:
    """A replay log shows both teams. Feeding that in would fit a model on
    information the live agent never has."""
    board = positions.Board(positions.brought_sides(_observations()))
    for observation in _observations():
        board.feed(observation)
    theirs = board.snapshot("p1", positions.brought_sides(_observations()))["theirs"]
    seen = [p for p in theirs["active"] if p] + list(theirs["bench"])
    assert {p["species"] for p in seen} == {"Incineroar", "Garchomp"}
    assert all(p["known"] is False for p in seen)


def test_a_side_that_played_three_is_still_counted_as_four() -> None:
    """Everyone brings four. A Pokemon that never came out was in the back at
    full health, not absent."""
    brought = positions.brought_sides(_observations())
    assert len(brought["p1"]) == positions.PICKED_TEAM_SIZE
    assert len(brought["p2"]) == positions.PICKED_TEAM_SIZE
    assert "Milotic" in brought["p1"]


# -- what the board tracks ---------------------------------------------------


def test_damage_status_and_faints_land_on_the_right_pokemon() -> None:
    rows = {r.turn: r for r in rows_for("p1")}
    # Turn 2: Incineroar is burned and at 60%, so we are ahead on HP and status.
    assert rows[2].features["status_advantage"] > 0.0
    assert rows[2].features["hp_advantage"] > 0.0
    # Turn 3 is scored before its own events, so the faint that happens during
    # it is not in it; turn 4 is where we are a Pokemon down.
    assert rows[3].features["pokemon_advantage"] == pytest.approx(0.0)
    assert rows[4].features["pokemon_advantage"] == pytest.approx(-1.0)


def test_the_final_position_is_not_scored() -> None:
    """The game is over, `faint_swing` short circuits it to 0 or 1, and a
    terminal state teaches a calibration fit nothing except that decided games
    are decided. Incineroar faints during turn 4; no turn 5 row exists."""
    assert max(r.turn for r in rows_for("p1")) == 4


def test_side_conditions_are_read_from_the_protocols_own_spelling() -> None:
    """`|-sidestart|p2|move: Tailwind` -- the prefix and the case both vary."""
    rows = {r.turn: r for r in rows_for("p1")}
    assert rows[3].features["speed_control"] == pytest.approx(-1.0)
    assert rows[3].features["speed_control"] == -rows_for("p2")[2].features["speed_control"]


def test_boosts_are_cleared_when_a_pokemon_switches_out() -> None:
    """A benched Pokemon's recorded boosts are stale, and scoring them credits
    an advantage that left the field."""
    board = positions.Board({"p1": {"Milotic", "Vanilluxe"}, "p2": set()})
    for observation in positions.parser.parse_log(LOG)[1]:
        board.feed(observation)
    milotic = board.mons["p1"]["Milotic"]
    assert milotic.active is False
    assert milotic.boosts == {}


# -- traces ------------------------------------------------------------------


def test_a_trace_without_a_result_yields_nothing(tmp_path: Path) -> None:
    """A file still being written, or a run stopped mid-battle, is a normal
    thing to find in a trace directory and not an error."""
    path = tmp_path / "unfinished.jsonl"
    path.write_text(
        json.dumps({"type": "battle_start", "battle_id": "b", "seq": 1, "payload": {}}) + "\n",
        encoding="utf-8",
    )
    assert positions.from_trace_file(path) == []


def test_a_trace_predating_the_bring_field_is_refused(tmp_path: Path) -> None:
    """Loudly, because the alternative is silent: without `selected` our side
    counts six against an opponent counted as four and every material feature is
    offset by a constant the fit would absorb into an intercept."""
    path = tmp_path / "old.jsonl"
    old_snapshot = {
        "ours": {"active": [{"fainted": False, "hp_pct": 100.0}], "bench": [], "remaining": 6},
        "theirs": {"active": [], "bench": [], "remaining": 4},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "turn": 1,
    }
    lines = [
        {"type": "battle_start", "battle_id": "b", "seq": 1, "payload": {}},
        {"type": "turn_start", "battle_id": "b", "seq": 2, "payload": {"state": old_snapshot}},
        {"type": "battle_end", "battle_id": "b", "seq": 3, "payload": {"result": "win"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in lines) + "\n", encoding="utf-8")

    with pytest.raises(positions.SnapshotTooOldError, match="selected"):
        positions.from_trace_file(path)


def _observations() -> list[positions.parser.Observation]:
    return positions.parser.parse_log(LOG)[1]

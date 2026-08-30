"""The pruning guard: what the candidate policy throws away.

`docs/04-decision-engine.md` section 3 requires this measurement and
`policy.discard_rate` implements the per-position half of it. What was missing
was everything around it -- reading real decisions out of traces, holding the
opponent's columns fixed while the row set changes, and reporting a number over
battles rather than over positions.

Almost every assertion here is about a way the harness could report a
comfortable number for the wrong reason: pairing a snapshot with another turn's
action set, measuring a legal set the tracer had already truncated, or
resampling positions instead of battles and reporting an interval twenty times
too narrow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from champions.search import discard

# -- a trace, hand built -----------------------------------------------------
#
# Three legal joint actions and one opponent column, which is the shape a real
# turn-one decision has under the revealed-moves-only opponent model.


def action(message: str) -> dict[str, Any]:
    return {
        "message": message,
        "label": message,
        "slots": [{"kind": "move", "move": message, "target": 1, "priority": 0}],
    }


LEGAL = [action("a"), action("b"), action("c")]
COLUMN = [{"message": "unrevealed", "slots": [{"kind": "none"}]}]
SNAPSHOT = {"turn": 1, "marker": "turn-1"}


def events(
    *,
    turn: int = 1,
    kept: tuple[str, ...] = ("a", "b"),
    battle_id: str = "battle-1",
    truncated: bool = False,
) -> list[dict[str, Any]]:
    return [
        {
            "type": "turn_start",
            "battle_id": battle_id,
            "payload": {"turn": turn, "state": dict(SNAPSHOT)},
        },
        {
            "type": "candidates",
            "battle_id": battle_id,
            "payload": {
                "turn": turn,
                "pruned": False,
                "truncated": truncated,
                "n_legal_joint_actions": len(LEGAL),
                "joint": LEGAL,
            },
        },
        {
            "type": "candidates",
            "battle_id": battle_id,
            "payload": {
                "turn": turn,
                "pruned": True,
                "k": len(kept),
                "joint": [a for a in LEGAL if a["message"] in kept],
                "opponent_joint": COLUMN,
            },
        },
    ]


def write(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def matrix_fn(
    snapshot: dict[str, Any],
    ours: list[dict[str, Any]],
    theirs: list[dict[str, Any]],
) -> np.ndarray:
    """Row `c` is the best answer to the only column, and the hand-built pruned
    set is the one that discards it."""
    assert snapshot["marker"] == "turn-1", "the snapshot was paired with the wrong turn"
    scores = {"a": 0.1, "b": 0.2, "c": 0.9}
    return np.array([[scores[o["message"]]] for o in ours], dtype=float)


def first_k(
    snapshot: dict[str, Any], actions: list[dict[str, Any]], k: int
) -> list[dict[str, Any]]:
    return actions[:k]


# -- reading decisions out of a trace ----------------------------------------


def test_a_decision_pairs_the_snapshot_with_that_turn_s_actions(tmp_path: Path) -> None:
    """The two `candidates` events and the `turn_start` before them are one
    decision. Pairing across turns would measure a position that never was."""
    rows = events(turn=1) + events(turn=2, kept=("a",))
    rows[3]["payload"]["state"] = {"turn": 2, "marker": "turn-2"}
    path = write(tmp_path / "t.jsonl", rows)

    found = discard.decisions(discard.read_events(path))

    assert [d.turn for d in found] == [1, 2]
    assert found[0].snapshot["marker"] == "turn-1"
    assert found[1].snapshot["marker"] == "turn-2"


def test_the_viewpoint_separates_the_two_traces_of_one_self_play_game(tmp_path: Path) -> None:
    """Self-play writes both sides of a game under one battle id and one turn
    numbering, so battle and turn alone name two different positions. Measured
    without the viewpoint, half of every self-play run silently collapsed onto
    the other half wherever positions were keyed rather than counted."""
    rows = [{"type": "battle_start", "battle_id": "b", "payload": {"player_role": "p2"}}]
    path = write(tmp_path / "t.jsonl", rows + events(battle_id="b"))

    [decision] = discard.decisions(discard.read_events(path))

    assert decision.viewpoint == "p2"


def test_a_truncated_action_list_is_refused_rather_than_measured(tmp_path: Path) -> None:
    """The tracer caps the legal list at 300. A truncated list is not the
    unpruned game, and measuring it would report a discard rate against a row
    set that is itself pruned -- silently, and in the flattering direction."""
    path = write(tmp_path / "t.jsonl", events(truncated=True))

    assert discard.decisions(discard.read_events(path)) == []


def test_a_decision_with_no_search_is_skipped(tmp_path: Path) -> None:
    """A watchdog that fires before the search emits its pruned event leaves a
    turn with a legal set and no candidate set. There is nothing to measure."""
    rows = events()
    del rows[2]
    path = write(tmp_path / "t.jsonl", rows)

    assert discard.decisions(discard.read_events(path)) == []


# -- the measurement ---------------------------------------------------------


def test_the_policy_is_handed_the_position_it_is_pruning(tmp_path: Path) -> None:
    """The implementation A `docs/04-decision-engine.md` section 3 specifies is
    four questions about the board, so a guard that hands a provider only the
    action list can measure only a provider that ignores the board -- which is
    the one the guard was written against and the one it found wanting."""
    path = write(tmp_path / "t.jsonl", events())
    seen: list[dict[str, Any]] = []

    def keep(
        snapshot: dict[str, Any], actions: list[dict[str, Any]], k: int
    ) -> list[dict[str, Any]]:
        seen.append(snapshot)
        return actions[:k]

    discard.measure_file(path, matrix_fn, keep=keep, k=2)

    assert [s["marker"] for s in seen] == ["turn-1"]


def test_mass_on_a_discarded_row_is_reported(tmp_path: Path) -> None:
    """The guard itself: the unpruned equilibrium puts everything on `c`, which
    the pruned set does not contain."""
    path = write(tmp_path / "t.jsonl", events())

    [measured] = discard.measure_file(path, matrix_fn, keep=first_k, k=2)

    assert measured.discarded_mass == pytest.approx(1.0)
    assert measured.n_legal == 3
    assert measured.n_columns == 1


def test_keeping_the_best_row_discards_nothing(tmp_path: Path) -> None:
    path = write(tmp_path / "t.jsonl", events())

    def best(
        snapshot: dict[str, Any], actions: list[dict[str, Any]], k: int
    ) -> list[dict[str, Any]]:
        return [a for a in actions if a["message"] == "c"][:k]

    [measured] = discard.measure_file(path, matrix_fn, keep=best, k=1)

    assert measured.discarded_mass == pytest.approx(0.0)
    assert measured.value_loss == pytest.approx(0.0)


def test_value_loss_is_what_the_pruning_cost_in_win_probability(tmp_path: Path) -> None:
    """Mass is the number section 3 asks for, and it is all or nothing: a
    discarded row worth 0.9001 against a kept row worth 0.9 reports the same 1.0
    as a discarded row that wins outright. The value loss says which it was."""
    path = write(tmp_path / "t.jsonl", events())

    [measured] = discard.measure_file(path, matrix_fn, keep=first_k, k=2)

    assert measured.value_loss == pytest.approx(0.7)


def test_the_policy_chooses_the_kept_rows_rather_than_the_trace(tmp_path: Path) -> None:
    """The trace records one k. Sweeping k means re-deriving the kept set, and
    the agreement between the two at the trace's own k is the check that the
    re-derivation is the selection the agent actually made."""
    path = write(tmp_path / "t.jsonl", events())

    def last_k(
        snapshot: dict[str, Any], actions: list[dict[str, Any]], k: int
    ) -> list[dict[str, Any]]:
        return actions[-k:]

    agreed = discard.measure_file(path, matrix_fn, keep=first_k, k=2)
    disagreed = discard.measure_file(path, matrix_fn, keep=last_k, k=2)

    assert agreed[0].matches_trace is True
    assert disagreed[0].matches_trace is False


def test_a_position_with_nothing_to_prune_is_not_a_measurement(tmp_path: Path) -> None:
    """Three legal actions and a budget of three discarded nothing because
    nothing could be discarded. Counting that zero would dilute the rate with
    every forced turn in the corpus rather than measure the policy."""
    path = write(tmp_path / "t.jsonl", events())

    assert discard.measure_file(path, matrix_fn, keep=first_k, k=3) == []


def test_a_sweep_of_budgets_solves_the_unpruned_game_once(tmp_path: Path) -> None:
    """The matrix does not depend on `k` and is the expensive half. Rebuilding
    it per budget would multiply a sweep's cost by its length for no change in
    any number."""
    [decision] = discard.decisions(discard.read_events(write(tmp_path / "t.jsonl", events())))
    calls = 0

    def counting(
        snapshot: dict[str, Any], ours: list[dict[str, Any]], theirs: list[dict[str, Any]]
    ) -> np.ndarray:
        nonlocal calls
        calls += 1
        return matrix_fn(snapshot, ours, theirs)

    measured = discard.measure_many(decision, counting, first_k, ks=(1, 2))

    assert [m.k for m in measured] == [1, 2]
    assert calls == 1


def test_two_policies_are_measured_against_one_solve_of_the_same_position(
    tmp_path: Path,
) -> None:
    """Section 3 says the guard is reported per implementation and is part of
    the benchmark. The matrix is the whole cost of a run and does not depend on
    the policy, so measuring the second one has to be an argument rather than a
    second sweep -- otherwise M7's three providers cost three sweeps to compare
    on positions that are only nominally the same."""
    [decision] = discard.decisions(discard.read_events(write(tmp_path / "t.jsonl", events())))
    calls = 0

    def counting(
        snapshot: dict[str, Any], ours: list[dict[str, Any]], theirs: list[dict[str, Any]]
    ) -> np.ndarray:
        nonlocal calls
        calls += 1
        return matrix_fn(snapshot, ours, theirs)

    def best(
        snapshot: dict[str, Any], actions: list[dict[str, Any]], k: int
    ) -> list[dict[str, Any]]:
        return [a for a in actions if a["message"] == "c"][:k]

    measured = discard.measure_many(decision, counting, {"first": first_k, "best": best}, ks=(2,))

    assert calls == 1
    assert {m.policy: round(m.discarded_mass, 3) for m in measured} == {"first": 1.0, "best": 0.0}


def test_the_trace_check_applies_only_to_the_policy_the_trace_was_written_by(
    tmp_path: Path,
) -> None:
    """`matches_trace` is the guard's own validity check: the kept set is
    re-derived rather than read off the trace, and agreeing with the recorded
    one is what says the measurement describes the selection the agent actually
    ran. It says nothing at all about a *different* policy, and reporting a
    mismatch there would read as a bug in a run that is working correctly."""
    rows = events()
    for candidate in rows[2]["payload"]["joint"]:
        candidate["policy_provider"] = "the-one-that-ran"
    [decision] = discard.decisions(discard.read_events(write(tmp_path / "t.jsonl", rows)))

    measured = discard.measure_many(
        decision, matrix_fn, {"the-one-that-ran": first_k, "another": first_k}, ks=(2,)
    )

    by_policy = {m.policy: m.matches_trace for m in measured}
    assert by_policy == {"the-one-that-ran": True, "another": None}


# -- summarising over battles ------------------------------------------------


def measurement(battle_id: str, mass: float, turn: int = 1) -> discard.Measurement:
    return discard.Measurement(
        battle_id=battle_id,
        viewpoint="p1",
        turn=turn,
        k=10,
        n_legal=3,
        n_columns=1,
        discarded_mass=mass,
        value_loss=0.0,
        matches_trace=True,
        policy="first",
    )


def test_the_summary_says_how_many_positions_the_trace_check_covered() -> None:
    """0 mismatches means two different things: agreement, and a check that was
    never run because the policy is not the one the traces were written by. The
    report says "disagreed on 0 positions" for both, so the count of positions
    the check actually covered travels beside it."""
    checked = measurement("b1", 0.0)
    unchecked = discard.Measurement(**{**vars(checked), "matches_trace": None, "turn": 2})

    summary = discard.summarise([checked, unchecked])

    assert summary.trace_mismatches == 0
    assert summary.trace_checked == 1


def test_the_summary_counts_battles_not_positions() -> None:
    """Twenty positions from one game share a board and a policy. Reporting n as
    the position count would claim twenty independent samples."""
    rows = [measurement("b1", 1.0, turn=t) for t in range(20)] + [measurement("b2", 0.0)]

    summary = discard.summarise(rows)

    assert summary.n_positions == 21
    assert summary.n_battles == 2


def test_the_interval_resamples_battles() -> None:
    """Resampling positions inside a battle would report an interval far too
    narrow: here every position in a battle agrees, so all the variance there is
    lives between the two battles and none of it within them."""
    rows = [measurement("b1", 1.0, turn=t) for t in range(20)]
    rows += [measurement("b2", 0.0, turn=t) for t in range(20)]

    summary = discard.summarise(rows, resamples=200, seed=0)

    assert summary.mean == pytest.approx(0.5)
    # Two battles, one at 0 and one at 1: a battle-level bootstrap draws both
    # the all-zero and the all-one resample, so the interval spans them.
    assert summary.low == pytest.approx(0.0)
    assert summary.high == pytest.approx(1.0)


def test_the_summary_reports_how_often_anything_was_discarded_at_all() -> None:
    """A mean of 0.05 is two different findings: every position leaking a
    little, or one position in twenty losing its answer entirely. The second is
    the one that matters and the mean alone cannot tell them apart."""
    rows = [measurement("b1", 0.0), measurement("b2", 1.0)]
    rows += [measurement(f"b{i}", 0.0) for i in range(3, 21)]

    summary = discard.summarise(rows)

    assert summary.mean == pytest.approx(0.05)
    assert summary.nonzero_fraction == pytest.approx(0.05)


def test_an_empty_measurement_set_summarises_to_nothing_rather_than_raising() -> None:
    """A trace directory with no completed searches in it is a normal thing to
    point this at, and a crash would look like a defect in the measurement."""
    summary = discard.summarise([])

    assert summary.n_positions == 0
    assert summary.mean == 0.0

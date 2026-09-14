"""M9, the coach: two losses per turn, from a trace or a replay.

`docs/specs/2026-09-13-coach.md` section 8. The rule of section 4 is walked
branch by branch on synthetic matrices, so that the label a player reads is
the label the spec defines and not whatever the code drifted to. The two
sources are checked against a hand-built trace, a real gate trace, and the
inline replay logs `tests/test_policy_data.py` already carries. The overlay
is checked against the trace validator, which is the contract the viewer
reads.
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from champions.belief.evaluate import truth_from_team_file
from champions.coach import classify, decisions, report
from champions.coach.analyze import (
    OPEN_SHEET,
    REVEALED,
    TEAM_FILE,
    Models,
    TurnAnalysis,
    analyze_decision,
    analyze_game,
    column_key,
    models_for,
    summarise,
)
from champions.coach.explain import explain, facts, template
from champions.coach.truth import TruthOracle
from champions.dex.loader import Dex
from champions.search.matrix import solve_both
from champions.teams import ALPHA, TEAMS_DIR
from champions.trace.schema import TraceEvent
from champions.trace.validate import validate_events
from tests.test_policy_data import LOG

FORMAT_ID = "gen9championsvgc2026regmc"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


# -- the rule, on synthetic numbers (spec section 4) ---------------------------


def _solve(matrix: list[list[float]]) -> tuple[np.ndarray, Any]:
    payoff = np.array(matrix, dtype=float)
    return payoff, solve_both(payoff)


def test_every_support_row_has_zero_ex_ante_loss() -> None:
    """The equilibrium's indifference property, which is what makes a non-zero
    loss mean "off the support" and nothing else."""
    payoff, eq = _solve([[0.6, 0.4], [0.3, 0.7], [0.1, 0.1]])
    ante = payoff @ eq.column
    for i in eq.support:
        assert eq.value - ante[i] == pytest.approx(0.0, abs=1e-6)
    assert eq.value - ante[2] > 0.2


def test_the_largest_weight_is_best_and_the_rest_of_the_support_is_solid() -> None:
    payoff, eq = _solve([[0.6, 0.4], [0.3, 0.7], [0.1, 0.1]])
    ante = payoff @ eq.column
    weights = eq.row
    labels = [
        classify.classify(float(weights[i]), float(eq.value - ante[i]), float(weights.max()))
        for i in range(3)
    ]
    assert sorted(labels[:2]) == [classify.BEST, classify.SOLID]
    assert labels[2] == classify.BLUNDER


@pytest.mark.parametrize(
    ("loss", "label"),
    [
        (0.01, classify.INACCURACY_LABEL),
        (0.049, classify.INACCURACY_LABEL),
        (0.05, classify.MISTAKE_LABEL),
        (0.149, classify.MISTAKE_LABEL),
        (0.15, classify.BLUNDER),
        (0.5, classify.BLUNDER),
    ],
)
def test_off_support_loss_bands(loss: float, label: str) -> None:
    assert classify.classify(0.0, loss, 1.0) == label


def test_a_zero_loss_row_the_lp_did_not_weight_is_still_on_the_support() -> None:
    """Many equilibria, one returned: a row exactly as good as the weighted one
    is Solid, not an Inaccuracy with a loss of nothing."""
    assert classify.on_support(0.0, 0.0)
    assert classify.classify(0.0, 0.0, 1.0) == classify.SOLID


def test_forced_needs_a_pure_equilibrium_and_dominated_alternatives() -> None:
    assert classify.is_forced(True, [0.7, 0.5, 0.6], 0.7)
    assert not classify.is_forced(True, [0.7, 0.68, 0.6], 0.7), "an alternative within the gap"
    assert not classify.is_forced(False, [0.7, 0.5], 0.7), "a mixed equilibrium is not forced"
    assert not classify.is_forced(True, [0.7], 0.7), "one row is not a decision"


def test_read_is_the_minimising_column_against_a_support_row() -> None:
    row = [0.6, 0.4, 0.5]
    assert classify.is_read(True, row, 1, ante_value=0.5)
    assert not classify.is_read(True, row, 0, ante_value=0.5), "not the counter"
    assert not classify.is_read(False, row, 1, ante_value=0.5), "off the support is a mistake"
    assert not classify.is_read(True, [0.5, 0.48, 0.5], 1, ante_value=0.5), "costs too little"


def test_gamble_is_low_weight_that_paid_off() -> None:
    assert classify.is_gamble(0.05, post_cell=0.6, game_value=0.5)
    assert not classify.is_gamble(0.5, post_cell=0.6, game_value=0.5), "carried real weight"
    assert not classify.is_gamble(0.05, post_cell=0.51, game_value=0.5), "did not pay off"


def test_unlucky_and_lucky_are_the_two_ends_of_luck() -> None:
    assert classify.is_unlucky(classify.BEST, 0.12)
    assert classify.is_unlucky(classify.INACCURACY_LABEL, 0.12)
    assert not classify.is_unlucky(classify.BLUNDER, 0.3), "a blunder is not unlucky"
    assert not classify.is_unlucky(classify.BEST, 0.05)
    assert classify.is_lucky(-0.12)
    assert not classify.is_lucky(0.12)


def test_tags_come_in_the_tables_order_and_are_independent() -> None:
    out = classify.tags(
        label=classify.SOLID,
        weight=0.05,
        loss=0.0,
        is_pure=False,
        ante_values=[0.5, 0.5],
        game_value=0.5,
        row_values=[0.6, 0.4],
        their_column=1,
        ante_value=0.5,
        post_cell=0.55,
        luck=0.2,
    )
    assert out == [classify.READ, classify.GAMBLE, classify.UNLUCKY]


# -- the analysis on a hand-built decision -----------------------------------


def _mon(
    dex: Dex,
    species: str,
    hp_pct: float = 100.0,
    known: bool = True,
    moves: list[str] | None = None,
    revealed: list[str] | None = None,
    fainted: bool = False,
) -> dict[str, Any]:
    entry = dex.species[species]
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
        "first_turn": False,
        "protect_counter": 0,
        "item": None,
        "ability": None,
    }
    if known:
        view["stats"] = {k: v + 32 + 20 for k, v in entry["baseStats"].items() if k != "hp"}
        view["max_hp"] = entry["baseStats"]["hp"] + 32 + 75
        view["hp"] = round(view["max_hp"] * view["hp_pct"] / 100)
        view["selected"] = True
        view["moves"] = [{"id": m, "pp": 20} for m in (moves or [])]
    else:
        view["revealed_moves"] = [{"id": m} for m in (revealed or [])]
    return view


def _side(active: list[Any], bench: list[Any]) -> dict[str, Any]:
    seen = [p for p in active if p] + bench
    return {
        "active": active,
        "bench": bench,
        "remaining": sum(1 for p in seen if not p["fainted"]),
        "revealed": len(seen),
    }


def _state(ours: dict[str, Any], theirs: dict[str, Any], turn: int = 2) -> dict[str, Any]:
    return {
        "turn": turn,
        "weather": {},
        "fields": {},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": ours,
        "theirs": theirs,
    }


def _move(dex: Dex, move_id: str, target: int) -> dict[str, Any]:
    entry = dex.moves[move_id]
    return {
        "kind": "move",
        "move": move_id,
        "name": entry["name"],
        "type": entry["type"],
        "category": entry["category"],
        "base_power": entry["basePower"],
        "priority": entry.get("priority", 0),
        "move_target": entry.get("target"),
        "target": target,
        "label": f"{entry['name']} -> {target}",
    }


def _joint(*slots: dict[str, Any]) -> dict[str, Any]:
    label = " + ".join(str(s["label"]) for s in slots)
    return {"message": label, "label": label, "slots": list(slots), "kinds": ["move"]}


@pytest.fixture(scope="module")
def position(dex: Dex) -> dict[str, Any]:
    """Two attackers each side, the opponent having revealed one move each."""
    ours = _side(
        [
            _mon(dex, "garchomp", moves=["earthquake", "protect"]),
            _mon(dex, "kingambit", moves=["ironhead", "protect"]),
        ],
        [_mon(dex, "dragonite", moves=["extremespeed"])],
    )
    theirs = _side(
        [
            _mon(dex, "corviknight", known=False, revealed=["bravebird"]),
            _mon(dex, "milotic", known=False, revealed=["scald"]),
        ],
        [],
    )
    return _state(ours, theirs)


def _rows(dex: Dex) -> list[dict[str, Any]]:
    eq, protect_a = _move(dex, "earthquake", 0), _move(dex, "protect", 0)
    iron, protect_b = _move(dex, "ironhead", 1), _move(dex, "protect", 0)
    return [
        _joint(eq, iron),
        _joint(eq, protect_b),
        _joint(protect_a, iron),
        _joint(protect_a, protect_b),
    ]


def test_a_decision_is_analysed_with_the_played_column_appended(
    dex: Dex, position: dict[str, Any]
) -> None:
    """The opponent switched, which the revealed-moves column builder never
    proposes, so the played column has to be added for the ex-post half."""
    rows = _rows(dex)
    their = decisions.joint_column(
        [
            {"kind": "switch", "species": "incineroar", "label": "switch to incineroar"},
            _move(dex, "scald", 1),
        ]
    )
    decision = decisions.Decision(
        turn=2,
        snapshot=position,
        rows=rows,
        played=0,
        their_played=their,
        next_snapshot=position,
        for_seq=7,
    )
    models = models_for(dex, None)
    result = analyze_decision(decision, models)

    assert result.information == {"ante": REVEALED, "post": REVEALED}
    assert result.n_rows == 4
    assert result.n_columns >= 2
    assert result.opponent_played_label == their["label"]
    assert result.ex_ante_loss is not None and result.ex_ante_loss >= 0.0
    assert result.ex_post_loss is not None and result.ex_post_loss >= 0.0
    assert 0.0 <= result.game_value <= 1.0
    assert result.label in classify.LABELS
    assert result.rolls, "the roll branches of the played cell are reported"
    assert sum(r["probability"] for r in result.rolls) == pytest.approx(1.0, abs=1e-6)
    assert result.expected is not None
    assert result.luck == pytest.approx(result.expected - result.realized)
    payload = result.payload()
    assert payload["scope"] == "turn" and payload["for_seq"] == 7
    assert payload["model"] == "analytic-one-turn"


def test_an_unrecorded_choice_is_reported_without_a_loss(
    dex: Dex, position: dict[str, Any]
) -> None:
    decision = decisions.Decision(
        turn=2,
        snapshot=position,
        rows=_rows(dex),
        played=None,
        their_played=None,
        next_snapshot=position,
        for_seq=7,
    )
    result = analyze_decision(decision, models_for(dex, None))
    assert result.label is None
    assert result.ex_ante_loss is None and result.ex_post_loss is None
    assert result.equilibrium, "the equilibrium is still reported"
    assert "unrecorded" in template(result).lower() or "does not record" in template(result)


def test_column_identity_is_kind_move_and_target(dex: Dex) -> None:
    a = decisions.joint_column([_move(dex, "scald", 1), _move(dex, "bravebird", 2)])
    b = decisions.joint_column([_move(dex, "scald", 1), _move(dex, "bravebird", 2)])
    c = decisions.joint_column([_move(dex, "scald", 2), _move(dex, "bravebird", 2)])
    assert column_key(a) == column_key(b) != column_key(c)


# -- the truth oracle --------------------------------------------------------


def test_the_truth_oracle_answers_from_a_team_file_and_from_a_sheet(dex: Dex) -> None:
    text = (TEAMS_DIR / f"{ALPHA}.txt").read_text(encoding="utf-8")
    from_file = TruthOracle(truth_from_team_file(text), dex)
    assert len(from_file) == 6
    species = next(iter(truth_from_team_file(text)))
    stats = from_file.stats_for(species)
    assert stats is not None and set(stats) == {"hp", "atk", "def", "spa", "spd", "spe"}
    assert from_file.set_for(species) is not None
    assert len(from_file.believed_moves(species)) == 4

    sheet = {
        k: v.__class__(**{**v.__dict__, "points": None})
        for k, v in truth_from_team_file(text).items()
    }
    from_sheet = TruthOracle(sheet, dex)
    assert from_sheet.stats_for(species) is None, "no points on an open sheet (D33)"
    assert from_sheet.believed_moves(species) == from_file.believed_moves(species)
    assert from_sheet.set_for("nosuchmon") is None


# -- the trace source --------------------------------------------------------


def _event(seq: int, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "battle_id": "battle-test-1",
        "seq": seq,
        "t": 1000.0 + seq,
        "type": kind,
        "payload": payload,
    }


@pytest.fixture
def trace(dex: Dex, position: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _rows(dex)
    after = _state(position["ours"], position["theirs"], turn=3)
    return [
        _event(
            0,
            "battle_start",
            {"format_id": FORMAT_ID, "player_role": "p2", "player_username": "us"},
        ),
        _event(1, "preview_decision", {"selected": ["garchomp", "kingambit", "dragonite"]}),
        _event(2, "turn_start", {"turn": 2, "state": position, "evaluation": {"win_prob": 0.5}}),
        _event(3, "candidates", {"turn": 2, "joint": rows, "pruned": False, "truncated": False}),
        _event(
            4,
            "candidates",
            {"turn": 2, "pruned": True, "k": 10, "game_value": 0.55, "joint": rows[:2]},
        ),
        _event(
            5,
            "equilibrium",
            {"turn": 2, "chosen": rows[1]["message"], "chosen_action": rows[1], "value": 0.55},
        ),
        _event(
            6,
            "turn_result",
            {
                "turn": 3,
                "observations": [
                    {
                        "turn": 2,
                        "side": "p1",
                        "slot": "p1a",
                        "attribute": "move",
                        "value": "Brave Bird",
                        "detail": {"target": "p2b: Kingambit"},
                    },
                    {
                        "turn": 2,
                        "side": "p1",
                        "slot": "p1b",
                        "attribute": "switch",
                        "value": "Incineroar",
                        "detail": {"how": "voluntary"},
                    },
                    {
                        "turn": 2,
                        "side": "p2",
                        "slot": "p2a",
                        "attribute": "move",
                        "value": "Earthquake",
                        "detail": {},
                    },
                ],
            },
        ),
        _event(7, "turn_start", {"turn": 3, "state": after, "evaluation": {"win_prob": 0.5}}),
        _event(8, "candidates", {"turn": 3, "joint": rows, "pruned": False}),
        _event(
            9,
            "equilibrium",
            {
                "turn": 3,
                "chosen": "/choose move nosuch",
                "chosen_action": _joint(_move(dex, "protect", 0)),
                "value": None,
                "watchdog_fired": True,
            },
        ),
        _event(
            10,
            "battle_end",
            {"result": "loss", "turns": 3, "state": after, "final_observations": []},
        ),
    ]


def test_the_trace_source_reads_what_the_agent_wrote(trace: list[dict[str, Any]], dex: Dex) -> None:
    game = decisions.from_trace(trace)
    assert game.side == "p2" and game.result == "loss" and game.turns == 3
    assert [d.turn for d in game.decisions] == [2, 3]

    first = game.decisions[0]
    assert len(first.rows) == 4 and first.played == 1
    assert first.for_seq == 5
    assert first.recorded["game_value"] == 0.55 and first.recorded["k"] == 10
    assert first.their_played is not None
    slots = first.their_played["slots"]
    assert slots[0]["kind"] == "move" and slots[0]["move"] == "bravebird"
    assert slots[0]["target"] == 2, "their p2b is our slot 2 from their side"
    assert slots[1]["kind"] == "switch" and slots[1]["species"] == "incineroar"
    assert first.next_snapshot["turn"] == 3

    second = game.decisions[1]
    assert second.played == len(second.rows) - 1, (
        "a chosen action outside the legal list is appended"
    )
    assert second.recorded["watchdog_fired"] is True
    assert second.their_played is None, "the game ended before they acted"


def test_a_team_file_is_the_ex_post_information_state_of_a_trace(
    trace: list[dict[str, Any]], dex: Dex
) -> None:
    text = (TEAMS_DIR / f"{ALPHA}.txt").read_text(encoding="utf-8")
    game = decisions.from_trace(trace, post_truths=truth_from_team_file(text))
    analysis = analyze_game(game, dex)
    assert analysis.information == {"ante": REVEALED, "post": TEAM_FILE}
    assert analysis.turns[0].information["post"] == TEAM_FILE


def test_the_overlay_validates_and_names_what_it_annotates(
    trace: list[dict[str, Any]], dex: Dex
) -> None:
    game = decisions.from_trace(trace)
    analysis = analyze_game(game, dex)
    explain(analysis, {2})
    events = report.overlay(game, analysis)

    assert validate_events([TraceEvent.model_validate(e) for e in events]) == []
    kinds = [e["type"] for e in events]
    originals = [e for e in events if e["type"] != "analysis"]
    assert [e["type"] for e in originals] == [e["type"] for e in trace], "every original, in order"
    assert kinds.count("analysis") == 2 + 1 + 1, "two turns, the preview, the game"

    for event in events:
        if event["type"] != "analysis":
            continue
        target = events[event["payload"]["for_seq"]]
        scope = event["payload"]["scope"]
        expected = {"turn": "equilibrium", "preview": "preview_decision", "game": "battle_end"}
        assert target["type"] == expected[scope]
        if scope == "turn":
            assert target["payload"]["turn"] == event["payload"]["turn"]
            assert event["payload"]["explanation"]

    summary = next(e["payload"] for e in events if e["payload"].get("scope") == "game")
    assert summary["decisions"] == 2 and "curve" in summary and "classifications" in summary


def test_the_report_renders_and_states_calibration(trace: list[dict[str, Any]], dex: Dex) -> None:
    game = decisions.from_trace(trace)
    analysis = analyze_game(game, dex)
    explain(analysis, set())
    text = report.markdown(game, analysis)
    assert text.startswith("# Review: battle-test-1")
    assert "## Critical turns" in text and "## Turns" in text and "## Writeups" in text
    assert ("not calibrated" in text) is (not analysis.calibrated)
    assert "No bring-4 verdict" in text


@pytest.mark.skipif(not glob.glob("runs/m8-gate/*/*/*.jsonl"), reason="no gate traces here")
def test_on_a_real_trace_the_curve_is_the_evaluation_the_agent_recorded(dex: Dex) -> None:
    """`docs/08`: a coach that reads a different evaluation from the one that
    played is the named failure. The recorded number is the check."""
    from champions.search import evaluate
    from champions.search.positions import read_events

    path = Path(sorted(glob.glob("runs/m8-gate/*/*/*.jsonl"))[0])
    events = read_events(path)
    recorded_features = next(
        (
            set((e["payload"].get("evaluation") or {}).get("features") or {})
            for e in events
            if e.get("type") == "turn_start"
        ),
        set(),
    )
    if recorded_features and recorded_features != set(evaluate.BOOTSTRAP_WEIGHTS) | {"faint_swing"}:
        # The trace was played by an agent whose evaluation had a different
        # feature vector (D85 added `speed_advantage`), so the recorded number
        # is that agent's, not this code's. The check still holds for any
        # trace played by the current agent.
        pytest.skip(f"trace predates the current feature vector: {sorted(recorded_features)}")
    game = decisions.from_trace(events)
    analysis = analyze_game(game, dex)
    for point in analysis.curve:
        if point["recorded"] is not None:
            assert point["win_prob"] == pytest.approx(point["recorded"], abs=1e-9)
    assert analysis.turns, "a gate trace has decisions"
    assert all(t.played is not None for t in analysis.turns)


# -- the replay source -------------------------------------------------------


def test_the_replay_source_rebuilds_both_players_decisions(dex: Dex) -> None:
    game = decisions.from_replay(LOG, "gen9championsvgc2026regmb-1", "p1", dex)
    assert game.side == "p1" and game.player == "alice" and game.result == "win"
    assert game.source == "replay"
    assert game.ante_truths is not None and set(game.ante_truths) == {
        "starmie",
        "dragonite",
        "incineroar",
        "torkoal",
    }, "the open sheet is what the human could know"

    first = game.decisions[0]
    assert first.turn == 1
    assert len(first.rows) == 12 * 10 - 2, "12 x 10 per-slot options minus the two double switches"
    assert first.played is not None
    assert first.rows[first.played]["label"] == "Iron Head -> foe slot 1 + Tailwind"
    assert first.their_played is not None
    assert first.their_played["label"] == "Ice Beam + switch to incineroar"
    assert first.next_snapshot["turn"] == 2

    turns = [d.turn for d in game.decisions]
    assert turns == [1, 2, 3], "turn 4 has nothing in it: the game ended"
    assert game.decisions[2].their_played is None, "p2 made no recorded choice on turn 3"

    mirror = decisions.from_replay(LOG, "gen9championsvgc2026regmb-1", "bob", dex)
    assert mirror.side == "p2" and mirror.result == "loss"
    assert mirror.decisions[0].their_played is not None
    assert mirror.decisions[0].their_played["label"] == "Iron Head + Tailwind"


def test_a_closed_sheet_replay_is_reviewed_on_revealed_moves(dex: Dex) -> None:
    closed = "\n".join(line for line in LOG.split("\n") if not line.startswith("|showteam|"))
    game = decisions.from_replay(closed, "gen9championsvgc2026regmb-2", "p1", dex)
    assert game.ante_truths is None and game.post_truths is None
    analysis = analyze_game(game, dex)
    assert analysis.information == {"ante": REVEALED, "post": REVEALED}


def test_a_replay_review_validates_as_a_trace_and_is_reproducible(dex: Dex) -> None:
    game = decisions.from_replay(LOG, "gen9championsvgc2026regmb-1", "p1", dex)
    assert validate_events([TraceEvent.model_validate(e) for e in game.events]) == []

    analysis = analyze_game(game, dex)
    assert analysis.information == {"ante": OPEN_SHEET, "post": OPEN_SHEET}
    again = analyze_game(game, dex)
    for a, b in zip(analysis.turns, again.turns, strict=True):
        assert a.ex_ante_loss == b.ex_ante_loss and a.label == b.label and a.tags == b.tags

    explain(analysis, {1})
    events = report.overlay(game, analysis)
    assert validate_events([TraceEvent.model_validate(e) for e in events]) == []
    assert report.markdown(game, analysis)


def test_a_player_name_that_is_not_in_the_replay_is_refused(dex: Dex) -> None:
    with pytest.raises(ValueError):
        decisions.from_replay(LOG, "gen9championsvgc2026regmb-1", "carol", dex)


# -- the summary and the writeup ---------------------------------------------


def _turn(turn: int, loss: float | None, label: str | None, tags: list[str]) -> TurnAnalysis:
    return TurnAnalysis(
        turn=turn,
        for_seq=turn,
        played=0 if loss is not None else None,
        played_label="x" if loss is not None else None,
        label=label,
        tags=tags,
        game_value=0.5,
        is_pure=False,
        on_support=loss == 0.0,
        ex_ante_loss=loss,
        ex_post_loss=None if loss is None else loss / 2,
        ante_value=None,
        expected=0.5 if loss is not None else None,
        realized=0.4,
        luck=0.1 if loss is not None else None,
        win_prob_before=0.5,
        win_prob_after=0.4,
        calibrated=True,
        equilibrium=[{"label": "x", "probability": 1.0, "ante_value": 0.5}],
        opponent_equilibrium=[],
        best_label="x",
        best_post_label="y",
        opponent_played_label="z",
        rolls=[],
        n_rows=2,
        n_columns=1,
        information={"ante": REVEALED, "post": REVEALED},
    )


def test_the_summary_counts_and_ranks_the_critical_turns() -> None:
    turns = [
        _turn(1, 0.0, classify.BEST, []),
        _turn(2, 0.2, classify.BLUNDER, [classify.READ]),
        _turn(3, 0.08, classify.MISTAKE_LABEL, []),
        _turn(4, None, None, [classify.FORCED]),
    ]
    curve: list[dict[str, Any]] = [
        {"turn": 1, "win_prob": 0.5},
        {"turn": 2, "win_prob": 0.6},
        {"turn": 3, "win_prob": 0.3},
        {"turn": 4, "win_prob": 0.25},
        {"turn": None, "win_prob": 0.0},
    ]
    summary = summarise(turns, curve)
    assert summary["decisions"] == 4 and summary["scored"] == 3
    assert summary["ex_ante_loss_total"] == pytest.approx(0.28)
    assert summary["classifications"][classify.BLUNDER] == 1
    assert summary["tags"][classify.FORCED] == 1 and summary["tags"][classify.READ] == 1
    assert [c["turn"] for c in summary["critical_by_loss"]] == [2, 3]
    assert [c["turn"] for c in summary["critical_by_drop"]] == [2, 4, 3]


def test_the_template_says_what_the_numbers_say() -> None:
    turn = _turn(5, 0.2, classify.BLUNDER, [classify.READ])
    text = template(turn)
    assert text.startswith("Turn 5: played x.")
    assert "blunder" in text and "20.0 points" in text
    assert "The opponent played z." in text and "best reply was y" in text
    assert "read it" in text
    sentences = facts(turn)
    assert all(s.endswith(".") for s in sentences)


def test_a_failing_language_model_falls_back_to_the_template() -> None:
    from champions.search.llm import LLMError

    class Broken:
        name = "broken"

        def complete(self, prompt: str) -> str:
            raise LLMError("no model")

    class Echo:
        name = "echo"

        def complete(self, prompt: str) -> str:
            return "prose"

    turn = _turn(5, 0.2, classify.BLUNDER, [])
    from champions.coach.explain import polish

    assert polish(turn, Broken()) == template(turn)
    assert polish(turn, Echo()) == "prose"


def test_models_for_names_its_information_state(dex: Dex) -> None:
    assert isinstance(models_for(dex, None), Models)
    assert models_for(dex, None).name == REVEALED
    text = (TEAMS_DIR / f"{ALPHA}.txt").read_text(encoding="utf-8")
    named = models_for(dex, truth_from_team_file(text), TEAM_FILE)
    assert named.name == TEAM_FILE and named.believed_moves is not None


def test_two_decisions_in_one_turn_are_both_reviewed(
    trace: list[dict[str, Any]], dex: Dex, position: dict[str, Any]
) -> None:
    """A faint forces a mid-turn switch request, which the tracer records as a
    second `turn_start` with the same turn number. Both are decisions; keying
    by turn number would drop the first."""
    rows = _rows(dex)
    forced = [
        _event(11, "turn_start", {"turn": 3, "state": position, "evaluation": {"win_prob": 0.5}}),
        _event(12, "candidates", {"turn": 3, "joint": rows[:2], "pruned": False}),
        _event(
            13, "equilibrium", {"turn": 3, "chosen": rows[0]["message"], "chosen_action": rows[0]}
        ),
    ]
    events = trace[:-1] + forced + [_event(14, "battle_end", trace[-1]["payload"])]
    game = decisions.from_trace(events)
    assert [d.turn for d in game.decisions] == [2, 3, 3]
    assert [d.for_seq for d in game.decisions] == [5, 9, 13]
    assert game.decisions[1].next_snapshot is position, "the forced switch's state follows"
    analysis = analyze_game(game, dex)
    assert len(analysis.turns) == 3
    overlay = report.overlay(game, analysis)
    assert validate_events([TraceEvent.model_validate(e) for e in overlay]) == []
    assert sum(1 for e in overlay if e["payload"].get("scope") == "turn") == 3


def test_the_corpus_prior_is_an_information_state_with_columns_on_turn_one(dex: Dex) -> None:
    """D85: a review against the prior sees the opponent's likely moves before
    any is revealed, which the revealed-only state cannot."""
    from collections import Counter

    from champions.belief.priors import SetHypothesis, SetPrior, SpeciesPrior
    from champions.coach.analyze import CORPUS_PRIOR, prior_models

    sets = [
        (
            SetHypothesis(
                "farigiraf",
                "sitrusberry",
                "armortail",
                frozenset({"trickroom", "psychic", "protect", "helpinghand"}),
                "quiet",
            ),
            40,
        ),
        (
            SetHypothesis(
                "farigiraf",
                "leftovers",
                "armortail",
                frozenset({"trickroom", "foulplay", "protect", "hypervoice"}),
                "quiet",
            ),
            10,
        ),
    ]
    entry = SpeciesPrior(
        species="farigiraf",
        sets=sets,
        items=Counter({"sitrusberry": 40, "leftovers": 10}),
        abilities=Counter({"armortail": 50}),
        natures=Counter({"quiet": 50}),
        moves=Counter(
            {
                "trickroom": 50,
                "protect": 50,
                "psychic": 40,
                "helpinghand": 40,
                "foulplay": 5,
                "hypervoice": 5,
            }
        ),
        count=50,
    )
    prior = SetPrior({"farigiraf": entry})
    models = prior_models(dex, prior)
    assert models.name == CORPUS_PRIOR
    assert models.believed_moves("farigiraf")[:2] == ["protect", "trickroom"]
    assert "foulplay" not in models.believed_moves("farigiraf"), (
        "one in ten is under the threshold"
    )
    assert models.believed_moves("nothing") == []
    assert (
        models.model.hypothesis.stats_for(
            {"species": "farigiraf", "base_stats": dex.species["farigiraf"]["baseStats"]}
        )["spe"]
        > 0
    )

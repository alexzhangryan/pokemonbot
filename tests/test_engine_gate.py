"""M8: the gate's rule, as code, on synthetic rows.

`docs/specs/2026-09-13-engine-gate.md` section 4 fixed the rule before any
number existed (D70). `scripts/engine_gate.py` applies it mechanically, and
these tests walk every branch so the rule cannot drift between the document
and the run. The rows are invented; the arithmetic is not.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.engine_gate import (
    BLIND_DEPTH,
    CONTROL,
    DEPTH,
    FIDELITY,
    Row,
    fallback_summary,
    gap,
    render,
    verdict,
)


def _row(arm: str, wins: int, games: int = 200, team: str = "regmb-alpha") -> Row:
    return Row(
        team=team,
        arm=arm,
        games=games,
        wins=wins,
        baseline_wins=games - wins,
        p50_ms=50.0,
        p95_ms=120.0,
        max_ms=300.0,
        frac_over_limit=0.0,
        worst_battle_s=12.0,
        clock_ok=True,
        watchdog_fires=0,
        decisions=1000,
        fallback_decisions=0,
        fallback_cell_fraction=0.0 if arm == FIDELITY else None,
        elapsed_s=60.0,
    )


def test_a_gain_is_an_interval_above_a_half() -> None:
    assert _row(CONTROL, 130).demonstrates_a_gain()
    assert not _row(CONTROL, 105).demonstrates_a_gain()
    assert not _row(CONTROL, 0, games=0).demonstrates_a_gain()


def test_a_gap_is_apart_when_its_interval_excludes_zero() -> None:
    wide = gap(_row(DEPTH, 140), _row(CONTROL, 100))
    assert wide.positive and wide.apart_from_zero
    narrow = gap(_row(DEPTH, 104), _row(CONTROL, 100))
    assert not narrow.apart_from_zero
    negative = gap(_row(DEPTH, 60), _row(CONTROL, 100))
    assert negative.apart_from_zero and not negative.positive


def test_depth_wins_when_it_clears_and_fidelity_does_not() -> None:
    rows = {CONTROL: _row(CONTROL, 100), DEPTH: _row(DEPTH, 150), FIDELITY: _row(FIDELITY, 104)}
    assert verdict(rows)["outcome"] == "depth"


def test_depth_wins_when_both_clear_but_depth_is_apart_above_fidelity() -> None:
    rows = {CONTROL: _row(CONTROL, 100), DEPTH: _row(DEPTH, 180), FIDELITY: _row(FIDELITY, 130)}
    assert verdict(rows)["outcome"] == "depth"


def test_fidelity_wins_when_depth_does_not_clear() -> None:
    rows = {CONTROL: _row(CONTROL, 100), DEPTH: _row(DEPTH, 106), FIDELITY: _row(FIDELITY, 150)}
    result = verdict(rows)
    assert result["outcome"] == "fidelity"
    assert result["fidelity_gap"]["low"] > 0


def test_both_clear_ships_fidelity_first() -> None:
    rows = {CONTROL: _row(CONTROL, 100), DEPTH: _row(DEPTH, 140), FIDELITY: _row(FIDELITY, 150)}
    assert verdict(rows)["outcome"] == "both"


def test_neither_clears() -> None:
    rows = {CONTROL: _row(CONTROL, 100), DEPTH: _row(DEPTH, 103), FIDELITY: _row(FIDELITY, 98)}
    assert verdict(rows)["outcome"] == "neither"


def test_a_depth_gain_that_is_not_above_the_control_is_not_depth() -> None:
    """Both depth and the control beat `oneply`, but depth does not beat the
    control: the gain is information, not depth."""
    rows = {CONTROL: _row(CONTROL, 140), DEPTH: _row(DEPTH, 142), FIDELITY: _row(FIDELITY, 141)}
    assert verdict(rows)["outcome"] == "neither"


def test_a_missing_arm_is_incomplete() -> None:
    result = verdict({CONTROL: _row(CONTROL, 100), DEPTH: _row(DEPTH, 150)})
    assert result["outcome"] == "incomplete" and result["missing"] == [FIDELITY]


def test_fallback_summary_reads_only_the_simulator_arm_fields(tmp_path: Path) -> None:
    trace = tmp_path / "x.simoracle0.jsonl"
    materialised = {"phase": "pruned", "materialized": True}
    events = [
        {"type": "candidates", "payload": {**materialised, "cells": 100, "fallback_cells": 0}},
        {"type": "candidates", "payload": {**materialised, "cells": 100, "fallback_cells": 25}},
        # A forced switch: the analytic model by design, not a simulator failure.
        {
            "type": "candidates",
            "payload": {
                "phase": "pruned",
                "materialized": False,
                "skipped": "forced switch",
                "cells": 6,
                "fallback_cells": 6,
            },
        },
        {"type": "candidates", "payload": {"phase": "unpruned"}},
        {"type": "candidates", "payload": {"phase": "pruned"}},
    ]
    trace.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    decisions, with_fallback, fraction = fallback_summary([trace])
    assert decisions == 2 and with_fallback == 1
    assert fraction == 0.125


def test_the_report_renders_every_arm_and_the_verdict() -> None:
    rows = [
        _row(CONTROL, 100),
        _row(BLIND_DEPTH, 110),
        _row(DEPTH, 106),
        _row(FIDELITY, 150),
    ]
    record = {
        "games": 200,
        "seed": 0,
        "rows": {f"regmb-alpha/{r.arm}": r.__dict__ for r in rows},
    }
    text = render(record, ["regmb-alpha", "regmb-beta"])
    assert "## `regmb-alpha`" in text and "## `regmb-beta`" not in text
    for arm in (CONTROL, BLIND_DEPTH, DEPTH, FIDELITY):
        assert f"| `{arm}` |" in text
    assert "Fidelity wins the gate" in text
    assert "0/1000" in text, "the simulator arm's fallback column"

"""T0.6 acceptance: 50 self-play games complete with no exceptions, all produce
valid traces, and zero games end by timeout or invalid choice."""

from __future__ import annotations

import json
from pathlib import Path

from champions.trace.validate import validate_trace_file
from scripts.selfplay import run_selfplay

N_GAMES = 50


async def test_fifty_selfplay_games_complete_with_valid_traces(
    showdown_server: int, tmp_path: Path
) -> None:
    trace_dir = tmp_path / "traces"

    p1, p2, failures = await run_selfplay(
        N_GAMES, showdown_server, trace_dir, seed=0, username_suffix="sp"
    )

    assert p1.n_finished_battles == N_GAMES
    assert p2.n_finished_battles == N_GAMES
    assert p1.n_won_battles + p2.n_won_battles == N_GAMES, "every game has a winner"

    # Zero games end by timeout or invalid choice. Showdown reports both back
    # over the protocol instead of raising, so a run can look clean without this.
    assert failures == []

    # Two agent-views per battle, since both players trace their own side.
    traces = sorted(trace_dir.glob("*.jsonl"))
    assert len(traces) == N_GAMES * 2

    problems = {p.name: validate_trace_file(p) for p in traces}
    assert not {name: probs for name, probs in problems.items() if probs}


async def test_selfplay_traces_record_the_expected_decision_events(
    showdown_server: int, tmp_path: Path
) -> None:
    trace_dir = tmp_path / "traces"
    await run_selfplay(2, showdown_server, trace_dir, seed=7, username_suffix="ev")

    for path in sorted(trace_dir.glob("*.jsonl")):
        events = [json.loads(line) for line in path.open(encoding="utf-8")]
        by_type: dict[str, list[dict]] = {}
        for event in events:
            by_type.setdefault(event["type"], []).append(event)

        assert len(by_type["battle_start"]) == 1
        assert len(by_type["preview_decision"]) == 1
        assert len(by_type["battle_end"]) == 1
        assert by_type["turn_start"], "no turns recorded"

        start = by_type["battle_start"][0]["payload"]
        # Champions reveals six species at preview and nothing else.
        assert len(start["our_team"]) == 6
        assert len(start["opponent_team_preview"]) == 6
        assert start["accept_open_team_sheet"] is False

        # Bring 6 pick 4.
        assert len(by_type["preview_decision"][0]["payload"]["selected"]) == 4

        # Every decision is wrapped by the watchdog and reports clock compliance.
        assert len(by_type["timing"]) == len(by_type["turn_start"])
        for timing in by_type["timing"]:
            assert timing["payload"]["exceeded_45s"] is False
            assert timing["payload"]["watchdog_fired"] is False

        for equilibrium in by_type["equilibrium"]:
            assert equilibrium["payload"]["n_legal_joint_actions"] > 0

        assert by_type["battle_end"][0]["payload"]["result"] in {"win", "loss", "tie"}


async def test_agents_refuse_to_accept_open_team_sheets() -> None:
    """Accepting OTS would produce an agent that does not transfer (DECISIONS.md D2)."""
    import pytest

    from champions.agents.baseline import RandomAgent

    with pytest.raises(ValueError, match="Open Team Sheets"):
        RandomAgent(accept_open_team_sheet=True)

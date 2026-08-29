"""T0.8: running random against a greedy damage maximizer produces a single
table containing win rate, confidence interval, and the three clock metrics."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from champions.harness.elo import percentile, wilson_interval
from champions.harness.ladder import (
    PLAYER_CLOCK_S,
    TURN_LIMIT_S,
    ArmResult,
    clock_metrics_from_traces,
    format_results_table,
    run_matchup,
)
from scripts.run_ladder import build_arms

N_GAMES = 20


# -- statistics ----------------------------------------------------------


def test_wilson_interval_brackets_the_point_estimate() -> None:
    low, high = wilson_interval(40, 50)
    assert low < 0.8 < high


def test_wilson_interval_stays_inside_zero_one_at_the_extremes() -> None:
    # The reason for Wilson over the normal approximation: at 0 and 1 wins the
    # normal interval runs outside [0, 1].
    for wins in (0, 50):
        low, high = wilson_interval(wins, 50)
        assert 0.0 <= low <= high <= 1.0


def test_wilson_interval_narrows_as_games_increase() -> None:
    small = wilson_interval(5, 10)
    large = wilson_interval(500, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_interval_with_no_games_is_maximally_uncertain() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


@pytest.mark.parametrize(
    ("q", "expected"),
    [(0, 1.0), (50, 3.0), (100, 5.0)],
)
def test_percentile_endpoints_and_median(q: float, expected: float) -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], q) == expected


def test_percentile_of_empty_is_zero() -> None:
    assert percentile([], 95) == 0.0


# -- clock metrics -------------------------------------------------------


def _write_trace(path: Path, decision_ms: list[float], watchdog: bool = False) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for seq, ms in enumerate(decision_ms):
            handle.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "battle_id": path.stem,
                        "seq": seq,
                        "t": 1.0 + seq,
                        "type": "timing",
                        "payload": {"total_ms": ms, "watchdog_fired": watchdog},
                    }
                )
                + "\n"
            )


def test_clock_metrics_flag_turns_over_the_limit(tmp_path: Path) -> None:
    path = tmp_path / "slow.jsonl"
    over = TURN_LIMIT_S * 1000 + 1
    _write_trace(path, [10.0, 10.0, over, over])

    metrics = clock_metrics_from_traces([path])

    assert metrics.n_decisions == 4
    assert metrics.frac_turns_over_limit == 0.5
    assert metrics.max_ms == over


def test_clock_metrics_flag_exhausting_the_player_clock(tmp_path: Path) -> None:
    path = tmp_path / "long.jsonl"
    # Individually inside the 45s turn limit, cumulatively past the 7 min clock.
    _write_trace(path, [40_000.0] * 12)

    metrics = clock_metrics_from_traces([path])

    assert metrics.frac_turns_over_limit == 0.0, "no single turn exceeded the limit"
    assert metrics.worst_battle_total_s > PLAYER_CLOCK_S
    assert metrics.would_exhaust_player_clock is True


def test_clock_metrics_pass_a_fast_battle(tmp_path: Path) -> None:
    path = tmp_path / "fast.jsonl"
    _write_trace(path, [0.2] * 30)

    metrics = clock_metrics_from_traces([path])

    assert metrics.frac_turns_over_limit == 0.0
    assert metrics.would_exhaust_player_clock is False
    assert metrics.watchdog_fires == 0


def test_clock_metrics_count_watchdog_fires(tmp_path: Path) -> None:
    path = tmp_path / "watchdog.jsonl"
    _write_trace(path, [1.0, 2.0], watchdog=True)

    assert clock_metrics_from_traces([path]).watchdog_fires == 2


# -- the table -----------------------------------------------------------


def test_table_contains_win_rate_interval_and_all_three_clock_metrics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    _write_trace(path, [1.0, 2.0, 3.0])
    metrics = clock_metrics_from_traces([path])

    table = format_results_table(
        [
            ArmResult(name="random", wins=10, games=20, clock=metrics),
            ArmResult(name="max-base-power", wins=10, games=20, clock=metrics),
        ]
    )

    assert "win rate" in table
    assert "95% CI" in table
    assert "p50 ms" in table and "p95 ms" in table  # latency distribution
    assert ">45s" in table  # fraction of turns over the turn limit
    assert "clock ok" in table  # whether the player clock would be exhausted
    assert re.search(r"random\s+20\s+50\.0%", table)


# -- end to end ----------------------------------------------------------


async def test_random_against_greedy_produces_one_table(
    showdown_server: int, tmp_path: Path
) -> None:
    arm_a, arm_b = build_arms(showdown_server)
    results = await run_matchup(arm_a, arm_b, N_GAMES, tmp_path, seed=99)

    assert len(results) == 2
    assert sum(r.wins for r in results) == N_GAMES
    for result in results:
        assert result.games == N_GAMES
        low, high = result.interval
        assert low <= result.win_rate <= high
        assert result.clock.n_decisions > 0
        assert result.clock.frac_turns_over_limit == 0.0
        assert result.clock.would_exhaust_player_clock is False

    table = format_results_table(results)
    assert "random" in table and "max-base-power" in table


async def test_greedy_never_aims_a_damaging_move_at_its_own_ally(
    showdown_server: int, tmp_path: Path
) -> None:
    """Negative targets are our own slots. Scoring base power alone ties an
    ally-targeted attack with a foe-targeted one, and the agent then spends the
    game hitting its partner; that regression cost it ~72 points of win rate."""
    arm_a, arm_b = build_arms(showdown_server)
    await run_matchup(arm_a, arm_b, 4, tmp_path, seed=123)

    greedy_traces = sorted(tmp_path.glob("*.maxbasepower123.jsonl"))
    assert greedy_traces, "no greedy traces written"

    decisions = 0
    for path in greedy_traces:
        for line in path.open(encoding="utf-8"):
            event = json.loads(line)
            if event["type"] != "equilibrium":
                continue
            decisions += 1
            assert not re.search(r"move \w+ -[12]", event["payload"]["chosen"]), (
                f"targeted own side: {event['payload']['chosen']}"
            )
    assert decisions > 0

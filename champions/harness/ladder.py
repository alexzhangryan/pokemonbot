"""Evaluation harness: seeded, paired matchups with clock compliance reported
beside win rate in the same table.

Clock compliance sits next to win rate from M0 rather than arriving at M11, so
a latency regression is visible in the same table as the win rate that bought it
(DECISIONS.md D7). The three clock metrics come from the trace's timing events,
which every agent emits through the deadline watchdog.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from champions.agents.baseline import TracingPlayer
from champions.harness.elo import percentile, wilson_interval

AgentFactory = Callable[[str, int, str], TracingPlayer]

# Showdown's VGC Timer for this format.
TURN_LIMIT_S = 45.0
PLAYER_CLOCK_S = 7 * 60.0


@dataclass(frozen=True)
class ClockMetrics:
    n_decisions: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    frac_turns_over_limit: float
    worst_battle_total_s: float
    would_exhaust_player_clock: bool
    watchdog_fires: int


@dataclass(frozen=True)
class ArmResult:
    name: str
    wins: int
    games: int
    clock: ClockMetrics

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.wins, self.games)


@dataclass
class _BattleTiming:
    per_decision_ms: list[float] = field(default_factory=list)
    watchdog_fires: int = 0

    @property
    def total_s(self) -> float:
        return sum(self.per_decision_ms) / 1000


def clock_metrics_from_traces(trace_paths: list[Path]) -> ClockMetrics:
    """Aggregate the three clock metrics from timing events across battles.

    Per-turn latency distribution, the fraction of turns exceeding 45 seconds,
    and whether cumulative usage would exhaust the 7 minute player clock.
    """
    battles: list[_BattleTiming] = []

    for path in trace_paths:
        timing = _BattleTiming()
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("type") != "timing":
                    continue
                payload = event.get("payload", {})
                if "total_ms" in payload:
                    timing.per_decision_ms.append(float(payload["total_ms"]))
                if payload.get("watchdog_fired"):
                    timing.watchdog_fires += 1
        battles.append(timing)

    all_ms = [ms for battle in battles for ms in battle.per_decision_ms]
    over_limit = sum(1 for ms in all_ms if ms > TURN_LIMIT_S * 1000)
    worst_battle_s = max((battle.total_s for battle in battles), default=0.0)

    return ClockMetrics(
        n_decisions=len(all_ms),
        p50_ms=percentile(all_ms, 50),
        p95_ms=percentile(all_ms, 95),
        max_ms=max(all_ms, default=0.0),
        frac_turns_over_limit=(over_limit / len(all_ms)) if all_ms else 0.0,
        worst_battle_total_s=worst_battle_s,
        would_exhaust_player_clock=worst_battle_s > PLAYER_CLOCK_S,
        watchdog_fires=sum(battle.watchdog_fires for battle in battles),
    )


async def run_matchup(
    arm_a: tuple[str, AgentFactory],
    arm_b: tuple[str, AgentFactory],
    n_games: int,
    trace_dir: Path | str,
    seed: int = 0,
) -> list[ArmResult]:
    """Play `n_games` between two arms and return a result per arm.

    Seeds and teams are fixed and passed in, so re-running with the same seed
    reproduces the run, and two arms compared against the same opponent see
    common random numbers rather than independent ones.
    """
    trace_dir = Path(trace_dir)
    name_a, make_a = arm_a
    name_b, make_b = arm_b

    username_a = f"{_username_safe(name_a)}{seed}"
    username_b = f"{_username_safe(name_b)}{seed}"

    player_a = make_a(username_a, seed, str(trace_dir))
    player_b = make_b(username_b, seed + 1, str(trace_dir))

    await player_a.battle_against(player_b, n_battles=n_games)
    await player_a.close_traces()
    await player_b.close_traces()
    for player in (player_a, player_b):
        await player.ps_client.stop_listening()

    return [
        ArmResult(
            name=name_a,
            wins=player_a.n_won_battles,
            games=player_a.n_finished_battles,
            clock=clock_metrics_from_traces(sorted(trace_dir.glob(f"*.{username_a}.jsonl"))),
        ),
        ArmResult(
            name=name_b,
            wins=player_b.n_won_battles,
            games=player_b.n_finished_battles,
            clock=clock_metrics_from_traces(sorted(trace_dir.glob(f"*.{username_b}.jsonl"))),
        ),
    ]


def _username_safe(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())[:12]


def format_results_table(results: list[ArmResult]) -> str:
    """One table: win rate, confidence interval, and the three clock metrics."""
    header = (
        f"{'arm':<22} {'games':>5} {'win rate':>9} {'95% CI':>16} "
        f"{'p50 ms':>8} {'p95 ms':>8} {'max ms':>9} {'>45s':>7} "
        f"{'worst battle':>13} {'clock ok':>9}"
    )
    lines = [header, "-" * len(header)]

    for result in results:
        low, high = result.interval
        clock = result.clock
        lines.append(
            f"{result.name:<22} {result.games:>5} {result.win_rate:>8.1%} "
            f"{f'[{low:.1%}, {high:.1%}]':>16} "
            f"{clock.p50_ms:>8.2f} {clock.p95_ms:>8.2f} {clock.max_ms:>9.2f} "
            f"{clock.frac_turns_over_limit:>6.1%} "
            f"{clock.worst_battle_total_s:>12.1f}s "
            f"{'yes' if not clock.would_exhaust_player_clock else 'NO':>9}"
        )

    lines.append("")
    lines.append(
        f"clock limits: {TURN_LIMIT_S:.0f}s per turn, "
        f"{PLAYER_CLOCK_S / 60:.0f} min player clock. "
        f"'clock ok' is whether the worst battle stayed inside the player clock."
    )
    return "\n".join(lines)

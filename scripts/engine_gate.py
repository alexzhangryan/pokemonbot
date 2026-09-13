"""Run the M8 engine gate and write `docs/engine-gate.md`.

    python scripts/engine_gate.py                       # 200 games x 4 arms x 2 teams
    python scripts/engine_gate.py --games 50            # a smoke run
    python scripts/engine_gate.py --teams regmb-beta    # one team
    python scripts/engine_gate.py --arms twoply         # one arm
    python scripts/engine_gate.py --resume              # continue an interrupted run
    python scripts/engine_gate.py --baseline greedy     # the secondary measurement (section 4)

`docs/specs/2026-09-13-engine-gate.md` sections 3 and 4 specify the arms and
the rule; D70 fixed both before any number existed. This script runs the
matchups and applies the rule mechanically, and the report it writes is
generated rather than hand written, the way `docs/pruning-guard.md` and
`docs/eval-calibration.md` are. `verdict` is a pure function of the rows so
the rule cannot drift between the spec and the run; `tests/test_engine_gate.py`
covers every branch of it on synthetic rows.

Every arm plays `oneply`, the incumbent, in a mirror match on one team, with
one seed. `--baseline greedy` is section 4's pre-registered secondary
measurement for the "neither clears" outcome: the same arms, plus `oneply`
itself, against max-base-power, which has a known baseline (D30) and more
headroom than a mirror. It is a check on sensitivity; the verdict is only ever
read from the primary run, so a secondary run writes its own report and JSON
and applies no rule. Each matchup gets its own username suffix so that several matchups
against one incumbent on one server do not collide -- the defect
`docs/STATUS.md` records against `run_ladder.py`. Results are written to the
JSON after every matchup, so an interrupted run resumes rather than restarts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from champions.harness.elo import wilson_interval
from champions.harness.ladder import ArmResult, ClockMetrics, run_matchup
from champions.teams import ALPHA, BETA, available_teams
from scripts.run_ladder import build_arm
from scripts.run_local_server import start_server

FORMAT_ID = "gen9championsvgc2026regmb"
BASELINE = "oneply"
#: Display names the ladder reports each baseline under.
BASELINE_DISPLAYS = {"oneply": "one-ply", "greedy": "max-base-power"}
CONTROL = "oneply-oracle"
DEPTH = "twoply-oracle"
FIDELITY = "sim-oracle"
BLIND_DEPTH = "twoply"
DEFAULT_ARMS = (CONTROL, BLIND_DEPTH, DEPTH, FIDELITY)
DEFAULT_TEAMS = (ALPHA, BETA)
DEFAULT_GAMES = 200
REPORT_PATH = Path("docs/engine-gate.md")
JSON_PATH = Path(f"data/eval/engine-gate.{FORMAT_ID}.json")
TRACE_DIR = Path("runs/m8-gate")
Z = 1.959964


@dataclass(frozen=True)
class Row:
    """One arm's result against the incumbent on one team."""

    team: str
    arm: str
    games: int
    wins: int
    baseline_wins: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    frac_over_limit: float
    worst_battle_s: float
    clock_ok: bool
    watchdog_fires: int
    decisions: int
    fallback_decisions: int
    fallback_cell_fraction: float | None
    elapsed_s: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.wins, self.games)

    def demonstrates_a_gain(self) -> bool:
        low, _ = self.interval
        return self.games > 0 and low > 0.5


@dataclass(frozen=True)
class Gap:
    """The difference between two arms' win rates, with its 95% interval."""

    estimate: float
    low: float
    high: float

    @property
    def apart_from_zero(self) -> bool:
        return self.low > 0.0 or self.high < 0.0

    @property
    def positive(self) -> bool:
        return self.estimate > 0.0 and self.apart_from_zero


def gap(a: Row, b: Row) -> Gap:
    """`a` minus `b`, two independent proportions. The arms share the opponent
    and the seeds, so treating them as independent is the conservative side."""
    pa, pb = a.win_rate, b.win_rate
    variance = (pa * (1 - pa) / a.games if a.games else 0.0) + (
        pb * (1 - pb) / b.games if b.games else 0.0
    )
    half = Z * math.sqrt(variance)
    return Gap(pa - pb, pa - pb - half, pa - pb + half)


def verdict(rows: dict[str, Row]) -> dict[str, Any]:
    """Section 4 of the spec, applied to one team's rows.

    Returns the outcome and the numbers it rested on. Outcomes: `depth`,
    `fidelity`, `both`, `neither`, or `incomplete` when an arm the rule needs
    is missing.
    """
    needed = (CONTROL, DEPTH, FIDELITY)
    if any(arm not in rows for arm in needed):
        return {"outcome": "incomplete", "missing": [a for a in needed if a not in rows]}

    control, depth, fidelity = rows[CONTROL], rows[DEPTH], rows[FIDELITY]
    depth_gap = gap(depth, control)
    fidelity_gap = gap(fidelity, control)
    depth_minus_fidelity = gap(depth, fidelity)

    depth_clears = (
        depth.demonstrates_a_gain()
        and depth_gap.positive
        and (not fidelity_gap.apart_from_zero or depth_minus_fidelity.positive)
    )
    fidelity_clears = fidelity_gap.positive
    depth_positive = depth.demonstrates_a_gain() and depth_gap.positive

    if depth_clears:
        outcome = "depth"
    elif fidelity_clears and depth_positive:
        outcome = "both"
    elif fidelity_clears:
        outcome = "fidelity"
    else:
        outcome = "neither"

    result: dict[str, Any] = {
        "outcome": outcome,
        "depth_gap": asdict(depth_gap),
        "fidelity_gap": asdict(fidelity_gap),
        "depth_minus_fidelity": asdict(depth_minus_fidelity),
        "control_gain": control.demonstrates_a_gain(),
        "depth_gain": depth.demonstrates_a_gain(),
        "fidelity_gain": fidelity.demonstrates_a_gain(),
    }
    if BLIND_DEPTH in rows:
        result["blind_depth_gain"] = rows[BLIND_DEPTH].demonstrates_a_gain()
    return result


OUTCOME_TEXT = {
    "depth": (
        "**Depth wins the gate.** The two-ply arm demonstrates a gain and its gap over the "
        "information control is apart from zero, and fidelity does not match it. The Rust "
        "engine is justified, on a branch, with section 2's brief: depth 2 with particles, or "
        "depth 3."
    ),
    "fidelity": (
        "**Fidelity wins the gate.** The simulator arm's gap over the information control is "
        "apart from zero and depth does not meet its clause. No engine. The next investment "
        "is the simulator payoff fed by belief particles, with the clock measured; `k`, the "
        "union (D67) and the belief head-to-head (D58) are re-opened against it."
    ),
    "both": (
        "**Both clear.** Fidelity ships first, because depth on a model that does not see "
        "items compounds nothing. Depth is re-asked on the simulator payoff, where section 2 "
        "says it fits on eight cores at one particle and one replicate; the engine decision "
        "becomes particles at depth 2 and is deferred with that reason."
    ),
    "neither": (
        "**Neither clears.** The payoff model is not the binding constraint at this pairing, "
        "which contradicts `docs/STATUS.md`'s reading and is reportable on its own. The "
        "pre-registered secondary measurement is each arm against `greedy`."
    ),
    "incomplete": "**Incomplete.** An arm the rule needs was not measured.",
}


# -- the traces ---------------------------------------------------------------


def fallback_summary(trace_paths: list[Path]) -> tuple[int, int, float | None]:
    """Materialised decisions, those with any fallback cell, and the mean
    fallback cell fraction across them, from an arm's traces.

    Only the simulator arm writes the fields; the rest report zero and None.
    Decisions the simulator was never asked to score -- forced switches, which
    the trace marks `skipped` -- are not counted, because the analytic model
    taking a forced switch is the design, not a failure of the simulator.
    """
    decisions = with_fallback = 0
    fractions: list[float] = []
    for path in trace_paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                payload = event.get("payload") or {}
                if event.get("type") != "candidates" or payload.get("phase") != "pruned":
                    continue
                if "cells" not in payload or not payload.get("materialized"):
                    continue
                decisions += 1
                cells = int(payload.get("cells") or 0)
                fallback = int(payload.get("fallback_cells") or 0)
                fractions.append(fallback / cells if cells else 1.0)
                if fallback:
                    with_fallback += 1
    mean = sum(fractions) / len(fractions) if fractions else None
    return decisions, with_fallback, mean


def row_from(
    team: str,
    arm: str,
    results: list[ArmResult],
    trace_dir: Path,
    username: str,
    elapsed_s: float,
    baseline_display: str = "one-ply",
) -> Row:
    mine = next(r for r in results if r.name != baseline_display)
    theirs = next(r for r in results if r.name == baseline_display)
    clock: ClockMetrics = mine.clock
    decisions, with_fallback, fraction = fallback_summary(
        sorted(trace_dir.glob(f"*.{username}.jsonl"))
    )
    return Row(
        team=team,
        arm=arm,
        games=mine.games,
        wins=mine.wins,
        baseline_wins=theirs.wins,
        p50_ms=clock.p50_ms,
        p95_ms=clock.p95_ms,
        max_ms=clock.max_ms,
        frac_over_limit=clock.frac_turns_over_limit,
        worst_battle_s=clock.worst_battle_total_s,
        clock_ok=not clock.would_exhaust_player_clock,
        watchdog_fires=clock.watchdog_fires,
        decisions=decisions,
        fallback_decisions=with_fallback,
        fallback_cell_fraction=fraction,
        elapsed_s=elapsed_s,
    )


BASELINE_DISPLAY = "one-ply"


# -- the run ------------------------------------------------------------------------


async def run_gate(
    arms: list[str],
    teams: list[str],
    games: int,
    seed: int,
    port: int,
    trace_dir: Path,
    json_path: Path,
    resume: bool,
    baseline: str = BASELINE,
) -> dict[str, Any]:
    baseline_display = BASELINE_DISPLAYS[baseline]
    record: dict[str, Any] = _load(json_path) if resume else {}
    record.setdefault("format", FORMAT_ID)
    record.setdefault("games", games)
    record.setdefault("seed", seed)
    record.setdefault("baseline", baseline)
    record.setdefault("rows", {})
    record["started"] = record.get("started") or datetime.now(UTC).isoformat()

    for team_index, team in enumerate(teams):
        for arm_index, arm in enumerate(arms):
            key = f"{team}/{arm}"
            if key in record["rows"]:
                print(f"{key}: already measured, skipping", flush=True)
                continue
            suffix = f"t{team_index}a{arm_index}"
            matchup_dir = trace_dir / team / arm
            matchup_dir.mkdir(parents=True, exist_ok=True)
            print(f"{key}: {games} games against {baseline} on {team} ...", flush=True)
            started = time.perf_counter()
            results = await run_matchup(
                build_arm(arm, port, team, opponent_team=team),
                build_arm(baseline, port, team),
                games,
                matchup_dir,
                seed=seed,
                username_suffix=suffix,
            )
            elapsed = time.perf_counter() - started
            display = next(r.name for r in results if r.name != baseline_display)
            username = f"{_username_safe(display)}{seed}{suffix}"
            row = row_from(team, arm, results, matchup_dir, username, elapsed, baseline_display)
            record["rows"][key] = asdict(row)
            low, high = row.interval
            print(
                f"{key}: {row.wins}/{row.games} = {row.win_rate:.1%} [{low:.1%}, {high:.1%}] "
                f"in {elapsed / 60:.1f} min; p95 {row.p95_ms:.0f} ms",
                flush=True,
            )
            _save(json_path, record)

    if baseline == BASELINE:
        record["verdicts"] = {
            team: verdict({r.arm: r for r in rows_for(record, team)}) for team in teams
        }
    record["finished"] = datetime.now(UTC).isoformat()
    _save(json_path, record)
    return record


def _username_safe(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())[:12]


def rows_for(record: dict[str, Any], team: str) -> list[Row]:
    return [Row(**data) for key, data in record["rows"].items() if key.startswith(f"{team}/")]


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _save(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


# -- the report -----------------------------------------------------------------------


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _gap_cell(g: dict[str, float] | None) -> str:
    if g is None:
        return "—"
    return f"{g['estimate']:+.1%} [{g['low']:+.1%}, {g['high']:+.1%}]"


def render(record: dict[str, Any], teams: list[str]) -> str:
    baseline = str(record.get("baseline") or BASELINE)
    secondary = baseline != BASELINE
    lines: list[str] = []
    lines.append(
        "# The engine gate: the secondary measurement" if secondary else "# The engine gate"
    )
    lines.append("")
    lines.append("Generated by `scripts/engine_gate.py` (`make gate`). Do not edit by hand.")
    lines.append("")
    lines.append(
        f"Run started {record.get('started', '?')}, finished {record.get('finished', '?')}, "
        f"at commit `{_commit()}` on {platform.platform()}. "
        f"{record.get('games')} games per arm, seed {record.get('seed')}, "
        f"every arm against `{baseline}`."
    )
    lines.append("")
    if secondary:
        lines.append(
            "This is section 4's pre-registered secondary measurement for the outcome in "
            "which neither depth nor fidelity clears the primary gate (`docs/engine-gate.md`): "
            f"the same arms, and `oneply` itself, against `{baseline}`, which has a known "
            "baseline (D30) and more headroom than a mirror. It checks whether the mirror "
            "was too insensitive to see an effect. No verdict is read from it."
        )
        lines.append("")
    lines.append("## What this measures")
    lines.append("")
    lines.append(
        "M8 asks whether marginal win rate comes from search depth or from payoff fidelity "
        "(`docs/01-plan.md`, D6). Every arm below plays `oneply`, the incumbent, in a mirror "
        "match on one team, so a win rate is against the agent that ships today. "
        "`oneply-oracle` is the information control: the analytic model handed the "
        "opponent's registered sets. **Depth** is `twoply-oracle` minus `oneply-oracle`; "
        "**fidelity** is `sim-oracle` minus `oneply-oracle`. `twoply` is depth under the "
        "agent's real information state. The rule that reads the numbers is section 4 of "
        "`docs/specs/2026-09-13-engine-gate.md`, fixed in D70 before any number existed."
    )
    lines.append("")
    lines.append(
        "Intervals are Wilson 95% on the win rate and normal-approximation 95% on a "
        "difference of two proportions. Clock columns are the agent's own decisions "
        "against the 45 s turn limit and the 7 minute player clock; they are reported, "
        "not applied. The fallback column is the share of the simulator arm's materialised "
        "decisions in which at least one cell could not be scored by the simulator and took "
        "the analytic value instead; forced switches are answered by the analytic model by "
        "design and are not counted."
    )
    lines.append("")

    for team in teams:
        rows = {r.arm: r for r in rows_for(record, team)}
        if not rows:
            continue
        lines.append(f"## `{team}`")
        lines.append("")
        lines.append(
            f"| arm | games | win rate vs `{baseline}` | 95% | vs `oneply-oracle` | p50 ms | "
            "p95 ms | > 45 s | worst battle | clock ok | fallback |"
        )
        lines.append("| --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | ---: |")
        control = rows.get(CONTROL)
        ordered = [a for a in (BASELINE, CONTROL, BLIND_DEPTH, DEPTH, FIDELITY) if a in rows]
        for arm in ordered + sorted(a for a in rows if a not in ordered):
            row = rows[arm]
            low, high = row.interval
            versus = (
                _gap_cell(asdict(gap(row, control)))
                if control is not None and arm != CONTROL
                else "—"
            )
            fallback = (
                f"{row.fallback_decisions}/{row.decisions}"
                if row.fallback_cell_fraction is not None
                else "—"
            )
            lines.append(
                f"| `{arm}` | {row.games} | {_pct(row.win_rate)} | [{_pct(low)}, {_pct(high)}] | "
                f"{versus} | {row.p50_ms:.0f} | {row.p95_ms:.0f} | {_pct(row.frac_over_limit)} | "
                f"{row.worst_battle_s:.0f} s | {'yes' if row.clock_ok else 'NO'} | {fallback} |"
            )
        lines.append("")
        if secondary:
            continue
        result = (record.get("verdicts") or {}).get(team) or verdict(rows)
        lines.append(f"**Verdict on `{team}`:** {OUTCOME_TEXT[result['outcome']]}")
        lines.append("")
        if result["outcome"] != "incomplete":
            lines.append(
                f"- depth gap: {_gap_cell(result['depth_gap'])}; "
                f"fidelity gap: {_gap_cell(result['fidelity_gap'])}; "
                f"depth minus fidelity: {_gap_cell(result['depth_minus_fidelity'])}."
            )
            lines.append(
                f"- demonstrates a gain over `oneply`: control {result['control_gain']}, "
                f"depth {result['depth_gain']}, fidelity {result['fidelity_gain']}"
                + (
                    f", blind depth {result['blind_depth_gain']}."
                    if "blind_depth_gain" in result
                    else "."
                )
            )
            lines.append("")

    lines.append("## Limits")
    lines.append("")
    lines.append(
        "- The oracle knows the opponent's six sets, not the bring; unrevealed brought slots "
        "are drawn at random per decision. Its numbers are a ceiling on what a belief "
        "could supply, not a forecast of a belief-fed arm."
    )
    lines.append(
        "- The simulator arm materialises a position from what the agent observed. PP, "
        "Choice locks, Encore, volatile durations and weather duration are not "
        "observable and are not set; `tests/test_rollout.py` names the exclusions and "
        "the fallback column counts what they cost."
    )
    lines.append(
        "- Neither checked-in team carries a held item, so fidelity's headroom on items "
        "specifically is not measured here (D30's item-heavy `regmb-beta` never landed)."
    )
    lines.append(
        "- Verdicts are per team and are not pooled; `regmb-beta`'s is the one about real "
        "play, `regmb-alpha`'s is the control (D30)."
    )
    lines.append("")
    return "\n".join(lines)


# -- main ------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--games", type=int, default=DEFAULT_GAMES)
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    parser.add_argument("--teams", default=",".join(DEFAULT_TEAMS))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--trace-dir", default=None, help=f"default {TRACE_DIR} (+ -<baseline>)")
    parser.add_argument("--json", default=None, help=f"default {JSON_PATH} (+ -<baseline>)")
    parser.add_argument("--out", default=None, help=f"default {REPORT_PATH} (+ -<baseline>)")
    parser.add_argument("--resume", action="store_true", help="keep rows already in --json")
    parser.add_argument(
        "--no-server", action="store_true", help="a Showdown server is already on --port"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="rewrite the report from --json; no games"
    )
    parser.add_argument(
        "--baseline",
        default=BASELINE,
        choices=sorted(BASELINE_DISPLAYS),
        help="the arm every other arm plays; `greedy` is the secondary measurement",
    )
    args = parser.parse_args()

    teams = [t for t in args.teams.split(",") if t]
    unknown = [t for t in teams if t not in available_teams()]
    if unknown:
        raise SystemExit(f"unknown teams {unknown}; available: {available_teams()}")
    arms = [a for a in args.arms.split(",") if a]
    secondary = args.baseline != BASELINE
    if secondary and BASELINE not in arms:
        arms = [BASELINE, *arms]
    tag = f"-{args.baseline}" if secondary else ""
    json_path = Path(args.json or f"data/eval/engine-gate{tag}.{FORMAT_ID}.json")
    out = Path(args.out or f"docs/engine-gate{tag}.md")
    trace_dir = Path(args.trace_dir or f"runs/m8-gate{tag}")
    if args.report_only:
        record = _load(json_path)
        if not secondary:
            record["verdicts"] = {
                team: verdict({r.arm: r for r in rows_for(record, team)}) for team in teams
            }
    else:
        process = None if args.no_server else start_server(port=args.port)
        try:
            record = asyncio.run(
                run_gate(
                    arms,
                    teams,
                    args.games,
                    args.seed,
                    args.port,
                    trace_dir,
                    json_path,
                    args.resume,
                    args.baseline,
                )
            )
        finally:
            if process is not None:
                process.terminate()

    out.write_text(render(record, teams), encoding="utf-8")
    print(f"\nwrote {out} and {json_path}")
    for team, result in record.get("verdicts", {}).items():
        print(f"{team}: {result['outcome']}")


if __name__ == "__main__":
    main()

"""Measure the belief filter on its own. `docs/03-belief-filter.md` section 5.

Two evaluation sets, because neither covers the other:

    python scripts/eval_belief.py corpus --replays 200
    python scripts/eval_belief.py traces --trace-dir traces --team regmb-beta

`corpus` runs the filter over stored forced-open-sheet Bo3 replays, where the
registered set is stated at turn 0 and the filter is never shown it. That gives
item, ability, nature, moveset and whole-set accuracy against real ladder teams.
It cannot give interval coverage: open sheets do not carry stat points.

`traces` reads the `belief` events out of self-play traces and scores them
against the team file the opponent actually played, which does carry stat
points. That is the only source of interval coverage in the project, and
coverage is the metric `CLAUDE.md` constraint 5 exists to be checked by.

Both print the turn-1 row separately, because turn 1 is the prior with no
in-battle updating -- the baseline `docs/03` section 5 says to beat -- and the
difference between it and the last row is what the filter actually contributes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from champions.belief import evaluate
from champions.belief.priors import SetPrior
from champions.corpus.store import CorpusStore
from champions.dex.loader import Dex

FORMAT_ID = "gen9championsvgc2026regmb"
DEFAULT_DATABASE = Path("data/corpus.sqlite")
TEAM_DIR = Path("data/teams")


def _row(label: str, score: dict[str, Any]) -> str:
    def cell(name: str) -> str:
        entry = score.get(name) or {}
        if not entry.get("n"):
            return f"{'--':>16}"
        return f"{entry['accuracy'] * 100:5.1f}% {entry['log_loss']:6.3f}"

    coverage = score.get("coverage") or {}
    coverage_cell = (
        f"{coverage['coverage'] * 100:5.1f}% w{coverage['mean_width']:5.1f}"
        if coverage.get("n")
        else f"{'--':>13}"
    )
    return (
        f"{label:<10}{cell('item'):>15}{cell('ability'):>15}{cell('nature'):>15}"
        f"{cell('moves'):>15}{cell('whole_set'):>15}{coverage_cell:>15}"
    )


def _header() -> str:
    columns = ("item", "ability", "nature", "moves", "whole set", "coverage")
    return f"{'':<10}" + "".join(f"{c:>15}" for c in columns)


def report(scores: dict[int, evaluate.BeliefScore], title: str) -> None:
    summary = evaluate.summarise(scores)
    turns = sorted(scores)
    print(f"\n{title}")
    print(_header())
    print("-" * (10 + 15 * 6))
    for turn in turns[:12]:
        print(_row(f"turn {turn}", scores[turn].as_dict()))
    if len(turns) > 12:
        print(f"{'...':<10}")
        print(_row(f"turn {turns[-1]}", scores[turns[-1]].as_dict()))
    print("-" * (10 + 15 * 6))
    print(_row("overall", summary["overall"]))

    merged = evaluate.merge(scores.values())
    for label, coverage in (
        ("modal particle, which is what the search reads", merged.coverage),
        ("union over particles, which is what the panel shows", merged.coverage_union),
    ):
        if not coverage.n:
            continue
        low, high = evaluate.wilson(coverage.covered, coverage.n)
        print(
            f"\ninterval coverage, {label}:\n"
            f"  {coverage.coverage * 100:.1f}% [{low * 100:.1f}%, {high * 100:.1f}%] "
            f"over {coverage.n} stat-turns, mean width "
            f"{coverage.mean_width:.1f} of 32 points"
        )
    print("\ncalibration (predicted -> realised):")
    for bucket in summary["overall"]["calibration"]:
        print(f"  {bucket['bucket']:>10}  n={bucket['n']:>6}  {bucket['realised']:.3f}")


def run_corpus(args: argparse.Namespace) -> dict[int, evaluate.BeliefScore]:
    dex = Dex.load(FORMAT_ID)
    prior = SetPrior.load()
    store = CorpusStore(args.database)
    try:
        logs = store.stored_logs()
        scores: dict[int, evaluate.BeliefScore] = {}
        used = 0
        for format_id, replay_id in logs:
            if used >= args.replays:
                break
            log = store.read_log(format_id, replay_id)
            if "|showteam|" not in log:
                continue
            for side in ("p1", "p2"):
                curve = evaluate.replay_belief_curve(
                    log,
                    prior,
                    dex,
                    side,
                    n_particles=args.particles,
                    seed=args.seed,
                )
                for turn, score in curve:
                    evaluate.merge([score])
                    target = scores.setdefault(turn, evaluate.BeliefScore())
                    merged = evaluate.merge([target, score])
                    scores[turn] = merged
            used += 1
            if used % 25 == 0:
                print(f"  ... {used} replays", flush=True)
        print(f"scored {used} replays, both sides of each")
        return scores
    finally:
        store.close()


def run_traces(args: argparse.Namespace) -> dict[int, evaluate.BeliefScore]:
    team_path = TEAM_DIR / f"{args.team}.txt"
    if not team_path.exists():
        raise SystemExit(f"No team file at {team_path}")
    truth = evaluate.truth_from_team_file(team_path.read_text(encoding="utf-8"))
    print(f"truth: {len(truth)} sets from {team_path}")

    paths = sorted(Path(args.trace_dir).glob("*.jsonl"))
    if not paths:
        raise SystemExit(f"No traces in {args.trace_dir}. Run `make selfplay` first.")

    scores: dict[int, evaluate.BeliefScore] = {}
    for path in paths:
        for turn, score in evaluate.score_trace(path, truth).items():
            target = scores.get(turn)
            scores[turn] = evaluate.merge([target, score]) if target else score
    print(f"scored {len(paths)} traces")
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    corpus = sub.add_parser("corpus", help="stored open-sheet replays; no coverage")
    corpus.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    corpus.add_argument("--replays", type=int, default=100)
    corpus.add_argument("--particles", type=int, default=32)
    corpus.add_argument("--seed", type=int, default=0)
    corpus.set_defaults(run=run_corpus, title="corpus (open-sheet replays)")

    traces = sub.add_parser("traces", help="self-play traces; the only source of coverage")
    traces.add_argument("--trace-dir", default="traces")
    traces.add_argument("--team", default="regmb-beta")
    traces.set_defaults(run=run_traces, title="self-play traces")

    parser.add_argument("--json", type=Path, default=None, help="also write the full table here")
    args = parser.parse_args()

    scores = args.run(args)
    if not scores:
        raise SystemExit("nothing scored")
    report(scores, args.title)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(evaluate.summarise(scores), indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()

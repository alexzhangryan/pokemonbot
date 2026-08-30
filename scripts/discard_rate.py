"""Run the pruning guard over traced decisions.

    python scripts/discard_rate.py                        # runs/m6-selfplay, k = 5,10,15,20
    python scripts/discard_rate.py --k 10                 # one budget
    python scripts/discard_rate.py --traces runs/x        # a different trace directory
    python scripts/discard_rate.py --limit 100            # a sample of battles
    python scripts/discard_rate.py --json out.json        # machine readable

Writes `docs/pruning-guard.md`, which is to the policy layer what
`docs/eval-calibration.md` is to the evaluation: the measurement that says
whether the thing may be relied on, regenerated rather than hand written.

`docs/04-decision-engine.md` section 3 requires this before candidate pruning
can be trusted: solve the unpruned game, and report how often its equilibrium
puts mass on a row the policy discarded. `champions/search/discard.py` explains
what the number does and does not cover; the short version is that it measures
the row side only, against whatever opponent columns the traced agent used, with
payoffs recomputed by today's evaluation function.

Reported per policy, then per `k`, then broken out by the number of opponent
columns -- because a one-column position is an argmax rather than an equilibrium
and would otherwise carry the average.

Every policy is measured against the same solve of the same position. The
unpruned matrix is the entire cost of a run and depends on neither the budget
nor the provider, so adding one is an argument rather than a second sweep, and
section 3 asks for the providers to be benchmarked *identically* rather than
comparably.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from champions.dex.loader import Dex
from champions.search import discard
from champions.search.learned import LearnedPolicy, UnionPolicy
from champions.search.payoff import TurnModel, payoff_matrix
from champions.search.policy import BasePowerPolicy, HeuristicPolicy

FORMAT_ID = "gen9championsvgc2026regmb"
DEFAULT_TRACES = Path("runs/m6-selfplay")
DEFAULT_KS = (5, 10, 15, 20)
REPORT_PATH = Path("docs/pruning-guard.md")

#: The name the union of A and B is reported under. Spelled out rather than
#: derived, because it is a row in a document and a key in a JSON file that
#: someone will diff against the last run.
UNION = "union-heuristic-learned"


def _union(dex: Dex) -> UnionPolicy:
    return UnionPolicy(HeuristicPolicy(dex), LearnedPolicy(dex), name=UNION)


#: The candidate providers this benchmark knows how to build. Implementation A
#: as `docs/04-decision-engine.md` section 3 specifies it; A as it shipped
#: through M6, which is the baseline every number written before D61 describes;
#: B, the learned prior; and the union of A and B, which the spec asks for
#: because if neither dominates the union may still beat both and is a
#: legitimate thing to ship. C, the language model provider, is blocked on a
#: model API key.
#:
#: Every one of them is measured against the same solve of the same position, so
#: the rows compare providers rather than runs.
PROVIDERS = {
    HeuristicPolicy.name: HeuristicPolicy,
    BasePowerPolicy.name: BasePowerPolicy,
    LearnedPolicy.name: LearnedPolicy,
    UNION: _union,
}

#: The providers that need `make fit-policy` to have been run. Named so that a
#: default run can skip them with an explanation rather than failing, while a
#: run that asked for one by name still fails loudly.
NEEDS_MODEL = (LearnedPolicy.name, UNION)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, default=DEFAULT_TRACES)
    parser.add_argument("--format-id", default=FORMAT_ID)
    parser.add_argument(
        "--k",
        type=int,
        nargs="+",
        default=list(DEFAULT_KS),
        help="candidate budgets to measure; the agent's default is 10",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="measure at most this many trace files (0 = all)",
    )
    parser.add_argument(
        "--policy",
        nargs="+",
        default=None,
        choices=list(PROVIDERS),
        help="candidate providers to measure, all against the same solve "
        "(default: every one that can be built)",
    )
    parser.add_argument("--resamples", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--no-report", action="store_true", help="print only, write no doc")
    return parser.parse_args()


def build_policies(dex: Dex, names: list[str] | None) -> dict[str, Any]:
    """Every named provider, or every one that can be built.

    A learned provider needs a fitted prior on disk. Asking for one by name and
    not having it is an error; a default run that cannot build it says so and
    measures the rest, because a missing model should not make the guard
    unrunnable on a fresh checkout.
    """
    wanted = names or list(PROVIDERS)
    out: dict[str, Any] = {}
    for name in wanted:
        try:
            out[name] = PROVIDERS[name](dex)
        except FileNotFoundError as missing:
            if names is not None or name not in NEEDS_MODEL:
                raise SystemExit(str(missing)) from missing
            print(f"skipping {name}: {missing}")
    if not out:
        raise SystemExit("no candidate providers could be built")
    return out


def main() -> None:
    args = parse_args()
    dex = Dex.load(args.format_id)
    model = TurnModel(dex)
    policies = build_policies(dex, args.policy)
    args.policy = list(policies)

    def matrix_fn(
        snapshot: dict[str, Any],
        ours: list[dict[str, Any]],
        theirs: list[dict[str, Any]],
    ) -> np.ndarray:
        return payoff_matrix(snapshot, ours, theirs, model)

    def keeper(provider: Any) -> discard.KeepFn:
        def keep(
            snapshot: dict[str, Any], actions: list[dict[str, Any]], k: int
        ) -> list[dict[str, Any]]:
            return provider.candidates(actions, snapshot, None, k)

        return keep

    keeps = {name: keeper(provider) for name, provider in policies.items()}

    files = discard.trace_files(args.traces)
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise SystemExit(f"no traces under {args.traces}")

    print(f"{len(files)} trace files under {args.traces}")

    started = time.perf_counter()
    decisions: list[discard.Decision] = []
    for path in files:
        decisions.extend(discard.decisions(discard.read_events(path)))
    print(f"{len(decisions)} measurable decisions ({time.perf_counter() - started:.1f}s to read)")

    # One payoff matrix per position, every budget measured against it. The
    # unpruned matrix does not depend on `k` and is the whole cost of the run.
    ks = sorted(args.k)
    started = time.perf_counter()
    rows: list[discard.Measurement] = []
    for i, decision in enumerate(decisions, start=1):
        rows.extend(discard.measure_many(decision, matrix_fn, keeps, ks))
        if i % 500 == 0:
            print(f"  {i}/{len(decisions)} positions ({time.perf_counter() - started:.0f}s)")
    elapsed = time.perf_counter() - started
    print(f"measured in {elapsed:.0f}s")

    results: list[dict[str, Any]] = []
    per_policy: dict[str, dict[int, list[discard.Measurement]]] = {}
    for name in args.policy:
        measured = discard.by_policy(rows).get(name, [])
        by_k = {k: [m for m in measured if m.k == k] for k in ks}
        per_policy[name] = by_k
        print()
        print(f"== {name} " + "=" * (60 - len(name)))
        for k in ks:
            summary = discard.summarise(by_k[k], resamples=args.resamples, seed=args.seed)
            results.append(
                {
                    "summary": summary.as_row(),
                    "by_columns": {
                        str(columns): discard.summarise(
                            group, resamples=args.resamples, seed=args.seed
                        ).as_row()
                        for columns, group in sorted(discard.by_columns(by_k[k]).items())
                    },
                }
            )
            report(summary, results[-1]["by_columns"])

    common = {
        name: compare(by_k, args.resamples, args.seed, name) for name, by_k in per_policy.items()
    }
    common = {name: rows_ for name, rows_ in common.items() if rows_}
    if common:
        results.append({"common_set": common})

    payload = {
        "format_id": args.format_id,
        "traces": args.traces.as_posix(),
        "policies": list(args.policy),
        "n_trace_files": len(files),
        "n_decisions": len(decisions),
        "elapsed_s": round(elapsed, 1),
        "results": results,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")

    if not args.no_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(document(payload, common), encoding="utf-8")
        print(f"wrote {args.report}")


def document(payload: dict[str, Any], common: dict[str, Any]) -> str:
    """The report, generated so it cannot drift from the numbers.

    Written the way `docs/eval-calibration.md` is: what was measured, what the
    number means, and what it does not cover, with the caveats beside the table
    rather than in a paragraph someone can read past.
    """
    per_k = [r for r in payload["results"] if "summary" in r]
    lines = [
        "# The pruning guard",
        "",
        "Generated by `scripts/discard_rate.py`. Do not edit by hand.",
        "",
        f"Source: `{payload['traces']}`, {payload['n_trace_files']:,} agent-view traces, "
        f"{payload['n_decisions']:,} decisions with an unpruned action set.",
        "",
        "## What this measures",
        "",
        "`docs/04-decision-engine.md` section 3 permits candidate pruning only if it",
        "does not drop actions that are uniquely correct, and requires that to be",
        "measured offline: solve the unpruned game, then report the probability its",
        "equilibrium places on rows the policy discarded.",
        "",
        "**Discarded mass** is that probability, averaged over positions. **Value loss**",
        "is the game value of the unpruned row set minus the value of the kept one,",
        "same columns -- what the pruning cost in win probability, on the one-turn",
        "model's own scale. Mass says how often pruning changed the answer; value loss",
        "says whether it mattered.",
        "",
        "Intervals are 95%, bootstrapped over **battles**. Positions inside one game",
        "share a board and a team, so resampling positions would report an interval far",
        "narrower than the evidence supports.",
        "",
        "## The providers",
        "",
        "| name | what it is |",
        "| --- | --- |",
        "| `heuristic-position` | Implementation A as `docs/04-decision-engine.md` section 3 "
        "specifies it: knockouts, threatened Protect, speed control that flips a race, Fake Out. |",
        "| `heuristic-base-power` | A as it shipped through M6 -- base power and nothing else. "
        "Kept because every number written before D61 describes it. |",
        "| `learned-prior` | Implementation B, fit to the replay corpus. "
        "`docs/policy-prior.md` is where it is fit and what it recalls. |",
        "| `union-heuristic-learned` | A and B interleaved to the same `k`. The spec asks for it "
        "because if neither dominates the union may still beat both. |",
        "",
        "C, the language model provider, is blocked on a model API key.",
        "",
        "## Results",
        "",
        "Every policy below was measured against the same solve of the same positions,",
        "so the rows are a comparison between providers rather than between runs.",
        "",
        "| policy | k | positions | battles | discarded mass | 95% | nonzero | "
        "mean value loss | worst |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in per_k:
        s = result["summary"]
        lines.append(
            f"| `{s['policy']}` | {s['k']} | {s['n_positions']:,} | {s['n_battles']:,} | "
            f"{s['mean']:.4f} | [{s['low']:.4f}, {s['high']:.4f}] | "
            f"{s['nonzero_fraction']:.1%} | "
            f"{s['mean_value_loss']:.4f} | {s['max_value_loss']:.4f} |"
        )

    if common:
        lines += [
            "",
            "### The same positions at every budget",
            "",
            "Each `k` above is measured on its own eligible set -- a turn with twelve legal",
            "actions is a measurement of pruning at 10 and not at 15 -- so the rows are not",
            "comparable to each other. These are.",
            "",
            "| policy | k | discarded mass | 95% | nonzero | mean value loss |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for policy, rows in common.items():
            for k, row in sorted(rows.items(), key=lambda kv: int(kv[0])):
                lines.append(
                    f"| `{policy}` | {k} | {row['mean']:.4f} | "
                    f"[{row['low']:.4f}, {row['high']:.4f}] | "
                    f"{row['nonzero_fraction']:.1%} | {row['mean_value_loss']:.4f} |"
                )

    lines += [
        "",
        "### By opponent column count",
        "",
        "A position with one opponent column is an argmax rather than an equilibrium:",
        "under the revealed-moves-only opponent model an early turn has nothing to",
        "predict. Broken out so the easy half does not carry the average.",
        "",
        "| policy | k | columns | positions | discarded mass | 95% | mean value loss |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in per_k:
        for columns, row in result["by_columns"].items():
            lines.append(
                f"| `{result['summary']['policy']}` | {result['summary']['k']} | {columns} | "
                f"{row['n_positions']:,} | "
                f"{row['mean']:.4f} | [{row['low']:.4f}, {row['high']:.4f}] | "
                f"{row['mean_value_loss']:.4f} |"
            )

    checks: dict[str, list[int]] = {}
    for result in per_k:
        row = result["summary"]
        seen = checks.setdefault(row["policy"], [0, 0])
        seen[0] += row["trace_mismatches"]
        seen[1] += row["trace_checked"]
    lines += [
        "",
        "## What it does not cover",
        "",
        "- **The row side only.** Opponent candidates are pruned too, and the columns",
        "  here are whatever the traced agent used. A row set is only correct against",
        "  some column set, so this bounds one of the two prunings.",
        "- **The policy the traces were played by.** The positions, the legal action",
        "  sets and the opponent columns are all the ones `heuristic-base-power`",
        "  produced while playing. A different policy measured on them is answering",
        '  "what would this have kept here?", which is the right question for the',
        "  guard and not the same as playing the games again with it.",
        "- **The one-turn model's payoffs.** Value loss is loss under the analytic turn",
        "  model and the current evaluation weights, not under the real game. It is the",
        "  quantity the search is actually optimising, which is what makes it the right",
        "  thing to bound, and it is not a win-rate claim.",
        "- **This agent's own games.** The self-play source is one team played by",
        "  both sides, so every action set here is the one that team produces.",
        "",
        "The kept set is re-derived from the policy rather than read off the trace, and",
        "agreeing with the traced candidate set is the check that this measures the",
        "selection the agent actually ran. It only means anything for the policy the",
        "traces were written by: a different provider keeping a different set is the",
        "measurement rather than a fault, so the check is not computed for one and the",
        "count below says so rather than reporting a flattering zero.",
        "",
    ]
    for policy, (mismatches, checked) in checks.items():
        lines.append(
            f"- `{policy}`: {mismatches:,} disagreements over {checked:,} positions checked."
            if checked
            else f"- `{policy}`: not the policy these traces were written by, so unchecked."
        )
    lines.append("")
    return "\n".join(lines)


def compare(
    by_k: dict[int, list[discard.Measurement]], resamples: int, seed: int, policy: str
) -> dict[str, Any]:
    """The same positions at every budget.

    Each `k` is otherwise measured on its own eligible set -- a turn with twelve
    legal actions is a measurement of pruning at 10 and not at 15 -- so reading
    the per-`k` numbers against each other compares two different position sets
    and would answer "does a bigger budget help?" with an artefact.
    """
    if len(by_k) < 2:
        return {}
    keys = [{(m.battle_id, m.viewpoint, m.turn) for m in rows} for rows in by_k.values()]
    common = set.intersection(*keys)
    if not common:
        return {}

    out: dict[str, Any] = {}
    print()
    print(f"{policy}: the same {len(common)} positions at every budget:")
    for k, rows in sorted(by_k.items()):
        summary = discard.summarise(
            [m for m in rows if (m.battle_id, m.viewpoint, m.turn) in common],
            resamples=resamples,
            seed=seed,
        )
        out[str(k)] = summary.as_row()
        print(
            f"  k = {k:>2}: mass {summary.mean:.4f} 95% [{summary.low:.4f}, {summary.high:.4f}], "
            f"nonzero {summary.nonzero_fraction:.1%}, value loss {summary.mean_value_loss:.4f}"
        )
    return out


def report(summary: discard.Summary, by_columns: dict[str, Any]) -> None:
    print()
    print(f"k = {summary.k}: {summary.n_positions} positions over {summary.n_battles} battles")
    print(
        f"  discarded mass  {summary.mean:.4f}  95% [{summary.low:.4f}, {summary.high:.4f}]"
        f"   nonzero on {summary.nonzero_fraction:.1%} of positions"
    )
    print(
        f"  value loss      {summary.mean_value_loss:.4f} mean, {summary.max_value_loss:.4f} worst"
    )
    if summary.trace_mismatches:
        # The re-derived kept set should be the one the agent recorded. A
        # mismatch means this is measuring a policy the trace did not run.
        print(f"  !! {summary.trace_mismatches} positions disagree with the traced candidate set")
    for columns, row in by_columns.items():
        print(
            f"  {columns} opponent column(s): {row['n_positions']:>6} positions, "
            f"mass {row['mean']:.4f} 95% [{row['low']:.4f}, {row['high']:.4f}], "
            f"value loss {row['mean_value_loss']:.4f}"
        )


if __name__ == "__main__":
    main()

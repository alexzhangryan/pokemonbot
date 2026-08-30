"""Fit the learned candidate prior to the replay corpus and report what it recalls.

    python scripts/fit_policy.py                     # the whole eligible population
    python scripts/fit_policy.py --limit 300         # a sample, for a fast loop
    python scripts/fit_policy.py --hidden 16         # one width instead of the grid
    python scripts/fit_policy.py --no-report         # print only, write no doc

Writes `data/policy/prior.<format>.json` and `docs/policy-prior.md`, the latter
generated rather than hand written for the same reason
`docs/eval-calibration.md` and `docs/pruning-guard.md` are: a measurement
someone retypes is a measurement that drifts.

This is step 3 of `docs/specs/2026-08-29-learned-policy-provider.md` section 4,
and it is a checkpoint rather than a stage on the way to step 4. The number it
produces -- top-`k` recall of the human's action on held-out players, beside
implementation A's on the same choice sets -- is reportable on its own. If B
does not beat A here, that is the result, and the guard in step 4 confirms it
cheaply rather than being asked for a different answer.

## What the comparison is, exactly

Per slot, not per joint action. A replay's label is one slot's choice, so the
only like-for-like comparison against A is at that granularity;
`policy.HeuristicPolicy.slot_scores` exposes the per-slot half of the score A
already sums into a joint one. The joint-action comparison is the pruning guard,
which is step 4 and measures a different thing -- what pruning throws away
against the equilibrium, rather than how often it keeps what a human played.

Both providers are scored on the same reconstructed choice sets, in the same
pass, so the rows differ by provider and by nothing else.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.corpus.replay import parse_replay  # noqa: E402
from champions.corpus.store import CorpusStore  # noqa: E402
from champions.dex.loader import Dex  # noqa: E402
from champions.search import learned  # noqa: E402
from champions.search.policy import HeuristicPolicy  # noqa: E402
from champions.search.policy_data import decisions_from_record  # noqa: E402
from champions.search.policy_features import FEATURE_NAMES  # noqa: E402

FORMAT_ID = "gen9championsvgc2026regmb"
REPORT_PATH = Path("docs/policy-prior.md")
MODEL_DIR = Path("data/policy")

#: Rating floor, as a percentile of the corpus's own rating distribution rather
#: than as a number. Spec section 3.4: a policy prior should imitate players
#: worth imitating, and the corpus grows, so a hardcoded 1269 would silently
#: become a different quantile every week.
DEFAULT_PERCENTILE = 75.0

#: Candidate budgets, per slot. `policy.DEFAULT_K` is ten *joint* actions, which
#: is roughly three per slot once the two halves are crossed, so 1, 3 and 5
#: bracket the budget the agent actually runs at.
DEFAULT_KS = (1, 3, 5)

#: Widths tried, chosen on validation log likelihood. Not a serious search: the
#: point is that the width is not chosen on the number that gets reported.
DEFAULT_HIDDEN = (8, 16, 32)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-db", type=Path, default=Path("data/corpus.sqlite"))
    parser.add_argument("--corpus-logs", type=Path, default=Path("data/replays"))
    parser.add_argument("--format-id", default=FORMAT_ID)
    parser.add_argument("--percentile", type=float, default=DEFAULT_PERCENTILE)
    parser.add_argument("--limit", type=int, default=0, help="use at most this many replays")
    parser.add_argument("--hidden", type=int, nargs="+", default=list(DEFAULT_HIDDEN))
    parser.add_argument("--l2", type=float, default=learned.DEFAULT_L2)
    parser.add_argument("--max-iter", type=int, default=learned.DEFAULT_MAX_ITER)
    parser.add_argument("--k", type=int, nargs="+", default=list(DEFAULT_KS))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resamples", type=int, default=1000)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--no-report", action="store_true", help="print only, write no doc")
    return parser.parse_args()


# -- the population ----------------------------------------------------------


def rating_floor(store: CorpusStore, format_id: str, percentile: float) -> float:
    """The percentile of the corpus's rating distribution, over both players.

    Both columns pooled, because a rating is a player's rating whichever side
    they were on and treating p1 and p2 as separate populations would be two
    estimates of one quantity.
    """
    ratings = [
        value
        for row in store.conn.execute(
            "SELECT p1_rating, p2_rating FROM replays WHERE rated = 1 AND format_id LIKE ? || '%'",
            (format_id,),
        )
        for value in row
        if value is not None
    ]
    if not ratings:
        raise SystemExit("no rated replays in the corpus; run `make scrape` first")
    return float(np.percentile(ratings, percentile))


def eligible(
    store: CorpusStore, format_id: str, floor: float, sheets: int, limit: int
) -> list[tuple[str, str]]:
    """`(format_id, replay_id)` for the replays a fit may read.

    Three filters and each earns its place.

    `rated` and the rating floor are the spec's population: imitate players
    worth imitating (section 3.4), on a corpus D39 records as skill confounded.

    `bring_fully_observed` is the one the spec does not name and the
    reconstruction needs. A replay only reveals a bring-4 through Pokemon that
    took the field, so when one never did, the switch half of every choice set
    in that game is short an option that was legal. That is not noise the model
    can average out -- it is a systematically smaller denominator.
    """
    rows = store.conn.execute(
        "SELECT format_id, id FROM replays "
        "WHERE format_id LIKE ? || '%' AND rated = 1 "
        "  AND p1_rating >= ? AND p2_rating >= ? "
        "  AND bring_fully_observed = 1 AND sheets_revealed = ? "
        "ORDER BY id",
        (format_id, floor, floor, sheets),
    ).fetchall()
    return [(fmt, rid) for fmt, rid in rows][: limit or None]


def collect(
    store: CorpusStore,
    replays: list[tuple[str, str]],
    dex: Dex,
    revealed_moves_fallback: bool = False,
) -> tuple[learned.PolicyDataset, np.ndarray]:
    """Reconstruct, vectorise and score every decision in one pass.

    A's score comes out of the same pass on purpose. Section 3 asks for the
    providers to be benchmarked identically; two passes would give two
    reconstructions, and a difference between them would show up as a
    difference between providers.
    """
    policy = HeuristicPolicy(dex)

    def baseline(decision: Any) -> list[float]:
        return policy.slot_scores(decision.options, decision.slot, decision.snapshot)

    def rows() -> Any:
        for fmt, replay_id in replays:
            log = store.read_log(fmt, replay_id)
            record = parse_replay(replay_id, log, fmt)
            yield from decisions_from_record(record, log, dex, revealed_moves_fallback)

    return learned.PolicyDataset.build(rows(), FEATURE_NAMES, baseline)


# -- the fit -----------------------------------------------------------------


def choose_width(
    train: learned.PolicyDataset,
    validation: learned.PolicyDataset,
    widths: list[int],
    l2: float,
    seed: int,
    max_iter: int,
) -> tuple[learned.PolicyModel, list[dict[str, Any]]]:
    """Fit each width, keep the one the validation split likes best.

    Log likelihood rather than recall, because recall at a budget is a step
    function of the ranking and log likelihood is not: two models that keep the
    human's action in the top three equally often can still differ in how much
    probability they put on it, and the smoother of the two criteria is the one
    to choose on.
    """
    trials = []
    best: tuple[float, learned.PolicyModel] | None = None
    for hidden in widths:
        started = time.time()
        model = learned.fit(train, hidden=hidden, l2=l2, seed=seed, max_iter=max_iter)
        score = learned.log_likelihood(validation, model.score(validation.x))
        trials.append(
            {
                "hidden": hidden,
                "train_log_likelihood": learned.log_likelihood(train, model.score(train.x)),
                "validation_log_likelihood": score,
                "seconds": round(time.time() - started, 1),
            }
        )
        print(
            f"  hidden {hidden:>3}: validation log likelihood {score:.4f} "
            f"({trials[-1]['seconds']}s)"
        )
        if best is None or score > best[0]:
            best = (score, model)
    assert best is not None
    return best[1], trials


def measure(
    data: learned.PolicyDataset,
    scores: dict[str, np.ndarray],
    ks: list[int],
    resamples: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Recall at every budget for every provider, on one set of decisions."""
    out = []
    for name, values in scores.items():
        for k in ks:
            low, high = learned.bootstrap_recall(data, values, k, resamples=resamples, seed=seed)
            out.append(
                {
                    "provider": name,
                    "k": k,
                    "recall": learned.recall(data, values, k),
                    "low": low,
                    "high": high,
                    "n_decisions": len(data),
                    "n_battles": data.n_battles,
                    "n_players": int(np.unique(data.player).size),
                }
            )
    return out


def uniform_scores(data: learned.PolicyDataset) -> np.ndarray:
    """The bar every provider has to clear: no ordering at all.

    A constant makes every option tied, and `learned.hits` counts a tie against
    the provider, so this reports what a budget of `k` recovers when the ranking
    is worthless. Without it a recall of 0.6 at `k = 3` reads as a result when
    the average slot has nine options and the number could be arithmetic.
    """
    return np.zeros(len(data.x))


# -- the report --------------------------------------------------------------


def describe(rows: list[dict[str, Any]]) -> None:
    print()
    print(f"{'provider':<20} {'k':>2} {'recall':>8}  95% interval")
    for row in rows:
        print(
            f"{row['provider']:<20} {row['k']:>2} {row['recall']:>8.4f}  "
            f"[{row['low']:.4f}, {row['high']:.4f}]"
        )


def verdict(rows: list[dict[str, Any]], k: int) -> str:
    """Which provider the intervals actually separate, at the agent's budget.

    Spec section 1 fixes this before the numbers arrive: if the intervals
    overlap, no difference has been demonstrated and A stays, because it is the
    incumbent, needs no corpus and has no inference cost.
    """
    at_k = {row["provider"]: row for row in rows if row["k"] == k}
    a, b = at_k.get(HeuristicPolicy.name), at_k.get("learned-prior")
    if a is None or b is None:
        return "not measured"
    if b["low"] > a["high"]:
        return f"B beats A at k = {k}: {b['recall']:.4f} against {a['recall']:.4f}, intervals apart"
    if a["low"] > b["high"]:
        return f"A beats B at k = {k}: {a['recall']:.4f} against {b['recall']:.4f}, intervals apart"
    return (
        f"no difference demonstrated at k = {k}: {b['recall']:.4f} against {a['recall']:.4f}, "
        "intervals overlap, so A stays"
    )


def document(payload: dict[str, Any]) -> str:
    lines = [
        "# The learned candidate prior",
        "",
        "Generated by `scripts/fit_policy.py`. Do not edit by hand.",
        "",
        f"Corpus reading: {payload['n_replays']:,} eligible replays of "
        f"{payload['n_rated']:,} rated, rating floor {payload['floor']:.0f} "
        f"(the {payload['percentile']:.0f}th percentile, recomputed at fit time). "
        f"{payload['n_decisions']:,} decisions, {payload['n_players']:,} players, "
        f"{payload['mean_options']:.1f} options per decision on average.",
        "",
        "## What this measures",
        "",
        "Implementation B of `docs/04-decision-engine.md` section 3: a model fit to the",
        "replay corpus that scores each legal option and returns the top `k`. The number",
        "here is **top-`k` recall of the human's action on held-out players** -- how often",
        "a budget of `k` would have kept what a strong human actually played.",
        "",
        "Per slot, not per joint action, because a replay's label is one slot's choice.",
        "A is scored on the same reconstructed choice sets in the same pass, so the rows",
        "differ by provider and by nothing else. `uniform` is the bar: no ordering at",
        "all, which is what a recall has to beat before it means anything.",
        "",
        "Recall is not the shipping criterion. The equilibrium and a strong human differ,",
        "and by how much is exactly what `docs/pruning-guard.md` measures. This says",
        "whether the model learned anything; that says whether it is worth pruning with.",
        "",
        "Intervals are 95%, bootstrapped over **battles**. Positions inside one game share",
        "a board and a team, so resampling positions would report an interval far narrower",
        "than the evidence supports.",
        "",
        "## Held-out players",
        "",
        f"**{payload['verdict']}**",
        "",
        "| provider | k | recall | 95% | decisions | battles | players |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in payload["test"]:
        lines.append(
            f"| `{row['provider']}` | {row['k']} | {row['recall']:.4f} | "
            f"[{row['low']:.4f}, {row['high']:.4f}] | {row['n_decisions']:,} | "
            f"{row['n_battles']:,} | {row['n_players']:,} |"
        )

    lines += [
        "",
        "## The fit",
        "",
        "Width chosen on validation log likelihood, which never touches the table above.",
        "",
        "| hidden | train log likelihood | validation log likelihood | seconds |",
        "| --- | --- | --- | --- |",
    ]
    for trial in payload["trials"]:
        lines.append(
            f"| {trial['hidden']} | {trial['train_log_likelihood']:.4f} | "
            f"{trial['validation_log_likelihood']:.4f} | {trial['seconds']} |"
        )
    lines += [
        "",
        f"Chosen: {payload['hidden']} hidden units, L2 {payload['l2']}, seed {payload['seed']}. "
        f"Split by player, {payload['n_train']:,} / {payload['n_validation']:,} / "
        f"{payload['n_test']:,} decisions.",
        "",
    ]

    if payload.get("closed_sheet"):
        lines += [
            "## The closed-sheet slice",
            "",
            "The corpus is open-sheet play and the agent declines Open Team Sheets, so the",
            "humans it imitates chose under strictly more information than the agent has",
            "(spec section 2). These replays are the ones where they did not.",
            "",
            "Read the comparison, not the level. Without `|showteam|` a player's own move",
            "set can only be reconstructed from what they revealed, which is a subset of",
            "the real four, so every choice set here is smaller than the one the human",
            "faced and every recall is optimistic. Both providers are scored on the same",
            "reconstructed sets, so the gap between them survives what the level does not.",
            "",
            "| provider | k | recall | 95% | decisions | battles |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in payload["closed_sheet"]:
            lines.append(
                f"| `{row['provider']}` | {row['k']} | {row['recall']:.4f} | "
                f"[{row['low']:.4f}, {row['high']:.4f}] | {row['n_decisions']:,} | "
                f"{row['n_battles']:,} |"
            )
        lines.append("")

    lines += [
        "## What this cannot tell you",
        "",
        "- **The label is noisy by construction and no amount of corpus helps.** A human",
        "  who switched because the sheet showed a Choice Scarf made a choice these",
        "  features cannot explain, and the features cannot be given the sheet without",
        "  fitting a model that cannot be served (spec section 2).",
        "- **Both providers are blindfolded on our own side.** The features are computed",
        "  on the assumed spread rather than the real one, because a replay does not",
        "  contain stat points and the trainer and the agent have to be handed the same",
        "  shape (D63). A is normally handed exact damage in play and is not here.",
        "- **A prevented move leaves no row.** Flinch, sleep and Taunt mean the `|move|`",
        "  line never appears, so the decisions measured are the ones that resolved.",
        "- **A switch carries one number about the Pokemon coming in.** Its health.",
        "  Every switch out of a slot otherwise has an identical vector, which is A's",
        "  defect too, and is the first thing to extend if B loses on switch positions.",
        "- **The split holds out players, not boards.** A held-out player's games may",
        "  have been seen from the other side.",
        "",
    ]
    return "\n".join(lines)


# -- entry point -------------------------------------------------------------


def main() -> None:
    args = parse_args()
    dex = Dex.load(args.format_id)

    with CorpusStore(args.corpus_db, logs_dir=args.corpus_logs) as store:
        floor = rating_floor(store, args.format_id, args.percentile)
        n_rated = store.conn.execute(
            "SELECT COUNT(*) FROM replays WHERE rated = 1 AND format_id LIKE ? || '%'",
            (args.format_id,),
        ).fetchone()[0]
        replays = eligible(store, args.format_id, floor, sheets=1, limit=args.limit)
        print(
            f"rating floor {floor:.0f} ({args.percentile:.0f}th percentile of "
            f"{n_rated:,} rated replays); {len(replays):,} eligible"
        )
        if not replays:
            raise SystemExit("no eligible replays; lower --percentile or scrape more")

        started = time.time()
        data, baseline = collect(store, replays, dex)
        print(
            f"{len(data):,} decisions from {data.n_battles:,} replays "
            f"in {time.time() - started:.0f}s"
        )

        closed = eligible(store, args.format_id, floor, sheets=0, limit=args.limit)
        closed_data, closed_baseline = (
            collect(store, closed, dex, revealed_moves_fallback=True) if closed else (None, None)
        )

    masks = learned.player_masks(data, seed=args.seed)
    train, validation, test = (data.subset(mask) for mask in masks)
    print(
        f"split by player: {len(train):,} train, {len(validation):,} validation, {len(test):,} test"
    )

    model, trials = choose_width(train, validation, args.hidden, args.l2, args.seed, args.max_iter)

    test_scores = {
        "uniform": uniform_scores(test),
        HeuristicPolicy.name: baseline[np.repeat(masks[2], data.sizes)],
        "learned-prior": model.score(test.x),
    }
    results = measure(test, test_scores, args.k, args.resamples, args.seed)
    describe(results)

    closed_results: list[dict[str, Any]] = []
    if closed_data is not None and closed_baseline is not None and len(closed_data):
        unseen = np.isin(closed_data.player, np.unique(train.player), invert=True)
        slice_ = closed_data.subset(unseen)
        slice_baseline = closed_baseline[np.repeat(unseen, closed_data.sizes)]
        if len(slice_):
            closed_results = measure(
                slice_,
                {
                    "uniform": uniform_scores(slice_),
                    HeuristicPolicy.name: slice_baseline,
                    "learned-prior": model.score(slice_.x),
                },
                args.k,
                args.resamples,
                args.seed,
            )
            print(f"\nclosed-sheet slice: {len(slice_):,} decisions")
            describe(closed_results)

    headline = max(args.k)
    model.metrics = {
        "test": [row for row in results if row["provider"] == "learned-prior"],
        "trials": trials,
    }
    payload: dict[str, Any] = {
        "format_id": args.format_id,
        "percentile": args.percentile,
        "floor": floor,
        "n_rated": n_rated,
        "n_replays": len(replays),
        "n_decisions": len(data),
        "n_players": int(np.unique(data.player).size),
        "mean_options": float(data.sizes.mean()) if len(data) else 0.0,
        "n_train": len(train),
        "n_validation": len(validation),
        "n_test": len(test),
        "hidden": model.hidden,
        "l2": args.l2,
        "seed": args.seed,
        "trials": trials,
        "test": results,
        "closed_sheet": closed_results,
        "verdict": verdict(results, headline),
    }
    print(f"\n{payload['verdict']}")

    model_path = args.model or MODEL_DIR / f"prior.{args.format_id}.json"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(json.dumps(model.as_json(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {model_path}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")

    if not args.no_report:
        args.report.write_text(document(payload), encoding="utf-8")
        print(f"wrote {args.report}")


if __name__ == "__main__":
    main()

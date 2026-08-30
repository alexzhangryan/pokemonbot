"""Fit the evaluation function and write its reliability diagram.

    python scripts/fit_eval.py                     # both sources, self-play primary
    python scripts/fit_eval.py --source corpus     # one of them
    python scripts/fit_eval.py --traces runs/x     # a different self-play directory
    python scripts/fit_eval.py --dry-run           # fit and report, write nothing

Writes `data/eval/weights.<format>.json`, which `champions/search/evaluate.py`
loads on import, and `docs/eval-calibration.md`, which is the reliability
diagram `docs/04-decision-engine.md` section 5 requires before the number is
read as a probability anywhere.

Both sources are fit, always, and both diagrams are written.

Self-play is preferred, when it earns it. Ladder outcomes are skill dominated
(D39: the higher-rated player won 57.4% of 1,808 rated games), so a model fit on
them is partly fitting who the players were, while self-play has both sides
played by the same agent and no such term to absorb. The corpus fit is kept
regardless, because it is the only thing that would notice self-play teaching
the evaluation the agent's own blind spots.

"When it earns it" is `shippable`, and it is not a formality. Both of the ways
self-play can fail here showed up on the first run:

- 150 battles leaves 23 in the test split, and a Platt scaling fit on 23 more.
  The result ranked positions better than the corpus fit (AUC 0.793 against
  0.760) and still scored a worse held-out log loss than a coin flip. Ranking
  and calibration are different claims and only one of them is what `win_prob`
  promises.
- Self-play between two copies of one team determined only three of the seven
  weights. Two features never vary at all (neither team carries Tailwind or a
  hazard move) and a third, `status_advantage`, varies on 291 rows out of 11,774
  because burn is the only status that matchup inflicts -- and came out at -1.34,
  the wrong sign, from 2.5% of the data.

So a weight is only kept when the source settled it, which `fit.bootstrap_weights`
measures by resampling battles rather than by any threshold on how often a
feature is nonzero. Weights the shipping source left undetermined are taken from
the other source, and the blend is then re-calibrated and re-measured, so the
reliability diagram describes the model that actually ships.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from champions.corpus.store import CorpusStore
from champions.search import fit as fitting
from champions.search import positions
from champions.search.evaluate import WEIGHTS_PATH, format_key

FORMAT_ID = "gen9championsvgc2026regmb"
DEFAULT_TRACES = Path("runs/m6-selfplay")
REPORT_PATH = Path("docs/eval-calibration.md")

#: How much larger a source must be before its weight overrules a confident
#: disagreement from the shipping source. Ten is not tuned; it is the point at
#: which "the small source learned its own team's quirk" is a better explanation
#: than "the large source is wrong", and both cases here are far past it.
CONTRADICTION_BATTLE_RATIO = 10


def load_selfplay(root: Path) -> list[positions.Position]:
    if not root.exists():
        raise SystemExit(
            f"no self-play traces in {root}. Generate some first, for example:\n"
            "  python scripts/selfplay.py 150 --trace-dir runs/m6-selfplay "
            "--agent-a oneply --agent-b oneply --team-a regmb-alpha --team-b regmb-alpha"
        )
    return positions.from_trace_dir(root)


def load_corpus(
    db: Path, logs: Path, limit: int | None, format_id: str
) -> list[positions.Position]:
    out: list[positions.Position] = []
    with CorpusStore(db, logs_dir=logs) as store:
        stored = [row for row in store.stored_logs() if row[0].startswith(format_id)]
        for stored_format, replay_id in stored[:limit]:
            log = store.read_log(stored_format, replay_id)
            out.extend(positions.from_replay_log(log, replay_id))
    return out


def fit_one(
    rows: list[positions.Position], source: str, seed: int, resamples: int
) -> dict[str, Any]:
    data = fitting.to_dataset(rows, source)
    train, validation, test = fitting.split_by_battle(data, seed=seed)
    model = fitting.calibrate(fitting.fit(train), validation)
    model.metrics = {
        "train": fitting.metrics(fitting.predict(model, train), train).as_row(),
        "test": fitting.metrics(fitting.predict(model, test), test).as_row(),
    }
    return {
        "source": source,
        "model": model,
        "data": data,
        "train": train,
        "validation": validation,
        "test": test,
        # What this source actually settled, as opposed to what it printed.
        "intervals": fitting.bootstrap_weights(train, resamples=resamples, seed=seed),
        "report": fitting.evaluate_model(model, test),
    }


def diagram(reliability: fitting.Reliability) -> list[str]:
    """The reliability diagram, as a table and as a bar per bin.

    Drawn in text on purpose. The project has no plotting dependency, the
    document is read in a terminal and in a diff as often as in a browser, and a
    twenty-character bar carries the one thing a glance needs: which way the
    model is wrong and by how much.
    """
    lines = [
        "| predicted | n | mean predicted | observed | gap | |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for b in reliability.bins:
        # Signed, predicted minus observed, so the column and the word agree.
        # `Bin.gap` is the magnitude, which read as a `+` on every row.
        signed = b.predicted - b.observed
        bar = ("#" * min(round(abs(signed) * 100), 20)) or "."
        lines.append(
            f"| {b.low:.1f}-{b.high:.1f} | {b.count} | {b.predicted:.3f} | "
            f"{b.observed:.3f} | {signed:+.3f} | `{bar}` "
            f"{'over' if signed > 0 else 'under'}confident |"
        )
    return lines


def report(
    results: list[dict[str, Any]], primary: str, borrowed: dict[str, tuple[float, str]]
) -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = [
        "# Evaluation function: calibration",
        "",
        "Generated by `scripts/fit_eval.py` (`make fit-eval`). Do not edit by hand.",
        "",
        f"Last fit: {stamp}. Shipping model: **{primary}**.",
        "",
        "`docs/04-decision-engine.md` section 5 requires a reliability diagram before",
        "this number is read as a probability anywhere. This is it. Every figure below",
        "is measured on a test split partitioned **by battle**, so no position in it",
        "shares a game with anything the weights or the calibration saw.",
        "",
    ]
    for result in results:
        model, source = result["model"], result["source"]
        reliability = result["report"]["reliability"]
        test = result["report"]["metrics"]
        out += [
            f"## {source}",
            "",
            f"{len(result['data'])} positions from {result['data'].n_battles} battles.",
            "",
            "| | value |",
            "| --- | ---: |",
            f"| test positions | {test['n']} ({test['n_battles']} battles) |",
            f"| log loss | {test['log_loss']} |",
            f"| log loss, always-base-rate | {test['base_log_loss']} |",
            f"| Brier | {test['brier']} |",
            f"| accuracy | {test['accuracy']} |",
            f"| AUC | {test['auc']} |",
            f"| **expected calibration error** | **{reliability.ece:.4f}** |",
            f"| maximum calibration error | {reliability.mce:.4f} |",
            "",
            "Weights, in log odds:",
            "",
            "| feature | weight | 95% interval |",
            "| --- | ---: | --- |",
        ]
        intervals = result["intervals"]
        open_questions = fitting.undetermined(intervals)
        is_primary = source == primary
        for name in sorted(intervals, key=lambda n: -abs(model.weights[n])):
            low, high = intervals[name]
            fitted = borrowed[name][0] if (is_primary and name in borrowed) else model.weights[name]
            if is_primary and name in borrowed:
                mark = f" *(taken from {borrowed[name][1]})*"
            elif name in open_questions:
                mark = " *(sign undetermined)*"
            else:
                mark = ""
            out.append(f"| {name} | {fitted:+.4f} | [{low:+.3f}, {high:+.3f}]{mark} |")
        out += [
            "",
            "The interval is a 95% bootstrap over **battles**, resampled with replacement.",
            "Battles rather than positions: twenty positions from one game are near",
            "duplicates, and resampling positions would report an interval roughly twenty",
            "times too narrow. A weight whose interval spans zero is one this source did not",
            "settle even the sign of, whatever the point estimate looks like beside it.",
        ]
        if open_questions:
            named = ", ".join(f"`{name}`" for name in sorted(open_questions))
            out += ["", f"**Undetermined here**: {named}."]
        if is_primary and borrowed:
            conflicting = sorted(name for name in borrowed if name not in open_questions)
            out += [
                "",
                "Weights marked *taken from* come from the other source. Two reasons, and the",
                "table above distinguishes them by whether the interval spans zero: a weight",
                "this source did not settle, or one both sources settled with **opposite**",
                "signs, where the source with at least ten times the battles wins.",
            ]
            if conflicting:
                out += [
                    "",
                    "Settled here with the opposite sign: "
                    + ", ".join(f"`{name}`" for name in conflicting)
                    + ".",
                ]
            out += [
                "",
                "The blend is re-calibrated and re-measured, so every number in this section",
                "describes the model that ships rather than the one before the substitution.",
            ]
        out += [
            "",
            f"Platt scaling, fit on the validation split: `{model.platt_a:.4f} * log_odds "
            f"{model.platt_b:+.4f}`.",
            "",
            "Free intercept, fit as a diagnostic and never applied: "
            f"`{model.free_intercept:+.4f}`.",
            "Every feature is a difference between the two sides, so a dead-even position is",
            "the zero vector and an intercept away from zero would mean the features are not",
            "the antisymmetric differences they claim to be.",
            "",
            "### Reliability",
            "",
            *diagram(reliability),
            "",
        ]
    return "\n".join(out) + "\n"


def constant_features(data: fitting.Dataset) -> set[str]:
    """Features that take one value everywhere in a source.

    A weight for one of these is not a measurement. It reads exactly like the
    others in a table, which is the reason to name them.
    """
    return {name for i, name in enumerate(data.names) if float(np.ptp(data.x[:, i])) == 0.0}


def shippable(result: dict[str, Any]) -> bool:
    """Whether a fit is worth preferring over saying nothing.

    One test, and it is the weakest one that still means something: the model
    must beat, on held-out battles, the constant that always predicts the base
    rate. A model that loses to that has not learned the position, and shipping
    it would put a number the coach reports as a probability and the search
    backs values through behind a claim of calibration.

    This is a guard against a specific way M6 could go wrong quietly. The first
    self-play fit here ranked positions *better* than the corpus fit (AUC 0.793
    against 0.760) while scoring a worse held-out log loss than a coin flip,
    because 150 battles leaves about 22 in the test split and the Platt scaling
    was fit on 22 more. Good ranking and bad calibration look identical in a
    weight table.
    """
    test = result["report"]["metrics"]
    return bool(test["log_loss"] < test["base_log_loss"])


def blend(primary: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, tuple[float, str]]:
    """Replace the weights the shipping source did not settle.

    "Did not settle" is `fitting.undetermined`: the bootstrap interval over
    battles spans zero, so the source has not even fixed the sign. Two ways that
    happens here, and self-play hits both:

    - The feature never varies. Neither checked-in team carries Tailwind or a
      hazard move, so `speed_control` and `hazard_advantage` are zero on every
      self-play position. No number of games fixes that, and shipping the zero
      means shipping an agent that believes Tailwind is worth nothing against an
      opponent whose team may be built around it.
    - The feature varies too rarely to mean anything. `status_advantage` was
      nonzero on 291 of 11,774 rows, because burn is the only status that
      matchup inflicts, and came out at -1.34 with an interval of [-3.54, +0.90]
      -- the wrong sign, stated confidently, from 2.5% of the data.

    Replacing a weight that varies changes predictions on the very positions the
    reliability diagram was measured over, so the caller re-calibrates and
    re-measures afterwards. The shipped numbers describe the shipped model.

    Mutates the primary model in place and returns what it changed.
    """
    borrowed: dict[str, tuple[float, str]] = {}
    open_questions = fitting.undetermined(primary["intervals"])
    for other in results:
        if other is primary:
            continue
        settled = set(other["intervals"]) - fitting.undetermined(other["intervals"])
        take = (open_questions & settled) | contradictions(primary, other)
        for name in sorted((take & settled) - set(borrowed)):
            value = other["model"].weights[name]
            primary["model"].weights[name] = value
            borrowed[name] = (value, other["source"])
    return borrowed


def contradictions(primary: dict[str, Any], other: dict[str, Any]) -> set[str]:
    """Weights where both sources are confident and they disagree on the sign.

    Not the same failure as an undetermined weight, and worth separating.
    `boost_advantage` came out at -0.51, interval [-0.952, -0.114], from self-play
    on the mirror matchup -- confidently negative, meaning the agent should avoid
    boosting itself -- against +0.12, interval [+0.098, +0.137], from 17,500
    corpus battles. Disjoint intervals: this is two sources measuring different
    things, not one of them being noisy.

    The mechanism is the team. Milotic has Competitive, so a large positive boost
    total on our side is most often the *consequence* of the opponent landing
    Intimidate or Icy Wind on us. In that matchup "we have +2" really does
    predict losing, and it is an artifact of six specific Pokemon rather than
    anything true about doubles.

    So the larger source wins, and only when it is larger by a wide margin --
    a narrow source learning its own team's quirk is the failure being guarded
    against, and two sources of comparable size disagreeing is a different
    problem that this rule would paper over.
    """
    if other["data"].n_battles < CONTRADICTION_BATTLE_RATIO * primary["data"].n_battles:
        return set()
    out = set()
    for name, (low, high) in primary["intervals"].items():
        other_low, other_high = other["intervals"][name]
        disjoint = high < other_low or other_high < low
        # Opposite sides of zero, not merely far apart. Two sources putting
        # `hp_advantage` at +1.31 and +0.62 have disjoint intervals and agree
        # about everything that matters; treating that as a contradiction hands
        # the corpus every weight and throws away the reason self-play is
        # preferred in the first place.
        opposed = (high < 0 < other_low) or (other_high < 0 < low)
        if disjoint and opposed:
            out.add(name)
    return out


def describe(result: dict[str, Any]) -> str:
    test = result["report"]["metrics"]
    return (
        f"held-out log loss {test['log_loss']} against {test['base_log_loss']} "
        f"for the base rate, over {test['n_battles']} test battles"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("both", "selfplay", "corpus"), default="both")
    parser.add_argument("--traces", type=Path, default=DEFAULT_TRACES)
    parser.add_argument("--corpus-db", type=Path, default=Path("data/corpus.sqlite"))
    parser.add_argument("--corpus-logs", type=Path, default=Path("data/replays"))
    parser.add_argument("--corpus-limit", type=int, default=None)
    parser.add_argument("--format-id", default=FORMAT_ID)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--l2", type=float, default=fitting.DEFAULT_L2)
    parser.add_argument(
        "--resamples",
        type=int,
        default=200,
        help="bootstrap resamples per source, over battles (0 is not allowed: the "
        "intervals decide which weights a source has earned)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    wanted = ("selfplay", "corpus") if args.source == "both" else (args.source,)
    results = []
    for source in wanted:
        rows = (
            load_selfplay(args.traces)
            if source == "selfplay"
            else load_corpus(args.corpus_db, args.corpus_logs, args.corpus_limit, args.format_id)
        )
        if not rows:
            print(f"{source}: no positions, skipping")
            continue
        results.append(fit_one(rows, source, args.seed, args.resamples))
        result = results[-1]
        print(
            f"{source}: {len(result['data'])} positions, {result['data'].n_battles} battles -> "
            f"log loss {result['report']['metrics']['log_loss']} "
            f"(base {result['report']['metrics']['base_log_loss']}), "
            f"ECE {result['report']['reliability'].ece:.4f}, "
            f"AUC {result['report']['metrics']['auc']}"
        )

    if not results:
        raise SystemExit("nothing to fit")

    # Self-play ships when it is present and when it has earned it. See the
    # module docstring for the first half and `shippable` for the second.
    preferred = next((r for r in results if r["source"] == "selfplay"), results[0])
    primary = preferred
    if not shippable(preferred):
        fallback = next((r for r in results if r is not preferred and shippable(r)), None)
        note = describe(preferred)
        if fallback is None:
            raise SystemExit(f"nothing fit well enough to ship: {note}")
        print(f"not shipping {preferred['source']} ({note}); shipping {fallback['source']} instead")
        primary = fallback

    borrowed = blend(primary, results)
    if borrowed:
        for name, (value, lender) in borrowed.items():
            low, high = primary["intervals"][name]
            why = "undetermined" if low <= 0 <= high else "confidently the other sign"
            print(
                f"  {name}: {value:+.4f} from {lender}; {primary['source']} had it "
                f"{why} at 95% [{low:+.3f}, {high:+.3f}]"
            )
        # The blend is a different model from the one measured above, so it is
        # calibrated and measured again. Reporting the pre-blend numbers for a
        # post-blend model is exactly the kind of unearned claim `docs/04`
        # section 5 exists to stop.
        fitting.calibrate(primary["model"], primary["validation"])
        primary["report"] = fitting.evaluate_model(primary["model"], primary["test"])
        # The metrics travel into the weights file beside the Platt terms, so
        # they have to be refreshed together or the file describes two models.
        primary["model"].metrics = {
            "train": fitting.metrics(
                fitting.predict(primary["model"], primary["train"]), primary["train"]
            ).as_row(),
            "test": primary["report"]["metrics"],
        }
        shipped = primary["report"]["metrics"]
        print(
            f"  shipped blend: log loss {shipped['log_loss']} (base {shipped['base_log_loss']}), "
            f"ECE {primary['report']['reliability'].ece:.4f}, AUC {shipped['auc']}"
        )
        if not shippable(primary):
            raise SystemExit(f"the blend is worse than the base rate: {describe(primary)}")

    if args.dry_run:
        print("dry run: nothing written")
        return

    path = WEIGHTS_PATH(args.format_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = primary["model"].as_json()
    payload["format_id"] = args.format_id
    payload["source"] = primary["source"]
    payload["fitted_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    payload["feature_order"] = list(primary["data"].names)
    payload["borrowed"] = {name: lender for name, (_, lender) in borrowed.items()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    REPORT_PATH.write_text(report(results, primary["source"], borrowed), encoding="utf-8")
    print(f"wrote {path} ({format_key(args.format_id)}) and {REPORT_PATH}")


if __name__ == "__main__":
    main()

"""Calibrate the coach's label bands and check that its loss means something.

    python scripts/calibrate_coach.py                  # 80 rated open-sheet games, both sides
    python scripts/calibrate_coach.py --limit 200      # more of them
    python scripts/calibrate_coach.py --write-bands    # also write data/eval/coach-bands.*.json

Writes `docs/coach-calibration.md` and `data/eval/coach-calibration.<format>.json`,
generated rather than hand written, the way `docs/eval-calibration.md` and
`docs/pruning-guard.md` are.

Two questions, both from `docs/06-coach-and-evaluation.md`.

**Section 2: where do the bands go?** The hand-set cutoffs in
`champions/coach/classify.py` (five and fifteen points) were picked before any
game was reviewed. The rule here, fixed before the numbers: among the
off-support decisions of the top rating quartile, half are inaccuracies,
thirty-five percent are mistakes and fifteen percent are blunders. Strong play
is the reference population, so a strong player's ordinary off-support loss is
an inaccuracy and only their rare large ones are blunders. The fitted cutoffs
are the 50th and 85th percentiles of that distribution.

**Section 8: does ex-ante loss measure skill?** If it does, it should fall
with rating, and ex-post loss should fall much less, since luck is not skill.
Reported as Spearman correlations of per-game mean loss with rating, with
bootstrap intervals over games, and as mean loss per rating quartile. The
dissociation -- ex-ante apart from zero, ex-post not, or ex-ante the more
negative with the interval on the difference apart from zero -- is the
strongest evidence available that the number means what it claims.

Every game is reviewed from both sides with the open sheet as the information
state (spec section 2), so the numbers are about human decisions under the
information the human had. About a second a turn; the default sample is
roughly half an hour.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.coach import classify, decisions  # noqa: E402
from champions.coach.analyze import analyze_game  # noqa: E402
from champions.corpus.replay import parse_replay  # noqa: E402
from champions.corpus.store import CorpusStore  # noqa: E402
from champions.dex.loader import Dex  # noqa: E402

PLAY_FORMAT = "gen9championsvgc2026regmc"
CORPUS_FORMAT = "gen9championsvgc2026regmcbo3"
DEFAULT_DB = Path("data/corpus.sqlite")
DEFAULT_LOGS = Path("data/replays")
REPORT_PATH = Path("docs/coach-calibration.md")

#: The rule of the module docstring: the label shares among a strong player's
#: off-support decisions.
INACCURACY_SHARE = 0.50
MISTAKE_SHARE = 0.35
BOOTSTRAP = 1000


@dataclass(frozen=True)
class GameView:
    replay_id: str
    side: str
    rating: int
    decisions: int
    scored: int
    ex_ante_mean: float | None
    ex_post_mean: float | None
    #: Every scored decision's ex-ante loss, for the band fit.
    losses: tuple[float, ...]
    off_support: tuple[float, ...]
    labels: dict[str, int]


def eligible(store: CorpusStore, format_id: str) -> list[tuple[str, str, Any]]:
    """`(replay_id, log, record)` for rated, decided, open-sheet games."""
    out = []
    for _, replay_id in store.stored_logs(format_id):
        log = store.read_log(format_id, replay_id)
        record = parse_replay(replay_id, log)
        if not record.rated or not record.sheets_revealed or record.result == "tie":
            continue
        if any(r is None for r in record.ratings):
            continue
        out.append((replay_id, log, record))
    return out


def review(replay_id: str, log: str, side: str, rating: int, dex: Dex) -> GameView:
    game = decisions.from_replay(log, replay_id, side, dex)
    analysis = analyze_game(game, dex)
    scored = [t for t in analysis.turns if t.ex_ante_loss is not None]
    losses = tuple(float(t.ex_ante_loss or 0.0) for t in scored)
    post = [float(t.ex_post_loss) for t in scored if t.ex_post_loss is not None]
    return GameView(
        replay_id=replay_id,
        side=side,
        rating=rating,
        decisions=len(analysis.turns),
        scored=len(scored),
        ex_ante_mean=float(np.mean(losses)) if losses else None,
        ex_post_mean=float(np.mean(post)) if post else None,
        losses=losses,
        off_support=tuple(x for x in losses if x > classify.LOSS_EPS),
        labels={label: sum(1 for t in scored if t.label == label) for label in classify.LABELS},
    )


# -- the numbers -------------------------------------------------------------


def quartiles(views: Sequence[GameView]) -> list[tuple[str, list[GameView]]]:
    ratings = sorted(v.rating for v in views)
    if len(ratings) < 4:
        return [("all", list(views))]
    cuts = [float(np.percentile(ratings, q)) for q in (25, 50, 75)]
    bands: list[tuple[str, list[GameView]]] = [
        (f"<= {cuts[0]:.0f}", [v for v in views if v.rating <= cuts[0]]),
        (f"{cuts[0]:.0f} to {cuts[1]:.0f}", [v for v in views if cuts[0] < v.rating <= cuts[1]]),
        (f"{cuts[1]:.0f} to {cuts[2]:.0f}", [v for v in views if cuts[1] < v.rating <= cuts[2]]),
        (f"> {cuts[2]:.0f}", [v for v in views if v.rating > cuts[2]]),
    ]
    return [(name, group) for name, group in bands if group]


def bootstrap_mean(values: Sequence[float], rng: np.random.Generator) -> tuple[float, float, float]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return float("nan"), float("nan"), float("nan")
    means = [float(rng.choice(arr, arr.size).mean()) for _ in range(BOOTSTRAP)]
    return float(arr.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def spearman(x: Any, y: Any) -> float:
    """Rank correlation without scipy, so this runs wherever the coach does."""
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if xa.size < 3 or np.all(xa == xa[0]) or np.all(ya == ya[0]):
        return float("nan")
    rx, ry = xa.argsort().argsort().astype(float), ya.argsort().argsort().astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


def bootstrap_spearman(x: Any, y: Any, rng: np.random.Generator) -> tuple[float, float, float]:
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    point = spearman(xa, ya)
    draws = []
    for _ in range(BOOTSTRAP):
        idx = rng.integers(0, xa.size, xa.size)
        draws.append(spearman(xa[idx], ya[idx]))
    clean = [d for d in draws if not np.isnan(d)]
    if not clean:
        return point, float("nan"), float("nan")
    return point, float(np.percentile(clean, 2.5)), float(np.percentile(clean, 97.5))


def fitted_bands(top: Sequence[GameView]) -> classify.Bands | None:
    losses = [x for v in top for x in v.off_support]
    if len(losses) < 20:
        return None
    return classify.Bands(
        inaccuracy=round(float(np.percentile(losses, 100 * INACCURACY_SHARE)), 4),
        mistake=round(float(np.percentile(losses, 100 * (INACCURACY_SHARE + MISTAKE_SHARE))), 4),
        source=(
            f"calibrated on {len(top)} top-quartile game views, {len(losses)} off-support decisions"
        ),
    )


def label_shares(views: Sequence[GameView], bands: classify.Bands) -> dict[str, float]:
    losses = [x for v in views for x in v.off_support]
    if not losses:
        return {
            label: 0.0
            for label in (classify.INACCURACY_LABEL, classify.MISTAKE_LABEL, classify.BLUNDER)
        }
    arr = np.asarray(losses)
    n = arr.size
    return {
        classify.INACCURACY_LABEL: float(np.sum(arr < bands.inaccuracy) / n),
        classify.MISTAKE_LABEL: float(
            np.sum((arr >= bands.inaccuracy) & (arr < bands.mistake)) / n
        ),
        classify.BLUNDER: float(np.sum(arr >= bands.mistake) / n),
    }


def measure(views: Sequence[GameView], seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    groups = quartiles(views)
    per_band = []
    for name, group in groups:
        ante = [v.ex_ante_mean for v in group if v.ex_ante_mean is not None]
        post = [v.ex_post_mean for v in group if v.ex_post_mean is not None]
        per_band.append(
            {
                "band": name,
                "game_views": len(group),
                "decisions": sum(v.scored for v in group),
                "ex_ante": bootstrap_mean(ante, rng),
                "ex_post": bootstrap_mean(post, rng),
                "hand_set_shares": label_shares(group, classify.HAND_SET),
            }
        )

    with_ante = [v for v in views if v.ex_ante_mean is not None]
    with_post = [v for v in views if v.ex_post_mean is not None]
    ante_corr = bootstrap_spearman(
        [v.rating for v in with_ante], [v.ex_ante_mean or 0.0 for v in with_ante], rng
    )
    post_corr = bootstrap_spearman(
        [v.rating for v in with_post], [v.ex_post_mean or 0.0 for v in with_post], rng
    )
    both = [v for v in views if v.ex_ante_mean is not None and v.ex_post_mean is not None]
    diffs = []
    ratings = np.asarray([v.rating for v in both], dtype=float)
    ante_arr = np.asarray([v.ex_ante_mean for v in both], dtype=float)
    post_arr = np.asarray([v.ex_post_mean for v in both], dtype=float)
    for _ in range(BOOTSTRAP):
        idx = rng.integers(0, len(both), len(both)) if both else np.array([], dtype=int)
        a = spearman(ratings[idx], ante_arr[idx]) if both else float("nan")
        p = spearman(ratings[idx], post_arr[idx]) if both else float("nan")
        if not (np.isnan(a) or np.isnan(p)):
            diffs.append(a - p)
    difference = (
        (
            ante_corr[0] - post_corr[0],
            float(np.percentile(diffs, 2.5)),
            float(np.percentile(diffs, 97.5)),
        )
        if diffs
        else (float("nan"), float("nan"), float("nan"))
    )

    top = groups[-1][1] if groups else []
    fitted = fitted_bands(top)
    return {
        "game_views": len(views),
        "games": len({v.replay_id for v in views}),
        "decisions": sum(v.scored for v in views),
        "bands_by_rating": per_band,
        "spearman_ex_ante": ante_corr,
        "spearman_ex_post": post_corr,
        "spearman_difference": difference,
        "hand_set": classify.HAND_SET.as_dict(),
        "fitted": fitted.as_dict() if fitted else None,
        "fitted_shares_all": label_shares(views, fitted) if fitted else None,
        "hand_set_shares_all": label_shares(views, classify.HAND_SET),
    }


# -- the document ------------------------------------------------------------


def _ci(triple: Sequence[float], scale: float = 100.0, digits: int = 1) -> str:
    point, low, high = triple
    if np.isnan(point):
        return "n/a"
    return f"{scale * point:.{digits}f} [{scale * low:.{digits}f}, {scale * high:.{digits}f}]"


def render(result: dict[str, Any], started: str, finished: str, seed: int) -> str:
    lines = [
        "# The coach, calibrated",
        "",
        "Generated by `scripts/calibrate_coach.py`. Do not edit by hand.",
        "",
        f"Run started {started}, finished {finished}, seed {seed}. "
        f"{result['games']} rated open-sheet Bo3 games reviewed from both sides: "
        f"{result['game_views']} game views, {result['decisions']} scored decisions.",
        "",
        "## What this measures",
        "",
        "`docs/06-coach-and-evaluation.md` section 2 says the label bands should be calibrated "
        "against rating bands rather than picked by hand, and section 8 says ex-ante loss should "
        "fall with rating while ex-post loss should not, since luck is not skill. Both are "
        "measured here on human games reviewed with the open sheet as the information state "
        "(`docs/specs/2026-09-13-coach.md` section 2). Losses are in win-probability points; "
        "intervals are bootstrap 95% over game views.",
        "",
        "## Loss by rating quartile",
        "",
        "| rating | game views | decisions | ex-ante loss per decision "
        "| ex-post loss per decision |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for band in result["bands_by_rating"]:
        lines.append(
            f"| {band['band']} | {band['game_views']} | {band['decisions']} | "
            f"{_ci(band['ex_ante'])} | {_ci(band['ex_post'])} |"
        )
    lines += [
        "",
        "## Does the loss track skill?",
        "",
        f"Spearman correlation of per-game mean loss with rating: ex-ante "
        f"{_ci(result['spearman_ex_ante'], 1.0, 3)}, ex-post "
        f"{_ci(result['spearman_ex_post'], 1.0, 3)}; difference (ex-ante minus ex-post) "
        f"{_ci(result['spearman_difference'], 1.0, 3)}.",
        "",
        _verdict(result),
        "",
        "## The bands",
        "",
        "The rule, fixed before the numbers: among the top quartile's off-support decisions, "
        f"{INACCURACY_SHARE:.0%} are inaccuracies, {MISTAKE_SHARE:.0%} mistakes and the rest "
        "blunders; the cutoffs are the matching percentiles of that distribution.",
        "",
        "| bands | inaccuracy below | mistake below | share inaccuracy | share mistake "
        "| share blunder |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    hand = result["hand_set_shares_all"]
    hand_bands = result["hand_set"]
    lines.append(
        f"| hand-set | {100 * hand_bands['inaccuracy']:.1f} | {100 * hand_bands['mistake']:.1f} | "
        f"{hand['inaccuracy']:.0%} | {hand['mistake']:.0%} | {hand['blunder']:.0%} |"
    )
    if result["fitted"]:
        fit = result["fitted_shares_all"]
        fit_bands = result["fitted"]
        lines.append(
            f"| fitted | {100 * fit_bands['inaccuracy']:.1f} | {100 * fit_bands['mistake']:.1f} | "
            f"{fit['inaccuracy']:.0%} | {fit['mistake']:.0%} | {fit['blunder']:.0%} |"
        )
        lines += ["", f"Fitted from: {result['fitted']['source']}."]
    else:
        lines += ["", "Too few top-quartile off-support decisions to fit bands (needs 20)."]
    lines += [
        "",
        "Shares are over every off-support decision in the sample. The fitted bands are applied "
        "only when written to `data/eval/coach-bands.<format>.json` (`--write-bands`), which "
        "`champions/coach/classify.py` reads at import; until then the coach reports hand-set.",
    ]
    return "\n".join(lines) + "\n"


def _verdict(result: dict[str, Any]) -> str:
    ante = result["spearman_ex_ante"]
    post = result["spearman_ex_post"]
    diff = result["spearman_difference"]
    if np.isnan(ante[0]):
        return "**No verdict**: too few games."
    ante_apart = ante[2] < 0
    post_apart = post[2] < 0
    diff_apart = diff[2] < 0
    if ante_apart and not post_apart:
        return (
            "**The dissociation holds**: ex-ante loss falls with rating with the interval apart "
            "from zero, and ex-post loss does not. The number measures something luck does not."
        )
    if ante_apart and diff_apart:
        return (
            "**The dissociation holds in degree**: both losses fall with rating, ex-ante more so, "
            "with the interval on the difference apart from zero."
        )
    if ante_apart:
        return (
            "**Ex-ante loss tracks rating**, but ex-post loss does too and the difference is not "
            "apart from zero at this sample; the dissociation is not demonstrated."
        )
    return (
        "**Not demonstrated**: ex-ante loss is not apart from zero against rating at this sample. "
        "Either the sample is too small, the information state is wrong for these players, or "
        "the loss does not measure skill on this ladder."
    )


# -- main --------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--format", default=CORPUS_FORMAT, help="the corpus format to read")
    parser.add_argument(
        "--play-format", default=PLAY_FORMAT, help="the format the bands are keyed by"
    )
    parser.add_argument(
        "--limit", type=int, default=80, help="games (each reviewed from both sides)"
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument(
        "--write-bands", action="store_true", help="write the fitted bands for the coach to use"
    )
    args = parser.parse_args()

    started = datetime.now(UTC).isoformat()
    dex = Dex.load(args.play_format)
    with CorpusStore(args.db, args.logs) as store:
        games = eligible(store, args.format)
    print(f"{len(games)} eligible games in the corpus", flush=True)
    random.Random(args.seed).shuffle(games)
    games = games[: args.limit]

    views: list[GameView] = []
    clock = time.perf_counter()
    for index, (replay_id, log, record) in enumerate(games, 1):
        for side, rating in zip(("p1", "p2"), record.ratings, strict=True):
            try:
                views.append(review(replay_id, log, side, int(rating), dex))
            except Exception as exc:  # one bad log must not cost the run
                print(f"  {replay_id} {side}: skipped ({exc!r})", flush=True)
        elapsed = time.perf_counter() - clock
        print(f"{index}/{len(games)} {replay_id} ({elapsed:.0f}s)", flush=True)

    result = measure(views, args.seed)
    finished = datetime.now(UTC).isoformat()
    document = render(result, started, finished, args.seed)
    args.report.write_text(document, encoding="utf-8")
    print(document)

    json_path = args.json or Path("data/eval") / f"coach-calibration.{args.play_format}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "started": started,
        "finished": finished,
        "seed": args.seed,
        "corpus_format": args.format,
        "views": [asdict(v) for v in views],
        **{k: v for k, v in result.items() if k != "views"},
    }
    json_path.write_text(json.dumps(payload, indent=1, default=float), encoding="utf-8")
    print(f"json: {json_path}", file=sys.stderr)

    if args.write_bands and result["fitted"]:
        bands_path = classify.BANDS_PATH(args.play_format)
        bands_path.write_text(json.dumps(result["fitted"], indent=1), encoding="utf-8")
        print(f"bands: {bands_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

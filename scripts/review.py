"""Review a finished game: the coach (`docs/specs/2026-09-13-coach.md`).

    python scripts/review.py traces/foo.jsonl              # one of our traces
    python scripts/review.py runs/m8-gate                  # the newest trace under a directory
    python scripts/review.py game.log --side p1            # a saved replay log
    python scripts/review.py gen9championsvgc2026regmc-123 --side alice   # from the replay site
    python scripts/review.py https://replay.pokemonshowdown.com/gen9championsvgc2026regmc-123

Writes `<stem>.review.jsonl` (the trace with the analysis overlay, which the
viewer can open) and `<stem>.review.md` (the review as a document) beside the
input, or under `--out`, and prints the document.

`--opponent-team FILE` supplies the opponent's registered sets for the
ex-post half of a trace review (a mirror match's own team file); a replay with
Open Team Sheets needs nothing, the sheet is in the log. `--llm` asks the
language model of `champions.search.llm.client_from_env` to write the critical
turns' prose; without it the writeup is the template, which says the same
things in plainer sentences.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.belief.evaluate import truth_from_team_file  # noqa: E402
from champions.belief.priors import SetPrior  # noqa: E402
from champions.coach import decisions, report  # noqa: E402
from champions.coach.analyze import COLUMN_BUDGET, analyze_game, prior_models  # noqa: E402
from champions.coach.explain import explain  # noqa: E402
from champions.corpus.scrape import HttpReplayClient  # noqa: E402
from champions.dex.loader import Dex  # noqa: E402
from champions.search.positions import read_events  # noqa: E402
from champions.trace.schema import TraceEvent  # noqa: E402
from champions.trace.validate import validate_events  # noqa: E402

FORMAT_ID = "gen9championsvgc2026regmc"

_REPLAY_ID = re.compile(r"(gen\d[a-z0-9]*-\d+(?:-[a-z0-9]+)?)\s*$")


def latest_trace(root: Path) -> Path | None:
    found = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    found = [p for p in found if ".review." not in p.name]
    return found[0] if found else None


def replay_id_of(text: str) -> str | None:
    """`gen9championsvgc2026regmc-123` out of an id, a URL, or a `.log` URL."""
    match = _REPLAY_ID.search(text.removesuffix(".log").removesuffix(".json"))
    return match.group(1) if match else None


def load_game(
    target: str,
    side: str | None,
    dex: Dex,
    format_id: str,
    opponent_team: Path | None,
) -> tuple[decisions.Game, Path]:
    """The game and the path its review is written beside."""
    path = Path(target)
    if path.is_dir():
        found = latest_trace(path)
        if found is None:
            raise SystemExit(f"no .jsonl traces under {path}/")
        path = found

    if path.is_file() and path.suffix == ".jsonl":
        events = read_events(path)
        post = None
        if opponent_team is not None:
            post = truth_from_team_file(opponent_team.read_text(encoding="utf-8"))
        return decisions.from_trace(events, post_truths=post), path

    if path.is_file():
        log = path.read_text(encoding="utf-8")
        battle_id = replay_id_of(path.stem) or path.stem
        return decisions.from_replay(log, battle_id, side or "p1", dex), path

    replay_id = replay_id_of(target)
    if replay_id is None:
        raise SystemExit(f"{target!r} is not a trace, a log file, or a replay id or URL")
    client = HttpReplayClient()
    try:
        fetched = client.fetch_log(replay_id)
    finally:
        client.close()
    if fetched is None:
        raise SystemExit(f"could not fetch replay {replay_id}")
    out_dir = Path("traces") / "reviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = out_dir / f"{replay_id}.log"
    saved.write_text(fetched, encoding="utf-8")
    return decisions.from_replay(fetched, replay_id, side or "p1", dex), saved


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "target", help="a trace .jsonl, a directory of them, a replay .log, or a replay id/URL"
    )
    parser.add_argument("--side", help="p1, p2, or a player name (replays only; default p1)")
    parser.add_argument("--format", default=FORMAT_ID)
    parser.add_argument(
        "--opponent-team",
        type=Path,
        help="team export for the ex-post half of a trace review",
    )
    parser.add_argument("--k", type=int, default=COLUMN_BUDGET, help="opponent column budget")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="write the critical turns' prose with the language model",
    )
    parser.add_argument("--out", type=Path, help="directory for the .review.jsonl and .review.md")
    parser.add_argument("--quiet", action="store_true", help="do not print the document")
    parser.add_argument(
        "--no-prior",
        action="store_true",
        help="review against revealed moves only, even when the corpus prior is built",
    )
    args = parser.parse_args()

    dex = Dex.load(args.format)
    game, source_path = load_game(args.target, args.side, dex, args.format, args.opponent_team)
    # Where no truth table exists, the corpus prior is the information state
    # the belief agent played under (D85); without it the review would solve
    # turn one against an opponent doing nothing.
    prior = None if args.no_prior else SetPrior.load_if_built()
    ante = post = None
    if prior is not None:
        if not game.ante_truths:
            ante = prior_models(dex, prior)
        if not game.post_truths:
            post = ante or prior_models(dex, prior)
    analysis = analyze_game(game, dex, k=args.k, ante=ante, post=post)

    client = None
    if args.llm:
        from champions.search.llm import client_from_env

        client = client_from_env()
    critical = {c["turn"] for c in analysis.summary["critical_by_loss"]}
    critical |= {c["turn"] for c in analysis.summary["critical_by_drop"]}
    explain(analysis, critical, client)

    events = report.overlay(game, analysis)
    problems = validate_events([TraceEvent.model_validate(e) for e in events])
    if problems:
        raise SystemExit("the overlay does not validate: " + "; ".join(problems))

    out_dir = args.out or source_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = source_path.name.removesuffix(".jsonl").removesuffix(".log")
    overlay_path = out_dir / f"{stem}.review.jsonl"
    document_path = out_dir / f"{stem}.review.md"
    with overlay_path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(TraceEvent.model_validate(event).to_line())
    document = report.markdown(game, analysis)
    document_path.write_text(document, encoding="utf-8")

    if not args.quiet:
        print(document)
    print(f"overlay:  {overlay_path}", file=sys.stderr)
    print(f"document: {document_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

"""Distil the corpus into the prior on what kind of turn a player plays (D91).

    python scripts/build_action_prior.py
    python scripts/build_action_prior.py --format gen9championsvgc2026regmcbo3 \
        --play-format gen9championsvgc2026regmc

Reads every stored replay of the corpus format, reconstructs both players'
joint actions turn by turn with the coach's replay reader
(`champions.coach.decisions.from_replay`), names each action's kind
(`champions.search.kinds.action_kind`: which slots attack, protect, use Fake
Out, switch) and counts the kinds by turn bucket. Writes
`data/policy/actionkinds.<play format>.json`, which `load_kind_prior` reads,
and `docs/action-kinds.md`, the table.

A slot that did nothing (flinched, asleep, the log said nothing) is not a
decision, so a kind containing `none` is dropped before the rates are taken;
single-slot kinds from the endgame are kept as their own kinds, since a
column set with one living slot offers only those.

Nothing here is a claim about the ladder. The corpus is Bo3 with open team
sheets, where a player knows what they are switching into; the first 101
ladder games, Bo1 without sheets, switched on 29 percent of first turns and
3 to 10 percent afterwards against the corpus's 23 and 12 to 29. The prior
is pinned with a weight below one for that reason, and the ladder analysis
reads the implied kind mass against the realised one each cycle.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.coach import decisions  # noqa: E402
from champions.corpus.replay import parse_replay  # noqa: E402
from champions.corpus.store import CorpusStore  # noqa: E402
from champions.dex.loader import Dex  # noqa: E402
from champions.formats import BO3_FORMAT_ID, FORMAT_ID  # noqa: E402
from champions.search.kinds import BUCKETS, action_kind, bucket, path_for  # noqa: E402

DEFAULT_DB = Path("data/corpus.sqlite")
DEFAULT_LOGS = Path("data/replays")
REPORT_PATH = Path("docs/action-kinds.md")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--format", default=BO3_FORMAT_ID, help="the corpus format to read")
    parser.add_argument("--play-format", default=FORMAT_ID, help="the format the prior is keyed by")
    parser.add_argument("--limit", type=int, default=None, help="replays to read (default: all)")
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    dex = Dex.load(args.play_format)
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    replays = 0
    sides = 0
    skipped = 0
    with CorpusStore(args.db, args.logs) as store:
        stored = list(store.stored_logs(args.format))
        if args.limit:
            stored = stored[: args.limit]
        for _, replay_id in stored:
            log = store.read_log(args.format, replay_id)
            record = parse_replay(replay_id, log)
            if record.result == "tie":
                continue
            replays += 1
            for side in ("p1", "p2"):
                try:
                    game = decisions.from_replay(log, replay_id, side, dex)
                except Exception:  # one bad log must not cost the run
                    skipped += 1
                    continue
                sides += 1
                for decision in game.decisions:
                    if decision.played is None:
                        continue
                    kind = action_kind(decision.rows[decision.played])
                    if "none" in kind.split("+"):
                        continue
                    counts[bucket(decision.turn)][kind] += 1

    buckets = {}
    totals = {}
    for b in BUCKETS:
        total = sum(counts[b].values())
        totals[b] = total
        buckets[b] = (
            {k: round(v / total, 4) for k, v in sorted(counts[b].items(), key=lambda kv: -kv[1])}
            if total
            else {}
        )

    out = {
        "format_id": args.play_format,
        "corpus_format": args.format,
        "written_at": datetime.now(UTC).isoformat(),
        "replays": replays,
        "sides": sides,
        "skipped_sides": skipped,
        "counts": totals,
        "buckets": buckets,
    }
    path = path_for(args.play_format)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    kinds = sorted(
        {k for b in BUCKETS for k in buckets[b]},
        key=lambda k: -sum(buckets[b].get(k, 0) for b in BUCKETS),
    )
    lines = [
        "# The kinds of turn people play",
        "",
        "Generated by `scripts/build_action_prior.py`. Do not edit by hand.",
        "",
        f"Source: `{args.format}`, {replays} replays, {sides} player-sides, "
        f"written {out['written_at'][:10]}.",
        f"Written to `{path.as_posix()}`, read by `champions.search.kinds.load_kind_prior` (D91).",
        "",
        "A joint action's kind names what each slot did: attack, protect, fakeout,",
        "or switch, sorted so order does not matter. Rates are per turn bucket over",
        "every decision of every player in the corpus; a slot that did nothing is",
        "not a decision and is left out. Single-slot kinds are the endgame.",
        "",
        "| kind | " + " | ".join(f"turn {b} (n={totals[b]})" for b in BUCKETS) + " |",
        "| --- | " + " | ".join("---:" for _ in BUCKETS) + " |",
    ]
    for k in kinds:
        lines.append(
            f"| `{k}` | " + " | ".join(f"{100 * buckets[b].get(k, 0):.1f}%" for b in BUCKETS) + " |"
        )
    lines += [
        "",
        "## How it is used",
        "",
        "`champions.search.matrix.solve_constrained` pins the column player's kind",
        "marginals to these rates with weight `champions.search.kinds.PRIOR_WEIGHT`",
        "and leaves the choice within a kind adversarial. Before D91 the plain",
        "equilibrium put 30 to 68 percent of the opponent's mass on a line with a",
        "Protect in it, because in a one-turn model a Protect is free; on the",
        "ladder the opponent protected on 4 to 14 percent of turns.",
        "",
    ]
    args.report.write_text("\n".join(lines), encoding="utf-8")
    print(f"{replays} replays, {sides} sides ({skipped} skipped) -> {path}")
    for b in BUCKETS:
        print(
            f"  turn {b} (n={totals[b]}): "
            + ", ".join(f"{k} {100 * v:.1f}%" for k, v in list(buckets[b].items())[:6])
        )


if __name__ == "__main__":
    main()

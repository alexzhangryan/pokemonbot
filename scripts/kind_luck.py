"""Mean luck by the kind of our line, on corpus games with open sheets (D94).

    python scripts/kind_luck.py --limit 60
    python scripts/kind_luck.py --limit 60 --json data/policy/kindluck.<format>.json

The coach's luck is the model's expected value of the played cell, given the
opponent's realised line, minus the evaluation of the position that
followed: positive means the model expected more than it got. On the ladder
it mixes two things, the belief's errors about the opponent's sets and the
one-turn model's own. On a corpus game with open team sheets the sets are
known, so what is left is the model's.

Grouped by the kind of the player's own line (`champions.search.kinds`),
because the ladder cycles found the optimism concentrated on one kind: our
attack beside a voluntary switch read +11 points a decision over 17 such
decisions under D92, against about zero for an attack beside an attack.
This says whether that is the model (present here too) or the belief and
the selection (absent here).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.coach import decisions  # noqa: E402
from champions.coach.analyze import analyze_game  # noqa: E402
from champions.corpus.replay import parse_replay  # noqa: E402
from champions.corpus.store import CorpusStore  # noqa: E402
from champions.dex.loader import Dex  # noqa: E402
from champions.formats import BO3_FORMAT_ID, FORMAT_ID  # noqa: E402
from champions.search.kinds import action_kind  # noqa: E402

DEFAULT_DB = Path("data/corpus.sqlite")
DEFAULT_LOGS = Path("data/replays")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--format", default=BO3_FORMAT_ID)
    parser.add_argument("--play-format", default=FORMAT_ID)
    parser.add_argument("--limit", type=int, default=60, help="games, each from both sides")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    dex = Dex.load(args.play_format)
    with CorpusStore(args.db, args.logs) as store:
        games = []
        for _, replay_id in store.stored_logs(args.format):
            log = store.read_log(args.format, replay_id)
            record = parse_replay(replay_id, log)
            if not record.rated or not record.sheets_revealed or record.result == "tie":
                continue
            games.append((replay_id, log))
    random.Random(args.seed).shuffle(games)
    games = games[: args.limit]
    print(f"{len(games)} games, both sides", flush=True)

    luck: dict[str, list[float]] = defaultdict(list)
    turns = 0
    clock = time.perf_counter()
    for index, (replay_id, log) in enumerate(games, 1):
        for side in ("p1", "p2"):
            try:
                game = decisions.from_replay(log, replay_id, side, dex)
                analysis = analyze_game(game, dex)
            except Exception as exc:  # one bad log must not cost the run
                print(f"  {replay_id} {side}: skipped ({exc!r})", flush=True)
                continue
            by_seq = {d.for_seq: d for d in game.decisions}
            for turn in analysis.turns:
                decision = by_seq.get(turn.for_seq)
                if turn.luck is None or decision is None or decision.played is None:
                    continue
                luck[action_kind(decision.rows[decision.played])].append(float(turn.luck))
                turns += 1
        if index % 10 == 0:
            print(
                f"  {index}/{len(games)} games, {turns} decisions, "
                f"{time.perf_counter() - clock:.0f}s",
                flush=True,
            )

    out = {}
    print(f"\n{turns} decisions with an ex-post cell")
    print(f"{'kind':20s} {'n':>5s} {'luck':>7s} {'se':>5s}")
    for kind, values in sorted(luck.items(), key=lambda kv: -len(kv[1])):
        n = len(values)
        mean = st.mean(values)
        se = st.pstdev(values) / math.sqrt(n) if n > 1 else float("nan")
        out[kind] = {"n": n, "luck": round(mean, 4), "se": round(se, 4)}
        print(f"{kind:20s} {n:5d} {100 * mean:+7.1f} {100 * se:5.1f}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "format_id": args.play_format,
                    "corpus_format": args.format,
                    "games": len(games),
                    "decisions": turns,
                    "written_at": datetime.now(UTC).isoformat(),
                    "kinds": out,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"-> {args.json}")


if __name__ == "__main__":
    main()

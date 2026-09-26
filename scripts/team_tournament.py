"""Which of the teams people play does this agent play best? (D96)

    python scripts/team_tournament.py --candidates corpus-01-floetteeternal,regmc-mence \
        --pool corpus-02-arcaninehisui,corpus-03-excadrill --games 10 --ports 8092,8093

Every candidate plays every pool team, `--games` games a pairing, the same
agent on both sides, one self-play worker per local server port. The
result is a table of each candidate's record against the pool with a Wilson
interval, written to `runs/tourney/<name>/results.json` and printed. The
agent is the one that will play the ladder, so the number is "how does this
agent do with this team against the teams it will meet", which is the
question STATUS asked and not "which team is best".

Pairings are seeded by their index, so a rerun reproduces the same games;
`--resume` skips pairings whose result file already exists.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Queue

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.teams import available_teams  # noqa: E402

WINS = re.compile(r"^\s+(\S+): (\d+) wins", re.MULTILINE)
FINISHED = re.compile(r"^finished: (\d+)/(\d+) battles", re.MULTILINE)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def run_pairing(
    candidate: str, pool: str, games: int, port: int, seed: int, agent: str, out: Path
) -> dict:
    trace_dir = out / f"{candidate}__{pool}"
    trace_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/selfplay.py",
            str(games),
            "--port",
            str(port),
            "--trace-dir",
            str(trace_dir),
            "--seed",
            str(seed),
            "--agent-a",
            agent,
            "--agent-b",
            agent,
            "--team-a",
            candidate,
            "--team-b",
            pool,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    text = proc.stdout + proc.stderr
    wins = {m.group(1): int(m.group(2)) for m in WINS.finditer(text)}
    finished = FINISHED.search(text)
    result = {
        "candidate": candidate,
        "pool": pool,
        "games": int(finished.group(1)) if finished else 0,
        "asked": games,
        "candidate_wins": wins.get("champ-a", 0),
        "pool_wins": wins.get("champ-b", 0),
        "seed": seed,
        "port": port,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "returncode": proc.returncode,
    }
    if proc.returncode != 0 or not finished:
        result["tail"] = text[-1500:]
    (trace_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--candidates", required=True, help="comma-separated team names")
    parser.add_argument("--pool", required=True, help="comma-separated team names")
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--ports", default="8092", help="one self-play worker per port")
    parser.add_argument("--agent", default="belief")
    parser.add_argument("--name", default="tourney")
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    known = set(available_teams())
    candidates = [c for c in args.candidates.split(",") if c]
    pool = [p for p in args.pool.split(",") if p]
    for name in candidates + pool:
        if name not in known:
            raise SystemExit(f"unknown team {name!r}; available: {sorted(known)}")
    ports = [int(p) for p in args.ports.split(",") if p]
    out = Path("runs") / args.name
    out.mkdir(parents=True, exist_ok=True)

    pairings = [
        (i, c, p) for i, (c, p) in enumerate((c, p) for c in candidates for p in pool if c != p)
    ]
    results: list[dict] = []
    queue: Queue[tuple[int, str, str]] = Queue()
    for item in pairings:
        existing = out / f"{item[1]}__{item[2]}" / "result.json"
        if args.resume and existing.exists():
            results.append(json.loads(existing.read_text(encoding="utf-8")))
            continue
        queue.put(item)
    total = queue.qsize()
    print(
        f"{len(pairings)} pairings, {total} to play, {args.games} games each, {len(ports)} workers",
        flush=True,
    )
    lock = threading.Lock()

    def worker(port: int) -> None:
        while True:
            try:
                index, candidate, pool_team = queue.get_nowait()
            except Exception:
                return
            result = run_pairing(
                candidate, pool_team, args.games, port, args.seed + index, args.agent, out
            )
            with lock:
                results.append(result)
                done = len(results)
                print(
                    f"[{done}/{len(pairings)}] {candidate} vs {pool_team}: "
                    f"{result['candidate_wins']}-{result['pool_wins']} "
                    f"({result['games']} games, {result['elapsed_s']}s, port {port})",
                    flush=True,
                )
            queue.task_done()

    threads = [threading.Thread(target=worker, args=(port,), daemon=True) for port in ports]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    table = []
    for candidate in candidates:
        rows = [r for r in results if r["candidate"] == candidate]
        wins = sum(r["candidate_wins"] for r in rows)
        games = sum(r["games"] for r in rows)
        lo, hi = wilson(wins, games)
        table.append(
            {
                "candidate": candidate,
                "wins": wins,
                "games": games,
                "rate": round(wins / games, 3) if games else None,
                "wilson": [round(lo, 3), round(hi, 3)],
                "by_pool": {r["pool"]: f"{r['candidate_wins']}-{r['pool_wins']}" for r in rows},
            }
        )
    table.sort(key=lambda t: -(t["rate"] or 0))
    (out / "results.json").write_text(
        json.dumps(
            {
                "agent": args.agent,
                "games_per_pairing": args.games,
                "pool": pool,
                "table": table,
                "pairings": results,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n{'candidate':28s} {'won':>4s} {'games':>5s} {'rate':>5s}  95% interval")
    for t in table:
        rate = f"{t['rate']:.2f}" if t["rate"] is not None else "n/a"
        print(
            f"{t['candidate']:28s} {t['wins']:4d} {t['games']:5d} {rate:>5s}  "
            f"{t['wilson'][0]:.2f}-{t['wilson'][1]:.2f}"
        )
    print(f"-> {out / 'results.json'}")


if __name__ == "__main__":
    main()

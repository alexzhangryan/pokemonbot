"""Play ladder games on the official Pokemon Showdown server.

    python scripts/ladder_live.py 10                    # ten games, the default agent and team
    python scripts/ladder_live.py 30 --agent adaptive   # another registered agent
    python scripts/ladder_live.py 5 --team regmb-rain
    python scripts/ladder_live.py 5 --no-review         # skip the coach between games
    python scripts/ladder_live.py --summary             # the record so far, from the ledger

The one script in the project that talks to play.pokemonshowdown.com rather
than to the local server. Everything else is the same as self-play: the same
agent registry, the same team files, the same traces (under `runs/live/` by
default), and the same rule about Open Team Sheets -- declined, always, or
the agent does not transfer to Champions (D2).

## Credentials

The bot needs a registered Showdown account, and the repository is public, so
the name and password live in `.env` (gitignored) or in the environment, never
in a source file:

    PS_USERNAME=champbot-something
    PS_PASSWORD=...

`.env.example` shows the shape. Register the name on the site first; an
unregistered name can log in but its rating is not kept. Name it so a human
opponent can tell it is a bot, and run one battle at a time -- Showdown
tolerates ladder bots that are registered, transparent and not disruptive,
and their rules page is the authority, not this docstring.

## What is kept

Three things per game, all under the trace directory (D78):

- The trace, `<battle_tag>.<username>.jsonl`, the same decision trace every
  other run writes. `make viewer-live` tails it while the game is on.
- The replay. The bot sends `/savereplay` to the room as the battle starts,
  which makes the server upload the finished log when the battle ends, so
  `https://replay.pokemonshowdown.com/<id>` holds the neutral record of the
  same game -- what the opponent revealed, seen from outside our belief.
  `--no-save-replays` turns this off.
- A row in `ledger.ndjson`: who we played, both ratings, the result, the
  trace path and the replay URL. It accumulates across runs; `--summary`
  reads it back. (`.ndjson`, not `.jsonl`, so the viewer does not list it as
  a trace.)

And a fourth, between games: the coach (`scripts/review.py`) runs on the
trace once its game has ended, writes the `.review.jsonl` and `.review.md`
beside it and prints its summary, and only then does the bot search for the
next game -- game, coach, game, coach (D79). The review re-solves every turn,
about a second each, so this costs under a minute per game and never shares
the CPU with a live search. `--no-review` skips it; `make review
GAME=runs/live` afterwards is the same result.

## What comes back

Showdown reports both players' Elo after every rated battle and the trace's
`battle_end` event already records ours (`rating`). The script prints the
result, the opponent and both ratings after each game, and a summary at the
end. GXE and Glicko are on the ladder page for the format, not in the
protocol, so read them there: https://pokemonshowdown.com/ladder/<format>.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import certifi

# The official server's certificate chain (Let's Encrypt) is rejected by the
# trust store this Anaconda Python uses by default -- "certificate has
# expired", though it has not -- and accepted by certifi's bundle, which httpx
# already ships. OpenSSL reads this variable when Python builds its default
# context, and poke-env's websocket uses that context, so it has to be set
# before poke-env is imported. `setdefault`: a machine that has fixed its
# store keeps its own setting.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from poke_env.ps_client import AccountConfiguration  # noqa: E402
from poke_env.ps_client.server_configuration import ShowdownServerConfiguration  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.teams import DEFAULT, available_teams, load_team  # noqa: E402
from scripts.selfplay import AGENTS, FORMAT_ID, ProtocolFailureWatcher, build_agent  # noqa: E402

ENV_PATH = Path(".env")
USERNAME_KEY = "PS_USERNAME"
PASSWORD_KEY = "PS_PASSWORD"
DEFAULT_TRACE_DIR = Path("runs/live")
LEDGER_NAME = "ledger.ndjson"
STATUS_NAME = "status.json"
#: The viewer's "stop after this game": a flag file, read between games.
STOP_NAME = "stop"
REPLAY_HOST = "https://replay.pokemonshowdown.com"
#: The agent a ladder run plays when none is named. `adaptive-belief` was the
#: default for an evening on a 17-7 start in its mirror against `belief`; the
#: mirror finished 26-22 (Wilson 0.40-0.67), not apart, and the incumbent
#: stays (D89).
DEFAULT_AGENT = "belief"


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    """`KEY=VALUE` lines out of a dotenv file, the environment winning.

    Hand-rolled rather than a dependency: the file has two keys and the one
    thing that matters is that its contents never reach a log or a trace.
    """
    values: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export ") :].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    for key in (USERNAME_KEY, PASSWORD_KEY):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def credentials(env: dict[str, str]) -> tuple[str, str]:
    username = env.get(USERNAME_KEY, "").strip()
    password = env.get(PASSWORD_KEY, "")
    if not username or not password:
        raise SystemExit(
            f"no Showdown account: put {USERNAME_KEY} and {PASSWORD_KEY} in {ENV_PATH} "
            f"(see .env.example) or in the environment. Register the name on "
            f"play.pokemonshowdown.com first, or the rating is not kept."
        )
    return username, password


# -- the record ----------------------------------------------------------


def replay_url(battle_tag: str) -> str:
    """Where the server puts the replay it is asked to save.

    A room `battle-<format>-<n>` uploads as `<format>-<n>`. A private room
    would carry a password suffix the protocol does not tell us; rated ladder
    rooms are public, so the bare id is the address.
    """
    return f"{REPLAY_HOST}/{battle_tag.removeprefix('battle-')}"


def result_of(battle: Any) -> str:
    won = battle.won
    return "win" if won else ("tie" if won is None else "loss")


def ledger_row(battle: Any, run: dict[str, Any], trace_path: Path | None) -> dict[str, Any]:
    """One finished game as the ledger keeps it. `run` is what was constant
    for the whole invocation (agent, team, format, seed, username)."""
    return {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **run,
        "battle_tag": battle.battle_tag,
        "opponent": battle.opponent_username,
        "opponent_rating": battle.opponent_rating,
        "rating": battle.rating,
        "result": result_of(battle),
        "turns": battle.turn,
        "trace": str(trace_path) if trace_path is not None else None,
        "replay": replay_url(battle.battle_tag),
    }


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def summarize(rows: list[dict[str, Any]]) -> str:
    """The record across every run the ledger has seen, as text."""
    if not rows:
        return "no games in the ledger yet"
    wins = sum(r["result"] == "win" for r in rows)
    ties = sum(r["result"] == "tie" for r in rows)
    ratings = [r["rating"] for r in rows if isinstance(r.get("rating"), int)]
    lines = [f"{len(rows)} games: {wins} won, {len(rows) - wins - ties} lost, {ties} tied"]
    if ratings:
        lines.append(f"rating {ratings[0]} -> {ratings[-1]} (peak {max(ratings)})")
    by_agent: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        key = (str(r.get("agent")), str(r.get("team")))
        by_agent.setdefault(key, []).append(r["result"] == "win")
    if len(by_agent) > 1:
        for (agent, team), results in sorted(by_agent.items()):
            lines.append(f"  {agent} on {team}: {sum(results)}/{len(results)} won")
    lines.append("last five:")
    for r in rows[-5:]:
        theirs = r.get("opponent_rating")
        lines.append(
            f"  {r['result']:4} vs {r.get('opponent')}{f' ({theirs})' if theirs else ''}"
            f"  {r.get('replay')}"
        )
    return "\n".join(lines)


class Report:
    """One line per finished battle, a row in the ledger, and the numbers for
    the summary."""

    def __init__(
        self,
        n_games: int,
        ledger: Path | None = None,
        run: dict[str, Any] | None = None,
        trace_path: Callable[[str], Path | None] | None = None,
    ) -> None:
        self.n_games = n_games
        self.ledger = ledger
        self.run = run or {}
        self.trace_path = trace_path
        self.finished = 0
        self.wins = 0
        self.ratings: list[int] = []
        self.rows: list[dict[str, Any]] = []

    def __call__(self, battle: Any) -> None:
        self.finished += 1
        won = battle.won
        self.wins += int(bool(won))
        rating = battle.rating
        if isinstance(rating, int):
            self.ratings.append(rating)
        theirs = battle.opponent_rating
        print(
            f"battle {self.finished}/{self.n_games}: {result_of(battle)} vs "
            f"{battle.opponent_username}{f' ({theirs})' if theirs else ''} -> our rating "
            f"{rating if rating is not None else 'unrated'}  [{battle.battle_tag}]",
            flush=True,
        )
        trace = self.trace_path(battle.battle_tag) if self.trace_path is not None else None
        row = ledger_row(battle, self.run, trace)
        self.rows.append(row)
        if self.ledger is not None:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")


# -- the side effects -----------------------------------------------------


class StatusFile:
    """What the run is doing between battles, for the viewer's phase pill.

    One small JSON document, rewritten in place (write to a sibling, then
    replace, so a reader never sees half of it). The trace says what the bot
    is doing inside a battle; this says what it is doing outside one:
    searching, reviewing, done.
    """

    def __init__(self, path: Path, of: int, run: dict[str, Any] | None = None) -> None:
        self.path = path
        self.of = of
        # Who is playing what: the viewer looks the rating up by these.
        self.run = {k: v for k, v in (run or {}).items() if k in ("username", "format")}

    def write(self, phase: str, game: int, **fields: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "phase": phase,
            "game": game,
            "of": self.of,
            "t": time.time(),
            **self.run,
            **fields,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)

    def read(self) -> dict[str, Any]:
        return dict(json.loads(self.path.read_text(encoding="utf-8")))


def save_replay_on_start(player: Callable[[], Any]) -> Callable[[Any], None]:
    """A battle-start callback that asks the room to save its replay.

    Sent at the start rather than the end because poke-env leaves the room the
    moment the battle finishes, and a `/savereplay` during the battle marks the
    room so the server re-uploads the complete log when it ends
    (`room-battle.ts`, `replaySaved`). The callback is synchronous; the send is
    scheduled on the running loop, which is the loop poke-env is driving.
    `player` is a getter because the callback is handed to the agent's
    constructor, before the agent exists.
    """

    def callback(battle: Any) -> None:
        asyncio.ensure_future(player().ps_client.send_message("/savereplay", battle.battle_tag))

    return callback


async def review_game(trace_path: Path) -> Path | None:
    """The coach on one finished trace, in its own process, awaited.

    Its output goes to a `.review.log` beside the trace; the overlay and the
    document land beside it too, as `make review` would put them. Returns the
    document's path, or None if the review failed (the log says why).
    """
    stem = trace_path.name.removesuffix(".jsonl")
    log = trace_path.with_name(f"{stem}.review.log")
    with log.open("wb") as handle:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "scripts/review.py",
            str(trace_path),
            "--quiet",
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        code = await process.wait()
    document = trace_path.with_name(f"{stem}.review.md")
    if code != 0 or not document.is_file():
        print(f"review failed (exit {code}); see {log}", flush=True)
        return None
    return document


def headline(document: str) -> str:
    """The part of the coach's document worth a terminal: the result line and
    the summary, up to the per-turn sections."""
    lines = document.splitlines()
    keep: list[str] = []
    for line in lines[1:]:
        if line.startswith("## Preview") or line.startswith("## Turn"):
            break
        if line.startswith("Thresholds are"):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


async def play(
    player: Any,
    n_games: int,
    between: Callable[[], Awaitable[None]] | None = None,
    status: StatusFile | None = None,
    stop: Path | None = None,
) -> int:
    """Ladder one game at a time, with `between` awaited after each.

    poke-env's own `ladder(n)` searches for the next game the moment the last
    one ends. This searches one, waits for it, closes its trace so the last
    event is on disk, runs `between` (the coach), and only then searches
    again -- so the review never overlaps a live search. `status`, if given,
    is told each phase for the viewer. `stop`, if given, is a flag file the
    viewer creates to mean "finish this game, then stop"; it is checked
    before every search, and a stale one from an earlier run is cleared
    before the first. Returns the number of games played.
    """
    if stop is not None:
        stop.unlink(missing_ok=True)
    played = 0
    for game in range(1, n_games + 1):
        if status is not None:
            status.write("searching", game)
        await player.ladder(1)
        played = game
        await player.close_traces()
        if between is not None:
            if status is not None:
                status.write("reviewing", game)
            await between()
        if stop is not None and stop.is_file():
            stop.unlink(missing_ok=True)
            print(f"stopped after game {game} of {n_games}, as asked", flush=True)
            break
    if status is not None:
        status.write("done", played, stopped=played < n_games)
    return played


async def main() -> None:
    # Opponents' names are whatever Showdown allows, and a Windows console
    # defaults to cp1252; printing a name it cannot encode must not end a run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("n_games", nargs="?", type=int, default=10)
    parser.add_argument("--agent", choices=sorted(AGENTS), default=DEFAULT_AGENT)
    parser.add_argument("--team", choices=available_teams(), default=DEFAULT)
    parser.add_argument("--format", default=FORMAT_ID)
    parser.add_argument("--trace-dir", type=Path, default=DEFAULT_TRACE_DIR)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--env", type=Path, default=ENV_PATH, help="dotenv file with the account")
    parser.add_argument(
        "--no-review",
        action="store_true",
        help="do not run the coach between games",
    )
    parser.add_argument(
        "--no-save-replays",
        action="store_true",
        help="do not ask the server to save each game's replay",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print the record from the ledger and exit; no account needed",
    )
    args = parser.parse_args()

    ledger = args.trace_dir / LEDGER_NAME
    if args.summary:
        print(summarize(read_ledger(ledger)))
        return

    username, password = credentials(read_env(args.env))
    run = {
        "username": username,
        "agent": args.agent,
        "team": args.team,
        "format": args.format,
        "seed": args.seed,
    }
    player: Any = None

    def trace_path(battle_tag: str) -> Path | None:
        try:
            return Path(player.trace_path(battle_tag))
        except KeyError:
            return None

    report = Report(args.n_games, ledger=ledger, run=run, trace_path=trace_path)
    status = StatusFile(args.trace_dir / STATUS_NAME, args.n_games, run=run)

    def on_battle_start(battle: Any) -> None:
        status.write("battle", report.finished + 1, battle_tag=battle.battle_tag)
        if not args.no_save_replays:
            save_replay(battle)

    save_replay = save_replay_on_start(lambda: player)

    async def coach() -> None:
        """The review of the game that just ended, printed, before the next."""
        if not report.rows or not report.rows[-1]["trace"]:
            return
        trace = Path(report.rows[-1]["trace"])
        print(f"coach reviewing {trace.name}...", flush=True)
        document = await review_game(trace)
        if document is not None:
            print(headline(document.read_text(encoding="utf-8")), flush=True)
            print(f"review: {document}", flush=True)
        print(flush=True)

    player = build_agent(
        args.agent,
        account_configuration=AccountConfiguration(username, password),
        server_configuration=ShowdownServerConfiguration,
        battle_format=args.format,
        team=load_team(args.team),
        trace_dir=str(args.trace_dir),
        seed=args.seed,
        max_concurrent_battles=1,
        # Start the timer ourselves: a stalled human costs us nothing then,
        # and our own decisions are far inside it.
        start_timer_on_battle_start=True,
        on_battle_end=report,
        on_battle_start=on_battle_start,
    )
    print(
        f"{username} laddering {args.format} as {args.agent} on {args.team}, "
        f"{args.n_games} game{'s' if args.n_games != 1 else ''}; traces in {args.trace_dir}/, "
        f"ledger in {ledger}",
        flush=True,
    )

    with ProtocolFailureWatcher() as watcher:
        try:
            await play(
                player,
                args.n_games,
                between=None if args.no_review else coach,
                status=status,
                stop=args.trace_dir / STOP_NAME,
            )
        finally:
            await player.close_traces()
            await player.shutdown()
            await player.ps_client.stop_listening()

    print()
    print(f"{report.wins}/{report.finished} won this run")
    if report.ratings:
        print(f"rating {report.ratings[0]} -> {report.ratings[-1]} (peak {max(report.ratings)})")
    print(f"ladder page: https://pokemonshowdown.com/ladder/{args.format}")
    if watcher.failures:
        print(f"{len(watcher.failures)} protocol failures; first: {watcher.failures[0]}")
    print()
    print("ledger so far:")
    print(summarize(read_ledger(ledger)))


if __name__ == "__main__":
    asyncio.run(main())

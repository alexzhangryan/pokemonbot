"""Run self-play games between two baseline agents on the local server.

Usage: python scripts/selfplay.py [n_games] [--port 8090] [--trace-dir traces]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any

from poke_env.ps_client import AccountConfiguration
from poke_env.ps_client.server_configuration import ServerConfiguration

from champions.agents.baseline import RandomAgent
from champions.teams import ALPHA, BETA, load_team
from champions.trace.validate import validate_trace_file

FORMAT_ID = "gen9championsvgc2026regmb"

# Showdown reports a rejected action back over the protocol rather than raising,
# so an agent sending illegal orders looks like a clean run unless these are
# watched for. poke-env logs them at its custom PS_ERROR level (25).
PROTOCOL_FAILURE_MARKERS = (
    "[Invalid choice]",
    "[Unavailable choice]",
    "lost due to inactivity",
    "forfeited",
)


class ProtocolFailureWatcher(logging.Handler):
    """Collects any Showdown protocol error surfaced through poke-env's logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.failures: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        if any(marker in message for marker in PROTOCOL_FAILURE_MARKERS):
            self.failures.append(message)

    def __enter__(self) -> ProtocolFailureWatcher:
        logging.getLogger().addHandler(self)
        logging.getLogger("poke-env").addHandler(self)
        return self

    def __exit__(self, *exc: object) -> None:
        logging.getLogger().removeHandler(self)
        logging.getLogger("poke-env").removeHandler(self)


def local_server(port: int) -> ServerConfiguration:
    return ServerConfiguration(
        f"ws://localhost:{port}/showdown/websocket",
        "https://play.pokemonshowdown.com/action.php?",
    )


async def run_selfplay(
    n_games: int,
    port: int,
    trace_dir: str | Path,
    seed: int = 0,
    username_suffix: str = "",
) -> tuple[RandomAgent, RandomAgent, list[str]]:
    """Play `n_games` between two random agents.

    Returns both players and any Showdown protocol failures (invalid choice,
    inactivity timeout) seen during the run.
    """
    server = local_server(port)
    common: dict[str, Any] = {
        "battle_format": FORMAT_ID,
        "server_configuration": server,
        "trace_dir": str(trace_dir),
        "max_concurrent_battles": 1,
        # The real 45s turn limit. Baselines never approach it, but the watchdog
        # path is exercised in every game rather than only once it matters.
        "decision_deadline_s": 45.0,
    }

    p1 = RandomAgent(
        account_configuration=AccountConfiguration(f"champ-a{username_suffix}", None),
        team=load_team(ALPHA),
        seed=seed,
        **common,
    )
    p2 = RandomAgent(
        account_configuration=AccountConfiguration(f"champ-b{username_suffix}", None),
        team=load_team(BETA),
        seed=seed + 1,
        **common,
    )

    with ProtocolFailureWatcher() as watcher:
        await p1.battle_against(p2, n_battles=n_games)
        await p1.close_traces()
        await p2.close_traces()
        # Disconnect before the caller (or a test fixture) tears the server
        # down, otherwise the sockets die mid-read and log a ConnectionReset
        # traceback that reads like a failure but is only shutdown ordering.
        for player in (p1, p2):
            await player.ps_client.stop_listening()
    return p1, p2, watcher.failures


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("n_games", nargs="?", type=int, default=5)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--trace-dir", default="traces")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    p1, p2, failures = await run_selfplay(args.n_games, args.port, args.trace_dir, seed=args.seed)

    print(f"finished: {p1.n_finished_battles}/{args.n_games} battles")
    print(f"  {p1.username}: {p1.n_won_battles} wins")
    print(f"  {p2.username}: {p2.n_won_battles} wins")
    print(f"protocol failures (invalid choice / timeout): {len(failures)}")
    for failure in failures[:5]:
        print(f"  ! {failure}")

    traces = sorted(Path(args.trace_dir).glob("*.jsonl"))
    problems = {p.name: validate_trace_file(p) for p in traces}
    invalid = {name: probs for name, probs in problems.items() if probs}
    print(f"traces: {len(traces)} written, {len(invalid)} invalid")
    for name, probs in list(invalid.items())[:5]:
        print(f"  ! {name}: {probs}")


if __name__ == "__main__":
    asyncio.run(main())

"""Run an agent that waits for challenges, so you can play against it yourself.

Usage: python scripts/play_human.py [--agent random|greedy] [--games 1]

Then open http://localhost:8090 in a browser, pick any username, and challenge
the bot in "[Gen 9 Champions] VGC 2026 Reg M-B". You will need your own legal
team; data/teams/regmb-alpha.txt can be pasted into the teambuilder.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from poke_env.ps_client import AccountConfiguration

from champions.agents.baseline import MaxBasePowerAgent, RandomAgent, TracingPlayer
from champions.dex.loader import Dex
from champions.teams import ALPHA, BETA, load_team
from scripts.selfplay import FORMAT_ID, local_server


def build_agent(kind: str, port: int, username: str, trace_dir: str) -> TracingPlayer:
    common: dict[str, Any] = {
        "account_configuration": AccountConfiguration(username, None),
        "battle_format": FORMAT_ID,
        "server_configuration": local_server(port),
        "trace_dir": trace_dir,
        "seed": 0,
    }
    if kind == "greedy":
        return MaxBasePowerAgent(team=load_team(BETA), dex=Dex.load(FORMAT_ID), **common)
    return RandomAgent(team=load_team(ALPHA), **common)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["random", "greedy"], default="greedy")
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--username", default="champbot")
    parser.add_argument("--trace-dir", default="runs/human")
    args = parser.parse_args()

    agent = build_agent(args.agent, args.port, args.username, args.trace_dir)

    print(f"'{args.username}' ({args.agent}) is waiting for {args.games} challenge(s).")
    print()
    print(f"  1. open http://localhost:{args.port} and pick any username")
    print("  2. import a team: paste data/teams/regmb-alpha.txt into the teambuilder")
    print(f"  3. challenge '{args.username}' in [Gen 9 Champions] VGC 2026 Reg M-B")
    print()
    print("  Decline Open Team Sheets if prompted; the bot always declines.")
    print()

    await agent.accept_challenges(None, args.games)
    await agent.close_traces()

    print(f"done. {agent.n_won_battles}/{agent.n_finished_battles} to the bot.")
    print(f"traces in {args.trace_dir}/")


if __name__ == "__main__":
    asyncio.run(main())

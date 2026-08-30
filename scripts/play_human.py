"""Run an agent that waits for challenges, so you can play against it yourself.

Usage: python scripts/play_human.py [--agent belief|oneply|greedy|random] [--games 1]

Then open http://localhost:8090 in a browser, pick any username, and challenge
the bot in "[Gen 9 Champions] VGC 2026 Reg M-B". You will need your own legal
team; data/teams/regmb-alpha.txt can be pasted into the teambuilder.
"""

from __future__ import annotations

import argparse
import asyncio

from poke_env.ps_client import AccountConfiguration

from champions.agents import commands
from champions.agents.baseline import TracingPlayer
from champions.teams import BETA, load_team
from scripts.selfplay import AGENTS, FORMAT_ID, local_server
from scripts.selfplay import build_agent as build_registered_agent


def build_agent(kind: str, port: int, username: str, trace_dir: str) -> TracingPlayer:
    """One agent by name, through the same registry self-play uses.

    Shared rather than duplicated because the two lists had already drifted: the
    viewer's "play the bot" dropdown is built from `scripts.selfplay.AGENTS`, so
    it offered agents this script had never heard of and the run died on an
    argparse error the moment anyone picked one.
    """
    return build_registered_agent(
        kind,
        team=load_team(BETA),
        account_configuration=AccountConfiguration(username, None),
        battle_format=FORMAT_ID,
        server_configuration=local_server(port),
        trace_dir=trace_dir,
        seed=0,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=sorted(AGENTS), default="belief")
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--username", default="champbot")
    parser.add_argument("--trace-dir", default="runs/human")
    parser.add_argument(
        "--control-stdin",
        action="store_true",
        help="read control commands (currently only 'forfeit') from stdin",
    )
    args = parser.parse_args()

    agent = build_agent(args.agent, args.port, args.username, args.trace_dir)
    if args.control_stdin:
        # So the viewer can concede a game and go back to waiting for the next
        # challenge, rather than having to kill the bot to get out of one.
        commands.listen([agent])

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

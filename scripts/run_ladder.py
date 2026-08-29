"""Evaluate agents against the frozen opponent pool, reporting win rate and
clock compliance in one table.

Usage: python scripts/run_ladder.py [n_games] [--port 8090] [--seed 0]
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from poke_env.ps_client import AccountConfiguration

from champions.agents.baseline import MaxBasePowerAgent, RandomAgent, TracingPlayer
from champions.dex.loader import Dex
from champions.harness.ladder import AgentFactory, format_results_table, run_matchup
from champions.teams import ALPHA, BETA, load_team
from scripts.selfplay import FORMAT_ID, local_server


def build_arms(port: int) -> tuple[tuple[str, AgentFactory], tuple[str, AgentFactory]]:
    server = local_server(port)
    # Teams are held fixed across arms: team quality is a confound in every
    # evaluation (DECISIONS.md D5).
    dex = Dex.load(FORMAT_ID)

    def make_random(username: str, seed: int, trace_dir: str) -> TracingPlayer:
        return RandomAgent(
            account_configuration=AccountConfiguration(username, None),
            battle_format=FORMAT_ID,
            server_configuration=server,
            team=load_team(ALPHA),
            trace_dir=trace_dir,
            seed=seed,
        )

    def make_greedy(username: str, seed: int, trace_dir: str) -> TracingPlayer:
        return MaxBasePowerAgent(
            account_configuration=AccountConfiguration(username, None),
            battle_format=FORMAT_ID,
            server_configuration=server,
            team=load_team(BETA),
            trace_dir=trace_dir,
            seed=seed,
            dex=dex,
        )

    return ("random", make_random), ("max-base-power", make_greedy)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("n_games", nargs="?", type=int, default=50)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--trace-dir", default="runs/ladder")
    args = parser.parse_args()

    arm_a, arm_b = build_arms(args.port)
    results = await run_matchup(arm_a, arm_b, args.n_games, Path(args.trace_dir), seed=args.seed)

    print()
    print(f"format {FORMAT_ID}, {args.n_games} games, seed {args.seed}")
    print()
    print(format_results_table(results))


if __name__ == "__main__":
    asyncio.run(main())

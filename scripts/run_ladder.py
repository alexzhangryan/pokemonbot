"""Evaluate agents against the frozen opponent pool, reporting win rate and
clock compliance in one table.

Usage:
    python scripts/run_ladder.py [n_games] [--port 8090] [--seed 0]
    python scripts/run_ladder.py 50 --arm-a oneply --arm-b greedy

Both arms play the same team by default. That is not cosmetic: the two
checked-in teams are not balanced against each other -- max-base-power on BETA
beats max-base-power on ALPHA 10-0 -- so running an arm on each measures the
teams at least as much as the agents (`docs/DECISIONS.md` D30). Pass `--team-a`
and `--team-b` separately only when the asymmetry is the thing being measured,
and the table says so when they differ.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from poke_env.ps_client import AccountConfiguration

from champions.agents.baseline import MaxBasePowerAgent, RandomAgent, TracingPlayer
from champions.agents.belief_agent import BeliefAgent, BeliefMovesOnly, BeliefStatsOnly
from champions.agents.oneply import OnePlyAgent
from champions.dex.loader import Dex
from champions.harness.ladder import AgentFactory, format_results_table, run_matchup
from champions.teams import ALPHA, available_teams, load_team
from scripts.selfplay import FORMAT_ID, local_server

#: The frozen opponent pool: the command-line name, the class, and the display
#: name used in the table. The display name also seeds the Showdown username, so
#: it stays stable -- traces are found by globbing it.
ARMS: dict[str, tuple[type[TracingPlayer], str]] = {
    "random": (RandomAgent, "random"),
    "greedy": (MaxBasePowerAgent, "max-base-power"),
    "oneply": (OnePlyAgent, "one-ply"),
    "belief": (BeliefAgent, "one-ply-belief"),
    # Ablations. M5 changes two things at once -- what the opponent's stats and
    # effects are, and what the opponent's action columns are -- and a single
    # head-to-head cannot say which one moved the number. These split it.
    "belief-stats": (BeliefStatsOnly, "belief-stats-only"),
    "belief-moves": (BeliefMovesOnly, "belief-moves-only"),
}

#: Agents that read their numbers from the resolved Champions dex. poke-env
#: ships mainline Gen 9 data and 303 moves differ, so these cannot be built
#: without it.
NEEDS_DEX = (MaxBasePowerAgent, OnePlyAgent, BeliefAgent)


def build_arm(name: str, port: int, team: str = ALPHA) -> tuple[str, AgentFactory]:
    """One arm of a matchup, by its command-line name."""
    server = local_server(port)
    agent_class, display = ARMS[name]
    dex = Dex.load(FORMAT_ID) if issubclass(agent_class, NEEDS_DEX) else None

    def make(username: str, seed: int, trace_dir: str) -> TracingPlayer:
        kwargs = {
            "account_configuration": AccountConfiguration(username, None),
            "battle_format": FORMAT_ID,
            "server_configuration": server,
            "team": load_team(team),
            "trace_dir": trace_dir,
            "seed": seed,
        }
        if dex is not None:
            kwargs["dex"] = dex
        return agent_class(**kwargs)

    return display, make


def build_arms(
    port: int, team_a: str = ALPHA, team_b: str = ALPHA
) -> tuple[tuple[str, AgentFactory], tuple[str, AgentFactory]]:
    """The default pairing: random against max-base-power, on one team."""
    return build_arm("random", port, team_a), build_arm("greedy", port, team_b)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("n_games", nargs="?", type=int, default=50)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--trace-dir", default="runs/ladder")
    parser.add_argument("--arm-a", choices=sorted(ARMS), default="random")
    parser.add_argument("--arm-b", choices=sorted(ARMS), default="greedy")
    parser.add_argument(
        "--team",
        default=ALPHA,
        choices=available_teams(),
        help="the team both arms play, so the result is about the agents",
    )
    parser.add_argument("--team-a", default=None, choices=available_teams())
    parser.add_argument("--team-b", default=None, choices=available_teams())
    args = parser.parse_args()

    team_a = args.team_a or args.team
    team_b = args.team_b or args.team

    arm_a = build_arm(args.arm_a, args.port, team_a)
    arm_b = build_arm(args.arm_b, args.port, team_b)
    results = await run_matchup(arm_a, arm_b, args.n_games, Path(args.trace_dir), seed=args.seed)

    print()
    print(f"format {FORMAT_ID}, {args.n_games} games, seed {args.seed}")
    if team_a == team_b:
        print(f"both arms on team {team_a}")
    else:
        print(
            f"WARNING: {arm_a[0]} plays {team_a} and {arm_b[0]} plays {team_b}. The two "
            f"checked-in teams are not balanced, so this table mixes agent strength "
            f"with team strength (DECISIONS.md D30)."
        )
    print()
    print(format_results_table(results))


if __name__ == "__main__":
    asyncio.run(main())

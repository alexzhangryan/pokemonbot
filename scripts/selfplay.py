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

from champions.agents import commands
from champions.agents.baseline import MaxBasePowerAgent, RandomAgent, TracingPlayer
from champions.agents.belief_agent import BeliefAgent
from champions.agents.oneply import OnePlyAgent
from champions.dex.loader import Dex
from champions.teams import ALPHA, BETA, available_teams, load_team
from champions.trace.validate import validate_trace_file

FORMAT_ID = "gen9championsvgc2026regmb"

# Showdown reports a rejected action back over the protocol rather than raising,
# so an agent sending illegal orders looks like a clean run unless these are
# watched for. poke-env logs them at its custom PS_ERROR level (25).
PROTOCOL_FAILURE_MARKERS = (
    "[Invalid choice]",
    "[Unavailable choice]",
    "lost due to inactivity",
)

# A forfeit used to be counted with those, and it does not belong there. Nothing
# in the agent concedes by accident: the only path to it is a deliberate request
# over the control channel (`champions/agents/commands.py`), or the person on the
# other side giving up. Both are events worth seeing and neither is a defect, so
# they are collected separately rather than inflating a failure count.
FORFEIT_MARKER = "forfeited"


class ProtocolFailureWatcher(logging.Handler):
    """Collects any Showdown protocol error surfaced through poke-env's logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.failures: list[str] = []
        self.forfeits: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        if FORFEIT_MARKER in message:
            self.forfeits.append(message)
        elif any(marker in message for marker in PROTOCOL_FAILURE_MARKERS):
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


AGENTS = {
    "random": RandomAgent,
    "greedy": MaxBasePowerAgent,
    "oneply": OnePlyAgent,
    "belief": BeliefAgent,
}

# Agents that compute their numbers from the resolved Champions dex rather than
# from poke-env's mainline data, and so cannot run without it built.
NEEDS_DEX = (MaxBasePowerAgent, OnePlyAgent, BeliefAgent)


def build_agent(kind: str, **kwargs: Any) -> TracingPlayer:
    """One of the baseline agents by name, as the viewer's control panel and the
    command line both refer to them."""
    try:
        agent_class = AGENTS[kind]
    except KeyError:
        raise ValueError(f"unknown agent {kind!r}; expected one of {sorted(AGENTS)}") from None
    if issubclass(agent_class, NEEDS_DEX):
        kwargs["dex"] = Dex.load(FORMAT_ID)
    return agent_class(**kwargs)


async def run_selfplay(
    n_games: int,
    port: int,
    trace_dir: str | Path,
    seed: int = 0,
    username_suffix: str = "",
    agent_a: str = "random",
    agent_b: str = "random",
    team_a: str = ALPHA,
    team_b: str = BETA,
    control_stdin: bool = False,
) -> tuple[TracingPlayer, TracingPlayer, list[str]]:
    """Play `n_games` between two agents.

    Teams are parameters, and defaulting them to two *different* teams is only
    right for a demonstration game. For any measurement that compares agents,
    pass the same team to both: the two checked-in teams are not balanced
    against each other -- greedy on BETA beats greedy on ALPHA 10-0 -- so a
    head-to-head between different teams measures the teams at least as much as
    the agents. See `docs/DECISIONS.md` D30.

    With `control_stdin`, the run reads commands from stdin so its supervisor
    can concede the current battle without killing the remaining games. See
    `champions/agents/commands.py`.

    Returns both players and any Showdown protocol failures (invalid choice,
    inactivity timeout) seen during the run.
    """
    server = local_server(port)

    finished = 0

    def report(battle: Any) -> None:
        """One line per completed battle, so a run in progress is legible.

        Only p1 reports, or every battle would be announced twice: both agents
        see the same game end.
        """
        nonlocal finished
        finished += 1
        result = "win" if battle.won else ("tie" if battle.won is None else "loss")
        print(f"battle {finished}/{n_games}: {battle.battle_tag} -> champ-a {result}", flush=True)

    common: dict[str, Any] = {
        "battle_format": FORMAT_ID,
        "server_configuration": server,
        "trace_dir": str(trace_dir),
        "max_concurrent_battles": 1,
        # The real 45s turn limit. Baselines never approach it, but the watchdog
        # path is exercised in every game rather than only once it matters.
        "decision_deadline_s": 45.0,
    }

    p1 = build_agent(
        agent_a,
        account_configuration=AccountConfiguration(f"champ-a{username_suffix}", None),
        team=load_team(team_a),
        seed=seed,
        on_battle_end=report,
        **common,
    )
    p2 = build_agent(
        agent_b,
        account_configuration=AccountConfiguration(f"champ-b{username_suffix}", None),
        team=load_team(team_b),
        seed=seed + 1,
        **common,
    )

    if control_stdin:
        commands.listen([p1, p2])

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
    parser.add_argument("--agent-a", choices=sorted(AGENTS), default="random")
    parser.add_argument("--agent-b", choices=sorted(AGENTS), default="random")
    parser.add_argument("--team-a", default=ALPHA, choices=available_teams())
    parser.add_argument("--team-b", default=BETA, choices=available_teams())
    parser.add_argument(
        "--control-stdin",
        action="store_true",
        help="read control commands (currently only 'forfeit') from stdin",
    )
    args = parser.parse_args()

    p1, p2, failures = await run_selfplay(
        args.n_games,
        args.port,
        args.trace_dir,
        seed=args.seed,
        agent_a=args.agent_a,
        agent_b=args.agent_b,
        team_a=args.team_a,
        team_b=args.team_b,
        control_stdin=args.control_stdin,
    )

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

"""Differential test harness.

At M0 there is no second implementation to compare against, so this validates
the simulator's own determinism and builds the position generator. The interface
is the point: when a custom engine arrives (M8, gated on the profiling decision
in DECISIONS.md D6), it implements `Engine` and `compare_engines` starts finding
real divergences without the harness changing.

An engine that silently diverges on one of roughly 250 modified moves is worse
than no engine, which is why this is a prerequisite rather than a follow-up.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Protocol

from champions.search.oracle import SimServer

FORMAT_ID = "gen9championsvgc2026regmb"


@dataclass(frozen=True)
class Position:
    """A replayable recipe for a battle position.

    Positions are recipes rather than serialized states so they stay portable
    across implementations: a second engine need not understand Showdown's
    serialization format to replay one, only to play the same moves.
    """

    format_id: str
    battle_seed: list[int]
    p1_team: str
    p2_team: str
    choices: list[dict[str, str | None]] = field(default_factory=list)

    def digest(self) -> str:
        payload = json.dumps(
            {
                "format_id": self.format_id,
                "battle_seed": self.battle_seed,
                "p1_team": self.p1_team,
                "p2_team": self.p2_team,
                "choices": self.choices,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Outcome:
    """What an engine reports after replaying a position."""

    turn: int
    ended: bool
    winner: str | None
    log_digest: str

    @staticmethod
    def digest_log(log: list[str]) -> str:
        """Digest the protocol log, ignoring lines that are not battle content.

        Showdown emits a `|t:|<unix seconds>` line at the start of each turn.
        Two replays of the same position that straddle a second boundary
        therefore produce different logs from wall time alone, which showed up
        as roughly 0.5-0.8% of positions "diverging" with identical turn, ended,
        and winner. Comparing that would mean a permanent low background rate of
        false divergences in every future engine comparison.
        """
        content = [line for line in log if not line.startswith("|t:|")]
        return hashlib.sha256("\n".join(content).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Divergence:
    position: Position
    left: Outcome
    right: Outcome
    fields: list[str]

    def __str__(self) -> str:
        return (
            f"position {self.position.digest()} diverged on {', '.join(self.fields)}: "
            f"{self.left} != {self.right}"
        )


class Engine(Protocol):
    """The seam a second implementation plugs into."""

    def rollout(self, position: Position) -> Outcome: ...


class ShowdownEngine:
    """The reference implementation: the vendored simulator via JSON-RPC."""

    def __init__(self, server: SimServer) -> None:
        self._server = server

    def rollout(self, position: Position) -> Outcome:
        state = self._server.create(
            position.format_id,
            position.p1_team,
            position.p2_team,
            seed=position.battle_seed,
        )
        handle = int(state["handle"])
        try:
            for choice in position.choices:
                if state["ended"]:
                    break
                state = self._server.step(handle, choice.get("p1"), choice.get("p2"))
            return Outcome(
                turn=int(state["turn"]),
                ended=bool(state["ended"]),
                winner=state["winner"],
                log_digest=Outcome.digest_log(state["log"]),
            )
        finally:
            self._server.destroy(handle)


def generate_positions(
    server: SimServer,
    n: int,
    seed: int,
    p1_team: str,
    p2_team: str,
    format_id: str = FORMAT_ID,
    max_turns: int = 8,
) -> list[Position]:
    """Generate `n` random legal positions, reproducibly from `seed`.

    Choices come from Showdown's own RandomPlayerAI, so legality (Choice locks,
    Encore, disabled moves, target legality, forced switches) is the simulator's
    definition rather than a reimplementation of it.
    """
    rng = random.Random(seed)
    positions: list[Position] = []

    for _ in range(n):
        battle_seed = [rng.randrange(0x10000) for _ in range(4)]
        n_turns = rng.randrange(1, max_turns + 1)

        state = server.create(format_id, p1_team, p2_team, seed=battle_seed)
        handle = int(state["handle"])
        choices: list[dict[str, str | None]] = []
        try:
            for _turn in range(n_turns):
                if state["ended"]:
                    break
                choice_seed = [rng.randrange(0x10000) for _ in range(4)]
                chosen = server.call("randomChoice", handle=handle, seed=choice_seed)["choices"]
                step = {"p1": chosen.get("p1"), "p2": chosen.get("p2")}
                choices.append(step)
                state = server.step(handle, step["p1"], step["p2"])
        finally:
            server.destroy(handle)

        positions.append(
            Position(
                format_id=format_id,
                battle_seed=battle_seed,
                p1_team=p1_team,
                p2_team=p2_team,
                choices=choices,
            )
        )

    return positions


def _compare_outcomes(left: Outcome, right: Outcome) -> list[str]:
    differing = []
    for name in ("turn", "ended", "winner", "log_digest"):
        if getattr(left, name) != getattr(right, name):
            differing.append(name)
    return differing


def compare_engines(left: Engine, right: Engine, positions: list[Position]) -> list[Divergence]:
    """Replay every position through both engines and report disagreements."""
    divergences = []
    for position in positions:
        left_outcome = left.rollout(position)
        right_outcome = right.rollout(position)
        differing = _compare_outcomes(left_outcome, right_outcome)
        if differing:
            divergences.append(Divergence(position, left_outcome, right_outcome, differing))
    return divergences


def check_determinism(engine: Engine, positions: list[Position]) -> list[Divergence]:
    """Replay each position twice through one engine and report disagreements.

    What the harness does at M0: an engine that is not deterministic under a
    fixed seed cannot be differentially tested against anything, so this is the
    precondition for every later comparison.
    """
    return compare_engines(engine, engine, positions)

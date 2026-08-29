"""T0.10: random legal positions are self-consistent under a fixed seed, and the
harness exposes a clean interface for plugging in a second implementation."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from champions.harness.differential import (
    Engine,
    Outcome,
    Position,
    ShowdownEngine,
    check_determinism,
    compare_engines,
    generate_positions,
)
from champions.search.oracle import SimServer
from champions.teams import ALPHA, BETA, load_team

# The acceptance criterion is 1,000 positions; scripts/differential.py runs that
# and is the recorded evidence. The suite runs a smaller sample so it stays fast
# enough to run on every change.
N_POSITIONS = 120


@pytest.fixture(scope="module")
def sim() -> Iterator[SimServer]:
    with SimServer() as server:
        yield server


@pytest.fixture(scope="module")
def positions(sim: SimServer) -> list[Position]:
    return generate_positions(
        sim, N_POSITIONS, seed=0, p1_team=load_team(ALPHA), p2_team=load_team(BETA)
    )


def test_generator_produces_distinct_positions(positions: list[Position]) -> None:
    assert len(positions) == N_POSITIONS
    assert len({p.digest() for p in positions}) == N_POSITIONS


def test_generated_positions_have_legal_choice_scripts(positions: list[Position]) -> None:
    for position in positions:
        assert position.choices, "position has no choices"
        assert len(position.battle_seed) == 4


def test_generation_is_reproducible_from_its_seed(sim: SimServer) -> None:
    alpha, beta = load_team(ALPHA), load_team(BETA)
    first = generate_positions(sim, 10, seed=42, p1_team=alpha, p2_team=beta)
    second = generate_positions(sim, 10, seed=42, p1_team=alpha, p2_team=beta)

    assert [p.digest() for p in first] == [p.digest() for p in second]


def test_positions_are_self_consistent_under_a_fixed_seed(
    sim: SimServer, positions: list[Position]
) -> None:
    divergences = check_determinism(ShowdownEngine(sim), positions)
    assert divergences == [], f"{len(divergences)} divergences: {divergences[:3]}"


def test_log_digest_ignores_wall_clock_timestamps() -> None:
    """Showdown emits |t:|<unix seconds> per turn. Including it in the digest
    made ~0.5-0.8% of positions falsely diverge whenever two replays straddled
    a second boundary, with identical turn, ended, and winner."""
    base = ["|t:|1000", "|gametype|doubles", "|turn|1"]
    later = ["|t:|9999", "|gametype|doubles", "|turn|1"]

    assert Outcome.digest_log(base) == Outcome.digest_log(later)


def test_log_digest_still_detects_real_differences() -> None:
    assert Outcome.digest_log(["|turn|1"]) != Outcome.digest_log(["|turn|2"])


def test_compare_engines_reports_divergence_between_two_implementations(
    sim: SimServer, positions: list[Position]
) -> None:
    """The seam a second implementation plugs into at M8."""

    class WrongEngine:
        """Stands in for an engine that diverges, e.g. on a modified move."""

        def __init__(self, inner: Engine) -> None:
            self._inner = inner

        def rollout(self, position: Position) -> Outcome:
            honest = self._inner.rollout(position)
            return Outcome(
                turn=honest.turn + 1,
                ended=honest.ended,
                winner=honest.winner,
                log_digest="wrong",
            )

    reference = ShowdownEngine(sim)
    sample = positions[:5]

    divergences = compare_engines(reference, WrongEngine(reference), sample)

    assert len(divergences) == len(sample)
    for divergence in divergences:
        assert "turn" in divergence.fields
        assert "log_digest" in divergence.fields
        assert divergence.position.digest() in str(divergence)


def test_engine_protocol_is_satisfied_by_the_reference(sim: SimServer) -> None:
    engine: Engine = ShowdownEngine(sim)
    assert hasattr(engine, "rollout")

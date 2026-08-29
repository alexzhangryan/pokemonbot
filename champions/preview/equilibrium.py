"""The team preview equilibrium: 15 x 15 for the bring, 6 x 6 for the leads.

`docs/04-decision-engine.md` section 6. Choosing four of six is a simultaneous
move game with 15 options a side, so 225 cells -- small enough to solve exactly,
which is true of no node inside a battle. Lead selection nests inside it as a
6 x 6 subgame over pairs from the chosen four.

The solve is minimax, so it needs no opponent model at all. That is worth being
explicit about, because M4 also produces a bring predictor and the two answer
different questions: the equilibrium asks what to play against an opponent who
plays well, the predictor asks what this opponent will actually do. Playing a
best response to the predictor is available here as `best_response`, and is the
more exploitative and more exploitable of the two.

What the solve does need is a value for each cell, and that is the part M4 could
not source from the corpus -- see `value.py` and the finding recorded in
`docs/STATUS.md`. The value function is therefore an argument, not a fixed
dependency, so a better one drops in without touching any of this.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from champions.preview.dataset import BRING_SIZE, LEAD_SIZE, subsets
from champions.search.matrix import Equilibrium, solve_both

#: A value function scores our four against their four as a win probability.
#: It must be antisymmetric -- value(a, b) + value(b, a) == 1 -- or the matrix
#: game is not zero sum and `solve_both` will refuse it.
ValueFunction = Callable[[Sequence[str], Sequence[str]], float]

ANTISYMMETRY_TOLERANCE = 1e-6


class NotAntisymmetricError(ValueError):
    """Raised when a value function fails value(a, b) + value(b, a) == 1."""


@dataclass(frozen=True)
class PreviewSolution:
    """The equilibrium over bring-4 subsets, plus what a person needs to read it."""

    our_options: tuple[tuple[str, ...], ...]
    their_options: tuple[tuple[str, ...], ...]
    payoff: np.ndarray
    equilibrium: Equilibrium

    @property
    def value(self) -> float:
        return float(self.equilibrium.value)

    @property
    def strategy(self) -> np.ndarray:
        return self.equilibrium.row

    def support(self, threshold: float = 1e-4) -> list[tuple[tuple[str, ...], float]]:
        """The bring-4s the equilibrium actually plays, with their weights."""
        return sorted(
            ((self.our_options[i], float(p)) for i, p in enumerate(self.strategy) if p > threshold),
            key=lambda pair: -pair[1],
        )

    def best_single(self) -> tuple[str, ...]:
        return self.our_options[int(np.argmax(self.strategy))]

    def as_report(self, limit: int = 5) -> str:
        lines = [
            f"game value {self.value:.4f}, support {len(self.support())} of {len(self.our_options)}"
        ]
        for option, weight in self.support()[:limit]:
            lines.append(f"  {weight:6.1%}  {', '.join(option)}")
        return "\n".join(lines)


def check_antisymmetry(value: ValueFunction, ours: Sequence[str], theirs: Sequence[str]) -> None:
    """Fail loudly rather than solve a game that is not zero sum."""
    total = value(ours, theirs) + value(theirs, ours)
    if abs(total - 1.0) > ANTISYMMETRY_TOLERANCE:
        raise NotAntisymmetricError(
            f"value(a, b) + value(b, a) = {total}, expected 1. "
            "A preview payoff must be zero sum; see champions/preview/value.py."
        )


def payoff_matrix(
    our_team: Sequence[str], their_team: Sequence[str], value: ValueFunction
) -> tuple[np.ndarray, list[tuple[str, ...]], list[tuple[str, ...]]]:
    """The 15 x 15 of win probabilities, and the option lists they are indexed by."""
    our_options = [tuple(our_team[i] for i in s) for s in subsets(len(our_team), BRING_SIZE)]
    their_options = [tuple(their_team[i] for i in s) for s in subsets(len(their_team), BRING_SIZE)]
    check_antisymmetry(value, our_options[0], their_options[0])
    matrix = np.array([[value(ours, theirs) for theirs in their_options] for ours in our_options])
    return matrix, our_options, their_options


def solve_preview(
    our_team: Sequence[str], their_team: Sequence[str], value: ValueFunction
) -> PreviewSolution:
    """Solve the bring-4 game exactly.

    The payoff is shifted to zero sum before the LP: win probabilities live in
    [0, 1] and sum to 1 across a cell pair, so `p - 0.5` is the zero sum form.
    The reported value is shifted back, because a win probability is what every
    consumer of this wants to read.
    """
    matrix, our_options, their_options = payoff_matrix(our_team, their_team, value)
    equilibrium = solve_both(matrix - 0.5)
    shifted = Equilibrium(
        row=equilibrium.row, column=equilibrium.column, value=equilibrium.value + 0.5
    )
    return PreviewSolution(
        our_options=tuple(our_options),
        their_options=tuple(their_options),
        payoff=matrix,
        equilibrium=shifted,
    )


def solve_leads(
    our_four: Sequence[str], their_four: Sequence[str], value: ValueFunction
) -> PreviewSolution:
    """The 6 x 6 lead subgame, once both brings are fixed."""
    our_options = [tuple(our_four[i] for i in s) for s in subsets(len(our_four), LEAD_SIZE)]
    their_options = [tuple(their_four[i] for i in s) for s in subsets(len(their_four), LEAD_SIZE)]
    check_antisymmetry(value, our_options[0], their_options[0])
    matrix = np.array([[value(ours, theirs) for theirs in their_options] for ours in our_options])
    equilibrium = solve_both(matrix - 0.5)
    return PreviewSolution(
        our_options=tuple(our_options),
        their_options=tuple(their_options),
        payoff=matrix,
        equilibrium=Equilibrium(
            row=equilibrium.row, column=equilibrium.column, value=equilibrium.value + 0.5
        ),
    )


def best_response(
    our_team: Sequence[str],
    their_team: Sequence[str],
    value: ValueFunction,
    their_distribution: np.ndarray,
) -> tuple[tuple[str, ...], float]:
    """The bring-4 that maximises value against a predicted opponent distribution.

    The exploitative alternative to the equilibrium, for use with the M4 bring
    predictor. It scores better against an opponent who plays as predicted and
    worse against one who does not, which is the whole trade and is why both are
    offered rather than one being chosen here.
    """
    matrix, our_options, _ = payoff_matrix(our_team, their_team, value)
    expected = matrix @ np.asarray(their_distribution, dtype=float)
    best = int(np.argmax(expected))
    return our_options[best], float(expected[best])

"""The matrix game solve.

Moves in doubles are simultaneous, so the root decision is not an argmax. It is
the mixed strategy Nash equilibrium of a zero sum payoff matrix, obtained by
linear program (`docs/04-decision-engine.md` section 2).

This matters for reasons specific to this game rather than as a refinement.
Protect, Fake Out and redirection are pure prediction interactions: an opponent
who learns the agent's deterministic response to a position beats it every time
from then on. Mixing is the correct solution concept, not a hedge.

The row player maximizes:

    max_{x >= 0, 1'x = 1}  min_j (A' x)_j

which is one small LP. At the sizes the policy layer produces -- ten candidates
a side, so a hundred cells -- this is microseconds and is not a cost centre.

The equilibrium value is also what makes the coach's ex-ante loss well defined,
so a correct value here is load bearing twice over.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog


class NotAZeroSumGameError(ValueError):
    pass


@dataclass(frozen=True)
class Equilibrium:
    """One solved game.

    `row` and `column` are probability vectors over each player's actions.
    `value` is the game's value to the row player, on whatever scale the payoff
    matrix used -- a win probability, if the payoffs came from `evaluate`.
    """

    row: np.ndarray
    column: np.ndarray
    value: float

    @property
    def is_pure(self) -> bool:
        """Whether the row strategy is (numerically) a single action.

        The time allocation rule in `docs/04-decision-engine.md` section 7 wants
        to commit early when the equilibrium is near pure, and the coach wants
        to know whether a turn had a forced answer.
        """
        return bool(np.max(self.row) > 1 - 1e-6)

    @property
    def support(self) -> list[int]:
        """Row actions carrying non-trivial probability mass.

        The pruning guard in section 3 is stated in terms of the unpruned
        equilibrium placing mass on a discarded action, so "carries mass" needs
        one definition rather than one per caller.
        """
        return [int(i) for i in np.flatnonzero(self.row > MASS_THRESHOLD)]


#: What counts as non-trivial probability mass. Used by the pruning guard and by
#: the coach; both need the same threshold or their numbers do not compare.
MASS_THRESHOLD = 1e-4


def _solve_for_row(payoff: np.ndarray) -> tuple[np.ndarray, float]:
    """Row player's maximin strategy and the resulting value.

    Variables are `[x_1..x_m, v]`. The LP is `min -v` subject to
    `v - sum_i A[i, j] x_i <= 0` for every column j, `sum_i x_i = 1`, `x >= 0`,
    with `v` free.

    The payoff is shifted to be strictly positive before solving and the shift
    is undone afterwards. This costs nothing -- adding a constant to every cell
    of a zero sum game shifts the value by that constant and leaves both
    equilibrium strategies untouched -- and it keeps the LP away from the
    degenerate `v = 0` corner that an all-zero payoff matrix would otherwise
    present, which is exactly the matrix a search produces when every candidate
    looks identical.
    """
    rows, columns = payoff.shape
    shift = float(np.min(payoff)) - 1.0
    shifted = payoff - shift

    # min -v  <=>  max v
    objective = np.zeros(rows + 1)
    objective[-1] = -1.0

    # For each column j: v - sum_i A[i, j] x_i <= 0
    inequality = np.zeros((columns, rows + 1))
    inequality[:, :rows] = -shifted.T
    inequality[:, -1] = 1.0

    equality = np.zeros((1, rows + 1))
    equality[0, :rows] = 1.0

    result = linprog(
        c=objective,
        A_ub=inequality,
        b_ub=np.zeros(columns),
        A_eq=equality,
        b_eq=np.ones(1),
        bounds=[(0.0, None)] * rows + [(None, None)],
        method="highs",
    )
    if not result.success:
        raise RuntimeError(f"Matrix game LP failed: {result.message}")

    strategy = np.asarray(result.x[:rows], dtype=float)
    # The LP returns a point on the simplex up to solver tolerance; clean it so
    # downstream sampling does not have to defend against a tiny negative.
    strategy = np.clip(strategy, 0.0, None)
    total = strategy.sum()
    strategy = strategy / total if total > 0 else np.full(rows, 1.0 / rows)
    return strategy, float(result.x[-1]) + shift


def solve_both(payoff: np.ndarray) -> Equilibrium:
    """Both players' equilibrium strategies and the value.

    The column player's strategy comes from solving the transposed, negated game
    rather than from reading the LP's dual. Both are correct; solving twice is
    obviously correct, and at a hundred cells the second solve is free. The two
    solves must agree on the value -- that is the minimax theorem, not an
    implementation detail -- so disagreement means something is wrong and is
    raised rather than averaged over.
    """
    payoff = np.asarray(payoff, dtype=float)
    if payoff.ndim != 2 or payoff.size == 0:
        raise ValueError(f"Payoff must be a non-empty 2D matrix, got shape {payoff.shape}")
    if not np.all(np.isfinite(payoff)):
        raise ValueError("Payoff matrix contains non-finite entries")

    row, value = _solve_for_row(payoff)
    column, negated = _solve_for_row(-payoff.T)

    if not np.isclose(value, -negated, atol=1e-6):
        raise NotAZeroSumGameError(
            f"The two solves disagree on the game value ({value} vs {-negated}). "
            "This should be impossible for a finite zero sum game."
        )
    return Equilibrium(row=row, column=column, value=value)


def solve(payoff: np.ndarray) -> tuple[np.ndarray, float]:
    """The row player's mixed strategy and the value of the game.

    The interface `docs/08-implementation-blueprint.md` section 3 names. Use
    `solve_both` when the opponent's strategy is wanted too, which the coach
    needs and the search does not.
    """
    equilibrium = solve_both(payoff)
    return equilibrium.row, equilibrium.value


def best_response_value(payoff: np.ndarray, column_strategy: np.ndarray) -> np.ndarray:
    """Each row action's expected value against a fixed column strategy.

    Not part of the solve, but the coach's ex-post analysis is exactly this
    against what the opponent actually did, and the policy benchmark uses it to
    ask what a pruned candidate set gave up.
    """
    return np.asarray(payoff, dtype=float) @ np.asarray(column_strategy, dtype=float)

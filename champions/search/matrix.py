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

from collections.abc import Mapping, Sequence
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


def solve_constrained(
    payoff: np.ndarray,
    kinds: Sequence[str],
    prior: Mapping[str, float],
    weight: float,
) -> Equilibrium:
    """The row player's best strategy against a column player whose *kind*
    marginals are partly pinned (D91).

    With probability `weight` the column player draws the kind of its joint
    action from `prior` and then plays the worst column of that kind for us;
    with probability `1 - weight` it plays the plain adversary. The row
    player's payoff is therefore

        (1 - w) * v + w * sum_k prior[k] * v_k

    where `v <= (x^T A)_j` for every column j and `v_k <= (x^T A)_j` for every
    column j of kind k. One LP in `[x, v, v_k...]`, the same shape as
    `_solve_for_row` with one extra value variable per kind; at `weight == 0`
    it is that LP exactly, and at `weight == 1` the plain `v` drops out.

    The column strategy is read off the duals of the two constraint families,
    which sum to one across the columns as the objective weights do, so
    `x^T A y == value` holds and the coach and the trace see one opponent
    mixture rather than a kind-by-kind one. `prior` must be over the kinds
    present in `kinds` and sum to one; `kinds` names one kind per column.
    """
    payoff = np.asarray(payoff, dtype=float)
    if payoff.ndim != 2 or payoff.size == 0:
        raise ValueError(f"Payoff must be a non-empty 2D matrix, got shape {payoff.shape}")
    if not np.all(np.isfinite(payoff)):
        raise ValueError("Payoff matrix contains non-finite entries")
    rows, columns = payoff.shape
    if len(kinds) != columns:
        raise ValueError(f"{len(kinds)} kinds for {columns} columns")
    weight = float(min(1.0, max(0.0, weight)))
    pinned = [k for k in dict.fromkeys(kinds) if prior.get(k, 0.0) > 0.0]
    if weight <= 0.0 or not pinned:
        return solve_both(payoff)
    total = sum(prior[k] for k in pinned)

    shift = float(np.min(payoff)) - 1.0
    shifted = payoff - shift
    free = weight < 1.0
    n_var = rows + (1 if free else 0) + len(pinned)
    k_index = {k: rows + (1 if free else 0) + i for i, k in enumerate(pinned)}

    objective = np.zeros(n_var)
    if free:
        objective[rows] = -(1.0 - weight)
    for k, i in k_index.items():
        objective[i] = -weight * prior[k] / total

    constraints: list[np.ndarray] = []
    families: list[tuple[str | None, int]] = []
    for j in range(columns):
        if free:
            row = np.zeros(n_var)
            row[:rows] = -shifted[:, j]
            row[rows] = 1.0
            constraints.append(row)
            families.append((None, j))
        k = kinds[j]
        if k in k_index:
            row = np.zeros(n_var)
            row[:rows] = -shifted[:, j]
            row[k_index[k]] = 1.0
            constraints.append(row)
            families.append((k, j))
    inequality = np.vstack(constraints)
    equality = np.zeros((1, n_var))
    equality[0, :rows] = 1.0

    result = linprog(
        c=objective,
        A_ub=inequality,
        b_ub=np.zeros(len(constraints)),
        A_eq=equality,
        b_eq=np.ones(1),
        bounds=[(0.0, None)] * rows + [(None, None)] * (n_var - rows),
        method="highs",
    )
    if not result.success:
        raise RuntimeError(f"Constrained matrix game LP failed: {result.message}")

    strategy = np.clip(np.asarray(result.x[:rows], dtype=float), 0.0, None)
    total_x = strategy.sum()
    strategy = strategy / total_x if total_x > 0 else np.full(rows, 1.0 / rows)

    column = np.zeros(columns)
    marginals = np.asarray(result.ineqlin.marginals, dtype=float)
    for (_, j), m in zip(families, marginals, strict=True):
        column[j] += max(0.0, -m)
    total_y = column.sum()
    if total_y > 0:
        column = column / total_y
    else:
        # A degenerate dual (every constraint slack at the optimum, which the
        # shift makes impossible in exact arithmetic). Fall back to the
        # adversary within the pinned kinds, so the trace still has a column.
        column = _pinned_best_response(shifted, strategy, kinds, prior, pinned, total)
    value = float(strategy @ payoff @ column)
    return Equilibrium(row=strategy, column=column, value=value)


def _pinned_best_response(
    shifted: np.ndarray,
    strategy: np.ndarray,
    kinds: Sequence[str],
    prior: Mapping[str, float],
    pinned: Sequence[str],
    total: float,
) -> np.ndarray:
    against = strategy @ shifted
    column = np.zeros(shifted.shape[1])
    for k in pinned:
        members = [j for j, kind in enumerate(kinds) if kind == k]
        worst = min(members, key=lambda j: against[j])
        column[worst] += prior[k] / total
    return column


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

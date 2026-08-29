"""M2: the matrix game solve, checked against games whose equilibria are known
in closed form.

A solver that is merely plausible is worse than none here: the value it returns
is what the coach's ex-ante loss is measured against, so a systematically wrong
value would produce confident, wrong coaching. Every case below has an answer
that can be written down without running the code.
"""

from __future__ import annotations

import numpy as np
import pytest

from champions.search.matrix import (
    MASS_THRESHOLD,
    Equilibrium,
    best_response_value,
    solve,
    solve_both,
)


def test_matching_pennies_is_uniform_with_value_zero() -> None:
    """The canonical case where argmax is exploitable and mixing is not optional."""
    payoff = np.array([[1.0, -1.0], [-1.0, 1.0]])

    equilibrium = solve_both(payoff)

    assert equilibrium.value == pytest.approx(0.0, abs=1e-9)
    assert equilibrium.row == pytest.approx([0.5, 0.5], abs=1e-6)
    assert equilibrium.column == pytest.approx([0.5, 0.5], abs=1e-6)
    assert not equilibrium.is_pure


def test_rock_paper_scissors_is_uniform_with_value_zero() -> None:
    payoff = np.array(
        [
            [0.0, -1.0, 1.0],
            [1.0, 0.0, -1.0],
            [-1.0, 1.0, 0.0],
        ]
    )

    equilibrium = solve_both(payoff)

    assert equilibrium.value == pytest.approx(0.0, abs=1e-9)
    assert equilibrium.row == pytest.approx([1 / 3, 1 / 3, 1 / 3], abs=1e-6)
    assert equilibrium.column == pytest.approx([1 / 3, 1 / 3, 1 / 3], abs=1e-6)


def test_a_saddle_point_game_solves_to_a_pure_strategy() -> None:
    """When one action dominates, the equilibrium is pure and says so.

    The time allocation rule commits early on a near-pure equilibrium, so
    `is_pure` being right on an obvious case is load bearing.
    """
    payoff = np.array([[4.0, 3.0], [2.0, 1.0]])

    equilibrium = solve_both(payoff)

    assert equilibrium.value == pytest.approx(3.0, abs=1e-9)
    assert equilibrium.row == pytest.approx([1.0, 0.0], abs=1e-6)
    assert equilibrium.is_pure
    assert equilibrium.support == [0]


def test_a_known_asymmetric_game() -> None:
    """Row mixes 1/2, column mixes 1/2, value 0.5 -- worked out by hand.

    For [[1, 0], [0, 1]] the row player equalises the column player's payoffs at
    x = (1/2, 1/2) and the value is 1/2. An off-by-one in the LP's constraint
    orientation still gives 0.5 on a symmetric game, which is why this one is
    here alongside the asymmetric case below.
    """
    assert solve_both(np.array([[1.0, 0.0], [0.0, 1.0]])).value == pytest.approx(0.5)

    # Row 0 dominates on column 1 but not column 0, so both are used.
    # Equalising: 2p + 0(1-p) = 0p + 1(1-p)  =>  p = 1/3, value = 2/3.
    payoff = np.array([[2.0, 0.0], [0.0, 1.0]])
    equilibrium = solve_both(payoff)
    assert equilibrium.row == pytest.approx([1 / 3, 2 / 3], abs=1e-6)
    assert equilibrium.value == pytest.approx(2 / 3, abs=1e-6)


def test_the_value_is_shift_invariant_and_the_strategies_are_not_disturbed() -> None:
    """Adding a constant to every cell shifts the value by it and nothing else.

    The solver relies on this to move an all-equal matrix off the degenerate
    corner, so it is asserted rather than assumed.
    """
    payoff = np.array([[1.0, -1.0], [-2.0, 3.0]])
    base = solve_both(payoff)

    for offset in (-10.0, 0.25, 1000.0):
        shifted = solve_both(payoff + offset)
        assert shifted.value == pytest.approx(base.value + offset, abs=1e-6)
        assert shifted.row == pytest.approx(base.row, abs=1e-6)
        assert shifted.column == pytest.approx(base.column, abs=1e-6)


def test_an_all_equal_matrix_solves_rather_than_degenerating() -> None:
    """The matrix a search produces when every candidate looks the same.

    It must not fail, and its value must be the shared payoff. Which strategy
    comes back is arbitrary -- every one is optimal -- so only the simplex
    property is asserted.
    """
    for constant in (0.0, 0.5, -3.0):
        equilibrium = solve_both(np.full((5, 4), constant))
        assert equilibrium.value == pytest.approx(constant, abs=1e-6)
        assert equilibrium.row.sum() == pytest.approx(1.0)
        assert np.all(equilibrium.row >= 0)


def test_degenerate_shapes_solve() -> None:
    """One row, one column, and one cell. A forced move is a real position."""
    single = solve_both(np.array([[0.7]]))
    assert single.value == pytest.approx(0.7)
    assert single.row == pytest.approx([1.0])
    assert single.is_pure

    one_row = solve_both(np.array([[0.2, 0.9, 0.5]]))
    assert one_row.value == pytest.approx(0.2)  # the opponent picks the worst cell
    assert one_row.row == pytest.approx([1.0])

    one_column = solve_both(np.array([[0.2], [0.9], [0.5]]))
    assert one_column.value == pytest.approx(0.9)  # we pick the best
    assert one_column.row == pytest.approx([0.0, 1.0, 0.0], abs=1e-6)


def test_strategies_are_valid_probability_distributions() -> None:
    rng = np.random.default_rng(0)
    for _ in range(30):
        payoff = rng.normal(size=(rng.integers(1, 9), rng.integers(1, 9)))
        equilibrium = solve_both(payoff)
        for strategy in (equilibrium.row, equilibrium.column):
            assert strategy.sum() == pytest.approx(1.0, abs=1e-9)
            assert np.all(strategy >= 0.0)


def test_the_equilibrium_is_actually_an_equilibrium() -> None:
    """The defining property: neither player can gain by deviating.

    Every pure deviation is checked against the value. This is the test that
    would catch a solver that returns a plausible-looking but wrong strategy,
    which no amount of comparing against hand-computed examples can do in
    general.
    """
    rng = np.random.default_rng(7)
    for _ in range(30):
        payoff = rng.normal(size=(rng.integers(2, 7), rng.integers(2, 7)))
        equilibrium = solve_both(payoff)

        # No pure column response beats the value against our row strategy.
        against_row = equilibrium.row @ payoff
        assert np.min(against_row) >= equilibrium.value - 1e-6

        # No pure row response beats the value against their column strategy.
        against_column = payoff @ equilibrium.column
        assert np.max(against_column) <= equilibrium.value + 1e-6


def test_the_solve_is_deterministic() -> None:
    """`CLAUDE.md`: deterministic by default. Two runs, identical output.

    Without this the agent makes different choices on reruns of the same
    position, which destroys the coach's reproducibility and makes the trace
    worthless for debugging.
    """
    payoff = np.array([[0.6, 0.2, 0.9], [0.1, 0.8, 0.3], [0.4, 0.4, 0.4]])
    first = solve_both(payoff)
    for _ in range(5):
        again = solve_both(payoff)
        assert np.array_equal(first.row, again.row)
        assert np.array_equal(first.column, again.column)
        assert first.value == again.value


def test_support_uses_the_shared_mass_threshold() -> None:
    equilibrium = Equilibrium(
        row=np.array([1.0 - MASS_THRESHOLD, MASS_THRESHOLD / 2]),
        column=np.array([1.0, 0.0]),
        value=0.0,
    )
    assert equilibrium.support == [0]


def test_best_response_value_is_the_expected_payoff_per_row() -> None:
    payoff = np.array([[1.0, 0.0], [0.0, 1.0]])
    values = best_response_value(payoff, np.array([0.25, 0.75]))
    assert values == pytest.approx([0.25, 0.75])


def test_the_documented_two_tuple_interface_still_works() -> None:
    """`matrix.solve(payoff) -> (strategy, value)` per the blueprint."""
    strategy, value = solve(np.array([[1.0, -1.0], [-1.0, 1.0]]))
    assert strategy == pytest.approx([0.5, 0.5], abs=1e-6)
    assert value == pytest.approx(0.0, abs=1e-9)


def test_malformed_payoffs_are_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty 2D"):
        solve_both(np.array([]))
    with pytest.raises(ValueError, match="non-empty 2D"):
        solve_both(np.array([1.0, 2.0]))
    with pytest.raises(ValueError, match="non-finite"):
        solve_both(np.array([[1.0, np.nan], [0.0, 1.0]]))

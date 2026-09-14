"""M11: time allocation, as arithmetic.

`docs/04-decision-engine.md` section 7 and D7. The turn limit was always
enforced by the watchdog; the 7 minute player clock is a total that nothing
enforced. These pin down the allocation rule: a budget never exceeds the turn
limit, never exceeds what is left, shrinks as the clock is spent, and leaves
the reserve alone; and the decisiveness test that decides whether the second
stage runs at all.
"""

from __future__ import annotations

import numpy as np
import pytest

from champions.search.clock import (
    DECISIVE_GAP,
    DEFAULT_RESERVE_S,
    MIN_TURN_BUDGET_S,
    PLAYER_CLOCK_S,
    TURN_LIMIT_S,
    ClockState,
    is_decisive,
    turn_budget,
)


def test_a_fresh_clock_offers_a_share_of_the_clock_and_never_the_whole_limit_early() -> None:
    budget = turn_budget(0.0, 1)
    assert 0 < budget <= TURN_LIMIT_S
    assert budget == pytest.approx((PLAYER_CLOCK_S - DEFAULT_RESERVE_S) / 12)


def test_the_budget_never_exceeds_the_turn_limit_or_what_is_left() -> None:
    for spent in (0.0, 100.0, 300.0, 400.0, 419.0, 420.0, 500.0):
        for turn in (1, 5, 12, 30):
            budget = turn_budget(spent, turn)
            assert budget <= TURN_LIMIT_S
            assert budget <= max(0.0, PLAYER_CLOCK_S - spent) + 1e-9
            assert budget >= 0.0


def test_the_share_shrinks_as_the_clock_is_spent_and_grows_as_the_game_runs_short() -> None:
    assert turn_budget(200.0, 3) < turn_budget(0.0, 3)
    # Fewer turns expected to remain, more per turn -- up to the limit.
    assert turn_budget(0.0, 11) > turn_budget(0.0, 1)
    assert turn_budget(0.0, 12) == TURN_LIMIT_S


def test_the_reserve_is_left_alone_until_nothing_else_remains() -> None:
    # Everything but the reserve spent: the share is zero, the minimum applies.
    spent = PLAYER_CLOCK_S - DEFAULT_RESERVE_S
    assert turn_budget(spent, 5) == pytest.approx(MIN_TURN_BUDGET_S)
    # Into the reserve: still the minimum, never more than what is left.
    assert turn_budget(PLAYER_CLOCK_S - 1.0, 5) == pytest.approx(1.0)
    assert turn_budget(PLAYER_CLOCK_S, 5) == 0.0


def test_the_clock_state_accumulates_and_reports_what_remains() -> None:
    clock = ClockState()
    clock.record(1.5)
    clock.record(2.5)
    clock.record(-1.0)
    assert clock.spent_s == pytest.approx(4.0)
    assert clock.decisions == 3
    assert clock.remaining_s() == pytest.approx(PLAYER_CLOCK_S - 4.0)


def test_decisive_needs_a_pure_equilibrium_and_a_wide_gap() -> None:
    pure_wide = is_decisive(np.array([1.0, 0.0, 0.0]), np.array([0.7, 0.6, 0.5]))
    assert pure_wide == (True, pytest.approx(0.1))
    pure_narrow, margin = is_decisive(np.array([1.0, 0.0]), np.array([0.7, 0.68]))
    assert not pure_narrow and margin == pytest.approx(0.02)
    mixed, _ = is_decisive(np.array([0.6, 0.4]), np.array([0.6, 0.6]))
    assert not mixed
    assert is_decisive(np.array([1.0]), np.array([0.5]))[0], "one row is decided by definition"
    assert is_decisive(np.array([1.0, 0.0]), np.array([0.7, 0.6]), gap=DECISIVE_GAP)[0]

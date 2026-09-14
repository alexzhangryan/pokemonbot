"""Time allocation: how long one decision may take, given the whole clock.

`docs/04-decision-engine.md` section 7 and D7. The clock was tracked from M0
and optimised at M11, which is here. Two numbers bound a decision and only one
of them was ever enforced: the 45 second turn limit, which the watchdog has
honoured since the first game, and the 7 minute player clock, which is a
*total* -- spending the full 45 s every turn forfeits a long game after nine
turns. `docs/specs/2026-09-13-engine-gate.md` section 2 named that gap and
left the allocation to M11.

## The rule

A turn's budget is the smaller of the turn limit and an even share of what is
left of the player clock, after a reserve, over the turns the game is expected
to still run. Early turns get a generous share; a game that runs long sees its
budget shrink rather than its clock run out; a decision never asks for more
than the limit. The reserve keeps forced switches and the endgame from being
decided on an empty clock.

The intended escalation rule of section 7 is the other half: run a cheap first
pass, commit at once when the equilibrium is near pure and the gap between the
best and second-best action is wide, and spend the rest of the budget only
when it is not. `is_decisive` is that test. The cheap pass here is the one-ply
solve and the escalation is one more ply (`champions/agents/adaptive.py`); M8
measured the extra ply as not apart from the incumbent (D71), so what M11
buys is not win rate but the structure -- a budget that cannot be exceeded and
is spent where the decision is close.

Everything here is arithmetic on floats so it is testable without a battle.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Showdown's VGC Timer, per turn and per player. The harness reports against
#: the same two numbers (`champions/harness/ladder.py`).
TURN_LIMIT_S = 45.0
PLAYER_CLOCK_S = 7 * 60.0

#: Kept back for forced switches, which are decisions the allocator does not
#: see coming, and for the last turns of a long game.
DEFAULT_RESERVE_S = 30.0

#: How long a game is expected to run. The M8 gate traces average about nine
#: turns and the long tail reaches twenty; twelve leaves the tail a budget.
DEFAULT_EXPECTED_TURNS = 12

#: The least a turn is offered while any clock remains, so a decision always
#: has time to prune and propose. The one-ply decision takes about 0.1 s.
MIN_TURN_BUDGET_S = 2.0

#: A decision is decisive when the equilibrium is pure and the best row beats
#: the second-best by at least this much against the equilibrium column mix,
#: in win-probability points. Below it, the budget is worth spending.
DECISIVE_GAP = 0.05


@dataclass
class ClockState:
    """What one battle has spent so far."""

    spent_s: float = 0.0
    decisions: int = 0

    def record(self, elapsed_s: float) -> None:
        self.spent_s += max(0.0, float(elapsed_s))
        self.decisions += 1

    def remaining_s(self, player_clock_s: float = PLAYER_CLOCK_S) -> float:
        return max(0.0, player_clock_s - self.spent_s)


def turn_budget(
    spent_s: float,
    turn: int,
    *,
    expected_turns: int = DEFAULT_EXPECTED_TURNS,
    reserve_s: float = DEFAULT_RESERVE_S,
    turn_limit_s: float = TURN_LIMIT_S,
    player_clock_s: float = PLAYER_CLOCK_S,
    min_budget_s: float = MIN_TURN_BUDGET_S,
) -> float:
    """Seconds this decision may take.

    `turn` is one-based; a forced switch inside a turn is charged to that
    turn. The share is over the turns expected to remain, never fewer than
    one, and the result never exceeds the turn limit nor what is actually
    left of the clock.
    """
    remaining = max(0.0, player_clock_s - spent_s)
    spendable = max(0.0, remaining - reserve_s)
    turns_left = max(1, expected_turns - max(1, turn) + 1)
    share = spendable / turns_left
    budget = max(share, min(min_budget_s, remaining))
    return float(min(turn_limit_s, budget, remaining))


def is_decisive(
    row: np.ndarray, ante_values: np.ndarray, gap: float = DECISIVE_GAP
) -> tuple[bool, float]:
    """Whether a solved game needs no more search, and the gap it rests on.

    `row` is the equilibrium's row strategy and `ante_values` each row's value
    against the equilibrium column mix. Pure and wide means decisive; anything
    mixed is by definition close, since two rows share the value.
    """
    row = np.asarray(row, dtype=float)
    values = np.asarray(ante_values, dtype=float)
    if values.size < 2:
        return True, float("inf")
    pure = bool(row.max() > 1 - 1e-6)
    ordered = np.sort(values)[::-1]
    margin = float(ordered[0] - ordered[1])
    return pure and margin >= gap, margin

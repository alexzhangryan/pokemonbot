"""Statistics for evaluation results.

No claims without measurement: a win rate is reported with an interval, never
alone (CLAUDE.md conventions).
"""

from __future__ import annotations

import math

# 95% two-sided normal quantile.
Z_95 = 1.959963984540054


def wilson_interval(wins: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because evaluation runs are small
    (50-200 games) and win rates can sit near 0 or 1, where the normal interval
    produces bounds outside [0, 1] and badly wrong coverage.
    """
    if n == 0:
        return (0.0, 1.0)

    p = wins / n
    denominator = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denominator
    margin = (z / denominator) * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return (max(0.0, center - margin), min(1.0, center + margin))


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile; q in [0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (q / 100) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

"""M6: fitting the evaluation function, and the guards on the claim.

The fit itself is a logistic regression and is not interesting. What these tests
protect are the four things that make its output trustworthy, each of which was
wrong at some point while it was being written:

- the split groups by battle, so a held-out number is actually held out;
- no intercept survives anywhere, including in the calibration;
- the bootstrap intervals resample battles rather than positions;
- a reliability diagram over empty bins does not flatter itself.
"""

from __future__ import annotations

import numpy as np
import pytest

from champions.search import fit
from champions.search.positions import Position


def make(n_battles: int, per_battle: int = 12, seed: int = 0, signal: float = 1.5) -> fit.Dataset:
    """A synthetic source with a known truth.

    One feature carries the signal and one is pure noise, and every position in a
    battle shares that battle's label -- which is the structure the real data
    has and the reason splitting by row would cheat.
    """
    rng = np.random.default_rng(seed)
    rows: list[Position] = []
    for battle in range(n_battles):
        edge = float(rng.normal())
        label = int(rng.random() < 1 / (1 + np.exp(-signal * edge)))
        for turn in range(per_battle):
            rows.append(
                Position(
                    features={
                        "pokemon_advantage": edge + float(rng.normal(0, 0.05)),
                        "hazard_advantage": float(rng.normal()),
                        "faint_swing": 0.0,
                    },
                    label=label,
                    source="synthetic",
                    battle_id=f"b{battle}",
                    turn=turn + 1,
                )
            )
    return fit.to_dataset(rows, "synthetic")


# -- the design matrix -------------------------------------------------------


def test_faint_swing_is_not_a_fitted_feature() -> None:
    """A wiped side is a decided game, short circuited in `evaluate`. Given a
    weight, the optimiser would spend it on positions that are not decided."""
    data = make(20)
    assert "faint_swing" not in data.names


def test_the_split_keeps_a_battle_whole() -> None:
    """Twenty positions from one game share a result and most of a board.
    Splitting them at random puts near-duplicates of a training row into the
    test set and reports a held-out number that is not held out."""
    data = make(60)
    parts = fit.split_by_battle(data, seed=3)
    seen = [set(part.groups.tolist()) for part in parts]
    assert seen[0] and seen[1] and seen[2]
    assert seen[0].isdisjoint(seen[1])
    assert seen[0].isdisjoint(seen[2])
    assert seen[1].isdisjoint(seen[2])


def test_both_viewpoints_of_one_battle_stay_together() -> None:
    """The corpus emits two near-mirror rows per replay under `id:p1` and
    `id:p2`. Split apart, a game appears on both sides of the split wearing the
    other player's hat."""
    rows = [
        Position({"pokemon_advantage": 1.0}, 1, "corpus", "rep-1:p1", 1),
        Position({"pokemon_advantage": -1.0}, 0, "corpus", "rep-1:p2", 1),
        Position({"pokemon_advantage": 0.5}, 1, "corpus", "rep-2:p1", 1),
        Position({"pokemon_advantage": -0.5}, 0, "corpus", "rep-2:p2", 1),
    ]
    parts = fit.split_by_battle(fit.to_dataset(rows), fractions=(0.5, 0.25, 0.25), seed=0)
    for part in parts:
        stems = {str(g).split(":")[0] for g in part.groups}
        # Whichever part a replay landed in, it brought both of its sides.
        for stem in stems:
            assert sum(1 for g in part.groups if str(g).startswith(stem)) == 2


# -- no intercept, anywhere --------------------------------------------------


def test_the_fit_carries_no_intercept() -> None:
    """A dead-even position is the zero vector and has to score 0.5, which is
    what lets the matrix game treat the payoff as zero sum."""
    model = fit.fit(make(200))
    assert model.win_prob({name: 0.0 for name in model.weights}) == pytest.approx(0.5)


def test_calibration_does_not_reintroduce_one() -> None:
    """Textbook Platt scaling is `a * x + b`, and fitting the `b` puts the
    intercept straight back: an even position scored 0.518."""
    data = make(200)
    train, validation, _ = fit.split_by_battle(data, seed=1)
    model = fit.calibrate(fit.fit(train), validation)
    assert model.platt_b == 0.0
    assert model.win_prob({name: 0.0 for name in model.weights}) == pytest.approx(0.5)


def test_the_free_intercept_is_reported_even_though_it_is_not_used() -> None:
    """It is the diagnostic that would have caught the six-against-four counting
    bug in `evaluate.py`: features that are not really differences show up as an
    intercept away from zero."""
    model = fit.fit(make(200))
    assert isinstance(model.free_intercept, float)


# -- the intervals -----------------------------------------------------------


def test_a_signal_feature_gets_an_interval_that_excludes_zero() -> None:
    intervals = fit.bootstrap_weights(make(300, signal=2.0), resamples=60)
    low, high = intervals["pokemon_advantage"]
    assert low > 0.0, (low, high)
    assert "pokemon_advantage" not in fit.undetermined(intervals)


def test_a_noise_feature_is_reported_as_undetermined() -> None:
    """The point of the whole exercise. Self-play put `status_advantage` at
    -1.34 -- the wrong sign, stated as confidently as the six numbers beside
    it -- off 291 rows out of 11,774."""
    intervals = fit.bootstrap_weights(make(300, signal=2.0), resamples=60)
    low, high = intervals["hazard_advantage"]
    assert low <= 0.0 <= high, (low, high)
    assert "hazard_advantage" in fit.undetermined(intervals)


def test_intervals_resample_battles_not_positions() -> None:
    """Resampling positions would report an interval about `per_battle` times too
    narrow, because positions inside a game are near duplicates. Checked by
    holding the battles fixed and multiplying the positions: a row-resampler
    would tighten, a battle-resampler must not.
    """

    def width(per_battle: int) -> float:
        data = make(120, per_battle=per_battle, seed=5)
        low, high = fit.bootstrap_weights(data, resamples=60)["pokemon_advantage"]
        return high - low

    assert width(24) > 0.5 * width(6)


# -- the diagram -------------------------------------------------------------


def test_empty_bins_are_dropped_rather_than_scored_as_perfect() -> None:
    """An empty bucket has a zero gap by construction. Counting it averages the
    expected calibration error over nothing and flatters the result."""
    probs = np.array([0.05, 0.06, 0.95, 0.96])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    reliability = fit.reliability(probs, y, bins=10)
    assert len(reliability.bins) == 2
    assert reliability.ece == pytest.approx(0.0, abs=0.06)


def test_a_confident_and_wrong_model_reports_a_large_calibration_error() -> None:
    probs = np.array([0.95, 0.95, 0.95, 0.95])
    y = np.array([0.0, 1.0, 0.0, 0.0])
    reliability = fit.reliability(probs, y, bins=10)
    assert reliability.ece == pytest.approx(0.70, abs=0.01)
    assert reliability.mce == pytest.approx(0.70, abs=0.01)


def test_the_base_rate_is_reported_beside_the_loss() -> None:
    """A fit that does not beat always-predict-the-base-rate has learned
    nothing, whatever its absolute log loss looks like."""
    data = make(80)
    metrics = fit.metrics(np.full(len(data), float(data.y.mean())), data)
    assert metrics.log_loss == pytest.approx(metrics.base_log_loss, abs=1e-9)


def test_auc_averages_tied_predictions() -> None:
    """A model that emits one probability everywhere ranks nothing, and must
    score 0.5 rather than whatever order it was handed the rows in."""
    probs = np.array([0.5, 0.5, 0.5, 0.5])
    assert fit.auc(probs, np.array([1.0, 0.0, 1.0, 0.0])) == pytest.approx(0.5)

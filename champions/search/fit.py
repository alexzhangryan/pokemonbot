"""Fitting the evaluation function, and proving it is calibrated.

`docs/04-decision-engine.md` section 5 asks for three things in order:
calibrated as a win probability, cheap, and trained on outcomes rather than
hand tuned. The bootstrap version in `evaluate.py` met only the second. This
module is what turns the other two into checked claims.

Four decisions here are load bearing.

**Split by battle, never by position.** Twenty positions from one game share a
result and most of a board; splitting them at random puts near-duplicates of a
training row into the test set and reports a held-out number that is not held
out. Every split in this module groups by `battle_id`.

**No intercept, anywhere.** Every feature is a difference of the two sides, so a
dead-even position has the zero vector, and a model with no intercept is forced
to answer 0.5 there. That is a property worth having rather than hoping a fit
discovers it, and `free_intercept` fits one anyway as a diagnostic: an intercept
far from zero means the features are not the antisymmetric differences they
claim to be, which is exactly how the six-against-four counting bug in
`evaluate.py` would have gone unnoticed had M6 simply fit around it.

"Anywhere" includes the calibration, which is the easy place to lose it. Platt
scaling is normally `a * x + b`, and fitting the `b` put the offset straight
back: an even position scored 0.518 and the test that asserts otherwise caught
it. `calibrate` fits the slope alone.

**Calibrate on a split the fit never saw.** A logistic regression is already a
probability, but only on its own training distribution. Platt scaling is fit on
a validation split and the reliability diagram is measured on a third, so the
calibration claim and the evidence for it never come from the same games.

**The reliability diagram is the gate, not the log loss.** A model can improve
average loss while being systematically overconfident, and overconfidence is the
failure that matters here: the coach reports an ex-ante loss in probability
units and the matrix game backs values up through it. So the numbers reported
are the ones that would show that -- expected and maximum calibration error, per
bin, with counts -- and log loss is reported beside them rather than instead.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize

from champions.search.positions import Position

#: `faint_swing` is not fit. A wiped side is a decided game, handled by the
#: short circuit in `evaluate.evaluate`, and giving it a weight would let the
#: optimiser spend it on positions that are not decided at all.
EXCLUDED_FEATURES = frozenset({"faint_swing"})

#: L2 strength. Small: there are seven features and thousands of positions, so
#: this is here to keep a feature that is nearly constant in one corpus from
#: acquiring a large weight, not to do real model selection.
DEFAULT_L2 = 1.0

#: Bins for the reliability diagram, over the predicted probability.
DEFAULT_BINS = 10


@dataclass(frozen=True)
class Dataset:
    """A design matrix, its labels, and which battle each row came from."""

    names: tuple[str, ...]
    x: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    source: str

    def __len__(self) -> int:
        return int(self.x.shape[0])

    @property
    def n_battles(self) -> int:
        return int(len(np.unique(self.groups)))


@dataclass(frozen=True)
class Bin:
    """One bucket of a reliability diagram."""

    low: float
    high: float
    count: int
    predicted: float
    observed: float

    @property
    def gap(self) -> float:
        return abs(self.predicted - self.observed)


@dataclass(frozen=True)
class Reliability:
    bins: tuple[Bin, ...]
    ece: float
    mce: float
    n: int


@dataclass(frozen=True)
class Metrics:
    n: int
    n_battles: int
    log_loss: float
    brier: float
    accuracy: float
    auc: float
    base_rate: float

    #: Log loss of the constant model that always predicts the base rate. The
    #: comparison that matters: a fit that does not beat this has learned
    #: nothing, whatever its absolute loss looks like.
    base_log_loss: float

    def as_row(self) -> dict[str, float]:
        return {
            "n": self.n,
            "n_battles": self.n_battles,
            "log_loss": round(self.log_loss, 4),
            "base_log_loss": round(self.base_log_loss, 4),
            "brier": round(self.brier, 4),
            "accuracy": round(self.accuracy, 4),
            "auc": round(self.auc, 4),
            "base_rate": round(self.base_rate, 4),
        }


@dataclass
class FittedModel:
    """Weights in log odds, plus the Platt scaling that calibrates them."""

    weights: dict[str, float]
    #: `a * log_odds`, fit on a split the weights never saw. Identity until
    #: `calibrate` runs. `platt_b` is kept at zero and never fit into: see
    #: `calibrate` for why an offset is not allowed here.
    platt_a: float = 1.0
    platt_b: float = 0.0
    l2: float = DEFAULT_L2
    #: Diagnostics only, never applied. See the module docstring and `calibrate`.
    free_intercept: float = 0.0
    platt_offset_diagnostic: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)

    def log_odds(self, features: dict[str, float]) -> float:
        raw = sum(self.weights.get(name, 0.0) * value for name, value in features.items())
        return self.platt_a * raw + self.platt_b

    def win_prob(self, features: dict[str, float]) -> float:
        return sigmoid(self.log_odds(features))

    def as_json(self) -> dict[str, Any]:
        return {
            "weights": {name: round(value, 6) for name, value in sorted(self.weights.items())},
            "platt": {"a": round(self.platt_a, 6), "b": round(self.platt_b, 6)},
            "l2": self.l2,
            "free_intercept": round(self.free_intercept, 6),
            "platt_offset_diagnostic": round(self.platt_offset_diagnostic, 6),
            "metrics": self.metrics,
        }


def sigmoid(x: float | np.ndarray) -> Any:
    return 0.5 * (1.0 + np.tanh(0.5 * np.asarray(x, dtype=float)))


# -- building the matrix ------------------------------------------------------


def to_dataset(positions: Sequence[Position], source: str = "mixed") -> Dataset:
    """Feature vectors as a matrix, with the battle each row belongs to.

    The column order is taken from the first position and applied to all of
    them, so a feature that is absent from one row is a zero in the right place
    rather than a silently shifted column.
    """
    if not positions:
        raise ValueError("no positions to fit")
    names = tuple(n for n in positions[0].features if n not in EXCLUDED_FEATURES)
    x = np.array([[p.features.get(n, 0.0) for n in names] for p in positions], dtype=float)
    y = np.array([p.label for p in positions], dtype=float)
    groups = np.array([p.battle_id for p in positions], dtype=object)
    return Dataset(names=names, x=x, y=y, groups=groups, source=source)


def split_by_battle(
    data: Dataset, fractions: tuple[float, float, float] = (0.7, 0.15, 0.15), seed: int = 0
) -> tuple[Dataset, Dataset, Dataset]:
    """Train, validation and test, partitioned by battle rather than by row.

    Both viewpoints of one corpus replay carry the same `battle_id` stem but
    different sides, and they are near mirror images of each other. They are
    split together, on the stem, so a game cannot appear on both sides of the
    split wearing the other player's hat.
    """
    stems = np.array([str(g).split(":")[0] for g in data.groups], dtype=object)
    unique = np.unique(stems)
    rng = np.random.default_rng(seed)
    rng.shuffle(unique)

    n_train = int(len(unique) * fractions[0])
    n_val = int(len(unique) * fractions[1])
    parts = (unique[:n_train], unique[n_train : n_train + n_val], unique[n_train + n_val :])
    return tuple(_subset(data, stems, set(part)) for part in parts)  # type: ignore[return-value]


def _subset(data: Dataset, stems: np.ndarray, keep: set[Any]) -> Dataset:
    mask = np.array([s in keep for s in stems], dtype=bool)
    return Dataset(
        names=data.names,
        x=data.x[mask],
        y=data.y[mask],
        groups=data.groups[mask],
        source=data.source,
    )


# -- the fit -----------------------------------------------------------------


def fit(train: Dataset, l2: float = DEFAULT_L2) -> FittedModel:
    """Logistic regression by L-BFGS, with no intercept.

    Written out rather than pulled from a library because the project has no
    scikit-learn dependency and this is thirty lines: the regularised negative
    log likelihood and its gradient, handed to the optimiser scipy already
    ships. Deterministic, so a refit of the same rows gives the same weights.
    """
    weights = _minimise(train.x, train.y, l2, intercept=False)
    with_intercept = _minimise(train.x, train.y, l2, intercept=True)
    return FittedModel(
        weights=dict(zip(train.names, weights.tolist(), strict=True)),
        l2=l2,
        free_intercept=float(with_intercept[-1]),
    )


def _minimise(x: np.ndarray, y: np.ndarray, l2: float, intercept: bool) -> np.ndarray:
    design = np.hstack([x, np.ones((x.shape[0], 1))]) if intercept else x
    n_features = design.shape[1]

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        z = design @ theta
        # log(1 + exp(z)) via logaddexp, which does not overflow on the
        # decisive positions the search cares most about getting right.
        loss = float(np.mean(np.logaddexp(0.0, z) - y * z))
        penalty = l2 * float(theta @ theta) / (2 * len(y))
        gradient = design.T @ (sigmoid(z) - y) / len(y) + l2 * theta / len(y)
        return loss + penalty, gradient

    result = minimize(
        objective, np.zeros(n_features), jac=True, method="L-BFGS-B", options={"maxiter": 500}
    )
    return np.asarray(result.x, dtype=float)


def bootstrap_weights(
    train: Dataset, resamples: int = 200, l2: float = DEFAULT_L2, seed: int = 0
) -> dict[str, tuple[float, float]]:
    """A 95% interval per weight, by resampling **battles** with replacement.

    This exists because a weight table cannot be read at face value and M6 found
    out the hard way. Self-play on a mirror team produced `status_advantage` at
    -1.34 -- the wrong sign, saying it is good to be the poisoned side -- from
    291 rows out of 11,774, because burn is the only status that matchup ever
    inflicts and it lands mostly on the turn an attack failed to kill. The
    number looked exactly like the six beside it.

    Resampling battles rather than rows is the whole point: positions inside one
    game are near duplicates, and resampling rows would report a confidence
    interval about twenty times too narrow, which is worse than not measuring.

    A sign the interval does not determine is a sign the source has not
    determined. `scripts/fit_eval.py` uses that, rather than a threshold on how
    often a feature is nonzero, to decide which weights a source has earned.
    """
    stems = np.array([str(g).split(":")[0] for g in train.groups], dtype=object)
    unique = np.unique(stems)
    index: dict[Any, np.ndarray] = {stem: np.flatnonzero(stems == stem) for stem in unique}
    rng = np.random.default_rng(seed)

    draws = np.empty((resamples, train.x.shape[1]), dtype=float)
    for i in range(resamples):
        picked = rng.choice(unique, size=len(unique), replace=True)
        rows = np.concatenate([index[stem] for stem in picked])
        draws[i] = _minimise(train.x[rows], train.y[rows], l2, intercept=False)

    low, high = np.percentile(draws, [2.5, 97.5], axis=0)
    return {name: (float(low[i]), float(high[i])) for i, name in enumerate(train.names)}


def undetermined(intervals: dict[str, tuple[float, float]]) -> set[str]:
    """Weights whose sign the source did not settle: the interval spans zero."""
    return {name for name, (low, high) in intervals.items() if low <= 0.0 <= high}


def calibrate(model: FittedModel, validation: Dataset) -> FittedModel:
    """Platt scaling, fit on a split the weights never saw. Slope only.

    One parameter, not two, and the missing one is the point. Textbook Platt
    scaling is `a * x + b`, and the offset would undo the whole reason this
    model is fit without an intercept: every feature is a difference between the
    two sides, so a dead-even position is the zero vector and has to score 0.5,
    which the matrix game relies on when it treats the payoff as zero sum. Fit
    with an offset here it came out at +0.074, and an even position scored 0.518.

    A slope is enough for the failure calibration exists to fix, which is
    systematic over- or under-confidence, and it cannot reshape the ranking the
    features produced -- that would be a second fit wearing a calibration's name.
    The offset is still computed, as a diagnostic, for the same reason
    `free_intercept` is: a large one means the features are not the antisymmetric
    differences they claim to be.
    """
    if len(validation) == 0:
        return model
    raw = validation.x @ _vector(model, validation.names)
    model.platt_a = float(_minimise(raw.reshape(-1, 1), validation.y, l2=0.0, intercept=False)[0])
    _, offset = _minimise(raw.reshape(-1, 1), validation.y, l2=0.0, intercept=True)
    model.platt_offset_diagnostic = float(offset)
    return model


def _vector(model: FittedModel, names: Sequence[str]) -> np.ndarray:
    return np.array([model.weights.get(name, 0.0) for name in names], dtype=float)


def predict(model: FittedModel, data: Dataset) -> np.ndarray:
    raw = data.x @ _vector(model, data.names)
    return np.asarray(sigmoid(model.platt_a * raw + model.platt_b), dtype=float)


# -- what makes the claim checkable ------------------------------------------


def reliability(probs: np.ndarray, y: np.ndarray, bins: int = DEFAULT_BINS) -> Reliability:
    """The diagram `docs/04` requires before this is used anywhere.

    Equal-width bins over the predicted probability, each reporting how often
    the model said that and how often it was right. Empty bins are dropped
    rather than reported as a perfect zero-gap bucket, which would flatter the
    expected calibration error by averaging over nothing.
    """
    edges = np.linspace(0.0, 1.0, bins + 1)
    out: list[Bin] = []
    ece = 0.0
    mce = 0.0
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        mask = (probs >= low) & (probs < high if high < 1.0 else probs <= high)
        count = int(mask.sum())
        if count == 0:
            continue
        predicted = float(probs[mask].mean())
        observed = float(y[mask].mean())
        out.append(Bin(float(low), float(high), count, predicted, observed))
        ece += count * abs(predicted - observed)
        mce = max(mce, abs(predicted - observed))
    n = int(len(y))
    return Reliability(tuple(out), ece / n if n else 0.0, mce, n)


def metrics(probs: np.ndarray, data: Dataset) -> Metrics:
    y = data.y
    clipped = np.clip(probs, 1e-12, 1 - 1e-12)
    base = float(y.mean()) if len(y) else 0.5
    base_clipped = min(max(base, 1e-12), 1 - 1e-12)
    return Metrics(
        n=len(y),
        n_battles=data.n_battles,
        log_loss=float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))),
        brier=float(np.mean((probs - y) ** 2)),
        accuracy=float(np.mean((probs >= 0.5) == (y >= 0.5))),
        auc=auc(probs, y),
        base_rate=base,
        base_log_loss=float(
            -(base * math.log(base_clipped) + (1 - base) * math.log(1 - base_clipped))
        ),
    )


def auc(probs: np.ndarray, y: np.ndarray) -> float:
    """Area under the ROC, by rank, with ties averaged.

    Reported because it separates the two ways this can be wrong: a model can
    rank positions perfectly and still be badly calibrated, and the fix for that
    is Platt scaling rather than better features. A low AUC means the opposite,
    and no amount of calibration will help.
    """
    positives = int(y.sum())
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return float("nan")
    order = np.argsort(probs, kind="mergesort")
    ranks = np.empty(len(probs), dtype=float)
    ranks[order] = np.arange(1, len(probs) + 1, dtype=float)
    # Average the ranks within each group of equal predictions, or a model that
    # emits many identical probabilities scores by the order it was handed them.
    values, inverse, counts = np.unique(probs, return_inverse=True, return_counts=True)
    sums = np.zeros(len(values))
    np.add.at(sums, inverse, ranks)
    ranks = (sums / counts)[inverse]
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def evaluate_model(model: FittedModel, data: Dataset, bins: int = DEFAULT_BINS) -> dict[str, Any]:
    probs = predict(model, data)
    return {
        "metrics": metrics(probs, data).as_row(),
        "reliability": reliability(probs, data.y, bins),
    }

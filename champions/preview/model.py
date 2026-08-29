"""A conditional logit over subsets, fitted by L-BFGS.

Bring-4 is not six independent coin flips. It is one choice of exactly four from
six, and lead selection is one choice of exactly two from four. Fitting six
independent logistic regressions and renormalising afterwards throws the
constraint away during training and recovers it as an afterthought; a
conditional logit puts it in the likelihood, where it belongs.

Each Pokemon gets a score from the shared weights, a subset scores as the sum of
its members, and the subsets compete in one softmax. So the model learns what
makes a *team of four* rather than what makes a Pokemon, and its output is
already the distribution over the fifteen that the preview equilibrium consumes.

No new dependency. scipy is already here for the matrix game LP, and a hand
written objective with an analytic gradient is thirty lines, deterministic from
a zero start, and inspectable -- which matters more than convenience for
something whose weights are going to be read.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

#: Column 0 is the bias in every feature space here, and shrinking it toward
#: zero would pull the base rate rather than the effects. It is left alone.
BIAS_COLUMN = 0


def membership(subsets: list[tuple[int, ...]], n_items: int) -> np.ndarray:
    """A (n_subsets, n_items) indicator of which items each subset contains."""
    matrix = np.zeros((len(subsets), n_items), dtype=float)
    for row, members in enumerate(subsets):
        matrix[row, list(members)] = 1.0
    return matrix


@dataclass(frozen=True)
class ChoiceModel:
    """Fitted weights plus the subset structure they were fitted under."""

    weights: np.ndarray
    membership: np.ndarray
    feature_names: tuple[str, ...] = ()
    l2: float = 1.0
    iterations: int = 0
    converged: bool = True

    def item_scores(self, features: np.ndarray) -> np.ndarray:
        """Utility per item. `features` is (n_items, width) or (N, n_items, width)."""
        return features @ self.weights

    def subset_log_probabilities(self, features: np.ndarray) -> np.ndarray:
        scores = self.item_scores(features) @ self.membership.T
        return scores - logsumexp(scores, axis=-1, keepdims=True)

    def subset_probabilities(self, features: np.ndarray) -> np.ndarray:
        """The distribution over subsets. Sums to one over the last axis."""
        return np.exp(self.subset_log_probabilities(features))

    def item_probabilities(self, features: np.ndarray) -> np.ndarray:
        """Per item marginals, implied by the subset distribution.

        These are what a human reads ("Pelipper is brought 81% of the time");
        the subset distribution is what the equilibrium uses. Deriving one from
        the other rather than fitting both keeps them consistent by
        construction.
        """
        return self.subset_probabilities(features) @ self.membership

    def top_features(self, k: int = 12) -> list[tuple[str, float]]:
        if not self.feature_names:
            return []
        order = np.argsort(-np.abs(self.weights))[:k]
        return [(self.feature_names[i], float(self.weights[i])) for i in order]


def fit_choice_model(
    features: np.ndarray,
    chosen: np.ndarray,
    membership_matrix: np.ndarray,
    l2: float = 1.0,
    feature_names: tuple[str, ...] = (),
    max_iterations: int = 500,
) -> ChoiceModel:
    """Fit by maximum penalised likelihood.

    `features` is (N, n_items, width), `chosen` is (N,) indices into the subset
    list. Deterministic: the optimiser starts from zeros and L-BFGS is not
    stochastic, so the same data gives the same weights on every run, which
    CLAUDE.md requires of anything that feeds a measurement.
    """
    features = np.asarray(features, dtype=float)
    chosen = np.asarray(chosen, dtype=int)
    if features.ndim != 3:
        raise ValueError(f"features must be (N, n_items, width), got {features.shape}")
    if len(features) == 0:
        raise ValueError("Cannot fit a model on zero examples")

    n_examples, _, width = features.shape
    rows = np.arange(n_examples)
    penalty_mask = np.ones(width)
    penalty_mask[BIAS_COLUMN] = 0.0

    def objective(weights: np.ndarray) -> tuple[float, np.ndarray]:
        item_scores = features @ weights  # (N, n_items)
        subset_scores = item_scores @ membership_matrix.T  # (N, n_subsets)
        normaliser = logsumexp(subset_scores, axis=1)  # (N,)
        log_likelihood = float(np.sum(subset_scores[rows, chosen] - normaliser))

        probabilities = np.exp(subset_scores - normaliser[:, None])
        # Observed minus expected membership, per item: the conditional logit
        # gradient. Everything else is bookkeeping.
        residual = membership_matrix[chosen] - probabilities @ membership_matrix
        gradient = np.einsum("ni,niw->w", residual, features)

        penalised = -log_likelihood + 0.5 * l2 * float(np.sum(penalty_mask * weights**2))
        return penalised, -gradient + l2 * penalty_mask * weights

    result = minimize(
        objective,
        np.zeros(width),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": max_iterations},
    )
    return ChoiceModel(
        weights=result.x,
        membership=membership_matrix,
        feature_names=feature_names,
        l2=l2,
        iterations=int(result.nit),
        converged=bool(result.success),
    )

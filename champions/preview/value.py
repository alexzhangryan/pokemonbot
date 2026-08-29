"""Win probability for a bring-4 against a bring-4.

`docs/04-decision-engine.md` section 6 says the trained evaluation function
supplies each cell of the preview matrix. It cannot, and the reason is worth
stating rather than working around: `champions/search/evaluate.py` reads a state
snapshot, and at team preview no state exists. Every one of its features -- HP
fractions, survivors, field conditions -- is identical for both sides before the
first turn, so it returns 0.5 for every pairing and the 15 x 15 matrix is
constant. A constant matrix has every strategy as an equilibrium, which is a
polite way of saying it has no answer.

So the preview needs its own value model, and the corpus supplies exactly the
labels for it: a bring-4, an opposing bring-4, and who won.

**Antisymmetry is structural, not learned.** The features are `g(ours) -
g(theirs)` and there is no intercept, so `score(B, A) == -score(A, B)` and
`P(win | B, A) == 1 - P(win | A, B)` to machine precision. The matrix game needs
a zero sum payoff; getting that by construction means `matrix.solve_both` can
never fail its consistency check for a reason originating here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from champions.preview.dataset import PreviewExample
from champions.preview.features import FeatureSpace

#: The eighteen types, taken from the dex rather than hardcoded.
TEAM_SUMMARY = ("mean_speed", "max_speed", "mean_stat_total", "mega_count")


@dataclass(frozen=True)
class PreviewValueModel:
    """Logistic on the difference of team summaries. No intercept, by design."""

    space: FeatureSpace
    types: tuple[str, ...]
    weights: np.ndarray
    l2: float = 1.0
    converged: bool = True

    #: Interaction features, appended after the separable block.
    INTERACTION_FEATURES = ("offense_into_them", "outspeed_fraction")

    @property
    def separable_width(self) -> int:
        return len(TEAM_SUMMARY) + len(self.types) + len(self.space.vocabulary)

    @property
    def width(self) -> int:
        return self.separable_width + len(self.INTERACTION_FEATURES)

    def team_vector(self, team: Sequence[str]) -> np.ndarray:
        """`g(team)`: everything the model knows about one side of four."""
        vector = np.zeros(self.separable_width, dtype=float)
        speeds = [self.space.speed(s) for s in team] or [0]
        vector[0] = float(np.mean(speeds)) / 100.0
        vector[1] = float(np.max(speeds)) / 100.0
        vector[2] = float(np.mean([self.space.stat_total(s) for s in team] or [0])) / 100.0
        vector[3] = float(sum(self.space.has_mega(s) for s in team))
        for species in team:
            for type_name in self.space.types(species):
                slot = self.types.index(type_name) if type_name in self.types else None
                if slot is not None:
                    vector[len(TEAM_SUMMARY) + slot] += 1.0
            index = self.space.index.get(species)
            if index is not None:
                vector[len(TEAM_SUMMARY) + len(self.types) + index] += 1.0
        return vector

    def interaction(self, ours: Sequence[str], theirs: Sequence[str]) -> np.ndarray:
        """Features of the pairing itself, which no separable model can express.

        This is what makes team preview a game rather than an argmax. With a
        payoff of the form `g(ours) - g(theirs)` the best bring-4 is the same
        whatever the opponent brings, the 15 x 15 has a dominant row, and
        solving it exactly buys nothing over sorting. An interaction term is
        what makes the right answer depend on theirs -- which is the entire
        premise of `docs/04-decision-engine.md` section 6.

        Antisymmetry survives because these enter as `h(a, b) - h(b, a)`.
        """
        offense = float(
            np.mean([self.space._offense(s, list(theirs)) for s in ours]) if len(ours) else 0.0
        )
        our_speeds = [self.space.speed(s) for s in ours]
        their_speeds = [self.space.speed(s) for s in theirs]
        outspeeds = float(
            np.mean([[a > b for b in their_speeds] for a in our_speeds])
            if our_speeds and their_speeds
            else 0.0
        )
        return np.array([offense, outspeeds])

    def difference(self, ours: Sequence[str], theirs: Sequence[str]) -> np.ndarray:
        separable = self.team_vector(ours) - self.team_vector(theirs)
        interaction = self.interaction(ours, theirs) - self.interaction(theirs, ours)
        return np.concatenate([separable, interaction])

    def win_probability(self, ours: Sequence[str], theirs: Sequence[str]) -> float:
        score = float(self.difference(ours, theirs) @ self.weights)
        return float(1.0 / (1.0 + np.exp(-score)))

    def matrix(
        self, our_options: Sequence[Sequence[str]], their_options: Sequence[Sequence[str]]
    ) -> np.ndarray:
        """A payoff matrix of win probabilities, our options by theirs.

        Computed from cached team vectors rather than pairwise, which turns 225
        feature builds into 30.
        """
        separable = self.weights[: self.separable_width]
        ours = np.stack([self.team_vector(t) for t in our_options]) @ separable
        theirs = np.stack([self.team_vector(t) for t in their_options]) @ separable
        scores = ours[:, None] - theirs[None, :]
        interaction = self.weights[self.separable_width :]
        for i, a in enumerate(our_options):
            for j, b in enumerate(their_options):
                scores[i, j] += (self.interaction(a, b) - self.interaction(b, a)) @ interaction
        return 1.0 / (1.0 + np.exp(-scores))


def train_value_model(
    examples: Sequence[PreviewExample], space: FeatureSpace, l2: float = 10.0
) -> PreviewValueModel:
    """Fit on games where both sides' bring-4 is fully observed.

    Both sides of a game produce an example, and under antisymmetry the two are
    the same equation written twice. They are both included anyway: it costs
    nothing, and it keeps the objective symmetric so no accident of ordering can
    tilt the fit.
    """
    types = tuple(sorted({t for entry in space.dex.types.values() for t in [entry["name"]]}))
    template = PreviewValueModel(space=space, types=types, weights=np.zeros(1))
    width = (
        len(TEAM_SUMMARY)
        + len(types)
        + len(space.vocabulary)
        + len(PreviewValueModel.INTERACTION_FEATURES)
    )
    template = PreviewValueModel(space=space, types=types, weights=np.zeros(width), l2=l2)

    rows: list[np.ndarray] = []
    labels: list[float] = []
    by_replay: dict[str, dict[str, PreviewExample]] = {}
    for example in examples:
        by_replay.setdefault(example.replay_id, {})[example.side] = example
    for sides in by_replay.values():
        if len(sides) != 2:
            continue
        for example in sides.values():
            other = sides["p2" if example.side == "p1" else "p1"]
            if not (example.usable_for_bring and other.usable_for_bring):
                continue
            rows.append(template.difference(example.brought_species, other.brought_species))
            labels.append(1.0 if example.won else 0.0)

    if not rows:
        raise ValueError("No fully observed matchups to fit a value model on")
    features = np.stack(rows)
    targets = np.array(labels)

    def objective(weights: np.ndarray) -> tuple[float, np.ndarray]:
        scores = features @ weights
        # log(1 + exp(x)) via logaddexp, which does not overflow.
        loss = float(np.sum(np.logaddexp(0.0, scores) - targets * scores))
        predictions = 1.0 / (1.0 + np.exp(-scores))
        gradient = features.T @ (predictions - targets)
        return loss + 0.5 * l2 * float(weights @ weights), gradient + l2 * weights

    result = minimize(objective, np.zeros(features.shape[1]), jac=True, method="L-BFGS-B")
    return PreviewValueModel(
        space=space, types=types, weights=result.x, l2=l2, converged=bool(result.success)
    )


def evaluate_value_model(
    model: PreviewValueModel, examples: Sequence[PreviewExample]
) -> dict[str, float]:
    """Accuracy, log loss and Brier score against always-guessing 0.5.

    A preview value model that cannot beat 0.5 is not a weak model, it is the
    statement that the bring-4 pairing does not determine the game -- which
    would itself be worth knowing before the equilibrium is built on top of it.
    """
    by_replay: dict[str, dict[str, PreviewExample]] = {}
    for example in examples:
        by_replay.setdefault(example.replay_id, {})[example.side] = example

    predictions, outcomes = [], []
    for sides in by_replay.values():
        if len(sides) != 2:
            continue
        example = sides["p1"]
        other = sides["p2"]
        if not (example.usable_for_bring and other.usable_for_bring):
            continue
        predictions.append(model.win_probability(example.brought_species, other.brought_species))
        outcomes.append(1.0 if example.won else 0.0)

    if not predictions:
        raise ValueError("No evaluable matchups")
    p = np.clip(np.array(predictions), 1e-9, 1 - 1e-9)
    y = np.array(outcomes)
    return {
        "n": float(len(y)),
        "accuracy": float(np.mean((p >= 0.5) == (y == 1))),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "coin_flip_log_loss": float(np.log(2)),
        "brier": float(np.mean((p - y) ** 2)),
        "coin_flip_brier": 0.25,
        "spread": float(np.std(p)),
    }

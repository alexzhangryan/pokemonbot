"""Bring-4 and lead predictors, and how they are scored.

`docs/04-decision-engine.md` section 6 wants these early, on the grounds that
bring-4 and leads are a large share of VGC win rate and the preview computation
is exact rather than approximate. They are trained here and evaluated in
isolation, before anything is allowed to depend on them.

Two predictors, same machinery. The bring model chooses four of the six shown at
preview. The lead model chooses two of the four that were brought -- conditional
on the bring rather than joint with it, because that is how the decision is
actually made and because conditioning keeps both problems small enough for the
data on hand.

Every number reported here is on held-out series, against three baselines that
have to be beaten for the model to be worth anything: uniform over the subsets,
the global base rate, and a species-marginal model with no matchup features at
all. The third is the one that matters. If matchup features add nothing, the
right conclusion is that this metagame's bring decisions are team-intrinsic, and
that is a finding rather than a failure.
"""

from __future__ import annotations

import collections
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from champions.dex.loader import Dex
from champions.harness.elo import wilson_interval
from champions.preview.dataset import (
    BRING_SIZE,
    LEAD_SIZE,
    PreviewExample,
    subset_index,
    subsets,
)
from champions.preview.features import DENSE_FEATURES, FeatureSpace
from champions.preview.model import ChoiceModel, fit_choice_model, membership

#: How an example is turned into the items being chosen among, and the index
#: of the subset that was actually chosen. Bring and lead differ only in these.
ItemsOf = Callable[[PreviewExample], tuple[Sequence[str], Sequence[str]]]
LabelOf = Callable[[PreviewExample, Sequence[str]], int | None]


@dataclass(frozen=True)
class PreviewPredictor:
    """A fitted choice model plus everything needed to apply it to a matchup."""

    space: FeatureSpace
    model: ChoiceModel
    n_items: int
    choose: int

    @property
    def subsets(self) -> list[tuple[int, ...]]:
        return subsets(self.n_items, self.choose)

    def features(self, items: Sequence[str], opponents: Sequence[str]) -> np.ndarray:
        return np.stack([self.space.row(s, items, opponents) for s in items])

    def subset_distribution(self, items: Sequence[str], opponents: Sequence[str]) -> np.ndarray:
        """Probability over the subsets of `items`, in `self.subsets` order."""
        return self.model.subset_probabilities(self.features(items, opponents))

    def marginals(self, items: Sequence[str], opponents: Sequence[str]) -> np.ndarray:
        """Per Pokemon probability of being chosen. Sums to `self.choose`."""
        return self.model.item_probabilities(self.features(items, opponents))

    def most_likely(self, items: Sequence[str], opponents: Sequence[str]) -> tuple[str, ...]:
        distribution = self.subset_distribution(items, opponents)
        return tuple(items[i] for i in self.subsets[int(np.argmax(distribution))])


def _training_arrays(
    examples: Sequence[PreviewExample],
    space: FeatureSpace,
    items_of: ItemsOf,
    label_of: LabelOf,
    choose: int,
) -> tuple[np.ndarray, np.ndarray]:
    rows: list[np.ndarray] = []
    labels: list[int] = []
    for example in examples:
        items, opponents = items_of(example)
        index = label_of(example, items)
        if index is None:
            continue
        rows.append(np.stack([space.row(s, items, opponents) for s in items]))
        labels.append(index)
    if not rows:
        raise ValueError("No usable training examples")
    return np.stack(rows), np.array(labels, dtype=int)


def _bring_items(example: PreviewExample) -> tuple[Sequence[str], Sequence[str]]:
    return example.team, example.opponent_team


def _bring_label(example: PreviewExample, items: Sequence[str]) -> int | None:
    return subset_index(example.brought, BRING_SIZE)


def _lead_items(example: PreviewExample) -> tuple[Sequence[str], Sequence[str]]:
    """Leads are chosen from the four brought, not from the six shown."""
    return example.brought_species, example.opponent_team


def _lead_label(example: PreviewExample, items: Sequence[str]) -> int | None:
    led = set(example.led_species)
    mask = [species in led for species in items]
    return subset_index(mask, LEAD_SIZE)


def build_feature_space(
    examples: Sequence[PreviewExample], dex: Dex, min_count: int = 25
) -> FeatureSpace:
    """Vocabulary from training examples only. Test species may be unseen."""
    counts = collections.Counter(s for e in examples for s in e.team)
    return FeatureSpace.build(dex, dict(counts), min_count=min_count)


def train_bring_predictor(
    examples: Sequence[PreviewExample],
    dex: Dex,
    l2: float = 1.0,
    min_count: int = 25,
    space: FeatureSpace | None = None,
) -> PreviewPredictor:
    usable = [e for e in examples if e.usable_for_bring]
    space = space or build_feature_space(usable, dex, min_count)
    features, labels = _training_arrays(usable, space, _bring_items, _bring_label, BRING_SIZE)
    model = fit_choice_model(
        features,
        labels,
        membership(subsets(6, BRING_SIZE), 6),
        l2=l2,
        feature_names=space.names(),
    )
    return PreviewPredictor(space=space, model=model, n_items=6, choose=BRING_SIZE)


def train_lead_predictor(
    examples: Sequence[PreviewExample],
    dex: Dex,
    l2: float = 1.0,
    min_count: int = 25,
    space: FeatureSpace | None = None,
) -> PreviewPredictor:
    usable = [e for e in examples if e.usable_for_bring and e.usable_for_lead]
    space = space or build_feature_space(usable, dex, min_count)
    features, labels = _training_arrays(usable, space, _lead_items, _lead_label, LEAD_SIZE)
    model = fit_choice_model(
        features,
        labels,
        membership(subsets(4, LEAD_SIZE), 4),
        l2=l2,
        feature_names=space.names(),
    )
    return PreviewPredictor(space=space, model=model, n_items=4, choose=LEAD_SIZE)


@dataclass(frozen=True)
class PredictorMetrics:
    """Held-out performance. Reported with intervals, per CLAUDE.md."""

    n: int
    top1: float
    top1_interval: tuple[float, float]
    log_loss: float
    uniform_log_loss: float
    marginal_accuracy: float
    marginal_baseline: float

    def as_row(self, label: str) -> str:
        low, high = self.top1_interval
        return (
            f"{label:<28} n={self.n:<5} top-1 {self.top1:6.1%} "
            f"[{low:.1%}, {high:.1%}]  log loss {self.log_loss:.3f} "
            f"(uniform {self.uniform_log_loss:.3f})  "
            f"per-Pokemon {self.marginal_accuracy:.1%} (base {self.marginal_baseline:.1%})"
        )


def evaluate_predictor(
    predictor: PreviewPredictor,
    examples: Sequence[PreviewExample],
    items_of: ItemsOf = _bring_items,
    label_of: LabelOf = _bring_label,
) -> PredictorMetrics:
    """Score a predictor on held-out examples.

    Three numbers, because one is not enough. Top-1 says how often the single
    most likely subset was the one played, which is what a person asks first and
    is a harsh bar at 15 options. Log loss says whether the whole distribution is
    right, which is what the preview equilibrium actually consumes -- a model can
    win on top-1 while being badly calibrated and still make the equilibrium
    worse. Per-Pokemon accuracy is the marginal view, against the base rate of
    always guessing "brought", which at four of six is already 66.7%.
    """
    hits = 0
    losses: list[float] = []
    marginal_hits = 0
    marginal_total = 0
    evaluated = 0
    n_subsets = len(subsets(predictor.n_items, predictor.choose))

    for example in examples:
        items, opponents = items_of(example)
        truth = label_of(example, items)
        if truth is None or len(items) != predictor.n_items:
            continue
        evaluated += 1
        distribution = predictor.subset_distribution(items, opponents)
        hits += int(np.argmax(distribution) == truth)
        losses.append(-float(np.log(max(distribution[truth], 1e-12))))

        # Top-k by marginal, not a 0.5 threshold. Marginals sum to `choose`
        # over `n_items`, so a threshold predicts "chosen" for almost everything
        # and scores the base rate no matter what the model learned.
        chosen = set(predictor.subsets[truth])
        marginals = predictor.marginals(items, opponents)
        predicted = set(np.argsort(-marginals)[: predictor.choose].tolist())
        for slot in range(predictor.n_items):
            marginal_total += 1
            marginal_hits += int((slot in predicted) == (slot in chosen))

    if evaluated == 0:
        raise ValueError("No evaluable examples")
    # A random top-k pick agrees with the truth on this fraction of slots, by
    # a hypergeometric argument: it is the bar the marginal view has to clear.
    k, n = predictor.choose, predictor.n_items
    random_agreement = (2 * k * k / n + n - 2 * k) / n
    return PredictorMetrics(
        n=evaluated,
        top1=hits / evaluated,
        top1_interval=wilson_interval(hits, evaluated),
        log_loss=float(np.mean(losses)),
        uniform_log_loss=float(np.log(n_subsets)),
        marginal_accuracy=marginal_hits / marginal_total,
        marginal_baseline=random_agreement,
    )


def evaluate_lead_predictor(
    predictor: PreviewPredictor, examples: Sequence[PreviewExample]
) -> PredictorMetrics:
    usable = [e for e in examples if e.usable_for_bring and e.usable_for_lead]
    return evaluate_predictor(predictor, usable, _lead_items, _lead_label)


def marginal_only_space(space: FeatureSpace) -> FeatureSpace:
    """The same vocabulary with the matchup features zeroed out.

    The baseline that matters. It answers "do the matchup features earn their
    place, or is the species marginal doing all the work", which is the question
    a species one-hot model always raises and rarely gets asked.
    """
    return _MarginalOnlySpace(
        dex=space.dex, chart=space.chart, vocabulary=space.vocabulary, index=space.index
    )


class _MarginalOnlySpace(FeatureSpace):
    def row(self, species: str, team: Sequence[str], opponents: Sequence[str]) -> np.ndarray:
        vector = super().row(species, team, opponents)
        vector[1 : len(DENSE_FEATURES)] = 0.0
        return vector


#: Regularisation strengths tried when selecting on validation data.
L2_GRID = (0.5, 2.0, 5.0, 20.0, 50.0, 200.0)


@dataclass(frozen=True)
class Selection:
    """A fitted predictor and the validation evidence for its hyperparameter."""

    predictor: PreviewPredictor
    l2: float
    validation: PredictorMetrics
    grid: tuple[tuple[float, float], ...]

    def as_row(self, label: str) -> str:
        tried = " ".join(f"{l2:g}:{loss:.4f}" for l2, loss in self.grid)
        return f"{label}: chose l2={self.l2:g} on validation log loss ({tried})"


def select_and_train(
    examples: Sequence[PreviewExample],
    dex: Dex,
    kind: str = "bring",
    grid: Sequence[float] = L2_GRID,
    validation_fraction: float = 0.25,
    min_count: int = 25,
) -> Selection:
    """Choose l2 on a validation split carved out of training, then refit on all of it.

    The split is by series, like the outer one, for the same reason: a
    best-of-three shares its teams across games, and validating on a game whose
    sibling was fitted on measures memorisation. Selecting on the test set would
    be worse still, and is the specific mistake this function exists to prevent.
    """
    from champions.preview.dataset import split_examples

    inner = split_examples(examples, test_fraction=validation_fraction, salt=f"{kind}-l2")
    train_fn = train_bring_predictor if kind == "bring" else train_lead_predictor
    evaluate_fn = evaluate_predictor if kind == "bring" else evaluate_lead_predictor

    scored: list[tuple[float, float]] = []
    best: tuple[float, float] | None = None
    for l2 in grid:
        space = build_feature_space([e for e in inner.train if e.usable_for_bring], dex, min_count)
        candidate = train_fn(inner.train, dex, l2=l2, space=space)
        metrics = evaluate_fn(candidate, inner.test)
        scored.append((l2, metrics.log_loss))
        if best is None or metrics.log_loss < best[1]:
            best = (l2, metrics.log_loss)

    assert best is not None
    chosen_l2 = best[0]
    space = build_feature_space([e for e in examples if e.usable_for_bring], dex, min_count)
    predictor = train_fn(examples, dex, l2=chosen_l2, space=space)
    validation = evaluate_fn(train_fn(inner.train, dex, l2=chosen_l2, space=space), inner.test)
    return Selection(predictor=predictor, l2=chosen_l2, validation=validation, grid=tuple(scored))

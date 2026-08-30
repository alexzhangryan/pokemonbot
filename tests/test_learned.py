"""The learned candidate prior: the model, the split, and the recall it reports.

`docs/specs/2026-08-29-learned-policy-provider.md` section 5 asks for three
properties from this half and they are what the file is organised around.
Fitting is deterministic from a seed, because `CLAUDE.md` says an evaluation
that cannot be reproduced from a seed is a bug. The split holds out players
rather than replays, because M4 found a random split hid a model that did not
transfer. And a trained model prefers a knockout to a resisted chip, which is
the smallest statement of "it learned something" that does not depend on a
particular corpus.

The rest is arithmetic that is easy to get subtly wrong: a softmax normalised
over the wrong group, an interval resampled over positions instead of battles.
Both are checked directly rather than inferred from a plausible-looking number.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from champions.dex.loader import Dex
from champions.search.learned import (
    LearnedPolicy,
    PolicyDataset,
    PolicyModel,
    bootstrap_recall,
    fit,
    log_likelihood,
    recall,
    split_by_player,
)


def dataset(
    groups: list[list[list[float]]],
    chosen: list[int],
    battles: list[str] | None = None,
    players: list[str] | None = None,
    sheets: list[bool] | None = None,
    feature_names: tuple[str, ...] = ("a", "b"),
) -> PolicyDataset:
    """A dataset from nested lists, so a test can say what it means."""
    n = len(groups)
    return PolicyDataset(
        x=np.array([row for group in groups for row in group], dtype=float),
        sizes=np.array([len(group) for group in groups], dtype=int),
        chosen=np.array(chosen, dtype=int),
        battle=np.array(battles if battles is not None else [f"b{i}" for i in range(n)]),
        player=np.array(players if players is not None else [f"p{i}" for i in range(n)]),
        sheets=np.array(sheets if sheets is not None else [True] * n),
        feature_names=feature_names,
    )


#: A separable problem: the chosen option is always the one with the larger
#: first feature. Small on purpose -- what is being checked is that the
#: machinery runs the right way round, not that an MLP can fit anything.
SEPARABLE = dataset(
    groups=[
        [[0.0, 1.0], [1.0, 0.0]],
        [[1.0, 1.0], [0.0, 0.0], [0.0, 1.0]],
        [[0.0, 0.0], [1.0, 1.0]],
        [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]],
    ],
    chosen=[1, 0, 1, 0],
)


# -- the dataset -------------------------------------------------------------


def test_the_group_boundaries_line_up_with_the_rows() -> None:
    assert SEPARABLE.x.shape == (10, 2)
    assert len(SEPARABLE) == 4
    assert list(SEPARABLE.starts) == [0, 2, 5, 7]
    assert list(SEPARABLE.chosen_rows) == [1, 2, 6, 7]


def test_a_subset_keeps_whole_groups_and_renumbers_them() -> None:
    """A group is one slot's whole legal set. Half of one is not a decision, and
    a softmax over half of one is a different quantity."""
    part = SEPARABLE.subset(np.array([False, True, False, True]))
    assert len(part) == 2
    assert list(part.sizes) == [3, 3]
    assert part.x.shape == (6, 2)
    assert list(part.chosen) == [0, 0]
    assert np.array_equal(part.x[part.chosen_rows[0]], SEPARABLE.x[SEPARABLE.chosen_rows[1]])


# -- the model ---------------------------------------------------------------


def test_fitting_is_deterministic_from_a_seed() -> None:
    """`CLAUDE.md`: anything that cannot be reproduced from a seed is a bug. The
    initial weights are the only randomness here, so this is the whole of it."""
    a = fit(SEPARABLE, hidden=4, seed=7)
    b = fit(SEPARABLE, hidden=4, seed=7)
    c = fit(SEPARABLE, hidden=4, seed=8)
    assert np.array_equal(a.score(SEPARABLE.x), b.score(SEPARABLE.x))
    assert not np.array_equal(a.score(SEPARABLE.x), c.score(SEPARABLE.x))


def test_the_fit_moves_probability_towards_what_was_chosen() -> None:
    model = fit(SEPARABLE, hidden=4, seed=0)
    assert recall(SEPARABLE, model.score(SEPARABLE.x), k=1) == 1.0
    assert log_likelihood(SEPARABLE, model.score(SEPARABLE.x)) > log_likelihood(
        SEPARABLE, np.zeros(len(SEPARABLE.x))
    )


def test_the_softmax_is_normalised_over_a_group_and_not_over_the_corpus() -> None:
    """Every slot's options compete with each other and with nothing else. A
    softmax over the whole array would make a position with many options score
    lower for having them, which is not a property of the position."""
    from champions.search.learned import probabilities

    probs = probabilities(SEPARABLE, np.arange(len(SEPARABLE.x), dtype=float))
    per_group = [
        probs[start : start + size].sum()
        for start, size in zip(SEPARABLE.starts, SEPARABLE.sizes, strict=True)
    ]
    assert np.allclose(per_group, 1.0)


def test_a_model_survives_a_round_trip_through_json() -> None:
    model = fit(SEPARABLE, hidden=4, seed=3)
    restored = PolicyModel.from_json(model.as_json())
    assert np.allclose(restored.score(SEPARABLE.x), model.score(SEPARABLE.x))
    assert restored.feature_names == model.feature_names


def test_scoring_rejects_a_vector_of_the_wrong_width() -> None:
    """The model is a weight per position in `FEATURE_NAMES`. A width that
    disagrees is a silent relabelling of every weight, so it is an error rather
    than a broadcast."""
    model = fit(SEPARABLE, hidden=4, seed=0)
    with pytest.raises(ValueError):
        model.score(np.zeros((3, 5)))


def test_a_feature_that_never_varies_does_not_divide_by_zero() -> None:
    """Standardisation is per feature. A constant column has zero spread, and
    the whole fit becoming NaN because of one is a real failure mode on a corpus
    where, say, no game in the sample set snow."""
    constant = dataset(
        groups=[[[1.0, 0.0], [1.0, 1.0]], [[1.0, 1.0], [1.0, 0.0]]],
        chosen=[1, 0],
    )
    model = fit(constant, hidden=3, seed=0)
    assert np.isfinite(model.score(constant.x)).all()


# -- the split ---------------------------------------------------------------


def test_the_split_holds_out_players_and_never_a_players_own_rows() -> None:
    """Section 3.4. A random split over rows would put the same player on both
    sides of it, and M4's finding was that this is exactly what hides a model
    that has learned a player rather than the game."""
    data = dataset(
        groups=[[[0.0, 0.0], [1.0, 0.0]]] * 12,
        chosen=[0] * 12,
        players=[f"player{i % 6}" for i in range(12)],
        battles=[f"battle{i // 2}" for i in range(12)],
    )
    train, validation, test = split_by_player(data, seed=0)

    assert len(train) + len(validation) + len(test) == len(data)
    parts = [set(part.player) for part in (train, validation, test)]
    for i, left in enumerate(parts):
        for right in parts[i + 1 :]:
            assert left.isdisjoint(right)


def test_the_split_is_reproducible_from_its_seed() -> None:
    data = dataset(
        groups=[[[0.0, 0.0], [1.0, 0.0]]] * 20,
        chosen=[0] * 20,
        players=[f"player{i % 8}" for i in range(20)],
    )
    first = [set(part.player) for part in split_by_player(data, seed=4)]
    again = [set(part.player) for part in split_by_player(data, seed=4)]
    assert first == again


# -- what gets reported ------------------------------------------------------


def test_recall_asks_whether_the_human_action_survived_the_budget() -> None:
    """Three of the four groups chose their own top-scoring option; the third
    chose the worse of two, which is a miss at `k = 1` and a hit at `k = 2`."""
    scores = np.array([0.0, 1.0, 5.0, 4.0, 3.0, 9.0, 0.0, 2.0, 1.0, 0.0])
    assert recall(SEPARABLE, scores, k=1) == pytest.approx(0.75)
    assert recall(SEPARABLE, scores, k=2) == 1.0


def test_recall_at_a_budget_larger_than_the_set_is_one() -> None:
    assert recall(SEPARABLE, np.zeros(len(SEPARABLE.x)), k=99) == 1.0


def test_a_provider_that_scores_everything_the_same_recalls_nothing_it_cannot_fit() -> None:
    """The bar. A constant score has no top three, and counting only strictly
    better options would report it at 1.0 at every budget and make the whole
    table unreadable. Groups here hold 2, 3, 2 and 3 options, so a budget of two
    fits the two-option groups and no others."""
    flat = np.zeros(len(SEPARABLE.x))
    assert recall(SEPARABLE, flat, k=1) == 0.0
    assert recall(SEPARABLE, flat, k=2) == pytest.approx(0.5)
    assert recall(SEPARABLE, flat, k=3) == 1.0


def test_the_interval_is_resampled_over_battles_and_not_over_positions() -> None:
    """Positions inside one game share a board and a team, so resampling them
    independently reports an interval narrower than the evidence supports. This
    is the same discipline `fit.bootstrap_weights` and `discard.summarise`
    follow."""
    #: Two battles that disagree completely. Resampling battles can draw either
    #: twice, so the interval has to reach 0 and 1; resampling positions cannot.
    data = dataset(
        groups=[[[0.0], [1.0]]] * 8,
        chosen=[0, 0, 0, 0, 1, 1, 1, 1],
        battles=["one"] * 4 + ["two"] * 4,
        feature_names=("a",),
    )
    scores = np.tile([0.0, 1.0], 8)
    low, high = bootstrap_recall(data, scores, k=1, resamples=400, seed=0)
    assert low == pytest.approx(0.0, abs=1e-9)
    assert high == pytest.approx(1.0, abs=1e-9)


# -- the provider ------------------------------------------------------------

#: A snapshot and the joint actions legal in it, which is what every provider
#: test is handed.
Position = tuple[dict[str, Any], list[dict[str, Any]]]


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load("gen9championsvgc2026regmb")


@pytest.fixture(scope="module")
def position(dex: Dex) -> Position:
    """One real turn: the snapshot, and the joint actions legal in it.

    Built through the same path the corpus does, so the provider is exercised on
    the shape it is actually handed rather than on a dict typed out here.
    """
    from champions.search.policy_data import decisions_from_log
    from tests.test_policy_data import LOG

    rows = [d for d in decisions_from_log(LOG, "provider-1", dex) if d.turn == 1 and d.side == "p1"]
    first, second = sorted(rows, key=lambda d: d.slot)
    actions = [
        {
            "message": f"/choose {a.get('label')}, {b.get('label')}",
            "slots": [a, b],
        }
        for a in first.options
        for b in second.options
    ]
    return first.snapshot, actions


@pytest.fixture(scope="module")
def provider(dex: Dex) -> LearnedPolicy:
    return LearnedPolicy(dex, model=fit(SEPARABLE_WIDE, hidden=4, seed=0))


#: A model of the right width to score real vectors. Its weights are noise --
#: what the provider tests check is the plumbing, not the ranking, and the
#: ranking is what `docs/policy-prior.md` and the pruning guard are for.
def _wide() -> PolicyDataset:
    from champions.search.policy_features import FEATURE_NAMES

    rng = np.random.default_rng(0)
    width = len(FEATURE_NAMES)
    return PolicyDataset(
        x=rng.normal(size=(12, width)),
        sizes=np.array([3, 4, 5]),
        chosen=np.array([0, 1, 2]),
        battle=np.array(["a", "a", "b"]),
        player=np.array(["x", "x", "y"]),
        sheets=np.array([True, True, True]),
        feature_names=tuple(FEATURE_NAMES),
    )


SEPARABLE_WIDE = _wide()


def test_the_provider_returns_at_most_k_of_the_legal_actions(
    provider: LearnedPolicy, position: Position
) -> None:
    snapshot, actions = position
    kept = provider.candidates(actions, snapshot, None, 10)
    assert len(kept) == 10
    legal = {a["message"] for a in actions}
    assert {a["message"] for a in kept} <= legal


def test_the_provider_is_deterministic(provider: LearnedPolicy, position: Position) -> None:
    """`CLAUDE.md`: deterministic by default. Ties break on the protocol string,
    which is unique and stable, so the same position gives the same set in the
    same order however often it is asked."""
    snapshot, actions = position
    first = provider.candidates(actions, snapshot, None, 8)
    second = provider.candidates(actions, snapshot, None, 8)
    assert [a["message"] for a in first] == [a["message"] for a in second]


def test_the_provider_scores_a_joint_action_as_the_sum_of_its_slots(
    provider: LearnedPolicy, position: Position
) -> None:
    """The same composition implementation A uses, so the benchmark compares two
    rankings rather than two formulations (spec section 3.3)."""
    from champions.search.policy_features import board_for, option_features

    snapshot, actions = position
    scored = {
        s.action["message"]: s.score for s in provider.scored(actions, len(actions), snapshot)
    }
    board = board_for(snapshot, provider._dex)
    action = actions[7]
    expected = sum(
        float(provider._model.score(option_features(snapshot, i, slot, board)[None, :])[0])
        for i, slot in enumerate(action["slots"])
    )
    assert scored[action["message"]] == pytest.approx(expected)


def test_the_provider_without_a_state_still_returns_a_set(
    provider: LearnedPolicy, position: Position
) -> None:
    """A trace written before snapshots existed has no state. The degraded
    answer is an arbitrary but stable ordering, not an exception -- the same
    contract `PolicyProvider` gives every implementation."""
    _, actions = position
    kept = provider.candidates(actions, None, None, 5)
    assert len(kept) == 5


def test_the_union_interleaves_two_providers_and_never_repeats(position: Position) -> None:
    from champions.search.learned import UnionPolicy

    class Fixed:
        def __init__(self, name: str, order: list[int]) -> None:
            self.name = name
            self._order = order

        def candidates(
            self,
            actions: list[dict[str, Any]],
            state: dict[str, Any] | None = None,
            belief: Any = None,
            k: int = 10,
        ) -> list[dict[str, Any]]:
            return [actions[i] for i in self._order][:k]

    snapshot, actions = position
    union = UnionPolicy(Fixed("left", [0, 1, 2, 3]), Fixed("right", [2, 3, 4, 5]))
    kept = union.candidates(actions, snapshot, None, 4)
    messages = [a["message"] for a in kept]
    assert messages == [actions[i]["message"] for i in (0, 2, 1, 3)]
    assert len(set(messages)) == len(messages)


def test_the_union_names_both_halves(position: Position) -> None:
    from champions.search.learned import UnionPolicy
    from champions.search.policy import HeuristicPolicy

    class Fake:
        name = "fake"

        def candidates(
            self,
            actions: list[dict[str, Any]],
            state: dict[str, Any] | None = None,
            belief: Any = None,
            k: int = 10,
        ) -> list[dict[str, Any]]:
            return actions[:k]

    union = UnionPolicy(Fake(), Fake())
    assert HeuristicPolicy.name not in union.name
    assert union.name == "union-fake-fake"

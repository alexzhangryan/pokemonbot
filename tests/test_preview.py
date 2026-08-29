"""M4: the preview dataset, the choice model, the value model, the equilibrium.

Mostly synthetic and entirely offline. The corpus is a moving target -- it grows
every time the scraper runs -- so tests that assert on its contents would fail
for the wrong reason. What is checked here is the machinery: that the split
cannot leak, that the model recovers a signal it is given, that antisymmetry is
exact, and that the equilibrium is a game rather than an argmax.
"""

from __future__ import annotations

import numpy as np
import pytest

from champions.preview.dataset import (
    BRING_SIZE,
    PreviewExample,
    Split,
    split_examples,
    subset_index,
    subsets,
)
from champions.preview.equilibrium import (
    NotAntisymmetricError,
    best_response,
    solve_leads,
    solve_preview,
)
from champions.preview.model import fit_choice_model, membership

SIX = ("aaa", "bbb", "ccc", "ddd", "eee", "fff")


def example(
    replay: str,
    series: str,
    brought=(True, True, True, True, False, False),
    led=(True, True, False, False, False, False),
    won=True,
    player="alex",
    opponent="sam",
) -> PreviewExample:
    return PreviewExample(
        replay_id=replay,
        series_id=series,
        side="p1",
        player=player,
        opponent=opponent,
        rating=1200,
        team=SIX,
        opponent_team=SIX,
        brought=tuple(brought),
        led=tuple(led),
        won=won,
        bring_observed=True,
    )


# -- dataset --------------------------------------------------------------


def test_bring_label_is_only_usable_when_four_appeared() -> None:
    """D34: three is not a truncated four, it is a different label."""
    complete = example("r1", "s1")
    assert complete.usable_for_bring
    partial = example("r2", "s2", brought=(True, True, True, False, False, False))
    assert not partial.usable_for_bring
    assert complete.brought_species == ("aaa", "bbb", "ccc", "ddd")
    assert complete.led_species == ("aaa", "bbb")


def test_split_never_puts_a_series_on_both_sides() -> None:
    """A best-of-three shares its twelve Pokemon across games.

    Splitting by replay would put game 1 in training and game 3 in test with the
    same teams on both sides, which measures memorisation and reports it as
    generalisation.
    """
    examples = [example(f"r{i}", f"series{i // 3}") for i in range(60)]
    split = split_examples(examples, test_fraction=0.3)
    assert {e.series_id for e in split.train} & {e.series_id for e in split.test} == set()
    assert len(split.train) + len(split.test) == len(examples)


def test_split_is_deterministic_and_salt_dependent() -> None:
    examples = [example(f"r{i}", f"s{i}") for i in range(40)]
    first = split_examples(examples, salt="a")
    assert [e.replay_id for e in first.test] == [
        e.replay_id for e in split_examples(examples, salt="a").test
    ]
    assert [e.replay_id for e in first.test] != [
        e.replay_id for e in split_examples(examples, salt="b").test
    ]


def test_unseen_players_subset_excludes_anyone_seen_in_training() -> None:
    """The honest number when a laddering player reuses one team all day."""
    examples = [example(f"r{i}", f"s{i}", player=f"p{i % 4}") for i in range(40)]
    split = split_examples(examples, test_fraction=0.5)
    trained_on = {e.player for e in split.train} | {e.opponent for e in split.train}
    assert all(e.player not in trained_on for e in split.unseen_players)


def test_subset_enumeration_is_fixed_and_indexable() -> None:
    assert len(subsets(6, 4)) == 15
    assert len(subsets(4, 2)) == 6
    assert subset_index((True, True, True, True, False, False), BRING_SIZE) == 0
    assert subset_index((True, True, False, False, False, False), BRING_SIZE) is None


def test_empty_split_summary_is_readable() -> None:
    assert "train 0" in Split(train=(), test=()).summary()


# -- the choice model -----------------------------------------------------


def choice_data(n: int = 300, seed: int = 0):
    """Teams of six where one feature decides which four are chosen."""
    rng = np.random.default_rng(seed)
    matrix = membership(subsets(6, 4), 6)
    features = np.zeros((n, 6, 2))
    features[:, :, 0] = 1.0
    features[:, :, 1] = rng.normal(size=(n, 6))
    scores = (features @ np.array([0.0, 5.0])) @ matrix.T
    chosen = np.argmax(scores, axis=1)
    return features, chosen, matrix


def test_model_recovers_a_signal_it_is_given() -> None:
    features, chosen, matrix = choice_data()
    model = fit_choice_model(features, chosen, matrix, l2=0.01)
    assert model.converged
    assert model.weights[1] > 1.0
    predicted = np.argmax(model.subset_probabilities(features), axis=1)
    assert float(np.mean(predicted == chosen)) > 0.95


def test_subset_probabilities_normalise_and_marginals_sum_to_the_choice_size() -> None:
    """The constraint is in the model, not bolted on afterwards.

    Exactly four of six are brought, so the marginals must sum to four for any
    weights at all -- including badly fitted ones. That is the property that
    fitting six independent logistic regressions would not give.
    """
    features, chosen, matrix = choice_data(n=50)
    model = fit_choice_model(features, chosen, matrix, l2=1.0)
    probabilities = model.subset_probabilities(features)
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert np.allclose(model.item_probabilities(features).sum(axis=1), 4.0)


def test_fitting_is_deterministic() -> None:
    """CLAUDE.md: anything that feeds a measurement reproduces from its inputs."""
    features, chosen, matrix = choice_data(n=80)
    first = fit_choice_model(features, chosen, matrix, l2=1.0)
    second = fit_choice_model(features, chosen, matrix, l2=1.0)
    assert np.array_equal(first.weights, second.weights)


def test_a_feature_constant_across_a_team_cannot_matter() -> None:
    """Not a quirk -- a property of choosing a fixed number of items.

    Adding a constant to every Pokemon on a team adds four times that constant
    to all fifteen subset scores, which cancels in the softmax. So team-level
    features are structurally incapable of influencing this model, and only
    within-team contrasts can. Worth a test, because it is the kind of thing
    that otherwise gets discovered as a feature that mysteriously never helps.
    """
    features, chosen, matrix = choice_data(n=60)
    model = fit_choice_model(features, chosen, matrix, l2=1.0)
    shifted = features.copy()
    shifted[:, :, 1] += 7.0
    assert np.allclose(model.subset_probabilities(features), model.subset_probabilities(shifted))


def test_fitting_rejects_empty_and_misshaped_input() -> None:
    matrix = membership(subsets(6, 4), 6)
    with pytest.raises(ValueError):
        fit_choice_model(np.zeros((0, 6, 2)), np.zeros(0, dtype=int), matrix)
    with pytest.raises(ValueError):
        fit_choice_model(np.zeros((5, 2)), np.zeros(5, dtype=int), matrix)


# -- the preview equilibrium ----------------------------------------------

OURS = ("aaa", "bbb", "ccc", "ddd", "eee", "fff")
THEIRS = ("ggg", "hhh", "iii", "jjj", "kkk", "lll")

#: A fixed per-Pokemon strength, so a separable value function can be built
#: without touching the dex.
STRENGTH = {name: value for value, name in enumerate(OURS + THEIRS)}


def separable(ours, theirs) -> float:
    difference = sum(STRENGTH[s] for s in ours) - sum(STRENGTH[s] for s in theirs)
    return float(1.0 / (1.0 + np.exp(-difference / 10.0)))


def interacting(ours, theirs) -> float:
    """A value that depends on the pairing: matching parity wins."""
    ours_parity = sum(STRENGTH[s] % 2 for s in ours)
    theirs_parity = sum(STRENGTH[s] % 2 for s in theirs)
    score = (ours_parity - theirs_parity) * (1 if ours_parity % 2 == 0 else -1)
    mirrored = (theirs_parity - ours_parity) * (1 if theirs_parity % 2 == 0 else -1)
    return float(1.0 / (1.0 + np.exp(-(score - mirrored) / 2.0)))


def test_value_functions_used_here_are_antisymmetric() -> None:
    for value in (separable, interacting):
        assert value(OURS[:4], THEIRS[:4]) + value(THEIRS[:4], OURS[:4]) == pytest.approx(1.0)


def test_a_non_zero_sum_value_function_is_refused() -> None:
    """Better to fail than to solve a game that is not the game."""
    with pytest.raises(NotAntisymmetricError):
        solve_preview(OURS, THEIRS, lambda a, b: 0.7)


def test_the_solve_is_fifteen_by_fifteen_and_returns_a_distribution() -> None:
    solution = solve_preview(OURS, THEIRS, separable)
    assert solution.payoff.shape == (15, 15)
    assert len(solution.our_options) == 15
    assert all(len(option) == BRING_SIZE for option in solution.our_options)
    assert solution.strategy.sum() == pytest.approx(1.0)
    assert (solution.strategy >= -1e-9).all()
    assert 0.0 <= solution.value <= 1.0


def test_a_separable_value_makes_the_preview_an_argmax_not_a_game() -> None:
    """The finding that made the value model grow an interaction term.

    With a payoff of the form g(ours) - g(theirs) the same bring-4 is best
    against every column, so the equilibrium is pure and the exact 15 x 15 solve
    buys nothing over sorting. This test pins that down so the interaction term
    cannot be quietly removed later.
    """
    solution = solve_preview(OURS, THEIRS, separable)
    best_per_column = {
        int(np.argmax(solution.payoff[:, j])) for j in range(solution.payoff.shape[1])
    }
    assert len(best_per_column) == 1
    assert len(solution.support()) == 1


def test_an_interacting_value_produces_a_genuine_game() -> None:
    solution = solve_preview(OURS, THEIRS, interacting)
    best_per_column = {
        int(np.argmax(solution.payoff[:, j])) for j in range(solution.payoff.shape[1])
    }
    assert len(best_per_column) > 1


def test_lead_subgame_is_six_by_six() -> None:
    solution = solve_leads(OURS[:4], THEIRS[:4], separable)
    assert solution.payoff.shape == (6, 6)
    assert all(len(option) == 2 for option in solution.our_options)
    assert solution.strategy.sum() == pytest.approx(1.0)


def test_best_response_beats_the_equilibrium_against_a_predictable_opponent() -> None:
    """Which is exactly why both are offered rather than one being chosen.

    A best response exploits a known distribution and is exploitable in turn;
    the equilibrium is neither. The predictor feeds the first, the solve is the
    second, and the trade between them is the caller's to make.
    """
    solution = solve_preview(OURS, THEIRS, interacting)
    theirs = np.zeros(15)
    theirs[3] = 1.0
    option, value = best_response(OURS, THEIRS, interacting, theirs)
    assert option in solution.our_options
    assert value >= float(solution.strategy @ solution.payoff[:, 3]) - 1e-9


def test_the_equilibrium_value_is_a_win_probability_not_a_zero_sum_score() -> None:
    """A mirror matchup is a coin flip, and must read as 0.5 rather than 0."""
    solution = solve_preview(OURS, OURS, separable)
    assert solution.value == pytest.approx(0.5, abs=1e-6)


# -- the value model ------------------------------------------------------

FORMAT_ID = "gen9championsvgc2026regmb"


@pytest.fixture(scope="module")
def space():
    from champions.dex.loader import Dex
    from champions.preview.features import FeatureSpace

    dex = Dex.load(FORMAT_ID)
    return FeatureSpace.build(dex, {"sneasler": 99, "incineroar": 99, "pelipper": 99})


def test_value_model_is_antisymmetric_by_construction(space) -> None:
    """Not fitted, not approximate: a property of the feature shape.

    The features are `g(ours) - g(theirs)` plus `h(a, b) - h(b, a)`, and there
    is no intercept, so any weights at all give value(a, b) + value(b, a) == 1.
    That is what lets `solve_both` treat the preview payoff as zero sum without
    ever hitting its consistency check.
    """
    from champions.preview.value import PreviewValueModel

    types = tuple(sorted({entry["name"] for entry in space.dex.types.values()}))
    rng = np.random.default_rng(7)
    width = len(PreviewValueModel.INTERACTION_FEATURES) + 4 + len(types) + len(space.vocabulary)
    model = PreviewValueModel(space=space, types=types, weights=rng.normal(size=width))
    a = ("sneasler", "incineroar", "pelipper", "kingambit")
    b = ("garchomp", "whimsicott", "tyranitar", "delphox")
    assert model.win_probability(a, b) + model.win_probability(b, a) == pytest.approx(1.0)


def test_value_model_matrix_agrees_with_pairwise_calls(space) -> None:
    """The 225-cell fast path must equal the slow one it replaces."""
    from champions.preview.value import PreviewValueModel

    types = tuple(sorted({entry["name"] for entry in space.dex.types.values()}))
    rng = np.random.default_rng(11)
    width = len(PreviewValueModel.INTERACTION_FEATURES) + 4 + len(types) + len(space.vocabulary)
    model = PreviewValueModel(space=space, types=types, weights=rng.normal(size=width) * 0.1)
    ours = [("sneasler", "incineroar", "pelipper", "kingambit"), ("garchomp",) * 4]
    theirs = [("whimsicott", "tyranitar", "delphox", "blastoise"), ("pelipper",) * 4]
    matrix = model.matrix(ours, theirs)
    for i, a in enumerate(ours):
        for j, b in enumerate(theirs):
            assert matrix[i, j] == pytest.approx(model.win_probability(a, b))


def test_features_depend_only_on_species_names(space) -> None:
    """The constraint the whole milestone is built around.

    Champions has no open team sheets, so a preview feature that reads an item
    or an ability would score well offline and be unusable in the game. The
    corpus supplies labels, never inputs (D33). Here that is checked the only
    way it can be: the feature row for a Pokemon is a function of the twelve
    names and nothing else, so two calls with identical names must agree
    exactly regardless of which replay they came from.
    """
    team = ("sneasler", "incineroar", "pelipper", "garchomp", "delphox", "tyranitar")
    opponents = ("whimsicott", "blastoise", "kingambit", "swampert", "metagross", "sableye")
    first = space.matrix(team, opponents)
    second = space.matrix(tuple(team), tuple(opponents))
    assert np.array_equal(first, second)

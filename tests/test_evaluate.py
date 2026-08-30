"""M2: the bootstrap evaluation function.

The weights are hand chosen, so there is no ground truth to check them against
and pretending otherwise would be theatre. What is checkable, and what these
tests check, are the structural properties the rest of the engine relies on:

- antisymmetry on equal information, because the matrix game treats the
  payoff as zero sum, and its deliberate absence on unequal information;
- terminal positions saturating at 0 and 1;
- monotonicity in each feature, which is the only claim the ordering makes;
- that the opponent's unrevealed Pokemon count as alive, which is the one place
  a plausible implementation is badly wrong.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from champions.search.evaluate import (
    BOOTSTRAP_WEIGHTS,
    IS_CALIBRATED,
    MODEL,
    WEIGHTS,
    WEIGHTS_PATH,
    Model,
    evaluate,
    features,
    load_model,
    win_prob,
)


def _mon(
    species: str,
    hp_pct: float = 100.0,
    fainted: bool = False,
    status: str | None = None,
    boosts: dict[str, int] | None = None,
    active: bool = False,
) -> dict[str, Any]:
    return {
        "species": species,
        "name": species,
        "hp_pct": 0.0 if fainted else hp_pct,
        "fainted": fainted,
        "status": status,
        "boosts": boosts or {},
        "active": active,
        "types": ["Normal"],
    }


def _side(active: list, bench: list) -> dict[str, Any]:
    seen = [p for p in active if p is not None] + bench
    return {
        "active": active,
        "bench": bench,
        "remaining": sum(1 for p in seen if not p["fainted"]),
        "revealed": len(seen),
    }


def _snapshot(
    ours: dict[str, Any] | None = None,
    theirs: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    base = {
        "turn": 1,
        "weather": {},
        "fields": {},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": ours
        or _side(
            [_mon("Incineroar", active=True), _mon("Aegislash", active=True)],
            [_mon("Corviknight"), _mon("Garchomp")],
        ),
        "theirs": theirs
        or _side(
            [_mon("Skarmory", active=True), _mon("Dragonite", active=True)],
            [_mon("Pelipper"), _mon("Arcanine")],
        ),
    }
    base.update(overrides)
    return base


def _mirror(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The same position seen from the other chair."""
    flipped = copy.deepcopy(snapshot)
    flipped["ours"], flipped["theirs"] = flipped["theirs"], flipped["ours"]
    flipped["side_conditions"], flipped["opponent_side_conditions"] = (
        flipped["opponent_side_conditions"],
        flipped["side_conditions"],
    )
    return flipped


def test_a_symmetric_position_is_a_coin_flip() -> None:
    assert win_prob(_snapshot()) == pytest.approx(0.5)


def test_the_evaluation_is_antisymmetric_between_equally_revealed_sides() -> None:
    """A position and its mirror must sum to one when both sides are fully seen.

    The matrix game treats one number as both players' payoff with opposite
    signs. If the evaluation were not antisymmetric on equal information, the
    game would not be zero sum and the equilibrium would be solving a different
    problem than the one posed. The restriction to equal information is real and
    is the subject of the next test.
    """
    positions = [
        _snapshot(),
        _snapshot(
            side_conditions={"TAILWIND": 3, "STEALTH_ROCK": 1},
            opponent_side_conditions={"SPIKES": 2},
        ),
        _snapshot(
            ours=_side(
                [_mon("A", 60.0, status="PAR", active=True), _mon("B", active=True)],
                [_mon("C"), _mon("D")],
            ),
            theirs=_side(
                [_mon("E", status="SLP", active=True), _mon("F", 10.0, active=True)],
                [_mon("G"), _mon("H")],
            ),
        ),
        _snapshot(
            ours=_side(
                [_mon("A", 40.0, active=True), None],
                [_mon("B", fainted=True), _mon("C"), _mon("D")],
            ),
            theirs=_side(
                [_mon("E", active=True), _mon("F", 80.0, active=True)],
                [_mon("G"), _mon("H")],
            ),
        ),
    ]
    for position in positions:
        assert win_prob(position) + win_prob(_mirror(position)) == pytest.approx(1.0, abs=1e-9)


def test_the_mirror_is_not_symmetric_when_information_is_not() -> None:
    """Deliberate, and worth pinning down so it is not "fixed" later.

    We see our own team exactly and theirs only as revealed. Mirroring a
    partially revealed position swaps the information along with the position,
    so it asks a different question and the two values do not have to sum to
    one. The agent only ever evaluates from its own information state, which is
    where the zero sum assumption is actually used.
    """
    lopsided = _snapshot(
        ours=_side([_mon("A", active=True), _mon("B", active=True)], [_mon("C"), _mon("D")]),
        theirs=_side([_mon("E", active=True)], []),
    )
    assert win_prob(lopsided) + win_prob(_mirror(lopsided)) != pytest.approx(1.0, abs=1e-6)


def test_terminal_positions_saturate() -> None:
    """A wiped side must have every one of its picked Pokemon revealed and
    fainted -- which is what actually happens, since a faint reveals."""
    wiped = _side(
        [None, None],
        [_mon(name, fainted=True) for name in "ABCD"],
    )
    healthy = _side([_mon("W", active=True), _mon("X", active=True)], [_mon("Y"), _mon("Z")])

    assert win_prob(_snapshot(ours=healthy, theirs=wiped)) == 1.0
    assert win_prob(_snapshot(ours=wiped, theirs=healthy)) == 0.0


def test_a_terminal_position_reports_infinite_log_odds_rather_than_a_huge_number() -> None:
    """So a caller cannot mistake "certain" for "very confident"."""
    wiped = _side([None, None], [_mon(name, fainted=True) for name in "ABCD"])
    healthy = _side([_mon("W", active=True), _mon("X", active=True)], [_mon("Y"), _mon("Z")])
    assert evaluate(_snapshot(ours=healthy, theirs=wiped)).log_odds == float("inf")
    assert evaluate(_snapshot(ours=wiped, theirs=healthy)).log_odds == float("-inf")


def test_unrevealed_opponents_count_as_alive() -> None:
    """The failure mode this feature exists to prevent.

    Turn one, the opponent has revealed exactly their two leads. Counting only
    revealed Pokemon would score them as having two against our four, i.e. as
    already half dead, and the agent would play a won game from move one.
    """
    ours = _side(
        [_mon("A", active=True), _mon("B", active=True)],
        [_mon("C"), _mon("D")],
    )
    theirs_revealed_two = _side([_mon("E", active=True), _mon("F", active=True)], [])

    vector = features(_snapshot(ours=ours, theirs=theirs_revealed_two), picked_team_size=4)

    assert vector["pokemon_advantage"] == 0.0
    assert vector["hp_advantage"] == pytest.approx(0.0)
    assert win_prob(_snapshot(ours=ours, theirs=theirs_revealed_two)) == pytest.approx(0.5)


def test_opponent_faints_are_counted_even_when_the_rest_is_unrevealed() -> None:
    ours = _side([_mon("A", active=True), _mon("B", active=True)], [_mon("C"), _mon("D")])
    theirs = _side([_mon("E", active=True), None], [_mon("F", fainted=True)])

    vector = features(_snapshot(ours=ours, theirs=theirs), picked_team_size=4)

    assert vector["pokemon_advantage"] == 1.0
    assert win_prob(_snapshot(ours=ours, theirs=theirs)) > 0.5


def test_picked_team_size_is_honoured() -> None:
    """Regulations change it, so it is a parameter and not a constant."""
    ours = _side([_mon("A", active=True)], [_mon("B")])
    theirs = _side([_mon("C", active=True)], [])

    assert features(_snapshot(ours=ours, theirs=theirs), 2)["pokemon_advantage"] == 0.0
    assert features(_snapshot(ours=ours, theirs=theirs), 4)["pokemon_advantage"] == -2.0


@pytest.mark.parametrize(
    "feature,better,worse",
    [
        (
            "pokemon_advantage",
            {"theirs": _side([_mon("E", active=True)], [_mon("F", fainted=True)])},
            {"theirs": _side([_mon("E", active=True)], [_mon("F")])},
        ),
        (
            "hp_advantage",
            {"theirs": _side([_mon("E", 20.0, active=True)], [_mon("F")])},
            {"theirs": _side([_mon("E", 90.0, active=True)], [_mon("F")])},
        ),
        (
            "status_advantage",
            {"theirs": _side([_mon("E", status="SLP", active=True)], [_mon("F")])},
            {"theirs": _side([_mon("E", active=True)], [_mon("F")])},
        ),
        (
            "speed_control",
            {"side_conditions": {"TAILWIND": 4}},
            {"opponent_side_conditions": {"TAILWIND": 4}},
        ),
        (
            "hazard_advantage",
            {"opponent_side_conditions": {"STEALTH_ROCK": 1}},
            {"side_conditions": {"STEALTH_ROCK": 1}},
        ),
    ],
)
def test_each_feature_moves_the_evaluation_the_right_way(
    feature: str, better: dict[str, Any], worse: dict[str, Any]
) -> None:
    """Every feature the model has an opinion about points the right way.

    Under the hand-chosen weights this held for all seven, because all seven were
    chosen positive. M6 fit them, and the fit does not have an opinion about all
    seven: `hazard_advantage` came back with its sign undetermined by 750
    self-play battles *and* by 25,000 corpus ones, so it ships at zero. Asserting
    monotonicity there would be asserting a belief nothing measured supports.

    So the claim is conditional on the weight, and a weight of zero is checked to
    be an actual zero rather than a feature quietly dropped from the model.
    """
    weight = WEIGHTS.get(feature)
    assert weight is not None, f"{feature} is not in the model at all"
    if weight == 0.0:
        assert win_prob(_snapshot(**better)) == win_prob(_snapshot(**worse)), feature
        return
    assert win_prob(_snapshot(**better)) > win_prob(_snapshot(**worse)), feature


def test_the_evaluation_stays_a_probability() -> None:
    """Including on lopsided positions, where a naive sigmoid overflows."""
    extreme = _snapshot(
        ours=_side(
            [_mon("A", boosts={"atk": 6, "spe": 6}, active=True), _mon("B", active=True)],
            [_mon("C"), _mon("D")],
        ),
        theirs=_side([_mon("E", 1.0, status="SLP", active=True)], [_mon("F", fainted=True)]),
        side_conditions={"TAILWIND": 4},
    )
    for position in (extreme, _mirror(extreme)):
        value = win_prob(position)
        assert 0.0 <= value <= 1.0


def test_features_and_value_come_back_together() -> None:
    """The coach explains a decision from the same numbers that made it."""
    result = evaluate(_snapshot())
    assert set(result.features) == set(features(_snapshot()))
    assert result.win_prob == pytest.approx(win_prob(_snapshot()))


def test_calibration_is_claimed_only_when_it_has_been_measured() -> None:
    """Load bearing: the coach reports ex-ante loss in probability units and must
    not do that with hand-chosen weights (`docs/04` section 5).

    Asserted as the invariant rather than as a constant. Before M6 this test said
    `IS_CALIBRATED is False`, which stops being a check the moment the fit lands
    -- either it fails and gets edited to True, and then nothing tests anything,
    or it silently passes because someone flipped the flag by hand. What must
    hold at every milestone is that the flag agrees with whether a fitted weights
    file exists, since that file is written by the same run that writes the
    reliability diagram.
    """
    fitted = WEIGHTS_PATH("gen9championsvgc2026regmb").is_file()
    assert IS_CALIBRATED is fitted
    assert evaluate(_snapshot()).calibrated is fitted
    if not fitted:
        assert MODEL.source == "bootstrap"


def test_an_absent_fit_falls_back_to_the_bootstrap_and_says_so() -> None:
    """A fresh clone has no weights file: the agent has to play before the fit
    has anything to read. That path must work and must not claim calibration."""
    model = load_model("nosuchformat")
    assert model.calibrated is False
    assert model.source == "bootstrap"
    assert model.weights == BOOTSTRAP_WEIGHTS
    assert model.platt_a == 1.0 and model.platt_b == 0.0


def test_a_fitted_model_applies_its_platt_terms() -> None:
    """The calibration is not decoration. A model whose Platt slope is not 1
    must produce a different number from its raw weights, or the scaling fit on
    the validation split is being computed and then thrown away.
    """
    raw = Model(weights={"pokemon_advantage": 1.0})
    scaled = Model(weights={"pokemon_advantage": 1.0}, platt_a=0.5, platt_b=0.25)
    vector = {"pokemon_advantage": 2.0}
    assert raw.log_odds(vector) == pytest.approx(2.0)
    assert scaled.log_odds(vector) == pytest.approx(1.25)

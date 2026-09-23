"""D91: the kinds of a joint action, the prior on them, the solver that pins
the column player's kind marginals to it, and the two column defects it was
built beside -- a side with only its second slot alive lost its action, and
the opponent never switched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_policy import PASS, _act, _mon, _move, _state

from champions.dex.loader import Dex
from champions.formats import FORMAT_ID
from champions.search import kinds
from champions.search.kinds import (
    KindPrior,
    action_kind,
    implied_kind_mass,
    load_kind_prior,
    solve_columns,
)
from champions.search.matrix import solve_both, solve_constrained
from champions.search.payoff import TurnModel, payoff_matrix
from champions.search.policy import EMPTY_SLOT, opponent_candidates


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


# -- the solver --------------------------------------------------------------


def _random_game(seed: int, rows: int = 7, columns: int = 9) -> np.ndarray:
    return np.random.default_rng(seed).uniform(0.0, 1.0, size=(rows, columns))


def test_zero_weight_is_the_plain_equilibrium() -> None:
    payoff = _random_game(1)
    kinds_ = ["attack+attack"] * 5 + ["attack+protect"] * 4
    plain = solve_both(payoff)
    pinned = solve_constrained(payoff, kinds_, {"attack+attack": 0.5, "attack+protect": 0.5}, 0.0)
    assert pinned.value == pytest.approx(plain.value, abs=1e-9)


def test_full_weight_pins_the_kind_marginals_and_the_value_is_consistent() -> None:
    payoff = _random_game(2)
    kinds_ = ["a", "a", "a", "b", "b", "b", "c", "c", "c"]
    prior = {"a": 0.2, "b": 0.5, "c": 0.3}
    eq = solve_constrained(payoff, kinds_, prior, 1.0)
    mass = implied_kind_mass(eq.column, kinds_)
    for k, p in prior.items():
        assert mass[k] == pytest.approx(p, abs=1e-6)
    assert eq.row.sum() == pytest.approx(1.0)
    assert float(eq.row @ payoff @ eq.column) == pytest.approx(eq.value, abs=1e-9)

    # The row strategy is optimal for the pinned game: no pure row does better
    # against the worst column of each kind, weighted by the prior.
    def pinned_value(x: np.ndarray) -> float:
        against = x @ payoff
        return sum(
            p * min(against[j] for j, kind in enumerate(kinds_) if kind == k)
            for k, p in prior.items()
        )

    best_pure = max(pinned_value(np.eye(payoff.shape[0])[i]) for i in range(payoff.shape[0]))
    assert pinned_value(eq.row) >= best_pure - 1e-9


def test_a_free_protect_stops_dominating_when_the_prior_says_people_rarely_protect() -> None:
    # Two of our rows, four of their columns. Their Protect columns cost us
    # nothing and their attacks cost us a lot, so the plain adversary never
    # attacks -- and we can never be punished for attacking. Pinning the
    # protect kind at ten percent makes their attacks real again.
    payoff = np.array(
        [
            [0.5, 0.5, 0.2, 0.9],  # we attack: protected -> even; they attack -> we trade
            [0.5, 0.5, 0.6, 0.6],  # we protect: safe either way
        ]
    )
    kinds_ = ["attack+protect", "protect+protect", "attack+attack", "attack+attack"]
    prior = {"attack+protect": 0.05, "protect+protect": 0.05, "attack+attack": 0.9}
    plain = solve_both(payoff)
    assert implied_kind_mass(plain.column, kinds_)["attack+attack"] == pytest.approx(0.0, abs=1e-6)
    pinned = solve_constrained(payoff, kinds_, prior, 0.8)
    mass = implied_kind_mass(pinned.column, kinds_)
    assert mass["attack+attack"] >= 0.8 * 0.9 - 1e-6
    assert pinned.value > plain.value


def test_solve_columns_falls_back_without_a_prior_or_a_matching_kind() -> None:
    payoff = _random_game(3, 3, 4)
    columns = [{"slots": [{"kind": "move", "move": "tackle"}]} for _ in range(4)]
    eq, note = solve_columns(payoff, columns, 1, None)
    assert note["weight"] == 0.0 and note["prior"] is None
    assert eq.value == pytest.approx(solve_both(payoff).value)
    prior = KindPrior(FORMAT_ID, FORMAT_ID, {"1": {"switch": 1.0}}, {"1": 10})
    eq2, note2 = solve_columns(payoff, columns, 1, prior)
    assert note2["prior"] is None
    assert eq2.value == pytest.approx(solve_both(payoff).value)


# -- kinds and the prior -----------------------------------------------------


def test_action_kind_names_the_slots_in_sorted_order() -> None:
    protect = {"kind": "move", "move": "protect"}
    fake = {"kind": "move", "move": "fakeout"}
    attack = {"kind": "move", "move": "closecombat"}
    switch = {"kind": "switch", "species": "rillaboom"}
    assert action_kind({"slots": [attack, protect]}) == "attack+protect"
    assert action_kind({"slots": [protect, attack]}) == "attack+protect"
    assert action_kind({"slots": [fake, switch]}) == "fakeout+switch"
    assert action_kind({"slots": [attack]}) == "attack"
    assert action_kind({"slots": [dict(EMPTY_SLOT), attack]}) == "attack+none"
    assert action_kind({"slots": []}) == "none"


def test_the_prior_renormalises_over_the_kinds_the_columns_offer() -> None:
    prior = KindPrior(
        FORMAT_ID,
        FORMAT_ID,
        {"1": {"attack+attack": 0.4, "attack+switch": 0.2, "attack+protect": 0.4}},
        {"1": 100},
    )
    over = prior.over(1, ["attack+attack", "attack+protect", "attack+protect"])
    assert over == pytest.approx({"attack+attack": 0.5, "attack+protect": 0.5})
    assert prior.over(1, ["fakeout+fakeout"]) is None
    # Turns beyond the last bucket read the last bucket.
    assert kinds.bucket(1) == "1" and kinds.bucket(3) == "3" and kinds.bucket(12) == "4+"


def test_load_kind_prior_reads_the_format_or_its_lineage(tmp_path: Path) -> None:
    assert load_kind_prior("gen9championsvgc2026regmc", tmp_path) is None
    lent = tmp_path / "actionkinds.gen9championsvgc2026regmb.json"
    lent.write_text(
        json.dumps(
            {
                "format_id": "gen9championsvgc2026regmb",
                "buckets": {"1": {"attack+attack": 1.0}},
                "counts": {"1": 5},
            }
        )
    )
    prior = load_kind_prior("gen9championsvgc2026regmc", tmp_path)
    assert prior is not None and prior.lent and prior.source_format == "gen9championsvgc2026regmb"
    own = tmp_path / "actionkinds.gen9championsvgc2026regmc.json"
    own.write_text(
        json.dumps(
            {
                "format_id": "gen9championsvgc2026regmc",
                "buckets": {"1": {"attack+switch": 1.0}},
                "counts": {"1": 5},
            }
        )
    )
    prior = load_kind_prior("gen9championsvgc2026regmc", tmp_path)
    assert prior is not None and not prior.lent and prior.rates(1) == {"attack+switch": 1.0}


def test_the_shipped_prior_exists_and_says_people_rarely_double_protect() -> None:
    prior = load_kind_prior(FORMAT_ID)
    assert prior is not None and not prior.lent
    for turn in (1, 2, 3, 4):
        rates = prior.rates(turn)
        assert abs(sum(rates.values()) - 1.0) < 1e-3
        assert rates.get("protect+protect", 0.0) < 0.1
        assert rates.get("attack+switch", 0.0) > 0.05


# -- the columns --------------------------------------------------------------


def _theirs_side(active: list[Any], bench: list[Any]) -> dict[str, Any]:
    seen = [p for p in active if p] + bench
    return {
        "active": active,
        "bench": bench,
        "remaining": sum(1 for p in seen if not p["fainted"]),
        "revealed": len(seen),
    }


def test_a_side_with_only_its_second_slot_alive_keeps_that_slot_index(dex: Dex) -> None:
    ours = [_mon(dex, "Rillaboom"), _mon(dex, "Incineroar")]
    fainted = _mon(dex, "Gholdengo", known=False, fainted=True)
    alive = _mon(dex, "Sneasler", known=False, revealed_moves=["closecombat", "protect"])
    state = _state(ours, [fainted, alive], turn=5)
    columns = opponent_candidates(state, dex, 24, switch_columns=0)
    assert columns
    for column in columns:
        assert len(column["slots"]) == 2
        assert column["slots"][0]["kind"] == "none"
        assert column["slots"][1]["kind"] == "move"
    # And the payoff model now sees the attack: our double pass fares worse
    # against Close Combat than against a column of nothing.
    rows = [_act(PASS, PASS)]
    attack = next(c for c in columns if c["slots"][1]["move"] == "closecombat")
    nothing = {"slots": [dict(EMPTY_SLOT), dict(EMPTY_SLOT)], "label": "nothing"}
    matrix = payoff_matrix(state, rows, [attack, nothing], TurnModel(dex))
    assert matrix[0, 0] < matrix[0, 1]


def test_switch_columns_name_each_living_slot_and_each_bench_pokemon(dex: Dex) -> None:
    ours = [_mon(dex, "Rillaboom"), _mon(dex, "Incineroar")]
    a = _mon(dex, "Sneasler", known=False, revealed_moves=["closecombat"])
    b = _mon(dex, "Gholdengo", known=False, revealed_moves=["makeitrain"])
    bench_alive = _mon(dex, "Garchomp", known=False)
    bench_alive["active"] = False
    bench_dead = _mon(dex, "Pelipper", known=False, fainted=True)
    bench_dead["active"] = False
    state = _state(ours, [a, b], turn=2)
    state["theirs"] = _theirs_side([a, b], [bench_alive, bench_dead])
    columns = opponent_candidates(state, dex, 24)
    switches = [c for c in columns if "switch" in c["kinds"]]
    assert {(c["slots"][0]["kind"], c["slots"][1]["kind"]) for c in switches} == {
        ("switch", "move"),
        ("move", "switch"),
    }
    assert all(
        next(s for s in c["slots"] if s["kind"] == "switch")["species"] == "Garchomp"
        for c in switches
    )
    assert (
        len(switches) == 2
    )  # one living bench Pokemon, two slots; the fainted one is not a target
    assert not [
        c for c in opponent_candidates(state, dex, 24, switch_columns=0) if "switch" in c["kinds"]
    ]
    assert len(opponent_candidates(state, dex, 24, switch_columns=1)) == 25 or len(
        columns
    ) - 1 == len(opponent_candidates(state, dex, 24, switch_columns=1))


def test_a_switch_column_puts_the_incoming_pokemon_in_front_of_our_attack(dex: Dex) -> None:
    ours = [_mon(dex, "Rillaboom"), _mon(dex, "Incineroar")]
    a = _mon(dex, "Sneasler", known=False, revealed_moves=["closecombat"])
    b = _mon(dex, "Gholdengo", known=False, revealed_moves=["makeitrain"])
    incoming = _mon(dex, "Corviknight", known=False)
    incoming["active"] = False
    state = _state(ours, [a, b], turn=2)
    state["theirs"] = _theirs_side([a, b], [incoming])
    columns = opponent_candidates(state, dex, 24)
    switch_a = next(c for c in columns if c["slots"][0]["kind"] == "switch")
    stay = {"slots": [dict(EMPTY_SLOT), switch_a["slots"][1]], "label": "stay"}
    # Grassy Glide into slot 1: Sneasler takes it, or Corviknight comes in and resists it.
    rows = [_act(_move(dex, "grassyglide", 1), PASS)]
    matrix = payoff_matrix(state, rows, [switch_a, stay], TurnModel(dex, place_incoming=True))
    assert matrix[0, 0] < matrix[0, 1]


def test_the_kind_of_every_column_is_one_the_prior_can_name(dex: Dex) -> None:
    ours = [_mon(dex, "Rillaboom"), _mon(dex, "Incineroar")]
    a = _mon(
        dex,
        "Sneasler",
        known=False,
        revealed_moves=["closecombat", "fakeout", "protect"],
        first_turn=True,
    )
    b = _mon(dex, "Gholdengo", known=False, revealed_moves=["makeitrain"])
    bench = _mon(dex, "Garchomp", known=False)
    bench["active"] = False
    state = _state(ours, [a, b], turn=1)
    state["theirs"] = _theirs_side([a, b], [bench])
    columns = opponent_candidates(state, dex, 24)
    prior = load_kind_prior(FORMAT_ID)
    assert prior is not None
    over = prior.over(1, [action_kind(c) for c in columns])
    assert over is not None
    assert {"attack+attack", "attack+protect", "attack+fakeout", "attack+switch"} <= set(over)


# -- the unseen bench ---------------------------------------------------------


def test_unseen_previewed_pokemon_join_the_bench_up_to_the_bring(dex: Dex) -> None:
    from champions.protocol.state import annotate_unseen

    ours = [_mon(dex, "Rillaboom"), _mon(dex, "Incineroar")]
    a = _mon(dex, "Sneasler", known=False)
    b = _mon(dex, "Gholdengo", known=False)
    state = _state(ours, [a, b], turn=1)
    preview = ["Sneasler", "Gholdengo", "Garchomp", "Pelipper", "Farigiraf", "Kingambit"]
    added = annotate_unseen(state, preview, dex, believed_ability=lambda s: "intimidate")
    assert added == 4
    bench = state["theirs"]["bench"]
    assert [p["species"] for p in bench] == ["Garchomp", "Pelipper", "Farigiraf", "Kingambit"]
    assert all(p["unseen"] and p["hp_pct"] == 100.0 and not p["fainted"] for p in bench)
    assert all(p["ability"] == "intimidate" and p["base_stats"] for p in bench)
    assert state["theirs"]["unseen"] == 2  # only two of the four can actually be in the bring
    # Every one of the four seen: nothing to add.
    full = _state(ours, [a, b], turn=5)
    full["theirs"]["bench"] = [
        _mon(dex, "Garchomp", known=False),
        _mon(dex, "Pelipper", known=False),
    ]
    assert annotate_unseen(full, preview, dex) == 0
    # And the switch columns can now name them.
    columns = opponent_candidates(state, dex, 24)
    switched = {s["species"] for c in columns for s in c["slots"] if s["kind"] == "switch"}
    assert switched == {"Garchomp", "Pelipper", "Farigiraf", "Kingambit"}


# -- row offsets (D94) ---------------------------------------------------------


def test_row_offsets_subtract_the_measured_optimism_from_rows_of_that_kind(tmp_path: Path) -> None:
    from champions.search.kinds import RowOffsets, apply_row_offsets, load_row_offsets

    attack = {"kind": "move", "move": "closecombat"}
    switch = {"kind": "switch", "species": "rillaboom"}
    rows = [{"slots": [attack, attack]}, {"slots": [attack, switch]}, {"slots": [switch, attack]}]
    payoff = np.full((3, 2), 0.5)
    offsets = RowOffsets(FORMAT_ID, {"attack+switch": 0.11}, {})
    shifted, applied = apply_row_offsets(payoff, rows, offsets)
    assert applied == {"attack+switch": 0.11}
    assert shifted[0].tolist() == [0.5, 0.5]
    assert shifted[1].tolist() == pytest.approx([0.39, 0.39])
    assert shifted[2].tolist() == pytest.approx([0.39, 0.39])
    assert payoff[1, 0] == 0.5  # the input is not mutated
    same, none = apply_row_offsets(payoff, rows, None)
    assert same is payoff and none == {}
    # Loading: the format's own file, else the lineage's, else nothing.
    assert load_row_offsets(FORMAT_ID, tmp_path) is None
    (tmp_path / f"rowoffsets.{FORMAT_ID}.json").write_text(
        json.dumps({"offsets": {"attack+switch": 0.11}, "source": "test"})
    )
    loaded = load_row_offsets(FORMAT_ID, tmp_path)
    assert loaded is not None and loaded.for_row(rows[1]) == 0.11 and loaded.for_row(rows[0]) == 0.0
    assert loaded.provenance == {"source": "test"}

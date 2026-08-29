"""T0.9: js/sim_server.js exposes create, step, serialize, and deserialize over
JSON-RPC on stdio."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from champions.search.oracle import SimServer, SimServerError
from champions.teams import ALPHA, BETA, load_team

FORMAT_ID = "gen9championsvgc2026regmb"
SEED = [1, 2, 3, 4]


@pytest.fixture(scope="module")
def sim() -> Iterator[SimServer]:
    with SimServer() as server:
        yield server


def _fresh_battle(sim: SimServer, seed: list[int] | None = None) -> int:
    state = sim.create(FORMAT_ID, load_team(ALPHA), load_team(BETA), seed=seed or SEED)
    return int(state["handle"])


def test_ping(sim: SimServer) -> None:
    assert sim.ping() is True


def test_create_starts_at_team_preview(sim: SimServer) -> None:
    state = sim.create(FORMAT_ID, load_team(ALPHA), load_team(BETA), seed=SEED)
    assert state["turn"] == 0
    assert state["requestState"] == "teampreview"
    assert state["ended"] is False


def test_step_through_preview_and_a_turn(sim: SimServer) -> None:
    handle = _fresh_battle(sim)

    state = sim.step(handle, "team 1234", "team 1234")
    assert state["turn"] == 1
    assert state["requestState"] == "move"

    state = sim.step(handle, "default", "default")
    assert state["turn"] == 2


def test_serialize_round_trips_to_an_equivalent_battle(sim: SimServer) -> None:
    handle = _fresh_battle(sim)
    sim.step(handle, "team 1234", "team 1234")
    original = sim.step(handle, "default", "default")

    revived = sim.deserialize(sim.serialize(handle))

    assert revived["turn"] == original["turn"]
    assert revived["ended"] == original["ended"]
    assert revived["requestState"] == original["requestState"]


def test_deserialized_battle_can_be_stepped(sim: SimServer) -> None:
    """A deserialized battle is inert until restarted; without that it throws."""
    handle = _fresh_battle(sim)
    sim.step(handle, "team 1234", "team 1234")

    revived = sim.deserialize(sim.serialize(handle))
    stepped = sim.step(revived["handle"], "default", "default")

    assert stepped["turn"] == 2


def test_request_exposes_both_sides(sim: SimServer) -> None:
    handle = _fresh_battle(sim)
    sim.step(handle, "team 1234", "team 1234")

    request = sim.request(handle)

    assert request["p1"] is not None
    assert request["p2"] is not None


def test_same_seed_produces_the_same_battle(sim: SimServer) -> None:
    """Deterministic by default: seed everything (CLAUDE.md conventions)."""
    logs = []
    for _ in range(2):
        handle = _fresh_battle(sim, seed=[9, 9, 9, 9])
        sim.step(handle, "team 1234", "team 1234")
        state = sim.step(handle, "default", "default")
        logs.append(state["log"])
        sim.destroy(handle)

    assert logs[0] == logs[1]


def _advance(sim: SimServer, handle: int, decision_points: int) -> dict[str, Any]:
    """Drive a battle forward N decision points with default choices.

    A decision point is not a turn: a KO produces a forced-switch request that
    consumes a step without incrementing `turn`, and which turns those are is a
    property of the teams. Tests that need "the battle moved" must not assume
    one step equals one turn.
    """
    state: dict[str, Any] = {}
    for _ in range(decision_points):
        state = sim.step(handle, "default", "default")
        if state["ended"]:
            break
    return state


def test_cloning_does_not_corrupt_the_parent_battle(sim: SimServer) -> None:
    """Regression: State.serializeBattle assigns state.log by reference, so a
    revived clone shared its parent's log array and every clone's step appended
    to the parent. Showdown throws "Infinite loop" once the log runs 1000 lines
    past sentLogPos, which surfaced as clone ~44 failing for no visible reason."""
    handle = _fresh_battle(sim)
    sim.step(handle, "team 1234", "team 1234")
    before = sim.step(handle, "default", "default")

    for _ in range(60):
        cloned = sim.clone(handle)["handle"]
        sim.step(cloned, "default", "default")
        sim.destroy(cloned)

    # Cloning is a read of the parent. Nothing about it may have moved.
    after = sim.request(handle)
    assert after["p1"] == sim.request(handle)["p1"]
    still = sim.step(handle, "default", "default")
    assert still["log"][: len(before["log"])] == before["log"]
    assert len(still["log"]) > len(before["log"])
    assert not still["ended"]


def test_clone_is_independent_of_its_parent(sim: SimServer) -> None:
    handle = _fresh_battle(sim)
    sim.step(handle, "team 1234", "team 1234")
    before = sim.step(handle, "default", "default")

    cloned = sim.clone(handle)["handle"]
    advanced = _advance(sim, cloned, 10)

    # The clone moved a long way; the parent must be exactly where it was.
    assert advanced["turn"] > before["turn"]
    assert len(advanced["log"]) > len(before["log"])

    parent = sim.request(handle)
    assert parent["p1"]["side"]["name"] == "p1"
    resumed = sim.step(handle, "default", "default")
    assert resumed["log"][: len(before["log"])] == before["log"]
    # One step off the parent is one step, not eleven.
    assert len(resumed["log"]) < len(advanced["log"])


def test_unknown_method_reports_an_error(sim: SimServer) -> None:
    with pytest.raises(SimServerError, match="Method not found"):
        sim.call("no_such_method")


def test_unknown_handle_reports_an_error(sim: SimServer) -> None:
    with pytest.raises(SimServerError, match="No such battle handle"):
        sim.step(999_999, "default", "default")


def test_destroy_frees_the_handle(sim: SimServer) -> None:
    handle = _fresh_battle(sim)
    sim.destroy(handle)

    with pytest.raises(SimServerError, match="No such battle handle"):
        sim.step(handle, "default", "default")

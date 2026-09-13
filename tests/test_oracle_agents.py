"""M8: the oracle opponent and the arms that are handed it.

The oracle has one job -- answer the belief's three questions from the team
file, exactly -- and its stats are checkable against the numbers the
simulator itself reports in a request, which is `tests/test_stats.py`'s
pattern. The arms are checked for the one thing a unit test can say about an
agent: that each completes real games and puts on the trace what
`docs/specs/2026-09-13-engine-gate.md` says it must.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from champions.agents.oracle import TeamOracle
from champions.dex.loader import Dex, to_id
from champions.harness.ladder import run_matchup
from champions.search.oracle import SimServer
from champions.teams import ALPHA, BETA, load_team
from scripts.run_ladder import build_arm

FORMAT_ID = "gen9championsvgc2026regmb"


@pytest.fixture(scope="module")
def dex() -> Dex:
    return Dex.load(FORMAT_ID)


def test_the_oracle_reports_the_simulators_own_stats(dex: Dex) -> None:
    oracle = TeamOracle(load_team(BETA), dex)
    with SimServer() as sim:
        state = sim.create(FORMAT_ID, load_team(ALPHA), load_team(BETA), seed=[1, 2, 3, 4])
        request = sim.request(int(state["handle"]))["p2"]
    for pokemon in request["side"]["pokemon"]:
        species = pokemon["details"].split(",")[0]
        stats = oracle.stats_for(to_id(species))
        assert stats is not None, species
        for stat_id, value in pokemon["stats"].items():
            assert stats[stat_id] == value, (species, stat_id)
        assert stats["hp"] == int(pokemon["condition"].split("/")[1].split(" ")[0])


def test_the_oracle_answers_the_set_and_the_moves(dex: Dex) -> None:
    oracle = TeamOracle(load_team(BETA), dex)
    garchomp = oracle.set_for("garchomp")
    assert garchomp is not None
    assert garchomp.species == "garchomp"
    assert garchomp.ability == "roughskin"
    assert len(garchomp.moves) == 4
    assert oracle.believed_moves("garchomp") == sorted(garchomp.moves)
    assert oracle.set_for("mewtwo") is None
    assert oracle.believed_moves("mewtwo") == []


def test_the_oracle_follows_a_forme_to_its_base_set(dex: Dex) -> None:
    oracle = TeamOracle(load_team(ALPHA), dex)
    shield = oracle.stats_for("aegislash")
    blade = oracle.stats_for("aegislashblade")
    assert shield is not None and blade is not None
    assert blade["atk"] > shield["atk"] and blade["def"] < shield["def"]
    assert oracle.set_for("aegislashblade") == oracle.set_for("aegislash")


def _solved(trace_dir: Path, pattern: str) -> list[dict]:
    payloads = []
    for path in sorted(trace_dir.glob(pattern)):
        for line in path.open(encoding="utf-8"):
            event = json.loads(line)
            payload = event.get("payload", {})
            if event["type"] == "candidates" and payload.get("phase") == "pruned":
                payloads.append(payload)
    return payloads


async def test_the_oracle_arm_plays_against_the_true_sets(
    showdown_server: int, tmp_path: Path
) -> None:
    results = await run_matchup(
        build_arm("oneply-oracle", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        2,
        tmp_path,
        seed=17,
    )
    assert sum(r.games for r in results) == 4

    solved = _solved(tmp_path, "*.oneplyoracle17.jsonl")
    assert solved
    for payload in solved:
        assert payload["opponent_model"] == "oracle-sets"
        assert payload["model"] == "analytic-one-turn"
    # With the true moves as columns, turn one is not an argmax against nothing.
    assert any(len(payload["opponent_joint"]) > 1 for payload in solved)


async def test_the_deep_oracle_arm_carries_both_seams(showdown_server: int, tmp_path: Path) -> None:
    await run_matchup(
        build_arm("twoply-oracle", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        1,
        tmp_path,
        seed=19,
    )
    solved = _solved(tmp_path, "*.twoplyoracle19.jsonl")
    assert solved
    for payload in solved:
        assert payload["opponent_model"] == "oracle-sets"
        assert payload["model"] == "analytic-two-ply"
        assert payload["k2"] > 0


async def test_the_simulator_arm_scores_its_cells_in_the_simulator(
    showdown_server: int, tmp_path: Path
) -> None:
    results = await run_matchup(
        build_arm("sim-oracle", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        2,
        tmp_path,
        seed=23,
    )
    assert sum(r.games for r in results) == 4

    solved = _solved(tmp_path, "*.simoracle23.jsonl")
    assert solved
    materialized = 0
    for payload in solved:
        assert payload["model"] == "simulator-one-turn"
        assert payload["opponent_model"] == "oracle-sets"
        assert payload["replicates"] >= 1
        assert payload["cells"] == len(payload["joint"]) * len(payload["opponent_joint"])
        assert 0 <= payload["fallback_cells"] <= payload["cells"]
        assert "rollout_s" in payload["timings"]
        materialized += int(payload["materialized"])
    assert materialized > 0, "the simulator never scored a decision"
    assert sum(p["fallback_cells"] for p in solved) < sum(p["cells"] for p in solved), (
        "every cell fell back to the analytic model"
    )

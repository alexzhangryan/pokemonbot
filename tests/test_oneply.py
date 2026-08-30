"""M2: the one ply agent, played against a real simulator.

The unit tests cover the pieces. This covers the thing the pieces are for: that
the agent completes games, that the equilibrium it solves reaches the trace, and
that it is measurably better than the baseline it replaces.

The strength check is deliberately a *mirror* match. The two checked-in teams
are not balanced against each other, so a matchup across them measures the teams
(DECISIONS.md D30) -- the first run of this comparison had the one ply agent
losing 1-9 to max-base-power, and max-base-power losing 0-10 to itself across
the same team pairing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from champions.harness.ladder import run_matchup
from champions.search.policy import HeuristicPolicy
from champions.teams import ALPHA
from scripts.run_ladder import build_arm

N_GAMES = 10


async def test_the_agent_completes_games(showdown_server: int, tmp_path: Path) -> None:
    results = await run_matchup(
        build_arm("oneply", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        N_GAMES,
        tmp_path,
        seed=7,
    )

    assert sum(r.games for r in results) == 2 * N_GAMES
    assert sum(r.wins for r in results) == N_GAMES


async def test_the_agent_beats_max_base_power_on_the_same_team(
    showdown_server: int, tmp_path: Path
) -> None:
    """The M2 acceptance claim, held to a deliberately loose bar.

    The measured rate over 50 games is 82% on this team, so requiring a bare
    majority over 10 leaves a lot of room -- which is the point. This test is
    here to catch the agent regressing to baseline strength, not to re-measure
    it; the number with its confidence interval belongs in a ladder run.
    """
    results = await run_matchup(
        build_arm("oneply", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        N_GAMES,
        tmp_path,
        seed=11,
    )
    oneply = next(r for r in results if r.name == "one-ply")
    assert oneply.wins > N_GAMES / 2, f"one-ply won only {oneply.wins}/{N_GAMES}"


async def test_the_solved_game_reaches_the_trace(showdown_server: int, tmp_path: Path) -> None:
    """A stochastic agent sampling a mixed strategy cannot be debugged from its
    output, so everything the decision rested on has to be on the trace
    (`CLAUDE.md` constraint 6)."""
    await run_matchup(
        build_arm("oneply", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        2,
        tmp_path,
        seed=3,
    )

    traces = sorted(tmp_path.glob("*.oneply3.jsonl"))
    assert traces, "no one-ply traces written"

    solved = 0
    for path in traces:
        for line in path.open(encoding="utf-8"):
            event = json.loads(line)
            if event["type"] != "candidates" or event["payload"].get("phase") != "pruned":
                continue
            payload = event["payload"]
            solved += 1

            probabilities = [c["equilibrium_probability"] for c in payload["joint"]]
            assert all(0.0 <= x <= 1.0 for x in probabilities), probabilities
            assert sum(probabilities) == pytest.approx(1.0, abs=1e-6)
            assert 0.0 <= payload["game_value"] <= 1.0
            assert len(payload["payoff"]) == len(payload["joint"])
            assert len(payload["payoff"][0]) == len(payload["opponent_joint"])
            assert payload["k"] >= len(payload["joint"])
            assert 0 <= payload["chosen_index"] < len(payload["joint"])
            # Naming the model on every decision, so a reader never mistakes an
            # analytic estimate for a simulator-backed one.
            assert payload["model"] == "analytic-one-turn"
            assert payload["opponent_model"] == "revealed-moves-only"
            assert set(payload["timings"]) == {"candidates_s", "payoff_s", "solve_s"}

    assert solved > 0, "no solved decisions on the trace"


async def test_pruning_actually_prunes(showdown_server: int, tmp_path: Path) -> None:
    """About 100 to 156 joint actions become at most k. If this stops being
    true the budget arithmetic in `docs/02` section 7 stops applying."""
    await run_matchup(
        build_arm("oneply", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        2,
        tmp_path,
        seed=5,
    )

    pruned_from_many = 0
    for path in sorted(tmp_path.glob("*.oneply5.jsonl")):
        for line in path.open(encoding="utf-8"):
            event = json.loads(line)
            payload = event.get("payload", {})
            if event["type"] != "candidates" or payload.get("phase") != "pruned":
                continue
            assert len(payload["joint"]) <= payload["k"]
            if payload["n_legal_joint_actions"] > payload["k"]:
                pruned_from_many += 1

    assert pruned_from_many > 0, "never saw a turn with more legal actions than k"


async def test_the_pruned_candidates_carry_reasons_only_the_position_can_give(
    showdown_server: int, tmp_path: Path
) -> None:
    """The policy is state aware or it is not, and the trace is where that shows.

    `docs/04-decision-engine.md` section 3 specifies a heuristic that reads the
    board -- knockouts on an average roll, threatened slots, flipped speed races
    -- and the one that shipped through M6 read nothing but base power. Every
    reason below is one `BasePowerPolicy` cannot produce, so this fails if the
    agent ever goes back to pruning without the snapshot.

    Content dependent, and deliberately over-provisioned because of it: the
    reasons only appear if the games contain the positions that produce them.
    Four games is about forty decisions, and a knockout candidate alone shows up
    in almost every one -- at two games this failed intermittently, since the
    simulator's RNG is not seeded by us and a short game can miss all four.
    """
    await run_matchup(
        build_arm("oneply", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        4,
        tmp_path,
        seed=21,
    )

    reasons = set()
    for path in sorted(tmp_path.glob("*.oneply21.jsonl")):
        for line in path.open(encoding="utf-8"):
            event = json.loads(line)
            payload = event.get("payload", {})
            if event["type"] != "candidates" or payload.get("phase") != "pruned":
                continue
            for candidate in payload["joint"]:
                reasons.update(candidate["policy_reasons"])

    assert reasons & {"knockout", "protect idle", "speed control idle", "fake out unavailable"}, (
        f"no reason that needs the position: {sorted(reasons)}"
    )


async def test_the_trace_names_which_policy_produced_the_candidate_set(
    showdown_server: int, tmp_path: Path
) -> None:
    """There is more than one implementation A now and M7 adds B and C. A trace
    that says only "heuristic" cannot be read back against the benchmark, so the
    provider names itself and the agent emits what it was given rather than a
    literal."""
    await run_matchup(
        build_arm("oneply", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        1,
        tmp_path,
        seed=22,
    )

    providers: set[str] = set()
    for path in sorted(tmp_path.glob("*.oneply22.jsonl")):
        for line in path.open(encoding="utf-8"):
            event = json.loads(line)
            payload = event.get("payload", {})
            if event["type"] == "candidates" and payload.get("phase") == "pruned":
                providers.update(c["policy_provider"] for c in payload["joint"])

    assert providers == {HeuristicPolicy.name}


async def test_the_agent_never_aims_a_damaging_move_at_its_own_ally(
    showdown_server: int, tmp_path: Path
) -> None:
    """The pruning regression, checked end to end.

    Scoring base power without reading the target made ally-aimed copies of each
    move tie with foe-aimed ones, and since every move appears once per legal
    target, nine of the ten survivors were friendly fire.
    """
    await run_matchup(
        build_arm("oneply", showdown_server, ALPHA),
        build_arm("greedy", showdown_server, ALPHA),
        4,
        tmp_path,
        seed=13,
    )

    decisions = 0
    for path in sorted(tmp_path.glob("*.oneply13.jsonl")):
        for line in path.open(encoding="utf-8"):
            event = json.loads(line)
            if event["type"] != "equilibrium":
                continue
            decisions += 1
            assert not re.search(r"move \w+ -[12]", event["payload"]["chosen"]), (
                f"targeted own side: {event['payload']['chosen']}"
            )
    assert decisions > 0

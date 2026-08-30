"""The emission contract behind the viewer: what the agent records about what it
saw and what it decided. See docs/07-observability.md section 2.

These run a real battle rather than a constructed state, because every bug this
file exists to catch was a bug about the real thing: an enum that stringifies
into display junk, an opponent field poke-env fills in with a sentinel, a slot
that is empty because a Pokemon fainted. None of those appear in a hand-built
fixture.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from champions.protocol import actions
from scripts.selfplay import run_selfplay


@pytest.fixture(scope="module")
def trace(showdown_server: int, tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    """One battle's worth of events, from one agent's point of view."""
    trace_dir = tmp_path_factory.mktemp("obs")
    asyncio.run(run_selfplay(1, showdown_server, trace_dir, seed=3, username_suffix="obs"))

    path = sorted(trace_dir.glob("*.jsonl"))[0]
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def of_type(trace: list[dict], type_: str) -> list[dict]:
    return [e["payload"] for e in trace if e["type"] == type_]


# -- observable state --------------------------------------------------------


def test_every_turn_records_the_full_observable_state(trace: list[dict]) -> None:
    turns = of_type(trace, "turn_start")
    assert turns, "no turns recorded"

    for payload in turns:
        state = payload["state"]
        assert state["player_role"] in {"p1", "p2"}
        # Doubles: two slots a side, and a slot may be empty after a faint.
        assert len(state["ours"]["active"]) == 2
        assert len(state["theirs"]["active"]) == 2
        assert set(state) >= {"weather", "fields", "side_conditions", "constraints"}


def test_our_side_is_fully_known_and_the_opponents_is_not(trace: list[dict]) -> None:
    """The knowledge asymmetry is the whole point of the belief filter (M3).

    If our own side ever reported percent HP, or the opponent's ever reported
    exact HP or a stat spread, the trace would be describing a game we are not
    playing.
    """
    for payload in of_type(trace, "turn_start"):
        state = payload["state"]

        for mon in [m for m in state["ours"]["active"] if m] + state["ours"]["bench"]:
            assert mon["known"] is True
            assert mon["max_hp"] is not None
            assert mon["stats"], "our own stats are known exactly"
            assert "moves" in mon

        for mon in [m for m in state["theirs"]["active"] if m] + state["theirs"]["bench"]:
            assert mon["known"] is False
            assert mon["hp"] is None and mon["max_hp"] is None
            assert mon["hp_is_percent"] is True
            assert mon["stats"] is None
            # Moves are revealed by use, never enumerated up front.
            assert "revealed_moves" in mon


def test_the_turn_a_pokemon_came_in_is_recorded(trace: list[dict]) -> None:
    """Fake Out works only on the turn its user switched in, and the candidate
    policy has to rank it on exactly that. Deriving it from the turn number is
    right on turn 1 and wrong after every switch, so it comes off poke-env
    rather than being inferred (`champions.search.policy`)."""
    turns = of_type(trace, "turn_start")
    for payload in turns:
        for mon in [m for m in payload["state"]["ours"]["active"] if m]:
            assert isinstance(mon["first_turn"], bool)

    assert any(
        mon["first_turn"] for payload in turns for mon in payload["state"]["ours"]["active"] if mon
    ), "no Pokemon was ever recorded as having just come in"


def test_state_carries_names_rather_than_stringified_python_objects(trace: list[dict]) -> None:
    """poke-env's enums stringify as "FLYING (pokemon type) object" and
    "Status.PAR". Baking either into the schema pushes a parsing problem onto
    every consumer, so the snapshot records `.name`."""
    for payload in of_type(trace, "turn_start"):
        state = payload["state"]
        for mon in [m for m in state["ours"]["active"] + state["theirs"]["active"] if m]:
            for type_name in mon["types"]:
                assert type_name.isalpha() and type_name.isupper(), type_name
            if mon["status"] is not None:
                assert "." not in mon["status"] and " " not in mon["status"]


def test_an_unrevealed_opponent_item_is_null_not_a_sentinel_string(trace: list[dict]) -> None:
    """poke-env reports it as the literal "unknown_item", which would read as a
    real item by that name to anything downstream."""
    for payload in of_type(trace, "turn_start"):
        for mon in payload["state"]["theirs"]["bench"]:
            assert mon["item"] != "unknown_item"


def test_move_numbers_come_from_the_champions_dex_not_from_poke_env(trace: list[dict]) -> None:
    """poke-env ships mainline Gen 9, and 303 moves differ in this format
    (CLAUDE.md constraint 1). Every move in the trace says which it came from."""
    seen = set()
    for payload in of_type(trace, "turn_start"):
        for mon in [m for m in payload["state"]["ours"]["active"] if m]:
            for move in mon["moves"]:
                seen.add(move["source"])

    assert seen == {"champions_dex"}, f"mainline move data leaked into the trace: {seen}"


# -- the decision ------------------------------------------------------------


def test_the_legal_action_set_is_recorded_with_the_decision(trace: list[dict]) -> None:
    candidates = of_type(trace, "candidates")
    turns = of_type(trace, "turn_start")
    assert len(candidates) == len(turns), "every decision records what it chose between"

    for payload in candidates:
        assert payload["n_legal_joint_actions"] > 0
        assert payload["joint"], "no joint actions described"
        # Doubles: the per-slot decomposition, which is what a person reads.
        assert len(payload["slot_options"]) == 2
        for action in payload["joint"]:
            assert action["message"].startswith("/choose")
            assert action["label"]


def test_the_chosen_action_is_one_of_the_recorded_candidates(trace: list[dict]) -> None:
    """Otherwise the candidate list is decoration rather than a record of the
    decision, and no review of it could be trusted."""
    events = [e for e in trace if e["type"] in {"candidates", "equilibrium"}]

    pairs = 0
    for candidates, equilibrium in zip(events[::2], events[1::2], strict=False):
        assert candidates["type"] == "candidates" and equilibrium["type"] == "equilibrium"
        messages = {a["message"] for a in candidates["payload"]["joint"]}
        if candidates["payload"]["truncated"]:
            continue
        assert equilibrium["payload"]["chosen"] in messages
        pairs += 1

    assert pairs > 0


def test_unbuilt_annotations_are_named_rather_than_faked(trace: list[dict]) -> None:
    """A zero would read as a measurement. The events say which fields are not
    computed yet so the viewer can render them as pending."""
    for payload in of_type(trace, "candidates"):
        assert set(payload["annotations_pending"]) == set(actions.PENDING_ANNOTATIONS)
        for action in payload["joint"]:
            assert "damage_rolls" not in action
            assert "ko_probability" not in action

    for payload in of_type(trace, "equilibrium"):
        assert "mixed_strategy" in payload["pending"]


def test_the_chosen_action_is_described_and_not_only_encoded(trace: list[dict]) -> None:
    for payload in of_type(trace, "equilibrium"):
        described = payload["chosen_action"]
        assert described["message"] == payload["chosen"]
        assert described["label"]
        for slot in described["slots"]:
            assert slot["kind"] in {"move", "switch", "pass", "default", "raw"}


# -- the protocol log --------------------------------------------------------


def test_the_protocol_is_recorded_so_the_trace_says_what_actually_happened(
    trace: list[dict],
) -> None:
    """poke-env keeps no log of its own: it folds each message into its battle
    state and discards it. Without this the trace records decisions with no
    account of their consequences."""
    logs = [payload["log"] for payload in of_type(trace, "turn_start")]
    assert any(logs), "no protocol captured on any turn"

    lines = [line for log in logs for line in log]
    assert any(line.startswith("|switch|") or line.startswith("|move|") for line in lines)
    # Request payloads are the agent's own input, not an account of the battle,
    # and they are large.
    assert not any(line.startswith("|request|") for line in lines)


def test_the_battle_end_event_carries_the_final_state(trace: list[dict]) -> None:
    end = of_type(trace, "battle_end")
    assert len(end) == 1
    assert end[0]["result"] in {"win", "loss", "tie"}
    assert end[0]["state"]["ours"]["remaining"] >= 0


def test_the_agent_and_dex_that_produced_the_trace_are_identified(trace: list[dict]) -> None:
    """A trace is only reproducible if it says which agent and which resolved
    dex produced it; the dex dump is content-hashed for exactly this."""
    start = of_type(trace, "battle_start")[0]
    assert start["agent"] == "RandomAgent"
    assert start["strategy"] == "uniform_random"
    assert start["dex_hash"], "no dex hash recorded"


def test_a_written_trace_is_a_path_the_viewer_can_read(
    showdown_server: int, tmp_path: Path
) -> None:
    """The viewer parses traces as plain JSON lines. This is the seam between
    the two halves, so it is checked rather than assumed."""
    from champions.viewer.server import create_app, read_events

    asyncio.run(run_selfplay(1, showdown_server, tmp_path, seed=11, username_suffix="seam"))
    written = sorted(tmp_path.glob("*.jsonl"))
    assert written

    client = TestClient(create_app(tmp_path))
    listed = client.get("/api/traces").json()["traces"]
    assert {t["id"] for t in listed} == {p.stem for p in written}

    for path in written:
        assert read_events(path), f"{path.name} read back empty"


def test_turn_result_is_a_parsed_digest_of_the_protocol(trace: list[dict]) -> None:
    """`docs/07-observability.md` section 2's open question, now answered.

    `turn_result` was specified and never emitted, and the entry in STATUS asked
    M3 to decide whether it should be a parsed digest of the log or dropped in
    favour of it. It is a digest, produced by the same parser that builds the
    replay corpus (D32), so what the agent observes live and what the corpus
    records offline cannot drift apart.
    """
    results = of_type(trace, "turn_result")
    assert results, "no turn_result events emitted"
    observations = [o for event in results for o in event["observations"]]
    assert observations
    for observation in observations:
        assert observation["side"] in ("p1", "p2", "")
        assert set(observation) >= {"seq", "turn", "side", "attribute", "value"}
    assert any(o["attribute"] == "move" for o in observations)
    assert any(o["attribute"] == "switch" for o in observations)


def test_observation_sequence_is_monotonic_across_the_battle(trace: list[dict]) -> None:
    """Order is Speed evidence, and it has to survive the trace (D32)."""
    seqs = [o["seq"] for event in of_type(trace, "turn_result") for o in event["observations"]]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_the_live_protocol_contains_nothing_the_parser_cannot_read(trace: list[dict]) -> None:
    """A second, independent check of the parser, on a different log source.

    `tests/test_parser.py` runs it over scraped replays from the public API;
    this runs it over a battle produced by the vendored simulator through
    poke-env. The two paths differ in what they filter and how they buffer, so
    agreeing on zero unhandled message types is worth more than either alone.
    """
    end = of_type(trace, "battle_end")
    assert end
    assert end[-1]["unhandled_messages"] == {}

import time
from pathlib import Path

from champions.trace.schema import EventType
from champions.trace.writer import Trace, read_events


async def test_synthetic_battle_round_trips(tmp_path: Path) -> None:
    trace = Trace("battle-1", trace_dir=tmp_path)

    trace.emit(
        EventType.BATTLE_START,
        {"format_id": "gen9championsvgc2026regmb", "our_team": ["Incineroar"], "side": "p1"},
    )
    trace.emit(
        EventType.TURN_START,
        {"turn": 1, "clock_p1": 420, "clock_p2": 420},
    )
    trace.emit(
        EventType.TIMING,
        {
            "phase_ms": {"belief_update": 1.2, "candidate_generation": 3.4},
            "total_ms": 4.6,
            "exceeded_45s": False,
            "watchdog_fired": False,
        },
    )
    trace.emit(EventType.BATTLE_END, {"result": "win", "turns": 1})
    await trace.close()

    events = read_events(trace.path)

    assert [e.type for e in events] == [
        "battle_start",
        "turn_start",
        "timing",
        "battle_end",
    ]
    assert [e.seq for e in events] == [0, 1, 2, 3]
    for event in events:
        assert event.schema_version == 1
        assert event.battle_id == "battle-1"
    assert events[0].payload["format_id"] == "gen9championsvgc2026regmb"
    assert events[2].payload["exceeded_45s"] is False
    assert events[3].payload == {"result": "win", "turns": 1}


async def test_multiple_battles_get_separate_files(tmp_path: Path) -> None:
    trace_a = Trace("battle-a", trace_dir=tmp_path)
    trace_b = Trace("battle-b", trace_dir=tmp_path)

    trace_a.emit(EventType.BATTLE_START, {"side": "p1"})
    trace_b.emit(EventType.BATTLE_START, {"side": "p2"})
    await trace_a.close()
    await trace_b.close()

    assert read_events(trace_a.path)[0].payload["side"] == "p1"
    assert read_events(trace_b.path)[0].payload["side"] == "p2"


async def test_unknown_fields_and_event_types_do_not_break_readers(tmp_path: Path) -> None:
    path = tmp_path / "old-agent-version.jsonl"
    path.write_text(
        '{"schema_version": 1, "battle_id": "b", "seq": 0, "t": 1.0, '
        '"type": "some_future_event_type", "payload": {}, '
        '"agent_version": "unreleased-field-from-the-future"}\n'
    )

    events = read_events(path)

    assert len(events) == 1
    assert events[0].type == "some_future_event_type"


async def test_emit_adds_under_one_millisecond(tmp_path: Path) -> None:
    trace = Trace("battle-timing", trace_dir=tmp_path)

    iterations = 1000
    start = time.perf_counter()
    for i in range(iterations):
        trace.emit(EventType.TURN_START, {"turn": i})
    elapsed = time.perf_counter() - start

    await trace.close()

    mean_ms = (elapsed / iterations) * 1000
    assert mean_ms < 1.0, f"emit() averaged {mean_ms:.4f} ms, expected under 1 ms"

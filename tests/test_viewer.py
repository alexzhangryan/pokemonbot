"""The viewer serves traces, follows a file as it grows, and refuses to leave
its directory. See docs/07-observability.md sections 3 and 5.

Nothing here starts a battle. The viewer's whole contract is with the file
format, so the tests write trace files directly, which is also the only
practical way to exercise the cases that matter: a half-written line, an event
type from a future milestone, a path that tries to escape the trace directory.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from champions.viewer.server import _read_from, create_app, read_events, tail_events


def write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def event(seq: int, type_: str = "turn_start", **payload: object) -> dict:
    return {
        "schema_version": 1,
        "battle_id": "battle-test-1",
        "seq": seq,
        "t": 1000.0 + seq,
        "type": type_,
        "payload": payload,
    }


@pytest.fixture
def trace_dir(tmp_path: Path) -> Path:
    write_events(
        tmp_path / "battle-test-1.champbot.jsonl",
        [
            event(0, "battle_start", format_id="gen9championsvgc2026regmb", agent="RandomAgent"),
            event(1, "turn_start", turn=1, state={"turn": 1}, log=["|move|p1a: X|Protect"]),
            event(2, "equilibrium", turn=1, chosen="/choose move protect, pass"),
        ],
    )
    return tmp_path


# -- serving -----------------------------------------------------------------


def test_lists_traces_with_a_summary_read_from_the_first_event(trace_dir: Path) -> None:
    client = TestClient(create_app(trace_dir))
    body = client.get("/api/traces").json()

    assert [t["id"] for t in body["traces"]] == ["battle-test-1.champbot"]
    summary = body["traces"][0]
    assert summary["battle_id"] == "battle-test-1"
    assert summary["format_id"] == "gen9championsvgc2026regmb"
    assert summary["agent"] == "RandomAgent"
    assert summary["size_bytes"] > 0


def test_reports_where_showdown_is_so_the_page_can_open_the_real_client(
    trace_dir: Path,
) -> None:
    """Smogon's client frame-busts (`self === top`), so the page cannot embed it
    and opens it as a separate window instead. The URL has to come from the
    server, which is the side that knows which port the simulator is on."""
    client = TestClient(create_app(trace_dir, showdown_port=9999, autostart_showdown=False))
    assert client.get("/api/traces").json()["showdown_url"].endswith(":9999")


def test_serves_the_events_of_one_trace(trace_dir: Path) -> None:
    client = TestClient(create_app(trace_dir))
    body = client.get("/api/trace/battle-test-1.champbot").json()

    assert [e["type"] for e in body["events"]] == ["battle_start", "turn_start", "equilibrium"]


def test_a_missing_trace_is_a_404_not_a_crash(trace_dir: Path) -> None:
    client = TestClient(create_app(trace_dir))
    assert client.get("/api/trace/nope").status_code == 404


@pytest.mark.parametrize(
    "attempt",
    [
        "../../secrets",
        "..%2f..%2fsecrets",
        "sub/../../outside",
    ],
)
def test_refuses_to_serve_anything_outside_the_trace_directory(
    trace_dir: Path, attempt: str
) -> None:
    """The trace id comes from the URL, so it is untrusted input becoming a path."""
    (trace_dir.parent / "secrets.jsonl").write_text("stolen\n", encoding="utf-8")
    (trace_dir.parent / "outside.jsonl").write_text("stolen\n", encoding="utf-8")

    client = TestClient(create_app(trace_dir))
    response = client.get(f"/api/trace/{attempt}")

    assert response.status_code == 404
    assert "stolen" not in response.text


def test_serves_the_battle_renderer_page(trace_dir: Path) -> None:
    """The Showdown scene is a same-origin frame, so it has to be served from
    here rather than loaded cross-origin (D19)."""
    client = TestClient(create_app(trace_dir))
    response = client.get("/static/battle.html")

    assert response.status_code == 200
    # It drives Showdown's Battle class over postMessage; both are load-bearing.
    assert "battle.js" in response.text
    assert "postMessage" in response.text


# -- forward compatibility ---------------------------------------------------


def test_renders_event_types_and_fields_it_has_never_heard_of(tmp_path: Path) -> None:
    """A trace from a later agent version must be served, not rejected.

    The review client has to open files produced by every milestone, so the
    server parses events as plain JSON rather than validating them against the
    schema it happens to know (docs/07-observability.md section 5).
    """
    path = tmp_path / "future.jsonl"
    write_events(
        path,
        [
            event(0, "battle_start"),
            {
                "schema_version": 99,
                "battle_id": "b",
                "seq": 1,
                "t": 1.0,
                "type": "belief_resample",  # a type that does not exist yet
                "payload": {"effective_particles": 812},
                "emitted_by": "M3",  # an envelope field that does not exist yet
            },
        ],
    )

    client = TestClient(create_app(tmp_path))
    body = client.get("/api/trace/future").json()

    assert [e["type"] for e in body["events"]] == ["battle_start", "belief_resample"]
    assert body["events"][1]["emitted_by"] == "M3"


def test_a_truncated_final_line_is_skipped_rather_than_failing_the_file(tmp_path: Path) -> None:
    path = tmp_path / "partial.jsonl"
    write_events(path, [event(0, "battle_start")])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"schema_version": 1, "battle_id": "b", "seq": 1, "ty')

    assert [e["type"] for e in read_events(path)] == ["battle_start"]


# -- tailing -----------------------------------------------------------------


def test_reads_only_complete_lines_and_reports_where_it_stopped(tmp_path: Path) -> None:
    """The writer appends while the viewer reads, so a partial tail is normal."""
    path = tmp_path / "growing.jsonl"
    write_events(path, [event(0), event(1)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "type": "half-writ')

    events, offset = _read_from(path, 0)
    assert [e["seq"] for e in events] == [0, 1]

    # The partial line is left for the next poll, and completing it yields it.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('ten", "payload": {}}\n')

    more, _ = _read_from(path, offset)
    assert [e["seq"] for e in more] == [2]


async def test_tail_replays_the_backlog_then_follows_new_events(tmp_path: Path) -> None:
    """Live and replay are the same code path: one is a file that keeps growing."""
    path = tmp_path / "live.jsonl"
    write_events(path, [event(0), event(1)])

    batches = []
    stream = tail_events(path, poll_interval_s=0.02)

    first = await anext(stream)
    batches.append(first)
    assert [e["seq"] for e in first["events"]] == [0, 1]
    assert first["live"] is False, "the backlog is history, not a live feed"

    write_events(path, [event(2)])

    async with asyncio.timeout(5):
        while True:
            batch = await anext(stream)
            if batch["events"]:
                break

    assert [e["seq"] for e in batch["events"]] == [2]
    assert batch["live"] is True
    # Freshness is what lets the client tell a battle in progress from one that
    # ended while the socket stayed open.
    assert batch["age_s"] is not None and batch["age_s"] < 60

    await stream.aclose()


def test_websocket_streams_the_trace(trace_dir: Path) -> None:
    client = TestClient(create_app(trace_dir))
    with client.websocket_connect("/ws/trace/battle-test-1.champbot") as ws:
        batch = ws.receive_json()

    assert batch["kind"] == "events"
    assert [e["type"] for e in batch["events"]] == ["battle_start", "turn_start", "equilibrium"]


def test_websocket_reports_a_missing_trace_instead_of_dropping_the_connection(
    trace_dir: Path,
) -> None:
    client = TestClient(create_app(trace_dir))
    with client.websocket_connect("/ws/trace/not-a-trace") as ws:
        message = ws.receive_json()

    assert message["kind"] == "error"

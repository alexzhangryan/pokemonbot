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
import shutil
import subprocess
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


# -- the review overlay (M9 writes it, M10 renders it) ------------------------


def test_a_review_file_is_listed_and_served_with_its_analysis_events(tmp_path: Path) -> None:
    """The coach writes `<stem>.review.jsonl` beside a trace: the same events
    plus `analysis` ones. The server must list it as a trace of the same battle
    and serve every event, since the client renders the overlay from them."""
    write_events(
        tmp_path / "battle-test-1.champbot.review.jsonl",
        [
            event(0, "battle_start", format_id="gen9championsvgc2026regmb", agent="OnePlyAgent"),
            event(1, "preview_decision", selected=["a", "b", "c", "d"]),
            event(2, "analysis", scope="preview", for_seq=1, turn=0, pending=[]),
            event(3, "turn_start", turn=1, state={"turn": 1}, log=[]),
            event(4, "equilibrium", turn=1, chosen="/choose move x, pass"),
            event(
                5,
                "analysis",
                scope="turn",
                for_seq=4,
                turn=1,
                classification="blunder",
                tags=["read"],
                ex_ante_loss=0.2,
            ),
            event(6, "analysis", scope="game", for_seq=7, turn=None, scored=1, decisions=1),
            event(7, "battle_end", result="loss", turns=1, state={"turn": 1}),
        ],
    )
    client = TestClient(create_app(tmp_path))
    listing = client.get("/api/traces").json()["traces"]
    assert [t["id"] for t in listing] == ["battle-test-1.champbot.review"]
    assert listing[0]["battle_id"] == "battle-test-1"

    served = client.get("/api/trace/battle-test-1.champbot.review").json()["events"]
    scopes = [e["payload"]["scope"] for e in served if e["type"] == "analysis"]
    assert scopes == ["preview", "turn", "game"]
    assert served[5]["payload"]["classification"] == "blunder"


# -- the client script, run without a browser --------------------------------


NODE = shutil.which("node")
VIEWER_SMOKE = Path(__file__).parent / "viewer_smoke.js"


def run_viewer_smoke(trace: Path) -> dict:
    completed = subprocess.run(
        [NODE or "node", str(VIEWER_SMOKE), str(trace)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=VIEWER_SMOKE.parent.parent,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_client_renders_a_reviewed_trace_and_a_plain_one(tmp_path: Path) -> None:
    """The one property the client must have: any trace renders without an
    exception. Exercised on a plain trace and on the same trace with the
    coach's overlay, by running the real script in a stub DOM."""
    plain = tmp_path / "battle-test-1.champbot.jsonl"
    write_events(
        plain,
        [
            event(0, "battle_start", format_id="gen9championsvgc2026regmb", agent="OnePlyAgent"),
            event(1, "preview_decision", selected=["a", "b"], pending=["payoff_matrix"]),
            event(
                2,
                "turn_start",
                turn=1,
                state={"turn": 1, "theirs": {"active": [], "bench": []}},
                log=[],
            ),
            event(
                3,
                "candidates",
                turn=1,
                joint=[{"message": "m", "label": "m", "slots": []}],
                pruned=False,
            ),
            event(4, "equilibrium", turn=1, chosen="m", chosen_action={"label": "m", "slots": []}),
            event(5, "timing", turn=1, total_ms=3.0, deadline_s=45.0, budget_s=32.5),
            event(6, "battle_end", result="loss", turns=1, state={"turn": 1}),
        ],
    )
    assert run_viewer_smoke(plain) == {"points": 2, "reviewed": 0, "game": False}

    reviewed = tmp_path / "battle-test-1.champbot.review.jsonl"
    write_events(
        reviewed,
        [
            event(0, "battle_start", format_id="gen9championsvgc2026regmb", agent="OnePlyAgent"),
            event(1, "preview_decision", selected=["a", "b"], pending=["payoff_matrix"]),
            event(
                2,
                "analysis",
                scope="preview",
                for_seq=1,
                turn=0,
                bring=["a"],
                leads=["a"],
                pending=["x"],
                reason="none",
            ),
            event(
                3,
                "turn_start",
                turn=1,
                state={"turn": 1, "theirs": {"active": [], "bench": []}},
                log=[],
            ),
            event(4, "equilibrium", turn=1, chosen="m", chosen_action={"label": "m", "slots": []}),
            event(
                5,
                "analysis",
                scope="turn",
                for_seq=4,
                turn=1,
                played_label="m",
                classification="mistake",
                tags=["read", "unlucky"],
                ex_ante_loss=0.08,
                ex_post_loss=0.2,
                game_value=0.5,
                is_pure=False,
                luck=0.12,
                expected_value=0.5,
                realized_value=0.38,
                equilibrium=[{"label": "m", "probability": 0.6, "ante_value": 0.5}],
                opponent_equilibrium=[{"label": "x", "probability": 1.0}],
                best="m",
                best_ex_post="n",
                opponent_played="x",
                rolls=[
                    {"probability": 0.5, "value": 0.4, "faints": []},
                    {"probability": 0.5, "value": 0.6, "faints": ["a"]},
                ],
                explanation="Turn 1: played m.",
                recorded={"game_value": 0.55, "k": 10},
                information={"ante": "revealed-moves-only", "post": "team-file"},
                calibrated=True,
                n_rows=2,
                n_columns=1,
                model="analytic-one-turn",
            ),
            event(
                6,
                "analysis",
                scope="game",
                for_seq=7,
                turn=None,
                scored=1,
                decisions=1,
                ex_ante_loss_total=0.08,
                ex_ante_loss_mean=0.08,
                ex_post_loss_total=0.2,
                classifications={"mistake": 1},
                tags={"read": 1},
                critical_by_loss=[{"turn": 1, "ex_ante_loss": 0.08}],
                critical_by_drop=[{"turn": 1, "drop": 0.12}],
                curve=[{"turn": 1, "win_prob": 0.5}, {"turn": None, "win_prob": 0.38}],
                calibrated=True,
                information={"ante": "revealed-moves-only", "post": "team-file"},
                bands={"source": "hand-set"},
            ),
            event(7, "battle_end", result="loss", turns=1, state={"turn": 1}),
        ],
    )
    assert run_viewer_smoke(reviewed) == {"points": 2, "reviewed": 2, "game": True}


def test_status_carries_the_ladder_phase_file_when_one_is_written(tmp_path: Path) -> None:
    from champions.viewer.server import create_app

    with TestClient(create_app(tmp_path, autostart_showdown=False)) as client:
        assert client.get("/api/status").json()["live"] is None
        (tmp_path / "status.json").write_text(
            json.dumps({"phase": "searching", "game": 3, "of": 10, "t": 1.0}), encoding="utf-8"
        )
        live = client.get("/api/status").json()["live"]
        assert (live["phase"], live["game"], live["of"]) == ("searching", 3, 10)
        (tmp_path / "status.json").write_text("{half", encoding="utf-8")
        assert client.get("/api/status").json()["live"] is None


def test_the_viewer_can_ask_a_ladder_run_to_stop_after_its_game(tmp_path: Path) -> None:
    from champions.viewer.server import create_app

    with TestClient(create_app(tmp_path, autostart_showdown=False)) as client:
        (tmp_path / "status.json").write_text(
            json.dumps({"phase": "battle", "game": 2, "of": 10, "t": 1.0}), encoding="utf-8"
        )
        assert client.get("/api/status").json()["live"]["stop_requested"] is False
        assert client.post("/api/live/stop").json() == {"stop_requested": True}
        assert (tmp_path / "stop").is_file()
        assert client.get("/api/status").json()["live"]["stop_requested"] is True
        assert client.post("/api/live/resume").json() == {"stop_requested": False}
        assert not (tmp_path / "stop").exists()


def test_the_ladder_rating_and_rank_come_from_the_site_cached(tmp_path: Path) -> None:
    from champions.viewer.ladder import LadderLookup
    from champions.viewer.server import create_app

    calls: list[str] = []

    async def fake_fetch(url: str) -> object:
        calls.append(url)
        if url.endswith("/users/champbot.json"):
            return {
                "username": "ChampBot",
                "ratings": {
                    "gen9championsvgc2026regmc": {
                        "elo": 1215.6,
                        "gxe": 58.2,
                        "rpr": 1502.1,
                        "w": 12,
                        "l": 7,
                    }
                },
            }
        if url.endswith("/ladder/gen9championsvgc2026regmc.json"):
            return {"toplist": [{"userid": "someoneelse"}, {"userid": "champbot"}]}
        raise AssertionError(url)

    lookup = LadderLookup(fetch=fake_fetch)
    with TestClient(create_app(tmp_path, autostart_showdown=False, ladder=lookup)) as client:
        data = client.get(
            "/api/ladder", params={"user": "Champ Bot", "format": "gen9championsvgc2026regmc"}
        ).json()
        assert (data["username"], data["elo"], data["gxe"], data["w"], data["l"]) == (
            "ChampBot",
            1215.6,
            58.2,
            12,
            7,
        )
        assert (data["rank"], data["top"]) == (2, 2)
        # A second ask inside the minute is served from the cache.
        client.get(
            "/api/ladder", params={"user": "champbot", "format": "gen9championsvgc2026regmc"}
        )
        assert len(calls) == 2
        assert client.get("/api/ladder", params={"user": "", "format": "x"}).status_code == 400


def test_an_unrated_user_and_an_unreachable_site_are_reported_not_raised(tmp_path: Path) -> None:
    import asyncio

    from champions.viewer.ladder import LadderLookup

    async def unrated(url: str) -> object:
        return {"username": "newbot", "ratings": {}}

    data = asyncio.run(LadderLookup(fetch=unrated).lookup("newbot", "gen9championsvgc2026regmc"))
    assert data["rated"] is False and "rank" not in data

    async def down(url: str) -> object:
        raise OSError("no route to host")

    data = asyncio.run(LadderLookup(fetch=down).lookup("newbot", "gen9championsvgc2026regmc"))
    assert "error" in data and "rated" not in data

    async def rated_but_no_ladder(url: str) -> object:
        if "/users/" in url:
            return {"username": "bot", "ratings": {"f": {"elo": 1000.0}}}
        raise OSError("ladder timed out")

    data = asyncio.run(LadderLookup(fetch=rated_but_no_ladder).lookup("bot", "f"))
    assert data["elo"] == 1000.0 and "rank" not in data and "rank_error" in data


def test_the_listing_says_how_a_game_ended_and_names_its_review(tmp_path: Path) -> None:
    write_events(
        tmp_path / "battle-test-2.champbot.jsonl",
        [
            event(0, "battle_start", player_username="champbot", opponent_username="rival"),
            event(1, "turn_start", turn=1, state={"turn": 1}, log=[]),
            event(2, "battle_end", result="win", turns=7, state={"turn": 7}),
        ],
    )
    write_events(
        tmp_path / "battle-test-2.champbot.review.jsonl",
        [
            event(0, "battle_start", player_username="champbot", opponent_username="rival"),
            event(1, "analysis", scope="game", for_seq=2, turn=None, scored=1, decisions=1),
            event(2, "battle_end", result="win", turns=7, state={"turn": 7}),
        ],
    )
    (tmp_path / "battle-test-3.champbot.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "battle_id": "battle-test-3",
                "seq": 0,
                "t": 1.0,
                "type": "battle_start",
                "payload": {"player_username": "champbot", "opponent_username": "x"},
            }
        )
        + "\n"
        + '{"schema_version": 1, "battle_id": "battle-test-3", "seq": 1, "type": "turn_st',
        encoding="utf-8",
    )
    with TestClient(create_app(tmp_path, autostart_showdown=False)) as client:
        by_id = {t["id"]: t for t in client.get("/api/traces").json()["traces"]}
    game = by_id["battle-test-2.champbot"]
    assert (game["player"], game["opponent"], game["result"], game["turns"]) == (
        "champbot",
        "rival",
        "win",
        7,
    )
    assert game["review"] == "battle-test-2.champbot.review" and game["is_review"] is False
    assert by_id["battle-test-2.champbot.review"]["is_review"] is True
    # A game still being written has no result yet, and a torn last line is not an error.
    unfinished = by_id["battle-test-3.champbot"]
    assert unfinished["result"] is None and unfinished["review"] is None


def test_status_names_the_bot_account_from_dotenv_without_the_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(tmp_path / "traces", autostart_showdown=False)) as client:
        assert client.get("/api/status").json()["account"] is None
        (tmp_path / ".env").write_text(
            'PS_USERNAME="champbot"\nPS_PASSWORD=hunter2\n', encoding="utf-8"
        )
        status = client.get("/api/status").json()
        assert status["account"]["username"] == "champbot"
        assert "hunter2" not in json.dumps(status)


def test_the_viewer_can_start_a_ladder_run_when_none_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D87: the start route launches the ladder script detached in the
    viewer's own directory, refuses while a run is active, and blank means
    until stopped."""
    import json

    from champions.viewer import server as viewer_server

    launched: list[tuple] = []

    def fake_launch(root: Path, games: int, agent: str, team: str) -> int:
        launched.append((root, games, agent, team))
        return 4242

    monkeypatch.setattr(viewer_server, "_account", lambda: {"username": "bot", "format": "f"})
    app = viewer_server.create_app(
        trace_dir=tmp_path, autostart_showdown=False, launch_ladder=fake_launch
    )
    with TestClient(app) as client:
        started = client.post("/api/live/start", json={"games": 3}).json()
        assert started["started"] and started["pid"] == 4242 and started["games"] == 3
        assert launched[-1] == (
            tmp_path.resolve(),
            3,
            viewer_server.DEFAULT_LADDER_AGENT,
            viewer_server.DEFAULT_TEAM,
        )

        client.post("/api/live/start", json={})
        assert launched[-1][1] == viewer_server.UNTIL_STOPPED, "blank plays until stopped"

        (tmp_path / "status.json").write_text(
            json.dumps({"phase": "battle", "game": 1, "of": 3}), encoding="utf-8"
        )
        assert client.post("/api/live/start", json={"games": 1}).status_code == 409
        (tmp_path / "status.json").write_text(
            json.dumps({"phase": "done", "game": 3, "of": 3}), encoding="utf-8"
        )
        assert client.post("/api/live/start", json={"games": 1}).status_code == 200

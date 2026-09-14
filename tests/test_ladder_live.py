"""The live ladder script's credential handling, without a network.

The one script that talks to the official server. What is tested is the part
that can go wrong silently: reading the account out of `.env` without ever
letting the password near a log, and refusing to start without one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from scripts.ladder_live import (
    LEDGER_NAME,
    PASSWORD_KEY,
    USERNAME_KEY,
    Report,
    StatusFile,
    credentials,
    headline,
    play,
    read_env,
    read_ledger,
    replay_url,
    review_game,
    save_replay_on_start,
    summarize,
)


def test_the_dotenv_file_is_read_with_quotes_comments_and_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(USERNAME_KEY, raising=False)
    monkeypatch.delenv(PASSWORD_KEY, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# the bot account\n"
        f'{USERNAME_KEY}="champbot"\n'
        f"export {PASSWORD_KEY}='hunter two'\n"
        "IGNORED\n",
        encoding="utf-8",
    )
    assert read_env(env) == {USERNAME_KEY: "champbot", PASSWORD_KEY: "hunter two"}
    assert credentials(read_env(env)) == ("champbot", "hunter two")


def test_the_environment_overrides_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(f"{USERNAME_KEY}=file\n{PASSWORD_KEY}=filepass\n", encoding="utf-8")
    monkeypatch.setenv(USERNAME_KEY, "shell")
    monkeypatch.setenv(PASSWORD_KEY, "shellpass")
    assert credentials(read_env(env)) == ("shell", "shellpass")


def test_no_account_refuses_to_start_and_says_where_to_put_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(USERNAME_KEY, raising=False)
    monkeypatch.delenv(PASSWORD_KEY, raising=False)
    with pytest.raises(SystemExit) as caught:
        credentials(read_env(tmp_path / "missing.env"))
    assert ".env.example" in str(caught.value)


def test_the_report_counts_wins_and_keeps_the_rating_series(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = Report(2)
    report(
        SimpleNamespace(
            won=True,
            rating=1012,
            opponent_rating=1000,
            opponent_username="a",
            battle_tag="b1",
            turn=4,
        )
    )
    report(
        SimpleNamespace(
            won=False,
            rating=1003,
            opponent_rating=None,
            opponent_username="b",
            battle_tag="b2",
            turn=5,
        )
    )
    assert (report.wins, report.finished, report.ratings) == (1, 2, [1012, 1003])
    out = capsys.readouterr().out
    assert "battle 1/2: win vs a (1000) -> our rating 1012" in out
    assert "battle 2/2: loss vs b -> our rating 1003" in out


def _battle(**overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "won": True,
        "rating": 1040,
        "opponent_rating": 1101,
        "opponent_username": "someone",
        "battle_tag": "battle-gen9championsvgc2026regmb-2345678901",
        "turn": 9,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_the_replay_address_is_the_room_without_its_prefix() -> None:
    assert (
        replay_url("battle-gen9championsvgc2026regmb-2345678901")
        == "https://replay.pokemonshowdown.com/gen9championsvgc2026regmb-2345678901"
    )


def test_every_finished_game_is_a_row_in_the_ledger_across_runs(tmp_path: Path) -> None:
    ledger = tmp_path / "live" / LEDGER_NAME
    run = {"username": "champbot", "agent": "oneply", "team": "regmb-worlds", "seed": 0}
    trace = tmp_path / "live" / "battle-x.champbot.jsonl"

    first = Report(1, ledger=ledger, run=run, trace_path=lambda tag: trace)
    first(_battle())
    second = Report(1, ledger=ledger, run={**run, "agent": "adaptive"}, trace_path=lambda tag: None)
    second(_battle(won=False, rating=1021, battle_tag="battle-gen9championsvgc2026regmb-2"))

    rows = read_ledger(ledger)
    assert [r["result"] for r in rows] == ["win", "loss"]
    assert rows[0]["trace"] == str(trace)
    assert rows[0]["replay"].endswith("/gen9championsvgc2026regmb-2345678901")
    assert rows[0]["opponent"] == "someone"
    assert (rows[0]["rating"], rows[0]["opponent_rating"], rows[0]["turns"]) == (1040, 1101, 9)
    assert rows[1]["agent"] == "adaptive"
    assert rows[1]["trace"] is None
    assert "T" in rows[0]["finished_at"]
    # The password never enters the row: the run dict is what the caller
    # chose to record, and the script records the username only.
    assert "password" not in json.dumps(rows)


def test_the_summary_reads_the_whole_ledger(tmp_path: Path) -> None:
    assert summarize([]) == "no games in the ledger yet"
    ledger = tmp_path / LEDGER_NAME
    report = Report(3, ledger=ledger, run={"agent": "oneply", "team": "t"})
    report(_battle(rating=1000))
    report(_battle(won=False, rating=990, opponent_username="b"))
    report(_battle(won=None, rating=995, opponent_rating=None))
    text = summarize(read_ledger(ledger))
    assert "3 games: 1 won, 1 lost, 1 tied" in text
    assert "rating 1000 -> 995 (peak 1000)" in text
    assert "loss vs b (1101)" in text


def test_the_ledger_is_not_named_like_a_trace_so_the_viewer_skips_it() -> None:
    assert not LEDGER_NAME.endswith(".jsonl")


def test_the_replay_is_requested_in_the_room_when_the_battle_starts() -> None:
    sent: list[tuple[str, str]] = []

    class Client:
        async def send_message(self, message: str, room: str = "") -> None:
            sent.append((message, room))

    player = SimpleNamespace(ps_client=Client())
    callback = save_replay_on_start(lambda: player)

    async def scenario() -> None:
        callback(_battle())
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert sent == [("/savereplay", "battle-gen9championsvgc2026regmb-2345678901")]


def test_the_agent_fires_the_battle_start_hook_once_per_battle() -> None:
    from champions.agents.baseline import TracingPlayer

    class Battle:
        battle_tag = "battle-x"
        player_role = "p1"
        player_username = "champbot"
        opponent_username = "someone"
        team: dict[str, object] = {}
        teampreview_opponent_team: list[object] = []

    started: list[str] = []
    player = TracingPlayer.__new__(TracingPlayer)
    player._started = set()
    player._on_battle_start = lambda battle: started.append(battle.battle_tag)
    player.trace_for = lambda battle: SimpleNamespace(emit=lambda *a, **k: None)  # type: ignore[method-assign, assignment, return-value]
    player._belief_enabled = False
    player._dex = None
    player._seed = 0
    player.strategy = "test"
    player._format = "gen9championsvgc2026regmb"

    battle = cast(Any, Battle())
    player._emit_battle_start_once(battle)
    player._emit_battle_start_once(battle)
    assert started == ["battle-x"]


def test_games_and_reviews_alternate_and_the_next_search_waits_for_the_coach() -> None:
    order: list[str] = []

    class Player:
        async def ladder(self, n: int) -> None:
            assert n == 1
            order.append("game")

        async def close_traces(self) -> None:
            order.append("closed")

    async def coach() -> None:
        order.append("coach")

    asyncio.run(play(Player(), 3, between=coach))
    assert order == ["game", "closed", "coach"] * 3
    order.clear()
    asyncio.run(play(Player(), 2))
    assert order == ["game", "closed"] * 2


def test_the_headline_is_the_summary_without_the_per_turn_sections() -> None:
    document = (
        "# Review: battle-x, bot against someone\n"
        "\n"
        "Reviewed from p1 (trace). Result: **loss** in 8 turns.\n"
        "\n"
        "Thresholds are hand-set (pending calibration).\n"
        "\n"
        "## Summary\n"
        "\n"
        "8 of 8 decisions scored. Ex-ante loss 51.9 in total.\n"
        "\n"
        "## Critical turns\n"
        "\n"
        "By avoidable loss: turn 6 (32.4).\n"
        "\n"
        "## Preview\n"
        "\n"
        "Brought a, b, c, d.\n"
        "\n"
        "## Turn 1\n"
    )
    text = headline(document)
    assert text.startswith("Reviewed from p1 (trace). Result: **loss** in 8 turns.")
    assert "By avoidable loss: turn 6" in text
    assert "Thresholds" not in text
    assert "Brought" not in text and "Turn 1" not in text


def test_a_review_that_fails_reports_the_log_and_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = tmp_path / "battle-x.bot.jsonl"
    trace.write_text("not a trace\n", encoding="utf-8")
    assert asyncio.run(review_game(trace)) is None
    assert "review failed" in capsys.readouterr().out
    assert (tmp_path / "battle-x.bot.review.log").is_file()


def test_the_status_file_follows_the_run_through_its_phases(tmp_path: Path) -> None:
    phases: list[tuple[str, int]] = []

    class Player:
        async def ladder(self, n: int) -> None:
            phases.append(("ladder", n))

        async def close_traces(self) -> None:
            pass

    status = StatusFile(tmp_path / "status.json", of=2)
    seen: list[str] = []

    async def coach() -> None:
        seen.append(status.read()["phase"])

    asyncio.run(play(Player(), 2, between=coach, status=status))
    assert seen == ["reviewing", "reviewing"]
    final = status.read()
    assert (final["phase"], final["game"], final["of"]) == ("done", 2, 2)
    assert isinstance(final["t"], float)
    assert not (tmp_path / "status.json.tmp").exists()


def test_a_stop_flag_ends_the_run_after_the_current_game(tmp_path: Path) -> None:
    stop = tmp_path / "stop"
    stop.write_text("stale, from an earlier run\n", encoding="utf-8")
    games: list[int] = []

    class Player:
        async def ladder(self, n: int) -> None:
            games.append(n)

        async def close_traces(self) -> None:
            pass

    async def coach() -> None:
        # The viewer asks during the second game's review.
        if len(games) == 2:
            stop.write_text("stop after the current game\n", encoding="utf-8")

    status = StatusFile(tmp_path / "status.json", of=5)
    played = asyncio.run(play(Player(), 5, between=coach, status=status, stop=stop))
    assert played == 2 and games == [1, 1]
    assert not stop.exists()
    final = status.read()
    assert (final["phase"], final["game"], final["stopped"]) == ("done", 2, True)


def test_the_status_file_names_the_account_and_format_for_the_viewer(tmp_path: Path) -> None:
    status = StatusFile(
        tmp_path / "status.json",
        of=3,
        run={"username": "champbot", "format": "gen9championsvgc2026regmc", "seed": 0},
    )
    status.write("searching", 1)
    row = status.read()
    assert (row["username"], row["format"]) == ("champbot", "gen9championsvgc2026regmc")
    assert "seed" not in row

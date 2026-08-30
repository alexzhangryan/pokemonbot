"""The viewer starts what it displays. See champions/viewer/control.py.

The rule these tests exist to protect is that a run started from the viewer
writes into the directory the viewer is watching. When runs were launched by
hand that was left to the operator, and the obvious pairing was wrong:
`play_human.py` defaulted to `runs/human` while `make viewer` watched `traces/`,
so a live battle produced a viewer showing nothing and no error anywhere.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from champions.viewer.control import Supervisor, port_is_open
from champions.viewer.server import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def supervisor(tmp_path: Path) -> Supervisor:
    return Supervisor(trace_dir=tmp_path, showdown_port=8099)


def client_for(tmp_path: Path, supervisor: Supervisor) -> TestClient:
    return TestClient(create_app(tmp_path, supervisor=supervisor, autostart_showdown=False))


# -- where traces go ---------------------------------------------------------


def test_runs_write_into_the_directory_the_viewer_is_watching(
    monkeypatch: pytest.MonkeyPatch, supervisor: Supervisor
) -> None:
    """The whole point of the control panel. Asserted on the argv rather than by
    running a battle, because it is the wiring that was wrong, not the agent."""
    spawned: dict[str, list[str]] = {}

    def fake_popen(argv: list[str], **_: object) -> object:
        spawned["argv"] = argv
        raise _Stop

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    for start in (
        lambda: supervisor.start_selfplay(games=1, agent_a="random", agent_b="random", seed=0),
        lambda: supervisor.start_host(agent="greedy", games=1, username="champbot"),
    ):
        with pytest.raises(_Stop):
            _run(start())
        argv = spawned["argv"]
        trace_dir = Path(argv[argv.index("--trace-dir") + 1])
        assert trace_dir.is_relative_to(supervisor.trace_dir), argv


def test_runs_inherit_the_viewers_interpreter(
    monkeypatch: pytest.MonkeyPatch, supervisor: Supervisor
) -> None:
    """`python` on PATH is not necessarily the venv the viewer is running in,
    and a run launched outside it fails on the first import."""
    spawned: dict[str, list[str]] = {}

    def fake_popen(argv: list[str], **_: object) -> object:
        spawned["argv"] = argv
        raise _Stop

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(_Stop):
        _run(supervisor.start_selfplay(games=1, agent_a="random", agent_b="random", seed=0))

    assert spawned["argv"][0] == supervisor.python
    # -u, or a run's progress sits in a pipe buffer until it exits.
    assert spawned["argv"][1] == "-u"


# -- the control API ---------------------------------------------------------


def test_status_reports_the_simulator_and_the_absence_of_a_run(
    tmp_path: Path, supervisor: Supervisor
) -> None:
    body = client_for(tmp_path, supervisor).get("/api/status").json()

    assert body["run"] is None
    assert body["showdown"]["port"] == 8099
    assert body["showdown"]["state"] in {"off", "external"}
    assert body["format_id"] == "gen9championsvgc2026regmb"


def test_only_one_run_at_a_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, supervisor: Supervisor
) -> None:
    """Two agent runs against one simulator would fight over usernames and make
    the trace directory impossible to read."""
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProcess())
    client = client_for(tmp_path, supervisor)

    assert client.post("/api/run/selfplay", json={"games": 1}).status_code == 200
    conflict = client.post("/api/run/selfplay", json={"games": 1})
    assert conflict.status_code == 409
    assert "already" in conflict.json()["detail"]


def test_hosting_a_bot_returns_what_the_page_needs_to_hand_over_a_challenge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, supervisor: Supervisor
) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProcess())
    client = client_for(tmp_path, supervisor)

    detail = client.post("/api/run/host", json={"agent": "greedy"}).json()["detail"]

    assert detail["username"] == "champbot"
    assert detail["format_id"] == "gen9championsvgc2026regmb"
    assert detail["showdown_url"].endswith("8099")
    assert detail["team_file"].endswith(".txt")


def test_rejects_a_nonsense_run_request(tmp_path: Path, supervisor: Supervisor) -> None:
    client = client_for(tmp_path, supervisor)
    assert client.post("/api/run/selfplay", json={"games": 0}).status_code == 422
    assert client.post("/api/run/selfplay", json={"games": 10_000}).status_code == 422


def test_an_unknown_agent_is_refused_rather_than_spawned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, supervisor: Supervisor
) -> None:
    """The agent name reaches a subprocess argv, so it is checked here."""
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProcess())
    client = client_for(tmp_path, supervisor)

    response = client.post("/api/run/selfplay", json={"games": 1, "agent_a": "wishful"})
    assert response.status_code == 409


# -- adopting an existing simulator ------------------------------------------


def test_a_simulator_started_elsewhere_is_adopted_and_not_stopped(
    monkeypatch: pytest.MonkeyPatch, supervisor: Supervisor
) -> None:
    """Starting a second server would only fail to bind, and killing one we did
    not start would take out someone's `make server`."""
    monkeypatch.setattr("champions.viewer.control.port_is_open", lambda *a, **k: True)

    _run(supervisor.ensure_showdown())

    status = supervisor.showdown_status()
    assert status["state"] == "external"
    assert status["ours"] is False


def test_port_probe_reports_a_closed_port() -> None:
    # 9 is discard; nothing serves it on a development machine.
    assert port_is_open("127.0.0.1", 9, timeout=0.2) is False


# -- the page itself ---------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_client_script_parses() -> None:
    """A syntax error in app.js is a blank page with one console message, which
    is easy to miss and expensive to find. Node is already a dependency of this
    project, so checking the file costs nothing."""
    static = REPO_ROOT / "champions" / "viewer" / "static"
    result = subprocess.run(
        ["node", "--check", str(static / "app.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_battle_frames_inline_script_parses(tmp_path: Path) -> None:
    """battle.html's script is inline, so `node --check` cannot see it directly
    and it went unchecked while app.js was covered. It is worth extracting for
    the same reason app.js is worth checking: the failure mode is a frame that
    renders nothing, with the only evidence a console message inside an iframe."""
    page = (REPO_ROOT / "champions" / "viewer" / "static" / "battle.html").read_text(
        encoding="utf-8"
    )
    start = page.index("<script>") + len("<script>")
    script = page[start : page.index("</script>", start)]

    extracted = tmp_path / "battle-inline.js"
    extracted.write_text(script, encoding="utf-8")

    result = subprocess.run(["node", "--check", str(extracted)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# -- conceding a game without ending the run ---------------------------------


def test_every_run_is_spawned_able_to_hear_a_forfeit(
    monkeypatch: pytest.MonkeyPatch, supervisor: Supervisor
) -> None:
    """Both spawners pass `--control-stdin`, or the run reads nobody's commands
    and the forfeit button silently does nothing."""
    spawned: dict[str, object] = {}

    def fake_popen(argv: list[str], **kwargs: object) -> object:
        spawned["argv"] = argv
        spawned["stdin"] = kwargs.get("stdin")
        raise _Stop

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    for start in (
        lambda: supervisor.start_selfplay(games=1, agent_a="random", agent_b="random", seed=0),
        lambda: supervisor.start_host(agent="greedy", games=1, username="champbot"),
    ):
        with pytest.raises(_Stop):
            _run(start())
        assert "--control-stdin" in spawned["argv"]
        # Without a pipe the child inherits the viewer's own stdin and the
        # command would go somewhere else entirely.
        assert spawned["stdin"] is subprocess.PIPE


def test_forfeit_reaches_the_run_without_killing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, supervisor: Supervisor
) -> None:
    """The distinction the button exists for: the battle ends, the run does not."""
    process = _FakeProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: process)
    client = client_for(tmp_path, supervisor)

    client.post("/api/run/selfplay", json={"games": 3})
    body = client.post("/api/run/forfeit", json={}).json()

    assert process.stdin.getvalue() == "forfeit\n"
    assert body["state"] == "running"
    assert body["forfeits_requested"] == 1
    assert client.get("/api/status").json()["run"]["state"] == "running"


def test_forfeiting_with_no_run_is_refused_rather_than_ignored(
    tmp_path: Path, supervisor: Supervisor
) -> None:
    response = client_for(tmp_path, supervisor).post("/api/run/forfeit", json={})
    assert response.status_code == 409
    assert "no run" in response.json()["detail"]


def test_a_hosted_bot_plays_more_than_one_game_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, supervisor: Supervisor
) -> None:
    """Conceding is pointless when the run has one game in it: the forfeit and
    the end of the run become the same event."""
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProcess())
    detail = client_for(tmp_path, supervisor).post("/api/run/host", json={}).json()["detail"]
    assert detail["games"] > 1


class _Stop(Exception):
    """Raised by a stubbed Popen to stop before a real process is created."""


class _FakeStdin(io.StringIO):
    """A pipe that remembers what was written and never closes underneath us."""

    def close(self) -> None:  # pragma: no cover - defeats StringIO.getvalue()
        pass


class _FakeProcess:
    stdout = None
    pid = -1

    def __init__(self) -> None:
        self.stdin = _FakeStdin()

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _run(coro: object) -> object:
    """Drive a coroutine to completion on a fresh loop.

    These tests are synchronous on purpose: the supervisor is called from
    request handlers, and the parts under test here are the argv it builds and
    the state it keeps, neither of which needs a running server.
    """
    import asyncio

    return asyncio.run(coro)  # type: ignore[arg-type]

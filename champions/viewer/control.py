"""Starting and stopping the things a session needs, from inside the viewer.

Before this, watching the bot meant three terminals and remembering that
`play_human.py` writes to `runs/human` while `make viewer` watches `traces/`.
That mismatch is not a documentation problem, it is a design one: the component
that displays traces should be the component that decides where they go. So the
supervisor owns the trace directory and hands it to every run it starts, and a
battle started here cannot fail to appear in the view.

Two deliberate choices:

Runs are subprocesses, not tasks in this event loop. A run is long, it drives a
websocket client with its own loop thread, and it is the part most likely to
crash; none of that should be able to take the viewer down with it. The cost is
that progress arrives as parsed stdout rather than as function returns, which is
a small price for a supervisor that outlives what it supervises.

The Showdown server is adopted rather than duplicated. If something is already
listening on the port, that is someone's `make server` or an earlier viewer, and
starting a second one would just fail to bind. We attach to it, mark it external,
and never stop it -- we did not start it, so it is not ours to kill.

A run can be talked to as well as killed. Stopping was the only lever here for a
while, and it is too blunt for the thing people actually want, which is to
abandon one bad game and keep the rest of the run. Every run is spawned with a
stdin pipe and `--control-stdin`, so `forfeit_run` can concede the battle in
progress and leave the process alive to play the next one. The channel carries
that one verb and nothing else; see `champions/agents/commands.py` for why.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from scripts.run_local_server import start_server
from scripts.selfplay import AGENTS

# The agent names the control panel offers, taken from the registry the command
# line uses rather than restated, so the two cannot drift apart.
AGENT_NAMES = frozenset(AGENTS)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FORMAT_ID = "gen9championsvgc2026regmb"

# Enough recent output to explain a failure, not so much that a long run keeps
# it all in memory.
LOG_LINES = 200

# The one thing this process ever says to a run. Named rather than inlined so
# the writer and `champions/agents/commands.py`'s reader are obviously the same
# word, since nothing checks that they agree.
FORFEIT_COMMAND = "forfeit\n"

ShowdownState = Literal["off", "starting", "ready", "external", "failed"]
RunState = Literal["idle", "running", "finished", "failed", "stopped"]


def port_is_open(host: str, port: int, timeout: float = 0.35) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


@dataclass
class Run:
    """One agent process the viewer started."""

    kind: Literal["selfplay", "host"]
    label: str
    process: subprocess.Popen[str]
    started_at: float
    detail: dict[str, Any] = field(default_factory=dict)
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    state: RunState = "running"
    #: Forfeits asked for, which is not the same as battles conceded: the
    #: request goes down a pipe and the agent answers on its own log. The page
    #: uses it only to say that the button was pressed.
    forfeits_requested: int = 0

    def status(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "elapsed_s": round(time.time() - self.started_at, 1),
            "detail": self.detail,
            "forfeits_requested": self.forfeits_requested,
            "log": list(self.log),
        }


class Supervisor:
    """Owns the Showdown server and at most one agent run."""

    def __init__(
        self,
        trace_dir: Path,
        showdown_port: int = 8090,
        host: str = "localhost",
        python: str | None = None,
    ) -> None:
        self.trace_dir = trace_dir
        self.showdown_port = showdown_port
        self.host = host
        # The interpreter running the viewer, so a run inherits the same venv
        # rather than whatever `python` happens to mean on PATH.
        self.python = python or sys.executable

        self._showdown: subprocess.Popen[str] | None = None
        self._showdown_state: ShowdownState = "off"
        self._showdown_error: str | None = None
        self._run: Run | None = None
        self._lock = asyncio.Lock()

    # -- showdown ---------------------------------------------------------

    @property
    def showdown_url(self) -> str:
        return f"http://{self.host}:{self.showdown_port}"

    async def ensure_showdown(self) -> None:
        """Start the simulator if nothing is already serving that port."""
        async with self._lock:
            if self._showdown_state in {"starting", "ready", "external"}:
                return
            if port_is_open(self.host, self.showdown_port):
                self._showdown_state = "external"
                return

            self._showdown_state = "starting"
            self._showdown_error = None

        try:
            # start_server blocks until the readiness line appears, so it runs
            # off the event loop; the page shows "starting" meanwhile.
            process = await asyncio.to_thread(start_server, self.showdown_port)
        except Exception as error:  # noqa: BLE001 - surfaced to the page verbatim
            self._showdown_state = "failed"
            self._showdown_error = str(error)
            return

        self._showdown = process
        self._showdown_state = "ready"

    def stop_showdown(self) -> None:
        """Stop the simulator, but only if we were the ones who started it."""
        if self._showdown is not None:
            _terminate(self._showdown)
            self._showdown = None
        self._showdown_state = "off"

    def showdown_status(self) -> dict[str, Any]:
        state = self._showdown_state
        # A process we started can die underneath us; a port we adopted can go
        # away. Either way the honest answer comes from looking, not from what
        # we recorded when we last acted.
        if state in {"ready", "external"} and not port_is_open(self.host, self.showdown_port):
            state = "off"
            self._showdown_state = "off"
        return {
            "state": state,
            "port": self.showdown_port,
            "url": self.showdown_url,
            "ours": self._showdown is not None,
            "error": self._showdown_error,
        }

    # -- runs -------------------------------------------------------------

    def run_status(self) -> dict[str, Any] | None:
        if self._run is None:
            return None
        self._reap()
        return self._run.status()

    def _reap(self) -> None:
        """Fold a finished process's exit code into the run's state."""
        run = self._run
        if run is None or run.state != "running":
            return
        code = run.process.poll()
        if code is None:
            return
        run.state = "finished" if code == 0 else "failed"

    async def start_selfplay(
        self, games: int, agent_a: str, agent_b: str, seed: int
    ) -> dict[str, Any]:
        _check_agent(agent_a)
        _check_agent(agent_b)
        await self.ensure_showdown()
        subdir = self.trace_dir / "selfplay"
        return self._spawn(
            kind="selfplay",
            label=f"self-play {games}x {agent_a} vs {agent_b}",
            argv=[
                "scripts/selfplay.py",
                str(games),
                "--port",
                str(self.showdown_port),
                "--trace-dir",
                str(subdir),
                "--seed",
                str(seed),
                "--agent-a",
                agent_a,
                "--agent-b",
                agent_b,
                "--control-stdin",
            ],
            detail={"games": games, "agent_a": agent_a, "agent_b": agent_b, "seed": seed},
        )

    async def start_host(self, agent: str, games: int, username: str) -> dict[str, Any]:
        """Put a bot on the ladder waiting to be challenged by a person."""
        _check_agent(agent)
        await self.ensure_showdown()
        subdir = self.trace_dir / "human"
        return self._spawn(
            kind="host",
            label=f"{username} ({agent}) waiting for a challenge",
            argv=[
                "scripts/play_human.py",
                "--agent",
                agent,
                "--games",
                str(games),
                "--port",
                str(self.showdown_port),
                "--username",
                username,
                "--trace-dir",
                str(subdir),
                "--control-stdin",
            ],
            detail={
                "agent": agent,
                "games": games,
                "username": username,
                "format_id": FORMAT_ID,
                "showdown_url": self.showdown_url,
                "team_file": "data/teams/regmb-alpha.txt",
            },
        )

    def _spawn(
        self, kind: Any, label: str, argv: list[str], detail: dict[str, Any]
    ) -> dict[str, Any]:
        self._reap()
        if self._run is not None and self._run.state == "running":
            raise RuntimeError(f"a run is already going: {self._run.label}")

        process = subprocess.Popen(
            # -u so progress lines arrive as they happen: Python block-buffers
            # stdout when it is a pipe, which would hold a whole run's output
            # until it exited.
            [self.python, "-u", *argv],
            cwd=REPO_ROOT,
            # The control channel. Without a pipe here the child inherits this
            # process's stdin, and a command written to it would go to whoever
            # started the viewer rather than to the agent.
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        run = Run(kind=kind, label=label, process=process, started_at=time.time(), detail=detail)
        self._run = run

        # A reader thread, because a pipe nobody drains fills and blocks the
        # child. Keeping the tail is a side benefit; draining is the point.
        asyncio.create_task(asyncio.to_thread(_pump, run))
        return run.status()

    def forfeit_run(self) -> dict[str, Any]:
        """Concede the battle in progress, leaving the run to play the next one.

        Deliberately not an error when there is no battle on the board: the run
        may be between games or waiting for a challenge, and the request is
        harmless there. The agent prints what it did either way, so the run log
        is the honest account of whether anything was conceded.
        """
        self._reap()
        run = self._run
        if run is None or run.state != "running":
            raise RuntimeError("no run is going")
        stream = run.process.stdin
        if stream is None:  # pragma: no cover - every run we spawn has a pipe
            raise RuntimeError("this run has no control channel")
        try:
            stream.write(FORFEIT_COMMAND)
            stream.flush()
        except (BrokenPipeError, ValueError) as error:
            # The process died between the reap above and this write.
            raise RuntimeError(f"the run is not listening: {error}") from error
        run.forfeits_requested += 1
        return run.status()

    def stop_run(self) -> None:
        if self._run is None:
            return
        if self._run.state == "running":
            _terminate(self._run.process)
            self._run.state = "stopped"

    # -- lifecycle --------------------------------------------------------

    def shutdown(self) -> None:
        self.stop_run()
        self.stop_showdown()

    def status(self) -> dict[str, Any]:
        return {
            "showdown": self.showdown_status(),
            "run": self.run_status(),
            "trace_dir": str(self.trace_dir),
            "format_id": FORMAT_ID,
            # Served rather than hardcoded in the page, so adding an agent to
            # the registry adds it to the control bar with no client change.
            "agents": sorted(AGENT_NAMES),
        }


def _check_agent(name: str) -> None:
    """Reject an unknown agent here rather than in the subprocess.

    The name ends up in an argv, and `selfplay.py` would reject it too — but as
    a run that starts and immediately dies, which reads as a broken agent rather
    than as a typo.
    """
    if name not in AGENT_NAMES:
        raise ValueError(f"unknown agent {name!r}; expected one of {sorted(AGENT_NAMES)}")


def _pump(run: Run) -> None:
    stream = run.process.stdout
    if stream is None:
        return
    for line in iter(stream.readline, ""):
        text = line.rstrip()
        if text:
            run.log.append(text)


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()

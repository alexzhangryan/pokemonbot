"""The live view and the review client, which are the same program.

docs/07-observability.md section 1 makes the point that these two differ only in
where the events come from, and section 5 asks for the client to be decoupled
from the agent by the schema alone. Both fall out of one decision made here: the
server never talks to the agent, it tails the trace file. A finished battle is a
file that has stopped growing, and a live battle is a file that has not, so
replay and live are the same code path with no branch between them.

That also means the viewer cannot perturb play. It holds no reference to a
Player, runs in its own process, and is incapable of sending anything to
Showdown. "Purely read-only" is a property of the architecture rather than a
rule someone has to remember.

The consequences worth knowing:

- Latency is a poll interval, not a push. `Trace` flushes after every event, so
  what arrives is at most POLL_INTERVAL_S stale. For watching a bot think, a
  quarter second is invisible; nothing here is on the decision path.
- The server tolerates events it does not understand. A trace written by a later
  agent version renders with the panels it can fill and marks the rest unknown,
  which is the requirement that lets this survive M1 through M11 unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from champions.teams import DEFAULT
from champions.viewer.control import FORMAT_ID, Supervisor
from champions.viewer.ladder import LadderLookup

STATIC_DIR = Path(__file__).parent / "static"

# How often a tailer checks a live file for new events.
POLL_INTERVAL_S = 0.25

# A trace is considered live if it has been written to within this window. Only
# ever a display hint: the tailer follows a file whether or not it looks live.
LIVE_WINDOW_S = 60.0


@dataclass(frozen=True)
class TraceFile:
    path: Path
    trace_id: str

    def summary(self, now: float) -> dict[str, Any]:
        """Enough to populate the trace list without reading the whole file.

        Deliberately cheap: stat plus a single line. The list is polled by the
        browser, and a directory of finished runs should not be re-parsed on
        every poll.
        """
        stat = self.path.stat()
        head = _first_event(self.path)
        payload = head.get("payload", {}) if head else {}
        # The tail says how it ended, if it has. One more short read.
        tail = _last_event(self.path)
        ended = tail.get("payload", {}) if tail and tail.get("type") == "battle_end" else None
        # The coach's overlay is a copy of this trace with the analysis
        # interleaved, written beside it (D76). A listing names it so the
        # client can open the reviewed copy in place of the plain one (D84).
        is_review = self.trace_id.endswith(".review")
        review_path = self.path.with_name(self.path.name[: -len(".jsonl")] + ".review.jsonl")
        return {
            "id": self.trace_id,
            "battle_id": head.get("battle_id") if head else self.trace_id,
            "format_id": payload.get("format_id"),
            "agent": payload.get("agent"),
            "strategy": payload.get("strategy"),
            "player": payload.get("player_username"),
            "opponent": payload.get("opponent_username"),
            "result": ended.get("result") if ended else None,
            "turns": ended.get("turns") if ended else None,
            "is_review": is_review,
            "review": f"{self.trace_id}.review"
            if not is_review and review_path.is_file()
            else None,
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
            "live": (now - stat.st_mtime) < LIVE_WINDOW_S,
        }


class SelfPlayRequest(BaseModel):
    games: int = Field(default=5, ge=1, le=500)
    agent_a: str = "random"
    agent_b: str = "random"
    seed: int = Field(default=0, ge=0)


class HostRequest(BaseModel):
    agent: str = "greedy"
    # More than one by default. A hosted bot that plays a single game and exits
    # makes conceding pointless -- the forfeit and the end of the run become the
    # same event -- and putting it up again is a button press either way.
    games: int = Field(default=3, ge=1, le=50)
    username: str = Field(default="champbot", min_length=1, max_length=18)


def create_app(
    trace_dir: Path | str = "traces",
    showdown_port: int = 8090,
    supervisor: Supervisor | None = None,
    autostart_showdown: bool = True,
    ladder: LadderLookup | None = None,
    launch_ladder: LadderLauncher | None = None,
) -> FastAPI:
    """The viewer, and the control panel that starts what it displays.

    The supervisor owns `trace_dir` and hands it to every run it starts, which
    is what makes a battle launched from the page certain to show up in it. That
    was not true when the runs were started by hand: `play_human.py` wrote to
    `runs/human` while the viewer watched `traces/`, and the two never met.
    """
    root = Path(trace_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    boss = supervisor or Supervisor(trace_dir=root, showdown_port=showdown_port)
    ratings = ladder or LadderLookup()
    launcher = launch_ladder or start_ladder_run

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Started in the background: the page should render immediately and
        # show the simulator coming up, rather than waiting on a node boot.
        task = asyncio.create_task(boss.ensure_showdown()) if autostart_showdown else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            boss.shutdown()

    app = FastAPI(title="Champions bot viewer", lifespan=lifespan)

    def resolve(trace_id: str) -> Path:
        """Map a trace id to a file inside the root, or refuse.

        The id comes from the URL, so it is untrusted input being turned into a
        filesystem path. Resolving and then checking containment rejects both
        `..` traversal and absolute paths, and rejects them after symlink
        resolution rather than by pattern-matching the string.
        """
        candidate = (root / f"{trace_id}.jsonl").resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"no trace {trace_id!r}")
        return candidate

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/traces")
    def list_traces() -> JSONResponse:
        now = time.time()
        summaries = []
        for found in sorted(root.rglob("*.jsonl")):
            try:
                summaries.append(_trace_file(root, found).summary(now))
            except (OSError, json.JSONDecodeError):
                # A file mid-write, or something that is not a trace. Skipping
                # one entry is better than failing the whole listing.
                continue
        summaries.sort(key=lambda s: s["modified"], reverse=True)
        return JSONResponse(
            {
                "trace_dir": str(root),
                "showdown_url": boss.showdown_url,
                "traces": summaries,
            }
        )

    # -- control ---------------------------------------------------------

    stop_flag = root / "stop"

    @app.get("/api/status")
    def status() -> JSONResponse:
        # What a ladder run says it is doing between battles, if one is
        # writing the file (`scripts/ladder_live.py`, D81). The trace cannot
        # say "searching for a game": there is no trace yet.
        live = _read_status_file(root / "status.json")
        if live is not None:
            live["stop_requested"] = stop_flag.is_file()
        return JSONResponse({**boss.status(), "live": live, "account": _account()})

    # The ladder run is not the viewer's subprocess, so the viewer cannot end
    # it and should not try. What it can do is leave a flag the run checks
    # between games: finish this one, then stop (D82).
    @app.post("/api/live/stop")
    def live_stop() -> JSONResponse:
        stop_flag.write_text("stop after the current game\n", encoding="utf-8")
        return JSONResponse({"stop_requested": True})

    @app.post("/api/live/resume")
    def live_resume() -> JSONResponse:
        stop_flag.unlink(missing_ok=True)
        return JSONResponse({"stop_requested": False})

    # Start a ladder run from the page (D87): `scripts/ladder_live.py` as a
    # detached process writing to this directory, so closing the viewer does
    # not forfeit a game. `games` of 0 means until the stop button.
    @app.post("/api/live/start")
    def live_start(body: dict[str, Any] | None = None) -> JSONResponse:
        body = body or {}
        live = _read_status_file(root / "status.json")
        if live is not None and live.get("phase") != "done":
            raise HTTPException(status_code=409, detail="a ladder run is already active")
        if _account() is None:
            raise HTTPException(status_code=409, detail="no ladder account in .env")
        try:
            games = int(body.get("games") or 0)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="games must be a number") from error
        agent = str(body.get("agent") or DEFAULT_LADDER_AGENT)
        team = str(body.get("team") or DEFAULT_TEAM)
        stop_flag.unlink(missing_ok=True)
        pid = launcher(root, games if games > 0 else UNTIL_STOPPED, agent, team)
        return JSONResponse(
            {"started": True, "pid": pid, "games": games, "agent": agent, "team": team}
        )

    # The bot's rating and rank on the official ladder, from the site's public
    # JSON, cached a minute (`champions/viewer/ladder.py`, D83).
    @app.get("/api/ladder")
    async def ladder_rating(user: str, format: str) -> JSONResponse:
        if not user.strip() or not format.strip():
            raise HTTPException(status_code=400, detail="user and format are required")
        return JSONResponse(await ratings.lookup(user, format))

    @app.post("/api/showdown/start")
    async def showdown_start() -> JSONResponse:
        await boss.ensure_showdown()
        return JSONResponse(boss.showdown_status())

    @app.post("/api/showdown/stop")
    def showdown_stop() -> JSONResponse:
        boss.stop_showdown()
        return JSONResponse(boss.showdown_status())

    @app.post("/api/run/selfplay")
    async def run_selfplay(request: SelfPlayRequest) -> JSONResponse:
        try:
            started = await boss.start_selfplay(
                games=request.games,
                agent_a=request.agent_a,
                agent_b=request.agent_b,
                seed=request.seed,
            )
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return JSONResponse(started)

    @app.post("/api/run/host")
    async def run_host(request: HostRequest) -> JSONResponse:
        """Put a bot on the local server waiting to be challenged.

        The response carries everything the page needs to hand the user a link
        into Showdown: the room-less client URL, the format, the bot's name and
        the team file to import.
        """
        try:
            started = await boss.start_host(
                agent=request.agent, games=request.games, username=request.username
            )
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return JSONResponse(started)

    @app.post("/api/run/forfeit")
    def run_forfeit() -> JSONResponse:
        """Concede the battle in progress without ending the run.

        This is the one thing the viewer can say to a running agent, and it is
        worth being precise about why it does not break the read-only property
        this module opens by claiming. The viewer still cannot influence how the
        agent plays: it holds no Player, and the channel carries a single verb
        that ends a battle rather than choosing inside one. The supervisor could
        already kill the process outright; conceding is strictly gentler.
        """
        try:
            return JSONResponse(boss.forfeit_run())
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/run/stop")
    def run_stop() -> JSONResponse:
        boss.stop_run()
        return JSONResponse(boss.status())

    @app.get("/api/trace/{trace_id:path}")
    def get_trace(trace_id: str) -> JSONResponse:
        path = resolve(trace_id)
        return JSONResponse({"id": trace_id, "events": list(read_events(path))})

    @app.websocket("/ws/trace/{trace_id:path}")
    async def stream_trace(websocket: WebSocket, trace_id: str) -> None:
        """Replay the file, then follow it.

        The client gets the same event objects either way and does not know
        which half of the stream it is in, beyond the `live` marker that says
        the backlog is drained.
        """
        await websocket.accept()
        try:
            path = (root / f"{trace_id}.jsonl").resolve()
            if not path.is_relative_to(root) or not path.is_file():
                await websocket.send_json({"kind": "error", "message": f"no trace {trace_id!r}"})
                await websocket.close()
                return

            async for batch in tail_events(path):
                await websocket.send_json(batch)
        except WebSocketDisconnect:
            pass

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _trace_file(root: Path, path: Path) -> TraceFile:
    return TraceFile(path=path, trace_id=path.relative_to(root).as_posix()[: -len(".jsonl")])


def read_events(path: Path) -> list[dict[str, Any]]:
    """Every complete event in the file.

    Parsed as plain dicts rather than through `TraceEvent`, on purpose: the
    viewer must render traces written by agent versions whose schema it does not
    have, and validating here would turn forward compatibility into a crash.
    Malformed lines are skipped, since a trace being tailed can end mid-line.
    """
    events = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _last_event(path: Path, window: int = 65536) -> dict[str, Any] | None:
    """The last complete line's event, reading only the file's tail."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - window))
            data = handle.read()
    except OSError:
        return None
    lines = [line for line in data.split(b"\n") if line.strip()]
    if not data.endswith(b"\n") and lines:
        lines.pop()  # a partial line still being written
    for raw in reversed(lines):
        try:
            return dict(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return None


#: What a run from the page plays when the page does not say.
DEFAULT_LADDER_AGENT = "belief"
DEFAULT_TEAM = DEFAULT
#: "Until stopped": a game count the stop flag will end long before.
UNTIL_STOPPED = 100_000

LadderLauncher = Callable[[Path, int, str, str], int]


def start_ladder_run(root: Path, games: int, agent: str, team: str) -> int:
    """`scripts/ladder_live.py`, detached, its output in `<root>/ladder.log`.

    Detached rather than a child of the viewer (`Supervisor` runs are
    children, and are meant to die with it): a ladder game is rated, and
    closing the page must not forfeit one. Returns the process id.
    """
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "ladder_live.py"
    log = root / "ladder.log"
    command = [
        sys.executable,
        str(script),
        str(games),
        "--agent",
        agent,
        "--team",
        team,
        "--trace-dir",
        str(root),
    ]
    flags: dict[str, Any] = {}
    if os.name == "nt":
        flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    else:
        flags["start_new_session"] = True
    with log.open("ab") as handle:
        process = subprocess.Popen(
            command,
            cwd=str(script.parent.parent),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            **flags,
        )
    return int(process.pid)


DETACHED_PROCESS = 0x00000008


def _account() -> dict[str, str] | None:
    """The bot's ladder account, from `.env` in the working directory: the
    username only, never the password. The viewer shows this account's
    rating whatever is on screen (D84)."""
    path = Path(".env")
    if not path.is_file():
        return None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("PS_USERNAME=") or line.startswith("export PS_USERNAME="):
                value = line.split("=", 1)[1].strip().strip("\"'")
                if value:
                    return {"username": value, "format": FORMAT_ID}
    except OSError:
        return None
    return None


def _first_event(path: Path) -> dict[str, Any] | None:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    return dict(json.loads(line))
                except json.JSONDecodeError:
                    return None
    return None


async def tail_events(
    path: Path, poll_interval_s: float = POLL_INTERVAL_S
) -> AsyncIterator[dict[str, Any]]:
    """Yield batches of events, following the file as it grows.

    Batched rather than one message per event because a completed battle's
    backlog is hundreds of events and the browser should lay it out once. The
    `live` flag on a batch means the backlog is drained and everything after
    this point is arriving as the agent produces it.

    A trailing partial line is left unconsumed and retried on the next poll,
    which is the case that matters: the writer appends while we read.

    `age_s` is how long ago the file was last written. It is what tells the
    client whether a battle is still in progress, which the arrival of events
    alone cannot: a battle that ended is a file nobody is appending to, and a
    battle mid-turn is a file that is simply quiet for a few seconds. Without
    it the view claims to be live for as long as the socket stays open.
    """
    offset = 0
    drained = False

    while True:
        events, offset = await asyncio.to_thread(_read_from, path, offset)
        age = await asyncio.to_thread(_age_s, path)
        if events or not drained:
            yield {"kind": "events", "events": events, "live": drained, "age_s": age}
        elif age is not None and age < LIVE_WINDOW_S:
            # No new events, but the file is fresh: keep the client's live
            # indicator honest without sending it anything to render.
            yield {"kind": "heartbeat", "live": True, "age_s": age, "events": []}
        drained = True
        await asyncio.sleep(poll_interval_s)


def _read_status_file(path: Path) -> dict[str, Any] | None:
    """The ladder's status file as a dict, or None if absent or mid-write."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _age_s(path: Path) -> float | None:
    """Seconds since the trace was last written, or None if it vanished."""
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _read_from(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Complete lines from `offset` onward, plus the offset after the last one."""
    events: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()

    consumed = 0
    for raw in data.splitlines(keepends=True):
        if not raw.endswith(b"\n"):
            break  # partial line; the writer is mid-append
        consumed += len(raw)
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            events.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return events, offset + consumed

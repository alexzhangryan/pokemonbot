"""A control channel from the supervisor to a running agent.

The viewer spawns agent runs as subprocesses (`champions/viewer/control.py`) and
until now the only thing it could do to one was kill it. That is the wrong
granularity for the common case, which is not "this run is bad" but "this *game*
is bad": a battle has gone long, or has gone somewhere uninteresting, and what
you want is the next one. Terminating the process to get there throws away every
remaining game in the run.

So a run can opt into reading single-word commands from its own stdin. Three
things about the shape of it are deliberate.

The only verb is `forfeit`. Not because a wider protocol would be hard, but
because a wider one would be a way for the viewer to influence *how the agent
plays*, and `champions/viewer/server.py` is built so that it structurally
cannot. Conceding is not a move -- it ends the battle rather than choosing
within it -- so it is the one instruction that does not compromise that.

It is opt in, off by default. `python scripts/selfplay.py 5 < /dev/null` behaves
exactly as it did.

The reader is a daemon thread, and it hands work to poke-env's loop rather than
doing any itself. poke-env runs its websocket client on `POKE_LOOP`, a loop on
another thread entirely, and touching a client from outside it is a race. So the
thread's whole job is to read a line and schedule a coroutine; the loop does the
rest.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Sequence
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from champions.agents.baseline import TracingPlayer

#: How long to wait for a scheduled command to finish before giving up on it.
#: A forfeit is one websocket write; if it has not gone out in this long, the
#: client is wedged and the honest thing is to say so rather than to block the
#: reader thread forever and stop accepting further commands.
COMMAND_TIMEOUT_S = 10.0


class CommandChannel:
    """Reads commands for a set of players and runs them on poke-env's loop."""

    def __init__(self, players: Sequence[TracingPlayer]) -> None:
        if not players:
            raise ValueError("a command channel needs at least one player")
        self.players = list(players)
        #: Battles conceded on request. Counted so a run can report a forfeit as
        #: what it is rather than leaving it to look like a loss on the board or,
        #: worse, like a protocol failure.
        self.forfeited: list[str] = []

    def handle(self, command: str) -> None:
        if command == "forfeit":
            self._forfeit()
        else:
            print(f"control: unknown command {command!r}", flush=True)

    def _forfeit(self) -> None:
        """Concede the current battle on whichever side is still in one.

        Self-play holds both players in this one process. Conceding on one side
        ends the battle for both, so this stops at the first player with a live
        battle rather than forfeiting twice and racing itself.
        """
        for player in self.players:
            future = asyncio.run_coroutine_threadsafe(
                player.forfeit_active(), player.ps_client.loop
            )
            try:
                tags = future.result(timeout=COMMAND_TIMEOUT_S)
            except Exception as error:  # noqa: BLE001 - reported, never fatal
                print(f"control: forfeit failed: {error}", flush=True)
                return
            if tags:
                self.forfeited.extend(tags)
                for tag in tags:
                    print(f"control: forfeited {tag}", flush=True)
                return
        print("control: nothing to forfeit", flush=True)


def listen(players: Sequence[TracingPlayer], stream: IO[str] | None = None) -> CommandChannel:
    """Start reading commands for `players`, and hand back the channel.

    Returns immediately. The thread is a daemon because the run's lifetime is
    the battles', not the reader's: when the last game finishes the process
    should exit, not sit waiting on a stdin nobody is going to write to.
    """
    channel = CommandChannel(players)
    source = stream if stream is not None else sys.stdin

    def pump() -> None:
        for line in source:
            command = line.strip().lower()
            if command:
                channel.handle(command)

    threading.Thread(target=pump, name="agent-commands", daemon=True).start()
    return channel

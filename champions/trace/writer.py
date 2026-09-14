"""Append-only JSONL trace writer, one file per battle. See docs/07-observability.md.

Trace.emit() is synchronous and writes the line before it returns, flushed, so
a reader tailing the file sees an event the moment the agent emits it. It used
to enqueue for a background task to drain, which kept file I/O off the decision
path in principle and in practice held every event of a turn until the search
next yielded the event loop -- the viewer saw the turn only once the bot had
mostly decided it (D81). An append and flush of a few kilobytes is well under
a millisecond, which `tests/test_trace.py` holds it to.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import IO, Any

from champions.trace.schema import TraceEvent

DEFAULT_TRACE_DIR = Path("traces")


class Trace:
    def __init__(
        self,
        battle_id: str,
        trace_dir: Path | str = DEFAULT_TRACE_DIR,
        name: str | None = None,
    ) -> None:
        """One trace file per agent-view of a battle.

        `name` overrides the filename stem, which matters in self-play: two
        agents in one process share a battle_id, and without distinct names they
        would append to the same file with independent seq counters. In a live
        game there is one agent per battle and the default is what you want.
        """
        self.battle_id = battle_id
        self.path = Path(trace_dir) / f"{name or battle_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._seq_counter = itertools.count()
        self._handle: IO[str] | None = None

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = TraceEvent(
            battle_id=self.battle_id,
            seq=next(self._seq_counter),
            type=event_type,
            payload=payload,
        )
        if self._handle is None:
            # Opened on the first event rather than in the constructor, so a
            # trace that never emits (a battle that never starts) leaves no
            # empty file for the viewer to list.
            self._handle = self.path.open("a", encoding="utf-8")
        self._handle.write(event.to_line())
        self._handle.flush()

    async def close(self) -> None:
        """Release the file. Async for the callers that bridge to poke-env's
        loop to do it; there is nothing left to wait for."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def read_events(path: Path | str) -> list[TraceEvent]:
    with Path(path).open(encoding="utf-8") as f:
        return [TraceEvent.parse_line(line) for line in f if line.strip()]

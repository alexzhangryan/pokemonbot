"""Append-only JSONL trace writer, one file per battle. See docs/07-observability.md.

Trace.emit() is synchronous and only enqueues, so it never blocks the decision
critical path; a background asyncio task drains the queue and does the actual
file I/O.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from pathlib import Path
from typing import Any

from champions.trace.schema import TraceEvent

DEFAULT_TRACE_DIR = Path("traces")


class Trace:
    def __init__(self, battle_id: str, trace_dir: Path | str = DEFAULT_TRACE_DIR) -> None:
        self.battle_id = battle_id
        self.path = Path(trace_dir) / f"{battle_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._seq_counter = itertools.count()
        self._queue: asyncio.Queue[TraceEvent] = asyncio.Queue()
        self._drain_task = asyncio.create_task(self._drain())

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = TraceEvent(
            battle_id=self.battle_id,
            seq=next(self._seq_counter),
            type=event_type,
            payload=payload,
        )
        self._queue.put_nowait(event)

    async def _drain(self) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            while True:
                event = await self._queue.get()
                f.write(event.to_line())
                f.flush()
                self._queue.task_done()

    async def close(self) -> None:
        await self._queue.join()
        self._drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._drain_task


def read_events(path: Path | str) -> list[TraceEvent]:
    with Path(path).open(encoding="utf-8") as f:
        return [TraceEvent.parse_line(line) for line in f if line.strip()]

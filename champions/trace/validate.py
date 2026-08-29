"""Structural validation for decision traces.

Used by the M0 acceptance tests and by anything that consumes traces, so that
"produced a valid trace" is a checked claim rather than "the file exists".

Deliberately structural only: it checks the envelope invariants every reader
depends on, not the payload shape of each event type, since payloads grow
through M1-M11 and readers must tolerate unknown fields (docs/07-observability.md).
"""

from __future__ import annotations

from pathlib import Path

from champions.trace.schema import SCHEMA_VERSION, EventType, TraceEvent
from champions.trace.writer import read_events


def validate_events(events: list[TraceEvent]) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    problems: list[str] = []

    if not events:
        return ["trace is empty"]

    seqs = [e.seq for e in events]
    if seqs != list(range(len(events))):
        problems.append(f"seq numbers are not contiguous from 0: {seqs[:10]}...")

    battle_ids = {e.battle_id for e in events}
    if len(battle_ids) != 1:
        problems.append(f"trace mixes multiple battle_ids: {sorted(battle_ids)}")

    for event in events:
        if event.schema_version != SCHEMA_VERSION:
            problems.append(
                f"seq {event.seq}: schema_version {event.schema_version} != {SCHEMA_VERSION}"
            )
        if not event.type:
            problems.append(f"seq {event.seq}: empty event type")
        if event.t <= 0:
            problems.append(f"seq {event.seq}: non-positive timestamp {event.t}")

    types = [e.type for e in events]
    if types[0] != EventType.BATTLE_START:
        problems.append(f"first event is {types[0]!r}, expected battle_start")
    if EventType.BATTLE_END not in types:
        problems.append("no battle_end event")
    elif types[-1] != EventType.BATTLE_END:
        problems.append(f"last event is {types[-1]!r}, expected battle_end")

    timestamps = [e.t for e in events]
    if timestamps != sorted(timestamps):
        problems.append("timestamps are not monotonically non-decreasing")

    return problems


def validate_trace_file(path: Path | str) -> list[str]:
    try:
        events = read_events(path)
    except Exception as exc:  # malformed JSON, bad encoding, truncated write
        return [f"could not parse {path}: {exc!r}"]
    return validate_events(events)

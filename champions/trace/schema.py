"""Decision trace event schema, frozen at M0. See docs/07-observability.md.

Every event carries the envelope fields (schema_version, battle_id, seq, t) plus
a `type` and a `payload`. The envelope is strict; the payload is an open dict,
since its shape is defined per event type by the component that emits it and
will keep growing through M1-M11 without needing a schema revision here.

Readers must not crash on an unrecognized event type or an unexpected field,
since the review client (built at M10) has to render traces produced by older
agent versions. `type` is therefore a plain string, not a validated enum:
EventType below is a catalog of the types known at M0, not an allowlist.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class EventType(StrEnum):
    BATTLE_START = "battle_start"
    PREVIEW_DECISION = "preview_decision"
    TURN_START = "turn_start"
    BELIEF = "belief"
    CANDIDATES = "candidates"
    PAYOFF_MATRIX = "payoff_matrix"
    EQUILIBRIUM = "equilibrium"
    TIMING = "timing"
    TURN_RESULT = "turn_result"
    BATTLE_END = "battle_end"
    ANALYSIS = "analysis"


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = SCHEMA_VERSION
    battle_id: str
    seq: int
    t: float = Field(default_factory=time.time)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def parse_line(cls, line: str) -> TraceEvent:
        return cls.model_validate_json(line)

    def to_line(self) -> str:
        return self.model_dump_json() + "\n"

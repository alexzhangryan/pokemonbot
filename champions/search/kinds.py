"""Kinds of joint action, and the prior on how often an opponent plays each (D91).

The one-turn model solves a zero-sum matrix game, and the column player of that
game is an adversary who protects whenever protecting costs nothing, which in a
one-turn model it always does. On the first 101 ladder games of the M-C cycle
the model's columns put 30 to 68 percent of their mass on a line with a
Protect in it and up to 34 percent on a double Protect; the opponents actually
protected on 4 to 14 percent of turns and never double-protected. They
switched on 29 percent of first turns, and the columns never contained a
switch at all.

This module names the *kind* of a joint action -- which of its slots attacks,
protects, uses Fake Out, or switches -- and carries a prior over kinds
distilled from the replay corpus by `scripts/build_action_prior.py`. The
solver (`matrix.solve_constrained`) pins the column player's kind marginals to
that prior with weight `PRIOR_WEIGHT` and leaves the choice *within* a kind
adversarial. With weight 0 it is the plain equilibrium; with weight 1 the
opponent chooses the kind of turn the corpus says people choose and the worst
line of that kind for us.

Keyed by format, like every other fitted artifact, and lent across
`champions.formats.LINEAGE` with the loan recorded on the object.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from champions.formats import lender
from champions.search.matrix import Equilibrium, solve_both, solve_constrained

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "policy"

#: The moves that count as "protect": a slot spent not acting, to be safe.
PROTECT_MOVES = frozenset(
    {
        "protect",
        "detect",
        "spikyshield",
        "banefulbunker",
        "burningbulwark",
        "silktrap",
        "kingsshield",
        "obstruct",
        "maxguard",
    }
)

#: Turn buckets the prior is keyed by. The first three turns have their own
#: rates -- Fake Out is a turn-one move and the lead pair decides whether
#: turn two is a pivot -- and everything after is one bucket.
BUCKETS = ("1", "2", "3", "4+")

#: How much of the column player's play is pinned to the prior. Hand-set:
#: the residual is adversarial so a position where a Protect is plainly right
#: still gets one, and the next ladder cycle reads the implied kind rates
#: against the realised ones to say whether this should move.
PRIOR_WEIGHT = 0.8


def bucket(turn: int) -> str:
    if turn <= 1:
        return "1"
    if turn == 2:
        return "2"
    if turn == 3:
        return "3"
    return "4+"


def slot_kind(slot: Mapping[str, Any]) -> str:
    """One slot's kind: attack, protect, fakeout, switch, or none."""
    kind = str(slot.get("kind") or "")
    if kind == "switch":
        return "switch"
    if kind == "move":
        move = str(slot.get("move") or "")
        if move in PROTECT_MOVES:
            return "protect"
        if move == "fakeout":
            return "fakeout"
        return "attack"
    if kind == "none":
        return "none"
    # `unrevealed` placeholders and anything unknown: the slot is doing
    # something, and an attack is what most somethings are.
    return "attack"


def action_kind(action: Mapping[str, Any]) -> str:
    """The joint action's kind, slots sorted so order does not matter."""
    slots = action.get("slots") or []
    return "+".join(sorted(slot_kind(s) for s in slots)) or "none"


@dataclass(frozen=True)
class KindPrior:
    """Rates of each joint-action kind by turn bucket, from the corpus."""

    format_id: str
    #: The format the rates were counted in, when lent.
    source_format: str
    buckets: dict[str, dict[str, float]]
    counts: dict[str, int]
    lent: bool = False

    def rates(self, turn: int) -> dict[str, float]:
        return dict(self.buckets.get(bucket(turn), {}))

    def over(self, turn: int, kinds: Sequence[str]) -> dict[str, float] | None:
        """The prior renormalised over the kinds a column set actually offers.

        A kind the columns cannot express -- no Fake Out user in play, no
        bench to switch to -- gets no mass, and the rest share the prior in
        proportion. None if nothing present carries any prior mass, in which
        case the caller falls back to the plain equilibrium.
        """
        rates = self.rates(turn)
        present = {k: rates.get(k, 0.0) for k in set(kinds)}
        total = sum(present.values())
        if total <= 0:
            return None
        return {k: v / total for k, v in present.items() if v > 0}


def path_for(format_id: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / f"actionkinds.{format_id}.json"


def load_kind_prior(format_id: str, data_dir: Path = DATA_DIR) -> KindPrior | None:
    """The prior for a format, its lineage's if it has none of its own, else None."""
    own = path_for(format_id, data_dir)
    if own.exists():
        return _read(own, format_id, lent=False)
    lent_from = lender(format_id)
    if lent_from is not None:
        borrowed = path_for(lent_from, data_dir)
        if borrowed.exists():
            return _read(borrowed, format_id, lent=True)
    return None


def _read(path: Path, format_id: str, lent: bool) -> KindPrior:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return KindPrior(
        format_id=format_id,
        source_format=str(raw.get("format_id") or format_id),
        buckets={
            str(b): {str(k): float(v) for k, v in rates.items()}
            for b, rates in raw["buckets"].items()
        },
        counts={str(b): int(n) for b, n in (raw.get("counts") or {}).items()},
        lent=lent,
    )


def solve_columns(
    payoff: np.ndarray,
    columns: Sequence[Mapping[str, Any]],
    turn: int,
    prior: KindPrior | None,
    weight: float = PRIOR_WEIGHT,
) -> tuple[Equilibrium, dict[str, Any]]:
    """Solve the turn's game, the column player's kinds pinned to the prior.

    Returns the equilibrium and a note for the trace saying what was pinned:
    the weight, the bucket, the prior over the kinds present, and the kind of
    every column. Without a prior, or with a column set none of whose kinds
    the prior has seen, this is `solve_both` and the note says so.
    """
    kinds = [action_kind(c) for c in columns]
    note: dict[str, Any] = {"weight": 0.0, "bucket": bucket(turn), "kinds": kinds, "prior": None}
    if prior is None or weight <= 0.0 or len(kinds) != payoff.shape[1]:
        return solve_both(payoff), note
    rates = prior.over(turn, kinds)
    if rates is None:
        return solve_both(payoff), note
    note.update({"weight": float(weight), "prior": rates, "lent": prior.lent})
    return solve_constrained(payoff, kinds, rates, weight), note


def implied_kind_mass(column: np.ndarray, kinds: Sequence[str]) -> dict[str, float]:
    """The column strategy's mass by kind, for reading a trace back."""
    out: dict[str, float] = {}
    for p, k in zip(column, kinds, strict=True):
        out[k] = out.get(k, 0.0) + float(p)
    return out

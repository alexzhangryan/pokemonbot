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
    per_column: bool = True,
) -> tuple[Equilibrium, dict[str, Any]]:
    """Solve the turn's game, the column player's play pinned to the prior.

    Returns the equilibrium and a note for the trace saying what was pinned:
    the weight, the bucket, the prior over the kinds present, and the kind of
    every column. Without a prior, or with a column set none of whose kinds
    the prior has seen, this is `solve_both` and the note says so.

    With `per_column`, the pinned marginals are the columns themselves rather
    than their kinds (D100). Pinning a kind leaves the choice *within* it
    adversarial, and that residual is unbounded in the size of the kind: the
    column player takes the worst of the group while holding the whole kind's
    mass. D99 widened the believed move set, which grew the largest kind group
    from 11.2 columns to 13.5 and dropped the mean reported game value from
    0.528 to 0.381 -- a model that got *more* accurate made the agent think it
    was losing, and it switched and protected more. Splitting the kind's rate
    across its members by their belief weight makes the pinned part an
    expectation over the lines the opponent is actually believed to play; the
    `1 - weight` residual is still the plain adversary, so a position where a
    Protect is plainly right still gets one.
    """
    kinds = [action_kind(c) for c in columns]
    note: dict[str, Any] = {"weight": 0.0, "bucket": bucket(turn), "kinds": kinds, "prior": None}
    if prior is None or weight <= 0.0 or len(kinds) != payoff.shape[1]:
        return solve_both(payoff), note
    rates = prior.over(turn, kinds)
    if rates is None:
        return solve_both(payoff), note
    note.update({"weight": float(weight), "prior": rates, "lent": prior.lent})
    if not per_column:
        return solve_constrained(payoff, kinds, rates, weight), note
    groups, spread = _per_column_prior(columns, kinds, rates)
    # A column whose kind carries no prior mass gets no group of its own and is
    # left to the free adversary, so read the spread defensively.
    note.update(
        {"per_column": True, "column_prior": [round(spread.get(g, 0.0), 5) for g in groups]}
    )
    return solve_constrained(payoff, groups, spread, weight), note


def _per_column_prior(
    columns: Sequence[Mapping[str, Any]],
    kinds: Sequence[str],
    rates: Mapping[str, float],
) -> tuple[list[str], dict[str, float]]:
    """One group per column, each kind's rate split across its members.

    The split is by `prior_weight`, which `policy.opponent_candidates` writes
    onto a column as the posterior probability that the opponent holds the
    moves the column plays. Columns carrying no weight -- switches, and every
    column when there is no belief -- split their kind evenly, which is already
    the right correction: it replaces "the worst member of this kind" with "a
    member of this kind", and that is where most of the pessimism was.
    """
    members: dict[str, list[int]] = {}
    for index, kind in enumerate(kinds):
        members.setdefault(kind, []).append(index)
    groups = [str(index) for index in range(len(kinds))]
    spread: dict[str, float] = {}
    for kind, indices in members.items():
        rate = float(rates.get(kind, 0.0))
        if rate <= 0.0:
            continue
        weights = [max(0.0, float(columns[i].get("prior_weight") or 0.0)) for i in indices]
        total = sum(weights)
        if total <= 0.0:
            weights = [1.0] * len(indices)
            total = float(len(indices))
        for index, w in zip(indices, weights, strict=True):
            spread[groups[index]] = rate * w / total
    return groups, spread


def implied_kind_mass(column: np.ndarray, kinds: Sequence[str]) -> dict[str, float]:
    """The column strategy's mass by kind, for reading a trace back."""
    out: dict[str, float] = {}
    for p, k in zip(column, kinds, strict=True):
        out[k] = out.get(k, 0.0) + float(p)
    return out


# -- row offsets (D94) ---------------------------------------------------------
#
# The one-turn model's systematic error by the kind of *our* line, measured as
# the coach's luck (expected minus realised) on the ladder, and subtracted
# from every cell of a row of that kind before the solve. The first cycles
# on every legal row (D92) found the optimism concentrated on one kind: an
# attack beside a voluntary switch read +11 points a decision over 17
# decisions, because the agent chooses that row exactly when the model
# overrates the partner that stays in, and that partner was knocked out on
# 59 percent of those turns against 30 percent of plain attacking turns.
# `scripts/kind_luck.py` measures the same on corpus games with open sheets,
# where the belief's errors are absent.


@dataclass(frozen=True)
class RowOffsets:
    """Win-probability points to subtract from a row, by its kind."""

    format_id: str
    offsets: dict[str, float]
    provenance: dict[str, Any]
    lent: bool = False

    def for_row(self, action: Mapping[str, Any]) -> float:
        return self.offsets.get(action_kind(action), 0.0)


def offsets_path(format_id: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / f"rowoffsets.{format_id}.json"


def load_row_offsets(format_id: str, data_dir: Path = DATA_DIR) -> RowOffsets | None:
    """The offsets for a format, its lineage's if it has none of its own, else None."""
    own = offsets_path(format_id, data_dir)
    if own.exists():
        return _read_offsets(own, format_id, lent=False)
    lent_from = lender(format_id)
    if lent_from is not None:
        borrowed = offsets_path(lent_from, data_dir)
        if borrowed.exists():
            return _read_offsets(borrowed, format_id, lent=True)
    return None


def _read_offsets(path: Path, format_id: str, lent: bool) -> RowOffsets:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return RowOffsets(
        format_id=format_id,
        offsets={str(k): float(v) for k, v in (raw.get("offsets") or {}).items()},
        provenance={k: v for k, v in raw.items() if k != "offsets"},
        lent=lent,
    )


def apply_row_offsets(
    payoff: np.ndarray, rows: Sequence[Mapping[str, Any]], offsets: RowOffsets | None
) -> tuple[np.ndarray, dict[str, float]]:
    """The payoff with each row's kind offset subtracted, and what was applied.

    Returns the same array when there is nothing to apply, so the coach's and
    the agent's numbers are unchanged wherever no offset is defined.
    """
    if offsets is None or not offsets.offsets:
        return payoff, {}
    applied: dict[str, float] = {}
    shifted = np.array(payoff, dtype=float, copy=True)
    for i, row in enumerate(rows):
        off = offsets.for_row(row)
        if off:
            shifted[i, :] -= off
            applied[action_kind(row)] = off
    if not applied:
        return payoff, applied
    # A cell is a win probability; an offset must not take it outside one.
    return np.clip(shifted, 0.0, 1.0), applied

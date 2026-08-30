"""Running the pruning guard over real decisions.

`docs/04-decision-engine.md` section 3 calls candidate pruning the highest
leverage decision in the engine and attaches one condition to it: pruning must
never drop an action that is uniquely correct, and that has to be *measured*
offline by solving the unpruned game and recording how often its equilibrium
puts non-trivial mass on a discarded action. `policy.discard_rate` has
implemented the per-position half of that since M2 and had never been run.

This is the other half: real positions, the opponent's columns held fixed, and
a number with an interval over battles.

## Why traces rather than fresh games

A trace already carries everything the measurement needs and carries it in the
form the agent actually saw. `turn_start` holds the exact snapshot the search
evaluated; the unpruned `candidates` event holds the full legal joint action
set, enumerated from the request rather than rederived; the pruned one holds the
opponent columns and the surviving rows. Replaying games to recover the same
three things would introduce a second path to them, which is the failure mode
`positions.py` was written to avoid on the feature side.

Two consequences follow, and both are limits on what the number means rather
than on how it is computed.

**The columns are whatever the traced agent used.** Pruning is measured on the
row side only, which is what section 3 asks for, but a row set is only "right"
against some column set. Under the revealed-moves-only opponent model an early
turn has a single column and the unpruned equilibrium degenerates to an argmax,
so `n_columns` travels with every measurement and the summary breaks the number
out by it.

**The payoffs are today's, not the trace's.** The matrix is recomputed with the
current evaluation function, so a trace written before M6's fit is measured
against the weights the agent would use now. That is the right choice for a
guard on the current engine and the wrong one for reproducing a past decision;
the recomputed matrix is not asserted to equal the recorded one.

## Two numbers, because mass alone is not readable

Discarded mass is all or nothing. A pruned set that loses a row worth 0.9001 to
one worth 0.9000 reports the same 1.0 as one that throws away the only winning
move, and section 3's threshold cannot tell them apart. So `value_loss` -- the
game value of the unpruned row set minus the game value of the kept one, same
columns -- is reported beside it. Mass says how often pruning changed the
answer; value loss says whether it mattered.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from champions.search.matrix import solve_both
from champions.search.policy import DEFAULT_K, BasePowerPolicy, discard_rate
from champions.search.positions import read_events

#: Percentile bounds for the reported interval, matching `fit.bootstrap_weights`
#: so the two are read on the same scale.
INTERVAL = (2.5, 97.5)


class MatrixFn(Protocol):
    """Payoffs for one position: rows ours, columns theirs.

    Injected rather than imported so the harness can be tested without a dex and
    so the M7 benchmark can hold the payoff model fixed while it varies the
    policy. `payoff_matrix` bound to a `TurnModel` is the production instance.
    """

    def __call__(
        self,
        snapshot: dict[str, Any],
        ours: list[dict[str, Any]],
        theirs: list[dict[str, Any]],
    ) -> np.ndarray: ...  # pragma: no cover


#: A candidate selection: the position, the legal joint actions and a budget in,
#: the survivors out. The snapshot is there because implementation A as
#: `docs/04-decision-engine.md` section 3 specifies it is four questions about
#: the board -- does this knock a target out, is this slot threatened, does this
#: flip a race, is this the turn Fake Out works -- and a guard that handed a
#: provider only the action list could measure only a provider that ignores the
#: board. Which is the one it was first run against, and the one it found
#: wanting. `HeuristicPolicy.candidates` has this shape once `belief` is
#: defaulted, and so will implementations B and C.
KeepFn = Callable[[dict[str, Any], list[dict[str, Any]], int], list[dict[str, Any]]]


@dataclass(frozen=True)
class Decision:
    """One traced decision, with the unpruned game it was solved out of."""

    battle_id: str
    #: Which side of the battle this trace is the view of. Self-play writes both
    #: viewpoints of one game under one battle id, so a position is only
    #: identified by battle and turn once this is part of the key.
    viewpoint: str
    turn: int
    snapshot: dict[str, Any]
    #: Every legal joint action, as the tracer described it.
    legal: list[dict[str, Any]]
    #: The opponent's columns the agent actually solved against.
    columns: list[dict[str, Any]]
    #: What the agent's own policy kept, by protocol message.
    kept: tuple[str, ...]
    #: The `k` the agent ran with, which is what `matches_trace` compares at.
    k: int
    #: Which provider produced `kept`. `matches_trace` is a statement about that
    #: policy and about no other, so a run measuring several has to know which
    #: one the trace is evidence for.
    traced_policy: str = ""


@dataclass(frozen=True)
class Measurement:
    """The guard, evaluated on one position, at one `k`, for one policy."""

    battle_id: str
    viewpoint: str
    turn: int
    k: int
    n_legal: int
    n_columns: int
    #: Equilibrium probability the unpruned game puts on discarded rows.
    discarded_mass: float
    #: Win probability the pruning cost, columns held fixed.
    value_loss: float
    #: Whether the re-derived kept set is the one the trace recorded. Only
    #: meaningful at the trace's own `k` and for the policy the trace was
    #: written by; None elsewhere.
    matches_trace: bool | None
    #: Which candidate provider this row measures.
    policy: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "battle_id": self.battle_id,
            "viewpoint": self.viewpoint,
            "turn": self.turn,
            "k": self.k,
            "policy": self.policy,
            "n_legal": self.n_legal,
            "n_columns": self.n_columns,
            "discarded_mass": self.discarded_mass,
            "value_loss": self.value_loss,
            "matches_trace": self.matches_trace,
        }


@dataclass(frozen=True)
class Summary:
    """The measurement over a set of positions, with an interval over battles."""

    k: int
    policy: str
    n_positions: int
    n_battles: int
    mean: float
    low: float
    high: float
    #: Fraction of positions where pruning discarded any mass at all.
    nonzero_fraction: float
    #: Mean and worst win probability given up.
    mean_value_loss: float
    max_value_loss: float
    #: Positions where the re-derived kept set differed from the traced one.
    trace_mismatches: int
    #: Positions the check ran on at all. Zero mismatches means agreement when
    #: this is large and means nothing when it is zero, and the two are not
    #: distinguishable from the mismatch count alone.
    trace_checked: int = 0

    def as_row(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "policy": self.policy,
            "n_positions": self.n_positions,
            "n_battles": self.n_battles,
            "mean": self.mean,
            "low": self.low,
            "high": self.high,
            "nonzero_fraction": self.nonzero_fraction,
            "mean_value_loss": self.mean_value_loss,
            "max_value_loss": self.max_value_loss,
            "trace_mismatches": self.trace_mismatches,
            "trace_checked": self.trace_checked,
        }


# -- reading decisions -------------------------------------------------------


def decisions(events: Sequence[dict[str, Any]]) -> list[Decision]:
    """The measurable decisions in one agent-view trace.

    A decision is a `turn_start`, the unpruned `candidates` event after it and
    the pruned one after that. Anything missing a piece is skipped rather than
    patched: a watchdog that fires before the search leaves a turn with a legal
    set and no candidate set, and a baseline agent's trace has no pruned event
    at all. Neither is an error, and neither is measurable.
    """
    out: list[Decision] = []
    snapshot: dict[str, Any] | None = None
    legal: dict[str, Any] | None = None
    viewpoint = ""

    for event in events:
        kind = event.get("type")
        payload = event.get("payload") or {}
        if kind == "battle_start":
            viewpoint = str(payload.get("player_role") or "")
            continue
        if kind == "turn_start":
            snapshot, legal = payload.get("state"), None
            continue
        if kind != "candidates":
            continue

        if not payload.get("pruned"):
            # Refused rather than measured. `MAX_TRACED_CANDIDATES` caps the
            # legal list at 300, and a capped list is not the unpruned game --
            # measuring it would compare the policy against a row set that is
            # already pruned, silently and in the flattering direction.
            legal = None if payload.get("truncated") else payload
            continue

        if snapshot is None or legal is None:
            continue
        out.append(
            Decision(
                battle_id=str(event.get("battle_id") or ""),
                viewpoint=viewpoint,
                turn=int(payload.get("turn", 0)),
                snapshot=snapshot,
                legal=list(legal.get("joint") or []),
                columns=list(payload.get("opponent_joint") or []),
                kept=tuple(a.get("message", "") for a in payload.get("joint") or []),
                k=int(payload.get("k") or len(payload.get("joint") or [])),
                traced_policy=_traced_policy(payload),
            )
        )
        legal = None

    return [d for d in out if d.legal and d.columns]


#: Traces written before there was more than one implementation A record
#: `policy_provider` as "heuristic", and the policy they were written by is the
#: base-power one. Mapped rather than special-cased at the comparison, so that a
#: reader can see the equivalence instead of inferring it from a missing check.
LEGACY_POLICY_NAMES = {"heuristic": BasePowerPolicy.name}


def _traced_policy(payload: dict[str, Any]) -> str:
    for candidate in payload.get("joint") or []:
        name = str(candidate.get("policy_provider") or "")
        if name:
            return LEGACY_POLICY_NAMES.get(name, name)
    return ""


# -- measuring ---------------------------------------------------------------


#: The name a single unnamed policy is measured under, so that the one-policy
#: convenience wrappers and the benchmark produce the same shape of row.
SOLE_POLICY = "policy"


def measure(
    decision: Decision,
    matrix_fn: MatrixFn,
    keep: KeepFn,
    k: int = DEFAULT_K,
) -> Measurement | None:
    """The guard on one position, or None if there is nothing to prune.

    A position with `k` or fewer legal actions is not a measurement of pruning:
    nothing was discarded because nothing could be, and counting those zeros
    would report a rate diluted by every forced turn in the corpus.
    """
    return next(iter(measure_many(decision, matrix_fn, keep, (k,))), None)


def measure_many(
    decision: Decision,
    matrix_fn: MatrixFn,
    keep: KeepFn | Mapping[str, KeepFn],
    ks: Sequence[int] = (DEFAULT_K,),
) -> list[Measurement]:
    """Every budget in `ks`, and every policy in `keep`, against one payoff matrix.

    The matrix is the expensive part and depends on neither -- it is the
    unpruned game, which is what pruning exists to avoid computing. Rebuilding
    it per budget would multiply the cost of a sweep by the length of the sweep
    for no change in any number, and rebuilding it per policy is worse than
    that: section 3 requires the providers to be benchmarked *identically*, and
    two sweeps compare them on positions that are only nominally the same.
    """
    budgets = [k for k in ks if k < len(decision.legal)]
    if not budgets:
        return []

    keeps = keep if isinstance(keep, Mapping) else {SOLE_POLICY: keep}
    full = matrix_fn(decision.snapshot, decision.legal, decision.columns)
    unpruned_value = float(solve_both(full).value)
    index = {a.get("message", ""): i for i, a in enumerate(decision.legal)}

    out: list[Measurement] = []
    for k in budgets:
        for name, keep_fn in keeps.items():
            kept_messages = [
                a.get("message", "") for a in keep_fn(decision.snapshot, decision.legal, k)
            ]
            kept_rows = [index[m] for m in kept_messages if m in index]
            if not kept_rows:
                continue
            out.append(
                Measurement(
                    battle_id=decision.battle_id,
                    viewpoint=decision.viewpoint,
                    turn=decision.turn,
                    k=k,
                    n_legal=len(decision.legal),
                    n_columns=len(decision.columns),
                    discarded_mass=discard_rate(full, kept_rows),
                    # Clamped at zero. The pruned game is a row subset of the
                    # full one, so its value cannot be higher; anything below
                    # zero is LP noise, and reporting it as a gain from pruning
                    # is nonsense.
                    value_loss=max(
                        0.0, unpruned_value - float(solve_both(full[kept_rows, :]).value)
                    ),
                    matches_trace=(
                        (set(kept_messages) == set(decision.kept))
                        if k == decision.k and _is_traced(name, decision)
                        else None
                    ),
                    policy=name,
                )
            )
    return out


def _is_traced(name: str, decision: Decision) -> bool:
    """Whether this policy is the one the trace recorded a kept set for.

    A trace from before providers named themselves carries no name, and there is
    only one policy it could have been, so it is still compared -- which is what
    the guard's first run did and what its 0 mismatches meant.
    """
    return not decision.traced_policy or decision.traced_policy == name


def measure_file(
    path: Path,
    matrix_fn: MatrixFn,
    keep: KeepFn | Mapping[str, KeepFn],
    k: int = DEFAULT_K,
) -> list[Measurement]:
    out = []
    for decision in decisions(read_events(path)):
        out.extend(measure_many(decision, matrix_fn, keep, (k,)))
    return out


def trace_files(root: Path) -> list[Path]:
    return sorted(Path(root).rglob("*.jsonl"))


# -- summarising -------------------------------------------------------------


def summarise(
    measurements: Sequence[Measurement],
    resamples: int = 1000,
    seed: int = 0,
) -> Summary:
    """The mean discarded mass with a 95% interval, resampling **battles**.

    The same argument `fit.bootstrap_weights` makes: positions inside one game
    share a board, a team and a policy, so resampling positions would report an
    interval far narrower than the evidence supports. A self-play trace
    directory holds both viewpoints of each game under one battle id, so
    grouping on it also stops the two halves of a game being counted as
    independent.
    """
    if not measurements:
        return Summary(
            k=0,
            policy="",
            n_positions=0,
            n_battles=0,
            mean=0.0,
            low=0.0,
            high=0.0,
            nonzero_fraction=0.0,
            mean_value_loss=0.0,
            max_value_loss=0.0,
            trace_mismatches=0,
        )

    mass = np.array([m.discarded_mass for m in measurements], dtype=float)
    losses = np.array([m.value_loss for m in measurements], dtype=float)
    battles = np.array([m.battle_id for m in measurements], dtype=object)
    unique = np.unique(battles)
    index = {battle: np.flatnonzero(battles == battle) for battle in unique}

    rng = np.random.default_rng(seed)
    draws = np.empty(resamples, dtype=float)
    for i in range(resamples):
        picked = rng.choice(unique, size=len(unique), replace=True)
        rows = np.concatenate([index[battle] for battle in picked])
        draws[i] = float(mass[rows].mean())
    low, high = (float(x) for x in np.percentile(draws, INTERVAL))

    return Summary(
        k=measurements[0].k,
        policy=measurements[0].policy,
        n_positions=len(measurements),
        n_battles=len(unique),
        mean=float(mass.mean()),
        low=low,
        high=high,
        nonzero_fraction=float((mass > 0).mean()),
        mean_value_loss=float(losses.mean()),
        max_value_loss=float(losses.max()),
        trace_mismatches=sum(1 for m in measurements if m.matches_trace is False),
        trace_checked=sum(1 for m in measurements if m.matches_trace is not None),
    )


def by_columns(measurements: Iterable[Measurement]) -> dict[int, list[Measurement]]:
    """Split by how many opponent columns the position had.

    A one-column position is an argmax rather than an equilibrium, and its
    discard rate answers a weaker question than a wide one does. Reporting the
    two together without saying which is which would let the easy half carry the
    number.
    """
    out: dict[int, list[Measurement]] = {}
    for measurement in measurements:
        out.setdefault(measurement.n_columns, []).append(measurement)
    return out


def by_policy(measurements: Iterable[Measurement]) -> dict[str, list[Measurement]]:
    """Split by candidate provider, which is what section 3 reports per.

    The counterpart of `by_columns`: the grouping the benchmark reads, over rows
    that came out of one solve of one position so the comparison is between the
    policies and not between two runs.
    """
    out: dict[str, list[Measurement]] = {}
    for measurement in measurements:
        out.setdefault(measurement.policy, []).append(measurement)
    return out

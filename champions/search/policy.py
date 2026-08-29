"""Candidate generation: reducing the joint action set to something solvable.

Roughly 156 joint actions a side means about 24,000 cells at one node, which is
2.5 times over the real clock budget before any belief sampling
(`docs/02-mechanics-deltas.md` section 7). Pruning to ten a side leaves 100
cells and about half a second. `docs/04-decision-engine.md` section 3 calls this
the highest leverage decision in the engine and says the right implementation is
an open empirical question, so it is an interface with more than one
implementation rather than a function.

This module has implementation A, the heuristic. B (a learned prior) and C (a
language model) arrive at M7 and are benchmarked against this one identically.

## The opponent's candidate set is the harder half

Our own actions come from the request, which already encodes Choice locks,
Encore, disabled moves, target legality and Mega availability -- reimplementing
that is a pure liability, so `_legal_orders` reads it rather than deriving it.

The opponent's actions cannot be read from anywhere. We know their revealed
moves and nothing else, so `opponent_candidates` enumerates what has been seen
and nothing more. On turn one that is an empty set, and the matrix degenerates
to a single column, and the equilibrium degenerates to an argmax against an
opponent modelled as doing nothing. That is a real weakness and it is the
honest one: the alternative is inventing moves the opponent has not shown, which
is guessing dressed as computation. M5's belief filter supplies the distribution
this is standing in for.

## The pruning guard

Pruning must never drop an action that is uniquely correct. Section 3 requires
measuring this offline by computing the unpruned equilibrium and recording how
often it puts non-trivial mass on a discarded action. `discard_rate` implements
that measurement; it is not called during play.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from champions.dex.loader import Dex
from champions.search.matrix import MASS_THRESHOLD, solve_both

#: A score no candidate can reach, so a disqualified action sorts below every
#: real one without competing with them on magnitude.
DISQUALIFIED = float("-inf")

#: Default candidate count per side. Section 7's budget arithmetic is written at
#: ten, and the local simulator turned out faster than the reference the figure
#: was computed against, so this has room to rise once M11 measures it.
DEFAULT_K = 10


class PolicyProvider(Protocol):
    """The candidate selection interface.

    `docs/08-implementation-blueprint.md` section 3 writes this as
    `candidates(state, belief, k)`, with enumeration as a separate interface
    (`actions.enumerate(request)`). Those two do not compose: a provider given
    only the state would have to enumerate the legal set itself, which means
    reimplementing Choice locks, Encore, disabled moves and target legality --
    the exact liability section 1 says to avoid by reading the request.

    So the enumerated legal set is passed in, and this interface selects from it
    rather than producing it. `state` and `belief` stay because implementations
    B and C need them: a learned prior scores actions in context, and the
    language model provider is shown the position. The heuristic uses neither,
    which is why its signature defaults them.
    """

    def candidates(
        self,
        actions: list[dict[str, Any]],
        state: dict[str, Any] | None,
        belief: Any,
        k: int,
    ) -> list[dict[str, Any]]: ...  # pragma: no cover


@dataclass(frozen=True)
class ScoredAction:
    """One candidate with the score that got it selected.

    The score is emitted onto the trace's `candidates` event so a reader can see
    not just what survived pruning but why, which is the difference between a
    debuggable decision and a list.
    """

    action: dict[str, Any]
    score: float
    reasons: tuple[str, ...]


class HeuristicPolicy:
    """Implementation A: cheap, deterministic, no corpus and no model.

    The bootstrap, available before anything is trained. It scores a joint
    action by summing per-slot scores, which ignores interactions between the
    two slots -- a double Protect and a Protect plus an attack score the same
    way -- and that is the known cost of being cheap. The payoff estimator sees
    the interaction; this only decides what the estimator looks at.
    """

    def __init__(self, dex: Dex) -> None:
        self._dex = dex

    def candidates(
        self,
        actions: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
        belief: Any = None,
        k: int = DEFAULT_K,
    ) -> list[dict[str, Any]]:
        return [scored.action for scored in self.scored(actions, k)]

    def scored(self, actions: list[dict[str, Any]], k: int = DEFAULT_K) -> list[ScoredAction]:
        """The top `k` joint actions by heuristic score, best first.

        Ties break on the action's protocol string, which is unique and stable,
        so the same position always produces the same candidate set in the same
        order (`CLAUDE.md`: deterministic by default).
        """
        ranked = sorted(
            (self._score(action) for action in actions),
            key=lambda scored: (-scored.score, scored.action["message"]),
        )
        return ranked[:k]

    def _score(self, action: dict[str, Any]) -> ScoredAction:
        total = 0.0
        reasons: list[str] = []
        for slot in action.get("slots", []):
            score, reason = self._score_slot(slot)
            if score == DISQUALIFIED:
                return ScoredAction(action=action, score=DISQUALIFIED, reasons=(reason,))
            total += score
            if reason:
                reasons.append(reason)
        return ScoredAction(action=action, score=total, reasons=tuple(reasons))

    def _score_slot(self, slot: dict[str, Any]) -> tuple[float, str]:
        kind = slot.get("kind")

        if kind == "switch":
            # Kept in the candidate set but ranked low: the one ply model scores
            # a switch as a lost turn (see `payoff._switch`), so a switch that
            # only pays off next turn cannot be seen from here. Ranking them out
            # entirely would make the agent unable to switch at all.
            return 0.5, "switch"

        if kind != "move":
            return 0.0, ""

        move_id = slot.get("move") or ""
        entry = self._dex.moves.get(move_id)
        if not entry:
            return 0.0, ""

        if entry["category"] == "Status":
            if move_id in PROTECT_LIKE:
                return 2.0, "protect"
            if move_id in SPEED_CONTROL:
                return 2.5, "speed control"
            if move_id in SETUP:
                return 1.5, "setup"
            return 1.0, "status"

        # A damaging move aimed at one of our own slots. Disqualified rather
        # than penalised, for the same reason `MaxBasePowerAgent` disqualifies
        # it: there is no base-power argument for ever doing this, and scoring
        # it on power alone makes it tie with the same move aimed at a foe.
        #
        # This matters far more here than it did there. An argmax loses one
        # action to the tie; pruning loses most of the candidate set, because
        # each move appears once per legal target and the ally-aimed copies
        # score identically. Measured on a real turn before this guard: nine of
        # the ten survivors were friendly fire, and the equilibrium was solving
        # a matrix almost entirely made of actions no one would ever play.
        power = float(entry.get("basePower") or 0)
        if power > 0 and int(slot.get("target", 0) or 0) < 0:
            return DISQUALIFIED, "friendly fire"

        # Base power is a poor proxy for damage and a good proxy for intent. The
        # payoff estimator computes the real number; this only has to get the
        # candidate into the matrix.
        score = 2.0 + power / 60.0
        if slot.get("priority", 0) > 0:
            score += 0.4
        return score, "attack"


#: Ranked above generic status moves because they are the interactions the
#: equilibrium exists to solve rather than value plays.
PROTECT_LIKE = {"protect", "detect", "spikyshield", "banefulbunker", "burningbulwark"}
SPEED_CONTROL = {"tailwind", "trickroom", "icywind", "electroweb", "thunderwave"}
SETUP = {"swordsdance", "nastyplot", "dragondance", "bulkup", "calmmind", "irondefense"}


def opponent_candidates(
    snapshot: dict[str, Any],
    dex: Dex,
    k: int = DEFAULT_K,
) -> list[dict[str, Any]]:
    """Joint actions the opponent might take, from what they have revealed.

    Built in the same described-action shape our own candidates use, so the
    payoff model does not need two code paths.

    Returns a single "no action" column when nothing has been revealed. That is
    a deliberately weak model and it is what makes the turn-one decision an
    argmax rather than an equilibrium; see the module docstring.
    """
    active = [p for p in snapshot["theirs"]["active"] if p is not None]
    per_slot: list[list[dict[str, Any]]] = []

    for slot_index, pokemon in enumerate(active):
        options: list[dict[str, Any]] = []
        for move in pokemon.get("revealed_moves", []):
            entry = dex.moves.get(move.get("id") or "")
            if not entry:
                continue
            options.append(
                {
                    "kind": "move",
                    "move": entry["id"],
                    "name": entry["name"],
                    "type": entry["type"],
                    "category": entry["category"],
                    "base_power": entry["basePower"],
                    "priority": entry.get("priority", 0),
                    # Their moves are aimed at our slots; the model reads a
                    # positive target as "the other side", which from their
                    # point of view is us.
                    "target": 1,
                    "label": entry["name"],
                }
            )
        if not options:
            options = [{"kind": "none", "label": "unrevealed"}]
        per_slot.append(options[:k])
        if slot_index >= 1:
            break

    if not per_slot:
        return [_joint([])]

    joint: list[dict[str, Any]] = []
    first = per_slot[0]
    second = per_slot[1] if len(per_slot) > 1 else [None]
    for a in first:
        for b in second:
            joint.append(_joint([s for s in (a, b) if s is not None]))
    return joint[:k]


def _joint(slots: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "message": " + ".join(s.get("label", s.get("kind", "?")) for s in slots) or "no action",
        "slots": slots,
        "label": " + ".join(s.get("label", "?") for s in slots) or "no action",
        "kinds": sorted({str(s.get("kind")) for s in slots}),
    }


def discard_rate(
    full_payoff: np.ndarray,
    kept_rows: list[int],
) -> float:
    """How much equilibrium mass the pruning threw away.

    The guard `docs/04-decision-engine.md` section 3 requires: solve the
    unpruned game, then measure the probability the unpruned equilibrium places
    on rows the policy discarded. Zero means the pruning was free on this
    position. This is an offline measurement -- it needs the full matrix, which
    is the thing pruning exists to avoid computing -- and is reported per policy
    implementation in the M7 benchmark.
    """
    equilibrium = solve_both(full_payoff)
    kept = set(kept_rows)
    return float(
        sum(
            probability
            for row, probability in enumerate(equilibrium.row)
            if row not in kept and probability > MASS_THRESHOLD
        )
    )

"""One more ply on the analytic model: the depth arm of the M8 engine gate.

`champions/search/payoff.py` resolves one turn and hands the result to
`win_prob`. This module keeps that resolver and replaces the leaf: each
resulting position is itself pruned, estimated and solved as a one-ply matrix
game, and the equilibrium value of that game is what the root cell is worth.
`docs/specs/2026-09-13-engine-gate.md` section 5.1 specifies it; D70 fixes the
rule it is measured under.

## What a second ply sees that the first cannot

Two things, and both are the point.

**The turn after a switch.** The one-ply model scores a switch as giving up the
turn, because the incoming Pokemon's value is a next-turn question it cannot
ask (`payoff.TurnModel._switch`). Here the switch places the incoming Pokemon
on the field, the opponent's move resolves against it, and the second ply acts
with it. That is the switch bias `docs/STATUS.md` names as the clearest thing
depth would fix, so the depth gap in the gate *is* the measurement of what the
bias costs.

**The turn after a knockout.** A knockout this turn is worth what it lets the
survivor do next turn, and the one-ply leaf reads only the HP and the count.

## What it still does not see

Everything the analytic model does not model -- items, abilities, secondary
effects, status effects, accuracy -- is absent at both plies. That is the
design: the gate isolates depth from fidelity, and the simulator arm
(`champions/search/rollout.py`) isolates fidelity from depth.

## Interior enumeration is approximate

`docs/04-decision-engine.md` section 1 enumerates the root from the request
because reimplementing legality is a liability. An interior node has no
request, so `enumerate_joint` builds our options from the position: each
active slot's known moves against each legal target for the move's target
type, plus switches to living brought bench Pokemon, minus pairs that switch
into the same Pokemon. It does not know about Choice locks, Encore, Disable,
trapping or Mega availability, and it treats any move with PP as usable. The
described-action shape is `champions.protocol.actions.describe`'s, field for
field, so `HeuristicPolicy` and `TurnModel` consume child actions unchanged.

## What happens between the plies

A position the first ply leaves behind is not yet a position the second ply
can act in. `next_turn` makes it one: a fainted slot on our side is refilled
with the first living brought Pokemon on the bench (a policy assumption,
stated as one -- the real game would ask), the opponent's fainted slot stays
empty because which Pokemon comes in is unobservable and `alive()` already
counts by faints, `first_turn` is true for whatever came in this turn and
false otherwise, and `protect_counter` rises for a slot that protected and
resets for one that did not.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from champions.dex.loader import Dex
from champions.protocol.actions import TARGET_LABELS
from champions.search.evaluate import alive, win_prob
from champions.search.matrix import solve_both
from champions.search.payoff import (
    EffectsProvider,
    OpponentHypothesis,
    TurnModel,
    payoff_matrix,
)
from champions.search.policy import HeuristicPolicy, PolicyProvider, opponent_candidates

#: The second ply's candidate budget per side. Smaller than the root's `k`
#: because every root cell pays for a whole child matrix: at `k = 10` and
#: `k2 = 6` with two branches a decision is 7,200 turn-model cells, about a
#: hundred times the one-ply decision M2 measured at 11 ms.
DEFAULT_K2 = 6

#: Showdown target types, grouped by what the player chooses. Everything not
#: listed takes no target choice (spread moves, self-targeting moves, field
#: moves), which the protocol encodes as target 0.
FOE_TARGETS = {"normal", "any", "adjacentFoe"}
ALLY_TARGETS = {"adjacentAlly"}
ALLY_OR_SELF_TARGETS = {"adjacentAllyOrSelf"}

BelievedMoves = Callable[[str], list[str]] | None


@dataclass
class TwoPlyStats:
    """What one decision cost, for the trace."""

    children_solved: int = 0
    memo_hits: int = 0
    leaves: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "children_solved": self.children_solved,
            "memo_hits": self.memo_hits,
            "leaves": self.leaves,
        }


class TwoPlyModel:
    """Values a root cell by solving the one-ply game at every position it reaches.

    Owns its `TurnModel`, built with `place_incoming=True`, because the second
    ply is worthless if the first leaves switched-in Pokemon off the field.
    The same providers the root uses -- `HeuristicPolicy` for our rows and
    `opponent_candidates` for their columns -- prune the child at `k2`.

    `value` has `TurnModel.value`'s signature so `payoff_matrix` accepts
    either; `row` exists so an agent can await between rows and stay
    interruptible. Call `reset` once per decision: children are memoised on
    the canonical position, and many root cells reach the same one.
    """

    def __init__(
        self,
        dex: Dex,
        hypothesis: OpponentHypothesis | None = None,
        effects: EffectsProvider | None = None,
        policy: PolicyProvider | None = None,
        k2: int = DEFAULT_K2,
        believed_moves: BelievedMoves = None,
        picked_team_size: int | None = None,
    ) -> None:
        self._dex = dex
        self._turn = TurnModel(
            dex,
            hypothesis,
            picked_team_size=picked_team_size,
            effects=effects,
            place_incoming=True,
        )
        self._policy = policy if policy is not None else HeuristicPolicy(dex)
        self._k2 = k2
        self._believed_moves = believed_moves
        self._picked = picked_team_size or dex.picked_team_size
        self._memo: dict[str, float] = {}
        self.stats = TwoPlyStats()

    @property
    def k2(self) -> int:
        return self._k2

    @property
    def turn_model(self) -> TurnModel:
        return self._turn

    def reset(self) -> None:
        self._memo.clear()
        self.stats = TwoPlyStats()

    # -- the cell --------------------------------------------------------

    def value(
        self,
        snapshot: dict[str, Any],
        our_action: dict[str, Any],
        their_action: dict[str, Any],
    ) -> float:
        """Expected value of one root cell, one ply deeper than `TurnModel.value`."""
        total = 0.0
        for outcome in self._turn.outcomes(snapshot, our_action, their_action):
            total += outcome.probability * self.child_value(outcome.snapshot)
        return total

    def row(
        self,
        snapshot: dict[str, Any],
        our_action: dict[str, Any],
        their_actions: list[dict[str, Any]],
    ) -> list[float]:
        return [self.value(snapshot, our_action, theirs) for theirs in their_actions]

    def matrix(
        self,
        snapshot: dict[str, Any],
        our_actions: list[dict[str, Any]],
        their_actions: list[dict[str, Any]],
    ) -> np.ndarray:
        return np.array(
            [self.row(snapshot, ours, their_actions) for ours in our_actions], dtype=float
        ).reshape(len(our_actions), len(their_actions))

    # -- the child -------------------------------------------------------

    def child_value(self, state: dict[str, Any]) -> float:
        """The value of the position one turn's resolution leaves behind."""
        key = _key(state)
        hit = self._memo.get(key)
        if hit is not None:
            self.stats.memo_hits += 1
            return hit

        child = next_turn(state)
        value = self._solve(child)
        self._memo[key] = value
        return value

    def _solve(self, child: dict[str, Any]) -> float:
        if is_terminal(child, self._picked):
            self.stats.leaves += 1
            return win_prob(child, self._picked)

        ours = enumerate_joint(child, self._dex)
        if not ours:
            self.stats.leaves += 1
            return win_prob(child, self._picked)

        scored = self._policy.scored(ours, self._k2, child)
        theirs = opponent_candidates(
            child, self._dex, self._k2, believed_moves=self._believed_moves
        )
        matrix = payoff_matrix(child, [s.action for s in scored], theirs, self._turn)
        self.stats.children_solved += 1
        return float(solve_both(matrix).value)


# -- between the plies -----------------------------------------------------


def is_terminal(state: dict[str, Any], picked_team_size: int) -> bool:
    """Whether the game is decided, which `win_prob` scores as 0 or 1."""
    return (
        alive(state["ours"], picked_team_size, known=True) == 0
        or alive(state["theirs"], picked_team_size, known=False) == 0
    )


def next_turn(state: dict[str, Any]) -> dict[str, Any]:
    """The position the second ply acts in, from the one the first left behind.

    Returns a new dict; the input is not modified. See the module docstring
    for what each step assumes.
    """
    child = copy.deepcopy(state)
    child["turn"] = int(child.get("turn") or 0) + 1

    ours = child["ours"]
    for index, view in enumerate(ours["active"]):
        if view is None or view.get("fainted"):
            replacement = _first_living(ours["bench"])
            if replacement is None:
                continue
            ours["bench"] = [p for p in ours["bench"] if p is not replacement]
            if view is not None:
                ours["bench"] = [*ours["bench"], view]
            ours["active"][index] = {**replacement, "_placed": True}

    for side in ("ours", "theirs"):
        active = child[side]["active"]
        for index, view in enumerate(active):
            if view is None:
                continue
            placed = bool(view.pop("_placed", False))
            protected = bool(view.pop("_protected", False))
            view["first_turn"] = placed
            view["protect_counter"] = int(view.get("protect_counter") or 0) + 1 if protected else 0
            active[index] = view
    return child


def _first_living(bench: list[dict[str, Any]]) -> dict[str, Any] | None:
    for view in bench:
        if not view.get("fainted") and view.get("selected", True):
            return view
    return None


# -- interior enumeration ----------------------------------------------------


def enumerate_joint(state: dict[str, Any], dex: Dex) -> list[dict[str, Any]]:
    """Our legal-enough joint actions in a position with no request. See the
    module docstring for what "enough" leaves out."""
    ours = state["ours"]
    our_active = list(ours["active"])
    foes = [
        i for i, p in enumerate(state["theirs"]["active"]) if p is not None and not p["fainted"]
    ]
    bench = [
        (i, p)
        for i, p in enumerate(ours["bench"])
        if not p.get("fainted") and p.get("selected", True)
    ]

    per_slot: list[list[dict[str, Any]]] = []
    for index, view in enumerate(our_active):
        options: list[dict[str, Any]] = []
        if view is None or view.get("fainted"):
            options.append({"kind": "pass", "label": "pass", "_choice": "pass"})
        else:
            for move in view.get("moves") or []:
                entry = dex.moves.get(move.get("id") or "")
                if not entry or move.get("pp") == 0:
                    continue
                for target in _targets_for(entry, index, our_active, foes):
                    options.append(_move_option(entry, target))
            for bench_index, pokemon in bench:
                options.append(_switch_option(pokemon, bench_index))
        per_slot.append(options)

    if not per_slot:
        return []
    if len(per_slot) == 1:
        return [_joint([a]) for a in per_slot[0] if a["kind"] != "pass"]

    joint: list[dict[str, Any]] = []
    for a in per_slot[0]:
        for b in per_slot[1]:
            if a["kind"] == "pass" and b["kind"] == "pass":
                continue
            if (
                a["kind"] == "switch"
                and b["kind"] == "switch"
                and a["bench_index"] == b["bench_index"]
            ):
                continue
            joint.append(_joint([a, b]))
    return joint


def _targets_for(
    entry: dict[str, Any],
    index: int,
    our_active: list[dict[str, Any] | None],
    foes: list[int],
) -> list[int]:
    target_type = str(entry.get("target") or "normal")
    if target_type in FOE_TARGETS:
        return [foe + 1 for foe in foes]
    if target_type in ALLY_TARGETS:
        partner = 1 - index
        if 0 <= partner < len(our_active):
            view = our_active[partner]
            if view is not None and not view.get("fainted"):
                return [-(partner + 1)]
        return []
    if target_type in ALLY_OR_SELF_TARGETS:
        return [-(index + 1)]
    return [0]


def _move_option(entry: dict[str, Any], target: int) -> dict[str, Any]:
    described = {
        "kind": "move",
        "move": entry["id"],
        "name": entry.get("name", entry["id"]),
        "type": entry.get("type"),
        "category": entry.get("category"),
        "base_power": entry.get("basePower"),
        "priority": entry.get("priority", 0),
        "move_target": entry.get("target"),
        "target": target,
        "target_label": TARGET_LABELS.get(target, f"slot {target}"),
        "mega": False,
        "_choice": f"move {entry['id']} {target}" if target else f"move {entry['id']}",
    }
    label = str(described["name"])
    if target:
        label += f" -> {described['target_label']}"
    described["label"] = label
    return described


def _switch_option(pokemon: dict[str, Any], bench_index: int) -> dict[str, Any]:
    return {
        "kind": "switch",
        "species": pokemon["species"],
        "name": pokemon.get("name") or pokemon["species"],
        "label": f"switch to {pokemon['species']}",
        "bench_index": bench_index,
        "_choice": f"switch {pokemon.get('name') or pokemon['species']}",
    }


def _joint(slots: list[dict[str, Any]]) -> dict[str, Any]:
    """`actions.describe`'s shape. The message is poke-env's wire form, which is
    unique per joint action and is what `HeuristicPolicy` breaks ties on."""
    cleaned = [{k: v for k, v in s.items() if k != "_choice"} for s in slots]
    return {
        "message": "/choose " + ", ".join(s["_choice"] for s in slots),
        "slots": cleaned,
        "label": " + ".join(s["label"] for s in cleaned),
        "kinds": sorted({str(s["kind"]) for s in cleaned}),
    }


def _key(state: dict[str, Any]) -> str:
    """A canonical spelling of a position, for the memo."""
    return json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)

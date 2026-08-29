"""The evaluation function: a position's value as a win probability.

One ply search cannot reach terminal states, so every leaf needs a number. The
requirements, in the order `docs/04-decision-engine.md` section 5 gives them:
calibrated as a probability rather than merely monotone, cheap because it sits
in the innermost loop, and eventually trained on replay outcomes rather than
hand tuned.

This is the bootstrap version and it meets exactly one of those three. It is
cheap. It is **not calibrated**, and `IS_CALIBRATED` says so in a way callers
can check, because the coach's ex-ante loss and the eval bar both interpret this
number as a probability and would report confident nonsense if handed an
uncalibrated one. M6 replaces the weights with a fit model and a reliability
diagram; the interface and the features do not change.

It reads the trace snapshot (`champions.protocol.state.snapshot`) rather than a
poke-env battle. That decouples it from the transport, and it means the coach
can recompute any historical evaluation from the trace file alone, which is what
makes a stochastic agent debuggable after the fact.

The asymmetry that matters: our side is known exactly and the opponent's is not.
Opponent HP arrives quantized to percent, and Pokemon they have not yet revealed
are alive but invisible. Counting only revealed Pokemon would score a fresh
opponent as nearly dead, so the surviving count is derived from the format's
picked team size minus observed faints, which is exact because faints are always
announced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Whether `win_prob` may be read as a probability. False until M6 fits the
#: weights to replay outcomes and produces a reliability diagram. The coach
#: checks this before reporting an ex-ante loss in probability units.
IS_CALIBRATED = False

#: Feature weights, in log odds. Hand chosen, not fit -- the ordering they
#: encode is uncontroversial (being ahead on Pokemon is worth more than being
#: ahead on HP, which is worth more than field control) but the magnitudes are
#: guesses, which is the whole reason `IS_CALIBRATED` is False.
WEIGHTS: dict[str, float] = {
    "pokemon_advantage": 1.10,
    "hp_advantage": 1.60,
    "active_hp_advantage": 0.55,
    "status_advantage": 0.30,
    "boost_advantage": 0.18,
    "speed_control": 0.35,
    "hazard_advantage": 0.12,
}

#: Statuses weighted by how much of a Pokemon they take out of the game. Sleep
#: and freeze remove turns outright; the rest are attrition. These are the
#: Champions numbers, not mainline: paralysis is 1/8 full-paralysis rather than
#: 1/4, and sleep is 2-3 turns rather than 1-4 (`docs/02-mechanics-deltas.md`).
STATUS_COST: dict[str, float] = {
    "SLP": 0.55,
    "FRZ": 0.50,
    "PAR": 0.22,
    "TOX": 0.30,
    "BRN": 0.25,
    "PSN": 0.15,
}

#: Tailwind is the only speed control this scores. Trick Room is deliberately
#: absent: it is a field effect that helps whichever side is slower, so its value
#: is an interaction between the field and the two teams' speeds rather than an
#: advantage to whoever set it. A linear model over side-differences cannot
#: express that, and giving it a fixed sign would be worse than omitting it. M6
#: can learn the interaction; until then the omission is the honest option.
SPEED_CONTROL_SIDE_CONDITIONS = {"TAILWIND"}
HAZARDS = {"STEALTH_ROCK", "SPIKES", "TOXIC_SPIKES", "STICKY_WEB"}


@dataclass(frozen=True)
class Evaluation:
    """A position's value, with the features that produced it.

    The features come back alongside the number because the coach needs to say
    *why* a position was scored the way it was, and recomputing them at report
    time would let the explanation drift from the decision.
    """

    win_prob: float
    log_odds: float
    features: dict[str, float]
    calibrated: bool = IS_CALIBRATED


def _alive(side: dict[str, Any], picked_team_size: int, known: bool) -> int:
    """How many Pokemon this side still has.

    For our own side the snapshot is complete and `remaining` is the answer. For
    the opponent it counts only what has been revealed, so an opponent who has
    shown two Pokemon and lost neither would score as having two rather than
    four. Faints are always announced, so picked size minus observed faints is
    exact regardless of how much has been revealed.
    """
    if known:
        return int(side["remaining"])
    seen = [p for p in side["active"] if p is not None] + list(side["bench"])
    fainted = sum(1 for p in seen if p["fainted"])
    return max(0, picked_team_size - fainted)


def _hp_total(side: dict[str, Any], picked_team_size: int, known: bool) -> float:
    """Total HP across the side, in Pokemon-equivalents.

    HP fraction is the common currency because it is the only one both sides
    speak: opponent HP arrives quantized to percent and their maximum is never
    known. Unrevealed Pokemon are counted at full health, which is what they are.
    """
    seen = [p for p in side["active"] if p is not None] + list(side["bench"])
    total = sum(0.0 if p["fainted"] else p["hp_pct"] / 100.0 for p in seen)
    if not known:
        unrevealed = max(0, picked_team_size - len(seen))
        total += float(unrevealed)
    return total


def _active_hp(side: dict[str, Any]) -> float:
    return sum(0.0 if p["fainted"] else p["hp_pct"] / 100.0 for p in side["active"] if p)


def _status_cost(side: dict[str, Any]) -> float:
    seen = [p for p in side["active"] if p is not None] + list(side["bench"])
    return sum(STATUS_COST.get(p["status"] or "", 0.0) for p in seen if not p["fainted"])


def _boost_total(side: dict[str, Any]) -> float:
    """Net boost stages on the field, offence and defence alike.

    Only active Pokemon: boosts are cleared on switch out, so a benched
    Pokemon's recorded boosts are stale.
    """
    total = 0
    for pokemon in side["active"]:
        if pokemon and not pokemon["fainted"]:
            total += sum(pokemon["boosts"].values())
    return float(total)


def _speed_control(conditions: dict[str, Any]) -> float:
    return float(any(name in SPEED_CONTROL_SIDE_CONDITIONS for name in conditions))


def _hazard_count(conditions: dict[str, Any]) -> float:
    return float(sum(v for name, v in conditions.items() if name in HAZARDS))


def features(snapshot: dict[str, Any], picked_team_size: int = 4) -> dict[str, float]:
    """The feature vector for a position, from this agent's point of view.

    Every feature is a difference (ours minus theirs), so between two sides that
    are equally revealed the evaluation is antisymmetric by construction: a
    position and its mirror sum to a log odds of zero and therefore to win
    probabilities summing to one. That is what lets the matrix game treat the
    payoff as zero sum without a correction, and building it in beats hoping
    fitted weights discover it at M6.

    It is deliberately *not* antisymmetric when the two sides are revealed
    unequally, which in a real battle is always. We see our own team exactly and
    theirs only as revealed, so mirroring the snapshot swaps the information as
    well as the position and asks a different question. This is not a defect to
    be corrected: the agent evaluates every cell from its own single information
    state, so the zero sum assumption the matrix game needs holds within a
    decision, which is where it is used.
    """
    ours, theirs = snapshot["ours"], snapshot["theirs"]

    our_alive = _alive(ours, picked_team_size, known=True)
    their_alive = _alive(theirs, picked_team_size, known=False)

    tailwind = _speed_control(snapshot["side_conditions"]) - _speed_control(
        snapshot["opponent_side_conditions"]
    )

    return {
        "pokemon_advantage": float(our_alive - their_alive),
        "hp_advantage": _hp_total(ours, picked_team_size, True)
        - _hp_total(theirs, picked_team_size, False),
        "active_hp_advantage": _active_hp(ours) - _active_hp(theirs),
        # Their status is our advantage, so this is negated relative to the rest.
        "status_advantage": _status_cost(theirs) - _status_cost(ours),
        "boost_advantage": _boost_total(ours) - _boost_total(theirs),
        "speed_control": tailwind,
        "hazard_advantage": _hazard_count(snapshot["opponent_side_conditions"])
        - _hazard_count(snapshot["side_conditions"]),
        # A side with nothing left has lost; this is what makes the terminal
        # positions saturate rather than merely score well.
        "faint_swing": (0.0 if our_alive else -1.0) + (0.0 if their_alive else 1.0),
    }


def evaluate(snapshot: dict[str, Any], picked_team_size: int = 4) -> Evaluation:
    """A position's value, with its features."""
    vector = features(snapshot, picked_team_size)

    if vector["faint_swing"] > 0:
        return Evaluation(1.0, math.inf, vector)
    if vector["faint_swing"] < 0:
        return Evaluation(0.0, -math.inf, vector)

    # `faint_swing` is deliberately absent from WEIGHTS: it is handled by the
    # short circuit above rather than by a weight, because a wiped side is a
    # decided game and not a very large advantage.
    log_odds = sum(WEIGHTS.get(name, 0.0) * value for name, value in vector.items())
    return Evaluation(_sigmoid(log_odds), log_odds, vector)


def win_prob(snapshot: dict[str, Any], picked_team_size: int = 4) -> float:
    """The interface `docs/08-implementation-blueprint.md` section 3 names.

    Not calibrated until M6. See `IS_CALIBRATED`.
    """
    return evaluate(snapshot, picked_team_size).win_prob


def _sigmoid(x: float) -> float:
    # Written in the numerically stable branches rather than as 1/(1+exp(-x)),
    # because a decisive position produces a large magnitude log odds and the
    # naive form overflows there -- on exactly the positions the search cares
    # most about getting right.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exponent = math.exp(x)
    return exponent / (1.0 + exponent)

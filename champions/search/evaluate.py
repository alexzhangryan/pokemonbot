"""The evaluation function: a position's value as a win probability.

One ply search cannot reach terminal states, so every leaf needs a number. The
requirements, in the order `docs/04-decision-engine.md` section 5 gives them:
calibrated as a probability rather than merely monotone, cheap because it sits
in the innermost loop, and eventually trained on replay outcomes rather than
hand tuned.

Which of the three it meets depends on whether M6 has been run here. It is
always cheap. The weights are fit and the number is a calibrated probability
when `data/eval/weights.<format>.json` exists, which is written by
`scripts/fit_eval.py` in the same run that writes `docs/eval-calibration.md`;
without that file it falls back to `BOOTSTRAP_WEIGHTS` and `IS_CALIBRATED` is
False. There is no way to claim calibration without having measured it, which
matters because the coach's ex-ante loss and the eval bar both read this number
as a probability and would report confident nonsense given an uncalibrated one.

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

The symmetry that also matters, and was missing until M6 measured it: only the
Pokemon that were *brought* count on either side. Reg M-B registers six and
brings four, and poke-env keeps all six in `battle.team` for the whole game, so
our side was being counted as six against an opponent who could only be counted
as four. Turn 1 of a dead-even position scored 0.996. Nothing in the output said
so, and fitting weights over the broken features would have absorbed it into an
intercept rather than revealed it. See `_in_play`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "eval"

#: Feature weights, in log odds. These are the fallback: hand chosen, not fit.
#: The ordering they encode is uncontroversial (being ahead on Pokemon is worth
#: more than being ahead on HP, which is worth more than field control) but the
#: magnitudes are guesses, which is why a position scored with them is not a
#: probability. `load_weights` replaces them with the M6 fit when it is present.
BOOTSTRAP_WEIGHTS: dict[str, float] = {
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


def format_key(format_id: str) -> str:
    """A format id as it appears in a weights filename."""
    return format_id.lower()


def WEIGHTS_PATH(format_id: str) -> Path:  # noqa: N802 - a path, named like one
    return DATA_DIR / f"weights.{format_key(format_id)}.json"


@dataclass(frozen=True)
class Model:
    """What `evaluate` scores with: weights, and whether they were fit.

    `calibrated` is not a setting. It is True exactly when the weights came from
    a file that `scripts/fit_eval.py` wrote, which is the same run that wrote the
    reliability diagram `docs/04-decision-engine.md` section 5 requires. There is
    deliberately no way to assert calibration without having measured it.
    """

    weights: dict[str, float]
    platt_a: float = 1.0
    platt_b: float = 0.0
    calibrated: bool = False
    source: str | None = None
    fitted_at: str | None = None

    def log_odds(self, vector: dict[str, float]) -> float:
        raw = sum(self.weights.get(name, 0.0) * value for name, value in vector.items())
        return self.platt_a * raw + self.platt_b


def load_model(format_id: str = "gen9championsvgc2026regmb") -> Model:
    """The fitted model if M6 has been run, the hand-written one otherwise.

    Missing weights are not an error. The agent has to run before the fit does:
    `scripts/fit_eval.py` reads self-play traces, which means a game has to be
    played with the bootstrap weights in order to produce the data the fit needs.
    So the fallback is the bootstrap, loudly uncalibrated.
    """
    path = WEIGHTS_PATH(format_id)
    if not path.is_file():
        return Model(weights=dict(BOOTSTRAP_WEIGHTS), calibrated=False, source="bootstrap")
    payload = json.loads(path.read_text(encoding="utf-8"))
    platt = payload.get("platt", {})
    return Model(
        weights={str(k): float(v) for k, v in payload["weights"].items()},
        platt_a=float(platt.get("a", 1.0)),
        platt_b=float(platt.get("b", 0.0)),
        calibrated=True,
        source=payload.get("source"),
        fitted_at=payload.get("fitted_at"),
    )


MODEL = load_model()

#: The weights actually in use. Kept as a module-level name because the viewer,
#: the coach and several tests read it.
WEIGHTS: dict[str, float] = MODEL.weights

#: Whether `win_prob` may be read as a probability. True once M6's fit is on
#: disk, because that is the run that produced the reliability diagram. The
#: coach checks this before reporting an ex-ante loss in probability units.
IS_CALIBRATED = MODEL.calibrated


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
    #: Whether this number may be read as a probability. Carried on the value
    #: rather than looked up beside it, so a stored evaluation still says what
    #: it was worth when it was made.
    calibrated: bool = False


def _in_play(side: dict[str, Any]) -> list[dict[str, Any]]:
    """The Pokemon on this side that can actually take part in the battle.

    Reg M-B registers six and brings four, and poke-env's `battle.team` holds
    all six for the whole game. Counting the two that were left behind is not a
    small error: it made our side worth six Pokemon against an opponent that can
    only ever be counted as four, which scored turn 1 of a dead-even position at
    a win probability of 0.996 before a single move was chosen.

    `selected` is recorded by `champions/protocol/state.py` for our side only --
    the opponent's bring is not observable, and does not need to be, because
    their count is derived from faints instead. A snapshot written before that
    field existed carries no `selected` on any Pokemon, and is passed through
    unfiltered rather than silently dropping the whole side; `docs/07` and
    `champions/search/positions.py` treat such a trace as unfit for fitting.
    """
    seen = [p for p in side["active"] if p is not None] + list(side["bench"])
    brought = [p for p in seen if p.get("selected")]
    return brought or seen


def _alive(side: dict[str, Any], picked_team_size: int, known: bool) -> int:
    """How many Pokemon this side still has.

    Our own side is counted, because we can see it. The opponent's is derived as
    the bring minus their observed faints, because we cannot: an opponent who has
    shown two Pokemon and lost neither must not score as having two. Faints are
    always announced, so the derivation is exact however little has been revealed.

    Both branches count over `_in_play`, and that is the fix. `remaining` on our
    side counts the registered six.
    """
    in_play = _in_play(side)
    if known:
        return sum(1 for p in in_play if not p["fainted"])
    return max(0, picked_team_size - sum(1 for p in in_play if p["fainted"]))


def _hp_total(side: dict[str, Any], picked_team_size: int, known: bool) -> float:
    """Total HP across the side, in Pokemon-equivalents.

    HP fraction is the common currency because it is the only one both sides
    speak: opponent HP arrives quantized to percent and their maximum is never
    known. Unrevealed Pokemon are counted at full health, which is what they are.
    """
    in_play = _in_play(side)
    total = sum(0.0 if p["fainted"] else p["hp_pct"] / 100.0 for p in in_play)
    if not known:
        unrevealed = max(0, picked_team_size - len(in_play))
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

    # `faint_swing` is deliberately absent from the weights: it is handled by
    # the short circuit above rather than by a weight, because a wiped side is a
    # decided game and not a very large advantage.
    log_odds = MODEL.log_odds(vector)
    return Evaluation(_sigmoid(log_odds), log_odds, vector, MODEL.calibrated)


def win_prob(snapshot: dict[str, Any], picked_team_size: int = 4) -> float:
    """The interface `docs/08-implementation-blueprint.md` section 3 names.

    Readable as a probability only when `IS_CALIBRATED`; see `load_model`.
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

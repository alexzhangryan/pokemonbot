"""One (state, option) pair as numbers, for both the trainer and the agent.

`docs/specs/2026-08-29-learned-policy-provider.md` section 3.2 asks for one
function, used by the fit and by the live provider. One function on purpose: M6
built `positions.py` for the same reason, because two paths to the same features
is how a model quietly stops being served the inputs it was fit on, and the
symptom is a good offline number and a bad live one with nothing in between to
read.

`champions.corpus.replay_state` emits snapshot-shaped dicts and
`champions.protocol.state` emits snapshots, so the trainer and the agent call
this on the same shape. `tests/test_policy_features.py` checks the resulting
vectors are equal over real traces rather than assuming it.

## The information the vector deliberately throws away

A live snapshot carries our exact stat spread. A replay carries a percentage and
nothing else, because a spectator stream does not contain stat points. There is
therefore no vector that both uses the real spread and matches between the two,
and the choice is which side to lose on:

- Fit and serve on the *hypothesis* spread that the opponent already gets. The
  agent knows more than it uses, and the model is served what it was fit on.
- Use the real spread when it is there. The agent uses what it knows, and every
  damage-derived feature is quietly a different quantity in play than in
  training.

The first, so `board_for` builds the board with `exact_stats=False`. This is not
a small loss and it is why it is stated here: the damage numbers in this vector
are approximations for both sides, where `HeuristicPolicy`'s are exact for ours.
The measurement that decides whether it mattered is the pruning guard, not this
docstring.

## What it does not see

Slot interaction, and belief. A joint action's two halves are vectorised
separately and their scores summed, exactly as in implementation A, so a double
Protect and a Protect plus an attack are indistinguishable here. Both are out of
scope for the same reason they are out of scope in A: they are separate changes
with their own measurements (spec section 6).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from champions.dex.damage import TypeChart
from champions.dex.loader import Dex
from champions.search.evaluate import STATUS_COST, alive
from champions.search.payoff import SPREAD_TARGETS, OpponentHypothesis, effective_speed
from champions.search.policy import Board

#: How many Pokemon a side brings, so that "how many are left" is a fraction of
#: the same denominator for both sides. Reg M-B registers six and plays four,
#: and counting our six against their four is the error M6 found in the
#: evaluation (`champions/search/evaluate.py`).
PICKED_TEAM_SIZE = 4

#: The weather the features distinguish. Champions keeps mainline's set; these
#: are the four that a Reg M-B team can actually set, in the spelling both
#: `state.snapshot` (poke-env's enum names) and `replay_state` (the protocol's
#: own, upper-cased) produce. The two agreeing is not luck -- poke-env names its
#: enum members after the protocol -- but it is load bearing, so it is asserted
#: by the agreement test rather than trusted.
WEATHER = {
    "weather_sun": ("SUNNYDAY", "DESOLATELAND"),
    "weather_rain": ("RAINDANCE", "PRIMORDIALSEA"),
    "weather_sand": ("SANDSTORM",),
    "weather_snow": ("SNOWSCAPE", "HAIL"),
}

#: Boosts, grouped into what they do rather than kept as six numbers. A learned
#: prior over 34 features and a few thousand battles does not have the data to
#: separate an Attack boost from a Special Attack one on a Pokemon that only
#: uses one of them, and the grouping is the same one the evaluation's
#: `boost_advantage` already makes.
OFFENSE_BOOSTS = ("atk", "spa")
DEFENSE_BOOSTS = ("def", "spd")

#: Turn index, scaled so that the difference between turn 1 and turn 5 is large
#: and the difference between turn 30 and turn 34 is not. Games run to about 20
#: turns and the early ones are where the position is least resolved.
TURN_SCALE = 10.0

#: Base power, divided by roughly the largest a Reg M-B move reaches, so the
#: feature lands in the same range as the fractions beside it. Not clipped: a
#: value above one is a real move and squashing it would make Explosion look
#: like Flare Blitz.
POWER_SCALE = 120.0

#: Priority runs -7 to +5 and is almost always 0, +1 or +4.
PRIORITY_SCALE = 4.0

#: Boost stages run -6 to +6.
BOOST_SCALE = 6.0

#: What an immunity reports on the effectiveness stage. Below the lowest real
#: stage a Reg M-B move reaches, so that "does nothing" is separable from "is
#: resisted twice" rather than being the same number.
IMMUNE_STAGE = -3.0

FEATURE_NAMES: tuple[str, ...] = (
    # the option
    "is_move",
    "is_switch",
    "is_physical",
    "is_special",
    "is_status_move",
    "base_power",
    "priority",
    "is_spread",
    "effectiveness",
    "target_is_ally",
    # the Pokemon that acts
    "actor_hp",
    "actor_boost_offense",
    "actor_boost_defense",
    "actor_boost_speed",
    "actor_status",
    "actor_first_turn",
    # what it is aimed at
    "target_hp",
    "target_status",
    "damage_fraction",
    "target_ko",
    "ally_damage_fraction",
    # the board
    "turn",
    "our_remaining",
    "their_remaining",
    "weather_sun",
    "weather_rain",
    "weather_sand",
    "weather_snow",
    "trick_room",
    "our_tailwind",
    "their_tailwind",
    "outspeeds_foe_a",
    "outspeeds_foe_b",
    # the switch
    "switch_incoming_hp",
)

FEATURE_INDEX = {name: i for i, name in enumerate(FEATURE_NAMES)}


def board_for(
    snapshot: dict[str, Any],
    dex: Dex,
    chart: TypeChart | None = None,
    hypothesis: OpponentHypothesis | None = None,
) -> Board:
    """The board this vector is read off, on the terms the model is fit on.

    Built once per position and passed to every option, because the joint action
    set repeats the same handful of per-slot moves across its rows and the roll
    distribution behind `damage_fraction` would otherwise be computed a hundred
    times a turn. `Board` caches per (slot, move, target); this only has to be
    the thing that is reused.
    """
    return Board.read(
        snapshot,
        dex,
        chart if chart is not None else TypeChart.from_dex(dex),
        hypothesis if hypothesis is not None else OpponentHypothesis(),
        exact_stats=False,
    )


def option_features(
    snapshot: dict[str, Any],
    slot_index: int,
    option: dict[str, Any],
    board: Board | None = None,
    dex: Dex | None = None,
) -> np.ndarray:
    """One slot's option as a vector, in `FEATURE_NAMES` order.

    `board` is optional so that a single call is possible; passing it is what
    every real caller does, since the point of the board is that a position's
    damage is computed once for the whole legal set. `dex` is only read when
    neither is supplied.
    """
    if board is None:
        if dex is None:
            raise ValueError("option_features needs either a board or a dex")
        board = board_for(snapshot, dex)

    values = dict.fromkeys(FEATURE_NAMES, 0.0)
    _board_features(values, board, snapshot, slot_index)
    _actor_features(values, board, slot_index)

    kind = option.get("kind")
    if kind == "switch":
        values["is_switch"] = 1.0
        values["switch_incoming_hp"] = _incoming_hp(snapshot, option)
    elif kind == "move":
        values["is_move"] = 1.0
        _move_features(values, board, slot_index, option)

    return np.array([values[name] for name in FEATURE_NAMES], dtype=float)


# -- the option --------------------------------------------------------------


def _move_features(
    values: dict[str, float], board: Board, slot_index: int, option: dict[str, Any]
) -> None:
    entry = board.dex.moves.get(str(option.get("move") or ""))
    if entry is None:
        # A move the dump does not carry. Marked as a move and otherwise blank,
        # rather than dropped: the softmax is over the slot's whole legal set,
        # so an option that cannot be vectorised still has to have a vector.
        return

    category = str(entry.get("category") or "")
    values["is_physical"] = float(category == "Physical")
    values["is_special"] = float(category == "Special")
    values["is_status_move"] = float(category == "Status")
    values["base_power"] = float(entry.get("basePower") or 0) / POWER_SCALE
    values["priority"] = float(entry.get("priority") or 0) / PRIORITY_SCALE
    values["is_spread"] = float(str(entry.get("target") or "") in SPREAD_TARGETS)
    values["target_is_ally"] = float(int(option.get("target", 0) or 0) < 0)

    targets = board.targets(slot_index, option, entry)
    _target_features(values, board, slot_index, entry, targets)


def _target_features(
    values: dict[str, float],
    board: Board,
    slot_index: int,
    entry: dict[str, Any],
    targets: list[tuple[str, int]],
) -> None:
    """What the option does to what it is aimed at.

    A spread move has more than one target, so the foe-side numbers are the
    maximum over them and the ally-side ones are the sum. That asymmetry is
    deliberate: the best thing a spread move does to the opponent is what makes
    it worth pressing, and everything it does to our own side is a cost that
    accumulates.
    """
    foe_damage = 0.0
    ally_damage = 0.0
    target_hp = 0.0
    target_status = 0.0
    effectiveness = 0.0

    for side, target_slot in targets:
        fraction = board.damage_fraction(entry, slot_index, side, target_slot)
        if side == "ours":
            ally_damage += fraction
            continue
        if fraction >= foe_damage:
            foe_damage = fraction
            view = board.view("theirs", target_slot) or {}
            target_hp = float(view.get("hp_pct") or 0.0) / 100.0
            target_status = STATUS_COST.get(str(view.get("status") or ""), 0.0)
            effectiveness = _effectiveness(board, entry, target_slot)

    values["damage_fraction"] = foe_damage
    values["ally_damage_fraction"] = ally_damage
    values["target_hp"] = target_hp
    values["target_status"] = target_status
    values["target_ko"] = float(foe_damage >= 1.0)
    values["effectiveness"] = effectiveness


def _effectiveness(board: Board, entry: dict[str, Any], target_slot: int) -> float:
    """The type modifier as a stage, which is what the simulator itself carries.

    Stages rather than a multiplier because the scale is then symmetric -- a
    resistance is -1 and a weakness is +1 -- which is the axis a linear layer
    can use, and because `TypeChart.effectiveness` is already the simulator's
    own representation (`champions/dex/damage.py` explains why collapsing to a
    float multiplier first loses a HP). An immunity is not a stage at all and is
    reported below the lowest real one.
    """
    unit = board.unit("theirs", target_slot)
    if unit is None:
        return 0.0
    move_type = str(entry.get("type") or "")
    types = list(unit.types)
    if board.chart.is_immune(move_type, types):
        return IMMUNE_STAGE
    return float(board.chart.effectiveness(move_type, types))


def _incoming_hp(snapshot: dict[str, Any], option: dict[str, Any]) -> float:
    """The health of the Pokemon a switch brings in.

    One number, not a matchup model. Without it every switch out of a slot has
    an identical vector and the provider picks between them arbitrarily, which
    is the same defect implementation A has (`policy.SWITCH` is a constant).
    Extending it is the first thing to try if B loses to A on positions that
    wanted a switch.
    """
    species = str(option.get("species") or "")
    for view in snapshot.get("ours", {}).get("bench") or []:
        # By species, because that is what the order carries. A side may legally
        # register two of the same species; the first healthy one is taken,
        # which is wrong in that rare case and is not worth a nickname round
        # trip through the order's protocol string to fix.
        if str(view.get("species") or "") == species and not view.get("fainted"):
            return float(view.get("hp_pct") or 0.0) / 100.0
    return 0.0


# -- the actor ---------------------------------------------------------------


def _actor_features(values: dict[str, float], board: Board, slot_index: int) -> None:
    view = board.view("ours", slot_index)
    if view is None:
        return
    boosts = view.get("boosts") or {}
    values["actor_hp"] = float(view.get("hp_pct") or 0.0) / 100.0
    values["actor_boost_offense"] = _boost(boosts, OFFENSE_BOOSTS)
    values["actor_boost_defense"] = _boost(boosts, DEFENSE_BOOSTS)
    values["actor_boost_speed"] = float(boosts.get("spe", 0)) / BOOST_SCALE
    values["actor_status"] = STATUS_COST.get(str(view.get("status") or ""), 0.0)
    values["actor_first_turn"] = float(board.first_turn(slot_index))


def _boost(boosts: dict[str, Any], stats: tuple[str, ...]) -> float:
    return sum(float(boosts.get(stat, 0)) for stat in stats) / (BOOST_SCALE * len(stats))


# -- the board ---------------------------------------------------------------


def _board_features(
    values: dict[str, float], board: Board, snapshot: dict[str, Any], slot_index: int
) -> None:
    if board.empty:
        return

    values["turn"] = float(snapshot.get("turn") or 0) / TURN_SCALE
    values["our_remaining"] = _remaining(snapshot, "ours")
    values["their_remaining"] = _remaining(snapshot, "theirs")

    weather = set(snapshot.get("weather") or {})
    for name, spellings in WEATHER.items():
        values[name] = float(any(spelling in weather for spelling in spellings))

    trick_room = "TRICK_ROOM" in (snapshot.get("fields") or {})
    values["trick_room"] = float(trick_room)
    values["our_tailwind"] = float("TAILWIND" in (snapshot.get("side_conditions") or {}))
    values["their_tailwind"] = float("TAILWIND" in (snapshot.get("opponent_side_conditions") or {}))

    ours = board.unit("ours", slot_index)
    if ours is None:
        return
    our_speed = effective_speed(ours, snapshot, ours=True)
    for index, name in enumerate(("outspeeds_foe_a", "outspeeds_foe_b")):
        theirs = board.unit("theirs", index)
        if theirs is None:
            continue
        their_speed = effective_speed(theirs, snapshot, ours=False)
        faster = our_speed < their_speed if trick_room else our_speed > their_speed
        values[name] = float(faster)


def _remaining(snapshot: dict[str, Any], side: str) -> float:
    """How much of a side is left, as a fraction of what it brought.

    `evaluate.alive` rather than the snapshot's own `remaining`, because
    `remaining` on our side counts the registered six against an opponent who
    can only ever be counted as four -- the M6 error that scored a dead-even
    turn 1 at a win probability of 0.996.

    Counted from announced faints on *both* sides, where the evaluation counts
    ours directly. Two reasons, and the second is the binding one. The answers
    are identical -- brought is four and every faint is announced, so four minus
    the faints is the count -- and only the derived form survives the trip
    through a replay, where every registered Pokemon looks brought because a
    spectator stream never says which four a player picked. Counting ours
    directly would put our side at six in training and four in play.
    """
    return alive(snapshot[side], PICKED_TEAM_SIZE, known=False) / PICKED_TEAM_SIZE

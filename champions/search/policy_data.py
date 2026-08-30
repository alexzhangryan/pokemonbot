"""Training rows for the learned candidate prior, out of a replay log.

`docs/specs/2026-08-29-learned-policy-provider.md` section 3.4 fits a prior to
the corpus. The corpus holds states (through `champions.corpus.replay_state`)
and it holds labels (the `actions` table). What it does not hold is the thing in
between, and the thing in between is most of the work: a discrete choice model
needs the *set* the human chose from, not just what they chose.

## What a replay does and does not contain

A protocol log shows outcomes. It never shows a request, so the legal option set
has to be rebuilt, and rebuilding it is where the information rules bite.

**Allowed, because the acting player knew it.** Their own four moves and their
own bring-4. Both are in the log: the moves in `|showteam|`, the bring in which
of the six ever took the field. Using them is not leakage -- our agent knows its
own team exactly, and a choice set that omitted a move the player could see is a
choice set the player never faced.

**Not allowed.** Anything about the opponent that play did not reveal.
`replay_state` drops `|showteam|` for the opposing side already, so the state
half is handled there; this module only has to not put it back, which it does by
building each side's options from that side's own sheet.

## Which slots produce a row, and which do not

81.2% of them do. The rest is not noise and the shape of it is worth knowing,
because two of the four buckets below were bugs that only a coverage count would
have found -- a dropped row and a wrong label both look like nothing at all.

- **13.3%: no recorded choice.** A move that was chosen and then prevented --
  flinch, sleep, paralysis, Taunt -- produces no `|move|` line, so there is
  nothing to label. This biases the measured decisions towards turns that
  resolved, and the alternative is guessing.
- **4.4%: nothing in the slot.** One Pokemon left, or a slot waiting on a
  replacement.
- **1.1%: a choice the log does not disambiguate.** Almost all of it a Sucker
  Punch that failed: `[still]`, `|-fail|`, and no line anywhere naming what it
  was aimed at. A label guessed out of the choice set is worse than no label.

The three cases that look like the last one and are not are handled in
`_choices`, and are the reason it reads the raw lines as well as the parsed
actions. A charging move prints no target on its own `|move|` line; the target
is on `|-anim|` when it fired the same turn and on the release turn's `|move|`
when it did not. And the release turn itself is *not* a choice -- Showdown tags
it `[from] lockedmove` -- so counting it produces a row claiming the player
picked the one move they had no choice about.

## Three places this is still knowingly wrong

Stated here rather than discovered later. Each is label or choice-set noise that
no quantity of corpus removes.

1. **Redirection relabels the target.** Rage Powder and Follow Me make the
   `|move|` line name the redirector, not the slot the human aimed at. The row
   keeps the move and loses the target.
2. **Choice locks, Encore, Disable and trapping are not modelled.** The option
   set is the full four moves and every living bench member, so a Choice-locked
   slot is offered options it did not have. This inflates the set rather than
   truncating it, which makes recall pessimistic rather than optimistic.
3. **Mega Evolution is not enumerated.** The live enumeration offers a mega
   variant of every move when the stone is unused; the reconstruction does not.
   That costs nothing: `policy_features.FEATURE_NAMES` has no mega entry, so the
   two variants have identical vectors and would be duplicate rows here and a
   tie there.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from poke_env.battle.double_battle import _SHOWDOWN_TARGET_SLOTS

from champions.corpus.replay import Action, ReplayRecord, parse_replay
from champions.corpus.replay_state import SIDES, turn_states
from champions.dex.loader import Dex, to_id
from champions.protocol.actions import TARGET_LABELS
from champions.search.policy_features import board_for, option_features

#: The two active slots of a doubles side, in the order the protocol names them
#: and `state.snapshot` lists them.
SLOT_LETTERS = ("a", "b")

#: Moves the simulator substitutes for a real choice. poke-env returns the
#: no-target position for these; they are never a chosen option here because
#: they are never chosen.
SUBSTITUTE_MOVES = {"struggle", "recharge", "fight"}

#: How `|switch|` is classified by `champions.corpus.replay.parse_replay`. Only
#: one of the four is a turn-start choice. `lead` is preview, `replacement`
#: follows a faint, and `pivot` is the consequence of a move that is already the
#: row for that slot.
VOLUNTARY = "voluntary"

#: Showdown's own target-slot table, taken from poke-env rather than copied.
#: This is the table the live enumeration reads, and a second copy of it here is
#: how the training choice set and the served one quietly stop matching. The
#: dex spells targets in camelCase and the table keys them in upper snake;
#: `tests/test_policy_data.py` checks every target the dex uses is present.
TARGET_SLOTS = _SHOWDOWN_TARGET_SLOTS

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass(frozen=True)
class Decision:
    """One slot's turn-start choice, with everything needed to fit or score it.

    `snapshot` is kept because the feature path is checked against it and the
    live provider is handed the same shape. It is also the largest field by an
    order of magnitude, which is why the builders are generators: a caller that
    is accumulating a training set converts each row to arrays and lets the
    snapshot go, and only one is ever alive.
    """

    battle_id: str
    player: str
    side: str
    turn: int
    slot: int
    options: tuple[dict[str, Any], ...]
    #: Index into `options` of what the human actually played.
    chosen: int
    #: `(len(options), len(FEATURE_NAMES))`, in `FEATURE_NAMES` order.
    features: np.ndarray
    snapshot: dict[str, Any]
    sheets_revealed: bool
    rating: int | None


def decisions_from_log(
    log: str,
    battle_id: str,
    dex: Dex,
    revealed_moves_fallback: bool = False,
) -> Iterator[Decision]:
    """Every turn-start decision in one replay, both sides.

    `revealed_moves_fallback` is for the closed-sheet slice and nothing else.
    Those replays carry no `|showteam|`, so the only move set available is the
    one play revealed, which is a subset of the real four and makes the choice
    set smaller than the one the human faced. Recall measured on it is
    optimistic in absolute terms; what stays meaningful is the comparison
    between providers, since both are scored on the same reconstructed set.
    """
    yield from decisions_from_record(
        parse_replay(battle_id, log), log, dex, revealed_moves_fallback
    )


def decisions_from_record(
    record: ReplayRecord,
    log: str,
    dex: Dex,
    revealed_moves_fallback: bool = False,
) -> Iterator[Decision]:
    """The same, for a caller that has already parsed the record.

    The script filters on rating, format and sheets before paying for state
    reconstruction, and reconstruction is by far the expensive half, so the
    record it filtered on is passed straight through rather than re-derived.
    """
    movesets = _movesets(record, revealed_moves_fallback)
    if not movesets:
        return

    lines = log.splitlines()
    brought = {side: set(record.brought(side)) for side in SIDES}
    choices = _choices(record.actions, lines)
    players = dict(zip(SIDES, record.players, strict=True))
    ratings = dict(zip(SIDES, record.ratings, strict=True))

    for turn, views in turn_states(lines, dex):
        if turn < 1:
            continue
        for side in SIDES:
            snapshot = views[side]
            board = None
            for slot in range(len(SLOT_LETTERS)):
                key = (turn, side, slot)
                if key not in choices:
                    continue
                active = _active(snapshot, slot)
                if active is None:
                    continue
                options = _options(snapshot, side, slot, active, movesets, brought[side], dex)
                chosen = _match(options, choices[key], side)
                if chosen is None:
                    continue
                if board is None:
                    board = board_for(snapshot, dex)
                yield Decision(
                    battle_id=record.replay_id,
                    player=players[side],
                    side=side,
                    turn=turn,
                    slot=slot,
                    options=tuple(options),
                    chosen=chosen,
                    features=np.stack([option_features(snapshot, slot, o, board) for o in options]),
                    snapshot=snapshot,
                    sheets_revealed=record.sheets_revealed,
                    rating=ratings[side],
                )


# -- what the player knew ----------------------------------------------------


def _movesets(
    record: ReplayRecord, revealed_moves_fallback: bool
) -> dict[tuple[str, str], tuple[str, ...]]:
    """`(side, species) -> move ids`, from the sheet or from what was revealed."""
    from_sheet = {(entry.side, entry.species): entry.moves for entry in record.sets if entry.moves}
    if from_sheet or not revealed_moves_fallback:
        return from_sheet

    revealed: dict[tuple[str, str], list[str]] = {}
    for action in record.actions:
        if action.action != "move" or not action.value or not action.species:
            continue
        moves = revealed.setdefault((action.side, to_id(action.species)), [])
        move = to_id(action.value)
        if move not in moves and move not in SUBSTITUTE_MOVES:
            moves.append(move)
    return {key: tuple(moves) for key, moves in revealed.items()}


def _choices(actions: Sequence[Action], lines: Sequence[str]) -> dict[tuple[int, str, int], Action]:
    """`(turn, side, slot) -> the action that was the turn-start choice`.

    Three things happen here and the last two are what a naive read of the
    `actions` table gets wrong.

    **First one wins.** A slot can produce two `|move|` lines in a turn -- Dancer
    copies one, Instruct repeats one -- and only the first was chosen.

    **A move the slot was locked into was not chosen.** Showdown tags the second
    turn of a two-turn move `[from] lockedmove`, and the parser carries that
    through as `via`. Without the skip, the release turn of every Electro Shot
    and Solar Beam becomes a training row claiming the player picked, out of
    four moves and every switch, the one move they had no choice about.

    **A charge turn's target is on a different line.** `|move|` for the charging
    turn prints no target at all, so the choice looks unresolvable and the whole
    decision is dropped. It is not unresolvable: the target is on the `|-anim|`
    line when the move released the same turn (Electro Shot in rain), and on the
    release turn's own `|move|` line when it did not. Both are recovered here.

    What is left after that is the genuinely unrecorded: a Sucker Punch that
    failed prints `[still]` and `|-fail|` and never names what it was aimed at.
    Those decisions stay dropped, because a label guessed from a choice set is
    worse than no label.
    """
    animated = _animation_targets(lines)
    out: dict[tuple[int, str, int], Action] = {}
    for index, action in enumerate(actions):
        if action.turn < 1 or not action.slot:
            continue
        if action.action == "switch":
            if action.detail.get("how") != VOLUNTARY:
                continue
        elif action.detail.get("via"):
            continue
        letter = action.slot[-1]
        if letter not in SLOT_LETTERS:
            continue
        key = (action.turn, action.side, SLOT_LETTERS.index(letter))
        if key in out:
            continue
        out[key] = _resolve_target(action, actions[index + 1 :], animated)
    return out


def _resolve_target(
    action: Action, later: Sequence[Action], animated: Mapping[tuple[int, str, str, str], str]
) -> Action:
    """The same action, with a target if one can be recovered from elsewhere."""
    if action.action != "move" or action.target or not action.slot:
        return action

    move = to_id(str(action.value or ""))
    target = animated.get((action.turn, action.side, action.slot, move))
    if not target:
        target = next(
            (
                other.target
                for other in later
                if other.slot == action.slot
                and to_id(str(other.value or "")) == move
                and other.detail.get("via") == "lockedmove"
                and other.target
            ),
            None,
        )
    if not target:
        return action
    return replace(action, target=target, detail={**action.detail, "target": target})


def _animation_targets(lines: Sequence[str]) -> dict[tuple[int, str, str, str], str]:
    """`(turn, side, slot, move) -> target`, out of `|-anim|`.

    Read from the raw lines because `champions/protocol/parser.py` lists
    `-anim` among the messages it deliberately ignores, which is right for a
    parser building observations -- an animation is not a fact about the battle.
    It is, however, the only place a same-turn charge release names its target.
    """
    out: dict[tuple[int, str, str, str], str] = {}
    turn = 0
    for line in lines:
        if line.startswith("|turn|"):
            digits = line.split("|")[2] if len(line.split("|")) > 2 else ""
            turn = int(digits) if digits.isdigit() else turn + 1
            continue
        if not line.startswith("|-anim|"):
            continue
        parts = line.split("|")[1:]
        if len(parts) < 4 or len(parts[1]) < 3 or not parts[3].startswith("p"):
            continue
        side, letter = parts[1][:2], parts[1][2]
        if letter not in SLOT_LETTERS:
            continue
        out.setdefault((turn, side, f"{side}{letter}", to_id(parts[2])), parts[3])
    return out


def _active(snapshot: Mapping[str, Any], slot: int) -> dict[str, Any] | None:
    actives = snapshot.get("ours", {}).get("active") or []
    if slot >= len(actives):
        return None
    view = actives[slot]
    if view is None or view.get("fainted"):
        return None
    return dict(view)


# -- the choice set ----------------------------------------------------------


def _options(
    snapshot: Mapping[str, Any],
    side: str,
    slot: int,
    active: Mapping[str, Any],
    movesets: Mapping[tuple[str, str], tuple[str, ...]],
    brought: set[str],
    dex: Dex,
) -> list[dict[str, Any]]:
    """Switches first, then moves, which is the order poke-env enumerates in.

    Order is not arbitrary. The softmax does not care, but the tie-break in
    every provider is positional after the score, so keeping the reconstruction
    in the live order stops a tie being broken one way in training and the other
    in play.
    """
    options: list[dict[str, Any]] = _switch_options(snapshot, brought)
    for move_id in _sheet_moves(str(active.get("species") or ""), side, movesets, dex):
        options.extend(_move_options(snapshot, move_id, slot, dex))
    return options


def _sheet_moves(
    species: str,
    side: str,
    movesets: Mapping[tuple[str, str], tuple[str, ...]],
    dex: Dex,
) -> tuple[str, ...]:
    """The sheet's move set for whatever is on the field now.

    The forme on the field and the name on the sheet are not always the same
    string, and they disagree in both directions, which is why this is a lookup
    with a fallback rather than a normalisation.

    A sheet registers Charizard and the field shows Charizard-Mega-Y, so the
    forme has to fall back to its base. That is not an edge case: Champions
    brings Mega Evolution back with 75 legal stones, and without the fallback
    every decision a Pokemon makes after Mega Evolving finds no move set and is
    dropped -- which would thin the corpus of the format's most distinctive
    Pokemon in proportion to how often they are worth using.

    A sheet also registers Rotom-Wash and Ninetales-Alola directly, and both
    have Rotom and Ninetales as their `baseSpecies`, so collapsing to the base
    unconditionally breaks the ones that were already right. Exact first, base
    second.
    """
    identifier = to_id(species)
    exact = movesets.get((side, identifier))
    if exact is not None:
        return exact
    entry = dex.species.get(identifier) or {}
    base = to_id(str(entry.get("baseSpecies") or ""))
    return movesets.get((side, base), ()) if base else ()


def _switch_options(snapshot: Mapping[str, Any], brought: set[str]) -> list[dict[str, Any]]:
    out = []
    for view in snapshot.get("ours", {}).get("bench") or []:
        species = to_id(str(view.get("species") or ""))
        if species not in brought or view.get("fainted"):
            continue
        out.append(
            {
                "kind": "switch",
                "species": species,
                "name": view.get("name") or species,
                "label": f"switch to {species}",
            }
        )
    return out


def _move_options(
    snapshot: Mapping[str, Any], move_id: str, slot: int, dex: Dex
) -> list[dict[str, Any]]:
    entry = dex.moves.get(move_id)
    if entry is None:
        return []
    return [
        {
            "kind": "move",
            "move": move_id,
            "name": entry.get("name", move_id),
            "type": entry.get("type"),
            "category": entry.get("category"),
            "base_power": entry.get("basePower"),
            "priority": entry.get("priority", 0),
            "move_target": entry.get("target"),
            "target": target,
            "target_label": TARGET_LABELS.get(target, f"slot {target}"),
            "mega": False,
            # Built the way `protocol.actions._move_label` builds it, target and
            # all. Two targets of one move are two options, and a label that did
            # not name the target would make them indistinguishable to anything
            # keyed on it.
            "label": _label(str(entry.get("name", move_id)), target),
        }
        for target in _targets(snapshot, str(entry.get("target") or "normal"), move_id, slot)
    ]


def _label(name: str, target: int) -> str:
    if not target:
        return name
    return f"{name} -> {TARGET_LABELS.get(target, f'slot {target}')}"


def _targets(snapshot: Mapping[str, Any], move_target: str, move_id: str, slot: int) -> list[int]:
    """The signed slots a move may be aimed at, filtered to occupied ones.

    Mirrors `DoubleBattle.get_possible_showdown_targets` on the one branch a Reg
    M-B replay can reach. The branches left out are Dynamax, Z-moves and
    Terastallization -- none legal here, and Tera is disabled in Champions
    (`CLAUDE.md` constraint 1) -- plus the two move-specific cases (Pollen Puff
    under Heal Block, Tera Starstorm) whose condition needs live state.

    `self` and `ally` are both our own side but they are not the same slot:
    `self` is the acting one and `ally` is the other, which is why the slot has
    to be threaded down here. Collapsing them would offer a `normal` move four
    targets where the request offers three.
    """
    if move_id in SUBSTITUTE_MOVES:
        return [0]
    slots = TARGET_SLOTS.get(_CAMEL.sub("_", move_target).upper())
    if slots is None:
        return [0]

    ours = snapshot.get("ours", {}).get("active") or []
    theirs = snapshot.get("theirs", {}).get("active") or []
    occupied = {0}
    occupied |= {-(i + 1) for i, view in enumerate(ours) if view is not None}
    occupied |= {i + 1 for i, view in enumerate(theirs) if view is not None}

    partner = 1 - slot if slot < len(SLOT_LETTERS) else slot
    mapping = {"empty": 0, "self": -(slot + 1), "ally": -(partner + 1), "opp1": 1, "opp2": 2}
    out: list[int] = []
    for name in slots:
        value = mapping.get(name, 0)
        if value in occupied and value not in out:
            out.append(value)
    return out or [0]


# -- the label ---------------------------------------------------------------


def _match(options: Sequence[dict[str, Any]], action: Action, side: str) -> int | None:
    """Which option the human played, or None if the log does not say.

    None rather than a guess. A row whose label is the wrong member of its own
    choice set trains the model against itself, and the volume lost to being
    strict is a few percent of a corpus that has hundreds of thousands of rows.
    """
    if action.action == "switch":
        species = to_id(str(action.value or ""))
        return next(
            (i for i, o in enumerate(options) if o["kind"] == "switch" and o["species"] == species),
            None,
        )

    move = to_id(str(action.value or ""))
    same = [i for i, o in enumerate(options) if o["kind"] == "move" and o["move"] == move]
    if len(same) <= 1:
        return same[0] if same else None

    target = _target_of(action, side)
    return next((i for i in same if options[i]["target"] == target), None)


def _target_of(action: Action, side: str) -> int | None:
    """`p2b: Nickname` to the signed slot the acting side would have typed."""
    text = action.target or action.detail.get("target")
    if not isinstance(text, str) or len(text) < 3:
        return None
    target_side, letter = text[:2], text[2]
    if letter not in SLOT_LETTERS:
        return None
    index = SLOT_LETTERS.index(letter) + 1
    return -index if target_side == side else index

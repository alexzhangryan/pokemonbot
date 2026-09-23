"""One shape for a reviewable decision, from either of two sources.

`docs/specs/2026-09-13-coach.md` section 5.2. The coach re-solves each turn of
a finished game, and to do that it needs five things per turn: the position
as the acting side saw it, that side's legal joint actions, which one it
played, what the opponent actually did, and the position that followed.

Two sources supply them.

**A trace of the agent's own game** already carries all five in the events
`champions.agents.baseline.TracingPlayer` emits: `turn_start.state`, the
unpruned `candidates.joint`, `equilibrium.chosen_action`, the next
`turn_result`'s observations, and the next `turn_start.state`. Nothing is
reconstructed and the coach reads exactly what the agent saw, which is what
makes the agent debuggable after the fact (`docs/07` section 1).

**A Showdown replay of anyone's game** carries none of them directly. The
position is rebuilt by `champions.corpus.replay_state.Observer` and the choice
set by `champions.search.policy_data`, both of which M7 built and validated
against live traces (D65). This module joins the two per-slot choice sets into
joint actions the way the live enumeration does, and synthesises the base
trace a live agent would have written, so the overlay and the viewer see one
kind of file whichever source produced it.

What the two sources cannot make identical is stated rather than hidden: a
replay's snapshot carries percentages and no exact stats for *either* side
(`replay_state` docstring), so a replay's numbers run on the same pessimistic
constant for our own spread that the model uses for the opponent's.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from champions.belief.evaluate import TruthSet, truth_from_replay
from champions.corpus.replay import ReplayRecord, parse_replay
from champions.corpus.replay_state import SIDES, Observer
from champions.dex.loader import Dex, to_id
from champions.protocol.state import annotate_unseen
from champions.search.evaluate import evaluate
from champions.search.policy_data import SlotChoice, slot_choices
from champions.trace.schema import EventType

TRACE = "trace"
REPLAY = "replay"

SLOT_LETTERS = ("a", "b")

#: A slot with nothing to do or nothing recorded, in the column shape.
NONE_SLOT: dict[str, Any] = {"kind": "none", "label": "no recorded action"}


@dataclass(frozen=True)
class Decision:
    """One turn-start decision, with everything the analysis needs."""

    turn: int
    #: The acting side's view at turn start, `state.snapshot()` shaped.
    snapshot: dict[str, Any]
    #: Our legal joint actions, described-action shaped.
    rows: list[dict[str, Any]]
    #: Index into `rows` of what was played, or None if the log did not say.
    played: int | None
    #: The opponent's realised joint action, column shaped, or None if the game
    #: ended before they acted.
    their_played: dict[str, Any] | None
    #: The view at the next turn start, or the final one after the last turn.
    next_snapshot: dict[str, Any]
    #: The seq of the base-trace event this decision's analysis annotates.
    for_seq: int
    #: What the live agent wrote about its own decision, when there was one.
    recorded: dict[str, Any] = field(default_factory=dict)
    #: The belief event's summary at this turn, when the trace has one.
    belief: dict[str, Any] | None = None


@dataclass(frozen=True)
class Game:
    battle_id: str
    format_id: str
    #: "p1" or "p2": the side the review is from.
    side: str
    player: str | None
    opponent: str | None
    #: "win", "loss", "tie", or None when the log does not say.
    result: str | None
    turns: int
    #: The base trace, original or synthesised, as plain dicts.
    events: list[dict[str, Any]]
    decisions: list[Decision]
    source: str
    #: The opponent's registered sets as the acting player could know them
    #: before the fact (an open sheet), and as the reviewer knows them after
    #: it (a sheet, or a team file). None means revealed moves only.
    ante_truths: Mapping[str, TruthSet] | None = None
    post_truths: Mapping[str, TruthSet] | None = None

    @property
    def opponent_side(self) -> str:
        return "p2" if self.side == "p1" else "p1"


# -- the trace source --------------------------------------------------------


def from_trace(
    events: Sequence[Mapping[str, Any]],
    *,
    post_truths: Mapping[str, TruthSet] | None = None,
    ante_truths: Mapping[str, TruthSet] | None = None,
    dex: Dex | None = None,
) -> Game:
    """A game out of the events a `TracingPlayer` wrote.

    With a `dex`, each decision's snapshot gets the opponent's previewed and
    not yet seen Pokemon on the bench, as the agent's own search did (D91),
    so the coach's switch columns are the same set the agent had rather than
    only the switch the opponent turned out to make. The recorded state is
    copied first; the trace itself is what was observed.
    """
    plain = [dict(e) for e in events]
    start = next((e for e in plain if e.get("type") == EventType.BATTLE_START), None)
    if start is None:
        raise ValueError("trace has no battle_start event")
    end = next((e for e in reversed(plain) if e.get("type") == EventType.BATTLE_END), None)
    head = start.get("payload") or {}
    side = str(head.get("player_role") or "p1")
    opponent_side = "p2" if side == "p1" else "p1"

    # One slot per decision, in order, rather than per turn number: a faint
    # forces a mid-turn switch request, which the tracer records as a second
    # `turn_start` with the same turn, and both are decisions.
    slots: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for event in plain:
        kind = event.get("type")
        payload = event.get("payload") or {}
        if kind == EventType.TURN_RESULT:
            observations.extend(payload.get("observations") or [])
            continue
        if kind == EventType.BATTLE_END:
            observations.extend(payload.get("final_observations") or [])
            continue
        if kind == EventType.TURN_START:
            slots.append({"start": payload, "turn": payload.get("turn")})
            continue
        if not slots:
            continue
        slot = slots[-1]
        if kind == EventType.CANDIDATES:
            slot["pruned" if payload.get("pruned") else "legal"] = payload
        elif kind == EventType.EQUILIBRIUM:
            slot["equilibrium"] = payload
            slot["equilibrium_seq"] = int(event.get("seq", -1))
        elif kind == EventType.BELIEF:
            slot["belief"] = payload

    final_state = (end or {}).get("payload", {}).get("state") if end else None
    turns = [int(s["turn"]) for s in slots if isinstance(s.get("turn"), int)]
    decisions: list[Decision] = []
    seen_turns: set[int] = set()
    for index, slot in enumerate(slots):
        turn = slot.get("turn")
        start_payload = slot.get("start")
        equilibrium = slot.get("equilibrium")
        if not isinstance(turn, int) or start_payload is None or equilibrium is None:
            continue
        snapshot = start_payload.get("state")
        if not snapshot:
            continue
        if dex is not None and head.get("opponent_team_preview"):
            snapshot = copy.deepcopy(snapshot)
            annotate_unseen(snapshot, list(head["opponent_team_preview"]), dex)
        legal = slot.get("legal") or {}
        joint = legal.get("joint") or (slot.get("pruned") or {}).get("joint") or []
        rows = [dict(a) for a in joint]
        chosen_message = equilibrium.get("chosen")
        played = next((i for i, r in enumerate(rows) if r.get("message") == chosen_message), None)
        if played is None and equilibrium.get("chosen_action"):
            rows.append(dict(equilibrium["chosen_action"]))
            played = len(rows) - 1
        if len(rows) < 2:
            continue

        following = slots[index + 1].get("start") if index + 1 < len(slots) else None
        next_snapshot = (following or {}).get("state") or final_state
        if not next_snapshot:
            continue

        pruned = slot.get("pruned") or {}
        # A second decision at the same turn number is the switch a faint
        # forced after the turn's moves resolved. The opponent has no
        # simultaneous action there -- theirs for this turn already
        # happened -- so there is no ex-post cell to score: resolving their
        # turn's attacks a second time against our switch-in read every
        # forced switch as eight to twenty points luckier than it was (D93).
        forced = turn in seen_turns
        seen_turns.add(turn)
        decisions.append(
            Decision(
                turn=turn,
                snapshot=snapshot,
                rows=rows,
                played=played,
                their_played=None
                if forced
                else _their_action_from_observations(observations, turn, opponent_side, snapshot),
                next_snapshot=next_snapshot,
                for_seq=int(slot.get("equilibrium_seq", -1)),
                recorded={
                    "chosen": chosen_message,
                    "value": equilibrium.get("value"),
                    "watchdog_fired": bool(equilibrium.get("watchdog_fired")),
                    "game_value": pruned.get("game_value"),
                    "k": pruned.get("k"),
                    "model": pruned.get("model"),
                    "opponent_model": pruned.get("opponent_model"),
                    "n_legal_joint_actions": equilibrium.get("n_legal_joint_actions"),
                    "truncated": bool(legal.get("truncated")),
                    "win_prob": (start_payload.get("evaluation") or {}).get("win_prob"),
                },
                belief=_belief_summary(slot.get("belief")),
            )
        )

    end_payload = (end or {}).get("payload") or {}
    return Game(
        battle_id=str(start.get("battle_id") or ""),
        format_id=str(head.get("format_id") or ""),
        side=side,
        player=head.get("player_username"),
        opponent=head.get("opponent_username"),
        result=end_payload.get("result"),
        turns=int(end_payload.get("turns") or (turns[-1] if turns else 0)),
        events=plain,
        decisions=decisions,
        source=TRACE,
        ante_truths=ante_truths,
        post_truths=post_truths if post_truths is not None else ante_truths,
    )


def _belief_summary(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    keep = ("particles", "alive", "effective_sample_size", "turns_observed", "max_weight")
    return {k: payload[k] for k in keep if k in payload}


def _their_action_from_observations(
    observations: Iterable[Mapping[str, Any]],
    turn: int,
    opponent_side: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    """The opponent's realised joint action at `turn`, column shaped.

    The first `move` or voluntary `switch` per slot wins, as in
    `policy_data._choices`; a slot with neither did nothing that turn, which
    is what the ex-post matrix should see.
    """
    active = (snapshot.get("theirs") or {}).get("active") or []
    per_slot: list[dict[str, Any] | None] = [None] * max(len(active), 1)
    seen = False
    for row in observations:
        if row.get("turn") != turn or row.get("side") != opponent_side:
            continue
        kind = row.get("attribute")
        slot_name = str(row.get("slot") or "")
        if not slot_name or slot_name[-1] not in SLOT_LETTERS:
            continue
        index = SLOT_LETTERS.index(slot_name[-1])
        if index >= len(per_slot) or per_slot[index] is not None:
            continue
        detail = row.get("detail") or {}
        if kind == "switch":
            if detail.get("how") != "voluntary":
                continue
            species = to_id(str(row.get("value") or row.get("species") or ""))
            per_slot[index] = {
                "kind": "switch",
                "species": species,
                "name": row.get("value") or species,
                "label": f"switch to {species}",
            }
            seen = True
        elif kind == "move":
            move_id = to_id(str(row.get("value") or ""))
            if not move_id:
                continue
            per_slot[index] = {
                "kind": "move",
                "move": move_id,
                "name": str(row.get("value")),
                "target": _signed_target(detail.get("target"), opponent_side),
                "label": str(row.get("value")),
            }
            seen = True
    if not seen:
        return None
    return joint_column([s if s is not None else dict(NONE_SLOT) for s in per_slot])


def _signed_target(target: Any, acting_side: str) -> int:
    """`p1b: Name` as the signed slot index from the acting side's view.

    Positive means the other side, which is how `payoff.targets_of` reads a
    described target, so their `p1a` is our slot 1 and their own `p2b` is -2.
    """
    if not isinstance(target, str) or len(target) < 3 or target[2] not in SLOT_LETTERS:
        return 0
    index = SLOT_LETTERS.index(target[2]) + 1
    return -index if target[:2] == acting_side else index


# -- the replay source -------------------------------------------------------


def resolve_side(record: ReplayRecord, side: str) -> str:
    """`p1`, `p2`, or a player name, to `p1` or `p2`."""
    if side in SIDES:
        return side
    for candidate, name in zip(SIDES, record.players, strict=True):
        if name.lower() == side.lower():
            return candidate
    raise ValueError(f"{side!r} is neither a side nor a player of {record.replay_id}")


def from_replay(log: str, battle_id: str, side: str, dex: Dex) -> Game:
    """A game out of a protocol log, reviewed from `side`."""
    record = parse_replay(battle_id, log)
    side = resolve_side(record, side)
    opponent_side = "p2" if side == "p1" else "p1"

    truths = truth_from_replay(record, opponent_side) if record.sheets_revealed else {}
    views, final = _views(log.splitlines(), dex)

    choices: dict[tuple[int, str], dict[int, SlotChoice]] = {}
    for choice in slot_choices(record, log, dex, revealed_moves_fallback=not truths):
        choices.setdefault((choice.turn, choice.side), {})[choice.slot] = choice

    events, seqs = _synthesise_events(record, side, views, final)

    decisions: list[Decision] = []
    turns = sorted(views)
    for index, turn in enumerate(turns):
        ours = choices.get((turn, side))
        if not ours:
            continue
        snapshot = views[turn][side]
        rows, played = _joint_rows(ours, snapshot)
        last = index + 1 >= len(turns)
        if len(rows) < 2 or (played is None and last):
            # A final `|turn|` with no action in it is the game ending, not a
            # decision; there is nothing to score and no position after it.
            continue
        theirs = choices.get((turn, opponent_side)) or {}
        next_snapshot = views[turns[index + 1]][side] if index + 1 < len(turns) else final[side]
        decisions.append(
            Decision(
                turn=turn,
                snapshot=snapshot,
                rows=rows,
                played=played,
                their_played=_their_action_from_choices(theirs, snapshot),
                next_snapshot=next_snapshot,
                for_seq=seqs[turn],
            )
        )

    winner = record.winner_side
    if record.result == "tie":
        result: str | None = "tie"
    elif winner is None:
        result = None
    else:
        result = "win" if winner == side else "loss"

    players = dict(zip(SIDES, record.players, strict=True))
    return Game(
        battle_id=record.replay_id,
        format_id=record.format_id,
        side=side,
        player=players.get(side) or None,
        opponent=players.get(opponent_side) or None,
        result=result,
        turns=record.turns or (turns[-1] if turns else 0),
        events=events,
        decisions=decisions,
        source=REPLAY,
        ante_truths=truths or None,
        post_truths=truths or None,
    )


def _views(
    lines: Sequence[str], dex: Dex
) -> tuple[dict[int, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Both sides' views at every turn start, and after the last line.

    `replay_state.turn_states` stops at the last turn boundary; the coach
    also needs the position the game ended in, which is what the last
    decision is scored against.
    """
    observer = Observer(dex)
    views: dict[int, dict[str, dict[str, Any]]] = {}
    pending = False
    for line in lines:
        if line.startswith("|turn|"):
            observer.feed(line)
            pending = True
            continue
        if pending:
            views[observer.turn] = {s: observer.view(s) for s in SIDES}
            pending = False
        observer.feed(line)
    if pending:
        views[observer.turn] = {s: observer.view(s) for s in SIDES}
    return views, {s: observer.view(s) for s in SIDES}


def _joint_rows(
    slots: Mapping[int, SlotChoice], snapshot: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], int | None]:
    """The joint action set out of per-slot choice sets, and the played index.

    Joined the way the live enumeration joins them: every pair minus the
    double switches into one Pokemon. A slot with no Pokemon contributes a
    `none` placeholder so slot indices line up with the snapshot's `active`
    list, which is how `TurnModel` reads them.
    """
    active = (snapshot.get("ours") or {}).get("active") or []
    per_slot: list[list[dict[str, Any]]] = []
    chosen: list[int | None] = []
    for index in range(max(len(active), 1)):
        choice = slots.get(index)
        if choice is None:
            per_slot.append([dict(NONE_SLOT)])
            chosen.append(0)
        else:
            per_slot.append([dict(o) for o in choice.options])
            chosen.append(choice.chosen)

    rows: list[dict[str, Any]] = []
    played: int | None = None
    if len(per_slot) == 1:
        for i, option in enumerate(per_slot[0]):
            rows.append(_joint_row([option]))
            if chosen[0] == i:
                played = len(rows) - 1
        return rows, played

    for i, first in enumerate(per_slot[0]):
        for j, second in enumerate(per_slot[1]):
            if (
                first.get("kind") == "switch"
                and second.get("kind") == "switch"
                and first.get("species") == second.get("species")
            ):
                continue
            rows.append(_joint_row([first, second]))
            if chosen[0] == i and chosen[1] == j and None not in chosen:
                played = len(rows) - 1
    return rows, played


def _joint_row(slots: list[dict[str, Any]]) -> dict[str, Any]:
    label = " + ".join(str(s.get("label", s.get("kind", "?"))) for s in slots)
    return {
        "message": label,
        "slots": slots,
        "label": label,
        "kinds": sorted({str(s.get("kind")) for s in slots}),
    }


def joint_column(slots: list[dict[str, Any]]) -> dict[str, Any]:
    """A column in the shape `policy.opponent_candidates` produces."""
    label = " + ".join(str(s.get("label", s.get("kind", "?"))) for s in slots) or "no action"
    return {
        "message": label,
        "slots": slots,
        "label": label,
        "kinds": sorted({str(s.get("kind")) for s in slots}),
    }


def _their_action_from_choices(
    theirs: Mapping[int, SlotChoice], snapshot: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The opponent's realised action out of their own matched choices.

    An option's target is signed from the opponent's point of view, and so is
    a column's (`payoff.targets_of` reads a positive target as the other
    side), so it carries over unchanged.
    """
    active = (snapshot.get("theirs") or {}).get("active") or []
    slots: list[dict[str, Any]] = []
    seen = False
    for index in range(max(len(active), 1)):
        choice = theirs.get(index)
        if choice is None or choice.chosen is None:
            slots.append(dict(NONE_SLOT))
            continue
        option = choice.options[choice.chosen]
        seen = True
        if option.get("kind") == "switch":
            slots.append(
                {
                    "kind": "switch",
                    "species": option.get("species"),
                    "name": option.get("name"),
                    "label": option.get("label"),
                }
            )
        else:
            slots.append(
                {
                    "kind": "move",
                    "move": option.get("move"),
                    "name": option.get("name"),
                    "type": option.get("type"),
                    "category": option.get("category"),
                    "base_power": option.get("base_power"),
                    "priority": option.get("priority", 0),
                    "target": int(option.get("target") or 0),
                    "label": option.get("name"),
                }
            )
    return joint_column(slots) if seen else None


def _synthesise_events(
    record: ReplayRecord,
    side: str,
    views: Mapping[int, Mapping[str, dict[str, Any]]],
    final: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """The base trace a live agent would have written for this replay.

    Returns the events and, per turn, the seq of its `equilibrium` event,
    which is what the turn's `analysis` will name in `for_seq`.
    """
    opponent_side = "p2" if side == "p1" else "p1"
    players = dict(zip(SIDES, record.players, strict=True))
    base_t = float(max(record.uploadtime or 0, 1))
    events: list[dict[str, Any]] = []

    def emit(kind: str, payload: dict[str, Any]) -> int:
        seq = len(events)
        events.append(
            {
                "schema_version": 1,
                "battle_id": record.replay_id,
                "seq": seq,
                "t": base_t + seq * 1e-3,
                "type": kind,
                "payload": payload,
            }
        )
        return seq

    preview = {s: [p.species for p in record.previews if p.side == s] for s in SIDES}
    emit(
        EventType.BATTLE_START,
        {
            "format_id": record.format_id,
            "player_role": side,
            "player_username": players.get(side),
            "opponent_username": players.get(opponent_side),
            "agent": "human",
            "strategy": "replay",
            "our_team": preview[side],
            "opponent_team_preview": preview[opponent_side],
            "seed": None,
            "source": REPLAY,
            "rated": record.rated,
            "ratings": dict(zip(SIDES, record.ratings, strict=True)),
        },
    )
    emit(
        EventType.PREVIEW_DECISION,
        {
            "order": None,
            "selected": list(record.brought(side)),
            "policy": "human",
            "pending": ["subset_distribution", "payoff_matrix", "equilibrium_weights"],
        },
    )

    by_turn: dict[int, list[dict[str, Any]]] = {}
    for observation in record.observations:
        by_turn.setdefault(observation.turn, []).append(observation.as_row())

    seqs: dict[int, int] = {}
    for turn in sorted(views):
        rows = by_turn.get(turn - 1) or []
        if rows:
            emit(EventType.TURN_RESULT, {"turn": turn, "observations": rows})
        state = views[turn][side]
        position = evaluate(state)
        emit(
            EventType.TURN_START,
            {
                "turn": turn,
                "state": state,
                "log": [],
                "evaluation": {
                    "win_prob": position.win_prob,
                    "log_odds": None
                    if position.log_odds in (float("inf"), float("-inf"))
                    else position.log_odds,
                    "calibrated": position.calibrated,
                    "features": position.features,
                },
            },
        )
        seqs[turn] = emit(
            EventType.EQUILIBRIUM,
            {
                "turn": turn,
                "chosen": None,
                "chosen_action": None,
                "strategy": "human",
                "value": None,
                "seed": None,
                "watchdog_fired": False,
                "proposals": [],
                "pending": ["mixed_strategy", "game_value"],
            },
        )

    winner = record.winner_side
    if record.result == "tie":
        result = "tie"
    elif winner is None:
        result = "unknown"
    else:
        result = "win" if winner == side else "loss"
    emit(
        EventType.BATTLE_END,
        {
            "result": result,
            "turns": record.turns,
            "rating": None,
            "state": final[side],
            "log": [],
            "final_observations": [],
            "unhandled_messages": dict(record.unhandled),
            "belief": None,
        },
    )
    return events, seqs

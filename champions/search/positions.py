"""Labelled positions: what M6 fits the evaluation function to.

A position is a feature vector and the eventual result of the game it came
from, scored from one side's point of view. `docs/04-decision-engine.md`
section 5 asks for the evaluation to be "trained on replay outcomes rather than
hand tuned", and this is the half of that which produces the training rows.

Two sources, and the difference between them is the point.

**Self-play** reads the trace directly. `turn_start` already carries the exact
snapshot the agent evaluated and `battle_end` carries the result, so there is no
reconstruction and no chance of the training features differing from the ones
the search computes at run time. Both sides are the same agent, so there is no
skill confound at all: D39 measured the higher-rated player winning 57.4% of
1,808 rated games, which means a model fit on ladder outcomes is partly fitting
"the stronger player was ahead" rather than "this position is winning".

**The corpus** reads a scraped replay and rebuilds the position from the
protocol. Real ladder play, two orders of magnitude more of it, and confounded
in exactly the way self-play is not. It is the check, not the primary.

Three things make the two commensurable, and each costs accuracy on the corpus
side rather than convenience:

1. The opponent is censored to what a player on that side would have seen. A
   replay log is a spectator's view and shows both teams; feeding that in would
   fit a model on information the live agent never has. Only Pokemon that have
   actually appeared count as revealed, exactly as in a live battle.
2. Both viewpoints of a replay are emitted, as two rows with opposite labels,
   so the corpus half inherits no bias from which side the log calls `p1`.
3. Ties are dropped rather than labelled 0.5. A logistic fit over a binary
   outcome has no place to put a draw, and there are few enough of them that
   inventing a convention costs more than it buys.

The features are computed by `champions.search.evaluate.features` in both cases
and never reimplemented here. That is not tidiness: a training set built from a
second implementation of the features is a model fit to a function the search
does not compute.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from champions.belief.evidence import parse_hp
from champions.protocol import parser
from champions.search.evaluate import features

#: How many Pokemon each side brings. Reg M-B registers six and plays four.
PICKED_TEAM_SIZE = 4

#: Side conditions the features read, in the protocol's spelling. The feature
#: code speaks poke-env's enum names, so they are translated on the way in.
SIDE_CONDITION_NAMES = {
    "tailwind": "TAILWIND",
    "stealth rock": "STEALTH_ROCK",
    "spikes": "SPIKES",
    "toxic spikes": "TOXIC_SPIKES",
    "sticky web": "STICKY_WEB",
}

#: Statuses, protocol spelling to the abbreviations `STATUS_COST` is keyed on.
STATUS_NAMES = {
    "slp": "SLP",
    "frz": "FRZ",
    "par": "PAR",
    "tox": "TOX",
    "brn": "BRN",
    "psn": "PSN",
}

BOOST_STATS = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")


class SnapshotTooOldError(ValueError):
    """A trace written before the snapshot recorded which Pokemon were brought.

    Refused rather than accepted, because the failure is silent otherwise: our
    side counts six against an opponent that can only be counted as four, every
    material feature is offset by a constant, and the fit absorbs the offset
    into an intercept instead of reporting it. See `champions/search/evaluate.py`.
    """


@dataclass(frozen=True)
class Position:
    """One evaluated position and how the game it came from ended."""

    features: dict[str, float]
    #: 1 if the side this is scored for went on to win.
    label: int
    source: str
    battle_id: str
    turn: int

    def as_row(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "battle_id": self.battle_id,
            "turn": self.turn,
            "label": self.label,
            **self.features,
        }


# -- self-play ---------------------------------------------------------------


def from_trace_file(path: Path) -> list[Position]:
    """Positions from one agent-view trace.

    Returns nothing rather than raising for a trace with no result: a file still
    being written, or a run stopped mid-battle, is a normal thing to find in a
    trace directory and not an error.
    """
    events = read_events(path)
    if not events:
        return []

    result = next(
        (e["payload"].get("result") for e in reversed(events) if e.get("type") == "battle_end"),
        None,
    )
    if result not in {"win", "loss"}:
        return []
    label = 1 if result == "win" else 0
    battle_id = str(events[0].get("battle_id") or path.stem)

    out: list[Position] = []
    for event in events:
        if event.get("type") != "turn_start":
            continue
        snapshot = event["payload"].get("state")
        if not snapshot:
            continue
        require_bring(snapshot, path)
        out.append(
            Position(
                features=features(snapshot, PICKED_TEAM_SIZE),
                label=label,
                source="selfplay",
                battle_id=battle_id,
                turn=int(snapshot.get("turn", event["payload"].get("turn", 0))),
            )
        )
    return out


def from_trace_dir(root: Path) -> list[Position]:
    out: list[Position] = []
    for path in sorted(Path(root).rglob("*.jsonl")):
        out.extend(from_trace_file(path))
    return out


def require_bring(snapshot: dict[str, Any], path: Path) -> None:
    ours = snapshot.get("ours", {})
    seen = [p for p in ours.get("active", []) if p] + list(ours.get("bench", []))
    if seen and not any("selected" in p for p in seen):
        raise SnapshotTooOldError(
            f"{path}: no `selected` on our side, so the bring is unknown and every "
            "material feature is offset by a constant. Re-run the games rather "
            "than fitting this."
        )


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a trace being tailed can end mid-line
    return events


# -- the corpus --------------------------------------------------------------


@dataclass
class Mon:
    """One Pokemon as the protocol has described it so far."""

    species: str
    hp_pct: float = 100.0
    fainted: bool = False
    status: str | None = None
    boosts: dict[str, int] = field(default_factory=dict)
    active: bool = False
    revealed: bool = False

    def as_snapshot(self, known: bool) -> dict[str, Any]:
        return {
            "species": self.species,
            "hp_pct": 0.0 if self.fainted else self.hp_pct,
            "fainted": self.fainted,
            "status": self.status,
            "boosts": {stat: value for stat, value in self.boosts.items() if value},
            "active": self.active,
            "known": known,
            "selected": True,
        }


class Board:
    """The observable state of a replay, rebuilt turn by turn.

    Deliberately not a general reimplementation of `protocol/state.py`: it
    reconstructs only what `evaluate.features` reads, and it produces the same
    snapshot shape so the features still come from one implementation. Anything
    beyond that would be a second state tracker to keep in step with the first.
    """

    def __init__(self, brought: dict[str, set[str]] | None = None) -> None:
        # Seeded with each side's whole bring, unrevealed and at full health,
        # rather than grown as Pokemon appear. A player knows their own four
        # from team preview, so a side counted only as what has walked on so far
        # is two Pokemon short on turn 1 -- which scored the opening position of
        # every corpus game at a two-Pokemon deficit for both players at once.
        # `revealed` stays False until something is actually observed, so the
        # opponent view is censored exactly as it was before.
        self.mons: dict[str, dict[str, Mon]] = {
            side: {name: Mon(species=name) for name in (brought or {}).get(side, ())}
            for side in ("p1", "p2")
        }
        self.active: dict[str, str | None] = {}
        self.conditions: dict[str, dict[str, int]] = {"p1": {}, "p2": {}}

    def feed(self, observation: parser.Observation) -> None:
        side, species = observation.side, observation.species
        if observation.kind == parser.FIELD:
            self.field(observation)
            return
        if side not in self.mons or not species:
            return
        mon = self.mons[side].setdefault(species, Mon(species=species))
        mon.revealed = True

        if observation.kind == parser.SWITCH:
            self.switch(observation, side, mon)
        elif observation.kind in (parser.DAMAGE, parser.HEAL):
            self.hp(observation, mon)
        elif observation.kind == parser.FAINT:
            mon.fainted, mon.hp_pct, mon.active = True, 0.0, False
            if observation.slot:
                self.active[observation.slot] = None
        elif observation.kind == parser.STATUS:
            mon.status = STATUS_NAMES.get((observation.value or "").lower())
        elif observation.kind == parser.BOOST:
            self.boost(observation, mon)

    def switch(self, observation: parser.Observation, side: str, mon: Mon) -> None:
        slot = observation.slot
        if slot:
            leaving = self.active.get(slot)
            if leaving and leaving in self.mons[side]:
                # Boosts are cleared on switch out; a stale set would be scored.
                self.mons[side][leaving].active = False
                self.mons[side][leaving].boosts = {}
            self.active[slot] = mon.species
        mon.active = True
        mon.boosts = {}
        self.hp(observation, mon, from_detail=True)

    def hp(self, observation: parser.Observation, mon: Mon, from_detail: bool = False) -> None:
        raw = observation.detail.get("hp") if from_detail else observation.value
        parsed = parse_hp(raw)
        if parsed is None:
            if raw and "fnt" in raw:
                mon.fainted, mon.hp_pct = True, 0.0
            return
        current, maximum = parsed
        if maximum > 0:
            mon.hp_pct = round(100.0 * current / maximum, 1)
        mon.fainted = current <= 0

    def boost(self, observation: parser.Observation, mon: Mon) -> None:
        stat = (observation.value or "").lower()
        if stat not in BOOST_STATS:
            return
        try:
            amount = int(observation.detail.get("amount", 0))
        except (TypeError, ValueError):
            return
        # Clamped to the legal range, which is what keeps a `-setboost` (which
        # reports a resulting stage rather than a delta) from compounding into a
        # stage no game can hold.
        mon.boosts[stat] = max(-6, min(6, mon.boosts.get(stat, 0) + amount))

    def field(self, observation: parser.Observation) -> None:
        event = observation.detail.get("event")
        side = observation.side
        if side not in self.conditions or event not in ("sidestart", "sideend"):
            return
        name = SIDE_CONDITION_NAMES.get(strip_effect(observation.value))
        if name is None:
            return
        if event == "sideend":
            self.conditions[side].pop(name, None)
        else:
            self.conditions[side][name] = self.conditions[side].get(name, 0) + 1

    def snapshot(self, viewpoint: str, brought: dict[str, set[str]]) -> dict[str, Any]:
        """The position as the player on `viewpoint` could see it."""
        other = "p2" if viewpoint == "p1" else "p1"
        return {
            "turn": 0,
            "ours": self.side(viewpoint, brought[viewpoint], known=True),
            "theirs": self.side(other, None, known=False),
            "side_conditions": dict(self.conditions[viewpoint]),
            "opponent_side_conditions": dict(self.conditions[other]),
        }

    def side(self, side: str, brought: set[str] | None, known: bool) -> dict[str, Any]:
        """One side, in the shape `evaluate.features` expects.

        `brought` is given for our own side only. For the opponent it is None
        and only revealed Pokemon are listed, which is what makes a corpus
        position carry the same information a live one does.
        """
        if brought is None:
            mons = [mon for mon in self.mons[side].values() if mon.revealed]
        else:
            mons = [mon for name, mon in self.mons[side].items() if name in brought]
        # `active` holds None for an empty slot, the same way `protocol/state.py`
        # emits it, so a side with nothing on the field is two gaps rather than
        # an empty list. Annotated because the two branches have different
        # element types and the union is the point.
        active: list[dict[str, Any] | None] = [m.as_snapshot(known) for m in mons if m.active]
        bench = [m.as_snapshot(known) for m in mons if not m.active]
        return {
            "active": active or [None, None],
            "bench": bench,
            "remaining": sum(1 for m in mons if not m.fainted),
            "revealed": len(mons),
        }


def strip_effect(value: str | None) -> str:
    """`move: Tailwind` and `Tailwind` are the same condition."""
    text = (value or "").strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    return text.strip().lower()


def from_replay_log(log: str, battle_id: str) -> list[Position]:
    """Positions from one scraped replay, one row per turn per side.

    Both viewpoints are emitted with opposite labels. A model fit on one side
    only would learn whatever asymmetry the log's choice of `p1` carries.
    """
    state, observations = parser.parse_log(log)
    winner = winner_side(log)
    if winner is None:
        return []

    brought = brought_sides(observations)
    board = Board(brought)
    out: list[Position] = []
    current = 0

    for observation in observations:
        # Scored at the *start* of each turn, before that turn's own events are
        # folded in, which is the position the player actually chose from and
        # the same moment `turn_start` records on the self-play side. Scoring
        # after the turn instead is an off-by-one that labels every row with the
        # number of the turn that produced it rather than the turn it faced.
        if observation.turn > current:
            current = observation.turn
            out.extend(rows(board, brought, winner, battle_id, current))
        board.feed(observation)

    # No row for the final position: the game is over, `faint_swing` short
    # circuits it to 0 or 1, and a terminal state teaches a calibration fit
    # nothing except that decided games are decided.
    return out


def rows(
    board: Board, brought: dict[str, set[str]], winner: str, battle_id: str, turn: int
) -> Iterator[Position]:
    for side in ("p1", "p2"):
        snapshot = board.snapshot(side, brought)
        snapshot["turn"] = turn
        yield Position(
            features=features(snapshot, PICKED_TEAM_SIZE),
            label=1 if side == winner else 0,
            source="corpus",
            battle_id=f"{battle_id}:{side}",
            turn=turn,
        )


def brought_sides(observations: Iterable[parser.Observation]) -> dict[str, set[str]]:
    """Which Pokemon each side actually played.

    Taken over the whole log, which uses information from later in the game to
    describe an earlier position -- and is correct anyway, because a player
    knows their own bring from team preview onward. The one gap is a Pokemon
    brought and never sent out, which no line in the log distinguishes from one
    that was left behind.
    """
    brought: dict[str, set[str]] = {"p1": set(), "p2": set()}
    for observation in observations:
        if observation.side in brought and observation.species:
            brought[observation.side].add(observation.species)

    # Padded to the bring. Everyone brings four; a side seen with three played
    # three, and the fourth sat at full health in the back the whole game. Left
    # unpadded, our own side was counted as three against an opponent the other
    # branch always counts as four, and turn 1 of an even game came out
    # asymmetric in 94 of 297 replays.
    for side, seen in brought.items():
        seen.update(f"__unplayed:{side}:{i}__" for i in range(PICKED_TEAM_SIZE - len(seen)))
    return brought


def winner_side(log: str) -> str | None:
    """Which side won, or None for a tie or an unfinished log.

    `|win|` names a player, not a side, so it is resolved through the `|player|`
    lines. A replay whose result cannot be read is dropped rather than guessed.
    """
    names: dict[str, str] = {}
    winner_name: str | None = None
    for line in log.splitlines():
        if line.startswith("|player|"):
            parts = line.split("|")
            if len(parts) > 3 and parts[2] in ("p1", "p2") and parts[3]:
                names[parts[3].strip()] = parts[2]
        elif line.startswith("|win|"):
            winner_name = line.split("|", 2)[2].strip()
        elif line.strip() in ("|tie", "|tie|"):
            # Matched exactly. `startswith("|tie")` also matches `|tier|`, which
            # every log carries, and silently made the whole corpus unusable.
            return None
    if winner_name is None:
        return None
    return names.get(winner_name)

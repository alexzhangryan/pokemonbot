"""Observations to evidence: what the protocol stream actually implies.

`champions/protocol/parser.py` says what happened, one line at a time. That is
the right granularity for a corpus and the wrong one for inference: "Metagross
used Ice Punch" and "Sinistcha is at 48/100" are two observations and one
inequality, and the inequality only exists because the first came immediately
before the second.

So this module folds the ordered stream into the four things the belief filter
can actually reason from:

- a **reveal** -- a move, item or ability that was directly seen, which is a
  hard constraint on the categorical half;
- **speed evidence** -- one Pokemon moved before another at equal priority,
  which is a strict inequality between two effective Speeds and is the only
  Speed evidence the protocol ever gives (D32, and the reason observations
  carry a monotonic `seq` at all);
- **damage dealt to us** -- exact, in HP, against a defender whose stats we know
  exactly, which bounds their offensive investment;
- **damage dealt to them** -- quantized to percent, against a defender whose
  maximum HP we do not know, which bounds their bulk and their HP *jointly*.

Everything here is a pure function of the observation stream plus the small
amount of state a single line cannot supply -- current HP per slot, live boosts,
weather -- which is the same division of labour `ParserState` already makes.

## What it refuses to infer

A damage figure carrying a `[from]` tag is residual or item damage rather than
the move's, so it is not attributed to the preceding move. A move called by
another move (`[from] move: Sleep Talk`) is not ordering evidence, because the
Pokemon acted at the calling move's priority. And a Pokemon whose boosts changed
earlier in the same turn is still usable, because boosts are tracked as the turn
resolves rather than read once from a turn-start snapshot -- which is the
difference between an Icy Wind turn producing a wrong Speed bound and producing
a right one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from champions.dex.loader import Dex, to_id
from champions.dex.stats import STAT_IDS
from champions.protocol import parser

#: Statuses that halve effective Speed. Kept here rather than imported from the
#: payoff model so the two can disagree loudly rather than drift quietly.
PARALYSIS_SPEED_FACTOR = 0.5
TAILWIND_SPEED_FACTOR = 2.0

_HP = re.compile(r"^(\d+)\s*/\s*(\d+)")
_FAINTED = re.compile(r"^0(\s|$)")


@dataclass(frozen=True)
class Actor:
    """Who a piece of evidence is about.

    `species` is the base species and is the key everything downstream is keyed
    on -- a Pokemon that Mega Evolves mid-battle is still one entry in the
    belief, and its stat points do not change when its forme does. `forme` is
    what it currently *is*, which is what the base stats have to come from:
    Greninja-Mega has 142 base Speed against Greninja's 122, and reading the
    base forme's numbers for a mega'd Pokemon makes every damage and Speed
    inference about it wrong. Mega Evolution is back in Champions and 75 Mega
    Stones are legal, so this is the common case rather than an exotic one.
    """

    side: str
    slot: str | None
    species: str | None
    forme: str | None = None
    #: Current types, which are not always the species' types: Protean rewrites
    #: them on every move, and both STAB and effectiveness follow.
    types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reveal:
    """A directly observed move, item or ability. A hard constraint."""

    turn: int
    seq: int
    actor: Actor
    kind: str  # "move" | "item" | "ability"
    value: str
    #: How it was seen: "used", "item", "enditem", "mega", "attributed", "ability".
    how: str = "used"

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "side": self.actor.side,
            "species": self.actor.species,
            "kind": self.kind,
            "value": self.value,
            "how": self.how,
        }


@dataclass(frozen=True)
class SpeedEvidence:
    """`faster` acted before `slower` at equal priority in the same turn.

    Trick Room reverses the implication rather than negating a number, which is
    the same thing for ordering and avoids reproducing arithmetic Champions
    removed. `trick_room` is carried so the consumer applies the reversal once.
    """

    turn: int
    seq: int
    faster: Actor
    slower: Actor
    trick_room: bool
    #: Everything multiplying the raw Speed stat other than the held item:
    #: paralysis, which is per Pokemon, and Tailwind, which is per side. Both
    #: are captured when the ordering happened rather than looked up later, so
    #: a resample can replay this evidence without reconstructing the field.
    faster_modifier: float
    slower_modifier: float
    faster_boost: int = 0
    slower_boost: int = 0
    #: The moves that were used. An ability can raise a move's priority --
    #: Prankster on a status move is the common one -- and then the ordering is
    #: evidence about the ability rather than about Speed. The consumer needs
    #: the moves to tell the two apart.
    faster_move: str | None = None
    slower_move: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "faster": self.faster.species,
            "slower": self.slower.species,
            "trick_room": self.trick_room,
        }


@dataclass(frozen=True)
class DamageEvidence:
    """One attack landing, with everything needed to run the damage layer on it.

    `lost` is in HP when the defender is ours and in percentage points when it
    is theirs, which is exactly the asymmetry `CLAUDE.md` constraint 5 is about.
    `denominator` says which: 100 means percent.
    """

    turn: int
    seq: int
    attacker: Actor
    defender: Actor
    move_id: str
    lost: float
    denominator: int
    spread: bool
    attacker_boosts: Mapping[str, int]
    defender_boosts: Mapping[str, int]
    weather: str | None
    attacker_status: str | None
    #: Types as they were when the hit landed, empty when nothing has changed
    #: them and the species entry is authoritative. Resolved here rather than by
    #: the consumer because Protean's `typechange` arrives between the `|move|`
    #: line and the `-damage` it caused, so the attacker's own `Actor` -- built
    #: when the move was announced -- is already stale by one message.
    attacker_types: tuple[str, ...] = ()
    defender_types: tuple[str, ...] = ()
    #: Current forme, when it is not the base species. Mega Evolution changes
    #: base stats, so the inference has to read them from the right entry.
    attacker_forme: str | None = None
    defender_forme: str | None = None
    #: A critical hit multiplies damage by 1.5 before the roll. Recorded rather
    #: than filtered out, so the consumer can choose to use the observation with
    #: the crit applied instead of discarding a perfectly good inequality.
    crit: bool = False

    @property
    def is_percent(self) -> bool:
        return self.denominator == 100

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "attacker": self.attacker.species,
            "defender": self.defender.species,
            "move": self.move_id,
            "lost": self.lost,
            "denominator": self.denominator,
            "spread": self.spread,
        }


Evidence = Reveal | SpeedEvidence | DamageEvidence


def parse_hp(value: str | None) -> tuple[int, int] | None:
    """`"48/100 par"` -> `(48, 100)`; `"0 fnt"` -> `(0, 0)`; anything else None."""
    if not value:
        return None
    match = _HP.match(value.strip())
    if match:
        return int(match.group(1)), int(match.group(2))
    if _FAINTED.match(value.strip()):
        return 0, 0
    return None


def _status_of(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.split()
    return parts[-1].upper() if len(parts) > 1 and parts[-1].isalpha() else None


@dataclass
class EvidenceBuilder:
    """Folds observations into evidence, carrying what a single one cannot say.

    One per battle, fed the observations from each turn in order. It is the
    live-battle mirror of what an offline pass over `reveals` would do, and both
    go through this same class so an offline evaluation and a live filter cannot
    disagree about what a turn implied.
    """

    dex: Dex
    hp: dict[str, tuple[int, int]] = field(default_factory=dict)
    boosts: dict[str, dict[str, int]] = field(default_factory=dict)
    statuses: dict[str, str | None] = field(default_factory=dict)
    formes: dict[str, str] = field(default_factory=dict)
    types: dict[str, tuple[str, ...]] = field(default_factory=dict)
    species_at: dict[str, Actor] = field(default_factory=dict)
    weather: str | None = None
    trick_room: bool = False
    tailwind: set[str] = field(default_factory=set)
    #: The last move used, per turn, with the actor -- damage lines that follow
    #: it with no `[from]` tag belong to it.
    _pending_move: tuple[Actor, str, int] | None = None
    _hit_this_move: set[str] = field(default_factory=set)
    _crit_slot: str | None = None
    _turn_order: list[tuple[Actor, int, int, str]] = field(default_factory=list)
    _turn: int = 0

    def feed(self, observations: Iterable[parser.Observation]) -> list[Evidence]:
        out: list[Evidence] = []
        for observation in observations:
            out.extend(self._one(observation))
        return out

    # -- per observation ------------------------------------------------

    def _one(self, observation: parser.Observation) -> list[Evidence]:  # noqa: C901
        if observation.turn != self._turn:
            self._turn = observation.turn
            self._turn_order = []
            self._pending_move = None

        slot = observation.slot or ""
        actor = Actor(
            observation.side,
            observation.slot,
            to_id(observation.species),
            forme=self.formes.get(slot),
            types=self.types.get(slot, ()),
        )
        if observation.slot:
            self.species_at[observation.slot] = actor

        handler = {
            parser.SWITCH: self._switch,
            parser.MOVE: self._move,
            parser.DAMAGE: self._damage,
            parser.HEAL: self._heal,
            parser.BOOST: self._boost,
            parser.ITEM: self._item,
            parser.ABILITY: self._ability,
            parser.FIELD: self._field,
            parser.FAINT: self._faint,
            parser.STATUS: self._status_observation,
            parser.EFFECT: self._effect,
            parser.FORME: self._forme,
        }.get(observation.kind)
        return handler(observation, actor) if handler else []

    def _switch(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        if observation.slot:
            self.boosts[observation.slot] = {}
            self._crit_slot = None
            parsed = parse_hp(observation.detail.get("hp"))
            if parsed:
                self.hp[observation.slot] = parsed
            self.statuses[observation.slot] = _status_of(observation.detail.get("hp"))
            # A switch resets forme and types to whatever came back in. The
            # detail carries the forme when it differs from the base species.
            forme = to_id(observation.detail.get("forme"))
            if forme:
                self.formes[observation.slot] = forme
            else:
                self.formes.pop(observation.slot, None)
            self.types.pop(observation.slot, None)
        self._pending_move = None
        return []

    def _faint(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        if observation.slot:
            current = self.hp.get(observation.slot)
            self.hp[observation.slot] = (0, current[1] if current else 0)
        return []

    def _move(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        move_id = to_id(observation.value)
        if not move_id:
            return []
        out: list[Evidence] = [
            Reveal(self._turn, observation.seq, actor, "move", move_id, how="used")
        ]

        called = bool(observation.detail.get("via"))
        self._pending_move = (actor, move_id, observation.seq)
        self._hit_this_move = set()
        if called:
            return out

        entry = self.dex.moves.get(move_id)
        priority = int(entry.get("priority", 0) or 0) if entry else 0

        # Only the immediately preceding actor. Anything further back is
        # implied by transitivity, and emitting it too would triple the
        # evidence count without adding an inequality.
        if self._turn_order:
            previous_actor, previous_priority, _, _ = self._turn_order[-1]
            if previous_priority == priority and previous_actor.slot != actor.slot:
                out.append(
                    SpeedEvidence(
                        turn=self._turn,
                        seq=observation.seq,
                        faster=previous_actor,
                        slower=actor,
                        trick_room=self.trick_room,
                        faster_modifier=self._speed_modifier(previous_actor),
                        slower_modifier=self._speed_modifier(actor),
                        faster_boost=self.boosts_for(previous_actor.slot).get("spe", 0),
                        slower_boost=self.boosts_for(actor.slot).get("spe", 0),
                        faster_move=self._turn_order[-1][3],
                        slower_move=move_id,
                    )
                )

        self._turn_order.append((actor, priority, observation.seq, move_id))
        return out

    def _damage(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        parsed = parse_hp(observation.value)
        if parsed is None or not observation.slot:
            return []
        before = self.hp.get(observation.slot)
        current, maximum = parsed
        if maximum == 0 and before:
            maximum = before[1]
        self.hp[observation.slot] = (current, maximum)

        if observation.detail.get("via") or self._pending_move is None or before is None:
            return []
        attacker, move_id, seq = self._pending_move
        if attacker.side == actor.side and attacker.slot == actor.slot:
            # Self-inflicted (confusion, Struggle recoil without a tag). Not
            # evidence about anyone's stats under a model that has neither.
            return []
        lost = float(before[0] - current)
        if lost <= 0:
            return []

        entry = self.dex.moves.get(move_id)
        if not entry or entry.get("category") == "Status":
            return []
        if entry.get("multihit"):
            # Several `-damage` lines share one `-move`, and each carries only
            # its own hit. Attributing the first as if it were the whole attack
            # would under-read the attacker's investment on every one of them.
            return []
        if observation.slot in self._hit_this_move:
            return []
        self._hit_this_move.add(observation.slot)

        crit = self._crit_slot == observation.slot
        self._crit_slot = None

        return [
            DamageEvidence(
                turn=self._turn,
                seq=observation.seq,
                attacker=attacker,
                defender=actor,
                move_id=move_id,
                lost=lost,
                denominator=maximum or before[1],
                spread=str(entry.get("target", "")) in SPREAD_TARGETS,
                attacker_boosts=dict(self.boosts.get(attacker.slot or "", {})),
                defender_boosts=dict(self.boosts.get(observation.slot, {})),
                weather=self.weather,
                attacker_status=self._status(attacker),
                crit=crit,
                attacker_types=self.types.get(attacker.slot or "", ()),
                defender_types=self.types.get(observation.slot, ()),
                attacker_forme=self.formes.get(attacker.slot or ""),
                defender_forme=self.formes.get(observation.slot),
            )
        ]

    def _heal(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        parsed = parse_hp(observation.value)
        if parsed and observation.slot:
            self.hp[observation.slot] = parsed
        return []

    def _boost(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        if not observation.slot:
            return []
        stat = str(observation.value or "").lower()
        if stat not in STAT_IDS:
            return []
        try:
            amount = int(observation.detail.get("amount", 0))
        except (TypeError, ValueError):
            return []
        table = self.boosts.setdefault(observation.slot, {})
        table[stat] = max(-6, min(6, table.get(stat, 0) + amount))
        return []

    def _item(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        item = to_id(observation.value)
        if not item:
            return []
        how = str(observation.detail.get("how") or "item")
        return [Reveal(self._turn, observation.seq, actor, "item", item, how=how)]

    def _ability(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        ability = to_id(observation.value)
        if not ability:
            return []
        how = str(observation.detail.get("how") or "ability")
        return [Reveal(self._turn, observation.seq, actor, "ability", ability, how=how)]

    def _field(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        event = str(observation.detail.get("event") or "")
        value = to_id(observation.value)
        if event == "weather":
            self.weather = None if value in ("none", "") else value
        elif event == "fieldstart" and "trickroom" in value:
            self.trick_room = True
        elif event == "fieldend" and "trickroom" in value:
            self.trick_room = False
        elif event == "sidestart" and "tailwind" in value:
            self.tailwind.add(observation.side)
        elif event == "sideend" and "tailwind" in value:
            self.tailwind.discard(observation.side)
        return []

    def _status_observation(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        if observation.slot:
            self.statuses[observation.slot] = str(observation.value or "").upper() or None
        return []

    def _effect(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        event = observation.detail.get("event")
        if event in ("curestatus", "cureteam") and observation.slot:
            self.statuses[observation.slot] = None
        elif event == "crit":
            # `-crit` names the target and precedes that target's `-damage`.
            self._crit_slot = observation.slot
        elif to_id(observation.value) == "typechange" and observation.slot:
            # `|-start|p2a: Greninja|typechange|Ice|[from] ability: Protean`.
            # Without this the filter reads a Protean user's resisted hit as
            # bulk it does not have, and its STAB attack as an attack stat it
            # does not have -- measured, on a checked-in test team, as pinning
            # both the wrong stats to their caps.
            raw = observation.detail.get("args") or []
            parts = [t.strip() for chunk in raw for t in str(chunk).split("/") if t.strip()]
            if parts:
                self.types[observation.slot] = tuple(parts)
        return []

    def _forme(self, observation: parser.Observation, actor: Actor) -> list[Evidence]:
        forme = to_id(observation.value)
        if forme and observation.slot:
            self.formes[observation.slot] = forme
            entry = self.dex.species.get(forme)
            if entry and entry.get("types"):
                self.types[observation.slot] = tuple(entry["types"])
        return []

    # -- helpers --------------------------------------------------------

    def _status(self, actor: Actor) -> str | None:
        return self.statuses.get(actor.slot or "")

    def _speed_modifier(self, actor: Actor) -> float:
        """Everything multiplying the raw Speed stat except the held item.

        Paralysis, which is per Pokemon, and Tailwind, which is per side. The
        held item is excluded because it is exactly what is being hypothesised:
        a Pokemon that outran something either invested in Speed or held a
        Choice Scarf, and the filter has to be able to conclude either.
        """
        modifier = PARALYSIS_SPEED_FACTOR if self._status(actor) == "PAR" else 1.0
        if actor.side in self.tailwind:
            modifier *= TAILWIND_SPEED_FACTOR
        return modifier

    def boosts_for(self, slot: str | None) -> dict[str, int]:
        return dict(self.boosts.get(slot or "", {}))


#: Move targets that hit more than one Pokemon and so take the 0.75 spread
#: modifier. Read as strings because the dex stores them that way.
SPREAD_TARGETS = frozenset({"allAdjacentFoes", "allAdjacent"})

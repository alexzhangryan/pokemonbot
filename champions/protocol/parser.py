"""Showdown protocol log to observations.

One parser, two consumers. The replay corpus (M3) runs it over scraped logs to
build the behavioural record; the belief filter (M5) will run it over the live
protocol log to update its particles. Writing it twice would guarantee that the
thing trained offline and the thing running online disagree, which is the
failure mode this module exists to prevent.

The blueprint's interface is `parser.apply(state, line) -> list[Observation]`
(`docs/08-implementation-blueprint.md` section 3), so that is the shape here:
`ParserState` carries what a line cannot supply on its own -- the current turn,
which nickname belongs to which species, which slot is active, and whether we
are in the choice phase or the residual phase -- and `apply` is a pure function
of the two.

Three things are deliberate.

**Order is data.** Observations carry a monotonic `seq`. The order of `|move|`
lines within a turn is the only Speed evidence the protocol ever gives, and
`docs/03-belief-filter.md` propagates stat intervals from exactly that. A
consumer that stores observations unordered has thrown away half the spread
inference before it starts.

**Attribution is generic.** `[from] ability: Drizzle` and `[from] item: Life
Orb` reveal an ability or an item regardless of which message carried them, and
`[of] p2b: Pelipper` says whose. One rule over every line beats twenty rules,
one per message type, and it keeps working when Showdown adds a message.

**Incompleteness is counted, not silent.** Unrecognised message types land in
`ParserState.unhandled` rather than being dropped. That makes parser coverage a
number a test can assert on, and makes a Showdown protocol change show up as a
rising count instead of as quietly missing evidence.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# The protocol's own vocabulary, kept as constants because these strings are
# matched in several places and a typo in one of them fails silently.
SIDES = ("p1", "p2")

#: Phases within a turn. Switches mean different things in each: a switch in
#: `action` was chosen this turn, a switch in `residual` is a forced replacement
#: after a faint, and neither is a choice the way a move is.
PHASE_PREVIEW = "preview"
PHASE_ACTION = "action"
PHASE_RESIDUAL = "residual"

#: Observation kinds. A closed vocabulary so consumers can switch on it.
MOVE = "move"
SWITCH = "switch"
ITEM = "item"
ABILITY = "ability"
FORME = "forme"
STATUS = "status"
DAMAGE = "damage"
HEAL = "heal"
BOOST = "boost"
FAINT = "faint"
EFFECT = "effect"
FIELD = "field"
TERA = "tera"

_IDENT = re.compile(r"^(p[12])([a-z]?):\s*(.*)$")
_RATING = re.compile(r"^(.*)'s rating: (\d+) &rarr; <strong>(\d+)</strong>")
_BESTOF = re.compile(r'href="/(game-bestof\d+-[a-z0-9-]+)"')
_GAME_NUMBER = re.compile(r"<strong>Game (\d+)</strong>")


@dataclass(frozen=True, slots=True)
class Observation:
    """One thing an observer of the protocol learned.

    `species` is resolved through the nickname table rather than taken from the
    line, because a nicknamed Pokemon reports its nickname in every message
    after the switch that introduced it.
    """

    seq: int
    turn: int
    side: str
    slot: str | None
    species: str | None
    kind: str
    value: str | None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "turn": self.turn,
            "side": self.side,
            "slot": self.slot,
            "species": self.species,
            "attribute": self.kind,
            "value": self.value,
            "detail": self.detail,
        }


@dataclass
class ParserState:
    """What a single line cannot tell you."""

    turn: int = 0
    phase: str = PHASE_PREVIEW
    seq: int = 0
    #: side -> nickname -> species. Populated by `|switch|`, `|drag|`, `|replace|`.
    nicknames: dict[str, dict[str, str]] = field(default_factory=lambda: {s: {} for s in SIDES})
    #: slot ("p1a") -> current nickname.
    active: dict[str, str] = field(default_factory=dict)
    #: Message types seen but not turned into observations, with counts.
    unhandled: Counter[str] = field(default_factory=Counter)
    #: True once any `|move|` has been seen this turn; switches before the first
    #: move of a turn are still choices, switches after a faint are not.
    saw_move_this_turn: bool = False

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def species_of(self, side: str, nickname: str) -> str | None:
        return self.nicknames.get(side, {}).get(nickname)


def split_ident(ident: str) -> tuple[str, str, str] | None:
    """`"p1a: Sinistcha"` -> `("p1", "p1a", "Sinistcha")`.

    Side-only idents (`"p1: Alex"`, used by `|-sidestart|`) return an empty
    slot, which is why the slot is returned separately from the side rather
    than sliced off by the caller.
    """
    match = _IDENT.match(ident)
    if not match:
        return None
    side, letter, name = match.groups()
    return side, f"{side}{letter}" if letter else "", name


def species_from_details(details: str) -> str:
    """`"Delphox, L50, M"` -> `"Delphox"`. Also handles `shiny` and no level."""
    return details.split(",")[0].strip()


def _tags(parts: list[str]) -> dict[str, str]:
    """The bracketed tags on a message: `[from] item: Life Orb`, `[of] p2b: X`."""
    tags: dict[str, str] = {}
    for part in parts:
        stripped = part.strip()
        if stripped.startswith("["):
            key, _, value = stripped.partition("]")
            tags[key[1:].strip()] = value.strip()
    return tags


def _effect(value: str) -> tuple[str | None, str]:
    """`"ability: Drizzle"` -> `("ability", "Drizzle")`; bare text -> `(None, text)`."""
    kind, sep, name = value.partition(":")
    if sep:
        return kind.strip().lower(), name.strip()
    return None, value.strip()


#: Message types that carry no belief-relevant information, or whose content the
#: replay-level parser reads out of the header directly. Listed explicitly so
#: that anything genuinely new lands in `unhandled` instead of being lost here.
IGNORED = frozenset(
    {
        "",
        "j",
        "join",
        "l",
        "leave",
        "c",
        "chat",
        "c:",
        "n",
        "name",
        "t:",
        "html",
        "uhtml",
        "raw",
        "player",
        "teamsize",
        "gametype",
        "gen",
        "tier",
        "rated",
        "rule",
        "clearpoke",
        "poke",
        "teampreview",
        "showteam",
        "start",
        "inactive",
        "inactiveoff",
        "upkeep",
        "turn",
        "win",
        "tie",
        "expire",
        "debug",
        "message",
        "-message",
        "-anim",
        "-hint",
        "-nothing",
        "-center",
        "-combine",
        "-waiting",
        "-hitcount",
        "-ohko",
        "-notarget",
        "-mustrecharge",
        "-prepare",
        "-zpower",
        "-zbroken",
        "seed",
        "split",
        "request",
        "error",
        "-block",
        # Live-only. The websocket sends a room header that the replay API
        # strips before publishing, so these appear on one source and not the
        # other -- which is the reason the parser is checked against both.
        "init",
        "title",
        "deinit",
        "noinit",
        "queryresponse",
        "updatesearch",
        "updateuser",
        "popup",
        "pm",
        "formats",
        "challstr",
        "usercount",
        "badge",
        "bigerror",
        "chatmsg",
    }
)


#: Messages that reveal an effect rather than a value: an ability activating, a
#: move's side effect starting or ending, an immunity firing. They are kept as
#: one kind because the belief filter cares that the effect happened and which
#: Pokemon it happened to, not which of twenty message types announced it.
EFFECT_MESSAGES = frozenset(
    {
        "-activate",
        "-start",
        "-end",
        "-immune",
        "-singleturn",
        "-singlemove",
        "-fail",
        "-miss",
        "-crit",
        "-supereffective",
        "-resisted",
        "-transform",
        "-swapboost",
        "-clearboost",
        "-copyboost",
        "-invertboost",
        "-clearnegativeboost",
        "-curestatus",
        "-cureteam",
        "cant",
        "-endability",
        "-primal",
        "-burst",
    }
)


#: Messages about the field or a whole side rather than about one Pokemon. Some
#: carry an ident and some do not -- `|-clearallboost|` has none at all -- which
#: is why they are handled apart from the per-Pokemon messages.
FIELD_MESSAGES = frozenset(
    {
        "-weather",
        "-fieldstart",
        "-fieldend",
        "-sidestart",
        "-sideend",
        "-clearallboost",
        "-fieldactivate",
        "-swapsideconditions",
        "-activateteam",
    }
)


def apply(state: ParserState, line: str) -> list[Observation]:  # noqa: C901
    """Fold one protocol line into `state` and return what it revealed.

    Mutates `state`; returns the observations, possibly none. Never raises on a
    malformed or unfamiliar line -- a corpus of a hundred thousand replays will
    contain protocol this parser has not seen, and a scraper that dies on one of
    them is worse than one that counts it.
    """
    if not line.startswith("|"):
        return []
    parts = line.split("|")[1:]
    if not parts:
        return []
    kind = parts[0]
    args = parts[1:]
    tags = _tags(args)
    out: list[Observation] = []

    # Phase and turn bookkeeping first: several handlers below read them.
    if kind == "turn":
        state.turn = int(args[0]) if args and args[0].isdigit() else state.turn + 1
        state.phase = PHASE_ACTION
        state.saw_move_this_turn = False
        return []
    if kind == "start":
        state.phase = PHASE_ACTION
        return []
    if kind == "upkeep":
        state.phase = PHASE_RESIDUAL
        return []

    def emit(
        side: str,
        slot: str | None,
        species: str | None,
        obs_kind: str,
        value: str | None,
        **detail: Any,
    ) -> None:
        out.append(
            Observation(
                seq=state.next_seq(),
                turn=state.turn,
                side=side,
                slot=slot or None,
                species=species,
                kind=obs_kind,
                value=value,
                detail={k: v for k, v in detail.items() if v is not None and v is not False},
            )
        )

    def subject(index: int = 0) -> tuple[str, str, str, str | None] | None:
        """Side, slot, nickname and resolved species of the ident at `args[index]`."""
        if len(args) <= index:
            return None
        parsed = split_ident(args[index])
        if parsed is None:
            return None
        side, slot, nickname = parsed
        return side, slot, nickname, state.species_of(side, nickname)

    def register(side: str, slot: str, nickname: str, details: str) -> str:
        """Bind a nickname to its species and mark it active in its slot.

        The base species is what stays bound, even across a `detailschange`, so
        one Pokemon keeps one identity through a battle it spends in two formes.
        Corpus joins and belief particles are both keyed on that identity.
        """
        species = species_from_details(details)
        table = state.nicknames.setdefault(side, {})
        base = table.get(nickname)
        if base is None:
            table[nickname] = species
            base = species
        if slot:
            state.active[slot] = nickname
        return base

    if kind in ("switch", "drag", "replace"):
        parsed = subject()
        if parsed is None:
            return []
        side, slot, nickname, _ = parsed
        details = args[1] if len(args) > 1 else ""
        base = register(side, slot, nickname, details)
        forme = species_from_details(details)
        if kind == "drag":
            how = "drag"
        elif state.turn == 0:
            how = "lead"
        elif "from" in tags:
            how = "pivot"
        elif state.phase == PHASE_RESIDUAL:
            how = "replacement"
        else:
            how = "voluntary"
        emit(
            side,
            slot,
            base,
            SWITCH,
            base,
            how=how,
            via=tags.get("from"),
            nickname=nickname if nickname != base else None,
            forme=forme if forme != base else None,
            hp=args[2] if len(args) > 2 else None,
        )
        return out

    if kind == "move":
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        state.saw_move_this_turn = True
        move = args[1] if len(args) > 1 else None
        target = args[2] if len(args) > 2 and not args[2].startswith("[") else None
        emit(
            side,
            slot,
            species,
            MOVE,
            move,
            target=target,
            via=tags.get("from"),
            miss="miss" in tags,
            notarget="notarget" in tags,
        )
    elif kind in ("detailschange", "-formechange"):
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        details = args[1] if len(args) > 1 else ""
        emit(
            side,
            slot,
            species,
            FORME,
            species_from_details(details),
            permanent=kind == "detailschange",
        )
    elif kind == "-mega":
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        emit(side, slot, species, ITEM, args[2] if len(args) > 2 else None, how="mega")
        emit(side, slot, species, FORME, f"{args[1]}-Mega" if len(args) > 1 else None, mega=True)
    elif kind in ("-item", "-enditem"):
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        emit(
            side,
            slot,
            species,
            ITEM,
            args[1] if len(args) > 1 else None,
            how=kind.lstrip("-"),
            via=tags.get("from"),
        )
    elif kind == "-ability":
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        emit(side, slot, species, ABILITY, args[1] if len(args) > 1 else None, how="ability")
    elif kind == "-status":
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        emit(side, slot, species, STATUS, args[1] if len(args) > 1 else None, via=tags.get("from"))
    elif kind in ("-damage", "-heal", "-sethp"):
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        emit(
            side,
            slot,
            species,
            DAMAGE if kind == "-damage" else HEAL,
            args[1] if len(args) > 1 else None,
            via=tags.get("from"),
            of=tags.get("of"),
        )
    elif kind in ("-boost", "-unboost", "-setboost"):
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        amount = args[2] if len(args) > 2 else "0"
        emit(
            side,
            slot,
            species,
            BOOST,
            args[1] if len(args) > 1 else None,
            amount=f"-{amount}" if kind == "-unboost" else amount,
            via=tags.get("from"),
        )
    elif kind == "faint":
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        emit(side, slot, species, FAINT, species)
    elif kind == "-terastallize":
        # Champions disables Terastallization. If this ever fires, the pinned mod
        # has changed under us and the whole damage layer is suspect (D5).
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        emit(side, slot, species, TERA, args[1] if len(args) > 1 else None)
    elif kind in FIELD_MESSAGES:
        ident = split_ident(args[0]) if args else None
        side, slot = (ident[0], ident[1]) if ident else ("", "")
        value = args[1] if ident and len(args) > 1 else (args[0] if args else None)
        emit(side, slot, None, FIELD, value, event=kind.lstrip("-"), via=tags.get("from"))
    elif kind in EFFECT_MESSAGES:
        parsed = subject()
        if parsed is None:
            return []
        side, slot, _, species = parsed
        effect_kind, effect_name = _effect(args[1]) if len(args) > 1 else (None, "")
        emit(
            side,
            slot,
            species,
            EFFECT,
            effect_name or None,
            event=kind.lstrip("-"),
            effect=effect_kind,
            via=tags.get("from"),
            # The positional arguments past the effect name, untagged and
            # unparsed. Most effects have none; the ones that do carry their
            # payload here rather than in a field per message type. `typechange`
            # is why this exists -- Protean rewrites a Pokemon's types mid-turn
            # and the belief filter's damage inference is wrong about both STAB
            # and effectiveness without it -- but the same slot serves any
            # future effect whose arguments matter.
            args=[a for a in args[2:] if not a.strip().startswith("[")] or None,
        )
    elif kind not in IGNORED:
        state.unhandled[kind] += 1
        return []

    # The generic attribution rule. `[from] ability: Drizzle` reveals an ability
    # and `[from] item: Life Orb` reveals an item, on whatever message happens to
    # carry the tag, and `[of]` says whose when it is not the subject's. One rule
    # over every line beats one rule per message type, and it keeps working when
    # Showdown adds a message.
    source = tags.get("from")
    if source:
        effect_kind, effect_name = _effect(source)
        if effect_kind in ("ability", "item") and effect_name:
            owner = split_ident(tags.get("of") or (args[0] if args else ""))
            if owner is not None:
                side, slot, nickname = owner
                out.append(
                    Observation(
                        seq=state.next_seq(),
                        turn=state.turn,
                        side=side,
                        slot=slot or None,
                        species=state.species_of(side, nickname),
                        kind=ABILITY if effect_kind == "ability" else ITEM,
                        value=effect_name,
                        detail={"how": "attributed", "on": kind},
                    )
                )
    return out


def parse_log(log: str) -> tuple[ParserState, list[Observation]]:
    """Run `apply` over a whole log. The convenience wrapper both callers use."""
    state = ParserState()
    observations: list[Observation] = []
    for line in log.splitlines():
        observations.extend(apply(state, line))
    return state, observations

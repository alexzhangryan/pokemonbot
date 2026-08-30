"""What each player could see, rebuilt from a protocol log.

The corpus holds 697,582 labelled human decisions and no states to go with them.
`champions/protocol/parser.py` turns a log into *observations* -- facts learned,
one per line -- which is what M3 needed and is not a board. Fitting a policy
prior needs the board: the thing the player was looking at when they chose.

So this replays a log and maintains one, in the exact shape
`champions.protocol.state.snapshot()` produces during play. The shape is not a
convenience. `docs/specs/2026-08-29-learned-policy-provider.md` section 3.2 has
one feature function serving both the trainer and the live agent, and that only
works if both are handed the same kind of thing.

## Two properties this has to have

**It is the observer's view, not the log's.** A replay is a spectator stream and
shows both teams; `|showteam|` shows their whole set. Our agent declines Open
Team Sheets (`CLAUDE.md` constraint 2), so a model fit on what a spectator knows
is fit on information the agent will never have. `view()` therefore takes a side
and reveals the opponent only as play revealed them -- and `|showteam|` is
dropped on the floor rather than merely unused, so no later change can quietly
start reading it.

**It agrees with the live snapshot.** `state.snapshot()` builds the same thing
from a poke-env battle. If the two disagree the model is served different inputs
from the ones it was fit on, silently and only in production. `turn_start`
carries both the snapshot the agent saw and that turn's protocol lines, so
`tests/test_replay_state.py` checks the agreement against real traces rather
than asserting it.

## What a replay cannot contain

Exact HP, stat spreads and PP, for *either* side: the spectator stream reports
percentages throughout. Those fields are emitted as None rather than guessed,
and the feature set is built on fractions for exactly this reason. A number that
looks computed and is invented is worse than a missing one.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from champions.dex.loader import Dex
from champions.protocol.parser import species_from_details, split_ident

SIDES = ("p1", "p2")

#: Protecting moves, for `protect_counter`. The same set `payoff.PROTECTING_MOVES`
#: honours, kept here rather than imported so the corpus layer does not depend on
#: the search layer.
PROTECT_LIKE = {"protect", "detect", "spikyshield", "banefulbunker", "burningbulwark"}

#: The line that carries whole registered sets. Dropped rather than parsed: see
#: the module docstring. Reg M-B on the Bo3 ladder forces these and Champions has
#: no such mechanism.
#:
#: `|poke|` is *not* in here and must not be. Team preview shows species only,
#: both players see it, and our agent sees it too -- it is Open Team *Sheets*
#: that get declined, not preview. Dropping `|poke|` costs the four Pokemon a
#: side has not led with, which is most of what "how many do they have left"
#: means.
SHEET_LINES = {"showteam"}


def _identifier(text: str) -> str:
    """Showdown's id form: lowercase, alphanumerics only."""
    return "".join(c for c in text.lower() if c.isalnum())


@dataclass
class _Mon:
    """One Pokemon as the observer has seen it."""

    species: str
    nickname: str = ""
    level: int = 50
    gender: str | None = None
    hp_pct: float = 100.0
    status: str | None = None
    fainted: bool = False
    boosts: dict[str, int] = field(default_factory=dict)
    effects: set[str] = field(default_factory=set)
    revealed_moves: list[str] = field(default_factory=list)
    last_move: str | None = None
    item: str | None = None
    ability: str | None = None
    protect_counter: int = 0
    #: Set when this Pokemon switches in, read and cleared at the next `|turn|`.
    just_switched: bool = False
    first_turn: bool = False
    used_protect: bool = False

    def reveal_move(self, move_id: str) -> None:
        if move_id and move_id not in self.revealed_moves:
            self.revealed_moves.append(move_id)


class Observer:
    """Replays a protocol stream and answers what either side could see.

    One observer per battle. `feed` takes lines in order; `view` may be called
    at any point and returns that side's state as of the last line fed.
    """

    def __init__(self, dex: Dex | None = None) -> None:
        self._dex = dex
        self.turn = 0
        self._players: dict[str, str] = {}
        #: side -> the Pokemon seen or previewed, in the order they appeared.
        #: A list rather than a dict because preview names a species with no
        #: nickname attached, and the nickname only arrives when it switches in.
        self._team: dict[str, list[_Mon]] = {side: [] for side in SIDES}
        #: slot ("p1a") -> index into `_team[side]`.
        self._active: dict[str, int | None] = {}
        self.weather: dict[str, int] = {}
        self.fields: dict[str, int] = {}
        self._side_conditions: dict[str, dict[str, int]] = {side: {} for side in SIDES}

    # -- feeding ---------------------------------------------------------

    def feed(self, line: str) -> None:
        if not line.startswith("|"):
            return
        parts = line.split("|")[1:]
        if not parts:
            return
        kind, args = parts[0], parts[1:]

        if kind in SHEET_LINES:
            # Deliberately dropped. See the module docstring: this is the one
            # place open-sheet information could enter, so it is refused here
            # rather than filtered out later.
            return

        handler = getattr(self, f"_on_{kind.lstrip('-').replace('-', '_')}", None)
        if handler is not None:
            handler(args)

    def _on_player(self, args: list[str]) -> None:
        if len(args) >= 2 and args[0] in SIDES:
            self._players[args[0]] = args[1]

    def _on_poke(self, args: list[str]) -> None:
        """Team preview: a species, and nothing else about it.

        Registered so that "how many do they have left" is six rather than the
        one or two that have led. The set behind it stays unknown, which is the
        difference between preview and a sheet.
        """
        if len(args) < 2 or args[0] not in SIDES:
            return
        self._team[args[0]].append(
            _Mon(
                species=_identifier(species_from_details(args[1])),
                **_details(args[1]),
            )
        )

    def _on_turn(self, args: list[str]) -> None:
        self.turn = int(args[0]) if args and args[0].isdigit() else self.turn + 1
        for team in self._team.values():
            for mon in team:
                # `first_turn` is what Fake Out turns on, and it is true during
                # the turn *after* the switch resolves rather than during it.
                mon.first_turn = mon.just_switched
                mon.just_switched = False
                # Only the reset. The increment happens as the Protect
                # resolves, because a knockout hands the other player a forced
                # switch and that is a decision point inside the turn.
                if not mon.used_protect:
                    mon.protect_counter = 0
                mon.used_protect = False

    def _on_switch(self, args: list[str]) -> None:
        self._bring_in(args)

    _on_drag = _on_switch
    _on_replace = _on_switch

    def _bring_in(self, args: list[str]) -> None:
        parsed = split_ident(args[0]) if args else None
        if parsed is None:
            return
        side, slot, nickname = parsed
        # Identifier form, because `state.snapshot()` records poke-env's
        # `pokemon.species` and that is what the feature function will be
        # handed during play. The display form is the protocol's, not ours.
        species = _identifier(species_from_details(args[1]) if len(args) > 1 else nickname)

        index = self._find(side, nickname, species)
        if index is None:
            self._team[side].append(
                _Mon(
                    species=species,
                    nickname=nickname,
                    **_details(args[1] if len(args) > 1 else ""),
                )
            )
            index = len(self._team[side]) - 1
        mon = self._team[side][index]
        mon.nickname = nickname
        mon.just_switched = True
        # Boosts and volatile effects do not survive a switch; status does.
        mon.boosts = {}
        mon.effects = set()
        if len(args) > 2:
            _apply_hp(mon, args[2])
        self._active[slot] = index

    def _find(self, side: str, nickname: str, species: str) -> int | None:
        """This Pokemon, by nickname if it has been seen, else by preview slot.

        A species may legally appear twice on a team, so an unclaimed preview
        entry is matched rather than the first entry of that species: claiming
        the wrong one would put a fainted Pokemon back on the field.
        """
        for i, mon in enumerate(self._team[side]):
            if mon.nickname == nickname and mon.species == species:
                return i
        for i, mon in enumerate(self._team[side]):
            if not mon.nickname and mon.species == species:
                return i
        return None

    def _on_move(self, args: list[str]) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is None or len(args) < 2:
            return
        move_id = _identifier(args[1])
        mon.reveal_move(move_id)
        mon.last_move = move_id

    def _on_damage(self, args: list[str]) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is not None and len(args) > 1:
            _apply_hp(mon, args[1])

    _on_heal = _on_damage
    _on_sethp = _on_damage

    def _on_faint(self, args: list[str]) -> None:
        """A faint empties the slot, it does not leave a corpse standing in it.

        This is what `state.snapshot()` records, because poke-env's
        `active_pokemon` holds None for a slot awaiting its replacement, and the
        agent gets a turn in that state -- the forced switch. Leaving the
        fainted Pokemon on the field would offer the policy a slot that cannot
        act.
        """
        parsed = split_ident(args[0]) if args else None
        mon = self._mon(args[0] if args else "")
        if mon is not None:
            mon.fainted = True
            mon.hp_pct = 0.0
        if parsed is not None:
            self._active[parsed[1]] = None

    def _on_status(self, args: list[str]) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is not None and len(args) > 1:
            mon.status = args[1].upper()

    def _on_curestatus(self, args: list[str]) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is not None:
            mon.status = None

    def _on_boost(self, args: list[str]) -> None:
        self._boost(args, sign=1)

    def _on_unboost(self, args: list[str]) -> None:
        self._boost(args, sign=-1)

    def _boost(self, args: list[str], sign: int) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is None or len(args) < 3:
            return
        stage = mon.boosts.get(args[1], 0) + sign * int(args[2])
        stage = max(-6, min(6, stage))
        if stage:
            mon.boosts[args[1]] = stage
        else:
            mon.boosts.pop(args[1], None)

    def _on_setboost(self, args: list[str]) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is not None and len(args) >= 3:
            mon.boosts[args[1]] = int(args[2])

    def _on_clearboost(self, args: list[str]) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is not None:
            mon.boosts = {}

    def _on_clearallboost(self, args: list[str]) -> None:
        for team in self._team.values():
            for mon in team:
                mon.boosts = {}

    def _on_singleturn(self, args: list[str]) -> None:
        """A one-turn effect took hold. For Protect, that it actually worked.

        `protect_counter` exists because consecutive Protects fail with rising
        probability, so it has to count successes: `|move|` is emitted for the
        attempt too, and the attempt that failed is exactly the one that resets
        the counter.
        """
        mon = self._mon(args[0] if args else "")
        if mon is None or len(args) < 2:
            return
        if _identifier(args[1].rpartition(":")[2]) in PROTECT_LIKE:
            mon.used_protect = True
            mon.protect_counter += 1

    def _on_item(self, args: list[str]) -> None:
        """An item revealed *by play*, which is legitimate information."""
        mon = self._mon(args[0] if args else "")
        if mon is not None and len(args) > 1:
            mon.item = _identifier(args[1])

    def _on_enditem(self, args: list[str]) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is not None:
            mon.item = None

    def _on_ability(self, args: list[str]) -> None:
        mon = self._mon(args[0] if args else "")
        if mon is not None and len(args) > 1:
            mon.ability = _identifier(args[1])

    def _on_weather(self, args: list[str]) -> None:
        name = args[0] if args else "none"
        if name in ("none", ""):
            self.weather = {}
            return
        # `[upkeep]` re-announces the weather already up, which sets the same
        # key to the same value. Only one weather is ever active.
        self.weather = {_identifier(name).upper(): self.turn}

    def _on_fieldstart(self, args: list[str]) -> None:
        if args:
            self.fields[_condition(args[0])] = self.turn

    def _on_fieldend(self, args: list[str]) -> None:
        if args:
            self.fields.pop(_condition(args[0]), None)

    def _on_sidestart(self, args: list[str]) -> None:
        side = _side_of(args[0]) if args else None
        if side and len(args) > 1:
            self._side_conditions[side][_condition(args[1])] = self.turn

    def _on_sideend(self, args: list[str]) -> None:
        side = _side_of(args[0]) if args else None
        if side and len(args) > 1:
            self._side_conditions[side].pop(_condition(args[1]), None)

    def _mon(self, ident: str) -> _Mon | None:
        parsed = split_ident(ident)
        if parsed is None:
            return None
        side, slot, nickname = parsed
        for mon in self._team[side]:
            if mon.nickname == nickname:
                return mon
        # A line addressed to a slot whose occupant was never named, which
        # happens when a log is joined mid-battle.
        index = self._active.get(slot)
        return self._team[side][index] if index is not None else None

    # -- reading ---------------------------------------------------------

    def view(self, side: str) -> dict[str, Any]:
        """The board as `side` could see it, shaped like `state.snapshot()`."""
        opponent = "p2" if side == "p1" else "p1"
        return {
            "turn": self.turn,
            "player_role": side,
            "player_username": self._players.get(side),
            "opponent_username": self._players.get(opponent),
            "weather": dict(self.weather),
            "fields": dict(self.fields),
            "side_conditions": dict(self._side_conditions[side]),
            "opponent_side_conditions": dict(self._side_conditions[opponent]),
            "ours": self._side_view(side, known=True),
            "theirs": self._side_view(opponent, known=False),
            "constraints": {},
        }

    def _side_view(self, side: str, known: bool) -> dict[str, Any]:
        on_field = [self._active.get(f"{side}{letter}") for letter in ("a", "b")]
        team = self._team[side]
        if not known:
            # The opponent's team is what has *appeared*, not what preview
            # named. poke-env fills `opponent_team` as Pokemon switch in, and
            # `state.snapshot()` counts it, so a view that counted preview
            # entries would report six against a live agent's two and every
            # "how many are left" feature would be reading a different quantity
            # in training than in play.
            keep = {i for i, mon in enumerate(team) if mon.nickname}
            on_field = [i for i in on_field if i is None or i in keep]
            team = [mon for i, mon in enumerate(team) if i in keep]
            index_of = {old: new for new, old in enumerate(sorted(keep))}
            on_field = [None if i is None else index_of[i] for i in on_field]
        return {
            "active": [
                None if i is None or i >= len(team) else self._mon_view(team[i], known)
                for i in on_field
            ],
            "bench": [
                self._mon_view(mon, known)
                for i, mon in enumerate(team)
                if i not in [j for j in on_field if j is not None]
            ],
            "remaining": sum(1 for mon in team if not mon.fainted),
            "revealed": len(team),
        }

    def _mon_view(self, mon: _Mon, known: bool) -> dict[str, Any]:
        entry = self._species_entry(mon.species)
        common: dict[str, Any] = {
            "species": mon.species,
            "name": mon.nickname or mon.species,
            "level": mon.level,
            "types": list(entry["types"]) if entry else [],
            "base_stats": dict(entry["baseStats"]) if entry else {},
            "hp_pct": 0.0 if mon.fainted else round(mon.hp_pct, 1),
            "status": mon.status,
            "status_counter": 0,
            "fainted": mon.fainted,
            "active": True,
            "boosts": dict(mon.boosts),
            "effects": sorted(mon.effects),
            "must_recharge": False,
            "preparing": False,
            "protect_counter": mon.protect_counter,
            "first_turn": mon.first_turn,
        }
        if known:
            # `known` says whose side it is, not that a replay contains exact
            # numbers. It does not, for either side, so these are None and the
            # features are fractions.
            return {
                **common,
                "known": True,
                "selected": True,
                "hp": None,
                "max_hp": None,
                "item": mon.item,
                "ability": mon.ability,
                "stats": None,
                "moves": [{"id": m} for m in mon.revealed_moves],
            }
        return {
            **common,
            "known": False,
            "hp": None,
            "max_hp": None,
            "hp_is_percent": True,
            "item": mon.item,
            "ability": mon.ability,
            "possible_abilities": [],
            "stats": None,
            "revealed_moves": [{"id": m} for m in mon.revealed_moves],
            "last_move": mon.last_move,
        }

    def _species_entry(self, species: str) -> dict[str, Any] | None:
        if self._dex is None:
            return None
        return self._dex.species.get(_identifier(species))


def turn_states(
    lines: Iterable[str], dex: Dex | None = None
) -> Iterator[tuple[int, dict[str, dict[str, Any]]]]:
    """`(turn, {"p1": view, "p2": view})` at the start of every turn.

    A turn is where both players chose, so it is where a training row comes
    from: the state at `|turn|n`, before any move of that turn resolves.
    """
    observer = Observer(dex)
    pending = False
    for line in lines:
        if line.startswith("|turn|"):
            observer.feed(line)
            pending = True
            continue
        if pending:
            yield observer.turn, {side: observer.view(side) for side in SIDES}
            pending = False
        observer.feed(line)
    if pending:
        yield observer.turn, {side: observer.view(side) for side in SIDES}


# -- line helpers ------------------------------------------------------------


def _details(details: str) -> dict[str, Any]:
    """Level and gender out of a `Species, L50, M` detail string."""
    out: dict[str, Any] = {}
    for part in (p.strip() for p in details.split(",")[1:]):
        if part.startswith("L") and part[1:].isdigit():
            out["level"] = int(part[1:])
        elif part in ("M", "F"):
            out["gender"] = part
    return out


def _apply_hp(mon: _Mon, text: str) -> None:
    """`54/100`, `0 fnt`, `100/100 par`."""
    value = text.split("[")[0].strip()
    if not value:
        return
    head, *rest = value.split(" ")
    if head in ("0", "0/100") or "fnt" in rest:
        mon.hp_pct = 0.0
        mon.fainted = True
        return
    if "/" in head:
        numerator, _, denominator = head.partition("/")
        # `50/100g`: Showdown appends the HP bar's colour (g, y, r) when it
        # differs from what the client would compute. Stripping it is not
        # cosmetic -- parsed naively this raises, and swallowing the error keeps
        # the *previous* HP, which leaves the state plausible and wrong.
        current, maximum = _number(numerator), _number(denominator)
        if current is None or maximum is None or maximum <= 0:
            return
        mon.hp_pct = current / maximum * 100.0
    for token in rest:
        if token.lower() in ("brn", "par", "slp", "frz", "psn", "tox"):
            mon.status = token.upper()


def _number(text: str) -> float | None:
    digits = "".join(c for c in text if c.isdigit() or c == ".")
    try:
        return float(digits)
    except ValueError:
        return None


def _condition(text: str) -> str:
    """`move: Tailwind` and `Tailwind` both become `TAILWIND`."""
    _, _, name = text.rpartition(":")
    return name.strip().upper().replace(" ", "_")


def _side_of(text: str) -> str | None:
    head = text.split(":")[0].strip()
    return head if head in SIDES else None

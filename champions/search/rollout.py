"""The simulator payoff: the fidelity arm of the M8 engine gate.

`champions/search/payoff.py` resolves one turn analytically and lists what it
leaves out -- abilities, items, secondary effects, status effects, weather,
multi-hit, recoil, healing, accuracy. Its docstring also names the alternative:
step the real simulator, which models all of it because it is the game, and
which was unavailable only because stepping needs a complete opponent team.
This module is that alternative. Given a complete opponent team (the belief's
particle, or the gate's oracle), it materialises the current position inside
`js/sim_server.js`, steps every cell of the matrix on a clone, and reads the
result back through `win_prob`. `docs/specs/2026-09-13-engine-gate.md` 5.2 and
5.3 specify it; D70 fixes how it is judged.

## Common random numbers, the way a simulator allows

The analytic model needs no randomness: it enumerates the sixteen rolls and
buckets them. A simulator rolls, and the roll it makes depends on its PRNG
state. So every cell of one decision is stepped under the same `R` replicate
seeds, fixed per `(seed, battle, turn)`, and a cell's value is the mean over
replicates. Two cells then differ by what the actions did, not by which
rolls they happened to draw, which is what `docs/04-decision-engine.md`
section 4 asks for. There is no roll bucketing inside a simulator, so `R` is
the whole roll integration and is what buys variance down.

## What it cannot do, and says so

A position observed from one side of the field does not carry everything the
simulator needs: PP, Choice locks, Encore and Taunt turns, volatile durations,
the opponent's exact HP. `materialize` sets what the position carries and no
more, and a choice the real battle allowed but the materialised one refuses is
a cell this model cannot score. That cell takes the fallback model's value and
the decision counts it, because an arm that quietly scored a third of its
cells some other way would not be measuring what its name says.

A position with an empty slot on our side is a forced-switch decision and is
not materialised at all; `begin` returns False and the agent uses the fallback
for the whole decision. A fainted Pokemon still in its slot is different: at a
move request it means there is nothing to replace it with, and the position is
materialised with that slot passing.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from poke_env.battle import Field, SideCondition, Weather

from champions.dex.loader import Dex, to_id
from champions.search.evaluate import win_prob
from champions.search.oracle import SimServer
from champions.search.payoff import CellModel

#: Replicate seeds per cell. Two is where the cost per decision at `k = 10`
#: lands near a second; the measured cost decides whether it rises.
DEFAULT_REPLICATES = 2

FOE_TARGETS = {"normal", "any", "adjacentFoe"}
ALLY_TARGETS = {"adjacentAlly"}
ALLY_OR_SELF_TARGETS = {"adjacentAllyOrSelf"}

#: Side conditions poke-env counts in layers rather than dating by turn.
STACKABLE = {"SPIKES", "TOXIC_SPIKES"}

#: Default durations, in turns, for what poke-env dates by the turn it began:
#: side conditions and field effects, which the protocol announces once.
#: Weather is deliberately absent. Showdown re-announces it every turn as
#: `[upkeep]` and poke-env re-dates it each time, so the snapshot carries the
#: last upkeep turn rather than the start and no remaining duration can be
#: derived; the simulator gets the weather fresh, which over-lengthens it by
#: up to four turns. Anything absent is left to the simulator's default.
DURATIONS = {
    "TAILWIND": 4,
    "REFLECT": 5,
    "LIGHT_SCREEN": 5,
    "AURORA_VEIL": 5,
    "SAFEGUARD": 5,
    "MIST": 5,
    "LUCKY_CHANT": 5,
    "TRICK_ROOM": 5,
    "GRAVITY": 5,
    "MAGIC_ROOM": 5,
    "WONDER_ROOM": 5,
    "ELECTRIC_TERRAIN": 5,
    "GRASSY_TERRAIN": 5,
    "MISTY_TERRAIN": 5,
    "PSYCHIC_TERRAIN": 5,
}

#: Showdown id -> poke-env enum name, so a simulator state reads back in the
#: names `champions.search.evaluate` and the live snapshot speak.
SIDE_CONDITION_NAMES = {to_id(m.name): m.name for m in SideCondition}
FIELD_NAMES = {to_id(m.name): m.name for m in Field}
WEATHER_NAMES = {to_id(m.name): m.name for m in Weather}
TERRAINS = {name for name in FIELD_NAMES.values() if name.endswith("_TERRAIN")}


# -- the team text --------------------------------------------------------------


@dataclass(frozen=True)
class ExportSet:
    """One block of a Showdown export: its 1-based slot, nickname and species."""

    index: int
    nickname: str
    species: str
    text: str


def export_sets(team: str) -> list[ExportSet]:
    """The sets in a Showdown export, in file order.

    The first line is `Nickname (Species) @ Item`, `Species @ Item`, either
    with a trailing gender marker, or just the species. Only the head is
    parsed here; `champions.belief.evaluate.truth_from_team_file` reads the
    rest.
    """
    sets: list[ExportSet] = []
    for block in re.split(r"\n\s*\n", team.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        head = lines[0].split("@")[0].strip()
        head = re.sub(r"\s*\((M|F)\)\s*$", "", head)
        match = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", head)
        if match:
            nickname, species = match.group(1).strip(), match.group(2).strip()
        else:
            nickname = species = head
        sets.append(ExportSet(len(sets) + 1, nickname, species, block))
    return sets


def team_order(
    sets: Sequence[ExportSet],
    leads: Iterable[str],
    bench: Iterable[str],
    rng: np.random.Generator,
    picked: int = 4,
) -> list[int]:
    """The team preview choice as 1-based slots: leads, then the known bench,
    then a seeded draw among the rest until `picked` are named.

    Names are matched by nickname first and species second, which is the same
    rule `js/sim_server.js` uses to find a Pokemon, so the two agree on who is
    who.
    """
    order: list[int] = []
    for name in list(leads) + list(bench):
        index = _slot_of(sets, name)
        if index is not None and index not in order:
            order.append(index)
    remaining = [s.index for s in sets if s.index not in order]
    while len(order) < picked and remaining:
        order.append(remaining.pop(int(rng.integers(len(remaining)))))
    return order[:picked]


def _slot_of(sets: Sequence[ExportSet], name: str) -> int | None:
    wanted = (name or "").lower()
    for s in sets:
        if s.nickname.lower() == wanted:
            return s.index
    wanted_id = to_id(name)
    for s in sets:
        if to_id(s.species) == wanted_id or to_id(s.nickname) == wanted_id:
            return s.index
    return None


# -- the position, as the simulator takes it ------------------------------------


def remaining_turns(name: str, started: Any, turn: int) -> int | None:
    duration = DURATIONS.get(name)
    if duration is None or not isinstance(started, int | float):
        return None
    return max(1, duration - (int(turn) - int(started)))


def _conditions(conditions: dict[str, Any], turn: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, value in conditions.items():
        if name in STACKABLE:
            out[to_id(name)] = {"layers": int(value)}
        else:
            out[to_id(name)] = {"remaining": remaining_turns(name, value, turn)}
    return out


def _pokemon_spec(view: dict[str, Any], set_item: str | None) -> dict[str, Any]:
    known = bool(view.get("known"))
    spec: dict[str, Any] = {
        "name": view.get("name") or view.get("species"),
        "species": view.get("species"),
        "fainted": bool(view.get("fainted")),
        "hp_pct": float(view.get("hp_pct") or 0.0),
        "status": view.get("status"),
        "boosts": dict(view.get("boosts") or {}),
        "first_turn": bool(view.get("first_turn")),
        "protect_counter": int(view.get("protect_counter") or 0),
        "status_counter": int(view.get("status_counter") or 0),
    }
    if known and view.get("hp") is not None:
        spec["hp"] = int(view["hp"])
    # Our own item is known exactly, so "the set has one and we hold none" is a
    # consumed item. Theirs is unknown until revealed, so nothing is inferred.
    if known and set_item and not view.get("item"):
        spec["item_consumed"] = True
    return spec


def position_payload(
    snapshot: dict[str, Any],
    our_items: dict[str, str | None],
    their_items: dict[str, str | None],
) -> dict[str, Any]:
    """`materialize`'s `position`, from a snapshot in `state.snapshot()`'s shape.

    `our_items` / `their_items` map a Pokemon's name to the item its set
    registers, which is what tells a missing item from a consumed one.
    """
    role = snapshot.get("player_role") or "p1"
    other = "p2" if role == "p1" else "p1"
    turn = int(snapshot.get("turn") or 1)

    def side(view: dict[str, Any], items: dict[str, str | None]) -> dict[str, Any]:
        # Our own bench carries the two that were not brought (`selected`
        # False); the simulator only has the four that were.
        seen = [p for p in view["active"] if p is not None] + [
            p for p in view["bench"] if p.get("selected", True)
        ]
        return {
            "pokemon": [_pokemon_spec(p, items.get(p.get("name") or "")) for p in seen],
        }

    ours = side(snapshot["ours"], our_items)
    ours["conditions"] = _conditions(snapshot.get("side_conditions") or {}, turn)
    theirs = side(snapshot["theirs"], their_items)
    theirs["conditions"] = _conditions(snapshot.get("opponent_side_conditions") or {}, turn)

    position: dict[str, Any] = {
        "turn": turn,
        "weather": None,
        "terrain": None,
        "pseudo_weather": {},
        "sides": {role: ours, other: theirs},
    }
    for name, started in (snapshot.get("weather") or {}).items():
        position["weather"] = {"id": to_id(name), "remaining": remaining_turns(name, started, turn)}
    for name, started in (snapshot.get("fields") or {}).items():
        entry = {"id": to_id(name), "remaining": remaining_turns(name, started, turn)}
        if name in TERRAINS:
            position["terrain"] = entry
        else:
            position["pseudo_weather"][to_id(name)] = {"remaining": entry["remaining"]}
    return position


# -- choices ----------------------------------------------------------------------


def our_choice(described: dict[str, Any]) -> str:
    """poke-env's wire form without the `/choose ` prefix, which is what the
    simulator's `choose` takes."""
    message = str(described.get("message") or "")
    return message[8:] if message.startswith("/choose ") else message


def their_choice(
    column: dict[str, Any],
    dex: Dex,
    our_alive: Sequence[bool],
    their_alive: Sequence[bool],
) -> str:
    """A column of the matrix as the opponent's choice string.

    Columns come from `opponent_candidates`, which aims every move at our slot
    1; the simulator wants a target only for moves that take one, and a living
    one. Anything that is not a move -- the "unrevealed" placeholder -- becomes
    `default`, the simulator's own first legal move, which is an action rather
    than the nothing the analytic model scores it as, and is the honest choice
    when a column says "we do not know".
    """
    parts: list[str] = []
    slots = list(column.get("slots") or [])
    for index, alive in enumerate(their_alive):
        if not alive:
            parts.append("pass")
            continue
        slot = slots[index] if index < len(slots) else None
        if not slot or slot.get("kind") != "move":
            parts.append("default")
            continue
        entry = dex.moves.get(slot.get("move") or "")
        if not entry:
            parts.append("default")
            continue
        target_type = str(entry.get("target") or "normal")
        if target_type in FOE_TARGETS:
            wanted = int(slot.get("target") or 1)
            if not (1 <= wanted <= len(our_alive) and our_alive[wanted - 1]):
                living = [i + 1 for i, ok in enumerate(our_alive) if ok]
                wanted = living[0] if living else 1
            parts.append(f"move {entry['id']} {wanted}")
        elif target_type in ALLY_TARGETS:
            partner = 1 - index
            if 0 <= partner < len(their_alive) and their_alive[partner]:
                parts.append(f"move {entry['id']} -{partner + 1}")
            else:
                parts.append("default")
        elif target_type in ALLY_OR_SELF_TARGETS:
            parts.append(f"move {entry['id']} -{index + 1}")
        else:
            parts.append(f"move {entry['id']}")
    return ", ".join(parts) if parts else "default"


# -- reading the simulator back --------------------------------------------------


def pokemon_name(pokemon: dict[str, Any]) -> str:
    """A serialized Pokemon's nickname: the registered set's name, which is how
    both the protocol and `materialize` refer to it."""
    registered = pokemon.get("set") or {}
    return str(registered.get("name") or _ref_id(pokemon.get("species")))


def _ref_id(value: Any) -> str:
    """`[Species:incineroar]` -> `incineroar`; anything else through `to_id`."""
    text = str(value or "")
    if text.startswith("[") and ":" in text:
        return text[text.index(":") + 1 : -1]
    return to_id(text)


def _mon_view(pokemon: dict[str, Any], dex: Dex, known: bool, brought: bool) -> dict[str, Any]:
    species = _ref_id(pokemon.get("species"))
    entry = dex.species.get(species) or dex.species.get(_ref_id(pokemon.get("baseSpecies"))) or {}
    hp, max_hp = int(pokemon.get("hp") or 0), int(pokemon.get("maxhp") or 1)
    fainted = bool(pokemon.get("fainted")) or hp <= 0
    status = str(pokemon.get("status") or "")
    registered = pokemon.get("set") or {}
    common: dict[str, Any] = {
        "species": species,
        "name": registered.get("name") or entry.get("name") or species,
        "level": int(registered.get("level") or 50),
        "types": list(entry.get("types") or []),
        "base_stats": dict(entry.get("baseStats") or {}),
        "hp_pct": 0.0 if fainted else round(100.0 * hp / max_hp, 1),
        "status": None if fainted or not status or status == "fnt" else status.upper(),
        "status_counter": 0,
        "fainted": fainted,
        "active": bool(pokemon.get("isActive")),
        "boosts": {k: v for k, v in (pokemon.get("boosts") or {}).items() if v},
        "effects": [],
        "must_recharge": False,
        "preparing": False,
        "protect_counter": 0,
        "first_turn": False,
    }
    moves = [{"id": m.get("id"), "pp": m.get("pp")} for m in (pokemon.get("moveSlots") or [])]
    if known:
        return {
            **common,
            "known": True,
            "selected": brought,
            "hp": 0 if fainted else hp,
            "max_hp": max_hp,
            "item": pokemon.get("item") or None,
            "ability": pokemon.get("ability"),
            "stats": None,
            "moves": moves,
        }
    return {
        **common,
        "known": False,
        "hp": None,
        "max_hp": None,
        "hp_is_percent": True,
        "item": None,
        "ability": None,
        "possible_abilities": [],
        "stats": None,
        "revealed_moves": [],
        "last_move": None,
    }


def _side_view(
    side: dict[str, Any],
    dex: Dex,
    known: bool,
    keep: set[str] | None,
    active_slots: int,
) -> dict[str, Any]:
    pokemon = list(side.get("pokemon") or [])
    active: list[dict[str, Any] | None] = [None] * active_slots
    bench: list[dict[str, Any]] = []
    for p in pokemon:
        name = pokemon_name(p)
        wanted = keep is None or name in keep
        view = _mon_view(p, dex, known, brought=wanted)
        if p.get("isActive") and 0 <= int(p.get("position") or 0) < active_slots:
            active[int(p.get("position") or 0)] = view
        elif wanted:
            bench.append(view)
    seen = [p for p in active if p is not None] + bench
    return {
        "active": active,
        "bench": bench,
        "remaining": sum(1 for p in seen if not p["fainted"]),
        "revealed": len(seen),
    }


def sim_snapshot(
    state: dict[str, Any],
    role: str,
    dex: Dex,
    brought: set[str] | None = None,
    revealed: set[str] | None = None,
) -> dict[str, Any]:
    """A serialized battle, read back as `state.snapshot()` would have shown it
    from `role`'s side of the field.

    `brought` names our four; `revealed` names the opponent's Pokemon that have
    appeared, since the evaluation counts theirs by what has been seen and
    derives the rest from faints. Both default to everyone on the side.
    """
    sides = list(state.get("sides") or [])
    ours_index = 0 if role == "p1" else 1
    slots = int(state.get("activePerHalf") or 2)
    ours = _side_view(sides[ours_index], dex, True, brought, slots)
    theirs = _side_view(sides[1 - ours_index], dex, False, revealed, slots)
    field_state = state.get("field") or {}
    turn = int(state.get("turn") or 0)

    fields: dict[str, int] = {}
    terrain = _ref_id(field_state.get("terrain"))
    if terrain:
        fields[FIELD_NAMES.get(terrain, terrain.upper())] = turn
    for pseudo in field_state.get("pseudoWeather") or {}:
        fields[FIELD_NAMES.get(to_id(pseudo), pseudo.upper())] = turn
    weather: dict[str, int] = {}
    weather_id = to_id(field_state.get("weather") or "")
    if weather_id:
        weather[WEATHER_NAMES.get(weather_id, weather_id.upper())] = turn

    def conditions(side: dict[str, Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        for cid, data in (side.get("sideConditions") or {}).items():
            name = SIDE_CONDITION_NAMES.get(to_id(cid), str(cid).upper())
            out[name] = int(data.get("layers") or 1) if name in STACKABLE else turn
        return out

    return {
        "turn": turn,
        "player_role": role,
        "player_username": None,
        "opponent_username": None,
        "weather": weather,
        "fields": fields,
        "side_conditions": conditions(sides[ours_index]),
        "opponent_side_conditions": conditions(sides[1 - ours_index]),
        "ours": ours,
        "theirs": theirs,
        "constraints": {},
    }


# -- the model ----------------------------------------------------------------------


@dataclass
class RolloutStats:
    """What one decision cost and how much of it the simulator actually scored."""

    cells: int = 0
    fallback_cells: int = 0
    rejected: int = 0
    materialize_s: float = 0.0
    step_s: float = 0.0
    problems: list[str] = field(default_factory=list)
    materialized: bool = False
    #: Why the decision was not materialised at all, when it was not: a forced
    #: switch or an empty slot is not a position a turn is decided from, and
    #: the analytic fallback scores it without that counting against the
    #: simulator.
    skipped: str | None = None
    #: The first few refusals, as the simulator worded them, with the choice
    #: that was refused. Capped so a bad decision does not bloat the trace.
    refusals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cells": self.cells,
            "fallback_cells": self.fallback_cells,
            "rejected_choices": self.rejected,
            "materialize_s": self.materialize_s,
            "step_s": self.step_s,
            "materialize_problems": list(self.problems),
            "materialized": self.materialized,
            "skipped": self.skipped,
            "refusals": list(self.refusals),
        }


REFUSALS_KEPT = 5


def replicate_seed(seed: int, battle_tag: str, turn: int, replicate: int) -> list[int]:
    """Four 16-bit words, the simulator's seed shape, derived per decision."""
    key = f"{seed}:{battle_tag}:{turn}:{replicate}".encode()
    digest = hashlib.sha256(key).digest()
    return [int.from_bytes(digest[i : i + 2], "big") for i in range(0, 8, 2)]


class RolloutModel:
    """Values a cell by stepping the simulator from the materialised position.

    `begin` once per decision, `value` per cell (through `payoff_matrix`,
    since it has `CellModel`'s signature), `end` when the matrix is built.
    The opponent's six sets come from `their_team`, a Showdown export -- in the
    gate, the oracle's copy of the truth; later, a belief particle rendered
    into the same format.
    """

    def __init__(
        self,
        sim: SimServer,
        dex: Dex,
        format_id: str,
        our_team: str,
        their_team: str,
        fallback: CellModel,
        seed: int = 0,
        replicates: int = DEFAULT_REPLICATES,
        picked_team_size: int | None = None,
        our_items: dict[str, str | None] | None = None,
        their_items: dict[str, str | None] | None = None,
    ) -> None:
        self._sim = sim
        self._dex = dex
        self._format = format_id
        self._our_team = our_team
        self._their_team = their_team
        self._our_sets = export_sets(our_team)
        self._their_sets = export_sets(their_team)
        self._our_items = our_items or {}
        self._their_items = their_items or {}
        self._fallback = fallback
        self._seed = seed
        self._replicates = max(1, replicates)
        self._picked = picked_team_size or dex.picked_team_size
        self._handle: int | None = None
        self._snapshot: dict[str, Any] | None = None
        self._role = "p1"
        self._brought: set[str] = set()
        self._revealed: set[str] = set()
        self._our_alive: list[bool] = []
        self._their_alive: list[bool] = []
        self._seeds: list[list[int]] = []
        self.stats = RolloutStats()

    @property
    def replicates(self) -> int:
        return self._replicates

    # -- per decision ----------------------------------------------------

    def begin(self, snapshot: dict[str, Any], battle_tag: str) -> bool:
        """Materialise `snapshot`. False when the position cannot be stepped
        from -- an empty slot on our side -- in which case nothing is held."""
        self.end()
        self.stats = RolloutStats()
        self._snapshot = snapshot
        self._role = str(snapshot.get("player_role") or "p1")
        other = "p2" if self._role == "p1" else "p1"
        turn = int(snapshot.get("turn") or 1)

        # A forced switch is answered mid-turn, with a fainted slot awaiting its
        # replacement; the simulator would materialise the *next* turn's move
        # request instead, and refuse the switch. Not a position a turn is
        # decided from, so the analytic fallback takes it and says why. An
        # empty slot is the same request seen by an older snapshot. A fainted
        # Pokemon still in its slot at a move request means the bench is empty,
        # and that slot passes, which the simulator takes.
        forced = (snapshot.get("constraints") or {}).get("force_switch")
        if forced is True or (isinstance(forced, list) and any(forced)):
            self.stats.skipped = "forced switch"
            return False
        our_active = list(snapshot["ours"]["active"])
        if not our_active or any(p is None for p in our_active):
            self.stats.skipped = "empty slot"
            return False
        their_active = [p for p in snapshot["theirs"]["active"] if p is not None]
        if not their_active:
            self.stats.skipped = "no opponent on the field"
            return False

        rng = np.random.default_rng(
            int.from_bytes(replicate_seed_bytes(self._seed, battle_tag, turn), "big")
        )
        our_bench = [p for p in snapshot["ours"]["bench"] if p.get("selected", True)]
        our_order = team_order(
            self._our_sets,
            [p["name"] for p in our_active],
            [p["name"] for p in our_bench],
            rng,
            self._picked,
        )
        their_bench = list(snapshot["theirs"]["bench"])
        their_order = team_order(
            self._their_sets,
            [p["name"] for p in their_active],
            [p["name"] for p in their_bench],
            rng,
            self._picked,
        )
        if len(our_order) < self._picked or len(their_order) < self._picked:
            self.stats.problems.append("could not name a full bring for both sides")
            return False

        players = {
            self._role: {
                "name": "us",
                "team": self._our_team,
                "order": ",".join(map(str, our_order)),
            },
            other: {
                "name": "them",
                "team": self._their_team,
                "order": ",".join(map(str, their_order)),
            },
        }
        position = position_payload(snapshot, self._our_items, self._their_items)

        started = time.perf_counter()
        result = self._sim.call(
            "materialize",
            formatId=self._format,
            seed=replicate_seed(self._seed, battle_tag, turn, -1),
            p1=players["p1"],
            p2=players["p2"],
            position=position,
        )
        self.stats.materialize_s = time.perf_counter() - started
        self.stats.problems.extend(str(p) for p in result.get("problems") or [])
        if result.get("requestState") != "move":
            self._sim.destroy(int(result["handle"]))
            return False

        self._handle = int(result["handle"])
        self._brought = {p["name"] for p in our_active} | {p["name"] for p in our_bench}
        self._revealed = {p["name"] for p in their_active} | {p["name"] for p in their_bench}
        self._our_alive = [not p.get("fainted") for p in our_active]
        self._their_alive = [
            p is not None and not p.get("fainted") for p in snapshot["theirs"]["active"]
        ]
        self._seeds = [
            replicate_seed(self._seed, battle_tag, turn, r) for r in range(self._replicates)
        ]
        self.stats.materialized = True
        return True

    def end(self) -> None:
        if self._handle is not None:
            try:
                self._sim.destroy(self._handle)
            finally:
                self._handle = None
        self._snapshot = None

    # -- per cell --------------------------------------------------------

    def value(
        self,
        snapshot: dict[str, Any],
        our_action: dict[str, Any],
        their_action: dict[str, Any],
    ) -> float:
        self.stats.cells += 1
        if self._handle is None or snapshot is not self._snapshot:
            self.stats.fallback_cells += 1
            return self._fallback.value(snapshot, our_action, their_action)

        ours = our_choice(our_action)
        theirs = their_choice(their_action, self._dex, self._our_alive, self._their_alive)
        role, other = self._role, ("p2" if self._role == "p1" else "p1")

        values: list[float] = []
        started = time.perf_counter()
        for seed in self._seeds:
            clone = int(self._sim.clone(self._handle)["handle"])
            try:
                result = self._sim.call(
                    "step", handle=clone, choices={role: ours, other: theirs}, seed=seed
                )
                accepted = result.get("accepted") or {}
                if not (accepted.get(role) and accepted.get(other)):
                    self.stats.rejected += 1
                    if len(self.stats.refusals) < REFUSALS_KEPT:
                        errors = [
                            line
                            for line in result.get("log", [])[-8:]
                            if line.startswith("|error|")
                        ]
                        refused = "ours" if not accepted.get(role) else "theirs"
                        self.stats.refusals.append(
                            f"{refused}: {ours if refused == 'ours' else theirs!r} -- "
                            + (errors[-1] if errors else "no error line")
                        )
                    break
                state = self._sim.serialize(clone)
            finally:
                self._sim.destroy(clone)
            after = sim_snapshot(state, role, self._dex, self._brought, self._revealed)
            values.append(win_prob(after, self._picked))
        self.stats.step_s += time.perf_counter() - started

        if len(values) < len(self._seeds):
            self.stats.fallback_cells += 1
            return self._fallback.value(snapshot, our_action, their_action)
        return float(sum(values) / len(values))

    def row(
        self,
        snapshot: dict[str, Any],
        our_action: dict[str, Any],
        their_actions: list[dict[str, Any]],
    ) -> list[float]:
        return [self.value(snapshot, our_action, theirs) for theirs in their_actions]


def replicate_seed_bytes(seed: int, battle_tag: str, turn: int) -> bytes:
    return hashlib.sha256(f"{seed}:{battle_tag}:{turn}:bring".encode()).digest()[:8]


BelievedMoves = Callable[[str], list[str]] | None

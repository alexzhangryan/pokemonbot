"""Payoff estimation: what one cell of the matrix game is worth.

Each cell `(our joint action, their joint action)` needs an expected value. This
module produces it by modelling one turn analytically with the M1 damage layer
and scoring the resulting position with `champions.search.evaluate`.

## Why analytic and not the simulator

`champions/search/oracle.py` can clone and step a real battle in about 2 ms, and
that is the higher fidelity option. It is not available yet, and the reason is
information rather than speed: stepping the simulator requires a *complete*
opponent team -- spreads, items, abilities, the two Pokemon they have not shown
-- and inventing one would be inventing the answer. Constructing that team from
observations is exactly the belief filter, which is M5.

So M2 estimates payoffs from what is actually known, and M5 swaps the estimator
for particles over hypothesised teams without the surrounding search changing.
`OpponentHypothesis` is the seam that swap happens at.

## What this models, and what it does not

Models: base power and type effectiveness from the Champions dex, the real stat
formula, boosts, speed order including priority and Trick Room, Protect,
switches as a lost turn, spread damage and its 0.75 modifier, faints, and the
sixteen damage rolls bucketed by whether the target faints.

Since the first live ladder games (D85) it also models the doubles
interactions those games were lost to, each read from the move's dex entry
rather than from a hand-written table wherever the dex carries it:

- Fake Out's flinch, and its failure off the turn its user came in.
- Follow Me and Rage Powder redirecting the other side's single-target moves.
- Helping Hand, stat-changing moves (Swords Dance, Icy Wind, Parting Shot,
  Close Combat's drop), status-inflicting moves and their type immunities.
- Trick Room being set or ended, Tailwind, Reflect, Light Screen, Aurora
  Veil, Wide Guard and Quick Guard, weather and terrain being set.
- Terrain and weather damage modifiers, Expanding Force in Psychic Terrain,
  priority failing into a grounded target on Psychic Terrain, Grassy Glide's
  priority on Grassy Terrain.
- Foul Play, Body Press, Psyshock, Low Kick and Grass Knot, Heavy Slam, Last
  Respects, Knock Off, Facade, Hex, Acrobatics, Super Fang and fixed damage.
- Sucker Punch failing unless the target is attacking and has not moved.
- Recoil, Life Orb recoil when the item is known, drain and healing moves,
  multi-hit moves as their expected hit count, accuracy folded into the roll
  buckets, and moves that switch their user out.
- Our own Mega Evolution, applied before the turn is ordered.

Does not model: abilities and items beyond what an `EffectsProvider` supplies,
secondary effects below 100%, weather-dependent accuracy, Substitute, Taunt,
Encore, Disable, trapping, or the opponent switching. Every one of those is a
real effect; they are absent rather than approximated because a wrong number
that looks computed is worse than a missing one -- the search will happily
exploit a fictitious advantage, and there is no test that catches it.

The consequence worth stating plainly: this estimator is better than greedy base
power because it accounts for effectiveness, bulk, speed and knockouts, and it
is much worse than the simulator. It is a floor, not a ceiling.

## Copy on write

A cell resolves into up to sixteen branches, and a turn of 300 cells is a few
thousand branch states. They used to be deep copies of the whole snapshot,
which is a few kilobytes of move lists per Pokemon; now the snapshot's
containers are copied one level at a time as they are written and views are
never mutated in place (`_clone`, `_put`). The model reads the same numbers
and the payoff for a turn takes milliseconds rather than seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from champions.dex.damage import (
    DamageContext,
    TypeChart,
    boosted,
    damage_roll_distribution,
    modify,
)
from champions.dex.loader import Dex, to_id
from champions.dex.stats import STAT_IDS
from champions.search.evaluate import win_prob

#: Per-stat point assumption for a Pokemon whose spread is unknown.
#:
#: Not a coherent spread: 32 is the per-stat cap and the total budget is 66, so
#: assuming it everywhere describes a team that cannot exist. That is
#: deliberate. Each stat is used in isolation -- their Attack against our
#: Defence, their Speed against ours -- and assuming investment in whichever one
#: is currently being read makes the estimate conservative in every direction at
#: once: the opponent hits hard, takes hits well, and moves first. Being
#: pessimistic about an unknown opponent is the right bias for a search, and M5
#: replaces the whole assumption with sampled particles that *are* coherent.
ASSUMED_POINTS = 32

#: Statuses that halve the effective Speed stat. In Champions paralysis keeps
#: mainline's speed penalty even though its full-paralysis chance dropped to 1/8
#: (`docs/02-mechanics-deltas.md` section 4).
PARALYSIS_SPEED_FACTOR = 0.5


@dataclass(frozen=True)
class OpponentHypothesis:
    """What we assume about stats we cannot see.

    The seam M5 replaces. A belief particle is a hypothesis with real numbers in
    it; this default is the same shape carrying a constant. M5 supplies
    `champions.belief.hypothesis.BeliefHypothesis`, which reads the same
    interface off the particle filter, so the search around it does not change.
    """

    points: int = ASSUMED_POINTS

    def stat(self, base_stats: dict[str, Any], stat_id: str) -> int:
        base = int(base_stats[stat_id])
        if stat_id == "hp":
            return base + self.points + 75
        # Neutral nature: assuming a helpful one everywhere would compound with
        # the already-pessimistic point assumption into a Pokemon that is
        # simultaneously fast, strong and bulky beyond anything legal.
        return base + self.points + 20

    def stats_for(self, view: dict[str, Any]) -> dict[str, int]:
        """All six stats for one unrevealed Pokemon.

        Per Pokemon rather than per stat, because a belief hypothesis knows
        which *species* it is reasoning about and a constant does not. The
        default ignores the extra information, which is exactly what makes
        swapping the seam a no-op for M2's numbers.
        """
        return {stat_id: self.stat(view["base_stats"], stat_id) for stat_id in STAT_IDS}


class EffectsProvider(Protocol):
    """Where the item and ability multipliers for one hit come from.

    M2 modelled neither, and that absence was measured: the agent's edge fell
    from 82% to 56% on a team built on items and abilities its model did not
    represent (D30). Supplying them is the point of M5, so this is the second
    seam the belief plugs into, alongside `OpponentHypothesis`.

    Structurally typed on purpose. The concrete implementation lives in
    `champions.belief`, and the search layer having a hard import of the belief
    layer would make the M2 agent depend on a corpus it does not use.
    """

    def attacker(
        self,
        view: dict[str, Any],
        move: dict[str, Any],
        defender_types: list[str],
        chart: TypeChart,
    ) -> Any: ...  # pragma: no cover

    def defender(
        self,
        view: dict[str, Any],
        move: dict[str, Any],
        defender_types: list[str],
        chart: TypeChart,
    ) -> Any: ...  # pragma: no cover


@dataclass(frozen=True)
class _NoEffect:
    base_power_modifiers: tuple[float, ...] = ()
    attack_modifiers: tuple[float, ...] = ()
    final_modifiers: tuple[float, ...] = ()
    stab_override: float | None = None
    ignore_burn: bool = False
    immune: bool = False
    type_override: str | None = None


_NO_EFFECT = _NoEffect()


class NoEffects:
    """The default: nothing multiplies anything. Preserves M2's arithmetic exactly."""

    def attacker(self, view: Any, move: Any, defender_types: Any, chart: Any) -> _NoEffect:
        return _NO_EFFECT

    def defender(self, view: Any, move: Any, defender_types: Any, chart: Any) -> _NoEffect:
        return _NO_EFFECT


@dataclass(frozen=True)
class Combatant:
    """One Pokemon as the turn model needs it, from either side."""

    species: str
    types: tuple[str, ...]
    stats: dict[str, int]
    hp: int
    max_hp: int
    hp_pct: float
    status: str | None
    boosts: dict[str, int]
    fainted: bool
    known: bool

    def stat(self, stat_id: str) -> int:
        return boosted(self.stats[stat_id], self.boosts.get(stat_id, 0))


def combatant(view: dict[str, Any], hypothesis: OpponentHypothesis) -> Combatant:
    """A snapshot's Pokemon entry as a `Combatant`.

    Our own entries carry exact stats and exact HP. The opponent's carry base
    stats, a percentage, and nothing else, so the rest comes from the
    hypothesis. Their maximum HP is reconstructed from base stats so that a
    percentage can be turned into points and back.
    """
    known = bool(view.get("known"))

    if known and view.get("stats"):
        stats = {stat_id: int(view["stats"][stat_id]) for stat_id in STAT_IDS if stat_id != "hp"}
        stats["hp"] = int(view["max_hp"])
        max_hp = int(view["max_hp"])
        hp = int(view["hp"])
    else:
        stats = hypothesis.stats_for(view)
        max_hp = stats["hp"]
        hp = round(max_hp * view["hp_pct"] / 100.0)

    return Combatant(
        species=view["species"],
        types=tuple(view["types"]),
        stats=stats,
        hp=0 if view["fainted"] else hp,
        max_hp=max_hp,
        hp_pct=0.0 if view["fainted"] else float(view["hp_pct"]),
        status=view["status"],
        boosts=dict(view["boosts"]),
        fainted=bool(view["fainted"]),
        known=known,
    )


def effective_speed(unit: Combatant, snapshot: dict[str, Any], ours: bool) -> float:
    """Speed for ordering purposes, with boosts, paralysis and Trick Room.

    Trick Room reverses the comparison rather than negating the number, which is
    the same thing for ordering and avoids reproducing the underflow arithmetic
    Champions removed.
    """
    speed = float(unit.stat("spe"))
    if unit.status == "PAR":
        speed *= PARALYSIS_SPEED_FACTOR
    conditions = snapshot["side_conditions"] if ours else snapshot["opponent_side_conditions"]
    if "TAILWIND" in conditions:
        speed *= 2.0
    return speed


@dataclass
class _Action:
    """One slot's action, resolved against the position it acts in."""

    side: str  # "ours" or "theirs"
    slot: int
    described: dict[str, Any]
    unit: Combatant

    @property
    def kind(self) -> str:
        return str(self.described.get("kind", "raw"))

    @property
    def move_id(self) -> str | None:
        return self.described.get("move") if self.kind == "move" else None

    @property
    def priority(self) -> int:
        return int(self.described.get("priority", 0) or 0)


@dataclass(frozen=True)
class Outcome:
    """One resolution of a cell, with the probability of reaching it."""

    probability: float
    snapshot: dict[str, Any]
    value: float
    faints: tuple[str, ...] = field(default=())


# -- the names the snapshot uses ------------------------------------------
#
# poke-env's enum names, as `champions.protocol.state` writes them. The dex
# names the same things in Showdown's ids; the maps below are the join.

SIDE_CONDITION_NAMES: dict[str, str] = {
    "tailwind": "TAILWIND",
    "reflect": "REFLECT",
    "lightscreen": "LIGHT_SCREEN",
    "auroraveil": "AURORA_VEIL",
    "wideguard": "WIDE_GUARD",
    "quickguard": "QUICK_GUARD",
    "stealthrock": "STEALTH_ROCK",
    "spikes": "SPIKES",
    "stickyweb": "STICKY_WEB",
    "toxicspikes": "TOXIC_SPIKES",
    "safeguard": "SAFEGUARD",
    "mist": "MIST",
}
TERRAIN_NAMES: dict[str, str] = {
    "electricterrain": "ELECTRIC_TERRAIN",
    "grassyterrain": "GRASSY_TERRAIN",
    "mistyterrain": "MISTY_TERRAIN",
    "psychicterrain": "PSYCHIC_TERRAIN",
}
WEATHER_NAMES: dict[str, str] = {
    "raindance": "RAINDANCE",
    "sunnyday": "SUNNYDAY",
    "sandstorm": "SANDSTORM",
    "snowscape": "SNOWSCAPE",
    "hail": "HAIL",
}
TERRAIN_TYPES: dict[str, str] = {
    "ELECTRIC_TERRAIN": "electric",
    "GRASSY_TERRAIN": "grass",
    "PSYCHIC_TERRAIN": "psychic",
}
#: One-turn side conditions the next turn must not inherit.
TRANSIENT_SIDE_CONDITIONS = frozenset({"WIDE_GUARD", "QUICK_GUARD"})
#: Abilities that set a terrain on entry, and the field they set.
SURGE_TERRAINS: dict[str, str] = {
    "psychicsurge": "PSYCHIC_TERRAIN",
    "grassysurge": "GRASSY_TERRAIN",
    "electricsurge": "ELECTRIC_TERRAIN",
    "mistysurge": "MISTY_TERRAIN",
}
#: Abilities that set weather on entry.
WEATHER_ABILITIES: dict[str, str] = {
    "drizzle": "RAINDANCE",
    "drought": "SUNNYDAY",
    "sandstream": "SANDSTORM",
    "snowwarning": "SNOWSCAPE",
}
#: Weather Ball's type and the moves whose accuracy the weather decides.
WEATHER_BALL_TYPES: dict[str, str] = {
    "RAINDANCE": "Water",
    "PRIMORDIALSEA": "Water",
    "SUNNYDAY": "Fire",
    "DESOLATELAND": "Fire",
    "SANDSTORM": "Rock",
    "SNOWSCAPE": "Ice",
    "HAIL": "Ice",
}
SURE_IN_RAIN = frozenset({"thunder", "hurricane"})
SURE_IN_SNOW = frozenset({"blizzard"})

#: Moves whose Protect effect the model honours. Not every protecting move --
#: these are the ones legal and common in Reg M-B doubles.
PROTECTING_MOVES = {"protect", "detect", "spikyshield", "banefulbunker", "burningbulwark"}
#: A consecutive Protect succeeds with this probability per prior use.
PROTECT_REPEAT_FACTOR = 1.0 / 3.0
#: Redirection, and the attacker types the powder one cannot pull.
REDIRECTING_MOVES = {"followme": (), "ragepowder": ("grass",)}
#: Priority attacks that fail unless the target is about to use a damaging move.
SUCKER_MOVES = frozenset({"suckerpunch", "thunderclap"})
#: Moves halved by Grassy Terrain against a grounded target.
GRASSY_HALVED = frozenset({"earthquake", "bulldoze", "magnitude"})
#: Parting Shot's drop lives in an `onHit` handler, not a dex field.
PARTING_SHOT_BOOSTS = {"atk": -1, "spa": -1}
#: Showdown's target strings that hit more than one slot.
SPREAD_TARGETS = {"allAdjacent", "allAdjacentFoes"}
#: Target strings that pick one foe (and can be redirected).
SINGLE_FOE_TARGETS = frozenset({"normal", "any", "adjacentFoe", "randomNormal", "scripted"})

TERRAIN_MODIFIER = 5325 / 4096
HELPING_HAND_MODIFIER = 6144 / 4096
SCREEN_MODIFIER_DOUBLES = 2732 / 4096
WEATHER_BOOST = 1.5
WEATHER_DAMPEN = 0.5
SAND_ROCK_SPD = 1.5
SNOW_ICE_DEF = 1.5
EXPANDING_FORCE_MODIFIER = 1.5
RISING_VOLTAGE_MODIFIER = 2.0
KNOCK_OFF_MODIFIER = 1.5
LIFE_ORB_RECOIL = 0.1
#: The expected hit count of a 2-5 hit move: 2, 3 at 35% each, 4, 5 at 15%.
MULTIHIT_EXPECTED = 3.1
#: Status immunities by the defender's type, Champions keeping mainline's.
STATUS_IMMUNE_TYPES: dict[str, frozenset[str]] = {
    "brn": frozenset({"fire"}),
    "par": frozenset({"electric"}),
    "psn": frozenset({"poison", "steel"}),
    "tox": frozenset({"poison", "steel"}),
    "frz": frozenset({"ice"}),
    "slp": frozenset(),
}
STATUS_NAMES = {"brn": "BRN", "par": "PAR", "psn": "PSN", "tox": "TOX", "frz": "FRZ", "slp": "SLP"}
MAX_STAGE = 6


class TurnModel:
    """Resolves one turn analytically. Stateless; safe to share across cells."""

    def __init__(
        self,
        dex: Dex,
        hypothesis: OpponentHypothesis | None = None,
        picked_team_size: int | None = None,
        effects: EffectsProvider | None = None,
        place_incoming: bool = False,
    ) -> None:
        self._dex = dex
        self._chart = TypeChart.from_dex(dex)
        self._hypothesis = hypothesis or OpponentHypothesis()
        self._picked_team_size = picked_team_size or dex.picked_team_size
        self._effects = effects or NoEffects()
        #: Whether a switch puts the named Pokemon on the field. Off by default
        #: so every one-ply number since M2 is unchanged; on when the model is
        #: used one ply deeper (`champions.search.twoply`). See `_switch`.
        self._place_incoming = place_incoming

    @property
    def hypothesis(self) -> OpponentHypothesis:
        return self._hypothesis

    @property
    def effects(self) -> EffectsProvider:
        return self._effects

    @property
    def places_incoming(self) -> bool:
        return self._place_incoming

    # -- public ---------------------------------------------------------

    def value(
        self,
        snapshot: dict[str, Any],
        our_action: dict[str, Any],
        their_action: dict[str, Any],
    ) -> float:
        """Expected value of one cell, over the bucketed roll outcomes."""
        outcomes = self.outcomes(snapshot, our_action, their_action)
        return sum(o.probability * o.value for o in outcomes)

    def outcomes(
        self,
        snapshot: dict[str, Any],
        our_action: dict[str, Any],
        their_action: dict[str, Any],
    ) -> list[Outcome]:
        """Every distinct resolution of a cell, with its probability.

        Rolls are bucketed by whether the target faints, per
        `docs/04-decision-engine.md` section 4: that is the discontinuity that
        changes the value, and the rest of the roll range moves the evaluation
        almost linearly. Two buckets per attack and at most four attacks bounds
        this at sixteen branches, which is cheap enough to enumerate exactly
        rather than sample -- so no common random numbers are needed on the roll
        dimension, because nothing is sampled. A miss is folded into the
        non-knockout bucket rather than branched, for the same bound.
        """
        start = self._mega_evolve(_clone(snapshot), our_action)
        start = _annotate_choices(start, our_action, their_action)
        actions = self._order(start, our_action, their_action)
        branches: list[tuple[float, dict[str, Any]]] = [(1.0, start)]

        for action in actions:
            branches = self._apply(branches, action)

        return [
            Outcome(
                probability=probability,
                snapshot=state,
                value=win_prob(state, self._picked_team_size),
            )
            for probability, state in branches
            if probability > 0.0
        ]

    # -- ordering -------------------------------------------------------

    def _order(
        self,
        snapshot: dict[str, Any],
        our_action: dict[str, Any],
        their_action: dict[str, Any],
    ) -> list[_Action]:
        """Every slot's action, fastest first.

        Switches resolve before moves, as they do in the simulator. Ties break
        deterministically on side and slot rather than randomly: the simulator
        breaks them by coin flip, but a search that samples the flip would make
        the same position score differently on reruns, which `CLAUDE.md` forbids
        and which would destroy the coach's reproducibility.
        """
        actions: list[_Action] = []
        for side, described in (("ours", our_action), ("theirs", their_action)):
            slots = described.get("slots", [])
            for index, slot_action in enumerate(slots):
                unit = self._unit(snapshot, side, index)
                if unit is None or unit.fainted:
                    continue
                actions.append(_Action(side, index, slot_action, unit))

        trick_room = "TRICK_ROOM" in (snapshot.get("fields") or {})
        fields = snapshot.get("fields") or {}

        def key(action: _Action) -> tuple:
            speed = effective_speed(action.unit, snapshot, action.side == "ours")
            priority = action.priority
            if (
                action.move_id == "grassyglide"
                and "GRASSY_TERRAIN" in fields
                and _grounded(snapshot[action.side]["active"][action.slot] or {})
            ):
                priority += 1
            return (
                0 if action.kind == "switch" else 1,
                -priority,
                speed if trick_room else -speed,
                action.side,
                action.slot,
            )

        return sorted(actions, key=key)

    def _unit(self, snapshot: dict[str, Any], side: str, slot: int) -> Combatant | None:
        active = snapshot[side]["active"]
        if slot >= len(active) or active[slot] is None:
            return None
        return combatant(active[slot], self._hypothesis)

    def _mega_evolve(self, state: dict[str, Any], our_action: dict[str, Any]) -> dict[str, Any]:
        """Our Mega Evolution, before the turn is ordered.

        Only ours: which turn the opponent megas is unobservable in advance, and
        poke-env rewrites their base stats once they have. The new stats are
        the old ones shifted by the change in base stat, which is exact for a
        neutral nature and within a few points otherwise; the item names the
        forme, so a species with two stones evolves into the right one.
        """
        for index, slot in enumerate(our_action.get("slots") or []):
            if not slot.get("mega"):
                continue
            view = state["ours"]["active"][index] if index < len(state["ours"]["active"]) else None
            if view is None:
                continue
            stone = self._dex.items.get(to_id(view.get("item")))
            formes = (stone or {}).get("megaStone") or {}
            forme = self._dex.species.get(to_id(next(iter(formes.values()), None)))
            if not forme or not view.get("stats"):
                continue
            base = view.get("base_stats") or {}
            new_base = forme.get("baseStats") or {}
            stats = {
                stat_id: int(view["stats"][stat_id])
                + int(new_base.get(stat_id, 0))
                - int(base.get(stat_id, 0))
                for stat_id in view["stats"]
            }
            abilities = forme.get("abilities") or {}
            ability = to_id(next(iter(abilities.values()), view.get("ability")))
            state = _put(
                state,
                "ours",
                index,
                {
                    **view,
                    "species": to_id(forme.get("name")),
                    "base_stats": dict(new_base),
                    "types": [str(t).upper() for t in forme.get("types") or view["types"]],
                    "stats": stats,
                    "ability": ability,
                },
            )
            if ability != to_id(view.get("ability")):
                state = _entry_effects(state, "ours", index)
        return state

    # -- resolution -----------------------------------------------------

    def _apply(
        self,
        branches: list[tuple[float, dict[str, Any]]],
        action: _Action,
    ) -> list[tuple[float, dict[str, Any]]]:
        out: list[tuple[float, dict[str, Any]]] = []
        for probability, state in branches:
            view = _view(state, action.side, action.slot)
            if view is None or view.get("fainted") or view.get("_flinched"):
                out.append((probability, state))
                continue
            state = _put(state, action.side, action.slot, {**view, "_acted": True})
            if action.kind == "switch":
                out.append((probability, self._switch(state, action)))
                continue
            if action.kind != "move":
                out.append((probability, state))
                continue
            move = self._dex.moves.get(action.move_id or "")
            if not move:
                out.append((probability, state))
                continue
            if move["category"] == "Status":
                out.extend(self._status(probability, state, action, move))
            else:
                out.extend(self._attack(probability, state, action, move))
        return out

    def _status(
        self,
        probability: float,
        state: dict[str, Any],
        action: _Action,
        move: dict[str, Any],
    ) -> list[tuple[float, dict[str, Any]]]:
        """A status move, branching where the game does.

        A Protect pressed again succeeds one time in three per consecutive
        use; the live games (D85) showed an agent pressing it three turns
        running because the model had it succeeding every time. The failed
        branch is the turn passed.
        """
        if str(move["id"]) in PROTECTING_MOVES:
            view = _view(state, action.side, action.slot) or {}
            repeats = int(view.get("protect_counter") or 0)
            success = PROTECT_REPEAT_FACTOR**repeats
            branches = [(probability * success, self._protect(state, action))]
            if success < 1.0:
                branches.append((probability * (1.0 - success), state))
            return branches
        return [(probability, self._status_move(state, action, move))]

    def _switch(self, state: dict[str, Any], action: _Action) -> dict[str, Any]:
        """A switch takes the acting Pokemon off the field and puts it on the bench.

        Moving it rather than deleting it is the whole point. The evaluation
        counts HP and survivors across active *and* bench, so emptying the slot
        without benching the occupant scores a switch as losing that Pokemon
        outright -- measured at 0.06 against a 0.82 baseline for a double
        switch, which made the agent treat switching as near-suicide.

        The incoming Pokemon is deliberately not put on the field. On our side we
        know who it is, but the value of having it in play is a next-turn
        question that one ply cannot see; on theirs we often do not know who it
        is at all. So the slot ends the turn empty, which scores a switch as
        giving up this turn's action while keeping the Pokemon. That is a real
        and intended bias against switching, and it is the clearest thing depth
        would fix.

        With `place_incoming` the model is being used one ply deeper
        (`champions.search.twoply`), the next turn *is* asked, and the switch
        names who comes in: that Pokemon leaves the bench for the slot, with
        `_placed` set so the next ply knows it just came in, and the outgoing
        one is benched with its boosts cleared, as the game clears them.
        Opponent columns never contain switches, so this only ever fires for
        our side.
        """
        view = _view(state, action.side, action.slot)
        if view is None:
            return state
        return self._bench(state, action.side, action.slot, action.described)

    def _bench(
        self, state: dict[str, Any], side: str, slot: int, described: dict[str, Any]
    ) -> dict[str, Any]:
        """Take the occupant of a slot off the field, keeping it alive."""
        state = _clone(state)
        side_state = state[side]
        active = side_state["active"]
        view = active[slot]
        if view is None:
            return state
        outgoing = {k: v for k, v in view.items() if not k.startswith("_")}
        outgoing["boosts"] = {}
        incoming = self._incoming(side_state["bench"], described) if self._place_incoming else None
        if incoming is None:
            side_state["bench"] = [*side_state["bench"], outgoing]
            active[slot] = None
            return state
        side_state["bench"] = [*[p for p in side_state["bench"] if p is not incoming], outgoing]
        active[slot] = {**incoming, "_placed": True}
        return _entry_effects(state, side, slot)

    @staticmethod
    def _incoming(bench: list[dict[str, Any]], described: dict[str, Any]) -> dict[str, Any] | None:
        """The bench entry a switch names, by nickname first and species second."""
        wanted_name = described.get("name")
        wanted_species = described.get("species")
        for view in bench:
            if view.get("fainted"):
                continue
            if wanted_name and view.get("name") == wanted_name:
                return view
        for view in bench:
            if view.get("fainted"):
                continue
            if wanted_species and view.get("species") == wanted_species:
                return view
        return None

    def _protect(self, state: dict[str, Any], action: _Action) -> dict[str, Any]:
        view = _view(state, action.side, action.slot)
        if view is None:
            return state
        return _put(state, action.side, action.slot, {**view, "_protected": True})

    # -- status moves ---------------------------------------------------

    def _status_move(
        self, state: dict[str, Any], action: _Action, move: dict[str, Any]
    ) -> dict[str, Any]:
        """A status move's effect, read off its dex entry.

        Protect was the one status move M2 modelled, because it is the
        interaction the equilibrium exists to handle. The rest arrived with
        the first live games (D85): the losses there were to Trick Room,
        Follow Me, Tailwind and set-up, none of which a model that scores a
        status move as a pass can see coming or answer.
        """
        move_id = str(move["id"])
        if move_id in PROTECTING_MOVES:
            return self._protect(state, action)

        actor = _view(state, action.side, action.slot) or {}
        opponent = _other(action.side)
        fields = state.get("fields") or {}

        if move_id in REDIRECTING_MOVES:
            return _put(state, action.side, action.slot, {**actor, "_redirect": move_id})

        if move_id == "helpinghand":
            partner = _partner(state, action.side, action.slot)
            if partner is None:
                return state
            index, view = partner
            return _put(state, action.side, index, {**view, "_helpinghand": True})

        if move.get("pseudoWeather") == "trickroom":
            state = _clone(state)
            fields = dict(state.get("fields") or {})
            if "TRICK_ROOM" in fields:
                del fields["TRICK_ROOM"]
            else:
                fields["TRICK_ROOM"] = 0
            state["fields"] = fields
            return state

        side_condition = SIDE_CONDITION_NAMES.get(str(move.get("sideCondition") or ""))
        if side_condition:
            if side_condition == "AURORA_VEIL" and not _snowing(state):
                return state
            target_side = opponent if move.get("target") == "foeSide" else action.side
            return _set_side_condition(state, target_side, side_condition)

        terrain = TERRAIN_NAMES.get(str(move.get("terrain") or ""))
        if terrain:
            state = _clone(state)
            state["fields"] = {
                **{k: v for k, v in fields.items() if k not in TERRAIN_NAMES.values()},
                terrain: 0,
            }
            return state

        weather = WEATHER_NAMES.get(to_id(str(move.get("weather") or "")))
        if weather:
            state = _clone(state)
            state["weather"] = {weather: 0}
            return state

        heal = move.get("heal")
        if heal or (move.get("flags") or {}).get("heal"):
            numerator, denominator = (heal or [1, 2])[:2]
            targets = self._status_targets(state, action, move)
            for side, slot in targets:
                target = _view(state, side, slot)
                if target is None or target.get("fainted"):
                    continue
                unit = combatant(target, self._hypothesis)
                state = _heal(state, side, slot, unit, unit.max_hp * numerator / denominator)
            if move_id == "rest":
                state = _put(state, action.side, action.slot, {**actor, "status": "SLP"})
            return state

        boosts = move.get("boosts")
        status = move.get("status")
        if boosts or status or move_id == "partingshot":
            for side, slot in self._status_targets(state, action, move):
                target = _view(state, side, slot)
                if target is None or target.get("fainted"):
                    continue
                if side != action.side and (target.get("_protected") or not _lands(move, target)):
                    continue
                if move_id == "partingshot":
                    state = _boost(state, side, slot, PARTING_SHOT_BOOSTS)
                if boosts:
                    state = _boost(state, side, slot, boosts)
                if status:
                    state = _inflict(state, side, slot, str(status), fields)
        if move.get("selfSwitch") and move_id != "batonpass":
            state = self._bench(state, action.side, action.slot, action.described)
        return state

    def _status_targets(
        self, state: dict[str, Any], action: _Action, move: dict[str, Any]
    ) -> list[tuple[str, int]]:
        target = str(move.get("target") or "self")
        if target in ("self", "adjacentAllyOrSelf") and not int(
            action.described.get("target", 0) or 0
        ):
            return [(action.side, action.slot)]
        if target == "allies":
            return [
                (action.side, i)
                for i, p in enumerate(state[action.side]["active"])
                if p is not None and not p.get("fainted")
            ]
        if target == "adjacentAlly":
            partner = _partner(state, action.side, action.slot)
            return [(action.side, partner[0])] if partner else []
        if target in ("all", "allySide", "foeSide"):
            return []
        return self._targets(state, action, move)

    # -- damaging moves -------------------------------------------------

    def _attack(
        self,
        probability: float,
        state: dict[str, Any],
        action: _Action,
        move: dict[str, Any],
    ) -> list[tuple[float, dict[str, Any]]]:
        move_id = str(move["id"])
        attacker_view = _view(state, action.side, action.slot) or {}
        fields = state.get("fields") or {}

        if move_id == "fakeout" and not attacker_view.get("first_turn", True):
            return [(probability, state)]
        if move_id in SUCKER_MOVES and not self._sucker_lands(state, action):
            return [(probability, state)]

        targets = self._targets(state, action, move)
        if not targets:
            return [(probability, state)]

        move = _weather_ball(move, state)
        grounded = _grounded(attacker_view)
        spread_move = move["target"] in SPREAD_TARGETS or (
            move_id == "expandingforce" and grounded and "PSYCHIC_TERRAIN" in fields
        )
        if move_id == "expandingforce" and spread_move and move["target"] not in SPREAD_TARGETS:
            targets = targets_of(
                {side: state[side]["active"] for side in ("ours", "theirs")},
                side=action.side,
                slot=action.slot,
                described_target=0,
                move_target="allAdjacentFoes",
            )
        live = [
            (s, i)
            for s, i in targets
            if (v := _view(state, s, i)) is not None and not v.get("fainted")
        ]
        spread = len(live) > 1
        physical = move["category"] == "Physical"
        accuracy = move.get("accuracy")
        hit_chance = 1.0 if accuracy is True or not accuracy else float(accuracy) / 100.0
        hit_chance = _weather_accuracy(move_id, hit_chance, state)

        # Each target's roll distribution is bucketed independently; the joint
        # branch count is the product, which for a spread move into two targets
        # is four. Damage dealt is carried on the branch for recoil and drain.
        branches: list[tuple[float, dict[str, Any], int]] = [(probability, state, 0)]
        for side, slot in live:
            expanded: list[tuple[float, dict[str, Any], int]] = []
            for branch_probability, branch_state, dealt in branches:
                target_view = _view(branch_state, side, slot)
                if (
                    target_view is None
                    or target_view["fainted"]
                    or (target_view.get("_protected") and (move.get("flags") or {}).get("protect"))
                    or (spread_move and _guarded(branch_state, side, "WIDE_GUARD"))
                    or (action.priority > 0 and _guarded(branch_state, side, "QUICK_GUARD"))
                    or (
                        action.priority > 0
                        and side != action.side
                        and "PSYCHIC_TERRAIN" in fields
                        and _grounded(target_view)
                    )
                ):
                    expanded.append((branch_probability, branch_state, dealt))
                    continue
                target = combatant(target_view, self._hypothesis)
                current = combatant(
                    _view(branch_state, action.side, action.slot) or attacker_view,
                    self._hypothesis,
                )

                # The held item and the ability, from whichever source knows
                # them: our own snapshot for our side, the belief filter's
                # posterior for theirs. The default provider returns nothing,
                # so M2's numbers are unchanged when no belief is supplied.
                offence = self._effects.attacker(
                    attacker_view, move, list(target.types), self._chart
                )
                # An -ate ability makes the move another type for everything
                # that follows: the defender's effects, immunity, the STAB
                # and the chart.
                move_type = str(getattr(offence, "type_override", None) or move["type"])
                typed = {**move, "type": move_type} if move_type != move["type"] else move
                defence = self._effects.defender(
                    target_view, typed, list(target.types), self._chart
                )
                if offence.immune or defence.immune:
                    expanded.append((branch_probability, branch_state, dealt))
                    continue

                fixed = _fixed_damage(move_id, current, target, self._dex.level)
                if fixed is not None:
                    rolls = [fixed] * 16
                else:
                    base_power = self._base_power(
                        move, current, target, attacker_view, target_view, branch_state
                    )
                    if base_power <= 0:
                        expanded.append((branch_probability, branch_state, dealt))
                        continue
                    for multiplier in offence.base_power_modifiers:
                        base_power = modify(base_power, multiplier)
                    if attacker_view.get("_helpinghand"):
                        base_power = modify(base_power, HELPING_HAND_MODIFIER)
                    attack = _offensive_stat(move, current, target, physical)
                    for multiplier in offence.attack_modifiers:
                        attack = modify(attack, multiplier)
                    defense = _defensive_stat(move, target, physical, branch_state)
                    rolls = damage_roll_distribution(
                        DamageContext(
                            base_power=base_power,
                            attack=attack,
                            defense=defense,
                            move_type=move_type,
                            attacker_types=list(current.types),
                            defender_types=list(target.types),
                            level=self._dex.level,
                            is_spread=spread,
                            attacker_burned=current.status == "BRN",
                            ignore_burn=offence.ignore_burn,
                            stab_override=offence.stab_override,
                            weather_modifiers=_weather_modifiers(typed, branch_state),
                            final_modifiers=(
                                *_screen_modifiers(move, side, branch_state),
                                *offence.final_modifiers,
                                *defence.final_modifiers,
                            ),
                        ),
                        self._chart,
                    )
                for bucket_probability, damage in _bucket(rolls, target.hp, hit_chance):
                    after = _apply_damage(branch_state, side, slot, damage, target)
                    if damage > 0 and side != action.side:
                        after = self._on_hit(after, action, move, side, slot)
                    expanded.append(
                        (branch_probability * bucket_probability, after, dealt + damage)
                    )
            branches = expanded

        out: list[tuple[float, dict[str, Any]]] = []
        for branch_probability, branch_state, dealt in branches:
            out.append((branch_probability, self._after_attack(branch_state, action, move, dealt)))
        return out

    def _on_hit(
        self,
        state: dict[str, Any],
        action: _Action,
        move: dict[str, Any],
        side: str,
        slot: int,
    ) -> dict[str, Any]:
        """What a landed hit does to its target beyond the damage."""
        secondary = move.get("secondary") or {}
        if int(secondary.get("chance") or 0) < 100:
            return state
        target = _view(state, side, slot)
        if target is None or target.get("fainted"):
            return state
        if secondary.get("volatileStatus") == "flinch" and not target.get("_acted"):
            state = _put(state, side, slot, {**target, "_flinched": True})
        if secondary.get("boosts"):
            state = _boost(state, side, slot, secondary["boosts"])
        if secondary.get("status"):
            state = _inflict(state, side, slot, str(secondary["status"]), state.get("fields") or {})
        return state

    def _after_attack(
        self, state: dict[str, Any], action: _Action, move: dict[str, Any], dealt: int
    ) -> dict[str, Any]:
        """The attacker's own consequences: stat drops, recoil, drain, a switch."""
        actor = _view(state, action.side, action.slot)
        if actor is None or actor.get("fainted"):
            return state
        unit = combatant(actor, self._hypothesis)
        own = (move.get("self") or {}).get("boosts")
        if own and dealt > 0:
            state = _boost(state, action.side, action.slot, own)
        recoil = move.get("recoil")
        if recoil and dealt > 0:
            state = _hurt(state, action.side, action.slot, unit, dealt * recoil[0] / recoil[1])
        if to_id(actor.get("item")) == "lifeorb" and dealt > 0:
            actor = _view(state, action.side, action.slot) or actor
            unit = combatant(actor, self._hypothesis)
            state = _hurt(state, action.side, action.slot, unit, unit.max_hp * LIFE_ORB_RECOIL)
        drain = move.get("drain")
        if drain and dealt > 0:
            actor = _view(state, action.side, action.slot) or actor
            if not actor.get("fainted"):
                unit = combatant(actor, self._hypothesis)
                state = _heal(state, action.side, action.slot, unit, dealt * drain[0] / drain[1])
        if move.get("selfSwitch") and dealt > 0:
            actor = _view(state, action.side, action.slot)
            if actor is not None and not actor.get("fainted"):
                state = self._bench(state, action.side, action.slot, action.described)
        return state

    def _base_power(
        self,
        move: dict[str, Any],
        attacker: Combatant,
        target: Combatant,
        attacker_view: dict[str, Any],
        target_view: dict[str, Any],
        state: dict[str, Any],
    ) -> int:
        """The move's base power in this position.

        The dex carries a number for most moves and a callback for a few; the
        callbacks that matter in doubles are reproduced here from the pinned
        source, and a move whose power this cannot compute keeps the dex
        figure rather than being guessed at.
        """
        move_id = str(move["id"])
        power = int(move.get("basePower") or 0)
        if move_id in ("lowkick", "grassknot"):
            power = _weight_power(self._weight(target_view))
        elif move_id in ("heavyslam", "heatcrash"):
            power = _ratio_power(self._weight(attacker_view), self._weight(target_view))
        elif move_id == "lastrespects":
            power = 50 * (1 + _fainted_count(state[_side_of(attacker_view, state)]))
        elif move_id in ("storedpower", "powertrip"):
            power = 20 * (1 + sum(max(0, v) for v in attacker.boosts.values()))
        elif move_id == "knockoff" and target_view.get("item"):
            power = modify(power, KNOCK_OFF_MODIFIER)
        elif _doubled(move_id, attacker, target, attacker_view):
            power *= 2
        elif (
            move_id == "expandingforce"
            and _grounded(attacker_view)
            and "PSYCHIC_TERRAIN" in (state.get("fields") or {})
        ):
            power = modify(power, EXPANDING_FORCE_MODIFIER)
        elif (
            move_id == "risingvoltage"
            and _grounded(target_view)
            and "ELECTRIC_TERRAIN" in (state.get("fields") or {})
        ):
            power = modify(power, RISING_VOLTAGE_MODIFIER)

        multihit = move.get("multihit")
        if isinstance(multihit, list):
            power = round(power * MULTIHIT_EXPECTED)
        elif isinstance(multihit, int) and multihit > 1:
            power *= multihit

        fields = state.get("fields") or {}
        move_type = str(move["type"]).lower()
        for terrain, boosted_type in TERRAIN_TYPES.items():
            if terrain in fields and move_type == boosted_type and _grounded(attacker_view):
                power = modify(power, TERRAIN_MODIFIER)
        if "MISTY_TERRAIN" in fields and move_type == "dragon" and _grounded(target_view):
            power = modify(power, 0.5)
        if "GRASSY_TERRAIN" in fields and move_id in GRASSY_HALVED and _grounded(target_view):
            power = modify(power, 0.5)
        return power

    def _weight(self, view: dict[str, Any]) -> float:
        entry = self._dex.species.get(to_id(view.get("species")))
        return float((entry or {}).get("weightkg") or 50.0)

    def _sucker_lands(self, state: dict[str, Any], action: _Action) -> bool:
        """Sucker Punch works only on a target that is about to attack."""
        for side, slot in self._targets(state, action, self._dex.moves[action.move_id or ""]):
            if side == action.side:
                return False
            target = _view(state, side, slot)
            if target is None or target.get("_acted"):
                return False
            chosen = target.get("_chosen") or {}
            entry = self._dex.moves.get(str(chosen.get("move") or ""))
            return bool(chosen.get("kind") == "move" and entry and entry["category"] != "Status")
        return False

    def _targets(
        self, state: dict[str, Any], action: _Action, move: dict[str, Any]
    ) -> list[tuple[str, int]]:
        targets = targets_of(
            {side: state[side]["active"] for side in ("ours", "theirs")},
            side=action.side,
            slot=action.slot,
            described_target=int(action.described.get("target", 0) or 0),
            move_target=str(move["target"]),
        )
        return _redirected(state, action, move, targets)


def targets_of(
    active: dict[str, list[Any]],
    side: str,
    slot: int,
    described_target: int,
    move_target: str,
) -> list[tuple[str, int]]:
    """Which slots a move hits, as (side, slot) pairs.

    Spread moves hit every adjacent slot including our own partner, which is the
    friendly-fire case the damage layer already handles and which a search that
    ignored it would happily walk into.

    Module level and taking the two active lists rather than a whole snapshot,
    because the candidate policy has to answer the same question about the same
    move -- and answering it twice, in two places, is how the two quietly stop
    agreeing about what Earthquake hits.
    """
    opponent = "theirs" if side == "ours" else "ours"

    if move_target in SPREAD_TARGETS:
        hits = [(opponent, i) for i in range(len(active[opponent]))]
        if move_target == "allAdjacent":
            hits += [(side, i) for i in range(len(active[side]))]
            hits = [(s, i) for s, i in hits if not (s == side and i == slot)]
        return [(s, i) for s, i in hits if active[s][i] is not None]

    if described_target > 0:
        hit_slot, hit_side = described_target - 1, opponent
    elif described_target < 0:
        hit_slot, hit_side = -described_target - 1, side
    else:
        # No target choice on a single-target move: the request had only one
        # legal target, so take the first living opposing slot.
        hit_side = opponent
        hit_slot = next((i for i, p in enumerate(active[hit_side]) if p is not None), -1)
    if hit_slot < 0 or hit_slot >= len(active[hit_side]) or active[hit_side][hit_slot] is None:
        return []
    return [(hit_side, hit_slot)]


def _redirected(
    state: dict[str, Any],
    action: _Action,
    move: dict[str, Any],
    targets: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    """Follow Me and Rage Powder pull a single-target move onto their user.

    Only the other side's single-target moves, only while the redirector is
    standing, and Rage Powder not from a Grass type. A move already aimed at
    the redirector is unchanged; one aimed at its partner is moved.
    """
    if len(targets) != 1 or str(move.get("target")) not in SINGLE_FOE_TARGETS:
        return targets
    side, slot = targets[0]
    if side == action.side:
        return targets
    for index, view in enumerate(state[side]["active"]):
        if view is None or view.get("fainted") or index == slot:
            continue
        redirect = view.get("_redirect")
        if not redirect:
            continue
        blocked_types = REDIRECTING_MOVES.get(str(redirect), ())
        if any(str(t).lower() in blocked_types for t in action.unit.types):
            continue
        return [(side, index)]
    return targets


# -- state helpers, all copy on write ----------------------------------------


def _annotate_choices(
    state: dict[str, Any], our_action: dict[str, Any], their_action: dict[str, Any]
) -> dict[str, Any]:
    """Each active view carries what its slot chose this turn, so a move that
    depends on the target's choice (Sucker Punch) can read it."""
    for side, described in (("ours", our_action), ("theirs", their_action)):
        for index, slot_action in enumerate(described.get("slots") or []):
            view = _view(state, side, index)
            if view is not None:
                state = _put(state, side, index, {**view, "_chosen": slot_action})
    return state


def _entry_effects(state: dict[str, Any], side: str, slot: int) -> dict[str, Any]:
    """What a Pokemon does by arriving: its weather, its terrain, Intimidate.

    Read from the view's ability, which is exact for our side and, for the
    opponent, whatever the battle has revealed. The lead sweep applies the
    same table from the belief before turn one (`champions.search.lead`).
    """
    view = _view(state, side, slot)
    if view is None:
        return state
    ability = to_id(view.get("ability"))
    if ability in WEATHER_ABILITIES:
        state = _clone(state)
        state["weather"] = {WEATHER_ABILITIES[ability]: 0}
    if ability in SURGE_TERRAINS:
        state = _clone(state)
        fields = {
            k: v for k, v in (state.get("fields") or {}).items() if k not in TERRAIN_NAMES.values()
        }
        state["fields"] = {**fields, SURGE_TERRAINS[ability]: 0}
    if ability == "intimidate":
        foe = _other(side)
        for index, target in enumerate(state[foe]["active"]):
            if target is not None and not target.get("fainted"):
                state = _boost(state, foe, index, {"atk": -1})
    return state


def _weather_ball(move: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Weather Ball is a 100-power move of the weather's type under weather."""
    if str(move.get("id")) != "weatherball":
        return move
    for weather, move_type in WEATHER_BALL_TYPES.items():
        if weather in (state.get("weather") or {}):
            return {**move, "type": move_type, "basePower": 100}
    return move


def _weather_accuracy(move_id: str, hit_chance: float, state: dict[str, Any]) -> float:
    weather = state.get("weather") or {}
    if move_id in SURE_IN_RAIN:
        if "RAINDANCE" in weather or "PRIMORDIALSEA" in weather:
            return 1.0
        if "SUNNYDAY" in weather or "DESOLATELAND" in weather:
            return 0.5
    if move_id in SURE_IN_SNOW and _snowing(state):
        return 1.0
    return hit_chance


def _clone(state: dict[str, Any]) -> dict[str, Any]:
    """A snapshot whose sides, active lists and condition maps are fresh.

    Views are shared with the original and must be replaced, never mutated;
    bench lists are copied by whoever appends to them. That is the whole
    discipline, and it is what turns a deep copy per branch into a dozen
    small allocations.
    """
    out = dict(state)
    for side in ("ours", "theirs"):
        out[side] = {**state[side], "active": list(state[side]["active"])}
    out["fields"] = dict(state.get("fields") or {})
    out["weather"] = dict(state.get("weather") or {})
    out["side_conditions"] = dict(state.get("side_conditions") or {})
    out["opponent_side_conditions"] = dict(state.get("opponent_side_conditions") or {})
    return out


def _view(state: dict[str, Any], side: str, slot: int) -> dict[str, Any] | None:
    active = state[side]["active"]
    if slot < 0 or slot >= len(active):
        return None
    return active[slot]


def _put(state: dict[str, Any], side: str, slot: int, view: dict[str, Any]) -> dict[str, Any]:
    state = _clone(state)
    state[side]["active"][slot] = view
    return state


def _other(side: str) -> str:
    return "theirs" if side == "ours" else "ours"


def _side_of(view: dict[str, Any], state: dict[str, Any]) -> str:
    return "ours" if view.get("known") else "theirs"


def _partner(state: dict[str, Any], side: str, slot: int) -> tuple[int, dict[str, Any]] | None:
    for index, view in enumerate(state[side]["active"]):
        if index != slot and view is not None and not view.get("fainted"):
            return index, view
    return None


def _conditions_key(side: str) -> str:
    return "side_conditions" if side == "ours" else "opponent_side_conditions"


def _set_side_condition(state: dict[str, Any], side: str, name: str) -> dict[str, Any]:
    state = _clone(state)
    conditions = state[_conditions_key(side)]
    if name in conditions and name not in ("SPIKES", "TOXIC_SPIKES"):
        return state
    conditions[name] = int(conditions.get(name, 0)) + 1
    return state


def _guarded(state: dict[str, Any], side: str, name: str) -> bool:
    return name in (state.get(_conditions_key(side)) or {})


def _snowing(state: dict[str, Any]) -> bool:
    weather = state.get("weather") or {}
    return "SNOWSCAPE" in weather or "HAIL" in weather


def _grounded(view: dict[str, Any]) -> bool:
    """Whether terrain reaches this Pokemon. Unknown abilities count as grounded."""
    if any(str(t).lower() == "flying" for t in view.get("types") or []):
        return False
    if to_id(view.get("ability")) == "levitate":
        return False
    return to_id(view.get("item")) != "airballoon"


def _lands(move: dict[str, Any], target: dict[str, Any]) -> bool:
    """Type immunities to status moves: a Fire type cannot be burned, and so on."""
    status = move.get("status")
    if status:
        immune = STATUS_IMMUNE_TYPES.get(str(status), frozenset())
        types = {str(t).lower() for t in target.get("types") or []}
        if types & immune:
            return False
        if str(move["id"]) == "thunderwave" and "ground" in types:
            return False
    powder = bool((move.get("flags") or {}).get("powder"))
    grass = any(str(t).lower() == "grass" for t in target.get("types") or [])
    return not (powder and grass)


def _doubled(
    move_id: str, attacker: Combatant, target: Combatant, attacker_view: dict[str, Any]
) -> bool:
    """Facade on a statused user, Hex on a statused target, Acrobatics with
    no item: the conditional doublings that fire from what the position shows."""
    if move_id == "facade":
        return attacker.status in ("BRN", "PAR", "PSN", "TOX")
    if move_id == "hex":
        return bool(target.status)
    if move_id == "acrobatics":
        return bool(attacker_view.get("known")) and not attacker_view.get("item")
    return False


def _boost(state: dict[str, Any], side: str, slot: int, boosts: dict[str, Any]) -> dict[str, Any]:
    view = _view(state, side, slot)
    if view is None:
        return state
    current = dict(view.get("boosts") or {})
    for stat, delta in boosts.items():
        if stat not in STAT_IDS or stat == "hp":
            continue
        value = max(-MAX_STAGE, min(MAX_STAGE, int(current.get(stat, 0)) + int(delta)))
        if value:
            current[stat] = value
        else:
            current.pop(stat, None)
    return _put(state, side, slot, {**view, "boosts": current})


def _inflict(
    state: dict[str, Any], side: str, slot: int, status: str, fields: dict[str, Any]
) -> dict[str, Any]:
    view = _view(state, side, slot)
    if view is None or view.get("status"):
        return state
    name = STATUS_NAMES.get(status)
    if name is None:
        return state
    immune = STATUS_IMMUNE_TYPES.get(status, frozenset())
    if {str(t).lower() for t in view.get("types") or []} & immune:
        return state
    if _grounded(view) and (
        "MISTY_TERRAIN" in fields or (status == "slp" and "ELECTRIC_TERRAIN" in fields)
    ):
        return state
    return _put(state, side, slot, {**view, "status": name})


def _heal(
    state: dict[str, Any], side: str, slot: int, unit: Combatant, amount: float
) -> dict[str, Any]:
    if unit.fainted or unit.max_hp <= 0:
        return state
    remaining = min(unit.max_hp, unit.hp + int(round(amount)))
    return _apply_damage(state, side, slot, unit.hp - remaining, unit)


def _hurt(
    state: dict[str, Any], side: str, slot: int, unit: Combatant, amount: float
) -> dict[str, Any]:
    if unit.fainted:
        return state
    return _apply_damage(state, side, slot, max(1, int(round(amount))), unit)


def _offensive_stat(
    move: dict[str, Any], attacker: Combatant, target: Combatant, physical: bool
) -> int:
    if move.get("overrideOffensivePokemon") == "target":
        return target.stat("atk" if physical else "spa")
    override = move.get("overrideOffensiveStat")
    if override in ("atk", "def", "spa", "spd", "spe"):
        return attacker.stat(str(override))
    return attacker.stat("atk" if physical else "spa")


def _defensive_stat(
    move: dict[str, Any], target: Combatant, physical: bool, state: dict[str, Any]
) -> int:
    override = move.get("overrideDefensiveStat")
    stat_id = str(override) if override in ("def", "spd") else ("def" if physical else "spd")
    value = target.stat(stat_id)
    weather = state.get("weather") or {}
    types = {str(t).lower() for t in target.types}
    if stat_id == "spd" and "SANDSTORM" in weather and "rock" in types:
        value = modify(value, SAND_ROCK_SPD)
    if stat_id == "def" and _snowing(state) and "ice" in types:
        value = modify(value, SNOW_ICE_DEF)
    return value


def _weather_modifiers(move: dict[str, Any], state: dict[str, Any]) -> tuple[float, ...]:
    weather = state.get("weather") or {}
    move_type = str(move["type"]).lower()
    if "RAINDANCE" in weather or "PRIMORDIALSEA" in weather:
        if move_type == "water":
            return (WEATHER_BOOST,)
        if move_type == "fire":
            return (WEATHER_DAMPEN,)
    if "SUNNYDAY" in weather or "DESOLATELAND" in weather:
        if move_type == "fire":
            return (WEATHER_BOOST,)
        if move_type == "water":
            return (WEATHER_DAMPEN,)
    return ()


def _screen_modifiers(move: dict[str, Any], side: str, state: dict[str, Any]) -> tuple[float, ...]:
    conditions = state.get(_conditions_key(side)) or {}
    physical = move["category"] == "Physical"
    if "AURORA_VEIL" in conditions:
        return (SCREEN_MODIFIER_DOUBLES,)
    if physical and "REFLECT" in conditions:
        return (SCREEN_MODIFIER_DOUBLES,)
    if not physical and "LIGHT_SCREEN" in conditions:
        return (SCREEN_MODIFIER_DOUBLES,)
    return ()


def _fixed_damage(move_id: str, attacker: Combatant, target: Combatant, level: int) -> int | None:
    if move_id in ("superfang", "ruination", "naturesmadness"):
        return max(1, target.hp // 2)
    if move_id in ("seismictoss", "nightshade"):
        return level
    if move_id == "finalgambit":
        return attacker.hp
    return None


def _weight_power(weight: float) -> int:
    if weight < 10:
        return 20
    if weight < 25:
        return 40
    if weight < 50:
        return 60
    if weight < 100:
        return 80
    if weight < 200:
        return 100
    return 120


def _ratio_power(attacker_weight: float, target_weight: float) -> int:
    ratio = attacker_weight / max(target_weight, 0.1)
    if ratio >= 5:
        return 120
    if ratio >= 4:
        return 100
    if ratio >= 3:
        return 80
    if ratio >= 2:
        return 60
    return 40


def _fainted_count(side: dict[str, Any]) -> int:
    seen = [p for p in side["active"] if p is not None] + list(side["bench"])
    return sum(1 for p in seen if p.get("fainted") and p.get("selected", True))


def _bucket(
    rolls: list[int], remaining_hp: int, hit_chance: float = 1.0
) -> list[tuple[float, int]]:
    """Group the sixteen rolls into (probability, damage) buckets.

    Split on the knockout threshold, which is the discontinuity that changes the
    value; within each side of it the evaluation moves almost linearly in HP, so
    the bucket's mean is a good summary. Returns one bucket when every roll
    lands on the same side of the threshold, which is the common case and is why
    this stays cheap.

    A miss lands in the non-knockout bucket: the knockout probability is
    exact, and the rest of the mass carries the mean of the damage it does,
    which is what folding accuracy into the expectation means.
    """
    kills = [d for d in rolls if d >= remaining_hp]
    survives = [d for d in rolls if d < remaining_hp]
    total = len(rolls)

    buckets = []
    kill_probability = hit_chance * len(kills) / total
    if kills:
        buckets.append((kill_probability, remaining_hp))
    survive_probability = 1.0 - kill_probability
    if survive_probability > 0.0:
        hit_mass = hit_chance * len(survives) / total
        mean = round(sum(survives) / len(survives)) if survives else 0
        damage = round(mean * hit_mass / survive_probability) if survive_probability else 0
        buckets.append((survive_probability, damage))
    return buckets


def _apply_damage(
    state: dict[str, Any], side: str, slot: int, damage: int, target: Combatant
) -> dict[str, Any]:
    """Subtract damage from a slot, in the snapshot's own units.

    The snapshot carries `hp_pct` for both sides and exact `hp` only for ours,
    so the percentage is what gets updated and the exact value follows where it
    exists. That keeps one representation flowing through the model and the
    evaluation instead of two that can disagree. Negative damage heals.
    """
    if damage == 0:
        return state
    state = _clone(state)
    view = dict(state[side]["active"][slot])

    remaining = max(0, min(target.max_hp, target.hp - damage))
    view["hp_pct"] = round(100.0 * remaining / target.max_hp, 1) if target.max_hp else 0.0
    if view.get("known") and view.get("max_hp"):
        view["hp"] = remaining
    if remaining == 0:
        view["fainted"] = True
        view["hp_pct"] = 0.0

    state[side]["active"][slot] = view
    state[side]["remaining"] = _count_remaining(state[side])
    return state


def _count_remaining(side: dict[str, Any]) -> int:
    seen = [p for p in side["active"] if p is not None] + list(side["bench"])
    return sum(1 for p in seen if not p["fainted"])


def clear_turn_flags(view: dict[str, Any]) -> dict[str, Any]:
    """A view with the model's one-turn markers removed, for the next ply."""
    return {k: v for k, v in view.items() if not k.startswith("_")}


class CellModel(Protocol):
    """Anything that can value one cell: the analytic turn (`TurnModel`), the
    same turn one ply deeper (`champions.search.twoply.TwoPlyModel`), or the
    simulator (`champions.search.rollout`). `payoff_matrix` is the seam."""

    def value(
        self,
        snapshot: dict[str, Any],
        our_action: dict[str, Any],
        their_action: dict[str, Any],
    ) -> float: ...  # pragma: no cover


def payoff_matrix(
    snapshot: dict[str, Any],
    our_actions: list[dict[str, Any]],
    their_actions: list[dict[str, Any]],
    model: CellModel,
) -> np.ndarray:
    """The full payoff matrix for one decision.

    Rows are our candidates, columns are theirs, entries are our win probability
    after the modelled turn. Every cell is evaluated against the same modelled
    turn resolution, which is the common random numbers requirement of
    `docs/04-decision-engine.md` section 4 discharged by construction: nothing
    here samples, so there is no randomness for two cells to disagree about.
    """
    return np.array(
        [[model.value(snapshot, ours, theirs) for theirs in their_actions] for ours in our_actions],
        dtype=float,
    ).reshape(len(our_actions), len(their_actions))

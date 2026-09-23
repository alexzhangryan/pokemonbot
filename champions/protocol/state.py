"""Observable state snapshot: what the agent can see, as plain JSON.

This is the payload behind `turn_start` (docs/07-observability.md section 2),
and it is deliberately a pure function of a poke-env battle rather than a class
the agent carries around. Two consequences that are the point of doing it this
way: the trace is self-contained, so the viewer renders a battle without ever
importing agent code or talking to a simulator; and the snapshot is the same
object a belief filter will later take as its observation, so M3 does not have
to invent one.

Knowledge asymmetry is explicit. Our own side is fully known and reports exact
HP, stats, item, ability, and PP. The opponent's side reports only what the
protocol has actually revealed, and fields that are still unknown are present
and null rather than absent, so a consumer can tell "not revealed" from "this
agent version did not emit it". Everything the opponent has not shown is
inference, which is M3's job, not this module's.

Move numbers come from the Champions dex when one is supplied, never from
poke-env, whose mainline Gen 9 figures are wrong here for 303 moves.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from poke_env.battle import AbstractBattle, DoubleBattle, Move, Pokemon

from champions.dex.loader import Dex, to_id

# poke-env tracks the opponent's HP on a 0-100 scale because that is all the
# protocol gives us. Percent HP is quantized, and damage-based inference off it
# carries about +/- 0.5% of max HP of error (CLAUDE.md constraint 5); the viewer
# marks opponent HP as approximate for that reason.
OPPONENT_HP_IS_PERCENT = True


def _name(value: object) -> str:
    """The bare name of a poke-env enum member.

    Necessary rather than cosmetic. poke-env's enums stringify verbosely and
    inconsistently -- `str(PokemonType.FLYING)` is "FLYING (pokemon type)
    object", `str(Status.PAR)` is "Status.PAR" -- so a trace built on `str()`
    would bake a display-layer parsing problem into the schema, and every
    consumer from the viewer to the coach would have to undo it. `.name` is the
    stable identity; anything without one is passed through as-is.
    """
    return getattr(value, "name", None) or str(value)


# poke-env reports an unrevealed opponent item as this sentinel string rather
# than as None, which would otherwise read as a real item called "unknown_item".
UNKNOWN_ITEM = "unknown_item"


def _item(pokemon: Pokemon) -> str | None:
    """The held item, or None when it is genuinely not known."""
    item = pokemon.item
    return None if not item or item == UNKNOWN_ITEM else item


def snapshot(battle: AbstractBattle, dex: Dex | None = None) -> dict[str, Any]:
    """The full observable state, from this agent's side of the battle."""
    return {
        "turn": battle.turn,
        "player_role": battle.player_role,
        "player_username": battle.player_username,
        "opponent_username": battle.opponent_username,
        "weather": {_name(w): t for w, t in battle.weather.items()},
        "fields": {_name(f): t for f, t in battle.fields.items()},
        "side_conditions": {_name(c): v for c, v in battle.side_conditions.items()},
        "opponent_side_conditions": {
            _name(c): v for c, v in battle.opponent_side_conditions.items()
        },
        "ours": _side(battle.team, _active_of(battle), dex, known=True),
        "theirs": _side(battle.opponent_team, _opponent_active_of(battle), dex, known=False),
        "constraints": _constraints(battle),
    }


def _side(
    team: dict[str, Pokemon],
    active: list[Pokemon | None],
    dex: Dex | None,
    known: bool,
) -> dict[str, Any]:
    """One side, split into the slots on the field and everything behind them.

    `active` is ordered by slot and may contain None (a fainted slot awaiting a
    switch), which the viewer needs in order to render an empty position rather
    than silently closing the gap. Bench membership is decided by object
    identity, not by species: a team may legally hold two of the same species.
    """
    on_field = [p for p in active if p is not None]
    return {
        "active": [None if p is None else _pokemon(p, dex, known) for p in active],
        "bench": [
            _pokemon(p, dex, known) for p in team.values() if not any(p is a for a in on_field)
        ],
        "remaining": sum(1 for p in team.values() if not p.fainted),
        "revealed": len(team),
    }


def _pokemon(pokemon: Pokemon, dex: Dex | None, known: bool) -> dict[str, Any]:
    """One Pokemon as observed. `known` is True for our own side only."""
    common: dict[str, Any] = {
        "species": pokemon.species,
        "name": pokemon.name,
        "level": pokemon.level,
        "types": [_name(t) for t in pokemon.types if t is not None],
        "base_stats": dict(pokemon.base_stats),
        "hp_pct": round(pokemon.current_hp_fraction * 100, 1),
        "status": _name(pokemon.status) if pokemon.status else None,
        "status_counter": pokemon.status_counter,
        "fainted": pokemon.fainted,
        "active": pokemon.active,
        # Only non-zero boosts: six zeroes per Pokemon per turn would be most of
        # the trace's volume and none of its information.
        "boosts": {stat: value for stat, value in pokemon.boosts.items() if value},
        "effects": sorted(_name(effect) for effect in pokemon.effects),
        "must_recharge": pokemon.must_recharge,
        "preparing": bool(pokemon.preparing),
        "protect_counter": pokemon.protect_counter,
        # Whether this Pokemon came in this turn, which is the condition Fake
        # Out turns on. Derivable from the turn number only on turn 1 and wrong
        # after every switch, so the candidate policy reads it rather than
        # inferring it (`champions.search.policy`).
        "first_turn": bool(pokemon.first_turn),
    }

    if known:
        return {
            **common,
            "known": True,
            # Whether this Pokemon was brought. Reg M-B registers six and brings
            # four, and `battle.team` keeps all six for the whole game, so
            # without this a consumer counting our team gets six against an
            # opponent it can only ever count as four. `evaluate.py` was doing
            # exactly that and scoring turn 1 of an even game at 0.996.
            "selected": bool(pokemon._selected_in_teampreview),
            "hp": pokemon.current_hp,
            "max_hp": pokemon.max_hp,
            "item": _item(pokemon),
            "ability": pokemon.ability,
            "stats": dict(pokemon.stats) if pokemon.stats else None,
            "moves": [_move(move, dex) for move in pokemon.moves.values()],
        }

    # The opponent. Null means "not revealed yet", which is a fact about our
    # information rather than a gap in the emission.
    return {
        **common,
        "known": False,
        "hp": None,
        "max_hp": None,
        "hp_is_percent": OPPONENT_HP_IS_PERCENT,
        "item": _item(pokemon),
        "ability": pokemon.ability,
        "possible_abilities": list(pokemon.possible_abilities),
        "stats": None,
        # Populated as moves are used, so this grows through the battle and is
        # exactly the "revealed moves" half of what M3 will reason over.
        "revealed_moves": [_move(move, dex) for move in pokemon.moves.values()],
        "last_move": pokemon.last_move.id if pokemon.last_move else None,
    }


def _move(move: Move, dex: Dex | None) -> dict[str, Any]:
    """One move, with its numbers taken from the Champions dex where available.

    poke-env's Move carries mainline Gen 9 values. 303 moves differ in this
    format, base power among the changed fields, so a viewer rendering
    `move.base_power` off poke-env would quietly be showing a different game
    (CLAUDE.md constraint 1). `source` records which of the two a reader is
    looking at rather than leaving it to be guessed.
    """
    entry = dex.moves.get(move.id) if dex is not None else None
    if entry is not None:
        return {
            "id": move.id,
            "name": entry.get("name", move.id),
            "type": entry.get("type"),
            "category": entry.get("category"),
            "base_power": entry.get("basePower"),
            "accuracy": entry.get("accuracy"),
            "priority": entry.get("priority"),
            "target": entry.get("target"),
            "pp": move.current_pp,
            "max_pp": move.max_pp,
            "source": "champions_dex",
        }
    return {
        "id": move.id,
        "name": move.id,
        "type": _name(move.type) if move.type else None,
        "category": _name(move.category) if move.category else None,
        "base_power": move.base_power,
        "accuracy": move.accuracy,
        "priority": move.priority,
        "target": move.target,
        "pp": move.current_pp,
        "max_pp": move.max_pp,
        "source": "poke_env_mainline",
    }


def _constraints(battle: AbstractBattle) -> dict[str, Any]:
    """What the request says we may and may not do this turn."""
    if isinstance(battle, DoubleBattle):
        return {
            "force_switch": list(battle.force_switch),
            "trapped": list(battle.trapped),
            "maybe_trapped": list(battle.maybe_trapped),
            "can_mega_evolve": [bool(flag) for flag in battle.can_mega_evolve],
            # Terastallization is disabled in Champions; emitted anyway so that a
            # trace showing it True is visibly wrong rather than silently absent.
            "can_tera": [bool(flag) for flag in battle.can_tera],
        }
    return {
        "force_switch": bool(battle.force_switch),
        "trapped": bool(battle.trapped),
        "maybe_trapped": bool(battle.maybe_trapped),
        "can_mega_evolve": bool(battle.can_mega_evolve),
        "can_tera": bool(battle.can_tera),
    }


def _active_of(battle: AbstractBattle) -> list[Pokemon | None]:
    if isinstance(battle, DoubleBattle):
        return list(battle.active_pokemon)
    return [battle.active_pokemon]


def _opponent_active_of(battle: AbstractBattle) -> list[Pokemon | None]:
    if isinstance(battle, DoubleBattle):
        return list(battle.opponent_active_pokemon)
    return [battle.opponent_active_pokemon]


def unseen_pokemon(species: str, dex: Dex) -> dict[str, Any]:
    """An opponent's previewed Pokemon that has not walked on yet, as a view (D91).

    Full health, benched, nothing revealed, flagged `unseen`. What the dex
    knows about the species -- name, types, base stats -- and nothing else,
    which is what team preview tells a player.
    """
    entry = dex.species.get(to_id(species)) or {}
    name = str(entry.get("name") or species)
    return {
        "species": species,
        "name": name,
        "level": 50,
        "types": list(entry.get("types") or []),
        "base_stats": dict(entry.get("baseStats") or {}),
        "hp_pct": 100.0,
        "status": None,
        "status_counter": 0,
        "fainted": False,
        "active": False,
        "boosts": {},
        "effects": [],
        "must_recharge": False,
        "preparing": False,
        "protect_counter": 0,
        "first_turn": False,
        "known": False,
        "hp": None,
        "max_hp": None,
        "hp_is_percent": OPPONENT_HP_IS_PERCENT,
        "item": None,
        "ability": None,
        "possible_abilities": [],
        "stats": None,
        "revealed_moves": [],
        "last_move": None,
        "unseen": True,
    }


def annotate_unseen(
    snapshot: dict[str, Any],
    preview: Sequence[str],
    dex: Dex,
    believed_ability: Callable[[str], str | None] | None = None,
    believed_moves: Callable[[str], list[str]] | None = None,
) -> int:
    """Put the opponent's previewed, not yet seen Pokemon on their bench (D91).

    The snapshot lists what has walked on; the opponent's switch columns need
    somewhere to switch *to*, and until every one of their four has been seen
    that is one of the previewed Pokemon that has not. Each arrives as
    `unseen_pokemon` builds it, with the ability and moves the belief
    expects when a belief is given. As many as the bring still leaves
    unaccounted for, so a side that has shown all four gets none; the
    evaluation derives the opponent's HP and count from the bring, so
    full-health entries change no value. Returns how many were added.
    Mutates `snapshot`; callers annotating a recorded state copy it first.
    """
    theirs = snapshot.get("theirs") or {}
    on_board = [p for p in [*theirs.get("active", []), *theirs.get("bench", [])] if p]
    seen = {to_id(str(p.get("species") or "")) for p in on_board}
    # `remaining` on the opponent's side counts what has been seen and lives,
    # not the bring; the bring is the format's, and what is unaccounted for
    # is the bring less everything that has walked on, fainted or not.
    room = int(dex.picked_team_size) - len(on_board)
    if room <= 0:
        return 0
    added: list[dict[str, Any]] = []
    for species in preview:
        if to_id(species) in seen:
            continue
        view = unseen_pokemon(species, dex)
        if believed_ability is not None:
            ability = believed_ability(species)
            if ability:
                view["ability"] = to_id(ability)
        if believed_moves is not None:
            view["believed_moves"] = list(believed_moves(species))
        added.append(view)
    if not added:
        return 0
    theirs["bench"] = [*theirs.get("bench", []), *added]
    theirs["unseen"] = min(room, len(added))
    return len(added)

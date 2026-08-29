"""Describing a chosen action in terms a person can read.

A `BattleOrder` renders itself as the protocol string we put on the wire
(`/choose move earthquake 1, switch milotic`). That is the right thing to send
and the wrong thing to read: it names the move by id, the target by a signed
slot index, and the switch by a nickname. The viewer and the coach both need
the same action described rather than encoded, so the description lives here
once instead of being re-derived in a template.

This module knows about the protocol, not about strategy. It never scores an
action. The value, damage, and knockout columns the viewer shows next to these
descriptions are filled in by the search layer from M1 onward; until then the
candidate list is honest about being unannotated.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import Move, Pokemon
from poke_env.player.battle_order import (
    BattleOrder,
    DefaultBattleOrder,
    DoubleBattleOrder,
    PassBattleOrder,
    SingleBattleOrder,
)

from champions.dex.loader import Dex

# Showdown's doubles target encoding, from our side of the field. Negative
# indices are our own slots and positive ones are the opponent's; 0 means the
# move does not take a target choice (spread moves, self-targeting moves).
TARGET_LABELS = {
    -2: "ally slot 2",
    -1: "ally slot 1",
    0: "no target choice",
    1: "foe slot 1",
    2: "foe slot 2",
}

# Annotations docs/07-observability.md specifies for each candidate that the
# decision layer does not compute yet. Emitted as a list on the candidates event
# so the viewer can render the columns as pending rather than as zeroes, and so
# the day they start arriving the viewer needs no change.
PENDING_ANNOTATIONS = (
    "damage_rolls",
    "ko_probability",
    "speed_order",
    "value",
    "policy_provider",
)


def describe(order: BattleOrder, dex: Dex | None = None) -> dict[str, Any]:
    """One joint action, as structure rather than as a protocol string."""
    slots = [describe_slot(single, dex) for single in single_orders(order)]
    return {
        "message": order.message,
        "slots": slots,
        "label": " + ".join(slot["label"] for slot in slots),
        "kinds": sorted({slot["kind"] for slot in slots}),
    }


def describe_slot(order: SingleBattleOrder, dex: Dex | None = None) -> dict[str, Any]:
    """One slot's half of a joint action."""
    if isinstance(order, PassBattleOrder):
        return {"kind": "pass", "label": "pass"}
    if isinstance(order, DefaultBattleOrder):
        return {"kind": "default", "label": "default"}

    inner = getattr(order, "order", None)

    if isinstance(inner, Move):
        entry = dex.moves.get(inner.id) if dex is not None else None
        name = entry.get("name", inner.id) if entry else inner.id
        target = getattr(order, "move_target", 0)
        described: dict[str, Any] = {
            "kind": "move",
            "move": inner.id,
            "name": name,
            "type": entry.get("type") if entry else None,
            "category": entry.get("category") if entry else None,
            "base_power": entry.get("basePower") if entry else None,
            # Priority orders the turn, so the search reads it from here rather
            # than re-resolving the move. poke-env would report the mainline
            # value; the Champions dex is the authority.
            "priority": entry.get("priority", 0) if entry else 0,
            "move_target": entry.get("target") if entry else None,
            "target": target,
            "target_label": TARGET_LABELS.get(target, f"slot {target}"),
            "mega": bool(getattr(order, "mega", False)),
        }
        described["label"] = _move_label(described)
        return described

    if isinstance(inner, Pokemon):
        return {
            "kind": "switch",
            "species": inner.species,
            "name": inner.name,
            "label": f"switch to {inner.species}",
        }

    # A raw protocol string, e.g. "/choose default" built by hand.
    return {"kind": "raw", "raw": str(inner), "label": str(inner)}


def _move_label(slot: dict[str, Any]) -> str:
    label = slot["name"]
    if slot["mega"]:
        label += " (mega)"
    if slot["target"]:
        label += f" -> {slot['target_label']}"
    return label


def single_orders(order: BattleOrder) -> list[SingleBattleOrder]:
    """The per-slot orders inside a joint action, one entry per active slot."""
    if isinstance(order, DoubleBattleOrder):
        return [order.first_order, order.second_order]
    return [order]  # type: ignore[list-item]

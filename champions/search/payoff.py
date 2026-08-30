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

Does not model: abilities, held items, secondary effects, status moves' effects,
weather, multi-hit moves, recoil, healing, or accuracy. Every one of those is a
real effect and their absence is the main reason this is a bootstrap. They are
absent rather than approximated because a wrong number that looks computed is
worse than a missing one -- the search will happily exploit a fictitious
advantage, and there is no test that catches it.

The consequence worth stating plainly: this estimator is better than greedy base
power because it accounts for effectiveness, bulk, speed and knockouts, and it
is much worse than the simulator. It is a floor, not a ceiling.
"""

from __future__ import annotations

import copy
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
from champions.dex.loader import Dex
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


class TurnModel:
    """Resolves one turn analytically. Stateless; safe to share across cells."""

    def __init__(
        self,
        dex: Dex,
        hypothesis: OpponentHypothesis | None = None,
        picked_team_size: int | None = None,
        effects: EffectsProvider | None = None,
    ) -> None:
        self._dex = dex
        self._chart = TypeChart.from_dex(dex)
        self._hypothesis = hypothesis or OpponentHypothesis()
        self._picked_team_size = picked_team_size or dex.picked_team_size
        self._effects = effects or NoEffects()

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
        dimension, because nothing is sampled.
        """
        actions = self._order(snapshot, our_action, their_action)
        branches: list[tuple[float, dict[str, Any]]] = [(1.0, copy.deepcopy(snapshot))]

        for action in actions:
            branches = self._apply(branches, action, snapshot)

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

        trick_room = "TRICK_ROOM" in snapshot.get("fields", {})

        def key(action: _Action) -> tuple:
            speed = effective_speed(action.unit, snapshot, action.side == "ours")
            return (
                0 if action.kind == "switch" else 1,
                -action.priority,
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

    # -- resolution -----------------------------------------------------

    def _apply(
        self,
        branches: list[tuple[float, dict[str, Any]]],
        action: _Action,
        original: dict[str, Any],
    ) -> list[tuple[float, dict[str, Any]]]:
        if action.kind == "switch":
            return [(p, self._switch(state, action)) for p, state in branches]
        if action.kind != "move":
            return branches

        move = self._dex.moves.get(action.move_id or "")
        if not move or move["category"] == "Status":
            # Protect is the one status move whose effect is modelled, because
            # it is the interaction the equilibrium exists to handle.
            if action.move_id in PROTECTING_MOVES:
                return [(p, self._protect(state, action)) for p, state in branches]
            return branches

        out: list[tuple[float, dict[str, Any]]] = []
        for probability, state in branches:
            out.extend(self._attack(probability, state, action, move))
        return out

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
        """
        state = copy.deepcopy(state)
        side = state[action.side]
        active = side["active"]
        if action.slot < len(active) and active[action.slot] is not None:
            side["bench"] = [*side["bench"], active[action.slot]]
            active[action.slot] = None
        return state

    def _protect(self, state: dict[str, Any], action: _Action) -> dict[str, Any]:
        state = copy.deepcopy(state)
        active = state[action.side]["active"]
        if action.slot < len(active) and active[action.slot] is not None:
            active[action.slot] = {**active[action.slot], "_protected": True}
        return state

    def _attack(
        self,
        probability: float,
        state: dict[str, Any],
        action: _Action,
        move: dict[str, Any],
    ) -> list[tuple[float, dict[str, Any]]]:
        targets = self._targets(state, action, move)
        if not targets:
            return [(probability, state)]

        spread = len(targets) > 1 or move["target"] in SPREAD_TARGETS
        physical = move["category"] == "Physical"

        # Each target's roll distribution is bucketed independently; the joint
        # branch count is the product, which for a spread move into two targets
        # is four.
        branches = [(probability, state)]
        for side, slot in targets:
            expanded: list[tuple[float, dict[str, Any]]] = []
            for branch_probability, branch_state in branches:
                target_view = branch_state[side]["active"][slot]
                if target_view is None or target_view.get("_protected") or target_view["fainted"]:
                    expanded.append((branch_probability, branch_state))
                    continue
                target = combatant(target_view, self._hypothesis)
                attacker_view = branch_state[action.side]["active"][action.slot] or {}

                # The held item and the ability, from whichever source knows
                # them: our own snapshot for our side, the belief filter's
                # posterior for theirs. The default provider returns nothing,
                # so M2's numbers are unchanged when no belief is supplied.
                offence = self._effects.attacker(
                    attacker_view, move, list(target.types), self._chart
                )
                defence = self._effects.defender(target_view, move, list(target.types), self._chart)
                if offence.immune or defence.immune:
                    expanded.append((branch_probability, branch_state))
                    continue

                base_power = int(move["basePower"])
                for multiplier in offence.base_power_modifiers:
                    base_power = modify(base_power, multiplier)
                attack = action.unit.stat("atk" if physical else "spa")
                for multiplier in offence.attack_modifiers:
                    attack = modify(attack, multiplier)

                rolls = damage_roll_distribution(
                    DamageContext(
                        base_power=base_power,
                        attack=attack,
                        defense=target.stat("def" if physical else "spd"),
                        move_type=move["type"],
                        attacker_types=list(action.unit.types),
                        defender_types=list(target.types),
                        level=self._dex.level,
                        is_spread=spread,
                        attacker_burned=action.unit.status == "BRN",
                        ignore_burn=offence.ignore_burn,
                        stab_override=offence.stab_override,
                        final_modifiers=(*offence.final_modifiers, *defence.final_modifiers),
                    ),
                    self._chart,
                )
                for bucket_probability, damage in _bucket(rolls, target.hp):
                    expanded.append(
                        (
                            branch_probability * bucket_probability,
                            _apply_damage(branch_state, side, slot, damage, target),
                        )
                    )
            branches = expanded
        return branches

    def _targets(
        self, state: dict[str, Any], action: _Action, move: dict[str, Any]
    ) -> list[tuple[str, int]]:
        return targets_of(
            {side: state[side]["active"] for side in ("ours", "theirs")},
            side=action.side,
            slot=action.slot,
            described_target=int(action.described.get("target", 0) or 0),
            move_target=str(move["target"]),
        )


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


#: Moves whose Protect effect the model honours. Not every protecting move --
#: these are the ones legal and common in Reg M-B doubles.
PROTECTING_MOVES = {"protect", "detect", "spikyshield", "banefulbunker", "burningbulwark"}

#: Showdown's target strings that hit more than one slot.
SPREAD_TARGETS = {"allAdjacent", "allAdjacentFoes"}


def _bucket(rolls: list[int], remaining_hp: int) -> list[tuple[float, int]]:
    """Group the sixteen rolls into (probability, damage) buckets.

    Split on the knockout threshold, which is the discontinuity that changes the
    value; within each side of it the evaluation moves almost linearly in HP, so
    the bucket's mean is a good summary. Returns one bucket when every roll
    lands on the same side of the threshold, which is the common case and is why
    this stays cheap.
    """
    kills = [d for d in rolls if d >= remaining_hp]
    survives = [d for d in rolls if d < remaining_hp]
    total = len(rolls)

    buckets = []
    if kills:
        buckets.append((len(kills) / total, remaining_hp))
    if survives:
        buckets.append((len(survives) / total, round(sum(survives) / len(survives))))
    return buckets


def _apply_damage(
    state: dict[str, Any], side: str, slot: int, damage: int, target: Combatant
) -> dict[str, Any]:
    """Subtract damage from a slot, in the snapshot's own units.

    The snapshot carries `hp_pct` for both sides and exact `hp` only for ours,
    so the percentage is what gets updated and the exact value follows where it
    exists. That keeps one representation flowing through the model and the
    evaluation instead of two that can disagree.
    """
    state = copy.deepcopy(state)
    view = dict(state[side]["active"][slot])

    remaining = max(0, target.hp - damage)
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


def payoff_matrix(
    snapshot: dict[str, Any],
    our_actions: list[dict[str, Any]],
    their_actions: list[dict[str, Any]],
    model: TurnModel,
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

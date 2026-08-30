"""The categorical half of the belief filter: weighted whole-team hypotheses.

`docs/03-belief-filter.md` section 3. Item, ability and moveset are discrete,
strongly correlated, and constrained across the team, so independent marginals
are wrong -- sampling an item from one marginal and a moveset from another
produces sets no player would register. The representation is therefore a
weighted set of coherent whole-team hypotheses, each assigning a complete set to
each of the opponent's six species, subject to Item Clause.

Each particle also carries one `SpreadBelief` per species, because a spread
interval is only meaningful given a nature and a nature is drawn with the set
(D33). That is the join between this module and `spreads.py`: the categorical
half chooses the nature and the effects, and the interval half then knows what
the arithmetic is.

## Sampling is a Gibbs sweep, not six draws

Item Clause couples the six, so sampling is a constrained assignment problem.
A sequential draw with rejection works and degrades exactly where it matters --
when the prior concentrates several Pokemon on the same item, which in this
format means Focus Sash and Sitrus Berry, the two most common items in the
corpus by a wide margin. So the initial assignment is sequential and is then
refined by a short Gibbs sweep, resampling one Pokemon's set conditioned on the
other five.

## Reveals are hard and inference is soft

A revealed move eliminates every particle whose set lacks it; a revealed item
eliminates every particle assigning it elsewhere, and Item Clause propagates
that elimination to the other five. Those are facts about the protocol.

Damage and Speed observations are not. Opponent HP is quantized to percent, the
effects table is explicitly partial, and an unmodelled ability is a real
possibility rather than a rounding error, so a particle those observations
cannot explain is down-weighted rather than deleted. `CLAUDE.md` constraint 5
calls this the single most likely source of a silent correctness bug in the
system, and eliminating on soft evidence is exactly how that bug happens.

## Resampling replays the evidence

`docs/03` says to resample from the prior restricted to the surviving
constraints when the effective particle count falls below a threshold. Fresh
particles carry fresh natures, so their spread intervals would start
unconstrained and the filter would silently forget every Speed and damage
observation it had made. Instead the soft evidence is kept in a bounded replay
buffer and re-applied to each new particle, so a resample changes which
hypotheses are alive without changing what has been observed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from champions.belief import effects as effect_table
from champions.belief.evidence import DamageEvidence, Evidence, Reveal, SpeedEvidence
from champions.belief.priors import SetHypothesis, SetPrior
from champions.belief.spreads import SpreadBelief, joint_restrict
from champions.dex.damage import DamageContext, TypeChart, boosted, damage_for_roll, modify
from champions.dex.loader import Dex, to_id
from champions.dex.stats import MAX_POINTS_PER_STAT, STAT_IDS

#: Default particle count. `docs/03` section 3 puts the real-clock budget at 20
#: to 50 alongside pruned search and roll integration, and says to start higher
#: while the clock is deferred and tune down at M11. The M2 measurement makes
#: that concrete: the whole decision costs about 11 ms against 45 seconds, so
#: there is room, and a filter that is too coarse is harder to diagnose than one
#: that is too slow.
DEFAULT_PARTICLES = 64

#: Resample when the effective sample size falls below this fraction of the
#: particle count. The standard trigger; the number is not load-bearing.
ESS_FRACTION = 0.5

#: What a particle's weight is multiplied by when soft evidence cannot explain
#: it. Not zero, on purpose -- see the module docstring. Two decades of evidence
#: still drive it to irrelevance, which is the behaviour we want, without any
#: single quantized observation being able to delete the truth.
SOFT_INCONSISTENT = 0.05

#: Percentage points of slack on an opponent HP reading. Each endpoint of a
#: percentage drop is rounded, so the drop itself carries up to two points of
#: error before anything compounds (`CLAUDE.md` constraint 5).
PERCENT_TOLERANCE = 2.0

#: Fractional slack on an exact HP damage figure when every effect on both sides
#: is in `effects.py`'s tables. Small: the only remaining error is the
#: difference between our modelled multipliers and the simulator's, which is
#: rounding.
DAMAGE_TOLERANCE = 0.05

#: The same, when either side holds an item or ability the table does not model.
#: Wide enough to survive one unmodelled multiplier in either direction, which
#: is the realistic failure. Wide bounds are the safe direction -- they weaken
#: the inference rather than eliminating the truth -- but they are not free, and
#: a blanket wide tolerance is why an earlier version of this filter narrowed no
#: interval at all across a whole battle.
UNCERTAIN_TOLERANCE = 0.4

#: Extra percentage-point slack under the same condition.
UNCERTAIN_PERCENT_TOLERANCE = 12.0

#: How much soft evidence is replayed onto a resampled particle. Bounded so a
#: long battle cannot make resampling quadratic; the oldest evidence is also the
#: least informative, because the intervals it implied have usually been
#: subsumed by later ones.
REPLAY_LIMIT = 48

#: Candidate sets offered per species when building a particle. The prior's
#: observed sets are heavily concentrated, so the tail past this adds
#: candidates that would essentially never be drawn.
CANDIDATES_PER_SPECIES = 48


@dataclass
class TeamConstraints:
    """What the protocol has established about the opponent's team. Hard facts.

    Keyed by species id rather than by slot: the same Pokemon occupies different
    slots across a battle, and Item Clause is a statement about the team.
    """

    moves: dict[str, set[str]] = field(default_factory=dict)
    item: dict[str, str] = field(default_factory=dict)
    ability: dict[str, str] = field(default_factory=dict)
    #: Items ruled out for a species because another species was seen holding
    #: them. Item Clause, propagated.
    excluded_items: dict[str, set[str]] = field(default_factory=dict)

    def add(self, reveal: Reveal) -> None:
        species = reveal.actor.species
        if not species:
            return
        if reveal.kind == "move":
            self.moves.setdefault(species, set()).add(reveal.value)
        elif reveal.kind == "item":
            self.item[species] = reveal.value
            for other in list(self.moves) + list(self.item) + list(self.ability):
                if other != species:
                    self.excluded_items.setdefault(other, set()).add(reveal.value)
        elif reveal.kind == "ability":
            self.ability[species] = reveal.value

    def note_species(self, species: str) -> None:
        """Register a species so Item Clause exclusions reach it.

        Called for every previewed species at the start, because an item
        revealed on turn one has to exclude itself from Pokemon that have not
        appeared yet -- which are exactly the ones we most need the prior to be
        right about.
        """
        self.moves.setdefault(species, set())
        for holder, item in self.item.items():
            if holder != species:
                self.excluded_items.setdefault(species, set()).add(item)

    def allows(self, hypothesis: SetHypothesis) -> bool:
        species = hypothesis.species
        required = self.moves.get(species)
        if required and not required.issubset(hypothesis.moves):
            return False
        known_item = self.item.get(species)
        if known_item is not None and hypothesis.item != known_item:
            return False
        if hypothesis.item and hypothesis.item in self.excluded_items.get(species, ()):
            return False
        known_ability = self.ability.get(species)
        return not (known_ability is not None and hypothesis.ability != known_ability)

    def as_dict(self) -> dict[str, Any]:
        return {
            "moves": {k: sorted(v) for k, v in self.moves.items() if v},
            "item": dict(self.item),
            "ability": dict(self.ability),
        }


@dataclass
class Particle:
    """One coherent hypothesis about the whole opponent team."""

    sets: dict[str, SetHypothesis]
    spreads: dict[str, SpreadBelief]
    log_weight: float = 0.0
    alive: bool = True

    def copy(self) -> Particle:
        return Particle(
            sets=dict(self.sets),
            spreads={k: v.copy() for k, v in self.spreads.items()},
            log_weight=self.log_weight,
            alive=self.alive,
        )


class ParticleFilter:
    """The opponent belief: weighted team hypotheses, updated by evidence.

    One per battle. `species` is the previewed six, which in Champions is the
    entire preview: six species with level and gender, and nothing else.
    """

    def __init__(
        self,
        dex: Dex,
        prior: SetPrior,
        species: Sequence[str],
        n_particles: int = DEFAULT_PARTICLES,
        rng: np.random.Generator | None = None,
    ) -> None:
        self.dex = dex
        self.prior = prior
        self.species = [to_id(s) for s in species]
        self.n_particles = n_particles
        self.rng = rng if rng is not None else np.random.default_rng(0)
        self.constraints = TeamConstraints()
        for species_id in self.species:
            self.constraints.note_species(species_id)
        self._chart = TypeChart.from_dex(dex)
        self._replay: list[Evidence] = []
        self._replay_context: BeliefContext | None = None
        self._bounds_cache: dict[tuple, tuple[int, int]] = {}
        self.resamples = 0
        self.particles: list[Particle] = []
        self._candidates: dict[str, list[tuple[SetHypothesis, float]]] = {}
        self.reseed()

    # -- construction ---------------------------------------------------

    def reseed(self) -> None:
        """Draw a fresh population from the prior, restricted to the constraints."""
        self._candidates = {s: self._candidate_sets(s) for s in self.species}
        self.particles = [self._draw_particle() for _ in range(self.n_particles)]
        for particle in self.particles:
            self._replay_onto(particle)
        self._renormalise()

    def _candidate_sets(self, species: str) -> list[tuple[SetHypothesis, float]]:
        """Sets this species could have, with prior weights, after hard constraints.

        Observed sets first, then composed ones from the marginals so that a
        species the corpus has barely seen is not made certain by a handful of
        observations. Falls back to composed sets alone when the constraints
        eliminate everything observed, which is what a revealed move nobody in
        the corpus ran looks like.
        """
        observed = self.prior.observed_sets(species)
        total = sum(weight for _, weight in observed) or 1
        mix = self.prior.species.get(species)
        empirical_weight = mix.empirical_weight() if mix else 0.0

        candidates: list[tuple[SetHypothesis, float]] = []
        for hypothesis, weight in observed[:CANDIDATES_PER_SPECIES]:
            if self.constraints.allows(hypothesis):
                candidates.append((hypothesis, empirical_weight * weight / total))

        composed = self._composed_candidates(species)
        composed_weight = max(1.0 - empirical_weight, 0.05 if candidates else 1.0)
        per = composed_weight / len(composed) if composed else 0.0
        candidates.extend((hypothesis, per) for hypothesis in composed)

        if not candidates:
            # Nothing survives. Rather than return an empty list -- which would
            # make the whole filter vacuous at the moment it matters most -- fall
            # back to a set built to satisfy the constraints directly.
            candidates = [(self._forced_set(species), 1.0)]
        return candidates

    def _composed_candidates(self, species: str, count: int = 8) -> list[SetHypothesis]:
        """Plausible sets built from this species' per-field marginals.

        Wrong as the *only* source, which is section 3's whole point. Right as a
        tail, so that a revealed move or item outside the corpus still has
        somewhere to live.
        """
        marginals = self.prior.marginals(species)
        learnset = self._legal_moves(species)
        required = self.constraints.moves.get(species, set())
        out: list[SetHypothesis] = []
        for _ in range(count):
            item = self.constraints.item.get(species) or self._draw(marginals["item"])
            if item in self.constraints.excluded_items.get(species, ()):
                item = None
            ability = self.constraints.ability.get(species) or self._draw(marginals["ability"])
            moves = set(required)
            pool = {m: p for m, p in marginals["move"].items() if not learnset or m in learnset}
            while len(moves) < 4 and pool:
                pick = self._draw({m: p for m, p in pool.items() if m not in moves})
                if pick is None:
                    break
                moves.add(pick)
            out.append(
                SetHypothesis(
                    species=species,
                    item=item,
                    ability=ability,
                    moves=frozenset(moves),
                    nature=self._draw(marginals["nature"]) or "hardy",
                    composed=True,
                )
            )
        return out

    def _forced_set(self, species: str) -> SetHypothesis:
        """The last resort: a set that satisfies every hard constraint by construction."""
        marginals = self.prior.marginals(species)
        return SetHypothesis(
            species=species,
            item=self.constraints.item.get(species),
            ability=self.constraints.ability.get(species)
            or self._default_ability(species)
            or self._draw(marginals["ability"]),
            moves=frozenset(self.constraints.moves.get(species, set())),
            nature=self._draw(marginals["nature"]) or "hardy",
            composed=True,
        )

    def _default_ability(self, species: str) -> str | None:
        entry = self.dex.species.get(species)
        if not entry:
            return None
        abilities = entry.get("abilities") or {}
        return to_id(abilities.get("0")) or None

    def _legal_moves(self, species: str) -> set[str]:
        learnset = self.dex.learnsets.get(species) or {}
        moves = learnset.get("learnset") if isinstance(learnset, dict) else None
        return set(moves) if isinstance(moves, dict) else set()

    def _draw(self, distribution: Mapping[str, float]) -> str | None:
        if not distribution:
            return None
        keys = list(distribution)
        weights = np.array([max(float(distribution[k]), 0.0) for k in keys])
        total = weights.sum()
        if total <= 0:
            return keys[int(self.rng.integers(len(keys)))]
        return keys[int(self.rng.choice(len(keys), p=weights / total))]

    def _draw_particle(self, sweeps: int = 2) -> Particle:
        """One coherent team: a sequential draw, then a short Gibbs refinement."""
        assignment: dict[str, SetHypothesis] = {}
        order = list(self.species)
        self.rng.shuffle(order)
        for species in order:
            assignment[species] = self._draw_set(species, assignment)
        for _ in range(sweeps):
            self.rng.shuffle(order)
            for species in order:
                others = {k: v for k, v in assignment.items() if k != species}
                assignment[species] = self._draw_set(species, others)
        return Particle(
            sets=assignment,
            spreads={s: self._fresh_spread(s, h) for s, h in assignment.items()},
        )

    def _draw_set(
        self,
        species: str,
        others: Mapping[str, SetHypothesis],
    ) -> SetHypothesis:
        """Resample one Pokemon's set conditioned on the other five.

        Item Clause enters as a filter on the candidate list, and the teammate
        lift as a multiplicative reweighting. Both are conditioning on the rest
        of the team, which is what section 3 asks for and what six independent
        draws cannot do.
        """
        taken = {h.item for h in others.values() if h.item}
        teammates = [s for s in self.species if s != species]
        lift = self.prior.teammate_lift(species, teammates)

        candidates = self._candidates.get(species) or self._candidate_sets(species)
        allowed = [(h, w) for h, w in candidates if not h.item or h.item not in taken]
        if not allowed:
            allowed = [(h, w) for h, w in candidates]
        weights = np.array([max(w, 1e-9) * lift for _, w in allowed])
        weights = weights / weights.sum()
        return allowed[int(self.rng.choice(len(allowed), p=weights))][0]

    def _fresh_spread(self, species: str, hypothesis: SetHypothesis) -> SpreadBelief:
        entry = self.dex.species.get(species) or {}
        base_stats = entry.get("baseStats") or dict.fromkeys(STAT_IDS, 100)
        try:
            nature_entry = self.dex.nature(hypothesis.nature)
        except KeyError:
            nature_entry = {}
        return SpreadBelief.unconstrained(base_stats, hypothesis.nature, nature_entry)

    # -- updating -------------------------------------------------------

    def observe(self, evidence: Iterable[Evidence], context: BeliefContext) -> None:
        """Fold a turn's evidence into the population."""
        # Kept so a resample can replay the soft evidence. Our own side is what
        # the replay needs and it barely changes within a battle -- the same
        # stats, the same sets -- so the latest view is the right one to reuse.
        self._replay_context = context
        soft: list[Evidence] = []
        hard = False
        for item in evidence:
            if isinstance(item, Reveal):
                if (
                    item.actor.side == context.opponent_side
                    and item.actor.species
                    and self._reveal_is_about_the_set(item)
                ):
                    self.constraints.add(item)
                    hard = True
            else:
                soft.append(item)

        if hard:
            self._apply_constraints()

        for item in soft:
            self._apply_soft(item, context)
        self._replay.extend(soft)
        del self._replay[:-REPLAY_LIMIT]

        self._renormalise()
        if self.effective_sample_size() < ESS_FRACTION * self.n_particles:
            self.resample()

    def _priority_could_explain(
        self,
        hypothesis: SetHypothesis | None,
        move_id: str | None,
        they_moved_first: bool,
    ) -> bool:
        """Whether an ability, rather than Speed, could account for going first.

        Prankster raises a status move's priority by one, Gale Wings does the
        same for a Flying move at full HP, Quick Draw does it at random. Reading
        any of those as a Speed inequality bounds the wrong quantity, and there
        is no tolerance wide enough to make a whole priority bracket safe --
        which is why this is a skip rather than a widened bound.
        """
        if not they_moved_first or hypothesis is None or hypothesis.ability is None:
            return False
        if hypothesis.ability not in effect_table.PRIORITY_ABILITIES:
            return False
        move = self.dex.moves.get(move_id or "")
        if hypothesis.ability == "prankster":
            return bool(move and move.get("category") == "Status")
        return True

    def _reveal_is_about_the_set(self, reveal: Reveal) -> bool:
        """Whether a reveal says something about the *registered* set.

        Not everything the protocol attributes to a Pokemon is a property of
        what its owner registered. A Mega Evolution replaces the ability
        outright -- Gengar-Mega is Shadow Tag whatever Gengar was -- so an
        `[from] ability: Shadow Tag` after the mega describes the forme, not the
        set, and taking it as a hard constraint eliminates every particle at
        once. The same guard covers Trace and Skill Swap.

        The test is legality: an ability the species cannot have was not
        registered on it. Anything not recognised is allowed through, because
        the corpus is a better authority on what people register than a
        hand-written exclusion list.
        """
        if reveal.kind != "ability":
            return True
        entry = self.dex.species.get(reveal.actor.species or "")
        if not entry:
            return True
        legal = {to_id(name) for name in (entry.get("abilities") or {}).values()}
        return not legal or reveal.value in legal

    def _apply_constraints(self) -> None:
        for particle in self.particles:
            if not particle.alive:
                continue
            for hypothesis in particle.sets.values():
                if not self.constraints.allows(hypothesis):
                    particle.alive = False
                    break
            else:
                # Item Clause within the particle itself. A particle that
                # assigns one item twice was never legal; it can only arise if
                # a reveal collided with an assignment made before it.
                items = [h.item for h in particle.sets.values() if h.item]
                if len(items) != len(set(items)):
                    particle.alive = False

    def _apply_soft(self, evidence: Evidence, context: BeliefContext) -> None:
        if isinstance(evidence, SpeedEvidence):
            self._apply_speed(evidence, context)
        elif isinstance(evidence, DamageEvidence):
            self._apply_damage(evidence, context)

    def _replay_onto(self, particle: Particle) -> None:
        context = self._replay_context
        if context is None:
            return
        for evidence in self._replay:
            if isinstance(evidence, SpeedEvidence):
                self._apply_speed(evidence, context, only=particle)
            elif isinstance(evidence, DamageEvidence):
                self._apply_damage(evidence, context, only=particle)

    # -- speed ----------------------------------------------------------

    def _apply_speed(
        self,
        evidence: SpeedEvidence,
        context: BeliefContext,
        only: Particle | None = None,
    ) -> None:
        """One ordering, turned into a bound on their Speed points.

        Only usable when exactly one of the two Pokemon is ours, because only
        then is one side of the inequality a number we know. Two of theirs
        moving in order relates two unknowns and is left on the floor; it would
        need a joint representation across Pokemon that the box does not have.
        """
        ours, theirs, they_are_faster = _sides(evidence, context.opponent_side)
        if ours is None or theirs is None or not theirs.species:
            return

        our_modifier = evidence.slower_modifier if they_are_faster else evidence.faster_modifier
        our_boost = evidence.slower_boost if they_are_faster else evidence.faster_boost
        our_speed = context.our_speed(ours.slot, our_boost, our_modifier)
        if our_speed is None:
            return

        # Trick Room reverses which of the two must be the larger number.
        theirs_is_larger = they_are_faster != evidence.trick_room
        their_modifier = evidence.faster_modifier if they_are_faster else evidence.slower_modifier
        their_boost = evidence.faster_boost if they_are_faster else evidence.slower_boost

        their_base = self._base_stats(theirs.species, theirs.forme)
        their_move = evidence.faster_move if they_are_faster else evidence.slower_move

        for particle in self._live(only):
            spread = particle.spreads.get(theirs.species)
            if spread is None:
                continue
            hypothesis = particle.sets.get(theirs.species)
            if self._priority_could_explain(hypothesis, their_move, they_are_faster):
                # Their ability may have raised the move's priority, in which
                # case the ordering says nothing about Speed at all. Skipping
                # is right rather than down-weighting: nothing was contradicted,
                # there is simply no inequality to draw.
                continue
            item_modifier = effect_table.speed_multiplier(hypothesis)
            modifier = their_modifier * item_modifier
            if modifier <= 0:
                continue
            allowed = [
                points
                for points in spread.feasible_points("spe")
                if _speed_consistent(
                    boosted(spread.stat_at("spe", points, their_base), their_boost) * modifier,
                    our_speed,
                    theirs_is_larger,
                )
            ]
            if not allowed:
                particle.log_weight += math.log(SOFT_INCONSISTENT)
                continue
            if not spread.restrict_points("spe", allowed):
                particle.log_weight += math.log(SOFT_INCONSISTENT)

    # -- damage ---------------------------------------------------------

    def _apply_damage(
        self,
        evidence: DamageEvidence,
        context: BeliefContext,
        only: Particle | None = None,
    ) -> None:
        if evidence.attacker.side == context.opponent_side:
            self._apply_incoming(evidence, context, only)
        else:
            self._apply_outgoing(evidence, context, only)

    def _apply_incoming(
        self,
        evidence: DamageEvidence,
        context: BeliefContext,
        only: Particle | None,
    ) -> None:
        """They hit us. Exact HP against a defender we know exactly, so this
        bounds their offensive investment and nothing else."""
        species = evidence.attacker.species
        move = self.dex.moves.get(evidence.move_id)
        if not species or not move or move.get("category") == "Status":
            return
        defender = context.our_pokemon(evidence.defender.slot)
        if defender is None:
            return
        defender_types = list(evidence.defender_types) or list(defender.get("types") or [])

        physical = move["category"] == "Physical"
        attack_stat = "atk" if physical else "spa"
        defense = boosted(
            int(defender["stats"]["def" if physical else "spd"]),
            evidence.defender_boosts.get("def" if physical else "spd", 0),
        )
        attacker_types = self._types_of(species, evidence.attacker_types)
        attacker_base = self._base_stats(species, evidence.attacker_forme)
        target = evidence.lost
        # D27: the simulator reports HP actually lost, so a roll that would
        # overkill is reported as the remaining HP. Treating that as the damage
        # would make every knockout look like an under-prediction.
        clamped = defender.get("hp") is not None and target >= float(defender["hp"])

        for particle in self._live(only):
            spread = particle.spreads.get(species)
            if spread is None:
                continue
            hypothesis = self._forme_effects(particle.sets.get(species), evidence.attacker_forme)
            attacker = effect_table.attacker_effects(
                hypothesis,
                {**move, "_attacker_types": attacker_types},
                defender_types,
                self._chart,
                statused=bool(evidence.attacker_status),
            )
            defender_side = effect_table.defender_effects(
                context.our_set(evidence.defender.slot),
                move,
                defender_types,
                self._chart,
                at_full_hp=defender.get("hp") == defender.get("max_hp"),
            )
            if defender_side.immune or attacker.immune:
                continue
            certain = attacker.is_certain and defender_side.is_certain
            tolerance = DAMAGE_TOLERANCE if certain else UNCERTAIN_TOLERANCE

            allowed = [
                points
                for points in spread.feasible_points(attack_stat)
                if _damage_consistent(
                    self._roll_bounds(
                        move=move,
                        attack=boosted(
                            spread.stat_at(attack_stat, points, attacker_base),
                            evidence.attacker_boosts.get(attack_stat, 0),
                        ),
                        defense=defense,
                        attacker_types=attacker_types,
                        defender_types=defender_types,
                        spread_move=evidence.spread,
                        attacker=attacker,
                        defender=defender_side,
                        burned=evidence.attacker_status == "BRN",
                        crit=evidence.crit,
                        weather=evidence.weather,
                    ),
                    target,
                    tolerance,
                    clamped,
                )
            ]
            if not allowed:
                particle.log_weight += math.log(SOFT_INCONSISTENT)
                continue
            if not spread.restrict_points(attack_stat, allowed):
                particle.log_weight += math.log(SOFT_INCONSISTENT)

    def _apply_outgoing(
        self,
        evidence: DamageEvidence,
        context: BeliefContext,
        only: Particle | None,
    ) -> None:
        """We hit them. The figure is a percentage of a maximum HP we do not
        know, so it bounds their bulk and their HP jointly (`docs/03` section 2).
        """
        species = evidence.defender.species
        move = self.dex.moves.get(evidence.move_id)
        if not species or not move or move.get("category") == "Status":
            return
        attacker = context.our_pokemon(evidence.attacker.slot)
        if attacker is None or not evidence.is_percent:
            return

        physical = move["category"] == "Physical"
        defense_stat = "def" if physical else "spd"
        attack = boosted(
            int(attacker["stats"]["atk" if physical else "spa"]),
            evidence.attacker_boosts.get("atk" if physical else "spa", 0),
        )
        defender_types = self._types_of(species, evidence.defender_types)
        defender_base = self._base_stats(species, evidence.defender_forme)
        attacker_types = list(evidence.attacker_types) or list(attacker.get("types") or [])
        boost = evidence.defender_boosts.get(defense_stat, 0)

        for particle in self._live(only):
            spread = particle.spreads.get(species)
            if spread is None:
                continue
            hypothesis = self._forme_effects(particle.sets.get(species), evidence.defender_forme)
            attacker_effects = effect_table.attacker_effects(
                context.our_set(evidence.attacker.slot),
                {**move, "_attacker_types": attacker_types},
                defender_types,
                self._chart,
                statused=False,
            )
            defender_effects = effect_table.defender_effects(
                hypothesis,
                move,
                defender_types,
                self._chart,
                at_full_hp=False,
            )
            if defender_effects.immune:
                continue
            certain = attacker_effects.is_certain and defender_effects.is_certain
            tolerance = PERCENT_TOLERANCE if certain else UNCERTAIN_PERCENT_TOLERANCE

            # The damage depends on the Defence points and not on the HP points,
            # and the HP points decide only what percentage a given damage is.
            # So the roll bounds are computed 33 times rather than 33 x 33, and
            # the joint sweep is pure arithmetic over the results. This is the
            # difference between a belief update costing six seconds and costing
            # a few milliseconds.
            bounds_by_defense = {
                defense_points: self._roll_bounds(
                    move=move,
                    attack=attack,
                    defense=boosted(
                        spread.stat_at(defense_stat, defense_points, defender_base), boost
                    ),
                    attacker_types=attacker_types,
                    defender_types=defender_types,
                    spread_move=evidence.spread,
                    attacker=attacker_effects,
                    defender=defender_effects,
                    burned=False,
                    crit=evidence.crit,
                    weather=evidence.weather,
                )
                for defense_points in spread.feasible_points(defense_stat)
            }

            allowed: list[tuple[int, int]] = []
            for hp_points in spread.feasible_points("hp"):
                max_hp = spread.stat_at("hp", hp_points, defender_base)
                low = max_hp * (evidence.lost - tolerance) / 100.0
                high = max_hp * (evidence.lost + tolerance) / 100.0
                for defense_points, bounds in bounds_by_defense.items():
                    if bounds[1] >= low and bounds[0] <= high:
                        allowed.append((hp_points, defense_points))

            if not allowed:
                particle.log_weight += math.log(SOFT_INCONSISTENT)
                continue
            if not joint_restrict(spread, "hp", defense_stat, allowed):
                particle.log_weight += math.log(SOFT_INCONSISTENT)

    def _roll_bounds(
        self,
        *,
        move: Mapping[str, Any],
        attack: int,
        defense: int,
        attacker_types: Sequence[str],
        defender_types: Sequence[str],
        spread_move: bool,
        attacker: effect_table.SetEffects,
        defender: effect_table.SetEffects,
        burned: bool,
        crit: bool = False,
        weather: str | None = None,
    ) -> tuple[int, int]:
        """The lowest and highest of the sixteen rolls, in HP.

        Only the two endpoints: the feasible-set test is an interval overlap, so
        the fourteen rolls between them change nothing and cost eight times as
        much.

        Memoized, because the population is far less diverse than it looks. Most
        of 64 particles hypothesise one of a handful of sets, and the sweep
        below evaluates this once per candidate point value for each of them --
        so the cache turns tens of thousands of damage calculations per turn
        into hundreds.
        """
        key = (
            str(move.get("id") or move.get("name")),
            attack,
            defense,
            tuple(attacker_types),
            tuple(defender_types),
            spread_move,
            burned,
            crit,
            weather,
            _effect_key(attacker),
            _effect_key(defender),
        )
        cached = self._bounds_cache.get(key)
        if cached is not None:
            return cached

        base_power = int(move.get("basePower") or 0)
        for multiplier in attacker.base_power_modifiers:
            base_power = modify(base_power, multiplier)
        for multiplier in attacker.attack_modifiers:
            attack = modify(attack, multiplier)
        defense = _weathered_defense(defense, defender_types, move, weather)
        context = DamageContext(
            base_power=base_power,
            attack=attack,
            defense=max(defense, 1),
            move_type=str(move.get("type") or ""),
            attacker_types=list(attacker_types),
            defender_types=list(defender_types),
            level=self.dex.level,
            is_spread=spread_move,
            is_crit=crit,
            attacker_burned=burned,
            ignore_burn=attacker.ignore_burn,
            stab_override=attacker.stab_override,
            weather_modifiers=_weather_modifiers(move, weather),
            final_modifiers=(*attacker.final_modifiers, *defender.final_modifiers),
        )
        bounds = (
            damage_for_roll(context, self._chart, 85),
            damage_for_roll(context, self._chart, 100),
        )
        self._bounds_cache[key] = bounds
        return bounds

    def _types_of(self, species: str, observed: Sequence[str] = ()) -> list[str]:
        """Observed types win over the species entry's.

        Protean rewrites a Pokemon's types on every move it uses, so the dex
        entry is a statement about what it started as. Using it anyway made the
        filter read a resisted hit as bulk and a STAB hit as raw offence, and
        pinned both to the wrong end of their ranges on a checked-in test team.
        """
        if observed:
            return list(observed)
        entry = self.dex.species.get(species) or {}
        return list(entry.get("types") or [])

    def _base_stats(self, species: str, forme: str | None = None) -> Mapping[str, int]:
        """Base stats for the forme currently on the field, not the base species.

        The points are a property of the set and do not change when a Pokemon
        Mega Evolves; the base stats they are added to do. Greninja-Mega has 142
        base Speed against Greninja's 122, so reading the base forme here makes
        every inference about a mega'd Pokemon wrong in the same direction.
        """
        entry = (forme and self.dex.species.get(forme)) or self.dex.species.get(species) or {}
        return entry.get("baseStats") or dict.fromkeys(STAT_IDS, 100)

    def _forme_effects(
        self,
        hypothesis: SetHypothesis | None,
        forme: str | None,
    ) -> SetHypothesis | None:
        """The set as it currently is, with a mega forme's ability substituted in.

        A Mega Evolution replaces the ability outright -- Gengar-Mega is Shadow
        Tag whatever Gengar was -- and a forme with exactly one ability leaves
        nothing to infer. Held item is untouched: the Mega Stone is still held.
        """
        if hypothesis is None or not forme or forme == hypothesis.species:
            return hypothesis
        entry = self.dex.species.get(forme) or {}
        abilities = entry.get("abilities") or {}
        if len(abilities) != 1:
            return hypothesis
        only = to_id(next(iter(abilities.values())))
        return SetHypothesis(
            species=hypothesis.species,
            item=hypothesis.item,
            ability=only or hypothesis.ability,
            moves=hypothesis.moves,
            nature=hypothesis.nature,
            composed=hypothesis.composed,
        )

    # -- population bookkeeping -----------------------------------------

    def _live(self, only: Particle | None = None) -> list[Particle]:
        if only is not None:
            return [only] if only.alive else []
        return [p for p in self.particles if p.alive]

    def weights(self) -> np.ndarray:
        raw = np.array(
            [math.exp(p.log_weight) if p.alive else 0.0 for p in self.particles],
            dtype=float,
        )
        total = raw.sum()
        if total <= 0:
            return np.zeros(len(self.particles))
        return raw / total

    def _renormalise(self) -> None:
        """Shift log weights so the largest is zero. Prevents underflow without
        changing any ratio, which is all the weights ever mean."""
        live = self._live()
        if not live:
            return
        peak = max(p.log_weight for p in live)
        for particle in live:
            particle.log_weight -= peak

    def effective_sample_size(self) -> float:
        weights = self.weights()
        square = float((weights**2).sum())
        return 1.0 / square if square > 0 else 0.0

    def resample(self) -> None:
        """Redraw from the prior restricted to the surviving constraints.

        Not a multinomial resample of the existing population: the reason the
        ESS collapsed is usually that a reveal killed most of it, and copying
        the survivors would concentrate the belief on whichever few hypotheses
        happened to be drawn first. Drawing fresh from the constrained prior and
        replaying the soft evidence keeps the diversity that makes the filter
        worth having.
        """
        self.resamples += 1
        self._candidates = {s: self._candidate_sets(s) for s in self.species}
        fresh = [self._draw_particle() for _ in range(self.n_particles)]
        for particle in fresh:
            self._replay_onto(particle)
        self.particles = fresh
        self._renormalise()

    # -- reading it back ------------------------------------------------

    def marginals(self, species: str) -> dict[str, Any]:
        """Per-field posteriors for one species, plus the spread interval.

        The interval reported is the union across live particles, which is a
        superset of any single particle's box. That is the safe direction and
        the honest one: the filter is not more certain about a stat than its
        least certain surviving hypothesis.
        """
        species = to_id(species)
        weights = self.weights()
        items: dict[str, float] = {}
        abilities: dict[str, float] = {}
        natures: dict[str, float] = {}
        moves: dict[str, float] = {}
        sets: dict[SetHypothesis, float] = {}
        lower: dict[str, int] = {}
        upper: dict[str, int] = {}

        for particle, weight in zip(self.particles, weights, strict=True):
            if weight <= 0:
                continue
            hypothesis = particle.sets.get(species)
            if hypothesis is None:
                continue
            sets[hypothesis] = sets.get(hypothesis, 0.0) + float(weight)
            if hypothesis.item:
                items[hypothesis.item] = items.get(hypothesis.item, 0.0) + float(weight)
            if hypothesis.ability:
                abilities[hypothesis.ability] = abilities.get(hypothesis.ability, 0.0) + float(
                    weight
                )
            natures[hypothesis.nature] = natures.get(hypothesis.nature, 0.0) + float(weight)
            for move in hypothesis.moves:
                moves[move] = moves.get(move, 0.0) + float(weight)
            spread = particle.spreads.get(species)
            if spread is not None and spread.feasible:
                for stat in STAT_IDS:
                    lower[stat] = min(lower.get(stat, MAX_POINTS_PER_STAT), spread.lower[stat])
                    upper[stat] = max(upper.get(stat, 0), spread.upper[stat])

        top = sorted(sets.items(), key=lambda kv: -kv[1])[:5]
        modal = self.expected_spread(species)
        return {
            "species": species,
            "item": _ranked(items),
            "ability": _ranked(abilities),
            "nature": _ranked(natures),
            "moves": _ranked(moves, limit=8),
            "sets": [{**h.as_dict(), "probability": round(p, 4)} for h, p in top],
            # The union across live particles. A superset of any one particle's
            # box, so it is the honest thing to *display* -- the filter is not
            # more certain about a stat than its least certain hypothesis.
            "points": {s: [lower.get(s, 0), upper.get(s, MAX_POINTS_PER_STAT)] for s in STAT_IDS},
            "stats": self._stat_bounds(species, lower, upper),
            # The highest-weight particle's box, which is what the search
            # actually reads through `BattleBelief.stats_for`. Reported
            # separately and scored separately, because measuring coverage on
            # the union while the payoff model consumes the modal box would be
            # measuring a number nothing uses -- and the union is nearly always
            # covering, so it would look reassuring while saying nothing.
            "points_modal": (
                {s: [modal.lower[s], modal.upper[s]] for s in STAT_IDS} if modal else None
            ),
            "stats_modal": modal.stats() if modal else None,
        }

    def _stat_bounds(
        self,
        species: str,
        lower: Mapping[str, int],
        upper: Mapping[str, int],
    ) -> dict[str, list[int]]:
        spread = self.expected_spread(species)
        if spread is None:
            return {}
        return {
            stat: [
                spread.stat_at(stat, lower.get(stat, 0)),
                spread.stat_at(stat, upper.get(stat, MAX_POINTS_PER_STAT)),
            ]
            for stat in STAT_IDS
        }

    def most_likely(self, species: str) -> SetHypothesis | None:
        """The single highest-weight set hypothesis for one species."""
        species = to_id(species)
        best: tuple[float, SetHypothesis] | None = None
        for particle, weight in zip(self.particles, self.weights(), strict=True):
            hypothesis = particle.sets.get(species)
            if hypothesis is None or weight <= 0:
                continue
            if best is None or weight > best[0]:
                best = (float(weight), hypothesis)
        return best[1] if best else None

    def expected_spread(self, species: str) -> SpreadBelief | None:
        """The highest-weight particle's spread belief for one species.

        A single particle's box rather than the union, because the caller wants
        six numbers that are jointly plausible: the union's midpoints can
        describe a spread spending more than 66 points, which is not a Pokemon.
        """
        species = to_id(species)
        best: tuple[float, SpreadBelief] | None = None
        for particle, weight in zip(self.particles, self.weights(), strict=True):
            spread = particle.spreads.get(species)
            if spread is None or weight <= 0:
                continue
            if best is None or weight > best[0]:
                best = (float(weight), spread)
        return best[1] if best else None

    def set_probability(self, species: str, hypothesis: SetHypothesis) -> float:
        """Posterior mass on one exact set. The quantity `docs/03` section 5
        takes the negative log of."""
        species = to_id(species)
        total = 0.0
        for particle, weight in zip(self.particles, self.weights(), strict=True):
            if particle.sets.get(species) == hypothesis:
                total += float(weight)
        return total

    def summary(self) -> dict[str, Any]:
        """The `belief` trace payload."""
        weights = self.weights()
        return {
            "particles": len(self.particles),
            "alive": int(sum(1 for p in self.particles if p.alive)),
            "effective_sample_size": round(self.effective_sample_size(), 2),
            "resamples": self.resamples,
            "constraints": self.constraints.as_dict(),
            "team": [self.marginals(s) for s in self.species],
            "max_weight": round(float(weights.max()) if len(weights) else 0.0, 4),
        }


@dataclass
class BeliefContext:
    """Our own side of the battle, as the filter needs to read it.

    A narrow view rather than the whole snapshot, so that the filter's
    dependency on poke-env's battle object is exactly zero and an offline
    evaluation over stored replays can supply the same four numbers.
    """

    opponent_side: str
    #: slot ("p1a") -> our Pokemon's snapshot view, with exact stats.
    ours_by_slot: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: slot -> the set we are actually running, for the effects table.
    our_sets: Mapping[str, SetHypothesis] = field(default_factory=dict)
    boosts: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    tailwind_sides: frozenset[str] = frozenset()

    def our_pokemon(self, slot: str | None) -> Mapping[str, Any] | None:
        return self.ours_by_slot.get(slot or "")

    def our_set(self, slot: str | None) -> SetHypothesis | None:
        return self.our_sets.get(slot or "")

    def boosts_of(self, slot: str | None) -> Mapping[str, int]:
        return self.boosts.get(slot or "", {})

    def our_speed(self, slot: str | None, boost: int, modifier: float) -> float | None:
        """Our own effective Speed, which is the known side of every ordering.

        The boost and the paralysis/Tailwind modifier come from the evidence
        rather than from here, because they are properties of the moment the
        ordering happened and this view is rebuilt every turn.
        """
        pokemon = self.our_pokemon(slot)
        if not pokemon or not pokemon.get("stats"):
            return None
        return boosted(int(pokemon["stats"]["spe"]), boost) * modifier


def _sides(
    evidence: SpeedEvidence,
    opponent_side: str,
) -> tuple[Any, Any, bool]:
    """Split an ordering into ours and theirs, and say whether theirs was faster."""
    if evidence.faster.side == opponent_side and evidence.slower.side != opponent_side:
        return evidence.slower, evidence.faster, True
    if evidence.slower.side == opponent_side and evidence.faster.side != opponent_side:
        return evidence.faster, evidence.slower, False
    return None, None, False


def _speed_consistent(their_speed: float, our_speed: float, theirs_is_larger: bool) -> bool:
    """The ordering inequality, with the tie allowed on both sides.

    A tie is broken by a coin flip in the simulator, so an observed ordering is
    consistent with equality either way, and excluding it would eliminate the
    true value on every mirror-speed matchup -- which are common, because
    everyone invests in the same benchmarks.
    """
    return their_speed >= our_speed if theirs_is_larger else their_speed <= our_speed


def _damage_consistent(
    bounds: tuple[int, int],
    observed: float,
    tolerance: float,
    clamped: bool,
) -> bool:
    low, high = bounds
    if clamped:
        # The reported figure is the target's remaining HP, so the true damage
        # was at least that. Only the upper roll has to reach it.
        return high >= observed * (1.0 - tolerance)
    return high >= observed * (1.0 - tolerance) and low <= observed * (1.0 + tolerance)


#: Weather multipliers on the move's type, applied where `modifyDamage` applies
#: them. Snow and sand are absent here because in generation 9 they do not
#: multiply damage -- they raise a stat, which `_weathered_defense` handles.
_WEATHER_BOOST = {
    "sunnyday": {"fire": 1.5, "water": 0.5},
    "desolateland": {"fire": 1.5, "water": 0.0},
    "raindance": {"water": 1.5, "fire": 0.5},
    "primordialsea": {"water": 1.5, "fire": 0.0},
}

#: Weather that raises a defensive stat by half for one type. Snow raises
#: Defence for Ice types and sand raises Special Defence for Rock types, and
#: both are common enough in this format -- Vanilluxe's Snow Warning is on a
#: checked-in test team -- that leaving them out made the damage inference read
#: an Ice type's bulk as investment.
_WEATHER_DEFENSE = {
    "snowscape": ("ice", "Physical"),
    "snow": ("ice", "Physical"),
    "hail": ("ice", "Physical"),
    "sandstorm": ("rock", "Special"),
}


def _weather_modifiers(move: Mapping[str, Any], weather: str | None) -> tuple[float, ...]:
    if not weather:
        return ()
    table = _WEATHER_BOOST.get(weather)
    if not table:
        return ()
    multiplier = table.get(str(move.get("type") or "").lower())
    return (multiplier,) if multiplier is not None else ()


def _weathered_defense(
    defense: int,
    defender_types: Sequence[str],
    move: Mapping[str, Any],
    weather: str | None,
) -> int:
    if not weather:
        return defense
    entry = _WEATHER_DEFENSE.get(weather)
    if not entry:
        return defense
    boosted_type, category = entry
    if move.get("category") != category:
        return defense
    if boosted_type not in {t.lower() for t in defender_types}:
        return defense
    return modify(defense, 1.5)


def _effect_key(effects: effect_table.SetEffects) -> tuple:
    """Everything about a `SetEffects` that changes a damage number."""
    return (
        effects.base_power_modifiers,
        effects.attack_modifiers,
        effects.final_modifiers,
        effects.stab_override,
        effects.ignore_burn,
    )


def _ranked(distribution: Mapping[str, float], limit: int = 6) -> list[dict[str, Any]]:
    total = sum(distribution.values()) or 1.0
    ranked = sorted(distribution.items(), key=lambda kv: -kv[1])[:limit]
    return [{"value": key, "probability": round(value / total, 4)} for key, value in ranked]

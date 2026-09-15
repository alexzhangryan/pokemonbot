"""Candidate generation: reducing the joint action set to something solvable.

Roughly 156 joint actions a side means about 24,000 cells at one node, which is
2.5 times over the real clock budget before any belief sampling
(`docs/02-mechanics-deltas.md` section 7). Pruning to ten a side leaves 100
cells and about half a second. `docs/04-decision-engine.md` section 3 calls this
the highest leverage decision in the engine and says the right implementation is
an open empirical question, so it is an interface with more than one
implementation rather than a function.

This module has implementation A, the heuristic. B (a learned prior) and C (a
language model) arrive at M7 and are benchmarked against this one identically.

## Two implementations of A, and why both are here

`HeuristicPolicy` is the A section 3 specifies: any move that knocks out a
target on an average roll, Protect when the slot is threatened, speed control
when it flips an outspeed, Fake Out on turn 1, plus the switches. Every one of
those is a question about the *position*, so it reads the snapshot and computes
damage with the M1 layer.

`BasePowerPolicy` is the A that actually shipped through M6, which did none of
that: it ranked moves by base power and never looked at the position at all.
`docs/pruning-guard.md` measured what that cost -- at the agent's own `k` the
unpruned equilibrium put mass on a discarded row on 64.2% of positions -- and it
is kept for two reasons. It is the baseline the specified A has to beat, and the
1,500 self-play traces the guard reads were produced by it, so it is the only
policy whose re-derived candidate set can be expected to match what those traces
recorded.

## The opponent's candidate set is the harder half

Our own actions come from the request, which already encodes Choice locks,
Encore, disabled moves, target legality and Mega availability -- reimplementing
that is a pure liability, so `_legal_orders` reads it rather than deriving it.

The opponent's actions cannot be read from anywhere. Without a belief,
`opponent_candidates` enumerates what has been seen and nothing more, and on
turn one that is an empty set: the matrix degenerates to a single column and the
equilibrium to an argmax against an opponent modelled as doing nothing. That was
a real weakness and it was the honest one, because the alternative -- inventing
moves the opponent has not shown -- is guessing dressed as computation.

M5 supplies the missing distribution rather than the missing guess.
`opponent_candidates` now takes an optional `believed_moves`, which
`champions.belief.BattleBelief` fills from a posterior over whole registered
sets. Revealed moves still come first; the belief fills the remaining budget.
The no-belief path is unchanged, so the M2 agent's measured numbers still mean
what they said.

## The pruning guard

Pruning must never drop an action that is uniquely correct. Section 3 requires
measuring this offline by computing the unpruned equilibrium and recording how
often it puts non-trivial mass on a discarded action. `discard_rate` implements
that measurement; it is not called during play.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from champions.dex.damage import DamageContext, TypeChart, damage_roll_distribution
from champions.dex.loader import Dex
from champions.search.matrix import MASS_THRESHOLD, solve_both
from champions.search.payoff import (
    PARALYSIS_SPEED_FACTOR,
    SPREAD_TARGETS,
    Combatant,
    OpponentHypothesis,
    charge_waived,
    combatant,
    effective_speed,
    targets_of,
)

#: A score no candidate can reach, so a disqualified action sorts below every
#: real one without competing with them on magnitude.
DISQUALIFIED = float("-inf")

#: Default candidate count for our rows. Section 7's budget arithmetic is
#: written at ten; D69 found the union beats A at 15 on the guard, and the
#: live games (D85) found the payoff loop had two orders of magnitude of clock
#: to spare, so the row budget rose to twelve with the rest of D85.
DEFAULT_K = 12

#: The opponent's column budget, and how many moves each of their slots may
#: contribute before targets multiply them. Columns are cheaper than rows for
#: the equilibrium -- a missing column is a threat the search cannot see, a
#: missing row is only a reply it cannot make -- so the budget is larger.
DEFAULT_COLUMN_K = 24
DEFAULT_PER_SLOT = 6


class PolicyProvider(Protocol):
    """The candidate selection interface.

    `docs/08-implementation-blueprint.md` section 3 writes this as
    `candidates(state, belief, k)`, with enumeration as a separate interface
    (`actions.enumerate(request)`). Those two do not compose: a provider given
    only the state would have to enumerate the legal set itself, which means
    reimplementing Choice locks, Encore, disabled moves and target legality --
    the exact liability section 1 says to avoid by reading the request.

    So the enumerated legal set is passed in, and this interface selects from it
    rather than producing it. `state` and `belief` stay because every
    implementation needs one or both: section 3's heuristic asks four questions
    about the position, a learned prior scores actions in context, and the
    language model provider is shown the board. Both default, because a caller
    that has neither -- a trace written before the snapshot existed, a test --
    should get the degraded ordering rather than an exception.
    """

    #: The name the trace and the pruning guard report the provider under.
    name: str

    def candidates(
        self,
        actions: list[dict[str, Any]],
        state: dict[str, Any] | None,
        belief: Any,
        k: int,
    ) -> list[dict[str, Any]]: ...  # pragma: no cover

    def scored(
        self,
        actions: list[dict[str, Any]],
        k: int = ...,
        state: dict[str, Any] | None = None,
    ) -> list[ScoredAction]: ...  # pragma: no cover


@dataclass(frozen=True)
class ScoredAction:
    """One candidate with the score that got it selected.

    The score is emitted onto the trace's `candidates` event so a reader can see
    not just what survived pruning but why, which is the difference between a
    debuggable decision and a list.
    """

    action: dict[str, Any]
    score: float
    reasons: tuple[str, ...]


class BasePowerPolicy:
    """Implementation A as it shipped through M6: base power, and nothing else.

    Kept, not deleted. `docs/pruning-guard.md` measured this policy over 11,774
    real positions and every number in that file describes it, so removing it
    would leave the only measured baseline in the project unreproducible. It is
    also the policy the 1,500 self-play traces were produced by, which makes it
    the only one whose re-derived candidate set can be expected to agree with
    what those traces recorded -- the guard's own validity check.

    It scores a joint action by summing per-slot scores, which ignores
    interactions between the two slots -- a double Protect and a Protect plus an
    attack score the same way -- and that is the known cost of being cheap. The
    payoff estimator sees the interaction; this only decides what the estimator
    looks at.
    """

    #: What the trace records as having produced the candidate set. Traces
    #: written before there was more than one implementation A say "heuristic",
    #: and this is the policy they were written by.
    name = "heuristic-base-power"

    def __init__(self, dex: Dex) -> None:
        self._dex = dex

    def candidates(
        self,
        actions: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
        belief: Any = None,
        k: int = DEFAULT_K,
    ) -> list[dict[str, Any]]:
        return [scored.action for scored in self.scored(actions, k)]

    def scored(
        self,
        actions: list[dict[str, Any]],
        k: int = DEFAULT_K,
        state: dict[str, Any] | None = None,
    ) -> list[ScoredAction]:
        """The top `k` joint actions by heuristic score, best first.

        Ties break on the action's protocol string, which is unique and stable,
        so the same position always produces the same candidate set in the same
        order (`CLAUDE.md`: deterministic by default).

        `state` is accepted and ignored, so that a benchmark can hold one call
        shape across every provider.
        """
        ranked = sorted(
            (self._score(action) for action in actions),
            key=lambda scored: (-scored.score, scored.action["message"]),
        )
        return ranked[:k]

    def slot_scores(
        self,
        options: Sequence[dict[str, Any]],
        slot_index: int = 0,
        state: dict[str, Any] | None = None,
    ) -> list[float]:
        """One score per option of a single slot.

        Public because the corpus benchmark scores per slot, not per joint
        action: a replay's label is one slot's choice, so the only comparison
        against a learned prior that is like for like is at that granularity.
        The composition is unchanged -- a joint action is still the sum of these
        -- so this exposes the existing quantity rather than adding one.
        """
        return [self._score_slot(option)[0] for option in options]

    def _score(self, action: dict[str, Any]) -> ScoredAction:
        total = 0.0
        reasons: list[str] = []
        for slot in action.get("slots", []):
            score, reason = self._score_slot(slot)
            if score == DISQUALIFIED:
                return ScoredAction(action=action, score=DISQUALIFIED, reasons=(reason,))
            total += score
            if reason:
                reasons.append(reason)
        return ScoredAction(action=action, score=total, reasons=tuple(reasons))

    def _score_slot(self, slot: dict[str, Any]) -> tuple[float, str]:
        kind = slot.get("kind")

        if kind == "switch":
            # Kept in the candidate set but ranked low: the one ply model scores
            # a switch as a lost turn (see `payoff._switch`), so a switch that
            # only pays off next turn cannot be seen from here. Ranking them out
            # entirely would make the agent unable to switch at all.
            return SWITCH, "switch"

        if kind != "move":
            return 0.0, ""

        move_id = slot.get("move") or ""
        entry = self._dex.moves.get(move_id)
        if not entry:
            return 0.0, ""

        if entry["category"] == "Status":
            if move_id in PROTECT_LIKE:
                return 2.0, "protect"
            if move_id in SPEED_CONTROL:
                return 2.5, "speed control"
            if move_id in SETUP:
                return 1.5, "setup"
            return STATUS, "status"

        # A damaging move aimed at one of our own slots. Disqualified rather
        # than penalised, for the same reason `MaxBasePowerAgent` disqualifies
        # it: there is no base-power argument for ever doing this, and scoring
        # it on power alone makes it tie with the same move aimed at a foe.
        #
        # This matters far more here than it did there. An argmax loses one
        # action to the tie; pruning loses most of the candidate set, because
        # each move appears once per legal target and the ally-aimed copies
        # score identically. Measured on a real turn before this guard: nine of
        # the ten survivors were friendly fire, and the equilibrium was solving
        # a matrix almost entirely made of actions no one would ever play.
        power = float(entry.get("basePower") or 0)
        if power > 0 and int(slot.get("target", 0) or 0) < 0:
            return DISQUALIFIED, "friendly fire"

        # Base power is a poor proxy for damage and a good proxy for intent. The
        # payoff estimator computes the real number; this only has to get the
        # candidate into the matrix.
        score = ATTACK_BASE + power / 60.0
        if slot.get("priority", 0) > 0:
            score += PRIORITY_BONUS
        return score, "attack"


class HeuristicPolicy:
    """Implementation A as `docs/04-decision-engine.md` section 3 specifies it.

    Section 3 names four things and every one of them is a question about the
    position rather than about the move: does this knock a target out on an
    average roll, is this slot threatened, does this speed control flip an
    outspeed, is this the turn Fake Out works. So this reads the snapshot and
    computes damage with the M1 layer, where `BasePowerPolicy` read nothing.

    ## What it costs

    One damage roll distribution per (our slot, move, target) and per (their
    active, revealed move, our slot), memoised for the position. That is tens of
    distributions, not thousands: the joint action set repeats the same per-slot
    moves across its rows, and the cache is keyed per slot rather than per joint
    action. M2 measured the whole decision at about 11 ms against a 45 second
    budget, so this is not where the clock goes.

    ## What it still does not see

    The same two things `BasePowerPolicy` did not. It scores a joint action by
    summing per-slot scores, so a double Protect and a Protect plus an attack
    score the same way; the payoff estimator sees that interaction and this only
    decides what the estimator looks at. And the threat model is the opponent's
    *revealed* moves, so an unrevealed one cannot make a slot look threatened --
    the same honest gap `opponent_candidates` has, with the same answer
    available (the belief filter's posterior) and not yet plumbed through here.
    """

    name = "heuristic-position"

    def __init__(self, dex: Dex) -> None:
        self._dex = dex
        self._chart = TypeChart.from_dex(dex)
        self._hypothesis = OpponentHypothesis()

    def candidates(
        self,
        actions: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
        belief: Any = None,
        k: int = DEFAULT_K,
    ) -> list[dict[str, Any]]:
        return [scored.action for scored in self.scored(actions, k, state)]

    def scored(
        self,
        actions: list[dict[str, Any]],
        k: int = DEFAULT_K,
        state: dict[str, Any] | None = None,
    ) -> list[ScoredAction]:
        """The top `k` joint actions by heuristic score, best first.

        Ties break on the action's protocol string, which is unique and stable,
        so the same position always produces the same candidate set in the same
        order (`CLAUDE.md`: deterministic by default).
        """
        position = Board.read(state, self._dex, self._chart, self._hypothesis)
        ranked = sorted(
            (self._score(action, position) for action in actions),
            key=lambda scored: (-scored.score, scored.action["message"]),
        )
        return diversify(ranked, k)

    def slot_scores(
        self,
        options: Sequence[dict[str, Any]],
        slot_index: int = 0,
        state: dict[str, Any] | None = None,
    ) -> list[float]:
        """One score per option of a single slot. See `BasePowerPolicy.slot_scores`.

        The board is read once for the whole option list, which is the same
        amortisation `scored` relies on: the damage cache is per (slot, move,
        target), and rebuilding it per option would compute every roll
        distribution once per row instead of once per position.
        """
        position = Board.read(state, self._dex, self._chart, self._hypothesis)
        return [self._score_slot(option, slot_index, position)[0] for option in options]

    def _score(self, action: dict[str, Any], position: Board) -> ScoredAction:
        total = 0.0
        reasons: list[str] = []
        for index, slot in enumerate(action.get("slots", [])):
            score, reason = self._score_slot(slot, index, position)
            if score == DISQUALIFIED:
                return ScoredAction(action=action, score=DISQUALIFIED, reasons=(reason,))
            total += score
            if reason:
                reasons.append(reason)
        return ScoredAction(action=action, score=total, reasons=tuple(reasons))

    def _score_slot(self, slot: dict[str, Any], index: int, position: Board) -> tuple[float, str]:
        kind = slot.get("kind")

        if kind == "switch":
            # Kept in the candidate set but ranked low: the one ply model scores
            # a switch as a lost turn (see `payoff._switch`), so a switch that
            # only pays off next turn cannot be seen from here. Ranking them out
            # entirely would make the agent unable to switch at all. Section 3
            # says "plus the switches" and means it -- unconditionally, because
            # the reason to switch is the reason this model cannot see.
            return SWITCH, "switch"

        if kind != "move":
            return 0.0, ""

        entry = self._dex.moves.get(slot.get("move") or "")
        if not entry:
            return 0.0, ""

        if entry["category"] == "Status":
            if _misaimed_status(entry, slot):
                return DISQUALIFIED, "status at an ally"
            return self._status(entry, slot, index, position)
        return self._attack(entry, slot, index, position)

    # -- damaging moves --------------------------------------------------

    def _attack(
        self,
        entry: dict[str, Any],
        slot: dict[str, Any],
        index: int,
        position: Board,
    ) -> tuple[float, str]:
        power = float(entry.get("basePower") or 0)

        # A damaging move aimed at one of our own slots. Disqualified rather
        # than penalised, for the same reason `MaxBasePowerAgent` disqualifies
        # it: there is no argument for ever doing this on purpose, and it is not
        # the same thing as a spread move that happens to catch our partner.
        #
        # An argmax loses one action to the tie; pruning loses most of the
        # candidate set, because each move appears once per legal target and the
        # ally-aimed copies score identically. Measured on a real turn before
        # this guard: nine of the ten survivors were friendly fire.
        if (
            power > 0
            and int(slot.get("target", 0) or 0) < 0
            and entry.get("target") not in SPREAD_TARGETS
        ):
            return DISQUALIFIED, "friendly fire"

        if (
            not position.empty
            and (entry.get("flags") or {}).get("charge")
            and not position.charged(index)
            and not charge_waived(entry, position.snapshot.get("weather") or {})
        ):
            # A two-turn move on its charge turn is a set-up move, not an
            # attack: Electro Shot outside rain deals nothing this turn.
            return SETUP_SAFE, "charge"

        if entry["id"] == "fakeout" and not position.empty:
            # Fake Out works only on the turn its user came in. Off that turn it
            # is not a weak attack, it is a guaranteed failure, and base power
            # cannot say the difference.
            if not position.first_turn(index):
                return FAKE_OUT_UNAVAILABLE, "fake out unavailable"
            return self._damage_score(entry, slot, index, position) + FAKE_OUT, "fake out"

        if position.empty:
            # Nothing can be known to knock anything out without a position, so
            # this is `BasePowerPolicy`'s ordering rather than an exception:
            # base power is a poor proxy for damage and a good proxy for intent.
            score = ATTACK_BASE + power / 60.0
            if slot.get("priority", 0) > 0:
                score += PRIORITY_BONUS
            return score, "attack"

        score = self._damage_score(entry, slot, index, position)
        reason = "knockout" if self._knocks_out(entry, slot, index, position) else "attack"

        # Icy Wind and Electroweb are the speed control doubles actually plays,
        # and both are Special rather than Status. Scored purely as attacks, the
        # one conditional section 3 asks for would never fire on the moves it
        # most obviously means.
        if entry["id"] in SPEED_CONTROL and position.speed_control_flips(
            entry["id"], index, slot, entry
        ):
            score += SPEED_CONTROL_BONUS
            reason = "speed control"
        return score, reason

    def _damage_score(
        self,
        entry: dict[str, Any],
        slot: dict[str, Any],
        index: int,
        position: Board,
    ) -> float:
        """`ATTACK_BASE`, plus what the average roll does, minus what it costs us.

        Damage enters as a *fraction of the target's remaining HP* rather than
        as a raw number, so that a move finishing a Pokemon off outranks a
        bigger number spent on a healthy one. A knockout is a step on top of
        that, because the difference between 99% and 100% of a target's HP is
        the whole value of the turn and no continuous function of damage says
        so.

        Our own slots enter the same sum negatively and more steeply. Earthquake
        aimed at a foe is not friendly fire and is not disqualified above, but a
        partner it kills is a real cost the base-power ranking could not see.
        """
        score = ATTACK_BASE
        for side, target_slot in position.targets(index, slot, entry):
            fraction = position.damage_fraction(entry, index, side, target_slot)
            if side == "ours":
                score -= PARTNER_DAMAGE * fraction
            else:
                score += DAMAGE * fraction
                if fraction >= 1.0:
                    score += KNOCKOUT
        if slot.get("priority", 0) > 0:
            # Priority is what turns an average-roll knockout into a real one.
            score += PRIORITY_BONUS
        return score

    def _knocks_out(
        self,
        entry: dict[str, Any],
        slot: dict[str, Any],
        index: int,
        position: Board,
    ) -> bool:
        return any(
            position.damage_fraction(entry, index, side, target_slot) >= 1.0
            for side, target_slot in position.targets(index, slot, entry)
            if side == "theirs"
        )

    # -- status moves ----------------------------------------------------

    def _status(
        self,
        entry: dict[str, Any],
        slot: dict[str, Any],
        index: int,
        position: Board,
    ) -> tuple[float, str]:
        move_id = entry["id"]

        if move_id in PROTECT_LIKE:
            if position.empty:
                return PROTECT_THREATENED, "protect"
            if position.protect_counter(index):
                # Consecutive Protects fail with rising probability, so a threat
                # is not on its own a reason to press it twice.
                return PROTECT_REPEATED, "protect repeated"
            if position.threatened(index):
                return PROTECT_THREATENED, "protect"
            return PROTECT_IDLE, "protect idle"

        if move_id in SPEED_CONTROL:
            if position.empty:
                return SPEED_CONTROL_FLIPS, "speed control"
            if position.speed_control_flips(move_id, index, slot, entry):
                return SPEED_CONTROL_FLIPS, "speed control"
            return SPEED_CONTROL_IDLE, "speed control idle"

        if move_id in SETUP:
            if not position.empty and position.threatened(index):
                # Setting up in front of something that kills the setter is the
                # move this heuristic exists to rank below Protect.
                return SETUP_THREATENED, "setup into a threat"
            return SETUP_SAFE, "setup"

        return STATUS, "status"


@dataclass
class Board:
    """One position, with the damage the heuristic keeps asking it about.

    Built once per `scored` call and thrown away. It exists because every
    question section 3 asks -- does this kill, is this slot threatened, does
    this flip a race -- is asked once per *joint action*, and answered per
    *slot*: the joint set repeats the same handful of per-slot moves across its
    rows, so the same roll distribution would otherwise be computed a hundred
    times a turn.

    `empty` is the no-snapshot case. `discard.KeepFn` and the M7 benchmark hand
    a provider positions one at a time and a trace written before the snapshot
    existed has none, so the state-free path is the old base-power ordering
    rather than an exception.

    Shared rather than private, because `champions.search.policy_features` asks
    the same questions of the same position and a second implementation of them
    is how a model quietly stops being served the inputs it was fit on. It is
    named for what it is -- the board -- rather than for the policy that first
    needed it.

    `exact_stats` is the one thing the two callers differ on. During play our own
    side carries a real spread; a replay carries a percentage and nothing else
    (`champions.corpus.replay_state`). `HeuristicPolicy` uses the real spread and
    keeps the numbers `docs/pruning-guard.md` reports. The learned provider sets
    this False so that a position produces the same vector whether it was
    reconstructed from a log or observed in a live battle -- the model is fit on
    the hypothesis and must therefore be served the hypothesis.
    """

    dex: Dex
    chart: TypeChart
    hypothesis: OpponentHypothesis
    snapshot: dict[str, Any]
    ours: list[dict[str, Any] | None]
    theirs: list[dict[str, Any] | None]
    empty: bool
    exact_stats: bool = True
    _units: dict[tuple[str, int], Combatant | None] = field(default_factory=dict)
    _damage: dict[tuple[str, int, str, str, int], float] = field(default_factory=dict)
    _threatened: dict[int, bool] = field(default_factory=dict)

    @classmethod
    def read(
        cls,
        state: dict[str, Any] | None,
        dex: Dex,
        chart: TypeChart,
        hypothesis: OpponentHypothesis,
        exact_stats: bool = True,
    ) -> Board:
        if not state or "ours" not in state or "theirs" not in state:
            return cls(dex, chart, hypothesis, {}, [], [], empty=True, exact_stats=exact_stats)
        return cls(
            dex,
            chart,
            hypothesis,
            state,
            list(state["ours"]["active"]),
            list(state["theirs"]["active"]),
            empty=False,
            exact_stats=exact_stats,
        )

    # -- what is on the field --------------------------------------------

    def view(self, side: str, index: int) -> dict[str, Any] | None:
        active = self.ours if side == "ours" else self.theirs
        if index < 0 or index >= len(active):
            return None
        view = active[index]
        return None if view is None or view.get("fainted") else view

    def unit(self, side: str, index: int) -> Combatant | None:
        key = (side, index)
        if key not in self._units:
            view = self.view(side, index)
            self._units[key] = None if view is None else self.combatant(view)
        return self._units[key]

    def combatant(self, view: dict[str, Any]) -> Combatant:
        """One Pokemon as the damage layer wants it.

        With `exact_stats` False the exact spread is dropped before `combatant`
        sees it, which sends our own side down the same hypothesis path the
        opponent already takes. That is a deliberate loss of information at play
        time: it is the only way the vector a model is served matches the vector
        it was fit on, since a replay never contained the spread.
        """
        if not self.exact_stats and view.get("stats"):
            view = {**view, "stats": None, "hp": None, "max_hp": None}
        return combatant(view, self.hypothesis)

    def first_turn(self, index: int) -> bool:
        """Whether this slot came in this turn, which is when Fake Out works.

        `first_turn` reached the snapshot with this policy; the 1,500 traces the
        pruning guard reads predate it. Turn 1 is when it is true for everything
        on the field, so the fallback keeps those traces measurable rather than
        scoring them as if Fake Out never worked at all.
        """
        view = self.view("ours", index)
        if view is not None and "first_turn" in view:
            return bool(view["first_turn"])
        return int(self.snapshot.get("turn") or 0) == 1

    def charged(self, index: int) -> bool:
        """Whether this slot is mid-way through a two-turn move."""
        view = self.view("ours", index)
        return bool(view is not None and view.get("preparing"))

    def protect_counter(self, index: int) -> int:
        view = self.view("ours", index)
        return int((view or {}).get("protect_counter") or 0)

    # -- damage ----------------------------------------------------------

    def targets(
        self, index: int, slot: dict[str, Any], entry: dict[str, Any]
    ) -> list[tuple[str, int]]:
        if self.empty:
            return []
        return targets_of(
            {"ours": self.ours, "theirs": self.theirs},
            side="ours",
            slot=index,
            described_target=int(slot.get("target", 0) or 0),
            move_target=str(entry.get("target") or "normal"),
        )

    def damage_fraction(
        self,
        entry: dict[str, Any],
        index: int,
        side: str,
        target_slot: int,
        attacker_side: str = "ours",
    ) -> float:
        """Average-roll damage as a fraction of the target's *remaining* HP.

        Capped at 1.0, so a move that overkills by a factor of three does not
        outrank one that also kills but has somewhere else to be. Section 3 says
        "knocks out a target on an average roll" and the average of the sixteen
        rolls is what that reads as -- exactly, not sampled, since sixteen rolls
        are cheap to enumerate and sampling would add variance to a quantity
        that has none.
        """
        key = (attacker_side, index, entry["id"], side, target_slot)
        if key in self._damage:
            return self._damage[key]

        attacker = self.unit(attacker_side, index)
        target = self.unit(side, target_slot)
        fraction = 0.0
        if attacker is not None and target is not None and target.hp > 0:
            fraction = min(1.0, self._average_damage(entry, attacker, target) / target.hp)
        self._damage[key] = fraction
        return fraction

    def _average_damage(
        self, entry: dict[str, Any], attacker: Combatant, target: Combatant
    ) -> float:
        move_type = str(entry["type"])
        if self.chart.is_immune(move_type, list(target.types)):
            return 0.0
        physical = entry["category"] == "Physical"
        rolls = damage_roll_distribution(
            DamageContext(
                base_power=int(entry.get("basePower") or 0),
                attack=attacker.stat("atk" if physical else "spa"),
                defense=target.stat("def" if physical else "spd"),
                move_type=move_type,
                attacker_types=list(attacker.types),
                defender_types=list(target.types),
                level=self.dex.level,
                is_spread=str(entry.get("target")) in SPREAD_TARGETS,
                attacker_burned=attacker.status == "BRN",
            ),
            self.chart,
        )
        return sum(rolls) / len(rolls)

    # -- threat ----------------------------------------------------------

    def threatened(self, index: int) -> bool:
        """Whether a revealed or believed opponent move takes a serious bite
        out of this slot. See `_revealed` for where the believed ones come
        from; without a belief this is revealed only."""
        if index in self._threatened:
            return self._threatened[index]

        ours = self.unit("ours", index)
        threat = False
        if ours is not None and ours.hp > 0:
            for slot_index in range(len(self.theirs)):
                if self.unit("theirs", slot_index) is None:
                    continue
                for entry in self._revealed(slot_index):
                    damage = self.damage_fraction(
                        entry, slot_index, "ours", index, attacker_side="theirs"
                    )
                    if damage >= THREAT_FRACTION:
                        threat = True
                        break
                if threat:
                    break
        self._threatened[index] = threat
        return threat

    def _revealed(self, slot_index: int) -> list[dict[str, Any]]:
        """The opponent's damaging moves this slot is known or believed to have.

        Revealed moves first. `believed_moves` on the view is what the belief
        agent writes onto the search snapshot (`OnePlyAgent._search`), so an
        unrevealed move the posterior puts mass on can make a slot look
        threatened -- the gap the docstring above used to name.
        """
        view = self.view("theirs", slot_index) or {}
        entries = []
        seen: set[str] = set()
        ids = [m.get("id") or "" for m in view.get("revealed_moves") or []]
        ids += [str(m) for m in view.get("believed_moves") or []]
        for move_id in ids:
            if move_id in seen:
                continue
            seen.add(move_id)
            entry = self.dex.moves.get(move_id)
            if entry and entry["category"] != "Status":
                entries.append(entry)
        return entries

    # -- speed -----------------------------------------------------------

    def speed_control_flips(
        self,
        move_id: str,
        index: int,
        slot: dict[str, Any],
        entry: dict[str, Any],
    ) -> bool:
        """Whether this move wins a speed race we are currently losing.

        Section 3's condition, taken literally. Trick Room reverses the
        comparison rather than changing a speed, which is the same question
        asked of a different transform -- and asking it that way also makes
        pressing Trick Room while Trick Room is up correctly worthless.
        """
        if move_id == "tailwind" and "TAILWIND" in (self.snapshot.get("side_conditions") or {}):
            return False

        trick_room = "TRICK_ROOM" in (self.snapshot.get("fields") or {})
        aimed = {
            target for side, target in self.targets(index, slot, entry) if side == "theirs"
        } or set(range(len(self.theirs)))

        for our_slot in range(len(self.ours)):
            ours = self.unit("ours", our_slot)
            if ours is None:
                continue
            our_speed = effective_speed(ours, self.snapshot, ours=True)
            for their_slot in range(len(self.theirs)):
                theirs = self.unit("theirs", their_slot)
                if theirs is None:
                    continue
                their_speed = effective_speed(theirs, self.snapshot, ours=False)
                if _wins(our_speed, their_speed, trick_room):
                    continue
                after_us, after_them, after_room = _speed_control(
                    move_id,
                    our_speed,
                    their_speed,
                    trick_room,
                    applies_to_target=their_slot in aimed,
                    target_immune=self.chart.is_immune("Electric", list(theirs.types))
                    or theirs.status is not None,
                )
                if _wins(after_us, after_them, after_room):
                    return True
        return False


def _wins(our_speed: float, their_speed: float, trick_room: bool) -> bool:
    """Whether we move first, which Trick Room reverses rather than negates."""
    return our_speed < their_speed if trick_room else our_speed > their_speed


def _speed_control(
    move_id: str,
    our_speed: float,
    their_speed: float,
    trick_room: bool,
    applies_to_target: bool,
    target_immune: bool,
) -> tuple[float, float, bool]:
    """The speed picture after the move resolves, for one of our/their pairs.

    Approximate on purpose in one place: a Speed drop is applied as its
    multiplier rather than through `boosted`, which differs only by the integer
    truncation and so can only change the answer on an exact tie. Everything the
    condition turns on is a strict inequality.
    """
    if move_id == "trickroom":
        return our_speed, their_speed, not trick_room
    if move_id == "tailwind":
        return our_speed * TAILWIND_FACTOR, their_speed, trick_room
    if move_id == "thunderwave":
        if not applies_to_target or target_immune:
            return our_speed, their_speed, trick_room
        return our_speed, their_speed * PARALYSIS_SPEED_FACTOR, trick_room
    # Icy Wind and Electroweb, which lower the Speed of every foe by one stage.
    return our_speed, their_speed * SPEED_DROP_FACTOR, trick_room


#: The score scale. Every number here is a ranking weight rather than a
#: quantity: the payoff estimator computes what a candidate is actually worth,
#: and these only decide which candidates it is asked about. They are stated as
#: constants so that `docs/pruning-guard.md` compares two policies rather than
#: two piles of literals.
SWITCH = 0.5
ATTACK_BASE = 2.0
STATUS = 1.0

#: What the average roll removing all of a target's remaining HP is worth, and
#: the step on top of it for the knockout itself. The step is large because the
#: difference between 99% and 100% of a target's HP is the whole value of the
#: turn, and no continuous function of damage says so.
DAMAGE = 2.0
KNOCKOUT = 4.0

#: The same fraction taken off one of our own slots, weighted more heavily than
#: the gain. A spread move that kills our partner to chip two foes is a trade
#: this heuristic should refuse to put in front of the estimator.
PARTNER_DAMAGE = 3.0

#: Priority is what turns an average-roll knockout into a real one.
PRIORITY_BONUS = 0.4

PROTECT_THREATENED = 3.5
PROTECT_IDLE = 0.5
PROTECT_REPEATED = 0.25

SPEED_CONTROL_FLIPS = 3.0
SPEED_CONTROL_IDLE = 0.75

#: What a flipped race is worth on top of a *damaging* speed control move's own
#: damage. The same swing the status ones get between flipping and not, so that
#: flipping a race is worth the same wherever it happens.
SPEED_CONTROL_BONUS = SPEED_CONTROL_FLIPS - SPEED_CONTROL_IDLE

#: On top of Fake Out's own damage, for the flinch. Off the turn it works it is
#: a guaranteed failure and ranks below every real action.
FAKE_OUT = 2.5
FAKE_OUT_UNAVAILABLE = 0.1

SETUP_SAFE = 1.5
SETUP_THREATENED = 0.5

#: Redirection and Helping Hand from the opponent's side: the support moves
#: a doubles turn is played around, ranked with Protect for the column budget.
SUPPORT = 3.0
#: A move that inflicts a major status.
STATUS_THREAT = 2.0

#: How much of our remaining HP a revealed opponent move has to threaten before
#: Protect counts as answering something. Half, because Protect trades this
#: turn's action for it and a quarter is not worth a turn.
THREAT_FRACTION = 0.5

#: Tailwind doubles Speed; a one-stage drop multiplies it by two thirds.
TAILWIND_FACTOR = 2.0
SPEED_DROP_FACTOR = 2.0 / 3.0


#: Ranked above generic status moves because they are the interactions the
#: equilibrium exists to solve rather than value plays.
PROTECT_LIKE = {"protect", "detect", "spikyshield", "banefulbunker", "burningbulwark"}
SPEED_CONTROL = {"tailwind", "trickroom", "icywind", "electroweb", "thunderwave"}
SETUP = {"swordsdance", "nastyplot", "dragondance", "bulkup", "calmmind", "irondefense"}


def opponent_candidates(
    snapshot: dict[str, Any],
    dex: Dex,
    k: int = DEFAULT_COLUMN_K,
    believed_moves: Callable[[str], list[str]] | None = None,
    per_slot: int = DEFAULT_PER_SLOT,
) -> list[dict[str, Any]]:
    """Joint actions the opponent might take, most threatening first.

    Built in the same described-action shape our own candidates use, so the
    payoff model does not need two code paths.

    Each active foe contributes its revealed moves, then the moves it is
    believed to have -- from `believed_moves`, or from a `believed_moves` list
    the agent has written onto the view -- up to `per_slot` moves. A
    single-target move becomes one option per living slot of ours it could be
    aimed at, and an ally-targeted one is aimed at the partner; until D85 every
    opponent move was aimed at our first slot, so the equilibrium never saw
    our second one attacked.

    The joint options are ranked by a threat score (damage to us as a fraction
    of remaining HP, knockouts, and the status moves doubles is played around:
    Protect, Fake Out on its turn, Trick Room, Tailwind, redirection, Helping
    Hand, set-up) and the top `k` are the columns. Deterministic: ties break
    on the label.

    With no belief and nothing revealed this is a single "no action" column,
    which makes turn one an argmax against an opponent modelled as doing
    nothing -- the weakness the module docstring names and the belief answers.
    """
    active = snapshot["theirs"]["active"]
    our_active = snapshot["ours"]["active"]
    our_slots = [i for i, p in enumerate(our_active) if p is not None and not p.get("fainted")]
    board = Board.read(snapshot, dex, _chart_for(dex), OpponentHypothesis())
    per_slot_options: list[list[tuple[float, dict[str, Any]]]] = []

    for slot_index, pokemon in enumerate(active):
        if slot_index >= 2:
            break
        if pokemon is None or pokemon.get("fainted"):
            continue
        move_ids = [m.get("id") or "" for m in pokemon.get("revealed_moves", [])]
        seen = set(move_ids)
        extra: list[str] = []
        if believed_moves is not None:
            extra = list(believed_moves(pokemon.get("species") or ""))
        elif pokemon.get("believed_moves"):
            extra = [str(m) for m in pokemon["believed_moves"]]
        for move_id in extra:
            if move_id not in seen:
                seen.add(move_id)
                move_ids.append(move_id)

        options: list[tuple[float, dict[str, Any]]] = []
        for move_id in move_ids[:per_slot]:
            entry = dex.moves.get(move_id)
            if not entry:
                continue
            for target in _opponent_targets(entry, slot_index, active, our_slots):
                option = {
                    "kind": "move",
                    "move": entry["id"],
                    "name": entry["name"],
                    "type": entry["type"],
                    "category": entry["category"],
                    "base_power": entry["basePower"],
                    "priority": entry.get("priority", 0),
                    # Their moves are aimed at our slots; the model reads a
                    # positive target as "the other side", which from their
                    # point of view is us, and a negative one as their own.
                    "target": target,
                    "label": _opponent_label(entry, target),
                }
                score = _threat(entry, slot_index, target, pokemon, board, snapshot)
                options.append((score, option))
        if not options:
            options = [(0.0, {"kind": "none", "label": "unrevealed"})]
        per_slot_options.append(options)

    if not per_slot_options:
        return [_joint([])]

    first = per_slot_options[0]
    second: list[tuple[float, dict[str, Any] | None]] = (
        list(per_slot_options[1]) if len(per_slot_options) > 1 else [(0.0, None)]
    )
    joint: list[tuple[float, dict[str, Any]]] = []
    for score_a, a in first:
        for score_b, b in second:
            joint.append((score_a + score_b, _joint([s for s in (a, b) if s is not None])))
    joint.sort(key=lambda pair: (-pair[0], pair[1]["label"]))
    return [action for _, action in joint[:k]]


def _opponent_targets(
    entry: dict[str, Any], slot_index: int, active: list[Any], our_slots: list[int]
) -> list[int]:
    """The target choices one opponent move has, in our described encoding."""
    target = str(entry.get("target") or "normal")
    if target in ("normal", "any", "adjacentFoe", "randomNormal"):
        return [i + 1 for i in our_slots] or [1]
    if target == "adjacentAlly":
        partner = [
            i
            for i, p in enumerate(active)
            if i != slot_index and p is not None and not p.get("fainted")
        ]
        return [-(partner[0] + 1)] if partner else []
    return [0]


def _opponent_label(entry: dict[str, Any], target: int) -> str:
    if target > 0:
        return f"{entry['name']} -> our slot {target}"
    if target < 0:
        return f"{entry['name']} -> ally"
    return str(entry["name"])


def _threat(
    entry: dict[str, Any],
    slot_index: int,
    target: int,
    pokemon: dict[str, Any],
    board: Board,
    snapshot: dict[str, Any],
) -> float:
    """How much this option is worth putting in front of the equilibrium.

    Same scale as `HeuristicPolicy`, read from their side: damage as a
    fraction of our remaining HP, the knockout step, and the status moves
    that decide doubles turns. Not a value -- the payoff model computes that
    -- only a ranking for which columns fit the budget.
    """
    move_id = str(entry["id"])
    if entry["category"] == "Status":
        if move_id in PROTECT_LIKE:
            return PROTECT_THREATENED if not pokemon.get("protect_counter") else PROTECT_REPEATED
        if move_id == "trickroom":
            up = "TRICK_ROOM" in (snapshot.get("fields") or {})
            return 0.2 if up else SPEED_CONTROL_FLIPS
        if move_id == "tailwind":
            up = "TAILWIND" in (snapshot.get("opponent_side_conditions") or {})
            return SPEED_CONTROL_IDLE if up else SPEED_CONTROL_FLIPS
        if move_id in ("followme", "ragepowder", "helpinghand"):
            return SUPPORT
        if move_id in SETUP or entry.get("boosts"):
            return SETUP_SAFE
        if entry.get("status"):
            return STATUS_THREAT
        return STATUS
    score = ATTACK_BASE
    if move_id == "fakeout":
        if not pokemon.get("first_turn", True):
            return FAKE_OUT_UNAVAILABLE
        score += FAKE_OUT
    targets = targets_of(
        {"ours": board.ours, "theirs": board.theirs},
        side="theirs",
        slot=slot_index,
        described_target=target,
        move_target=str(entry.get("target") or "normal"),
    )
    for side, hit in targets:
        if side != "ours":
            continue
        fraction = board.damage_fraction(entry, slot_index, "ours", hit, attacker_side="theirs")
        score += DAMAGE * fraction
        if fraction >= 1.0:
            score += KNOCKOUT
    if int(entry.get("priority", 0) or 0) > 0:
        score += PRIORITY_BONUS
    return score


#: Target strings of status moves that are meant for an ally.
ALLY_STATUS_TARGETS = frozenset({"adjacentAlly", "adjacentAllyOrSelf", "allies", "allySide"})


def _misaimed_status(entry: dict[str, Any], slot: dict[str, Any]) -> bool:
    """A status move aimed at our own partner that is not for allies: Encore,
    Will-O-Wisp, Thunder Wave or Taunt at the Pokemon beside us. The request
    lists the target as legal, and the smoke games with the Perish team spent
    a turn on Encore into Archaludon (D87). `allyanim` is the dex's own mark
    for a move with an ally-targeted use (Heal Pulse, Pollen Puff, Decorate)."""
    if int(slot.get("target", 0) or 0) >= 0:
        return False
    target = str(entry.get("target") or "normal")
    if target in ALLY_STATUS_TARGETS:
        return False
    return not (entry.get("flags") or {}).get("allyanim")


def diversify(ranked: list[ScoredAction], k: int) -> list[ScoredAction]:
    """The top `k` of a ranking, with no one (slot, move) taking more than half.

    On turn one the Fake Out bonus put Fake Out in one slot of every row the
    budget held -- eight of eight at preview, most of twelve live (D87) -- so
    the equilibrium could not consider a turn without it. A row is skipped
    when a slot's move already fills `max(2, k // 2)` kept rows; the skipped
    rows fill the tail if nothing else does. Switches and passes are not
    capped, since two switches to different Pokemon are different moves.
    """
    cap = max(2, k // 2)
    kept: list[ScoredAction] = []
    skipped: list[ScoredAction] = []
    counts: dict[tuple[int, str], int] = {}
    for scored in ranked:
        if len(kept) >= k:
            break
        keys = [
            (index, str(slot.get("move")))
            for index, slot in enumerate(scored.action.get("slots", []))
            if slot.get("kind") == "move"
        ]
        if any(counts.get(key, 0) >= cap for key in keys):
            skipped.append(scored)
            continue
        kept.append(scored)
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
    if len(kept) < k:
        kept.extend(skipped[: k - len(kept)])
    return kept


_CHARTS: dict[str, TypeChart] = {}


def _chart_for(dex: Dex) -> TypeChart:
    """One type chart per format, since building one reads the whole dex."""
    key = str(getattr(dex, "format_id", id(dex)))
    if key not in _CHARTS:
        _CHARTS[key] = TypeChart.from_dex(dex)
    return _CHARTS[key]


def _joint(slots: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "message": " + ".join(s.get("label", s.get("kind", "?")) for s in slots) or "no action",
        "slots": slots,
        "label": " + ".join(s.get("label", "?") for s in slots) or "no action",
        "kinds": sorted({str(s.get("kind")) for s in slots}),
    }


def discard_rate(
    full_payoff: np.ndarray,
    kept_rows: list[int],
) -> float:
    """How much equilibrium mass the pruning threw away.

    The guard `docs/04-decision-engine.md` section 3 requires: solve the
    unpruned game, then measure the probability the unpruned equilibrium places
    on rows the policy discarded. Zero means the pruning was free on this
    position. This is an offline measurement -- it needs the full matrix, which
    is the thing pruning exists to avoid computing -- and is reported per policy
    implementation in the M7 benchmark.
    """
    equilibrium = solve_both(full_payoff)
    kept = set(kept_rows)
    return float(
        sum(
            probability
            for row, probability in enumerate(equilibrium.row)
            if row not in kept and probability > MASS_THRESHOLD
        )
    )

"""The lead sweep: choosing four and a lead pair at team preview (D86).

Until the second ladder cycle the preview decision was `random_teampreview`:
four of six and a lead drawn by chance, which is how Sneasler and Dragonite
came to lead into Indeedee and Mega Gardevoir on Psychic Terrain. The M4 bring
and lead predictors were rejected out of sample (D39, D56), so there is no
preview value model. What there is, since D85, is a one-turn model that can
price a lead pair against a lead pair -- with the opponent's likely moves from
the corpus prior, their stats from the belief, the terrain their ability
sets, and Intimidate on the way in.

So the sweep asks the search's own question of turn one: for each of our
fifteen lead pairs, against each of theirs, what is the equilibrium value of
the opening turn? The pair with the best mean is the lead. The back two are
the remaining Pokemon whose pairs scored best, which is a proxy for their
value in this matchup rather than a bring-4 model; `docs/04` section 6 wants
the real thing, and this is what stands in for it.

Anytime, because preview is on a clock and `teampreview` is synchronous in
poke-env: the opponent's pairs are visited in a seeded order, in rounds, and
a round counts only when all fifteen of our pairs have faced that opponent.
So every one of our pairs has seen exactly the same opponents when the budget
runs out, whatever was scored by then decides, and the choice is a function of
the seed and the number of *completed* rounds rather than of where the clock
happened to fall (D90).
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from champions.dex.loader import Dex, to_id
from champions.search.matrix import solve_both
from champions.search.payoff import CellModel, OpponentHypothesis, entry_effects, payoff_matrix
from champions.search.policy import PolicyProvider, opponent_candidates
from champions.search.twoply import enumerate_joint

#: Wall-clock budget for the sweep. poke-env's `teampreview` is synchronous,
#: so this blocks the event loop; well under the 90 s Showdown's VGC Timer
#: gives team preview and under the websocket's patience. The full 15 rounds
#: take 8.2 s on this box with nothing else running, and at an 8 s budget the
#: first 76 live games reached 7 or 8 rounds each and none of them all 15 --
#: live is about half the speed of the bench, between the belief's wider
#: columns and whatever else has the box. The margin is for that (D90).
PREVIEW_BUDGET_S = 20.0
#: Row and column budgets at preview, smaller than a live turn's because the
#: sweep solves up to 225 openings rather than one.
PREVIEW_K = 8
PREVIEW_COLUMN_K = 12


@dataclass
class LeadChoice:
    """What the sweep decided, and the table it decided from."""

    #: 1-based indices into the team, lead pair first.
    order: list[int]
    lead: tuple[int, int]
    scores: dict[tuple[int, int], float]
    rounds: int
    of_rounds: int
    elapsed_s: float
    evaluated: int
    #: Per Pokemon (0-based index), the mean value of the pairs it led in.
    singles: dict[int, float] = field(default_factory=dict)

    def as_message(self) -> str:
        return "/team " + "".join(str(i) for i in self.order)


def lead_sweep(
    ours: list[dict[str, Any]],
    theirs: list[dict[str, Any]],
    dex: Dex,
    model: CellModel,
    policy: PolicyProvider,
    believed_moves: Callable[[str], list[str]] | None,
    believed_ability: Callable[[str], str | None] | None = None,
    seed: int = 0,
    budget_s: float = PREVIEW_BUDGET_S,
    k: int = PREVIEW_K,
    column_k: int = PREVIEW_COLUMN_K,
    turn_one_fields: dict[str, Any] | None = None,
    max_rounds: int | None = None,
) -> LeadChoice:
    """Choose four and a lead from previewed views.

    `ours` are our six as `champions.protocol.state` describes a known
    Pokemon; `theirs` their six as it describes an unknown one. `model` and
    `policy` are the agent's own, so preview is priced by exactly the machine
    that will play the turn.
    """
    started = time.perf_counter()
    our_pairs = list(itertools.combinations(range(len(ours)), 2))
    their_pairs = list(itertools.combinations(range(len(theirs)), 2))
    rng = np.random.default_rng(seed)
    rng.shuffle(their_pairs)

    totals: dict[tuple[int, int], float] = dict.fromkeys(our_pairs, 0.0)
    rounds = 0
    evaluated = 0
    out_of_time = False
    for a, b in their_pairs:
        if max_rounds is not None and rounds >= max_rounds:
            break
        # A round is committed whole or not at all. Half a round leaves some
        # of our pairs having faced one more opponent than the others, and
        # the mean that decides the lead would then compare pairs scored
        # against different opponents -- which is the comparison the sweep
        # exists to make fair (D90). The cells of an abandoned round are
        # still counted in `evaluated`: the work happened.
        pending: dict[tuple[int, int], float] = {}
        for pair in our_pairs:
            if time.perf_counter() - started > budget_s:
                out_of_time = True
                break
            snapshot = opening(ours, theirs, pair, (a, b), dex, believed_ability, turn_one_fields)
            pending[pair] = _value(snapshot, dex, model, policy, believed_moves, k, column_k)
            evaluated += 1
        if out_of_time:
            break
        for pair, value in pending.items():
            totals[pair] += value
        rounds += 1

    scores = {pair: (totals[pair] / rounds) if rounds else float("nan") for pair in our_pairs}
    scored = [(s, p) for p, s in scores.items() if s == s]
    # Nothing priced (a zero budget) leads with the first two, which is what
    # a person watching would notice; ties break toward the earlier pair.
    lead = max(scored, key=lambda sp: (sp[0], -sp[1][0], -sp[1][1]))[1] if scored else (0, 1)

    singles: dict[int, float] = {}
    for index in range(len(ours)):
        values = [scores[p] for p in our_pairs if index in p and scores[p] == scores[p]]
        singles[index] = sum(values) / len(values) if values else float("nan")
    rest = [i for i in range(len(ours)) if i not in lead]
    rest.sort(key=lambda i: (-(singles[i] if singles[i] == singles[i] else -1.0), i))
    back = rest[:2]
    order = [lead[0] + 1, lead[1] + 1, *[i + 1 for i in back]]
    return LeadChoice(
        order=order,
        lead=lead,
        scores=scores,
        rounds=rounds,
        of_rounds=len(their_pairs),
        elapsed_s=time.perf_counter() - started,
        evaluated=evaluated,
        singles=singles,
    )


def opening(
    ours: list[dict[str, Any]],
    theirs: list[dict[str, Any]],
    our_pair: tuple[int, int],
    their_pair: tuple[int, int],
    dex: Dex,
    believed_ability: Callable[[str], str | None] | None = None,
    turn_one_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn one as the snapshot describes it, for one pair of leads each.

    Our four are the lead pair plus the first two others, so the evaluation
    counts four against the opponent's derived four; which two is a constant
    across the sweep and cancels in the comparison. Entry effects on both
    sides -- a Surge terrain, weather, Intimidate, and the seed a terrain
    pops -- are applied in the game's order, fastest first, so the slower
    Surge's terrain is the one standing when the turn is priced. Our
    abilities are exact; the opponent's are what the belief expects. Before
    D90 only the opponent's fired, so our own Grassy Surge, our Intimidate
    and the Grassy Seed under Unburden were all invisible to the sweep.

    Every Pokemon is put at full health, because at team preview every Pokemon
    *is* at full health, and the views do not say so. poke-env reports
    `current_hp_fraction` as 0 for a Pokemon it has never seen in battle, which
    is exactly what `teampreview_opponent_team` is, so the opponent's whole
    team arrives here reading 0% and not fainted. The evaluation read that as
    four healthy Pokemon against a side with none and scored every opening in
    the first 76 live games at a mean of 0.94, best pair to worst spanning
    0.10 (D90).
    """
    fields: dict[str, Any] = dict(turn_one_fields or {})

    def healthy(view: dict[str, Any]) -> dict[str, Any]:
        view["hp_pct"] = 100.0
        view["fainted"] = False
        if view.get("known") and view.get("max_hp"):
            view["hp"] = view["max_hp"]
        return view

    def ours_view(index: int, active: bool) -> dict[str, Any]:
        view = {**ours[index], "selected": True, "active": active, "first_turn": True}
        view["boosts"] = {}
        view["protect_counter"] = 0
        return healthy(view)

    def theirs_view(index: int, active: bool) -> dict[str, Any]:
        view = {**theirs[index], "active": active, "first_turn": True, "protect_counter": 0}
        view["boosts"] = {}
        if believed_ability is not None and not view.get("ability"):
            believed = believed_ability(view.get("species") or "")
            if believed:
                view["ability"] = to_id(believed)
        return healthy(view)

    our_bench = [i for i in range(len(ours)) if i not in our_pair][:2]
    their_bench = [i for i in range(len(theirs)) if i not in their_pair]
    our_side = {
        "active": [ours_view(i, True) for i in our_pair],
        "bench": [ours_view(i, False) for i in our_bench],
        "remaining": 2 + len(our_bench),
        "revealed": 2 + len(our_bench),
    }
    their_side = {
        "active": [theirs_view(i, True) for i in their_pair],
        "bench": [theirs_view(i, False) for i in their_bench],
        "remaining": len(theirs),
        "revealed": len(theirs),
    }
    snapshot: dict[str, Any] = {
        "turn": 1,
        "weather": {},
        "fields": fields,
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": our_side,
        "theirs": their_side,
    }
    # Slowest last, so the slowest Surge's terrain is the one standing. Ties
    # go to us arriving first, which means their terrain stands and their
    # Intimidate lands on our final numbers: the pessimistic reading, and the
    # project forbids settling it with the coin flip the game uses.
    arrivals = [("ours", 0), ("ours", 1), ("theirs", 0), ("theirs", 1)]
    arrivals.sort(key=lambda a: (-_preview_speed(snapshot[a[0]]["active"][a[1]]), a[0] != "ours"))
    for side, slot in arrivals:
        snapshot = entry_effects(snapshot, side, slot)
    return snapshot


def _preview_speed(view: dict[str, Any]) -> float:
    """Speed for ordering entry effects: exact for ours, the default
    hypothesis for theirs, which is all preview knows."""
    stats = view.get("stats")
    if view.get("known") and stats:
        return float(stats["spe"])
    return float(OpponentHypothesis().stat(view["base_stats"], "spe"))


def _value(
    snapshot: dict[str, Any],
    dex: Dex,
    model: CellModel,
    policy: PolicyProvider,
    believed_moves: Callable[[str], list[str]] | None,
    k: int,
    column_k: int,
) -> float:
    described = enumerate_joint(snapshot, dex)
    described = [d for d in described if "switch" not in d.get("kinds", [])]
    if not described:
        return 0.5
    rows = [s.action for s in policy.scored(described, k, snapshot)]
    columns = opponent_candidates(snapshot, dex, column_k, believed_moves=believed_moves)
    matrix = payoff_matrix(snapshot, rows, columns, model)
    return float(solve_both(matrix).value)

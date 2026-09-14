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
poke-env: the opponent's pairs are visited in a seeded order, in rounds, so
every one of our pairs has seen the same opponents when the budget runs out,
and whatever was scored by then decides. The seed is the battle's, so a
replayed preview makes the same choice.
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
from champions.search.payoff import SURGE_TERRAINS, WEATHER_ABILITIES, CellModel, payoff_matrix
from champions.search.policy import PolicyProvider, opponent_candidates
from champions.search.twoply import enumerate_joint

#: Wall-clock budget for the sweep. poke-env's `teampreview` is synchronous,
#: so this blocks the event loop; well under the preview timer and under the
#: websocket's patience.
PREVIEW_BUDGET_S = 8.0
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
    rng.shuffle(their_pairs)  # type: ignore[arg-type]

    totals: dict[tuple[int, int], float] = dict.fromkeys(our_pairs, 0.0)
    counts: dict[tuple[int, int], int] = dict.fromkeys(our_pairs, 0)
    rounds = 0
    evaluated = 0
    out_of_time = False
    for a, b in their_pairs:
        for pair in our_pairs:
            if time.perf_counter() - started > budget_s:
                out_of_time = True
                break
            snapshot = opening(ours, theirs, pair, (a, b), dex, believed_ability, turn_one_fields)
            totals[pair] += _value(snapshot, dex, model, policy, believed_moves, k, column_k)
            counts[pair] += 1
            evaluated += 1
        if out_of_time:
            break
        rounds += 1

    scores = {
        pair: (totals[pair] / counts[pair]) if counts[pair] else float("nan") for pair in our_pairs
    }
    scored = [(s, p) for p, s in scores.items() if counts[p]]
    # Nothing priced (a zero budget) leads with the first two, which is what
    # a person watching would notice; ties break toward the earlier pair.
    lead = max(scored, key=lambda sp: (sp[0], -sp[1][0], -sp[1][1]))[1] if scored else (0, 1)

    singles: dict[int, float] = {}
    for index in range(len(ours)):
        values = [scores[p] for p in our_pairs if index in p and counts[p]]
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
    across the sweep and cancels in the comparison. Entry abilities the belief
    expects -- a Surge terrain, weather, Intimidate -- are applied, since the
    turn is priced after they fire.
    """
    fields: dict[str, Any] = dict(turn_one_fields or {})
    weather: dict[str, Any] = {}
    our_boosts: dict[str, int] = {}
    for index in their_pair:
        ability = (
            to_id(believed_ability(theirs[index].get("species") or "")) if believed_ability else ""
        )
        if ability in SURGE_TERRAINS:
            fields = {SURGE_TERRAINS[ability]: 0}
        if ability in WEATHER_ABILITIES:
            weather = {WEATHER_ABILITIES[ability]: 0}
        if ability == "intimidate":
            our_boosts["atk"] = our_boosts.get("atk", 0) - 1

    def ours_view(index: int, active: bool) -> dict[str, Any]:
        view = {**ours[index], "selected": True, "active": active, "first_turn": True}
        view["boosts"] = dict(our_boosts) if active and our_boosts else {}
        view["protect_counter"] = 0
        return view

    def theirs_view(index: int, active: bool) -> dict[str, Any]:
        return {**theirs[index], "active": active, "first_turn": True, "protect_counter": 0}

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
    return {
        "turn": 1,
        "weather": weather,
        "fields": fields,
        "side_conditions": {},
        "opponent_side_conditions": {},
        "ours": our_side,
        "theirs": their_side,
    }


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

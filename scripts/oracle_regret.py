"""What does the belief's set uncertainty actually cost, in win probability?

The upper bound on everything the opponent model can ever buy, measured without
playing a game. For every decision in a self-play trace -- where both teams are
known, because we wrote both team files -- rebuild that decision's payoff matrix
twice over the *same* logged rows and columns:

* once with the belief's hypothesis, which is what the agent saw;
* once with `TeamOracle`, which is the truth.

Then ask what the oracle's matrix says about the row the agent actually chose.
The gap between that row and the oracle's best row is the win probability set
uncertainty cost on that decision. Nothing here samples and both matrices are
built from the same snapshot, so the comparison is exact rather than statistical.

Two numbers come out, and they answer different questions:

* **regret** -- oracle-best row minus chosen row, under the oracle's matrix. What
  a perfect opponent model would have been worth. This is the Phase 1 gate: if it
  is small, no amount of belief work can pay, and the loss is in the payoff model
  or the search instead.
* **bias** -- the belief's own value for the chosen cell minus the oracle's. How
  wrong the numbers the agent reported were, signed, so optimism and pessimism do
  not cancel in the mean.

Usage:
    python scripts/oracle_regret.py runs/d100-ab-a --team-a regmc-aero --team-b regmc-aero
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import numpy as np

from champions.agents.oracle import TeamOracle
from champions.belief.hypothesis import BeliefEffects, BeliefHypothesis
from champions.dex.loader import Dex
from champions.formats import FORMAT_ID
from champions.search.payoff import TurnModel, payoff_matrix
from champions.teams import load_team

#: Rows and columns a decision must have before it is worth re-solving. A
#: single-column decision has nothing for the opponent model to be wrong about.
MIN_COLUMNS = 2


def _events(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _decisions(events: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Each decision as (snapshot, candidates payload).

    The snapshot is the `turn_start` state, which is what the agent searched
    from; the candidates payload carries the rows, the columns, the payoff matrix
    the agent built and the row it chose. Paired by order rather than by turn,
    because several decisions share a turn number when a replacement is forced.
    """
    out = []
    snapshot = None
    for event in events:
        kind = event.get("type")
        payload = event.get("payload") or {}
        if kind == "turn_start":
            snapshot = payload.get("state")
        elif kind == "candidates" and "opponent_joint" in payload and snapshot is not None:
            out.append((snapshot, payload))
    return out


def _oracle_model(dex: Dex, oracle: TeamOracle) -> TurnModel:
    """The same model the belief agent builds, with the truth in the belief slot.

    `BeliefHypothesis` and `BeliefEffects` both take a `SetSource`, and
    `TeamOracle` satisfies it -- that interface is the whole reason this
    measurement is cheap.
    """
    return TurnModel(
        dex,
        hypothesis=BeliefHypothesis(belief=oracle),
        effects=BeliefEffects(oracle),
        place_incoming=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("--team-a", required=True, help="team the champ-a traces played")
    parser.add_argument("--team-b", help="team champ-b played; defaults to --team-a")
    parser.add_argument("--format", default=FORMAT_ID)
    parser.add_argument("--limit", type=int, default=0, help="stop after this many battles")
    args = parser.parse_args()

    dex = Dex.load(args.format)
    team_b = args.team_b or args.team_a
    # The oracle a side needs is over its OPPONENT's team.
    oracles = {
        "champ-a": _oracle_model(dex, TeamOracle(load_team(team_b), dex)),
        "champ-b": _oracle_model(dex, TeamOracle(load_team(args.team_a), dex)),
    }

    regret: list[float] = []
    bias: list[float] = []
    by_turn: dict[int, list[float]] = {}
    changed = 0
    skipped = 0
    battles = 0

    for path in sorted(args.trace_dir.rglob("*.jsonl")):
        seat = "champ-a" if path.name.endswith(".champ-a.jsonl") else "champ-b"
        model = oracles.get(seat)
        if model is None:
            continue
        battles += 1
        if args.limit and battles > args.limit:
            break
        for snapshot, payload in _decisions(_events(path)):
            rows = [dict(j) for j in payload.get("joint") or []]
            columns = [dict(c) for c in payload.get("opponent_joint") or []]
            chosen = payload.get("chosen_index")
            believed = payload.get("payoff")
            if chosen is None or len(columns) < MIN_COLUMNS or not rows:
                continue
            try:
                truth = payoff_matrix(snapshot, rows, columns, model)
            except Exception:
                skipped += 1
                continue
            if truth.shape[0] <= chosen:
                skipped += 1
                continue
            # Score a row by its worst column, which is what the solve is
            # ultimately protecting, then by its mean, so a row that is best
            # only against one column does not win on noise.
            score = truth.mean(axis=1)
            best = int(np.argmax(score))
            gap = float(score[best] - score[chosen])
            regret.append(gap)
            turn = int(payload.get("turn") or 0)
            by_turn.setdefault(turn, []).append(gap)
            if best != chosen:
                changed += 1
            if believed:
                believed_matrix = np.asarray(believed, dtype=float)
                if believed_matrix.shape == truth.shape:
                    bias.append(float(believed_matrix[chosen].mean() - truth[chosen].mean()))

    n = len(regret)
    if not n:
        print("no decisions scored; check the trace directory and team names")
        return

    print(f"{battles} agent-traces, {n} decisions re-solved, {skipped} skipped\n")
    print("REGRET -- what a perfect opponent model was worth, win-probability points")
    print(f"  mean                        {statistics.mean(regret) * 100:7.2f}")
    print(f"  median                      {statistics.median(regret) * 100:7.2f}")
    print(f"  p90                         {sorted(regret)[int(0.9 * n)] * 100:7.2f}")
    print(f"  the oracle prefers another row {changed / n:7.1%}")
    print(f"  per game at 6.5 decisions   {statistics.mean(regret) * 100 * 6.5:7.2f}")
    if bias:
        print("\nBIAS -- the belief's own value for the chosen cell minus the truth")
        print(f"  mean (positive = optimistic){statistics.mean(bias) * 100:7.2f}")
        print(f"  mean absolute               {statistics.mean([abs(b) for b in bias]) * 100:7.2f}")
    print("\nregret by turn")
    for turn in sorted(by_turn)[:10]:
        vals = by_turn[turn]
        print(f"  turn {turn:2d}  n={len(vals):5d}  {statistics.mean(vals) * 100:6.2f}")


if __name__ == "__main__":
    main()

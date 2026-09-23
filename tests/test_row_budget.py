"""D92: the one-ply agents solve over every legal joint action; the two-ply
agents keep the heuristic's budget, because their second ply multiplies it.
The live behaviour is in `test_oneply` (every legal row but the disqualified).
"""

from __future__ import annotations

from champions.agents.adaptive import AdaptiveAgent
from champions.agents.belief_agent import AdaptiveBeliefAgent, BeliefAgent
from champions.agents.oneply import OnePlyAgent
from champions.agents.twoply import TwoPlyAgent
from champions.search.policy import DEFAULT_K


def test_one_ply_agents_take_every_row_and_two_ply_agents_keep_the_budget() -> None:
    assert OnePlyAgent.row_budget is None
    assert BeliefAgent.row_budget is None
    assert TwoPlyAgent.row_budget == DEFAULT_K
    assert AdaptiveAgent.row_budget == DEFAULT_K
    # The adaptive belief agent is a belief agent that escalates; the budget
    # comes from the escalating side of its ancestry.
    assert AdaptiveBeliefAgent.row_budget == DEFAULT_K


def test_widening_by_kind_adds_the_best_rows_of_each_kind_the_budget_left_out() -> None:
    from champions.search.policy import DISQUALIFIED, ScoredAction, widen_by_kind

    def row(label: str, score: float, *slots: dict) -> ScoredAction:
        return ScoredAction(
            action={"message": label, "slots": list(slots)}, score=score, reasons=()
        )

    atk = {"kind": "move", "move": "closecombat"}
    prot = {"kind": "move", "move": "protect"}
    sw = {"kind": "switch", "species": "rillaboom"}
    ranked = [
        row("a1", 9.0, atk, atk),
        row("a2", 8.0, atk, atk),
        row("p1", 7.0, atk, prot),
        row("s1", 6.0, atk, sw),
        row("s2", 5.0, sw, atk),
        row("s3", 4.0, atk, sw),
        row("pp", 3.0, prot, prot),
        row("bad", DISQUALIFIED, sw, sw),
    ]
    kept = ranked[:2]
    out = widen_by_kind(ranked, kept, 2)
    assert [s.action["message"] for s in out] == ["a1", "a2", "p1", "s1", "s2", "pp"]
    # Nothing is added for a kind the budget already holds, and never a disqualified row.
    assert [s.action["message"] for s in widen_by_kind(ranked, ranked[:7], 2)] == [
        s.action["message"] for s in ranked[:7]
    ]
    assert widen_by_kind(ranked, [], 1)[0].action["message"] == "a1"

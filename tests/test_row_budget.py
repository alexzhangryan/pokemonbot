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

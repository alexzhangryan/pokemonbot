"""The one ply agent, pruning with implementation C instead of the heuristic.

Everything structural is `OnePlyAgent`'s -- enumerate, prune, estimate, solve,
sample. The only change is the candidate provider: `champions.search.language`'s
`LanguagePolicy`, which shows a language model A's shortlist with its computed
numbers and plays the order it returns.

Like `BeliefAgent`, this is a subclass rather than a flag so it can be run
against the other agents on the same team, seed and process -- the head-to-head
that would say whether C is worth its latency, once the guard says whether it is
worth trusting at all (`docs/pruning-guard.md`).

Latency: a decision now waits on a model call, which the search runs
synchronously inside `_search`. `docs/04-decision-engine.md` section 7 defers the
turn clock for the MVP, so this is acceptable for measurement; a live clock at
M11 would need the call moved off the event loop. The watchdog still applies --
if it fires before the model answers, the agent plays the pre-equilibrium
heuristic pick, which is `OnePlyAgent`'s existing anytime fallback.
"""

from __future__ import annotations

from typing import Any

from champions.agents.oneply import OnePlyAgent
from champions.search.language import LanguagePolicy
from champions.search.llm import LLMClient


class LanguageAgent(OnePlyAgent):
    """One ply equilibrium, pruning with the language-model provider."""

    strategy = "one-ply-language"

    def __init__(self, *args: Any, client: LLMClient | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Set after `super().__init__`, which is where `self.dex` becomes
        # available; `LanguagePolicy` builds its own client from the environment
        # when one is not injected.
        self._policy = LanguagePolicy(self.dex, client=client)

"""The replay corpus: scrape, parse, store.

`docs/05-data-pipeline.md` section 2. Two format IDs for two different reasons.
`gen9championsvgc2026regmb` is ordinary ladder play under hidden information,
which is the regime the agent plays in and therefore the source of behavioural
priors. `gen9championsvgc2026regmbbo3` carries Force Open Team Sheets, so every
replay reveals both players' complete sets at team preview -- labelled training
pairs available from the public replay API and from nowhere else.

The agent never plays with open sheets, because Champions has none (D2).
Consuming forced-sheet replays as labels is a data question, not a play
question, and the two never touch.
"""

from champions.corpus.replay import ReplayRecord, parse_replay
from champions.corpus.store import CorpusStore

__all__ = ["CorpusStore", "ReplayRecord", "parse_replay"]

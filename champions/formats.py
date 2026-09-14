"""The format the project targets, and what a new regulation inherits.

Everything is keyed by format id (`CLAUDE.md` constraint 3); this module is
where the id the entry points default to lives, and the one place that says
which older format a new one may borrow fitted artifacts from.

Regulation M-B left the official ladder on 2026-09-09 and Regulation M-C
replaced it (D80). M-C is the same `champions` mod with a larger legal pool
(thirty species legalised, none banned), so the artifacts fit on M-B play --
the evaluation weights (M6), the learned candidate prior (M7) and the coach's
bands (D77) -- are the best available prior for M-C until they are refit on
M-C data. `lender` names that relationship so a loader can fall back to the
M-B file and say so, rather than silently running uncalibrated or silently
using a file for the wrong format.
"""

from __future__ import annotations

#: What the entry points play when nothing says otherwise.
FORMAT_ID = "gen9championsvgc2026regmc"

#: The Bo3 sibling with Force Open Team Sheets, which the corpus scrapes.
BO3_FORMAT_ID = FORMAT_ID + "bo3"

#: A format's predecessor whose fitted artifacts it may use, marked as lent.
#: Only across formats that share a mod and a mechanics set.
LINEAGE: dict[str, str] = {
    "gen9championsvgc2026regmc": "gen9championsvgc2026regmb",
    "gen9championsvgc2026regmcbo3": "gen9championsvgc2026regmbbo3",
}


def lender(format_id: str) -> str | None:
    """The format whose artifacts `format_id` may borrow, if any."""
    return LINEAGE.get(format_id.lower())

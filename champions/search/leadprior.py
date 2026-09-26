"""What people lead and bring with the team we are playing (D95).

The lead sweep (`champions.search.lead`) prices each of our fifteen lead
pairs with the one-turn model against the opponent's fifteen. Its values are
ordered, not calibrated (D86), and on 161 rated games the turn-one value did
not separate wins from losses: the bot led Gholdengo and Incineroar in half
its games at a losing record, where the 47 corpus games on the same six led
that pair twice and Rillaboom with Sneasler seven times.

`scripts/corpus_teams.py` writes `data/policy/teams.<format>.json`: every
six the corpus has seen three or more times, with the lead pairs and the
brings people chose. This module reads the entry for the six we are playing
and gives the sweep two share tables, leads by pair and brings by species,
each normalised so the most common choice is 1.0. The sweep blends them
with its own values at `LEAD_PRIOR_WEIGHT`, so a lead the corpus never saw
is still available when the model likes it enough, and a lead the corpus
always plays does not need the model's agreement to be tried.

A team the corpus has not seen gets no prior and the sweep is unchanged.
Keyed by format, lent along `LINEAGE` like every other fitted artifact.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from champions.dex.loader import to_id
from champions.formats import lender

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "policy"

#: The weight of the corpus's shares against the sweep's values when choosing
#: the lead and the back two. Hand-set; the next ladder cycles read the lead
#: distribution and the turn-one win probability by lead to move it.
LEAD_PRIOR_WEIGHT = 0.3

#: Games a team needs in the corpus before its shares are used at all.
MIN_GAMES = 5


@dataclass(frozen=True)
class TeamPrior:
    """The corpus's leads and brings for one six."""

    format_id: str
    six: tuple[str, ...]
    games: int
    wins: int
    #: Lead pair, as `a+b` with the two species ids sorted, to its count.
    leads: dict[str, int] = field(default_factory=dict)
    #: Bring, as the sorted species ids joined with `+`, to its count.
    brings: dict[str, int] = field(default_factory=dict)
    lent: bool = False

    def lead_shares(self) -> dict[str, float]:
        """Lead pair to its share, scaled so the most common pair is 1.0."""
        total = max(self.leads.values(), default=0)
        return {k: v / total for k, v in self.leads.items()} if total else {}

    def bring_shares(self) -> dict[str, float]:
        """Species to how often it was brought, scaled so the most brought is 1.0."""
        counts: dict[str, float] = {}
        for bring, n in self.brings.items():
            for species in bring.split("+"):
                counts[species] = counts.get(species, 0.0) + n
        top = max(counts.values(), default=0.0)
        return {k: v / top for k, v in counts.items()} if top else {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "games": self.games,
            "wins": self.wins,
            "leads": dict(sorted(self.leads.items(), key=lambda kv: -kv[1])[:6]),
            "lent": self.lent,
        }


def pair_key(a: str, b: str) -> str:
    return "+".join(sorted((to_id(a), to_id(b))))


def teams_path(format_id: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / f"teams.{format_id}.json"


def load_team_prior(
    format_id: str, six: Sequence[str], data_dir: Path = DATA_DIR, min_games: int = MIN_GAMES
) -> TeamPrior | None:
    """The corpus entry for `six`, from the format's file or its lineage's."""
    key = "+".join(sorted(to_id(s) for s in six))
    for candidate, lent in ((format_id, False), (lender(format_id), True)):
        if candidate is None:
            continue
        path = teams_path(candidate, data_dir)
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        for team in raw.get("teams") or []:
            if team.get("key") == key and int(team.get("games") or 0) >= min_games:
                return TeamPrior(
                    format_id=format_id,
                    six=tuple(sorted(to_id(s) for s in six)),
                    games=int(team["games"]),
                    wins=int(team.get("wins") or 0),
                    leads={str(k): int(v) for k, v in (team.get("leads") or {}).items()},
                    brings={str(k): int(v) for k, v in (team.get("brings") or {}).items()},
                    lent=lent,
                )
    return None


def blend(
    values: Mapping[Any, float], shares: Mapping[Any, float], weight: float
) -> dict[Any, float]:
    """`(1 - weight) * value + weight * share`, a missing share counting as zero.

    Values are win probabilities in [0, 1] and shares are scaled to a top of
    1.0, so the two are on one scale and `weight` reads as a fraction.
    """
    if not shares or weight <= 0:
        return dict(values)
    return {k: (1.0 - weight) * v + weight * float(shares.get(k, 0.0)) for k, v in values.items()}

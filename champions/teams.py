"""Fixed legal teams, built for Reg M-B and legal in Reg M-C.

Teams are supplied by a human rather than built by the agent (DECISIONS.md D5),
so these are checked-in fixtures. All are validated against the format the
project plays (`champions.formats.FORMAT_ID`, Reg M-C since D80) by
tests/test_teams.py, which shells out to the vendored simulator's own team
validator rather than trusting them by eye. The `regmb-` prefix says which
regulation each was built for, not which it is legal in: M-C legalised
thirty species and banned none, so every M-B team is an M-C team, and none
of them uses what M-C added.

Team quality is a confound in every evaluation, so evaluations hold the team
fixed across arms.

`DEFAULT` is what the agent plays when nothing says otherwise: the ladder, the
self-play script, the viewer's run panel and the human-play script all take
it. `MENCE` is Alex's Reg M-C team (D87, D88): Mega Salamence's Tailwind and
Hyper Voice with Sneasler, Mega Floette, Rillaboom, Incineroar and Gholdengo,
with the stat points as registered; it replaced a Perish Song team the model
could not play (D88). `WORLDS` is Takuma Yamazaki's 2026 World
Championships winning team
(San Francisco, 2026-08-28 to 30, 395 players, Regulation Set M-B), with the
published stat points (D75). `RAIN` is the team DaniVGC03 won Maddo's Cup #9
with (234 players, Reg M-B, 2026-06-20, 13-1), with hand-chosen stat points
(D73). `ALPHA` and `BETA` are the bare and the plain team the earlier
milestones were measured on; the evaluation weights were fit on `ALPHA`
self-play (M6) and have not been refit.
"""

from __future__ import annotations

from pathlib import Path

TEAMS_DIR = Path(__file__).resolve().parent.parent / "data" / "teams"

ALPHA = "regmb-alpha"
BETA = "regmb-beta"
RAIN = "regmb-rain"
WORLDS = "regmb-worlds"
MENCE = "regmc-mence"
DEFAULT = MENCE


def load_team(name: str) -> str:
    """Return a team in Showdown export format, ready to hand to poke-env."""
    path = TEAMS_DIR / f"{name}.txt"
    if not path.exists():
        available = sorted(p.stem for p in TEAMS_DIR.glob("*.txt"))
        raise FileNotFoundError(f"No team named {name!r}. Available: {available}")
    return path.read_text(encoding="utf-8")


def available_teams() -> list[str]:
    return sorted(p.stem for p in TEAMS_DIR.glob("*.txt"))

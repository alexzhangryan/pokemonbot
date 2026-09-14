"""Fixed legal teams for Reg M-B.

Teams are supplied by a human rather than built by the agent (DECISIONS.md D5),
so these are checked-in fixtures. All are validated against
`gen9championsvgc2026regmb` by tests/test_teams.py, which shells out to the
vendored simulator's own team validator rather than trusting them by eye.

Team quality is a confound in every evaluation, so evaluations hold the team
fixed across arms.

`DEFAULT` is what the agent plays when nothing says otherwise: the ladder, the
self-play script, the viewer's run panel and the human-play script all take
it. `WORLDS` is Takuma Yamazaki's 2026 World Championships winning team
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
DEFAULT = WORLDS


def load_team(name: str) -> str:
    """Return a team in Showdown export format, ready to hand to poke-env."""
    path = TEAMS_DIR / f"{name}.txt"
    if not path.exists():
        available = sorted(p.stem for p in TEAMS_DIR.glob("*.txt"))
        raise FileNotFoundError(f"No team named {name!r}. Available: {available}")
    return path.read_text(encoding="utf-8")


def available_teams() -> list[str]:
    return sorted(p.stem for p in TEAMS_DIR.glob("*.txt"))

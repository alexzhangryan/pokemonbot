"""Fixed legal teams for Reg M-B.

Teams are supplied by a human rather than built by the agent (DECISIONS.md D5),
so these are checked-in fixtures. Both are validated against
`gen9championsvgc2026regmb` by tests/test_teams.py, which shells out to the
vendored simulator's own team validator rather than trusting them by eye.

Team quality is a confound in every evaluation, so evaluations hold the team
fixed across arms.
"""

from __future__ import annotations

from pathlib import Path

TEAMS_DIR = Path(__file__).resolve().parent.parent / "data" / "teams"

ALPHA = "regmb-alpha"
BETA = "regmb-beta"


def load_team(name: str) -> str:
    """Return a team in Showdown export format, ready to hand to poke-env."""
    path = TEAMS_DIR / f"{name}.txt"
    if not path.exists():
        available = sorted(p.stem for p in TEAMS_DIR.glob("*.txt"))
        raise FileNotFoundError(f"No team named {name!r}. Available: {available}")
    return path.read_text(encoding="utf-8")


def available_teams() -> list[str]:
    return sorted(p.stem for p in TEAMS_DIR.glob("*.txt"))

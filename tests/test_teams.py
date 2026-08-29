"""The checked-in teams must actually be legal in Reg M-B.

Validated by shelling out to the vendored simulator's own team validator rather
than by inspection, since legality depends on the champions mod's banlist and
learnsets, not on what looks reasonable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from champions.teams import ALPHA, BETA, available_teams, load_team

FORMAT_ID = "gen9championsvgc2026regmb"
SHOWDOWN_DIR = Path(__file__).resolve().parent.parent / "vendor" / "showdown"


def _pack(team_text: str) -> str:
    packed = subprocess.run(
        ["node", "pokemon-showdown", "pack-team"],
        cwd=SHOWDOWN_DIR,
        input=team_text,
        capture_output=True,
        text=True,
        check=True,
    )
    return packed.stdout.strip()


@pytest.mark.parametrize("team_name", [ALPHA, BETA])
def test_team_is_legal_in_regmb(team_name: str) -> None:
    packed = _pack(load_team(team_name))

    result = subprocess.run(
        ["node", "pokemon-showdown", "validate-team", FORMAT_ID],
        cwd=SHOWDOWN_DIR,
        input=packed,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"{team_name} rejected: {result.stderr}"


@pytest.mark.parametrize("team_name", [ALPHA, BETA])
def test_team_has_six_pokemon(team_name: str) -> None:
    # Reg M-B is bring 6, pick 4: min and max team size are both 6.
    assert len(_pack(load_team(team_name)).split("]")) == 6


def test_available_teams_lists_the_checked_in_teams() -> None:
    assert set(available_teams()) >= {ALPHA, BETA}


def test_missing_team_names_the_alternatives() -> None:
    with pytest.raises(FileNotFoundError, match="Available:"):
        load_team("no-such-team")

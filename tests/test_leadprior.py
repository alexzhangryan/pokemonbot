"""D95: the corpus's leads and brings for the six we play, blended into the sweep."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from champions.formats import FORMAT_ID
from champions.search.leadprior import TeamPrior, blend, load_team_prior, pair_key


def test_shares_scale_to_the_most_common_choice() -> None:
    prior = TeamPrior(
        FORMAT_ID,
        ("a", "b", "c", "d", "e", "f"),
        games=10,
        wins=5,
        leads={"a+b": 4, "c+d": 2},
        brings={"a+b+c+d": 3, "a+b+e+f": 1},
    )
    assert prior.lead_shares() == {"a+b": 1.0, "c+d": 0.5}
    shares = prior.bring_shares()
    assert shares["a"] == 1.0 and shares["c"] == 0.75 and shares["e"] == 0.25


def test_blend_is_a_convex_mix_with_missing_shares_as_zero() -> None:
    values = {(0, 1): 0.6, (2, 3): 0.5}
    shares = {(2, 3): 1.0}
    out = blend(values, shares, 0.3)
    assert out[(0, 1)] == pytest.approx(0.42)
    assert out[(2, 3)] == pytest.approx(0.65)
    assert blend(values, {}, 0.3) == values
    assert blend(values, shares, 0.0) == values


def test_load_team_prior_finds_the_six_regardless_of_order(tmp_path: Path) -> None:
    six = ["Sneasler", "Floette-Eternal", "Salamence", "Rillaboom", "Incineroar", "Gholdengo"]
    key = "+".join(sorted(pair_key(s, s).split("+")[0] for s in six))
    (tmp_path / f"teams.{FORMAT_ID}.json").write_text(
        json.dumps(
            {
                "teams": [
                    {
                        "key": key,
                        "games": 47,
                        "wins": 23,
                        "leads": {"rillaboom+sneasler": 7},
                        "brings": {"gholdengo+incineroar+rillaboom+sneasler": 9},
                    },
                    {"key": "x+y+z", "games": 2, "leads": {}, "brings": {}},
                ]
            }
        )
    )
    prior = load_team_prior(FORMAT_ID, reversed(six), tmp_path)
    assert prior is not None and prior.games == 47 and prior.leads == {"rillaboom+sneasler": 7}
    assert load_team_prior(FORMAT_ID, ["x", "y", "z"], tmp_path) is None  # below MIN_GAMES
    assert load_team_prior(FORMAT_ID, ["nobody"], tmp_path) is None


def test_the_shipped_file_knows_the_default_team() -> None:
    from champions.teams import DEFAULT, load_team

    six = [
        line.split(" @")[0].split("\n")[0]
        for line in load_team(DEFAULT).split("\n\n")
        if line.strip()
    ]
    prior = load_team_prior(FORMAT_ID, six)
    assert prior is not None and prior.games >= 5
    assert sum(prior.lead_shares().values()) > 0

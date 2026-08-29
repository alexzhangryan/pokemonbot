"""The protocol parser, against two real replays and some hand-built lines.

The fixtures are real logs from the public replay API, kept verbatim apart from
chat text, which is redacted because it is the one part of a public replay that
is a person talking and no consumer here reads it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from champions.protocol import parser

FIXTURES = Path(__file__).parent / "fixtures" / "replays"
BO1 = FIXTURES / "gen9championsvgc2026regmb-2672465082.log"
BO3 = FIXTURES / "gen9championsvgc2026regmbbo3-2672453466.log"


@pytest.fixture(scope="module")
def bo1() -> str:
    return BO1.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bo3() -> str:
    return BO3.read_text(encoding="utf-8")


def test_no_unhandled_messages_on_real_logs(bo1: str, bo3: str) -> None:
    """Coverage is a number, not a hope.

    An unrecognised message type is counted rather than dropped, so this test
    fails loudly if Showdown adds protocol we do not read -- which is the case
    the corpus would otherwise absorb silently as missing evidence.
    """
    for log in (bo1, bo3):
        state, _ = parser.parse_log(log)
        assert dict(state.unhandled) == {}


def test_nicknames_resolve_to_species(bo1: str) -> None:
    """A Dragonite nicknamed "ophis-Mega" is a Dragonite.

    Every message after the switch reports the nickname, so a parser that reads
    species off the line gets four phantom Pokemon per side. This fixture
    contains a nickname that looks exactly like a forme name, which is the case
    that makes a string heuristic look like it works.
    """
    state, observations = parser.parse_log(bo1)
    assert state.nicknames["p1"]["ophis-Mega"] == "Dragonite"
    assert state.nicknames["p2"]["Samurott"] == "Samurott-Hisui"
    faints = [o for o in observations if o.kind == parser.FAINT]
    assert all(o.species is not None for o in faints)
    assert "Dragonite" in {o.species for o in faints}


def test_switch_classification(bo1: str) -> None:
    """Lead, voluntary, pivot and replacement are four different decisions.

    They are the same protocol message. Only the phase and the tags tell them
    apart, and the difference matters: a replacement after a faint is forced,
    and counting it as a chosen switch would teach a policy prior that players
    switch far more than they do.
    """
    _, observations = parser.parse_log(bo1)
    hows = [o.detail.get("how") for o in observations if o.kind == parser.SWITCH]
    assert hows.count("lead") == 4
    assert "voluntary" in hows
    assert "replacement" in hows
    assert "pivot" in hows


def test_generic_attribution_reveals_abilities_and_items(bo1: str) -> None:
    """`[from] ability: Protean` reveals Protean, wherever it appears."""
    _, observations = parser.parse_log(bo1)
    attributed = [o for o in observations if o.detail.get("how") == "attributed"]
    assert any(o.kind == parser.ABILITY and o.value == "Protean" for o in attributed)
    assert all(o.side in ("p1", "p2") for o in attributed)
    assert all(o.species is not None for o in attributed)


def test_mega_reveals_stone_and_forme(bo3: str) -> None:
    """Mega Evolution is back in Champions, and it reveals the item for free."""
    _, observations = parser.parse_log(bo3)
    stones = {
        o.value for o in observations if o.kind == parser.ITEM and o.detail.get("how") == "mega"
    }
    assert stones == {"Delphoxite", "Swampertite"}
    formes = {o.value for o in observations if o.kind == parser.FORME and o.detail.get("mega")}
    assert "Delphox-Mega" in formes


def test_sequence_is_monotonic_and_preserves_within_turn_order(bo3: str) -> None:
    """Order is the only Speed evidence the protocol ever gives.

    `docs/03-belief-filter.md` propagates stat intervals from the order moves
    resolve in. A consumer that stores observations unordered has discarded half
    the spread inference before it starts, so the ordering is part of the
    contract and is tested as such.
    """
    _, observations = parser.parse_log(bo3)
    seqs = [o.seq for o in observations]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    turns = [o.turn for o in observations]
    assert turns == sorted(turns)


def test_unknown_message_is_counted_not_raised() -> None:
    state = parser.ParserState()
    assert parser.apply(state, "|thisisnotarealmessage|p1a: X|thing") == []
    assert state.unhandled["thisisnotarealmessage"] == 1


@pytest.mark.parametrize(
    "line",
    ["", "|", "|move|", "|switch|garbage", "not a protocol line", "|-damage|p1a: X"],
)
def test_malformed_lines_do_not_raise(line: str) -> None:
    """A hundred thousand replays will contain protocol we have not seen.

    A scraper that dies on one of them is worse than one that counts it.
    """
    state = parser.ParserState()
    parser.apply(state, line)


def test_split_ident_handles_side_only_idents() -> None:
    """`|-sidestart|p1: Alex|move: Tailwind` has no slot letter."""
    assert parser.split_ident("p1a: Sneasler") == ("p1", "p1a", "Sneasler")
    assert parser.split_ident("p2: Alex") == ("p2", "", "Alex")
    assert parser.split_ident("nonsense") is None


def test_species_from_details() -> None:
    assert parser.species_from_details("Greninja, L50, F, shiny") == "Greninja"
    assert parser.species_from_details("Sinistcha, L50") == "Sinistcha"

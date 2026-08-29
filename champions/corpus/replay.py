"""One replay log to one structured record.

Split from the scraper on purpose. `docs/05-data-pipeline.md` requires the raw
log to be kept alongside the parsed form "because the parser will be wrong at
first and re-parsing beats re-scraping", and that is only true if parsing is a
pure function of a stored log with no network in it. `scripts/scrape_replays.py
--reparse` rebuilds every derived table from disk.

What comes out, per side: the six at team preview, the complete sets when the
format forced open sheets, which Pokemon were actually brought and which led,
every action in order, and every observation a hidden-information watcher would
have made. The last two are different things and both are wanted -- the sets are
the label, the observations are the evidence a belief filter has to work from.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from champions.dex.loader import to_id
from champions.protocol import parser

PARSER_VERSION = 1

_RATING_LINE = re.compile(r"^(.*)'s rating: (\d+) &rarr; <strong>(\d+)</strong>")
_BESTOF = re.compile(r'href="/(game-bestof\d+-[a-z0-9-]+)"')
_GAME_NUMBER = re.compile(r"<strong>Game (\d+)</strong>")


@dataclass(frozen=True, slots=True)
class PokemonSet:
    """A complete set, from `|showteam|`. Ground truth, not inference.

    `points` and `ivs` are always empty in practice and that is the finding, not
    an omission: `docs/05-data-pipeline.md` section 5 says stat points and
    natures appear in no public dataset, and the open-sheet reveal confirms it
    for points. Nature does come through, which is worth knowing -- it is half
    of the spread and the corpus can learn a prior over it after all.
    """

    side: str
    species: str
    nickname: str | None
    item: str | None
    ability: str | None
    moves: tuple[str, ...]
    nature: str | None
    points: str | None
    gender: str | None
    ivs: str | None
    level: int | None


@dataclass(frozen=True, slots=True)
class PreviewEntry:
    """One of the six shown at team preview, and what happened to it."""

    side: str
    index: int
    species: str
    details: str
    appeared: bool
    lead: bool


@dataclass(frozen=True, slots=True)
class Action:
    """A choice, as far as the log reveals one.

    Moves and switches only. The log shows outcomes rather than submitted
    choices, so a move that was chosen and then prevented shows up as the
    prevention; that is a known and unavoidable ceiling on this table.
    """

    seq: int
    turn: int
    side: str
    slot: str | None
    species: str | None
    action: str
    value: str | None
    target: str | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    """Everything one replay contributes to the corpus."""

    replay_id: str
    format_id: str
    format_name: str | None
    uploadtime: int | None
    players: tuple[str, str]
    ratings: tuple[int | None, int | None]
    ratings_after: tuple[int | None, int | None]
    rated: bool
    winner: str | None
    result: str
    turns: int
    teamsize: dict[str, int]
    sheets_revealed: bool
    series_id: str | None
    game_number: int | None
    previews: tuple[PreviewEntry, ...]
    sets: tuple[PokemonSet, ...]
    actions: tuple[Action, ...]
    observations: tuple[parser.Observation, ...]
    unhandled: dict[str, int]
    log_sha256: str

    @property
    def winner_side(self) -> str | None:
        """ "p1", "p2", or None for a tie or an unfinished log."""
        if self.winner is None:
            return None
        for side, name in zip(("p1", "p2"), self.players, strict=True):
            if name == self.winner:
                return side
        return None

    def brought(self, side: str) -> tuple[str, ...]:
        return tuple(p.species for p in self.previews if p.side == side and p.appeared)

    @property
    def bring_fully_observed(self) -> bool:
        """Whether every Pokemon that was brought actually appeared.

        The log only reveals a bring-4 through Pokemon that took the field, so a
        game won without the fourth ever switching in yields three. M4 needs the
        complete label; this flag is how it selects the games that carry one.
        """
        return all(len(self.brought(side)) == self.teamsize.get(side, 0) for side in ("p1", "p2"))


def parse_packed_set(side: str, packed: str) -> PokemonSet | None:
    """One Pokemon out of a `|showteam|` payload.

    Showdown's packed format, positionally:
    `nickname|species|item|ability|moves|nature|evs|gender|ivs|shiny|level|misc`.
    An empty species field means the nickname *is* the species, which is the
    common case and the one that breaks a naive positional read.
    """
    fields = packed.split("|")
    if not fields or not fields[0].strip():
        return None
    nickname = fields[0].strip()

    def at(index: int) -> str:
        return fields[index].strip() if len(fields) > index else ""

    species_raw = at(1) or nickname
    level = at(10)
    return PokemonSet(
        side=side,
        species=to_id(species_raw),
        nickname=nickname if to_id(nickname) != to_id(species_raw) else None,
        item=to_id(at(2)) or None,
        ability=to_id(at(3)) or None,
        moves=tuple(to_id(m) for m in at(4).split(",") if m.strip()),
        nature=to_id(at(5)) or None,
        points=at(6) or None,
        gender=at(7) or None,
        ivs=at(8) or None,
        level=int(level) if level.isdigit() else None,
    )


def _header(log: str) -> dict[str, Any]:
    """The pre-battle block: players, ratings, preview, sheets, series, result.

    Read with a straight line scan rather than through the observation parser,
    because none of it is an observation -- it is metadata about the game, and
    the belief filter has no use for it.
    """
    info: dict[str, Any] = {
        "players": {},
        "ratings": {},
        "ratings_after": {},
        "preview": {"p1": [], "p2": []},
        "sets": [],
        "teamsize": {},
        "rated": False,
        "winner": None,
        "tie": False,
        "format_name": None,
        "series_id": None,
        "game_number": None,
        "sheets_revealed": False,
    }
    for line in log.splitlines():
        if not line.startswith("|"):
            continue
        parts = line.split("|")[1:]
        kind = parts[0]
        args = parts[1:]
        if kind == "player" and len(args) >= 2 and args[0] in ("p1", "p2"):
            if args[1]:
                info["players"][args[0]] = args[1]
            if len(args) >= 4 and args[3].isdigit():
                info["ratings"][args[0]] = int(args[3])
        elif kind == "tier" and args:
            info["format_name"] = args[0]
        elif kind == "rated":
            info["rated"] = True
        elif kind == "poke" and len(args) >= 2:
            info["preview"][args[0]].append(args[1])
        elif kind == "teamsize" and len(args) >= 2 and args[1].isdigit():
            info["teamsize"][args[0]] = int(args[1])
        elif kind == "showteam" and len(args) >= 2:
            # The packed team uses "|" as its own field separator, so the split
            # above has already taken the message apart. Rejoin everything after
            # the side to get the payload back before touching it.
            info["sheets_revealed"] = True
            for packed in "|".join(args[1:]).split("]"):
                parsed = parse_packed_set(args[0], packed)
                if parsed is not None:
                    info["sets"].append(parsed)
        elif kind == "win" and args:
            info["winner"] = args[0]
        elif kind == "tie":
            info["tie"] = True
        elif kind == "raw" and args:
            match = _RATING_LINE.match(args[0])
            if match:
                info["ratings_after"][match.group(1)] = int(match.group(3))
        elif kind == "uhtml" and len(args) >= 2 and args[0] == "bestof":
            series = _BESTOF.search(args[1])
            number = _GAME_NUMBER.search(args[1])
            if series:
                info["series_id"] = series.group(1)
            if number:
                info["game_number"] = int(number.group(1))
    return info


def parse_replay(
    replay_id: str, log: str, format_id: str = "", uploadtime: int | None = None
) -> ReplayRecord:
    """A replay log to a `ReplayRecord`. Pure, and never touches the network."""
    info = _header(log)
    state, observations = parser.parse_log(log)

    players = (info["players"].get("p1", ""), info["players"].get("p2", ""))
    after = info["ratings_after"]
    if not format_id:
        format_id = replay_id.rsplit("-", 1)[0]

    # Which of the six took the field, and which two led. The log is the only
    # witness: a Pokemon brought but never sent in is indistinguishable from one
    # left at preview, which `bring_fully_observed` exists to flag.
    appeared: dict[str, set[str]] = {"p1": set(), "p2": set()}
    leads: dict[str, set[str]] = {"p1": set(), "p2": set()}
    for obs in observations:
        if obs.kind != parser.SWITCH or obs.species is None:
            continue
        species = to_id(obs.species)
        appeared[obs.side].add(species)
        if obs.detail.get("how") == "lead":
            leads[obs.side].add(species)

    previews: list[PreviewEntry] = []
    for side in ("p1", "p2"):
        for index, details in enumerate(info["preview"][side]):
            species = to_id(parser.species_from_details(details))
            previews.append(
                PreviewEntry(
                    side=side,
                    index=index,
                    species=species,
                    details=details,
                    appeared=species in appeared[side],
                    lead=species in leads[side],
                )
            )

    actions = tuple(
        Action(
            seq=obs.seq,
            turn=obs.turn,
            side=obs.side,
            slot=obs.slot,
            species=to_id(obs.species) if obs.species else None,
            action=obs.kind,
            value=to_id(obs.value) if obs.value else None,
            target=obs.detail.get("target"),
            detail=obs.detail,
        )
        for obs in observations
        if obs.kind in (parser.MOVE, parser.SWITCH) and obs.detail.get("how") != "drag"
    )

    if info["tie"]:
        result = "tie"
    elif info["winner"]:
        result = "win"
    else:
        result = "unfinished"

    return ReplayRecord(
        replay_id=replay_id,
        format_id=format_id,
        format_name=info["format_name"],
        uploadtime=uploadtime,
        players=players,
        ratings=(info["ratings"].get("p1"), info["ratings"].get("p2")),
        ratings_after=(after.get(players[0]), after.get(players[1])),
        rated=info["rated"],
        winner=info["winner"],
        result=result,
        turns=state.turn,
        teamsize=info["teamsize"],
        sheets_revealed=info["sheets_revealed"],
        series_id=info["series_id"],
        game_number=info["game_number"],
        previews=tuple(previews),
        sets=tuple(info["sets"]),
        actions=actions,
        observations=tuple(observations),
        unhandled=dict(state.unhandled),
        log_sha256=hashlib.sha256(log.encode("utf-8")).hexdigest(),
    )

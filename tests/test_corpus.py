"""The replay corpus: record extraction, storage, and the scraper's contract.

No network. The scraper is tested against a fake transport, and everything else
against the two committed fixtures, so the whole file runs offline and in
milliseconds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from champions.corpus.replay import parse_packed_set, parse_replay
from champions.corpus.scrape import PAGE_SIZE, reparse, scrape_format
from champions.corpus.store import CorpusStore

FIXTURES = Path(__file__).parent / "fixtures" / "replays"
BO1_ID = "gen9championsvgc2026regmb-2672465082"
BO3_ID = "gen9championsvgc2026regmbbo3-2672453466"


def load(replay_id: str) -> str:
    return (FIXTURES / f"{replay_id}.log").read_text(encoding="utf-8")


@pytest.fixture
def bo1_record():
    return parse_replay(BO1_ID, load(BO1_ID), uploadtime=1788024407)


@pytest.fixture
def bo3_record():
    return parse_replay(BO3_ID, load(BO3_ID), uploadtime=1788022584)


# -- record extraction ----------------------------------------------------


def test_bo3_open_sheets_give_twelve_complete_sets(bo3_record) -> None:
    assert bo3_record.sheets_revealed
    assert len(bo3_record.sets) == 12
    for pokemon_set in bo3_record.sets:
        assert pokemon_set.item and pokemon_set.ability and pokemon_set.moves


def test_open_sheets_reveal_nature_but_not_points(bo3_record) -> None:
    """The correction to `docs/05-data-pipeline.md` section 5.

    That section says stat points *and natures* appear in no public dataset. The
    first half is right and the second is not: every set in a forced-open-sheet
    replay carries its nature, and none carries points or IVs. It matters,
    because it moves nature out of the in-battle inference half of the belief
    filter and into the learnable half.
    """
    assert all(s.nature for s in bo3_record.sets)
    assert all(s.points is None for s in bo3_record.sets)
    assert all(s.ivs is None for s in bo3_record.sets)


def test_bo1_has_no_sheets(bo1_record) -> None:
    """Hidden information is the regime the agent actually plays in."""
    assert not bo1_record.sheets_revealed
    assert bo1_record.sets == ()


def test_identifiers_are_showdown_ids(bo3_record) -> None:
    """Everything joinable is normalised, because the dex is keyed that way."""
    sneasler = next(s for s in bo3_record.sets if s.species == "sneasler")
    assert sneasler.item == "focussash"
    assert sneasler.ability == "poisontouch"
    assert "closecombat" in sneasler.moves
    assert sneasler.nature == "jolly"


def test_preview_records_all_six_and_flags_who_played(bo1_record) -> None:
    for side in ("p1", "p2"):
        entries = [p for p in bo1_record.previews if p.side == side]
        assert len(entries) == 6
        assert sum(p.lead for p in entries) == 2
        assert 0 < sum(p.appeared for p in entries) <= 4


def test_bring_fully_observed_flags_the_usable_labels(bo1_record, bo3_record) -> None:
    """A bring-4 is only a label when all four took the field.

    The log's only witness to a bring is a Pokemon appearing, so a game won
    without the fourth switching in yields three. M4 needs the complete label;
    this flag is how it selects the games that carry one, and pretending
    otherwise would train a bring predictor on truncated targets.
    """
    assert bo1_record.bring_fully_observed
    assert not bo3_record.bring_fully_observed
    assert len(bo3_record.brought("p1")) == 3
    assert bo3_record.teamsize["p1"] == 4


def test_result_and_winner_side(bo1_record, bo3_record) -> None:
    assert bo1_record.result == "win"
    assert bo1_record.winner_side == "p2"
    assert bo3_record.winner_side == "p1"
    assert bo3_record.ratings == (1192, 1115)
    assert bo3_record.ratings_after == (1212, 1095)


def test_bo3_series_is_linked(bo3_record) -> None:
    """A best-of-3 is three replays and one match. The link is in the header."""
    assert bo3_record.series_id == "game-bestof3-gen9championsvgc2026regmbbo3-2672449913"
    assert bo3_record.game_number == 3


def test_actions_exclude_forced_drags(bo1_record) -> None:
    assert bo1_record.actions
    assert all(a.detail.get("how") != "drag" for a in bo1_record.actions)
    assert {a.action for a in bo1_record.actions} <= {"move", "switch"}


def test_packed_set_with_explicit_species_and_nickname() -> None:
    """An empty species field means the nickname is the species."""
    plain = parse_packed_set("p1", "Sneasler||FocusSash|PoisonTouch|PoisonJab|Jolly||F|||50|")
    assert plain is not None and plain.species == "sneasler" and plain.nickname is None
    nicked = parse_packed_set("p1", "maki|Blaziken|LifeOrb|SpeedBoost|Overheat|Modest||M|||50|")
    assert nicked is not None and nicked.species == "blaziken" and nicked.nickname == "maki"
    assert parse_packed_set("p1", "") is None


# -- storage --------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> CorpusStore:
    with CorpusStore(tmp_path / "corpus.sqlite", tmp_path / "replays") as opened:
        yield opened


def test_store_round_trip(store: CorpusStore, bo3_record) -> None:
    store.write_log(bo3_record.format_id, bo3_record.replay_id, load(BO3_ID))
    store.upsert(bo3_record)
    stats = store.stats()
    assert stats.replays == 1
    assert stats.with_sheets == 1
    assert stats.sets == 12
    assert stats.previews == 12
    assert stats.reveals == len(bo3_record.observations)
    row = store.conn.execute("SELECT * FROM replays WHERE id = ?", (BO3_ID,)).fetchone()
    assert row["winner_side"] == "p1"
    assert row["series_id"].endswith("2672449913")
    assert json.loads(row["unhandled"]) == {}


def test_upsert_is_idempotent(store: CorpusStore, bo3_record) -> None:
    """Re-parsing the same log twice must leave the corpus as it was once.

    Derived rows are deleted before rewrite for exactly this reason: `--reparse`
    over a hundred thousand replays would otherwise multiply every table by the
    number of times the parser has been fixed.
    """
    store.upsert(bo3_record)
    first = store.stats()
    store.upsert(bo3_record)
    assert store.stats() == first


def test_reparse_rebuilds_from_disk_without_network(store: CorpusStore, bo3_record) -> None:
    store.write_log(bo3_record.format_id, BO3_ID, load(BO3_ID))
    store.upsert(bo3_record)
    store.conn.execute("DELETE FROM sets")
    store.conn.commit()
    assert store.stats().sets == 0
    done, failed = reparse(store)
    assert (done, failed) == (1, 0)
    assert store.stats().sets == 12


def test_stored_logs_finds_logs_the_database_has_not_seen(store: CorpusStore) -> None:
    """The raw log is the source of truth; the database is derived from it."""
    store.write_log("gen9championsvgc2026regmbbo3", BO3_ID, load(BO3_ID))
    assert store.stored_logs() == [("gen9championsvgc2026regmbbo3", BO3_ID)]
    assert not store.has_replay(BO3_ID)
    assert store.has_log("gen9championsvgc2026regmbbo3", BO3_ID)


# -- the scraper ----------------------------------------------------------


class FakeClient:
    """A replay API with a fixed corpus and a count of what was asked for."""

    def __init__(
        self, pages: list[list[dict[str, Any]]], log: str, missing: set[str] = frozenset()
    ):
        self.pages = pages
        self.log = log
        self.missing = missing
        self.searches: list[int | None] = []
        self.fetched: list[str] = []

    def search(self, format_id: str, before: int | None = None) -> list[dict[str, Any]]:
        self.searches.append(before)
        if before is None:
            return self.pages[0]
        for index, page in enumerate(self.pages):
            if page and page[-1]["uploadtime"] == before:
                return self.pages[index + 1] if index + 1 < len(self.pages) else []
        return []

    def fetch_log(self, replay_id: str) -> str | None:
        self.fetched.append(replay_id)
        return None if replay_id in self.missing else self.log


def page(
    n: int, start: int, format_id: str = "gen9championsvgc2026regmbbo3"
) -> list[dict[str, Any]]:
    return [
        {"id": f"{format_id}-{start + i}", "uploadtime": 1_700_000_000 - (start + i)}
        for i in range(n)
    ]


def test_scrape_stores_everything_on_one_short_page(store: CorpusStore) -> None:
    client = FakeClient([page(3, 0)], load(BO3_ID))
    stats = scrape_format(store, client, "gen9championsvgc2026regmbbo3", full=True)
    assert (stats.fetched, stats.pages, stats.missing) == (3, 1, 0)
    assert store.stats().replays == 3
    assert len(client.searches) == 1


def test_scrape_paginates_only_on_a_full_page(store: CorpusStore) -> None:
    """A page of exactly PAGE_SIZE means there is another; anything less ends it."""
    client = FakeClient([page(PAGE_SIZE, 0), page(2, PAGE_SIZE)], load(BO3_ID))
    stats = scrape_format(store, client, "gen9championsvgc2026regmbbo3", full=True)
    assert stats.pages == 2
    assert stats.fetched == PAGE_SIZE + 2
    assert client.searches[1] is not None


def test_scrape_never_refetches_a_stored_log(store: CorpusStore) -> None:
    """The rule the whole design turns on. `docs/05` section 2."""
    client = FakeClient([page(3, 0)], load(BO3_ID))
    scrape_format(store, client, "gen9championsvgc2026regmbbo3", full=True)
    again = FakeClient([page(3, 0)], load(BO3_ID))
    stats = scrape_format(store, again, "gen9championsvgc2026regmbbo3", full=True)
    assert again.fetched == []
    assert stats.skipped_known == 3 and stats.fetched == 0


def test_incremental_run_stops_at_the_first_page_with_nothing_new(store: CorpusStore) -> None:
    """Newest first means a page of known replays implies the rest are known too."""
    first = FakeClient(
        [page(PAGE_SIZE, 0), page(PAGE_SIZE, PAGE_SIZE), page(1, 2 * PAGE_SIZE)], load(BO3_ID)
    )
    scrape_format(store, first, "gen9championsvgc2026regmbbo3", full=True)
    second = FakeClient(
        [page(PAGE_SIZE, 0), page(PAGE_SIZE, PAGE_SIZE), page(1, 2 * PAGE_SIZE)], load(BO3_ID)
    )
    stats = scrape_format(store, second, "gen9championsvgc2026regmbbo3")
    assert stats.stopped_because == "caught_up"
    assert stats.pages == 1 and second.fetched == []


def test_scrape_survives_a_missing_replay(store: CorpusStore) -> None:
    """404 means gone, not broken. One deleted replay must not end a run."""
    ids = [entry["id"] for entry in page(3, 0)]
    client = FakeClient([page(3, 0)], load(BO3_ID), missing={ids[1]})
    stats = scrape_format(store, client, "gen9championsvgc2026regmbbo3", full=True)
    assert (stats.fetched, stats.missing) == (2, 1)


def test_max_replays_caps_a_run(store: CorpusStore) -> None:
    client = FakeClient([page(PAGE_SIZE, 0), page(PAGE_SIZE, PAGE_SIZE)], load(BO3_ID))
    stats = scrape_format(store, client, "gen9championsvgc2026regmbbo3", max_replays=5, full=True)
    assert stats.fetched == 5
    assert stats.stopped_because == "max_replays"
    assert len(client.fetched) == 5

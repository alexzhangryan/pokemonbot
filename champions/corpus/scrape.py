"""The polite replay scraper.

`docs/05-data-pipeline.md` section 2. The endpoint, confirmed from the client's
`WEB-API.md`, returns up to 51 results; a 51st result signals another page,
reached with `before=<uploadtime>` from the last entry. Individual replays come
back by appending `.log` to the replay URL.

Three properties this is built for, all of them consequences of the corpus
taking weeks to accumulate rather than minutes.

**Never re-fetch.** A replay whose raw log is already on disk is never
requested again, whatever else changes. Parsing is separately re-runnable
(`--reparse`), so being wrong about the parser costs no requests.

**Resumable and interruptible.** State lives in the store, not in the process,
so a run that is killed halfway loses at most one replay and the next run picks
up where it stopped.

**Slow on purpose.** One request per second by default, exponential backoff with
`Retry-After` honoured, and a `User-Agent` that says who this is. The corpus is
worth more than the hour saved by hammering a volunteer-run service.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from champions.corpus.replay import parse_replay
from champions.corpus.store import CorpusStore

REPLAY_BASE = "https://replay.pokemonshowdown.com"
USER_AGENT = "champions-bot/0.1 (research; +https://github.com/alexzhangryan/pokemonbot)"

#: The API's page size. A page of exactly this many means there is another page.
PAGE_SIZE = 51

BO1_FORMAT = "gen9championsvgc2026regmb"
BO3_FORMAT = "gen9championsvgc2026regmbbo3"
FORMATS = (BO3_FORMAT, BO1_FORMAT)


class ReplayClient(Protocol):
    """The transport. A protocol so tests can supply a corpus without a network."""

    def search(self, format_id: str, before: int | None = None) -> list[dict[str, Any]]: ...

    def fetch_log(self, replay_id: str) -> str | None: ...


@dataclass
class HttpReplayClient:
    """httpx against the public replay API, rate limited and backing off."""

    min_interval: float = 1.0
    timeout: float = 30.0
    max_retries: int = 5
    max_backoff: float = 60.0
    sleep: Callable[[float], None] = time.sleep
    _client: httpx.Client | None = field(default=None, repr=False)
    _last_request: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": USER_AGENT},
                timeout=self.timeout,
                follow_redirects=True,
            )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            self.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    def _get(self, url: str) -> httpx.Response | None:
        """A GET with backoff. None means "gone, stop asking" rather than "failed"."""
        assert self._client is not None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                response = self._client.get(url)
            except httpx.HTTPError:
                self.sleep(min(self.max_backoff, 2.0**attempt))
                continue
            if response.status_code == 404:
                return None
            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else 2.0**attempt
                self.sleep(min(self.max_backoff, wait))
                continue
            response.raise_for_status()
            return response
        return None

    def search(self, format_id: str, before: int | None = None) -> list[dict[str, Any]]:
        url = f"{REPLAY_BASE}/search.json?format={format_id}"
        if before is not None:
            url += f"&before={before}"
        response = self._get(url)
        if response is None:
            return []
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def fetch_log(self, replay_id: str) -> str | None:
        response = self._get(f"{REPLAY_BASE}/{replay_id}.log")
        return response.text if response is not None else None


@dataclass
class ScrapeStats:
    """What a run did. Printed at the end and asserted on in tests."""

    format_id: str
    pages: int = 0
    seen: int = 0
    fetched: int = 0
    skipped_known: int = 0
    missing: int = 0
    failed: int = 0
    stopped_because: str = "exhausted"

    def as_row(self) -> str:
        return (
            f"{self.format_id}: {self.fetched} new, {self.skipped_known} known, "
            f"{self.missing} gone, {self.failed} failed, over {self.pages} pages "
            f"({self.stopped_because})"
        )


def iter_search(
    client: ReplayClient, format_id: str, max_pages: int | None = None
) -> Iterator[list[dict[str, Any]]]:
    """Walk the search endpoint backwards in time, newest first.

    Termination is the API's own signal: a page shorter than `PAGE_SIZE` is the
    last one. `before` is taken from the last entry of the page just read, which
    the endpoint treats as exclusive -- verified against the live API, where
    consecutive pages share no IDs.
    """
    before: int | None = None
    pages = 0
    while max_pages is None or pages < max_pages:
        page = client.search(format_id, before=before)
        if not page:
            return
        pages += 1
        yield page
        if len(page) < PAGE_SIZE:
            return
        before = page[-1].get("uploadtime")
        if before is None:
            return


def scrape_format(
    store: CorpusStore,
    client: ReplayClient,
    format_id: str,
    max_replays: int | None = None,
    max_pages: int | None = None,
    full: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ScrapeStats:
    """Fetch and store every replay for one format that we do not already have.

    Default behaviour is incremental: the newest replays are first, so a page on
    which nothing is new means everything older is already stored, and the walk
    stops. `full=True` walks to exhaustion instead, which is what a first run or
    a backfill after a gap wants.
    """
    stats = ScrapeStats(format_id=format_id)
    known = store.known_ids(format_id)

    for page in iter_search(client, format_id, max_pages=max_pages):
        stats.pages += 1
        new_on_page = 0
        for entry in page:
            replay_id = entry.get("id")
            if not replay_id:
                continue
            stats.seen += 1
            if replay_id in known or store.has_log(format_id, replay_id):
                stats.skipped_known += 1
                continue
            new_on_page += 1
            log = client.fetch_log(replay_id)
            if log is None:
                stats.missing += 1
                continue
            try:
                record = parse_replay(
                    replay_id, log, format_id=format_id, uploadtime=entry.get("uploadtime")
                )
            except Exception as error:  # noqa: BLE001 - one bad log must not end a run
                stats.failed += 1
                if progress:
                    progress(f"  parse failed for {replay_id}: {error!r}")
                continue
            path = store.write_log(format_id, replay_id, log)
            store.upsert(record, log_path=path)
            known.add(replay_id)
            stats.fetched += 1
            if progress and stats.fetched % 25 == 0:
                progress(f"  {format_id}: {stats.fetched} fetched")
            if max_replays is not None and stats.fetched >= max_replays:
                stats.stopped_because = "max_replays"
                return stats
        if not full and new_on_page == 0 and stats.pages > 0:
            stats.stopped_because = "caught_up"
            return stats
    if max_pages is not None and stats.pages >= max_pages:
        stats.stopped_because = "max_pages"
    return stats


def reparse(
    store: CorpusStore,
    format_id: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Rebuild every derived table from the raw logs on disk. No network.

    Returns `(reparsed, failed)`. This is the whole reason raw logs are kept:
    the parser will be wrong at first, and re-parsing beats re-scraping.
    """
    done = failed = 0
    for stored_format, replay_id in store.stored_logs(format_id):
        try:
            log = store.read_log(stored_format, replay_id)
            existing = store.conn.execute(
                "SELECT uploadtime FROM replays WHERE id = ?", (replay_id,)
            ).fetchone()
            record = parse_replay(
                replay_id,
                log,
                format_id=stored_format,
                uploadtime=existing["uploadtime"] if existing else None,
            )
            store.upsert(record)
            done += 1
            if progress and done % 100 == 0:
                progress(f"  reparsed {done}")
        except Exception as error:  # noqa: BLE001
            failed += 1
            if progress:
                progress(f"  reparse failed for {replay_id}: {error!r}")
    return done, failed

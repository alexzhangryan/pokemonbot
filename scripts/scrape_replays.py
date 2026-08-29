"""Scrape the Showdown replay corpus for both Champions Reg M-B formats.

    python scripts/scrape_replays.py                    # incremental, both formats
    python scripts/scrape_replays.py --full             # walk back to the first replay
    python scripts/scrape_replays.py --format gen9championsvgc2026regmbbo3 --max-replays 200
    python scripts/scrape_replays.py --reparse          # rebuild tables from stored logs
    python scripts/scrape_replays.py --stats            # what the corpus currently holds

Incremental is the default and is safe to re-run as often as you like: replays
whose raw log is already on disk are never re-fetched, and the walk stops at the
first page with nothing new on it.

The first `--full` run is long. It is meant to be: one request per second
against a service nobody is paying for. Kill it whenever; it resumes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.corpus.scrape import (  # noqa: E402
    FORMATS,
    HttpReplayClient,
    reparse,
    scrape_format,
)
from champions.corpus.store import CorpusStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "corpus.sqlite"
DEFAULT_LOGS = REPO_ROOT / "data" / "replays"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", action="append", dest="formats", help="format ID, repeatable")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--max-replays", type=int, default=None, help="per format, this run")
    parser.add_argument("--max-pages", type=int, default=None, help="per format, this run")
    parser.add_argument("--full", action="store_true", help="walk to exhaustion, not just new")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between requests")
    parser.add_argument(
        "--reparse", action="store_true", help="rebuild from stored logs, no network"
    )
    parser.add_argument("--stats", action="store_true", help="report and exit")
    args = parser.parse_args()

    formats = tuple(args.formats) if args.formats else FORMATS

    with CorpusStore(args.db, args.logs) as store:
        if args.stats:
            report(store)
            return 0

        if args.reparse:
            for format_id in formats:
                done, failed = reparse(store, format_id, progress=print)
                print(f"{format_id}: reparsed {done}, failed {failed}")
            report(store)
            return 0

        client = HttpReplayClient(min_interval=args.interval)
        try:
            for format_id in formats:
                print(f"scraping {format_id} ...")
                stats = scrape_format(
                    store,
                    client,
                    format_id,
                    max_replays=args.max_replays,
                    max_pages=args.max_pages,
                    full=args.full,
                    progress=print,
                )
                print(stats.as_row())
        except KeyboardInterrupt:
            print("\ninterrupted; everything fetched so far is stored")
        finally:
            client.close()
        report(store)
    return 0


def report(store: CorpusStore) -> None:
    stats = store.stats()
    print(
        f"\ncorpus: {stats.replays} replays "
        f"({stats.with_sheets} with open sheets), "
        f"{stats.sets} sets, {stats.previews} preview slots, "
        f"{stats.actions} actions, {stats.reveals} reveals"
    )
    for format_id, count in sorted(stats.by_format.items()):
        print(f"  {format_id}: {count}")


if __name__ == "__main__":
    raise SystemExit(main())

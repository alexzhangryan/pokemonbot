"""Build the set prior the belief filter draws from, out of the replay corpus.

Usage:
    python scripts/build_priors.py
    python scripts/build_priors.py --format gen9championsvgc2026regmbbo3
    python scripts/build_priors.py --stats-only

The artifact is content-hashed and written to `data/priors/setprior.<hash>.json`,
the same convention `scripts/build_dex.py` uses and for the same reason: two
builds that disagree should be two files rather than one file whose meaning
depends on when it was written. Stale files are removed, because
`SetPrior.load` refuses to guess between them.

Gitignored like the dex dump. It is derived from the corpus in seconds and the
corpus is the thing worth keeping.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from champions.belief.priors import PRIOR_DIR, SetPrior
from champions.corpus.store import CorpusStore

DEFAULT_DATABASE = Path("data/corpus.sqlite")


def build(database: Path, format_id: str | None, prior_dir: Path) -> tuple[Path, SetPrior]:
    prior = SetPrior.from_corpus(database, format_id)
    content = json.dumps(prior.to_json(), sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()[:12]
    prior_dir.mkdir(parents=True, exist_ok=True)

    out_path = prior_dir / f"setprior.{content_hash}.json"
    out_path.write_bytes(content)
    for stale in prior_dir.glob("setprior.*.json"):
        if stale != out_path:
            stale.unlink()
    return out_path, prior


def describe(prior: SetPrior) -> str:
    """What the prior actually contains, since the answer changes as the corpus grows."""
    counts = sorted((entry.count for entry in prior.species.values()), reverse=True)
    covered = sum(1 for n in counts if n >= 32)
    lines = [
        f"sets            {prior.meta.get('sets', 0):>7,}",
        f"species         {len(prior.species):>7,}",
        f"well observed   {covered:>7,}  (>= 32 sets, so mostly empirical rather than composed)",
        f"teammate pairs  {len(prior.pairs):>7,}",
    ]
    if counts:
        lines.append(f"median species  {counts[len(counts) // 2]:>7,} sets")
    top = sorted(prior.species.values(), key=lambda e: -e.count)[:8]
    lines.append("")
    lines.append("most seen:")
    for entry in top:
        distinct = len(entry.sets)
        lines.append(
            f"  {entry.species:<22} {entry.count:>5} sets, {distinct:>4} distinct, "
            f"{entry.empirical_weight():.2f} empirical weight"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--format",
        default=None,
        help="Restrict to one format ID. Default is every format in the corpus, "
        "since Reg M-B Bo1 and Bo3 are the same game with different labelling.",
    )
    parser.add_argument("--prior-dir", type=Path, default=PRIOR_DIR)
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Describe the corpus without writing an artifact.",
    )
    args = parser.parse_args()

    if not args.database.exists():
        raise SystemExit(
            f"No corpus at {args.database}. Build one with:\n    make scrape\n"
            f"(or `make scrape-full` for the full backfill)."
        )

    if args.stats_only:
        prior = SetPrior.from_corpus(args.database, args.format)
        print(describe(prior))
        return

    store = CorpusStore(args.database)
    try:
        stats = store.stats()
    finally:
        store.close()

    path, prior = build(args.database, args.format, args.prior_dir)
    print(describe(prior))
    print()
    print(f"corpus          {stats.replays:,} replays, {stats.with_sheets:,} with team sheets")
    print(f"wrote           {path}")


if __name__ == "__main__":
    main()

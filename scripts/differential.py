"""Run the differential harness over random legal positions.

At M0 there is no second implementation, so this checks the simulator's
determinism under a fixed seed and exercises the position generator.

Usage: python scripts/differential.py [n_positions] [--seed 0]
"""

from __future__ import annotations

import argparse
import time

from champions.harness.differential import (
    FORMAT_ID,
    ShowdownEngine,
    check_determinism,
    generate_positions,
)
from champions.search.oracle import SimServer
from champions.teams import ALPHA, BETA, load_team


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("n_positions", nargs="?", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-turns", type=int, default=8)
    args = parser.parse_args()

    with SimServer() as server:
        start = time.perf_counter()
        positions = generate_positions(
            server,
            args.n_positions,
            seed=args.seed,
            p1_team=load_team(ALPHA),
            p2_team=load_team(BETA),
            max_turns=args.max_turns,
        )
        generated_s = time.perf_counter() - start

        unique = len({p.digest() for p in positions})
        print(f"format {FORMAT_ID}, seed {args.seed}")
        print(f"generated {len(positions)} positions ({unique} distinct) in {generated_s:.1f}s")

        start = time.perf_counter()
        divergences = check_determinism(ShowdownEngine(server), positions)
        checked_s = time.perf_counter() - start

    print(f"checked determinism in {checked_s:.1f}s")
    print(f"divergences: {len(divergences)}")
    for divergence in divergences[:10]:
        print(f"  ! {divergence}")

    if divergences:
        raise SystemExit(1)
    print("all positions self-consistent under a fixed seed")


if __name__ == "__main__":
    main()

"""Print a decision trace in readable form.

Usage: python scripts/show_trace.py [path-or-dir] [--full]

With no argument, shows the most recently written trace under traces/.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from champions.trace.validate import validate_trace_file

TRUNCATE = 150


def latest_trace(root: Path) -> Path | None:
    candidates = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="traces")
    parser.add_argument("--full", action="store_true", help="do not truncate payloads")
    args = parser.parse_args()

    target = Path(args.path)
    if target.is_dir():
        found = latest_trace(target)
        if found is None:
            raise SystemExit(f"no .jsonl traces under {target}/")
        target = found

    problems = validate_trace_file(target)
    print(f"{target}")
    print(f"valid: {'yes' if not problems else 'NO -> ' + '; '.join(problems)}")
    print()

    counts: dict[str, int] = {}
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            counts[event["type"]] = counts.get(event["type"], 0) + 1
            payload = json.dumps(event["payload"])
            if not args.full and len(payload) > TRUNCATE:
                payload = payload[:TRUNCATE] + "…"
            print(f"{event['seq']:>4}  {event['type']:<17} {payload}")

    print()
    print("  ".join(f"{name}={n}" for name, n in sorted(counts.items())))


if __name__ == "__main__":
    main()

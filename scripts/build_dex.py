"""Invoke js/dump_dex.js for a format ID and write the hashed dex dump.

Usage:
    python scripts/build_dex.py <format_id> [<format_id> ...]
    python scripts/build_dex.py <format_id> --delta [--base-mod gen9]

--delta additionally dumps the format's own mod and --base-mod (mainline
gen9 by default) with no isNonstandard filtering, diffs every move, item,
and ability field by field, and writes docs/dex-delta.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DUMP_DEX_JS = REPO_ROOT / "js" / "dump_dex.js"
DEX_DIR = REPO_ROOT / "data" / "dex"
DELTA_DOC = REPO_ROOT / "docs" / "dex-delta.md"
SHOWDOWN_COMMIT_FILE = REPO_ROOT / "vendor" / "SHOWDOWN_COMMIT"

CATEGORIES = ("moves", "items", "abilities")


def _run_dump_dex(*args: str) -> bytes:
    result = subprocess.run(
        ["node", str(DUMP_DEX_JS), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _write_hashed(prefix: str, content: bytes) -> Path:
    content_hash = hashlib.sha256(content).hexdigest()[:12]
    DEX_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DEX_DIR / f"{prefix}.{content_hash}.json"
    out_path.write_bytes(content)
    return out_path


def build_dex(format_id: str) -> Path:
    content = _run_dump_dex(format_id)
    return _write_hashed(format_id, content)


def dump_mod(mod_id: str) -> dict[str, Any]:
    content = _run_dump_dex("--mod", mod_id)
    _write_hashed(f"mod-{mod_id}", content)
    return json.loads(content)


def diff_mods(base: dict[str, Any], override: dict[str, Any]) -> dict[str, dict[str, Any]]:
    delta: dict[str, dict[str, Any]] = {}
    for category in CATEGORIES:
        base_entries = base[category]
        override_entries = override[category]
        base_ids = set(base_entries)
        override_ids = set(override_entries)

        modified: dict[str, dict[str, tuple[Any, Any]]] = {}
        for entry_id in sorted(base_ids & override_ids):
            b, o = base_entries[entry_id], override_entries[entry_id]
            changed = {
                key: (b.get(key), o.get(key))
                for key in b.keys() | o.keys()
                if b.get(key) != o.get(key)
            }
            if changed:
                modified[entry_id] = changed

        delta[category] = {
            "added": sorted(override_ids - base_ids),
            "removed": sorted(base_ids - override_ids),
            "modified": modified,
        }
    return delta


def _format_value(value: Any) -> str:
    text = json.dumps(value)
    return text if len(text) <= 200 else text[:200] + "…"


def render_delta_doc(base_mod: str, override_mod: str, delta: dict[str, dict[str, Any]]) -> str:
    showdown_commit = (
        SHOWDOWN_COMMIT_FILE.read_text().strip() if SHOWDOWN_COMMIT_FILE.exists() else "unknown"
    )

    lines = [
        f"# Dex Delta: `{override_mod}` vs `{base_mod}` (mainline)",
        "",
        f"Generated from `vendor/showdown` at commit `{showdown_commit}` by "
        "`scripts/build_dex.py --delta`. Not published. This is the engineering "
        f"checklist for M1: every move, item, and ability where `{override_mod}` "
        f"differs from unmodified `{base_mod}`.",
        "",
        "## Summary",
        "",
        "| Category | Added | Removed | Modified |",
        "| --- | --- | --- | --- |",
    ]
    for category in CATEGORIES:
        d = delta[category]
        lines.append(
            f"| {category} | {len(d['added'])} | {len(d['removed'])} | {len(d['modified'])} |"
        )
    lines.append("")

    for category in CATEGORIES:
        d = delta[category]
        lines.append(f"## {category.capitalize()} ({len(d['modified'])} modified)")
        lines.append("")
        if d["added"]:
            lines.append(f"Added in `{override_mod}`: {', '.join(d['added'])}")
            lines.append("")
        if d["removed"]:
            lines.append(f"Removed in `{override_mod}`: {', '.join(d['removed'])}")
            lines.append("")
        for entry_id in sorted(d["modified"]):
            lines.append(f"### `{entry_id}`")
            for field, (old, new) in sorted(d["modified"][entry_id].items()):
                lines.append(f"- `{field}`: {_format_value(old)} -> {_format_value(new)}")
            lines.append("")

    return "\n".join(lines)


def build_delta(format_id: str, base_mod: str) -> Path:
    format_dump = json.loads(_run_dump_dex(format_id))
    override_mod = format_dump["mod"]

    override_dump = dump_mod(override_mod)
    base_dump = dump_mod(base_mod)

    delta = diff_mods(base_dump, override_dump)
    DELTA_DOC.parent.mkdir(parents=True, exist_ok=True)
    DELTA_DOC.write_text(render_delta_doc(base_mod, override_mod, delta))
    return DELTA_DOC


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("format_ids", nargs="+")
    parser.add_argument("--delta", action="store_true")
    parser.add_argument("--base-mod", default="gen9")
    args = parser.parse_args()

    for format_id in args.format_ids:
        out_path = build_dex(format_id)
        print(f"{format_id} -> {out_path.relative_to(REPO_ROOT)}")

    if args.delta:
        for format_id in args.format_ids:
            delta_path = build_delta(format_id, args.base_mod)
            print(
                f"delta({format_id} mod vs {args.base_mod}) -> {delta_path.relative_to(REPO_ROOT)}"
            )


if __name__ == "__main__":
    main()

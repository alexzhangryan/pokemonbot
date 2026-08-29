"""Loader for the resolved Champions dex dumped by scripts/build_dex.py.

This exists because poke-env ships mainline Gen 9 data, and Champions is not
mechanically Gen 9. The T0.3 delta found 303 moves, 256 items, and 8 abilities
that differ from mainline, including base power changes (Anchor Shot 80 -> 90,
Apple Acid 80 -> 90) and the PP cap. Any component that reads move or item
numbers from poke-env is reading the wrong ones; it reads them from here
instead.

The dump is content-hashed and gitignored. Regenerate with:
    python scripts/build_dex.py gen9championsvgc2026regmb
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEX_DIR = REPO_ROOT / "data" / "dex"


class DexNotBuiltError(FileNotFoundError):
    pass


@dataclass(frozen=True)
class Dex:
    """The resolved dex for one format ID, post mod resolution."""

    format_id: str
    mod: str
    gen: int
    path: Path
    _data: dict[str, Any]

    @classmethod
    def load(cls, format_id: str, dex_dir: Path | str = DEX_DIR) -> Dex:
        dex_dir = Path(dex_dir)
        candidates = sorted(dex_dir.glob(f"{format_id}.*.json"))
        if not candidates:
            raise DexNotBuiltError(
                f"No dex dump for {format_id!r} in {dex_dir}. Build it with:\n"
                f"    python scripts/build_dex.py {format_id}"
            )
        if len(candidates) > 1:
            # Distinct hashes mean distinct vendor builds; picking silently would
            # make which mechanics are in force depend on filesystem ordering.
            raise ValueError(
                f"Multiple dex dumps for {format_id!r}, ambiguous which build is "
                f"current: {[p.name for p in candidates]}. Remove the stale ones."
            )
        path = candidates[0]
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            format_id=format_id,
            mod=data["mod"],
            gen=data["gen"],
            path=path,
            _data=data,
        )

    @property
    def content_hash(self) -> str:
        return self.path.stem.split(".")[-1]

    @cached_property
    def species(self) -> dict[str, Any]:
        return self._data["species"]

    @cached_property
    def moves(self) -> dict[str, Any]:
        return self._data["moves"]

    @cached_property
    def items(self) -> dict[str, Any]:
        return self._data["items"]

    @cached_property
    def abilities(self) -> dict[str, Any]:
        return self._data["abilities"]

    @cached_property
    def learnsets(self) -> dict[str, Any]:
        return self._data["learnsets"]

    @cached_property
    def mega_stones(self) -> dict[str, dict[str, str]]:
        """item id -> {base species name: mega forme name}.

        Mega Evolution is back in Champions and is driven by the held item, so
        this is subject to Item Clause = 1.
        """
        return {
            item_id: item["megaStone"]
            for item_id, item in self.items.items()
            if item.get("megaStone")
        }

    def move(self, move_id: str) -> dict[str, Any]:
        return self.moves[move_id]

    def base_power(self, move_id: str, default: int = 0) -> int:
        move = self.moves.get(move_id)
        return int(move["basePower"]) if move else default

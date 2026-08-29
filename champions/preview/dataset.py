"""Team preview examples, read out of the replay corpus.

One example is one side of one game at team preview: the six species that side
showed, the six the opponent showed, which four were brought, which two led, and
whether that side won. Both sides of a game yield an example, and they are
mirror images of each other.

Two things about this dataset are load bearing and easy to get wrong.

**Species only.** The corpus knows far more than species for open-sheet Bo3
games -- items, abilities, moves, natures, all of it. None of that may be used
as a feature. Champions has no open team sheets, so at preview the agent knows
exactly six names per side and nothing else, and a model trained on anything
richer would score beautifully offline and be unusable in the game it was built
for. The open-sheet corpus is the source of *labels*, never of inputs (D33).

**Group before splitting.** A best-of-three is two or three replays played by
the same two teams, and a laddering player brings the same six for hours. Split
those across train and test and the model is scored on teams it has memorised.
Examples are grouped by series first, and `unseen_players` marks the harder
subset where neither player appeared in training at all.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from champions.corpus.store import CorpusStore

#: Reg M-B brings four of six and leads two of four.
TEAM_SIZE = 6
BRING_SIZE = 4
LEAD_SIZE = 2


@dataclass(frozen=True, slots=True)
class PreviewExample:
    """One side of one game, as it looked at team preview plus what happened."""

    replay_id: str
    series_id: str
    side: str
    player: str
    opponent: str
    rating: int | None
    team: tuple[str, ...]
    opponent_team: tuple[str, ...]
    brought: tuple[bool, ...]
    led: tuple[bool, ...]
    won: bool
    bring_observed: bool

    @property
    def brought_species(self) -> tuple[str, ...]:
        return tuple(s for s, b in zip(self.team, self.brought, strict=True) if b)

    @property
    def led_species(self) -> tuple[str, ...]:
        return tuple(s for s, b in zip(self.team, self.led, strict=True) if b)

    @property
    def usable_for_bring(self) -> bool:
        """Whether the bring label is complete rather than truncated (D34)."""
        return self.bring_observed and sum(self.brought) == BRING_SIZE

    @property
    def usable_for_lead(self) -> bool:
        return sum(self.led) == LEAD_SIZE


def load_examples(store: CorpusStore, format_id: str | None = None) -> list[PreviewExample]:
    """Every preview in the corpus, both sides, in a stable order."""
    where = "WHERE r.format_id = ?" if format_id else ""
    args = (format_id,) if format_id else ()
    rows = store.conn.execute(
        f"""SELECT r.id, r.series_id, r.p1, r.p2, r.p1_rating, r.p2_rating,
                   r.winner_side, r.bring_fully_observed,
                   p.side, p.slot_index, p.species, p.appeared, p.lead
            FROM replays r JOIN previews p ON p.replay_id = r.id
            {where}
            ORDER BY r.id, p.side, p.slot_index""",
        args,
    ).fetchall()

    grouped: dict[tuple[str, str], list] = {}
    meta: dict[str, dict] = {}
    for row in rows:
        grouped.setdefault((row["id"], row["side"]), []).append(row)
        meta[row["id"]] = row

    examples: list[PreviewExample] = []
    for (replay_id, side), slots in grouped.items():
        other = "p2" if side == "p1" else "p1"
        opposing = grouped.get((replay_id, other))
        if opposing is None or len(slots) != TEAM_SIZE or len(opposing) != TEAM_SIZE:
            # A forfeit at preview can leave one side unrecorded. Nothing to
            # learn from half a matchup.
            continue
        row = meta[replay_id]
        examples.append(
            PreviewExample(
                replay_id=replay_id,
                series_id=row["series_id"] or replay_id,
                side=side,
                player=row["p1"] if side == "p1" else row["p2"],
                opponent=row["p2"] if side == "p1" else row["p1"],
                rating=row["p1_rating"] if side == "p1" else row["p2_rating"],
                team=tuple(s["species"] for s in slots),
                opponent_team=tuple(s["species"] for s in opposing),
                brought=tuple(bool(s["appeared"]) for s in slots),
                led=tuple(bool(s["lead"]) for s in slots),
                won=row["winner_side"] == side,
                bring_observed=bool(row["bring_fully_observed"]),
            )
        )
    return examples


@dataclass(frozen=True, slots=True)
class Split:
    """A deterministic train/test split, grouped so teams cannot leak across it."""

    train: tuple[PreviewExample, ...]
    test: tuple[PreviewExample, ...]
    unseen_players: tuple[PreviewExample, ...] = field(default=())

    def summary(self) -> str:
        return (
            f"train {len(self.train)}, test {len(self.test)} "
            f"({len(self.unseen_players)} of them with two unseen players)"
        )


def _bucket(key: str, salt: str) -> float:
    """A stable [0, 1) from a string. Not `hash()`, which Python randomises."""
    digest = hashlib.sha256(f"{salt}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def split_examples(
    examples: Iterable[PreviewExample], test_fraction: float = 0.25, salt: str = "m4"
) -> Split:
    """Split by series, then mark the subset with no player seen in training.

    Splitting by replay would put game 1 of a best-of-three in training and game
    3 in test, with the same twelve Pokemon on both sides -- a memorisation test
    dressed up as a generalisation test. Grouping by series removes that.

    Player-level leakage survives it: someone laddering brings the same six for
    hours across unrelated series. Rather than throw away the data that causes
    it, the split reports `unseen_players` alongside, which is the honest number
    when the two disagree.
    """
    items = list(examples)
    train: list[PreviewExample] = []
    test: list[PreviewExample] = []
    for example in items:
        (test if _bucket(example.series_id, salt) < test_fraction else train).append(example)

    seen = {e.player for e in train} | {e.opponent for e in train}
    unseen = [e for e in test if e.player not in seen and e.opponent not in seen]
    return Split(train=tuple(train), test=tuple(test), unseen_players=tuple(unseen))


def subsets(n: int, size: int) -> list[tuple[int, ...]]:
    """Index subsets, in a fixed order. 15 of them for four of six."""
    from itertools import combinations

    return list(combinations(range(n), size))


def subset_index(chosen: Sequence[bool], size: int) -> int | None:
    """Which of the enumerated subsets a boolean mask corresponds to."""
    picked = tuple(i for i, flag in enumerate(chosen) if flag)
    if len(picked) != size:
        return None
    return subsets(len(chosen), size).index(picked)

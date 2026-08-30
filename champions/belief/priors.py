"""The learned prior the particle filter draws from.

`docs/03-belief-filter.md` section 3 asks for "a learned joint over (species,
ability, item, moveset), conditioned on the rest of the team", and warns that
independent marginals are wrong: sampling an item from one marginal and a
moveset from another produces sets no player would register.

The corpus makes the joint almost free. Every forced-open-sheet Bo3 replay
carries the complete registered set for all twelve Pokemon, so the empirical
distribution over *whole sets* is directly observable -- 37,284 of them across
238 species at the time of writing -- and a set drawn from it is coherent by
construction, because a real person registered it. That is a stronger guarantee
than any consistency check over marginals could give.

D33 puts nature in here too. `docs/05-data-pipeline.md` section 5 says stat
points and natures appear in no public dataset; the nature half is wrong, and
every one of those 37,284 sets is labelled with one. So nature is drawn from the
prior alongside item, ability and moves, and `champions/belief/spreads.py` is
left with stat points alone.

## Backoff, and why it is per-field

A species seen twice in the corpus has two observed sets, and drawing only from
those would make the filter certain about a Pokemon it has barely seen. So the
distribution over sets is mixed with a *composed* set built from that species'
per-field marginals, and the marginals themselves back off to the format-wide
marginal. The mixture weight falls as the species' count rises, so a heavily
played Pokemon is drawn from real sets and a rare one is drawn from something
plausible rather than from something memorised.

Composed sets are marked. A caller can tell whether a hypothesis came from a
registered team or from a mixture, which matters when reading the trace.

## Team conditioning

Full conditioning on the other five is not identifiable at this scale: the joint
over six species is far sparser than the corpus. What is identifiable is the
pairwise lift -- how much more often this set appears when that teammate is on
the team than at large -- and that is what `teammate_lift` supplies. It is a
score applied at sampling time rather than a distribution, deliberately: a lift
that is wrong reweights, where a badly estimated conditional distribution can
zero out the truth.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from champions.dex.loader import REPO_ROOT, to_id

PRIOR_DIR = REPO_ROOT / "data" / "priors"
PRIOR_VERSION = 1

#: How many observed sets a species needs before the prior stops leaning on
#: composed marginals. At `n` observed sets the mixture puts `n / (n + K)` on
#: the empirical distribution, so a species seen 32 times is 50/50 and one seen
#: 300 times is 90% empirical. Chosen so that the median species in the corpus
#: sits in the mixed regime rather than at either extreme.
BACKOFF_K = 32.0

#: Add-alpha smoothing on every categorical count, so a field value that exists
#: in the dex but never in the corpus still has non-zero probability. Without it
#: a revealed item nobody in the corpus ran would make every particle
#: inconsistent at once.
ALPHA = 0.5

#: Ceiling on the log of the teammate lift, so a species pair the corpus has
#: seen a handful of times cannot swing a particle's weight by more than about
#: two. The lift is a nudge from weak evidence; clipping keeps it one.
MAX_LOG_LIFT = 0.7

#: Moves are stored as a frozenset, not a tuple: the packed team format's order
#: is the order the player typed them in and carries no information, so two sets
#: differing only in move order are one set.
Moves = frozenset


@dataclass(frozen=True)
class SetHypothesis:
    """One complete hypothesised set for one species.

    Everything hidden about a Pokemon except its stat points, which
    `champions/belief/spreads.py` carries as an interval instead.
    """

    species: str
    item: str | None
    ability: str | None
    moves: frozenset[str]
    nature: str
    #: True when this came from per-field marginals rather than from a set some
    #: player actually registered. A composed set is plausible, not observed.
    composed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "species": self.species,
            "item": self.item,
            "ability": self.ability,
            "moves": sorted(self.moves),
            "nature": self.nature,
            "composed": self.composed,
        }


@dataclass
class SpeciesPrior:
    """Everything the corpus says about one species."""

    species: str
    #: The empirical distribution over whole registered sets.
    sets: list[tuple[SetHypothesis, int]] = field(default_factory=list)
    items: Counter[str] = field(default_factory=Counter)
    abilities: Counter[str] = field(default_factory=Counter)
    natures: Counter[str] = field(default_factory=Counter)
    moves: Counter[str] = field(default_factory=Counter)
    count: int = 0

    def empirical_weight(self) -> float:
        """How much of the mixture the observed sets get. See BACKOFF_K."""
        return self.count / (self.count + BACKOFF_K) if self.count else 0.0


def _normalise(counter: Mapping[str, float], alpha: float = ALPHA) -> dict[str, float]:
    total = sum(counter.values()) + alpha * max(len(counter), 1)
    return {key: (value + alpha) / total for key, value in counter.items()}


class SetPrior:
    """The prior over opponent sets, built from the corpus and read at runtime.

    Built offline by `scripts/build_priors.py` and serialised to JSON, so a live
    battle never touches SQLite. The artifact is content-addressed the same way
    the dex dump is, for the same reason: two builds that disagree should be two
    files rather than one file whose meaning depends on when it was written.
    """

    def __init__(
        self,
        species: dict[str, SpeciesPrior],
        pairs: dict[tuple[str, str], int] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.species = species
        self.pairs = pairs or {}
        self.meta = meta or {}
        self._global_items: Counter[str] = Counter()
        self._global_abilities: Counter[str] = Counter()
        self._global_natures: Counter[str] = Counter()
        self._global_moves: Counter[str] = Counter()
        for entry in species.values():
            self._global_items.update(entry.items)
            self._global_abilities.update(entry.abilities)
            self._global_natures.update(entry.natures)
            self._global_moves.update(entry.moves)
        self._species_counts = Counter({k: v.count for k, v in species.items()})
        self._total_sets = sum(self._species_counts.values())

    # -- building -------------------------------------------------------

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]]) -> SetPrior:
        """Build from `sets` rows: species, item, ability, moves, nature.

        Rows arrive grouped by `(replay_id, side)` so teammate pairs can be
        counted; a row with no group key contributes to the marginals only.
        """
        species: dict[str, SpeciesPrior] = {}
        pairs: Counter[tuple[str, str]] = Counter()
        teams: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
        observed = 0

        for row in rows:
            species_id = to_id(row.get("species"))
            if not species_id:
                continue
            moves = frozenset(to_id(m) for m in str(row.get("moves") or "").split(",") if to_id(m))
            nature = to_id(row.get("nature")) or "hardy"
            item = to_id(row.get("item")) or None
            ability = to_id(row.get("ability")) or None

            entry = species.setdefault(species_id, SpeciesPrior(species=species_id))
            entry.count += 1
            observed += 1
            if item:
                entry.items[item] += 1
            if ability:
                entry.abilities[ability] += 1
            entry.natures[nature] += 1
            entry.moves.update(moves)

            hypothesis = SetHypothesis(
                species=species_id,
                item=item,
                ability=ability,
                moves=moves,
                nature=nature,
            )
            entry.sets.append((hypothesis, 1))

            key = (str(row.get("replay_id") or ""), str(row.get("side") or ""))
            if key[0]:
                teams[key].append(species_id)

        # Collapse duplicate sets into counts. Registered teams repeat heavily
        # across a ladder, and a list with one entry per observation would be
        # 37k long for no gain.
        for entry in species.values():
            counted: Counter[SetHypothesis] = Counter()
            for hypothesis, weight in entry.sets:
                counted[hypothesis] += weight
            entry.sets = sorted(counted.items(), key=lambda kv: (-kv[1], _set_key(kv[0])))

        for members in teams.values():
            unique = sorted(set(members))
            for i, a in enumerate(unique):
                for b in unique[i + 1 :]:
                    pairs[(a, b)] += 1

        return cls(
            species=species,
            pairs=dict(pairs),
            meta={"version": PRIOR_VERSION, "sets": observed, "species": len(species)},
        )

    @classmethod
    def from_corpus(cls, database: Path | str, format_id: str | None = None) -> SetPrior:
        connection = sqlite3.connect(str(database))
        connection.row_factory = sqlite3.Row
        try:
            if format_id:
                cursor = connection.execute(
                    "SELECT s.* FROM sets s JOIN replays r ON r.id = s.replay_id "
                    "WHERE r.format_id = ?",
                    (format_id,),
                )
            else:
                cursor = connection.execute("SELECT * FROM sets")
            prior = cls.from_rows(dict(row) for row in cursor)
        finally:
            connection.close()
        prior.meta["source"] = str(database)
        prior.meta["format_id"] = format_id
        return prior

    # -- serialisation --------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "version": PRIOR_VERSION,
            "meta": self.meta,
            "species": {
                species_id: {
                    "count": entry.count,
                    "sets": [
                        [
                            hypothesis.item,
                            hypothesis.ability,
                            sorted(hypothesis.moves),
                            hypothesis.nature,
                            weight,
                        ]
                        for hypothesis, weight in entry.sets
                    ],
                    "items": dict(entry.items),
                    "abilities": dict(entry.abilities),
                    "natures": dict(entry.natures),
                    "moves": dict(entry.moves),
                }
                for species_id, entry in sorted(self.species.items())
            },
            "pairs": [[a, b, n] for (a, b), n in sorted(self.pairs.items())],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SetPrior:
        species: dict[str, SpeciesPrior] = {}
        for species_id, entry in data.get("species", {}).items():
            sets = [
                (
                    SetHypothesis(
                        species=species_id,
                        item=item,
                        ability=ability,
                        moves=frozenset(moves),
                        nature=nature,
                    ),
                    int(weight),
                )
                for item, ability, moves, nature, weight in entry.get("sets", [])
            ]
            species[species_id] = SpeciesPrior(
                species=species_id,
                sets=sets,
                items=Counter(entry.get("items", {})),
                abilities=Counter(entry.get("abilities", {})),
                natures=Counter(entry.get("natures", {})),
                moves=Counter(entry.get("moves", {})),
                count=int(entry.get("count", 0)),
            )
        pairs = {(a, b): int(n) for a, b, n in data.get("pairs", [])}
        return cls(species=species, pairs=pairs, meta=dict(data.get("meta", {})))

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, prior_dir: Path | str = PRIOR_DIR) -> SetPrior:
        """The one built prior. Ambiguity is an error, as it is for the dex."""
        prior_dir = Path(prior_dir)
        candidates = sorted(prior_dir.glob("setprior.*.json"))
        if not candidates:
            raise PriorNotBuiltError(
                f"No set prior in {prior_dir}. Build it with:\n    python scripts/build_priors.py"
            )
        if len(candidates) > 1:
            raise ValueError(
                f"Multiple set priors in {prior_dir}, ambiguous which is current: "
                f"{[p.name for p in candidates]}. Remove the stale ones."
            )
        return cls.from_json(json.loads(candidates[0].read_text(encoding="utf-8")))

    @classmethod
    def load_if_built(cls, prior_dir: Path | str = PRIOR_DIR) -> SetPrior | None:
        try:
            return cls.load(prior_dir)
        except (PriorNotBuiltError, ValueError, json.JSONDecodeError):
            return None

    # -- reading --------------------------------------------------------

    def knows(self, species: str) -> bool:
        return to_id(species) in self.species

    def observed_sets(self, species: str) -> list[tuple[SetHypothesis, int]]:
        entry = self.species.get(to_id(species))
        return list(entry.sets) if entry else []

    def marginals(self, species: str) -> dict[str, dict[str, float]]:
        """Per-field marginals for one species, backed off to the format-wide ones."""
        entry = self.species.get(to_id(species))
        return {
            "item": _normalise(entry.items if entry and entry.items else self._global_items),
            "ability": _normalise(
                entry.abilities if entry and entry.abilities else self._global_abilities
            ),
            "nature": _normalise(
                entry.natures if entry and entry.natures else self._global_natures
            ),
            "move": _normalise(entry.moves if entry and entry.moves else self._global_moves),
        }

    def probability(self, hypothesis: SetHypothesis) -> float:
        """The prior probability of one set: the same mixture `sample` draws from.

        Reported on the trace so a reader can tell a common set from a
        speculative one, and used by `champions/belief/evaluate.py` as the
        no-updating baseline `docs/03` section 5 requires.
        """
        entry = self.species.get(hypothesis.species)
        empirical = 0.0
        if entry and entry.count:
            total = sum(weight for _, weight in entry.sets)
            for observed, weight in entry.sets:
                if observed == hypothesis:
                    empirical = weight / total
                    break
        marginals = self.marginals(hypothesis.species)
        composed = _composed_probability(hypothesis, marginals)
        mix = entry.empirical_weight() if entry else 0.0
        return mix * empirical + (1.0 - mix) * composed

    def teammate_lift(self, species: str, teammates: Sequence[str]) -> float:
        """How much more often this species appears beside these teammates.

        A multiplicative score in log space, clipped, not a conditional
        distribution -- see the module docstring for why. Returns 1.0 when there
        is no evidence either way.
        """
        species_id = to_id(species)
        own = self._species_counts.get(species_id, 0)
        if not own or not self._total_sets:
            return 1.0
        total = 0.0
        seen = 0
        for teammate in teammates:
            other = to_id(teammate)
            if other == species_id:
                continue
            other_count = self._species_counts.get(other, 0)
            if not other_count:
                continue
            joint = self.pairs.get(_pair(species_id, other), 0)
            expected = own * other_count / self._total_sets
            if expected <= 0:
                continue
            total += math.log((joint + 1.0) / (expected + 1.0))
            seen += 1
        if not seen:
            return 1.0
        return float(math.exp(max(-MAX_LOG_LIFT, min(MAX_LOG_LIFT, total / seen))))


class PriorNotBuiltError(FileNotFoundError):
    pass


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _set_key(hypothesis: SetHypothesis) -> tuple:
    return (
        hypothesis.item or "",
        hypothesis.ability or "",
        tuple(sorted(hypothesis.moves)),
        hypothesis.nature,
    )


def _composed_probability(
    hypothesis: SetHypothesis,
    marginals: Mapping[str, Mapping[str, float]],
) -> float:
    """The probability of a set under independent per-field marginals.

    Wrong as a *sampler* -- that is the whole point of section 3 -- and adequate
    as a backoff density for a species the corpus has barely seen. The moveset
    term treats the four moves as an unordered draw without replacement, which
    is closer than a product of four independent draws and still cheap.
    """
    probability = marginals["item"].get(hypothesis.item or "", 1e-6)
    probability *= marginals["ability"].get(hypothesis.ability or "", 1e-6)
    probability *= marginals["nature"].get(hypothesis.nature, 1e-6)
    for move in hypothesis.moves:
        probability *= marginals["move"].get(move, 1e-6)
    return probability


def iter_corpus_sets(
    database: Path | str, format_id: str | None = None
) -> Iterator[dict[str, Any]]:
    """Rows from the corpus `sets` table, for callers that want them directly."""
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    try:
        if format_id:
            cursor = connection.execute(
                "SELECT s.* FROM sets s JOIN replays r ON r.id = s.replay_id WHERE r.format_id = ?",
                (format_id,),
            )
        else:
            cursor = connection.execute("SELECT * FROM sets")
        for row in cursor:
            yield dict(row)
    finally:
        connection.close()

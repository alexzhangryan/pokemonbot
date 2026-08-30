"""Measuring the filter on its own, not through downstream win rate.

`docs/03-belief-filter.md` section 5 lists four things, and this module produces
all four:

- **Negative log likelihood of the true set** under the belief at turn `t`,
  plotted against `t`, on held-out replays where the truth is eventually
  revealed. Forced-sheet Bo3 replays give the truth at turn 0, which makes them
  the cleanest evaluation set -- and the corpus is 4,000-odd of them.
- **Calibration**: bucket predicted probabilities and check the realised
  frequencies match.
- **Interval coverage** for the spread layer: the fraction of the time the true
  stat point value falls inside the maintained interval. Coverage below nominal
  means the quantization error term is too small and the filter is eliminating
  the truth. `CLAUDE.md` constraint 5 calls this the single most likely source
  of a silent correctness bug in the whole system.
- **Baseline to beat**: usage-frequency marginals with no in-battle updating,
  which is exactly the belief at turn 1, so it comes free.

## Two evaluation sets, because neither is sufficient alone

Open-sheet replays reveal item, ability, moves and nature -- but never stat
points, which is the finding D33 corrected `docs/05` on. So the corpus can
measure everything except the one metric the spread layer exists to be judged
by.

Self-play can. Both teams are files we hold, so the true point allocation is
known exactly, and there is no skill confound because both sides are the same
agent. So coverage is measured against self-play traces and the categorical
metrics against the corpus, and each says so rather than pretending to cover
the other.

That split is the same conclusion M4 reached from the other direction: the
corpus cannot answer a question that turns on unobserved fields, and self-play
can (D39).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from champions.belief.filter import BattleBelief
from champions.belief.priors import SetHypothesis, SetPrior
from champions.corpus.replay import ReplayRecord, parse_replay
from champions.dex.loader import Dex, to_id
from champions.dex.stats import STAT_IDS
from champions.protocol import parser

#: Probability floor when the belief puts no mass at all on the truth. A zero
#: would make the mean log loss infinite and hide everything else in the table,
#: so it is reported as a bounded miss plus an explicit count of how often it
#: happened -- which is the number that actually matters.
FLOOR = 1e-4


@dataclass
class FieldScore:
    """Accuracy and log loss for one categorical field."""

    n: int = 0
    correct: int = 0
    log_loss: float = 0.0
    misses: int = 0

    def add(self, probability: float, correct: bool) -> None:
        self.n += 1
        self.correct += int(correct)
        if probability <= 0.0:
            self.misses += 1
        self.log_loss += -math.log(max(probability, FLOOR))

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0

    @property
    def mean_log_loss(self) -> float:
        return self.log_loss / self.n if self.n else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "accuracy": round(self.accuracy, 4),
            "log_loss": round(self.mean_log_loss, 4),
            "zero_mass": self.misses,
        }


@dataclass
class CoverageScore:
    """Interval coverage and width for the spread layer."""

    n: int = 0
    covered: int = 0
    width: int = 0

    def add(self, low: int, high: int, truth: int) -> None:
        self.n += 1
        self.covered += int(low <= truth <= high)
        self.width += high - low

    @property
    def coverage(self) -> float:
        return self.covered / self.n if self.n else 0.0

    @property
    def mean_width(self) -> float:
        return self.width / self.n if self.n else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "coverage": round(self.coverage, 4),
            "mean_width": round(self.mean_width, 2),
        }


@dataclass
class BeliefScore:
    """Everything measured at one point in a battle, aggregated over battles."""

    item: FieldScore = field(default_factory=FieldScore)
    ability: FieldScore = field(default_factory=FieldScore)
    nature: FieldScore = field(default_factory=FieldScore)
    moves: FieldScore = field(default_factory=FieldScore)
    whole_set: FieldScore = field(default_factory=FieldScore)
    #: Coverage of the box the search actually reads: the modal particle's.
    coverage: CoverageScore = field(default_factory=CoverageScore)
    #: Coverage of the union across live particles. Almost always covering, so
    #: it is a sanity check rather than the metric -- if this one ever drops
    #: below nominal, the filter has eliminated the truth outright.
    coverage_union: CoverageScore = field(default_factory=CoverageScore)
    #: (predicted probability, realised outcome) for the calibration buckets.
    calibration: list[tuple[float, int]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "item": self.item.as_dict(),
            "ability": self.ability.as_dict(),
            "nature": self.nature.as_dict(),
            "moves": self.moves.as_dict(),
            "whole_set": self.whole_set.as_dict(),
            "coverage": self.coverage.as_dict(),
            "coverage_union": self.coverage_union.as_dict(),
            "calibration": calibration_table(self.calibration),
        }


def calibration_table(
    pairs: Sequence[tuple[float, int]],
    buckets: int = 10,
) -> list[dict[str, Any]]:
    """Predicted probability against realised frequency, in equal-width buckets.

    Perfect calibration puts `realised` on the bucket midpoint. Systematically
    above means the belief is under-confident and below means over-confident,
    and over-confident is the direction that hurts: the search will commit to a
    hypothesis the filter has no right to be sure of.
    """
    table: list[dict[str, Any]] = []
    for index in range(buckets):
        low, high = index / buckets, (index + 1) / buckets
        inside = [
            outcome
            for probability, outcome in pairs
            if low <= probability < high or (index == buckets - 1 and probability == 1.0)
        ]
        if not inside:
            continue
        table.append(
            {
                "bucket": f"{low:.1f}-{high:.1f}",
                "n": len(inside),
                "realised": round(sum(inside) / len(inside), 4),
            }
        )
    return table


# ---------------------------------------------------------------- truth


@dataclass(frozen=True)
class TruthSet:
    """The registered set for one Pokemon. What the belief is scored against."""

    species: str
    item: str | None
    ability: str | None
    moves: frozenset[str]
    nature: str | None
    #: Stat points, present only for a team whose file we hold. Open-sheet
    #: replays never carry them (D33 corrected the nature half of that; the
    #: points half stands).
    points: Mapping[str, int] | None = None


def truth_from_replay(record: ReplayRecord, side: str) -> dict[str, TruthSet]:
    return {
        to_id(s.species): TruthSet(
            species=to_id(s.species),
            item=to_id(s.item) or None,
            ability=to_id(s.ability) or None,
            moves=frozenset(to_id(m) for m in s.moves if to_id(m)),
            nature=to_id(s.nature) or None,
        )
        for s in record.sets
        if s.side == side
    }


def truth_from_team_file(text: str) -> dict[str, TruthSet]:
    """Parse a Showdown export into truth sets, stat points included.

    The export format is the only source in the project that carries a point
    allocation, which is why coverage can only be measured against teams we
    wrote. Deliberately tolerant: an unrecognised line is skipped rather than
    raising, because the file is a human artifact.
    """
    truths: dict[str, TruthSet] = {}
    for block in text.split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        head, _, item = lines[0].partition("@")
        species = to_id(head.split("(")[0])
        if not species:
            continue
        ability = nature = None
        moves: list[str] = []
        points: dict[str, int] = dict.fromkeys(STAT_IDS, 0)
        for line in lines[1:]:
            if line.startswith("Ability:"):
                ability = to_id(line.split(":", 1)[1])
            elif line.endswith("Nature"):
                nature = to_id(line.rsplit(" ", 1)[0])
            elif line.startswith("EVs:"):
                for chunk in line.split(":", 1)[1].split("/"):
                    parts = chunk.split()
                    if len(parts) == 2 and parts[0].isdigit():
                        stat = _STAT_LABELS.get(parts[1])
                        if stat:
                            points[stat] = int(parts[0])
            elif line.startswith("-"):
                moves.append(to_id(line[1:]))
        truths[_base_species(species)] = TruthSet(
            species=_base_species(species),
            item=to_id(item) or None,
            ability=ability,
            moves=frozenset(moves),
            nature=nature,
            points=points,
        )
    return truths


#: Showdown's export stat labels. `Spd` means Speed and `SpD` means Special
#: Defence -- a case sensitive legacy alias that M1 already had to find the hard
#: way (`sim/dex-data.ts:419`), and which silently writes points onto the wrong
#: stat if it is normalised away.
_STAT_LABELS = {
    "HP": "hp",
    "Atk": "atk",
    "Def": "def",
    "SpA": "spa",
    "SpD": "spd",
    "Spe": "spe",
    "Spd": "spe",
}

#: Formes a team file may name that the belief keys on the base species instead.
_FORME_SUFFIXES = ("mega", "megax", "megay", "primal")


def _base_species(species: str) -> str:
    for suffix in _FORME_SUFFIXES:
        if species.endswith(suffix) and len(species) > len(suffix):
            return species[: -len(suffix)]
    return species


# ---------------------------------------------------- scoring a summary


def score_summary(
    summary: Mapping[str, Any],
    truth: Mapping[str, TruthSet],
    score: BeliefScore | None = None,
) -> BeliefScore:
    """Score one `belief` trace payload against the truth.

    Reads the emitted summary rather than re-running the filter, so what is
    measured is exactly what the agent believed at the moment it decided --
    which is the only version of the belief that affected a game.
    """
    score = score or BeliefScore()
    for entry in summary.get("team", []):
        species = to_id(entry.get("species"))
        true_set = truth.get(species)
        if true_set is None:
            continue

        _score_field(score.item, entry.get("item"), true_set.item, score.calibration)
        _score_field(score.ability, entry.get("ability"), true_set.ability, score.calibration)
        _score_field(score.nature, entry.get("nature"), true_set.nature, score.calibration)

        ranked_moves = entry.get("moves") or []
        believed = {e["value"]: e["probability"] for e in ranked_moves}
        # A Pokemon has four moves, so "is it in the believed top four" is the
        # question a search asks -- the columns of the payoff matrix are drawn
        # from exactly that set. Scoring a move as correct only above 50%
        # probability would report 0% for a filter that had ranked all four
        # right, which is what an earlier version of this did.
        top_four = {e["value"] for e in ranked_moves[:4]}
        for move in true_set.moves:
            probability = float(believed.get(move, 0.0))
            score.moves.add(probability, move in top_four)
            score.calibration.append((probability, 1))

        exact = 0.0
        for candidate in entry.get("sets", []):
            if _matches(candidate, true_set):
                exact = float(candidate.get("probability", 0.0))
                break
        score.whole_set.add(exact, exact >= 0.5)

        if true_set.points is not None:
            for target, key in (
                (score.coverage, "points_modal"),
                (score.coverage_union, "points"),
            ):
                table = entry.get(key) or {}
                for stat in STAT_IDS:
                    bounds = table.get(stat)
                    if bounds:
                        target.add(int(bounds[0]), int(bounds[1]), int(true_set.points[stat]))
    return score


def _score_field(
    field_score: FieldScore,
    ranked: Sequence[Mapping[str, Any]] | None,
    truth: str | None,
    calibration: list[tuple[float, int]],
) -> None:
    if truth is None:
        return
    ranked = ranked or []
    probability = 0.0
    for entry in ranked:
        if entry.get("value") == truth:
            probability = float(entry.get("probability", 0.0))
            break
    top = ranked[0]["value"] if ranked else None
    field_score.add(probability, top == truth)
    for entry in ranked:
        calibration.append((float(entry.get("probability", 0.0)), int(entry.get("value") == truth)))


def _matches(candidate: Mapping[str, Any], truth: TruthSet) -> bool:
    return (
        candidate.get("item") == truth.item
        and candidate.get("ability") == truth.ability
        and frozenset(candidate.get("moves") or ()) == truth.moves
        and candidate.get("nature") == truth.nature
    )


# ------------------------------------------------------- trace scoring


def score_trace(
    path: Path | str,
    truth: Mapping[str, TruthSet],
    turns: Sequence[int] | None = None,
) -> dict[int, BeliefScore]:
    """Score every `belief` event in one trace, keyed by turn.

    Keyed by turn because the headline plot in `docs/03` section 5 is NLL
    against `t`: a filter that is right at turn 12 and wrong at turn 1 is a
    different thing from one that is right throughout, and only the second is
    worth anything to a search that has to decide on turn 1.
    """
    by_turn: dict[int, BeliefScore] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") != "belief":
            continue
        payload = event.get("payload") or {}
        turn = int(payload.get("turn", 0))
        if turns is not None and turn not in turns:
            continue
        score_summary(payload, truth, by_turn.setdefault(turn, BeliefScore()))
    return by_turn


# ------------------------------------------------------ corpus scoring


def replay_belief_curve(
    log: str,
    prior: SetPrior,
    dex: Dex,
    side: str,
    n_particles: int = 32,
    seed: int = 0,
) -> list[tuple[int, BeliefScore]]:
    """Run the filter over a stored replay from one player's viewpoint.

    The evaluation set `docs/03` section 5 calls the cleanest: a forced-open-
    sheet Bo3 replay states the truth at turn 0 and then plays a game the filter
    can be run over without ever being shown it.

    One asymmetry is unavoidable and worth stating. A replay log is the
    spectator view, which shows both sides' exact HP, where a live agent sees
    the opponent's as a percentage. That makes the damage inference here
    slightly *better* informed than in a real battle, so the coverage measured
    from self-play traces -- which use the real, quantized view -- is the one to
    trust on the spread layer.
    """
    record = parse_replay("eval", log)
    truth = truth_from_replay(record, side)
    opponent_side = "p2" if side == "p1" else "p1"
    ours = [p.species for p in record.previews if p.side == side]
    theirs = [p.species for p in record.previews if p.side == opponent_side]
    if not theirs or not truth:
        return []

    belief = BattleBelief(
        dex=dex,
        prior=prior,
        opponent_species=theirs,
        player_role=side,
        n_particles=n_particles,
        seed=seed,
    )
    truth_theirs = truth_from_replay(record, opponent_side)
    snapshot = _spectator_snapshot(record, side, ours, dex)

    curve: list[tuple[int, BeliefScore]] = []
    state = parser.ParserState()
    pending: list[parser.Observation] = []
    turn = 0
    for line in log.splitlines():
        observations = parser.apply(state, line)
        pending.extend(observations)
        if line.startswith("|turn|"):
            belief.update(pending, snapshot)
            pending = []
            turn = state.turn
            curve.append((turn, score_summary(belief.summary(), truth_theirs)))
    if pending:
        belief.update(pending, snapshot)
        curve.append((turn + 1, score_summary(belief.summary(), truth_theirs)))
    return curve


def _spectator_snapshot(
    record: ReplayRecord,
    side: str,
    ours: Sequence[str],
    dex: Dex,
) -> dict[str, Any]:
    """A minimal snapshot of our own side, built from the replay's team sheet.

    Enough for the filter's `BeliefContext`: exact stats for whichever of our
    Pokemon an inequality is about. Stat points are not in the sheet, so the
    neutral all-zero allocation is used and the resulting Speed and damage
    inferences are correspondingly approximate. That is a real limitation of
    the corpus evaluation and it is why the categorical metrics are the ones
    reported from it.
    """
    from champions.dex.stats import StatSpread, stats_for_species

    active: list[dict[str, Any] | None] = []
    for species in ours[:2]:
        species_id = to_id(species)
        entry = dex.species.get(species_id)
        if entry is None:
            active.append(None)
            continue
        stats = stats_for_species(species_id, StatSpread(points={}, nature="hardy"), dex)
        active.append(
            {
                "species": species_id,
                "known": True,
                "types": list(entry.get("types") or []),
                "base_stats": dict(entry.get("baseStats") or {}),
                "stats": stats,
                "hp": stats["hp"],
                "max_hp": stats["hp"],
                "hp_pct": 100.0,
                "status": None,
                "boosts": {},
                "item": None,
                "ability": None,
                "moves": [],
                "fainted": False,
            }
        )
    return {
        "ours": {"active": active, "bench": [], "remaining": len(active), "revealed": len(active)},
        "theirs": {"active": [], "bench": [], "remaining": 0, "revealed": 0},
        "side_conditions": {},
        "opponent_side_conditions": {},
        "fields": {},
        "weather": {},
    }


def merge(scores: Iterable[BeliefScore]) -> BeliefScore:
    """Sum a collection of scores into one."""
    total = BeliefScore()
    for score in scores:
        for name in ("item", "ability", "nature", "moves", "whole_set"):
            source: FieldScore = getattr(score, name)
            target: FieldScore = getattr(total, name)
            target.n += source.n
            target.correct += source.correct
            target.log_loss += source.log_loss
            target.misses += source.misses
        for name in ("coverage", "coverage_union"):
            source_coverage: CoverageScore = getattr(score, name)
            target_coverage: CoverageScore = getattr(total, name)
            target_coverage.n += source_coverage.n
            target_coverage.covered += source_coverage.covered
            target_coverage.width += source_coverage.width
        total.calibration.extend(score.calibration)
    return total


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A Wilson interval, as `champions/harness/elo.py` uses for win rates.

    The normal approximation leaves [0, 1] at the extremes, and coverage is
    supposed to sit near 1.0, which is exactly where it breaks.
    """
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def bootstrap_hypotheses(prior: SetPrior, species: str, n: int = 5) -> list[SetHypothesis]:
    """The `n` most probable sets for a species under the prior alone.

    The no-updating baseline `docs/03` section 5 requires, in the form the rest
    of this module scores.
    """
    observed = prior.observed_sets(species)
    return [hypothesis for hypothesis, _ in observed[:n]]


def summarise(scores: Mapping[int, BeliefScore]) -> dict[str, Any]:
    """The per-turn table, plus the aggregate. What the script prints."""
    turns = sorted(scores)
    return {
        "by_turn": [{"turn": turn, **scores[turn].as_dict()} for turn in turns],
        "overall": merge(scores.values()).as_dict(),
        "first_turn": scores[turns[0]].as_dict() if turns else {},
        "last_turn": scores[turns[-1]].as_dict() if turns else {},
    }


def turn_curve(scores: Mapping[int, BeliefScore], attribute: str) -> np.ndarray:
    """One metric against turn, as an array, for whoever wants to plot it."""
    turns = sorted(scores)
    return np.array(
        [[turn, getattr(scores[turn], attribute).mean_log_loss] for turn in turns],
        dtype=float,
    )

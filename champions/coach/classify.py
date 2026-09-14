"""Move classification and tags: `docs/specs/2026-09-13-coach.md` section 4.

Every threshold the coach applies lives here and nowhere else, in
win-probability points. They are **hand-set**. `docs/06-coach-and-evaluation.md`
section 2 says they should be calibrated empirically against rating bands,
because a five-point loss means something different in a close game than in a
decided one; that calibration needs the replay corpus and its ratings, which
are not on this machine, and is the first follow-up the spec names. Until it
runs, these are the bands, and the report says so.

The functions are pure so that `tests/test_coach.py` can walk every branch of
the rule on synthetic numbers, which is what keeps the rule in the spec and
the rule in the run from drifting apart.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from champions.formats import FORMAT_ID, lender
from champions.search.matrix import MASS_THRESHOLD

#: An ex-ante loss within this of zero is zero: the row is as good as the
#: equilibrium's, whether or not the LP happened to weight it.
LOSS_EPS = 1e-6

#: Off-support loss bands, hand-set. Below `INACCURACY` is an inaccuracy,
#: below `MISTAKE` a mistake, at or above it a blunder. `load_bands` replaces
#: them with the calibrated ones when `scripts/calibrate_coach.py` has written
#: a file, the same arrangement `search.evaluate` uses for its weights: the
#: calibrated numbers cannot be claimed without the run that measured them.
INACCURACY = 0.05
MISTAKE = 0.15

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "eval"


def BANDS_PATH(format_id: str) -> Path:  # noqa: N802 - a path, named like one
    return DATA_DIR / f"coach-bands.{format_id}.json"


@dataclass(frozen=True)
class Bands:
    """The two cutoffs that turn an off-support loss into a label."""

    inaccuracy: float = INACCURACY
    mistake: float = MISTAKE
    #: "hand-set", or what the calibration wrote (its source and sample).
    source: str = "hand-set"

    def as_dict(self) -> dict[str, float | str]:
        return asdict(self)


HAND_SET = Bands()


def load_bands(format_id: str = FORMAT_ID) -> Bands:
    """The calibrated bands for a format, or the hand-set ones."""
    path = BANDS_PATH(format_id)
    if not path.is_file():
        # Borrow the predecessor's bands, marked (`champions.formats`, D80).
        previous = lender(format_id)
        if previous is not None and BANDS_PATH(previous).is_file():
            path = BANDS_PATH(previous)
    if not path.is_file():
        return HAND_SET
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Bands(
            inaccuracy=float(raw["inaccuracy"]),
            mistake=float(raw["mistake"]),
            source=str(raw.get("source") or f"calibrated ({path.name})"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return HAND_SET


BANDS = load_bands()

#: Forced: the equilibrium is pure and every other row is at least this far
#: below the game value against the equilibrium column mix.
FORCED_GAP = 0.05

#: Read: the opponent's played column minimises the played row and costs at
#: least this much against what the row was worth under the mix.
READ_COST = 0.03

#: Gamble: the played row carried less than this weight and its ex-post cell
#: beat the game value by at least `GAMBLE_GAIN`.
GAMBLE_WEIGHT = 0.10
GAMBLE_GAIN = 0.03

#: Unlucky and Lucky: the realised position sits at least this far below
#: (above) the played cell's expected value.
LUCK = 0.10

BEST = "best"
SOLID = "solid"
INACCURACY_LABEL = "inaccuracy"
MISTAKE_LABEL = "mistake"
BLUNDER = "blunder"

FORCED = "forced"
READ = "read"
GAMBLE = "gamble"
UNLUCKY = "unlucky"
LUCKY = "lucky"

LABELS = (BEST, SOLID, INACCURACY_LABEL, MISTAKE_LABEL, BLUNDER)
TAGS = (FORCED, READ, GAMBLE, UNLUCKY, LUCKY)


def on_support(weight: float, loss: float) -> bool:
    """Whether a row is as good as the equilibrium's.

    Either clause suffices. The weight clause is the pruning guard's definition
    of "carries mass" (`matrix.MASS_THRESHOLD`), so the coach and the guard
    agree on what the support is. The loss clause is for the rows the LP did
    not weight but could have: a matrix game can have many equilibria, and the
    solver returns one.
    """
    return weight > MASS_THRESHOLD or loss <= LOSS_EPS


def classify(weight: float, loss: float, max_weight: float, bands: Bands | None = None) -> str:
    """The label for a played row, from its equilibrium weight and ex-ante loss.

    `max_weight` is the largest row weight in the equilibrium; a row carrying
    it is Best, and there is exactly one such row unless the LP split the
    weight exactly, in which case each is. `bands` defaults to the loaded
    ones, calibrated or hand-set.
    """
    bands = bands or BANDS
    if on_support(weight, loss):
        return BEST if weight >= max_weight - LOSS_EPS and weight > MASS_THRESHOLD else SOLID
    if loss < bands.inaccuracy:
        return INACCURACY_LABEL
    if loss < bands.mistake:
        return MISTAKE_LABEL
    return BLUNDER


def is_forced(is_pure: bool, ante_values: Sequence[float], game_value: float) -> bool:
    """A pure equilibrium whose alternatives all lose at least `FORCED_GAP`."""
    if not is_pure:
        return False
    values = np.asarray(ante_values, dtype=float)
    if values.size < 2:
        return False
    others = np.delete(values, int(np.argmax(values)))
    return bool(np.all(others <= game_value - FORCED_GAP))


def is_read(
    support: bool,
    row_values: Sequence[float],
    their_column: int,
    ante_value: float,
) -> bool:
    """The opponent picked the specific counter to a row that was a fine choice.

    `row_values` is the played row across every column of the ex-ante matrix,
    `their_column` the index of what they actually did, and `ante_value` what
    the row was worth against the equilibrium mix.
    """
    if not support:
        return False
    values = np.asarray(row_values, dtype=float)
    if their_column < 0 or their_column >= values.size:
        return False
    hit = float(values[their_column])
    return hit <= float(values.min()) + LOSS_EPS and hit <= ante_value - READ_COST


def is_gamble(weight: float, post_cell: float, game_value: float) -> bool:
    """A low-weight row that paid off against what the opponent actually did."""
    return weight < GAMBLE_WEIGHT and post_cell >= game_value + GAMBLE_GAIN


def is_unlucky(label: str, luck: float) -> bool:
    """A reasonable decision whose realised position fell well short of its cell.

    `luck` is expected minus realised, so positive is bad for us.
    """
    return label in (BEST, SOLID, INACCURACY_LABEL) and luck >= LUCK


def is_lucky(luck: float) -> bool:
    return luck <= -LUCK


def tags(
    *,
    label: str,
    weight: float,
    loss: float,
    is_pure: bool,
    ante_values: Sequence[float],
    game_value: float,
    row_values: Sequence[float] | None,
    their_column: int | None,
    ante_value: float,
    post_cell: float | None,
    luck: float | None,
) -> list[str]:
    """Every tag of section 4 that applies, in the table's order."""
    out: list[str] = []
    if is_forced(is_pure, ante_values, game_value):
        out.append(FORCED)
    support = on_support(weight, loss)
    if (
        row_values is not None
        and their_column is not None
        and is_read(support, row_values, their_column, ante_value)
    ):
        out.append(READ)
    if post_cell is not None and is_gamble(weight, post_cell, game_value):
        out.append(GAMBLE)
    if luck is not None:
        if is_unlucky(label, luck):
            out.append(UNLUCKY)
        if is_lucky(luck):
            out.append(LUCKY)
    return out

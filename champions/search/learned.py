"""Implementation B: a candidate prior fit to what strong humans played.

`docs/04-decision-engine.md` section 3 names three candidate providers and
`docs/specs/2026-08-29-learned-policy-provider.md` specifies this one. A small
network scores a single (state, option) pair to a scalar; the softmax over one
slot's legal set is the model's distribution over that slot's choices; the loss
is cross entropy against what the human played.

## Why one option at a time

The alternative is a fixed action space -- a vector of logits, one per possible
action, padded to the largest legal set. That is the standard shape and it is
the wrong one here. A slot has three legal options in one position and fourteen
in another, the options are not the same actions between positions (Iron Head at
foe slot 1 is a different row from Iron Head at foe slot 2 and neither exists
when Metagross is not out), and the padding would have to carry a mask that the
loss then has to honour. Scoring one option at a time and normalising over the
set has none of that: the same weights serve every arity, and an option the
model has never seen still gets a score because the score is a function of its
features rather than of its index.

The cost is that options cannot see each other. That is the same limitation
implementation A has and the spec keeps it deliberately (section 6): a joint
action scores as the sum of its two slots, so a double Protect and a Protect
plus an attack are indistinguishable to both providers, and the comparison
between them is therefore about the ranking rather than about two different
formulations of the problem.

## Why numpy and L-BFGS rather than a framework

The project already fits `champions/search/fit.py` this way, the parameter count
here is in the hundreds, and full-batch L-BFGS on a few hundred thousand rows
takes about a minute. What that buys is the property `CLAUDE.md` asks for
without any further care: the fit is a deterministic function of the seed, with
no shuffling, no dropout and no device-dependent reduction order to make two
runs of the same command disagree.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

from champions.dex.loader import Dex
from champions.search.policy import DEFAULT_K, ScoredAction
from champions.search.policy_data import Decision
from champions.search.policy_features import board_for, option_features

#: Where `scripts/fit_policy.py` writes the fitted prior, and where the provider
#: looks for it.
MODEL_DIR = Path("data/policy")

#: Hidden units. Small on purpose: 34 features and a few hundred thousand rows
#: do not need capacity, and the thing being asked of the layer is that damage
#: and health interact rather than that a large function be memorised.
DEFAULT_HIDDEN = 16

#: L2 on both weight matrices, not on the bias. Same role as
#: `fit.DEFAULT_L2` -- it keeps a nearly-constant feature from acquiring a large
#: weight, and it is not model selection.
DEFAULT_L2 = 1e-4

#: L-BFGS iterations. The loss is flat well before this on every fit measured so
#: far; the cap is here so a pathological corpus cannot run without end.
DEFAULT_MAX_ITER = 400

#: Train, then validation, then test, by player. Validation is carved out for
#: the same reason M6 carves one out: something has to choose between fits
#: without touching the number that gets reported.
DEFAULT_FRACTIONS = (0.7, 0.15, 0.15)

#: Below this, a feature is treated as constant and left unscaled rather than
#: divided by its own noise.
MIN_SCALE = 1e-8


@dataclass(frozen=True)
class PolicyDataset:
    """Every option of every decision, stacked, with the group boundaries.

    Flat rather than a list of arrays. One matrix multiply over the whole corpus
    is what makes full-batch L-BFGS viable at all, and the group structure --
    which rows compete with which -- is carried alongside as sizes rather than
    inside the array.
    """

    #: `(n_options_total, n_features)`, groups contiguous and in order.
    x: np.ndarray
    #: `(n_decisions,)`, how many options each decision had.
    sizes: np.ndarray
    #: `(n_decisions,)`, the chosen option's index *within* its group.
    chosen: np.ndarray
    #: `(n_decisions,)`, the replay each decision came from. The unit every
    #: interval is resampled over.
    battle: np.ndarray
    #: `(n_decisions,)`, who made it. The unit the split holds out.
    player: np.ndarray
    #: `(n_decisions,)`, whether that replay was open-sheet.
    sheets: np.ndarray
    feature_names: tuple[str, ...]

    def __len__(self) -> int:
        return int(self.sizes.shape[0])

    @property
    def starts(self) -> np.ndarray:
        """Where each group begins in `x`."""
        out = np.zeros(len(self), dtype=int)
        np.cumsum(self.sizes[:-1], out=out[1:])
        return out

    @property
    def chosen_rows(self) -> np.ndarray:
        """The chosen option's index in `x` rather than in its group."""
        return self.starts + self.chosen

    @property
    def n_battles(self) -> int:
        return int(np.unique(self.battle).size)

    def subset(self, keep: np.ndarray) -> PolicyDataset:
        """The decisions `keep` selects, whole.

        Whole because a group is one slot's entire legal set: half of one is not
        a decision, and a softmax over half of one is a different quantity from
        the one the model was fit to produce.
        """
        keep = np.asarray(keep, dtype=bool)
        rows = np.repeat(keep, self.sizes)
        return PolicyDataset(
            x=self.x[rows],
            sizes=self.sizes[keep],
            chosen=self.chosen[keep],
            battle=self.battle[keep],
            player=self.player[keep],
            sheets=self.sheets[keep],
            feature_names=self.feature_names,
        )

    @classmethod
    def build(
        cls,
        decisions: Iterable[Decision],
        feature_names: Sequence[str],
        baseline: Any = None,
    ) -> tuple[PolicyDataset, np.ndarray]:
        """Accumulate a dataset out of the corpus, one decision at a time.

        Returns the dataset and the baseline provider's score for every row, so
        that A and B are measured on exactly the positions and choice sets --
        one pass, one reconstruction, no chance of the two drifting apart. The
        baseline is a callable taking a `Decision` and returning one score per
        option; `None` gives an array of zeroes, which every recall reads as an
        arbitrary ordering rather than as a provider.
        """
        x: list[np.ndarray] = []
        sizes: list[int] = []
        chosen: list[int] = []
        battle: list[str] = []
        player: list[str] = []
        sheets: list[bool] = []
        scores: list[np.ndarray] = []

        for decision in decisions:
            x.append(decision.features)
            sizes.append(len(decision.options))
            chosen.append(decision.chosen)
            battle.append(decision.battle_id)
            player.append(decision.player)
            sheets.append(decision.sheets_revealed)
            scores.append(
                np.zeros(len(decision.options))
                if baseline is None
                else np.asarray(baseline(decision), dtype=float)
            )

        empty = np.zeros((0, len(feature_names)))
        return (
            cls(
                x=np.concatenate(x) if x else empty,
                sizes=np.array(sizes, dtype=int),
                chosen=np.array(chosen, dtype=int),
                battle=np.array(battle, dtype=object),
                player=np.array(player, dtype=object),
                sheets=np.array(sheets, dtype=bool),
                feature_names=tuple(feature_names),
            ),
            np.concatenate(scores) if scores else np.zeros(0),
        )


@dataclass
class PolicyModel:
    """Standardisation, one hidden layer, one output. No output bias.

    An output bias would be added to every option of every group and cancel in
    the softmax, so it is not a parameter -- it is a direction the optimiser can
    wander in without changing the loss.
    """

    feature_names: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray
    w1: np.ndarray
    b1: np.ndarray
    w2: np.ndarray
    l2: float = DEFAULT_L2
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def hidden(self) -> int:
        return int(self.b1.shape[0])

    def score(self, x: np.ndarray) -> np.ndarray:
        """One logit per row, in the row order given."""
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.feature_names):
            raise ValueError(f"expected (n, {len(self.feature_names)}) features, got {x.shape}")
        return np.tanh((x - self.mean) / self.scale @ self.w1 + self.b1) @ self.w2

    def as_json(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "hidden": self.hidden,
            "l2": self.l2,
            "mean": [round(float(v), 8) for v in self.mean],
            "scale": [round(float(v), 8) for v in self.scale],
            "w1": [[round(float(v), 8) for v in row] for row in self.w1],
            "b1": [round(float(v), 8) for v in self.b1],
            "w2": [round(float(v), 8) for v in self.w2],
            "metrics": self.metrics,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PolicyModel:
        return cls(
            feature_names=tuple(payload["feature_names"]),
            mean=np.array(payload["mean"], dtype=float),
            scale=np.array(payload["scale"], dtype=float),
            w1=np.array(payload["w1"], dtype=float),
            b1=np.array(payload["b1"], dtype=float),
            w2=np.array(payload["w2"], dtype=float),
            l2=float(payload.get("l2", DEFAULT_L2)),
            metrics=dict(payload.get("metrics", {})),
        )


# -- the fit -----------------------------------------------------------------


def fit(
    data: PolicyDataset,
    hidden: int = DEFAULT_HIDDEN,
    l2: float = DEFAULT_L2,
    seed: int = 0,
    max_iter: int = DEFAULT_MAX_ITER,
) -> PolicyModel:
    """Grouped softmax cross entropy, minimised full batch by L-BFGS."""
    mean = data.x.mean(axis=0) if len(data.x) else np.zeros(data.x.shape[1])
    spread = data.x.std(axis=0) if len(data.x) else np.zeros(data.x.shape[1])
    scale = np.where(spread < MIN_SCALE, 1.0, spread)
    xs = (data.x - mean) / scale

    n_features = xs.shape[1]
    rng = np.random.default_rng(seed)
    # Scaled so that the pre-activation of a standardised input lands inside
    # tanh's linear region rather than saturated, which is where a saturated
    # start would leave the gradient at zero.
    theta0 = np.concatenate(
        [
            rng.normal(0.0, 1.0 / np.sqrt(n_features), n_features * hidden),
            np.zeros(hidden),
            rng.normal(0.0, 1.0 / np.sqrt(hidden), hidden),
        ]
    )

    starts, sizes = data.starts, data.sizes
    chosen_rows = data.chosen_rows
    n_groups = max(1, len(data))

    def unpack(theta: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        cut = n_features * hidden
        return (
            theta[:cut].reshape(n_features, hidden),
            theta[cut : cut + hidden],
            theta[cut + hidden :],
        )

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        w1, b1, w2 = unpack(theta)
        h = np.tanh(xs @ w1 + b1)
        z = h @ w2

        probs, log_z = _softmax(z, starts, sizes)
        loss = float(-(z[chosen_rows] - log_z).sum() / n_groups)
        loss += 0.5 * l2 * (float(np.sum(w1 * w1)) + float(np.sum(w2 * w2)))

        dz = probs / n_groups
        dz[chosen_rows] -= 1.0 / n_groups
        dw2 = h.T @ dz + l2 * w2
        da = (dz[:, None] * w2) * (1.0 - h * h)
        dw1 = xs.T @ da + l2 * w1
        db1 = da.sum(axis=0)
        return loss, np.concatenate([dw1.ravel(), db1, dw2])

    result = minimize(
        objective,
        theta0,
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": max_iter},
    )
    w1, b1, w2 = unpack(result.x)
    return PolicyModel(
        feature_names=data.feature_names,
        mean=mean,
        scale=scale,
        w1=w1,
        b1=b1,
        w2=w2,
        l2=l2,
    )


def _softmax(z: np.ndarray, starts: np.ndarray, sizes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-group softmax and log partition, shift-stabilised.

    `reduceat` rather than `bincount` because the groups are contiguous by
    construction and a segmented reduction over contiguous groups is one pass.
    """
    if not len(starts):
        return np.zeros_like(z), np.zeros(0)
    top = np.maximum.reduceat(z, starts)
    shifted = np.exp(z - np.repeat(top, sizes))
    totals = np.add.reduceat(shifted, starts)
    return shifted / np.repeat(totals, sizes), np.log(totals) + top


def probabilities(data: PolicyDataset, scores: np.ndarray) -> np.ndarray:
    """The model's distribution over each slot's options, one row per option."""
    probs, _ = _softmax(np.asarray(scores, dtype=float), data.starts, data.sizes)
    return probs


def log_likelihood(data: PolicyDataset, scores: np.ndarray) -> float:
    """Mean log probability of the human's action. Higher is better."""
    if not len(data):
        return 0.0
    scores = np.asarray(scores, dtype=float)
    _, log_z = _softmax(scores, data.starts, data.sizes)
    return float((scores[data.chosen_rows] - log_z).mean())


# -- what gets reported ------------------------------------------------------


def hits(data: PolicyDataset, scores: np.ndarray, k: int) -> np.ndarray:
    """Per decision, whether the human's option is in the top `k` by score.

    Rank by counting rather than by sorting: it is one pass instead of a sort
    per group, and it settles ties explicitly, which matters more than the
    speed. The count is of options scoring *at least* what the human's did, the
    chosen option included, and the hit is that count fitting inside the budget.

    So a tie counts against the provider, and that is deliberate. A provider
    that scores every option identically has not kept the human's action in its
    top three; it has no top three. Counting only strictly better options would
    score that provider at 1.0 everywhere and make the whole table unreadable.
    """
    scores = np.asarray(scores, dtype=float)
    chosen_scores = np.repeat(scores[data.chosen_rows], data.sizes)
    at_least = np.add.reduceat((scores >= chosen_scores).astype(float), data.starts)
    return at_least <= k


def recall(data: PolicyDataset, scores: np.ndarray, k: int) -> float:
    if not len(data):
        return float("nan")
    return float(hits(data, scores, k).mean())


def bootstrap_recall(
    data: PolicyDataset,
    scores: np.ndarray,
    k: int,
    resamples: int = 1000,
    seed: int = 0,
) -> tuple[float, float]:
    """A 95% interval on recall, resampled over battles.

    Battles, not decisions. Positions inside one game share a board, a pair of
    teams and a player, so resampling them independently reports an interval
    narrower than the evidence supports. Same discipline as
    `fit.bootstrap_weights` and `discard.summarise`.
    """
    if not len(data):
        return (float("nan"), float("nan"))
    hit = hits(data, scores, k).astype(float)
    battles, index = np.unique(data.battle, return_inverse=True)
    order = np.argsort(index, kind="stable")
    grouped = np.split(hit[order], np.cumsum(np.bincount(index))[:-1])

    rng = np.random.default_rng(seed)
    draws = np.empty(resamples)
    for i in range(resamples):
        picked = rng.integers(0, len(battles), len(battles))
        draws[i] = np.concatenate([grouped[j] for j in picked]).mean()
    return (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))


def player_masks(
    data: PolicyDataset,
    seed: int = 0,
    fractions: tuple[float, float, float] = DEFAULT_FRACTIONS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The three splits as boolean masks over decisions.

    Masks rather than only the datasets, because a caller usually has a second
    array in the same decision order -- the baseline provider's scores, say --
    and re-deriving the split to subset it is how the two stop lining up.
    """
    players = np.unique(data.player)
    shuffled = np.random.default_rng(seed).permutation(players)
    first = int(round(fractions[0] * len(shuffled)))
    second = first + int(round(fractions[1] * len(shuffled)))
    buckets = (shuffled[:first], shuffled[first:second], shuffled[second:])
    return tuple(np.isin(data.player, bucket) for bucket in buckets)  # type: ignore[return-value]


def split_by_player(
    data: PolicyDataset,
    seed: int = 0,
    fractions: tuple[float, float, float] = DEFAULT_FRACTIONS,
) -> tuple[PolicyDataset, PolicyDataset, PolicyDataset]:
    """Train, validation, test, with no player on two sides of the line.

    Players rather than replays, per spec section 3.4: M4's finding was that
    leads transferred to unseen players and the bring-4 did not, and a random
    split would have shown one number for both.

    What this does *not* remove is the opponent. A held-out player's games can
    still have been seen from the other side, so the board is not always novel
    even though the decision maker is. Removing that would mean holding out
    whole games by both players and would cost roughly half the corpus; the
    property the split is for -- that the model has not memorised the person
    whose choices it is being scored on -- survives without it.
    """
    return tuple(  # type: ignore[return-value]
        data.subset(mask) for mask in player_masks(data, seed, fractions)
    )


# -- the providers -----------------------------------------------------------


class LearnedPolicy:
    """Implementation B as a `PolicyProvider`: the fitted prior, at play time.

    A joint action scores as the sum of its two slots' logits, which is the same
    composition `HeuristicPolicy` uses. That is deliberate rather than
    convenient: the benchmark is then comparing two rankings of the same
    quantity instead of two different formulations of what a joint action is.
    Slot interaction is out of scope here for the same reason it is out of scope
    there (spec section 6), and is the obvious follow-up if B wins.

    `belief` is accepted and ignored, as A ignores it. The posterior is a real
    input a candidate provider could use and plumbing it in is a separate change
    with its own measurement.
    """

    name = "learned-prior"

    def __init__(
        self,
        dex: Dex,
        model: PolicyModel | None = None,
        path: Path | None = None,
    ) -> None:
        self._dex = dex
        self._model = model if model is not None else load_model(dex.format_id, path)

    def candidates(
        self,
        actions: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
        belief: Any = None,
        k: int = DEFAULT_K,
    ) -> list[dict[str, Any]]:
        return [scored.action for scored in self.scored(actions, k, state)]

    def scored(
        self,
        actions: list[dict[str, Any]],
        k: int = DEFAULT_K,
        state: dict[str, Any] | None = None,
    ) -> list[ScoredAction]:
        """The top `k` joint actions by summed logit, best first.

        Ties break on the action's protocol string, as in every other provider,
        so the same position always produces the same candidate set in the same
        order.

        The per-slot score is memoised across the joint set. Roughly 156 joint
        actions are built from about a dozen distinct per-slot options, so
        without the cache the same vector would be built and scored a dozen
        times a turn for no change in any answer.
        """
        board = board_for(state, self._dex) if state else None
        cache: dict[tuple[Any, ...], float] = {}

        def slot_score(index: int, slot: dict[str, Any]) -> float:
            if board is None or state is None:
                return 0.0
            key = (
                index,
                slot.get("kind"),
                slot.get("move"),
                slot.get("target"),
                slot.get("species"),
            )
            if key not in cache:
                vector = option_features(state, index, slot, board)
                cache[key] = float(self._model.score(vector[None, :])[0])
            return cache[key]

        ranked = sorted(
            (
                ScoredAction(
                    action=action,
                    score=sum(
                        slot_score(index, slot)
                        for index, slot in enumerate(action.get("slots", []))
                    ),
                    reasons=(self.name,),
                )
                for action in actions
            ),
            key=lambda scored: (-scored.score, scored.action["message"]),
        )
        return ranked[:k]


class UnionPolicy:
    """Two providers' sets, interleaved to `k`.

    Measured for free in the same run as its two halves (spec section 1). If
    neither dominates, the union may still beat both, and shipping it is a
    legitimate outcome rather than a consolation: the cost of a candidate set is
    the payoff matrix it produces, and a union of two fives is the same ten
    columns as either provider's ten.

    Interleaved rather than concatenated, so that the budget is split evenly and
    a provider that is confident about its first pick gets that pick in
    regardless of what the other one thinks. Duplicates collapse, which is why
    the two are asked for `k` each rather than `k // 2`: where they agree, the
    union should spend the freed slots on their next choices rather than on
    nothing.
    """

    def __init__(self, first: Any, second: Any, name: str | None = None) -> None:
        self.first = first
        self.second = second
        self.name = name or f"union-{first.name}-{second.name}"

    def candidates(
        self,
        actions: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
        belief: Any = None,
        k: int = DEFAULT_K,
    ) -> list[dict[str, Any]]:
        left = self.first.candidates(actions, state, belief, k)
        right = self.second.candidates(actions, state, belief, k)

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pair in zip_longest(left, right):
            for action in pair:
                if action is None:
                    continue
                message = str(action.get("message", ""))
                if message in seen:
                    continue
                seen.add(message)
                out.append(action)
                if len(out) == k:
                    return out
        return out


def load_model(format_id: str, path: Path | None = None) -> PolicyModel:
    """The fitted prior for a format, from disk.

    Keyed by format id and never by a global constant, because Reg M-A and Reg
    M-B are different mods with different legal pools (`CLAUDE.md` constraint
    3), and a prior fit on one is not a prior for the other.
    """
    path = path if path is not None else MODEL_DIR / f"prior.{format_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no fitted policy prior at {path}. Build one with `make fit-policy`."
        )
    return PolicyModel.from_json(json.loads(path.read_text(encoding="utf-8")))

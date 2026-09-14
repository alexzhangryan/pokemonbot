"""The coach's numbers: two losses per turn, and what they decompose into.

`docs/06-coach-and-evaluation.md` section 1 and `docs/specs/2026-09-13-coach.md`
sections 1 to 4. Chess analysis reports one number because chess is perfect
information, sequential and deterministic. None of that holds here, so one
number would conflate playing badly, being read, and getting unlucky, and
those are exactly the three things a player wants told apart.

Per decision, with `A` the ex-ante payoff matrix (rows ours, columns theirs),
`(x, y, v)` its equilibrium and `a` the played row:

- **ex-ante loss** `v - (A y)[a]`: what the decision cost against an opponent
  who plays the equilibrium, given what was knowable. Zero on the support.
- **ex-post loss** `max_r A_post[r, j*] - A_post[a, j*]`: what it cost against
  what the opponent actually did, `j*`, under full information.
- **luck** `A_post[a, j*] - win_prob(next position)`: how far the realised
  position fell short of the played cell. Positive is bad. Not purely rolls:
  everything the one-turn model does not represent lands here too, which is
  why the model's own roll branches are reported beside it.

The matrix is the same one the agent plays by -- `payoff_matrix` over a
`TurnModel`, solved by `solve_both` -- with the pruning removed and the
information state made explicit (`Models`). `docs/01-plan.md`: the coach is
the same core run offline with a larger budget, not a second engine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from champions.belief.evaluate import TruthSet
from champions.belief.hypothesis import BeliefEffects, BeliefHypothesis
from champions.belief.priors import SetPrior
from champions.coach import classify
from champions.coach.decisions import REPLAY, Decision, Game
from champions.coach.truth import PriorSource, TruthOracle
from champions.dex.loader import Dex
from champions.search.evaluate import evaluate, win_prob
from champions.search.matrix import MASS_THRESHOLD, solve_both
from champions.search.payoff import TurnModel, payoff_matrix
from champions.search.policy import opponent_candidates
from champions.trace.schema import EventType

#: The offline column budget (spec section 3). Two known four-move sets give
#: sixteen joint columns; this keeps all of them with room for the played one.
COLUMN_BUDGET = 25

#: How many rows and columns of the equilibrium the event lists.
LISTED = 8

#: How many critical turns each list names.
CRITICAL = 3

REVEALED = "revealed-moves-only"
CORPUS_PRIOR = "corpus-prior"
OPEN_SHEET = "open-sheet"
TEAM_FILE = "team-file"

MODEL = "analytic-one-turn"
EFFECTS = "known-own-side"

#: Why the preview pseudo-turn carries no verdict (spec section 4).
PREVIEW_PENDING = ["subset_distribution", "payoff_matrix", "equilibrium_weights"]
PREVIEW_REASON = (
    "no preview value model: the corpus fit was rejected out of sample (D39) and the "
    "self-play separability test that would license a 15 x 15 matrix was not run (D56)"
)


@dataclass(frozen=True)
class Models:
    """An information state: who the opponent is assumed to be.

    `name` goes on every analysis event. `believed_moves` builds the columns
    and `model` values the cells; both read the same source, so a column the
    opponent is believed to have is valued with the set it is believed to
    carry.
    """

    name: str
    dex: Dex
    model: TurnModel
    believed_moves: Callable[[str], list[str]] | None


def prior_models(dex: Dex, prior: SetPrior) -> Models:
    """The information state the belief agent plays under, for a review with
    no truth table: the corpus prior's sets and move frequencies (D85)."""
    source = PriorSource(prior, dex)
    model = TurnModel(
        dex,
        hypothesis=BeliefHypothesis(belief=source),
        effects=BeliefEffects(source),
        place_incoming=True,
    )
    return Models(CORPUS_PRIOR, dex, model, source.believed_moves)


def models_for(dex: Dex, truths: Mapping[str, TruthSet] | None, name: str = OPEN_SHEET) -> Models:
    """The models for a truth table, or the revealed-only ones without one.

    Our own side's items and abilities are read off the snapshot by
    `BeliefEffects` either way (spec section 2): the player knew them.
    """
    if not truths:
        model = TurnModel(
            dex,
            hypothesis=BeliefHypothesis(belief=None),
            effects=BeliefEffects(None),
            place_incoming=True,
        )
        return Models(REVEALED, dex, model, None)
    oracle = TruthOracle(truths, dex)
    model = TurnModel(
        dex,
        hypothesis=BeliefHypothesis(belief=oracle),
        effects=BeliefEffects(oracle),
        place_incoming=True,
    )
    return Models(name, dex, model, oracle.believed_moves)


@dataclass
class TurnAnalysis:
    """Everything the `analysis` event for one turn carries."""

    turn: int
    for_seq: int
    played: int | None
    played_label: str | None
    label: str | None
    tags: list[str]
    game_value: float
    is_pure: bool
    on_support: bool | None
    ex_ante_loss: float | None
    ex_post_loss: float | None
    ante_value: float | None
    expected: float | None
    realized: float
    luck: float | None
    win_prob_before: float
    win_prob_after: float
    calibrated: bool
    equilibrium: list[dict[str, Any]]
    opponent_equilibrium: list[dict[str, Any]]
    best_label: str
    best_post_label: str | None
    opponent_played_label: str | None
    rolls: list[dict[str, Any]]
    n_rows: int
    n_columns: int
    information: dict[str, str]
    recorded: dict[str, Any] = field(default_factory=dict)
    belief: dict[str, Any] | None = None
    explanation: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "scope": "turn",
            "turn": self.turn,
            "for_seq": self.for_seq,
            "played": self.played,
            "played_label": self.played_label,
            "classification": self.label,
            "tags": list(self.tags),
            "game_value": self.game_value,
            "is_pure": self.is_pure,
            "on_support": self.on_support,
            "ex_ante_loss": self.ex_ante_loss,
            "ex_post_loss": self.ex_post_loss,
            "ante_value": self.ante_value,
            "expected_value": self.expected,
            "realized_value": self.realized,
            "luck": self.luck,
            "win_prob_before": self.win_prob_before,
            "win_prob_after": self.win_prob_after,
            "calibrated": self.calibrated,
            "equilibrium": self.equilibrium,
            "opponent_equilibrium": self.opponent_equilibrium,
            "best": self.best_label,
            "best_ex_post": self.best_post_label,
            "opponent_played": self.opponent_played_label,
            "rolls": self.rolls,
            "n_rows": self.n_rows,
            "n_columns": self.n_columns,
            "information": dict(self.information),
            "model": MODEL,
            "effects": EFFECTS,
            "recorded": dict(self.recorded),
            "belief": self.belief,
            "explanation": self.explanation,
        }


@dataclass
class GameAnalysis:
    turns: list[TurnAnalysis]
    curve: list[dict[str, Any]]
    summary: dict[str, Any]
    preview: dict[str, Any]
    calibrated: bool
    information: dict[str, str]

    def by_turn(self) -> dict[int, TurnAnalysis]:
        return {t.turn: t for t in self.turns}


# -- one decision ------------------------------------------------------------


def column_key(column: Mapping[str, Any]) -> tuple[tuple[str, str, int], ...]:
    """What makes two columns the same action: per slot, kind, move or
    species, and target."""
    out = []
    for slot in column.get("slots") or []:
        kind = str(slot.get("kind", "none"))
        what = str(slot.get("move") or slot.get("species") or "")
        out.append((kind, what, int(slot.get("target") or 0) if kind == "move" else 0))
    return tuple(out)


def analyze_decision(
    decision: Decision,
    ante: Models,
    post: Models | None = None,
    k: int = COLUMN_BUDGET,
) -> TurnAnalysis:
    """The two losses and their decomposition for one decision."""
    post = post or ante
    snapshot = decision.snapshot
    rows = decision.rows

    columns = list(opponent_candidates(snapshot, ante.dex, k, believed_moves=ante.believed_moves))
    their: int | None = None
    if decision.their_played is not None:
        key = column_key(decision.their_played)
        their = next((i for i, c in enumerate(columns) if column_key(c) == key), None)
        if their is None:
            columns.append(decision.their_played)
            their = len(columns) - 1

    matrix = payoff_matrix(snapshot, rows, columns, ante.model)
    equilibrium = solve_both(matrix)
    x, y, value = equilibrium.row, equilibrium.column, float(equilibrium.value)
    ante_values = matrix @ y
    losses = np.maximum(0.0, value - ante_values)
    best = int(np.argmax(x))

    before = evaluate(snapshot)
    after = win_prob(decision.next_snapshot)

    played = decision.played
    label: str | None = None
    support: bool | None = None
    loss = weight = ante_value = None
    if played is not None:
        loss = float(losses[played])
        weight = float(x[played])
        ante_value = float(ante_values[played])
        support = classify.on_support(weight, loss)
        label = classify.classify(weight, loss, float(x.max()))

    post_column: np.ndarray | None = None
    best_post: int | None = None
    ex_post_loss = expected = luck = None
    rolls: list[dict[str, Any]] = []
    if their is not None:
        if post is ante:
            post_column = matrix[:, their]
        else:
            post_column = np.array(
                [post.model.value(snapshot, r, columns[their]) for r in rows], dtype=float
            )
        best_post = int(np.argmax(post_column))
        if played is not None:
            expected = float(post_column[played])
            ex_post_loss = max(0.0, float(post_column[best_post]) - expected)
            luck = expected - after
            rolls = [
                {
                    "probability": round(o.probability, 4),
                    "value": round(o.value, 4),
                    "faints": list(o.faints),
                }
                for o in post.model.outcomes(snapshot, rows[played], columns[their])
            ]

    if played is not None and label is not None:
        tags = classify.tags(
            label=label,
            weight=float(weight or 0.0),
            loss=float(loss or 0.0),
            is_pure=equilibrium.is_pure,
            ante_values=ante_values.tolist(),
            game_value=value,
            row_values=matrix[played, :].tolist() if their is not None else None,
            their_column=their,
            ante_value=float(ante_value or 0.0),
            post_cell=expected,
            luck=luck,
        )
    else:
        forced = classify.is_forced(equilibrium.is_pure, ante_values, value)
        tags = [classify.FORCED] if forced else []

    listed = [i for i in np.argsort(-x) if x[i] > MASS_THRESHOLD][:LISTED]
    listed_columns = [i for i in np.argsort(-y) if y[i] > MASS_THRESHOLD][:LISTED]

    return TurnAnalysis(
        turn=decision.turn,
        for_seq=decision.for_seq,
        played=played,
        played_label=_label(rows[played]) if played is not None else None,
        label=label,
        tags=tags,
        game_value=value,
        is_pure=equilibrium.is_pure,
        on_support=support,
        ex_ante_loss=loss,
        ex_post_loss=ex_post_loss,
        ante_value=ante_value,
        expected=expected,
        realized=after,
        luck=luck,
        win_prob_before=before.win_prob,
        win_prob_after=after,
        calibrated=before.calibrated,
        equilibrium=[
            {
                "label": _label(rows[i]),
                "probability": round(float(x[i]), 4),
                "ante_value": round(float(ante_values[i]), 4),
            }
            for i in listed
        ],
        opponent_equilibrium=[
            {"label": _label(columns[i]), "probability": round(float(y[i]), 4)}
            for i in listed_columns
        ],
        best_label=_label(rows[best]),
        best_post_label=_label(rows[best_post]) if best_post is not None else None,
        opponent_played_label=_label(columns[their]) if their is not None else None,
        rolls=rolls,
        n_rows=len(rows),
        n_columns=len(columns),
        information={"ante": ante.name, "post": post.name},
        recorded=dict(decision.recorded),
        belief=decision.belief,
    )


def _label(action: Mapping[str, Any]) -> str:
    return str(action.get("label") or action.get("message") or "?")


# -- one game ----------------------------------------------------------------


def analyze_game(
    game: Game,
    dex: Dex,
    k: int = COLUMN_BUDGET,
    ante: Models | None = None,
    post: Models | None = None,
) -> GameAnalysis:
    """Every decision of a game, the curve, the summary and the preview."""
    name = OPEN_SHEET if game.source == REPLAY else TEAM_FILE
    ante = ante or models_for(dex, game.ante_truths, name)
    if post is None:
        same = game.post_truths is game.ante_truths or (
            not game.post_truths and not game.ante_truths
        )
        post = ante if same else models_for(dex, game.post_truths, name)

    turns = [analyze_decision(d, ante, post, k) for d in game.decisions]
    curve = _curve(game.events)
    if turns:
        calibrated = all(t.calibrated for t in turns)
    else:
        calibrated = bool(curve and curve[0]["calibrated"])
    return GameAnalysis(
        turns=turns,
        curve=curve,
        summary=summarise(turns, curve),
        preview=_preview(game),
        calibrated=calibrated,
        information={"ante": ante.name, "post": post.name},
    )


def _curve(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """`win_prob` at every turn start and at the end, recomputed from the
    snapshot the agent evaluated, with what the trace recorded beside it."""
    out: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") or {}
        state = payload.get("state")
        if not state:
            continue
        if event.get("type") == EventType.TURN_START:
            position = evaluate(state)
            recorded = (payload.get("evaluation") or {}).get("win_prob")
            out.append(
                {
                    "turn": payload.get("turn"),
                    "win_prob": position.win_prob,
                    "recorded": recorded,
                    "calibrated": position.calibrated,
                }
            )
        elif event.get("type") == EventType.BATTLE_END:
            position = evaluate(state)
            out.append(
                {
                    "turn": None,
                    "win_prob": position.win_prob,
                    "recorded": None,
                    "calibrated": position.calibrated,
                }
            )
    return out


def summarise(turns: Sequence[TurnAnalysis], curve: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [t for t in turns if t.ex_ante_loss is not None]
    ante = [float(t.ex_ante_loss or 0.0) for t in scored]
    post = [float(t.ex_post_loss) for t in scored if t.ex_post_loss is not None]
    labels = {label: sum(1 for t in scored if t.label == label) for label in classify.LABELS}
    tags = {tag: sum(1 for t in turns if tag in t.tags) for tag in classify.TAGS}

    by_loss = sorted(
        (t for t in scored if (t.ex_ante_loss or 0.0) > classify.LOSS_EPS),
        key=lambda t: -(t.ex_ante_loss or 0.0),
    )[:CRITICAL]

    drops: list[dict[str, Any]] = []
    for before, after in zip(curve, curve[1:], strict=False):
        drop = float(before["win_prob"]) - float(after["win_prob"])
        if drop > 0 and before.get("turn") is not None:
            drops.append({"turn": before["turn"], "drop": round(drop, 4)})
    drops.sort(key=lambda d: -d["drop"])

    return {
        "decisions": len(turns),
        "scored": len(scored),
        "bands": classify.BANDS.as_dict(),
        "ex_ante_loss_total": round(sum(ante), 4),
        "ex_ante_loss_mean": round(sum(ante) / len(ante), 4) if ante else None,
        "ex_post_loss_total": round(sum(post), 4),
        "ex_post_loss_mean": round(sum(post) / len(post), 4) if post else None,
        "classifications": labels,
        "tags": tags,
        "critical_by_loss": [
            {"turn": t.turn, "ex_ante_loss": round(float(t.ex_ante_loss or 0.0), 4)}
            for t in by_loss
        ],
        "critical_by_drop": drops[:CRITICAL],
    }


def _preview(game: Game) -> dict[str, Any]:
    """The bring-4 pseudo-turn, without the verdict there is nothing to compute."""
    selected: list[str] = []
    leads: list[str] = []
    opponent_seen: list[str] = []
    for event in game.events:
        payload = event.get("payload") or {}
        if event.get("type") == EventType.PREVIEW_DECISION:
            selected = list(payload.get("selected") or [])
        elif event.get("type") == EventType.TURN_START and payload.get("turn") == 1 and not leads:
            state = payload.get("state") or {}
            leads = [
                str(p.get("species"))
                for p in (state.get("ours") or {}).get("active") or []
                if p is not None
            ]
        elif event.get("type") == EventType.BATTLE_END:
            state = payload.get("state") or {}
            theirs = state.get("theirs") or {}
            opponent_seen = [
                str(p.get("species"))
                for p in [*(theirs.get("active") or []), *(theirs.get("bench") or [])]
                if p is not None
            ]
    return {
        "scope": "preview",
        "bring": selected,
        "leads": leads,
        "opponent_bring_observed": opponent_seen,
        "pending": list(PREVIEW_PENDING),
        "reason": PREVIEW_REASON,
    }

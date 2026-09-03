"""Implementation C: the language-model candidate provider.

`docs/04-decision-engine.md` section 3 specifies three candidate providers. A is
the heuristic (`champions.search.policy`), B the learned prior
(`champions.search.learned`), and C is this one: a language model that *selects
among* candidates whose consequences the engine has already computed.

## What the model is and is not asked

The engine computes the numbers -- exactly the same numbers `HeuristicPolicy`
scores on, off the same `Board`: damage as a fraction of each target's remaining
HP, whether that is a knockout, whether a slot is threatened, whether a speed
control flips a race, whether Fake Out is available. The model is shown those,
one line per candidate, and asked only to order them. It never computes damage,
speed order or a knockout threshold. That inversion is the whole case for C in
section 3, and it is why this provider is a thin thing on top of A rather than a
new estimator: A already answers every question the brief states.

## Why it shortlists with A first

Roughly 156 joint actions is too many to put in a prompt, and most of them are
the same handful of moves aimed at different targets. So A ranks the legal set,
the top `shortlist` go to the model with their computed numbers, and the model
reorders those. The cost is the ceiling: C can only surface what A's shortlist
contains, so on a position where the equilibrium's answer is outside A's top
`shortlist`, C cannot recover it any more than A can. `shortlist` is set well
above `k` so the model has room to disagree with A's ordering; raising it is the
first lever if C loses the guard to A on positions A's shortlist already misses.

## The fallback is A

Every way the model can fail -- the server is down, the reply is unparseable,
the ranking is short -- lands on A's ordering rather than an exception, because a
candidate provider that can raise mid-battle is not one that can play. A dead
backend makes C *equal* A, which is the honest floor: with nothing from the
model, the engine keeps the ordering it computed.

## Determinism

The model is the one non-deterministic component in the search. `champions.search.llm`
pins temperature and seed and caches replies per prompt, so a rerun of the guard
reads the same rankings off disk. `CLAUDE.md`'s from-a-seed reproducibility holds
for a warm cache and is a documented soft spot for a cold one; the guard is run
and reported off a warm cache for exactly that reason.
"""

from __future__ import annotations

from typing import Any

from champions.dex.damage import TypeChart
from champions.dex.loader import Dex
from champions.search.llm import LLMClient, LLMError, client_from_env, ensure_reachable, rank
from champions.search.payoff import SPREAD_TARGETS, OpponentHypothesis
from champions.search.policy import DEFAULT_K, Board, HeuristicPolicy, ScoredAction

#: How many of A's top candidates the model is shown. Above every `k` the guard
#: sweeps (5, 10, 15, 20) so the prompt -- and therefore the cached reply -- does
#: not change with the caller's budget, and the model always has more candidates
#: than it will be asked to keep.
DEFAULT_SHORTLIST = 20


class LanguagePolicy:
    """Implementation C as a `PolicyProvider`: A's shortlist, ordered by a model.

    Holds a `HeuristicPolicy` for the shortlist and the fallback, a `Board` per
    position for the briefs, and an `LLMClient` for the ordering. `belief` is
    accepted and ignored, as it is in A and B -- the posterior is a separate
    change with its own measurement.
    """

    name = "language-model"

    def __init__(
        self,
        dex: Dex,
        client: LLMClient | None = None,
        *,
        shortlist: int = DEFAULT_SHORTLIST,
        heuristic: HeuristicPolicy | None = None,
    ) -> None:
        self._dex = dex
        self._heuristic = heuristic if heuristic is not None else HeuristicPolicy(dex)
        self._client = client if client is not None else client_from_env()
        self._chart = TypeChart.from_dex(dex)
        self._hypothesis = OpponentHypothesis()
        self._shortlist = shortlist

    def ensure_available(self) -> None:
        """Fail loudly if the backend is unreachable, for a measurement to call
        before it starts. See `champions.search.llm.ensure_reachable`: the live
        fallback to A is right in a battle and wrong in the guard."""
        ensure_reachable(self._client)

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
        """The top `k` joint actions, ordered by the model, best first.

        The shortlist is A's top `max(shortlist, k)`; the model reorders it; the
        top `k` of that order is returned. Without a state -- a trace written
        before the snapshot existed, a bare test -- there is nothing to put in a
        brief, so this is A's ordering unchanged.
        """
        depth = max(self._shortlist, k)
        shortlisted = self._heuristic.scored(actions, depth, state)
        if not state or len(shortlisted) <= 1:
            return shortlisted[:k]

        board = Board.read(state, self._dex, self._chart, self._hypothesis)
        if board.empty:
            return shortlisted[:k]

        header = self._header(state, board)
        briefs = [self._brief(scored, board) for scored in shortlisted]
        try:
            order = rank(self._client, header, briefs)
        except LLMError:
            # A dead or erroring backend makes C equal A rather than unplayable.
            return shortlisted[:k]

        ranked = [
            ScoredAction(
                action=shortlisted[index].action,
                # Descending so `scored[0]` is the model's top pick, which is what
                # the agent proposes as its pre-equilibrium answer. The magnitude
                # is a rank, not a probability, and is only ever compared within
                # one position.
                score=float(len(order) - position),
                reasons=(self.name, *shortlisted[index].reasons),
            )
            for position, index in enumerate(order)
        ]
        return ranked[:k]

    # -- rendering the position for the model --------------------------------

    def _header(self, state: dict[str, Any], board: Board) -> str:
        """The board, in a couple of lines the model can reason over.

        Deliberately short: the per-candidate briefs carry the numbers that
        decide the turn, and a long header buries them. Turn, both active pairs
        with HP and status, and the field state that changes what a move is
        worth.
        """
        parts = [f"Turn {int(state.get('turn') or 0)}."]
        ours = self._side_line(state, "ours")
        theirs = self._side_line(state, "theirs")
        if ours:
            parts.append(f"Your active: {ours}.")
        if theirs:
            parts.append(f"Opponent active: {theirs}.")

        field = []
        weather = sorted(state.get("weather") or {})
        if weather:
            field.append("weather " + ", ".join(w.lower() for w in weather))
        if "TRICK_ROOM" in (state.get("fields") or {}):
            field.append("trick room up")
        if "TAILWIND" in (state.get("side_conditions") or {}):
            field.append("your tailwind up")
        if "TAILWIND" in (state.get("opponent_side_conditions") or {}):
            field.append("opponent tailwind up")
        if field:
            parts.append("Field: " + "; ".join(field) + ".")
        return " ".join(parts)

    def _side_line(self, state: dict[str, Any], side: str) -> str:
        active = state.get(side, {}).get("active") or []
        return ", ".join(self._mon(view) for view in active if view and not view.get("fainted"))

    def _mon(self, view: dict[str, Any]) -> str:
        name = view.get("species") or view.get("name") or "?"
        hp = view.get("hp_pct")
        text = f"{name}" if hp is None else f"{name} {int(round(float(hp)))}%"
        status = view.get("status")
        if status:
            text += f" ({str(status).lower()})"
        return text

    def _brief(self, scored: ScoredAction, board: Board) -> str:
        """One candidate as a line: what it is, plus the numbers A computed.

        The heuristic's own reasons ride along -- `knockout`, `protect idle`,
        `speed control`, `fake out`, `switch`, `friendly fire` -- because they
        encode the position questions a damage percentage alone cannot answer
        (whether Protect is idle, whether a race flips), and they are exactly
        what A ranked on. The per-move damage is added on top so the model sees
        the raw number behind the verdict.
        """
        slots = scored.action.get("slots", [])
        rendered = (self._slot(i, slot, board) for i, slot in enumerate(slots))
        details = "; ".join(filter(None, rendered))
        label = scored.action.get("label") or scored.action.get("message") or "?"
        reasons = ", ".join(scored.reasons)
        text = label
        if details:
            text += f" -- {details}"
        if reasons:
            text += f" [{reasons}]"
        return text

    def _slot(self, index: int, slot: dict[str, Any], board: Board) -> str:
        kind = slot.get("kind")
        if kind == "switch":
            return f"bring {slot.get('species') or slot.get('label') or 'bench Pokemon'}"
        if kind != "move":
            return ""

        entry = self._dex.moves.get(str(slot.get("move") or ""))
        if entry is None:
            return str(slot.get("label") or "")
        if entry["category"] == "Status":
            return f"{entry['name']} (status)"

        bits = []
        for side, target in board.targets(index, slot, entry):
            fraction = board.damage_fraction(entry, index, side, target)
            view = board.view(side, target) or {}
            who = view.get("species") or ("your " + ("Pokemon" if side == "ours" else "foe"))
            tag = "ally " if side == "ours" else ""
            ko = " KO" if fraction >= 1.0 else ""
            bits.append(f"{int(round(fraction * 100))}% to {tag}{who}{ko}")
        spread = " (spread)" if str(entry.get("target")) in SPREAD_TARGETS else ""
        return f"{entry['name']}{spread}: " + ", ".join(bits) if bits else entry["name"]

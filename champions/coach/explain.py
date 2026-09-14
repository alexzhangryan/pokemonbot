"""The natural language writeup, grounded in the numbers and nothing else.

`docs/06-coach-and-evaluation.md` section 3 puts the language model in the
coach unconditionally: explaining why a move was wrong, from engine-computed
numbers, is what these models are good at and there is no clock to time out.
Section 3 of `docs/04` is the arrangement that makes that safe -- the model is
never asked to compute a number, only to talk about ones already computed.

So the writeup is built in two layers. `template` renders the facts of a
`TurnAnalysis` as plain sentences and needs no model at all; it is what a
fresh clone with no Ollama reachable gets, and what the tests exercise.
`polish` hands those same facts to `champions.search.llm.client_from_env`
(D68's mocked, cached client) with the instruction to rewrite them as coaching
prose without adding a number the facts do not contain, and falls back to the
template on any `LLMError`. The facts are the contract; the prose is optional.
"""

from __future__ import annotations

from collections.abc import Iterable

from champions.coach import classify
from champions.coach.analyze import GameAnalysis, TurnAnalysis
from champions.search.llm import LLMClient, LLMError

TAG_SENTENCES = {
    classify.FORCED: "The position was forced: every alternative lost at least five points.",
    classify.READ: "The opponent read it and chose the specific counter to what was played.",
    classify.GAMBLE: "It was a gamble that paid off; the equilibrium would rarely have played it.",
    classify.UNLUCKY: (
        "The decision was fine and the outcome was not; that is the roll, not the play."
    ),
    classify.LUCKY: "The position came out better than the play deserved.",
}

INSTRUCTION = (
    "You are a Pokemon doubles coach. Rewrite the facts below as two or three sentences of "
    "plain coaching prose for the player who made this decision. Use only the numbers and "
    "names given; do not add, change or estimate any number. Do not use headings or lists."
)


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * value:.0f}%"


def facts(turn: TurnAnalysis) -> list[str]:
    """The sentences every writeup is made of, in order."""
    out: list[str] = []
    if turn.played_label is None:
        out.append(f"Turn {turn.turn}: the log does not record what was played.")
    else:
        out.append(f"Turn {turn.turn}: played {turn.played_label}.")
    out.append(
        f"Win probability before the turn {pct(turn.win_prob_before)}, after it "
        f"{pct(turn.win_prob_after)}; the game value of the position was {pct(turn.game_value)}."
    )
    if turn.label is not None:
        loss = turn.ex_ante_loss or 0.0
        if turn.label in (classify.BEST, classify.SOLID):
            out.append(f"Classification: {turn.label}, on the equilibrium support.")
        else:
            out.append(
                f"Classification: {turn.label}, off the support, "
                f"costing {100.0 * loss:.1f} points against an opponent playing the equilibrium."
            )
    mix = ", ".join(f"{pct(e['probability'])} {e['label']}" for e in turn.equilibrium[:4])
    if turn.is_pure:
        out.append(f"The equilibrium was pure: {turn.best_label}.")
    elif mix:
        out.append(f"The equilibrium mixed {mix}.")
    if turn.opponent_played_label is not None:
        out.append(f"The opponent played {turn.opponent_played_label}.")
        if turn.best_post_label is not None and turn.ex_post_loss is not None:
            if turn.ex_post_loss <= classify.LOSS_EPS:
                out.append("Against that, what was played was the best reply.")
            else:
                out.append(
                    f"Against that, the best reply was {turn.best_post_label}, "
                    f"{100.0 * turn.ex_post_loss:.1f} points better after the fact."
                )
    if turn.expected is not None and turn.luck is not None:
        swing = abs(100.0 * turn.luck)
        if swing < 0.5:
            verdict = "which is what the line expected"
        elif turn.luck > 0:
            verdict = (
                f"{swing:.1f} points worse than the line expected, from the roll and from "
                f"effects the model does not represent"
            )
        else:
            verdict = (
                f"{swing:.1f} points better than the line expected, from the roll and from "
                f"effects the model does not represent"
            )
        out.append(
            f"The played line expected {pct(turn.expected)}; the position that followed was "
            f"worth {pct(turn.realized)}, {verdict}."
        )
    for tag in turn.tags:
        sentence = TAG_SENTENCES.get(tag)
        if sentence:
            out.append(sentence)
    if not turn.calibrated:
        out.append(
            "The evaluation is not calibrated on this machine, so read these as rankings "
            "rather than probabilities."
        )
    return out


def template(turn: TurnAnalysis) -> str:
    return " ".join(facts(turn))


def summary_line(turn: TurnAnalysis) -> str:
    """One line per turn for the table: label, tags, played."""
    head = turn.label or "unscored"
    if turn.tags:
        head += " (" + ", ".join(turn.tags) + ")"
    return f"{head}: {turn.played_label or 'unrecorded'}"


def polish(turn: TurnAnalysis, client: LLMClient) -> str:
    """The template's facts as prose from a language model, or the template."""
    prompt = INSTRUCTION + "\n\nFacts:\n" + "\n".join(f"- {f}" for f in facts(turn))
    try:
        reply = client.complete(prompt).strip()
    except LLMError:
        return template(turn)
    return reply or template(turn)


def explain(
    analysis: GameAnalysis, critical: Iterable[int], client: LLMClient | None = None
) -> None:
    """Fill every turn's `explanation` in place.

    Every turn gets the template. The critical turns get the model's prose
    when a client is given (spec section 5.5), since those are the ones a
    player reads in full.
    """
    wanted = set(critical)
    for turn in analysis.turns:
        if client is not None and turn.turn in wanted:
            turn.explanation = polish(turn, client)
        else:
            turn.explanation = template(turn)

"""The analysis overlay and the review document.

`docs/07-observability.md` section 2: the coach appends a parallel stream of
`analysis` events keyed to the turns of the trace they annotate, so a reviewed
game is the original trace plus an overlay rather than a separate artifact.
`overlay` produces that file. It has to pass `validate_events` -- contiguous
seqs, `battle_start` first, `battle_end` last -- because that contract is what
the viewer reads, so the events are inserted and re-sequenced rather than
appended.

`markdown` is the interim reader until M10 renders the overlay: the chess.com
review as a document, with the header, the turn table and the writeups in the
layout `docs/07` section 4 describes for the client.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from champions.coach import classify
from champions.coach.analyze import GameAnalysis
from champions.coach.decisions import Game
from champions.coach.explain import pct, summary_line
from champions.trace.schema import EventType


def overlay(game: Game, analysis: GameAnalysis) -> list[dict[str, Any]]:
    """The base events with the analysis interleaved, re-sequenced."""
    by_seq = {t.for_seq: t for t in analysis.turns}
    out: list[dict[str, Any]] = []

    def push(kind: str, payload: dict[str, Any], t: float) -> None:
        out.append(
            {
                "schema_version": 1,
                "battle_id": game.battle_id,
                "seq": len(out),
                "t": t,
                "type": kind,
                "payload": payload,
            }
        )

    for event in game.events:
        original_seq = int(event.get("seq", -1))
        kind = str(event.get("type"))
        t = float(event.get("t") or 1.0)
        if kind == EventType.BATTLE_END:
            push(
                EventType.ANALYSIS,
                {
                    "scope": "game",
                    "for_seq": len(out) + 1,
                    "turn": None,
                    "calibrated": analysis.calibrated,
                    "information": dict(analysis.information),
                    "curve": list(analysis.curve),
                    **analysis.summary,
                },
                t,
            )
        push(kind, dict(event.get("payload") or {}), t)
        if kind == EventType.PREVIEW_DECISION:
            push(EventType.ANALYSIS, {**analysis.preview, "for_seq": len(out) - 1, "turn": 0}, t)
        turn = by_seq.get(original_seq)
        if turn is not None:
            payload = turn.payload()
            payload["for_seq"] = len(out) - 1
            push(EventType.ANALYSIS, payload, t)
    return out


def markdown(game: Game, analysis: GameAnalysis) -> str:
    """The review as a document."""
    lines: list[str] = []
    who = game.player or game.side
    against = game.opponent or game.opponent_side
    lines.append(f"# Review: {game.battle_id}, {who} against {against}")
    lines.append("")
    lines.append(
        f"Reviewed from {game.side} ({game.source}). Result: **{game.result or 'unknown'}** "
        f"in {game.turns} turns. Information: ex-ante {analysis.information['ante']}, "
        f"ex-post {analysis.information['post']}."
    )
    if not analysis.calibrated:
        lines.append("")
        lines.append(
            "**The evaluation is not calibrated on this machine** (`data/eval/weights.*.json` "
            "is absent), so every probability below is a ranking, not a probability."
        )
    lines.append("")
    bands = classify.BANDS
    if bands.source == "hand-set":
        lines.append(
            "Thresholds are hand-set (`champions/coach/classify.py`), pending calibration "
            "against rating bands (`scripts/calibrate_coach.py`)."
        )
    else:
        lines.append(
            f"Thresholds: inaccuracy below {100 * bands.inaccuracy:.1f} points, mistake below "
            f"{100 * bands.mistake:.1f}, blunder above; {bands.source}."
        )
    lines.append("")

    summary = analysis.summary
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"{summary['scored']} of {summary['decisions']} decisions scored. Ex-ante loss "
        f"{_pts(summary['ex_ante_loss_total'])} in total, {_pts(summary['ex_ante_loss_mean'])} "
        f"per decision; ex-post loss {_pts(summary['ex_post_loss_total'])} in total."
    )
    lines.append("")
    counts = summary["classifications"]
    lines.append("| " + " | ".join(classify.LABELS) + " |")
    lines.append("| " + " | ".join("---:" for _ in classify.LABELS) + " |")
    lines.append("| " + " | ".join(str(counts.get(label, 0)) for label in classify.LABELS) + " |")
    lines.append("")
    tags = {k: v for k, v in summary["tags"].items() if v}
    if tags:
        lines.append("Tags: " + ", ".join(f"{k} x{v}" for k, v in tags.items()) + ".")
        lines.append("")

    lines.append("## Critical turns")
    lines.append("")
    if summary["critical_by_loss"]:
        lines.append(
            "By avoidable loss: "
            + ", ".join(
                f"turn {c['turn']} ({_pts(c['ex_ante_loss'])})" for c in summary["critical_by_loss"]
            )
            + "."
        )
    else:
        lines.append("By avoidable loss: none; every scored decision was on the support.")
    if summary["critical_by_drop"]:
        lines.append(
            "By win-probability drop: "
            + ", ".join(
                f"turn {c['turn']} ({_pts(c['drop'])})" for c in summary["critical_by_drop"]
            )
            + "."
        )
    lines.append("")

    preview = analysis.preview
    lines.append("## Preview")
    lines.append("")
    lines.append(
        f"Brought {', '.join(preview['bring']) or 'unknown'}; led "
        f"{', '.join(preview['leads']) or 'unknown'}. Opponent showed "
        f"{', '.join(preview['opponent_bring_observed']) or 'nobody'}. "
        f"No bring-4 verdict: {preview['reason']}."
    )
    lines.append("")

    lines.append("## Turns")
    lines.append("")
    lines.append(
        "Losses and luck are in win-probability points. Luck is the played line's expected "
        "value minus the position that followed, so positive is bad for the player and "
        "includes everything the one-turn model does not represent, not only the rolls."
    )
    lines.append("")
    lines.append("| turn | win prob | played | label | tags | ex-ante | ex-post | luck |")
    lines.append("| ---: | ---: | --- | --- | --- | ---: | ---: | ---: |")
    curve = {c["turn"]: c["win_prob"] for c in analysis.curve if c["turn"] is not None}
    for turn in analysis.turns:
        lines.append(
            f"| {turn.turn} | {pct(curve.get(turn.turn, turn.win_prob_before))} | "
            f"{turn.played_label or 'unrecorded'} | {turn.label or ''} | "
            f"{', '.join(turn.tags)} | {_pts(turn.ex_ante_loss)} | {_pts(turn.ex_post_loss)} | "
            f"{_pts(turn.luck)} |"
        )
    lines.append("")

    lines.append("## Writeups")
    lines.append("")
    for turn in analysis.turns:
        lines.append(f"**Turn {turn.turn}.** {summary_line(turn)}")
        lines.append("")
        if turn.explanation:
            lines.append(turn.explanation)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _pts(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * float(value):.1f}"


def curve_rows(analysis: GameAnalysis) -> Sequence[tuple[Any, float]]:
    """`(turn, win_prob)` pairs, for anything that wants to plot the curve."""
    return [(c["turn"], float(c["win_prob"])) for c in analysis.curve]

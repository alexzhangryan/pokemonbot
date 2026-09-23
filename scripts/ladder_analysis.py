"""Aggregate a live ladder run: the record, the openings, and what the coach says.

    python scripts/ladder_analysis.py --trace-dir traces --since 2026-09-15T03:30
    python scripts/ladder_analysis.py --trace-dir traces --last 12
    python scripts/ladder_analysis.py --trace-dir traces --seed 1

Reads `ledger.ndjson` under the trace directory, filters the rows, then reads
each game's trace and its `.review.jsonl` (the coach's output, D79) and
prints the things a play-analyse-improve cycle reads first:

- the record with a Wilson interval, game length, and the clock;
- the bring and the lead, each with its record, against what the opponent
  showed at preview;
- the opponent's Pokemon most often faced and most often lost to;
- the coach's per-game aggregates in wins against losses (ex-ante loss,
  ex-post loss, luck), the label counts, and luck by turn;
- how often the opponent's actual line was on the model's support, and the
  probability the model gave it, which is the belief and turn model's
  accuracy seen from outside;
- the lines the coach preferred on mistakes and blunders, by kind.

Nothing here is a claim; it is the table the next decision is read from.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def load(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}"


def review_path(trace: Path) -> Path:
    return trace.with_name(trace.name.replace(".jsonl", ".review.jsonl"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, default=Path("runs/live"))
    parser.add_argument("--since", help="ISO timestamp; rows finished at or after it")
    parser.add_argument("--until", help="ISO timestamp; rows finished before it")
    parser.add_argument("--last", type=int, help="the last N rows")
    parser.add_argument("--seed", type=int, help="rows with this seed only")
    parser.add_argument("--agent")
    parser.add_argument("--team")
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    rows = load(args.trace_dir / "ledger.ndjson")
    if args.since:
        rows = [r for r in rows if (r.get("finished_at") or "") >= args.since]
    if args.until:
        rows = [r for r in rows if (r.get("finished_at") or "") < args.until]
    if args.seed is not None:
        rows = [r for r in rows if r.get("seed") == args.seed]
    if args.agent:
        rows = [r for r in rows if r.get("agent") == args.agent]
    if args.team:
        rows = [r for r in rows if r.get("team") == args.team]
    if args.last:
        rows = rows[-args.last :]

    games: list[dict[str, Any]] = []
    for r in rows:
        trace = Path(r["trace"])
        if not trace.exists():
            trace = args.trace_dir / trace.name
        if not trace.exists():
            continue
        rp = review_path(trace)
        events = load(rp) if rp.exists() else load(trace)
        games.append({"row": r, "events": events, "reviewed": rp.exists()})

    n = len(games)
    wins = sum(1 for g in games if g["row"]["result"] == "win")
    lo, hi = wilson(wins, n)
    print(
        f"{n} games, {wins} won ({pct(wins / n if n else None)}%, Wilson {lo:.2f}-{hi:.2f}); "
        f"{sum(g['reviewed'] for g in games)} reviewed"
    )
    agents = Counter((g["row"]["agent"], g["row"]["team"], g["row"]["seed"]) for g in games)
    for (a, t, s), c in agents.most_common():
        w = sum(
            1
            for g in games
            if (g["row"]["agent"], g["row"]["team"], g["row"]["seed"]) == (a, t, s)
            and g["row"]["result"] == "win"
        )
        print(f"  {a} on {t} seed {s}: {w}/{c}")
    turns = [g["row"].get("turns") or 0 for g in games]
    if turns:
        print(f"game length: mean {st.mean(turns):.1f} turns, median {st.median(turns)}")

    # Openings.
    lead_rec: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    bring_rec: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    opp_faced: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    opp_brought: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    t1_wp: dict[str, list[float]] = defaultdict(list)
    preview_elapsed: list[float] = []
    preview_rounds: list[int] = []
    timing_ms: list[float] = []
    watchdog = 0
    per_game: list[dict[str, Any]] = []
    labels = Counter()
    labels_by_result: dict[str, Counter] = defaultdict(Counter)
    luck_by_turn: dict[int, list[float]] = defaultdict(list)
    exante_by_turn: dict[int, list[float]] = defaultdict(list)
    on_support = [0, 0]
    opp_prob: list[float] = []
    opp_top = [0, 0]
    preferred: Counter = Counter()
    preferred_kind: Counter = Counter()
    played_kind_bad: Counter = Counter()
    our_faints: Counter = Counter()
    their_faints: Counter = Counter()
    first_faint: Counter = Counter()
    first_faint_result: dict[str, Counter] = defaultdict(Counter)
    tags = Counter()
    mistakes: list[tuple[float, str, int, str, str, str]] = []
    kind_actual: dict[str, Counter] = defaultdict(Counter)
    kind_model: dict[str, Counter] = defaultdict(Counter)
    kind_n: Counter = Counter()

    for g in games:
        r = g["row"]
        res = r["result"]
        won = 1 if res == "win" else 0
        ev = g["events"]
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in ev:
            by_type[e["type"]].append(e)
        start = by_type["battle_start"][0]["payload"] if by_type["battle_start"] else {}
        us = str(start.get("player_role") or "p1")
        them = "p2" if us == "p1" else "p1"
        prev = by_type["preview_decision"][0]["payload"] if by_type["preview_decision"] else {}
        if prev:
            lead = "+".join(sorted(prev.get("lead", [])))
            bring = "+".join(sorted(prev.get("selected", [])))
            lead_rec[lead][0] += won
            lead_rec[lead][1] += 1
            bring_rec[bring][0] += won
            bring_rec[bring][1] += 1
            if prev.get("elapsed_s") is not None:
                preview_elapsed.append(prev["elapsed_s"])
            if prev.get("rounds") is not None:
                preview_rounds.append(prev["rounds"])
        for sp in start.get("opponent_team_preview", []):
            opp_faced[sp][0] += won
            opp_faced[sp][1] += 1
        # What the opponent actually brought: every species seen switching in on p2.
        seen = set()
        for e in by_type["turn_result"] + by_type["battle_end"]:
            obs = e["payload"].get("observations") or e["payload"].get("final_observations") or []
            for o in obs:
                if o.get("side") == them and o.get("attribute") == "switch":
                    seen.add(o["species"].lower())
                if o.get("attribute") == "faint":
                    (our_faints if o.get("side") == us else their_faints)[o["species"].lower()] += 1
        for sp in seen:
            opp_brought[sp][0] += won
            opp_brought[sp][1] += 1
        # First of ours to faint.
        faint_seq = []
        for e in by_type["turn_result"] + by_type["battle_end"]:
            obs = e["payload"].get("observations") or e["payload"].get("final_observations") or []
            for o in obs:
                if o.get("attribute") == "faint" and o.get("side") == us:
                    faint_seq.append((o.get("turn", 0), o.get("seq", 0), o["species"].lower()))
        if faint_seq:
            sp = sorted(faint_seq)[0][2]
            first_faint[sp] += 1
            first_faint_result[sp][res] += 1
        for e in by_type["timing"]:
            p = e["payload"]
            if p.get("phase") == "choose_move":
                timing_ms.append(p.get("total_ms") or 0)
                watchdog += 1 if p.get("watchdog_fired") else 0
        analyses = [
            e["payload"] for e in by_type["analysis"] if e["payload"].get("scope") == "turn"
        ]
        if not analyses:
            continue
        cands_by_turn: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for e in by_type["candidates"]:
            if "opponent_joint" in e["payload"]:
                cands_by_turn[e["payload"]["turn"]].append(e["payload"])
        cands_used: Counter = Counter()
        ea = sum(a.get("ex_ante_loss") or 0 for a in analyses)
        ep = sum(a.get("ex_post_loss") or 0 for a in analyses)
        lk = [a["luck"] for a in analyses if a.get("luck") is not None]
        t1 = next((a.get("win_prob_before") for a in analyses if a.get("turn") == 1), None)
        if t1 is not None:
            t1_wp[res].append(t1)
        per_game.append(
            {
                "result": res,
                "ex_ante": ea,
                "ex_post": ep,
                "luck": st.mean(lk) if lk else 0.0,
                "n": len(analyses),
                "opponent": r["opponent"],
                "t1": t1,
            }
        )
        for a in analyses:
            lab = a.get("classification")
            labels[lab] += 1
            labels_by_result[res][lab] += 1
            for t in a.get("tags") or []:
                tags[t] += 1
            if a.get("luck") is not None:
                luck_by_turn[min(a["turn"], 8)].append(a["luck"])
            exante_by_turn[min(a["turn"], 8)].append(a.get("ex_ante_loss") or 0)
            op = a.get("opponent_played")
            oe = a.get("opponent_equilibrium") or []
            if op and oe:
                lbl = op if isinstance(op, str) else op.get("label")
                match = next((c for c in oe if c.get("label") == lbl), None)
                on_support[1] += 1
                if match:
                    on_support[0] += 1
                    opp_prob.append(match.get("probability", 0.0))
                    opp_top[0] += 1 if oe[0].get("label") == lbl else 0
                else:
                    opp_prob.append(0.0)
                opp_top[1] += 1
            # The model's kind of turn against the realised one (D91).
            lst = cands_by_turn.get(a["turn"], [])
            if op and lst:
                c = lst[min(cands_used[a["turn"]], len(lst) - 1)]
                cands_used[a["turn"]] += 1
                lbl = op if isinstance(op, str) else op.get("label") or ""
                realised = _kind(lbl)
                cols = c.get("opponent_joint") or []
                probs = c.get("opponent_equilibrium") or []
                kinds_ = (c.get("column_prior") or {}).get("kinds") or [
                    _kind(col.get("label", "")) for col in cols
                ]
                if "none" not in realised and len(kinds_) == len(probs):
                    b = _bucket(a["turn"])
                    kind_n[b] += 1
                    kind_actual[b][realised] += 1
                    for k, pr in zip(kinds_, probs, strict=True):
                        kind_model[b][k] += pr
            if lab in ("mistake", "blunder"):
                best = a.get("best") or ""
                preferred[best] += 1
                preferred_kind[_kind(best)] += 1
                played_kind_bad[_kind(a.get("played_label") or "")] += 1
                mistakes.append(
                    (
                        a.get("ex_ante_loss") or 0,
                        r["opponent"],
                        a["turn"],
                        a.get("played_label") or "",
                        best,
                        res,
                    )
                )

    def rec(d: dict[str, list[int]], top: int) -> None:
        for k, (w, c) in sorted(d.items(), key=lambda kv: -kv[1][1])[:top]:
            print(f"  {k:48s} {w:3d}/{c:<3d} {pct(w / c)}%")

    print("\nleads (won/played):")
    rec(lead_rec, args.top)
    print("brings (won/played):")
    rec(bring_rec, args.top)
    print("opponent showed at preview (our won/played):")
    rec(opp_faced, args.top)
    print("opponent brought (our won/played):")
    rec(opp_brought, args.top)
    print("first of ours to faint (count: result):")
    for sp, c in first_faint.most_common():
        print(f"  {sp:16s} {c:3d}  {dict(first_faint_result[sp])}")
    print("our faints:", dict(our_faints.most_common()))
    print("their faints:", dict(their_faints.most_common(10)))

    if preview_elapsed:
        print(
            f"\npreview: elapsed mean {st.mean(preview_elapsed):.1f}s, "
            f"rounds {Counter(preview_rounds).most_common(4)}"
        )
    if timing_ms:
        s = sorted(timing_ms)
        print(
            f"turn clock: mean {st.mean(s) / 1000:.1f}s, "
            f"p95 {s[int(0.95 * (len(s) - 1))] / 1000:.1f}s, max {s[-1] / 1000:.1f}s, "
            f"watchdog fired {watchdog}"
        )

    if per_game:
        print("\ncoach per game (mean):")
        print(
            f"  {'':6s} {'n':>3s} {'ex-ante':>8s} {'ex-post':>8s} "
            f"{'luck/dec':>9s} {'t1 wp':>6s} {'dec/game':>8s}"
        )
        for res in ("win", "loss"):
            gs = [g for g in per_game if g["result"] == res]
            if not gs:
                continue
            t1s = [g["t1"] for g in gs if g["t1"] is not None]
            print(
                f"  {res:6s} {len(gs):3d} {100 * st.mean(g['ex_ante'] for g in gs):8.1f} "
                f"{100 * st.mean(g['ex_post'] for g in gs):8.1f} "
                f"{100 * st.mean(g['luck'] for g in gs):9.3f} "
                f"{(st.mean(t1s) if t1s else float('nan')):6.3f} {st.mean(g['n'] for g in gs):8.1f}"
            )
        print("labels:", dict(labels.most_common()))
        for res in ("win", "loss"):
            print(f"  {res}:", dict(labels_by_result[res].most_common()))
        print("tags:", dict(tags.most_common()))
        print(
            "luck by turn (mean, n):",
            {t: (round(100 * st.mean(v), 1), len(v)) for t, v in sorted(luck_by_turn.items())},
        )
        print(
            "ex-ante loss by turn (mean pts):",
            {t: round(100 * st.mean(v), 1) for t, v in sorted(exante_by_turn.items())},
        )
        if on_support[1]:
            print(
                f"\nopponent's line on the model's support: {on_support[0]}/{on_support[1]} "
                f"({pct(on_support[0] / on_support[1])}%); "
                f"mean prob given it {st.mean(opp_prob):.3f}; "
                f"was the model's top column {opp_top[0]}/{opp_top[1]}"
            )
        if kind_n:
            print("\nthe opponent's kind of turn, realised against the model's mass (D91):")
            for b in ("1", "2", "3", "4+"):
                n_b = kind_n[b]
                if not n_b:
                    continue
                print(f"  turn {b} (n={n_b})")
                ks = sorted(
                    set(kind_actual[b]) | set(kind_model[b]), key=lambda k: -kind_actual[b][k]
                )
                for k in ks:
                    print(
                        f"    {k:18s} realised {kind_actual[b][k] / n_b:6.1%}"
                        f"   model {kind_model[b][k] / n_b:6.1%}"
                    )
        print(
            "\non mistakes and blunders, the coach preferred (kind):",
            dict(preferred_kind.most_common()),
        )
        print("  and we played (kind):", dict(played_kind_bad.most_common()))
        print("  preferred lines:")
        for k, c in preferred.most_common(args.top):
            print(f"    {c:3d} {k}")
        print("\nworst decisions by ex-ante loss:")
        for loss, opp, turn, played, best, res in sorted(mistakes, reverse=True)[: args.top]:
            print(
                f"  {100 * loss:5.1f} t{turn:<2d} {res:4s} vs {opp:16s} "
                f"played {played!r:50s} best {best!r}"
            )


def _bucket(turn: int) -> str:
    return "1" if turn <= 1 else "2" if turn == 2 else "3" if turn == 3 else "4+"


def _kind(label: str) -> str:
    parts = [p.strip() for p in label.split("+")]
    kinds = []
    for p in parts:
        if p in ("no recorded action", "no action", "unrevealed"):
            kinds.append("none")
        elif p.startswith("switch"):
            kinds.append("switch")
        elif p.lower().startswith("protect") or p.lower().startswith("detect"):
            kinds.append("protect")
        elif p.lower().startswith("fake out"):
            kinds.append("fakeout")
        elif p == "pass":
            kinds.append("pass")
        else:
            kinds.append("attack")
    return "+".join(sorted(kinds))


if __name__ == "__main__":
    main()

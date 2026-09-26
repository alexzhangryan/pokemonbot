"""The teams the corpus plays, their records, leads and brings, as team files.

    python scripts/corpus_teams.py                    # the table
    python scripts/corpus_teams.py --write 10         # plus team files for the top ten
    python scripts/corpus_teams.py --six sneasler,floetteeternal,salamence,rillaboom,incineroar

Every M-C corpus replay carries open team sheets, so each side's six is
known with items, abilities, moves and natures -- and never stat points
(`docs/05-data-pipeline.md` section 5). This groups the sides by their six,
counts games and wins, and for each team records the most common set of each
member, the lead pairs and the brings people chose with it.

Two outputs:

- `data/policy/teams.<format>.json`: every team seen `--min` times or more,
  with its record, sets, leads and brings. `champions.search.lead` reads the
  leads and brings of the team being played as a prior (D95).
- With `--write N`, `data/teams/corpus-<rank>-<anchor>.txt` for the top N by
  games: the most common set of each member in Showdown's export format, the
  stat points chosen by the belief's own rule for a nature (D85, the two
  stats a registered spread of that nature most likely maxes), since the
  sheets never say. These are the candidates for the team tournament STATUS
  proposes: which of the teams people play does *this* agent play best.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from champions.belief.spreads import SpreadBelief  # noqa: E402
from champions.corpus.replay import parse_replay  # noqa: E402
from champions.corpus.store import CorpusStore  # noqa: E402
from champions.dex.loader import Dex, to_id  # noqa: E402
from champions.formats import BO3_FORMAT_ID, FORMAT_ID  # noqa: E402

DEFAULT_DB = Path("data/corpus.sqlite")
DEFAULT_LOGS = Path("data/replays")
TEAMS_DIR = Path("data/teams")
STAT_LABELS = {"hp": "HP", "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}


def team_key(species: list[str]) -> str:
    return "+".join(sorted(to_id(s) for s in species))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--format", default=BO3_FORMAT_ID)
    parser.add_argument("--play-format", default=FORMAT_ID)
    parser.add_argument("--min", type=int, default=3, help="games a team needs to be listed")
    parser.add_argument("--top", type=int, default=20, help="rows to print")
    parser.add_argument("--write", type=int, default=0, help="team files for the top N")
    parser.add_argument("--six", help="print one team's entry, six species comma-separated")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    dex = Dex.load(args.play_format)
    teams: dict[str, dict[str, Any]] = {}
    with CorpusStore(args.db, args.logs) as store:
        for _, replay_id in store.stored_logs(args.format):
            record = parse_replay(replay_id, store.read_log(args.format, replay_id))
            if not record.sheets_revealed or record.result == "tie":
                continue
            winner = record.winner_side
            for side in ("p1", "p2"):
                sets = [s for s in record.sets if s.side == side]
                if len(sets) != 6:
                    continue
                key = team_key([s.species for s in sets])
                entry = teams.setdefault(
                    key,
                    {
                        "six": sorted(to_id(s.species) for s in sets),
                        "games": 0,
                        "wins": 0,
                        "sets": defaultdict(Counter),
                        "leads": Counter(),
                        "brings": Counter(),
                        "replays": [],
                    },
                )
                entry["games"] += 1
                entry["wins"] += int(winner == side)
                entry["replays"].append(replay_id)
                for s in sets:
                    entry["sets"][to_id(s.species)][
                        json.dumps(
                            {
                                "species": s.species,
                                "item": s.item,
                                "ability": s.ability,
                                "moves": list(s.moves),
                                "nature": s.nature,
                            },
                            sort_keys=True,
                        )
                    ] += 1
                previews = [p for p in record.previews if p.side == side]
                lead = sorted(to_id(p.species) for p in previews if p.lead)
                brought = sorted(to_id(p.species) for p in previews if p.appeared)
                if len(lead) == 2:
                    entry["leads"]["+".join(lead)] += 1
                if 2 <= len(brought) <= 4:
                    entry["brings"]["+".join(brought)] += 1

    listed = []
    for key, entry in teams.items():
        if entry["games"] < args.min:
            continue
        common = {
            species: json.loads(counter.most_common(1)[0][0])
            for species, counter in entry["sets"].items()
        }
        listed.append(
            {
                "key": key,
                "six": entry["six"],
                "games": entry["games"],
                "wins": entry["wins"],
                "win_rate": round(entry["wins"] / entry["games"], 3),
                "sets": common,
                "leads": dict(entry["leads"].most_common()),
                "brings": dict(entry["brings"].most_common()),
                "replays": entry["replays"][:50],
            }
        )
    listed.sort(key=lambda t: (-t["games"], -t["wins"]))

    out_path = args.json or Path("data/policy") / f"teams.{args.play_format}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "format_id": args.play_format,
                "corpus_format": args.format,
                "written_at": datetime.now(UTC).isoformat(),
                "distinct_teams": len(teams),
                "listed": len(listed),
                "min_games": args.min,
                "teams": listed,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(teams)} distinct teams, {len(listed)} with {args.min}+ games -> {out_path}")

    if args.six:
        key = team_key(args.six.split(","))
        match = next((t for t in listed if t["key"] == key), None)
        print(json.dumps(match, indent=1) if match else f"no team {key} with {args.min}+ games")
        return

    print(f"{'rank':>4s} {'games':>5s} {'won':>4s} {'rate':>5s}  six")
    for rank, t in enumerate(listed[: args.top], 1):
        print(
            f"{rank:4d} {t['games']:5d} {t['wins']:4d} {t['win_rate']:5.2f}  {' '.join(t['six'])}"
        )

    for rank, t in enumerate(listed[: args.write], 1):
        anchor = t["six"][0]
        path = TEAMS_DIR / f"corpus-{rank:02d}-{anchor}.txt"
        path.write_text(export_team(t, dex), encoding="utf-8")
        print(f"  wrote {path}")


def export_team(team: dict[str, Any], dex: Dex) -> str:
    """The team in Showdown's export format, points by the belief's rule."""
    blocks = []
    for species in team["six"]:
        s = team["sets"][species]
        entry = dex.species.get(to_id(s["species"])) or {}
        nature = to_id(s.get("nature") or "serious")
        try:
            nature_entry = dex.nature(nature)
        except KeyError:
            nature, nature_entry = "serious", {}
        spread = SpreadBelief.unconstrained(entry.get("baseStats") or {}, nature, nature_entry)
        points = spread.allocation()
        evs = " / ".join(f"{v} {STAT_LABELS[k]}" for k, v in points.items() if v)
        lines = [f"{s['species']}" + (f" @ {s['item']}" if s.get("item") else "")]
        if s.get("ability"):
            lines.append(f"Ability: {s['ability']}")
        lines.append("Level: 50")
        if evs:
            lines.append(f"EVs: {evs}")
        lines.append(f"{nature.capitalize()} Nature")
        lines.extend(f"- {m}" for m in s["moves"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


if __name__ == "__main__":
    main()

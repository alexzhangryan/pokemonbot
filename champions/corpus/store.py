"""SQLite storage for the replay corpus.

`docs/05-data-pipeline.md` section 6: SQLite is sufficient at this scale and
keeps the whole corpus one portable file, which matters for reproducibility.

Two rules the schema is built around.

Raw logs live on disk, not in the database, and are never re-fetched. The
database holds the path and the log's SHA-256; `--reparse` rebuilds every
derived table from those files with no network access at all. That is what makes
being wrong about the parser cheap, which we should assume we are.

Derived tables are keyed by `(replay_id, ...)` and deleted before rewrite, so
re-parsing is idempotent. `replays.parser_version` records which version of the
parser produced the rows, so a corpus that is half-reparsed is detectable rather
than merely wrong.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from champions.corpus.replay import PARSER_VERSION, ReplayRecord

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replays (
    id TEXT PRIMARY KEY,
    format_id TEXT NOT NULL,
    format_name TEXT,
    uploadtime INTEGER,
    p1 TEXT, p2 TEXT,
    p1_rating INTEGER, p2_rating INTEGER,
    p1_rating_after INTEGER, p2_rating_after INTEGER,
    rated INTEGER NOT NULL DEFAULT 0,
    winner TEXT, winner_side TEXT, result TEXT,
    turns INTEGER,
    p1_teamsize INTEGER, p2_teamsize INTEGER,
    sheets_revealed INTEGER NOT NULL DEFAULT 0,
    bring_fully_observed INTEGER NOT NULL DEFAULT 0,
    series_id TEXT, game_number INTEGER,
    log_path TEXT, log_sha256 TEXT,
    unhandled TEXT,
    parser_version INTEGER, scraped_at INTEGER, parsed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_replays_format ON replays(format_id, uploadtime);
CREATE INDEX IF NOT EXISTS idx_replays_series ON replays(series_id, game_number);

CREATE TABLE IF NOT EXISTS previews (
    replay_id TEXT NOT NULL,
    side TEXT NOT NULL,
    slot_index INTEGER NOT NULL,
    species TEXT NOT NULL,
    details TEXT,
    appeared INTEGER NOT NULL DEFAULT 0,
    lead INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (replay_id, side, slot_index)
);
CREATE INDEX IF NOT EXISTS idx_previews_species ON previews(species);

CREATE TABLE IF NOT EXISTS sets (
    replay_id TEXT NOT NULL,
    side TEXT NOT NULL,
    species TEXT NOT NULL,
    nickname TEXT,
    item TEXT, ability TEXT, moves TEXT,
    nature TEXT, points TEXT, gender TEXT, ivs TEXT, level INTEGER,
    source TEXT NOT NULL,
    PRIMARY KEY (replay_id, side, species)
);
CREATE INDEX IF NOT EXISTS idx_sets_species ON sets(species);

CREATE TABLE IF NOT EXISTS actions (
    replay_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    turn INTEGER NOT NULL,
    side TEXT NOT NULL,
    slot TEXT,
    species TEXT,
    action TEXT NOT NULL,
    value TEXT,
    target TEXT,
    detail TEXT,
    PRIMARY KEY (replay_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_actions_species ON actions(species, action, value);

CREATE TABLE IF NOT EXISTS reveals (
    replay_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    turn INTEGER NOT NULL,
    side TEXT NOT NULL,
    slot TEXT,
    species TEXT,
    attribute TEXT NOT NULL,
    value TEXT,
    detail TEXT,
    PRIMARY KEY (replay_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_reveals_attribute ON reveals(attribute, species, value);
"""


@dataclass(frozen=True)
class CorpusStats:
    replays: int
    with_sheets: int
    sets: int
    previews: int
    actions: int
    reveals: int
    by_format: dict[str, int]


class CorpusStore:
    """The corpus, as one SQLite file plus a directory of raw logs.

    `reveals` holds the complete observation stream and `actions` holds the
    subset of it that were choices. They overlap by design: the two answer
    different questions -- what a watcher learned, and what a player did -- and
    both are keyed by `(replay_id, seq)` so a consumer that wants one view can
    join or filter to it without re-deriving anything.
    """

    def __init__(self, db_path: Path | str, logs_dir: Path | str | None = None) -> None:
        self.db_path = Path(db_path)
        self.logs_dir = Path(logs_dir) if logs_dir else self.db_path.parent / "replays"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> CorpusStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- raw logs ---------------------------------------------------------

    def log_path(self, format_id: str, replay_id: str) -> Path:
        return self.logs_dir / format_id / f"{replay_id}.log"

    def has_log(self, format_id: str, replay_id: str) -> bool:
        """Whether the raw log is already on disk. The never-re-fetch guard."""
        return self.log_path(format_id, replay_id).exists()

    def write_log(self, format_id: str, replay_id: str, log: str) -> Path:
        path = self.log_path(format_id, replay_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(log, encoding="utf-8")
        return path

    def read_log(self, format_id: str, replay_id: str) -> str:
        return self.log_path(format_id, replay_id).read_text(encoding="utf-8")

    def known_ids(self, format_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT id FROM replays WHERE format_id = ?", (format_id,)
        ).fetchall()
        return {row["id"] for row in rows}

    def has_replay(self, replay_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM replays WHERE id = ?", (replay_id,)).fetchone()
        return row is not None

    def stored_logs(self, format_id: str | None = None) -> list[tuple[str, str]]:
        """`(format_id, replay_id)` for every raw log on disk, parsed or not."""
        roots = (
            [self.logs_dir / format_id]
            if format_id
            else sorted(p for p in self.logs_dir.iterdir() if p.is_dir())
        )
        found: list[tuple[str, str]] = []
        for root in roots:
            if root.exists():
                found.extend((root.name, path.stem) for path in sorted(root.glob("*.log")))
        return found

    # -- writes -----------------------------------------------------------

    def upsert(
        self,
        record: ReplayRecord,
        log_path: Path | str | None = None,
        scraped_at: int | None = None,
    ) -> None:
        """Write one record, replacing any derived rows it already has.

        Idempotent by construction: the four derived tables are deleted for this
        replay before insert, so re-parsing the same log twice leaves the corpus
        in the same state as parsing it once.
        """
        now = int(time.time())
        path = str(log_path) if log_path else str(self.log_path(record.format_id, record.replay_id))
        existing = self.conn.execute(
            "SELECT scraped_at FROM replays WHERE id = ?", (record.replay_id,)
        ).fetchone()
        first_seen = scraped_at or (existing["scraped_at"] if existing else now)

        with self.conn:
            for table in ("previews", "sets", "actions", "reveals"):
                self.conn.execute(f"DELETE FROM {table} WHERE replay_id = ?", (record.replay_id,))
            self.conn.execute(
                """INSERT OR REPLACE INTO replays
                   (id, format_id, format_name, uploadtime, p1, p2, p1_rating, p2_rating,
                    p1_rating_after, p2_rating_after, rated, winner, winner_side, result, turns,
                    p1_teamsize, p2_teamsize, sheets_revealed, bring_fully_observed,
                    series_id, game_number, log_path, log_sha256, unhandled,
                    parser_version, scraped_at, parsed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.replay_id,
                    record.format_id,
                    record.format_name,
                    record.uploadtime,
                    record.players[0],
                    record.players[1],
                    record.ratings[0],
                    record.ratings[1],
                    record.ratings_after[0],
                    record.ratings_after[1],
                    int(record.rated),
                    record.winner,
                    record.winner_side,
                    record.result,
                    record.turns,
                    record.teamsize.get("p1"),
                    record.teamsize.get("p2"),
                    int(record.sheets_revealed),
                    int(record.bring_fully_observed),
                    record.series_id,
                    record.game_number,
                    path,
                    record.log_sha256,
                    json.dumps(record.unhandled),
                    PARSER_VERSION,
                    first_seen,
                    now,
                ),
            )
            self.conn.executemany(
                """INSERT OR REPLACE INTO previews
                   (replay_id, side, slot_index, species, details, appeared, lead)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        record.replay_id,
                        p.side,
                        p.index,
                        p.species,
                        p.details,
                        int(p.appeared),
                        int(p.lead),
                    )
                    for p in record.previews
                ],
            )
            self.conn.executemany(
                """INSERT OR REPLACE INTO sets
                   (replay_id, side, species, nickname, item, ability, moves, nature,
                    points, gender, ivs, level, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        record.replay_id,
                        s.side,
                        s.species,
                        s.nickname,
                        s.item,
                        s.ability,
                        ",".join(s.moves),
                        s.nature,
                        s.points,
                        s.gender,
                        s.ivs,
                        s.level,
                        "showteam",
                    )
                    for s in record.sets
                ],
            )
            self.conn.executemany(
                """INSERT OR REPLACE INTO actions
                   (replay_id, seq, turn, side, slot, species, action, value, target, detail)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        record.replay_id,
                        a.seq,
                        a.turn,
                        a.side,
                        a.slot,
                        a.species,
                        a.action,
                        a.value,
                        a.target,
                        json.dumps(a.detail),
                    )
                    for a in record.actions
                ],
            )
            self.conn.executemany(
                """INSERT OR REPLACE INTO reveals
                   (replay_id, seq, turn, side, slot, species, attribute, value, detail)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        record.replay_id,
                        o.seq,
                        o.turn,
                        o.side,
                        o.slot,
                        o.species,
                        o.kind,
                        o.value,
                        json.dumps(o.detail),
                    )
                    for o in record.observations
                ],
            )

    def stats(self) -> CorpusStats:
        def count(sql: str) -> int:
            row = self.conn.execute(sql).fetchone()
            return int(row[0]) if row else 0

        by_format = {
            row["format_id"]: row["n"]
            for row in self.conn.execute(
                "SELECT format_id, COUNT(*) AS n FROM replays GROUP BY format_id"
            )
        }
        return CorpusStats(
            replays=count("SELECT COUNT(*) FROM replays"),
            with_sheets=count("SELECT COUNT(*) FROM replays WHERE sheets_revealed = 1"),
            sets=count("SELECT COUNT(*) FROM sets"),
            previews=count("SELECT COUNT(*) FROM previews"),
            actions=count("SELECT COUNT(*) FROM actions"),
            reveals=count("SELECT COUNT(*) FROM reveals"),
            by_format=by_format,
        )

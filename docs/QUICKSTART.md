# Quickstart

How to set the project up and manually exercise everything M0 built.

Everything below is run from the repository root, `C:\dev\pokemonbot`. Commands
use forward-slash paths (`.venv/Scripts/python.exe`), which work unmodified in
both PowerShell and Git Bash — the two shells this was tested from. They also
work with the venv activated (`python` alone is then enough), so if you've
already run `.venv\Scripts\Activate.ps1` (PowerShell) or
`source .venv/Scripts/activate` (Git Bash), feel free to drop the
`.venv/Scripts/` prefix.

## 1. Prerequisites

| Tool | Version used | Check |
| --- | --- | --- |
| Python | 3.12.10 | `python --version` |
| Node.js | 24.19.0 (LTS) | `node --version` |
| Git | 2.55 | `git --version` |
| GNU Make | 4.4.1 (optional) | `make --version` |

Python, Node, and Make were all installed with `winget`
(`Python.Python.3.12`, `OpenJS.NodeJS.LTS`, `ezwinports.make`). If a command
is "not recognized", open a new terminal: `winget` updates `PATH` only for
new shells.

Make is optional — every command below has a raw `.venv/Scripts/python.exe
scripts/...` form — but if it's installed, `make help` lists shortcuts for
everything in this guide and they're shown alongside each section.

## 2. One-time setup

```powershell
# Python environment
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# Showdown simulator
git clone https://github.com/smogon/pokemon-showdown.git vendor/showdown
cd vendor/showdown
npm install
node build
cd ../..

# The resolved Champions dex (gitignored, regenerated locally)
.venv/Scripts/python.exe scripts/build_dex.py gen9championsvgc2026regmb --delta
```

Or, with `make`: `make venv`, then `make vendor` (clones, checks out the
pinned commit, builds), then `make dex`.

Pin the checkout to the commit in `vendor/SHOWDOWN_COMMIT` before `npm install`:

```powershell
# PowerShell, from vendor/showdown
git checkout (Get-Content ../SHOWDOWN_COMMIT)
```

```bash
# Git Bash, from vendor/showdown
git checkout "$(cat ../SHOWDOWN_COMMIT)"
```

The last step writes `data/dex/gen9championsvgc2026regmb.<hash>.json` and
regenerates `docs/dex-delta.md`. The hash is content-addressed: an unchanged
vendor build always produces the same one.

## 3. Run the tests

```powershell
.venv/Scripts/python.exe -m pytest       # or: make test
```

About 90 tests in roughly 40 seconds. They start and stop their own Showdown
server, so nothing needs to be running first. Also available:

```powershell
.venv/Scripts/python.exe -m ruff check .      # lint       (make lint)
.venv/Scripts/python.exe -m ruff format .     # format     (make format)
.venv/Scripts/python.exe -m mypy .            # types      (make typecheck)
```

`make check` runs lint, typecheck, and test in one go.

## 4. Open the viewer

One command. It starts the Showdown simulator itself, opens a window, and gives
you buttons for everything else:

```powershell
.venv/Scripts/python.exe scripts/viewer.py    # or: make viewer
```

```
viewer  http://127.0.0.1:8100/
traces  C:\dev\pokemonbot\traces
sim     starting on port 8090
```

The window that opens is the bot's-eye view. The centre is a battle stage —
opponent above, the real Showdown battle animation in the middle, us below — and
the slot the agent is acting with and the slots it is aiming at are marked on
the panels, including when it points a move at its own partner.

Around the stage are the legal action set the agent chose from, the protocol log
of what actually happened, and the clock. Panels for things that are not built
yet (win probability, damage rolls, the belief filter, the mixed strategy) are
hatched and tagged with the milestone that fills them, so an empty panel is
never mistaken for a measured zero.

The animation is Showdown's own renderer replaying this battle's protocol log
out of the trace, so it scrubs with the turn list. It needs internet for the
renderer and its sprites, which come from Smogon; everything else in the viewer
works offline, and the frame says so if it cannot load. It is drawn from the
bot's side of the field, not p1's. Hover it for a speed control — Showdown's own
presets, plus **skip animations** — and a **hide** button; both choices are
remembered. Moves animate while a battle is live and land instantly when you
scrub, which is the distinction that makes a spine and an animation coexist. The renderer is *mainline* Showdown's, so
a Champions-only forme can ask for a sprite that does not exist upstream (Mega
Greninja does today) — that shows as a missing image in the animation and
nothing else. The panels above and below it are drawn from the Champions dex,
which is the half that is always right.

The bar under the header runs everything, left to right:

- **sim** — a dot and a word. The simulator starts on its own when the viewer
  does. If something is already listening on that port the viewer adopts it and
  will not stop it, since it did not start it.
- **self-play** — games, the baseline on each side, and a seed.
- **play the bot** — puts a bot up waiting to be challenged. A single line then
  appears with its name, the format, the team to import, and **Open Showdown**.
- the current run's progress, with **output** for the full log and **stop**.

Everything started here writes into the directory the viewer is already
watching, so a battle you launch cannot fail to show up. Arrow keys scrub the
turn list. The viewer never talks to the agent or to Showdown and cannot
influence play.

Flags: `--no-server` to leave the simulator alone, `--no-open` to just serve it,
`--tab` for a browser tab instead of an app window, `--port` / `--showdown-port`,
and a directory argument to watch somewhere other than `traces/`.

## 5. Play against the bot yourself

In the control panel, under **Play the bot**, pick an agent and press **Put the
bot up**. The panel then shows the four steps and a link:

1. Open Showdown and pick any username. The link goes to
   <http://localhost:8090>, which redirects to `https://localhost--8090.psim.us`
   — the official Showdown client UI, loaded from Smogon but connected to *your*
   local server. This needs internet for the client assets; the battles
   themselves are entirely local.
2. Open the teambuilder and paste in `data/teams/regmb-alpha.txt`.
3. Find Users → `champbot` → Challenge, in
   **[Gen 9 Champions] VGC 2026 Reg M-B**.
4. Decline Open Team Sheets if prompted — the bot always declines, by design,
   because Champions has no such mechanism.

Switch back to the viewer as you play; it attaches to the battle on its own and
follows it live.

The equivalent by hand, if you want the bot in its own terminal:

```powershell
.venv/Scripts/python.exe scripts/run_local_server.py 8090        # or: make server
.venv/Scripts/python.exe scripts/play_human.py --agent greedy    # or: make play
```

If you do it this way, point the viewer at the same directory the agent writes
to (`scripts/viewer.py runs/human`) — mismatching those is the one way to end up
with a viewer that shows nothing.

## 6. Watch two bots play each other

**Self-play** in the control panel, or:

```powershell
.venv/Scripts/python.exe scripts/selfplay.py 50   # or: make selfplay GAMES=50
.venv/Scripts/python.exe scripts/selfplay.py 20 --agent-a greedy --agent-b random
```

```
finished: 50/50 battles
  champ-a: 14 wins
  champ-b: 36 wins
protocol failures (invalid choice / timeout): 0
traces: 100 written, 0 invalid
```

Two traces per battle, one per agent's own view, because each agent only ever
sees its own side. The viewer follows the newest *battle* rather than the newest
file, so it does not flip between the two views of one game.

## 7. Read a decision trace

Traces are append-only JSONL, one file per agent-view of a battle:

```powershell
.venv/Scripts/python.exe scripts/show_trace.py            # most recent under traces/  (make trace)
.venv/Scripts/python.exe scripts/show_trace.py runs/human # or a directory   (make trace TRACE=runs/human)
.venv/Scripts/python.exe scripts/show_trace.py path/to/one.jsonl --full
```

```
valid: yes

   0  battle_start      {"format_id": "gen9championsvgc2026regmb", "player_role": "p2", ...}
   1  preview_decision  {"order": "/team 6215", "selected": ["gyarados", "tyranitar", ...]}
   2  turn_start        {"turn": 1, "active": ["milotic", "tyranitar"], ...}
   3  timing            {"turn": 1, "total_ms": 0.29, "watchdog_fired": false, ...}
   4  equilibrium       {"turn": 1, "chosen": "/choose move icebeam 1, move earthquake", ...}
```

Every event carries `schema_version`, `battle_id`, `seq`, and `t`. What to look
for:

- `battle_start` — the six species each side revealed at preview, and
  `accept_open_team_sheet: false`.
- `preview_decision` — which four were brought (bring 6, pick 4).
- `turn_start` — the full observable state, plus the protocol lines seen since
  the previous decision. Our own side reports exact HP, stats, items and PP;
  the opponent's reports only what has been revealed, with unknowns as null.
- `candidates` — the legal action set, per slot and as joint actions, each
  described rather than only encoded. `annotations_pending` names the columns
  the search layer does not fill yet.
- `equilibrium` — the action chosen out of `n_legal_joint_actions` (about 98 in
  a typical mid-game doubles turn), and `pending` for the parts of a real
  equilibrium that arrive at M5.
- `timing` — per-decision latency plus `watchdog_fired` and `exceeded_45s`.

Traces are considerably larger than at T0.4, roughly 300KB to 1MB per battle,
because `candidates` enumerates the whole legal joint action set. That shrinks
once M2 prunes it.

## 8. Evaluate agents against each other

```powershell
.venv/Scripts/python.exe scripts/run_ladder.py 50   # or: make ladder GAMES=50
```

```
arm                    games  win rate           95% CI   p50 ms   p95 ms    max ms    >45s  worst battle  clock ok
-------------------------------------------------------------------------------------------------------------------
random                    50    20.0%   [11.2%, 33.0%]     0.16     0.33     16.31   0.0%          0.0s       yes
max-base-power            50    80.0%   [67.0%, 88.8%]     0.29     0.62      6.28   0.0%          0.0s       yes
```

Win rate and clock compliance are deliberately in the same table, so a latency
regression is visible next to the win rate that bought it. `--seed N` makes a
run reproducible; the same seed gives the same games.

## 9. Benchmark the simulator

```powershell
.venv/Scripts/python.exe scripts/bench.py   # or: make bench
```

Writes `docs/benchmarks.md` and compares local throughput against the reference
figures. On this machine a clone plus a step costs about 2.1 ms, against 4.7 ms
for the reference container.

## 10. Check simulator determinism

```powershell
.venv/Scripts/python.exe scripts/differential.py 1000   # or: make differential GAMES=1000
```

```
generated 1000 positions (1000 distinct) in 5.1s
checked determinism in 8.5s
divergences: 0
all positions self-consistent under a fixed seed
```

Exits non-zero if anything diverges. At M0 there is no second implementation, so
this checks the simulator against itself; the same harness compares a custom
engine at M8.

## 11. Troubleshooting

**`node` or `python` not recognized.** Open a new terminal.

**`DexNotBuiltError`.** Run the `scripts/build_dex.py` line from step 2.

**"Multiple dex dumps ... ambiguous which build is current".** You rebuilt
Showdown at a different commit. Delete the stale
`data/dex/gen9championsvgc2026regmb.*.json` and rebuild.

**Backslash paths don't work in Git Bash.** `.venv\Scripts\python.exe` (or
anything else with backslashes) only works in PowerShell/cmd. In Git Bash use
forward slashes: `.venv/Scripts/python.exe` — every command in this guide
already uses the slash form for exactly this reason, or use `make`, which
works from either shell.

**Port 8090 already in use.** A server is still running. Find and stop it:

```powershell
# PowerShell
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -like '*pokemon-showdown*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

```bash
# Git Bash
netstat -ano | grep ':8090.*LISTENING' | awk '{print $5}' | xargs -r -I{} powershell.exe -c "Stop-Process -Id {} -Force"
```

**`ConnectionResetError` / `no close frame received` at the end of a run.**
Shutdown ordering noise when the server goes away while sockets are still open.
Harmless, and the scripts disconnect cleanly to avoid it.

**Tests are slow the first time.** The session-scoped fixture builds and starts
a Showdown server once, which takes a few seconds.

## 12. Build the replay corpus

The corpus is scraped from Showdown's public replay API. Two format IDs, for two
different reasons: `gen9championsvgc2026regmb` is ordinary ladder play under
hidden information, and `gen9championsvgc2026regmbbo3` forces open team sheets,
so every replay reveals both players' complete sets.

```bash
make scrape          # fetch anything new for both formats, seconds if caught up
make corpus          # report what is stored
make scrape-full     # backfill the Bo3 corpus to exhaustion; hours, resumable
```

Or the script directly, which has the knobs:

```bash
python scripts/scrape_replays.py --format gen9championsvgc2026regmbbo3 --max-replays 200
python scripts/scrape_replays.py --reparse    # rebuild the tables from stored logs
```

Three things worth knowing before you run it.

**It is slow on purpose.** One request per second against a service nobody is
paying for. `--interval` changes it; please do not make it much smaller.

**It is resumable and never re-fetches.** A replay whose raw log is on disk is
never requested again. Kill a run whenever you like and start it again; it loses
at most one replay.

**Parsing is separate from fetching.** Raw logs go to `data/replays/`, the
derived tables to `data/corpus.sqlite`, and both are gitignored. When the parser
improves, `--reparse` rebuilds everything from disk with no network access at
all. That is the whole reason the raw logs are kept.

The corpus is a plain SQLite file, so the easiest way to look at it is SQL:

```bash
python -c "import sqlite3; c=sqlite3.connect('data/corpus.sqlite'); print(c.execute('SELECT item, COUNT(*) n FROM sets GROUP BY item ORDER BY n DESC LIMIT 10').fetchall())"
```

Tables: `replays`, `previews` (all six per side, flagged for who played and who
led), `sets` (complete sets, open-sheet games only), `actions` (moves and
switches in order) and `reveals` (the full observation stream). `actions` and
`reveals` are both keyed on `(replay_id, seq)`, and `seq` is ordered, because the
order moves resolve in is the only Speed evidence a replay contains.

## 13. What is not built yet

M0 through M3 are done. What that leaves:

- No opponent model. The agent's belief about the opponent is "their revealed
  moves, and nothing if they have revealed none", so on turn one the matrix game
  has a single column. The belief filter is M5, and it is what the corpus was
  built to feed.
- No bring-4 or lead prediction, and no preview equilibrium. That is M4, and it
  is next.
- The evaluation function is hand-weighted and says so (`IS_CALIBRATED = False`).
  M6 fits it and requires a reliability diagram before it is used anywhere.
- No coach and no game review client. The live view (section 4) is built and
  renders everything the agent emits; the review overlay — move classification,
  ex-ante and ex-post loss, explanations — is M9.

See `docs/STATUS.md` for where things actually stand and `docs/01-plan.md` for
what comes next.

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

# The belief filter's set prior, distilled from the replay corpus (section 13).
# Optional: without it the agents still play, with no belief.
.venv/Scripts/python.exe scripts/build_priors.py
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
.venv/Scripts/python.exe scripts/play_human.py --agent belief    # or: make play
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

## 13. Build the belief filter's prior, and watch it work

The belief filter (M5) is what turns "six species and nothing else" into a
distribution over the opponent's items, abilities, moves, natures and stat
points. It needs one artifact, distilled from the corpus in about a second:

```bash
make priors          # or: python scripts/build_priors.py
```

That writes `data/priors/setprior.<hash>.json` — gitignored, like the dex dump,
because it is derived and the corpus is the thing worth keeping. Rebuild it
whenever the corpus grows. Without it every agent still runs; the belief simply
reports itself as unavailable and `battle_start` records `"belief": false`.

With it built, the `belief` agent is available everywhere the others are:

```bash
python scripts/play_human.py --agent belief          # or: make play AGENT=belief
python scripts/selfplay.py 10 --agent-a belief --agent-b oneply --team-a regmb-beta --team-b regmb-beta
```

and in the viewer's control panel, under both **Self-play** and **Play the bot**.

### What to look at

Open the viewer and pick a battle. The **Opponent** panel on the right is the
belief. Per Pokemon it shows what has actually been revealed, then four ranked
posteriors — item, ability, nature, moves — and then one bar per stat.

Each bar has two marks on a 0–32 point scale. The wide translucent band is the
union over live particles: the filter is not more certain about a stat than its
least certain surviving hypothesis. The solid marker inside it is the modal
particle's box, which is what the search actually reads. A marker much narrower
than the band means the belief has concentrated; a band that never shrinks means
nothing has been learned about the spread.

The panel header carries the population: how many particles are alive, the
effective sample size, and how many times the filter has resampled. A resample
is normal — it happens when a reveal kills most of the population, and the new
draw comes from the prior restricted to everything revealed so far.

The four things worth doing by hand, because each exercises a different half:

- **Watch an item get pinned.** Bring something with a Sitrus Berry and let it
  proc. The item posterior for that Pokemon goes to 100%, and — this is the part
  worth watching — that item drops out of the other five, because Item Clause
  says a team holds one of each.
- **Watch a move narrow.** Use a move the corpus rarely sees on that species.
  The set posterior collapses onto whichever registered sets contain it, and if
  none do, the filter falls back to composed sets rather than concluding the
  Pokemon is impossible.
- **Watch Speed narrow.** Outspeed something, or be outsped. A same-priority
  ordering is a strict inequality against a Speed we know exactly, and it is
  usually the first bar to move.
- **Watch it be wrong.** Run a team the corpus has never seen. The prior will be
  confidently wrong about items, and the reveals will correct it turn by turn.
  That is the shape of the thing working.

### Measure it, do not eyeball it

```bash
make eval-belief                                    # against data/teams/regmb-beta.txt
python scripts/eval_belief.py corpus --replays 200  # against real ladder teams
```

Two evaluation sets, because neither covers the other. `traces` scores the
`belief` events out of self-play traces against the team file the opponent
actually played — the only source in the project that carries stat points, and
therefore the only source of **interval coverage**. `corpus` runs the filter
over stored forced-open-sheet Bo3 replays, where the registered set is stated at
turn 0 and the filter is never shown it, which is where the item, ability,
nature and moveset numbers come from.

Coverage is the number to watch. It is the fraction of the time the true stat
point value falls inside the maintained interval, and it is reported twice — for
the box the search reads and for the union over particles. Below the nominal
level means the filter is eliminating the truth, which `CLAUDE.md` constraint 5
calls the single most likely source of a silent correctness bug in the system.
The turn-1 row is the prior with no in-battle updating, so the difference
between it and the last row is what the filter actually contributes.

## 14. Fit the evaluation function

The bar at the top of the viewer is a win probability, and it is only a
probability because it has been fit and measured. Two commands, and they are
separated because one takes hours and the other takes seconds.

```bash
make eval-games    # 750 self-play games -> runs/m6-selfplay/ (hours)
make fit-eval      # fit both sources, ship one, write the diagram (seconds)
```

`make fit-eval` writes two files. `data/eval/weights.<format>.json` is what
`champions/search/evaluate.py` loads on import — and its mere existence is what
makes `IS_CALIBRATED` True, so there is no way to claim calibration without
having run the fit that measures it. `docs/eval-calibration.md` is the
reliability diagram `docs/04-decision-engine.md` section 5 requires before that
number is read as a probability anywhere.

Read the diagram before trusting the model. The number to watch is the expected
calibration error, and beside it the per-bin table: for each band of predicted
probability, how often the model said it and how often it was right. A model can
improve its average log loss while being systematically overconfident, and
overconfidence is the failure that matters, because the coach reports loss in
probability units and the matrix game backs values up through this.

Two things in that report are worth understanding rather than skimming:

- **Every weight comes with a 95% interval, bootstrapped over battles.** A
  weight whose interval spans zero is one that source did not settle the sign
  of. This is not decoration: self-play on the two checked-in teams produced
  `status_advantage` at -1.34 — the sign that says being burned is good — from
  291 rows out of 11,774, and it looked exactly like the six numbers beside it.
- **The shipping model is a blend, and says which weights came from where.**
  Self-play is preferred (ladder outcomes are skill dominated, D39) but cannot
  settle every weight from two teams that carry no Tailwind and no hazards, so
  those come from the corpus. The blend is re-calibrated and re-measured, so the
  diagram describes the model that ships.

Without a weights file everything still works: the evaluation falls back to the
hand-chosen weights, `IS_CALIBRATED` is False, and the viewer draws the bar
hatched and labelled "not a probability". That path is the normal state of a
fresh clone — the agent has to play before the fit has anything to read.

## 15. What is not built yet

M0 through M6 are done. What that leaves:

- The preview equilibrium is built and exact, and its value function is not
  wired into play: M4 could not fit one from replay outcomes, because skill
  dominates at that sample size. Self-play is the recommended source, and M6
  has now shown it works — with the caveat that two teams do not cover enough
  of the game for every feature.
- Search is one ply. The turn model scores a switch as giving up the turn, which
  is a real and intended bias that depth would fix; M8 weighs depth against the
  alternatives.
- The policy layer has one provider, the heuristic. M7 benchmarks it against a
  learned prior and a language model on decision quality, discard rate and
  latency — and `policy.discard_rate`, the guard `docs/04` section 3 requires,
  has still never been run against a real position.
- No coach and no game review client. The live view (section 4) is built and
  renders everything the agent emits; the review overlay — move classification,
  ex-ante and ex-post loss, explanations — is M9. Note that nothing currently
  checks `IS_CALIBRATED` before reporting; whoever writes the coach has to
  decide what it does when the flag is False.

See `docs/STATUS.md` for where things actually stand and `docs/01-plan.md` for
what comes next.

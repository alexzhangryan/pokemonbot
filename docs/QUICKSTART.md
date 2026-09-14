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
.venv/Scripts/python.exe scripts/build_dex.py gen9championsvgc2026regmc --delta

# The belief filter's set prior, distilled from the replay corpus (section 13).
# Optional: without it the agents still play, with no belief.
.venv/Scripts/python.exe scripts/build_priors.py
```

Or, with `make`: `make venv`, then `make vendor` (clones, checks out the
pinned commit, builds), then `make dex`. `pyproject.toml` wants Python 3.12 or
newer; if `python` on the box is older (one Windows box had 3.11 as the
default and 3.13 installed beside it), point the venv at the right one:
`make venv SYSTEM_PYTHON="py -3.13"`.

**Never run `python -m venv .venv` over an existing venv with a different
interpreter.** Without `--clear` it re-points `pyvenv.cfg` and leaves the old
interpreter's compiled packages in place, and every C extension then fails to
import (`No module named 'numpy._core._multiarray_umath'`, then pydantic,
orjson, torch — `make test` reports 25 collection errors). Rebuild with
`python -m venv --clear .venv` and reinstall.

Pin the checkout to the commit in `vendor/SHOWDOWN_COMMIT` before `npm install`:

```powershell
# PowerShell, from vendor/showdown
git checkout (Get-Content ../SHOWDOWN_COMMIT)
```

```bash
# Git Bash, from vendor/showdown
git checkout "$(cat ../SHOWDOWN_COMMIT)"
```

The last step writes `data/dex/gen9championsvgc2026regmc.<hash>.json` and
regenerates `docs/dex-delta.md`. The hash is content-addressed: an unchanged
vendor build always produces the same one.

## 3. Run the tests

```powershell
.venv/Scripts/python.exe -m pytest       # or: make test
```

About 520 tests in roughly two minutes. They start and stop their own Showdown
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
2. Open the teambuilder and paste in `data/teams/regmc-perish.txt` (the agent's
   default team, D75) or any of the other three.
3. Find Users → `champbot` → Challenge, in
   **[Gen 9 Champions] VGC 2026 Reg M-C**.
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

   0  battle_start      {"format_id": "gen9championsvgc2026regmc", "player_role": "p2", ...}
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

The default team everywhere is `regmc-perish` (`champions.teams.DEFAULT`, D87; `regmb-worlds` before it):
Takuma Yamazaki's 2026 World Championships winner, published stat points
included, see D75. `regmb-rain` is the Maddo's Cup #9 winner with hand-chosen
points (D73). Pass `--team` (ladder) or `--team-a`/`--team-b` (self-play) for
`regmb-alpha` or `regmb-beta`, which every measurement before 2026-09-13 was
made on.

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
`data/dex/gen9championsvgc2026regmc.*.json` and rebuild.

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
different reasons: `gen9championsvgc2026regmc` is ordinary ladder play under
hidden information, and `gen9championsvgc2026regmcbo3` forces open team sheets,
so every replay reveals both players' complete sets.

```bash
make scrape          # fetch anything new for both formats, seconds if caught up
make corpus          # report what is stored
make scrape-full     # backfill the Bo3 corpus to exhaustion; hours, resumable
```

Or the script directly, which has the knobs:

```bash
python scripts/scrape_replays.py --format gen9championsvgc2026regmcbo3 --max-replays 200
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

## 15. Fit the learned candidate prior

The candidate policy decides what the equilibrium is even allowed to consider,
and `docs/04-decision-engine.md` section 3 specifies three providers benchmarked
identically rather than one. B is the learned one: a model fit to the replay
corpus that scores each legal option.

```bash
make fit-policy    # reconstruct, fit, write the recall table (about four minutes)
make discard       # the pruning guard, now three-way (about half an hour)
```

`make fit-policy` reads the corpus and nothing else, so it needs `make scrape`
to have run and no self-play at all. It writes
`data/policy/prior.<format>.json`, which `champions.search.learned.LearnedPolicy`
loads, and `docs/policy-prior.md`, which is the measurement.

Two things in that report decide whether to believe it.

- **`uniform` is the bar.** It is a provider that scores every option the same,
  so its recall is what a budget of `k` recovers with no ordering at all. A
  recall of 0.7 at `k = 3` means nothing until you know the average slot has
  nine options and chance would have given you almost none of them.
- **Recall is not the shipping criterion.** It measures how often the provider
  would have kept what a strong human played, and the equilibrium and a strong
  human are not the same thing. `make discard` is the criterion:
  `docs/pruning-guard.md` reports what each provider throws away against the
  unpruned equilibrium, all of them measured against one solve of each position.

Without a prior on disk, `make discard` says so and measures the providers that
do not need one, rather than failing. That is the normal state of a fresh clone.

## 16. What is not built yet

M0 through M8 are done. What that leaves:

- The preview equilibrium is built and exact, and its value function is not
  wired into play: M4 could not fit one from replay outcomes, because skill
  dominates at that sample size. Self-play is the recommended source, and M6
  has now shown it works — with the caveat that two teams do not cover enough
  of the game for every feature.
- Search is one ply, on purpose. M8 built a two-ply model and a simulator-backed
  payoff and measured both against the shipping agent; neither cleared the
  gate, so no engine was built and the one-ply agent stays (section 17,
  `docs/engine-gate.md`, D71, D72).
- The policy layer ships the specified heuristic (A). M7 built and measured a
  learned prior and a language-model provider; both lost the pruning guard
  (`docs/pruning-guard.md`). The union of A and the learned prior beats A at
  higher budgets and is the one open provider question (D69).
- The belief filter is built and off by default; it was measured as neutral
  (D48) and its prior needs the corpus (sections 12 and 13).
- The frozen opponent pool has nothing between `greedy`, which the agent beats
  95% of the time, and the agent itself, so every win-rate measurement is a
  rout or a mirror (D72). More teams would fix that and two other limits.
- The coach (section 18), the review in the viewer (section 19) and the clock
  allocation (section 20) are built. What remains is measurement: the coach's
  bands are fitted from one corpus sample and its validity check is one run;
  the adaptive agent has one ladder table.

See `docs/STATUS.md` for where things actually stand and `docs/01-plan.md` for
what comes next.

## 17. Run the M8 engine gate

M8 decided whether marginal win rate comes from search depth or from payoff
fidelity, and whether a Rust engine is justified (`docs/01-plan.md`, D6). The
arms and the rule are fixed in `docs/specs/2026-09-13-engine-gate.md` and D70;
`docs/engine-gate.md` is the generated result, and the answer was neither
(D71). Rerunning it is a few hours, most of it the `twoply-oracle` arm.

```powershell
.venv/Scripts/python.exe scripts/engine_gate.py --games 20      # or: make gate GATE_GAMES=20
.venv/Scripts/python.exe scripts/engine_gate.py                 # the real thing: 200 games x 4 arms x 2 teams
.venv/Scripts/python.exe scripts/engine_gate.py --resume        # continue an interrupted run
.venv/Scripts/python.exe scripts/engine_gate.py --report-only   # rewrite the report from the JSON
.venv/Scripts/python.exe scripts/engine_gate.py --baseline greedy  # the secondary measurement (D72)
```

The script starts its own Showdown server on `--port` (8090), plays each arm
against `oneply` in a mirror match on each team, writes
`data/eval/engine-gate.<format>.json` after every matchup, and renders
`docs/engine-gate.md` with the verdict per team. Traces land in `runs/m8-gate/`.
`--baseline greedy` plays the same arms, and `oneply` itself, against
max-base-power instead, writes `docs/engine-gate-greedy.md`, and attaches no
verdict; it is the sensitivity check section 4 of the spec names.

The arms are also available to `make ladder` and `make selfplay`:

```powershell
.venv/Scripts/python.exe scripts/run_ladder.py 20 --arm-a twoply --arm-b oneply --team regmb-alpha
.venv/Scripts/python.exe scripts/run_ladder.py 20 --arm-a sim-oracle --arm-b oneply --team regmb-beta
```

`oneply-oracle`, `twoply-oracle` and `sim-oracle` are told the opponent's
registered sets (`--team-b`'s file, which in a mirror is their own). `sim-oracle`
starts one `js/sim_server.js` process per agent and scores every cell of the
matrix by stepping the real simulator from the current position
(`materialize` in `js/sim_server.js`, `champions/search/rollout.py`); expect
about half a second per decision. `twoply` and `twoply-oracle` solve a one-ply
game at every position the first ply reaches (`champions/search/twoply.py`);
about 60 ms per decision.

## 18. Review a game with the coach

M9 is the coach of `docs/06-coach-and-evaluation.md` part I, built to
`docs/specs/2026-09-13-coach.md` (D76). It takes a finished game, re-solves
every turn offline with the pruning removed, and reports two losses per turn:
ex-ante (what the decision cost against an opponent playing the equilibrium,
given what was knowable) and ex-post (what it cost against what the opponent
actually did, under full information). Each turn gets a label (best, solid,
inaccuracy, mistake, blunder) and tags (forced, read, gamble, unlucky, lucky),
the game gets a win probability curve and its critical turns, and every
number comes with a sentence.

It reads two kinds of game:

```powershell
.venv/Scripts/python.exe scripts/review.py traces                              # the newest trace under traces/
.venv/Scripts/python.exe scripts/review.py runs/m8-gate/regmb-alpha/oneply-oracle/battle-gen9championsvgc2026regmb-650.oneply0t0a0.jsonl --opponent-team data/teams/regmb-alpha.txt
.venv/Scripts/python.exe scripts/review.py gen9championsvgc2026regmc-1234567 --side p1   # fetched from the replay site
.venv/Scripts/python.exe scripts/review.py saved.log --side alice                          # a saved replay log
```

A **trace** of the agent's own game needs nothing else: the position, the
legal actions, the choice and the opponent's reply are all on the trace. The
ex-ante half runs on the agent's own information (revealed moves, pessimistic
stats); `--opponent-team` supplies the opponent's registered sets for the
ex-post half, which in a mirror match is the team file the run played.

A **replay** of anyone's game is rebuilt from the log with the M7
reconstruction (`champions/corpus/replay_state.py`, `champions/search/policy_data.py`)
and reviewed from `--side` (`p1`, `p2`, or a player name). On the Bo3 ladder
the log carries Open Team Sheets, and the coach uses the sheet for both halves
because the human saw it. The replay is saved under `traces/reviews/`.

Either way it writes `<stem>.review.jsonl`, the trace with `analysis` events
interleaved (valid by `champions/trace/validate.py`, so the viewer can open it,
though it does not render the overlay until M10), and `<stem>.review.md`, the
review as a document, which it also prints. `--llm` asks the language model of
section 15 (`ollama serve` and a pulled model) to write the critical turns'
prose from the same facts; without it, the template does. `--k` is the
opponent column budget (25). About a second per turn.

Read the header first. If the evaluation weights are not fit on this machine
(section 14) the document says so and every probability is a ranking. The
classification thresholds are hand-set (`champions/coach/classify.py`) until
they are calibrated against rating bands, which needs the corpus.

`make review GAME=<path or id> REVIEW_ARGS="--side p1"` is the same thing.

### Calibrating the bands

`docs/06` section 2 wants the label thresholds calibrated against rating
bands rather than hand-set, and section 8 wants evidence that ex-ante loss
measures skill. One script does both (D77), over the corpus of section 12:

```powershell
.venv/Scripts/python.exe scripts/calibrate_coach.py                 # or: make calibrate-coach
.venv/Scripts/python.exe scripts/calibrate_coach.py --limit 200     # more games, more minutes
.venv/Scripts/python.exe scripts/calibrate_coach.py --write-bands   # and let the coach use the fit
```

It reviews rated open-sheet Bo3 games from both sides, writes
`docs/coach-calibration.md` and `data/eval/coach-calibration.<format>.json`,
and prints the document. The bands are fitted by a rule fixed before the
numbers (among the top quartile's off-support decisions, half are
inaccuracies, thirty-five percent mistakes, the rest blunders) and applied
only with `--write-bands`, which writes `data/eval/coach-bands.<format>.json`
for `champions/coach/classify.py` to read; until then every review says its
thresholds are hand-set. The validity half is the Spearman correlation of
per-game mean loss with rating, ex-ante beside ex-post, with a verdict on
whether the two come apart.

## 19. Read a review in the viewer

The viewer of section 4 renders the coach's overlay (M10, D77). Open it as
usual and pick a `….review` entry from the trace picker; the review files the
coach writes sit beside the traces and are listed with them.

```powershell
make review GAME=runs/m8-gate/regmb-beta/sim-oracle REVIEW_ARGS="--opponent-team data/teams/regmb-beta.txt"
make viewer TRACES=runs/m8-gate
```

What changes on a reviewed trace, and only there:

- The spine carries a mark per turn (★ best, ✓ solid, ?! inaccuracy, ? mistake,
  ?? blunder) and a letter per tag; hover for the sentence.
- The eval strip draws the whole game's win-probability curve with the
  selected turn on it.
- A review block heads the decision column: both losses in total, the label
  counts, the tags, and the critical turns as buttons that jump to them.
- Each turn's panel shows the label and tags, ex-ante and ex-post loss, luck,
  the re-solved equilibrium (in place of the live agent's pending strategy
  block), the opponent's mix, the roll branches and the writeup.
- The preview pseudo-turn shows the bring, the leads, and why there is no
  verdict.

A trace the coach has not reviewed renders exactly as before.

## 20. The clock

M11 (D77). Every agent now tracks what each battle has spent and writes the
budget it was offered, the spend so far and the remaining player clock on
every `timing` event; the ladder table's clock columns read those. The
`adaptive` agent allocates: a turn gets the smaller of the 45 s limit and an
even share of the clock left after a reserve, spread over the turns the game
is expected to still run (`champions/search/clock.py`), and it spends that
share on a second ply only when the one-ply equilibrium is close, which is
`docs/04` section 7's rule.

```powershell
.venv/Scripts/python.exe scripts/run_ladder.py 50 --arm-a adaptive --arm-b oneply --team regmb-worlds
.venv/Scripts/python.exe scripts/selfplay.py 5 --agent-a adaptive --agent-b greedy
```

The pruned `candidates` event of an adaptive decision says whether it was
`decisive` (played as solved) or `escalated` (re-solved one ply deeper), and
the gap it rested on. `docs/STATUS.md` carries the first measurement.

## 21. Play the official ladder

`docs/06` section 6 asks for ladder performance on the proxy as the external
check, reported as GXE. `scripts/ladder_live.py` is the one script that
connects to play.pokemonshowdown.com; everything else about it is the local
setup: the same agents, teams, traces and the Open Team Sheets refusal.

1. Register an account for the bot on https://play.pokemonshowdown.com (the
   name has to be registered or the rating is not kept). Name it so an
   opponent can tell it is a bot, and read Showdown's rules on bots first;
   registered, transparent and one battle at a time is the shape they
   tolerate.
2. Copy `.env.example` to `.env` and fill in `PS_USERNAME` and `PS_PASSWORD`.
   `.env` is gitignored; the repository is public; never put either
   anywhere else.
3. Check the format is still on the ladder. Regulation M-B left it on
   2026-09-09 (the server answers `/search` with "not ladderable"), and the
   project moved to Reg M-C on 2026-09-14 (D80). When M-C goes the same way,
   the format id changes: move the Showdown pin (`vendor/SHOWDOWN_COMMIT`),
   `make dex` for the new id, change `FORMAT_ID` in `champions/formats.py`
   and add the new id to `LINEAGE` there so the fitted artifacts carry over.
   The list of what is ladderable is in the `|formats|` message the server
   sends on connect; the bit `0x2` is "shows in search".

Two things found on the first connection from this box (2026-09-13). The
official server's certificate chain is rejected by the trust store this
Anaconda Python uses by default ("certificate has expired", though it has
not); the script sets `SSL_CERT_FILE` to certifi's bundle before poke-env
loads and the handshake then succeeds. And poke-env cannot log a guest in on
the official server (it sends an empty token, which only the local
`--no-security` server accepts), so the account really is required; keep
the name free of spaces. The path was verified up to authentication with a
throwaway guest by hand; the registered login is the one step only a real
account exercises.

```powershell
.venv/Scripts/python.exe scripts/ladder_live.py 10                  # or: make ladder-live LIVE_GAMES=10
.venv/Scripts/python.exe scripts/ladder_live.py 30 --agent adaptive --team regmb-worlds
```

One line per game: result, opponent, their rating, our rating after. Elo
comes back in the protocol; GXE is on the ladder page the script prints at
the end. The first fifteen to twenty games are provisional, and a live game
takes five to ten minutes against a human.

**Watching it live.** In a second terminal:

```powershell
make viewer-live        # the viewer on runs/live/, no local simulator
```

It is the same viewer as `make viewer`, pointed at the live directory. The
side pane lists every game — the opponent and the result, colour coded,
newest first — and a game appears there as it starts, with the decision
points streaming in as the bot makes them. A finished game opens as the
coach's review of it once the review is written, which is before the next
game starts (D84); there is one entry per game. Start it before
or during a run, either works. The pill in the top bar says what the bot is
doing — searching for a game, thinking on turn N (with the seconds so far),
waiting for the opponent, coach reviewing the last game — and the viewer
moves to the next game on its own when one starts, unless you picked a
trace from the list yourself (D81). While a run is on, the control bar
shows a **play ladder** button when nothing is running (D87): a number of
games, or blank to play until you press stop, started as the same script
`make ladder-live` runs, detached so closing the page forfeits nothing, with
the default agent (`adaptive-belief`) and team. While a run is on it
shows a **stop after this game** button: the run finishes the game on the
board, reviews it, and stops (D82); **cancel stop** takes it back. Each
candidate row shows the bot's score for it — its expected win probability
against the opponent's equilibrium mix — beside its worst case, its weight
in the mix and the policy prior that ranked it; the strategy block shows
the game value and the opponent's expected replies. The page is laid out
for half a screen beside an editor. The **current Elo** block in the
middle of the top bar is the bot's standing on the official ladder — Elo
large, then rank, GXE and record — from the site's public JSON, refreshed
when a rated game ends and otherwise on the minute; rank is a number only
when the bot is in the published top 500, since the site publishes no rank
beyond it, and the Glicko estimate is in the block's tooltip (D83).

**What is kept, per game (D78).** Three files' worth, all under `runs/live/`:

- the trace, `<battle_tag>.<username>.jsonl`, the same decision trace as any
  other run;
- the replay, on the server: the bot asks the room to save it as the battle
  starts, so `https://replay.pokemonshowdown.com/<id>` holds the neutral
  record of the same game once it ends (`--no-save-replays` to opt out);
- a row in `ledger.ndjson`: opponent, both ratings, result, turns, the trace
  path and the replay URL. It accumulates across runs.

```powershell
make ladder-summary                       # the record so far, from the ledger
make review GAME=runs/live                # the coach on every live trace
make review GAME=<replay URL> REVIEW_ARGS="--side <bot name>"   # the same game, from the replay
```

**Game, coach, game, coach (D79).** By default the run alternates: a game
ends, the coach reviews its trace (about a second a turn, in its own
process), prints the summary and the critical turns, writes the
`.review.jsonl` and `.review.md` beside the trace, and only then does the bot
search for the next game. The review never overlaps a live search, and the
viewer shows the finished game with its overlay as soon as the review lands.
`LIVE_ARGS="--no-review"` plays back to back instead, and `make review
GAME=runs/live` afterwards is the same result. The review of a live trace
uses revealed moves as the information state, since nobody shows the bot a
sheet.

This is the first time the agent faces people. Every number before it was a
mirror or a scripted opponent, so whatever it scores is a finding.

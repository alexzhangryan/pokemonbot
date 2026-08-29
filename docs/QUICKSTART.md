# Quickstart

How to set the project up and manually exercise everything M0 built.

Everything below is run from the repository root, `C:\dev\pokemonbot`, in
PowerShell. Commands use `.venv\Scripts\python.exe` explicitly so they work
whether or not the virtualenv is activated.

## 1. Prerequisites

| Tool | Version used | Check |
| --- | --- | --- |
| Python | 3.12.10 | `python --version` |
| Node.js | 24.19.0 (LTS) | `node --version` |
| Git | 2.55 | `git --version` |

Both Python and Node were installed with `winget` (`Python.Python.3.12`,
`OpenJS.NodeJS.LTS`). If a command is "not recognized", open a new terminal:
`winget` updates `PATH` only for new shells.

## 2. One-time setup

```powershell
# Python environment
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"

# Showdown simulator, pinned to the commit in vendor/SHOWDOWN_COMMIT
git clone https://github.com/smogon/pokemon-showdown.git vendor/showdown
cd vendor/showdown
git checkout (Get-Content ..\SHOWDOWN_COMMIT)
npm install
node build
cd ..\..

# The resolved Champions dex (gitignored, regenerated locally)
.venv\Scripts\python.exe scripts/build_dex.py gen9championsvgc2026regmb --delta
```

The last step writes `data/dex/gen9championsvgc2026regmb.<hash>.json` and
regenerates `docs/dex-delta.md`. The hash is content-addressed: an unchanged
vendor build always produces the same one.

## 3. Run the tests

```powershell
.venv\Scripts\python.exe -m pytest
```

56 tests, about 30 seconds. They start and stop their own Showdown server, so
nothing needs to be running first. Also available:

```powershell
.venv\Scripts\python.exe -m ruff check .      # lint
.venv\Scripts\python.exe -m ruff format .     # format
.venv\Scripts\python.exe -m mypy .            # types
```

## 4. Play against the bot yourself

The most direct way to see it work. In one terminal:

```powershell
.venv\Scripts\python.exe scripts/run_local_server.py 8090
```

In a second terminal:

```powershell
.venv\Scripts\python.exe scripts/play_human.py --agent greedy
```

Then in a browser:

1. Open <http://localhost:8090>. It redirects to
   `https://localhost--8090.psim.us` — the official Showdown client UI, loaded
   from Smogon but connected to *your* local server. This needs internet for the
   client assets; the battles themselves are entirely local.
2. Choose any username (the local server runs with `--no-security`, so no
   password is needed).
3. Open the teambuilder, create a team for
   **[Gen 9 Champions] VGC 2026 Reg M-B**, and paste in the contents of
   `data/teams/regmb-alpha.txt`.
4. Find Users → `champbot` → Challenge, pick the Reg M-B format, and send it.

Use `--agent random` for the weaker opponent. `--games 5` to keep it alive for
several matches. If prompted about Open Team Sheets, decline — the bot always
declines, by design, because Champions has no such mechanism.

## 5. Watch two bots play each other

```powershell
.venv\Scripts\python.exe scripts/selfplay.py 50
```

```
finished: 50/50 battles
  champ-a: 14 wins
  champ-b: 36 wins
protocol failures (invalid choice / timeout): 0
traces: 100 written, 0 invalid
```

Two traces per battle, one per agent's own view, because each agent only ever
sees its own side. To watch a game as it happens, run a single game and open the
browser client first — though at roughly 10 games/second you will want
`selfplay.py 1` and quick fingers.

## 6. Read a decision trace

Traces are append-only JSONL, one file per agent-view of a battle:

```powershell
.venv\Scripts\python.exe scripts/show_trace.py            # most recent under traces/
.venv\Scripts\python.exe scripts/show_trace.py runs/human # or a directory
.venv\Scripts\python.exe scripts/show_trace.py path\to\one.jsonl --full
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
- `turn_start` / `equilibrium` — the state, and the action chosen out of
  `n_legal_joint_actions` (about 98 in a typical mid-game doubles turn).
- `timing` — per-decision latency plus `watchdog_fired` and `exceeded_45s`.

## 7. Evaluate agents against each other

```powershell
.venv\Scripts\python.exe scripts/run_ladder.py 50
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

## 8. Benchmark the simulator

```powershell
.venv\Scripts\python.exe scripts/bench.py
```

Writes `docs/benchmarks.md` and compares local throughput against the reference
figures. On this machine a clone plus a step costs about 2.1 ms, against 4.7 ms
for the reference container.

## 9. Check simulator determinism

```powershell
.venv\Scripts\python.exe scripts/differential.py 1000
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

## 10. Troubleshooting

**`node` or `python` not recognized.** Open a new terminal.

**`DexNotBuiltError`.** Run the `scripts/build_dex.py` line from step 2.

**"Multiple dex dumps ... ambiguous which build is current".** You rebuilt
Showdown at a different commit. Delete the stale
`data/dex/gen9championsvgc2026regmb.*.json` and rebuild.

**Port 8090 already in use.** A server is still running. Find and stop it:

```powershell
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -like '*pokemon-showdown*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

**`ConnectionResetError` / `no close frame received` at the end of a run.**
Shutdown ordering noise when the server goes away while sockets are still open.
Harmless, and the scripts disconnect cleanly to avoid it.

**Tests are slow the first time.** The session-scoped fixture builds and starts
a Showdown server once, which takes a few seconds.

## 11. What is not built yet

M0 is foundation only. Nothing here plays well:

- No damage calculator. `MaxBasePowerAgent` is greedy on *base power*, which is
  not damage — no types, stats, items, or spread reduction. That is M1.
- No opponent modelling, no search, no equilibrium solve. The `equilibrium`
  trace event exists and currently records a uniform or argmax policy.
- No coach, no live view, no game review client.

See `docs/09-m0-tasks.md` for what M0 covered and `docs/01-plan.md` for what
comes next.

# Status

Mutable. Current state only. History belongs in `DECISIONS.md`.

Whoever finishes a work session updates this file before stopping. Whoever starts one reads it first.

Last updated: 2026-08-29, by Claude Code.

## Current milestone

M0 complete: T0.1 through T0.10 all done, every acceptance criterion met. See `docs/09-m0-tasks.md`.

Since then the observability surface was pulled forward out of M10 (D13) and
verified in use, **M1 is done** (the Champions stat and damage layer, both
halves validated against the simulator rather than against the transcription),
**M2 is done** (the one ply agent prunes, estimates payoffs, solves the matrix
game by LP and samples the equilibrium; it beats max-base-power 82.0% [69.2%,
90.2%] over 50 games on a shared team and 56.0% [42.3%, 68.8%] on a different
shared team, and the gap between those two numbers is the most useful thing M2
measured), **M3 is done** (the shared protocol parser, the replay scraper and
the SQLite corpus, with the Bo3 backfill still running), and **M4 is done**: the
bring-4 and lead predictors and the exact preview equilibrium.

M4's headline is a negative result that is worth more than the code. Leads
predict well and transfer to unseen players; the bring-4 does not predict at all
from species, and the preview value model is worse than a coin flip out of
sample. The control says why -- skill dominates these ladder outcomes -- and the
same boundary showed up in M2 from the other direction. Details below.

M5, the belief filter, is next.

M0's definition of done, checked: `pytest` green (56 tests, ~31s), a 50 game self-play run completes and produces valid traces, and the local benchmark numbers are recorded in `docs/benchmarks.md`.

New to this project: read `docs/QUICKSTART.md`. It covers setup and how to manually exercise everything, including playing against the bot yourself in a browser.

## Done

- Design complete. Ten documents in `docs/`, plus the M0 task list.
- Champions mechanics verified against the Showdown source, not against articles. Stat formula, status rebalances, Mega Evolution, disabled Terastallization, PP cap.
- Engine performance benchmarked in a cloud container: 303 turns per second, 4.7 ms per clone plus step. Needs reproducing on the development machine at T0.9.
- Action space measured on a live request: about 156 joint actions per side with the Mega flag available.
- Repository scaffolded with README, `pyproject.toml`, `.gitignore`, `CLAUDE.md`, and `docs/`.
- T0.1: git initialized, package skeleton created under `champions/`, `scripts/`, `js/`, `tests/` per the implementation blueprint. Venv with dev extras installed. `ruff check .` and `pytest` (one smoke test) both pass.
- T0.2: `smogon/pokemon-showdown` vendored at commit `bb179fbf8449e3c31632bd56f671ffb4404fa6e7` (recorded in `vendor/SHOWDOWN_COMMIT`), built. Confirmed both `champions` and `championsregma` mods and both Reg M-B format IDs exist in this checkout.
- T0.3: `js/dump_dex.js` (format dump + a `--mod` raw-comparison mode) and `scripts/build_dex.py` (hash + write `data/dex/`, `--delta` for the mainline diff) written and working. For `gen9championsvgc2026regmb`: 355 legal species, 148 non-nonstandard items — matches `docs/02-mechanics-deltas.md`'s figures (its "347" was an approximation; 148 items and 75 legal Mega Stones match exactly). `docs/dex-delta.md` committed: 303 modified moves, 256 modified items, 8 modified abilities vs mainline gen9, 0 added/removed in any category. The ability diff independently confirms the six newly legalized abilities and the Healer/Unseen Fist text changes already documented by hand. Both the dex dump hash and the delta doc reproduce byte-for-byte on a second run against the same vendor build.
- T0.4: `champions/trace/schema.py` (`TraceEvent` envelope: schema_version, battle_id, seq, t, type, payload; `type` is a plain str and the envelope allows extra fields, so unrecognized event types/fields from another agent version don't crash a reader) and `champions/trace/writer.py` (`Trace.emit()` is synchronous and only enqueues; a background asyncio task drains to `<battle_id>.jsonl`). `tests/test_trace.py`: synthetic battle round-trips, unknown fields/types tolerated, `emit()` measured at well under 1 ms/call (mean over 1000 calls).
- T0.5: `scripts/run_local_server.py` starts the vendored build with `--no-security` (bare `/trn USERNAME` login, no signed assertion needed) and blocks until ready via a bounded reader-thread/queue wait. `tests/conftest.py` wraps it in a session-scoped fixture; `tests/support_showdown.py` holds a hand-built, format-validated team (the format rejects challenges with no team set — this was the non-obvious part). `tests/test_local_server.py` confirms a websocket connection and a challenge in `gen9championsvgc2026regmb` are both accepted. Along the way, found and fixed a real bug: `docs/dex-delta.md` was written without `encoding="utf-8"`, so Windows silently mojibake'd non-ASCII move text — caught by `ruff format --check`, not by any test.

- T0.6: `champions/agents/baseline.py` — `TracingPlayer` wraps poke-env's `Player` to emit the trace and route every decision through the watchdog; `RandomAgent` is uniform over legal joint actions. Open Team Sheets is declined explicitly and the constructor refuses `accept_open_team_sheet=True` outright (D2). Two fixed legal teams in `data/teams/`, validated against the simulator's own `validate-team`. `champions/trace/validate.py` makes "produced a valid trace" a checked claim. Acceptance: 50/50 games complete, 100 agent-view traces all valid, 0 protocol failures. Two bugs fixed getting there: both self-play agents wrote to one file (they share a battle_tag) so `Trace` now takes a `name` override; and `close_traces()` awaited poke-env `POKE_LOOP` tasks from the caller's loop, which raises, so it now bridges.
- T0.7: `champions/search/watchdog.py` — `decide_with_deadline()` returns the best action proposed so far when the deadline expires, with a fallback so it never returns without a legal action. Emits `watchdog_fired`, `finished`, `exceeded_45s`. Documented caveat: cancellation is cooperative, so search code must await periodically to stay interruptible.
- T0.8: `champions/harness/{elo,ladder}.py` — Wilson intervals (chosen over the normal approximation, which leaves [0,1] at the extremes), and one table carrying win rate, CI, and the three clock metrics. `champions/dex/loader.py` reads the T0.3 dump, because poke-env ships mainline Gen 9 data and 303 moves differ. `MaxBasePowerAgent` is named for what it does: greedy on base power, not a damage maximizer, since the damage layer is M1. Acceptance table at 50 games, seed 1: random 20.0% [11.2%, 33.0%], max-base-power 80.0% [67.0%, 88.8%], 0.0% of turns over 45s, clock ok for both.
- T0.9: `js/sim_server.js` (JSON-RPC over stdio: create, step, serialize, deserialize, clone, request, randomChoice, destroy) and `champions/search/oracle.py`. `scripts/bench.py` writes `docs/benchmarks.md`. Local vs the cloud reference: full battles 27.2/s vs 26.8, turns 569.5/s vs 303, serializations 1,826/s vs 2,386, deserializations 1,308/s vs 1,026. Clone plus step is 2.13 ms locally against roughly 4.7 ms reference, so the per-turn simulator budget is larger here than the figure the M8 gate was sketched against.
- T0.10: `champions/harness/differential.py` — `Position` as a replayable recipe (not a serialized state, so a second engine need not understand Showdown's serialization), the `Engine` protocol, `compare_engines`, `check_determinism`. Positions are generated with Showdown's own `RandomPlayerAI`, so choice legality is the simulator's definition rather than a reimplementation. Acceptance: 1000 positions, 1000 distinct, 0 divergences, reproduced across runs.
- `docs/QUICKSTART.md`, plus `scripts/play_human.py` (bot waits for challenges so you can play it in a browser) and `scripts/show_trace.py` (readable trace dump).

## Done since M0

- Observability, built ahead of schedule (D13, D14, D15). `docs/07-observability.md`
  put both clients at M10; with no consumer nothing forced the emission to be
  complete, and `turn_start` was carrying four species names and no state.

  - `champions/protocol/state.py` — `snapshot()`, the full observable state as
    plain JSON. Our side exact (HP, stats, item, ability, PP), the opponent's
    only as revealed, unknowns explicitly null. Move numbers come from the
    Champions dex, and each move records which source it came from, so mainline
    poke-env values leaking in is a test failure rather than a silent wrong game.
  - `champions/protocol/actions.py` — `describe()`, a `BattleOrder` as structure
    rather than as a `/choose` string.
  - `champions/agents/baseline.py` — the decision pipeline moved into
    `TracingPlayer.choose_move`; subclasses now supply only `_search` and a
    strategy name, so a new agent gets the whole observability surface without
    opting in. `turn_start` now carries the state snapshot and the protocol log,
    `candidates` is emitted (per-slot and joint, with `annotations_pending`), and
    `equilibrium` carries the described action and its own `pending` list.
  - `champions/viewer/` — FastAPI server plus a no-build-step single page.
    Tails the trace directory, so live and replay are one code path and the
    viewer cannot influence play. `scripts/viewer.py` opens it as a standalone
    app window; `make viewer`.
  - The battle stage (D16). Opponent above, us below, field conditions between,
    back sprites for our own side, and the chosen action marked on the board:
    which slot is acting, which slots it is aimed at, and the friendly-fire case
    where a move points at our own partner. Rendered from the trace because
    Smogon's client refuses to be framed — its source carries an explicit
    `self === top` frame-bust, so an embed is not available at any price short
    of vendoring the client repo. A **Showdown ↗** button opens the real client
    on the current battle in its own positioned window instead.
  - The viewer as the session's control surface (D17, D18). It starts the
    Showdown simulator on launch, and a **Control** panel runs self-play
    (games, seed, agent per side) and puts a bot up to be challenged, handing
    back the steps and an **Open Showdown** link. `champions/viewer/control.py`
    is the supervisor: runs are subprocesses so a crashing agent cannot take the
    window down, an already-listening port is adopted rather than duplicated and
    never stopped by us, and one run at a time is enforced with a 409.

    This exists because a live battle did not show up in the viewer. The cause
    was that `play_human.py` defaults to `runs/human` while `make viewer`
    watches `traces/`, and nothing could keep those consistent. Now the
    supervisor owns the trace directory and passes it to every run it starts.

    A second, separate cause of the same symptom, also fixed: auto-follow keyed
    on the most recently written *file*, and a battle writes two (one per
    agent-view, D10) milliseconds apart, so it alternated between them and
    rebuilt the websocket about once a second. It now keys on `battle_id`.

    `scripts/viewer.py` is the only command a session needs. The individual
    scripts and make targets still work and are what the tests use.
  - The battle animation (D19). `champions/viewer/static/battle.html` loads
    Showdown's own renderer and replays the protocol log from the trace,
    embedded between the opponent panel and ours and seeking to whatever turn
    the spine has selected. D16 had concluded this was impossible; it was right
    about the client, which frame-busts, and wrong about the renderer, which
    Smogon publishes for embedding. Needs internet (CDN); the frame says so if
    it cannot load. The renderer is mainline Showdown's, so Champions-only
    formes can resolve to sprites that do not exist upstream — Mega Greninja
    404s — which degrades to a missing image and nothing more.
    Follow-up fixes after using it (D21-D24): the scene now `play()`s
    with animation while following a live battle and only seeks instantly when
    scrubbing — previously every update took `seekTurn`'s fast-forward path, so
    turns advanced but no move ever animated, and the only visible changes were
    the forced-switch decision points that happen to trigger a full redraw. It
    is drawn from the traced agent's viewpoint via `setViewpoint(player_role)`,
    rather than the renderer's p1-near default, which had been showing half of
    all battles from the opponent's chair. And there is a speed control on the
    scene (Showdown's own presets plus "skip animations"), remembered locally.

    Animation then still failed against a human opponent (D23): it was keyed on
    the live badge, whose window was 25s, and a person thinking longer than that
    flipped every update back to an instant seek. It now keys on the protocol
    log actually growing while the reader is following the front of the trace,
    which is what "something happened" really means; the badge window went to
    90s as well. The browser test uses 30s turns specifically to cross the old
    threshold.

    The control drawer became one 40px bar (D24), the four-step challenge card
    became a single line, and the agent list is served from the registry rather
    than hardcoded in the page.
  - A **Live battle** button appears when something is being written to that is
    not what is on screen (D20). Pinning to a chosen trace is correct; being
    silent about a live battle elsewhere is what made it look broken.
  - `tests/test_viewer.py` (14), `tests/test_observability.py` (13) and
    `tests/test_control.py` (10). The last includes `node --check` on the client
    script and on battle.html's inline one: a syntax error there is a blank page
    with one console message, which is cheap to catch and expensive to find. It
    caught two during this work.
  - Browser QA with Playwright against a real Chromium: zero console errors,
    zero failed requests, no horizontal scroll at 1500 / 1100 / 760 / 375px,
    scrubbing and trace switching verified, and a live run verified end to end
    (viewer opened on an empty directory, attached to the battle as it appeared,
    badge went live, turns accumulated with no reload).

  Three real defects fell out of having a consumer, all now covered by tests:
  poke-env enums were serialising as `"FLYING (pokemon type) object"` and
  `"Status.PAR"`; an unrevealed opponent item was serialising as the literal
  string `"unknown_item"`; and nothing anywhere recorded what actually happened
  in a turn.

### M1: the stat and damage layer

Both halves are validated cell by cell against the vendored simulator. The
simulator is the authority throughout; these modules are the things under test.

- `champions/dex/stats.py` -- the linear Champions stat formula, transcribed
  from `data/mods/champions/scripts.ts` `statModify` including the 16 bit
  truncated nature step, which is `trunc(trunc(stat * 110, 16) / 100)` and not
  `int(stat * 1.1)`. The two agree across Reg M-B's range; writing the second
  would have made that agreement an assumption instead of a checked fact.
  `max_pp` and `export_points` live here too.
- `champions/dex/damage.py` -- base damage, spread modifier, weather hook, crit,
  the sixteen rolls, STAB, type effectiveness, burn, and the final 16 bit
  truncation, plus `modify` (the 4096ths fixed point multiply), the boost table,
  `TypeChart`, `damage_roll_distribution` and `ko_probability`. Item and ability
  multipliers enter through `DamageContext.final_modifiers` rather than being
  enumerated: there are roughly 250 of them and hand transcription is the error
  this project keeps avoiding (D26).
- Natures and the type chart are now dumped from the simulator alongside
  species, moves, items and abilities, and read through `Dex` (D25). Neither is
  overridden by the mod today, which is a fact about this build and not a
  guarantee.

The load-bearing finding, established by diffing rather than assuming:
**Champions does not change the shape of the damage calculation.** Its
`modifyDamage` override is numerically identical to `sim/battle-actions.ts` --
the only difference in the whole function is a `Math.min(typeMod, 2)` argument
on two protocol messages -- and `getDamage` is not overridden at all. What
Champions changes is every *input*: stats, roughly 300 base powers, roughly 250
items, 8 abilities, and Tera being off. `CLAUDE.md`'s rule against
`@smogon/calc` therefore stands, and for a sharper reason than the one written
there: the shared skeleton is exactly what would make a mainline calculator
wrong quietly (D26).

Validation:

- `tests/test_stats.py` (44). A probe team of six sets with deliberately chosen
  point allocations and natures, run through the simulator, every one of the six
  stats compared independently against the formula. Plus linearity, the mainline
  endpoint correspondence, HP never taking a nature, and effective PP.
- `tests/test_damage.py` (10). A probe battle held still -- clean abilities on
  all four Pokemon, no items, no weather, no boosts, turn one only, fresh battle
  per seed -- across 40 seeds and 5 choice pairs. 480 damage events over 12
  distinct cells, every one of them a member of the sixteen predicted rolls
  after clamping at the target's remaining HP (D27). Coverage: physical and
  special, STAB and not, spread and single target, friendly fire, a double
  immunity, and effectiveness at 0.5x, 1x, 2x and 4x. 20 crits occurred and were
  checked. The widest cell showed 15 of its 16 distinct roll values, and a
  separate test fails if that sweep ever degenerates.

Two defects found and fixed in the probes themselves, both worth knowing:

- Showdown's export parser maps the string `"Spd"` to **Speed**, a case
  sensitive legacy alias; Special Defense is `"SpD"` (`sim/dex-data.ts:419`). So
  `"spd".capitalize()` silently writes points onto the wrong stat and produces a
  team that is still legal, so nothing complains. This first showed up as two
  Aegislash cells disagreeing with the simulator and looked like a stat bug.
  `champions.dex.stats.SHOWDOWN_STAT_LABELS` and `export_points` now exist so
  nothing writes that string by hand again.
- The simulator reports HP actually lost, so a roll that would overkill is
  reported as the target's remaining HP. Comparing raw predictions against that
  looks like the damage layer over-predicting on exactly the cells that matter
  most (D27).

### M2: the one ply agent

`champions/agents/oneply.py`, plugged into `TracingPlayer._search`, so it
inherits the whole observability surface. Four pieces, each testable alone:

- `champions/search/matrix.py` -- the zero sum matrix game by LP (SciPy HiGHS),
  returning both players' strategies and the value. Solved twice, once per
  player, because at a hundred cells the second solve is free and obviously
  correct beats reading the dual; the two must agree on the value, which is the
  minimax theorem and is raised rather than averaged over if it fails.
- `champions/search/evaluate.py` -- the bootstrap evaluation, a logistic over
  side-difference features read off the trace snapshot. Antisymmetric between
  equally revealed sides, which is what lets the matrix game treat the payoff as
  zero sum. It declares `IS_CALIBRATED = False`, because the coach's ex-ante
  loss reads this as a probability and hand-chosen weights are not one.
- `champions/search/payoff.py` -- the analytic one turn model and the payoff
  matrix (D28). Damage, speed order, priority, Trick Room, Protect, switches,
  spread damage, friendly fire, faints, and the sixteen rolls bucketed on the
  knockout threshold. Nothing samples, so the common random numbers requirement
  is discharged by construction rather than by seeding.
- `champions/search/policy.py` -- `PolicyProvider` and implementation A, the
  heuristic. `discard_rate` implements the pruning guard section 3 requires, for
  the M7 benchmark. The interface takes the enumerated legal set rather than
  deriving it (D29).

Measured, all with both arms on the same team (D30), 50 games, seed 1:

| matchup | win rate | 95% CI | p50 | p95 | max | >45s |
| --- | --- | --- | --- | --- | --- | --- |
| one-ply vs random, ALPHA | 98.0% | [89.5%, 99.6%] | 12.2 ms | 44.9 ms | 84 ms | 0.0% |
| one-ply vs max-base-power, ALPHA | 82.0% | [69.2%, 90.2%] | 10.9 ms | 17.7 ms | 43 ms | 0.0% |
| one-ply vs max-base-power, BETA | 56.0% | [42.3%, 68.8%] | 11.6 ms | 20.5 ms | 46 ms | 0.0% |

Clock compliance is not close to binding: the whole decision costs about 11 ms
at the median against a 45 second budget, so there are roughly three orders of
magnitude of headroom before M11 has anything to optimize.

**The 82% / 56% gap is the finding.** ALPHA carries no items, no stat points and
inert abilities; BETA is a real competitive team built on Intimidate, Protean,
Focus Sash, Sitrus Berry, Leftovers, Rough Skin, Competitive and a Mega. The
payoff model represents none of those. So the agent's advantage is close to
proportional to how much of the game its model actually contains, and the
cheapest available reading is that items and abilities are worth more than
search depth. That is a concrete prior for the M8 engine gate, which was framed
as depth against evaluation quality and now has a third candidate ahead of both.

Three defects found by running it, each now covered by a test:

- The evaluation counts HP and survivors across active *and* bench, and the turn
  model's switch handler emptied the active slot without benching its occupant.
  A double switch scored 0.06 against a 0.82 baseline: the agent read switching
  as losing the Pokemon outright.
- The heuristic scored a damaging move aimed at our own partner identically to
  the same move aimed at a foe. Every move appears once per legal target, so
  nine of the ten surviving candidates were friendly fire and the equilibrium
  was solving a matrix made almost entirely of actions no one would play.
  `MaxBasePowerAgent` had already learned this and the lesson was not carried
  over; it costs far more under pruning than it did under an argmax.
- The equilibrium draw was seeded with the builtin `hash()`, which Python
  randomizes per process. The agent would have made different choices on every
  rerun of the same seeded battle (D31).

And one that was not a defect in the agent at all: the first measurement had the
one ply agent losing 1-9 to max-base-power, which read as the agent being
broken. The two arms were playing different teams. Max-base-power against itself
across the same pairing is 0-10 (D30).

### M3: the replay scraper and corpus

One parser feeds two consumers, which is the decision the milestone turns on
(D32). `champions/protocol/parser.py` was an empty stub the blueprint had
already specified; the corpus is a thin driver over it, and so is the live
agent.

- `champions/protocol/parser.py` -- `apply(state, line) -> list[Observation]`,
  to the signature `docs/08` section 3 froze. `ParserState` carries what a line
  cannot supply on its own: the turn, the nickname-to-species table, the active
  slots, and whether we are in the choice phase or the residual phase. Three
  properties are contractual rather than incidental. Observations carry a
  monotonic `seq`, because the order moves resolve in is the only Speed evidence
  the protocol gives and `docs/03` propagates stat intervals from it.
  Attribution is generic, so `[from] ability: Drizzle` and `[from] item: Life
  Orb` reveal an ability or an item on whatever message carries the tag -- one
  rule that catches Protean, Levitate, Life Orb recoil and Leftovers without
  enumerating any of them. And unrecognised message types are counted in
  `unhandled` rather than dropped, which makes coverage a number a test asserts
  on.
- `champions/corpus/replay.py` -- a log to a `ReplayRecord`, pure and with no
  network in it. Header, both team previews, the open-sheet reveal, who was
  brought and who led, every action in order, and every observation. The packed
  `|showteam|` format is parsed positionally, including the empty-species field
  that means the nickname is the species.
- `champions/corpus/store.py` -- SQLite, the five tables `docs/05` section 6
  specifies. `reveals` holds the complete observation stream and `actions` holds
  the subset that were choices; they overlap by design and are both keyed on
  `(replay_id, seq)`. Raw logs live on disk and are never re-fetched, derived
  rows are deleted before rewrite so parsing is idempotent, and
  `replays.parser_version` records which parser produced them (D35).
- `champions/corpus/scrape.py` -- the polite scraper. One request per second by
  default, exponential backoff with `Retry-After` honoured, a `User-Agent` that
  says who this is, 404 treated as gone rather than as broken. Pagination
  terminates on the API's own signal, a page shorter than 51. Incremental by
  default: newest first, so a page with nothing new on it means everything older
  is stored, and the walk stops. State lives in the store, so a killed run
  loses at most one replay.
- `scripts/scrape_replays.py`, plus `make scrape`, `make scrape-full` and
  `make corpus`. `--reparse` rebuilds every derived table from stored logs with
  no network access at all.
- `turn_result` is now emitted, which closes the open question `docs/07` section
  2 left for M3. It is a parsed digest, produced by the same parser, so the live
  agent and the offline corpus cannot drift apart. `battle_end` carries the
  final observations and the unhandled-message count alongside.

Measured, on 408 replays across both formats:

| | |
| --- | --- |
| unhandled protocol message types | 0 |
| open-sheet replays | 359 |
| complete sets extracted | 4,392 |
| natures present | 4,392 of 4,392 |
| stat point spreads present | 0 |
| bring-4 fully observed | 73% of games |

Three findings, each of which changes something downstream:

- **Open team sheets reveal natures** (D33). `docs/05` section 5 says stat
  points *and* natures appear in no public dataset, and `docs/03`'s two
  structurally different halves are justified by that split. The first half is
  right and the second is not. Nature is a free label at scale, from the only
  source that has it, and it is half of what a spread is -- so it moves into the
  learnable half, and the remaining inference problem is stat points alone,
  which is strictly smaller and better conditioned than the design assumed.
- **A bring-4 is only a label when all four appeared** (D34). The log's only
  witness to a bring is a Pokemon taking the field, so a game won before the
  fourth switched in yields three, and three is not a truncated four. 73% of
  games are fully observed; M4 trains on those and the flag is a reported
  number rather than a silent filter.
- **The unhandled counter earns its keep immediately.** Zero types across the
  first 80 replays, then `-clearallboost` at 358 -- a field-wide message with no
  ident at all, which the per-Pokemon path would have dropped silently. Fixed
  and re-parsed from disk with no network, which is what D35 exists for. The
  live path found two more the replay API strips, `init` and `title`, and the
  test that found them checks the parser against a second, independent log
  source for exactly that reason.

Two caveats worth carrying forward. Two Bo3 replays in 361 have no `|showteam|`;
both are unrated, so open sheets are probably not forced outside rated play, and
`sheets_revealed` is per replay rather than assumed per format. And the actions
table records outcomes, not submitted choices: a move chosen and then prevented
appears as the prevention. That is a ceiling on the table, not a bug in it.

### M4: bring-4, leads, and the preview equilibrium

`docs/04-decision-engine.md` section 6 calls this likely the highest ratio of win
rate to engineering effort in the project, on the grounds that the preview game
is small enough to solve exactly. The machinery is built and exact. What M4
found is that exactness is not the binding constraint -- the value of a cell is.

- `champions/preview/dataset.py` -- preview examples from the corpus, both
  sides, with a split grouped by series. A best-of-three is two or three games
  played by the same two teams, so splitting by replay would put game 1 in
  training and game 3 in test and call memorisation generalisation. Player-level
  leakage survives that, so the split also marks `unseen_players`, the subset
  where neither player appeared in training at all, and every headline number is
  reported on both.
- `champions/preview/features.py` -- species one-hot over a vocabulary built
  from training data only, plus six dense matchup features from the dex. Nothing
  reads an item, ability, move or nature (D36).
- `champions/preview/model.py` -- a conditional logit over subsets, fitted by
  L-BFGS with an analytic gradient. Bring-4 is one choice of exactly four from
  six, not six coin flips, so the constraint lives in the likelihood rather than
  being restored by renormalising afterwards. Its output is already the
  distribution over the fifteen that the equilibrium consumes, and its per
  Pokemon marginals are derived from that rather than fitted separately.
- `champions/preview/value.py` -- `P(win | our four, their four)`, antisymmetric
  by construction because the features are `g(a) - g(b)` with no intercept
  (D37).
- `champions/preview/equilibrium.py` -- the exact 15 x 15 solve and the nested
  6 x 6 lead subgame, reusing `search/matrix.solve_both`. The value function is
  an argument, not an import. `best_response` is offered alongside for use with
  the bring predictor, since a best response and an equilibrium answer different
  questions and the trade between them is the caller's.
- `scripts/train_bring4.py` and `tests/test_preview.py` (22).

Measured on held-out series, about 2,300 replays:

| model | top-1 | uniform | log loss | uniform | unseen players |
| --- | --- | --- | --- | --- | --- |
| leads, 2 of 4 | 38.5% [35.1%, 41.9%] | 16.7% | 1.616 | 1.792 | 33.1% |
| bring-4, 4 of 6 | 9.4% [7.5%, 11.6%] | 6.7% | 2.660 | 2.708 | 6.1% |

The preview value model reaches 61.0% accuracy in training and 46.2% on held-out
games, with a log loss of 0.727 against a coin flip's 0.693. It is not wired
into anything.

**Leads are predictable and the bring-4 is not.** That asymmetry is the finding,
and the control explains it: over 1,808 rated games the higher-rated player won
57.4%, so these outcomes are predictable, by skill, which preview features do
not observe. Leads are species-intrinsic -- a Fake Out user, a weather setter
and a Trick Room setter each have a role that barely depends on the opponent --
so a species main effect captures them, and it transfers to players never seen
in training. The bring-4 is a matchup decision that turns on what those four
will actually do, which means items and abilities, which preview never reveals.
That is the same boundary M2 hit from the other side when the agent's advantage
fell from 82% to 56% on a team whose items and abilities its model did not
represent. Two milestones, two methods, one conclusion (D39).

**A separable value function makes the preview an argmax, not a game** (D38).
The first value model was `sigmoid(g(ours) - g(theirs))`, which is a perfectly
good antisymmetric payoff and has no game in it whatever: the same bring-4 is
best against all fifteen of the opponent's options, so the equilibrium is pure
and the exact solve buys nothing over sorting. Measured, then fixed by adding
interaction features that enter as `h(a, b) - h(b, a)` so antisymmetry survives.
The test suite now requires a separable value to yield one best response and an
interacting one to yield several, in both directions, because this is the kind
of premise failure that is silent rather than loud.

**`search/evaluate.py` cannot supply preview cells** (D37), which `docs/04`
section 6 assumes it can. It reads a state snapshot and at preview there is no
state, so every feature is identical for both sides and it returns 0.5 for all
225 cells. A constant matrix has every strategy as an equilibrium.

What M4 leaves for M6: the value function is the whole problem, and the corpus
cannot answer it at this scale because skill dominates. Self-play can, and the
arithmetic is favourable -- the local simulator runs 27 battles a second, so all
225 cells at 20 games each is roughly 4,500 battles, single-digit minutes, with
no skill confound at all because both sides are the same agent. That is the
recommended source for preview cell values, and it is a change to `docs/04`
section 6 rather than a gap in it.

## In flight

The Bo3 backfill. `make scrape-full` is walking `gen9championsvgc2026regmbbo3`
back to its first replay at one request per second. It is resumable and stateless
between runs, so killing it costs at most one replay and re-running picks up
where it stopped. Re-run `make scrape` periodically regardless: it is incremental
and stops at the first page with nothing new on it, which takes seconds.

The Bo1 format is deliberately not backfilled yet. It is roughly ten times
denser than Bo3 and carries no labels, so it is the cheaper corpus to collect
late and the more expensive one to collect first.

## Blocked

Nothing.

## Broken, not mine

Nothing broken.

The two `tests/test_oracle.py` clone failures recorded here last session were
not a clone bug. `js/sim_server.js` clone is fully independent: driving a clone
ten decision points forward leaves the parent's turn counter and protocol log
untouched, verified directly against the simulator. The tests were asserting
that two `default` choices always advance the turn, which is false -- a KO
produces a forced-switch request that consumes a step without incrementing
`turn`, and which turns those are is a property of the teams. `regmb-beta.txt`
was replaced in the working tree, which moved a KO onto turn 2 and broke the
assumption. Both tests now assert what they actually meant (the parent's log is
an unchanged prefix, and the clone's is far ahead) and are team independent.

M0 through M4 are committed and pushed. M1 through M3 landed as four commits
split by area; none of the four is individually runnable, because the work
arrived as one uncommitted mass and `champions/agents/baseline.py` carries all
of it in a single diff, so the split is for readable history rather than for
bisecting.

Commits in this repository carry no `Co-Authored-By` trailer. Five that did were
rewritten and force-pushed on 2026-08-29 at Alex's request; the trees were
byte-identical before and after, only the messages changed.

Still uncommitted and not mine: `data/teams/regmb-beta.txt` has been replaced
with a different six, and `.gitignore` has three added lines. Also uncommitted
and deliberately so: `.claude/`, `.agents/` and `skills-lock.json`, which are
tooling configuration rather than project work and are not Claude Code's to
commit unasked.

## Next action

M5: the belief filter. Spread interval propagation first, then categorical
particles from the learned prior. `docs/03-belief-filter.md` is the spec, and
three things from M3 and M4 change how it should be built.

Nature is a learnable label now, not an inference target (D33), and there are
27,600 labelled sets in the corpus with a nature on every one of them. The
categorical half of the filter should predict it alongside item, ability and
moves, and interval propagation is left with stat points alone.

The observation stream it needs already exists and is already ordered.
`champions/protocol/parser.py` emits it live as `turn_result` and offline into
`reveals`, both keyed on a monotonic `seq`, because the order moves resolve in
is the only Speed evidence the protocol gives (D32). M5 consumes that rather
than re-parsing anything.

And M4 says what the prior is worth. A species-only prior over the opponent's
*bring* carries almost no information (9.4% against a uniform 6.7%), so the
belief filter should not expect much from team composition alone; what it needs
is the set-level prior over items and abilities, which is where both M2 and M4
independently located the missing information.

Worth doing alongside, and now carried for a third session, which is long
enough that it should either be done or dropped: re-run T0.8's acceptance table
on a single team so its numbers mean what they say (D30), and run
`policy.discard_rate` over a batch of real positions to measure how much
equilibrium mass the heuristic's pruning throws away. The second is the guard
`docs/04` section 3 requires; it is implemented and has never been run against
a real position.

## Open questions

Questions raised by implementation that the design has not answered. Cowork picks these up, revises the relevant document, and clears the entry.

- Whether the coach should ingest games from Champions itself, given the target game produces no replay file. Deferred until M9.
- What the eventual path to Champions looks like, and how much of the decision layer stays portable when the transport changes.
- **Answered.** `turn_result` is a parsed digest of the log, and it is now
  emitted (D32). The observations come from the same parser that builds the
  replay corpus, so the live agent and the offline corpus cannot drift apart,
  and `battle_end` carries the final observations plus a count of any protocol
  the parser could not read. `docs/07-observability.md` section 2 should record
  the payload shape.
- Whether to vendor `smogon/pokemon-showdown-client` so a real Showdown client
  can be embedded in the viewer rather than opened beside it (D16). It would cost
  a second pinned repo and a Node build step, and cuts against two `CLAUDE.md`
  conventions, so it is deliberately not done. Worth revisiting only if the
  rendered stage turns out to be insufficient in practice.
- Traces are 300KB to 1MB per battle, up from a few KB, because `candidates`
  enumerates the whole legal joint action set (about 98 to 156 joint actions per
  turn). `docs/07-observability.md` section 5 says to rotate and compress per
  battle, which nothing does yet. The previous version of this entry expected
  M2's pruning to remove most of the volume; it did the opposite. The one ply
  agent emits a *second* `candidates` event carrying the pruned set, its policy
  scores, the full payoff matrix and both equilibrium strategies, on top of the
  unpruned one the base class already emits. Measured over 50 games on the same
  team: 363 KB per battle for one-ply against 289 KB for max-base-power, so
  about 26% larger rather than smaller. Compression is now worth doing on its
  own merits rather than waiting for a reduction that is not coming.
- `docs/05-data-pipeline.md` section 5 says stat points **and natures** appear in
  no public dataset, and calls that split the boundary of the learnable
  component. The nature half is wrong: every set in a forced-open-sheet Bo3
  replay carries its nature, measured at 4,392 of 4,392 (D33). The correction
  matters beyond the sentence, because `docs/03-belief-filter.md`'s two
  structurally different halves are justified by that split, and one of the two
  is smaller than the document assumes. Both documents need revising.
- Two Bo3 replays in 361 carry no `|showteam|`, and both are unrated. The
  likeliest reading is that Force Open Team Sheets applies to rated play only,
  but two cases is not a measurement. Worth confirming from the format's rule
  table in the vendored mod rather than from the corpus, since the corpus can
  only ever show the ones that exist.
- `docs/05-data-pipeline.md` section 3, tournament team lists from RK9 and
  Victory Road, is specified and unbuilt. M3 delivered sections 1, 2 and 6. The
  case for section 3 was joint distributions over set composition at tournament
  level; the Bo3 open-sheet corpus now supplies joint distributions too, from a
  weaker population but at far greater scale and with no scraping-terms
  question attached. Worth deciding whether section 3 is still wanted before
  anyone builds it.

- `docs/04-decision-engine.md` section 6 says the trained evaluation function
  supplies each cell of the preview matrix. It cannot: `search/evaluate.py`
  reads a state snapshot and there is no state at preview, so it returns 0.5 for
  all 225 cells (D37). The section needs a different source for cell values, and
  the recommendation from M4 is self-play rather than the corpus -- roughly
  4,500 battles for a full 15 x 15 at 20 games a cell, single-digit minutes
  locally, and no skill confound because both sides are the same agent.
- `docs/04-decision-engine.md` section 6 also calls the preview "likely the
  highest ratio of win rate to engineering effort in the entire project". That
  rests on the preview being a game, which requires the payoff to have an
  interaction term; a separable payoff makes it an argmax (D38). Worth saying so
  in the document, because the failure is silent -- the solve returns a
  confident pure strategy either way.
- M4 could not fit a preview value function from replay outcomes: skill
  dominates at this sample size, with the higher-rated player winning 57.4% of
  1,808 rated games (D39). M6 plans to fit the in-battle evaluation on the same
  corpus and will meet the same confound. Whether M6 controls for rating,
  restricts to a rating band, or moves to self-play labels is a design question
  that should be settled before it starts rather than after.

- poke-env's battle state is built from mainline Gen 9 data, and Champions differs in 303 moves, 256 items, and 8 abilities. Answered as far as the numbers go: nothing numeric comes from poke-env. Stats, base power, PP, natures, the type chart and the format's own rule table are read from the resolved dump through `Dex`, and the search layer works on the trace snapshot and plain numbers rather than on poke-env objects. The structural half is now effectively decided too -- `evaluate`, `payoff` and `policy` all read the snapshot dict, so poke-env is confined to transport and legality. Worth confirming in `docs/08` that this is the intended shape rather than an accident.
- `docs/02-mechanics-deltas.md` says "roughly 347 species"; the measured legal pool is 355 (264 excluding battle-only formes, 74 of which are Mega formes). Worth reconciling so the number in the doc is the one the code agrees with, and so it is clear which of the three counts the doc meant.
- The local simulator is faster than the reference container: 2.13 ms per clone plus step against 4.7 ms. The budget arithmetic in `docs/02-mechanics-deltas.md` section 7, and the depth-2 feasibility conclusion drawn from it, were computed at 4.7 ms. Worth redoing at the local figure before M8 treats that conclusion as settled. M2 makes this concrete rather than theoretical, since it is the first thing to spend the per-turn budget.
- `docs/02-mechanics-deltas.md` section 4 says effective PP is `1.6 * min(base PP, 20)`. That is the mainline fully-PP-upped factor and it is wrong for Champions. The mod caps base PP at 20 in `Scripts.init` and overrides `calculatePP` to `(pp / 5 + 1) * 4`, so effective PP is `0.8 * pp + 4`: a 20 PP move has 20, not 32. Confirmed against the simulator's own request objects, which report `maxpp` 8 for Protect (whose base PP the mod also lowers to 5), 16 for Flare Blitz and 8 for Fire Blast. `champions.dex.stats.max_pp` implements the real formula and `tests/test_stats.py` checks it. The doc needs the correction.
- `docs/02-mechanics-deltas.md` section 4 also says Champions overrides `modifyDamage`, which is true, and is easy to read as meaning the damage formula differs, which is not. The override is numerically identical to mainline (D26). Worth saying so explicitly in the doc, because "roughly 250 moves and 250 items carry overrides" plus "modifyDamage is overridden" reads as a changed formula when the real situation is an unchanged formula fed entirely different inputs — which is the more dangerous shape and deserves to be stated as such.
- The evaluation function is not calibrated and says so (`IS_CALIBRATED = False`). `docs/04-decision-engine.md` section 5 requires a reliability diagram before it is used anywhere, and the coach's ex-ante loss is defined in probability units. Nothing currently checks the flag before reporting. M6 fits the weights; whoever writes the coach should decide what it does when handed an uncalibrated evaluation -- refuse, or report in log odds.
- The opponent model is "their revealed moves, and nothing if they have revealed none", so on turn one the matrix has a single column and the equilibrium degenerates to an argmax against an opponent doing nothing. That also inflates the game value badly -- 0.997 on turn one is typical -- which does not much affect the choice, since every cell shares the bias, but does make `game_value` unusable for the coach until M5. Inventing unrevealed moves is not the fix; the belief filter is.
- `docs/04-decision-engine.md` section 4 mandates roll bucketing as a cost reduction. It turned out to be cheap enough to enumerate exactly: two buckets per attack, at most four attacks, sixteen branches. So nothing samples, and the common random numbers requirement in the same section is satisfied by there being no randomness rather than by shared seeds. Worth reflecting in the document, since a reader implementing to spec would build a sampler that is not needed.
- The turn model scores a switch as giving up the turn, because the incoming Pokemon's value is a next-turn question. This is a real and intended bias against switching and it is the clearest thing depth would fix. Nothing measures how much it costs; it should be quantified before M8 weighs depth against the alternatives.

## Notes for the next session

The repository was moved off OneDrive during T0.1: it now lives at `C:\dev\pokemonbot`, not `C:\Users\bingk\OneDrive\Desktop\pokemonbot`. Git history is intact (local clone, then `origin` repointed to `https://github.com/alexzhangryan/pokemonbot.git`). The old OneDrive copy may still be sitting on disk pending manual deletion by Alex — if so, it is stale and should be ignored, not worked from.

Claude Code never runs `git push` in this repository — Alex pushes himself. Local commits can get ahead of `origin/main`; check `git log` vs `git log origin/main` rather than assuming they match.

`data/dex/*.json` (the dex dumps) are gitignored and regenerated locally via `python scripts/build_dex.py gen9championsvgc2026regmb --delta`; only `docs/dex-delta.md` and `vendor/SHOWDOWN_COMMIT` are committed.

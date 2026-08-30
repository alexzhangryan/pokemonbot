# Status

Mutable. Current state only. History belongs in `DECISIONS.md`.

Whoever finishes a work session updates this file before stopping. Whoever starts one reads it first.

Last updated: 2026-08-29, by Claude Code.

New to this project: read `docs/QUICKSTART.md`. It covers setup and how to
manually exercise everything, including playing against the bot yourself in a
browser.

## Current milestone

**M0 through M6 are done. M7, the policy provider benchmark, is next**, and the
thing D55 said to do first — build the implementation A `docs/04-decision-engine.md`
section 3 actually specifies — is done and measured. See **Next action**.

| | what it delivered | where it is measured |
| --- | --- | --- |
| M0 | scaffolding, dex dump, trace schema, local server, watchdog, harnesses | `docs/09-m0-tasks.md`, `docs/benchmarks.md` |
| — | the observability surface, pulled forward out of M10 (D13) | below |
| M1 | the Champions stat and damage layer, validated cell by cell against the simulator | below |
| M2 | the one ply agent: prune, estimate, solve by LP, sample the equilibrium | below |
| M3 | the shared protocol parser, the replay scraper, the SQLite corpus | below |
| M4 | the bring-4 and lead predictors, the exact preview equilibrium | below |
| M5 | the belief filter, wired into play | below |
| M6 | the fitted, calibrated evaluation function | `docs/eval-calibration.md` |
| — | the pruning guard, run against real positions | `docs/pruning-guard.md` |
| — | the specified implementation A, built and measured against the old one (D61) | `docs/pruning-guard.md` |

Three milestones in a row have produced negative results worth more than their
code, and they compose into one reading.

- **M4.** Leads predict well and transfer to unseen players; the bring-4 does
  not predict at all from species, and the preview value model is worse than a
  coin flip out of sample. Skill dominates these ladder outcomes and preview
  features do not observe it.
- **M5.** The belief supplies exactly the opponent information M2 identified as
  missing. Each half is win-rate neutral alone and the two together are
  neutral-to-worse.
- **M6.** The evaluation is now calibrated, and the way it got there was by
  refusing to believe its own weight table: three of its four findings are bugs
  a naive fit would have absorbed into a coefficient.

The binding constraint has moved from what the search *knows* to what its
one-turn payoff model can *do* with what it knows. The pruning guard briefly
added a second constraint — whether the candidate set even contains the answer —
and rebuilding implementation A to section 3's specification has largely removed
it: discarded mass at the agent's own `k` fell from 0.639 to 0.174 and the win
probability given up fell from 0.061 to 0.008.

The next four sections are the milestone record and are long. The operational
ones a session actually needs are **In flight**, **Blocked**, **Tests**,
**Uncommitted**, **Next action** and **Open questions**, at the end of the file.

## M0

Complete, every acceptance criterion met. `docs/09-m0-tasks.md` has the
task-by-task detail and is the place to read it; what follows is only what a
later milestone still reasons from.

- Showdown is vendored at `bb179fbf8449e3c31632bd56f671ffb4404fa6e7`
  (`vendor/SHOWDOWN_COMMIT`) and gitignored. Both mods and both Reg M-B format
  IDs exist in this checkout.
- The dex dump is the single source of every number: 355 legal species, 148
  non-nonstandard items, 75 Mega Stones, and 303 moves / 256 items / 8 abilities
  differing from mainline gen9 with nothing added or removed
  (`docs/dex-delta.md`). Reproduces byte-for-byte against an unchanged build.
- Local engine performance, against the cloud reference the design's budget
  arithmetic was written at: 27.2 full battles/s vs 26.8, **569.5 turns/s vs
  303**, clone plus step **2.13 ms vs 4.7 ms** (`docs/benchmarks.md`). The
  per-turn simulator budget here is larger than the figure the M8 gate was
  sketched against.
- The action space is about 156 joint actions per side with the Mega flag
  available; measured on a live request.
- Baselines at 50 games, seed 1: random 20.0% [11.2%, 33.0%], max-base-power
  80.0% [67.0%, 88.8%], 0.0% of turns over 45s.
- The differential harness passes 1,000 distinct positions with 0 divergences,
  reproduced across runs. It has a second engine's worth of interface and no
  second engine.
- `docs/QUICKSTART.md`, `scripts/play_human.py` and `scripts/show_trace.py`.

## Milestones since M0

### Observability, pulled forward out of M10 (D13-D24)

`docs/07-observability.md` put both clients at M10. With no consumer, nothing
forced the emission to be complete, and `turn_start` was carrying four species
names and no state. Building the consumer first is what made the emission real.
D13 through D24 are the twelve decisions this took and are the place to read how
it got here; what it *is* today:

- `champions/protocol/state.py` — `snapshot()`, the full observable state as
  plain JSON. Our side exact (HP, stats, item, ability, PP), the opponent's only
  as revealed, unknowns explicitly null. Every number comes from the Champions
  dex and records which source it came from, so a mainline poke-env value
  leaking in is a test failure rather than a silently wrong game.
- `champions/protocol/actions.py` — `describe()`, a `BattleOrder` as structure
  rather than as a `/choose` string.
- `champions/agents/baseline.py` — the whole decision pipeline lives in
  `TracingPlayer.choose_move` and subclasses supply only `_search` and a strategy
  name, so a new agent inherits the entire observability surface without opting
  in.
- `champions/viewer/` — a FastAPI server and a no-build-step page that tails the
  trace directory, so live and replay are one code path and the viewer cannot
  influence play. It renders its own battle stage *and* embeds Showdown's own
  renderer replaying the protocol log (D16, D19: the client frame-busts, the
  renderer is published for embedding). Needs internet for the renderer's CDN
  and says so when it cannot reach it.
- `champions/viewer/control.py` — the supervisor. `scripts/viewer.py` is the
  only command a session needs: it starts the simulator, runs self-play or puts
  a bot up to be challenged, and owns the trace directory so no two components
  can disagree about where traces go (D17, D18). Runs are subprocesses, so a
  crashing agent cannot take the window down.
- `tests/test_viewer.py` (14), `tests/test_observability.py` (13),
  `tests/test_control.py` (10), plus Playwright browser QA against real Chromium:
  zero console errors, zero failed requests, no horizontal scroll at 1500 / 1100
  / 760 / 375px, and a live run attached to from an empty directory.

Three real defects fell out of having a consumer, all now covered by tests:
poke-env enums serialising as `"FLYING (pokemon type) object"` and
`"Status.PAR"`, an unrevealed opponent item serialising as the literal string
`"unknown_item"`, and nothing anywhere recording what actually happened in a
turn.

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
- `champions/search/evaluate.py` -- the evaluation, a logistic over
  side-difference features read off the trace snapshot. Antisymmetric between
  equally revealed sides, which is what lets the matrix game treat the payoff as
  zero sum. Hand-weighted at M2 and declaring `IS_CALIBRATED = False`; fit and
  calibrated at M6, where the flag became derived from whether a fitted weights
  file exists rather than being a constant anyone can edit.
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

Measured on 408 replays across both formats when M3 closed, and again on the
corpus as it stands (`make corpus`):

| | at M3 | 2026-08-29 |
| --- | --- | --- |
| replays | 408 | 17,096 (16,556 Bo3, 540 Bo1) and growing |
| unhandled protocol message types | 0 | 0 |
| open-sheet replays | 359 | 16,490 |
| complete sets extracted | 4,392 | 197,880 |
| natures present | 4,392 of 4,392 | all of them |
| stat point spreads present | 0 | 0 |
| bring-4 fully observed | 73% of games | not re-measured |

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

### M5: the belief filter

`docs/03-belief-filter.md`, built end to end and wired into play. Six modules
under `champions/belief/`, all reading the trace snapshot and the observation
stream rather than poke-env, so the offline evaluation and the live filter run
the same code on the same inputs.

- `champions/belief/spreads.py` -- the interval half. `docs/03` section 2's
  closed form, `u_i <- min(u_i, 32, 66 - sum_{j != i} l_j)`, which is exact for
  one resource constraint over a box and costs a handful of integer operations.
  Observations are inverted by scanning the 33 legal point values through
  `compute_stat` rather than by rearranging the formula, so the 16-bit
  truncation M1 transcribed once is not transcribed a second time backwards.
  `allocation()` produces a *legal* whole spread, which six independent
  midpoints are not (D46).
- `champions/belief/priors.py` -- the learned joint over whole registered sets,
  built from the corpus. 183,576 sets across 266 species as of the last
  `make priors`, 191 of them seen often enough to be drawn empirically rather
  than composed
  from marginals. A drawn set is coherent because a player registered it, which
  is a stronger guarantee than any consistency check over marginals gives.
  Nature is one of the fields (D40).
- `champions/belief/evidence.py` -- the observation stream folded into the four
  things that can actually be reasoned from: a reveal, a Speed ordering, damage
  we took (exact HP, so an exact bound on their offence), and damage they took
  (percent, so a joint bound on their bulk and HP). It tracks HP, boosts,
  status, weather, Tailwind, Trick Room, current types and current forme,
  because a single protocol line supplies none of those.
- `champions/belief/particles.py` -- the particle filter. Whole-team hypotheses
  under Item Clause, sampled by a sequential draw plus a short Gibbs sweep,
  each carrying one `SpreadBelief` per species. Reveals are hard filters;
  Speed and damage are likelihoods (D41).
- `champions/belief/effects.py` -- what a hypothesised item and ability do to
  the numbers, with every table re-derived from the pinned Showdown source by
  the test suite (D42, D43).
- `champions/belief/filter.py` and `hypothesis.py` -- the assembly, and the two
  seams it plugs into. `champions/search/payoff.py` and `policy.py` have no
  import of `champions.belief` in either direction (D47).
- `scripts/build_priors.py` (`make priors`), `scripts/eval_belief.py`
  (`make eval-belief`), the `belief` agent in every registry, and a belief panel
  in the viewer showing per-Pokemon posteriors and two intervals per stat --
  the union over particles and the modal particle's box, which are different
  numbers and are used for different things.

`champions/protocol/parser.py` gained one field: `EFFECT` observations now carry
their untagged positional arguments in `detail["args"]`. `typechange` is why --
Protean rewrites a Pokemon's types mid-turn and both STAB and effectiveness
follow. Additive, so stored corpus rows stay valid; a `--reparse` picks it up.

Measured over 100 agent-view traces against a team whose file we hold, which
is the only source in the project that carries a stat point allocation --
30,492 stat-turns:

| | modal box (what the search reads) | union over particles |
| --- | --- | --- |
| interval coverage | 97.6% [97.4%, 97.7%] | 99.0% [98.9%, 99.1%] |
| mean width | 29.9 of 32 points | 31.0 of 32 points |

The categorical half, on the same traces: item top-1 52.5% (log loss 3.14),
ability 99.7% (0.03), nature 27.3% (1.18), and 71.7% of the true moves inside
the believed top four. Those are against a hand-written test team the corpus has
never seen, so they are a floor rather than a fair reading -- `make eval-belief`
and `scripts/eval_belief.py corpus` are the two halves, and the corpus half runs
against real ladder teams where the registered set is the truth.

Both are below nominal and both are reported as measured. Three changes moved
coverage during the build -- the forme fix, the type-change fix, and the
priority-ability guard -- and each is a decision entry, because each was a case
of the filter being confidently wrong in a way nothing in the output showed.

**The headline is a negative result, and it is an interaction.** Measured on
`regmb-beta`, both arms on the same team, 50 games, seed 1:

| agent | vs max-base-power | 95% CI |
| --- | --- | --- |
| one-ply (M2) | 58.0% | [44.2%, 70.6%] |
| belief, stats and effects only | 58.0% | [44.2%, 70.6%] |
| belief, believed action columns only | 58.0% | [44.2%, 70.6%] |
| belief, both | 44.0% / 46.0% | [31.2%, 57.7%] / [33.0%, 59.6%] |

and directly against the M2 agent, same team, 50 games: **52.0% [38.5%, 65.2%]**.

Each half of M5 on its own is exactly neutral, and the two together lose twelve
to fourteen points. The full agent was run twice, on two separate local servers,
and produced 44.0% and 46.0%; the three ablation arms tie at 58.0%. The
intervals all overlap, so this is not established at 50 games -- but two
independent runs of the combination landing below three independent runs of its
parts is not what a pure noise story predicts, and the direct head-to-head being
a dead heat says the effect is real and small rather than large.

The most likely mechanism, and it is a hypothesis rather than a finding: with
both halves on, the agent is playing an equilibrium against an opponent model
that is detailed but wrong in a *correlated* way. The M2 agent had a single "no
action" column, so it was effectively an argmax and the opponent model could not
mislead it. With believed columns it hedges, and with believed stats it hedges
against a specific wrong opponent -- and hedging against a confidently wrong
model is worse than not hedging at all. The one-turn analytic payoff is the
common factor: it models neither secondary effects nor status nor healing nor
accuracy, so a more precise opponent inside a coarse model may not be an
improvement.

What that implies for M6 and M8 is concrete. The M2 finding was that items and
abilities are worth more than search depth (D30); M5 supplies them and the win
rate did not move, so the missing piece is more likely the *payoff model* than
the information going into it. That is an argument for the simulator-backed
rollout M8 was going to weigh against depth, and the belief filter is exactly
what makes it possible -- stepping the simulator needs a complete opponent team,
which is what a particle is.

### M6: the fitted evaluation function

`docs/04-decision-engine.md` section 5, done. `champions/search/evaluate.py` now
loads fitted weights and `IS_CALIBRATED` is True — not because a flag was
flipped, but because it is now derived: True exactly when
`data/eval/weights.<format>.json` exists, and that file is written by the same
run that writes `docs/eval-calibration.md`. There is no way to claim calibration
without having measured it (D51).

Three modules and one entry point:

- `champions/search/positions.py` — labelled positions from both sources. The
  self-play half reads `turn_start.state` and `battle_end.result` straight off
  the trace, so the training features cannot differ from the ones the search
  computes. The corpus half rebuilds the position from a scraped replay's
  protocol, censoring the opponent to what a player on that side would have
  seen, emitting both viewpoints with opposite labels, and dropping ties.
- `champions/search/fit.py` — logistic regression by L-BFGS (no scikit-learn;
  thirty lines of regularised negative log likelihood and its gradient), split
  by battle, Platt scaling fit on a validation split, reliability diagram and
  metrics measured on a third, and bootstrap intervals per weight.
- `scripts/fit_eval.py` (`make fit-eval`) — fits both sources, blends, ships,
  and writes `docs/eval-calibration.md`. `make eval-games` generates the
  self-play it reads.
- The eval bar. `TracingPlayer` emits the evaluation on `turn_start`, so every
  agent has one and the viewer renders a number it did not compute. `calibrated`
  travels with the number, so a trace written before this still says its numbers
  are not probabilities.

**The shipped model**, over a test split partitioned by battle, as of the
re-fit on 2026-08-29:

| | self-play | corpus | shipped blend |
| --- | ---: | ---: | ---: |
| positions | 11,774 | 216,630 | — |
| battles | 750 | 31,696 | — |
| log loss | 0.5305 | 0.5665 | **0.5263** |
| log loss, base rate | 0.6931 | 0.6931 | 0.6931 |
| AUC | 0.8043 | 0.7649 | **0.8066** |
| expected calibration error | 0.0193 | 0.0068 | **0.0189** |

The blend beats either source alone on log loss and AUC. Weights:
`hp_advantage` +1.31, `pokemon_advantage` +0.84 and `active_hp_advantage` +0.49
from self-play; `status_advantage` +0.65, `speed_control` +0.51 and
`boost_advantage` +0.12 from the corpus; `hazard_advantage` at **0.0**,
undetermined by both.

The corpus column moves whenever the corpus grows and the self-play column does
not, since `runs/m6-selfplay/` is fixed. `docs/eval-calibration.md` carries
whatever the last `make fit-eval` measured and is the file to trust over this
table. A corpus "battle" is one viewpoint, so 31,696 of them are about 15,850
replays.

**Four findings, and three of them are bugs the fit would have hidden.**

1. The evaluation returned **0.996 on a dead-even opening position** (D51). Our
   side counted the registered six, the opponent the brought four. A fit with a
   free intercept absorbs a constant offset and reports a healthy log loss, so
   this would have survived M6 as a coefficient. It is caught by fitting
   *without* an intercept and reporting one as a diagnostic: on the corpus that
   diagnostic is +0.0000.
2. Platt scaling put the intercept back (D53). Textbook Platt is `a * x + b`,
   the `b` came out at +0.074, and an even position scored 0.518. The
   calibration now fits a slope alone. A structural invariant has to be defended
   at every stage that can reintroduce it, not only at the one thinking about it.
3. A weight table cannot be read at face value (D52). Self-play at 750 battles
   settled only three of seven weights. `status_advantage` came out at **-1.34**
   — the sign that says being burned is good — off 291 rows out of 11,774,
   because burn is the only status that mirror matchup inflicts. It looked
   exactly like the six numbers beside it. `fit.bootstrap_weights` resamples
   *battles* and reports a 95% interval per weight; a weight whose interval
   spans zero is one the source did not settle, and is taken from one that did.
4. `hazard_advantage` is undetermined by 750 self-play battles and by 31,696
   corpus ones alike, which is a result rather than a gap: entry hazards do not
   measurably predict the outcome of a Reg M-B doubles game.

Also measured on the way: the first self-play fit, at 150 battles, ranked
positions *better* than the corpus fit (AUC 0.793 against 0.760) and scored a
worse held-out log loss than a coin flip (0.7175 against 0.6931). Ranking and
calibration are different claims, and `shippable` now refuses to ship a model
that loses to always-predict-the-base-rate.

Two limits of the self-play source that more games will not fix. Neither
checked-in team carries Tailwind or a hazard move, so two features are constant
across the entire source. And `boost_advantage` is confidently negative there,
[-0.95, -0.11], against the corpus's [+0.098, +0.137] measured over the 17,500
battles the corpus held at the time —
Competitive on Milotic makes a large positive boost total on our side usually the
*consequence* of the opponent landing Intimidate or Icy Wind. Both are team
artifacts, and both argue for a third and fourth checked-in team before the next
fit.

### The viewer: conceding a game (D49, D50)

The control bar grew a **forfeit game** button beside **stop run**. Wanting out
of a game is common and wanting out of a run is not, and killing the process was
the only way out of either. A run is now spawned with a stdin pipe and
`--control-stdin`; `champions/agents/commands.py` reads one verb and schedules
`TracingPlayer.forfeit_active()` onto poke-env's loop. Verified end to end: game
1 of 4 conceded mid-battle, games 2 through 4 played out, 0 invalid traces, 0
protocol failures.

The bar was also tidied — the three bare number boxes are labelled, "play the
bot" defaults to three games (with one, the forfeit and the end of the run are
the same event), stopping a run asks and conceding does not, and the status line
drops the battle tag it had no room for.

Forfeiting found a real bug that has nothing to do with forfeiting (D50).
Showdown can hand out a request and then end the battle underneath it, and the
agent answered it: `battle_end` followed by a whole further turn of events, which
is invalid by our own validator and renders in the viewer as a turn that never
happened, plus a `/choose` into a room we had left. `choose_move` now returns
poke-env's `_EmptyBattleOrder` before emitting anything when `battle.finished`.

### The pruning guard, finally run

`docs/04-decision-engine.md` section 3 permits candidate pruning on one
condition: that it never drops an action that is uniquely correct, measured
offline by solving the unpruned game and recording how often its equilibrium
puts mass on a discarded row. `policy.discard_rate` implemented the per-position
half of that at M2 and had never been run against a real position, through five
sessions. It has now been run against 11,774 of them.

- `champions/search/discard.py` — reads decisions out of agent-view traces. A
  trace already carries all three inputs in the form the agent saw them:
  `turn_start.state` is the snapshot the search evaluated, the unpruned
  `candidates` event is the full legal joint set enumerated from the request,
  and the pruned one is the opponent columns and the surviving rows. Rebuilding
  them any other way would be a second path to the same three things, which is
  the failure `positions.py` exists to avoid on the feature side.
- `scripts/discard_rate.py` (`make discard`) — sweeps `k`, breaks the number out
  by opponent column count, and writes `docs/pruning-guard.md` and
  `data/eval/discard.<format>.json`.
- `tests/test_discard.py` (18, four of them added by D62).

**Measured over 750 self-play battles**, at the agent's own `k = 10`, with the
opponent columns the agent actually solved against and payoffs from the shipping
evaluation. This is the policy that was shipping at the time, now
`heuristic-base-power`:

| | at k = 10 |
| --- | --- |
| positions with anything to prune | 6,745 over 750 battles |
| equilibrium mass on discarded rows | **0.639** 95% [0.628, 0.650] |
| positions where any mass was discarded | **64.2%** |
| win probability given up, mean / worst | 0.061 / 0.580 |

On those same positions the budget is a real lever: k=5 discards 0.807, k=15
discards 0.519 and k=20 discards 0.320. Clock is not what stops us raising it —
M2 measured the whole decision at about 11 ms against a 45 second budget. In the
end it was not the lever that was used: the section below rebuilds the policy
instead, and gets a lower number at `k = 10` than raising the budget to 20 gave.

Two things make the number readable rather than alarming on its own. Mass is
all-or-nothing, so a row worth 0.9001 lost to one worth 0.9000 scores the same
1.0 as throwing away the only winning move; `value_loss` is reported beside it
and says which it was. And a position with one opponent column is an argmax
rather than an equilibrium — under the revealed-moves-only model most early
turns are — so the table is broken out by column count, and the wider positions
are *worse*, not better: 0.811 at two columns against 0.608 at one.

The validity check that matters: the kept set is re-derived from the policy
rather than read off the trace, and it disagreed with the traced candidate set
on **0** of 6,745 positions. This measures the selection the agent actually ran.

The finding is not that pruning is broken; it is that this particular heuristic
is — the implemented implementation A was a much thinner thing than the one
`docs/04` section 3 describes, and the rows it discarded were almost all
ordinary attacks that base power happened to rank low. D55 settled that section
3 stands and the code moves to meet it; the next section is that work, and it is
what makes this one the *before* half of a pair rather than the current state.

### The specified implementation A, built (D61, D62)

`docs/04-decision-engine.md` section 3 names four things the heuristic provider
does — a move that knocks out a target on an average roll, Protect when the slot
is threatened, speed control when it flips an outspeed, Fake Out on turn 1, plus
the switches — and the policy that shipped through M6 did none of them. It
ranked joint actions by base power and never looked at the board. D55 settled
that section 3 stands as written and the implementation moves to meet it.

- `champions/search/policy.py` — `HeuristicPolicy` is now the specified A. It
  reads the snapshot and computes damage with the M1 layer: a knockout is the
  mean of the sixteen rolls reaching the target's remaining HP, a threat is a
  revealed opponent move taking half a slot's remaining HP, a flipped outspeed
  is a race we currently lose and would win, and Fake Out is scored on
  `first_turn` rather than on the turn number. `_Position` memoises the damage
  per slot, because the joint set repeats the same handful of per-slot moves
  across its rows.
- `BasePowerPolicy` is the old one, kept. Every number written before this
  session describes it, and the 1,500 traces the guard reads were produced by
  it, which makes it the only policy whose re-derived candidate set can be
  expected to match what those traces recorded.
- `champions/search/payoff.py` — `targets_of` extracted to module level, so the
  policy and the turn model answer "what does Earthquake hit" from one
  implementation rather than two that quietly stop agreeing.
- `champions/protocol/state.py` — `first_turn` per Pokemon.
- `champions/agents/oneply.py` — the snapshot is taken before pruning rather
  than after, and the trace records which provider produced the candidate set
  rather than the literal `"heuristic"`.
- `champions/search/discard.py` and `scripts/discard_rate.py` — the guard hands
  the provider the position, and measures any number of providers against one
  solve of it (D62).
- `tests/test_policy.py` (18, new), plus additions to `test_discard.py`,
  `test_oneply.py` and `test_observability.py`.

**Measured over the same 6,745 positions and 750 battles, at the agent's own
`k = 10`:**

| | discarded mass | 95% | nonzero | mean value loss | worst |
| --- | --- | --- | --- | --- | --- |
| `heuristic-base-power` | 0.6391 | [0.6283, 0.6499] | 64.2% | 0.0607 | 0.5799 |
| `heuristic-position` | **0.1743** | [0.1656, 0.1846] | **18.1%** | **0.0078** | 0.3888 |

The specified A at `k = 10` discards less than the old one at `k = 20` (0.320),
so this bought more than doubling the budget would have. The remaining mass is
still concentrated in the wider positions — 0.154 at one opponent column against
0.281 at two — which is the same shape the old policy had and says the harder
half is still the harder half.

Cost: 0.67 ms per decision against 0.11 ms, on the widest positions in the
corpus, against a decision M2 measured at about 11 ms and a 45 second budget.
Not where the clock goes.

Two limits worth stating, because neither is fixed by more games. The threat
model is the opponent's *revealed* moves, so an unrevealed move cannot make a
slot look threatened — the belief filter is the thing that answers this and it
is not plumbed into the policy yet. And the positions, legal sets and opponent
columns are all the ones `heuristic-base-power` produced while playing: the
guard asks "what would this have kept here?", which is the right question for a
guard and is not the same as playing the games again with the new policy.

## In flight

**The Bo3 backfill, still.** `scrape_replays.py --format
gen9championsvgc2026regmbbo3 --full` has been walking the format back toward its
first replay since 14:28 on 2026-08-29 at one request per second (it shows as
two processes — a venv launcher and its child — not two scrapers). Resumable and
stateless between runs, so killing it costs at most one replay. `make scrape` is
the cheap incremental one: newest first, stops at the first page with nothing
new, seconds.

Consequence for anything measured against the corpus: **it is a moving number.**
It went 15,897 → 17,096 replays over a few hours on 2026-08-29. `make corpus`
is the only trustworthy reading, and any figure written down here is the reading at the
moment it was written.

The Bo1 format is deliberately not backfilled. It is roughly ten times denser
than Bo3 and carries no labels, so it is the cheaper corpus to collect late and
the more expensive one to collect first.

**A correction.** The 2026-08-30 revision of this file recorded the corpus at
"roughly 25,600 usable replays". It was not: 25,600 was the *battle* count the
M6 fit reports, which is two per replay because both viewpoints are emitted, and
the corpus never held that many replays. Replays are the unit the scraper and
`make corpus` speak; battles are the unit `fit.py` and every interval speak, and
the two were being read as the same thing.

`data/priors/setprior.*.json` and the M6 fit were both rebuilt on 2026-08-29
against a 15,897-replay corpus, which the backfill has already moved past.
Rebuilding is one command each (`make priors`, `make fit-eval`) and neither
breaks anything by going stale; the prior is simply older than the corpus. The
pruning guard is unaffected — it reads self-play traces, not the corpus.

`runs/m6-selfplay/` holds 750 self-play games (1,500 agent-view traces) that
`make fit-eval` and `make discard` both read. It is gitignored and reproducible
with `make eval-games`, which takes hours; deleting it costs that, not
correctness.

## Blocked

Nothing.

## Tests

**377 pass in about 150s**, whole suite, as of 2026-08-29. One is known flaky.

`make lint` and `ruff format --check` are clean. **`make typecheck` is not**:
`mypy .` reports 45 errors across 11 files, and `make check` therefore fails on
its second step. None are in the newer modules — the concentrations are
`scripts/run_ladder.py` (7), `champions/preview/model.py` (5), and `value.py`,
`search/fit.py` and `search/matrix.py` (4 each), mostly poke-env kwargs and
numpy/scipy stub friction. Long-standing, nobody's this session, and worth a
pass of its own rather than a line here. The count is unchanged by this
session's work: the five modules it touched typecheck clean.

`tests/test_oneply.py::test_the_agent_beats_max_base_power_on_the_same_team`
asserts the one ply agent wins more than 5 of 10 games; D48 measures that agent
at 58%, and the simulator's own RNG is not seeded by us, so P(5 or fewer wins)
is about a third on every run. It has failed and then passed repeatedly with
identical code.

Deliberately not fixed, because both fixes are decisions rather than repairs:
raising the game count makes an already slow suite slower, and lowering the
threshold weakens an acceptance criterion to make a test green. Worth settling
explicitly rather than by whoever hits it next.

Fixed, and recorded here only because it was listed as broken: the two
`tests/test_oracle.py` clone failures were never a clone bug. `js/sim_server.js`
clone is fully independent, verified directly against the simulator. The tests
had assumed two `default` choices always advance the turn, which a KO's forced
switch makes false; both now assert what they meant and are team independent.

## Uncommitted

`HEAD` is `f0a8cca`, "M4: bring-4, leads, and the preview equilibrium". **M5,
M6, the viewer's forfeit work, the pruning guard and the specified policy A are
all uncommitted**, in the working tree only. Five commits, in this order, would
give readable history:

| commit | what is in it |
| --- | --- |
| viewer (D49, D50) | `champions/agents/commands.py`, `tests/test_commands.py`, edits to the supervisor, the server, the page and both run scripts |
| M5 | everything under `champions/belief/`, `champions/agents/belief_agent.py`, `scripts/build_priors.py`, `scripts/eval_belief.py`, `tests/test_belief.py`, edits to the parser, payoff, policy, both agents, the viewer, the Makefile and three docs |
| M6 | `champions/search/positions.py`, `champions/search/fit.py`, `scripts/fit_eval.py`, `tests/test_positions.py`, `tests/test_fit.py`, `data/eval/weights.*.json`, `docs/eval-calibration.md`, edits to `evaluate.py`, `state.py`, `baseline.py`, `tests/test_evaluate.py` |
| the pruning guard | `champions/search/discard.py`, `scripts/discard_rate.py`, `tests/test_discard.py`, `data/eval/discard.*.json`, `docs/pruning-guard.md`, edits to the Makefile and `CLAUDE.md` |
| the specified A (D61, D62) | `tests/test_policy.py`, edits to `champions/search/policy.py`, `payoff.py`, `discard.py`, `champions/protocol/state.py`, `champions/agents/oneply.py`, `scripts/discard_rate.py`, `tests/test_discard.py`, `tests/test_oneply.py`, `tests/test_observability.py`, and the regenerated `docs/pruning-guard.md` and `data/eval/discard.*.json` |

The viewer commit is independent and could land first. The last one is the only
one that is genuinely separable — it is this session's work and nothing before it
depends on it. None of the other four is individually runnable in the sense of
passing on its own, for the same reason M1 through M3 were not: the work arrived
as one mass and `champions/agents/baseline.py` carries much of it in a single
diff. The split is for readable history rather than for bisecting.

Commits in this repository carry no `Co-Authored-By` trailer. Five that did were
rewritten and force-pushed on 2026-08-29 at Alex's request; the trees were
byte-identical before and after, only the messages changed.

Uncommitted and not Claude Code's: `data/teams/regmb-beta.txt` has been replaced
with a different six, and `.gitignore` has three added lines. Uncommitted and
deliberately so: `.claude/`, `.agents/` and `skills-lock.json`, which are tooling
configuration rather than project work.

## Next action

**M7: the policy provider benchmark**, and the thing D55 said to do first is
done. `docs/08-implementation-blueprint.md` puts heuristic against learned prior
against language model, measured identically on decision quality, discard rate
and latency. `champions/search/policy.py` now has `PolicyProvider` and two
implementations of A, both measured; the work is B and C.

The harness is finished rather than half-finished. `discard.measure_many` takes
a mapping of provider name to `keep` callable and measures every one of them
against a single solve of each position, so adding B is a dict entry in
`scripts/discard_rate.py`'s `PROVIDERS` and adding C is another. Section 3 asks
for the providers to be benchmarked *identically*, and that is now what the
harness does rather than what a reader has to assume about two separate runs.

Read before starting, in this order:

1. `docs/pruning-guard.md`. The numbers to beat are 0.174 discarded mass and
   0.008 value loss at `k = 10`, not the 0.639 and 0.061 that were there before
   this session. The old policy is still in the table as
   `heuristic-base-power`, which is what a benchmark against a weak baseline
   would have looked like.
2. M6's discipline. It moved the evaluation from "the ordering is
   uncontroversial" to "here is the reliability diagram" by refusing to believe
   its own weight table. Three providers producing three numbers is not a result
   until the numbers come with intervals over *battles*;
   `fit.bootstrap_weights` and `discard.summarise` are both that pattern.
3. The two limits in the section above. The guard scores what a provider *would*
   have kept on positions another provider played, and the threat model sees
   only revealed moves. Neither is fixed by more games, and both bound what any
   of the three numbers can mean.

Two smaller decisions from the same triage land inside M7 rather than beside it
(D57, D58): traces get gzipped per battle before the three-provider runs multiply
them, and the `belief` against `oneply` head-to-head is deferred until after M8,
so it is not a prerequisite for anything here.

Two things the specified A leaves behind, both cheap and neither blocking:

- **Its win-rate effect is unmeasured.** The guard says the candidate set now
  contains the equilibrium's answer far more often; it does not say the agent
  wins more, and the one-turn payoff model is enough of a bottleneck that it
  might not. `oneply` against the same agent built on `BasePowerPolicy` is a
  paired mirror matchup and the cheapest real answer.
- **The belief is not plumbed into the policy.** `opponent_candidates` takes a
  `believed_moves` callable and the threat model does not, so an unrevealed move
  cannot make a slot look threatened. The seam exists on the payoff side; this
  is the same seam one layer up.

Three things M6 leaves behind:

- **A third and fourth checked-in team.** Two of the seven evaluation weights
  cannot be fit from self-play at all, because neither `regmb-alpha` nor
  `regmb-beta` carries Tailwind or a hazard move, and a third is actively
  misleading because Competitive on Milotic inverts what a positive boost total
  means. This is not a sample size problem and no number of games fixes it. Two
  more teams, chosen to cover speed control and hazards, would make the
  self-play source able to settle its own weights.
- The corpus half of the fit is skill confounded by construction (D39) and is
  used as a lender rather than as the shipping model. If `hazard_advantage` is
  ever wanted as a real number rather than a zero, the corpus is where it has to
  come from, and 31,696 battles were not enough to settle its sign.
- `scripts/selfplay.py` derives Showdown usernames as `champ-a` / `champ-b` with
  no per-run suffix, so two self-play runs against one server collide and both
  hang. This is the same defect already recorded for `scripts/run_ladder.py`.
  `run_selfplay` already takes `username_suffix`; the command line does not
  expose it. One flag fixes both.

Two smaller things M5 leaves behind:

- The 44% / 58% / 52% spread across three matchups at 50 games each is mostly
  interval width. The comparison worth making at higher n is `belief` against
  `oneply` directly, which is paired and has less variance than either arm
  against a third agent. **Deferred until after M8 (D58)**, because the
  hypothesis it tests — that hedging against a detailed opponent model inside a
  coarse payoff model is worse than not hedging — is a claim about the payoff
  model M7 and M8 are about to replace. Not a prerequisite for anything here.
- `scripts/run_ladder.py` derives Showdown usernames from the arm's display
  name, so two runs of the same matchup collide, and a killed run leaves the
  name held until the local server restarts. Still worth the one flag, since
  every high-n run needs it.

## Open questions

Questions raised by implementation that the design has not answered. Cowork picks
these up, revises the relevant document, and clears the entry.

This section was triaged on 2026-08-29. Most of what was in it were not questions:
they were corrections the implementation had already made, and decisions nobody
had been asked for. The four live decisions were put to Alex and are now D55
through D58; two more entries were closed as D59 and D60. What is left is grouped
by what it actually needs — a document edit, an hour of work, or an answer.

### Settled. Waiting only on the document (D55-D60)

None of these need a decision. Each names the edit.

- **D55. Done, nothing left for Cowork.** Section 3's implementation A stood as
  written and the code has moved to meet it (D61). No edit to section 3 was
  needed and none was made. `docs/pruning-guard.md` no longer needs the
  disclaimer the entry asked for, because it is regenerated with both policies
  in one table and names which is which.
- **D56.** Section 6's preview gets a coarse separability test before any full
  self-play matrix. Section 6 needs two corrections regardless of how it comes
  out: the trained evaluation cannot supply cell values, because there is no state
  at preview and `search/evaluate.py` returns 0.5 for all 225 cells (D37); and the
  "highest ratio of win rate to engineering effort" claim rests on the payoff
  having an interaction term, which is exactly what the test measures (D38). Say
  so in the document, because the failure is silent — the solve returns a
  confident pure strategy either way. What section 6 should say *instead* waits on
  the measurement.
- **D57.** `docs/07-observability.md` section 5's rotation and compression is
  being built as per-battle gzip. The document should also record the constraint
  that made the *how* load bearing: `champions/search/discard.py` reconstructs the
  unpruned game from the `joint` list, so compress the file and never thin the
  event.
- **D58.** M5's `belief` against `oneply` head-to-head is deferred until after M7
  and M8. No document edit; the entry is here so the next session does not
  re-raise it.
- **D59.** `docs/05-data-pipeline.md` section 3, tournament team lists from RK9
  and Victory Road, is cut. Delete the section.
- **D60.** The Showdown client is not vendored (D16) and the entry is closed.

### Corrections the implementation has already made

Eight places where the code found the truth and the document still carries the old
thing. No decision in any of them; they want one editing pass.

- `docs/02-mechanics-deltas.md` section 4 says effective PP is
  `1.6 * min(base PP, 20)`. It is `0.8 * pp + 4`. The mod caps base PP at 20 in
  `Scripts.init` and overrides `calculatePP` to `(pp / 5 + 1) * 4`, so a 20 PP
  move has 20, not 32. Confirmed against the simulator's own request objects,
  which report `maxpp` 8 for Protect (whose base PP the mod also lowers to 5), 16
  for Flare Blitz and 8 for Fire Blast. `champions.dex.stats.max_pp` implements
  the real formula and `tests/test_stats.py` checks it.
- The same section says Champions overrides `modifyDamage`, which is true, and is
  easy to read as meaning the damage formula differs, which it does not — the
  override is numerically identical to mainline (D26). Worth saying so explicitly,
  because "roughly 250 moves and 250 items carry overrides" plus "modifyDamage is
  overridden" reads as a changed formula when the real situation is an unchanged
  formula fed entirely different inputs, which is the more dangerous shape and
  deserves to be stated as such.
- `docs/02-mechanics-deltas.md` says "roughly 347 species". The measured legal
  pool is 355, or 264 excluding battle-only formes, 74 of which are Mega formes.
  Reconcile so the number in the document is the one the code agrees with, and so
  it is clear which of the three counts the sentence meant.
- `docs/04-decision-engine.md` section 4 mandates roll bucketing as a cost
  reduction. It turned out cheap enough to enumerate exactly: two buckets per
  attack, at most four attacks, sixteen branches. Nothing samples, so the common
  random numbers requirement in the same section is satisfied by there being no
  randomness rather than by shared seeds. A reader implementing to spec would
  build a sampler that is not wanted.
- `docs/05-data-pipeline.md` section 5 says stat points **and natures** appear in
  no public dataset, and calls that split the boundary of the learnable component.
  The nature half is wrong: every set in a forced-open-sheet Bo3 replay carries
  its nature, measured at 4,392 of 4,392 (D33). The correction matters beyond the
  sentence, because `docs/03-belief-filter.md`'s two structurally different halves
  are justified by that split, and one of the two is smaller than the document
  assumes.
- `docs/03-belief-filter.md` section 2 proposes 25 nature hypotheses per Pokemon,
  each carrying its own interval set. Natures are labelled in the corpus (D33), so
  the implementation draws one nature per particle from the prior instead and
  leaves the interval layer with stat points alone (D40). Section 5's sentence
  assuming the interval layer is where natures are resolved needs the same fix.
- `docs/07-observability.md` section 2 should record the `turn_result` payload
  shape. It is a parsed digest of the log and it is now emitted (D32). The
  observations come from the same parser that builds the replay corpus, so the
  live agent and the offline corpus cannot drift apart, and `battle_end` carries
  the final observations plus a count of any protocol the parser could not read.
- `docs/08-implementation-blueprint.md` should confirm that confining poke-env to
  transport and legality is the intended shape rather than an accident. Nothing
  numeric comes from poke-env: stats, base power, PP, natures, the type chart and
  the format's own rule table are read from the resolved dump through `Dex`, and
  `evaluate`, `payoff` and `policy` all read the trace snapshot dict rather than
  poke-env objects.

### Chores filed here by mistake

Three things that read as questions and are not.

- **`champions/protocol/state.py` reports the base forme's base stats for a
  Pokemon that has Mega Evolved**, because poke-env does. Champions has 75 legal
  Mega Stones, so this is common. The belief filter works around it by reading
  base stats from the dex for whichever forme is on the field (D45); the payoff
  model does not, and has been reading base-forme stats for opponents since M2.
  Fixing it at the source fixes both at once.
- **`scripts/selfplay.py` and `scripts/run_ladder.py` collide on Showdown
  usernames.** Two runs against one server both hang, and a killed run leaves the
  name held until the local server restarts. `run_selfplay` already takes
  `username_suffix`; the command line does not expose it. One flag fixes both.
- **Two Bo3 replays in 361 carry no `|showteam|`, and both are unrated.** The
  likeliest reading is that Force Open Team Sheets applies to rated play only, but
  two cases is not a measurement. Confirm from the format's rule table in the
  vendored mod rather than from the corpus, since the corpus can only ever show
  the ones that exist.

### Still open

Five. Each states the choice rather than describing the situation.

- **Interval coverage is 97.8% against a nominal target, and the fix is a trade.**
  `docs/03-belief-filter.md` section 5 says coverage "should be at or above the
  nominal level". It is 97.8% for the box the search reads and 99.3% for the union
  over particles. Three separate causes were found and fixed during the build
  (D44, D45, and the type-change fix), each of which moved the number, so the
  remaining shortfall is most likely more of the same — effects the table does not
  model — rather than the quantization the document attributes it to.

  *Widen the tolerance until coverage is nominal.* An honest guarantee that the
  filter cannot eliminate the true hypothesis, at the cost of wider boxes and a
  weaker belief. The argument for it is that M5 already showed belief precision is
  not the binding constraint, so the safety is cheap.

  *Keep narrow boxes and model more effects.* Each one added is measurable
  directly in the coverage table, but the tail is 139 abilities and 109 items that
  the pinned Showdown source says can change a damage number, against the 23
  type-boosting items, 17 resist berries, Life Orb, Expert Belt, Choice Scarf and
  eleven abilities `champions/belief/effects.py` models today; the rest are
  counted, not approximated (D43). The argument against widening is that it makes
  the belief weaker in the one dimension it was built to be strong.

  Note that the *ordering* under the second option is not a design question — the
  corpus answers it. The items actually played are heavily concentrated, with
  Focus Sash, Sitrus Berry and Life Orb alone covering a third of every set in it.
- **What the coach does when handed an uncalibrated evaluation.** `IS_CALIBRATED`
  is True exactly when `data/eval/weights.<format>.json` exists, and that file is
  written by the same run that writes `docs/eval-calibration.md`, so calibration
  cannot be claimed without having been measured (D51). Nothing checks the flag
  before reporting, and a fresh clone has no weights file and legitimately falls
  back to the hand-chosen ones. The options are to suppress confidence numbers
  while still giving move advice, to refuse to run at all, or to check a default
  weights file into the repository so a fresh clone is calibrated — which then
  goes stale silently. Whoever writes the coach decides; it does not block before
  M9.
- **Whether the coach should ingest games from Champions itself**, given the
  target game produces no replay file. Deferred until M9.
- **What the eventual path to Champions looks like**, and how much of the decision
  layer stays portable when the transport changes.
- **What the switch bias costs.** The turn model scores a switch as giving up the
  turn, because the incoming Pokemon's value is a next-turn question. This is a
  real and intended bias against switching and it is the clearest thing depth
  would fix. Nothing measures how much it costs. Quantify it as part of M8's
  justification rather than after M8 has committed to depth.

One arithmetic job belongs beside that last entry and is not a question. The local
simulator is faster than the reference container: 2.13 ms per clone plus step
against 4.7 ms. The budget arithmetic in `docs/02-mechanics-deltas.md` section 7,
and the depth-2 feasibility conclusion drawn from it, were computed at 4.7 ms.
Redo it at the local figure before M8 treats that conclusion as settled.

### Cleared by measurement since the last triage

- **The opponent model is no longer degenerate.** It was "their revealed moves,
  and nothing if they have revealed none", so on turn one the matrix had a single
  column and the equilibrium degenerated to an argmax against an opponent doing
  nothing — with a game value around 0.997 that made it unusable for the coach.
  `opponent_candidates` now takes an optional `believed_moves`, which
  `BattleBelief` fills from a posterior over whole registered sets, and the
  `belief` agent supplies it. The old path is unchanged and still the default, so
  M2's measured numbers still mean what they said. `game_value` is a real number
  now; whether it is a *good* one is D58's question, deferred.
- **The M4 skill confound was settled rather than inherited.** M4 could not fit a
  preview value function from replay outcomes because skill dominates at that
  sample size, with the higher-rated player winning 57.4% of 1,808 rated games
  (D39), and M6 was expected to meet the same confound fitting the in-battle
  evaluation on the same corpus. It did, and answered it by measurement rather
  than by picking a rating band: self-play is the preferred source, the corpus is
  the check, and a weight is taken from whichever source settled its sign over a
  bootstrap resampling battles (D52). `hazard_advantage` ships at zero because
  neither source settled it, which is itself the finding.

## Notes for the next session

The repository was moved off OneDrive during T0.1: it now lives at `C:\dev\pokemonbot`, not `C:\Users\bingk\OneDrive\Desktop\pokemonbot`. Git history is intact (local clone, then `origin` repointed to `https://github.com/alexzhangryan/pokemonbot.git`). The old OneDrive copy may still be sitting on disk pending manual deletion by Alex — if so, it is stale and should be ignored, not worked from.

Claude Code never runs `git push` in this repository — Alex pushes himself. Local commits can get ahead of `origin/main`; check `git log` vs `git log origin/main` rather than assuming they match.

`data/dex/*.json` (the dex dumps) are gitignored and regenerated locally via `python scripts/build_dex.py gen9championsvgc2026regmb --delta`; only `docs/dex-delta.md` and `vendor/SHOWDOWN_COMMIT` are committed.

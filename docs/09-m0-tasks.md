# M0 Task List

Foundation milestone. Nothing here is intelligent. Everything here is depended on by everything else.

Definition of done for M0: every acceptance criterion below is met, `pytest` is green, a 50 game self-play run completes and produces valid traces, and the local benchmark numbers are recorded.

## T0.1 Repository initialization

Initialize git, commit the existing docs, create the virtual environment, install dependencies from `pyproject.toml`, and create the package skeleton described in `docs/08-implementation-blueprint.md`.

Acceptance: `ruff check .` passes, `pytest` runs and reports zero failures on an empty suite, `git log` has an initial commit.

## T0.2 Vendor and pin Showdown

Clone `smogon/pokemon-showdown` into `vendor/showdown` at a specific commit, run `npm install`, run `node build`. Record the commit hash in a tracked file, for example `vendor/SHOWDOWN_COMMIT`.

The reference build for the numbers in `docs/02-mechanics-deltas.md` was taken on 2026-08-29. Any later commit is fine as long as the hash is recorded and the dex delta is regenerated.

Acceptance: `node -e "require('./vendor/showdown/dist/sim/dex')"` resolves without error, the commit hash is committed, and `vendor/showdown` is gitignored.

## T0.3 Dex dump and mainline delta

Write `js/dump_dex.js` to dump the resolved dex for a format ID: species with base stats and abilities, learnsets, items including the `megaStone` map, and full move data, all post mod resolution. Write `scripts/build_dex.py` to invoke it, hash the output, and write `data/dex/<format_id>.<hash>.json`.

Then produce the delta: dump the same structures for mainline gen 9 and diff, yielding the explicit list of modified moves, items, and abilities. Commit the delta as a document. This list is not published anywhere and it is the engineering checklist for M1.

Acceptance: the dump exists and contains roughly 347 legal species and 148 non-nonstandard items, the delta report enumerates the modified entries, and re-running with an unchanged vendor build reproduces the same hash.

## T0.4 Trace schema and writer

Implement `champions/trace/schema.py` with the event types in `docs/07-observability.md`, and `champions/trace/writer.py` writing append-only JSONL, one file per battle, through an async queue so it never sits on the decision critical path.

Every event carries `schema_version`, `battle_id`, `seq`, and `t`. Unknown fields must not break readers, since the review client will run against traces from older agent versions.

Acceptance: a synthetic battle emits JSONL that validates against the schema and round-trips, and the writer adds under 1 ms to a decision, measured rather than assumed.

## T0.5 Local Showdown server

Run the vendored build as a local server with security disabled for testing.

Acceptance: the server accepts a websocket connection and a challenge in `gen9championsvgc2026regmb`.

## T0.6 Random agent completing games

Connect through `poke-env` in doubles, in the format, with a fixed legal team. Handle team preview, select four, and decline the Open Team Sheets prompt explicitly. Emit trace events throughout.

The team preview prompt is the failure point. An unhandled Open Team Sheets request stalls the agent at exactly the moment the timer is least forgiving.

Acceptance: 50 self-play games complete with no exceptions, all 50 produce valid traces, and zero games end by timeout or invalid choice.

## T0.7 Deadline watchdog

An anytime wrapper around decision making. The decision maintains a current best action and returns it when the deadline arrives, whether or not it has finished.

This is a correctness requirement, not performance work. Showdown's `VGC Timer` auto-loses inactive players, so a live game is clock enforced regardless of the roadmap.

Acceptance: a deliberately slow mock decision returns within its deadline, and the trace records that the watchdog fired and that the result was unfinished.

## T0.8 Evaluation harness with clock compliance

Seeded, paired matchups against a frozen opponent pool. Report win rate with confidence intervals, and in the same table report the per turn latency distribution, the fraction of turns exceeding 45 seconds, and whether cumulative usage would exhaust the 7 minute player clock.

Same seeds and same teams across arms, so comparisons are paired rather than independent.

Acceptance: running random against a greedy damage maximizer produces a single table containing win rate, confidence interval, and the three clock metrics.

## T0.9 Simulator server and local benchmark

Write `js/sim_server.js` exposing create, step, serialize, and deserialize over JSON-RPC on stdio. Write `scripts/bench.py` to reproduce the throughput numbers locally.

Reference figures from the cloud container, single core: 303 turns per second, 26.8 full battles per second, 2,386 serializations per second, 1,026 deserializations per second, so roughly 4.7 ms per clone plus step. Local numbers will differ and the local ones are the ones that matter, since they are what the M8 engine decision is made against.

Acceptance: the benchmark runs and its output is recorded in the repo, with the local figures compared against the reference.

## T0.10 Differential harness skeleton

Generate random legal positions, run them through the simulator twice under a fixed seed, and assert identical outcomes. The harness has no second implementation to compare against until M1, so at M0 it is validating its own determinism and building the position generator.

Acceptance: 1,000 random positions are self-consistent under a fixed seed, and the harness exposes a clean interface for plugging in a second implementation.

## Dependency order

```
T0.1 ─→ T0.2 ─→ T0.3 ─→ (M1 stat and damage layer)
        T0.2 ─→ T0.5 ─→ T0.6 ─→ T0.8
        T0.2 ─→ T0.9 ─→ T0.10
T0.4 ───────────────────→ emitted by T0.6 onward
T0.7 ───────────────────→ wraps the decision path used by T0.6
```

T0.1 through T0.3 are strictly sequential. T0.4 and T0.7 are independent and can be done at any point before T0.6. T0.9 and T0.10 are independent of the play path.

## Notes for whoever picks this up

Do not write a damage calculator at M0. That is M1, and it depends on the delta from T0.3 being complete and reviewed.

Do not skip T0.4 because nothing needs it yet. Every later component emits to it, and adding emission afterwards means touching all of them.

Verify the stat formula empirically in T0.3 rather than trusting the transcription in `docs/02-mechanics-deltas.md`. Build a team with known stat point allocations, start a battle, and compare the simulator's reported stats against `base + points + 75` for HP and `(base + points + 20) * nature` for the rest.

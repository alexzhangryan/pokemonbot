# Status

Mutable. Current state only. History belongs in `DECISIONS.md`.

Whoever finishes a work session updates this file before stopping. Whoever starts one reads it first.

Last updated: 2026-08-28, by Claude Code.

## Current milestone

M0 complete. T0.1 through T0.10 all done, every acceptance criterion met. See `docs/09-m0-tasks.md`.

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

## In flight

Nothing.

## Blocked

Nothing.

## Next action

M1: the Champions stat and damage layer, validated cell by cell against the simulator. `docs/dex-delta.md` is the engineering checklist and T0.10's harness is the validation seam. Start by verifying the stat formula empirically rather than trusting the transcription — build a team with known stat point allocations, start a battle, and compare the simulator's reported stats against `base + points + 75` for HP and `(base + points + 20) * nature` for the rest. `docs/09-m0-tasks.md` flags this as the thing to check first.

## Open questions

Questions raised by implementation that the design has not answered. Cowork picks these up, revises the relevant document, and clears the entry.

- Whether the coach should ingest games from Champions itself, given the target game produces no replay file. Deferred until M9.
- What the eventual path to Champions looks like, and how much of the decision layer stays portable when the transport changes.
- poke-env's battle state is built from mainline Gen 9 data, and Champions differs in 303 moves, 256 items, and 8 abilities. M0 works around this where it matters (`champions/dex/loader.py` reads the resolved dump instead), but anything reading `move.base_power`, PP, or item behaviour off a poke-env object is reading mainline numbers. `docs/08-implementation-blueprint.md` says to wrap poke-env rather than depend on it deeply; M1 should decide how far that wrapper goes — whether the protocol layer builds its own state objects or keeps patching poke-env's.
- `docs/02-mechanics-deltas.md` says "roughly 347 species"; the measured legal pool is 355 (264 excluding battle-only formes, 74 of which are Mega formes). Worth reconciling so the number in the doc is the one the code agrees with, and so it is clear which of the three counts the doc meant.
- The local simulator is faster than the reference container: 2.13 ms per clone plus step against 4.7 ms. The budget arithmetic in `docs/02-mechanics-deltas.md` section 7, and the depth-2 feasibility conclusion drawn from it, were computed at 4.7 ms. Worth redoing at the local figure before M8 treats that conclusion as settled.

## Notes for the next session

The repository was moved off OneDrive during T0.1: it now lives at `C:\dev\pokemonbot`, not `C:\Users\bingk\OneDrive\Desktop\pokemonbot`. Git history is intact (local clone, then `origin` repointed to `https://github.com/alexzhangryan/pokemonbot.git`). The old OneDrive copy may still be sitting on disk pending manual deletion by Alex — if so, it is stale and should be ignored, not worked from.

Claude Code never runs `git push` in this repository — Alex pushes himself. Local commits can get ahead of `origin/main`; check `git log` vs `git log origin/main` rather than assuming they match.

`data/dex/*.json` (the dex dumps) are gitignored and regenerated locally via `python scripts/build_dex.py gen9championsvgc2026regmb --delta`; only `docs/dex-delta.md` and `vendor/SHOWDOWN_COMMIT` are committed.

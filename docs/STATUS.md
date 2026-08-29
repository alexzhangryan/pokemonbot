# Status

Mutable. Current state only. History belongs in `DECISIONS.md`.

Whoever finishes a work session updates this file before stopping. Whoever starts one reads it first.

Last updated: 2026-08-28, by Claude Code.

## Current milestone

M0. In progress: T0.1 through T0.5 done. See `docs/09-m0-tasks.md`.

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

## In flight

Nothing.

## Blocked

Nothing.

## Next action

T0.6 (random agent completing games, via poke-env) is next by task number and depends on T0.5, now done. T0.7 (deadline watchdog) is independent and can be done anytime before it wraps T0.6's decision path.

## Open questions

Questions raised by implementation that the design has not answered. Cowork picks these up, revises the relevant document, and clears the entry.

- Whether Showdown's current policy permits bot accounts on the public ladder. Needs checking before any laddering, not before M0.
- Whether the coach should ingest games from Champions itself, given the target game produces no replay file. Deferred until M9.
- What the eventual path to Champions looks like, and how much of the decision layer stays portable when the transport changes.

## Notes for the next session

The repository was moved off OneDrive during T0.1: it now lives at `C:\dev\pokemonbot`, not `C:\Users\bingk\OneDrive\Desktop\pokemonbot`. Git history is intact (local clone, then `origin` repointed to `https://github.com/alexzhangryan/pokemonbot.git`). The old OneDrive copy may still be sitting on disk pending manual deletion by Alex — if so, it is stale and should be ignored, not worked from.

Claude Code never runs `git push` in this repository — Alex pushes himself. Local commits can get ahead of `origin/main`; check `git log` vs `git log origin/main` rather than assuming they match.

`data/dex/*.json` (the dex dumps) are gitignored and regenerated locally via `python scripts/build_dex.py gen9championsvgc2026regmb --delta`; only `docs/dex-delta.md` and `vendor/SHOWDOWN_COMMIT` are committed.

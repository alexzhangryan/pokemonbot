# Status

Mutable. Current state only. History belongs in `DECISIONS.md`.

Whoever finishes a work session updates this file before stopping. Whoever starts one reads it first.

Last updated: 2026-08-28, by Claude Code.

## Current milestone

M0. In progress: T0.1 through T0.3 done. See `docs/09-m0-tasks.md`.

## Done

- Design complete. Ten documents in `docs/`, plus the M0 task list.
- Champions mechanics verified against the Showdown source, not against articles. Stat formula, status rebalances, Mega Evolution, disabled Terastallization, PP cap.
- Engine performance benchmarked in a cloud container: 303 turns per second, 4.7 ms per clone plus step. Needs reproducing on the development machine at T0.9.
- Action space measured on a live request: about 156 joint actions per side with the Mega flag available.
- Repository scaffolded with README, `pyproject.toml`, `.gitignore`, `CLAUDE.md`, and `docs/`.
- T0.1: git initialized, package skeleton created under `champions/`, `scripts/`, `js/`, `tests/` per the implementation blueprint. Venv with dev extras installed. `ruff check .` and `pytest` (one smoke test) both pass.
- T0.2: `smogon/pokemon-showdown` vendored at commit `bb179fbf8449e3c31632bd56f671ffb4404fa6e7` (recorded in `vendor/SHOWDOWN_COMMIT`), built. Confirmed both `champions` and `championsregma` mods and both Reg M-B format IDs exist in this checkout.
- T0.3: `js/dump_dex.js` (format dump + a `--mod` raw-comparison mode) and `scripts/build_dex.py` (hash + write `data/dex/`, `--delta` for the mainline diff) written and working. For `gen9championsvgc2026regmb`: 355 legal species, 148 non-nonstandard items — matches `docs/02-mechanics-deltas.md`'s figures (its "347" was an approximation; 148 items and 75 legal Mega Stones match exactly). `docs/dex-delta.md` committed: 303 modified moves, 256 modified items, 8 modified abilities vs mainline gen9, 0 added/removed in any category. The ability diff independently confirms the six newly legalized abilities and the Healer/Unseen Fist text changes already documented by hand. Both the dex dump hash and the delta doc reproduce byte-for-byte on a second run against the same vendor build.

## In flight

Nothing. Stopped after T0.3 at the user's request, to review the delta before continuing.

## Blocked

Nothing.

## Next action

T0.4 (trace schema and writer) or T0.5 (local Showdown server) — both independent of T0.3 and of each other per the dependency order in `docs/09-m0-tasks.md`. T0.4 is next by task number and nothing else needs it yet, but it blocks less the earlier it's done.

## Open questions

Questions raised by implementation that the design has not answered. Cowork picks these up, revises the relevant document, and clears the entry.

- Whether Showdown's current policy permits bot accounts on the public ladder. Needs checking before any laddering, not before M0.
- Whether the coach should ingest games from Champions itself, given the target game produces no replay file. Deferred until M9.
- What the eventual path to Champions looks like, and how much of the decision layer stays portable when the transport changes.

## Notes for the next session

The repository was moved off OneDrive during T0.1: it now lives at `C:\dev\pokemonbot`, not `C:\Users\bingk\OneDrive\Desktop\pokemonbot`. Git history is intact (local clone, then `origin` repointed to `https://github.com/alexzhangryan/pokemonbot.git`). The old OneDrive copy may still be sitting on disk pending manual deletion by Alex — if so, it is stale and should be ignored, not worked from.

Claude Code never runs `git push` in this repository — Alex pushes himself. Local commits can get ahead of `origin/main`; check `git log` vs `git log origin/main` rather than assuming they match.

`data/dex/*.json` (the dex dumps) are gitignored and regenerated locally via `python scripts/build_dex.py gen9championsvgc2026regmb --delta`; only `docs/dex-delta.md` and `vendor/SHOWDOWN_COMMIT` are committed.

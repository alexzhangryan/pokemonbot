# Status

Mutable. Current state only. History belongs in `DECISIONS.md`.

Whoever finishes a work session updates this file before stopping. Whoever starts one reads it first.

Last updated: 2026-08-29, by Cowork.

## Current milestone

M0. Not started. See `docs/09-m0-tasks.md`.

## Done

- Design complete. Eight documents in `docs/`, plus the M0 task list.
- Champions mechanics verified against the Showdown source, not against articles. Stat formula, status rebalances, Mega Evolution, disabled Terastallization, PP cap.
- Engine performance benchmarked in a cloud container: 303 turns per second, 4.7 ms per clone plus step. Needs reproducing on the development machine at T0.9.
- Action space measured on a live request: about 156 joint actions per side with the Mega flag available.
- Repository scaffolded with README, `pyproject.toml`, `.gitignore`, `CLAUDE.md`, and `docs/`.

## In flight

Nothing.

## Blocked

Nothing.

## Next action

T0.1. Initialize git, create the virtual environment, install dependencies, create the package skeleton.

## Open questions

Questions raised by implementation that the design has not answered. Cowork picks these up, revises the relevant document, and clears the entry.

- Whether Showdown's current policy permits bot accounts on the public ladder. Needs checking before any laddering, not before M0.
- Whether the coach should ingest games from Champions itself, given the target game produces no replay file. Deferred until M9.
- What the eventual path to Champions looks like, and how much of the decision layer stays portable when the transport changes.

## Notes for the next session

The repository was scaffolded from a Cowork session and has not been committed to git yet. T0.1 covers that.

If the repository still lives under OneDrive, move it. `vendor/showdown` plus `node_modules` is tens of thousands of files and OneDrive will churn on them and can lock files mid-build.

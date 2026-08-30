# CLAUDE.md

Project context for Claude Code. Read `docs/` for the full design. This file is the short version plus the things that are easy to get wrong.

## What this is

An agent and coach for Pokemon Champions doubles, Regulation Set M-B.

The target is the game Pokemon Champions. Pokemon Showdown is a proxy for execution and evaluation only. Where the two differ, follow Champions.

Current milestone: M0. See `docs/09-m0-tasks.md` for the task list and acceptance criteria.

## Non-obvious constraints

These are the ones that silently corrupt everything downstream if missed.

1. Champions is not mechanically Generation 9. The stat formula at level 50 is linear: `HP = base + points + 75` and `stat = (base + points + 20) * nature`. Roughly 250 moves and 250 items carry overrides in the `champions` mod. Terastallization is disabled, Mega Evolution is back, PP is capped at 20 base and always maxed, and paralysis, sleep, and freeze are rebalanced. Never use `@smogon/calc` or any mainline damage formula. See `docs/02-mechanics-deltas.md`.

2. Always decline Open Team Sheets. Showdown's Reg M-B prompts for it at team preview. Champions has no such mechanism, so accepting produces an agent that does not transfer. The prompt must be handled explicitly or the agent stalls at preview.

3. Reg M-A and Reg M-B use different Showdown mods (`championsregma` and `champions`). Key everything by format ID, never by a global constant. Regulation M-B expires 2026-09-09, so nothing hardcodes the legal pool.

4. Pin the Showdown commit hash. The mod is under active development and an unpinned dependency turns a mechanics change into an unexplained regression.

5. Opponent HP arrives quantized to percent. Damage-based inference carries about plus or minus 0.5 percent of max HP of error. Treat derived bounds as soft or the belief filter will eliminate the true hypothesis.

6. Every component emits to the decision trace. The schema is defined at M0 and is how the agent is debugged, since a stochastic agent sampling a mixed strategy over a sampled belief cannot be debugged from its output.

7. The search is anytime from the start. Showdown's `VGC Timer` rule auto-loses inactive players, so a live game is clock enforced regardless of whether we are optimizing for it yet. Return the best action found so far when the deadline arrives.

## Two surfaces work on this project

This project is worked on from Claude Code (implementation) and from a Cowork session (design, research, analysis). They share no memory and no context. The repository filesystem is the only channel between them, so the protocol below is what keeps them coherent.

Ownership, to avoid collisions:

- Claude Code owns everything under `champions/`, `js/`, `scripts/`, and `tests/`. Cowork does not write code.
- Either surface may write `docs/`. Cowork writes there most often, since design is its job.
- `docs/STATUS.md` is written by whoever finishes a session.
- `docs/DECISIONS.md` is append only, written by whoever makes a decision.

Protocol:

1. Start of session, read `docs/STATUS.md` first. It states the current milestone, what is done, what is in flight, what is blocked, and the next action.
2. End of session, update `docs/STATUS.md` before stopping. This is not optional. It is the only thing the other surface will see.
3. Any decision that changes the design, contradicts a document, or would surprise someone later gets an entry appended to `docs/DECISIONS.md`. Never edit or delete an existing entry. Reversals are appended with a reference to what they supersede.
4. When implementation contradicts the design, do not silently work around it. Add an entry under Open Questions in `STATUS.md` describing what the design assumed and what turned out to be true. Cowork revises the design document and clears the entry.
5. `docs/` in this repository is canonical. A mirror exists in a claude.ai Project for Cowork's benefit, but it is a mirror. On any conflict, the repository wins.

Channels between the two:

- Cowork reads this repository from GitHub (`https://github.com/alexzhangryan/pokemonbot`, public), so it sees whatever has been pushed, regardless of whether the development machine is online. Push at the end of every session or the other surface is working from stale state.
- Cowork writes through a device bridge and cannot run git, so its changes arrive as uncommitted files in the working tree, usually under `docs/`. Review and commit them like any other diff.

The repository is public. Never commit credentials or API keys. Showdown login details and any model provider key belong in `.env`, which is gitignored. See `docs/10-workflow.md`.

## Conventions

- Python 3.12, type hints required, `ruff` for lint and format, `pytest` for tests.
- Node is used only for the Showdown simulator: the dex dumper and the JSON-RPC sim server. No application logic in JavaScript.
- Deterministic by default. Seed everything. Any test or evaluation that cannot be reproduced from a seed is a bug.
- Common random numbers across compared alternatives. Payoff cells are compared as differences, so shared seeds remove most of the variance for free.
- No claims without measurement. Win rates come with confidence intervals, and clock compliance is reported beside win rate in the same table.

## Layout

```
docs/            design documents, read 01 through 09 in order
vendor/showdown/ pinned Showdown checkout, built, gitignored
js/              dex dumper and sim server
champions/       the Python package
scripts/         entry points
tests/
```

## Where to look

| Question | Document |
| --- | --- |
| What are we building and why | `docs/01-plan.md` |
| What does Champions actually do mechanically | `docs/02-mechanics-deltas.md` |
| How do we model the opponent | `docs/03-belief-filter.md` |
| How does the agent decide | `docs/04-decision-engine.md` |
| Where does data come from | `docs/05-data-pipeline.md` |
| How is the coach specified, how is anything measured | `docs/06-coach-and-evaluation.md` |
| What gets logged and how is it displayed | `docs/07-observability.md` |
| Stack, interfaces, milestones | `docs/08-implementation-blueprint.md` |
| What am I doing right now | `docs/09-m0-tasks.md` |
| Where did we leave off | `docs/STATUS.md` |
| Why is it built this way | `docs/DECISIONS.md` |
| How do we work day to day | `docs/10-workflow.md` |
| How do I set it up and run it | `docs/QUICKSTART.md` |
| What actually differs from mainline | `docs/dex-delta.md` |
| How fast is the simulator here | `docs/benchmarks.md` |
| Is the evaluation function actually calibrated | `docs/eval-calibration.md` |
| What does candidate pruning throw away | `docs/pruning-guard.md` |
| Did the learned candidate prior learn anything | `docs/policy-prior.md` |

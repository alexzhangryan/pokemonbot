# Champions Bot

An agent and coach for Pokemon Champions doubles, Regulation Set M-B.

## What this is

Two products from one core.

1. An agent that plays Champions doubles at a high level.
2. A coach that reviews games afterwards and explains what went right and wrong, in the spirit of chess.com's game review, plus a live view of what the agent is thinking while it plays.

## Target and proxy

The target is Pokemon Champions. Direct integration with the game is not currently feasible, so Pokemon Showdown serves as the execution and evaluation environment. Showdown is a proxy, not the destination, and wherever the two differ the design follows Champions.

The most important consequence: in Champions, team preview reveals the opponent's six Pokemon and nothing else. Showdown's Reg M-B format offers an optional Open Team Sheets rule that would reveal items, abilities, and moves. The agent always declines it, because playing with information the real game never provides would train and evaluate the wrong agent.

Showdown format IDs: `gen9championsvgc2026regmb`, `gen9championsvgc2026regmbbo3`.

## Status

Design complete. Implementation begins at M0.

## Documentation

Read in order.

| Document | Contents |
| --- | --- |
| `docs/01-plan.md` | Goals, decisions, and the milestone list. Start here. |
| `docs/02-mechanics-deltas.md` | Verified Champions mechanics read from the Showdown source, plus engine benchmarks. The factual foundation. |
| `docs/03-belief-filter.md` | Opponent set and stat spread inference. |
| `docs/04-decision-engine.md` | Action space, equilibrium solve, candidate policies, evaluation function. |
| `docs/05-data-pipeline.md` | Dex extraction, replay corpus, tournament lists, storage. |
| `docs/06-coach-and-evaluation.md` | Coach specification and evaluation methodology. |
| `docs/07-observability.md` | Decision trace schema, live view, and the game review client. |
| `docs/08-implementation-blueprint.md` | Stack, repo layout, interfaces, dependency order, first week. |

## Quick facts

Regulation M-B runs 2026-06-17 to 2026-09-09. Doubles, bring 6 pick 4, level 50 with 31 IVs, 66 stat points capped at 32 per stat, no Terastallization, one Mega Stone per team as a held item. Timers are 45 seconds per turn and 7 minutes of total player clock, deferred until after the MVP.

Champions is not mechanically identical to Generation 9. The stat formula is linear, status conditions are rebalanced, Mega Evolution is back, and roughly 250 moves and 250 items carry overrides. Public damage calculators are wrong for this format. See `docs/02-mechanics-deltas.md`.

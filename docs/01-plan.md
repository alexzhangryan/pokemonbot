# Plan

Top level document. Finalized 2026-08-29. Owner: Alex Ryan.

## 1. Target and proxy

The target is Pokemon Champions, Regulation Set M-B doubles. Direct integration with the game is not currently feasible, so Pokemon Showdown is the execution and evaluation environment. Showdown is a proxy, not the destination. Where the two differ, the design follows Champions.

Format IDs on the proxy: `gen9championsvgc2026regmb` and `gen9championsvgc2026regmbbo3`.

Rules that drive design: doubles, bring 6 pick 4, no Terastallization, one Mega Stone as a held item, level 50 with 31 IVs, 66 stat points capped at 32 per stat, timers of 45 seconds per turn and 7 minutes total. Regulation M-B runs 2026-06-17 to 2026-09-09, so nothing hardcodes the legal pool.

### Information regime, settled

In Champions, team preview reveals the opponent's six Pokemon and nothing more. There is no mechanism to see items, abilities, moves, or spreads. This is a permanent property of the target, not a variable.

Showdown's Reg M-B format offers an optional Open Team Sheets rule, and its Bo3 variant forces it. The agent always declines when offered and never plays the forced variant competitively, because an agent trained or evaluated with information the real game does not provide is the wrong agent.

Forced-sheet Bo3 replays remain valuable for a different reason. They are labeled training data, mapping six species to six complete sets, which is exactly what the set prior needs and what no other public source provides. Consuming them as data is unrelated to playing under those rules. See `05-data-pipeline.md`.

## 2. Goals

Three deliverables from one core.

1. A strong agent, measured by win rate against a frozen opponent pool and by ladder performance on the proxy.
2. A research artifact. Doubles is underexplored relative to singles, and this is a clean partially observable simultaneous move game with a well specified belief space.
3. A coach and observability layer. Live insight into what the agent is computing while it plays, and a post game review in the spirit of chess.com, reporting two losses per turn rather than one so that a bad decision is distinguishable from a good decision that got read or got unlucky.

The coach is not a separate system. It is the same core run offline with a larger budget.

## 3. Decisions

- Algorithmic core. The exact engine computes damage, speed, and knockout facts, and a pluggable policy layer selects among candidates. See below on where an LLM fits.
- Team building is out of scope. Fixed human teams. The bot handles bring-4, leads, and in battle play.
- Custom engine deferred behind a measured decision gate.
- The clock is tracked from M0 and optimized at M11. Optimizing for 45 seconds before the agent plays well is premature, but the constraint is real and the gap must stay visible the whole way. Concretely: per phase timing in every trace, clock compliance reported as a first class harness metric alongside win rate, and a hard watchdog for live play. See below.
- Clock compliance is a reported number, not a note. Every evaluation run reports the distribution of per turn decision latency, the fraction of turns that would exceed 45 seconds, and whether cumulative usage would exhaust the 7 minute player clock. A run that wins more often while blowing the budget is not an improvement, and that has to be visible at the moment it happens rather than at M11.
- The watchdog is not optimization. Showdown's Reg M-B carries the `VGC Timer` rule and inactive players lose automatically, so any live game is clock enforced regardless of our plans. From the first live game the agent must return its current best action when the budget is nearly spent, even if the search is unfinished. Losing on time is not a slow agent, it is a forfeit.
- Repository lives at `pokemonbot` on the development machine, to become a git repository.

### Where an LLM fits

The earlier objection to an LLM in the decision loop rested on two claims. One has weakened and one has not.

Latency has genuinely improved since PokeLLMon (February 2024) and PokeChamp (March 2025), and with the turn clock deferred for the MVP it is not a blocking constraint at all. That objection is retired for now and becomes an empirical question later.

Arithmetic reliability has not improved enough to trust a language model with damage rolls, speed tiers, and knockout thresholds, which is most of what a VGC decision reduces to. But that objection is architectural rather than fundamental: the fix is to never ask the model to compute. The exact engine produces the numbers, and the model selects among candidates that already carry their computed consequences.

So the design treats the policy layer as a pluggable interface with three implementations to be benchmarked identically: hand written heuristics, a learned prior fit on replays, and an LLM over engine-annotated candidates. No commitment is made in advance about which wins. See `04-decision-engine.md`.

## 4. What measurement changed

Reading the Showdown source and benchmarking the simulator overturned three assumptions.

Champions is not mechanically Generation 9. The stat formula is linear rather than the mainline quadratic, Terastallization is disabled in code, Mega Evolution is back and does not revert on fainting, PP is capped and always maxed, and paralysis, sleep, and freeze are all rebalanced. Roughly 250 moves and 250 items carry overrides. Every public damage calculator is wrong for this format.

The action space is smaller than estimated. About 156 joint actions per side with the Mega flag available, not 200 to 250, because picking 4 leaves only two bench Pokemon.

The compute budget is a number rather than a guess. The stock simulator does 303 turns per second and a state clone costs 1.4 ms, so a turn under the real clock affords roughly 8,000 simulator steps single core. With the clock deferred the MVP is not bound by this, but the number defines what the shipped product will need and it makes the engine decision a measurement rather than a hunch.

## 5. Core algorithmic commitments

Solve the matrix game, do not argmax. Simultaneous moves mean the root decision is a mixed strategy Nash equilibrium computed by linear program. Argmax is exploitable in exactly the Protect and Fake Out interactions that decide doubles games, and the equilibrium value is what makes the coach's ex-ante loss well defined.

Factor the belief. The categorical part (item, ability, moves) is a particle set over coherent whole teams. The continuous part (stat points and nature) is maintained as exact intervals by closed form propagation, because the spread space is $10^7$ per Pokemon and the derived stats are affine in the points.

Solve team preview exactly. Bring-4 is a $15 \times 15$ matrix game, small enough for an exact equilibrium given a value function. Given how much of VGC win rate lives in team selection, this is likely the best effort-to-win-rate ratio in the project.

Instrument from turn one. Every decision emits a structured trace. The live view, the post game review, and the evaluation harness are three consumers of one schema. The user interface is late work, but the emission contract is not.

## 6. Milestones

- M0. Pinned Showdown build, dex dump, local server, agent connection, random agent completing games, ELO harness with a frozen opponent pool, differential test harness, and the decision trace schema.
- M1. Champions stat and damage layer with roll distributions, validated against the simulator.
- M2. One ply agent: enumerate from the request, prune to a candidate set, estimate payoffs with common random numbers and roll bucketing, solve the matrix game by linear program.
- M3. Replay scraper and corpus, both Bo1 and Bo3.
- M4. Bring-4 and lead predictors, then the exact preview equilibrium.
- M5. Belief filter: spread intervals first, then categorical particles from the learned prior.
- M6. Trained and calibrated evaluation function.
- M7. Policy layer benchmark: heuristic against learned prior against LLM, measured identically.
- M8. Engine decision gate. Profile and decide whether marginal win rate comes from depth or from evaluation quality. Build the Rust engine only if depth wins.
- M9. Coach: win probability curve, per turn ex-ante and ex-post loss, critical turns, move classification, bring-4 verdict, natural language writeup.
- M10. Observability client: live view and game review interface. See `07-observability.md`.
- M11. Clock compliance. Adaptive time allocation, latency profiling, and whatever the M7 result implies about running the policy layer inside 45 seconds.

## 7. Open questions

- Showdown's current policy on bot accounts on the public ladder, to be checked before laddering.
- Whether the regulation rollover after 2026-09-09 lands mid project. Assume it does and keep the whole pipeline regeneration friendly.
- Whether the coach should accept manually entered Champions games in addition to Showdown replay IDs. Relevant because the target is Champions, where no replay file exists to feed it.
- What the eventual path to Champions itself looks like, and how much of the decision layer stays portable when the transport changes.

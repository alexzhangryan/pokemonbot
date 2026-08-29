# Decision Engine Design

## 1. Action space

Measured on a live request with four Pokemon picked: 10 options for one slot, 8 for the other, about 78 joint after removing pairs where both slots switch into the same bench Pokemon, and roughly 156 while the Mega flag is still available.

Enumeration comes from the request object rather than from first principles. The request already encodes disabled moves, Choice locks, Encore, target legality, and Mega availability, and reimplementing that logic is a pure liability.

## 2. Solve the matrix game, do not argmax

Moves are simultaneous, so the root decision is the mixed strategy Nash equilibrium of the payoff matrix, obtained by linear program. For a zero sum matrix $A$ the row player's value and strategy come from

$$\max_{x \ge 0,\ \mathbf{1}^\top x = 1} \ \min_j \ (A^\top x)_j$$

which is a single small LP. At the pruned sizes below this is microseconds and is not a cost centre.

Argmax over expected value is exploitable in a way that matters here specifically. Protect, Fake Out, and redirection are all pure prediction interactions, and an opponent who learns the agent's deterministic response beats it every time. Mixing is not a refinement, it is the correct solution concept.

The equilibrium value $V(\sigma^*)$ is also what makes the coach's ex-ante loss well defined, so this component serves both deliverables.

## 3. The policy layer is pluggable

Reducing 156 joint actions to a workable candidate set is the highest leverage decision in the engine, and the right implementation is an open empirical question. So it is an interface with three implementations, benchmarked identically.

```python
class PolicyProvider(Protocol):
    def candidates(self, state, belief, k: int) -> list[JointAction]: ...
```

Implementation A, heuristics. The bootstrap, available before any corpus exists. Any move that knocks out a target on an average roll, Protect when the slot is threatened, speed control when it flips an outspeed, Fake Out on turn 1, plus the switches. Cheap, deterministic, no external dependency.

Implementation B, learned prior. A policy network trained on the replay corpus, returning top $k$ by predicted probability. Cheap at inference, no clock risk, and fit to the actual metagame rather than to pretraining data.

Implementation C, language model. The exact engine computes each candidate's consequences first, and the model selects among candidates that already carry their computed numbers. This matters: the model is never asked to compute damage, speed order, or knockout thresholds, only to reason about them once computed. That inverts the arrangement that made earlier LLM agents unreliable.

On the case for C. PokeLLMon dates to February 2024 and PokeChamp to March 2025, both before substantial improvements in latency and reasoning quality, and both asked the model to do arithmetic it is bad at. With the turn clock deferred for the MVP, latency is not a blocking constraint at all, so C deserves a fair test rather than a prior dismissal. The two questions worth answering are whether it beats B on decision quality, and what its latency distribution looks like against a 45 second budget once the clock returns at M11. Both are measurable.

Guard for all three: pruning must never drop an action that is uniquely correct. Measure this offline and without a clock, by computing the unpruned equilibrium and recording how often it places non-trivial mass on a discarded action. This number is reported per implementation and is part of the benchmark, not an afterthought.

## 4. Payoff estimation

Each cell $(a_i, b_j)$ needs an expected value over the belief particles and the damage roll distribution. Two practices matter more than the estimator itself.

Common random numbers. Evaluate every cell against the same particle set and the same roll seeds. The decision depends only on differences between cells, and pairing the randomness removes most of the variance from those differences at zero extra cost. Without this the agent makes different choices on reruns of the same position, which also destroys the coach's reproducibility and makes the trace worthless for debugging.

Roll bucketing. Do not sample the damage rolls uniformly. Group them by outcome, principally whether the target faints, since that is the discontinuity that changes the value. A handful of buckets weighted by probability mass captures nearly all of the signal at a fraction of the cost.

## 5. Evaluation function

Needed because search cannot reach terminal states. Requirements in order:

1. Calibrated as a win probability, not merely monotone. The eval bar, the ex-ante loss, and the value backup through a matrix game all depend on the scale being a probability.
2. Cheap. It sits inside the innermost loop.
3. Trained on replay outcomes rather than hand tuned, once the corpus exists.

Bootstrap version: a hand written linear combination of HP fractions, Pokemon remaining, speed control state (Tailwind and Trick Room turns left), field and weather state, and a type matchup summary. Good enough to make the one ply agent work and to validate the harness.

Trained version: logistic regression or a small network over the same features plus learned embeddings, fit to eventual game outcome on the replay corpus, then explicitly calibrated on a held out split. A reliability diagram is required before it is used anywhere, because the calibration claim is load bearing for the coach.

## 6. Team preview is separately and exactly solvable

The preview decision is a $15 \times 15$ matrix game over bring-4 subsets, so 225 cells. That is small enough to solve exactly, which is not true of any in battle node.

Each cell needs a game value estimate for a bring-4 pairing, which the trained evaluation function supplies at negligible cost. So the agent plays a genuine equilibrium at preview rather than a heuristic, and lead selection nests inside it as a $6 \times 6$ subgame.

Given that bring-4 and leads are widely regarded as a large share of VGC win rate, and that the computation is exact rather than approximate, this is likely the highest ratio of win rate to engineering effort in the entire project. Build it early.

## 7. Time allocation: tracked now, optimized later

The MVP does not optimize for the clock. It does track it, in three ways that cost almost nothing to add now and are expensive to add later.

Per phase timing. Every decision records its wall clock cost broken down by belief update, candidate generation, payoff estimation, equilibrium solve, and policy provider call. Emitted every turn from M0.

Compliance metrics in the harness. Every evaluation run reports the latency distribution, the fraction of turns that would exceed the 45 second budget, and whether cumulative usage would exhaust the 7 minute player clock. These sit next to win rate in the same table. A change that wins more often while blowing the budget has to look like the trade-off it is, at the moment it is made.

A watchdog, which is a correctness requirement rather than an optimization. Showdown's Reg M-B carries the `VGC Timer` rule and inactive players lose automatically, so every live game is clock enforced whatever the roadmap says. The search runs against a deadline and returns the best action found so far when the deadline arrives, always, from the first live game. Any anytime structure suffices: keep a current best action updated as particles and roll buckets accumulate, and return it on interrupt. Doing this from the start also makes the M11 work a matter of choosing better deadlines rather than restructuring the search.

The intended eventual rule: run a cheap first pass at low particle and roll counts. If the equilibrium is near pure and the gap between the best and second best action exceeds a threshold, commit immediately. Otherwise escalate until the gap resolves or the slice is spent, tracking cumulative usage against the 7 minute total and tightening the cap as it depletes.

That rule also produces a by-product the coach wants anyway, namely a per turn measure of how close the decision was, which is a first approximation to identifying critical turns.

## 8. Search depth

One ply for the MVP. Under the real clock the measurements say depth 2 needs roughly 100 seconds per turn on the stock simulator with pruning already applied.

The gate: profile the agent once the belief filter and trained evaluation are in, and determine whether marginal win rate comes from more depth or from better evaluation and more particles. If depth wins, build the Rust engine restricted to the Reg M-B pool, which is the roughly 100 times speedup that makes depth 2 fit.

Either way the differential test harness against the Showdown simulator is the prerequisite, because a custom engine that silently diverges on one of the roughly 250 modified moves is worse than no custom engine at all.

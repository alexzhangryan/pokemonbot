# Belief Filter Design

Depends on `02-mechanics-deltas.md` for the stat formula and the compute budget.

## 1. What is hidden

In Champions the observable at preview is six species with level and gender. Hidden per Pokemon: held item, ability, four moves, six stat point values, and nature. Hidden per team: which four are brought, and which two lead. This is the only regime the agent plays in.

## 2. Do not represent the spread as a distribution

The naive move is a distribution over spreads. The space forbids it. Counting integer solutions to $\sum_i p_i = 66$ with $0 \le p_i \le 32$ over six stats gives 10,008,272 spreads, and allowing $\sum_i p_i \le 66$ gives 136,663,185. Times 25 natures that is $2.5 \times 10^8$ hypotheses for a single Pokemon.

The resolution is that we never need the spread. We need the six derived stats, and those are affine in the points:

$$\text{HP} = \text{base}_{\text{hp}} + p_{\text{hp}} + 75, \qquad \text{stat}_i = (\text{base}_i + p_i + 20)\,\nu_i$$

Every observation therefore lands as a linear inequality in $p$, and the feasible region is a box intersected with one resource constraint. That structure is cheap to maintain exactly.

### Interval propagation is exact here

Maintain lower and upper bounds $[l_i, u_i]$ per stat. The only coupling is $\sum_i p_i \le 66$, and for a single resource constraint over box-bounded variables the tightened bound is exact in closed form:

$$u_i \leftarrow \min\left(u_i,\ 32,\ 66 - \sum_{j \ne i} l_j\right)$$

No linear program is needed. This is a handful of arithmetic operations per update, so the spread layer costs essentially nothing, and it is exact rather than approximate.

This is also the point where the format is more informative than mainline VGC. Under a 66 point budget with a 32 point cap, offensive and defensive investment are close to genuinely zero sum, so a single damage observation constrains the entire spread rather than one stat. The same technique applied to mainline Regulation I would yield much looser bounds.

### Natures

25 natures, of which 5 are neutral. Keep them as discrete hypotheses per Pokemon, each carrying its own interval set, with a prior weight from usage data. Most are eliminated quickly, because a nature is a 10 percent multiplier while the point granularity is 1, so a single tight speed observation often rules out most of them.

### Observations and the inequalities they produce

- Damage you deal to them bounds their defensive point allocation and their HP jointly, since the reported figure is a percentage of max HP.
- Damage they deal to you bounds their offensive point allocation, given your own stats, which you know exactly.
- Move order bounds effective Speed strictly against a known quantity, with the inequality direction flipping under Trick Room and shifting under Tailwind or an Icy Wind drop.
- Immunities, failures, and residual damage bound item and ability rather than the spread.

### Quantization

Opponent HP is reported as a percentage. Each damage observation carries roughly $\pm 0.5$ percent of maximum HP of quantization error, about $\pm 0.9$ HP on a 175 HP Pokemon, and it compounds across chained inferences. Treat bounds as soft with an explicit error term rather than as hard constraints, or the filter will eliminate the true hypothesis. This is the single most likely source of a silent correctness bug in the whole system, and the interval coverage metric in `06-coach-and-evaluation.md` exists specifically to catch it.

## 3. Represent the categorical part as particles

Item, ability, and moveset are discrete, strongly correlated, and constrained across the team. Independent marginals are wrong: sampling an item from one marginal and a moveset from another produces sets no player would register.

Representation: a weighted set of coherent whole-team hypotheses. Each particle assigns item, ability, and four moves to each of the six species, subject to Item Clause across the team.

Sampling: because Item Clause couples the six, sampling is a constrained assignment problem rather than six independent draws. A sequential draw with rejection works but degrades when the prior concentrates several Pokemon on the same item. A short Gibbs sweep over the six, resampling one Pokemon's set conditioned on the other five, is the robust option.

Prior: a learned joint over (species, ability, item, moveset), conditioned on the rest of the team. Labels come from forced-sheet Bo3 replays and tournament team lists. See `05-data-pipeline.md`.

Particle count: under the real clock the budget allows roughly 20 to 50 particles alongside pruned search and roll integration. With the clock deferred for the MVP, start higher and tune down at M11 rather than guessing now. Prefer the top $k$ most probable coherent teams over i.i.d. draws, since the value estimate is an expectation and stratification cuts variance at the same cost.

Updating: reveals are hard filters. A revealed move eliminates every particle lacking it, a revealed item eliminates every particle assigning it elsewhere, and Item Clause propagates that elimination to the other five. Resample when the effective particle count falls below a threshold, drawing from the prior restricted to the surviving constraints.

## 4. Bring-4 and lead prediction

Separate from the in battle filter and worth building first, since it is small, supervised, and independently evaluable.

Input: both six species lists. Output: a distribution over the $\binom{6}{4} = 15$ subsets, and conditioned on that, over the $\binom{4}{2} = 6$ lead pairs.

Why it is a good first learned component: the label space is tiny so it trains on a modest corpus, the usage frequency baseline gives a clean floor to beat, and it feeds three consumers at once, namely the agent's own preview decision, the belief prior at turn 1, and the coach's first useful output.

Game theoretic caveat: the agent's own bring-4 must be an equilibrium response to the predicted distribution, not a best response to its mode. Best responding to the mode is exploitable by anyone who notices. The preview stage is small enough to solve exactly, covered in `04-decision-engine.md`.

## 5. Evaluating the filter on its own

The filter must be measurable in isolation, not only through downstream win rate.

- Negative log likelihood of the true opponent set under the belief at turn $t$, plotted against $t$, on held out replays where the truth is eventually revealed. Forced-sheet Bo3 replays give the truth at turn 0, which makes them the cleanest evaluation set.
- Calibration: bucket predicted probabilities and check realized frequencies match.
- Interval coverage for the spread layer: the fraction of the time the true stat falls inside the maintained interval, which should be at or above the nominal level. Coverage below nominal means the quantization error term is too small.
- Baseline to beat: usage frequency marginals with no in battle updating.

Every one of these quantities is part of the decision trace, so the live view and the review client display them without additional plumbing. See `07-observability.md`.

# Coach Specification and Evaluation Methodology

## Part I: The coach

### 1. Why it needs two numbers per turn

Chess analysis reports one number because chess is perfect information, sequential, and deterministic. None of that holds here, so a single centipawn-loss analogue would conflate three different failure modes: playing badly, being read, and getting unlucky. Players care most about distinguishing exactly those.

At turn $t$, with belief state $b_t$ and equilibrium $\sigma^*$ over the candidate set:

Ex-ante loss, decision quality given what was knowable.

$$L_{\text{ante}}(a) = V(\sigma^*) - V(a, \sigma^*_{\text{opp}})$$

Ex-post loss, outcome quality given what actually happened, recomputed with the replay's full information including the opponent's realized action.

$$L_{\text{post}}(a) = \max_{a'} V(a', b_{\text{opp}}) - V(a, b_{\text{opp}})$$

The decomposition is the product. High $L_{\text{ante}}$ is an avoidable mistake. Low $L_{\text{ante}}$ with high $L_{\text{post}}$ is a correct decision that lost to a read or a roll. Reporting both is the coach's core value, and formalizing that split for a simultaneous move imperfect information game is a defensible contribution in its own right.

Roll outcomes are reported as probabilities rather than folded into an average. "This line wins 78 percent of rolls" is more useful than an expected value and it is what makes the luck component legible.

### 2. Move classification

Chess.com's labels do not transfer directly, because they assume a single best move exists. Here the equilibrium is frequently mixed, and an action carrying 30 percent weight is not a mistake.

Primary axis, from $L_{\text{ante}}$ measured in win probability:

| Label | Condition |
| --- | --- |
| Best | On the equilibrium support and carrying the largest weight |
| Solid | On the support with non-trivial weight |
| Inaccuracy | Off support, small loss |
| Mistake | Off support, moderate loss |
| Blunder | Off support, large loss |

Thresholds are calibrated empirically against rating bands rather than picked by hand, since a win probability loss means something different in a close game than in a decided one.

Orthogonal tags, which is where this differs from chess and where most of the coaching value lives:

- Unlucky. Low $L_{\text{ante}}$, high realized loss attributable to rolls, accuracy, or secondary effects.
- Read. Low $L_{\text{ante}}$, and the opponent selected the specific counter to the chosen action.
- Gamble. The action carried low equilibrium weight and paid off anyway. Worth flagging so the player does not learn the wrong lesson from a win.
- Forced. The equilibrium was effectively pure and any other action lost badly. Not praise, just context.

The tags are the point. A player who sees "Solid, but Read" learns something a single number cannot express.

### 3. Offline is a different regime

The coach has no clock. That permits far more belief particles, full roll enumeration rather than bucketing, deeper search if the engine allows, and no risk from a language model call on the critical path.

This is why the language model belongs in the coach unconditionally, whatever the M7 benchmark says about live play. Explaining why a move was wrong, grounded in engine-computed numbers, is what these models are actually good at, and there is nothing to time out.

### 4. Outputs

- A win probability curve across the game, one point per turn, from the calibrated evaluation function.
- Per turn $L_{\text{ante}}$ and $L_{\text{post}}$, plus classification and tags.
- Critical turn identification. First approximation is the largest single turn drop in win probability. Better approximation is the largest $L_{\text{ante}}$, which isolates avoidable losses.
- The equilibrium mixed strategy at each analyzed turn, which reads naturally as "70 percent Fake Out, 30 percent Protect" and teaches that some positions have no single right answer.
- A bring-4 verdict at preview, supplied directly by the exact $15 \times 15$ solve.
- A natural language writeup per critical turn, generated from the numbers above.

Input is a Showdown replay ID or URL. Whether to also accept manually entered Champions games is open, and matters because the target game produces no replay file.

### 5. Honest limitations

The ex-ante loss is only as good as the belief model, and the belief model is fitted to a metagame. A coach that flags a novel but correct play as a mistake because the prior did not expect it is failing in a way a chess engine does not. Report belief entropy alongside the loss so the reader can see when the analysis is operating outside its support.

## Part II: Evaluation methodology

Fixed now, because retrofitting rigor is how portfolio projects lose credibility.

### 6. Agent evaluation

Opponent pool, frozen and versioned at each milestone: random legal, a greedy damage maximizer, a heuristic doubles bot, the previous milestone's agent, and any identifiable public bot on the ladder. Other bots are already active in this format, which gives external reference points that cost nothing to use.

Statistics. Report win rate with confidence intervals, not raw counts. Seed the simulator and fix teams across arms so comparisons are paired. A 55 percent win rate over 100 games is not evidence of anything, and the number of games needed should be computed in advance from the effect size worth detecting.

Ablations, one component at a time against the same frozen pool: belief tracking on and off, equilibrium solve versus argmax, policy provider (heuristic, learned, language model), particle count swept, search depth swept, evaluation function hand written versus trained.

External check: ladder performance on the proxy, reported as GXE rather than as anecdotes about peak rating.

### 7. Component evaluation, independent of win rate

Each component must be measurable without playing a game, or debugging becomes guesswork.

- Belief filter: negative log likelihood of the true opponent set against turn number, on held out forced-sheet replays. Baseline is usage marginals with no updating.
- Spread intervals: coverage, the fraction of the time the true stat lies inside the maintained interval. Below nominal coverage means the quantization error term is too small.
- Bring-4 predictor: top-1 and top-3 accuracy over the 15 subsets, against the usage frequency baseline.
- Evaluation function: reliability diagram and Brier score on held out games.
- Policy provider: how often the unpruned equilibrium places non-trivial mass on a discarded action, plus latency distribution.
- Engine, if built: differential agreement rate against the Showdown simulator over randomly generated positions under a fixed seed. This one has to be effectively perfect, not merely high.

### 8. Coach evaluation

Harder, and separate. Three angles in increasing strength:

1. Calibration of the win probability curve. Necessary, not sufficient.
2. Agreement with strong players. Check that flagged critical turns overlap with turns strong players identify themselves, in public analysis or solicited annotation.
3. Predictive validity. If $L_{\text{ante}}$ measures something real, it should correlate with player rating across a large replay sample. Strong players should accumulate less ex-ante loss per game than weak players, while ex-post loss should separate them much less, since luck is not skill. Confirming that dissociation would be a genuinely interesting empirical result and is the strongest available evidence that the metric means what it claims.

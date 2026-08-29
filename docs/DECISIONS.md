# Decision Log

Append only. Newest at the bottom. Never edit or delete an entry. If a decision is reversed, append the reversal with its reasoning and reference the entry it supersedes.

Each entry records what was decided, why, and which surface decided it. The surface matters because it tells the next reader where the supporting context lives.

Format:

```
## D<n>. <decision> — <date>, <surface>
Context: what prompted it.
Decision: what was chosen.
Rationale: why, including what was rejected.
Consequences: what this forces or forecloses.
```

---

## D1. Target Champions, use Showdown as a proxy — 2026-08-28, Cowork

Context: direct integration with Pokemon Champions is not currently feasible.

Decision: build for Champions, execute and evaluate on Pokemon Showdown. Where the two differ, follow Champions.

Rationale: Showdown is the only environment where the agent can play and be measured at volume, but optimizing for Showdown-specific affordances produces an agent that does not transfer to the real target.

Consequences: every Showdown-only feature has to be assessed for fidelity before use. The decision layer must stay portable across transports.

## D2. Always decline Open Team Sheets — 2026-08-29, Cowork

Context: Showdown's Reg M-B carries an opt-in `Open Team Sheets` rule, and its Bo3 variant forces it. Champions has no such mechanism, ever.

Decision: the agent declines the prompt in every game and does not compete in the forced variant. Forced-sheet Bo3 replays are still consumed as training data.

Rationale: training or evaluating with information the target game never provides yields the wrong agent. Consuming those replays as labels is a separate question from playing under those rules, and they are the only public source of complete labeled sets.

Consequences: the client must handle the preview prompt explicitly, or the agent stalls at team preview. Full set inference is required, so the belief filter is a major subsystem rather than an afterthought.

## D3. Algorithmic core with a pluggable policy layer — 2026-08-29, Cowork

Context: whether to build an LLM-driven agent or a search-driven one.

Decision: the exact engine computes damage, speed, and knockout facts. Candidate selection sits behind a `PolicyProvider` interface with three implementations to be benchmarked identically at M7: heuristics, a learned prior, and a language model over engine-annotated candidates.

Rationale: language models remain unreliable at the arithmetic that VGC decisions reduce to, but that objection is architectural rather than fundamental, and the fix is to never ask the model to compute. The latency objection that applied to PokeLLMon and PokeChamp has weakened substantially and is not a blocker while the clock is deferred. No commitment is warranted in advance about which implementation wins.

Consequences: three implementations to build and benchmark rather than one. The interface boundary must stay clean enough that swapping providers is a configuration change.

## D4. Solve the matrix game rather than taking an argmax — 2026-08-29, Cowork

Context: moves are simultaneous.

Decision: the root decision is the mixed strategy Nash equilibrium of the payoff matrix, computed by linear program.

Rationale: argmax over expected value is exploitable in exactly the Protect, Fake Out, and redirection interactions that decide doubles games. Mixing is the correct solution concept, not a refinement. The equilibrium value is also what makes the coach's ex-ante loss well defined.

Consequences: the agent is stochastic, so every decision must record its RNG seed or nothing is reproducible.

## D5. Bring-4 only, no team building — 2026-08-29, Cowork

Context: scope.

Decision: teams are supplied by a human. The agent handles bring-4, leads, and in battle play.

Rationale: team construction against a metagame is a separate optimization problem and arguably a second project.

Consequences: team quality is a confound in every evaluation, so teams must be fixed across arms.

## D6. Defer the custom engine behind a measured gate — 2026-08-29, Cowork

Context: whether to write a fast doubles engine, since `poke-engine` is singles only and no mature open doubles engine exists.

Decision: use the Showdown simulator as the oracle. Revisit at M8 by profiling whether marginal win rate comes from search depth or from evaluation quality.

Rationale: measurements say a pruned one ply agent fits the real clock while depth 2 needs roughly a 100 times faster engine. Which of depth and evaluation quality matters more is an empirical question that cannot be answered before both exist.

Consequences: the differential test harness is a prerequisite, because an engine that silently diverges on one of roughly 250 modified moves is worse than no engine.

## D7. Track the clock from M0, optimize it at M11 — 2026-08-29, Cowork

Context: the 45 second turn limit and 7 minute player clock.

Decision: per phase timing in every trace, clock compliance reported beside win rate in every evaluation, an anytime search with a deadline watchdog from the first live game, and no optimization work until M11.

Rationale: optimizing before the agent plays well is premature, but a deferred constraint that nobody measures becomes a rewrite. The watchdog is separate: Showdown's `VGC Timer` auto-loses inactive players, so losing on time is a forfeit rather than a performance problem.

Consequences: the search must be structured as anytime from the beginning, which is a design constraint rather than a later optimization.

## D8. Define the trace schema at M0, build the interface at M10 — 2026-08-29, Cowork

Context: the observability and coaching requirement.

Decision: the decision trace schema is frozen at M0 and every component emits it from the day it is written. The live view and the review client come last.

Rationale: a stochastic agent sampling a mixed strategy over a sampled belief cannot be debugged from its output, and retrofitting emission into six finished components means touching all six. It also makes the live view and the review client the same program, one reading a socket and one reading a file.

Consequences: components cannot be written without the schema existing, so T0.4 blocks more than it appears to.

## D9. Factor the belief, do not enumerate spreads — 2026-08-29, Cowork

Context: the spread space is over $10^7$ per Pokemon before natures.

Decision: categorical attributes (item, ability, moves) as a particle set over coherent whole teams. Stat points and nature as exact intervals maintained by closed form propagation.

Rationale: derived stats are affine in the points and the only coupling is a single resource constraint, so interval tightening is exact in closed form and costs nothing. A distribution over spreads is both intractable and unnecessary, since the spread itself is never needed, only the stats it implies.

Consequences: two structurally different halves of one filter, evaluated by different metrics. Interval coverage becomes a required metric, since soft bounds are the only defense against percent quantization eliminating the true hypothesis.

## D10. Trace files are per agent-view, not per battle — 2026-08-28, Claude Code

Context: `docs/07-observability.md` specifies "one file per battle". In self-play both agents run in one process and share a battle tag, so both wrote to the same file, interleaving two sides' events under two independent seq counters and producing a trace that fails its own validator.

Decision: the trace file is one agent's view of one battle. `Trace` takes a `name` override and agents use `<battle_tag>.<username>.jsonl`. `battle_id` still carries the battle tag, so the two views of a game remain correlatable.

Rationale: a trace records what one agent knew and decided, and an agent only ever sees its own side. In a live game there is one agent per battle and this is identical to the documented behaviour; the distinction only appears in self-play, which is a harness artifact.

Consequences: a 50 game self-play run produces 100 files. Any consumer aggregating over a run should not assume file count equals battle count.

## D11. The M0 greedy baseline maximizes base power, not damage — 2026-08-28, Claude Code

Context: T0.8's acceptance criterion names "a greedy damage maximizer", while the M0 notes say explicitly not to write a damage calculator, since that is M1 and depends on the T0.3 delta being reviewed.

Decision: the baseline is `MaxBasePowerAgent`, greedy on summed base power, named for what it does rather than what it approximates. A true damage-maximizing baseline arrives with the M1 damage layer.

Rationale: base power ignores types, stats, items, abilities, and spread reduction, so calling it a damage maximizer would misrepresent both the agent and any win rate measured against it. It reads base power from the resolved Champions dex rather than poke-env, whose mainline numbers are wrong for 303 moves.

Consequences: the frozen opponent pool gets a stronger member at M1 and every win rate measured against the M0 pool is against a weaker opponent than the name suggests. Comparisons across that boundary are not paired.

## D12. Differential comparison ignores wall-clock lines in the protocol log — 2026-08-28, Claude Code

Context: T0.10's first real run reported 8 of 1000 positions diverging, with identical turn, ended, and winner. Showdown emits a `|t:|<unix seconds>` line at the start of each turn, so two replays of one position that straddle a second boundary differ by wall time alone.

Decision: the log digest excludes `|t:|` lines. Everything else in the protocol stream is compared.

Rationale: left in, this is a permanent 0.5 to 0.8 percent background rate of false divergences in every future engine comparison, which is how a differential harness becomes something people ignore. The excluded lines carry no battle state.

Consequences: a divergence that consists only of timing differences is invisible to the harness. That is intended; no correctness property depends on wall time. Any future non-deterministic-but-meaningful protocol line has to be handled explicitly rather than inherited by this filter.

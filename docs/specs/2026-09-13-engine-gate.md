# M8: the engine gate

Design, 2026-09-13, Claude Code. Status: approved by Alex in session, being built.

`docs/01-plan.md` and `docs/08-implementation-blueprint.md` both define M8 the
same way: profile and decide whether marginal win rate comes from search depth
or from evaluation quality, and build the Rust engine only if depth wins.
`docs/04-decision-engine.md` section 8 names the gate and D6 defers the engine
behind it. This spec turns that sentence into arms, a rule, and an order of
work, and it fixes the rule before any number exists, which is how M7 was run
(D67) and the reason its negative results were readable.

Everything four milestones of measurement left behind points at one thing.
M5 supplied the opponent information M2 said was missing and the win rate did
not move (D48). M6 calibrated the evaluation. M7 tried two providers against
the specified A and both lost the guard (D67, D69). `docs/STATUS.md` states the
conclusion: the binding constraint is what the one-turn payoff model can do
with what it knows. M8 asks which of the two ways of doing more with it is
worth the engineering: seeing further, or seeing the same turn more faithfully.

## 1. The question, stated so a number can answer it

The agent's payoff model is `champions/search/payoff.py`: one turn resolved
analytically, then `win_prob`. Its docstring lists what it does not model —
abilities, items, secondary effects, status effects, weather, multi-hit,
recoil, healing, accuracy — and it scores a switch as giving up the turn
because the incoming Pokemon's value is a next-turn question. Those are the
two directions the gate compares:

- **Depth.** The same model, one ply deeper. The turn after a switch is
  visible, so the switch bias goes away; a knockout set up this turn is
  scored next turn. Everything the model does not represent is still absent.
- **Fidelity.** The same one turn, resolved by the simulator instead of the
  analytic model. Every effect the docstring lists is present, because the
  simulator is the game. Nothing is seen further ahead.

Each is built as an agent that differs from `OnePlyAgent` in exactly that one
respect, and each is measured against `OnePlyAgent` the way every arm since
M2 has been measured: same team, same seeds, paired, intervals, clock
compliance beside win rate in one table (`docs/06-coach-and-evaluation.md`
section 6, D30).

## 2. The budget arithmetic, redone at the local figure

`docs/02-mechanics-deltas.md` section 7 computed the search budget at 4.7 ms
per clone-plus-step and concluded that depth 2 "does not fit" and "needs an
engine roughly 100 times faster". `docs/benchmarks.md` measured 2.13 ms on
this hardware, over JSON-RPC, which is the figure any real caller pays.
`docs/STATUS.md` asked for the arithmetic to be redone before M8 treats that
conclusion as settled. Redone:

| quantity | at 4.7 ms | at 2.13 ms |
| --- | ---: | ---: |
| full matrix, one node, 156 × 156 = 24,336 cells | 114 s | 51.8 s |
| pruned node, 10 × 10 = 100 cells | 0.47 s | 0.21 s |
| pruned node × 20 particles × 5 roll buckets, 10⁴ steps | 47 s | 21.3 s |
| depth 2, pruned both plies, no particles, 10⁴ nodes | 100 s | 21.3 s |
| depth 2 with 20 particles × 5 buckets, 10⁶ steps | 78 min | 35 min |
| steps per 45 s turn, one core | 9,600 | 21,100 |
| steps per 45 s turn, eight cores, if it scales | 77,000 | 169,000 |

Two of section 7's three conclusions survive and one does not.

1. A pruned one-ply agent with belief sampling fits the real clock. Still
   true, and now on one core rather than eight.
2. "Depth 2 needs a 100 times faster engine." **Not at the local figure.**
   Depth 2 with pruning on both plies is 21 s per turn on one core, inside
   the 45 s turn limit, and about 3 s across eight cores if the simulator
   scales across processes (unmeasured; each `SimServer` is one Node process,
   so it should). What a 100 times faster engine buys is not depth 2 but
   depth 2 *with* belief particles and roll replicates, which is 35 minutes
   on the stock simulator, or depth 3.
3. The per-turn budget is about 21,000 steps on one core rather than 8,000.

One constraint section 7 does not state and this gate must: the 7 minute
player clock is a total. Spending the full 45 s every turn exhausts it after
nine turns, so the per-turn ceiling is not a per-turn budget, and a depth-2
agent at 21 s a turn forfeits a long game on the clock. M11 owns the
allocation rule; M8 reports the clock columns and does not optimise them.

Consequence for the gate: the Rust engine is not the only route to depth 2.
If depth wins, the engine question is "particles at depth 2 or depth 3", and
that is what D71 will have to justify, not "depth 2 at all".

## 3. The arms

Five agents, all playing `regmb-alpha` and separately `regmb-beta`, each
measured against `OnePlyAgent` in a mirror match on the same team.

| arm | payoff model | depth | what it knows about the opponent |
| --- | --- | --- | --- |
| `oneply` | analytic | 1 | revealed moves; constant pessimistic stats; no items or abilities. **The incumbent.** |
| `oneply-oracle` | analytic | 1 | the opponent's six registered sets |
| `twoply` | analytic | 2 | as `oneply` |
| `twoply-oracle` | analytic | 2 | as `oneply-oracle` |
| `sim-oracle` | simulator | 1 | as `oneply-oracle` |

**Why an oracle rather than the belief.** The simulator needs a complete
opponent team to step, and the belief filter is what supplies one
(`payoff.py` docstring, D48). But `data/priors/` is not built on this machine
— it needs the corpus, which lives only on the retired Windows box — and even
where it is, a belief-fed arm mixes two effects: how faithful the payoff is
and how wrong the particles are. The oracle removes the second. It is the
ceiling of what any belief could supply, so a fidelity result measured
against it is the *most* fidelity can be worth, and a negative result is
decisive: if the simulator does not help when handed the truth, no particle
will make it help.

What the oracle knows: the opponent's six registered sets, exactly (in a
mirror match, its own team file). What it does not know: which four were
brought and in what order. Revealed Pokemon are certain; the unrevealed
brought slots are drawn uniformly, seeded per decision, from the species not
yet seen. That is the same information limit `alive()` already works under.

**Why `oneply-oracle` is an arm at all.** D48 measured belief-supplied
information as neutral inside the analytic model, with intervals too wide to
say more. `oneply-oracle` re-tests that with zero belief error, and it is the
control the two headline comparisons need:

- **fidelity** = `sim-oracle` − `oneply-oracle`: the same information, the
  same depth, a different resolver.
- **depth** = `twoply-oracle` − `oneply-oracle`: the same information, the
  same resolver, one more ply. `twoply` − `oneply` is the same question
  under the agent's real information state and is reported beside it.

## 4. The rule, fixed now

- `N = 200` games per arm per team, seed fixed, both arms on the same team.
  Wilson 95% intervals, as `champions/harness/elo.py` computes them.
- An arm **demonstrates a gain** when its interval against `oneply` excludes
  0.5.
- Two arms are **apart** when the 95% interval on the difference of their win
  rates against `oneply` excludes zero (two independent proportions; the arms
  share the opponent and the seeds, so this is conservative).
- **Depth wins the gate** when `twoply-oracle` demonstrates a gain, the depth
  gap is apart from zero, and either the fidelity gap is not apart from zero
  or the depth gap exceeds the fidelity gap with the interval on their
  difference apart from zero. Then the engine is justified, starts on a
  branch (`m8/rust-engine`, `docs/10-workflow.md` section 6), and its brief
  is section 2's: depth 2 with particles, or depth 3.
- **Fidelity wins** when the fidelity gap is apart from zero and depth does
  not meet its clause. No engine. The next investment is the simulator payoff
  fed by belief particles, with the clock measured; `k` and the union
  (D67), and the belief head-to-head (D58), are re-opened against the new
  payoff, since both were deferred to it by name.
- **Both clear.** Fidelity ships first: it is cheaper and depth on a model
  that does not see items compounds nothing. Depth is re-asked on the
  simulator payoff, where section 2 says it fits on eight cores at one
  particle and one replicate. The engine decision is then "particles at depth
  2", deferred with that reason written down.
- **Neither clears, on either team.** The payoff model is not the binding
  constraint at this pairing, which contradicts `docs/STATUS.md`'s reading
  and is reportable on its own. The pre-registered secondary measurement is
  each arm against `greedy` (max-base-power), which has a known baseline
  (D30: 82% on alpha, 56% on beta) and more headroom than a mirror. It is a
  check on sensitivity, not a substitute for the primary rule.
- **Per team, not pooled.** D30's finding is that the gap between the teams
  measures what the model does not represent; alpha's items and abilities
  are inert and beta's are not, so fidelity is expected to matter least on
  alpha and most on beta. The verdict is stated per team; if the teams
  disagree, beta's verdict is the one about real play and alpha's is
  reported as the control it is.
- **The clock does not veto.** The MVP defers the clock (D7). An arm whose
  p95 exceeds 45 s or whose worst battle exhausts the 7 minute clock is
  marked in the table and named in the finding.

## 5. Components

### 5.1 `champions/search/twoply.py`: one more ply on the analytic model

`TwoPlyModel` wraps a `TurnModel` and replaces `win_prob` at the leaves with
the value of a one-ply matrix game solved on the resulting position.

For one root cell: `TurnModel.outcomes` gives the bucketed branches (at most
sixteen, usually one or two). For each branch state the child value is:

- **terminal**, when either side has nothing alive or we have no legal
  action: `win_prob` of the state.
- otherwise: enumerate our joint actions from the state, prune both sides
  with the same providers the root uses (`HeuristicPolicy.scored` at `k2`,
  `opponent_candidates` at `k2`), build the child matrix with the same
  `TurnModel`, `solve_both`, and take the equilibrium value.

The cell's value is the probability-weighted sum of child values. Children
are memoised on a canonical hash of the state, because many cells share one
(every Protect column, for a start).

Two changes to the turn resolution that only matter once there is a next
turn, both made in `TurnModel` behind a flag the root does not set:

- **A switch places the incoming Pokemon.** `_switch` benches the acting
  Pokemon and leaves the slot empty, which is the switch bias
  `docs/STATUS.md` names as the clearest thing depth would fix. At depth 2
  the slot is filled with the Pokemon the switch names (`described["species"]`)
  from the bench, so the second ply can act with it and the evaluation sees
  it. Opponent columns never contain switches (`opponent_candidates` emits
  moves only), so only our side needs this.
- **A fainted slot is refilled before the second ply.** Ours with the first
  living brought Pokemon on the bench, which is a policy assumption and is
  stated as one. Theirs is left empty: which Pokemon comes in is unobservable
  and `alive()` already counts by faints.

**Interior enumeration is first-principles and approximate.**
`docs/04-decision-engine.md` section 1 says to enumerate from the request
because reimplementing legality is a liability. An interior node has no
request, so the child enumerates from the state: each active slot's known
moves against each legal target for the move's target type, plus switches to
living brought bench Pokemon, minus same-target double switches. Choice locks,
Encore, Disable, trapping, PP and Mega availability are not represented. The
described-action shape matches `champions/protocol/actions.describe` field for
field, so `HeuristicPolicy` and `TurnModel` consume child actions unchanged.
The root still enumerates from the request; the approximation is one ply
down, where the alternative is no ply at all.

`TwoPlyAgent(OnePlyAgent)` proposes the one-ply answer first, as the anytime
structure requires, then replaces it with the two-ply draw. It awaits between
root cells so the watchdog can land. The trace records `model:
"analytic-two-ply"`, `k2`, the number of children solved and memo hits, and
the timings split by ply.

Cost: `k × k` root cells × branches × `k2 × k2` child cells. At `k = 10`,
`k2 = 6`, two branches, that is 7,200 turn-model cells, about a hundred
times the one-ply decision M2 measured at 11 ms. `k2` defaults to 6 and is a
constructor argument.

### 5.2 `js/sim_server.js`: `materialize`

A battle at a given position, rather than at turn 0. Parameters: format,
seed, both sides' full six in export format, each side's bring order (the two
leads first), and the position: turn, weather, pseudo-weather, per-side
conditions, and per Pokemon its HP (a fraction, or an exact number where
known), status, boosts, whether it has fainted, and whether its item has been
consumed.

Procedure: `create`; both sides choose `team <order>` so that the brought four
lead with the two actives first; the battle is then at turn 1 with the leads
on the field. Then, through Showdown's own methods rather than by patching
serialized JSON: `battle.turn`; `pokemon.sethp` (never below 1 unless
fainted); `pokemon.setStatus`; `pokemon.boosts`; fainted Pokemon get `hp = 0`,
`fainted = true`, `status = 'fnt'`; consumed items are cleared; `field.setWeather`,
`field.addPseudoWeather`, `side.addSideCondition`, with Tailwind's remaining
duration derived from the snapshot where poke-env recorded the turn it began.
Finally `battle.makeRequest('move')` so both sides hold a fresh request.

Positions with an empty active slot on our side are forced-switch decisions
and are not materialised; the simulator arm falls back to the analytic model
for that decision and counts it (5.3).

### 5.3 `champions/search/rollout.py`: the simulator payoff

`RolloutModel` produces a matrix in the shape `discard.MatrixFn` names —
`matrix(snapshot, ours, theirs) -> np.ndarray` — so it drops into the same
place `payoff_matrix(..., model=TurnModel)` occupies.

Per decision: materialise once per opponent hypothesis (one, for the
oracle), fix `R` replicate seeds derived from `(seed, battle, turn)`, and for
every cell and every replicate: `clone`, set the clone's PRNG seed to the
replicate's, `step` with both choices, value the result. The value of a cell
is the mean over replicates. Every cell sees the same replicate seeds, which
is the common random numbers requirement (`docs/04` section 4) done the way
the simulator allows: there is no roll bucketing inside a simulator, so
replicates are the roll integration, and `R` is what buys variance down.
`R = 2` by default; the measured cost per cell decides whether it rises.

Choices. Ours come from the described action's `message`
(`/choose move earthquake 1, switch milotic`), which is poke-env's wire form
and what the simulator parses. Theirs are built from the column dict: `move
<id> <target>` per slot, the target renumbered from their side of the field.
Showdown resolves a move by id string; verified against the pinned commit at
build time, not assumed.

The post-step position is read back from the simulator's serialized state
through `sim_snapshot(state, side)`, which produces the
`champions.protocol.state.snapshot()` shape — our side exact, theirs as
percentages, `selected` on our brought four, their side as what has appeared
— and hands it to `win_prob` unchanged. It is validated against
`champions.corpus.replay_state.Observer` on the same log, field by field over
the fields both carry, the way M7 validated the observer against the live
snapshot.

A choice the real battle allowed and the materialised one refuses (a Choice
lock the snapshot does not carry, a Disable) is a cell the arm cannot score.
It takes the analytic value for that cell and the decision records the
fallback count on the trace; the gate table reports the fraction of decisions
with any fallback. An arm that falls back often is not measuring what it says.

The simulator call is synchronous over stdio. Cells are awaited between so the
watchdog can cancel, and the clock columns therefore mean what they say.

### 5.4 `champions/agents/oracle.py`: the oracle and the four arms

`TeamOracle(team_export, dex)` parses a Showdown export into six sets and
answers the three questions the belief answers today: `stats_for(species)`
(exact, from base stats, points and nature through `champions/dex/stats.py`),
`set_for(species)` (a `SetHypothesis` with the true item, ability, moves and
nature), and `believed_moves(species)` (the true four). `BeliefHypothesis`,
`BeliefEffects` and `opponent_candidates` read exactly those three off
`BattleBelief`, so the oracle stands in for it structurally; the annotation
becomes a `Protocol` naming the three methods rather than the concrete class.

Agents: `OraclePlyAgent`, `TwoPlyAgent`, `TwoPlyOracleAgent`,
`SimOracleAgent`, registered in `scripts/run_ladder.py` and
`scripts/selfplay.py` under the names in section 3. The oracle takes
`--opponent-team`, defaulting to the arm's own team (the mirror). Traces carry
`opponent_model: "oracle-sets"` and, for the simulator arm, `model:
"simulator-one-turn"` with `replicates` and `fallbacks`.

### 5.5 `scripts/engine_gate.py` (`make gate`)

Runs the matchups and writes `docs/engine-gate.md` and
`data/eval/engine-gate.<format>.json`, generated rather than hand written,
the way `docs/pruning-guard.md` and `docs/eval-calibration.md` are. Each arm
against `oneply`, `N` games, one seed, each team, sequentially on one local
server; `run_matchup` gains a username suffix so arms on one server do not
collide, which also closes the defect `docs/STATUS.md` records against
`run_ladder.py` and `selfplay.py`.

The report: per team, one row per arm with win rate, Wilson interval, the
difference against `oneply-oracle` with its interval, the clock columns, and
the fallback fraction for the simulator arm; then section 4's rule applied
mechanically and the verdict printed per team. The verdict function is unit
tested on synthetic results for every branch of the rule, so the rule cannot
drift between the spec and the run.

## 6. Order of work

1. This spec; `docs/02-mechanics-deltas.md` section 7 gains the local
   arithmetic; `docs/04-decision-engine.md` section 3 gains the paragraph
   D67 asked for (B built, measured, rejected; the union's `k` dependence);
   D70 records the rule and the reversed budget conclusion.
2. `twoply.py`, `TwoPlyAgent`, tests. First number: a 50 game smoke run of
   `twoply` against `oneply` on alpha, to check the cost and that the agent
   completes games. A bad smoke number does not cancel the full run; the
   rule is pre-registered.
3. `materialize`, `rollout.py`, `sim_snapshot`, the equivalence tests. First
   number: materialise cost and per-cell cost, which is the clock column
   before a game is played.
4. `TeamOracle`, the four agents, `engine_gate.py`, the verdict tests.
5. The run: 4 arms × 2 teams × 200 games. `docs/engine-gate.md`, D71 with
   the verdict, `docs/STATUS.md`.

Each step is a checkpoint and a commit. Steps 2 and 3 are independent and 3
is the risky one: it is the first code in the project that constructs a
simulator state rather than observing one.

## 7. Testing

TDD throughout, as with D61 and M7.

- **Two-ply.** Deterministic from seed. A state with a side wiped out returns
  `win_prob` without solving. A hand-built position where switching into a
  resist is right at depth 2 and scored as giving up the turn at depth 1 —
  the switch bias, as a test rather than a sentence. Interior enumeration
  never yields a fainted Pokemon, a same-target double switch, or a move the
  Pokemon does not have. Identical children hit the memo. Values stay in
  `[0, 1]`.
- **Materialize.** Against real seeded simulator battles played by
  `randomChoice`: at every turn, rebuild the observer's view from the log,
  materialise it with both true teams, copy the real battle's PRNG seed onto
  the materialised one, and step both with the same choices. The request
  before the step and the log lines after it must match exactly. Positions
  carrying an effect the snapshot cannot represent — opponent PP, Choice
  locks, Encore, Taunt, Disable, Substitute, Perish Song, volatile durations
  — are skipped and counted; the test asserts equality on the rest and
  reports the skipped fraction, and the exclusion list is in the test file
  by name.
- **Rollout.** The same cell scored twice gives the same value. Every
  described kind produces a parseable choice. The fallback path is counted.
  `sim_snapshot` agrees with `Observer.view` on every field both carry, over
  the same battles the materialise test plays.
- **Oracle.** Parses both checked-in teams; the stats it computes equal the
  ones the simulator reports in its request, which is `tests/test_stats.py`'s
  existing pattern; `believed_moves` returns the registered four.
- **Gate.** The verdict function on synthetic results covering every branch
  of section 4. Table formatting. The username suffix keeps two arms on one
  server apart.

## 8. What this will not do

- **No Rust engine.** The gate decides whether one is built. Nothing here
  starts it, and the branch named in section 4 is created only by the
  verdict.
- **No belief-fed simulator arm.** It needs `data/priors/`, which needs the
  corpus, which is not on this machine. The oracle is the ceiling; the
  belief-fed arm is the first follow-up if fidelity wins.
- **No depth 2 on the simulator.** Section 2 says it fits on eight cores at
  one particle and one replicate; it is the follow-up section 4 names for
  the "both clear" outcome, not a fifth arm.
- **Interior nodes are approximate.** The request is the authority at the
  root only. Section 5.1 lists what the child enumeration does not know.
- **No provider or budget change.** `k` stays 10, A stays the provider, the
  union stays measured and unshipped. All three were deferred to M8's payoff
  model by name (D67) and are re-opened *after* the verdict, against
  whichever model won.
- **No coach work, no clock optimisation.** The clock is reported, not
  managed (M11).
- **The teams are not pooled.** Two verdicts, stated separately.

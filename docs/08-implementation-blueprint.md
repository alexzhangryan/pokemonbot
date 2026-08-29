# Implementation Blueprint

Ready to code against.

## 1. Stack

- Python 3.12 as the primary language. The ML, the belief filter, and the search orchestration live here.
- `poke-env` for Showdown transport. It has a `DoublesEnv` and handles the websocket protocol, reconnection, and the challenge flow. Wrap it rather than depending on it deeply, since the state model needs Champions-specific fields it does not know about, and the Open Team Sheets prompt has to be handled explicitly (always decline).
- Node subprocess running a pinned build of `smogon/pokemon-showdown` in two roles: a one-off dex dumper, and a long lived JSON-RPC wrapper over `BattleStream` used as the rollout oracle and the differential oracle.
- SQLite for the replay corpus. One portable file, which matters for reproducibility.
- PyTorch for learned components, SciPy `linprog` for the matrix game solve, NumPy throughout.
- FastAPI and websockets for the observability server, single page frontend, no build step.
- Rust behind PyO3 only if the engine gate opens.

Pin the Showdown commit hash. The Champions mod is under active development and an unpinned dependency turns every mechanics change into an unexplained regression.

## 2. Repository layout

```
pokemonbot/
  README.md
  pyproject.toml
  .gitignore
  docs/                       this document set
  vendor/showdown/            pinned checkout, built, gitignored
  data/
    dex/<format_id>.<hash>.json
    corpus.sqlite
  js/
    dump_dex.js               resolved dex extraction
    sim_server.js             JSON-RPC over BattleStream
  champions/
    trace/   schema.py  writer.py            defined at M0
    dex/     loader.py  stats.py  damage.py
    protocol/ parser.py  state.py  actions.py
    belief/  particles.py  spreads.py  priors.py
    search/  matrix.py  policy.py  evaluate.py  oracle.py
    agents/  baseline.py  oneply.py
    preview/ bring4.py
    coach/   analyze.py  classify.py  report.py
    viewer/  server.py  static/
    harness/ ladder.py  elo.py  differential.py
  scripts/
    build_dex.py  scrape_replays.py  train_bring4.py  run_ladder.py  review.py
  tests/
```

## 3. Core interfaces

Keep these stable. Everything later depends on them.

```python
# trace, defined first
Trace.emit(event_type: str, payload: dict) -> None

# dex
Dex.load(format_id: str) -> Dex
stats.compute(base: int, points: int, nature_mult: float, is_hp: bool) -> int
damage.roll_distribution(attacker, defender, move, field) -> list[tuple[int, float]]

# protocol
parser.apply(state: BattleState, line: str) -> list[Observation]
actions.enumerate(request: dict) -> list[JointAction]

# belief
Belief.update(obs: Observation) -> None
Belief.sample(k: int) -> list[TeamHypothesis]
Belief.stat_interval(species: str, stat: str) -> tuple[int, int]

# search
class PolicyProvider(Protocol):
    def candidates(self, state, belief, k: int) -> list[JointAction]: ...

oracle.step(state, our_action, their_action, seed) -> BattleState
evaluate.win_prob(state: BattleState) -> float          # calibrated
matrix.solve(payoff: np.ndarray) -> tuple[np.ndarray, float]

# agent
Agent.choose(request: dict, state: BattleState) -> str
```

Three notes. `actions.enumerate` reads the request object rather than reconstructing legality, because the request already encodes Choice locks, Encore, disabled moves, target legality, and Mega availability. `evaluate.win_prob` must be calibrated rather than merely monotone. `Trace.emit` is called from every component, so it is written before any of them.

## 4. Dependency order

```
trace schema ────────────────→ every component emits from birth
dex dump ─→ stats ─→ damage ─┬─→ belief/spreads ─→ belief/particles ─→ agent
                             └─→ evaluate ─→ preview/bring4 ─→ coach ─→ viewer
protocol/state + actions ────→ agents ─→ harness ─→ every measurement
oracle + differential ───────→ confidence in any custom engine
corpus ─→ priors ─→ belief/particles
corpus ─→ evaluate (trained) ─→ coach
```

The dex dump blocks everything numeric. The harness blocks every claim. The trace schema blocks nothing but gets harder to add every day it is deferred.

## 5. Milestones

- M0. Pinned Showdown build, dex dump, local server, agent connection, random agent completing games, ELO harness with a frozen opponent pool and clock compliance metrics, differential test harness, trace schema and writer, deadline watchdog.
- M1. Champions stat and damage layer with roll distributions, validated cell by cell against the simulator. Most mechanics bugs surface here.
- M2. One ply agent: enumerate from the request, prune with the heuristic policy provider, estimate payoffs with common random numbers and roll bucketing, solve the matrix game by LP.
- M3. Replay scraper and corpus, both Bo1 and Bo3.
- M4. Bring-4 and lead predictors, evaluated in isolation, then the exact $15 \times 15$ preview equilibrium.
- M5. Belief filter: spread interval propagation first, then categorical particles from the learned prior.
- M6. Trained and calibrated evaluation function. Reliability diagram required before use.
- M7. Policy provider benchmark: heuristic against learned prior against language model, measured identically on decision quality, discard rate, and latency.
- M8. Engine decision gate. Profile and decide between depth and evaluation quality. Build the Rust engine only if depth wins.
- M9. Coach: analysis overlay, ex-ante and ex-post loss, classification and tags, critical turns, natural language writeup.
- M10. Observability clients: live view and game review.
- M11. Clock compliance: adaptive time allocation, latency profiling, and whatever M7 implies about running the policy layer inside 45 seconds.

## 6. First week

1. Initialize the git repository, pin `smogon/pokemon-showdown` under `vendor/`, build it, commit the hash.
2. Define and freeze the trace schema, write `champions/trace/`, and make the JSONL writer work. Small, and it stops being cheap the moment other components exist.
3. Write `js/dump_dex.js`, dump the resolved dex for `gen9championsvgc2026regmb`, diff against mainline gen 9, and commit the delta list as a document. Nobody has published this.
4. Implement `champions/dex/stats.py` from the verified formula and unit test it against simulator-reported stats for a hand built team.
5. Stand up a local Showdown server and get a random agent playing itself to completion through poke-env, including declining the Open Team Sheets prompt.
6. Build the ELO harness with seeded, paired matchups and a frozen pool of random and greedy agents. Report clock compliance in the same table as win rate from the first run, even though the random agent trivially passes it.
7. Write `js/sim_server.js` exposing step and clone over JSON-RPC, and reproduce the 4.7 ms per clone-plus-step figure locally so the budget numbers are measured rather than inherited.
8. Start the replay scraper on the Bo3 format, since that corpus is the labeled one and it accumulates while everything else is built.

## 7. Known risks

- Mechanics drift in the Champions mod. Mitigated by the pinned commit, the content hash, and the differential harness.
- Regulation rollover after 2026-09-09 changing the legal pool or the mod. Nothing hardcodes species lists or format IDs.
- Percent quantization on opponent HP silently eliminating the true hypothesis. Mitigated by soft bounds with an explicit error term, caught by the interval coverage metric.
- Candidate pruning discarding a uniquely correct action. Mitigated by the offline unpruned comparison, reported per policy provider.
- Deferring clock optimization until M11 and discovering the architecture cannot meet it. Mitigated by emitting per-phase timing from M0, reporting clock compliance beside win rate in every evaluation run, and structuring the search as anytime from the start so the deadline is a parameter rather than a redesign.
- Losing live games on the timer before M11. Not a performance problem but a forfeit, and unrelated to whether we are optimizing yet. Mitigated by the watchdog, which returns the best action found so far when the deadline arrives.
- Scope creep between the agent and the coach. The coach reuses the agent's components and adds only offline analysis. If it starts needing its own evaluation function, something has gone wrong.

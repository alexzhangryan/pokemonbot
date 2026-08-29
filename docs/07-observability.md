# Observability, Live View, and Game Review

## 1. The principle

The user interface is late work. The emission contract is not.

A stochastic agent that samples from a mixed strategy over a sampled belief cannot be debugged by reading its output. When it loses, the question is always whether the belief was wrong, the candidate set was wrong, the payoff estimates were noisy, or the equilibrium was correct and the roll went badly. Only a recorded trace answers that, and retrofitting one into six components after the fact means touching all of them again.

So the trace schema is defined at M0 and every component emits it natively from the day it is written. The live view and the review client are consumers built later against a contract that already exists.

There is a second reason this ordering pays. The review client and the live view are the same program. One reads events from a websocket, the other reads the identical events from a file. Building the schema first makes that unification free rather than a refactor.

## 2. Decision trace

Append-only JSONL, one file per battle, plus a websocket broadcast of the identical event stream. One producer, two sinks.

Every event carries `schema_version`, `battle_id`, `seq`, and `t` (wall clock). Payload by type:

`battle_start` — format ID, our full team, opponent's six species as revealed at preview, whether we are p1 or p2.

`preview_decision` — predicted distribution over the opponent's 15 bring-4 subsets, the $15 \times 15$ payoff matrix, our equilibrium weights over subsets, the sampled choice, the lead subgame, and the RNG seed used to sample.

`turn_start` — turn number, full observable state (both sides: species, HP percent, status, boosts, items and abilities revealed so far, field conditions, weather, speed control timers), and clock remaining on both sides.

`belief` — for each of the opponent's six: the top $k$ set hypotheses with weights, the maintained stat interval per stat, the nature posterior, and per-Pokemon entropy. Plus effective particle count and total belief entropy.

`candidates` — the pruned joint action set, each annotated with what the engine computed for it: damage roll distribution per target, knockout probability, resulting speed order, and which policy provider proposed it.

`payoff_matrix` — rows, columns, estimated values, and sample count per cell. Capped to the pruned set by default. Full unpruned matrices only under a debug flag, since they are two orders of magnitude larger.

`equilibrium` — the mixed strategy vector, the game value, the sampled action, and the RNG seed. Recording the seed is what makes a live decision exactly reproducible offline, which matters more than it sounds: without it the coach cannot re-derive what the agent actually saw.

`timing` — milliseconds per phase (belief update, candidate generation, payoff estimation, equilibrium solve, policy provider call), plus the total. Also the budget state: remaining turn budget, remaining player clock, a boolean for whether this turn would have exceeded 45 seconds, and whether the watchdog fired and returned an unfinished result. Emitted every turn from M0.

The two booleans matter more than the raw milliseconds. They are what the harness aggregates into clock compliance metrics, and they are what makes a latency regression visible in the same table as the win rate that bought it. The live view renders them as a per turn indicator so a slow decision is obvious while it is happening rather than in a log afterwards.

`turn_result` — the opponent's realized action, the actual outcome, and every observation extracted from it for the belief update.

`battle_end` — result, final state, cumulative timing.

The coach appends a parallel stream of `analysis` events keyed to the same `seq` numbers: ex-ante loss, ex-post loss, classification, tags, belief entropy at the time, and the generated explanation. So a reviewed game is the original trace plus an analysis overlay, not a separate artifact.

## 3. Live view

Runs as its own window alongside Showdown, served locally, showing what the agent is computing as it computes it.

- Win probability bar, updating each turn from the calibrated evaluation function.
- Current candidate set with per-action values and the equilibrium weights, so you can watch the agent decide rather than infer it afterwards.
- Belief panel: the opponent's six with, for each, the top item and move hypotheses with probabilities and the maintained stat interval bars. This is the single most useful debugging surface in the system, because a belief that has gone wrong is visible immediately and is otherwise invisible.
- Timing readout per phase, with the running total clock.
- Damage roll distributions for the candidate the agent is leaning toward.

Purely read-only. The live view never influences play.

## 4. Game review

The chess.com analogue, and the primary user-facing deliverable of the coaching side.

Layout:

- Left edge: vertical eval bar showing win probability, animating as the reviewer scrubs turns.
- Centre: the battle state at the selected turn. Both active pairs with HP, status, and boosts, the benched Pokemon, field conditions, and speed control timers. The opponent's six carry belief annotations showing what was known versus inferred at that point.
- Right: the turn list, one row per turn, each with its classification icon and tags, scrollable and clickable to scrub. This is the spine of the interface, exactly as the move list is in chess.com.
- Bottom: detail panel for the selected turn. The action played, its classification and tags, ex-ante and ex-post loss, the equilibrium mixed strategy rendered as percentages, the candidate table with values, the damage roll distribution with the realized roll marked, and the natural language explanation.
- Header: game summary. Result, accuracy-style aggregate over ex-ante loss, count of each classification, and the identified critical turns as jump links.

The bring-4 verdict occupies its own pseudo-turn before turn 1, since preview is a real decision with a real equilibrium and players rarely get feedback on it.

## 5. Implementation notes

The client is decoupled from the agent by the schema alone. It never imports agent code, and it must render a trace produced by a different version of the agent without crashing, degrading gracefully on unknown event types and missing fields.

Serving: FastAPI with a websocket endpoint for live and a static file endpoint for stored traces, with the frontend as a single page. Avoid a build step for as long as possible. The value here is in the data and the layout, not in the framework.

Rendering Pokemon sprites and type icons from Showdown's public assets keeps the visual work near zero.

Traces are large. Rotate and compress per battle, keep the payoff matrices capped by default, and never let trace writing sit on the decision critical path. Write to a queue and flush asynchronously.

## 6. Where this lands in the plan

M0 defines and freezes the schema, and adds a trivial writer plus a JSONL dump. Every subsequent milestone emits its own events as it is built. M9 produces the analysis overlay. M10 builds the two clients, which by then is a rendering exercise against a stream that already contains everything they need.

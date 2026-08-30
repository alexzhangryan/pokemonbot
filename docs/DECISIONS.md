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

## D13. The live view is built at M1, not M10, and reads trace files rather than the agent — 2026-08-28, Claude Code

Context: `docs/07-observability.md` section 6 places both clients at M10, on the reasoning that by then the stream already contains everything they need. In practice the reverse bites first: with no consumer, nothing forces the emission to be complete, and `turn_start` at M0 carried four species names and no state at all. The schema was frozen; the payloads behind it were not.

Decision: build the live view now, as a standalone FastAPI process that tails the JSONL trace directory. It never imports agent code, holds no reference to a `Player`, and cannot send anything to Showdown. A finished battle is a file that stopped growing and a live battle is a file that has not, so replay and live are one code path with no branch between them.

Rationale: a consumer is what makes an emission gap visible. Building it early turned three real defects into failing checks within an hour — poke-env enums serialised as `"FLYING (pokemon type) object"`, unrevealed opponent items serialised as the literal string `"unknown_item"`, and no record anywhere of what actually happened in a turn. All three would otherwise have been discovered at M10 with nine milestones of traces already written in the broken shape. Tailing files rather than subscribing to the agent makes "purely read-only" a property of the architecture instead of a rule someone has to keep remembering, and costs only a 250ms poll latency on a path that is not the decision path.

Consequences: the viewer lags by up to one poll interval, which is invisible at human speed and irrelevant to the agent. The review client of section 4 is still M9/M10 work; what exists now is section 3. Every future milestone that computes something new must emit it or it will visibly not appear, which is the intended pressure.

## D14. Unbuilt quantities are named in the trace, never defaulted — 2026-08-28, Claude Code

Context: the viewer has columns for damage rolls, knockout probability, win probability, and the mixed strategy, none of which the agent computes before M1 to M6. The obvious options were to omit them or to emit zeroes.

Decision: events carry an explicit list of what could not be computed — `annotations_pending` on `candidates`, `pending` on `equilibrium` and `preview_decision` — and the viewer renders exactly those as hatched blocks tagged with the milestone that fills them.

Rationale: a zero is indistinguishable from a measurement, and an omission is indistinguishable from a bug in the reader. Both are dangerous specifically here, because the panels in question are the ones used to judge whether the agent is reasoning correctly. Naming the gap makes the trace self-describing: the viewer needs no table of which milestone the file came from, and a trace from a later agent version fills its columns without a client change.

Consequences: the emitting component owns the list of what it is missing, so a component that starts computing a quantity must also drop it from its pending list, or the viewer will keep showing it as unbuilt. That coupling is deliberate and cheap; the alternative is a version table in the client.

## D15. The trace records the raw Showdown protocol per decision — 2026-08-28, Claude Code

Context: poke-env folds each protocol message into its battle state and keeps no log, and this poke-env version exposes no per-turn observation history. A trace of decisions with no account of their consequences cannot answer the first question anyone asks of a losing game.

Decision: `TracingPlayer` overrides `_handle_battle_message` to buffer the raw lines before the superclass consumes them, and attaches everything seen since the previous decision to the next `turn_start` as `log`. `|request|` payloads and pure chatter are dropped; everything else is kept verbatim.

Rationale: this is the server's own account of the battle, already censored to our side of the field, and it is the ground truth every other event is derived from. Paraphrasing it would put a translation layer between a reader and the thing they are trying to check. Capture must precede `super()`, because the superclass dispatches the request that calls `choose_move`.

Consequences: `turn_result` as specified in `docs/07-observability.md` section 2 is still unemitted; the log plus consecutive state snapshots covers what it was for, and the belief filter at M3 will need the extracted observations rather than the raw lines. Whether `turn_result` becomes a parsed digest of this log or is dropped in favour of it is an open question for M3.

## D16. The battle view is rendered from the trace, because Showdown's client refuses to be framed — 2026-08-29, Claude Code

Context: the obvious way to put a real battle window between the two perspective panels is to iframe the Showdown client. The local server does not host a client at all — it serves an 873 byte redirect to `https://<host>--<port>.psim.us/`, which is Smogon's hosted client configured to connect back to the local server.

Finding: that client deliberately refuses to run framed. Its source carries an OWASP-cited frame-bust — `if (self === top) { app = new App(); } else { LM.innerHTML += ' IN FRAME<br />Please visit Showdown directly.'; top.location = self.location; }` — so it halts and additionally tries to navigate the embedding page to itself. Chrome blocked that navigation only because it was cross-origin without a user gesture. No response header is involved; there is nothing to configure.

Decision: render the battle from the trace instead. The centre column is a stage — opponent above, us below, field conditions between, back sprites for our own side — and the real client is opened in its own positioned window by a button, which is a top-level window and so unaffected.

Rationale: defeating a third-party site's anti-framing control on their host is not something to do. Self-hosting a patched client would be legitimate but costs a second vendored repo pinned against the server commit, a Node build step, and cuts against two conventions in `CLAUDE.md`. The trace already contains everything a battle view needs, and rendering it ourselves buys something an embed could never provide: the stage marks which of our slots is acting and which slots the chosen action is aimed at, including the case where the agent points a move at its own partner. That is the mistake most worth seeing on a board and it is invisible in a `/choose` string.

Consequences: no animations, and the view is only as good as the snapshot. Anything the state snapshot omits is absent from the battle view, which is the same pressure D13 describes and is intended. If a real embedded client is ever wanted, it means vendoring `smogon/pokemon-showdown-client`, and that is a decision to take deliberately rather than by drifting into it.

## D17. The viewer owns the session: it starts the simulator and the runs it displays — 2026-08-29, Claude Code

Context: a session took three terminals — `make server`, `make play` or `make selfplay`, `make viewer` — and the defaults did not line up. `play_human.py` wrote to `runs/human` while `make viewer` watched `traces/`, so the first live battle produced a viewer showing nothing, with no error anywhere to explain it. That is not a documentation failure. The component that displays traces was not the component deciding where they go, so nothing could keep the two consistent.

Decision: the viewer starts the Showdown simulator on launch and gains a control panel that launches self-play runs and puts a bot up to be challenged. The supervisor owns the trace directory and passes it to every run it starts. `scripts/viewer.py` is the only command a session needs; the individual scripts and make targets still work and are still what the tests use.

Rationale: making the directory a parameter of the thing that already knows it removes the mismatch by construction rather than by instruction. Runs are subprocesses rather than tasks in the viewer's event loop, so a crashing agent cannot take the window down with it, and progress arrives as parsed stdout. An already-listening port is adopted rather than duplicated and is never stopped by us, since starting a second server would only fail to bind and killing one we did not start would take out someone else's.

Consequences: the viewer now has a lifecycle to get wrong — orphaned processes if it is killed rather than shut down, and a single-run limit that is a deliberate simplification rather than a technical one. Two agent runs against one simulator would fight over usernames and make the trace directory unreadable, so the second is refused with a 409.

## D18. Auto-follow keys on the battle, not on the most recently written file — 2026-08-29, Claude Code

Context: the viewer followed whichever trace file had the newest mtime. A battle produces one file per agent-view (D10), and in self-play both are written milliseconds apart, so "newest" alternated between `champ-a` and `champ-b` on almost every poll. The viewer tore down and rebuilt its websocket roughly once a second and never settled long enough to render a running battle.

Decision: auto-follow compares `battle_id`. A switch happens only when a genuinely different battle appears; within one battle the first id in sorted order is chosen, which is stable across polls. An explicit pick from the trace selector still pins the view until the reader changes it.

Rationale: the two files are two views of one thing, and the thing is what a reader is following. Keying on the file made an implementation detail of the trace layout into visible flicker.

Consequences: switching between the two sides of the same battle is a manual choice, which is correct — they are different agents' knowledge and conflating them was never wanted.

## D19. The battle animation is Showdown's own renderer, replaying our trace — 2026-08-29, Claude Code

Context: D16 concluded that a real Showdown view was unavailable because the client frame-busts. That conclusion was right about the client and wrong about the renderer. The frame-bust lives in the client's own index page (`if (self === top) app = new App()`); the battle renderer is a separate set of files that Smogon publishes and explicitly supports embedding. `replay-embed.js` says so in its header comment: "can also be used by third parties to embed PS replays."

Decision: `static/battle.html` loads Showdown's renderer (`battle.js` and its data files, in the order `replay-embed.js` uses) and drives the `Battle` class directly. The viewer embeds it as a same-origin frame between the opponent panel and ours, and feeds it the protocol log already in the trace, seeking to whichever turn the spine has selected.

Rationale: the trace already had to record the full protocol for the decision log to mean anything (D15), and a faithful protocol stream is exactly what the renderer consumes, so the animation costs no new data. A same-origin frame rather than inline because `battle.css` is a large global stylesheet written for Showdown's own page and would restyle the whole instrument. `Battle` rather than `replay-embed.js` because the embed brings a whole replay page with its own controls and speed chooser, and two scrubbers disagreeing about the current turn is worse than no animation.

This also required making the recorded log faithful. It had been filtered down to what read well, dropping `|upkeep|` and the blank separator lines; the renderer segments turns using exactly those.

Consequences: the animation needs internet, since the renderer and its sprites come from Smogon's CDN. Everything else in the viewer works offline, and the frame says so rather than showing an empty box. The renderer is *mainline* Showdown's, so a Champions-only forme can resolve to a sprite that does not exist upstream — Mega Greninja 404s today. That degrades to a missing image inside the scene and affects nothing else, but it is a standing reason the annotated stage above and below the scene is not redundant with it: our panels are drawn from the Champions dex, the animation is drawn by mainline assets.

## D20. A live battle elsewhere is advertised, not silently ignored — 2026-08-29, Claude Code

Context: the viewer pins to a trace once the reader picks one by hand, which is right — jumping away from something being read is worse than being stale. But a pinned viewer during a live battle looks exactly like a broken live view, and that is how it was reported.

Decision: when any trace is being written to and it is not the battle on screen, the header shows a **Live battle** button that switches to it and clears the pin.

Rationale: the pin is correct behaviour and the silence is not. The fix is to make the state visible rather than to remove the pin.

## D21. The scene plays when live and seeks when scrubbed — 2026-08-29, Claude Code

Context: the embedded renderer tracked turns correctly and yet no move ever animated, which read as "the live view is frozen except when a Pokemon switches in". Two facts combined. `Battle.seekTurn`'s forward path calls `scene.animationOff()` and fast-forwards, so every update landed on the turn's end state with nothing played. And a forced switch produces a second decision point on the same turn number, which takes `seekTurn`'s `turn <= this.turn` branch and does a full `resetStep()` -- a visible redraw. So the only updates that looked like anything were the switches.

Decision: two modes rather than one. Following a live battle calls `play()`, which animates the queue as lines arrive, after an instant seek to the current turn on first attach so joining mid-battle does not replay the whole game. Scrubbing, and any non-live view, calls `pause()` then `seekTurn(turn, true)` and lands immediately.

Rationale: these are genuinely different actions and the renderer already distinguishes them; the bug was asking one call to serve both. Animating the way to a turn someone just clicked makes the view lag their own input, and fast-forwarding a live battle discards the only thing an animation is for.

Consequences: a speed control is now meaningful, so there is one, using Showdown's own presets plus a "skip animations" mode that forces the seek path everywhere. The choice is remembered per browser.

## D22. The scene is drawn from the traced agent's viewpoint — 2026-08-29, Claude Code

Context: the renderer defaults to drawing p1 on the near side. A trace is one agent's view and that agent is p2 about half the time, so half of all battles were shown from the opponent's chair, with the bot's own team across the top.

Decision: the viewer passes `battle_start.player_role` to the frame, which calls `battle.setViewpoint(role)`.

Rationale: the entire point of this window is the bot's perspective, and `player_role` was already on the trace for exactly this kind of question.

## D23. Animation is keyed on the log growing, not on the live badge — 2026-08-29, Claude Code

Context: D21 made the scene animate while "following a live battle", where live meant the trace file had been written to within 25 seconds. That works against a fast opponent and fails against a person. A human deciding a doubles turn routinely takes longer than 25 seconds, the badge flips to replay, and every subsequent update silently reverts to an instant seek. The reported symptom was that animations still never played; the test that had passed used a stand-in moving every five seconds, which never crossed the threshold.

Decision: the scene plays when the protocol log has actually grown and the reader is following the front of the trace, and seeks otherwise. The staleness heuristic no longer takes part in the decision. Its window also went from 25 to 90 seconds, which is a better badge in its own right.

Rationale: log growth is the thing that literally means "something happened", and `following` is the thing that means "and you are watching the front of it". The badge was a proxy for both and was accurate for neither.

Consequences: a stand-in opponent that plays faster than a person is not a valid test of this path. The browser test now uses thirty-second turns specifically to cross the old threshold, and asserts that animation is still running afterwards.

## D24. The control surface is one bar, not a drawer — 2026-08-29, Claude Code

Context: the controls arrived as a toggled drawer of four cards — simulator, self-play, play the bot, current run — each with a heading, a form and its own buttons. It was reported as clunky, and it was: setting a session up is a handful of small decisions, and putting them behind a toggle made a two-second job feel like a mode to enter and leave.

Decision: one always-visible 40px row under the header. A dot and a word for the simulator, inline fields for self-play, one select and a button to put a bot up, and the current run's progress at the right with opt-in output. The challenge hand-off collapsed from a four-step numbered card to a single line ending in the link.

Rationale: none of these controls needs a heading to be understood next to its own field, and none of them is used often enough to earn permanent vertical space, but all of them are used often enough that hiding them costs more than showing them. The agent list is served from the registry rather than hardcoded as options, so adding an agent needs no client change.

## D25. Natures and the type chart are dumped from the simulator, not transcribed — 2026-08-29, Claude Code

Context: the stat formula multiplies by a nature, and the damage formula multiplies by type effectiveness. Both tables are small, universal, and identical in the champions mod today, so both were candidates for being written down as constants in Python.

Decision: `js/dump_dex.js` dumps `natures` and `types` alongside species, moves, items and abilities. `champions.dex.loader.Dex` exposes both, and the stat and damage layers read them from there.

Rationale: the mod overrides roughly 300 moves and 250 items. That it does not currently override natures or the type chart is a fact about this build, not a guarantee, and Reg M-B expires 2026-09-09. Dumping them costs nothing and turns a future change from a wrong number into a diff. It is the same argument that put the dex dump between the code and poke-env.

Consequences: the format dump's content hash changed. `docs/dex-delta.md` is unaffected -- it is produced from the separate whole-mod dumps, which did not change -- and reproduces byte for byte.

## D26. The damage layer is native, even though the formula is mainline — 2026-08-29, Claude Code

Context: `CLAUDE.md` says never to use `@smogon/calc` or any mainline damage formula, on the stated grounds that Champions is not mechanically Generation 9. M1 tested that by diffing the mod's `modifyDamage` against `sim/battle-actions.ts`.

Finding: the two are numerically identical. The only difference in the entire function is that `-supereffective` and `-resisted` gained a `Math.min(typeMod, 2)` argument, which is a protocol message. `getDamage`, which computes the base damage, is not overridden at all.

Decision: `champions/dex/damage.py` reimplements the formula natively anyway, and the rule in `CLAUDE.md` stands.

Rationale: the shape is shared; every input is not. Stats come from a linear formula, roughly 300 moves have different base power, roughly 250 items and 8 abilities behave differently, and Terastallization is off. A mainline calculator would feed mainline stats, mainline base powers and a live Tera type into a skeleton that happens to match, and would be wrong quietly rather than loudly. Sharing the skeleton is what makes that failure hard to see, so it is an argument for the rule rather than against it.

Consequences: the rule's justification in `CLAUDE.md` is now sharper than "the formula differs", and should be read as "the inputs differ, and the shared shape is the trap". Item and ability multipliers are not enumerated in the damage layer; they enter through `DamageContext.final_modifiers`, because roughly 250 hand transcriptions is the error this project keeps avoiding.

## D27. Damage is compared against the simulator as set membership, clamped at the target's HP — 2026-08-29, Claude Code

Context: validating a damage layer cell by cell means comparing against a simulator that samples one of sixteen rolls. Forcing a specific roll would mean reaching into the PRNG.

Decision: `tests/test_damage.py` runs a probe battle over many seeds and asserts that every damage the simulator reports is a member of the sixteen values predicted for that cell, after clamping each prediction at the target's remaining HP.

Rationale: over enough seeds the observed values sweep most of the roll range, so membership becomes a claim about the whole distribution rather than about one number, and a separate test fails if the sweep degenerates to a handful of values. The clamp is not a fudge: the simulator reports HP actually lost, so a roll that would overkill is reported as the remaining HP, and predicting more damage than the target has left is correct rather than a divergence. Getting this wrong is what the first run of the test looked like.

Consequences: crits are detected from the log and fed back into the prediction rather than being forced, so they are checked whenever they occur -- 20 in 480 events on the current probe. The probe holds everything else still: clean abilities on all four Pokemon, no items, no weather, no boosts, turn one only, and a fresh battle per seed.

## D28. M2's payoff estimator is analytic, not simulator-backed — 2026-08-29, Claude Code

Context: `docs/04-decision-engine.md` section 4 says each cell needs an expected value over belief particles and the damage roll distribution. `champions/search/oracle.py` can clone and step a real battle in 2.13 ms, which is the higher fidelity way to get one.

Decision: `champions/search/payoff.py` models one turn analytically with the M1 damage layer and scores the result with `champions/search/evaluate.py`. The simulator is not called during a decision.

Rationale: the obstacle is information, not speed. Stepping the simulator requires a complete opponent team — spreads, items, abilities, and the two Pokemon they have not shown — and inventing one would be inventing the answer. Constructing that team from observations is the belief filter, which is M5. So M2 estimates from what is known, and `OpponentHypothesis` is the seam M5 swaps particles into without the surrounding search changing.

Consequences: the model does not represent abilities, items, secondary effects, status move effects, weather, multi-hit, recoil, healing or accuracy. Those are absent rather than approximated, because a wrong number that looks computed is worse than a missing one — the search will exploit a fictitious advantage and no test catches it. The cost is measurable and was measured: against max-base-power on a team with no items and inert abilities the agent wins 82%; on a team built around Intimidate, Protean, Focus Sash, Sitrus Berry, Leftovers and Rough Skin it wins 56%. The gap is the size of what is not modelled, and it says items and abilities are worth more than depth at M8.

Also: `champions/search/payoff.py` is a file the layout in `docs/08-implementation-blueprint.md` section 2 does not list. Payoff estimation is its own concern — common random numbers, roll bucketing, and a one-turn model — and folding it into `oneply.py` would have made the agent the biggest module in the package.

## D29. The policy provider selects from the enumerated action set rather than producing it — 2026-08-29, Claude Code

Context: `docs/08-implementation-blueprint.md` section 3 specifies `PolicyProvider.candidates(state, belief, k)` alongside a separate `actions.enumerate(request)`.

Decision: the interface is `candidates(actions, state, belief, k)` — the legal set is passed in.

Rationale: the two documented interfaces do not compose. A provider handed only the state would have to enumerate the legal set itself, which means reimplementing Choice locks, Encore, disabled moves, target legality and Mega availability — the exact liability section 1 says to avoid by reading the request. `state` and `belief` stay in the signature because implementations B and C need them; the heuristic uses neither and defaults them.

## D30. Every arm in a comparison plays the same team — 2026-08-29, Claude Code

Context: `run_matchup` takes two arms, and `scripts/run_ladder.py` built one on team ALPHA and the other on BETA. `docs/06-coach-and-evaluation.md` and D5 both say team quality is a confound and evaluations hold the team fixed. "Fixed" had been implemented as *each arm always gets the same team*, not *both arms get the same team*.

Finding: max-base-power on BETA beats max-base-power on ALPHA 10 games to 0. The teams are not close. Every head-to-head number this harness has produced across the two teams — including T0.8's acceptance table of random 20% against max-base-power 80% — is a mixture of agent strength and team strength in unknown proportion.

Decision: `run_ladder.py` takes `--team` and gives both arms the same one by default; `run_selfplay` takes `team_a` and `team_b`. Passing different teams still works and prints a warning in the results table. `build_arms` now defaults both arms to ALPHA.

Rationale: an A/B test where the arms differ in two ways measures neither. This was not a subtle effect — it inverted a result. The one ply agent measured 1-9 against max-base-power across the two teams and 17-3 on the same team, and the first number was read as the agent being broken.

Consequences: T0.8's numbers should be treated as unreproduced until re-run on one team, and the entry in `docs/STATUS.md` says so. The teams themselves are not the problem and should not be equalized: ALPHA is a bare team with no items and no stat points, BETA is a real competitive team. Keeping both is useful precisely because the gap between the agent's win rate on each measures what the model does not represent.

## D31. The agent samples its mixed strategy, seeded per decision with a stable hash — 2026-08-29, Claude Code

Context: solving the matrix game yields a distribution over actions. Playing its mode is not playing the equilibrium.

Decision: `OnePlyAgent` draws from the mixed strategy. The draw uses a generator seeded from `sha256(seed, battle_tag, turn)`.

Rationale: Protect, Fake Out and redirection are prediction interactions, and an opponent who learns a deterministic reply beats it from then on — which is the whole reason section 2 says to solve rather than argmax. Seeding per decision rather than from one shared generator means a battle reproduces its own choices regardless of what else the agent played concurrently, and the ladder does run games in parallel.

The hash is `hashlib.sha256`, not the builtin `hash()`. Python randomizes string hashing per process unless `PYTHONHASHSEED` is set, so the builtin would have given the same battle a different draw on every rerun — silently, and only across processes, which is the hardest kind of irreproducibility to notice. `CLAUDE.md` requires that anything not reproducible from a seed is a bug.

## D32. One protocol parser, shared by the corpus and the belief filter — 2026-08-29, Claude Code

Context: M3 needs to turn replay logs into structured behaviour. M5 needs to turn the live protocol log into belief updates. These are the same operation on the same input, and `docs/08-implementation-blueprint.md` section 3 already specifies one interface for it, `parser.apply(state, line) -> list[Observation]`.

Decision: `champions/protocol/parser.py` is written once, at M3, to that signature, and the corpus is a thin driver over it.

Rationale: writing it twice guarantees that the thing trained offline and the thing running online disagree, and the disagreement would be invisible — the corpus would be internally consistent and the live agent would be internally consistent, and only the transfer between them would be wrong. That is the failure mode this project keeps paying to avoid.

Three properties fell out of the shared design and are worth stating because they are contractual rather than incidental:

- Observations carry a monotonic `seq`. The order moves resolve in is the only Speed evidence the protocol ever gives, and `docs/03-belief-filter.md` propagates stat intervals from exactly that. A consumer that stores observations unordered has discarded half the spread inference before it starts, so `reveals` is keyed on `(replay_id, seq)`.
- Attribution is generic. `[from] ability: Drizzle` reveals an ability and `[from] item: Life Orb` reveals an item on whatever message happens to carry the tag, with `[of]` naming the owner. One rule over every line beats twenty rules, one per message type, and it keeps working when Showdown adds a message. It is what catches Protean, Levitate, Life Orb recoil and Leftovers without any of them being enumerated.
- Unrecognised message types are counted in `ParserState.unhandled`, not dropped. Coverage becomes a number a test asserts on. Measured: zero unhandled types across 80 real replays from both formats.

## D33. Open team sheets reveal natures — 2026-08-29, Claude Code

Context: `docs/05-data-pipeline.md` section 5 says stat points *and natures* appear in no public dataset, and calls that split "the boundary of the learnable component": items, abilities and moves predictable from data, spreads only inferrable from play. `docs/03-belief-filter.md`'s two structurally different halves are justified by it.

Finding: the second half of that claim is false. Every set in a forced-open-sheet Bo3 replay carries its nature. Measured over the first 456 sets scraped: 456 natures present, 0 stat point spreads, 0 IVs.

Decision: nature moves into the learnable half. The corpus stores it, and M5's categorical particle prior should predict it alongside item, ability and moves rather than leaving it to interval propagation.

Rationale: it is a free label at scale from the only source that has it, and nature is half of what a spread is. The remaining inference problem is stat points alone, which is strictly smaller and better conditioned than the one the design assumed — a prior over natures narrows the Speed hypotheses that interval propagation has to separate.

Consequences: `docs/05-data-pipeline.md` section 5 and `docs/03-belief-filter.md` need the correction. The sentence to keep is the one about stat points; the one about natures should say the opposite.

## D34. A bring-4 is only a label when all four appeared — 2026-08-29, Claude Code

Context: M4 predicts the bring-4 from the six at team preview. The replay log's only witness to a bring is a Pokemon actually taking the field.

Decision: `previews.appeared` records who played, `replays.p1_teamsize`/`p2_teamsize` records how many were brought, and `replays.bring_fully_observed` is set when the two agree for both sides. M4 trains on the flagged subset.

Rationale: a game won before the fourth Pokemon ever switched in yields three, and three is not a truncated four — it is a different label. Training on it teaches the predictor that players bring three. Measured on the first 80 replays, 68% of games are fully observed, so the flag costs about a third of the corpus and the alternative costs correctness.

Consequences: the corpus is larger than the bring-4 training set by design, and the gap is a reported number rather than a silent filter. Games that are not fully observed are still complete evidence for everything else — leads, actions, reveals and open sheets are all unaffected.

## D35. Raw logs are the source of truth; the database is derived — 2026-08-29, Claude Code

Context: `docs/05-data-pipeline.md` section 2 requires preserving the raw log alongside the parsed form, "because the parser will be wrong at first and re-parsing beats re-scraping".

Decision: logs are written to `data/replays/<format_id>/<replay_id>.log` and never re-fetched once present. Every derived table is deleted and rewritten per replay on upsert, so parsing is idempotent, and `scripts/scrape_replays.py --reparse` rebuilds the entire corpus from disk with no network access. `replays.parser_version` records which parser produced the rows.

Rationale: this makes being wrong about the parser cost nothing but CPU, which is the correct price given we should assume we are. It also decouples the two failure modes completely: a scrape can be interrupted at any point and a parse can be wrong at any point, and neither can damage the other. The scraper's resumability is a consequence rather than a feature — state lives in the store, so a killed run loses at most one replay.

## D36. Preview models use species only, even though the corpus knows more — 2026-08-29, Claude Code

Context: the Bo3 open-sheet corpus carries items, abilities, moves and natures for every Pokemon (D33). The bring-4 and lead predictors could use all of it.

Decision: every feature is a function of the twelve species names and the resolved dex. Nothing reads an item, an ability, a move or a nature.

Rationale: Champions has no open team sheets, so at preview the agent knows six names per side and nothing else. A model fitted on set information would score well offline and be unusable in the game it was built for -- the exact failure mode `CLAUDE.md` names when it says Showdown is a proxy and Champions is the target. The open-sheet corpus is a source of *labels*, never of inputs, and the separation is the same one D2 draws for play.

Consequences: `tests/test_preview.py` checks that a feature row is a pure function of the names it is given. The constraint is also why the predictors are weaker than they would otherwise be, which is the correct trade.

## D37. The preview needs its own value model, not `search/evaluate.py` — 2026-08-29, Claude Code

Context: `docs/04-decision-engine.md` section 6 says the trained evaluation function supplies each cell of the 15 x 15 preview matrix at negligible cost.

Finding: it cannot. `champions/search/evaluate.py` is a function of a state snapshot, and at team preview there is no state. Every feature it reads -- HP fractions, survivors, speed control, field conditions -- is identical for both sides before the first turn, so it returns 0.5 for every pairing. A constant payoff matrix has every strategy as an equilibrium, which is a polite way of saying it has no answer.

Decision: `champions/preview/value.py` is a separate model, fitted on the corpus to `P(win | our four, their four)`. `solve_preview` takes the value function as an argument rather than importing one, so a better one drops in without touching the solver.

Rationale: the two questions are different. Mid-battle evaluation asks what a position is worth; preview evaluation asks what a matchup is worth before anything has happened. Sharing an interface between them would have forced one of the two to be wrong.

## D38. A separable preview value makes the equilibrium an argmax — 2026-08-29, Claude Code

Context: the first preview value model was `sigmoid(g(ours) - g(theirs))`, which is antisymmetric and therefore a legitimate zero sum payoff.

Finding: it is also separable, and a separable payoff has no game in it. The best bring-4 is the same against every column, the 15 x 15 has a dominant row, and the exact solve returns a pure strategy that sorting would have found for free. Measured: one distinct best response across all fifteen of the opponent's options.

Decision: the value model carries interaction features -- our four's type coverage into their four, and the fraction of their four we outspeed -- entering as `h(a, b) - h(b, a)` so antisymmetry survives. With those, the payoff depends on the pairing and the equilibrium can mix.

Rationale: `docs/04` section 6 argues the preview is worth solving exactly *because* it is a game. If the payoff has no interaction term then it is not one, and the whole section's premise fails quietly rather than loudly. A test now pins this down in both directions: a separable value must produce a single best response, an interacting one must produce several.

## D39. M4's models are reported as measured, including the one that does not work — 2026-08-29, Claude Code

Context: M4 produces three models. Two are useful and one is not.

Measured on held-out series, corpus of about 2,300 replays:

- Leads: top-1 38.5% [35.1%, 41.9%] against a uniform 16.7%, log loss 1.616 against 1.792. It survives restriction to games where neither player was seen in training (33.1%), so it is a fact about species rather than about players.
- Bring-4: top-1 9.4% [7.5%, 11.6%] against a uniform 6.7%, and on unseen players 6.1% [3.2%, 11.2%] with a log loss slightly *worse* than uniform. Training top-1 is 10.5%, so this is not overfitting -- the signal is not in species-only features.
- Preview value: held-out accuracy 46.2% and log loss 0.727 against a coin flip's 0.693. It is worse than useless out of sample while reaching 61% in training.

Decision: all three are reported with intervals and baselines, the value model is not wired into anything, and the equilibrium keeps its value function as a parameter.

Rationale: the control settles the interpretation. Over 1,808 rated games the higher-rated player won 57.4%, so these outcomes *are* predictable -- by skill, which the preview features do not observe and cannot. The honest conclusion is not "the model is weak" but "at this sample size, on this ladder, bring-4 composition does not determine the game, and player strength does". Publishing 46.2% as a result would be worse than publishing nothing; suppressing it would be worse still, because M6 is going to ask the same question with the same corpus and deserves to know the answer already found.

The asymmetry between leads and bring is itself the finding. Leads are species-intrinsic -- a Fake Out user, a weather setter and a Trick Room setter each have a role that does not depend much on the opponent -- so a species main effect captures them. The bring-4 is a matchup decision that depends on what the four will actually do, which means items and abilities, which preview never reveals. That is the same boundary M2 found from the other side, where the agent's advantage collapsed from 82% to 56% on a team whose items and abilities its model did not represent.

## D40. Nature is drawn from the prior; interval propagation carries stat points alone — 2026-08-29, Claude Code

Context: `docs/03-belief-filter.md` section 2 proposes keeping the 25 natures as discrete hypotheses per Pokemon, each carrying its own interval set, because `docs/05-data-pipeline.md` section 5 says stat points *and* natures appear in no public dataset.

Finding: the second half of that premise is wrong, and D33 already recorded why — every set in a forced-open-sheet Bo3 replay carries its nature, measured at 4,392 of 4,392 when D33 was written and at 50,352 of 50,352 now.

Decision: a nature is drawn with the set, from the learned prior, alongside item, ability and moves. Each particle therefore fixes one nature and carries one `SpreadBelief` per species conditioned on it, and the interval layer's only remaining unknown is the six point values.

Rationale: the two designs cost the same to run and differ in what they can learn. Twenty-five interval sets per Pokemon can only be pruned by observation; one nature drawn from 50,000 labelled sets starts at the right answer most of the time and is *then* pruned by observation. The nature posterior comes out of the particle weights for free, so nothing is lost.

Consequences: the categorical and interval halves are no longer independent — a particle's spread box is only meaningful given its nature — which is why a resample redraws the sets and replays the soft evidence onto the new ones rather than carrying the old boxes across.

## D41. Reveals are hard, inference is soft, and a particle is never deleted by a damage figure — 2026-08-29, Claude Code

Context: `CLAUDE.md` constraint 5 says opponent HP arrives quantized to percent, that damage-based inference carries about plus or minus 0.5% of maximum HP of error, and that treating derived bounds as hard will eliminate the true hypothesis. `docs/03` section 5 makes interval coverage the metric that catches it.

Decision: a revealed move, item or ability is a hard filter — particles that contradict it are dead. A Speed ordering or a damage figure is a likelihood: a particle it cannot explain has its weight multiplied by 0.05 and keeps its place in the population.

Rationale: the two kinds of evidence have different epistemic status. "Greninja used Blizzard" is a fact about the protocol. "Their Special Attack must be at least 130" is a conclusion from a quantized reading, a partial effects table, and an assumption that nothing unmodelled intervened — and any of the three can be wrong. A factor of 0.05 drives a genuinely wrong hypothesis to irrelevance in two observations while leaving one bad reading survivable.

Consequences: measured over 12 self-play traces against a known team, the maintained interval contains the true point value 97.8% of the time for the box the search reads and 99.3% for the union over particles. Both are below the nominal level, and both are reported as measured rather than tuned until they looked right. The honest reading is that the residual is unmodelled effects rather than quantization, so the fix is a larger effects table rather than a wider tolerance.

## D42. Champions changes which items are legal, not what they do — 2026-08-29, Claude Code

Context: `CLAUDE.md` constraint 1 says roughly 250 moves and 250 items carry overrides in the `champions` mod, and D26 found that the *moves* half is exactly as dangerous as it sounds — the damage formula is unchanged and every input to it is different.

Finding: the items half is the opposite shape. `data/mods/champions/items.ts` is 1,046 lines and every entry but one is `inherit: true` plus an `isNonstandard` toggle; the sole mechanical change in the file is White Herb's Parting Shot desync fix, which is not a damage effect. `abilities.ts` is the same: Anger Shell, Berserk, Disguise, Healer, Natural Cure, Regenerator and Unseen Fist have handler changes and none of them is a damage multiplier.

Decision: `champions/belief/effects.py` applies mainline multipliers — Life Orb at 5324/4096, type-boosting items at 4915/4096, resist berries at 0.5, Choice Scarf at 1.5x Speed — to the surviving pool, and `tests/test_belief.py` re-derives every table from the pinned vendored source and fails if they disagree.

Rationale: what Champions changed about items is the *pool*, and the pool change is large: 148 items survive, and Choice Band, Choice Specs and Assault Vest are not among them. Reading "256 modified items" as "modified mechanics" would have made this table impossible to write; reading it correctly makes it a transcription a test can check.

Consequences: a Showdown bump that changes an item's multiplier is a failing test rather than a quietly different damage number. D26's warning about mainline calculators still stands for moves and stats, where the real risk always was.

## D43. An ability is only "unmodelled" if it could change a number the protocol does not announce — 2026-08-29, Claude Code

Context: the belief filter widens its damage tolerance when a hypothesised item or ability falls outside `effects.py`'s tables, because an unmodelled multiplier is a real possibility rather than a rounding error.

Finding: treating every unrecognised ability that way made almost every particle carry the wide tolerance, and the spread layer narrowed no interval at all across a whole battle — measured at a mean width of 32.0 of 32 points, which is the prior. The cause is that most abilities do not multiply damage at all, and the ones that change the game most visibly — Intimidate, Protean, Competitive, Snow Warning — announce themselves in the protocol as a boost, a type change or a weather message.

Decision: `DAMAGE_AFFECTING_ABILITIES` and `DAMAGE_AFFECTING_ITEMS` are derived from the pinned Showdown source by which damage handlers each definition touches — 139 of 320 abilities and 109 items — and only a hypothesis that is outside our tables *and* inside those sets widens the tolerance. Both sets are re-derived and compared in the test suite.

Rationale: this is the same distinction the trace draws everywhere else between "not known" and "not emitted". An ability the protocol tells us about is not an unknown, and treating it as one throws the announcement away.

Consequences: mean interval width fell from 32.0 to 30.2 of 32 points at 97.8% coverage. Narrowing further is a matter of modelling more of the 139, and each one modelled shows up in exactly this measurement.

## D44. Priority-modifying abilities make an ordering unusable, so it is skipped rather than widened — 2026-08-29, Claude Code

Context: the only Speed evidence the protocol gives is the order `|move|` lines resolve in (D32), and the inference reads a same-priority ordering as an inequality between two effective Speeds.

Finding: Prankster raises a status move's priority by one, Gale Wings does the same for a Flying move at full HP, and Quick Draw does it at random. Reading any of those as a Speed inequality bounds the wrong quantity, and no tolerance is wide enough to make a whole priority bracket safe. Measured: adding the guard moved interval coverage from 95.9% to 97.8%.

Decision: when a particle hypothesises a priority-modifying ability and the move used is one that ability affects, that particle draws no bound from the ordering. Skipped, not down-weighted — nothing was contradicted, there is simply no inequality available.

Rationale: down-weighting would punish a hypothesis for being consistent with the evidence, which is backwards. The distinction between "this hypothesis is unlikely" and "this observation is uninformative about this hypothesis" is one a particle filter exists to keep straight.

## D45. Mega Evolution is a base-stat change, and the belief has to follow it — 2026-08-29, Claude Code

Context: Mega Evolution is back in Champions and 75 Mega Stones are legal (`docs/02-mechanics-deltas.md`), so a mega-evolved Pokemon is the common case rather than an exotic one.

Finding: three separate things break if the belief keys everything on the base species. Greninja-Mega has 142 base Speed against Greninja's 122 and 133 Special Attack against 103, so every damage and Speed bound drawn about it is wrong in the same direction. Gengar-Mega's ability is Shadow Tag whatever Gengar was registered with, so attributing it to the base species makes every particle inconsistent at once. And poke-env keeps reporting the base forme's base stats in the state snapshot, so the payoff model has the same problem independently.

Decision: the belief keys particles on the base species — one Pokemon is one entry, and its stat points do not change when its forme does — and reads base stats from whichever forme is currently on the field. `SpreadBelief.stat_at` takes an optional base-stat override for exactly this. A revealed ability is accepted as a constraint only if it is one the base species can legally have.

Rationale: points belong to the set and base stats belong to the forme, and conflating them is what made the filter confidently wrong. Keeping one interval box per Pokemon rather than one per forme is what lets evidence from before and after the Mega Evolution accumulate on the same hypothesis.

Consequences: the snapshot's `base_stats` being the base forme's is a separate and still-open problem that predates M5 and affects the M2 agent too. It is recorded as an open question rather than worked around here.

## D46. Six independent midpoints are not a spread — 2026-08-29, Claude Code

Context: the search needs one number per stat, and the belief maintains an interval per stat.

Finding: taking the midpoint of each interval independently produces an allocation that is usually illegal. Unconstrained, each midpoint is 16 and the six sum to 96 against a budget of 66 — an opponent half again bulkier, faster and stronger than any legal set, which is the same failure `ASSUMED_POINTS = 32` chose deliberately and which this whole layer exists to replace. A test caught it; nothing in the output would have.

Decision: `SpreadBelief.allocation()` starts at the lower bounds — which the resource constraint guarantees are affordable together — and distributes the remaining budget in proportion to each stat's slack. Unconstrained that is 11 in every stat, which is the mean of a uniform allocation; as evidence narrows one stat, the budget it frees moves to the others.

Rationale: the point of the interval layer is that `sum(p) <= 66` couples the six. An estimator that ignores the coupling has discarded the only thing that made the representation worth maintaining.

## D47. The belief filter lives in the base agent; consuming it is a separate agent — 2026-08-29, Claude Code

Context: `TracingPlayer` owns the whole observability surface so that a new agent gets it without opting in (D13). The belief is the most useful debugging surface in the system, and it is also a decision input.

Decision: `TracingPlayer` runs the filter and emits the `belief` event for every agent, degrading to no belief when the dex or the prior is missing. `OnePlyAgent` does not read it: its `_turn_model` and `_opponent_candidates` are the M2 ones, so its measured numbers still mean what they said. `BeliefAgent` overrides exactly those two and nothing else.

Rationale: the two agents have to be runnable against each other on the same team, on the same seed, in the same process, because that head-to-head is the only measurement that says whether M5 bought anything. A flag on one class would have made "the same agent with the belief off" and "the M2 agent" two things that were hard to keep identical.

Consequences: `champions/search/payoff.py` and `champions/search/policy.py` have no import of `champions.belief` in either direction. `EffectsProvider` is structurally typed and defaults to a no-op that preserves M2's arithmetic exactly, which `tests/test_payoff.py` and `tests/test_oneply.py` still check by continuing to pass unchanged.

## D48. M5's win rate result is reported as measured, including the interaction that costs — 2026-08-29, Claude Code

Context: M5 supplies exactly what M2 identified as the missing piece. D30 measured the one ply agent at 82% against max-base-power on a team with no items, no stat points and inert abilities, and 56% on a team built on Intimidate, Protean, Focus Sash, Sitrus Berry, Leftovers, Rough Skin, Competitive and a Mega, and concluded that items and abilities were worth more than search depth. D39 reached the same boundary from the corpus side.

Measured on `regmb-beta`, both arms on the same team, 50 games, seed 1:

| agent | vs max-base-power | 95% CI |
| --- | --- | --- |
| one-ply (M2) | 58.0% | [44.2%, 70.6%] |
| belief, stats and effects only | 58.0% | [44.2%, 70.6%] |
| belief, believed action columns only | 58.0% | [44.2%, 70.6%] |
| belief, both | 44.0% / 46.0% | [31.2%, 57.7%] / [33.0%, 59.6%] |

and `belief` against `oneply` directly: 52.0% [38.5%, 65.2%].

Decision: the ablation arms stay in `scripts/run_ladder.py` as first-class entries, the numbers go in `docs/STATUS.md` unrounded and with intervals, and no knob is tuned to make the combined arm look better.

Rationale: a single head-to-head could not have said which half moved the number, and the answer turns out to be neither. Each half alone is exactly neutral; the two together are worse. The combined arm was run twice, on two separate local Showdown servers, and produced 44.0% and 46.0%, so it reproduces. The intervals all overlap at 50 games, so this is a direction rather than a fact -- and the direct head-to-head being a dead heat says the effect is real and small rather than large.

The mechanism is a hypothesis, not a finding. With one "no action" column the M2 agent was effectively an argmax, and an opponent model cannot mislead an argmax. With believed columns it hedges, and with believed stats it hedges against a *specific* wrong opponent -- and hedging against a confidently wrong model can be worse than not hedging. The common factor is the payoff: one analytic turn that models no secondary effects, no status, no healing and no accuracy. A more precise opponent inside a coarse model is not obviously an improvement.

Consequences: this reverses the reading of D30. The binding constraint is no longer the information the search has about the opponent -- M5 supplies it and the win rate did not move -- so the next candidate is the payoff model itself, which is what M6 fits and what M8 was going to weigh depth against. The belief filter is also precisely what makes the simulator-backed alternative available: stepping `js/sim_server.js` requires a complete opponent team, and a particle is one. The thing built here to raise the win rate may turn out to earn its keep as the input to the thing that does.

## D49. Forfeiting is the one thing the viewer may say to a running agent — 2026-08-29, Claude Code

Context: `champions/viewer/server.py` opens by claiming the viewer cannot perturb play, and that this is a property of the architecture rather than a rule: the server tails trace files, holds no `Player`, and has no path to Showdown. The supervisor in `champions/viewer/control.py` sits beside that and can start and kill runs. Until now killing was the only way out of a game that had gone long or gone nowhere, and it takes every remaining game in the run with it.

Decision: a run is spawned with a stdin pipe and `--control-stdin`, and the supervisor can write exactly one word to it. `champions/agents/commands.py` reads it on a daemon thread and schedules `TracingPlayer.forfeit_active()` onto poke-env's loop. `POST /api/run/forfeit` exposes it; the page shows **forfeit game** beside **stop run** while a run is going.

Rationale: conceding does not compromise the property the module claims. The viewer still cannot influence *how* the agent plays — the channel carries one verb, that verb ends a battle rather than choosing inside one, and the supervisor could already kill the process outright, which is strictly blunter. A wider protocol would be a different decision and is deliberately not taken.

Consequences: `--control-stdin` is opt in, so a run started by hand behaves as before. "Play the bot" now defaults to three games rather than one, because with a single game the forfeit and the end of the run are the same event. A forfeit is no longer counted as a protocol failure by `scripts/selfplay.py`: nothing in the agent concedes by accident, so `forfeited` is collected separately from `[Invalid choice]` and the inactivity timeout.

## D50. A finished battle is never decided — 2026-08-29, Claude Code

Context: found by forfeiting. Showdown can hand out a request and then end the battle underneath it — we concede, the other side concedes, or an inactivity timer fires — and poke-env dispatches the request it already had.

Finding: the agent answered it. A conceded battle's trace carried `battle_end` at seq 32 followed by a full turn of `turn_start`, `belief`, `candidates`, `timing` and `equilibrium`, which is invalid by `champions/trace/validate.py`'s own rule that the last event is `battle_end`, and which the viewer renders as a turn that never happened. The agent then sent `/choose` into a room it had left, and Showdown answered with a popup.

Decision: `TracingPlayer.choose_move` returns poke-env's `_EmptyBattleOrder` before emitting anything when `battle.finished`. An empty message is the one poke-env declines to send at all, so the popup goes with it.

Rationale: this is not a forfeit bug. Forfeiting only made it easy to hit, because it ends a battle at an arbitrary moment rather than at a turn boundary. The same race exists whenever a game ends while a decision is in flight, and M6 was about to read these traces as training data.

## D51. The evaluation counted our six against their four — 2026-08-29, Claude Code

Context: M6 began by fitting `champions/search/evaluate.py`'s features to outcomes. Before fitting anything, the features were scored on a real turn-1 position.

Finding: **it returned 0.996 on a dead-even opening position.** Reg M-B registers six Pokemon and brings four, poke-env keeps all six in `battle.team` for the entire game, and `_alive` read `side["remaining"]` for our side — six — against an opponent derived as the bring minus observed faints — four. Every material feature carried a constant two-Pokemon offset in our favour.

Decision: `champions/protocol/state.py` records `selected` on each of our Pokemon, `evaluate._in_play` filters both sides to the Pokemon that can actually take part, and `champions/search/positions.py` refuses a trace written before that field existed rather than fitting it.

Rationale: the interesting part is that fitting would have hidden it. A logistic regression with a free intercept absorbs a constant offset into the intercept and reports a perfectly reasonable-looking log loss, and the bug would have survived M6 as a coefficient. It is caught instead by fitting *without* an intercept and reporting one as a diagnostic: on the corpus the diagnostic intercept is +0.0000, which is the check that the features are the antisymmetric differences they claim to be.

Consequences: `IS_CALIBRATED` is no longer a constant. It is True exactly when `data/eval/weights.<format>.json` exists, which is written by the same run that writes `docs/eval-calibration.md`, so there is no way to assert calibration without having measured it.

## D52. A weight is kept only when its source settled the sign — 2026-08-29, Claude Code

Context: M6 fits two sources. Self-play is preferred because ladder outcomes are skill dominated (D39), and the corpus is kept as the check.

Finding, in three parts, all measured:

1. The first self-play fit, over 150 battles, ranked positions *better* than the corpus fit (AUC 0.793 against 0.760) and scored a worse held-out log loss than a coin flip — 0.7175 against 0.6931 — because 150 battles leaves 23 in the test split and the Platt scaling was fit on 23 more. Ranking and calibration are different claims.
2. At 750 battles that reversed: 0.5301 against 0.6931, ECE 0.0207, AUC 0.8043.
3. Even at 750, self-play settled only three of the seven weights. Neither checked-in team carries Tailwind or a hazard move, so two features are constant across the entire source. `status_advantage` varies on 291 rows out of 11,774, because burn is the only status that matchup inflicts, and came out at **-1.34** — the sign that says being burned is good — with a bootstrap interval of [-3.35, +0.83]. `boost_advantage` came out confidently negative, [-0.95, -0.11], against the corpus's [+0.098, +0.137] over 17,500 battles; the mechanism is Competitive on Milotic, which makes a large positive boost total on our side usually the *consequence* of the opponent landing Intimidate or Icy Wind.

Decision: `fit.bootstrap_weights` resamples **battles** with replacement and reports a 95% interval per weight. A weight whose interval spans zero is one the source did not settle, and is taken from a source that did. A weight both sources settle *with opposite signs* goes to the source with at least ten times the battles. The blend is then re-calibrated and re-measured, so the reliability diagram describes the model that ships.

Rationale: the alternative is a threshold on how often a feature is nonzero, which is a number with no defence. The interval is a measurement, it subsumes the constant-column case as the degenerate one, and it turns "this weight is not trustworthy" from a judgement into an output. Resampling battles rather than rows is the whole point: positions inside a game are near duplicates and a row-resampler reports an interval roughly twenty times too narrow.

Consequences: the shipped model takes `hp_advantage`, `pokemon_advantage` and `active_hp_advantage` from self-play and `status_advantage`, `speed_control` and `boost_advantage` from the corpus, and beats either source alone on the self-play test split (log loss 0.5264 against 0.5305 and 0.5894, AUC 0.8061). `hazard_advantage` ships at zero, undetermined by 750 self-play battles and by 25,000 corpus ones alike, which is itself the finding: entry hazards do not measurably predict the outcome of a Reg M-B doubles game.

## D53. The calibration gets a slope and no offset — 2026-08-29, Claude Code

Context: the fit deliberately has no intercept, because every feature is a difference between the two sides, so a dead-even position is the zero vector and must score 0.5 — which is what lets the matrix game treat the payoff as zero sum.

Finding: textbook Platt scaling is `a * x + b`, and fitting the `b` put the intercept straight back. It came out at +0.074 and an even position scored 0.518. `tests/test_evaluate.py::test_a_symmetric_position_is_a_coin_flip` caught it.

Decision: `fit.calibrate` fits the slope alone. The offset is still computed and stored as `platt_offset_diagnostic`, never applied, for the same reason `free_intercept` is.

Rationale: a slope fixes systematic over- or under-confidence, which is the failure calibration exists to address. An offset encodes "one side wins more often", which is meaningless over antisymmetric features and destroys a structural property the search depends on. The general lesson is that a structural invariant has to be defended at every stage that can reintroduce it, not only at the one that was thinking about it.

## D54. The pruning guard is a harness, not a function, and it says the heuristic is the problem — 2026-08-29, Claude Code

Context: `docs/04-decision-engine.md` section 3 permits candidate pruning on one condition — that it never drops an action that is uniquely correct — and says to check it offline by solving the unpruned game and recording how often its equilibrium places non-trivial mass on a discarded action. `policy.discard_rate` implemented that for one position at M2. It had never been called on a real position, and the entry had been carried in `STATUS.md` for five sessions.

Decision: `champions/search/discard.py` reads decisions out of agent-view traces rather than replaying games. A trace already carries the three inputs the measurement needs, in the form the agent saw them: `turn_start.state` is the snapshot the search evaluated, the unpruned `candidates` event is the full legal joint set enumerated from the request, and the pruned one is the opponent columns and the surviving rows. `scripts/discard_rate.py` (`make discard`) sweeps `k` and writes `docs/pruning-guard.md`, which stands to the policy layer as `docs/eval-calibration.md` stands to the evaluation.

Three choices inside it are load bearing:

- **Intervals over battles**, the same argument `fit.bootstrap_weights` makes (D52). Positions inside one game share a board, a team and a policy. A self-play directory also holds both viewpoints of each game under one battle id, so grouping on it is what stops the two halves of a game counting as independent.
- **Value loss is reported beside mass.** Mass is all or nothing: a discarded row worth 0.9001 against a kept row worth 0.9000 scores the same 1.0 as a discarded row that wins outright. Section 3's threshold cannot tell those apart and the number is unreadable without something that can.
- **The kept set is re-derived from the policy, not read off the trace.** Sweeping `k` requires re-deriving it anyway, and the agreement at the trace's own `k` is then a check that the harness is measuring the selection the agent actually ran. It disagreed on 0 of 6,745 positions.

Finding, over 11,774 traced decisions from 750 self-play battles, at the agent's own `k = 10`: the heuristic discards equilibrium mass on **64.2%** of positions, mean discarded mass **0.639** 95% [0.628, 0.650], giving up a mean 0.061 win probability and up to 0.580 in the worst case. On the same positions k=5 discards 0.807, k=15 discards 0.519 and k=20 discards 0.320. Positions with more than one opponent column are worse, not better: 0.811 at two columns against 0.608 at one.

Rationale for reading this as a verdict on the heuristic rather than on pruning: the rows the equilibrium wants and the policy drops are, on a 30-trace sample, 98 of 100 pure move joints, spread across heuristic ranks 10 to 22 and beyond. Base power is not the ordering the payoff model computes. And the implemented `HeuristicPolicy` scores nothing that depends on the position — not the defender, not typing or bulk, not speed, not what is threatened — while section 3 specifies an A that tests all four. The measurement is of a policy weaker than the design's.

Consequences: M7's baseline is now a measured quantity instead of an assumption, and the benchmark it needs is half built — `discard.measure` takes a `keep` callable, so a second provider is one argument. It also reframes M7: the first task is to build the A section 3 actually specifies, because comparing a learned prior and a language model against a policy that misses the answer two thirds of the time would flatter both. The gap between the specified and implemented A is filed as an Open Question rather than closed here, since which one section 3 means is Cowork's to say.

## D55. The specified policy A is the intended A, and gets built before M7 benchmarks anything — 2026-08-29, Claude Code

Context: D54 measured the implemented `HeuristicPolicy` discarding equilibrium mass on 64.2% of positions and filed the gap between it and `docs/04-decision-engine.md` section 3 as an Open Question, since which A section 3 meant was Cowork's to say. Alex answered it directly in a Claude Code session.

Decision: section 3's A stands as written. Implementation A is rebuilt to test the position — a move that knocks out a target on an average roll, Protect when the slot is threatened, speed control when it flips an outspeed, Fake Out on turn 1 — before the M7 provider benchmark runs. `docs/04` section 3 is unchanged; the implementation moves to meet it.

Rationale: the discarded rows are 98 of 100 pure move joints sitting at heuristic ranks 10 through 22, which says base power is not the ordering the payoff model computes — the baseline is broken rather than merely cheap. Benchmarking a learned prior and a language model against a policy that misses the answer two thirds of the time would flatter both by construction, and the benchmark harness (`discard.measure`, which takes a `keep` callable) already exists, so the cost is a day of implementation rather than a day of scaffolding.

Consequences: every discard-rate number on record — `docs/pruning-guard.md` in full — describes the old policy and becomes the *before* half of a pair rather than the baseline M7 reports against. The guard has to be re-run on the rebuilt A before the provider comparison means anything.

## D56. The preview gets a separability test before it gets 4,500 battles — 2026-08-29, Claude Code

Context: `docs/04-decision-engine.md` section 6 says the trained evaluation supplies each preview cell; D37 established it cannot, because there is no state at preview. The replacement source recommended out of M4 was self-play, roughly 4,500 battles for a full 15 x 15 at 20 games a cell. Section 6 also calls the preview the highest ratio of win rate to engineering effort in the project, which D38 showed rests on the payoff having an interaction term.

Decision: measure separability first, on a coarse grid, before committing to the full matrix. If the payoff is separable the preview is an argmax rather than a game and section 6 collapses to the lead predictor, which already works and transfers.

Rationale: separability is the precondition for section 6 being true at all, and it is far cheaper to test than to assume. The failure mode is silent — the solve returns a confident pure strategy whether or not there is a game to solve — so nothing downstream will report that the 4,500 battles bought an argmax.

Consequences: the full self-play matrix is not scheduled. If the coarse grid shows an interaction term, it is; if it does not, section 6 is rewritten around the lead predictor and the preview stops being treated as cheap upside.

## D57. Traces are compressed per battle, and the joint list is never thinned — 2026-08-29, Claude Code

Context: traces run 363 KB per battle for the one-ply agent against 289 KB for max-base-power, because both the unpruned and pruned `candidates` events are emitted in full. `docs/07-observability.md` section 5 specifies rotation and compression per battle; nothing implemented it, on the expectation that M2's pruning would remove the volume. It added to it.

Decision: gzip each trace when its battle closes. The reader path accepts plain and gzipped alike so live tailing and replay stay one code path. The unpruned `joint` list is not truncated, sampled or dropped.

Rationale: compression is now worth doing on its own merits rather than waiting for a reduction that is not coming, and M7's three-provider benchmark is about to multiply the run count. The constraint on *how* is the load-bearing half: `champions/search/discard.py` reconstructs the unpruned game from the `joint` list, so thinning the event to save space would silently disable the pruning guard. The harness refuses a trace already marked `truncated`, but it cannot refuse one whose list was never written. Compress the file; do not thin the event.

Consequences: `docs/07` section 5 is satisfied. Any future volume work has to come from compression or retention, not from emitting less.

## D58. The belief head-to-head is deferred until the payoff model improves — 2026-08-29, Claude Code

Context: M5 left `belief` against `oneply` at 52.0% [38.5%, 65.2%] over 50 games — a dead heat with an interval too wide to read. The open entry proposed several hundred paired games to settle it.

Decision: not now. The belief stays available and stays off by default. The head-to-head is re-run after M7 and M8, on whatever payoff model those leave behind.

Rationale: the hypothesis M5 raised is that hedging against a detailed opponent model *inside a coarse payoff model* is worse than not hedging. If that is right, the quantity being measured is a property of the payoff model, and measuring it now dates the answer to a model about to be replaced. The binding constraint has already moved from what the search knows to what its one-turn payoff can do with what it knows.

Consequences: M5's numbers stay as they are, and stay unresolved, which is the honest state. The `run_ladder.py` and `selfplay.py` username-collision fix is no longer on the critical path for this, though it remains a defect worth fixing before any high-n run.

## D59. Tournament team lists are cut from the data pipeline — 2026-08-29, Claude Code

Context: `docs/05-data-pipeline.md` section 3 specifies scraping tournament team lists from RK9 and Victory Road. M3 delivered sections 1, 2 and 6 and left it unbuilt. The case for it was joint distributions over set composition at tournament level.

Decision: cut section 3. The Bo3 forced-open-sheet corpus supplies joint distributions over whole registered sets already.

Rationale: the corpus population is weaker than a tournament field, but it is far larger, it is already built and parsed, and it carries no scraping-terms question. The remaining argument for section 3 was population quality alone, which is not worth a second scraper and a second legal question at this stage.

Consequences: `docs/05` loses a section. Reversible at any point if the corpus population turns out to matter — nothing depends on section 3 having been cut.

## D60. The Showdown client is not vendored; the entry is closed rather than carried — 2026-08-29, Claude Code

Context: D16 decided against vendoring `smogon/pokemon-showdown-client` so a real client could be embedded in the viewer rather than opened beside it. The Open Question carried it as "worth revisiting only if the rendered stage turns out to be insufficient in practice."

Decision: close it. The condition for revisiting has not been met and the entry is not a question anyone is waiting on.

Rationale: the viewer renders its own stage and embeds Showdown's published renderer against the protocol log, which has been sufficient through six milestones and browser QA. The costs D16 named — a second pinned repository and a Node build step, against two `CLAUDE.md` conventions — have not fallen.

Consequences: none, beyond a shorter Open Questions list. If the stage does turn out insufficient, that is a new entry with a real trigger behind it rather than a standing invitation.

## D61. Implementation A is rebuilt against the position, and the old one is kept as the baseline — 2026-08-29, Claude Code

Context: D55 decided that `docs/04-decision-engine.md` section 3 stands as written and that implementation A is rebuilt to meet it before M7 benchmarks anything. The shipped A ranked joint actions by base power and never read the board; `docs/pruning-guard.md` measured it discarding equilibrium mass on 64.2% of positions at the agent's own `k`.

Decision: `HeuristicPolicy` now reads the snapshot and computes damage with the M1 layer — a move that knocks a target out on the average of the sixteen rolls, Protect when a revealed opponent move threatens half the slot's remaining HP, speed control when it flips a race we are currently losing, Fake Out on the turn its user came in, plus the switches unconditionally. The old policy is kept as `BasePowerPolicy` rather than deleted, and both are measured in the same run.

Rationale: keeping the old one costs a hundred lines and buys two things that are hard to get any other way. Every number written before this change describes it, so `docs/pruning-guard.md` reports a comparison rather than an unexplained improvement; and the 1,500 self-play traces the guard reads were produced by it, which makes it the only policy whose re-derived candidate set can be expected to agree with what those traces recorded — the guard's own validity check.

Measured, over the same 6,745 positions and 750 battles, at the agent's own `k = 10`:

| | discarded mass | 95% | nonzero | mean value loss | worst |
| --- | --- | --- | --- | --- | --- |
| `heuristic-base-power` | 0.6391 | [0.6283, 0.6499] | 64.2% | 0.0607 | 0.5799 |
| `heuristic-position` | 0.1743 | [0.1656, 0.1846] | 18.1% | 0.0078 | 0.3888 |

The specified A at `k = 10` discards less than the old one at `k = 20` (0.320), so this buys more than doubling the budget would have. Cost is 0.67 ms against 0.11 ms per decision on the widest positions in the corpus, on a decision M2 measured at about 11 ms against a 45 second budget.

Three readings of section 3 are decisions rather than transcription, and are recorded here because a later reader would otherwise have to infer them from the code:

- **"Knocks out on an average roll" is the mean of the sixteen rolls**, exact rather than sampled, and damage enters the score as a fraction of the target's *remaining* HP rather than as a raw number. A knockout is a step on top of that fraction, because the difference between 99% and 100% of a target's HP is the whole value of the turn and no continuous function of damage says so.
- **Icy Wind and Electroweb get the speed-control conditional even though they are Special.** They are the speed control doubles actually plays; scored purely as attacks, the conditional section 3 asks for would never fire on the moves it most obviously means.
- **Damage to our own slots subtracts.** Earthquake aimed at a foe is not friendly fire and stays legal, but a partner it kills is a real cost the base-power ranking could not see. Friendly fire proper — a single-target damaging move aimed at our own slot — stays disqualified.

Consequences: `champions/protocol/state.py` gains `first_turn` per Pokemon, because Fake Out's condition is derivable from the turn number only on turn 1 and wrong after every switch; traces written before it fall back to the turn number. The threat model is the opponent's *revealed* moves only, so an unrevealed move cannot make a slot look threatened — the same honest gap `opponent_candidates` has, with the same answer available from the belief filter and not yet plumbed through. The one-ply agent now takes its snapshot before pruning rather than after.

## D62. The pruning guard measures every provider against one solve of one position — 2026-08-29, Claude Code

Context: `champions/search/discard.py` took a single `keep` callable of `(actions, k)`. Section 3 requires the guard to be reported per implementation and to be part of the benchmark rather than an afterthought, and M7 puts three providers through it.

Decision: `KeepFn` gains the snapshot, and `measure_many` takes either one callable or a mapping of name to callable. Every policy in that mapping is measured against the same payoff matrix, and `Measurement` and `Summary` carry which policy they describe. `matches_trace` is computed only for the policy the trace names, with the legacy `policy_provider` value `"heuristic"` mapped to `BasePowerPolicy`.

Rationale: the snapshot is not optional any more — the specified A is four questions about the board, so a guard that hands a provider only the action list can measure only a provider that ignores the board, which is the one it was first run against and the one it found wanting. Sharing the solve is not just a saving: the unpruned matrix is the entire cost of a run, and section 3 asks for the providers to be benchmarked *identically*, which two sweeps do not do — they compare them on positions that are only nominally the same.

Consequences: `docs/pruning-guard.md` is now a per-policy table and `data/eval/discard.<format>.json` carries a `policies` list and a `policy` on every row. A two-policy sweep over 11,774 decisions takes about 24 minutes against 11 for one. `Summary` also reports `trace_checked`, because zero mismatches means agreement when the check ran and means nothing when it did not, and the mismatch count alone cannot tell those apart.

## D63. The learned policy's features are computed on the assumed spread, not the real one — 2026-08-30, Claude Code

Context: `docs/specs/2026-08-29-learned-policy-provider.md` section 3.2 requires one feature function serving both the trainer and the live agent, so that a model cannot be served different inputs from the ones it was fit on. Building it exposed an asymmetry the spec did not name: a live snapshot carries our exact stat spread, and a replay carries a percentage and nothing else, because a spectator stream does not contain stat points. Every damage-derived feature therefore has two possible values and no function of the snapshot alone can produce the same one from both sources.

Decision: the feature path computes damage from the `OpponentHypothesis` spread for *both* sides, and drops the exact numbers it has when they are there. `Board` gains `exact_stats`, which `HeuristicPolicy` leaves True and `policy_features.board_for` sets False. Both sides' surviving Pokemon are counted the same way for the same reason: from announced faints against the four a side brings, rather than counting our own directly, because every registered Pokemon looks brought in a replay.

Rationale: the alternative is a model fit on approximate damage and served exact damage, which is out of distribution in exactly the dimension the model leans on hardest, and silent — the offline number would be fine and only the live one would be wrong. Serving what was fit is worth more than serving the best number available, and the cost is bounded: the hypothesis is the same assumption the payoff model already makes about every opponent, and the pruning guard measures whether it cost anything.

The measurement that closes this is the guard, not the argument. If B underperforms A, the exact-spread half of A's advantage is a hypothesis to test rather than a fact, and the test is cheap: A can be re-measured with `exact_stats=False`.

Consequences: `champions/search/policy_features.py` reports damage numbers that are approximations on our own side, where `HeuristicPolicy`'s are exact. `tests/test_policy_features.py` checks equality over real traces — 133,922 option vectors across 11,774 turns of the 1,500 M6 self-play traces, zero mismatches — so the property is measured rather than asserted. `_Position` is renamed `Board` and `evaluate._alive` is renamed `alive`, both because a second implementation of them would be a second answer.

## D64. The replay observer tracks forme changes, because Stance Change moves the base stats — 2026-08-30, Claude Code

Context: the vector-equality check above failed on 124 of the first 564 comparisons, all of them `damage_fraction` and all of them Aegislash. poke-env follows `|-formechange|` and reports the Blade forme's base stats; the reconstruction looked the species up in the dex and got Shield's. Blade has 140 Attack and 50 Defence where Shield has the reverse, so the two disagreed by roughly a factor of two.

Decision: `champions/corpus/replay_state.py` handles `|-formechange|` and `|detailschange|`, keeping the forme beside the species rather than replacing it. Base stats and types are read from the forme; `species` continues to report the base name, which is what poke-env reports through a forme change and what the existing snapshot-equivalence test compares. A switch clears it.

Rationale: this was found by the equality check rather than by reading the code, which is the second time that check has found a real reconstruction bug — the first four are in `cafb64d`'s message. It is worth noting that neither `tests/test_replay_state.py`'s per-field comparison nor any unit test would have caught this one: base stats are not in the compared field list, and the error only becomes visible once something computes damage from them.

Consequences: reconstruction is correct for Stance Change and for Mega Evolution, which matters beyond this feature path — Champions has 75 legal Mega Stones and `docs/STATUS.md` already carries the mirror-image defect on the live side, where `champions/protocol/state.py` reports the *base* forme's stats for a Pokemon that has Mega Evolved because poke-env does. The M6 self-play teams contain no Mega that was ever evolved, so the two paths agree today and would diverge the moment one is; fixing the live side at the source is still open.

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

Consequences: reconstruction is correct for Stance Change and for Mega Evolution, which matters beyond this feature path — Champions has 75 legal Mega Stones. It also puts a question mark over a standing `docs/STATUS.md` entry, which says the live side reports the *base* forme's stats after a Mega Evolution because poke-env does: `Pokemon.mega_evolve` in the pinned poke-env calls `_update_from_pokedex(mega_species, store_species=False)`, which replaces the base stats and keeps the species, exactly as the forme change does. That entry is left standing and marked as needing re-measurement rather than deleted on the strength of reading someone else's source. Separately and not in doubt: that method reads poke-env's mainline Gen 9 pokedex, so a Mega's base stats in a live snapshot are mainline's rather than the mod's, which `CLAUDE.md` constraint 1 says they must not be.

## D65. A corpus decision's choice set is rebuilt from what the acting player knew, and nothing else — 2026-08-30, Claude Code

Context: `docs/specs/2026-08-29-learned-policy-provider.md` section 3.4 fits a policy prior to the corpus. The corpus has states (D63's reconstruction) and it has labels (the `actions` table). It does not have the thing in between: a discrete choice model is fit to the *set* the human chose from, and a protocol log shows outcomes rather than requests, so the legal option set has to be rebuilt before any of it means anything.

Decision: a slot's options are its own four moves from `|showteam|`, one per legal target, plus a switch to each living member of its own observed bring-4. Targets come from poke-env's own `_SHOWDOWN_TARGET_SLOTS`, imported rather than copied, because a second table is how the reconstructed choice set and the served one stop matching. The training population is rated replays with both players at or above the 75th percentile of the corpus's own rating distribution, recomputed at fit time, *and* `bring_fully_observed = 1`.

Rationale: the information rule is that the choice set may use anything the acting player knew and nothing else. Their own moves and their own bring are theirs — our agent knows its own team exactly, and a choice set that omitted a move the player could see is a set the player never faced. The opponent's sheet is not theirs, and `replay_state` already drops it, so this module only has to not put it back, which it does by building each side's options from that side's own sheet.

`bring_fully_observed` is the filter the spec does not name and the reconstruction needs. A replay reveals a bring-4 only through Pokemon that took the field, so a game won without the fourth ever switching in yields three, and the switch half of every choice set in that game is short an option that was legal. That is not noise a model averages out; it is a systematically smaller denominator. It costs 4,362 eligible replays down to 3,118, which is cheap for the property.

Four things this is knowingly wrong about, all of them stated in `champions/search/policy_data.py` rather than discovered later. A prevented move — flinch, sleep, Taunt — produces no `|move|` line and therefore no row, which biases the measured decisions towards turns that resolved. Redirection makes the `|move|` line name the redirector rather than the slot the human aimed at, so the row keeps the move and loses the target. Choice locks, Encore, Disable and trapping are not modelled, so a locked slot is offered options it did not have — which inflates the set and makes recall pessimistic rather than optimistic. And Mega Evolution is not enumerated, which costs nothing, because `policy_features.FEATURE_NAMES` has no mega entry and the two variants of a move therefore have identical vectors.

Consequences: 76,029 decisions from 3,108 replays, 928 players, 9.5 options each, in 91 seconds. The closed-sheet slice has no sheets at all — 624 replays and not one `|showteam|` — so its choice sets can only be built from moves the player revealed, which is a subset of the real four. Its recall is therefore optimistic in absolute terms for every provider, and only the gap between providers survives; `docs/policy-prior.md` says so beside the table rather than in a footnote.

## D66. A locked-in move is not a decision, and a charging move's target is on a different line — 2026-08-30, Claude Code

Context: D65's reconstruction was measured by counting which slots produce a training row, which turned out to be the only thing that could have found either of these. Both look like nothing from the outside — one is a row that quietly disappears, the other is a row with a wrong label — and no unit test written from the specification would have asked about either.

Decision: `_choices` reads the raw protocol lines as well as the parsed actions. A move tagged `[from] lockedmove` is skipped, because the slot had no choice about it. A move whose `|move|` line prints no target has one recovered from `|-anim|` when it fired the same turn, and from the release turn's own `|move|` line when it did not.

Rationale, on the first: Showdown tags the second turn of a two-turn move `[from] lockedmove`, and `champions/protocol/parser.py` already carries that through as `via`. Counting it as a choice produces a training row claiming the player picked, out of four moves and every legal switch, the one move they had no choice about. It is 15 rows in 9,470 — small, and the kind of small that a model has no way to discount.

On the second: 438 of 9,470 move actions print no target, and dropping all of them was the previous behaviour. Roughly two thirds are recoverable and the log says so plainly. Electro Shot under rain and Solar Beam under sun charge and fire in the same turn, and the target appears on `|-anim|` — which `parser.py` lists among the messages it deliberately ignores, correctly, since an animation is not a fact about the battle. When the move does charge for a turn, the target is on the release line. What is left is a Sucker Punch that failed: `[still]`, `|-fail|`, and no line anywhere naming what it was aimed at. Those stay dropped, because a label guessed out of the choice set is worse than no label.

Consequences: 79.7% of slots produce a row, up from 77.0% before D65's Mega fix and 81.2% after this one; the residual is 13.3% with no recorded choice at all, 4.4% with nothing in the slot, and 1.1% genuinely unresolvable. `champions/search/policy_data.py` now takes the log lines as well as the record, which is why `_choices` has a second parameter. Four tests cover it, and each was checked to fail with its fix reverted rather than assumed to.

The wider point is the one worth keeping. Three separate reconstruction bugs have now been found by comparing against something — D64's by the vector-equality check, D65's and both of these by a coverage count — and none by reading the code. A reconstruction is only as good as the thing it is checked against, and a count of what it silently threw away is one of the cheapest such things available.

## D67. Implementation B loses the pruning guard and does not ship; A stays, and the union is a budget-dependent maybe — 2026-08-30, Claude Code

Context: `docs/specs/2026-08-29-learned-policy-provider.md` section 1 fixed the decision rule before any number existed — the 95% intervals decide, and if A's and B's overlap then no difference has been demonstrated and A stays, because A is the incumbent, needs no corpus and has no inference cost. Step 4 measured all four providers against one solve of each of 6,745 positions over 750 battles (D62's harness), so the rows differ by provider and by nothing else.

Decision: **A, `heuristic-position`, remains the agent's candidate provider.** B does not replace it and is not wired into play. `LearnedPolicy` and `UnionPolicy` stay in the tree as measured providers in `scripts/discard_rate.py`, because they are the evidence for this entry and because the union is not closed as a question.

Rationale: B discards 0.4153 of the equilibrium's mass at `k = 10` against A's 0.1743, on the same positions, intervals nowhere near touching. It is not close and no reading of the intervals rescues it. B at `k = 20` (0.1721) is still worse than A at `k = 10`, which is the sharpest way to say it: doubling the search's column budget does not buy back what B's ordering costs.

The part that is a finding rather than a disappointment is that **B wins the recall comparison and loses this one**. On held-out players B recalls the human's action at 0.8954 against A's 0.7397 at `k = 5`, intervals apart (`docs/policy-prior.md`). Both numbers are correct. Spec section 6 predicted the direction — "it does not learn the equilibrium, it learns what strong humans played" — and the size is the new information: the gap is large enough that imitation recall is not a usable proxy for the guard in this format. Any later provider, C included, should be read on the guard before its accuracy is believed.

The union is left open on purpose. Interleaved to the same `k` it beats A at 15 (0.0978 against 0.1339) and 20 (0.0576 against 0.1012) with intervals apart, ties A at 10 (0.1793 [0.1694, 0.1899] against 0.1743 [0.1656, 0.1846]), and loses at 5 (0.3379 against 0.3208). The ordering follows from what interleaving does: half the budget is spent on B's ranking, which is a loss while the budget is small and a gain once A's ranking has saturated and the marginal column is worth more as coverage than as rank. It is therefore not shippable at the budget the agent runs at, and it *is* the best provider measured at budgets above it. That makes `k` itself the open question rather than the provider, and `k` is a search-cost decision that M8's deeper payoff model changes, so settling it now would be settling it against a model about to be replaced.

Consequences: `docs/04-decision-engine.md` section 3 should record that B was built, measured and rejected, with the union's `k` dependence noted, so that nobody reads the section as still specifying an unbuilt provider. The four things B leaves cheap to retry are in `docs/STATUS.md` rather than here — the assumed-spread handicap (D63) is one flag, switch options carry one feature, slot interaction is absent by construction (spec section 6), and the belief is not plumbed in. None of them is worth spending before M8, because all four are changes to a provider feeding a one-turn payoff model that M7 and M8 exist to replace.

## D68. Implementation C is built now behind a swappable client, mocked with a local Ollama model, before any paid API — 2026-09-02, Claude Code

Context: `docs/04-decision-engine.md` section 3 names C, the language-model provider, and it has sat "blocked on a model API key" since M7. The pipeline for C — compute each candidate's consequences, put them in a prompt, call a model, parse the answer back into an ordering, measure the result on the guard — had never been exercised end to end. Running an unproven pipeline against a paid API is exactly the wrong order: the first thing to establish is that the plumbing is correct, and that costs nothing to establish against a free local model. Alex's direction was to validate the whole thing with a free mock first and defer the real API to the end, so that no money is spent on a pipeline that does not yet work.

Decision: **C is built now, behind an `LLMClient` protocol (`champions/search/llm.py`), with a local Ollama model as the mock backend.** `champions/search/language.py`'s `LanguagePolicy` is the provider; `champions/agents/language_agent.py`'s `LanguageAgent` plays with it; both are wired into `scripts/discard_rate.py` and `scripts/selfplay.py` the same way A and B are. The paid provider is deferred and drops in at one seam — `client_from_env` — with no change to the provider, the agent or the guard. The default mock is `qwen2.5:3b-instruct`, a small instruct model rather than a reasoning one, because the task is to order a list whose numbers are already computed and chain-of-thought only adds latency, which matters on a CPU-only box.

Four properties are load-bearing and each is a small decision inside this one:

- **The engine computes; the model only orders.** C shows the model A's top `shortlist` candidates, each already carrying the damage, knockout, threat and speed numbers `HeuristicPolicy` computed off the same `Board`. The model never does arithmetic, which is section 3's whole case for C and the thing PokeLLMon and PokeChamp got wrong. The cost is a ceiling: C can only surface what A's shortlist contains, so it cannot beat A on a position whose answer A's shortlist already misses. `shortlist` (20) is set above every `k` the guard sweeps so the prompt — and therefore the cached reply — does not change with the budget.
- **The fallback is A, never an exception.** A dead server, an unparseable reply, or a short ranking lands on A's ordering. A candidate provider that can raise mid-battle cannot play, so a broken model makes C *equal* A, which is the honest floor.
- **Determinism is bought with a cache.** `CLAUDE.md` requires reproducibility from a seed and a model does not offer it the way an LP does. Temperature and seed are pinned, and every reply is cached on disk keyed by `(model, prompt)`, so a rerun of the guard reads the same rankings rather than paying for — and possibly varying on — a second call. Reproducibility holds for a warm cache and is a stated soft spot for a cold one.
- **C is not the default and is excluded from a default guard run.** A stays the agent's provider (D67 is unchanged). C calls a model once per position, so sweeping the whole trace directory through it is minutes-to-hours and needs a running server; `discard_rate.py` measures it only when asked by name (`--policy language-model --limit N`), and `discard-llm` prints rather than overwriting the committed A/B report.

Rationale: D67 fixed the reading for any future provider — believe the guard, not the recall — and C is subject to it. B recalled strong humans far better than A and pruned far worse; an LLM that looks convincing on transcripts has shown nothing until it has a discarded-mass number beside A's 0.174 at `k = 10`. Building the mock first is what makes that number cheap to get. The Ollama backend runs a real model over the same HTTP shape a paid provider uses, so the pipeline is validated for correctness before a cent is spent, and the switch to a paid model is a change in one function.

Verified this session on a CPU-only machine with no built simulator: the decision half — prompt, call, parse, order — runs end to end against Ollama (`scripts/llm_smoke.py`), with `qwen2.5:3b-instruct` returning a clean full ordering in ~2.6s that puts the guaranteed knockout first and the idle Protect last. The dex-free transport, cache and parse are covered by `tests/test_llm.py` (22 tests); the provider glue by `tests/test_language.py`. What is *not* done here and is the next real step: the guard number for C, which needs the built dex and the self-play traces (the same prerequisites A and B's guard rows already have) and belongs on the machine that has them.

Consequences: `docs/04-decision-engine.md` section 3 should stop saying C is blocked on an API key and record that it is built behind a swappable client with a local mock, that it is measured on the guard like A and B, and that the paid provider is the deferred piece. New files: `champions/search/llm.py`, `champions/search/language.py`, `champions/agents/language_agent.py`, `scripts/llm_smoke.py`, `tests/test_llm.py`, `tests/test_language.py`. `champions/agents/oneply.py` takes a `policy` argument so B and C can play through the same search (default unchanged, so the M2–M6 record is untouched). `data/llm/` is the reply cache and is gitignored. The determinism caveat for a cold cache is the one property a careful reader should carry forward.

## D69. The mocked C loses the guard at every budget below its shortlist, and the guard's own number is distribution-dependent — 2026-09-03, Claude Code

Context: D68 built C behind a swappable client and named its single next step: a discarded-mass number beside A's, produced the same way A's and B's were. That needed the built dex and the self-play traces, which existed only on the Windows box — until this session, which set the Mac up as a full working environment (venv, vendored Showdown at the pinned commit, dex dump reproducing `docs/dex-delta.md` byte for byte) and regenerated the traces there: `make eval-games`, 750 battles, 1,500 traces, 0 invalid, 0 protocol failures, about 30 minutes rather than the Windows box's hours.

Two measurements came out of it, and the second reframes how the first — and every earlier guard number — must be read.

**C, mocked with `qwen2.5:3b-instruct`, loses the guard to A decisively.** Measured over 291 positions from 50 trace files, C and A scored on the same positions in the same run, warm cache, so the rows differ by provider and nothing else. Discarded mass, C against A: 0.781 vs 0.521 at `k = 5`, 0.678 vs 0.382 at `k = 10`, 0.523 vs 0.341 at `k = 15`, intervals nowhere near each other; identical at `k = 20` by construction, because C reorders A's top-20 shortlist and keeping all 20 is the same set. The model's reordering of a shortlist whose numbers were already computed for it is worse than the ordering it was handed. The answer STATUS's framing asked for is therefore: the mock does **not** produce a number in A's neighbourhood, so a paid model has to clear a bar the free one could not, and whether to spend on that attempt is Alex's call. The swap remains one function (`client_from_env`); a larger local model (`CHAMPIONS_LLM_MODEL`) is the free intermediate step if wanted. This is D67's rule arriving for the third provider in a row: read it on the guard, and the guard said no.

**The guard's number depends on whose play produced the positions.** The regenerated traces were played by the current agent, which prunes with A (`heuristic-position`) — the Windows traces the committed report was measured on were played under `heuristic-base-power`, pre-D61. On its own positions, A discards 0.378 [0.368, 0.388] at `k = 10` against the 0.174 [0.166, 0.185] it scored on base-power's positions, and every other provider moved the same direction (9,334 eligible positions vs 6,745). Two orderings that were budget-dependent maybes are now clean: the union beats A at `k = 10` with intervals apart (0.336 [0.325, 0.347] vs 0.378), where on the old positions it tied, and B overtakes A at `k = 20` (0.227 vs 0.282). The mechanism is the caveat both earlier sessions carried, now measured rather than stated: the guard asks "what would this provider have kept here?", and an agent that prunes well plays into harder positions — the easy discards never recur because the better policy already avoided the lines that produce them. Nothing about D67's rejection of B as the *default provider* changes (A still wins at the agent's own `k = 10` on both position sets), but the union-at-high-`k` result is stronger than D67 recorded, and any future guard comparison must say which position set its rows were measured on. `docs/pruning-guard.md` and `data/eval/discard.*.json` are now the Mac-trace measurement; the Windows-trace numbers survive only in D61/D67 and the milestone record.

Consequences: the Mac at `/Users/alexryan/Desktop/pokemonbot/pokemonbot` is now a full working environment, and the Makefile picks the venv layout by OS. Two `tests/test_language.py` tests that required the dex fixture — and so had never once run on the machine C was written on — were wrong about interfaces (`ScoredAction` subscripted as a dict; model indices read against the raw action list instead of A's shortlist order) and are fixed; the suite is 473 passing. C stays built, stays off by default, and waits on a decision about a stronger backend rather than on any code. M8 remains the next milestone.

## D70. The engine gate's rule is fixed before its numbers, and the depth-2 budget conclusion is reversed at the local figure — 2026-09-13, Claude Code

Context: M8 is the engine decision gate (`docs/01-plan.md`, `docs/08-implementation-blueprint.md` section 5, D6): profile and decide whether marginal win rate comes from search depth or from evaluation quality, and build the Rust engine only if depth wins. Four milestones of measurement (D48, D52, D67, D69) left the one-turn payoff model as the binding constraint. `docs/STATUS.md` asked for two things before M8 treats anything as settled: redo the budget arithmetic at the locally measured 2.13 ms per clone-plus-step rather than the reference container's 4.7 ms, and quantify the switch bias as part of M8's justification rather than after it.

Decision: M8 is run as `docs/specs/2026-09-13-engine-gate.md` specifies, and the rule is fixed now. Five arms — `oneply` (the incumbent), `oneply-oracle`, `twoply`, `twoply-oracle`, `sim-oracle` — each measured against `oneply` in a mirror match on the same team, 200 games, on `regmb-alpha` and separately on `regmb-beta`. Fidelity is `sim-oracle` minus `oneply-oracle`; depth is `twoply-oracle` minus `oneply-oracle`. The 95% intervals decide: depth wins the gate only when the depth gap is apart from zero and either the fidelity gap is not or the depth gap exceeds it with the interval on their difference apart from zero; fidelity wins when its gap is apart from zero and depth's clause fails; both clearing ships fidelity first and re-asks depth on the simulator payoff; neither clearing on either team is a finding against the premise and triggers the pre-registered secondary measurement against `greedy`. Verdicts are per team, not pooled. The clock is reported and does not veto.

Two readings inside that rule are decisions rather than transcription:

- **The opponent is an oracle, not the belief.** The simulator needs a complete opponent team, and the belief filter is the designed source of one (D48). The prior is not built on this machine and a belief-fed arm would confound particle error with model fidelity, so every non-incumbent arm is handed the opponent's six registered sets — the ceiling of what any belief could supply. What it is not handed is the bring and its order; unrevealed brought slots are drawn uniformly. A negative fidelity result against the oracle is decisive; a positive one is an upper bound, and the belief-fed arm is the first follow-up.
- **Depth is measured on the analytic model, not the simulator.** That isolates depth from fidelity at a cost of milliseconds per node, and it is where the switch bias lives: the two-ply child places the incoming Pokemon, so the switch bias is quantified by the depth gap itself rather than by a separate measurement.

**The budget conclusion is reversed.** `docs/02-mechanics-deltas.md` section 7 concluded, at 4.7 ms, that depth 2 "needs an engine roughly 100 times faster". At 2.13 ms, depth 2 with pruning on both plies and no particles is 21.3 s per turn on one core — inside the 45 s turn limit — and about 3 s across eight simulator processes if they scale. What a 100 times faster engine buys is depth 2 *with* belief particles and roll replicates (35 minutes on the stock simulator) or depth 3. Section 7 now says so, and a depth verdict at this gate justifies an engine with that brief, not with the old one. The 7 minute player clock is a total, so the per-turn ceiling is not a per-turn budget; M11 owns the allocation.

Consequences: `k`, the union (D67) and the belief head-to-head (D58) stay deferred until the verdict and are then re-opened against whichever payoff model won. No engine work starts before D71 records the verdict. `docs/04-decision-engine.md` section 3 now carries the paragraph D67 asked for, so it no longer reads as though B were unbuilt. This machine is a third working environment (Windows, `C:\Users\aryan\pokemonbot`, Python 3.13), set up this session; the corpus and the belief prior are not on it.

## D71. The engine gate: neither depth nor fidelity clears, and no engine is built — 2026-09-13, Claude Code

Context: D70 fixed the arms and the rule before any number existed. The run is `docs/engine-gate.md`: each arm against `oneply`, the incumbent, in a mirror match on one team, 200 games, seed 0, on `regmb-alpha` and separately on `regmb-beta`. Wilson 95% intervals on the win rate; normal 95% intervals on the difference of two proportions; the two headline gaps are `twoply-oracle` minus `oneply-oracle` (depth) and `sim-oracle` minus `oneply-oracle` (fidelity).

| arm | alpha, vs `oneply` | beta, vs `oneply` |
| --- | --- | --- |
| `oneply-oracle` (information control) | 44.0% [37.3%, 50.9%] | 47.0% [40.2%, 53.9%] |
| `twoply` (depth, blind) | 51.5% [44.6%, 58.3%] | 43.5% [36.8%, 50.4%] |
| `twoply-oracle` (depth) | 43.0% [36.3%, 49.9%] | 52.0% [45.1%, 58.8%] |
| `sim-oracle` (fidelity) | 49.0% [42.2%, 55.9%] | 53.0% [46.1%, 59.8%] |
| depth gap | −1.0% [−10.7%, +8.7%] | +5.0% [−4.8%, +14.8%] |
| fidelity gap | +5.0% [−4.8%, +14.8%] | +6.0% [−3.8%, +15.8%] |

Decision: **no engine.** Section 4's rule reads "neither clears" on both teams: no arm demonstrates a gain over the incumbent, and neither gap is apart from zero. The Rust engine is not justified and no branch is opened. The pre-registered secondary measurement — the same arms and `oneply` itself against `greedy`, which has a known baseline (D30) and more headroom than a mirror — runs next, as a check on the mirror's sensitivity and not as a substitute for the rule. `k`, the union (D67) and the belief head-to-head (D58) stay deferred: they were deferred until a payoff model won, and none did.

Rationale, and what the numbers do and do not say. The premise this milestone was framed on — `docs/STATUS.md`'s "the binding constraint is what the one-turn payoff model can do with what it knows" — is contradicted at this pairing in the only sense a measurement can contradict it: handing the analytic model the truth about the opponent's sets moved nothing (the control is *below* the incumbent on both teams), one more ply on that model moved nothing outside the interval, and stepping the real simulator moved nothing outside the interval either. On beta both gaps sit at +5 to +6 points with intervals reaching +15, which is consistent with small real effects that a 200-game mirror cannot resolve: detecting a 5-point difference between two arms at 80% power needs about 1,500 games per arm. So the honest statement is "not demonstrated", not "absent", and the secondary measurement exists precisely to tell those apart. On alpha the depth gap is negative, which is the control team behaving as D30 predicts.

Two rows were measured twice, and the record says so. The first run's `sim-oracle` rows (93/200 on alpha, 105/200 on beta) were produced with a defect in the rollout: a side down to one Pokemon has one slot permanently empty, and the model either skipped the decision (17% of decisions scored by the analytic fallback) or laid the survivor out in the wrong slot (2–4% of cells refused and scored analytically, and targets aimed at the wrong slot). Fixed in `265dca4` by holding an empty slot with a fainted Pokemon so the simulator's slot numbering matches the snapshot's; the two `sim-oracle` matchups were re-run with the same seed and incumbent (98/200, 106/200; zero fallback cells, forced switches aside), and the other six rows are the first run's. This was a correctness fix in an arm, made with the reason stated, not a re-roll of a number that looked wrong; the verdict is the same either way.

Caveats the report carries and this entry repeats: the oracle is the ceiling of what a belief could supply, not a forecast of a belief-fed arm; neither checked-in team holds an item, so fidelity's headroom on items is unmeasured; weather duration is unobservable from poke-env's snapshot and the simulator gets it fresh; forced switches are answered by the analytic model by design. The clock: every arm stayed inside both limits; `twoply-oracle` is the expensive one at about 2 s per decision (p95 6 s on beta, one battle at 109 s of total thinking), because true-move columns make the child matrices wide.

Consequences: `docs/engine-gate.md` and `data/eval/engine-gate.*.json` are the record; "What the switch bias costs" moves from open to measured (the depth gap above). The secondary measurement is in flight when this is written and is reported in `docs/engine-gate-greedy.md` with no verdict attached. If it too shows nothing apart from zero, the next question is not the payoff model but the search's *inputs* — the same question M4 (skill), M5 (belief) and M7 (imitation) each answered negatively — and the cheapest live candidates are the union at higher `k` on the current model and a third and fourth team (M6's finding that two teams cannot fit every weight). If it does show an effect, the arms that show it are the ones worth a proper, larger mirror.

## D72. The gate's secondary measurement shows nothing apart either, and it had no headroom to show it in — 2026-09-13, Claude Code

Context: D71's rule for the "neither clears" outcome was the pre-registered secondary measurement: `oneply` and the four gate arms against `greedy` (max-base-power), 200 games per arm per team, seed 0, as a check on whether the mirror was too insensitive to see an effect. `docs/engine-gate-greedy.md` is the report; it carries no verdict by design.

| arm | alpha, vs `greedy` | beta, vs `greedy` |
| --- | --- | --- |
| `oneply` (incumbent) | 95.0% [91.0%, 97.3%] | 96.0% [92.3%, 98.0%] |
| `oneply-oracle` | 91.5% [86.8%, 94.6%] | 94.5% [90.4%, 96.9%] |
| `twoply` | 91.5% [86.8%, 94.6%] | 97.5% [94.3%, 98.9%] |
| `twoply-oracle` | 88.5% [83.3%, 92.2%] | 96.0% [92.3%, 98.0%] |
| `sim-oracle` | 89.0% [83.9%, 92.6%] | 97.0% [93.6%, 98.6%] |

Decision: it is D71's first reading. No arm is apart from the incumbent, on either team, and on alpha every arm handed the truth or given depth or fidelity is *below* the incumbent by a few points inside overlapping intervals. Nothing here re-opens the engine question, and the next work is the search's inputs rather than its payoff model (D71's list: the union at higher `k` on the current model, and a third and fourth team).

Rationale, with the limit that makes this check weaker than intended. D30 measured the one-ply agent at 82% against `greedy` on alpha and 56% on beta; that agent pruned with base power. The specified A (D61) moved it to 95% and 96%, so `greedy` no longer has the headroom the secondary measurement was chosen for: an arm cannot show a large gain over a baseline that already wins nineteen games in twenty. The check therefore says only that nothing *large* was hidden by the mirror; a 5-point effect is as invisible here as it was there. The frozen pool has no opponent between `greedy` and the incumbent, and building one is the same "more teams, more opponents" work the next action names. The alpha ordering (every enriched arm slightly below the incumbent) is within noise and is noted, not interpreted.

Consequences: M8 closes with no engine and no provider change. `docs/engine-gate-greedy.md` and its JSON are committed. The frozen pool's lack of a mid-strength opponent is recorded as a limit of every future win-rate measurement, and a third and fourth team (M6's request) would also give the pool one.

## D73. The default team is a published Reg M-B tournament winner, with hand-chosen stat points — 2026-09-13, Claude Code

Context: Alex asked for the highest win-rate Reg M-B team to become the team the agent plays. The project's own win data is the replay corpus, which is not on this machine, is skill-confounded (D39), and never carries stat points; Alex chose a published team instead. Aggregator sites (ChampTeams, ChampionsMeta, Pikalytics tournaments) render their rankings client-side or behind a paywall and could not be read; per-Pokemon "win rates" on Pikalytics are usage-weighted and not a team's. The one source with a full, attributable team and a record is the Limitless tournament database.

Decision: `data/teams/regmb-rain.txt` is the team DaniVGC03 won Maddo's Cup #9 with — 234 players, Reg M-B, 2026-06-20, a 13-1 record, the largest early Reg M-B event with a published team list — and `champions.teams.DEFAULT` names it. The ladder, self-play, the viewer's run panel and the human-play script now default to it. Mega Swampert (Wave Crash, High Horsepower, Ice Punch, Protect), Pelipper (Drizzle: Weather Ball, Hurricane, Tailwind, Wide Guard), Archaludon (Stamina: Electro Shot, Dragon Pulse, Flash Cannon, Protect), Sinistcha (Hospitality: Rage Powder, Matcha Gotcha, Trick Room, Protect), Maushold (Friend Guard: Follow Me, Super Fang, Rain Dance, Protect), Gholdengo (Good as Gold: Make It Rain, Shadow Ball, Nasty Plot, Protect); items, abilities, moves and natures as published. The simulator's own validator accepts it.

Rationale, and the two things the file cannot claim. **"Highest win rate" is the tournament record, not a measured rate**: 13-1 over one event is the best documented record found, and it is one event. **The stat points are a guess.** No public source carries Champions point allocations (the same finding as `docs/05-data-pipeline.md` section 5 and D33), so each set carries 32 HP, 32 in its main attacking or defensive stat and 2 in a third — the 66-point budget at the per-stat cap — chosen by the obvious reading of each nature. A real spread would differ on the bulk-versus-speed margins. Every number measured on this team inherits that.

Consequences: `ALPHA` and `BETA` stay checked in and are still what every measurement so far was made on; the evaluation weights were fit on `ALPHA` self-play (M6) and are not refit here. The team plays two abilities the analytic payoff model does not represent (Hospitality, Friend Guard) and a Mega, and it holds items — the first checked-in team that does — which changes what a future fidelity measurement can see (D71's caveat). `make eval-games` still plays `ALPHA`, so the fitted evaluation's provenance is unchanged; refitting on the new team is a separate decision. The M8 gate's `DEFAULT_TEAMS` stay `(ALPHA, BETA)` as the record of what D71 measured.

## D74. A silent switch-in rules out the abilities it would have announced — 2026-09-13, Claude Code

Context: Alex, playing against the bot, noted that an Incineroar that switches in without lowering anyone's Attack cannot have Intimidate and therefore has Blaze, and asked the bot to reason the same way. The belief filter took abilities only as positive reveals (`|-ability|` and `[from] ability:` attributions); silence said nothing.

Decision: `EvidenceBuilder` now emits, at the end of each observation batch, a `Reveal` with `how="absent"` for every ability that (a) the species can legally have, (b) the simulator announces on switch-in under a condition that held, and (c) was not announced for that slot since the switch. `TeamConstraints` reads an absent reveal as an exclusion: particles whose set carries the ability die, composed and forced sets avoid it, and the belief's summary lists `excluded_abilities`. The announcing table (`evidence.ANNOUNCED_ON_SWITCH_IN`) is transcribed from the pinned simulator's `onStart` handlers and kept deliberately short — Intimidate needs an adjacent foe (checked against who was on the field at that sequence number), weather and terrain setters are silent only when one is already up (so they are ruled out only when none was), and everything else in the table announces unconditionally. Abilities whose announcement depends on the opponent's team or a once-per-battle flag (Frisk, Anticipation, Download, Trace, Supersweet Syrup, Costar and the like) are not in the table, because their silence has an innocent explanation. Nothing is ruled out while Neutralizing Gas is up, or for a Mega forme, whose ability is the forme's rather than the registered one.

Rationale: an exclusion is exactly as hard a fact as a reveal and arrives earlier — before the Pokemon has moved — and it is the kind of inference the filter was built for (`docs/03-belief-filter.md` section 3). Keeping the table short is the same discipline D43 applied to effects: a wrong exclusion eliminates the true hypothesis, which the coverage metric would catch only later.

Consequences: the filter's coverage on the checked-in teams is unchanged in the tests; the live effect is only visible with a built prior, which this machine does not have. `tests/test_belief.py` covers the four cases: the silent Incineroar, the announced one, a lead that came in before any foe, and Neutralizing Gas.

## D75. The default team is the 2026 World Championships winner, with its published stat points — 2026-09-13, Claude Code

Context: hours after D73 made the Maddo's Cup #9 winner the default team, Alex supplied a screenshot of the #1 team at the 2026 World Championships and asked for that instead. Worlds 2026 (San Francisco, 2026-08-28 to 30, 395 players in the VGC division) was played in Regulation Set M-B, the format this project is pinned to, and unlike D73's source the page carries stat points.

Decision: `data/teams/regmb-worlds.txt` is Takuma Yamazaki's winning team and `champions.teams.DEFAULT` names it; `regmb-rain` stays checked in as `RAIN`. Floette-Eternal @ Floettite (Flower Veil; Moonblast, Dazzling Gleam, Light of Ruin, Protect; Timid; 2 HP / 32 SpA / 32 Spe), Basculegion @ Life Orb (Adaptability; Wave Crash, Last Respects, Aqua Jet, Protect; Adamant; 13 HP / 32 Atk / 4 Def / 3 SpD / 14 Spe), Kingambit @ Chople Berry (Defiant; Sucker Punch, Kowtow Cleave, Low Kick, Iron Head; Adamant; 32 HP / 32 Atk / 2 Def), Dragonite @ Dragoninite (Multiscale; Dragon Pulse, Heat Wave, Extreme Speed, Protect; Modest; 2 HP / 32 SpA / 32 Spe), Garchomp @ Choice Scarf (Rough Skin; Dragon Claw, Stomping Tantrum, Earthquake, Rock Slide; Adamant; 2 HP / 32 Atk / 32 Spe), Sneasler @ Focus Sash (Poison Touch; Close Combat, Dire Claw, Fake Out, Feint; Jolly; 2 HP / 32 Atk / 32 Spe). The simulator's validator accepts it.

Rationale, and the one thing that had to be decided rather than copied. Items, moves, natures and the mega stones agree across the tournament team sheet on Limitless and the MetaVGC page in Alex's screenshot; the stat points come from the screenshot (Limitless does not show them) and sum to the 66-point budget on every set. **Two abilities disagree between the sources**: the team sheet says Basculegion is Adaptability and Sneasler is Poison Touch; the screenshot's page says Swift Swim and Unburden. The team sheet is the submitted list and the team carries no rain setter, which makes Swift Swim the incoherent reading, so the file follows the sheet. If Alex knows otherwise, the file is two words to change. "Floette-Mega" on the page is the mega of Floette-Eternal — Light of Ruin and Fairy Aura say so, and the Reg M-B dex has no base Floette — which is what the export names.

Consequences: as D73, the team is the first default to hold items, and it plays a Mega, Multiscale, Adaptability, Defiant, Rough Skin and a Choice lock — none of which the analytic payoff model represents, and the last of which the M8 rollout cannot materialise (D71's exclusion list). Nothing has been measured on it. The M6 evaluation weights were fit on `ALPHA` self-play and are not refit; `make eval-games` still plays `ALPHA`. The M8 gate's teams stay `(ALPHA, BETA)` as D71's record.

## D76. The coach is the one-turn model re-solved offline with the pruning removed and the information state named; it runs uncalibrated and says so; its thresholds are hand-set until the corpus calibrates them — 2026-09-13, Claude Code

Context: M8 closed with no engine and `docs/STATUS.md` left the next step to Alex, who chose M9 over the two search-input candidates. `docs/06-coach-and-evaluation.md` part I specifies two losses per turn, five labels and four tags, with thresholds "calibrated empirically against rating bands"; `docs/07-observability.md` section 2 specifies the output as `analysis` events keyed to the trace; `docs/STATUS.md` had left open what the coach does when `IS_CALIBRATED` is False and whether it ingests Champions games. `docs/specs/2026-09-13-coach.md` is the design; this entry records the four choices in it that a later reader could reasonably have made differently.

Decision, in four parts.

1. **The coach re-solves rather than reads.** Every decision is rebuilt as the matrix game the agent plays — `payoff_matrix` over `TurnModel`, `solve_both` — with the whole legal set as rows instead of `k = 10` and a column budget of 25, and the two losses are computed from that solve. The trace's recorded equilibrium is carried on the event as `recorded`, not used. Rationale: `docs/01-plan.md` says the coach is the same core run offline with a larger budget, and the pruning guard measured that the live `k = 10` discards real mass (D61, D69), so a coach that read the live matrix would score the agent against its own pruning. The two-ply and simulator payoffs are not used: neither cleared M8 (D71, D72), and `docs/08` names a coach growing its own evaluation as the failure mode.

2. **The information state is explicit and differs by source.** On the agent's own trace the ex-ante half uses the agent's information (revealed moves, the pessimistic constant) and the ex-post half uses a team file when given. On an open-sheet replay both halves use the sheet, because the human saw it. Every event records `information.ante` and `information.post`. Our own side's items and abilities are applied on both halves (`BeliefEffects`), a small departure from the incumbent's `NoEffects`, because the player knew them. The belief filter is not a source: its prior needs the corpus, which is not on this machine, and the coach has to run on a fresh clone. The seam is `SetSource` and `champions/coach/truth.py` is what a belief-backed source would replace.

3. **Uncalibrated is reported, not refused.** Every `analysis` event carries `calibrated`; the document prints one line when it is False; nothing is suppressed. A monotone miscalibration leaves the row ordering, and so the label, intact, and the losses are the numbers the agent played by. A coach that refused to run on a fresh clone would never be tried. This closes the `docs/STATUS.md` open question.

4. **Thresholds are hand-set, in one file, and the report says so.** Five points and fifteen for the loss bands, five for Forced, three for Read and Gamble, ten for Unlucky and Lucky, all in win-probability points in `champions/coach/classify.py`. `docs/06` section 2's calibration against rating bands needs the corpus and its ratings; it and section 8's predictive-validity check are the first follow-up and are one script over scraped replays. A fifth tag, Lucky, is added as the mirror of Unlucky; the four specified tags are implemented as specified.

Two things the spec declines. There is no bring-4 verdict, because there is no preview value model to solve with (D39, D56); the preview pseudo-turn is emitted with `pending` and that reason, the way the agent's own `preview_decision` is. And Champions ingestion is not built: the coach accepts any protocol log, so a Champions game transcribed into protocol form is reviewable today, and a transcription tool is a separate deliverable; the question stays open.

Consequences: `champions/coach/` (six modules), `scripts/review.py`, `make review`, `tests/test_coach.py` (31 tests, every branch of the rule), QUICKSTART section 18. `champions/search/policy_data.py` gained a public `slot_choices` generator that `decisions_from_record` is now expressed through, so the coach's replay path and M7's training path are one reconstruction. The overlay validates as a trace; the viewer opens it and does not render it, which is M10's job. On a trace review the recomputed curve equals the recorded evaluation turn for turn, asserted in the tests. About a second per turn.

## D77. M10 renders the overlay in the existing viewer; M11 allocates the clock as a share of what is left and escalates to two plies only when the one-ply answer is close; the coach's bands are calibrated by a rule fixed before the numbers — 2026-09-13, Claude Code

Context: with M9 built, three things stood between the project and the end of `docs/01-plan.md`'s milestone list: a review client (M10), the clock (M11), and the calibration the coach's spec deferred (D76 part 4). Alex asked for all three in one session. Each had a design question that could have gone another way.

**M10: one viewer, not two.** `docs/07-observability.md` section 4 lays out a review client and section 1 says the review client and the live view are the same program. Decision: the review is rendered by the existing viewer (`champions/viewer/static/app.js`), which now folds the coach's `analysis` events into the same decision points it already builds: the game-scope event becomes a summary block with the critical turns as jump links and the win-probability curve drawn under the eval bar; the turn-scope event becomes a label and tags in the spine and a review panel (both losses, luck, the re-solved equilibrium, the opponent's mix, the roll branches, the explanation); the preview-scope event becomes the pseudo-turn's verdict block. A plain trace renders exactly as before, since every review surface keys on events it does not have. No build step, no framework, as section 5 asks. The server needed nothing: `.review.jsonl` files already list and serve as traces of the same battle.

**M11: allocation, and escalation only when close.** `docs/04-decision-engine.md` section 7 states the intended rule and D7 deferred it to M11. Decision: `champions/search/clock.py` gives a turn the smaller of the 45 s limit and an even share of the player clock left after a 30 s reserve, spread over the turns the game is expected to still run (twelve, the gate traces' long tail); the base agent now tracks every battle's spend and puts the budget, the spend so far and the remaining clock on every timing event, so the harness and viewer read the allocation rather than infer it. `champions/agents/adaptive.py` applies it: the one-ply solve first; if the equilibrium is pure and the best row beats the second by five points against the column mix, play it; otherwise re-solve one ply deeper inside the budget. The escalation target is the two-ply model because it exists and is the only deeper search there is; M8 measured it as not apart from the incumbent (D71), so the claim is about where time goes, not win rate, and the ladder table is what says whether it costs anything. The rule's numbers (reserve, expected turns, gap) are constants with the reasoning beside them, not fitted.

**The coach's bands: a rule, then the numbers.** `docs/06` section 2 wants the label thresholds calibrated against rating bands. Decision: `scripts/calibrate_coach.py` reviews rated open-sheet Bo3 games from both sides and fits the two cutoffs so that, among the top rating quartile's off-support decisions, half are inaccuracies, thirty-five percent mistakes and the rest blunders — strong play is the reference population, so a strong player's ordinary off-support loss is an inaccuracy. The same run reports section 8's validity check: Spearman correlation of per-game mean loss with rating, ex-ante beside ex-post, with bootstrap intervals, and the dissociation stated as a verdict with the same pre-registered shape the engine gate used. The fitted bands take effect only when written to `data/eval/coach-bands.<format>.json` (`--write-bands`), which `classify.load_bands` reads at import — the arrangement `search.evaluate` uses for its weights, so calibration cannot be claimed without the run that measured it, and every report says which bands it used.

Consequences: `champions/search/clock.py`, `champions/agents/adaptive.py` (`adaptive` in the ladder and self-play registries), the budget fields on every timing event, `tests/test_clock.py` and `tests/test_adaptive.py`; the viewer's review surfaces and a server test that a review file lists and serves; `scripts/calibrate_coach.py`, `make calibrate-coach`, `docs/coach-calibration.md` generated, `classify.Bands`. The first calibration run and the first Worlds-team baseline are recorded in `docs/STATUS.md`.

## D78. Every live ladder game keeps three records: the trace, the server's replay, and a row in a ledger; the coach runs on them afterwards, not during — 2026-09-14, Claude Code

Context: `scripts/ladder_live.py` was built on 2026-09-13 to play the official ladder and wrote the trace only. Alex asked for two things before the first games are played: that everything needed to see what went right and wrong in a game is collected, so the games can improve the agent, and that the viewer can watch the games live. The trace already held the agent's side; what was missing was the other side's record, an index across runs, and a viewer command that does not start a local simulator.

**Three records per game.** The trace (`runs/live/<battle_tag>.<username>.jsonl`), unchanged. The replay: the bot sends `/savereplay` to the room as the battle starts, which marks the room so the server re-uploads the complete log when the battle ends (`room-battle.ts`, `replaySaved`), and the ledger records the address; sent at the start because poke-env leaves the room the moment the battle finishes. The replay is the neutral record of the same game — what the opponent actually revealed, seen from outside our belief — and `scripts/review.py` already reads replays, so the same game reviews from both sources. A ledger, `runs/live/ledger.ndjson`, one row per finished game (username, agent, team, format, seed, opponent, both ratings, result, turns, trace path, replay URL), appended across runs; `--summary` reads it back. Named `.ndjson` so the viewer's `*.jsonl` listing does not take it for a trace.

**The coach after, not during.** `--review` runs `scripts/review.py` on each trace as its game ends, in a separate process, writing the overlay and document beside it. Off by default: the review re-solves every turn and the next game's search would share the CPU with it, and the live agent is clock enforced (D77). `make review GAME=runs/live` afterwards is the same result without the contention.

**The viewer on the live directory.** `make viewer-live` is the existing viewer pointed at `runs/live/` with `--no-server`: the games are on the official server, and the viewer only tails what the bot writes, which it already did (D13, D24). Nothing in the viewer changed. The base agent gains `on_battle_start`, the counterpart of `on_battle_end`, which is how the ladder script asks for the replay without the agent knowing the ladder exists.

Consequences: `scripts/ladder_live.py` (ledger, replay request, `--review`, `--summary`, `--no-save-replays`), `champions/agents/baseline.py` (`on_battle_start`), `Makefile` (`viewer-live`, `ladder-summary`), `tests/test_ladder_live.py`, QUICKSTART section 21. Saving replays makes the bot's games public under its name, which is the transparency Showdown asks of a bot anyway.

## D79. On the live ladder the coach runs between games, not after the run: game, coach, game, coach — 2026-09-14, Claude Code

Context: D78 made the coach opt-in on the live ladder (`--review`), running as a detached process while the next game was searched, on the reasoning that the review would share the CPU with a clock-enforced search. Alex asked for the sequence to be strict instead: the next game is not searched until the coach has reviewed the last one.

Decision: `scripts/ladder_live.py` no longer calls poke-env's `ladder(n)`, which searches for the next game the moment the last one ends. It ladders one game at a time (`play`): search one, wait for it, close its trace so the last event is on disk, run `scripts/review.py` on it in a subprocess and await it, print the document's summary and critical turns, and only then search again. The review is on by default and `--no-review` turns it off, the reverse of D78's flag. The contention D78 avoided does not arise, since nothing is searching while the coach runs; the cost is under a minute of idle between games, against five to ten minutes of play. The viewer shows each finished game with its overlay as soon as the review lands, which is before the next game starts.

Supersedes the `--review` paragraph of D78. The records D78 keeps (trace, replay, ledger) are unchanged.

## D80. The project plays Regulation M-C; the Showdown pin moves to 2026-09-13; a new regulation borrows its predecessor's fitted artifacts along a named lineage — 2026-09-14, Claude Code

Context: the first attempt to ladder (D78, D79) was answered by the official server with "Your format gen9championsvgc2026regmb is not ladderable." Regulation M-B left the ladder on 2026-09-09, its nominal end, and the `|formats|` list the server sends on connect shows `[Gen 9 Champions] VGC 2026 Reg M-C` as the only searchable Champions VGC format (Random Doubles is the other searchable Champions format). Alex chose to move.

**What M-C is.** Upstream commit `812501ede` (2026-09-09) adds it. It is the live `champions` mod with thirty species legalised and none banned: Mega Salamence, Mega Baxcalibur, Mega Golisopod, the Z megas of Garchomp, Lucario and Absol, Rillaboom, Cinderace, Inteleon, Indeedee, Toxtricity, Pawmot, Squawkabilly and the rest. M-B was frozen into a new `championsregmb` mod; `championsregma` was removed. The ruleset is unchanged (Flat Rules, VGC Timer, Open Team Sheets, still declined per D2). Every checked-in team is legal in M-C, verified with the simulator's validator; none uses what M-C added. The team files keep their `regmb-` names, which say what they were built for.

**The pin.** `vendor/SHOWDOWN_COMMIT` moves from `bb179fbf8` (2026-08-27) to `aa6d5f085` (2026-09-13, upstream head). Between them the `champions` mod changed 2,100 lines, mostly learnsets, plus Eject Button, Run Away, Double Shock and M-A bug fixes. The dex was rebuilt for both formats and `docs/dex-delta.md` regenerated: 293 moves, 251 items and 7 abilities differ from mainline, against 303, 256 and 8 at the old pin. The pin's guard fired once: `tests/test_belief.py` derives the damage-affecting ability set from the vendored source and found Aura Guard (halves contact damage), added to `champions/belief/effects.py`. That is the guard working as D4 intended.

**The format id.** `champions/formats.py` is new and is where the default lives (`FORMAT_ID`); every script and test that named the format now names M-C, and `scripts/selfplay.build_agent` loads the dex for the format it is told to play rather than for the default, which the live ladder's `--format` needed.

**The lineage.** The fitted artifacts are keyed by format id (M6 weights, M7 prior, D77 bands, all under `data/`), and none exists for M-C. Rather than copy files or silently run uncalibrated, `champions.formats.LINEAGE` names M-B as M-C's lender, and the three loaders fall back to the lender's file when the format's own is absent, marking the model's `source` as lent. The evaluation weights therefore say "fit on gen9championsvgc2026regmb, lent to gen9championsvgc2026regmc" on every trace and report. The rationale: same mod, same mechanics, a strictly larger pool the teams do not use; the fit is the best available prior for M-C until it is refit on M-C play, and a refit is `make fit-eval` once there are M-C traces. The corpus formats move with it (`gen9championsvgc2026regmc` and its Bo3), so `make scrape` now collects M-C replays and the M-B corpus on the other Windows box is an M-B corpus.

Consequences: `vendor/SHOWDOWN_COMMIT`, `docs/dex-delta.md`, `champions/formats.py`, `champions/belief/effects.py`, the three loaders, `scripts/selfplay.py`, the format constant in twelve scripts and six tests, `champions/teams.py` docstring, `CLAUDE.md` constraint 3, QUICKSTART. Every number in `docs/` before this entry was measured at the old pin under M-B.

## D81. Trace events are written and flushed at emit, not queued; the viewer names the bot's phase and follows the newest live battle on its own — 2026-09-14, Claude Code

Context: watching the first live games, Alex reported the viewer as very delayed, and asked for a status line ("thinking", "waiting for opponent") beside the battle.

**The delay.** `Trace.emit` enqueued events for a background asyncio task to write, on the reasoning that file I/O should stay off the decision path. The task ran on poke-env's loop, the same loop the search runs on, and the search yields that loop only at a few `await asyncio.sleep(0)` points per turn. So a turn's `turn_start` and `candidates` events, emitted before the search began, reached disk only when the search next yielded — and the viewer, which tails the file, showed the turn when the bot was already most of the way to deciding it. Decision: `emit` writes the line and flushes before returning. An append of a few kilobytes is well under a millisecond, which `tests/test_trace.py` already held the queue-based `emit` to and still holds this one to. The `close` coroutine stays, since callers bridge to poke-env's loop to call it. The tail poll stays at 0.25 s; the trace list poll goes from 1.5 s to 1 s.

**The phase.** A turn's events land in a fixed order — `turn_start`, the search's, `equilibrium` — so a live trace whose newest turn has no equilibrium is a bot thinking, and one whose newest turn has one is a bot waiting for the opponent, and the pill says so with the seconds since. What the trace cannot say is what happens between battles, since there is no trace then: `scripts/ladder_live.py` writes `runs/live/status.json` (`searching`, `battle`, `reviewing`, `done`, with the game count), replaced atomically, and `/api/status` serves it as `live`. The pill reads both.

**Following.** The ladder writes one file per game, and the viewer pinned the reader to the file they were on, offering a button for the newer live one — which, per game, is not following. Now a reader who has not chosen a trace by hand and whose battle has ended is moved to the newest live one; a reader who picked from the list keeps the button.

Consequences: `champions/trace/writer.py`, `champions/viewer/server.py` (`live` on the status), `champions/viewer/static/` (the pill, the auto-follow, the tick), `scripts/ladder_live.py` (`StatusFile`, `play(status=)`), tests in `test_ladder_live.py` and `test_viewer.py`. The writer's docstring and `docs/07-observability.md`'s "never blocks the decision path" are now "writes before returning, under a millisecond".

## D82. The viewer asks a ladder run to stop through a flag file read between games; candidate scores are the row's expected payoff against the opponent's equilibrium mix; the half-screen layout is three narrow columns — 2026-09-14, Claude Code

Context: three requests from Alex after watching the live viewer beside an editor: a way to stop the bot that lets the current game finish, the score the bot gives each candidate, and a page that holds together at half a screen.

**The stop.** The ladder run is started from a terminal (`make ladder-live`), not from the viewer's control panel, so the viewer has no process to signal and should not acquire one: killing a run mid-game forfeits a rated battle. What it can ask is "finish this game, then stop". Decision: `POST /api/live/stop` writes `runs/live/stop`; `play` in `scripts/ladder_live.py` checks for it before every search, after the coach's review, and ends the run there, recording `stopped` on the status file; `POST /api/live/resume` removes it. A stale flag from an earlier run is cleared when a run starts, so a run never stops on a request nobody made this session. The control sits in the bar, shown only while a ladder run's status file says it is active, and the phase pill says "stopping after this game" once asked.

**The scores.** The one-ply agent's scored `candidates` event already carries the payoff matrix, the opponent's columns and equilibrium mix, the game value, and per row the policy score and equilibrium probability; the viewer showed none of it and left the equilibrium block reading "not computed yet". Decision: the candidates table shows, per row, its expected win probability against the opponent's mix (the matrix row dotted with the column mix), its minimum over the opponent's columns, its equilibrium weight and its policy score, sorted by weight then score, with the support highlighted; the strategy block shows the game value, pure or mixed, the support size, `k`, the payoff model and the opponent's expected replies with their weights. Rows before the search has run keep the pending rendering, which is what a thinking bot shows live. Nothing new is emitted; the trace already had it.

**Half a screen.** The 1180px breakpoint stacked the decision pane under the field, and at 960px that cut the scene off and squeezed both halves. Decision: down to 880px the layout is three narrow columns (a 124px spine, the field, the decision pane), each scrolling in its own column, and the top bar drops its prose (the brand's subtitle, the battle meta) so the pills stay whole; below 880px it stacks as before. Checked by screenshot at 960 by 1040 with a headless browser, which is how the layout should be checked from now on rather than in a stub DOM.

Consequences: `champions/viewer/server.py` (the two routes, `stop_requested` on `live`), `champions/viewer/static/` (the ladder group, the scored table, the strategy block, the breakpoints), `scripts/ladder_live.py` (`play(stop=)`, `STOP_NAME`), one test each in `tests/test_viewer.py` and `tests/test_ladder_live.py`.

## D83. The viewer shows the bot's Elo, GXE, Glicko, record and rank from Showdown's public JSON, cached a minute; rank is the position in the published top 500 or "not in top 500" — 2026-09-14, Claude Code

Context: Alex asked for the live Elo and ladder rank in the viewer. Elo arrives in the protocol after every rated game and the trace records it on `battle_end`; rank does not arrive at all, and neither do GXE or the Glicko estimate, which are the numbers `docs/06` section 6 reports.

Decision: `champions/viewer/ladder.py` fetches two public endpoints — `pokemonshowdown.com/users/<user>.json` for the user's ratings per format (elo, gxe, rpr, rprd, w, l) and `pokemonshowdown.com/ladder/<format>.json` for the top 500 in order — and the viewer serves the join on `/api/ladder?user=&format=`, cached sixty seconds per user and format so the viewer never polls the site harder than a person refreshing the ladder page. Rank is the user's position in the top 500 when present and "not in top 500" otherwise, because the site publishes no rank beyond it. Who to look up comes from the ladder run's status file, which now carries the account and format, or failing that from a rated battle's trace (`battle_start.player_username` with `battle_end.rating` non-null); self-play traces name no ladder account and show nothing. The client refetches when a rated battle ends, which is when the number moves, and otherwise on the minute. Failures are reported in the result, not raised: an unrated user, a site that will not answer, a ladder that will not answer after the user did.

Consequences: `champions/viewer/ladder.py`, `champions/viewer/server.py` (`/api/ladder`, `create_app(ladder=)` for tests), `champions/viewer/static/` (the rating group in the bar), `scripts/ladder_live.py` (`StatusFile(run=)` writes username and format), two tests in `tests/test_viewer.py` on a fake fetcher and one in `tests/test_ladder_live.py`. Checked against the real site with the bot's account.

## D84. The viewer lists games in the side pane as "vs opponent, result", one entry per game with the coach's review behind it; a finished game opens as its review; the rating shown is the configured account's — 2026-09-14, Claude Code

Context: Alex found switching between games confusing. The top bar held a dropdown of file names, a reviewed game appeared twice (the trace and its `.review` copy), and the coach's verdict had to be found by picking the second entry. The Elo block also did not appear on his screen, because the running viewer predated the route and because the lookup keyed on a status file the running ladder script had not written.

**The list.** The dropdown leaves the top bar. The side pane, above the turn spine, lists every game in the directory newest first: the bot's name, "vs", the opponent, and the result as a colour-coded pill — green win, red loss, amber tie, a pulsing "in progress" while the file is being written — with a "coach" mark when a review exists. At half width the row is the opponent and the result, since the bot is always us. The listing (`/api/traces`) now carries `player`, `result`, `turns`, `is_review` and `review` (the id of the reviewed copy), the result read from the file's last complete line so a directory of finished runs still costs two short reads per file per poll. The hidden `select` remains the one place the current choice is kept, so the follow, pin and live logic did not change.

**One entry per game.** The coach's overlay is a copy of the trace with the analysis interleaved (D76), so the reviewed copy is the trace plus more. Reviews are not listed; each game's entry opens the review when one exists and the game is over, the plain trace otherwise, and a game on screen that finishes and gets reviewed is reopened as its review the moment the review lands — which, with the coach between games (D79), is before the next game starts. The review is the default view of a finished game.

**The rating's subject.** `/api/status` now names the bot account from `.env` (`PS_USERNAME`, never the password), and the Elo block looks that account up whatever is on screen; the ladder status file and the trace remain fallbacks. The block also needs the viewer restarted to exist at all, since the server's routes do not reload the way its static files do.

Consequences: `champions/viewer/server.py` (`summary`, `_last_event`, `_account`), `champions/viewer/static/` (the games list, `openIdFor`, the hidden picker), two tests in `tests/test_viewer.py`.

## D85. After the first ten ladder games (3-7): the belief agent plays the ladder, the turn model resolves the status moves the games were lost to, the opponent's columns aim at both our slots and are ranked by threat, and the evaluation asks who moves first — 2026-09-14, Claude Code

Context: the first ten rated games on the Reg M-C ladder (`oneply`, `regmb-worlds`, `runs/live/ledger.ndjson`) went 3 wins, 7 losses. Alex asked for the losses analysed, the engine improved, and the cycle repeated (ten games, analyse, improve) up to five times while away, with the implementation left to Claude Code's judgement. This entry is the first cycle's analysis and what it changed.

**What the reviews said.** The coach's `luck` column — the played line's expected value minus the position that followed — was 40 to 70 win-probability points in the wrong direction on the decisive turn of every loss. That is not luck; it is the one-turn model expecting positions it could not have. Reading the traces beside the reviews, the losses were to four things the model scored as a pass: Trick Room (three of seven losses: R3pulse6, remembering never, gdog6969), Follow Me plus Expanding Force on Psychic Terrain (three opponents led Indeedee), set-up that went unpunished (Quiver Dance, Swords Dance, Calm Mind), and Tailwind. Underneath all four was one structural fact: the shipping agent modelled the opponent from revealed moves only, so on turn one its matrix had a single "unrevealed + unrevealed" column and every turn-one decision was an argmax against an opponent doing nothing (the trace of the R3pulse6 game shows it: game value 0.61 with one column, then 0.90 on turn two with Trick Room already up). And every opponent move the model did consider was aimed at our first slot, never the second.

**Decisions, in order of what they fixed.**

1. **The belief agent plays the ladder.** The M-B corpus on this box had 500 replays; M-C's Bo3 ladder (Force Open Team Sheets) had 800 more by this afternoon, scraped in the background, and `make priors` built the set prior from all 1,300 (15,288 sets, 234 species; Sneasler, Rillaboom, Kingambit, Incineroar, Salamence the most seen). `scripts/ladder_live.py --agent belief` is now how the cycles are played. The belief supplies the three things the reviews were missing: opponent columns on turn one, coherent opponent stats instead of 32 points in every stat, and our own items and abilities in the damage calculation (Life Orb Basculegion's recoil, Multiscale, Adaptability), which `oneply` never had. M5's head-to-head (D58, deferred) is still unmeasured against people; this is a judgement that an agent which sees the opponent's likely moves on turn one cannot be worse than one that sees none, on positions where the difference decided the game.

2. **The turn model resolves the doubles interactions**, each read from the move's dex entry where the dex carries it (`champions/search/payoff.py`): Fake Out's flinch and its failure off the first turn; Follow Me and Rage Powder redirecting single-target moves (not spread, not Grass into powder); Helping Hand; every `boosts`, `self.boosts` and 100% `secondary` (Swords Dance, Quiver Dance, Icy Wind, Snarl, Close Combat's drop, Parting Shot's drop); status infliction with type immunities and terrain blocks; Trick Room set and ended; Tailwind, Reflect, Light Screen, Aurora Veil, Wide Guard, Quick Guard; weather and terrain set and their damage modifiers, Expanding Force turning spread on Psychic Terrain, priority failing into a grounded target there, Grassy Glide's priority; Foul Play, Body Press, Psyshock, Low Kick, Grass Knot, Heavy Slam, Last Respects, Knock Off, Facade, Hex, Acrobatics, Super Fang and fixed damage; Sucker Punch failing unless its target is attacking and has not moved; recoil, Life Orb recoil when the item is known, drain, healing, multi-hit as the expected count; accuracy folded into the roll buckets (the knockout probability exact, the miss carried in the non-knockout bucket's mean, so the sixteen-branch bound stands); self-switching moves; a consecutive Protect succeeding one time in three per repeat (the agent had pressed Protect three turns running with a lone Pokemon); and our own Mega Evolution before the turn is ordered, stats shifted by the change in base stat. Branch states are copy-on-write instead of deep copies, which is what pays for the extra resolution: the payoff phase of a live turn is 0.1 to 0.2 s at twelve rows by twenty-four columns.

3. **The opponent's columns aim at every living slot of ours and are ranked.** `opponent_candidates` gives each single-target move one option per target and Helping Hand its partner, scores every joint option on the heuristic's own scale from their side (damage fraction, knockouts, Fake Out on its turn, Trick Room and Tailwind when they would flip the race, redirection and Helping Hand with Protect), and keeps the top `DEFAULT_COLUMN_K = 24`. Revealed moves come first, then believed ones, six per slot. The row budget `DEFAULT_K` rose from 10 to 12 (D69's union result and the clock headroom). By D67's rule a budget change is Alex's; it is made under the standing instruction for these cycles and is one line to revert.

4. **The belief reaches the candidate policy.** `OnePlyAgent._search` writes each foe's believed moves onto the search snapshot (`believed_moves` on the view, after the trace's own snapshot was emitted, so the trace records what was observed); `Board._revealed` reads them, so an unrevealed move can make a slot threatened and Protect can rank as an answer to it.

5. **The evaluation asks who moves first.** `speed_advantage`, in [-1, 1]: each of our active Pokemon against each of theirs, +1 if ours moves first, -1 if theirs does, with Trick Room reversing the comparison, Tailwind, paralysis and stages applied, and an unrevealed Speed taken at 16 points rather than the payoff model's pessimistic 32 (pessimism has no sign once the room can reverse it). This is the interaction the file's own docstring said a side-difference model could not express; as a comparison it can. Its weight is hand-set at 0.40 and carried as `SUPPLEMENTARY_WEIGHTS`, filled in when the fitted file lacks it and named on the model's `source`, so a number built on it is never mistaken for a fitted one. The next `make fit-eval` fits it with the rest.

**What is not changed.** The coach still reviews with revealed moves only (its `SetSource` seam takes the prior; not wired this cycle). The M6 weights are still the M-B fit, lent. The two-ply and simulator arms are untouched apart from `next_turn` clearing the model's one-turn markers. Abilities and items beyond the effects table, secondaries under 100%, Substitute, Taunt, Encore and the opponent's switches are still absent.

**What the cycle measures.** The next ten games under `belief`, in the same ledger. Ten games is a coin-flip-wide interval; the reviews' `luck` column on the losses is the better instrument for whether the model's expectations now match what happens, and that is what the next analysis reads first.

Consequences: `champions/search/payoff.py` (rewritten resolution, copy-on-write), `champions/search/policy.py` (`opponent_candidates`, `_threat`, `Board._revealed`, `DEFAULT_K`, `DEFAULT_COLUMN_K`), `champions/search/evaluate.py` (`speed_advantage`, `SUPPLEMENTARY_WEIGHTS`), `champions/search/twoply.py` (`next_turn`), `champions/agents/oneply.py` (`column_k`, `_annotate_belief`), `tests/test_turn_effects.py` (nineteen tests), `tests/test_payoff.py` and `tests/test_coach.py` (updated expectations), `data/priors/` (gitignored, rebuilt), the corpus (1,300 replays).

## D86. After the first belief cycle (6-4): the lead is chosen by a one-turn sweep at preview, a switch places the incoming Pokemon, an unobserved spread is two maxed stats named by the nature, and the coach reviews against the corpus prior — 2026-09-14, Claude Code

Context: the first cycle under D85 — ten rated games with `belief` on `regmb-worlds` — went 6 wins, 4 losses, against 3-7 for `oneply` before it. The four losses were read from the traces while the cycle ran. Alex returned before the second cycle and asked to stop and analyse together, so everything below is **built, tested and not yet played**; the next run is the measurement.

**What the four losses had in common.** Three began at preview. The bring and the lead were `random_teampreview` — four of six and a lead drawn by chance, which the M9 spec had noted and D39/D56 had left without a model — and the draw put Sneasler (four times weak to Psychic) or Kingambit (weak to Ground) on the field opposite Indeedee's terrain or Glimmora's Earth Power (bandamnjohnny, 5stack, MXA42). The model then priced turn one at 9-13% before a move was made and had no answer, because the one answer, switching the exposed lead out, was scored as an empty slot: `place_incoming` was off for the one-ply model, so a switch gave up the action *and* the incoming Pokemon's presence, and never ranked. The fourth loss (skaidrammm) and MXA42 shared a second cause: Glimmora's Earth Power was priced at 11 points of Special Attack, the uniform allocation the spread belief starts from, when a registered Glimmora carries 32; Kingambit was left in front of a hit the model had at survivable and was not. The coach's `luck` column agreed: the worst turn of every loss was 31 to 55 points of expectation the model could not have had.

**Decisions.**

1. **The lead sweep** (`champions/search/lead.py`, `OnePlyAgent.teampreview`). At preview, every one of our fifteen lead pairs is priced against the opponent's fifteen with the agent's own turn-one machinery: `enumerate_joint` for our options, the heuristic policy at `k = 8`, the opponent's columns from the corpus prior at 12, the belief's stats and effects, the entry effects the belief expects (a Surge terrain, weather, Intimidate), and the equilibrium value of the opening. The pair with the best mean leads; the back two are the remaining Pokemon whose pairs scored best. Anytime under `preview_budget_s = 8.0` (poke-env's `teampreview` is synchronous, so the sweep blocks the loop; eight seconds covered 8-10 of the 15 rounds in the smoke run, about 150 openings), the opponents visited in a seeded order in rounds so every pair has seen the same opponents at the deadline, the seed the battle's so a replayed preview repeats. The trace's `preview_decision` carries the pair and single values, rounds and elapsed. It is not a bring-4 model and says so on the event (`pending` unchanged). In the test the sweep leads Kingambit and keeps Sneasler off Indeedee's terrain. **One open observation**: in a mirror smoke run both belief agents priced their own openings at 0.88-0.98, because each side knows its own four moves exactly and the other's from a prior; the sweep's *ordering* is what is used, but the level says the opening value is not a calibrated probability.

2. **A switch places the incoming Pokemon** in the one-ply model (`place_incoming=True` in `OnePlyAgent`, `BeliefAgent` and the coach's models). The opponent's moves then resolve against what is on the field and the evaluation counts it; the outgoing Pokemon is benched with its boosts cleared. The one-ply bias against switching is now only the lost action, which is the real cost. M2's reason for the empty slot — "the value of having it in play is a next-turn question" — was true of the *benefit*; the *hit it takes this turn* is a this-turn question, and it was the one that decided three games.

3. **An unobserved spread is two maxed stats** (`SpreadBelief.allocation`, `preferred_stats`). 66 points under a cap of 32 is two maxed stats and two over, and the nature, which the open sheets label and the particle carries, names one of them. The raised stat is maxed first; the partner is Speed for an attacker with base Speed of 70 or more, HP otherwise, the better offence for a Speed nature, HP for a defensive one; a neutral nature takes the better offence. Evidence that caps a preferred stat sends the budget on as before. The uniform 11-everywhere mean was the right prior over allocations and the wrong estimate of any one of them; D85's live games showed the cost. `docs/03` section 5's coverage numbers are unaffected (they are about the box, not the point inside it); the belief's stat error, if `make eval-belief` is rerun, will move.

4. **The coach reviews against the corpus prior** where it has no truth table (`champions/coach/truth.py` `PriorSource`, `analyze.prior_models`, `scripts/review.py --no-prior` to refuse it): the prior's most common set for the species and its move frequencies at the belief agent's own threshold. The information state is named `corpus-prior` on every review, between `revealed-moves-only` and `open-sheet`. It is what the belief agent actually played under, and a review that solved turn one against an opponent doing nothing could not say whether the turn-one choice was right. The two reviews written under it this cycle read sensibly; the calibration (D77) was fitted under revealed-only and is not re-run.

**What was not done.** Nothing here is measured against people yet. The consecutive-Protect fix (D85) held: no game this cycle repeated Protect with a lone Pokemon. Double Protect on turn one still appears in the smoke traces; a one-ply model has no tempo and cannot see what a wasted turn costs, and that stays open. The full test suite passes at 615 with the new tests (`tests/test_lead.py`, one each in `tests/test_payoff.py`, `tests/test_belief.py`, `tests/test_coach.py`).

Consequences: `champions/search/lead.py` (new), `champions/agents/oneply.py` (`teampreview`, `_lead_choice`, `_believed_ability`, `place_incoming`), `champions/agents/belief_agent.py` (`_believed_ability`, `place_incoming`), `champions/belief/spreads.py` (`allocation`, `preferred_stats`, `FAST_ENOUGH_BASE_SPEED`), `champions/coach/truth.py` (`PriorSource`), `champions/coach/analyze.py` (`prior_models`, `CORPUS_PRIOR`, `place_incoming`), `scripts/review.py` (`--no-prior`), the four test files.

## D87. After the second belief cycle (1-4, stopped at five): type-changing abilities are modelled, the belief prefers what the battle revealed, the row set is diversified, the adaptive agent runs on the belief, the default team is Alex's Reg M-C team, and the viewer starts a ladder run — 2026-09-14, Claude Code

Context: the second cycle, ten games of `belief` with D86's changes, was stopped from the viewer after five at 1-4. Alex then asked for everything the cycle-two analysis proposed, plus a new team and a way to start the ladder from the viewer, and to stop there. This is what was built; the two measurements it started (the evaluation refit and the two-ply mirror) are recorded in `docs/STATUS.md` as they stand, not here.

**What the five games showed, beyond D86.**

- **Pixilate.** In the vad3retro loss the model priced Mega Gardevoir's Hyper Voice as a Normal-type move: Basculegion, a Ghost, immune; Dragonite neutral. Pixilate, Aerilate, Refrigerate and Galvanize were in the effects table's *unmodelled* bucket, which is a 1.0 multiplier and no type change. In the game it was a 1.2x Fairy spread move from +1 Special Attack and it swept both. Decision: `TYPE_CHANGING_ABILITIES` in `champions/belief/effects.py`; `SetEffects.type_override`; the payoff model carries the changed type through the defender's effects, the immunity check, STAB and the chart. Mega Salamence's Aerilate Hyper Voice is the other common case.
- **A revealed ability outranks the prior.** The particle for Gardevoir still carried Trace after the Mega, because the prior's sets name the base forme's ability and the filter had nothing to eliminate on. `BeliefEffects._set_for` now overlays an ability or item the view shows (poke-env reports both once revealed) onto the hypothesis.
- **The Fake Out crowding.** At preview every row the budget held led with Fake Out; at `k = 12` live, most did. `HeuristicPolicy.scored` now goes through `diversify`: no (slot, move) fills more than half the budget, the skipped rows filling the tail if nothing else does. The pruning guard's numbers (`docs/pruning-guard.md`) describe the ranking before this and are not rerun.
- **Tempo.** Twice the agent double-Protected while the opponent set up (Rock Polish, Calm Mind), and once the equilibrium put 84% on double Protect. A one-ply model has no cost for a turn given away. Rather than a hand rule, `AdaptiveBeliefAgent` (`adaptive-belief` in the registry) puts M11's escalating agent on the belief's seams — one ply when decisive, two when not, on the allocated clock — so the second ply can be measured under a model in which status moves do something. Escalation cost 3-10 s a turn in the mirror against budgets of 32-45 s.
- **Contention.** The preview sweep's budget is wall-clock, and with two local self-play runs sharing the box it reached 4-5 of 15 rounds; alone, 10. Local runs and the live bot should not share a machine.
- **The team.** `regmb-worlds` is a Reg M-B team: two Pokemon four times weak to Ice, no speed control, no redirection, and the sweep kept leading Dragonite with Garchomp into Icy Wind. Alex supplied a Reg M-C team, `regmc-perish` (Mega Gengar, Mega Froslass, Incineroar, Politoed, Archaludon, Rillaboom; Perish Song, Aurora Veil, Fake Out, Eject Button), with its stat points; it is `DEFAULT`, validated by the simulator like the others. Nothing is measured on it yet; the model has no Perish Song, no Encore and no Eject Button, so three of its moves are passes to the search, which is the first thing the next analysis should look at.
- **The viewer starts the ladder.** `POST /api/live/start` launches `scripts/ladder_live.py` detached (a rated game must not die with the page) in the viewer's directory with the belief agent and the default team; a blank count plays until the existing stop button. The ladder group shows the form when idle and the run's controls while it plays. `make ladder-summary` no longer crashes on an opponent name the Windows console cannot encode.
- **Entry abilities and Weather Ball.** The new team is built on them: Mega Froslass's Snow Warning is what makes Aurora Veil legal to set and Blizzard sure to hit, and Politoed's Drizzle is what makes Weather Ball a 100-power Water move. The model now fires an ability on arrival — a Mega whose ability changed, a switch placed by `place_incoming` — for weather, Surge terrains and Intimidate (`_entry_effects`), types Weather Ball by the weather, and gives Blizzard, Thunder and Hurricane their weather accuracy. The lead sweep shares the same tables.
- **Status moves at an ally.** The request lists Encore, Will-O-Wisp or Thunder Wave aimed at our own partner as legal, and the smoke games spent a turn on Encore into Archaludon. `HeuristicPolicy` disqualifies a status move aimed at an ally unless the dex marks it as having an ally use (`allyanim`) or its target is an ally target.
- **The default agent is `adaptive-belief`**, on the ladder script and the viewer's start button. The mirror stood at 17-7 over `belief` (Wilson 0.51-0.85, n = 24) when the decision was made; the run continues to 49 and `docs/STATUS.md` carries the final count. By D67's rule that is Alex's decision; it is made under the standing instruction and is one default to revert.

Consequences: `champions/belief/effects.py`, `champions/belief/hypothesis.py`, `champions/search/payoff.py` (type override, `_entry_effects`, Weather Ball, weather accuracy), `champions/search/policy.py` (`diversify`, `_misaimed_status`), `champions/agents/belief_agent.py` (`AdaptiveBeliefAgent`), `scripts/selfplay.py` (registry), `champions/teams.py` and `data/teams/regmc-perish.txt`, `champions/viewer/server.py` (`start_ladder_run`, `/api/live/start`), the viewer's `index.html` and `app.js`, `scripts/ladder_live.py` (console encoding, `DEFAULT_AGENT`), tests in `tests/test_turn_effects.py`, `test_policy.py`, `test_lead.py`, `test_teams.py`, `test_viewer.py`; `data/priors/` rebuilt from 1,445 replays (the M-C Bo3 ladder had 945 in total).

## D88. Two-turn moves charge unless the weather waives it, the evaluation values holding a weather the team uses, and the Perish Song team is replaced by a simpler one — 2026-09-14, Claude Code

Context: watching the new team's smoke games, Alex saw the agent Mega Evolve Froslass for snow and then set rain again, and never use the fact that Electro Shot fires in one turn under rain. Both are mechanics the model did not have: a two-turn move was priced as an instant hit at full power, and no feature of the evaluation could tell one weather from another, so snow replacing rain cost nothing and rain replacing snow gained nothing.

**Decisions.**

1. **Charge moves** (`flags.charge` in the dex: Electro Shot, Solar Beam, Solar Blade, Meteor Beam, Sky Attack, Fly, Dig, Dive, Bounce, Phantom Force). On its charge turn the move deals nothing, marks the user as preparing, and applies the charge's own boost (Electro Shot and Meteor Beam raise Special Attack); the weather that waives the charge (rain for Electro Shot, sun for the Solar moves, `CHARGE_WAIVED_BY`) makes it fire at once, as does a user poke-env reports as already `preparing`. The heuristic policy ranks a charging move as set-up rather than as an attack, so Electro Shot outside rain no longer crowds the rows as a 130-power hit. Solar Beam's halved power in other weathers is not modelled.

2. **`weather_synergy`** in the evaluation: per side, the count of Pokemon in play carrying a move or ability that wants the weather that is up (`WEATHER_USERS`: Weather Ball under any, Electro Shot, Thunder, Hurricane and Swift Swim under rain, the Solar moves and Chlorophyll under sun, Blizzard, Aurora Veil and Slush Rush under snow, Sand Rush and Sand Force under sand), ours minus theirs. Ours from exact moves, theirs from revealed and believed. Hand weight 0.25, carried in `SUPPLEMENTARY_WEIGHTS` like `speed_advantage` (D85), fitted by the next `make fit-eval`. With it, Politoed's rain is worth two on this team (Weather Ball, Electro Shot) and Froslass's snow two (Blizzard, Aurora Veil), so the trade the agent made is at least priced; the payoff of the turn (Electro Shot firing or charging) is what separates them.

3. **The team.** In the same smoke games the agent used Perish Song with its own Pokemon on the field and without Mega Gengar's Shadow Tag to hold the opponent in, and lost to its own clock: Perish Song is a pass to the model, so the search cannot see that it ends its own side. Alex scrapped `regmc-perish` for a team the model can play, `regmc-mence` (Mega Salamence with Tailwind and Hyper Voice, Sneasler, Mega Floette-Eternal, Rillaboom, Incineroar, Gholdengo; every move in it is one the model resolves), which is `DEFAULT` and validated like the others. D87's team paragraph stands as the record; the file it names is gone.

What this does not do is plan: the model still sees one turn, so "set rain now for Electro Shot next turn" is visible only through the feature and through the second ply of `adaptive-belief`. Perish Song, Encore and Eject Button remain passes to the search.

Consequences: `champions/teams.py` and `data/teams/regmc-mence.txt` (replacing `regmc-perish.txt`), `champions/search/payoff.py` (`_charges_this_turn`, `charge_waived`, `CHARGE_WAIVED_BY`, `CHARGE_BOOSTS`), `champions/search/evaluate.py` (`weather_synergy`, `WEATHER_USERS`), `champions/search/policy.py` (the charge rule, `Board.charged`), tests in `tests/test_turn_effects.py`, `test_policy.py`.

## D89. The adaptive belief agent is not apart from the belief agent at 48 games; the default goes back to `belief` — 2026-09-14, Claude Code

Context: D87 made `adaptive-belief` the ladder default on a 17-7 start in its mirror against `belief` on the worlds team. The mirror ran on to 48 games and finished 26-22 (Wilson 0.40-0.67).

Decision: not apart from one half, so by D67's rule the incumbent stays: `DEFAULT_AGENT` in `scripts/ladder_live.py` and `DEFAULT_LADDER_AGENT` in the viewer are `belief` again. The agent stays in the registry; the escalation costs 3-10 s a turn against a 32-45 s budget, so the question is win rate alone, and a 5-point gap needs about 1,500 games per arm (D71). The early lead was the interval, not the agent. Traces in `runs/mc-adaptive/worlds/`, gitignored, seeds 0 and 1.

Consequences: `scripts/ladder_live.py`, `champions/viewer/server.py`, the viewer's `index.html`, `Makefile`, QUICKSTART, `docs/STATUS.md`.

## D90. The evaluation is refit on M-C, a fit may no longer write a weight it did not measure, and the lead sweep prices our own side of team preview — 2026-09-14, Claude Code

Context: Alex ran the bot for 76 more ladder games on `regmc-mence` (36-40, Wilson 0.37-0.58) and asked what the data supports. Three cheap items came out of reading it, and each turned up a defect while being built. The coach's reviews say the losses are not ex-ante mistakes: ex-ante loss per game is 22.6 points in wins against 30.0 in losses, while ex-post loss is 46.9 against 80.7 and mean luck per decision is -0.012 against +0.053. Luck is what the one-turn model does not represent, so the gap is model error rather than choice error.

Decision, in three parts.

**1. The M-C evaluation is refit, ending the M-B loan (D80).** `data/eval/weights.gen9championsvgc2026regmc.json` is fit on the 196 self-play battles left running at the end of the last session (`runs/mc-selfplay/worlds`, 3,112 positions): held-out log loss 0.4006 against a base rate's 0.693, ECE 0.0533, AUC 0.8921. `speed_advantage` is fit rather than hand-set, at +0.2110 with an interval of [+0.057, +0.352]. Five weights come from the corpus fit and `docs/eval-calibration.md` names each.

**2. A fit may not write a weight no source measured.** Two rules, both in `scripts/fit_eval.py`. A feature constant in *every* source is left out of the file entirely, and `champions.search.evaluate.load_model` fills it from `BOOTSTRAP_WEIGHTS` and says so on the model's `source`. A feature constant in the *shipping* source is taken from any source that varied it, settled or not, because a degenerate [0, 0] interval is no evidence where a wide one is weak evidence. The first M-C fit, written earlier in this session under the old rules, shipped `speed_control`, `hazard_advantage` and `weather_synergy` at exactly 0.0 -- an agent that believes Tailwind is worth nothing, on a team whose Mega Salamence carries it. They are now +0.1983 and +0.8773 from the corpus and hand-set at 0.25 respectively. The shipped metrics are unchanged, because a feature constant in self-play is constant in its test split too.

**3. The lead sweep prices our own side, and three defects in it are fixed.** Under D86 only the *opponent's* entry effects fired at preview, so our own Grassy Surge, our own Intimidate and Sneasler's Grassy Seed under Unburden were all invisible to the sweep that chooses the lead. Now both sides' entry effects fire, in speed order so the slower Surge's terrain stands, ties going to us arriving first because the project forbids settling them with the coin flip the game uses. `champions.search.payoff` gained `TERRAIN_SEEDS` and `consume_seeds` (the seed pops, the stat rises, the item is gone) and `unburden_active`, which doubles Speed for a Pokemon of ours holding Unburden and no item. Unburden is read on our side only: the snapshot cannot tell an unknown item from a used one, and the ability announces nothing, so the opponent's is the belief's to infer and does not yet.

The three defects:

- **The opponent's whole team was priced at zero HP.** poke-env reports `current_hp_fraction` as 0 for a Pokemon it has never seen in battle, which is precisely what `teampreview_opponent_team` is. Every opening in the 76 live games therefore scored a mean of 0.936, best pair to worst spanning 0.098, because the evaluation read four healthy Pokemon against a side with none. `lead.opening` now puts every Pokemon at full health, which at team preview every Pokemon is.
- **Their six were weighed against our four.** `evaluate.alive` caps the opponent's count at the bring; `_hp_total` summed over whatever was in play, which in a battle is at most four but at preview is six. It now derives their total as the bring minus the damage done to it, which is identical everywhere else and is why nothing in the fit moved. Under the refit weights a dead-even opening scored 0.0103 before this.
- **A round could be committed half finished.** The sweep visits the opponent's pairs in rounds and averages, but the wall-clock cut fell anywhere, so some of our pairs had faced one more opponent than the others and the mean that chooses the lead compared pairs scored against different opponents. Rounds now commit whole or not at all, which also makes the choice a function of the seed and the completed round count rather than of where the clock fell; `lead_sweep` takes `max_rounds` so the anytime test is reproducible from a seed as `CLAUDE.md` requires.

**The preview budget is 8 s, raised to 20 s.** The full 15 rounds take 8.2 s on this box with nothing else running. At 8 s the live games reached 7 or 8 rounds in 48 of 76 and all 15 in none, so live runs about half the bench's speed; 20 s is that margin, and Showdown's VGC Timer allows 90 s at preview.

Rationale, and what is and is not claimed. Together these change what the sweep reports from a saturated 0.94 to a spread of 0.35 to 0.61 around a coin flip on the benchmark matchup, and they change which pair it leads: Sneasler and Incineroar rather than Rillaboom and Gholdengo on the corpus's second most common M-C team. That is a better-conditioned number, not a measured gain. **None of this has played a rated game**, and the D71 arithmetic still holds: a 5-point effect needs about 1,500 games an arm, so the next ladder cycle will not settle it either. What is claimed is narrower and checkable: the preview value was wrong in two ways that a dead-even opening exposes, the ordering it produced was compressed into a region where pair differences were 0.01 wide, and a weight of exactly zero was being shipped for three features no source had measured.

The live data also says two things this entry does not act on. The bot brought Sneasler in 20 of 76 games and never once led Rillaboom with it; the 47 corpus games humans played on this exact six lead that pair 7 times and win 5. And turn-one win probability averaged 0.533 in games won against 0.523 in games lost, so the opening value did not distinguish a good matchup from a bad one -- which is what the two fixes above were meant to be a precondition for, not a substitute for.

Consequences: `champions/search/payoff.py` (`TERRAIN_SEEDS`, `consume_seeds`, `unburden_active`, `UNBURDEN_SPEED`, `entry_effects` now public), `champions/search/lead.py`, `champions/search/evaluate.py`, `scripts/fit_eval.py`, `data/eval/weights.gen9championsvgc2026regmc.json`, `docs/eval-calibration.md`, and tests in `test_lead.py` and `test_evaluate.py`. `docs/STATUS.md`'s open item 4 (the evaluation weights are an M-B loan) is closed; item 2 (the opening values are ordered, not calibrated) is improved but not closed, because nothing has measured them against outcomes. The next measurement is a ladder cycle on the changed sweep, and the first thing to read off it is whether the bring and lead distribution moves toward what humans play on this team.

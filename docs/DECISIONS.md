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

# Implementation B: the learned candidate prior

Design, 2026-08-29, Claude Code. Status: approved, not yet built.

`docs/04-decision-engine.md` section 3 specifies three candidate providers,
benchmarked identically. A is built and measured (`docs/pruning-guard.md`, D61).
This is B, the learned prior: a model fit to the replay corpus that scores each
legal action and returns the top `k`.

C, the language model provider, is blocked on a model API key and is not in
scope here.

## 1. What it has to beat

A discards 0.174 of the equilibrium's mass at `k = 10`, 95% [0.166, 0.185], over
6,745 positions and 750 battles. B is measured on the same positions in the same
run, because `discard.measure_many` takes a mapping of provider to callable and
solves each position once (D62).

**The intervals decide, not the point estimates.** If A's and B's 95% intervals
overlap, no difference has been demonstrated and A stays: it is the incumbent,
it needs no corpus, and it has no inference cost. This is fixed now rather than
after the numbers arrive.

A third provider is measured for free in the same run: the **union** of A's and
B's sets, five from each at `k = 10`. If neither dominates, the union may still
beat both, and it is a legitimate thing to ship.

## 2. The finding that shapes everything: the corpus is open-sheet

Every set in the corpus has `source = 'showteam'`, and 24,496 of 25,117 replays
have `sheets_revealed = 1` (read 2026-08-29; the backfill is still running and
`make corpus` is the only trustworthy count). The Bo3 ladder forces Open Team
Sheets, so **both players could see each other's species, items, abilities and
moves from preview onward**.

Our agent cannot. `CLAUDE.md` constraint 2 is that Open Team Sheets are always
declined, because Champions has no such mechanism and an agent that depends on
them does not transfer.

So the corpus records decisions made under strictly more information than the
agent will ever have. Two responses are possible and only one is right:

- **Reconstruct each side's view as if sheets were closed** — our six known
  exactly, theirs only as revealed by play. Features then match the information
  state the agent actually occupies.
- Include sheet knowledge as features. Rejected: it fits a model that cannot be
  served, and the mismatch would surface as an unexplained gap between offline
  accuracy and the discard rate.

The cost of the right choice is **label noise we cannot remove**. A human who
switched because they knew the opponent held a Choice Scarf made a choice our
features cannot explain. This is stated as a limitation, measured where
possible, and is the first hypothesis to test if B underperforms.

The 621 closed-sheet replays are too few to train on and are held back as a
clean evaluation slice — decisions made under our own information state. It is a
small slice and will carry a wide interval; it is a check, not a headline.

## 3. Components

### 3.1 `champions/corpus/replay_state.py`

Replay protocol log → per turn, per side, the observed state in
`champions.protocol.state.snapshot()`'s exact shape.

This is the expensive half and the half that survives a negative result:
`docs/STATUS.md` already lists `hazard_advantage` and the belief prior as
blocked on corpus-side state, and both want this.

It tracks what a snapshot carries and the existing parser does not: HP, status,
boosts, fainted flags, active slots, weather, field and side conditions, and
revealed moves per opposing Pokemon. It reuses `champions/protocol/parser.py`
for nickname resolution and line splitting rather than re-lexing the protocol.

**It is validated against our own games, not asserted.** `turn_start` on every
agent-view trace carries both `state` (the snapshot the agent saw) and `log`
(that turn's protocol lines). Replaying the accumulated log through this module
must reproduce that snapshot. 1,500 traces already exist to check it on. Fields
a replay observer genuinely cannot know — our exact stats and PP — are excluded
from the comparison and from the feature set, and the test names them rather
than skipping them silently.

### 3.2 `champions/search/policy_features.py`

One function, `option_features(snapshot, slot_index, option) -> np.ndarray`,
used by both the trainer and the live provider.

One function on purpose. M6 built `positions.py` for exactly this reason: two
paths to the same features is how a model quietly stops being served the inputs
it was fit on. Because `replay_state` emits snapshot-shaped dicts, the trainer
and the agent call the same code on the same shape.

Features, per (slot, option), all derived from the snapshot:

- the option: category, base power, type effectiveness against the resolved
  target, priority, whether it is a switch, whether the target is our own slot
- the acting Pokemon: HP fraction, boosts, status, whether it just came in
- the target: HP fraction, status, whether the average roll knocks it out
- the board: turn index, remaining counts both sides, weather, Trick Room,
  Tailwind, speed order against each opposing slot

Deliberately excluded: species identity as a free parameter. M4 found species
features did not transfer to unseen players. Species enters only through the
numbers it implies — stats, types, damage — which is the same discipline the
evaluation function follows.

### 3.3 `champions/search/learned.py`

A small MLP scoring one (state, option) pair to a scalar logit, shared across
options. Softmax over a slot's legal option set gives a distribution; the loss
is cross-entropy against the human's chosen option.

Scoring options independently and normalising over the set handles the variable
number of options per slot without padding to a fixed action space, and it is
what lets the same weights serve a slot with three legal moves and one with
twelve.

`LearnedPolicy` implements `PolicyProvider` with `name = "learned-prior"`. A
joint action scores as the sum of its two slots' logits — the same composition A
uses, so the benchmark compares the ranking rather than two formulations. Slot
interaction is out of scope for the same reason it is absent from A, and is the
obvious follow-up if B wins.

`UnionPolicy` takes two providers and interleaves their sets to `k`.

### 3.4 `scripts/fit_policy.py` (`make fit-policy`)

Builds the training set, fits, and writes `docs/policy-prior.md` — generated,
not hand written, the way `docs/eval-calibration.md` and `docs/pruning-guard.md`
are.

**Split by player, not by replay.** M4's finding was that leads predicted well
and transferred to unseen players while the bring-4 did not; a random split
would have hidden that. Held-out players never appear in training.

**Population.** Rated replays only, both players at or above the 75th percentile
of the rating distribution — 1269 at the 2026-08-29 reading, recomputed at fit
time rather than hardcoded, since ratings run 1000–1721 and the corpus grows. A
policy prior should
imitate players worth imitating, and D39 records the corpus as skill-confounded
by construction.

**Intervals over battles**, matching `fit.bootstrap_weights` and
`discard.summarise`. Positions inside one game share a board and a team.

Reported: top-`k` recall of the human's action on held-out players, against A's
top-`k` on the same positions; loss curves; and the closed-sheet slice as a
separate line.

### 3.5 Wiring

`PROVIDERS` in `scripts/discard_rate.py` gains `learned-prior` and the union;
`make discard` then produces the three-way table. `Makefile` and `CLAUDE.md`
gain the new target and doc row. The agent's default provider does not change
until the numbers say so.

## 4. Order of work

1. `replay_state.py` and its equivalence test against the traces. Nothing
   downstream is trustworthy until reconstruction is, and this is the piece that
   survives a negative result.
2. `policy_features.py`, tested on both a reconstructed snapshot and a live one.
3. `fit_policy.py` and the model. First number: top-`k` recall against A.
4. `LearnedPolicy`, then `make discard` for the three-way table.

Each step is a checkpoint. If step 3's recall is at or below A's, that is
reportable on its own and step 4 is a short confirmation rather than a hope.

## 5. Testing

TDD throughout, as with D61. Specifically:

- **Reconstruction**: the trace equivalence test above, per-turn, over a sample
  of real self-play traces; plus unit tests on hand-built protocol fragments for
  faints, switches, boosts, weather and side conditions.
- **Features**: the same position through `replay_state` and through a live
  snapshot produces identical vectors. This is the test that keeps the two paths
  one path.
- **Model**: fitting is deterministic from a seed (`CLAUDE.md`: any evaluation
  that cannot be reproduced from a seed is a bug), and a trained model scores a
  knockout above a resisted chip on a held-out position.
- **Provider**: `LearnedPolicy` returns at most `k`, is deterministic, and never
  returns an action that was not in the legal set.

## 6. What this will not do

- **No slot interaction.** A double Protect and a Protect plus an attack score
  the same way, exactly as in A.
- **No belief.** The provider takes `belief` and ignores it, as A does. Plumbing
  the posterior into candidate scoring is a separate change with its own
  measurement.
- **It does not learn the equilibrium.** It learns what strong humans played.
  Those differ, and the discard rate is the measurement of by how much — which
  is why the guard, not accuracy, is the shipping criterion.

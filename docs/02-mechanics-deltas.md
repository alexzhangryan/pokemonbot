# Verified Champions Mechanics and Engine Benchmarks

All facts here were read from the Showdown source (`smogon/pokemon-showdown`, cloned and built 2026-08-29) or measured on the built simulator. Nothing here is inferred from articles.

## 1. Format wiring

Reg M-B is defined in `config/formats.ts` as:

```
name: "[Gen 9 Champions] VGC 2026 Reg M-B"
mod: 'champions'
gameType: 'doubles'
bestOfDefault: true
ruleset: ['Flat Rules', 'VGC Timer', 'Open Team Sheets']
```

Reg M-A uses a separate mod, `championsregma`, so the two regulations are genuinely different data sets. Code selects the mod by format ID and never assumes one.

Resolved rule table for Reg M-B: picked team size 4, min and max team size 6, adjust level 50, team preview on. `Flat Rules` expands to Obtainable, Team Preview, Species Clause, Nickname Clause, Item Clause = 1, Adjust Level = 50, Picked Team Size = Auto, Min Team Size = 6, Cancel Mod, with Mythical and Restricted Legendary banned.

Note what is absent: there is no Sleep Clause and no Sleep Moves Clause in Flat Rules. Spore is unrestricted.

Measured legal pool: roughly 347 species, 148 non-nonstandard items, 75 legal Mega Stones, 76 Mega formes in the mod.

## 2. Open Team Sheets on the proxy

Champions itself has no open team sheet mechanism. Team preview shows six species and nothing else, permanently.

Showdown's Reg M-B carries an `Open Team Sheets` rule that prompts both players at preview and reveals, in the rule's own words, "the Pokémon and all non-stat information about them" if both accept. The Bo3 variant carries `Force Open Team Sheets` and always reveals.

Policy for this project:

- The agent always declines when prompted. Playing with information the target game never provides would produce an agent that does not transfer.
- The forced Bo3 variant is not a competitive target.
- Forced-sheet Bo3 replays are still consumed as training data, because they hand over complete labeled sets that exist in no other public source. Consuming them as data has nothing to do with playing under those rules. See `05-data-pipeline.md`.

The client must handle the prompt explicitly. An unhandled prompt at team preview risks stalling the agent at the one moment where the clock is least forgiving.

## 3. The stat formula is not the mainline formula

The single most important finding. From `data/mods/champions/scripts.ts`, at fixed level 50 with no Level Clause Mod:

$$\text{HP} = \text{base} + p_{\text{hp}} + 75, \qquad \text{stat}_i = \left(\text{base}_i + p_i + 20\right) \cdot \nu_i$$

where $p_i$ is the stat point allocation stored in the `evs` field and $\nu_i \in \{0.9, 1.0, 1.1\}$ is the nature multiplier, applied with 16 bit truncation.

Consequences:

- One stat point is exactly plus one to the final stat. The relationship is linear and integer valued, not the mainline quadratic with flooring.
- The values coincide with mainline at the endpoints. Base 100 with 0 points gives 120, matching 0 EVs and 31 IVs at level 50. With 32 points it gives 152, matching 252 EVs exactly. So a point is worth roughly 8 EVs and the 32 point cap equals the mainline 252 EV cap.
- The total budget of 66 points is worth roughly 520 EV equivalent against mainline's 510, so Champions spreads are marginally more generous overall, not less.
- Natures still exist and still matter. They are a hidden variable alongside the spread.

Every public damage calculator will be wrong for this format. The stat layer must be reimplemented from this formula, which is a small job rather than a risk, but skipping it silently corrupts everything downstream.

## 4. Other verified mechanic changes

Status, from `data/mods/champions/conditions.ts`:

- Paralysis: full paralysis chance is $1/8$, down from $1/4$.
- Sleep: duration is sampled from the multiset $\{2, 3, 3\}$, so a $1/3$ chance of waking on turn 2 and otherwise turn 3. There is no 1 turn sleep and no 4 turn sleep.
- Freeze: a hard 3 turn timer plus an independent $1/4$ thaw check each turn. Freeze always ends by turn 3.

Mechanics, from `scripts.ts`:

- `canTerastallize` returns null. Terastallization is fully disabled.
- Mega Evolution is driven by `item.megaStone`, so Mega Stones occupy the held item slot and are subject to Item Clause. Confirmed in a live replay as `|-mega|p1b: Swampert|Swampert|Swampertite`.
- Mega Evolutions do not revert after fainting.
- All moves with base PP above 20 are capped at 20, and PP is computed as if always fully PP-upped. Effective PP is $1.6 \times \min(\text{base PP}, 20)$.
- Trick Room speed underflow is removed.
- `formeChange`, `clearVolatile`, `modifyDamage`, `spreadMoveHit`, and `hitStepMoveHitLoop` are all overridden.

Abilities, from `abilities.ts`:

- Healer is 50 percent, up from 30.
- Unseen Fist now bypasses Protect on contact moves but deals one quarter damage when it does.
- Natural Cure has its `onCheckShow` removed, so status reveal behavior on switch out differs from mainline. This matters for belief tracking, not only for play.
- Anger Shell and Berserk have their trigger checks changed around multihit moves.
- Disguise is reimplemented as a zero effectiveness first hit.
- Six abilities are newly legalized: Dragonize, E-Levate, Fire Mane, Mega Sol, Piercing Drill, Spicy Spray. These arrive with the Legends Z-A Mega Evolutions, and the stone list confirms Z-A megas are in (Meganiumite, Feraligite, Emboarite, Chesnaughtite, Delphoxite, Greninjite, Dragalgite, and others).

Scale of the diff: roughly 250 moves and roughly 250 items carry overrides. This cannot be transcribed by hand. The pipeline dumps the resolved dex programmatically and diffs it against mainline gen 9 to produce the delta list.

## 5. Measured engine performance

Single core, Node 22, on the built simulator:

| Operation | Rate | Cost |
| --- | --- | --- |
| Full battles played to completion | 26.8 per second | 37 ms |
| Turns advanced | 303 per second | 3.3 ms |
| `State.serializeBattle` | 2,386 per second | 0.42 ms |
| `State.deserializeBattle` | 1,026 per second | 0.97 ms |

One clone plus one turn advance costs roughly 4.7 ms.

## 6. Measured action space

From a real request object in a live Reg M-B battle with four Pokemon picked, so two on the field and two on the bench:

- Slot A: 8 move-target combinations plus 2 switches, so 10.
- Slot B: 6 move-target combinations plus 2 switches, so 8.
- Joint, after removing pairs where both slots switch into the same bench Pokemon: about 78.
- With the Mega flag still available, roughly 156.

Smaller than the 200 estimated from first principles, mostly because picked team size 4 leaves only two bench Pokemon.

## 7. What the numbers imply

Against the real 45 second clock, the full matrix at one node is about $156 \times 156 \approx 2.4 \times 10^4$ cells, roughly 114 seconds at 4.7 ms per cell, which is 2.5 times over budget before any belief sampling.

Pruned to 10 candidates per side, one node is 100 cells, about 0.5 seconds. That leaves genuine room: 100 cells times 20 belief particles times 5 roll buckets is $10^4$ evaluations, roughly 47 seconds on one core and comfortably inside budget across 8 cores.

Depth 2 with the same pruning is $10^4$ nodes, around 100 seconds per turn on one core, and does not fit.

Conclusions:

1. A pruned one ply agent with belief sampling and roll integration fits the real clock on the stock simulator.
2. Depth 2 or deeper needs an engine roughly 100 times faster, which is what a Rust engine restricted to the Reg M-B pool would plausibly deliver.
3. The per turn budget under the real clock is about 8,000 simulator steps single core, or roughly 60,000 across 8 cores.

The MVP defers the clock, so none of this constrains early work. It constrains the shipped product, and it is why decision latency is measured from M0 rather than discovered at M11.

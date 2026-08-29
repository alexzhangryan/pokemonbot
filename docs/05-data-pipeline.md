# Data Pipeline Design

Four sources, ordered by how much the project depends on them.

## 1. Resolved dex extraction (blocking, do first)

Everything downstream needs the Champions numbers, and roughly 250 moves plus roughly 250 items carry overrides in the mod. Hand transcription is not viable.

Procedure: clone and build `smogon/pokemon-showdown`, then dump the resolved dex for the format ID programmatically.

```js
const {Dex} = require('./dist/sim/dex');
const dex = Dex.forFormat('gen9championsvgc2026regmb');
```

Dump species with base stats and abilities, learnsets, items including the `megaStone` mapping, and full move data, all post mod resolution. Then diff against the same dump for mainline gen 9 to produce an explicit delta list. That list is both the engineering checklist and a legitimate artifact for a writeup, since nobody has published it.

Store a content hash of the dump. Showdown updates frequently and a silent mechanics change mid project would otherwise appear as an unexplained win rate regression. Regenerate and re-diff on a schedule, and fail loudly when the hash moves.

Reg M-A uses a different mod (`championsregma`), so key the dump by format ID rather than by a global constant. The same discipline handles the regulation rollover after 2026-09-09.

## 2. Showdown replay corpus (the behavioral data)

Endpoint, confirmed from the client's `WEB-API.md`:

```
https://replay.pokemonshowdown.com/search.json?format=gen9championsvgc2026regmb
```

Returns up to 51 results, paginate with `before=<uploadtime>` taken from the last entry, where a 51st result signals more pages. Individual replays are available by appending `.log` or `.json` to the replay URL.

Scrape both format IDs, for different reasons.

`gen9championsvgc2026regmb` gives ordinary ladder games under hidden information, which is the regime the agent plays in and therefore the source of behavioral priors: what players bring, what they lead, what they click.

`gen9championsvgc2026regmbbo3` carries `Force Open Team Sheets`, so every one of those replays reveals both players' complete sets at team preview. These are fully labeled training pairs mapping six species to six complete sets, available at scale from the public replay API and from no other source. They are also the clean evaluation set for the belief filter, since ground truth is known from turn 0.

Note the separation of concerns. The agent never plays with open sheets, because Champions has none. Consuming forced-sheet replays as labels is a data question, not a play question, and the two never touch.

Per replay, extract: format ID, both team previews, the open team sheet reveal if present, the bring-4 and leads, every action and every reveal in order, player ratings, and the result. Preserve the raw log alongside the parsed form, because the parser will be wrong at first and re-parsing beats re-scraping.

Scrape politely. Rate limit, back off, cache by replay ID, never re-fetch a stored log.

## 3. Tournament team lists (the structural prior)

Champions tournament play is well underway, including the 2026 World Championships. RK9 runs the official events and publishes standings and team lists. Official lists reveal species, ability, held item, and moves, but never stat points or nature. Victory Road and Pokemon Zone aggregate results and team reports on top.

Value: human optimized, tournament level joint distributions over set composition. They describe what a coherent set looks like at the top of the metagame, which is exactly the structure a particle prior needs.

Caveat: tournament and ladder metagames differ in composition and skill. Fit behavioral priors on ladder replays and use tournament lists as a structural prior over coherence, not as a frequency prior over what will be faced.

Check what is publicly downloadable and under what terms before building a scraper, and prefer any documented export over page scraping.

## 4. Aggregate usage statistics (the fallback)

Pikalytics, Smogon moveset statistics, Pokemon Zone. Cheap to consume, useful for initialization and for sanity checking the other scrapers, but marginals only. Never sample a particle directly from these, for the reason in `03-belief-filter.md`.

## 5. What no source contains

Stat points and natures appear in no public dataset. Neither tournament lists nor open team sheets include them, by explicit design in both cases. They are only ever inferred in battle, by the interval propagation in the belief filter document.

This defines the boundary of the learnable component. Items, abilities, and moves can be predicted from data. Spreads can only be inferred from play. That split is why the belief filter has two structurally different halves.

## 6. Storage

SQLite is sufficient at this scale and keeps the whole corpus a single portable file, which matters for reproducibility.

Tables: `replays` (id, format, uploadtime, ratings, result, raw log path), `previews` (replay id, side, six species, whether sheets were revealed), `sets` (replay id, side, species, item, ability, moves, source of truth), `actions` (replay id, turn, side, slot, action, target), `reveals` (replay id, turn, side, species, attribute, value).

Keep resolved dex dumps as versioned JSON outside the database, keyed by format ID and content hash.

Decision traces from live play and from coach runs are stored separately as append-only JSONL, not in SQLite. See `07-observability.md`.

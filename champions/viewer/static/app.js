/* Champions bot viewer — client.
 *
 * Renders a decision trace. It knows the schema and nothing else: no agent
 * code, no simulator, no assumptions about which milestone produced the file.
 * docs/07-observability.md section 5 requires that a trace from a different
 * agent version renders without crashing, so every read of a payload here is
 * defensive and every field the agent does not emit yet renders as an explicit
 * "not computed" rather than as a zero.
 *
 * The one structural idea: a trace is a flat event stream, and this file folds
 * it into decision points. A decision point is a `turn_start` plus the
 * candidates, timing and equilibrium events that follow it. That grouping is
 * what makes the turn list a spine you can scrub, and it is why the same code
 * renders a finished battle and a live one — a live battle is a stream whose
 * last decision point is still filling in.
 */

"use strict";

// ---------------------------------------------------------------- constants

/* What is not built yet, and when it lands. The agent tells us which fields it
 * could not fill via `pending` / `annotations_pending` on its own events; this
 * map turns those names into something a person can read. An unknown name still
 * renders — it just says "pending" without a milestone, which is the graceful
 * degradation the schema contract asks for. */
const PENDING = {
  win_probability: ["M6", "calibrated evaluation function"],
  damage_rolls: ["M1", "Champions damage layer"],
  ko_probability: ["M1", "Champions damage layer"],
  speed_order: ["M1", "stat layer"],
  value: ["M4", "payoff estimation"],
  policy_provider: ["M2", "candidate generation"],
  mixed_strategy: ["M5", "equilibrium solver"],
  game_value: ["M5", "equilibrium solver"],
  belief: ["M3", "belief filter"],
  subset_distribution: ["M7", "bring-4 model"],
  payoff_matrix: ["M7", "bring-4 model"],
  equilibrium_weights: ["M7", "bring-4 model"],
};

const TYPE_COLORS = {
  normal: "#a8a878", fire: "#f08030", water: "#6890f0", electric: "#f8d030",
  grass: "#78c850", ice: "#98d8d8", fighting: "#c03028", poison: "#a040a0",
  ground: "#e0c068", flying: "#a890f0", psychic: "#f85888", bug: "#a8b820",
  rock: "#b8a038", ghost: "#705898", dragon: "#7038f8", dark: "#705848",
  steel: "#b8b8d0", fairy: "#ee99ac", stellar: "#8fb8c8", "???": "#68a090",
};

/* Showdown's sprite filenames hyphenate the forme, poke-env's species ids do
 * not. Megas matter here specifically: Mega Evolution is back in Champions and
 * 75 Mega Stones are legal, so mega formes are common rather than exotic. */
const FORME_SUFFIXES = [
  "megax", "megay", "mega", "primal", "alola", "galar", "hisui", "paldea",
  "therian", "origin", "incarnate", "crowned", "eternamax", "gmax",
];

const SPRITE_BASE = "https://play.pokemonshowdown.com/sprites";

/* How recently the trace must have been written for the view to call itself
 * live. A person deciding a doubles turn can easily take a minute, and calling
 * that "not live" was both wrong on the badge and, when animation was keyed off
 * it, the reason moves stopped animating mid-game. */
const LIVE_AFTER_WRITE_S = 90;
const MAX_CANDIDATE_ROWS = 24;

// ------------------------------------------------------------------- state

const ui = {
  picker: document.getElementById("trace-picker"),
  meta: document.getElementById("battle-meta"),
  liveBadge: document.getElementById("live-badge"),
  follow: document.getElementById("follow"),
  gotoLive: document.getElementById("goto-live"),
  phase: document.getElementById("phase"),
  ladderGroup: document.getElementById("ladder-group"),
  ladderLabel: document.getElementById("ladder-label"),
  ladderStop: document.getElementById("ladder-stop"),
  ladderResume: document.getElementById("ladder-resume"),
  ladderStartForm: document.getElementById("ladder-start-form"),
  ladderGames: document.getElementById("ladder-games"),
  ladderStart: document.getElementById("ladder-start"),
  ratingGroup: document.getElementById("rating-group"),
  rating: document.getElementById("rating"),
  eloValue: document.getElementById("elo-value"),
  showdown: document.getElementById("showdown"),
  simDot: document.getElementById("sim-dot"),
  simState: document.getElementById("sim-state"),
  simStart: document.getElementById("sim-start"),
  simStop: document.getElementById("sim-stop"),
  spGames: document.getElementById("sp-games"),
  spSeed: document.getElementById("sp-seed"),
  spAgentA: document.getElementById("sp-agent-a"),
  spAgentB: document.getElementById("sp-agent-b"),
  spStart: document.getElementById("sp-start"),
  hostAgent: document.getElementById("host-agent"),
  hostGames: document.getElementById("host-games"),
  hostStart: document.getElementById("host-start"),
  hostInvite: document.getElementById("host-invite"),
  runLabel: document.getElementById("run-label"),
  runLog: document.getElementById("run-log"),
  runLogToggle: document.getElementById("run-log-toggle"),
  runForfeit: document.getElementById("run-forfeit"),
  runStop: document.getElementById("run-stop"),
  evalStrip: document.getElementById("eval-strip"),
  layout: document.getElementById("layout"),
  empty: document.getElementById("empty"),
  turnList: document.getElementById("turn-list"),
  gameList: document.getElementById("game-list"),
  theirs: document.getElementById("side-theirs"),
  ours: document.getElementById("side-ours"),
  conditions: document.getElementById("conditions"),
  log: document.getElementById("log"),
  review: document.getElementById("review"),
  chosen: document.getElementById("chosen"),
  analysis: document.getElementById("analysis"),
  timing: document.getElementById("timing"),
  strategy: document.getElementById("strategy"),
  candidates: document.getElementById("candidates"),
  belief: document.getElementById("belief"),
  scene: document.getElementById("scene"),
  sceneFrame: document.getElementById("scene-frame"),
  sceneToggle: document.getElementById("scene-toggle"),
  sceneShow: document.getElementById("scene-show"),
  sceneSpeed: document.getElementById("scene-speed"),
  sceneVolume: document.getElementById("scene-volume"),
};

let socket = null;
let events = [];
let points = [];
let selected = null;
/* Following means "always show the newest decision". Scrubbing turns it off,
 * because a view that yanks itself away mid-read is worse than a stale one. */
let following = true;
// What the ladder script says it is doing between battles (`status.json` in
// the trace directory, served on /api/status). Null when nothing wrote one.
let ladderStatus = null;
let liveStream = false;
let battleStart = null;
let battleEnd = null;
/* The coach's game-scope `analysis` event, when the file is a review
 * (`scripts/review.py`). Null for a plain trace, and every review surface
 * below renders nothing in that case, so the live view is unchanged. */
let gameAnalysis = null;
let battleId = null;
let showdownUrl = null;
/* Set once the reader picks a trace by hand. Until then the viewer follows
 * whatever is newest, which is what makes "start the viewer, then start a
 * battle" work: the file does not exist yet when the window opens. After an
 * explicit choice it stays put, because silently jumping away from the trace
 * someone is reading is worse than making them pick again. */
let pinned = false;
// The listing as last polled, by trace id, and which file is actually open
// (a finished game opens as its review when the coach has written one).
let traceIndex = new Map();
let openedId = null;
// The bot's ladder account, from the server's status (`.env`), if any.
let account = null;

/* What to open for a listed trace: the coach's review of it when there is
 * one and the game is over, else the trace itself. One entry per game in the
 * list, the reviewed copy behind it (D84). */
function openIdFor(traceId) {
  const trace = traceIndex.get(traceId);
  if (trace && trace.review && !trace.live) return trace.review;
  return traceId;
}
/* The battle currently on screen, as opposed to which of its two agent-view
 * files. Auto-follow keys on this; see loadTraceList. */
let currentBattle = null;

/* The Showdown scene. `sceneReady` gates posting, because the renderer loads a
 * dozen scripts from Smogon's CDN before it can take a log; anything sent
 * earlier is buffered by the frame itself. `sceneSent` tracks how much of the
 * log the frame already has, so a live battle appends rather than rebuilds. */
let sceneReady = false;
let sceneSent = 0;
let sceneHidden = false;
let sceneSpeed = "normal";
// Showdown's renderer plays cries and music at 50 by default, which is loud
// next to a silent instrument panel. Percent, 0 mutes.
let sceneVolume = 30;

// -------------------------------------------------------------------- util

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

const titled = (value) =>
  typeof value === "string" && value.length ? value[0].toUpperCase() + value.slice(1) : value;

/* poke-env stringifies its enums as "Status.PAR" / "<Weather.SUN: 4>". Both
 * show up in the trace, and neither is what anyone wants to read. */
const enumName = (value) => {
  if (typeof value !== "string") return "";
  const match = value.match(/([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)/);
  return (match ? match[2] : value).replace(/[<>]/g, "").split(":")[0].trim();
};

const pretty = (value) => titled(enumName(value).toLowerCase().replace(/_/g, " "));

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/* Traces written before actions were described carry only the wire string.
 * Falling back to it keeps an old file readable instead of showing a row of
 * ellipses where the decisions should be. */
function actionLabel(equilibrium) {
  if (!equilibrium) return null;
  if (equilibrium.chosen_action && equilibrium.chosen_action.label) {
    return equilibrium.chosen_action.label;
  }
  return equilibrium.chosen || null;
}

function spriteUrl(species, facing) {
  let name = String(species || "").toLowerCase();
  for (const suffix of FORME_SUFFIXES) {
    if (name.length > suffix.length && name.endsWith(suffix)) {
      name = `${name.slice(0, -suffix.length)}-${suffix}`;
      break;
    }
  }
  // Our own side shows its back, as in the game: it is the fastest possible
  // cue for which row is which, and costs nothing.
  const set = facing === "back" ? "gen5-back" : "gen5";
  return `${SPRITE_BASE}/${set}/${name}.png`;
}

/* A dashed, hatched block naming what is missing and when it arrives. Used
 * everywhere the agent has told us it could not compute something. */
function pendingBlock(keys, heading) {
  const box = el("div", "pending");
  if (heading) box.appendChild(el("div", "pending-label", heading));
  const list = el("div", "pending-note");
  /* Several missing fields often come from one unbuilt component, and naming
   * it twice reads as a rendering bug rather than as two gaps. */
  const parts = [
    ...new Set(
      (keys || []).map((key) => {
        const [milestone, what] = PENDING[key] || [null, key.replace(/_/g, " ")];
        return milestone ? `${what} (${milestone})` : what;
      })
    ),
  ];
  list.textContent = parts.length ? `not computed yet — ${parts.join(", ")}` : "not computed yet";
  box.appendChild(list);
  return box;
}

/* Tables carry long mono action strings and a variable number of annotation
 * columns. Giving each its own scroller keeps a wide one from widening its
 * column and scrolling the entire page sideways. */
function scroller(node) {
  const box = el("div", "table-scroll");
  box.appendChild(node);
  return box;
}

// ------------------------------------------------------- folding the stream

/* Group the flat event stream into decision points. Everything between one
 * `turn_start` and the next belongs to that decision, whatever its type, so an
 * event this client has never heard of is still attached to the right turn and
 * is available to whatever renders it later. */
function fold(all) {
  const grouped = [];
  let current = null;
  battleStart = null;
  battleEnd = null;
  gameAnalysis = null;

  for (const event of all) {
    const type = event.type;
    const payload = event.payload || {};
    // The envelope carries the room id, which is what Showdown wants in a URL.
    if (event.battle_id) battleId = event.battle_id;

    if (type === "battle_start") {
      battleStart = payload;
      continue;
    }
    if (type === "preview_decision") {
      grouped.push({ kind: "preview", seq: event.seq, payload, events: [event] });
      continue;
    }
    if (type === "battle_end") {
      battleEnd = payload;
      continue;
    }
    /* The coach's overlay (M9, `docs/07-observability.md` section 2). Three
     * scopes: the game summary, the preview pseudo-turn, and one per decision.
     * The per-decision one is emitted right after its turn's `equilibrium`, so
     * it attaches to `current` like any other event of that turn. */
    if (type === "analysis") {
      if (payload.scope === "game") {
        gameAnalysis = payload;
        continue;
      }
      if (payload.scope === "preview") {
        const preview = grouped.find((point) => point.kind === "preview");
        if (preview) preview.analysis = payload;
        continue;
      }
    }
    if (type === "turn_start") {
      current = {
        kind: "turn",
        seq: event.seq,
        turn: payload.turn,
        state: payload.state || null,
        evaluation: payload.evaluation || null,
        log: payload.log || [],
        events: [event],
      };
      grouped.push(current);
      continue;
    }
    if (!current) continue;

    current.events.push(event);
    if (type === "candidates") current.candidates = payload;
    else if (type === "timing") current.timing = payload;
    else if (type === "equilibrium") current.equilibrium = payload;
    else if (type === "belief") current.belief = payload;
    else if (type === "analysis") current.analysis = payload;
  }

  /* The same turn number can produce several decisions: a fainted slot forces a
   * mid-turn switch request. Numbering them makes the spine honest about that
   * rather than showing what looks like a duplicate row. */
  const seen = new Map();
  for (const point of grouped) {
    if (point.kind !== "turn") continue;
    const n = (seen.get(point.turn) || 0) + 1;
    seen.set(point.turn, n);
    point.repeat = n;
  }
  return grouped;
}

// ----------------------------------------------------------------- chrome

function renderMeta() {
  ui.meta.replaceChildren();
  if (!battleStart) return;

  const add = (key, value) => {
    if (value === undefined || value === null || value === "") return;
    const span = el("span");
    span.append(`${key} `, el("b", null, value));
    ui.meta.appendChild(span);
  };

  add("agent", battleStart.agent || battleStart.strategy);
  add("as", battleStart.player_role);
  add("vs", battleStart.opponent_username);
  add("format", battleStart.format_id);
  if (battleEnd) add("result", battleEnd.result);
  if (gameAnalysis) {
    add(
      "reviewed",
      `${pts(gameAnalysis.ex_ante_loss_total)} pts ex-ante over ${gameAnalysis.scored} decisions`
    );
  }
}

// ------------------------------------------------------------- the review

/* Win-probability points, the unit every coach number is in. */
const pts = (value) =>
  value === null || value === undefined ? "n/a" : (Number(value) * 100).toFixed(1);
const pct = (value) =>
  value === null || value === undefined ? "n/a" : `${(Number(value) * 100).toFixed(0)}%`;

/* Chess.com's vocabulary, because it is the one players already read. The
 * glyphs are the annotation marks; the classes carry the colour. */
const LABEL_MARKS = {
  best: "★",
  solid: "✓",
  inaccuracy: "?!",
  mistake: "?",
  blunder: "??",
};

const TAG_TITLES = {
  forced: "forced: every alternative lost badly",
  read: "read: the opponent chose the specific counter",
  gamble: "gamble: low equilibrium weight, and it paid off",
  unlucky: "unlucky: a fine decision, a bad roll",
  lucky: "lucky: better than the play deserved",
};

function labelBadge(label) {
  const badge = el("span", `label-badge label-${label || "none"}`);
  badge.append(el("span", "mark", LABEL_MARKS[label] || "·"), ` ${label || "unscored"}`);
  return badge;
}

function tagChips(tags) {
  const chips = el("span", "tags");
  for (const tag of tags || []) {
    const chip = el("span", `tag tag-${tag}`, tag);
    chip.title = TAG_TITLES[tag] || tag;
    chips.appendChild(chip);
  }
  return chips;
}

/* Jump to a turn from the summary, the way a chess review's critical moves are
 * links into the move list. */
function jumpTo(turn) {
  const point = points.find((p) => p.kind === "turn" && p.turn === turn);
  if (!point) return;
  following = false;
  select(point);
}

function renderReview(point) {
  ui.review.replaceChildren();
  if (!gameAnalysis) return;
  const g = gameAnalysis;

  const box = el("div", "review");
  const head = el("div", "review-head");
  head.append(el("span", "review-title", "Review"));
  const info = g.information || {};
  head.append(
    el(
      "span",
      "hint",
      `ex-ante ${info.ante || "?"} · ex-post ${info.post || "?"}${
        g.calibrated ? "" : " · evaluation uncalibrated"
      }`
    )
  );
  box.appendChild(head);

  const row = el("div", "timing");
  const stat = (value, key, tone) => {
    const cell = el("div", "stat");
    cell.appendChild(el("div", `v${tone ? ` ${tone}` : ""}`, value));
    cell.appendChild(el("div", "k", key));
    row.appendChild(cell);
  };
  stat(pts(g.ex_ante_loss_total), "ex-ante loss, total", Number(g.ex_ante_loss_total) > 0.3 ? "warn" : "ok");
  stat(pts(g.ex_ante_loss_mean), "per decision");
  stat(pts(g.ex_post_loss_total), "ex-post loss, total");
  stat(`${g.scored ?? 0}/${g.decisions ?? 0}`, "scored");
  box.appendChild(row);

  const counts = el("div", "label-counts");
  for (const label of ["best", "solid", "inaccuracy", "mistake", "blunder"]) {
    const n = (g.classifications || {})[label] || 0;
    const cell = el("span", `count label-${label}${n ? "" : " zero"}`);
    cell.append(el("span", "mark", LABEL_MARKS[label]), ` ${n} ${label}`);
    counts.appendChild(cell);
  }
  box.appendChild(counts);

  const tags = Object.entries(g.tags || {}).filter(([, n]) => n);
  if (tags.length) {
    const line = el("div", "known");
    line.append("tags ");
    for (const [tag, n] of tags) {
      const chip = el("span", `tag tag-${tag}`, `${tag} ×${n}`);
      chip.title = TAG_TITLES[tag] || tag;
      line.appendChild(chip);
    }
    box.appendChild(line);
  }

  const critical = (title, entries, key) => {
    if (!entries || !entries.length) return;
    const line = el("div", "critical");
    line.append(el("span", "belief-label", title));
    for (const entry of entries) {
      const button = el("button", "ghost small jump", `turn ${entry.turn} · ${pts(entry[key])}`);
      button.type = "button";
      button.addEventListener("click", () => jumpTo(entry.turn));
      line.appendChild(button);
    }
    box.appendChild(line);
  };
  critical("avoidable", g.critical_by_loss, "ex_ante_loss");
  critical("swings", g.critical_by_drop, "drop");

  const bands = g.bands || {};
  box.appendChild(
    el(
      "div",
      "hint",
      bands.source && bands.source !== "hand-set"
        ? `bands ${bands.source}`
        : "thresholds hand-set (champions/coach/classify.py) until calibrated"
    )
  );
  ui.review.appendChild(box);
}

/* The per-decision half of the review: the label, the tags, the two losses
 * and what they decompose into. Nothing here is computed; every number is read
 * off the coach's event, the same rule as the eval bar. */
function renderAnalysis(point) {
  ui.analysis.replaceChildren();
  const a = point.analysis;
  if (!a) return;

  if (point.kind === "preview") {
    const box = el("div", "analysis");
    box.appendChild(el("h2", null, "Preview review"));
    const line = el("div", "known");
    line.append(
      "brought ",
      el("b", null, (a.bring || []).join(", ") || "unknown"),
      " · led ",
      el("b", null, (a.leads || []).join(", ") || "unknown"),
      " · opponent showed ",
      el("b", null, (a.opponent_bring_observed || []).join(", ") || "nobody")
    );
    box.appendChild(line);
    const pending = pendingBlock(a.pending, "Bring-4 verdict");
    if (a.reason) pending.appendChild(el("div", "pending-note", a.reason));
    box.appendChild(pending);
    ui.analysis.appendChild(box);
    return;
  }

  const box = el("div", "analysis");
  const head = el("h2", null, "Review");
  head.appendChild(labelBadge(a.classification));
  head.appendChild(tagChips(a.tags));
  box.appendChild(head);

  const row = el("div", "timing");
  const stat = (value, key, tone) => {
    const cell = el("div", "stat");
    cell.appendChild(el("div", `v${tone ? ` ${tone}` : ""}`, value));
    cell.appendChild(el("div", "k", key));
    row.appendChild(cell);
  };
  const ante = a.ex_ante_loss;
  const post = a.ex_post_loss;
  stat(pts(ante), "ex-ante loss", ante === null ? null : ante >= 0.15 ? "danger" : ante >= 0.05 ? "warn" : "ok");
  stat(pts(post), "ex-post loss", post === null ? null : post >= 0.15 ? "danger" : post >= 0.05 ? "warn" : null);
  stat(pct(a.game_value), "game value");
  const luck = a.luck;
  stat(
    luck === null || luck === undefined ? "n/a" : `${luck > 0 ? "−" : "+"}${pts(Math.abs(luck))}`,
    "luck",
    luck === null || luck === undefined ? null : luck >= 0.1 ? "danger" : luck <= -0.1 ? "ok" : null
  );
  box.appendChild(row);

  const line = (label, value) => {
    if (value === null || value === undefined) return;
    const known = el("div", "known");
    known.append(`${label} `, el("b", null, value));
    box.appendChild(known);
  };
  line("played", a.played_label || "unrecorded");
  line("equilibrium best", a.best);
  line("opponent played", a.opponent_played);
  line("best after the fact", a.best_ex_post);
  if (a.expected_value !== null && a.expected_value !== undefined) {
    line("expected → realised", `${pct(a.expected_value)} → ${pct(a.realized_value)}`);
  }

  if (Array.isArray(a.opponent_equilibrium) && a.opponent_equilibrium.length) {
    const table = el("table");
    const thead = el("thead");
    const tr = el("tr");
    tr.append(el("th", null, "their equilibrium"), el("th", null, "weight"));
    thead.appendChild(tr);
    table.appendChild(thead);
    const body = el("tbody");
    for (const entry of a.opponent_equilibrium) {
      const r = el("tr");
      if (entry.label === a.opponent_played) r.classList.add("is-chosen");
      r.append(el("td", null, entry.label), el("td", "num", pct(entry.probability)));
      body.appendChild(r);
    }
    table.appendChild(body);
    box.appendChild(scroller(table));
  }

  if (Array.isArray(a.rolls) && a.rolls.length > 1) {
    const table = el("table");
    const thead = el("thead");
    const tr = el("tr");
    tr.append(el("th", null, "roll branch"), el("th", null, "probability"), el("th", null, "value"));
    thead.appendChild(tr);
    table.appendChild(thead);
    const body = el("tbody");
    for (const branch of a.rolls) {
      const r = el("tr");
      r.append(
        el("td", null, (branch.faints || []).length ? `faints: ${branch.faints.join(", ")}` : "no faint"),
        el("td", "num", pct(branch.probability)),
        el("td", "num", pct(branch.value))
      );
      body.appendChild(r);
    }
    table.appendChild(body);
    box.appendChild(scroller(table));
  }

  if (a.explanation) box.appendChild(el("p", "explanation", a.explanation));

  const info = a.information || {};
  const foot = el("div", "hint");
  foot.textContent = `${a.n_rows ?? "?"} rows × ${a.n_columns ?? "?"} columns · ${
    a.model || "?"
  } · ex-ante ${info.ante || "?"}, ex-post ${info.post || "?"}${
    a.calibrated ? "" : " · evaluation uncalibrated"
  }`;
  box.appendChild(foot);
  ui.analysis.appendChild(box);
}

function renderTurnList() {
  ui.turnList.replaceChildren();

  for (const point of points) {
    const row = el("button", "turn-row");
    row.type = "button";
    row.setAttribute("aria-current", String(point === selected));

    if (point.kind === "preview") {
      row.classList.add("preview");
      row.appendChild(el("span", "n", "PRE"));
      row.appendChild(el("span", "what", (point.payload.selected || []).join(" ") || "bring 4"));
    } else {
      row.appendChild(el("span", "n", point.repeat > 1 ? `${point.turn}.${point.repeat}` : point.turn));
      const what = point.analysis && point.analysis.played_label
        ? point.analysis.played_label
        : actionLabel(point.equilibrium) || "…";
      row.appendChild(el("span", "what", what));
      /* The clock is a correctness surface, not a performance one: VGC Timer
       * auto-loses an inactive player, so a slow turn is flagged in the spine
       * where it cannot be missed. */
      if (point.timing && (point.timing.exceeded_45s || point.timing.watchdog_fired)) {
        row.classList.add("slow");
      }
      /* A reviewed turn carries its mark in the spine, the way a chess move
       * list does: the label glyph, and a letter per tag. */
      if (point.analysis) {
        const a = point.analysis;
        row.classList.add("reviewed", `label-${a.classification || "none"}`);
        const mark = el("span", "mark", LABEL_MARKS[a.classification] || "·");
        const tags = a.tags || [];
        if (tags.length) mark.append(el("span", "tag-letters", tags.map((t) => t[0].toUpperCase()).join("")));
        mark.title = [a.classification || "unscored", ...tags.map((t) => TAG_TITLES[t] || t)].join("\n");
        row.appendChild(mark);
      }
    }

    row.addEventListener("click", () => {
      following = false;
      select(point);
    });
    ui.turnList.appendChild(row);
  }

  if (battleEnd) {
    const row = el("button", `turn-row result-${battleEnd.result}`);
    row.type = "button";
    row.disabled = true;
    row.appendChild(el("span", "n", "END"));
    const summary = gameAnalysis ? ` · ${pts(gameAnalysis.ex_ante_loss_total)} pts lost` : "";
    row.appendChild(el("span", "what", `${battleEnd.result} · ${battleEnd.turns} turns${summary}`));
    ui.turnList.appendChild(row);
  }

  const current = ui.turnList.querySelector('[aria-current="true"]');
  if (current && following) current.scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------- field view

/* Resolve the chosen action onto the board: which of our slots is acting, and
 * which slots it is aimed at.
 *
 * Showdown's doubles target encoding is relative to us — negative indices are
 * our own slots, positive ones the opponent's, and 0 means the move takes no
 * target choice, which covers both self-targeting moves and spreads. A spread
 * move is the interesting case and the one worth being careful about: it has
 * no target index at all, so the only honest thing to draw is the actor, and
 * guessing at "everything adjacent" would be inventing a targeting rule this
 * layer does not know. `target_label` from the trace carries the rest. */
function boardRoles(equilibrium) {
  const roles = { ours: {}, theirs: {} };
  const action = equilibrium && equilibrium.chosen_action;
  if (!action || !action.slots) return roles;

  const at = (side, index) => {
    if (!roles[side][index]) roles[side][index] = { acting: false, hitBy: [] };
    return roles[side][index];
  };

  action.slots.forEach((slot, index) => {
    if (slot.kind !== "move" && slot.kind !== "switch") return;
    at("ours", index).acting = true;
    if (slot.kind !== "move") return;

    const target = Number(slot.target || 0);
    if (target > 0) at("theirs", target - 1).hitBy.push(slot.name);
    // A slot can be both acting and aimed at: in doubles the agent can point a
    // move at its own partner, which is exactly the mistake worth seeing on the
    // board rather than deducing from a `/choose` string.
    else if (target < 0) at("ours", -target - 1).hitBy.push(slot.name);
  });
  return roles;
}


function hpBar(pct) {
  const bar = el("div", "hp-bar");
  if (pct <= 20) bar.classList.add("low");
  else if (pct <= 50) bar.classList.add("mid");
  const fill = el("span");
  fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  bar.appendChild(fill);
  return bar;
}

function monCard(mon, options) {
  const { compact = false, facing = "front", role = null } = options || {};

  if (!mon) return el("div", "mon empty", "empty slot");

  const card = el("div", "mon");
  if (mon.fainted) card.classList.add("fainted");

  /* What the agent decided, drawn on the board it decided about. This is the
   * one thing an embedded Showdown window could not have shown. */
  if (role && role.acting) card.classList.add("is-actor");
  if (role && role.hitBy.length) card.classList.add("is-target");
  if (role && (role.acting || role.hitBy.length)) {
    const parts = [];
    if (role.acting) parts.push("acting");
    if (role.hitBy.length) parts.push(`← ${role.hitBy.join(", ")}`);
    card.appendChild(el("div", `marker${role.acting ? "" : " target"}`, parts.join(" ")));
  }

  const sprite = el("img", "sprite");
  sprite.src = spriteUrl(mon.species, facing);
  sprite.alt = "";
  sprite.loading = "lazy";
  /* Sprites come from Showdown's public assets. If that is unreachable the page
   * must still be fully usable, so a failed image is removed rather than left
   * as a broken icon; every card carries its species as text regardless. */
  sprite.addEventListener("error", () => sprite.classList.add("missing"));
  card.appendChild(sprite);

  const body = el("div");
  const name = el("div", "name");
  name.appendChild(document.createTextNode(mon.species));
  if (!compact && mon.level && mon.level !== 50) name.appendChild(el("span", "lvl", `L${mon.level}`));
  if (!compact) {
    const types = el("span", "types");
    for (const type of mon.types || []) {
      const key = enumName(type).toLowerCase();
      const pill = el("span", "type", enumName(type).slice(0, 3));
      pill.style.background = TYPE_COLORS[key] || "#6d7480";
      types.appendChild(pill);
    }
    name.appendChild(types);
  }
  body.appendChild(name);

  const pct = typeof mon.hp_pct === "number" ? mon.hp_pct : 0;
  body.appendChild(hpBar(pct));

  const hp = el("div", "hp-text");
  if (mon.known && mon.hp !== null && mon.max_hp) {
    hp.textContent = `${mon.hp}/${mon.max_hp}`;
    hp.appendChild(el("span", "approx", ` · ${pct}%`));
  } else {
    /* Opponent HP arrives quantized to percent, so anything derived from it
     * carries about ±0.5% of max HP of error (CLAUDE.md constraint 5). The
     * tilde is there so nobody reads it as exact. */
    hp.appendChild(el("span", "approx", "~"));
    hp.appendChild(document.createTextNode(`${pct}%`));
  }
  body.appendChild(hp);

  const chips = el("div", "chips");
  const status = enumName(mon.status || "").toLowerCase();
  if (status) chips.appendChild(el("span", `chip st-${status}`, status));
  for (const [stat, value] of Object.entries(mon.boosts || {})) {
    chips.appendChild(el("span", `chip ${value > 0 ? "up" : "down"}`, `${stat} ${value > 0 ? "+" : ""}${value}`));
  }
  if (!compact) {
    for (const effect of (mon.effects || []).slice(0, 4)) {
      chips.appendChild(el("span", "chip", pretty(effect)));
    }
    if (mon.item) chips.appendChild(el("span", "chip", mon.item));
    else if (!mon.known) chips.appendChild(el("span", "chip unknown", "item unknown"));
  }
  if (chips.childElementCount) body.appendChild(chips);

  card.appendChild(body);
  return card;
}

function renderSide(container, side, label, className, roles) {
  container.replaceChildren();
  container.className = `side ${className}`;
  const facing = className === "ours" ? "back" : "front";
  if (!side) {
    /* A trace written before `turn_start` carried a state snapshot. Saying so
     * beats an empty panel, which reads as a broken viewer. */
    const note = el("div", "pending");
    note.appendChild(el("div", "pending-label", label));
    note.appendChild(
      el("div", "pending-note", "this trace records no board state — written by an earlier agent")
    );
    container.appendChild(note);
    return;
  }

  const head = el("div", "side-head");
  head.appendChild(el("span", "who", label));
  head.appendChild(el("span", "count", `${side.remaining} left · ${side.revealed} seen`));
  container.appendChild(head);

  const slots = el("div", "slots");
  (side.active || []).forEach((mon, index) => {
    slots.appendChild(monCard(mon, { facing, role: (roles || {})[index] || null }));
  });
  container.appendChild(slots);

  if ((side.bench || []).length) {
    const bench = el("div", "bench");
    for (const mon of side.bench) bench.appendChild(monCard(mon, { compact: true, facing }));
    container.appendChild(bench);
  }
}

function renderConditions(state) {
  ui.conditions.replaceChildren();
  if (!state) return;

  const add = (text) => ui.conditions.appendChild(el("span", "chip field", text));
  for (const key of Object.keys(state.weather || {})) add(pretty(key));
  for (const key of Object.keys(state.fields || {})) add(pretty(key));
  for (const [key, value] of Object.entries(state.side_conditions || {})) {
    add(`ours: ${pretty(key)}${value > 1 ? ` ×${value}` : ""}`);
  }
  for (const [key, value] of Object.entries(state.opponent_side_conditions || {})) {
    add(`theirs: ${pretty(key)}${value > 1 ? ` ×${value}` : ""}`);
  }
}

/* The protocol, lightly formatted. Kept close to the wire on purpose: this is
 * the server's account of what happened, and paraphrasing it would put a
 * translation layer between the reader and the ground truth at exactly the
 * moment they are trying to work out why the agent did something. */
function renderLog(lines) {
  ui.log.replaceChildren();
  if (!lines || !lines.length) {
    ui.log.appendChild(el("span", "empty-line", "no protocol recorded for this decision"));
    return;
  }
  ui.log.textContent = lines.map((line) => line.replace(/^\|/, "")).join("\n");
}

// ---------------------------------------------------------- decision panel

function renderChosen(point) {
  ui.chosen.replaceChildren();
  const eq = point.equilibrium;

  if (point.kind === "preview") {
    const box = el("div", "chosen");
    box.appendChild(el("div", "label", "team preview · bring 4"));
    box.appendChild(el("div", "action", (point.payload.selected || []).join(", ") || "—"));
    box.appendChild(el("div", "wire", point.payload.order || ""));
    ui.chosen.appendChild(box);
    ui.chosen.appendChild(pendingBlock(point.payload.pending, "Preview equilibrium"));
    return;
  }

  const box = el("div", "chosen");
  box.appendChild(el("div", "label", eq ? `chose · ${eq.strategy || "unknown policy"}` : "deciding"));
  box.appendChild(el("div", "action", actionLabel(eq) || "…"));
  if (eq && eq.chosen) box.appendChild(el("div", "wire", eq.chosen));
  ui.chosen.appendChild(box);
}

function renderTiming(point) {
  ui.timing.replaceChildren();
  const timing = point.timing;
  if (!timing) return;

  ui.timing.appendChild(el("h2", null, "Clock"));
  const row = el("div", "timing");

  const stat = (value, key, tone) => {
    const box = el("div", "stat");
    box.appendChild(el("div", `v${tone ? ` ${tone}` : ""}`, value));
    box.appendChild(el("div", "k", key));
    row.appendChild(box);
  };

  const ms = Number(timing.total_ms || 0);
  /* Two thresholds, both from the rule rather than from taste: 45s is the per
   * turn budget the agent is built against, and anything within an order of
   * magnitude of it is worth looking at. */
  const tone = timing.exceeded_45s ? "danger" : ms > 4500 ? "warn" : "ok";
  stat(ms < 10 ? `${ms.toFixed(2)}ms` : `${Math.round(ms)}ms`, "decision", tone);
  stat(`${timing.deadline_s ?? "—"}s`, "deadline");
  stat(timing.proposals ?? "—", "proposals");
  stat(timing.watchdog_fired ? "fired" : "no", "watchdog", timing.watchdog_fired ? "warn" : null);

  ui.timing.appendChild(row);
}

function renderStrategy(point) {
  ui.strategy.replaceChildren();
  const eq = point.equilibrium;
  if (!eq) return;

  ui.strategy.appendChild(el("h2", null, "Strategy"));

  /* A reviewed turn has the equilibrium the coach re-solved offline with the
   * pruning removed. It is the honest mixed strategy for the position, so it
   * is shown here in place of the live agent's pending block. */
  const review = point.analysis;
  if (review && Array.isArray(review.equilibrium) && review.equilibrium.length) {
    const table = el("table");
    const thead = el("thead");
    const tr = el("tr");
    tr.append(el("th", null, "re-solved offline"), el("th", null, "weight"), el("th", null, "value"));
    thead.appendChild(tr);
    table.appendChild(thead);
    const body = el("tbody");
    for (const entry of review.equilibrium) {
      const r = el("tr");
      if (entry.label === review.played_label) r.classList.add("is-chosen");
      r.append(
        el("td", null, entry.label),
        el("td", "num", pct(entry.probability)),
        el("td", "num", pct(entry.ante_value))
      );
      body.appendChild(r);
    }
    table.appendChild(body);
    ui.strategy.appendChild(scroller(table));
    const note = el("div", "known");
    note.append(
      "game value ",
      el("b", null, pct(review.game_value)),
      review.is_pure ? " · pure" : " · mixed",
      review.recorded && review.recorded.game_value !== null && review.recorded.game_value !== undefined
        ? ` · the live agent solved ${pct(review.recorded.game_value)} at k = ${review.recorded.k ?? "?"}`
        : ""
    );
    ui.strategy.appendChild(note);
    return;
  }

  if (Array.isArray(eq.mixed_strategy) && eq.mixed_strategy.length) {
    const table = el("table");
    const body = el("tbody");
    for (const entry of eq.mixed_strategy) {
      const tr = el("tr");
      tr.appendChild(el("td", null, entry.label || entry.action || "?"));
      tr.appendChild(el("td", "num", `${(Number(entry.weight || 0) * 100).toFixed(1)}%`));
      body.appendChild(tr);
    }
    table.appendChild(body);
    ui.strategy.appendChild(scroller(table));
    return;
  }

  /* The one-ply agent puts the solved game on its scored `candidates` event:
   * the payoff matrix, both sides' equilibrium mixes and the game value. That
   * is the live strategy, so it is shown here rather than the pending block
   * the equilibrium event still names. */
  const solved = point.candidates && Array.isArray(point.candidates.payoff) ? point.candidates : null;
  if (solved) {
    const line = el("div", "known");
    const support = Array.isArray(solved.support) ? solved.support.length : null;
    line.append(
      "game value ",
      el("b", null, pct(solved.game_value)),
      solved.is_pure ? " \u00b7 pure" : " \u00b7 mixed",
      support !== null ? ` \u00b7 ${plural(support, "action")} in support` : "",
      solved.k ? ` \u00b7 k = ${solved.k}` : "",
      solved.model ? ` \u00b7 ${solved.model}` : ""
    );
    ui.strategy.appendChild(line);

    const theirs = solved.opponent_joint || [];
    const mix = solved.opponent_equilibrium || [];
    const rows = theirs
      .map((action, i) => ({ label: action.label || action.message || `column ${i + 1}`, weight: Number(mix[i] || 0) }))
      .filter((row) => row.weight > 0.005)
      .sort((a, b) => b.weight - a.weight);
    if (rows.length) {
      const table = el("table");
      const thead = el("thead");
      const tr = el("tr");
      tr.append(el("th", null, "their expected reply"), el("th", null, "weight"));
      thead.appendChild(tr);
      table.appendChild(thead);
      const body = el("tbody");
      for (const row of rows) {
        const r = el("tr");
        r.append(el("td", null, row.label), el("td", "num", pct(row.weight)));
        body.appendChild(r);
      }
      table.appendChild(body);
      ui.strategy.appendChild(scroller(table));
    }
    return;
  }

  const line = el("div", "known");
  line.append(
    "policy ",
    el("b", null, eq.strategy || "unknown"),
    eq.value !== null && eq.value !== undefined ? ` · value ${pct(eq.value)}` : ""
  );
  ui.strategy.appendChild(line);
  ui.strategy.appendChild(pendingBlock(eq.pending || ["mixed_strategy"], null));
}

function renderCandidates(point) {
  ui.candidates.replaceChildren();
  const candidates = point.candidates;
  if (!candidates) return;

  const head = el("h2", null, "Candidates");
  head.appendChild(
    el("span", "hint", `${candidates.n_legal_joint_actions} legal joint ${
      candidates.n_legal_joint_actions === 1 ? "action" : "actions"
    }`)
  );
  ui.candidates.appendChild(head);

  /* Per slot first. The joint list is the product of these and is an order of
   * magnitude longer for the same information, so the readable decomposition
   * leads and the full enumeration follows. */
  (candidates.slot_options || []).forEach((options, index) => {
    const block = el("div", "belief-mon");
    const top = el("div", "top");
    top.appendChild(el("span", null, `slot ${index + 1}`));
    top.appendChild(el("span", "hint", plural(options.length, "option")));
    block.appendChild(top);

    const chips = el("div", "chips");
    for (const option of options) {
      const chip = el("span", "chip", option.label);
      if (option.kind === "move" && option.type) {
        chip.style.borderColor = TYPE_COLORS[String(option.type).toLowerCase()] || "";
      }
      chips.appendChild(chip);
    }
    block.appendChild(chips);
    ui.candidates.appendChild(block);
  });

  const chosenMessage = point.equilibrium ? point.equilibrium.chosen : null;
  const joint = candidates.joint || [];
  if (!joint.length) return;

  /* Scored: the search has run and each row carries what the bot thinks of
   * it. `score` is the row's expected win probability against the opponent's
   * equilibrium mix, `worst` its minimum over the opponent's columns, `weight`
   * how often the equilibrium plays it, `policy` the prior that ranked it
   * before any of that was computed. Sorted by what the bot would play. */
  if (Array.isArray(candidates.payoff) && candidates.payoff.length) {
    const mix = candidates.opponent_equilibrium || [];
    const scored = joint.map((action, i) => {
      const row = candidates.payoff[i] || [];
      const score = row.reduce((sum, cell, j) => sum + Number(cell) * Number(mix[j] || 0), 0);
      const worst = row.length ? Math.min(...row.map(Number)) : null;
      return {
        action,
        score,
        worst,
        weight: Number(action.equilibrium_probability || 0),
        policy: typeof action.policy_score === "number" ? action.policy_score : null,
      };
    });
    scored.sort((a, b) => b.weight - a.weight || b.score - a.score);

    const head2 = el("div", "hint");
    head2.textContent = `${scored.length} scored against ${mix.length} opponent replies \u00b7 score = expected win probability vs their mix`;
    ui.candidates.appendChild(head2);

    const table = el("table", "scored");
    const thead = el("thead");
    const headRow = el("tr");
    headRow.append(
      el("th", null, "joint action"),
      el("th", "num", "score"),
      el("th", "num", "worst"),
      el("th", "num", "weight"),
      el("th", "num", "policy")
    );
    thead.appendChild(headRow);
    table.appendChild(thead);
    const body = el("tbody");
    for (const entry of scored.slice(0, MAX_CANDIDATE_ROWS)) {
      const tr = el("tr");
      if (chosenMessage && entry.action.message === chosenMessage) tr.classList.add("is-chosen");
      if (entry.weight > 0.005) tr.classList.add("in-support");
      tr.append(
        el("td", null, entry.action.label),
        el("td", "num", pct(entry.score)),
        el("td", "num", entry.worst === null ? "\u2014" : pct(entry.worst)),
        el("td", "num", entry.weight > 0.0005 ? pct(entry.weight) : "\u00b7"),
        el("td", "num", entry.policy === null ? "\u2014" : entry.policy.toFixed(2))
      );
      body.appendChild(tr);
    }
    table.appendChild(body);
    ui.candidates.appendChild(scroller(table));
    if (scored.length > MAX_CANDIDATE_ROWS) {
      ui.candidates.appendChild(el("div", "more", `${scored.length - MAX_CANDIDATE_ROWS} more`));
    }
    return;
  }

  const table = el("table");
  const thead = el("thead");
  const headRow = el("tr");
  headRow.appendChild(el("th", null, "joint action"));
  /* The columns the search layer will fill. They exist now, dimmed, so that the
   * shape of the table does not change under anyone the day they arrive — and
   * so an empty column reads as unbuilt rather than as all-zero. */
  const pendingCols = (candidates.annotations_pending || []).filter((key) => key !== "policy_provider");
  for (const key of pendingCols) {
    const [milestone] = PENDING[key] || [];
    const th = el("th", "pending-col", `${key.replace(/_/g, " ")}${milestone ? ` ${milestone}` : ""}`);
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const body = el("tbody");
  const shown = joint.slice(0, MAX_CANDIDATE_ROWS);
  for (const action of shown) {
    const tr = el("tr");
    if (chosenMessage && action.message === chosenMessage) tr.classList.add("is-chosen");
    tr.appendChild(el("td", null, action.label));
    for (const _ of pendingCols) tr.appendChild(el("td", "na", "—"));
    body.appendChild(tr);
  }
  table.appendChild(body);
  ui.candidates.appendChild(scroller(table));

  if (joint.length > shown.length || candidates.truncated) {
    const note = joint.length - shown.length;
    ui.candidates.appendChild(
      el("div", "more", candidates.truncated ? `${note} more shown of a truncated list` : `${note} more`)
    );
  }
}

/* What we know about the opponent, split into what was revealed and what is
 * inferred. The second half is the belief filter (M5), and it is the single
 * most useful debugging surface in the system: a stochastic agent sampling a
 * mixed strategy over a sampled belief cannot be debugged from its output
 * (CLAUDE.md constraint 6), so the belief has to be legible on its own.
 *
 * Two intervals are shown per stat and they mean different things. The wide one
 * is the union over live particles -- the filter is not more certain than its
 * least certain surviving hypothesis. The marker is the modal particle's box,
 * which is what the search actually reads. When the marker sits outside the
 * union it is a bug; when it is much narrower, the belief is concentrated. */
function renderBelief(point) {
  ui.belief.replaceChildren();
  const state = point.state;
  if (!state || !state.theirs) return;

  const belief = point.belief || null;
  const head = el("h2", null, "Opponent");
  head.appendChild(
    el(
      "span",
      "hint",
      belief
        ? `${belief.alive}/${belief.particles} particles · ESS ${belief.effective_sample_size} · ${
            belief.resamples
          } resample${belief.resamples === 1 ? "" : "s"}`
        : "revealed vs inferred"
    )
  );
  ui.belief.appendChild(head);

  const bySpecies = new Map();
  for (const entry of (belief && belief.team) || []) bySpecies.set(entry.species, entry);

  const team = [...(state.theirs.active || []).filter(Boolean), ...(state.theirs.bench || [])];
  const seen = new Set();
  for (const mon of team) {
    seen.add(mon.species);
    ui.belief.appendChild(beliefBox(mon, bySpecies.get(mon.species)));
  }
  /* The Pokemon that have not appeared yet. They are the ones the prior is
   * carrying alone, and leaving them out would hide exactly the half of the
   * belief that has had no evidence applied to it. */
  for (const [species, entry] of bySpecies) {
    if (!seen.has(species)) ui.belief.appendChild(beliefBox({ species, unseen: true }, entry));
  }

  if (!belief) {
    ui.belief.appendChild(
      pendingBlock(["belief"], "Set hypotheses, stat intervals, nature posterior")
    );
  }
}

function beliefBox(mon, entry) {
  const box = el("div", "belief-mon");
  const top = el("div", "top");
  top.appendChild(el("span", null, mon.species));
  top.appendChild(
    el("span", "hint", mon.unseen ? "not seen" : mon.fainted ? "fainted" : `~${mon.hp_pct}%`)
  );
  box.appendChild(top);

  const moves = (mon.revealed_moves || []).map((move) => move.name || move.id);
  const known = el("div", "known");
  known.append("seen ", el("b", null, moves.length ? moves.join(", ") : "no moves yet"));
  box.appendChild(known);

  const traits = el("div", "known");
  traits.append(
    "ability ",
    el("b", null, mon.ability || `one of ${(mon.possible_abilities || []).join(" / ") || "?"}`),
    " · item ",
    el("b", null, mon.item || "unknown")
  );
  box.appendChild(traits);

  if (!entry) return box;

  for (const [label, ranked] of [
    ["item", entry.item],
    ["ability", entry.ability],
    ["nature", entry.nature],
    ["moves", entry.moves],
  ]) {
    if (!ranked || !ranked.length) continue;
    const row = el("div", "belief-row");
    row.appendChild(el("span", "belief-label", label));
    const chips = el("div", "chips");
    for (const item of ranked.slice(0, label === "moves" ? 6 : 3)) {
      const chip = el("span", "chip", `${item.value} ${Math.round(item.probability * 100)}%`);
      chip.style.opacity = String(0.45 + 0.55 * Math.min(1, item.probability));
      chips.appendChild(chip);
    }
    row.appendChild(chips);
    box.appendChild(row);
  }

  if (entry.points) box.appendChild(spreadBars(entry));
  return box;
}

const STAT_LABELS = { hp: "HP", atk: "Atk", def: "Def", spa: "SpA", spd: "SpD", spe: "Spe" };

function spreadBars(entry) {
  const wrap = el("div", "spread");
  wrap.appendChild(el("div", "belief-label", "stat points, 0-32"));
  for (const stat of ["hp", "atk", "def", "spa", "spd", "spe"]) {
    const union = entry.points[stat];
    if (!union) continue;
    const modal = (entry.points_modal || {})[stat] || union;
    const stats = (entry.stats || {})[stat];

    const row = el("div", "spread-row");
    row.appendChild(el("span", "spread-stat", STAT_LABELS[stat] || stat));

    const track = el("div", "spread-track");
    const band = el("span", "spread-band");
    band.style.left = `${(union[0] / 32) * 100}%`;
    band.style.width = `${Math.max(1.5, ((union[1] - union[0]) / 32) * 100)}%`;
    track.appendChild(band);

    const marker = el("span", "spread-modal");
    marker.style.left = `${(modal[0] / 32) * 100}%`;
    marker.style.width = `${Math.max(1.5, ((modal[1] - modal[0]) / 32) * 100)}%`;
    track.appendChild(marker);
    row.appendChild(track);

    row.appendChild(
      el("span", "spread-value", stats ? `${stats[0]}–${stats[1]}` : `${union[0]}–${union[1]}`)
    );
    wrap.appendChild(row);
  }
  return wrap;
}

/* The eval bar `docs/04-decision-engine.md` section 5 asks for.
 *
 * The number is read off the trace, never computed here. The viewer does not
 * import the agent and must not: an evaluation recomputed at display time would
 * be a different function from the one the search used, and the whole point of
 * the bar is to show what the agent thought.
 *
 * `calibrated` travels with the number. An uncalibrated evaluation is still
 * worth showing -- it orders positions correctly long before it is a
 * probability -- but saying "63%" about it would be a claim the trace does not
 * support, so it is drawn hatched and labelled in log odds instead. */
function renderEval(point) {
  const strip = ui.evalStrip;
  const evaluation = point && point.evaluation;
  strip.replaceChildren();

  if (!evaluation) {
    strip.className = "pending-block";
    const bar = el("div", "pending-bar");
    bar.appendChild(el("span"));
    strip.append(
      el("div", "pending-label", "Win probability"),
      bar,
      el("div", "pending-note", "not computed yet — calibrated evaluation function (M6)")
    );
    return;
  }

  const probability = Number(evaluation.win_prob);
  const calibrated = Boolean(evaluation.calibrated);
  strip.className = calibrated ? "eval-block" : "eval-block uncalibrated";

  const head = el("div", "eval-head");
  head.append(
    el("span", "eval-label", "Win probability"),
    el("span", "eval-value", calibrated ? `${(probability * 100).toFixed(0)}%` : "uncalibrated")
  );

  const bar = el("div", "eval-bar");
  const fill = el("span");
  fill.style.width = `${Math.max(0, Math.min(1, probability)) * 100}%`;
  bar.appendChild(fill);

  const note = el("div", "eval-note");
  const odds = evaluation.log_odds;
  note.textContent = calibrated
    ? `log odds ${odds === null ? "decided" : odds.toFixed(2)} · fit and calibrated (M6)`
    : `log odds ${odds === null ? "decided" : odds.toFixed(2)} · hand-weighted, not a probability`;

  strip.append(head, bar, note);

  /* The whole game, when the file is a review: the coach's curve with the
   * selected turn marked, so scrubbing the spine walks a line rather than a
   * number. Drawn as SVG from the event; nothing is recomputed. */
  const curve = gameAnalysis && Array.isArray(gameAnalysis.curve) ? gameAnalysis.curve : null;
  if (curve && curve.length > 1) strip.appendChild(evalCurve(curve, point.kind === "turn" ? point.turn : null));
}

function evalCurve(curve, currentTurn) {
  const NS = "http://www.w3.org/2000/svg";
  const width = 100;
  const height = 28;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "eval-curve");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");

  const x = (index) => (index / (curve.length - 1)) * width;
  const y = (value) => (1 - Math.max(0, Math.min(1, Number(value)))) * (height - 2) + 1;

  const half = document.createElementNS(NS, "line");
  half.setAttribute("x1", "0");
  half.setAttribute("x2", String(width));
  half.setAttribute("y1", String(y(0.5)));
  half.setAttribute("y2", String(y(0.5)));
  half.setAttribute("class", "half");
  svg.appendChild(half);

  const path = document.createElementNS(NS, "path");
  path.setAttribute(
    "d",
    curve.map((c, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(c.win_prob).toFixed(2)}`).join(" ")
  );
  svg.appendChild(path);

  const index = curve.findIndex((c) => c.turn === currentTurn);
  if (index >= 0) {
    const dot = document.createElementNS(NS, "circle");
    dot.setAttribute("cx", x(index).toFixed(2));
    dot.setAttribute("cy", y(curve[index].win_prob).toFixed(2));
    dot.setAttribute("r", "1.6");
    svg.appendChild(dot);
  }
  return svg;
}

// --------------------------------------------------------- Showdown scene

/* Every protocol line in the trace, in order.
 *
 * This is the same log the viewer prints under "what the server said", handed
 * to Showdown's renderer instead of to a <pre>. It is why the animation costs
 * nothing extra to produce: the trace already had to record what happened for
 * the decision log to mean anything (D15), and a faithful protocol stream is
 * exactly what the renderer eats. */
function allProtocolLines() {
  const lines = [];
  for (const point of points) {
    for (const line of point.log || []) lines.push(line);
  }
  if (battleEnd && battleEnd.log) {
    for (const line of battleEnd.log) lines.push(line);
  }
  return lines;
}

function postScene(message) {
  if (!ui.sceneFrame.contentWindow) return;
  ui.sceneFrame.contentWindow.postMessage({ kind: "battle", ...message }, "*");
}

function resetScene() {
  sceneSent = 0;
  if (sceneReady) postScene({ reset: true, lines: [], turn: 0 });
}

/* Show the selected decision in the animation.
 *
 * `animate` is on only when following a live battle: a scrub should land on the
 * turn immediately, or clicking through the spine would queue up a minute of
 * animation the reader did not ask for. */
function renderScene(point) {
  if (!sceneReady || sceneHidden || !point) return;

  const lines = allProtocolLines();
  const turn = point.kind === "preview" ? 0 : point.turn || 0;
  // Build on the first post as well as when the log has shrunk: the frame has
  // no battle to append to until one has been built.
  const reset = sceneSent === 0 || lines.length < sceneSent;

  /* Animate when the log has actually grown under a reader who is following.
   *
   * Deliberately not keyed on the live badge. The badge is a staleness
   * heuristic, and a human taking a minute over a doubles turn flips it — which
   * silently turned every subsequent update back into an instant seek and was
   * why moves stopped animating partway through a real game. Log growth is the
   * thing that actually means "something happened"; `following` is the thing
   * that means "and you are watching the front of it". */
  const grew = sceneSent > 0 && lines.length > sceneSent;
  const mode = grew && following && !reset ? "play" : "seek";

  postScene({
    reset,
    lines,
    turn,
    mode,
    // The trace is one agent's view, and that agent is as often p2 as p1. Left
    // to its default the renderer draws p1 at the bottom, which shows a battle
    // the bot played as p2 from the opponent's chair.
    viewpoint: battleStart && battleStart.player_role ? battleStart.player_role : null,
  });
  sceneSent = lines.length;
}

function setSceneSpeed(speed) {
  sceneSpeed = speed;
  postScene({ kind: "battle-speed", speed });
  try {
    localStorage.setItem("champions.speed", speed);
  } catch {
    // Storage unavailable; the choice just does not persist.
  }
}

function setSceneVolume(volume) {
  sceneVolume = Math.max(0, Math.min(100, Number(volume) || 0));
  postScene({ kind: "battle-volume", volume: sceneVolume });
  try {
    localStorage.setItem("champions.volume", String(sceneVolume));
  } catch {
    // Storage unavailable; the choice just does not persist.
  }
}

window.addEventListener("message", (event) => {
  if (event.source !== ui.sceneFrame.contentWindow) return;
  if (!event.data || event.data.kind !== "battle-ready") return;
  sceneReady = true;
  sceneSent = 0;
  setSceneSpeed(sceneSpeed);
  setSceneVolume(sceneVolume);
  renderScene(selected);
});

function setSceneHidden(hidden) {
  sceneHidden = hidden;
  ui.scene.hidden = hidden;
  ui.sceneShow.hidden = !hidden;
  try {
    localStorage.setItem("champions.scene", hidden ? "hidden" : "shown");
  } catch {
    // Private windows and blocked site data: the preference is a convenience,
    // not something to fail over.
  }
  if (!hidden) {
    sceneSent = 0;
    renderScene(selected);
  }
}

// ------------------------------------------------------------- selection

function select(point) {
  selected = point;
  if (!point) return;

  const roles = boardRoles(point.equilibrium);
  renderSide(ui.theirs, point.state && point.state.theirs, "opponent", "theirs", roles.theirs);
  renderSide(ui.ours, point.state && point.state.ours, "us", "ours", roles.ours);
  renderConditions(point.state);
  renderEval(point);
  renderLog(point.log);
  renderReview(point);
  renderChosen(point);
  renderAnalysis(point);
  renderTiming(point);
  renderStrategy(point);
  renderCandidates(point);
  renderBelief(point);
  renderScene(point);
  renderTurnList();
  ui.follow.hidden = following || !liveStream;
}

/* What the bot is doing, read off the trace. A turn's events land in order --
 * `turn_start`, then the search's, then `equilibrium` -- so a live trace whose
 * newest turn has no equilibrium yet is a bot thinking, and one whose newest
 * turn has one is a bot waiting on the opponent. Between battles the trace is
 * silent and the ladder script's status file says what is happening. */
function phaseOf() {
  const now = Date.now() / 1000;
  const since = (t) => (typeof t === "number" ? ` \u00b7 ${Math.max(0, Math.round(now - t))}s` : "");
  const ladder = ladderStatus && ladderStatus.phase ? ladderStatus : null;
  const ladderText = () => {
    if (!ladder) return null;
    const n = ladder.game && ladder.of ? ` (${ladder.game}/${ladder.of})` : "";
    if (ladder.phase === "searching") return { text: `searching${n}${since(ladder.t)}`, tone: "busy" };
    if (ladder.phase === "reviewing") return { text: `coach reviewing${n}${since(ladder.t)}`, tone: "busy" };
    if (ladder.phase === "done") return { text: "ladder run finished", tone: "idle" };
    if (ladder.phase === "battle") return { text: `in battle${n}`, tone: "live" };
    return null;
  };

  if (!events.length) return ladderText() || { text: "waiting for a battle", tone: "idle" };
  if (battleEnd) {
    const after = ladderText();
    if (after && after.tone !== "live") return after;
    return { text: `battle over \u00b7 ${battleEnd.result || "ended"}`, tone: "idle" };
  }
  if (!liveStream) return { text: "replay", tone: "idle" };

  const last = points.length ? points[points.length - 1] : null;
  if (!last) return { text: "team preview", tone: "thinking" };
  if (last.kind === "preview") return { text: "waiting for turn 1", tone: "live" };
  if (!last.equilibrium) return { text: `thinking \u00b7 turn ${last.turn}${since(last.events[0].t)}`, tone: "thinking" };
  const decided = last.events.find((e) => e.type === "equilibrium");
  return { text: `waiting for opponent \u00b7 turn ${last.turn}${since(decided && decided.t)}`, tone: "live" };
}

function renderPhase() {
  const phase = phaseOf();
  const stopping =
    ladderStatus && ladderStatus.stop_requested && ladderStatus.phase !== "done"
      ? " \u00b7 then stop"
      : "";
  ui.phase.textContent = phase.text + stopping;
  ui.phase.className = `badge ${phase.tone}`;
}

/* The ladder run's own controls. It is not a subprocess of the viewer, so the
 * only lever is a flag file it reads between games (D82). */
/* The bot's rating and rank. Who to ask about: the ladder run's status file
 * names the account and format while a run is on; otherwise a rated battle's
 * trace does (`battle_end.rating` is null for anything unrated, and self-play
 * names are not ladder accounts). Polled every minute and again when a battle
 * ends, which is when the number moves. */
let ratingKey = null;
let ratingFetchedAt = 0;

function ratingSubject() {
  if (account && account.username && account.format) {
    return { user: account.username, format: account.format };
  }
  if (ladderStatus && ladderStatus.username && ladderStatus.format) {
    return { user: ladderStatus.username, format: ladderStatus.format };
  }
  if (battleStart && battleEnd && battleEnd.rating !== null && battleEnd.rating !== undefined) {
    return { user: battleStart.player_username, format: battleStart.format_id };
  }
  return null;
}

function renderRating(data) {
  if (!data || data.rated === false || data.error) {
    ui.ratingGroup.hidden = true;
    return;
  }
  ui.eloValue.textContent = typeof data.elo === "number" ? String(Math.round(data.elo)) : "\u2014";
  const parts = [];
  if (typeof data.rank === "number") parts.push(`rank #${data.rank}`);
  else if (data.rank === null) parts.push(`not in top ${data.top || 500}`);
  if (typeof data.gxe === "number") parts.push(`GXE ${data.gxe.toFixed(1)}%`);
  if (typeof data.w === "number" && typeof data.l === "number") parts.push(`${data.w}\u2013${data.l}`);
  ui.rating.textContent = parts.join(" \u00b7 ");
  ui.ratingGroup.title = [
    data.username ? `${data.username} on ${data.format}` : "",
    typeof data.rpr === "number" ? `Glicko estimate ${Math.round(data.rpr)}` : "",
    "Showdown's own numbers; rank is the position in the published top 500",
  ]
    .filter(Boolean)
    .join(" \u00b7 ");
  ui.ratingGroup.hidden = typeof data.elo !== "number";
}

async function pollRating(force) {
  const subject = ratingSubject();
  if (!subject) {
    ui.ratingGroup.hidden = true;
    ratingKey = null;
    return;
  }
  const key = `${subject.user}|${subject.format}`;
  const stale = Date.now() - ratingFetchedAt > 60000;
  if (!force && key === ratingKey && !stale) return;
  ratingKey = key;
  ratingFetchedAt = Date.now();
  try {
    const params = new URLSearchParams({ user: subject.user, format: subject.format });
    renderRating(await api(`/api/ladder?${params}`));
  } catch {
    // The site is unreachable or slow; the last reading stays up.
  }
}

function renderLadder() {
  const ladder = ladderStatus && ladderStatus.phase ? ladderStatus : null;
  const active = Boolean(ladder && ladder.phase !== "done");
  // With an account configured the group is always there: the start form
  // when nothing is running, the run's own controls while it is (D87).
  const canStart = Boolean(account && account.username);
  ui.ladderGroup.hidden = !active && !canStart;
  ui.ladderStartForm.hidden = active || !canStart;
  ui.ladderStop.hidden = !active || Boolean(ladder && ladder.stop_requested);
  ui.ladderResume.hidden = !active || !(ladder && ladder.stop_requested);
  if (!active) {
    ui.ladderLabel.textContent = canStart ? "idle" : "";
    return;
  }
  const untilStopped = ladder.of && ladder.of >= 100000;
  const n = ladder.game && ladder.of ? (untilStopped ? `game ${ladder.game}` : `game ${ladder.game}/${ladder.of}`) : "";
  const what = { searching: "searching", battle: "in battle", reviewing: "coach reviewing" }[ladder.phase] || ladder.phase;
  ui.ladderLabel.textContent = `${n} \u00b7 ${what}`;
}

let ratedEndSeen = null;

function refresh() {
  points = fold(events);
  renderMeta();
  renderPhase();
  if (battleEnd && battleEnd !== ratedEndSeen) {
    ratedEndSeen = battleEnd;
    // The rating moves at the end of a rated game; fetch it fresh then.
    if (battleEnd.rating !== null && battleEnd.rating !== undefined) pollRating(true);
  }

  if (!points.length) {
    ui.layout.hidden = true;
    ui.empty.hidden = false;
    return;
  }
  ui.layout.hidden = false;
  ui.empty.hidden = true;

  /* Following pins the view to the newest decision. Otherwise hold whatever the
   * reader was looking at, matched by seq so it survives the list growing. */
  const keep = selected ? points.find((p) => p.seq === selected.seq) : null;
  select(following || !keep ? points[points.length - 1] : keep);
}

// ---------------------------------------------------------------- control

/* The viewer starts what it displays. Everything below drives the supervisor
 * over the control API and reflects its state; none of it touches the trace
 * stream, which still arrives the same way whoever started the battle. */

const SIM_TONE = { ready: "ok", external: "ok", starting: "busy", failed: "bad", off: "" };
const SIM_TEXT = {
  ready: "ready",
  external: "ready (external)",
  starting: "starting",
  failed: "failed",
  off: "stopped",
};

/* Agent names people should recognise. Anything the server offers that is not
 * in here still appears, under its own id, rather than being dropped. */
const AGENT_LABELS = { random: "random", greedy: "max base power" };

let lastRunKey = null;
let agentsFilled = false;

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `${path} failed`);
  return data;
}

function fillAgents(agents) {
  if (agentsFilled || !agents || !agents.length) return;
  agentsFilled = true;
  for (const [select, preferred] of [
    [ui.spAgentA, "random"],
    [ui.spAgentB, "random"],
    [ui.hostAgent, "greedy"],
  ]) {
    select.replaceChildren();
    for (const name of agents) {
      const option = el("option", null, AGENT_LABELS[name] || name);
      option.value = name;
      select.appendChild(option);
    }
    if (agents.includes(preferred)) select.value = preferred;
  }
}

function renderStatus(status) {
  fillAgents(status.agents);
  ladderStatus = status.live || null;
  account = status.account || null;
  renderPhase();
  renderLadder();
  pollRating(false);

  const sim = status.showdown || {};
  const state = sim.state || "off";
  ui.simDot.className = `dot ${SIM_TONE[state] || ""}`;
  ui.simState.textContent = sim.error
    ? `sim ${SIM_TEXT[state] || state}: ${sim.error}`
    : `sim ${SIM_TEXT[state] || state}`;
  ui.simStart.disabled = state === "ready" || state === "external" || state === "starting";
  // A simulator someone else started is not ours to stop.
  ui.simStop.disabled = !sim.ours;

  const run = status.run;
  const running = Boolean(run && run.state === "running");
  ui.spStart.disabled = running;
  ui.hostStart.disabled = running;
  ui.runForfeit.hidden = !running;
  ui.runStop.hidden = !running;
  ui.runLogToggle.hidden = !run;

  if (!run) {
    ui.runLabel.textContent = "no run";
    ui.runLabel.classList.add("dim");
    ui.runLog.textContent = "";
    ui.hostInvite.hidden = true;
    return;
  }

  /* Progress if there is any, the label otherwise.
   *
   * Self-play prints one line per finished battle and that is the most useful
   * thing to show; a hosted bot prints its instructions once and then waits, and
   * echoing the tail of those turns the status line into a fragment of a
   * sentence. The full output is one click away either way. */
  const log = run.log || [];
  /* `control:` lines are the agent answering a forfeit. They belong on the
   * status line for the same reason the battle counter does: it is the only
   * confirmation that the button did anything, and the alternative is opening
   * the output panel to find out. */
  const progress = [...log]
    .reverse()
    .find((line) => line.startsWith("battle ") || line.startsWith("control: "));
  ui.runLabel.classList.toggle("dim", !running);
  ui.runLabel.textContent = running
    ? shorten(progress) || run.label
    : `${run.label} — ${run.state}`;
  ui.runLabel.title = progress ? `${run.label}
${progress}` : run.label;
  ui.runLog.textContent = log.join("\n");
  if (!ui.runLog.hidden) ui.runLog.scrollTop = ui.runLog.scrollHeight;

  /* A newly started run is the thing the reader wants to be looking at, so
   * release any manual pin rather than leaving them on an old battle
   * wondering why nothing is happening. */
  const key = `${run.kind}:${run.label}:${run.state}`;
  if (key !== lastRunKey) {
    lastRunKey = key;
    if (running) {
      pinned = false;
      following = true;
    }
  }

  if (run.kind === "host" && running) renderInvite(run.detail || {});
  else ui.hostInvite.hidden = true;
}

/* The battle tag is most of the length of a progress line and none of its
 * meaning: "battle 2/4" and the result are what a glance is after, and the tag
 * is already in the trace picker, the meta line and this label's tooltip. The
 * status line is the one place on screen with no room for it. */
function shorten(line) {
  if (!line) return line;
  return line
    .replace(/battle-[a-z0-9]+-\d+/g, "")
    .replace(/:\s*->/, ":")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/* The handoff to Showdown, in one line.
 *
 * This was a numbered four-step card. The steps were all true and nobody needs
 * them twice: what a returning reader actually wants is the bot's name, the
 * format, and the way in. */
function renderInvite(detail) {
  if (ui.hostInvite.dataset.for === detail.username) {
    ui.hostInvite.hidden = false;
    return;
  }
  ui.hostInvite.dataset.for = detail.username || "";
  ui.hostInvite.replaceChildren();

  ui.hostInvite.append(
    el("b", null, detail.username || "the bot"),
    " is waiting in ",
    el("code", null, detail.format_id || ""),
    " — import ",
    el("code", null, detail.team_file || ""),
    ", challenge it, and decline Open Team Sheets."
  );

  const launch = el("a", "launch", "Open Showdown ↗");
  launch.href = detail.showdown_url || "#";
  launch.target = "champions-showdown";
  launch.rel = "noopener";
  ui.hostInvite.appendChild(launch);
  ui.hostInvite.hidden = false;
}

async function pollStatus() {
  try {
    renderStatus(await api("/api/status"));
  } catch {
    ui.simDot.className = "dot bad";
    ui.simState.textContent = "viewer unreachable";
  }
}

function wireControl() {
  const act = async (button, path, body) => {
    button.disabled = true;
    try {
      await api(path, body);
      await pollStatus();
    } catch (error) {
      ui.runLabel.textContent = String(error.message || error);
      ui.runLabel.classList.remove("dim");
    } finally {
      button.disabled = false;
    }
  };

  ui.simStart.addEventListener("click", () => act(ui.simStart, "/api/showdown/start", {}));
  ui.simStop.addEventListener("click", () => act(ui.simStop, "/api/showdown/stop", {}));

  /* Conceding is not confirmed and stopping is. A forfeit costs one game out of
   * a run you can simply run again, which is the whole reason the button
   * exists; a dialog in front of it would put the friction back. Killing the
   * run takes every remaining game with it, which is worth a question. */
  ui.runForfeit.addEventListener("click", () => act(ui.runForfeit, "/api/run/forfeit", {}));
  ui.runStop.addEventListener("click", () => {
    if (window.confirm("Stop the run? Any games it has left will not be played.")) {
      act(ui.runStop, "/api/run/stop", {});
    }
  });

  ui.runLogToggle.addEventListener("click", () => {
    ui.runLog.hidden = !ui.runLog.hidden;
    if (!ui.runLog.hidden) ui.runLog.scrollTop = ui.runLog.scrollHeight;
  });

  ui.spStart.addEventListener("click", () =>
    act(ui.spStart, "/api/run/selfplay", {
      games: Number(ui.spGames.value) || 1,
      seed: Number(ui.spSeed.value) || 0,
      agent_a: ui.spAgentA.value,
      agent_b: ui.spAgentB.value,
    })
  );

  ui.hostStart.addEventListener("click", () =>
    act(ui.hostStart, "/api/run/host", {
      agent: ui.hostAgent.value,
      games: Number(ui.hostGames.value) || 1,
    })
  );
}

// -------------------------------------------------------------- transport

function setLive(isLive) {
  // Called on every poll, so make a no-op actually do nothing: reassigning the
  // class would restart the badge's pulse animation four times a second.
  if (isLive === liveStream) {
    ui.follow.hidden = following || !isLive;
    return;
  }
  liveStream = isLive;
  ui.liveBadge.textContent = isLive ? "live" : "replay";
  ui.liveBadge.className = `badge ${isLive ? "live" : "idle"}`;
  ui.follow.hidden = following || !isLive;
  renderPhase();
}

function openTrace(traceId) {
  openedId = traceId;
  if (socket) {
    socket.onclose = null;
    socket.close();
  }
  events = [];
  points = [];
  selected = null;
  following = true;
  // A different battle is a different log, so the renderer starts over rather
  // than having a second game appended to the first.
  resetScene();
  setLive(false);
  refresh();

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws/trace/${encodeURI(traceId)}`);

  socket.onmessage = (message) => {
    let batch;
    try {
      batch = JSON.parse(message.data);
    } catch {
      return;
    }
    if (batch.kind === "error") {
      ui.liveBadge.textContent = "not found";
      return;
    }
    if (batch.events && batch.events.length) {
      events = events.concat(batch.events);
      refresh();
    }
    /* Live means two things at once, and both are required: the backlog is
     * drained, so anything further is arriving as the agent produces it, and
     * the file is still being written, so there is something further to come.
     * A finished battle satisfies the first and not the second, and calling
     * that live would leave the badge lit for the rest of the session. */
    const fresh = typeof batch.age_s === "number" && batch.age_s < LIVE_AFTER_WRITE_S;
    setLive(Boolean(batch.live) && fresh);
  };

  socket.onclose = () => setLive(false);

  const url = new URL(location.href);
  url.searchParams.set("trace", traceId);
  history.replaceState(null, "", url);
}

/* The games list in the side pane: one row per game, newest first, with who
 * it was against and how it went. The self-play case writes one file per
 * side of a battle and both are listed, since they are two views. */
function renderGameList(traces, currentId) {
  ui.gameList.replaceChildren();
  const games = traces.filter((trace) => !trace.is_review);
  if (!games.length) {
    ui.gameList.appendChild(el("div", "game-empty", "no games yet"));
    return;
  }
  for (const trace of games) {
    const row = el("button", "game-row");
    row.type = "button";
    row.dataset.trace = trace.id;
    if (trace.id === currentId) row.classList.add("is-current");

    const who = el("span", "who");
    who.append(
      el("span", "us", trace.player || trace.agent || trace.id),
      el("span", "vs", " vs "),
      el("b", null, trace.opponent || "?")
    );
    row.appendChild(who);

    let tone = "pending";
    let text = "waiting";
    if (trace.live && !trace.result) {
      tone = "live";
      text = "in progress";
    } else if (trace.result) {
      tone = trace.result;
      text = trace.result;
    }
    const status = el("span", `game-status ${tone}`, text);
    if (trace.review) status.title = "reviewed by the coach";
    row.appendChild(status);
    if (trace.review) row.appendChild(el("span", "game-reviewed", "coach"));
    row.title = `${trace.id}${trace.turns ? ` \u00b7 ${trace.turns} turns` : ""}`;

    row.addEventListener("click", () => {
      pinned = true;
      currentBattle = trace.battle_id;
      ui.picker.value = trace.id;
      openTrace(openIdFor(trace.id));
      renderGameList(traces, trace.id);
    });
    ui.gameList.appendChild(row);
  }
}

async function loadTraceList() {
  let data;
  try {
    const response = await fetch("/api/traces");
    data = await response.json();
  } catch {
    return;
  }
  showdownUrl = data.showdown_url || null;

  const previous = ui.picker.value;
  ui.picker.replaceChildren();
  traceIndex = new Map(data.traces.map((trace) => [trace.id, trace]));
  // Reviews ride behind their game (D84); the list and the picker hold games.
  const listed = data.traces.filter((trace) => !trace.is_review);

  if (!listed.length) {
    ui.picker.appendChild(el("option", null, "no traces yet"));
    ui.picker.disabled = true;
    renderGameList(data.traces, null);
    return;
  }
  ui.picker.disabled = false;

  for (const trace of listed) {
    const option = el("option", null, `${trace.live ? "● " : ""}${trace.id}`);
    option.value = trace.id;
    ui.picker.appendChild(option);
  }

  const wanted = new URL(location.href).searchParams.get("trace");
  const ids = listed.map((trace) => trace.id);
  data = { ...data, traces: listed };

  /* Follow battles, not files.
   *
   * The listing is newest-first, so the newest battle is data.traces[0]. But a
   * battle writes one file per agent-view, and in self-play both are written
   * within milliseconds of each other, so "the most recently modified file"
   * flips between champ-a and champ-b on almost every poll. Following that
   * directly tore the websocket down and rebuilt it once a second, which is why
   * a running battle never settled long enough to show anything.
   *
   * Keying on battle_id makes the two views of one battle indistinguishable for
   * this purpose, so a switch only happens when a genuinely different battle
   * shows up. Within a battle we pick the first id in sorted order, which is
   * stable across polls for the same reason. */
  const newestBattle = data.traces[0].battle_id;
  const sameBattle = data.traces
    .filter((trace) => trace.battle_id === newestBattle)
    .map((trace) => trace.id)
    .sort();

  let target = sameBattle[0];
  if (pinned && ids.includes(previous)) target = previous;
  else if (!previous && ids.includes(wanted)) target = wanted;
  else if (previous && currentBattle === newestBattle && ids.includes(previous)) {
    // Already watching this battle, possibly the other side of it. Stay put.
    target = previous;
  }

  ui.picker.value = target;
  if (target !== previous) {
    currentBattle = newestBattle;
    openTrace(openIdFor(target));
  } else if (openedId === target && openIdFor(target) !== target) {
    // The game on screen has finished and the coach has written its review:
    // show the review in place of the plain trace (D84).
    openTrace(openIdFor(target));
  }
  renderGameList(data.traces, target);

  /* If a battle is being written to and it is not the one on screen, say so.
   *
   * Pinning is deliberate — the viewer should not yank someone off the trace
   * they are reading — but silent pinning is indistinguishable from a broken
   * live view, which is precisely how this failed in practice. The button is
   * the difference between "nothing is happening" and "something is happening
   * over here". */
  const liveElsewhere = data.traces.find(
    (trace) => trace.live && trace.battle_id !== currentBattle
  );
  /* A reader who has not pinned anything and whose battle has ended is
   * waiting for the next one: take them there. The ladder writes a new file
   * per game, and "click the button every game" is not following live. */
  if (liveElsewhere && !pinned && !liveStream) {
    following = true;
    currentBattle = liveElsewhere.battle_id;
    ui.picker.value = liveElsewhere.id;
    openTrace(openIdFor(liveElsewhere.id));
    renderGameList(data.traces, liveElsewhere.id);
    ui.gotoLive.hidden = true;
    return;
  }
  ui.gotoLive.hidden = !liveElsewhere;
  ui.gotoLive.dataset.trace = liveElsewhere ? liveElsewhere.id : "";
}

// ------------------------------------------------------------------- init

ui.picker.addEventListener("change", () => {
  pinned = true;
  openTrace(openIdFor(ui.picker.value));
});

ui.follow.addEventListener("click", () => {
  following = true;
  refresh();
});

ui.gotoLive.addEventListener("click", () => {
  const target = ui.gotoLive.dataset.trace;
  if (!target) return;
  pinned = false;
  following = true;
  currentBattle = null;
  ui.picker.value = target;
  openTrace(openIdFor(target));
  ui.gotoLive.hidden = true;
});

/* The real Showdown client, in its own window beside this one.
 *
 * A window rather than a frame because the client refuses to run framed — it
 * checks `self === top` and, finding otherwise, halts and tries to navigate
 * the outer page to itself. That is a deliberate anti-framing measure in
 * Smogon's own source, so the battle view above is rendered from the trace and
 * the real client is opened properly instead. */
ui.showdown.addEventListener("click", () => {
  if (!showdownUrl) return;
  const room = battleId ? `/${battleId}` : "/";

  // Beside this window if it fits on the screen, otherwise pinned to the right.
  const width = Math.min(1180, Math.max(720, screen.availWidth - window.outerWidth - 8));
  const height = Math.max(640, window.outerHeight);
  const beside = window.screenX + window.outerWidth + 4;
  const left = beside + width <= screen.availWidth ? beside : screen.availWidth - width;

  window.open(
    showdownUrl + room,
    "champions-showdown",
    `popup=yes,width=${width},height=${height},left=${Math.max(0, left)},top=${window.screenY}`
  );
});

/* Keyboard scrubbing, because reading a trace is mostly stepping through it. */
document.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLSelectElement) return;
  const index = points.indexOf(selected);
  if (index < 0) return;

  if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
    following = false;
    select(points[Math.max(0, index - 1)]);
  } else if (event.key === "ArrowDown" || event.key === "ArrowRight") {
    const next = Math.min(points.length - 1, index + 1);
    following = next === points.length - 1;
    select(points[next]);
  } else {
    return;
  }
  event.preventDefault();
});

ui.ladderStop.addEventListener("click", async () => {
  ui.ladderStop.disabled = true;
  try {
    await api("/api/live/stop", {});
    await pollStatus();
  } finally {
    ui.ladderStop.disabled = false;
  }
});
ui.ladderStart.addEventListener("click", async () => {
  ui.ladderStart.disabled = true;
  try {
    const games = Number(ui.ladderGames.value) || 0;
    await api("/api/live/start", { games });
    ladderStatus = { phase: "searching", game: 1, of: games || 100000, t: Date.now() / 1000 };
    renderLadder();
    renderPhase();
  } catch (error) {
    alert(`could not start the ladder run: ${error.message || error}`);
  } finally {
    ui.ladderStart.disabled = false;
  }
});

ui.ladderResume.addEventListener("click", async () => {
  ui.ladderResume.disabled = true;
  try {
    await api("/api/live/resume", {});
    await pollStatus();
  } finally {
    ui.ladderResume.disabled = false;
  }
});

ui.sceneSpeed.addEventListener("change", () => setSceneSpeed(ui.sceneSpeed.value));
ui.sceneVolume.addEventListener("input", () => setSceneVolume(ui.sceneVolume.value));
try {
  const savedVolume = localStorage.getItem("champions.volume");
  if (savedVolume !== null && savedVolume !== "") {
    sceneVolume = Math.max(0, Math.min(100, Number(savedVolume) || 0));
    ui.sceneVolume.value = String(sceneVolume);
  }
} catch {
  // Storage unavailable; the default volume it is.
}
try {
  const saved = localStorage.getItem("champions.speed");
  if (saved) {
    sceneSpeed = saved;
    ui.sceneSpeed.value = saved;
  }
} catch {
  // Storage unavailable; normal speed it is.
}

ui.sceneToggle.addEventListener("click", () => setSceneHidden(true));
ui.sceneShow.addEventListener("click", () => setSceneHidden(false));
try {
  if (localStorage.getItem("champions.scene") === "hidden") setSceneHidden(true);
} catch {
  // Storage unavailable; the scene stays shown, which is the default anyway.
}

wireControl();
loadTraceList();
pollStatus();

/* New battles write new files, so the list has to keep discovering them; the
 * events themselves arrive over the socket, not from this poll. */
setInterval(loadTraceList, 1000);
setInterval(pollStatus, 1200);
// The pill carries elapsed seconds, so it ticks even when nothing arrives.
setInterval(renderPhase, 1000);
setInterval(() => pollRating(false), 15000);

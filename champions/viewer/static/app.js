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
  hostStart: document.getElementById("host-start"),
  hostInvite: document.getElementById("host-invite"),
  runLabel: document.getElementById("run-label"),
  runLog: document.getElementById("run-log"),
  runLogToggle: document.getElementById("run-log-toggle"),
  runStop: document.getElementById("run-stop"),
  layout: document.getElementById("layout"),
  empty: document.getElementById("empty"),
  turnList: document.getElementById("turn-list"),
  theirs: document.getElementById("side-theirs"),
  ours: document.getElementById("side-ours"),
  conditions: document.getElementById("conditions"),
  log: document.getElementById("log"),
  chosen: document.getElementById("chosen"),
  timing: document.getElementById("timing"),
  strategy: document.getElementById("strategy"),
  candidates: document.getElementById("candidates"),
  belief: document.getElementById("belief"),
  scene: document.getElementById("scene"),
  sceneFrame: document.getElementById("scene-frame"),
  sceneToggle: document.getElementById("scene-toggle"),
  sceneShow: document.getElementById("scene-show"),
  sceneSpeed: document.getElementById("scene-speed"),
};

let socket = null;
let events = [];
let points = [];
let selected = null;
/* Following means "always show the newest decision". Scrubbing turns it off,
 * because a view that yanks itself away mid-read is worse than a stale one. */
let following = true;
let liveStream = false;
let battleStart = null;
let battleEnd = null;
let battleId = null;
let showdownUrl = null;
/* Set once the reader picks a trace by hand. Until then the viewer follows
 * whatever is newest, which is what makes "start the viewer, then start a
 * battle" work: the file does not exist yet when the window opens. After an
 * explicit choice it stays put, because silently jumping away from the trace
 * someone is reading is worse than making them pick again. */
let pinned = false;
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
    if (type === "turn_start") {
      current = {
        kind: "turn",
        seq: event.seq,
        turn: payload.turn,
        state: payload.state || null,
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
      row.appendChild(el("span", "what", actionLabel(point.equilibrium) || "…"));
      /* The clock is a correctness surface, not a performance one: VGC Timer
       * auto-loses an inactive player, so a slow turn is flagged in the spine
       * where it cannot be missed. */
      if (point.timing && (point.timing.exceeded_45s || point.timing.watchdog_fired)) {
        row.classList.add("slow");
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
    row.appendChild(el("span", "what", `${battleEnd.result} · ${battleEnd.turns} turns`));
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

  const line = el("div", "known");
  line.append(
    "policy ",
    el("b", null, eq.strategy || "unknown"),
    eq.value !== null && eq.value !== undefined ? ` · value ${eq.value}` : ""
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

/* What we know about the opponent, split into what was revealed and what has to
 * be inferred. The second half is the belief filter's job (M3) and is the
 * single most useful debugging surface in the system once it exists, so the
 * panel is laid out now around the shape it will take. */
function renderBelief(point) {
  ui.belief.replaceChildren();
  const state = point.state;
  if (!state || !state.theirs) return;

  const head = el("h2", null, "Opponent");
  head.appendChild(el("span", "hint", "revealed vs inferred"));
  ui.belief.appendChild(head);

  const team = [...(state.theirs.active || []).filter(Boolean), ...(state.theirs.bench || [])];
  for (const mon of team) {
    const box = el("div", "belief-mon");
    const top = el("div", "top");
    top.appendChild(el("span", null, mon.species));
    top.appendChild(el("span", "hint", mon.fainted ? "fainted" : `~${mon.hp_pct}%`));
    box.appendChild(top);

    const moves = (mon.revealed_moves || []).map((move) => move.name || move.id);
    const known = el("div", "known");
    known.append("moves ", el("b", null, moves.length ? moves.join(", ") : "none seen"));
    box.appendChild(known);

    const traits = el("div", "known");
    traits.append(
      "ability ",
      el("b", null, mon.ability || `one of ${(mon.possible_abilities || []).join(" / ") || "?"}`),
      " · item ",
      el("b", null, mon.item || "unknown")
    );
    box.appendChild(traits);

    ui.belief.appendChild(box);
  }

  ui.belief.appendChild(
    pendingBlock(["belief"], "Set hypotheses, stat intervals, nature posterior")
  );
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

window.addEventListener("message", (event) => {
  if (event.source !== ui.sceneFrame.contentWindow) return;
  if (!event.data || event.data.kind !== "battle-ready") return;
  sceneReady = true;
  sceneSent = 0;
  setSceneSpeed(sceneSpeed);
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
  renderLog(point.log);
  renderChosen(point);
  renderTiming(point);
  renderStrategy(point);
  renderCandidates(point);
  renderBelief(point);
  renderScene(point);
  renderTurnList();
  ui.follow.hidden = following || !liveStream;
}

function refresh() {
  points = fold(events);
  renderMeta();

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
  ui.runStop.hidden = !running;
  ui.runLogToggle.hidden = !run;

  if (!run) {
    ui.runLabel.textContent = "idle";
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
  const progress = [...log].reverse().find((line) => line.startsWith("battle "));
  ui.runLabel.classList.toggle("dim", !running);
  ui.runLabel.textContent = running
    ? progress || run.label
    : `${run.label} — ${run.state}`;
  ui.runLabel.title = run.label;
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
  ui.runStop.addEventListener("click", () => act(ui.runStop, "/api/run/stop", {}));

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
    act(ui.hostStart, "/api/run/host", { agent: ui.hostAgent.value, games: 1 })
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
}

function openTrace(traceId) {
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

  if (!data.traces.length) {
    ui.picker.appendChild(el("option", null, "no traces yet"));
    ui.picker.disabled = true;
    return;
  }
  ui.picker.disabled = false;

  for (const trace of data.traces) {
    const option = el("option", null, `${trace.live ? "● " : ""}${trace.id}`);
    option.value = trace.id;
    ui.picker.appendChild(option);
  }

  const wanted = new URL(location.href).searchParams.get("trace");
  const ids = data.traces.map((trace) => trace.id);

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
    openTrace(target);
  }

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
  ui.gotoLive.hidden = !liveElsewhere;
  ui.gotoLive.dataset.trace = liveElsewhere ? liveElsewhere.id : "";
}

// ------------------------------------------------------------------- init

ui.picker.addEventListener("change", () => {
  pinned = true;
  openTrace(ui.picker.value);
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
  openTrace(target);
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

ui.sceneSpeed.addEventListener("change", () => setSceneSpeed(ui.sceneSpeed.value));
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
setInterval(loadTraceList, 1500);
setInterval(pollStatus, 1200);

#!/usr/bin/env node
"use strict";

// JSON-RPC 2.0 over stdio, one request per line, exposing the vendored Showdown
// simulator as the rollout and differential oracle.
//
// Methods:
//   create      {formatId, seed?, p1: {name, team}, p2: {name, team}} -> {handle, ...state}
//   materialize {formatId, seed?, p1: {name, team, order}, p2: {...}, position}
//                                                                    -> {handle, problems, ...state}
//   step        {handle, choices: {p1, p2}, seed?}                    -> {accepted, ...state}
//   serialize   {handle}                                             -> {state}
//   deserialize {state}                                              -> {handle, ...state}
//   clone       {handle}                                             -> {handle, ...state}
//   request     {handle}                                             -> {p1, p2} active requests
//   destroy     {handle}                                             -> {ok}
//   ping        {}                                                   -> {pong}
//
// Teams are accepted in export or packed format. Seeds are [n,n,n,n]; supplying
// one makes a battle exactly reproducible, which everything downstream depends
// on (CLAUDE.md: deterministic by default, seed everything).
//
// `materialize` is the M8 simulator payoff's entry point: a battle at a given
// position rather than at turn 0 (docs/specs/2026-09-13-engine-gate.md 5.2).
// It plays team preview so the right four lead, then sets what the position
// says -- HP, status, boosts, faints, consumed items, side conditions, weather,
// pseudo-weather, the turn -- through the simulator's own methods, and issues
// fresh requests. What it cannot set from an observed position (PP, Choice
// locks, Encore, volatile durations) it does not pretend to; the equivalence
// test names those exclusions.

const path = require("path");
const readline = require("readline");

const SHOWDOWN = path.join(__dirname, "..", "vendor", "showdown", "dist", "sim");
const { Battle } = require(path.join(SHOWDOWN, "battle"));
const { State } = require(path.join(SHOWDOWN, "state"));
const { Teams } = require(path.join(SHOWDOWN, "teams"));
const { PRNG } = require(path.join(SHOWDOWN, "prng"));
const { toID } = require(path.join(SHOWDOWN, "dex"));
const { RandomPlayerAI } = require(path.join(SHOWDOWN, "tools", "random-player-ai"));

const BOOST_IDS = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"];

function newBattle({ formatId, seed, p1, p2, strictChoices = false }) {
  const battle = new Battle({
    formatid: formatId,
    seed: seed ?? undefined,
    strictChoices,
  });
  battle.setPlayer("p1", { name: p1.name ?? "p1", team: packTeam(p1.team) });
  battle.setPlayer("p2", { name: p2.name ?? "p2", team: packTeam(p2.team) });
  return battle;
}

function findPokemon(side, spec) {
  const name = String(spec.name || "").toLowerCase();
  const species = toID(spec.species || "");
  return (
    side.pokemon.find((p) => p.name.toLowerCase() === name) ||
    side.pokemon.find((p) => p.species.id === species || p.baseSpecies.id === species) ||
    null
  );
}

// One Pokemon's observed state onto a simulator Pokemon. Returns a problem
// string or null. Direct field writes are used where the simulator's own
// method refuses a state the game can legitimately be in (a status on a
// benched Pokemon, a forme it would only reach through a move).
function applyPokemon(battle, side, spec) {
  const mon = findPokemon(side, spec);
  if (!mon) return `${side.id}: no Pokemon matching ${spec.name || spec.species}`;

  if (spec.species && toID(spec.species) !== mon.species.id) {
    const forme = battle.dex.species.get(spec.species);
    if (forme.exists && forme.baseSpecies === mon.baseSpecies.name) {
      // A Mega is permanent for the battle and spends the Mega flag; a
      // battle-only forme (Stance Change, Zen Mode) is temporary and reverts
      // on switch-out, and applying it permanently would have the simulator
      // announce a regression at the end of the turn that never happened.
      const permanent = !!(forme.isMega || forme.isPrimal);
      mon.formeChange(forme, null, permanent);
      if (permanent) mon.canMegaEvo = null;
    }
  }

  if (spec.fainted) {
    mon.hp = 0;
    mon.fainted = true;
    mon.faintQueued = false;
    mon.status = "fnt";
    return null;
  }

  let hp = spec.hp != null ? Number(spec.hp) : Math.round((mon.maxhp * Number(spec.hp_pct ?? 100)) / 100);
  mon.hp = Math.max(1, Math.min(mon.maxhp, Math.round(hp)));

  const status = spec.status ? String(spec.status).toLowerCase() : "";
  if (status !== (mon.status || "")) {
    if (!status) {
      mon.clearStatus();
    } else if (!mon.setStatus(status, null, null, true) || mon.status !== status) {
      mon.status = status;
      mon.statusState = battle.initEffectState({ id: status, target: mon });
    }
    if (status === "slp") {
      const turns = Math.max(1, Number(spec.status_turns_left ?? 2));
      mon.statusState.startTime = turns;
      mon.statusState.time = turns;
    } else if (status === "tox") {
      mon.statusState.stage = Math.max(0, Number(spec.status_counter ?? 0));
    }
  }

  const boosts = {};
  for (const id of BOOST_IDS) boosts[id] = Number(spec.boosts?.[id] ?? 0);
  mon.boosts = boosts;

  if (spec.item_consumed) mon.setItem("");

  // Fake Out and First Impression read how many actions the Pokemon has taken
  // since it came in; the observed position says whether it just did.
  mon.activeMoveActions = spec.first_turn ? 0 : 2;
  mon.activeTurns = spec.first_turn ? 1 : 2;

  if (mon.isActive) {
    delete mon.volatiles["stall"];
    const counter = Number(spec.protect_counter ?? 0);
    if (counter > 0) {
      mon.addVolatile("stall");
      if (mon.volatiles["stall"]) mon.volatiles["stall"].counter = Math.min(729, 3 ** counter);
    }
  }
  return null;
}

function applySide(battle, side, spec, problems) {
  for (const id of Object.keys(side.sideConditions)) side.removeSideCondition(id);
  const source = side.active.find((p) => p) || side.pokemon[0];
  for (const [id, info] of Object.entries(spec.conditions || {})) {
    const layers = Math.max(1, Number(info?.layers ?? 1));
    for (let i = 0; i < layers; i++) side.addSideCondition(id, source);
    const state = side.sideConditions[toID(id)];
    if (!state) {
      problems.push(`${side.id}: could not set side condition ${id}`);
    } else if (info?.remaining != null && state.duration != null) {
      state.duration = Number(info.remaining);
    }
  }
  for (const pokemon of spec.pokemon || []) {
    const problem = applyPokemon(battle, side, pokemon);
    if (problem) problems.push(problem);
  }
  side.pokemonLeft = side.pokemon.filter((p) => !p.fainted).length;
}

function applyField(battle, position, problems) {
  const field = battle.field;
  const source = battle.sides[0].active.find((p) => p) || battle.sides[0].pokemon[0];
  field.clearWeather();
  field.clearTerrain();
  for (const id of Object.keys(field.pseudoWeather)) field.removePseudoWeather(id);

  if (position.weather) {
    field.setWeather(position.weather.id, source);
    if (field.weather !== toID(position.weather.id)) {
      problems.push(`could not set weather ${position.weather.id}`);
    } else if (position.weather.remaining != null && field.weatherState.duration != null) {
      field.weatherState.duration = Number(position.weather.remaining);
    }
  }
  if (position.terrain) {
    field.setTerrain(position.terrain.id, source);
    if (field.terrain !== toID(position.terrain.id)) {
      problems.push(`could not set terrain ${position.terrain.id}`);
    } else if (position.terrain.remaining != null && field.terrainState.duration != null) {
      field.terrainState.duration = Number(position.terrain.remaining);
    }
  }
  for (const [id, info] of Object.entries(position.pseudo_weather || {})) {
    field.addPseudoWeather(id, source);
    const state = field.pseudoWeather[toID(id)];
    if (!state) {
      problems.push(`could not set field effect ${id}`);
    } else if (info?.remaining != null && state.duration != null) {
      state.duration = Number(info.remaining);
    }
  }
}

const battles = new Map();
let nextHandle = 1;

function packTeam(team) {
  if (!team) return team;
  // Already packed if it has no newlines and contains the packed delimiter.
  if (!team.includes("\n") && team.includes("|")) return team;
  return Teams.pack(Teams.import(team));
}

function summarize(handle, battle) {
  return {
    handle,
    turn: battle.turn,
    ended: battle.ended,
    winner: battle.winner ?? null,
    requestState: battle.requestState,
    // The log is the protocol stream; callers parse it the same way they parse
    // a live battle, so a rollout and a real game look identical to the client.
    log: battle.log,
  };
}

function revive(state) {
  // A deserialized battle is inert until restarted: it has no `send`. The
  // callback is a sink because callers read `battle.log` from the summary.
  const battle = State.deserializeBattle(state);
  battle.restart(() => {});
  return battle;
}

function snapshot(battle) {
  // State.serializeBattle assigns `state.log = battle.log` by reference, not by
  // copy. Reviving that state hands the new battle the *same* log array as the
  // original, so every clone's steps append to its parent's log. Showdown
  // throws "Infinite loop" once log.length - sentLogPos exceeds 1000, which
  // showed up as clone number ~44 failing for no visible reason.
  //
  // The JSON round trip is also exactly what a clone costs, so this is the
  // honest thing to be measuring in scripts/bench.py.
  return JSON.parse(JSON.stringify(State.serializeBattle(battle)));
}

function getBattle(handle) {
  const battle = battles.get(handle);
  if (!battle) throw new Error(`No such battle handle: ${handle}`);
  return battle;
}

function newHandle(battle) {
  const handle = nextHandle++;
  battles.set(handle, battle);
  return handle;
}

const methods = {
  ping() {
    return { pong: true };
  },

  create(params) {
    const battle = newBattle(params);
    return summarize(newHandle(battle), battle);
  },

  // A battle at a given position. `order` per side is the team preview choice
  // ("1,3,2,4": the brought four by 1-based slot in the team text, the two
  // leads first). `position` is {turn, weather?, terrain?, pseudo_weather,
  // sides: {p1: {conditions, pokemon: [...]}, p2: {...}}}; see
  // champions/search/rollout.py for the exact shape, which is the Python
  // side's to define. Problems are reported, not thrown: a position the
  // simulator cannot fully reproduce is still a battle, and the caller
  // decides whether a partial one is usable.
  materialize({ formatId, seed, p1, p2, position }) {
    const battle = newBattle({ formatId, seed, p1, p2 });
    const problems = [];
    for (const [id, player] of [["p1", p1], ["p2", p2]]) {
      if (!battle.choose(id, `team ${player.order}`)) {
        problems.push(`${id}: team preview choice "team ${player.order}" refused`);
      }
    }
    if (battle.requestState !== "move") {
      problems.push(`battle is in ${battle.requestState || "no"} request state after preview`);
      return { ...summarize(newHandle(battle), battle), problems };
    }
    for (const side of battle.sides) {
      applySide(battle, side, position.sides?.[side.id] || {}, problems);
    }
    applyField(battle, position, problems);
    battle.turn = Number(position.turn ?? battle.turn);
    battle.makeRequest("move");
    return { ...summarize(newHandle(battle), battle), problems };
  },

  step({ handle, choices, seed }) {
    const battle = getBattle(handle);
    // A caller that wants common random numbers across cells hands every
    // clone the same seed before stepping it.
    if (seed) battle.prng = new PRNG(seed);
    const accepted = {};
    for (const side of ["p1", "p2"]) {
      const choice = choices?.[side];
      if (choice !== undefined && choice !== null) {
        accepted[side] = battle.choose(side, choice);
      }
    }
    return { ...summarize(handle, battle), accepted };
  },

  serialize({ handle }) {
    return { state: State.serializeBattle(getBattle(handle)) };
  },

  deserialize({ state }) {
    const battle = revive(state);
    return summarize(newHandle(battle), battle);
  },

  clone({ handle }) {
    // Serialize then deserialize: the simulator has no cheaper deep copy, and
    // this is the cost the search budget is actually built on.
    const battle = revive(snapshot(getBattle(handle)));
    return summarize(newHandle(battle), battle);
  },

  // Legal random choices for whichever sides currently have a request, using
  // Showdown's own RandomPlayerAI rather than a reimplementation of legality
  // (Choice locks, Encore, disabled moves, target legality, forced switches).
  randomChoice({ handle, seed }) {
    const battle = getBattle(handle);
    const choices = {};
    for (const [index, side] of ["p1", "p2"].entries()) {
      const request = battle.sides[index].activeRequest;
      if (!request || request.wait) continue;

      let captured = null;
      const sink = {
        write(choice) {
          captured = choice;
        },
      };
      const ai = new RandomPlayerAI(sink, {
        seed: seed ? [seed[0], seed[1], seed[2], seed[3] + index] : undefined,
      });
      ai.receiveRequest(request);
      if (captured !== null) choices[side] = captured;
    }
    return { choices };
  },

  request({ handle }) {
    const battle = getBattle(handle);
    return {
      p1: battle.sides[0].activeRequest ?? null,
      p2: battle.sides[1].activeRequest ?? null,
    };
  },

  destroy({ handle }) {
    const battle = battles.get(handle);
    if (battle) {
      battle.destroy();
      battles.delete(handle);
    }
    return { ok: true };
  },

  count() {
    return { open: battles.size };
  },
};

function handleLine(line) {
  if (!line.trim()) return;

  let request;
  try {
    request = JSON.parse(line);
  } catch (err) {
    respond({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
    return;
  }

  const { id = null, method, params = {} } = request;
  const fn = methods[method];
  if (!fn) {
    respond({
      jsonrpc: "2.0",
      id,
      error: { code: -32601, message: `Method not found: ${method}` },
    });
    return;
  }

  try {
    respond({ jsonrpc: "2.0", id, result: fn(params) });
  } catch (err) {
    respond({
      jsonrpc: "2.0",
      id,
      error: { code: -32000, message: err.message, data: err.stack },
    });
  }
}

function respond(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

readline
  .createInterface({ input: process.stdin, terminal: false })
  .on("line", handleLine);

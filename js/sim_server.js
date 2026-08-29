#!/usr/bin/env node
"use strict";

// JSON-RPC 2.0 over stdio, one request per line, exposing the vendored Showdown
// simulator as the rollout and differential oracle.
//
// Methods:
//   create      {formatId, seed?, p1: {name, team}, p2: {name, team}} -> {handle, ...state}
//   step        {handle, choices: {p1, p2}}                           -> {...state}
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

const path = require("path");
const readline = require("readline");

const SHOWDOWN = path.join(__dirname, "..", "vendor", "showdown", "dist", "sim");
const { Battle } = require(path.join(SHOWDOWN, "battle"));
const { State } = require(path.join(SHOWDOWN, "state"));
const { Teams } = require(path.join(SHOWDOWN, "teams"));

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

  create({ formatId, seed, p1, p2, strictChoices = false }) {
    const battle = new Battle({
      formatid: formatId,
      seed: seed ?? undefined,
      strictChoices,
    });
    battle.setPlayer("p1", { name: p1.name ?? "p1", team: packTeam(p1.team) });
    battle.setPlayer("p2", { name: p2.name ?? "p2", team: packTeam(p2.team) });
    return summarize(newHandle(battle), battle);
  },

  step({ handle, choices }) {
    const battle = getBattle(handle);
    for (const side of ["p1", "p2"]) {
      const choice = choices?.[side];
      if (choice !== undefined && choice !== null) {
        battle.choose(side, choice);
      }
    }
    return summarize(handle, battle);
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

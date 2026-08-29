#!/usr/bin/env node
"use strict";

// Dumps the resolved dex for a Showdown format ID, post mod resolution:
// legal species (with base stats and abilities), their learnsets, non-nonstandard
// items (including the megaStone map), non-nonstandard moves, and non-nonstandard
// abilities. Prints one JSON object to stdout.
//
// Usage: node js/dump_dex.js <formatId>
//        node js/dump_dex.js --mod <modId>
//
// The --mod form dumps every existing move, item, and ability for a mod with no
// isNonstandard filtering and no format/ruleset (so no species legality concept
// applies). It exists to compare mods field by field, e.g. champions vs gen9, for
// the mainline delta report — the ordinary format dump is filtered to the legal
// and non-nonstandard pool relevant to actually playing the format.

const path = require("path");
const { Dex } = require(path.join(__dirname, "..", "vendor", "showdown", "dist", "sim", "dex"));

function toPlainObject(entry) {
  // Effect objects carry methods (onModifyMove, etc.) that JSON.stringify already
  // drops; round-tripping through JSON here keeps the two dumps (mod vs mainline)
  // structurally identical for diffing.
  return JSON.parse(JSON.stringify(entry));
}

function dumpDex(formatId) {
  const dex = Dex.forFormat(formatId);
  const format = dex.formats.get(formatId);
  if (!format.exists) {
    throw new Error(`Unknown format: ${formatId}`);
  }
  const ruleTable = dex.formats.getRuleTable(format);

  const legalSpecies = dex.species
    .all()
    .filter((s) => s.exists && !ruleTable.isBannedSpecies(s));

  const species = {};
  const learnsets = {};
  for (const s of legalSpecies) {
    species[s.id] = toPlainObject(s);
    learnsets[s.id] = toPlainObject(dex.species.getLearnsetData(s.id));
  }

  const items = {};
  for (const item of dex.items.all()) {
    if (item.exists && !item.isNonstandard) {
      items[item.id] = toPlainObject(item);
    }
  }

  const moves = {};
  for (const move of dex.moves.all()) {
    if (move.exists && !move.isNonstandard) {
      moves[move.id] = toPlainObject(move);
    }
  }

  const abilities = {};
  for (const ability of dex.abilities.all()) {
    if (ability.exists && !ability.isNonstandard) {
      abilities[ability.id] = toPlainObject(ability);
    }
  }

  // Natures carry the stat multiplier in the Champions stat formula, so they are
  // a numeric input to the damage layer and are resolved from the simulator like
  // everything else rather than transcribed from memory. The champions mod does
  // not override them today; dumping them means that stops being an assumption.
  const natures = {};
  for (const nature of dex.natures.all()) {
    if (nature.exists) natures[nature.id] = toPlainObject(nature);
  }

  // The type chart drives the effectiveness multiplier in the damage layer, so
  // it is dumped for the same reason as natures: a mod may change it, and this
  // makes that a diff rather than a surprise.
  const types = {};
  for (const type of dex.types.all()) {
    if (type.exists) types[type.id] = toPlainObject(type);
  }

  // The resolved rule table's numbers, not its rule names. `pickedTeamSize` is
  // the one everything downstream needs: the evaluation function cannot count
  // the opponent's surviving Pokemon without it, because only the revealed ones
  // are visible and the rest are alive by default. Regulations change these, so
  // reading them beats hardcoding 4 (CLAUDE.md: nothing hardcodes the pool).
  const rules = {
    gameType: format.gameType ?? null,
    pickedTeamSize: ruleTable.pickedTeamSize ?? null,
    maxTeamSize: ruleTable.maxTeamSize ?? null,
    minTeamSize: ruleTable.minTeamSize ?? null,
    adjustLevel: ruleTable.adjustLevel ?? null,
    teamPreview: ruleTable.has("teampreview"),
    names: [...ruleTable.keys()].filter((k) => !k.startsWith("-") && !k.startsWith("+")).sort(),
  };

  return {
    formatId,
    mod: format.mod,
    gen: dex.gen,
    counts: {
      species: Object.keys(species).length,
      items: Object.keys(items).length,
      moves: Object.keys(moves).length,
      abilities: Object.keys(abilities).length,
      natures: Object.keys(natures).length,
      types: Object.keys(types).length,
    },
    species,
    learnsets,
    items,
    moves,
    abilities,
    natures,
    types,
    rules,
  };
}

function dumpMod(modId) {
  const dex = Dex.mod(modId);

  const moves = {};
  for (const move of dex.moves.all()) {
    if (move.exists) moves[move.id] = toPlainObject(move);
  }

  const items = {};
  for (const item of dex.items.all()) {
    if (item.exists) items[item.id] = toPlainObject(item);
  }

  const abilities = {};
  for (const ability of dex.abilities.all()) {
    if (ability.exists) abilities[ability.id] = toPlainObject(ability);
  }

  return {
    mod: modId,
    gen: dex.gen,
    counts: {
      moves: Object.keys(moves).length,
      items: Object.keys(items).length,
      abilities: Object.keys(abilities).length,
    },
    moves,
    items,
    abilities,
  };
}

function main() {
  if (process.argv[2] === "--mod") {
    const modId = process.argv[3];
    if (!modId) {
      process.stderr.write("Usage: node js/dump_dex.js --mod <modId>\n");
      process.exit(1);
    }
    process.stdout.write(JSON.stringify(dumpMod(modId)));
    return;
  }

  const formatId = process.argv[2];
  if (!formatId) {
    process.stderr.write("Usage: node js/dump_dex.js <formatId>\n");
    process.exit(1);
  }
  const dump = dumpDex(formatId);
  process.stdout.write(JSON.stringify(dump));
}

main();

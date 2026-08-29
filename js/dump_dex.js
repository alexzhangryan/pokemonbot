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

  return {
    formatId,
    mod: format.mod,
    gen: dex.gen,
    counts: {
      species: Object.keys(species).length,
      items: Object.keys(items).length,
      moves: Object.keys(moves).length,
      abilities: Object.keys(abilities).length,
    },
    species,
    learnsets,
    items,
    moves,
    abilities,
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

/* Run the viewer's client script against a trace file without a browser.
 *
 * `node tests/viewer_smoke.js path/to/trace.jsonl`
 *
 * The viewer has no build step and no test runner of its own, and the one
 * property docs/07-observability.md section 5 demands of it -- render any
 * trace without crashing -- is exactly the kind of thing a syntax check does
 * not catch. So this stands up the smallest DOM the script needs, loads it,
 * feeds it every event of the file, and selects every decision point in
 * turn. Any exception is a non-zero exit, which tests/test_viewer.py asserts
 * against. Nothing is rendered; what is checked is that every code path the
 * file reaches runs.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeElement(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    className: "",
    textContent: "",
    children: [],
    style: {},
    dataset: {},
    attributes: {},
    listeners: {},
    hidden: false,
    disabled: false,
    _value: "",
    classList: {
      add(...names) {
        for (const name of names) if (!node.className.split(" ").includes(name)) node.className += ` ${name}`;
      },
      remove(...names) {
        node.className = node.className
          .split(" ")
          .filter((n) => n && !names.includes(n))
          .join(" ");
      },
      toggle(name, force) {
        const has = node.className.split(" ").includes(name);
        const want = force === undefined ? !has : Boolean(force);
        if (want && !has) node.classList.add(name);
        if (!want && has) node.classList.remove(name);
      },
      contains(name) {
        return node.className.split(" ").includes(name);
      },
    },
    appendChild(child) {
      node.children.push(child);
      return child;
    },
    append(...items) {
      for (const item of items) node.children.push(typeof item === "string" ? { text: item } : item);
    },
    replaceChildren(...items) {
      node.children = items;
    },
    setAttribute(key, value) {
      node.attributes[key] = String(value);
    },
    getAttribute(key) {
      return node.attributes[key];
    },
    addEventListener(type, fn) {
      (node.listeners[type] = node.listeners[type] || []).push(fn);
    },
    querySelector() {
      return null;
    },
    scrollIntoView() {},
    get childElementCount() {
      return node.children.length;
    },
    get value() {
      return node._value;
    },
    set value(v) {
      node._value = v;
    },
  };
  return node;
}

const byId = new Map();
const document = {
  createElement: (tag) => makeElement(tag),
  createElementNS: (_ns, tag) => makeElement(tag),
  createTextNode: (text) => ({ text: String(text) }),
  getElementById(id) {
    if (!byId.has(id)) byId.set(id, makeElement("div"));
    return byId.get(id);
  },
  addEventListener() {},
};

class FakeWebSocket {
  constructor() {
    this.onmessage = null;
    this.onclose = null;
  }
  close() {}
}

const sandbox = {
  document,
  window: { addEventListener() {}, confirm: () => false, open() {}, screenX: 0, screenY: 0, outerWidth: 1000, outerHeight: 800 },
  location: { protocol: "http:", host: "localhost", href: "http://localhost/" },
  history: { replaceState() {} },
  WebSocket: FakeWebSocket,
  fetch: async () => {
    throw new Error("no network in the smoke test");
  },
  localStorage: { getItem: () => null, setItem() {} },
  screen: { availWidth: 1920 },
  setInterval: () => 0,
  HTMLSelectElement: class {},
  URL,
  Number,
  String,
  Math,
  JSON,
  Array,
  Object,
  Map,
  Set,
  Boolean,
  Error,
  console,
};
sandbox.window.document = document;
sandbox.window.location = sandbox.location;
sandbox.globalThis = sandbox;

const tracePath = process.argv[2];
if (!tracePath) {
  console.error("usage: node tests/viewer_smoke.js trace.jsonl");
  process.exit(2);
}

const appPath = path.join(__dirname, "..", "champions", "viewer", "static", "app.js");
const context = vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(appPath, "utf8"), context, { filename: "app.js" });

const events = fs
  .readFileSync(tracePath, "utf8")
  .split("\n")
  .filter((line) => line.trim())
  .map((line) => JSON.parse(line));
context.__events = events;

/* Feed the whole file, then scrub every point; a live file arrives in
 * batches, so also feed it one event at a time, which is where a
 * half-built decision point has to render without its later events. */
vm.runInContext(
  `
  events = __events;
  refresh();
  for (const point of points) { following = false; select(point); }
  events = [];
  for (const one of __events) { events = events.concat([one]); refresh(); }
  __result = { points: points.length, reviewed: points.filter((p) => p.analysis).length, game: Boolean(gameAnalysis) };
  `,
  context,
  { filename: "smoke.js" }
);

console.log(JSON.stringify(context.__result));

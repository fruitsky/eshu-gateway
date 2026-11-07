/* Loads the dashboard's real app.js in a stubbed browser context and renders
   the F2 breakdown helpers, so the tests exercise the shipped renderer (and
   its escaping) rather than a copy. Prints one JSON object on stdout.

   Usage: node tests/fixtures/f2_harness.js '<json>'
   Input:  {"row": <request row with breakdown>}
   Output: {"headline", "body", "flags", "steps", "escapeCalls"} or {"error"}. */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

process.on('unhandledRejection', function () {});

const ROOT = path.resolve(__dirname, '..', '..', 'dashboard', 'static');

function noop() {}
function makeProxy(name) {
  const fn = function () { return makeProxy(name + '()'); };
  return new Proxy(fn, {
    get: function (t, prop) {
      if (prop === 'then') return undefined;
      if (prop === Symbol.toPrimitive) return function () { return ''; };
      if (prop === 'length') return 0;
      return makeProxy(name + '.' + String(prop));
    },
    set: function () { return true; },
    apply: function () { return makeProxy(name + '()'); },
    has: function () { return true; },
  });
}
function Ctor(name) { return function () { return makeProxy(name); }; }

const stubResponse = { ok: true, status: 200, json: function () { return Promise.resolve({}); }, text: function () { return Promise.resolve(''); } };

const ctx = {
  console: console, Math: Math, JSON: JSON, Date: Date, Set: Set, Map: Map,
  Array: Array, Object: Object, String: String, Number: Number, Boolean: Boolean,
  RegExp: RegExp, Error: Error, Symbol: Symbol, Intl: Intl,
  parseInt: parseInt, parseFloat: parseFloat, isNaN: isNaN, isFinite: isFinite,
  encodeURIComponent: encodeURIComponent, decodeURIComponent: decodeURIComponent,
  Promise: Promise,
  setTimeout: noop, setInterval: noop, clearTimeout: noop, clearInterval: noop,
  requestAnimationFrame: noop, cancelAnimationFrame: noop,
  fetch: function () { return Promise.resolve(stubResponse); },
  MutationObserver: Ctor('MutationObserver'),
  IntersectionObserver: Ctor('IntersectionObserver'),
  ResizeObserver: Ctor('ResizeObserver'),
  Audio: Ctor('Audio'), Image: Ctor('Image'),
  WebSocket: Ctor('WebSocket'), EventSource: Ctor('EventSource'),
  XMLHttpRequest: Ctor('XMLHttpRequest'),
  Notification: makeProxy('Notification'),
  AudioContext: Ctor('AudioContext'),
  speechSynthesis: makeProxy('speechSynthesis'),
  SpeechSynthesisUtterance: Ctor('SpeechSynthesisUtterance'),
  Blob: Ctor('Blob'),
  URL: { createObjectURL: function () { return 'blob:x'; }, revokeObjectURL: noop },
  ClipboardItem: Ctor('ClipboardItem'),
  localStorage: makeProxy('localStorage'),
  sessionStorage: makeProxy('sessionStorage'),
  navigator: makeProxy('navigator'),
  window: makeProxy('window'),
  document: makeProxy('document'),
  location: makeProxy('location'),
  Headers: function () { this.has = function () { return false; }; this.set = function () {}; },
};
ctx.window.fetch = ctx.fetch;
ctx.globalThis = ctx;
vm.createContext(ctx);

function load(file) {
  vm.runInContext(fs.readFileSync(path.join(ROOT, file), 'utf8'), ctx, { filename: file });
}
load('state.js');
load('app.js');

const input = JSON.parse(process.argv[2] || '{}');
const row = input.row;
const out = {};

if (typeof ctx.bdHeadlineHtml !== 'function' || typeof ctx.bdBodyHtml !== 'function') {
  process.stdout.write(JSON.stringify({ error: 'breakdown renderer missing from app.js' }));
  process.exit(0);
}

out.headline = ctx.bdHeadlineHtml(row);
out.body = ctx.bdBodyHtml(row);
out.flags = typeof ctx.bdFlagsHtml === 'function' ? ctx.bdFlagsHtml(row) : '';
out.steps = typeof ctx.bdStepsHtml === 'function' ? ctx.bdStepsHtml(row) : '';

/* Re-render with an escapeHtml spy: every agent-controlled string must pass
   through it. The spy delegates, so the output is identical. */
const original = ctx.escapeHtml;
const seen = [];
ctx.escapeHtml = function (value) {
  seen.push(String(value == null ? '' : value));
  return original(value);
};
out.spiedHeadline = ctx.bdHeadlineHtml(row);
out.spiedBody = ctx.bdBodyHtml(row);
out.escapeCalls = seen;
ctx.escapeHtml = original;

process.stdout.write(JSON.stringify(out));

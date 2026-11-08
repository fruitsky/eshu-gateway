/* Loads the dashboard's real app.js in a stubbed browser context and drives the
   F2 review-card helpers, so the tests exercise the shipped renderer (and its
   escaping) and the approve paths rather than a copy. Prints one JSON object.

   Usage: node tests/fixtures/f2_harness.js '<json>'
   Input:  {"action": "render"|"bulk"|"session-override"|"card"|"decoded", ...}
   Output: {"error"} or the fields for that action. */
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

const stubResponse = {
  ok: true, status: 200,
  json: function () { return Promise.resolve({}); },
  text: function () { return Promise.resolve(''); },
};

const ctx = {
  console: console, Math: Math, JSON: JSON, Date: Date, Set: Set, Map: Map,
  Array: Array, Object: Object, String: String, Number: Number, Boolean: Boolean,
  RegExp: RegExp, Error: Error, Symbol: Symbol, Intl: Intl,
  parseInt: parseInt, parseFloat: parseFloat, isNaN: isNaN, isFinite: isFinite,
  encodeURIComponent: encodeURIComponent, decodeURIComponent: decodeURIComponent,
  Promise: Promise, setTimeout: setTimeout, clearTimeout: clearTimeout,
  setInterval: noop, clearInterval: noop,
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
const flush = function () { return new Promise(function (resolve) { setTimeout(resolve, 25); }); };

function setRequests(list) {
  vm.runInContext('requestsData = ' + JSON.stringify(list || []) + ';', ctx);
}

/* Spies: the approve paths must reach the server (or not) exactly once. */
const posts = [];
const toasts = [];
ctx.authFetch = function (url, init) {
  posts.push({ url: url, method: (init && init.method) || 'GET' });
  return Promise.resolve({
    ok: true, status: 200,
    json: function () { return Promise.resolve({ status: 'ok' }); },
  });
};
ctx.showToast = function (message, type) { toasts.push({ message: message, type: type || 'success' }); };
ctx.fetchRequests = function () { return Promise.resolve(); };
ctx.closePanelIfShowing = function () {};
ctx.refreshSessionOverrides = function () { return Promise.resolve(); };
ctx.playApprovalPulse = function () {};

function approvePosts() {
  return posts.filter(function (p) { return /\/api\/approve\//.test(p.url); })
              .map(function (p) { return p.url.split('/').pop(); });
}

(async function main() {
  const action = input.action || 'render';
  const out = {};

  if (action === 'render') {
    const row = input.row;
    if (typeof ctx.bdHeadlineHtml !== 'function' || typeof ctx.bdBodyHtml !== 'function') {
      process.stdout.write(JSON.stringify({ error: 'breakdown renderer missing from app.js' }));
      return;
    }
    out.headline = ctx.bdHeadlineHtml(row);
    out.body = ctx.bdBodyHtml(row);
    out.flags = typeof ctx.bdFlagsHtml === 'function' ? ctx.bdFlagsHtml(row) : '';
    out.steps = typeof ctx.bdStepsHtml === 'function' ? ctx.bdStepsHtml(row) : '';
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
  } else if (action === 'decoded') {
    const row = input.row;
    if (typeof ctx.bdDecodedPreviewHtml !== 'function') {
      process.stdout.write(JSON.stringify({ error: 'bdDecodedPreviewHtml missing from app.js' }));
      return;
    }
    out.preview = ctx.bdDecodedPreviewHtml(row);
  } else if (action === 'bulk') {
    setRequests(input.requests);
    ctx._ccSelectedIds = new Set(input.selected || []);
    ctx.bulkApprove();
    await flush();
    out.approved = approvePosts();
    out.selected = Array.from(ctx._ccSelectedIds);
    out.toasts = toasts;
  } else if (action === 'session-override') {
    setRequests(input.requests);
    ctx._sessionOverrideCtx = {
      session_id: 'session-1', host: '10.0.0.1', hostname: 'web01',
      request_id: Number(input.requestId),
    };
    await ctx.confirmSessionOverride();
    await flush();
    out.approved = approvePosts();
    out.toasts = toasts;
  } else if (action === 'card') {
    setRequests(input.requests);
    const id = Number(input.requestId);
    await ctx.handleAction(id, 'approve');
    await flush();
    out.afterFirst = approvePosts();
    await ctx.handleAction(id, 'approve');
    await flush();
    out.approved = approvePosts();
    out.toasts = toasts;
  } else {
    out.error = 'unknown action ' + action;
  }

  process.stdout.write(JSON.stringify(out));
})().catch(function (err) {
  process.stdout.write(JSON.stringify({ error: String(err && err.message || err) }));
});

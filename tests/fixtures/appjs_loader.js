/* Loads dashboard/static/{state,app}.js in a stubbed browser context.

   Shared by the UI test harnesses (F2 review card, F7 inner target) so they
   exercise the shipped code. Returns the VM context; the caller drives the
   functions it needs. */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

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

function load() {
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
  for (const file of ['state.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(ROOT, file), 'utf8'), ctx, { filename: file });
  }
  return ctx;
}

module.exports = { load, makeProxy, ROOT };

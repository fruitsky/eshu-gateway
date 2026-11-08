/* Loads the dashboard's real app.js in a stubbed browser context and drives the
   F2 review-card helpers, so the tests exercise the shipped renderer (and its
   escaping) and the approve paths rather than a copy. Prints one JSON object.

   Usage: node tests/fixtures/f2_harness.js '<json>'
   Input:  {"action": "render"|"bulk"|"session-override"|"card"|"decoded", ...}
   Output: {"error"} or the fields for that action. */
'use strict';
const vm = require('vm');
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const ctx = load();

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

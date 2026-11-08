/* X1: sidebar action dispatch and the two-press rule, from the real app.js. */
'use strict';
const vm = require('vm');
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const out = {};

if (typeof ctx.contextActionsHtml !== 'function') {
  process.stdout.write(JSON.stringify({ error: 'contextActionsHtml missing from app.js' }));
  process.exit(0);
}

function longUnknown(id) {
  return {
    id: id, status: 'pending', command: 'x'.repeat(600), target_ip: '192.0.2.10',
    breakdown: {
      version: 1,
      steps: [{ text: 'x', kind: 'unknown', why: '', section: '' }],
      sections: [], summary: { steps: 1, read: 0, write: 0, unknown: 1 },
      headline: '1 step · 1 unknown', inner_targets: [], flags: [], complete: true,
    },
  };
}

function posts() {
  const seen = [];
  ctx.authFetch = function (url, init) {
    seen.push({ url: String(url), method: (init && init.method) || 'GET' });
    return Promise.resolve({
      ok: true, status: 200,
      json: function () { return Promise.resolve({ status: 'ok' }); },
    });
  };
  ctx.showToast = function () {};
  ctx.fetchRequests = function () { return Promise.resolve(); };
  ctx.closePanelIfShowing = function () {};
  ctx.playApprovalPulse = function () {};
  return seen;
}

(async () => {
  if (input.action === 'panel') {
    out.integration = ctx.contextActionsHtml({
      id: 3, status: 'pending', _type: 'integration',
      integration: 'omada', tool: 'list_sites',
    });
    out.window = ctx.contextActionsHtml({
      id: 4, status: 'pending', _type: 'window', command: 'uptime',
    });
    out.ssh = ctx.contextActionsHtml({ id: 5, status: 'pending', command: 'uptime' });
    out.closed = ctx.contextActionsHtml({ id: 6, status: 'denied', command: 'uptime' });
  } else if (input.action === 'two-press') {
    const req = longUnknown(7);
    vm.runInContext('requestsData = ' + JSON.stringify([req]) + ';', ctx);
    const seen = posts();
    const approvePosts = function () {
      return seen.filter(function (post) { return /\/api\/approve\//.test(post.url); });
    };
    await ctx.handleAction(7, 'approve');
    out.firstPosts = approvePosts().length;
    out.labelAfterFirst = ctx.contextApproveLabel(req);
    await ctx.handleAction(7, 'approve');
    out.secondPosts = approvePosts().length;
    out.secondUrl = approvePosts().length
      ? approvePosts()[approvePosts().length - 1].url : '';
  } else {
    out.error = 'unknown action ' + input.action;
  }
  process.stdout.write(JSON.stringify(out));
})().catch(function (e) {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
});

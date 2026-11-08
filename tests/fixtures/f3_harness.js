/* Drives the real F3 plan-card helpers from dashboard/static/app.js.

   Usage: node tests/fixtures/f3_harness.js '<json>'
   Input:  {"action": "plan-ttl"|"plan-replaced"|"plan-s4"|"plan-authfetch"}
   Output: {"error"} or the fields for that action. */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const out = {};

if (typeof ctx.planCardHtml !== 'function') {
  process.stdout.write(JSON.stringify({ error: 'planCardHtml missing from app.js' }));
  process.exit(0);
}

function bd(overrides) {
  return Object.assign({
    version: 1,
    steps: [{ text: 'uptime', op: 'uptime', kind: 'read', why: '', section: 'System', context: [] }],
    sections: ['System'],
    summary: { steps: 1, read: 1, write: 0, unknown: 0 },
    headline: '1 step · 1 read · 0 write',
    inner_targets: [],
    flags: [],
    complete: true,
  }, overrides || {});
}

function item(overrides) {
  return Object.assign({
    id: 1, host: '*', command: 'uptime', max_uses: 1, uses: 0, removed: 0,
    breakdown: bd(),
  }, overrides || {});
}

function plan(overrides) {
  return Object.assign({
    id: 1, session_id: 'sess-x', agent: 'hermes',
    session_goal: 'Add the app vhost and reload nginx',
    note: '', hosts: 1, kept: 1, until: 0, status: 'proposed',
    items: [item()],
  }, overrides || {});
}

function stubDecide() {
  const posts = [];
  ctx.authFetch = function (url, init) {
    posts.push({ url: url, body: (init && init.body) || '' });
    return Promise.resolve({
      ok: true, status: 200,
      json: function () { return Promise.resolve({ status: 'approved' }); },
    });
  };
  ctx.showToast = function () {};
  ctx.fetchSessionPlans = function () { return Promise.resolve(); };
  ctx.fetchRequests = function () { return Promise.resolve(); };
  ctx._activeSessionSid = '';
  return posts;
}

(async () => {
  if (input.action === 'plan-ttl') {
    const p = plan({ id: 7 });
    ctx.setPlanTtl({ value: String(input.ttl || 30), getAttribute: function () { return '7'; } });
    out.rendered = ctx.planCardHtml(p);
    out.again = ctx.planCardHtml(p);  // a later poll re-renders from the same state
    out.ttl = ctx.planTtlFor(p);
  } else if (input.action === 'plan-replaced' || input.action === 'plan-s4') {
    let p;
    if (input.action === 'plan-s4') {
      p = plan({
        id: 8,
        items: [item({
          id: 9,
          command: 'x'.repeat(600),
          breakdown: bd({ summary: { steps: 1, read: 0, write: 0, unknown: 1 },
                          headline: '1 step · 1 unknown', steps: [{ text: 'x', kind: 'unknown', why: '', section: '' }] }),
        })],
      });
    } else {
      p = plan({ id: 6 });
    }
    // For the replacement case, simulate the operator looking at an older
    // proposal (id 5) for the session. The S4 case starts from a clean state,
    // so its second press comes only from the long unclassified step.
    ctx._planDisplayed = {};
    if (input.action === 'plan-replaced') ctx._planDisplayed['sess-x'] = 5;
    ctx._sessionPlans = [p];
    ctx.renderPlans();
    out.replaced = !!ctx._planReplaced[String(p.id)];
    out.needsConfirm = ctx.planNeedsConfirm(p);
    out.card = ctx.planCardHtml(p);
    const posts = stubDecide();
    const planPosts = function () {
      return posts.filter(function (post) {
        return /\/api\/session-plans\//.test(post.url);
      });
    };
    await ctx.decidePlan(p.id, 'approve');
    out.firstPressPosts = planPosts().length;
    out.afterFirstPress = ctx.planCardHtml(p);
    await ctx.decidePlan(p.id, 'approve');
    out.secondPressPosts = planPosts().length;
    out.secondBody = planPosts().length ? planPosts()[planPosts().length - 1].body : '';
  } else if (input.action === 'plan-authfetch') {
    const calls = { auth: [], plain: [] };
    ctx.authFetch = function (url) {
      calls.auth.push(String(url));
      return Promise.resolve({ ok: true, json: function () { return Promise.resolve([]); } });
    };
    ctx.fetch = function (url) {
      calls.plain.push(String(url));
      return Promise.resolve({ ok: true, json: function () { return Promise.resolve([]); } });
    };
    await ctx.fetchSessionPlans();
    out.calls = calls;
  } else {
    out.error = 'unknown action ' + input.action;
  }
  process.stdout.write(JSON.stringify(out));
})().catch(function (e) {
  process.stdout.write(JSON.stringify({ error: String(e && e.stack || e) }));
});

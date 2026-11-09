/* F6: the session wrap-up card and the "how approved" stats widget. */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const payload = String(input.payload || '');
const out = {};

if (typeof ctx.sessionSummaryHtml !== 'function'
    || typeof ctx.approvalsHtml !== 'function') {
  process.stdout.write(JSON.stringify(
    { error: 'sessionSummaryHtml/approvalsHtml missing from app.js' }));
  process.exit(0);
}

const writes = {};
writes[payload] = 2;

out.summary = ctx.sessionSummaryHtml({
  commands: 3, duration_seconds: 3725, waited_seconds: 75, decided: 2,
  failed: 1, goal: payload,
  by_approval: {
    'allowlist': 1, 'human': 1, 'plan': 0, 'read-lane': 0,
    'session-override': 1, 'host-override': 0, 'window': 0, 'fleet': 0,
  },
  writes_by_host: writes,
});
out.summaryNoCommands = ctx.sessionSummaryHtml({ commands: 0 });
out.summaryNoWait = ctx.sessionSummaryHtml({
  commands: 1, decided: 0, waited_seconds: 0,
  by_approval: { 'human': 1 }, writes_by_host: {},
});
out.approvals = ctx.approvalsHtml({
  buckets: [
    { key: 'allowlist', label: 'allowlist' },
    { key: 'human', label: 'human' },
    { key: 'session-override', label: 'session override' },
    { key: 'plan', label: 'plan' },
  ],
  series: [
    { date: payload, 'allowlist': 1, 'human': 2, 'session-override': 0, 'plan': 0 },
    { date: '2026-11-08', 'allowlist': 0, 'human': 0, 'session-override': 1, 'plan': 0 },
  ],
  totals: { 'allowlist': 1, 'human': 2, 'session-override': 1, 'plan': 0 },
  approved: 4,
  override_share: 25,
});
out.approvalsEmpty = ctx.approvalsHtml({});
out.durations = [
  ctx.fmtDuration(0), ctx.fmtDuration(45), ctx.fmtDuration(125),
  ctx.fmtDuration(3725), ctx.fmtDuration(-5),
];
process.stdout.write(JSON.stringify(out));

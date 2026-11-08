/* F18: escaping of agent text in every sidebar section. */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const payload = String(input.payload || '');
const out = {};

if (typeof ctx.ctxWaitingBody !== 'function') {
  process.stdout.write(JSON.stringify({ error: 'ctxWaitingBody missing from app.js' }));
  process.exit(0);
}

out.waiting = ctx.ctxWaitingBody({ waiting: payload });
out.session = ctx.ctxSessionBody({
  session: {
    goal: payload, agent: payload,
    commands: [{ id: 1, command: payload, status: payload, exit_code: 1 }],
    plan: { id: 2, until: 1700000000, items: 1 }, override: { until: 1700000000 },
  },
});
out.seen = ctx.ctxSeenBody({
  seen: { count: 2, approved: 1, denied: 0, last_at: 0, last_exit_code: null },
  request_command: payload,
});
out.like = ctx.ctxLikeBody({
  like: { count: 1, approved: 1, denied: 0,
          examples: [{ id: 3, command: payload, hostname: payload, created_at: 0 }] },
});
out.host = ctx.ctxHostBody({
  host: { hostname: payload, inner_target: payload, zero_trust: true,
          read_lane: payload, failed_24h: 0, last_seen: 0 },
});
process.stdout.write(JSON.stringify(out));

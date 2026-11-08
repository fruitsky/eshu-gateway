/* Drives the real F5 helpers from dashboard/static/app.js. */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const out = {};

if (typeof ctx.execBadgeHtml !== 'function') {
  process.stdout.write(JSON.stringify({ error: 'execBadgeHtml missing from app.js' }));
  process.exit(0);
}

if (input.action === 'badge') {
  out.html = ctx.execBadgeHtml({
    id: 1,
    exit_code: input.exit_code === undefined ? 0 : input.exit_code,
    duration_ms: input.duration_ms || 0,
  });
} else if (input.action === 'output') {
  out.html = ctx.execOutputHtml({ output: input.output || '', truncated: !!input.truncated });
} else {
  out.error = 'unknown action ' + input.action;
}
process.stdout.write(JSON.stringify(out));

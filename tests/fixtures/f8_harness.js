/* F8: the test-traffic suggestion and the excluded marker. */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const ctx = load();
const out = {};

if (typeof ctx.looksLikeTestGoal !== 'function'
    || typeof ctx.excludedMarkHtml !== 'function') {
  process.stdout.write(JSON.stringify(
    { error: 'looksLikeTestGoal/excludedMarkHtml missing from app.js' }));
  process.exit(0);
}

out.test = [
  ctx.looksLikeTestGoal('Smoke test the edge'),
  ctx.looksLikeTestGoal('deploy nginx'),
  ctx.looksLikeTestGoal(''),
];
out.excludedChip = ctx.excludedMarkHtml({ id: 5, excluded: 1 });
out.excludeLink = ctx.excludedMarkHtml({ id: 5 });
process.stdout.write(JSON.stringify(out));

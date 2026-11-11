/* X2: the runbooks page renders agent-authored text escaped.
   Loads the shipped app.js in the stubbed browser context and returns the
   HTML for the list and the detail with a hostile payload in every field. */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const payload = String(input.payload || '');
const out = {};

if (typeof ctx.runbookListHtml !== 'function') {
  process.stdout.write(JSON.stringify({ error: 'runbookListHtml missing from app.js' }));
  process.exit(0);
}
if (typeof ctx.runbookDetailHtml !== 'function') {
  process.stdout.write(JSON.stringify({ error: 'runbookDetailHtml missing from app.js' }));
  process.exit(0);
}

const row = {
  id: 7, title: payload, when_to_use: payload, tags: [payload, 'nginx'],
  step_count: 2, session_id: payload, created_at: 1700000000, retired: false,
};
out.list = ctx.runbookListHtml([row]);
out.listRetired = ctx.runbookListHtml([
  Object.assign({}, row, { retired: true, title: payload + ' retired' })]);
out.empty = ctx.runbookListHtml([]);

const breakdown = {
  version: 1,
  steps: [{ kind: 'write', text: payload, op: '', section: '', context: [payload], why: payload }],
  sections: [], summary: { steps: 1, read: 0, write: 1, unknown: 0 },
  headline: payload, inner_targets: [], flags: [], complete: true,
};
const detail = {
  id: 7, title: payload, when_to_use: payload, tags: [payload],
  steps: [
    { kind: 'command', command: payload, host: payload, note: payload,
      ref: 'req:1', breakdown: breakdown },
    { kind: 'tool', integration: payload, tool: payload, method: payload,
      path: payload, note: payload, ref: 'call:2' },
    { kind: 'instruction', text: payload },
  ],
  dead_ends: [{ ref: payload, note: payload }],
  session_id: payload, conclusion_id: 3, created_at: 1700000000, retired: true,
};
out.detail = ctx.runbookDetailHtml(detail);
process.stdout.write(JSON.stringify(out));

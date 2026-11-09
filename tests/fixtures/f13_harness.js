/* F13: escaping and states in the session conclusion card. */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const payload = String(input.payload || '');
const out = {};

if (typeof ctx.conclusionHtml !== 'function') {
  process.stdout.write(JSON.stringify(
    { error: 'conclusionHtml missing from app.js' }));
  process.exit(0);
}

const base = {
  id: 5, outcome: 'solved', summary: payload,
  worked: [{ ref: 'req:12', note: payload }],
  did_not_work: [{ note: payload }],
  root_cause: payload, fix: payload, follow_ups: [payload],
  tags: [payload, 'nginx'], runbook_candidate: true, operator_note: payload,
};

out.draft = ctx.conclusionHtml(Object.assign({}, base, { status: 'draft' }), null);
out.confirmed = ctx.conclusionHtml(
  Object.assign({}, base, { status: 'confirmed' }), null);
out.withRunbook = ctx.conclusionHtml(
  Object.assign({}, base, { status: 'confirmed' }), 7);
out.rejected = ctx.conclusionHtml(
  Object.assign({}, base, { status: 'rejected' }), null);
process.stdout.write(JSON.stringify(out));

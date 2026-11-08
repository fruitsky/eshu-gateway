/* Renders the F7 inner-target helpers from the real app.js.

   Usage: node tests/fixtures/f7_harness.js '<json>'
   Input:  {"action": "chip", "value": "pct:103"}
           {"action": "override", "data": {"pct": [...], "qm": [...], ...}}
   Output: {"chip": "<html>"} / {"warning": "<html>", "escapeCalls": [...]} */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const out = {};

if (typeof ctx.innerTargetChipHtml !== 'function') {
  process.stdout.write(JSON.stringify({ error: 'innerTargetChipHtml missing from app.js' }));
  process.exit(0);
}

const original = ctx.escapeHtml;
const seen = [];
ctx.escapeHtml = function (value) {
  seen.push(String(value == null ? '' : value));
  return original(value);
};

if (input.action === 'chip') {
  out.chip = ctx.innerTargetChipHtml(input.value);
} else if (input.action === 'override') {
  if (typeof ctx.overrideInnerWarningHtml !== 'function') {
    process.stdout.write(JSON.stringify({ error: 'overrideInnerWarningHtml missing from app.js' }));
    process.exit(0);
  }
  out.warning = ctx.overrideInnerWarningHtml(input.data);
} else {
  out.error = 'unknown action ' + input.action;
}
out.escapeCalls = seen;
process.stdout.write(JSON.stringify(out));

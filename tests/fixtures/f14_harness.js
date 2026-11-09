/* F14: escaping and markers in the inventory list. */
'use strict';
const { load } = require('./appjs_loader');

process.on('unhandledRejection', function () {});

const input = JSON.parse(process.argv[2] || '{}');
const ctx = load();
const payload = String(input.payload || '');
const out = {};

if (typeof ctx.invEntityHtml !== 'function'
    || typeof ctx.invSourceChips !== 'function') {
  process.stdout.write(JSON.stringify(
    { error: 'invEntityHtml/invSourceChips missing from app.js' }));
  process.exit(0);
}

out.chips = ctx.invSourceChips([
  { source: payload, label: payload, stale: true },
]);
out.entity = ctx.invEntityHtml({
  name: payload,
  ips: [payload],
  fqdns: [payload],
  hosts: [payload],
  vmids: [payload],
  urls: [payload],
  sources: [{ source: 'npm', label: payload, stale: false }],
  conflicts: [{ ip: payload, names: [payload] }],
  extra: { mac: payload },
});
out.empty = ctx.invEntityHtml({ name: 'x' });
process.stdout.write(JSON.stringify(out));

/* Integration-call arguments on approval cards (formatCallArgs).
 *
 * Loads the shipped app.js through the shared stub-browser loader and prints
 * one JSON object on stdout. Exit 2: Node is present but the harness could
 * not load. All data is synthetic.
 */
'use strict';

const { load } = require('../fixtures/appjs_loader');

let ctx;
try {
  ctx = load();
} catch (e) {
  process.stderr.write('load-failed: ' + e.message + '\n');
  process.exit(2);
}

const f = ctx.formatCallArgs;
if (typeof f !== 'function') {
  process.stderr.write('missing helper: formatCallArgs\n');
  process.exit(1);
}

const out = {
  nested: f({ command: 'config/entity_registry/update',
              payload: { entity_id: 'switch.example_plug', labels: ['example_label'] } }),
  plain: f({ domain: 'light', service: 'turn_on' }),
  list_and_number: f({ ids: [1, 2], limit: 5, flag: true, none: null }),
  empty: f({}),
  missing: f(undefined),
};
process.stdout.write(JSON.stringify(out), () => process.exit(0));

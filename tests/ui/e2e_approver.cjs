/* E2E-1: the approver-token row must escape every operator/agent field. */
'use strict';
const { load } = require('../fixtures/appjs_loader');
const ctx = load();
if (typeof ctx.approverTokenRow !== 'function') {
  process.stderr.write('approverTokenRow missing\n');
  process.exit(2);
}
const html = ctx.approverTokenRow({
  id: 7,
  gateway_ip: '<img src=x onerror=alert(1)>',
  commands: ['hostname', '<script>alert(2)</script>'],
  expires_at: 1,
  revoked: false,
  expired: false,
});
process.stdout.write(html);
process.exit(0);

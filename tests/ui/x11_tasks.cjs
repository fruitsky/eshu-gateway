/* X11 Stage 5 browser tasks. Each task starts from the Agents page, uses
 * clicks only (no JSON, no API shortcuts for the task itself), reloads, and
 * asserts the saved state through the UI. Synthetic data only.
 */
'use strict';

const BASE = (process.env.ESHU_UI_URL || 'http://127.0.0.1:8790/').replace(/\/?$/, '/');
const PASSWORD = process.env.ESHU_UI_PASSWORD || '';

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (e) {
  process.stderr.write('playwright-not-available: ' + e.message.split('\n')[0] + '\n');
  process.exit(2);
}

const failures = [];
function ok(cond, msg) { if (!cond) failures.push(msg); }
async function box(loc) { return loc.boundingBox(); }
async function login(context) {
  const r = await context.request.post(BASE + 'api/auth/login', {
    data: { password: PASSWORD }, headers: { 'X-Eshu-CSRF': '1' } });
  if (!r.ok()) throw new Error('login failed: HTTP ' + r.status());
}
async function gotoAgents(page) {
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.evaluate(() => switchView('agents'));
  await page.waitForTimeout(1400);
}
async function openAgent(page, name) {
  const card = page.locator('[data-agent-card]', { hasText: name }).first();
  await card.click();
  await page.waitForTimeout(1100);
}
async function waitSaved(page) {
  await page.waitForFunction(() => {
    const n = document.querySelector('#x11-notice');
    return n && /^Saved:/.test(n.textContent || '');
  }, null, { timeout: 10000 });
}
async function noSideScroll(page, where) {
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  ok(widths[0] <= widths[1] + 1, where + ': horizontal scroll (' + widths[0] + ' > ' + widths[1] + ')');
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('dialog', (d) => d.accept());
  await login(page.context());

  const clickCounts = {};
  let clicks = 0;
  async function tap(loc) { clicks += 1; await loc.click(); }
  function startTask() { clicks = 0; }
  function finishTask(id) { clickCounts[id] = clicks; }

  // T1 — Create an agent, guided.
  startTask();
  await gotoAgents(page);
  const name = 'stage5-' + Date.now();
  await tap(page.locator('[data-new-agent]'));
  await page.locator('#x11-new-name').fill(name);
  await tap(page.locator('[data-template="household"]'));
  await tap(page.locator('#x11-new-next'));
  ok(await page.locator('[data-new-integrations]').count() === 1,
     'T1: integrations step is missing');
  await tap(page.locator('#x11-new-next'));
  await tap(page.locator('#x11-new-next'));
  ok(/can read, asks you before anything changes/.test(
       await page.locator('[data-new-review]').textContent()),
     'T1: review is not in plain words');
  await tap(page.locator('#x11-new-create'));
  await page.waitForSelector('[data-new-token]', { timeout: 15000 });
  ok(/^[a-f0-9]{64}$/.test((await page.locator('[data-new-token]').textContent()).trim()),
     'T1: the token was not shown once');
  ok(/MCP URL/.test(await page.locator('#x11-new-body').textContent()) &&
     /Authorization: Bearer/.test(await page.locator('#x11-new-body').textContent()),
     'T1: MCP URL and header are not shown');
  await tap(page.locator('button', { hasText: 'Done' }));
  await page.waitForTimeout(1200);
  await page.reload({ waitUntil: 'networkidle' });
  await page.evaluate(() => switchView('agents'));
  await page.waitForTimeout(1600);
  const newCard = page.locator('[data-agent-card]', { hasText: name }).first();
  ok(await newCard.count() === 1, 'T1: the new agent is missing after reload');
  ok(/Active/.test(await newCard.textContent()), 'T1: the status badge is missing');
  ok(!/Household/.test(await newCard.textContent()),
     'T1: the template badge is still on the card');
  finishTask('T1');

  // T2 — Run commands on machines.
  startTask();
  await gotoAgents(page);
  await openAgent(page, 'ops-agent'); clicks += 1;
  await tap(page.locator('[data-x11-nav="run"]'));
  const runText = await page.locator('#x11-sec-run').textContent();
  ok(/Pick the machines this agent may run commands on\./.test(runText),
     'T2: the Run commands sentence is missing');
  ok(!/only SSH setting/.test(runText),
     'T2: the Run commands section still talks about the tool key');
  await page.locator('[data-machine] [data-ssh-host="192.0.2.11"]').click(); clicks += 1;
  await tap(page.locator('[data-ssh-mode="normal"]'));
  ok(await page.locator('[data-unsaved="ssh"]').isVisible(),
     'T2: the Run commands title does not say Not saved');
  ok(/Run commands/.test(await page.locator('#x11-change-count').textContent()),
     'T2: the save bar does not name Run commands');
  await tap(page.locator('#x11-save-changes'));
  await page.waitForSelector('#x11-preview-apply', { timeout: 15000 });
  await tap(page.locator('#x11-preview-apply'));
  await waitSaved(page);
  ok(/commands/.test(await page.locator('#x11-notice').textContent()),
     'T2: the saved confirmation does not name what changed');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1400);
  ok(await page.evaluate(() => location.hash) === '#agent/1/run', 'T2: route did not persist');
  ok(await page.locator('[data-ssh-host="192.0.2.11"]').isChecked(),
     'T2: the machine is not saved');
  ok(await page.locator('[data-ssh-mode="normal"]').getAttribute('aria-pressed') === 'true',
     'T2: the mode is not saved');
  ok(await page.locator('[data-machine-list]').count() === 1 &&
     await page.locator('[data-ssh-mode]').count() === 3,
     'T2: one Run commands section is not rendered');
  finishTask('T2');

  // T3 — Use an integration; the two questions set the rows.
  startTask();
  await gotoAgents(page);
  await openAgent(page, 'ops-agent'); clicks += 1;
  await tap(page.locator('[data-x11-nav="tools"]'));
  await tap(page.locator('[data-integration-tile="Sonarr"]'));
  await page.waitForTimeout(600);
  ok(await page.locator('[data-int-questions]').count() === 1,
     'T3: the two questions are missing');
  await tap(page.locator('[data-allow-reading]'));
  await tap(page.locator('[data-changes-ask]'));
  await tap(page.locator('#x11-save-changes'));
  await page.waitForSelector('#x11-preview-apply', { timeout: 15000 });
  await tap(page.locator('#x11-preview-apply'));
  await waitSaved(page);
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1400);
  ok(await page.locator('[data-allow-reading]').getAttribute('aria-pressed') === 'true',
     'T3: Allow all reading is not saved');
  ok(await page.locator('[data-changes-ask]').getAttribute('aria-pressed') === 'true',
     'T3: Changes ask me first is not saved');
  finishTask('T3');

  // T4 — Let an agent switch a device on its own.
  startTask();
  await gotoAgents(page);
  await openAgent(page, 'ops-agent'); clicks += 1;
  await tap(page.locator('[data-x11-nav="tools"]'));
  await tap(page.locator('[data-integration-tile="Home Assistant"]'));
  await page.waitForTimeout(600);
  await tap(page.locator('[data-int-tab="devices"]'));
  const coffee = page.locator('[data-device="switch.coffee"][data-device-action="switch.turn_on"]');
  ok(await coffee.getAttribute('data-state') === 'ask', 'T4: the coffee pill does not start as ask');
  await tap(coffee);
  await tap(page.locator('#x11-save-changes'));
  await page.waitForSelector('#x11-preview-apply', { timeout: 15000 });
  await tap(page.locator('#x11-preview-apply'));
  await waitSaved(page);
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1400);
  ok(await page.locator('[data-device="switch.coffee"][data-device-action="switch.turn_on"]')
       .getAttribute('data-state') === 'run', 'T4: the device is not saved as run');
  finishTask('T4');

  // T5 — Home-wide rules inside Home Assistant.
  startTask();
  await gotoAgents(page);
  await openAgent(page, 'ops-agent'); clicks += 1;
  await tap(page.locator('[data-x11-nav="tools"]'));
  await tap(page.locator('[data-integration-tile="Home Assistant"]'));
  await page.waitForTimeout(600);
  await tap(page.locator('[data-int-tab="homewide"]'));
  ok(await page.locator('[data-homewide-banner]').textContent() === 'Applies to every agent.',
     'T5: the Home-wide banner is missing');
  await tap(page.locator('[data-protect-change]'));
  await page.locator('[data-protect="switch.kettle"]').check(); clicks += 1;
  await tap(page.locator('[data-save-protected]'));
  await page.waitForTimeout(1600);
  ok(/Kettle/.test(await page.locator('[data-protected-summary]').textContent()),
     'T5: the protected device is not saved');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  ok(await page.locator('[data-homewide-banner]').isVisible(),
     'T5: Home-wide did not survive reload');
  await page.evaluate(() => switchView('integrations'));
  await page.waitForTimeout(1000);
  ok(await page.locator('#integration-list button', { hasText: 'Home-wide rules' }).count() >= 1,
     'T5: Integrations → Home Assistant has no Home-wide rules link');
  finishTask('T5');

  // T6 — See what an agent did.
  startTask();
  await gotoAgents(page);
  ok(await page.locator('[data-agent-counters] [data-counter]').count() === 3,
     'T6: the three counters are missing');
  ok(await page.locator('#x11-root > .x11-tabs').count() === 0,
     'T6: the Agents page still has a top-level tab bar');
  await openAgent(page, 'ops-agent'); clicks += 1;
  await tap(page.locator('[data-x11-nav="activity"]'));
  ok(await page.locator('#x11-sec-activity').count() === 1,
     'T6: Activity is missing');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1300);
  ok(/activity$/.test(await page.evaluate(() => location.hash)),
     'T6: the Activity route did not persist');
  finishTask('T6');

  // T7 — Stop an agent: the danger zone is still the only place.
  startTask();
  await gotoAgents(page);
  await openAgent(page, 'ops-agent'); clicks += 1;
  await tap(page.locator('[data-token-actions]'));
  ok(await page.locator('[data-danger-zone]').isVisible(), 'T7: the danger zone is missing');
  ok(await page.locator('[data-danger-revoke]').count() === 1 &&
     await page.locator('[data-danger-delete]').count() === 1,
     'T7: revoke/delete are missing');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  ok(await page.locator('[data-danger-zone]').isVisible(),
     'T7: the danger zone is missing after reload');
  finishTask('T7');

  // T8 — End-to-end testing.
  startTask();
  await gotoAgents(page);
  await openAgent(page, 'e2e-candidate'); clicks += 1;
  await tap(page.locator('[data-test-setup-open]'));
  await tap(page.locator('[data-test-host="192.0.2.10"]'));
  await tap(page.locator('[data-test-apply]'));
  await page.waitForSelector('[data-test-setup="2"]', { timeout: 15000 });
  const commands = (await page.locator('#x11-test-commands').inputValue()).split('\n');
  for (const cmd of ['hostname', 'id -un', 'cat /etc/hostname', 'printf e2e-plan',
    'printf e2e-plan-a', 'printf e2e-plan-b', 'printf e2e-wait', 'printf e2e-window']) {
    ok(commands.includes(cmd), 'T8: the command list is missing ' + cmd);
  }
  ok(/7 days/.test(await page.locator('[data-test-setup="2"]').textContent()),
     'T8: the token lifetime is not 7 days');
  await tap(page.locator('[data-test-mint]'));
  await page.waitForSelector('[data-test-setup="3"]', { timeout: 10000 });
  ok(/^e2e_/.test((await page.locator('[data-test-token]').textContent()).trim()),
     'T8: no e2e_ approver token was shown');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await tap(page.locator('button', { hasText: 'All agents' }));
  await page.waitForTimeout(1400);
  const e2eCard = page.locator('[data-agent-card]', { hasText: 'e2e-candidate' }).first();
  ok(/Active/.test(await e2eCard.textContent()), 'T8: the status badge is missing');
  ok(/web01/.test(await e2eCard.textContent()), 'T8: the one machine is not on the card');
  finishTask('T8');

  // T9 — Websites is one section, with no tool switch.
  startTask();
  await gotoAgents(page);
  await openAgent(page, 'db-agent'); clicks += 1;
  await tap(page.locator('[data-x11-nav="websites"]'));
  await page.locator('[data-web-enabled]').check(); clicks += 1;
  await page.locator('#x11-web-allow').fill('docs.example.test');
  ok(await page.locator('#x11-sec-websites [data-tool-name], #x11-sec-websites [data-choice]').count() === 0,
     'T9: Websites still has a tool switch');
  await tap(page.locator('#x11-save-changes'));
  await page.waitForSelector('#x11-preview-apply', { timeout: 15000 });
  await tap(page.locator('#x11-preview-apply'));
  await waitSaved(page);
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1400);
  ok(await page.locator('[data-web-enabled]').isChecked(), 'T9: Websites is not saved on');
  ok(await page.locator('#x11-web-allow').inputValue() === 'docs.example.test',
     'T9: the site is not saved');
  finishTask('T9');

  // Style: square corners everywhere, no x11-only badge/button look-alikes.
  const style = await page.evaluate(() => {
    const roots = [document.getElementById('x11-root'),
      document.getElementById('x11-preview-modal'),
      document.getElementById('x11-new-modal')].filter(Boolean);
    const bad = [];
    roots.forEach((root) => {
      root.querySelectorAll('*').forEach((el) => {
        const cs = getComputedStyle(el);
        const radii = [cs.borderTopLeftRadius, cs.borderTopRightRadius,
          cs.borderBottomLeftRadius, cs.borderBottomRightRadius];
        if (radii.some((r) => parseFloat(r) > 0)) {
          bad.push((el.id || el.className || el.tagName) + ':' + radii.join(','));
        }
      });
    });
    return { bad: bad.slice(0, 8),
      lookalikes: document.querySelectorAll('#x11-root .x11-badge, #x11-root .x11-button, #x11-root .x11-btn').length,
      buttons: document.querySelectorAll('#x11-root button').length };
  });
  ok(style.bad.length === 0, 'style: rounded corners remain: ' + style.bad.join(' | '));
  ok(style.lookalikes === 0, 'style: x11-only badge/button classes remain');
  ok(style.buttons > 0, 'style: no buttons found');

  ok(errors.length === 0, 'browser errors: ' + errors.slice(0, 5).join(' | '));

  // Phone: T2, T4, T8 at 390 px, no horizontal scroll.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const phoneErrors = [];
  phone.on('pageerror', (e) => phoneErrors.push(e.message));
  phone.on('dialog', (d) => d.accept());
  await login(phone.context());
  await gotoAgents(phone);
  await openAgent(phone, 'ops-agent'); clicks += 1;
  await tap(phone.locator('[data-x11-nav="run"]'));
  await tap(phone.locator('[data-ssh-mode="read_only"]'));
  await noSideScroll(phone, 'phone T2');
  clicks += 1;
  await phone.locator('#x11-save-changes').dispatchEvent('click');
  await phone.waitForSelector('#x11-preview-apply', { timeout: 15000 });
  await tap(phone.locator('#x11-preview-apply'));
  await waitSaved(phone);
  await phone.reload({ waitUntil: 'networkidle' });
  await phone.waitForTimeout(1400);
  ok(await phone.locator('[data-ssh-mode="read_only"]').getAttribute('aria-pressed') === 'true',
     'phone T2: the mode is not saved');

  await gotoAgents(phone);
  await openAgent(phone, 'ops-agent'); clicks += 1;
  await tap(phone.locator('[data-x11-nav="tools"]'));
  await tap(phone.locator('[data-integration-tile="Home Assistant"]'));
  await phone.waitForTimeout(600);
  await tap(phone.locator('[data-int-tab="devices"]'));
  await noSideScroll(phone, 'phone T4');
  const phoneCoffee = phone.locator('[data-device="switch.coffee"][data-device-action="switch.turn_on"]');
  await tap(phoneCoffee);
  await tap(phone.locator('#x11-save-changes'));
  await phone.waitForSelector('#x11-preview-apply', { timeout: 15000 });
  await tap(phone.locator('#x11-preview-apply'));
  await waitSaved(phone);
  await phone.reload({ waitUntil: 'networkidle' });
  await phone.waitForTimeout(1400);
  ok(await phone.locator('[data-device="switch.coffee"][data-device-action="switch.turn_on"]')
       .getAttribute('data-state') === 'ask', 'phone T4: the pill is not saved');

  await gotoAgents(phone);
  await openAgent(phone, 'phone-e2e'); clicks += 1;
  await tap(phone.locator('[data-test-setup-open]'));
  await tap(phone.locator('[data-test-host="192.0.2.10"]'));
  await tap(phone.locator('[data-test-apply]'));
  await phone.waitForSelector('[data-test-setup="2"]', { timeout: 15000 });
  await noSideScroll(phone, 'phone T8');
  ok(await phone.locator('#x11-test-commands').inputValue().then((v) => v.includes('printf e2e-window')),
     'phone T8: commands are not pre-filled');
  await tap(phone.locator('[data-test-mint]'));
  await phone.waitForSelector('[data-test-setup="3"]', { timeout: 10000 });
  ok(/^e2e_/.test((await phone.locator('[data-test-token]').textContent()).trim()),
     'phone T8: no token');
  ok(phoneErrors.length === 0, 'phone browser errors: ' + phoneErrors.slice(0, 5).join(' | '));

  await browser.close();
  process.stdout.write(JSON.stringify({ clickCounts }) + '\n');
  if (failures.length) {
    process.stderr.write(failures.join('\n') + '\n');
    process.exit(1);
  }
  process.stdout.write('x11_tasks: ok\n');
})().catch((e) => {
  process.stderr.write('x11_tasks crashed: ' + (e && e.stack || e) + '\n');
  process.exit(1);
});

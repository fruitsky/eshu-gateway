/* X11 Stage 5 screenshots: 1440 and 390, synthetic data only.
 * Run against tests/ui/serve_ui.py; not part of the pass/fail gate.
 */
'use strict';
const path = require('path');
const fs = require('fs');
const BASE = (process.env.ESHU_UI_URL || 'http://127.0.0.1:8790/').replace(/\/?$/, '/');
const PASSWORD = process.env.ESHU_UI_PASSWORD || '';
const OUT = path.resolve(__dirname, '..', '..', 'docs', 'screenshots', 'x11', 'stage5');

let chromium;
try { ({ chromium } = require('playwright')); }
catch (e) { process.stderr.write('playwright-not-available\n'); process.exit(2); }

async function login(context) {
  const r = await context.request.post(BASE + 'api/auth/login', {
    data: { password: PASSWORD }, headers: { 'X-Eshu-CSRF': '1' } });
  if (!r.ok()) throw new Error('login failed');
}
async function gotoAgents(page) {
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.evaluate(() => switchView('agents'));
  await page.waitForTimeout(1500);
}
async function openAgent(page, name) {
  await page.locator('[data-agent-card]', { hasText: name }).first().click();
  await page.waitForTimeout(1200);
}
async function shot(page, name) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, name), fullPage: false });
}
async function run(width, suffix, candidate) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 950 } });
  page.on('dialog', (d) => d.accept());
  await login(page.context());
  await gotoAgents(page);
  await shot(page, 'agents-' + suffix + '.png');

  // T1: each step.
  const name = 'shot-' + suffix + '-' + Date.now();
  await page.locator('[data-new-agent]').click();
  await page.locator('#x11-new-name').fill(name);
  await page.locator('[data-template="household"]').click();
  await shot(page, 't1-name-' + suffix + '.png');
  await page.locator('#x11-new-next').click();
  await page.waitForTimeout(250);
  await shot(page, 't1-integrations-' + suffix + '.png');
  await page.locator('#x11-new-next').click();
  await page.waitForTimeout(250);
  await shot(page, 't1-machines-' + suffix + '.png');
  await page.locator('#x11-new-next').click();
  await page.waitForTimeout(250);
  await shot(page, 't1-review-' + suffix + '.png');
  await page.locator('#x11-new-create').click();
  await page.waitForSelector('[data-new-token]', { timeout: 15000 });
  await page.evaluate(() => {
    const token = document.querySelector('[data-new-token]');
    if (token) token.textContent = 'synthetic-token-shown-once';
    document.querySelectorAll('.x11-connect code').forEach((el) => {
      if (/^Authorization: Bearer /.test(el.textContent || '')) {
        el.textContent = 'Authorization: Bearer synthetic-token-shown-once';
      }
    });
  });
  await shot(page, 't1-token-' + suffix + '.png');
  await page.locator('button', { hasText: 'Done' }).click();
  await page.waitForTimeout(900);

  // Run commands, integration page, HA Devices, HA Home-wide.
  await gotoAgents(page);
  await openAgent(page, 'ops-agent');
  await page.locator('[data-x11-nav="run"]').click();
  await page.waitForTimeout(500);
  await shot(page, 'run-commands-' + suffix + '.png');
  await page.locator('[data-x11-nav="websites"]').click();
  await page.waitForTimeout(500);
  await shot(page, 'websites-' + suffix + '.png');
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(500);
  await page.locator('[data-integration-tile="Sonarr"]').click();
  await page.waitForTimeout(700);
  await shot(page, 'integration-sonarr-' + suffix + '.png');
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(500);
  await page.locator('[data-integration-tile="Home Assistant"]').click();
  await page.waitForTimeout(600);
  await page.locator('[data-int-tab="devices"]').click();
  await page.waitForTimeout(700);
  await shot(page, 'ha-devices-' + suffix + '.png');
  await page.locator('[data-int-tab="homewide"]').click();
  await page.waitForTimeout(500);
  await shot(page, 'ha-homewide-' + suffix + '.png');

  // The save bar with "Not saved", then the in-app leave warning.
  await page.locator('[data-x11-nav="run"]').click();
  await page.waitForTimeout(400);
  await page.locator('[data-ssh-mode="read_only"]').click();
  await page.waitForTimeout(400);
  await shot(page, 'save-bar-' + suffix + '.png');
  await page.locator('button', { hasText: 'All agents' }).click();
  await page.waitForTimeout(400);
  await shot(page, 'leave-warning-' + suffix + '.png');
  await page.locator('[data-leave-stay]').click();
  await page.waitForTimeout(200);

  // T8: machine, token form, token shown.
  await gotoAgents(page);
  await openAgent(page, candidate);
  await page.locator('[data-test-setup-open]').click();
  await page.waitForTimeout(500);
  await shot(page, 't8-step1-' + suffix + '.png');
  await page.locator('[data-test-host="192.0.2.10"]').check();
  await page.waitForTimeout(200);
  await page.locator('[data-test-apply]').click();
  await page.waitForSelector('[data-test-setup="2"]', { timeout: 15000 });
  await page.waitForTimeout(300);
  await shot(page, 't8-step2-' + suffix + '.png');
  await page.locator('[data-test-mint]').click();
  await page.waitForSelector('[data-test-setup="3"]', { timeout: 10000 });
  await page.evaluate(() => {
    const token = document.querySelector('[data-test-token]');
    if (token) token.textContent = 'e2e_synthetic-token-shown-once';
  });
  await page.waitForTimeout(300);
  await shot(page, 't8-step3-' + suffix + '.png');

  await browser.close();
}

(async () => {
  await run(1440, '1440', 'e2e-candidate');
  await run(390, '390', 'phone-e2e');
  process.stdout.write('x11_shots: ok\n');
})().catch((e) => {
  process.stderr.write('x11_shots failed: ' + (e && e.stack || e) + '\n');
  process.exit(1);
});

/* Screenshot the X11 Stage 4 agent pages at 1440 and 390.
 *
 * Synthetic data only. Run against tests/ui/serve_ui.py with
 * ESHU_UI_SYNTHETIC_HA=1 and tests/ui/seed_ui.py:
 *   NODE_PATH=/path/to/node_modules ESHU_UI_URL=http://127.0.0.1:8790/ \
 *   ESHU_UI_PASSWORD=... node scripts/x11_stage4_shots.cjs
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'docs', 'screenshots', 'x11', 'stage4');
const BASE = (process.env.ESHU_UI_URL || 'http://127.0.0.1:8790/').replace(/\/?$/, '/');
const PASSWORD = process.env.ESHU_UI_PASSWORD || '';

async function login(context) {
  const r = await context.request.post(BASE + 'api/auth/login', {
    data: { password: PASSWORD }, headers: { 'X-Eshu-CSRF': '1' } });
  if (!r.ok()) throw new Error('login failed: HTTP ' + r.status());
}

const STATES = {
  'tools-overview': async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/tools', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
  },
  'integration-sonarr': async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/tools/Sonarr', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
  },
  'ha-devices-switches': async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/tools/Home%20Assistant/devices',
      { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await page.locator('[data-device-filter="switch"]:visible').first().click();
    await page.waitForTimeout(400);
    await page.evaluate(() => { const m = document.querySelector('.main-body'); if (m) m.scrollTop = 0; });
  },
  'ha-devices-sensors': async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/tools/Home%20Assistant/devices',
      { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const sensor = page.locator('[data-device-filter="temperature"]:visible').first();
    if (await sensor.count()) await sensor.click();
    else await page.locator('[data-device-filter="sensor"]:visible').first().click();
    await page.waitForTimeout(500);
    await page.evaluate(() => { const m = document.querySelector('.main-body'); if (m) m.scrollTop = 0; });
  },
  'ha-runs': async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/tools/Home%20Assistant/runs',
      { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
  },
  'ha-files': async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/tools/Home%20Assistant/files',
      { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
  },
  overview: async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/overview', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
  },
  'phone-tools': async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/tools', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
  },
  'phone-ha-devices': async (page, aid) => {
    await page.goto(BASE + '#agent/' + aid + '/tools/Home%20Assistant/devices',
      { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await page.locator('[data-device-filter="switch"]:visible').first().click();
    await page.waitForTimeout(400);
  },
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await login(context);
  for (const name of ['Empty shelf', 'Spare box']) {
    const r = await context.request.post(BASE + 'api/integrations', {
      data: { name, base_url: 'http://192.0.2.71', auth_type: 'none',
        kind: 'custom' },
      headers: { 'X-Eshu-CSRF': '1' } });
    if (!r.ok()) {
      const body = await r.json().catch(() => ({}));
      if (!/already exists/.test(String(body.detail || ''))) {
        throw new Error('could not create ' + name + ': HTTP ' + r.status());
      }
    }
  }
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.evaluate(() => switchView('agents'));
  await page.waitForTimeout(900);
  await page.locator('[data-agent-card]').first().click();
  await page.waitForTimeout(1000);
  const aid = await page.evaluate(() => x11State.agent.id);
  await page.close();
  await context.close();

  for (const [width, height] of [[1440, 1000], [390, 844]]) {
    const ctx = await browser.newContext({ viewport: { width, height } });
    await login(ctx);
    for (const [name, run] of Object.entries(STATES)) {
      // The two phone states are only meaningful at 390 px.
      if (width !== 390 && name.startsWith('phone-')) continue;
      const page = await ctx.newPage();
      await page.goto(BASE, { waitUntil: 'networkidle' });
      await run(page, aid);
      const sw = await page.evaluate(() => document.documentElement.scrollWidth);
      if (sw > width + 1) throw new Error(`${name}/${width}: ${sw}px wide`);
      const file = path.join(OUT, `${name}-${width}.png`);
      await page.screenshot({ path: file });
      console.log(path.relative(ROOT, file));
      await page.close();
    }
    await ctx.close();
  }
  await browser.close();
})().catch((e) => {
  process.stderr.write(String(e && e.stack || e) + '\n');
  process.exit(1);
});

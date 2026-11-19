/* Screenshot the real X11 Agents UI at 1440 and 390. Synthetic data only.
 *
 * Run against tests/ui/serve_ui.py with ESHU_UI_SYNTHETIC_HA=1 and the
 * synthetic seed:
 *   ESHU_UI_URL=http://127.0.0.1:8796/ ESHU_UI_PASSWORD=... \
 *   NODE_PATH=/path/to/node_modules node scripts/x11_ui_shots.cjs
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'docs', 'screenshots', 'x11');
const BASE = (process.env.ESHU_UI_URL || 'http://127.0.0.1:8796/').replace(/\/?$/, '/');
const PASSWORD = process.env.ESHU_UI_PASSWORD || '';

async function login(context) {
  const r = await context.request.post(BASE + 'api/auth/login', {
    data: { password: PASSWORD }, headers: { 'X-Eshu-CSRF': '1' } });
  if (!r.ok()) throw new Error('login failed: HTTP ' + r.status());
}
async function agents(page) {
  await page.evaluate(() => switchView('agents'));
  await page.waitForTimeout(900);
}
async function firstEditor(page) {
  await page.locator('[data-agent-card]').first().click();
  await page.waitForTimeout(1200);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const states = {
    agents: async (page) => { await agents(page); },
    rules: async (page) => {
      await agents(page);
      await page.locator('[data-x11-tab="rules"]').click();
      await page.waitForTimeout(900);
      await page.evaluate(() => document.querySelectorAll('[data-rules]').forEach((d) => { d.open = true; }));
      await page.waitForTimeout(200);
    },
    agent: async (page) => { await agents(page); await firstEditor(page); },
    'agent-tools': async (page) => {
      await agents(page); await firstEditor(page);
      await page.locator('[data-x11-nav="tools"]').click();
      await page.waitForTimeout(500);
      const group = page.locator('details[data-tool-group="Home Assistant"]');
      if (!(await group.evaluate((e) => e.open))) await group.locator('summary').click();
      await page.waitForTimeout(400);
      await page.evaluate(() => { document.querySelector('.main-body').scrollTop = 0; });
    },
    'agent-files': async (page) => {
      await agents(page); await firstEditor(page);
      await page.locator('[data-x11-nav="tools"]').click();
      await page.waitForTimeout(400);
      const group = page.locator('details[data-tool-group="Home Assistant"]');
      if (!(await group.evaluate((e) => e.open))) await group.locator('summary').click();
      await page.waitForTimeout(300);
      await page.locator('.x11-subhead').scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
    },
    'agent-devices': async (page) => {
      await agents(page); await firstEditor(page);
      await page.locator('[data-x11-nav="tools"]').click();
      await page.waitForTimeout(400);
      const group = page.locator('details[data-tool-group="Home Assistant"]');
      if (!(await group.evaluate((e) => e.open))) await group.locator('summary').click();
      await page.waitForTimeout(300);
      await page.locator('[data-always-line] .link-btn:visible').first().click();
      await page.waitForTimeout(500);
      if (page.viewportSize().width <= 760) {
        await page.evaluate(() => {
          const m = document.querySelector('.main-body');
          const g = document.getElementById('x11-device-grid');
          if (m && g) m.scrollTop += g.getBoundingClientRect().top - 78;
        });
      } else {
        await page.locator('#x11-device-grid').scrollIntoViewIfNeeded();
      }
      await page.waitForTimeout(300);
    },
    'agent-save-preview': async (page) => {
      await agents(page); await firstEditor(page);
      await page.locator('[data-x11-nav="tools"]').click();
      await page.waitForTimeout(400);
      await page.locator('[data-device="cover.garage"][data-action="cover.open_cover"]:visible').check();
      await page.waitForTimeout(300);
      await page.locator('#x11-save-changes').click();
      await page.waitForTimeout(800);
    },
    'agent-json': async (page) => {
      await agents(page); await firstEditor(page);
      await page.locator('[data-x11-nav="json"]').click();
      await page.waitForTimeout(500);
      await page.locator('#x11-sec-json').scrollIntoViewIfNeeded();
    },
    integrations: async (page) => {
      await page.evaluate(() => switchView('integrations'));
      await page.waitForTimeout(900);
      await page.locator('#integration-list').scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
    },
  };

  for (const [width, height] of [[1440, 1000], [390, 844]]) {
    const context = await browser.newContext({ viewport: { width, height } });
    await login(context);
    for (const [name, run] of Object.entries(states)) {
      const page = await context.newPage();
      page.on('dialog', (d) => d.accept());
      await page.goto(BASE, { waitUntil: 'networkidle' });
      await run(page);
      const sw = await page.evaluate(() => document.documentElement.scrollWidth);
      if (sw > width + 1) throw new Error(`${name}/${width}: ${sw}px wide`);
      const file = path.join(OUT, `${name}-${width}.png`);
      await page.screenshot({ path: file });
      console.log(path.relative(ROOT, file));
      await page.close();
    }
    await context.close();
  }
  await browser.close();
})().catch((e) => {
  process.stderr.write(String(e && e.stack || e) + '\n');
  process.exit(1);
});

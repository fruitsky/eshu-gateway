/* Render the X11 static mock at 1440 and 390. Synthetic data only.
 *
 * Run from the repo root:
 *   node scripts/x11_mock_shots.cjs
 *
 * The page is a local file; no dashboard, database, network or credentials
 * are involved. The script fails if a view is wider than its viewport.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PAGE = path.join(ROOT, 'docs', 'mockups', 'x11', 'index.html');
const OUT = path.join(ROOT, 'docs', 'screenshots', 'x11');
const VIEWS = ['agents', 'agent', 'ha', 'editor', 'preview', 'overview'];
const NAMES = {
  agents: 'agents',
  agent: 'agent',
  ha: 'agent-home-assistant',
  editor: 'agent-editor',
  preview: 'agent-save-preview',
  overview: 'runs-without-asking',
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  for (const view of VIEWS) {
    for (const [width, height] of [[1440, 1000], [390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto('file://' + PAGE + '?view=' + view);
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(250);
      if (errors.length) throw new Error(`${view}/${width}: ${errors[0]}`);
      const scrollWidth = await page.evaluate(
        () => document.documentElement.scrollWidth);
      if (scrollWidth > width + 1) {
        throw new Error(`${view}/${width}: ${scrollWidth}px wide`);
      }
      const file = path.join(OUT, `${NAMES[view]}-${width}.png`);
      await page.screenshot({ path: file, fullPage: true });
      console.log(path.relative(ROOT, file));
      await page.close();
    }
  }
  await browser.close();
})().catch((error) => {
  process.stderr.write(String(error.message || error) + '\n');
  process.exit(1);
});

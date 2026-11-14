/* Per-view browser check: no horizontal scroll, the view renders, and every
 * visible text node meets WCAG AA contrast (4.5:1, 3:1 for large text).
 *
 * The dashboard is served by tests/ui/serve_ui.py on a throwaway seeded
 * database (ESHU_UI_URL, ESHU_UI_PASSWORD). Exit 2 means "no browser here"
 * and the Python test turns that into a skip; any other non-zero exit is a
 * real failure, with the failing view and element on stderr.
 *
 * Run by hand:
 *   NODE_PATH=/path/to/node_modules ESHU_UI_URL=http://127.0.0.1:8790/ \
 *   ESHU_UI_PASSWORD=... node tests/ui/ui_views.cjs
 */
'use strict';

const BASE = (process.env.ESHU_UI_URL || 'http://127.0.0.1:8790/').replace(/\/?$/, '/');
const PASSWORD = process.env.ESHU_UI_PASSWORD || '';
const VIEWS = ['home', 'history', 'gateways', 'stats', 'controls',
               'integrations', 'inventory', 'runbooks', 'agents', 'settings'];

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (e) {
  process.stderr.write('playwright-not-available: ' + e.message.split('\n')[0] + '\n');
  process.exit(2);
}

function measure() {
  const W = window.innerWidth;
  const out = { overflow: [], contrast: [] };
  const parse = (c) => {
    const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (!m) return null;
    return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const blend = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1,
  });
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const bgOf = (el) => {
    for (let p = el; p; p = p.parentElement) {
      const cs = getComputedStyle(p);
      const c = parse(cs.backgroundColor);
      if (c && c.a >= 0.99) return c;
    }
    return parse(getComputedStyle(document.body).backgroundColor) || { r: 15, g: 13, b: 10, a: 1 };
  };
  const label = (el) => {
    const id = el.id ? '#' + el.id : '';
    const cls = (el.className && typeof el.className === 'string')
      ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    return el.tagName.toLowerCase() + id + cls;
  };
  document.querySelectorAll('body *').forEach((el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const hasText = Array.from(el.childNodes).some(
      (n) => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!hasText) return;
    const fg = parse(cs.color);
    if (!fg || fg.a === 0) return;
    const bg = bgOf(el);
    const effective = fg.a < 0.99 ? blend(fg, bg) : fg;
    const size = parseFloat(cs.fontSize) || 16;
    const bold = parseInt(cs.fontWeight, 10) >= 700;
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    const cr = ratio(effective, bg);
    if (cr < need && !out.contrast.some((c) => c.el === label(el))) {
      out.contrast.push({ el: label(el), ratio: Math.round(cr * 100) / 100, need,
        text: el.textContent.trim().slice(0, 40) });
    }
  });
  out.contrast.sort((a, b) => a.ratio - b.ratio);
  out.contrast = out.contrast.slice(0, 8);
  if (document.documentElement.scrollWidth > W + 1) {
    out.overflow.push({ doc: document.documentElement.scrollWidth, width: W });
  }
  return out;
}

const REQUIRED = {
  home: '.cc-empty-headline',
  history: '#view-history table',
  gateways: '#gateways-table-body tr',
  stats: '#view-stats',
  controls: '#view-controls',
  integrations: '#view-integrations',
  inventory: '#view-inventory',
  runbooks: '#view-runbooks',
  agents: '#view-agents',
  settings: '#view-settings',
};

(async () => {
  const failures = [];
  const browser = await chromium.launch();
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    const req = page.context().request;
    const login = await req.post(BASE + 'api/auth/login', {
      data: { password: PASSWORD }, headers: { 'X-Eshu-CSRF': '1' } });
    if (!login.ok()) {
      process.stderr.write('login failed: HTTP ' + login.status() + '\n');
      process.exit(1);
    }
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(700);

    for (const view of VIEWS) {
      await page.evaluate((v) => switchView(v), view);
      await page.waitForTimeout(450);
      const ok = await page.evaluate((sel) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && cs.display !== 'none' && r.left < innerWidth;
      }, REQUIRED[view]);
      if (!ok) failures.push(`${view}/${w}: required element not visible (${REQUIRED[view]})`);
      const m = await page.evaluate(measure);
      if (m.overflow.length) {
        failures.push(`${view}/${w}: horizontal scroll (${m.overflow[0].doc}px > ${m.overflow[0].width}px)`);
      }
      for (const c of m.contrast) {
        failures.push(`${view}/${w}: contrast ${c.ratio}:1 < ${c.need} for ${c.el} (${JSON.stringify(c.text)})`);
      }
    }

    // The plan card is below the fold with two pending JITs; its Approve must
    // still be reachable through the fixed strip (and be a real in-viewport
    // button, not just text).
    await page.evaluate(() => switchView('home'));
    await page.waitForTimeout(500);
    const cta = await page.evaluate(() => {
      const strip = document.getElementById('plan-cta');
      const cardBtn = document.querySelector('#plans-widget .plan-card .btn-approve');
      if (!strip || !cardBtn) return null;
      const cr = cardBtn.getBoundingClientRect();
      const cardInView = cr.width > 0 && cr.top >= 0 && cr.bottom <= innerHeight;
      const btn = strip.querySelector('.btn-approve');
      const r = btn.getBoundingClientRect();
      return { hidden: strip.classList.contains('hidden'), cardInView,
        ctaInView: r.width > 0 && r.top >= 0 && r.bottom <= innerHeight,
        text: (document.getElementById('plan-cta-text') || {}).textContent || '' };
    });
    if (!cta) failures.push(`home/${w}: no plan card or plan strip`);
    else if (!cta.cardInView && (cta.hidden || !cta.ctaInView)) {
      failures.push(`home/${w}: plan Approve is below the fold and the strip is not usable (${JSON.stringify(cta)})`);
    }
    if (cta && !cta.cardInView && !/Plan #\d+/.test(cta.text)) {
      failures.push(`home/${w}: plan strip does not name the plan (${JSON.stringify(cta.text)})`);
    }

    // The gateways table must fit its wrapper at 1440 (the sticky Actions
    // column used to paint over Modes), and the first paint at 390 must show
    // the host name.
    await page.evaluate(() => switchView('gateways'));
    await page.waitForTimeout(400);
    if (w === 1440) {
      const fit = await page.evaluate(() => {
        const wrap = document.querySelector('#view-gateways .table-wrap');
        const table = wrap && wrap.querySelector('table');
        return table ? { sw: table.scrollWidth, cw: wrap.clientWidth } : null;
      });
      if (!fit || fit.sw > fit.cw + 1) {
        failures.push(`gateways/1440: table ${fit && fit.sw}px wider than its ${fit && fit.cw}px wrapper`);
      }
    } else {
      const host = await page.evaluate(() => {
        const row = document.querySelector('#gateways-table-body tr');
        return row && row.children[1] ? row.children[1].innerText.trim() : '';
      });
      if (!host.includes('web01.example.test')) {
        failures.push(`gateways/390: first row's host is clipped (${JSON.stringify(host)})`);
      }
    }

    // 390-only checks for the controls the audit found broken.
    if (w === 390) {
      await page.evaluate(() => switchView('inventory'));
      await page.waitForTimeout(400);
      const inv = await page.evaluate(() => {
        const i = document.querySelector('#inv-interval');
        const save = Array.from(document.querySelectorAll('#view-inventory button'))
          .find((b) => b.textContent.trim() === 'Save');
        if (!i || !save) return null;
        const a = i.getBoundingClientRect(), b = save.getBoundingClientRect();
        return { iw: a.width, sameRow: Math.abs(a.top - b.top) < 16 };
      });
      if (!inv || inv.iw > 80 || !inv.sameRow) {
        failures.push(`inventory/390: interval control not inline (${JSON.stringify(inv)})`);
      }
      await page.evaluate(() => openSessionModal('sess-alpha'));
      await page.waitForTimeout(500);
      const sess = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('#session-modal .session-modal-actions button'));
        const close = document.querySelector('#session-modal .modal-close');
        const label = document.querySelector('#session-modal .session-modal-label');
        if (!btns.length || !close || !label) return null;
        const l = label.getBoundingClientRect(), c = close.getBoundingClientRect();
        return { n: btns.length, closeOnLabelRow: Math.abs(l.top - c.top) < 24 };
      });
      if (!sess || sess.n !== 3 || !sess.closeOnLabelRow) {
        failures.push(`sessions/390: modal header layout (${JSON.stringify(sess)})`);
      }
      await page.keyboard.press('Escape');
    }
    await page.close();
  }
  await browser.close();
  if (failures.length) {
    process.stderr.write(failures.join('\n') + '\n');
    process.exit(1);
  }
  process.stdout.write('ui_views: ok\n');
})();

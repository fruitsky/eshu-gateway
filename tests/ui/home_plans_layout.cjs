/* Render the home view and measure where the plan card lands.
 *
 * This is the browser half of tests/test_x5_plans_visible.py. It serves
 * dashboard/ itself (so /static/... resolves the way the app serves it), boots
 * the real index.html + app.js, then drives the real render functions with a
 * proposed plan and, in the second scenario, one pending JIT.
 *
 * Output: one JSON object on stdout. Exit 2 means "no browser here" and the
 * Python test turns that into a skip with the reason below. Any other non-zero
 * exit is a real failure.
 *
 * Run it by hand:  NODE_PATH=/path/to/node_modules node tests/ui/home_plans_layout.cjs
 */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const STATIC = path.join(ROOT, 'dashboard');

function fail(why, code) {
  process.stderr.write(why + '\n');
  process.exit(code);
}

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (e) {
  fail('playwright-not-available: ' + e.message.split('\n')[0], 2);
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.json': 'application/json',
};

function serve() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '');
      const file = path.join(STATIC, rel);
      if (!file.startsWith(STATIC) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        res.writeHead(404);
        res.end('not found');
        return;
      }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    });
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

/* Runs inside the page. `scenario` is 'idle' (plan only) or 'queue' (plan plus
 * one pending JIT). Uses the real globals: state.js declares requestsData with
 * `let`, so assignments must be bare, not window.*. */
async function measure(scenario) {
  const plan = {
    id: 7, session_id: 'sess-abc', agent: 'web01-agent', note: 'deploy the config',
    origin: 'agent', strict_sessions: true, hosts: 1,
    items: [{
      id: 1, command: 'systemctl restart nginx', host: 'web01.example.test',
      max_uses: 1, breakdown: { summary: { safe: 1, unknown: 0 } },
    }],
  };
  _sessionPlans = [plan];
  _pendingWinReqs = [];
  _pendingIntegrationCalls = [];
  _gatewaysData = [];
  const queued = scenario === 'queue';
  requestsData = queued ? [{
    id: 11, command: 'uptime', status: 'pending', ttl: 120,
    created_at: Math.floor(Date.now() / 1000), target_ip: '192.0.2.10',
    hostname: 'web01.example.test', risk: 'low',
    breakdown: { summary: { safe: 1, unknown: 0 } },
  }] : [];
  const container = document.getElementById('jit-tickets');
  container.dataset.sig = '';
  renderJitTickets();
  await new Promise((r) => setTimeout(r, 60));

  const widget = document.getElementById('plans-widget');
  const btn = widget && widget.querySelector('.btn-approve');
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height, bottom: r.bottom, right: r.right };
  };
  const hit = btn
    ? document.elementFromPoint(btn.getBoundingClientRect().x + btn.getBoundingClientRect().width / 2,
                               btn.getBoundingClientRect().y + btn.getBoundingClientRect().height / 2)
    : null;
  const star = document.querySelector('.starfield-wrap');
  const sessions = document.getElementById('cc-sessions-host');
  const queue = document.getElementById('cc-queue');
  const headline = document.querySelector('.cc-empty-headline');
  const order = Array.from(document.querySelectorAll('#cc-queue, #plans-widget, #cc-sessions-host'))
    .map((el) => el.id);
  const overlaps = (a, b) => {
    if (!a || !b) return false;
    return !(a.right <= b.x || b.right <= a.x || a.bottom <= b.y || b.bottom <= a.y);
  };
  return {
    viewport: { w: innerWidth, h: innerHeight },
    planHidden: !widget || widget.classList.contains('hidden'),
    widgetDisplay: widget ? getComputedStyle(widget).display : null,
    slot: widget && widget.parentElement ? widget.parentElement.id : null,
    widget: widget ? rect(widget) : null,
    button: btn ? rect(btn) : null,
    hitIsButton: !!(btn && hit === btn),
    hitDesc: hit ? hit.tagName + '.' + (hit.className || '') : null,
    starfield: star ? rect(star) : null,
    buttonOverlapsStarfield: overlaps(btn ? rect(btn) : null, star ? rect(star) : null),
    queueBottom: queue ? queue.getBoundingClientRect().bottom : null,
    headlineBottom: headline ? headline.getBoundingClientRect().bottom : null,
    domOrder: order,
    scroll: {
      height: document.getElementById('cc-feed-scroll').scrollHeight,
      top: document.getElementById('cc-feed-scroll').scrollTop,
    },
    innerHeight: innerHeight,
  };
}

(async () => {
  const server = await serve();
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch();
  const results = {};
  const errors = [];
  try {
    for (const [name, width, height] of [['desktop', 1440, 900], ['phone', 390, 844]]) {
      for (const scenario of ['idle', 'queue']) {
        const page = await browser.newPage({ viewport: { width, height } });
        page.on('pageerror', (e) => errors.push(name + '/' + scenario + ': ' + e.message));
        await page.route('**/api/**', (route) =>
          route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
        await page.goto(base + '/static/index.html', { waitUntil: 'load' });
        await page.waitForTimeout(250);
        // The auth gate is not what this test is about: show the shell.
        await page.evaluate(() => {
          const main = document.getElementById('main-content');
          main.style.display = '';
          main.classList.remove('hidden');
          for (const id of ['login-overlay', 'setup-overlay']) {
            const el = document.getElementById(id);
            if (el) { el.classList.add('hidden'); el.style.display = 'none'; }
          }
        });
        results[name + '-' + scenario] = await page.evaluate(measure, scenario);
        await page.close();
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
  if (errors.length) fail('page-errors: ' + errors.join(' | '), 1);
  process.stdout.write(JSON.stringify(results, null, 2));
})().catch((e) => fail('harness-failed: ' + (e && e.stack ? e.stack : e), 1));

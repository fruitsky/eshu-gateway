/* X11 Stage 3 browser check: the two tabs, the card summary, the device grid,
 * Check -> Apply with hash/acks, 409 reload, expiry, JSON errors and Resume.
 * Synthetic data only; run against tests/ui/serve_ui.py.
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
function fail(msg) { failures.push(msg); }
function ok(cond, msg) { if (!cond) fail(msg); }

async function login(context) {
  const r = await context.request.post(BASE + 'api/auth/login', {
    data: { password: PASSWORD }, headers: { 'X-Eshu-CSRF': '1' } });
  if (!r.ok()) throw new Error('login failed: HTTP ' + r.status());
}

async function openAgents(page) {
  await page.evaluate(() => switchView('agents'));
  await page.waitForTimeout(900);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('dialog', (d) => d.accept());
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  const bodies = [];
  page.on('request', (r) => {
    if (r.method() !== 'PUT' || !/\/api\/agents\/\d+\/config$/.test(r.url())) return;
    try { bodies.push(JSON.parse(r.postData())); } catch (e) { /* ignore */ }
  });
  await login(page.context());
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await openAgents(page);

  // 1. Two tabs, no old sidebar entry, card summary.
  ok(await page.locator('[data-x11-tab]').count() === 2, 'agents: expected exactly two tabs');
  ok(await page.locator('#nav-allowances').count() === 0, 'agents: Allowances sidebar entry still present');
  ok(await page.locator('[data-agent-card]').count() >= 2, 'agents: expected at least two agent cards');
  const summary = (await page.locator('[data-agent-card] .x11-card-summary').first().textContent()) || '';
  ok(/tools/.test(summary) && /asks first for/.test(summary) && /without asking/.test(summary),
     'agents: card summary is not the plain one-liner: ' + summary);
  const banner = page.locator('.x11-paused-banner');
  ok(await banner.count() === 1 && /\d+ paused/.test(await banner.textContent()),
     'agents: paused banner missing');

  // 2. Paused reason and Resume.
  await banner.locator('[data-paused-review]').click();
  await page.waitForTimeout(1000);
  ok(await page.locator('.x11-editor').count() === 1, 'editor: did not open from the paused banner');
  const reason = (await page.locator('.x11-paused-reason').first().textContent()) || '';
  ok(/the device changed in Home Assistant/.test(reason), 'paused: reason is not in plain words: ' + reason);
  const resume = page.locator('[data-resume-id]').first();
  ok(await resume.count() === 1, 'paused: Resume button missing');
  await resume.click();
  await page.waitForTimeout(1200);
  ok(await page.locator('#x11-sec-overview .x11-paused-reason').count() === 0, 'paused: Resume did not clear the paused row');

  // 3. A safe Check -> Apply sends the hash and no acks.
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(300);
  await page.locator('[data-tool-row="home_assistant_call_service"] [data-choice="run"]').click();
  await page.waitForTimeout(400);
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(700);
  ok(await page.locator('#x11-preview-modal').isVisible(), 'preview: modal did not open');
  ok(await page.locator('#x11-preview-apply').isEnabled(), 'preview: safe apply should be enabled without acks');
  await page.locator('#x11-preview-apply').click();
  await page.waitForTimeout(1000);
  const safeApply = bodies.filter((b) => b.preview === false).pop() || {};
  ok(!!safeApply.preview_hash, 'apply: preview_hash was not sent');
  ok(safeApply.risk_ack === false && safeApply.resume_ack === false,
     'apply: acks were sent when the preview did not ask');

  // 4. Device grid: area groups, protected lock, label column read-only.
  ok(await page.locator('#x11-device-grid').count() === 1, 'grid: did not open for call service');
  const areas = (await page.locator('.x11-area-row').allTextContents()).join('|');
  ok(/Living room/.test(areas) && /Garage/.test(areas), 'grid: rows are not grouped by real area: ' + areas);
  ok(await page.locator('[data-device-row="switch.plug"] .x11-lock').count() > 0,
     'grid: protected device is not locked');
  const mode = await page.locator('select[data-action-mode="switch.turn_on"]:visible').inputValue();
  ok(mode === 'label', 'grid: seeded label action is not in label mode');
  ok(await page.locator('select[data-action-label="switch.turn_on"]:visible').count() === 1,
     'grid: label picker missing');
  ok(await page.locator('[data-device="switch.lamp"][data-action="switch.turn_on"]').count() === 0,
     'grid: label column is still editable');

  // 5. Expiry remove sends expires_at: 0.
  const light = page.locator('[data-perm-edit][data-domain="light"][data-service="turn_off"]');
  ok(await light.locator('[data-expiry-clear]').count() === 1, 'expiry: remove control missing');
  await light.locator('[data-expiry-clear]').click();
  await page.waitForTimeout(300);
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(700);
  const expiryDoc = bodies.filter((b) => b.preview === true).slice(-1)[0] || {};
  const lightPerm = (((expiryDoc.document || {}).home_assistant || {}).permissions || [])
    .filter((p) => p.domain === 'light' && p.service === 'turn_off')[0] || {};
  ok(lightPerm.expires_at === 0, 'expiry: remove did not send expires_at 0');
  await page.locator('#x11-preview-modal button', { hasText: 'Cancel' }).click();
  await page.waitForTimeout(300);
  await page.evaluate(() => x11Discard());
  await page.waitForTimeout(500);

  // 6. A risky change needs the explicit risk checkbox.
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(300);
  if (!(await page.locator('#x11-device-grid').count())) {
    await page.locator('[data-tool-row="home_assistant_call_service"] [data-choice="run"]').click();
    await page.waitForTimeout(400);
  }
  await page.locator('[data-device="cover.garage"][data-action="cover.open_cover"]').check();
  await page.waitForTimeout(300);
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(700);
  ok(await page.locator('[data-x11-ack="risk"]').count() === 1, 'risk: preview did not ask for a risk ack');
  ok(await page.locator('#x11-preview-apply').isDisabled(), 'risk: Apply was enabled before the checkbox');
  await page.locator('[data-x11-ack="risk"]').check();
  await page.waitForTimeout(200);
  ok(await page.locator('#x11-preview-apply').isEnabled(), 'risk: Apply stayed disabled after checking');
  await page.locator('#x11-preview-apply').click();
  await page.waitForTimeout(1000);
  const riskyApply = bodies.filter((b) => b.preview === false).pop() || {};
  ok(riskyApply.risk_ack === true, 'risk: risk_ack was not sent after checking it');

  // 7. A stale version is a 409 and reloads with the reason.
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(300);
  await page.locator('[data-tool-row="home_assistant_get_config"] [data-choice="ask"]').click();
  await page.waitForTimeout(300);
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(700);
  const aid = await page.evaluate(() => x11State.agent.id);
  const bump = await page.context().request.put(BASE + 'api/agents/' + aid + '/policy', {
    data: { rate_limit_per_min: 31 }, headers: { 'Content-Type': 'application/json', 'X-Eshu-CSRF': '1' } });
  ok(bump.ok(), '409: could not bump the config version');
  if (await page.locator('[data-x11-ack="risk"]').count()) await page.locator('[data-x11-ack="risk"]').check();
  if (await page.locator('[data-x11-ack="resume"]').count()) await page.locator('[data-x11-ack="resume"]').check();
  await page.waitForTimeout(200);
  await page.locator('#x11-preview-apply').click();
  await page.waitForTimeout(1200);
  const notice = (await page.locator('#x11-notice').textContent()) || '';
  ok(/changed since you opened it/.test(notice), '409: reload message is not the required one: ' + notice);

  // 8. JSON: server errors next to the document, then a valid check.
  await page.locator('[data-x11-nav="json"]').click();
  await page.waitForTimeout(300);
  await page.locator('#x11-json-doc').fill(JSON.stringify({ schema_version: 1, ssh: { mode: 'bogus' } }, null, 2));
  await page.locator('#x11-json-check').click();
  await page.waitForTimeout(700);
  const jerr = (await page.locator('#x11-json-error').textContent()) || '';
  ok(jerr.length > 4 && /ssh|mode/i.test(jerr), 'JSON: server error not shown with a field path: ' + jerr);
  const doc = await page.evaluate(() => {
    const d = JSON.parse(JSON.stringify(x11State.draft));
    d.ssh.mode = 'read_only';
    return d;
  });
  await page.locator('#x11-json-doc').fill(JSON.stringify(doc, null, 2));
  await page.locator('#x11-json-check').click();
  await page.waitForTimeout(800);
  ok(await page.locator('#x11-preview-modal').isVisible(), 'JSON: a valid document did not reach the preview');
  await page.locator('#x11-preview-modal button', { hasText: 'Cancel' }).click();

  ok(errors.length === 0, 'browser errors: ' + errors.slice(0, 4).join(' | '));

  // 9. Phone width: no horizontal scroll, tabs and grid usable.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await login(phone.context());
  await phone.goto(BASE, { waitUntil: 'networkidle' });
  await openAgents(phone);
  let m = await phone.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  ok(m[0] <= m[1] + 1, '390: horizontal scroll on agents (' + m[0] + ' > ' + m[1] + ')');
  await phone.locator('[data-x11-tab="rules"]').click();
  await phone.waitForTimeout(800);
  ok(await phone.locator('[data-rules="protected"]').count() === 1, '390: rules tabs are not usable');
  await phone.locator('[data-x11-tab="agents"]').click();
  await phone.waitForTimeout(400);
  await phone.locator('[data-agent-card]').first().click();
  await phone.waitForTimeout(1000);
  await phone.locator('[data-x11-nav="tools"]').click();
  await phone.waitForTimeout(300);
  if (!(await phone.locator('#x11-device-grid').count())) {
    await phone.locator('[data-tool-row="home_assistant_call_service"] [data-choice="run"]').click();
    await phone.waitForTimeout(500);
  }
  ok(await phone.locator('.x11-mobile-modes').isVisible(), '390: device action choices are not visible');
  m = await phone.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  ok(m[0] <= m[1] + 1, '390: horizontal scroll on the device grid (' + m[0] + ' > ' + m[1] + ')');
  ok(await phone.locator('[data-device-row="switch.plug"] .x11-lock').count() > 0,
     '390: protected lock missing from the list');

  await browser.close();
  if (failures.length) {
    process.stderr.write(failures.join('\n') + '\n');
    process.exit(1);
  }
  process.stdout.write('x11_check: ok\n');
})().catch((e) => {
  process.stderr.write('x11_check crashed: ' + (e && e.stack || e) + '\n');
  process.exit(1);
});

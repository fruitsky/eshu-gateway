/* X11 Stage 3 browser check: tabs, one-section editor, deep links, danger zone,
 * grouped device grid, the Check preview, JSON and the 390 px layout.
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
function ok(cond, msg) { if (!cond) failures.push(msg); }
function overlap(a, b) {
  return !((a.x + a.width) <= b.x || (b.x + b.width) <= a.x ||
           (a.y + a.height) <= b.y || (b.y + b.height) <= a.y);
}
async function box(loc) { return loc.boundingBox(); }
async function login(context) {
  const r = await context.request.post(BASE + 'api/auth/login', {
    data: { password: PASSWORD }, headers: { 'X-Eshu-CSRF': '1' } });
  if (!r.ok()) throw new Error('login failed: HTTP ' + r.status());
}
async function openAgents(page) {
  await page.evaluate(() => switchView('agents'));
  await page.waitForTimeout(900);
}
async function scrollMain(page, bottom) {
  await page.evaluate((toBottom) => {
    const m = document.querySelector('.main-body');
    if (m) m.scrollTop = toBottom ? m.scrollHeight : 0;
    window.scrollTo(0, 0);
  }, bottom);
  await page.waitForTimeout(150);
}
async function openGroup(page, name) {
  const d = page.locator('details[data-tool-group="' + name + '"]');
  if (!(await d.evaluate((e) => e.open))) await d.locator('summary').click();
  await page.waitForTimeout(200);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  let dangerDismissed = false;
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => {
    if (/Revoke this token/.test(d.message())) { dangerDismissed = true; d.dismiss(); }
    else d.accept();
  });
  const bodies = [];
  page.on('request', (r) => {
    if (r.method() !== 'PUT' || !/\/api\/agents\/\d+\/config$/.test(r.url())) return;
    try { bodies.push(JSON.parse(r.postData())); } catch (e) { /* ignore */ }
  });
  await login(page.context());
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await openAgents(page);

  // 1. Tabs, normal New agent button, card summary in words.
  ok(await page.locator('[data-x11-tab]').count() === 2, 'agents: expected exactly two tabs');
  ok(await page.locator('#nav-allowances').count() === 0, 'agents: Allowances sidebar entry still present');
  ok(await page.locator('.x11-title-row [data-new-agent]').count() === 1,
     'agents: New agent is not a normal button beside the title');
  ok(await page.locator('.x11-new-card').count() === 0, 'agents: New agent is still a card');
  ok(await page.locator('[data-agent-card]').count() >= 2, 'agents: expected at least two cards');
  const summaries = await page.locator('[data-agent-card] .x11-card-summary').allTextContents();
  const summary = summaries[0] || '';
  ok(/tools run without asking/.test(summary) && /ask first/.test(summary) &&
     /on its own/.test(summary) && /SSH/.test(summary),
     'agents: card summary is not in words: ' + summary);
  ok(summaries.some((x) => /No tools yet/.test(x)), 'agents: an empty agent does not say No tools yet');
  const banner = page.locator('.x11-paused-banner');
  ok(await banner.count() === 1 && /\d+ paused/.test(await banner.textContent()),
     'agents: paused banner missing');

  // 2. Deep link, one section at a time.
  await page.locator('[data-agent-card]').first().click();
  await page.waitForTimeout(900);
  const aid = await page.evaluate(() => x11State.agent.id);
  await page.goto(BASE + '#agent/' + aid + '/tools', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1300);
  ok((await page.evaluate(() => location.hash)) === '#agent/' + aid + '/tools',
     'deep link: hash was not preserved');
  ok(await page.locator('.x11-section').count() === 1, 'editor: more than one section is visible');
  ok(await page.locator('#x11-sec-tools').count() === 1, 'deep link: tools section did not open');
  await page.locator('[data-x11-nav="overview"]').click();
  await page.waitForTimeout(300);
  ok(await page.locator('.x11-section').count() === 1, 'editor: more than one section after nav');
  ok(await page.locator('#x11-sec-overview').count() === 1, 'editor: overview did not open');

  // 3. Danger zone confirmation (dismiss; no revoke request).
  const beforeRevoke = await page.evaluate(() => x11State.agent.revoked);
  await page.locator('[data-danger-revoke]').click();
  await page.waitForTimeout(300);
  ok(dangerDismissed, 'danger zone: Revoke did not ask for confirmation');
  ok(await page.evaluate(() => x11State.agent.revoked) === beforeRevoke,
     'danger zone: a dismissed confirmation still changed the agent');

  // 4. Paused reason and Resume.
  const reason = (await page.locator('.x11-paused-reason').first().textContent()) || '';
  ok(/the device changed in Home Assistant/.test(reason), 'paused: reason is not in plain words: ' + reason);
  await page.locator('[data-resume-id]').first().click();
  await page.waitForTimeout(1200);
  ok(await page.locator('#x11-sec-overview .x11-paused-reason').count() === 0,
     'paused: Resume did not clear the paused row');

  // 5. Tools: collapsible groups, search, disabled always-ask, description.
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(300);
  ok(await page.locator('details[data-tool-group]').count() >= 1, 'tools: no integration groups');
  await openGroup(page, 'Home Assistant');
  const toolSummary = (await page.locator('details[data-tool-group="Home Assistant"] summary').textContent()) || '';
  ok(/tools/.test(toolSummary) && /ask first/.test(toolSummary),
     'tools: group summary is not in words: ' + toolSummary);
  ok(await page.locator('[data-tool-row="home_assistant_call_service"] [data-choice="run"]').isDisabled(),
     'tools: always-ask tool still offers Runs without asking');
  ok(/Always asks you/.test(await page.locator('[data-always-line]').first().textContent()),
     'tools: always-ask explanation missing');
  ok(await page.locator('[data-tool-row="home_assistant_call_service"] [data-always-line] .link-btn').count() === 1,
     'tools: call_service does not link to the device picker');
  ok(await page.locator('[data-tool-row="home_assistant_ha_addon_action"] [data-always-line] .link-btn').count() === 0 &&
     /Eshu requires your approval for this tool/.test(
       await page.locator('[data-tool-row="home_assistant_ha_addon_action"] [data-always-line]').textContent()),
     'tools: a non-device always-gate tool has the device link or the wrong text');
  ok(!/REQUIRES OPERATOR APPROVAL/.test(await page.locator('[data-tool-row="home_assistant_call_service"]').textContent()),
     'tools: an ALL-CAPS AI instruction is visible');
  const desc = page.locator('[data-tool-row="home_assistant_call_service"] .x11-tool-desc');
  ok(await desc.locator('.x11-more').count() === 1, 'tools: long description has no more control');
  ok(await desc.locator('.x11-desc-rest').isHidden(), 'tools: the rest of the description starts open');
  await desc.locator('.x11-more').click();
  ok(!(await desc.locator('.x11-desc-rest').isHidden()), 'tools: more did not reveal the rest');

  // 6. Grid: grouped headers, protected line, label full name, styling.
  ok(await page.locator('#x11-device-grid').count() === 1, 'grid: not present in Tools');
  const groupHeads = (await page.locator('table.x11-devices thead tr:first-child th').allTextContents()).join('|');
  ok(/Lights: on \/ off/.test(groupHeads) && /Plugs and switches: on \/ off/.test(groupHeads) &&
     /Covers: open \/ close/.test(groupHeads),
     'grid: grouped headers missing: ' + groupHeads);
  ok(await page.locator('[data-device-row="switch.plug"] .x11-protected-line:visible').count() === 1,
     'grid: protected row is not one Protected line');
  ok(/Protected/.test(await page.locator('[data-device-row="switch.plug"] .x11-protected-line:visible').textContent()) &&
     /Rules for all agents/.test(await page.locator('[data-device-row="switch.plug"] .x11-protected-line:visible').textContent()),
     'grid: protected line text missing');
  ok(await page.locator('[data-device-row="switch.plug"] .x11-lock:not(.x11-protected-line):visible').count() === 0,
     'grid: protected row still has repeated Locked cells');
  const labelFull = (await page.locator('.x11-label-full').first().textContent()) || '';
  ok(/Label: Living room lights/.test(labelFull), 'grid: full label name is not shown: ' + labelFull);
  const searchBg = await page.locator('.x11-device-tools input[type="search"]').evaluate(
    (e) => getComputedStyle(e).backgroundColor);
  ok(!/255, 255, 255/.test(searchBg), 'grid: search box is still browser-white');
  const nameFont = await page.locator('[data-device-row="switch.lamp"] .x11-device-name:visible').evaluate(
    (e) => getComputedStyle(e).fontFamily);
  const idFont = await page.locator('[data-device-row="switch.lamp"] .x11-device-id:visible').evaluate(
    (e) => getComputedStyle(e).fontFamily);
  ok(/Space Grotesk/.test(nameFont), 'grid: device name is not in the body font: ' + nameFont);
  ok(/JetBrains Mono/.test(idFont), 'grid: entity id is not in mono: ' + idFont);
  const checkAppearance = await page.locator('[data-device="switch.lamp"][data-action="switch.turn_off"]:visible').evaluate(
    (e) => getComputedStyle(e).appearance);
  ok(checkAppearance === 'none', 'grid: checkbox is still the browser default');
  ok(await page.locator('[data-device-row="switch.lamp_child_lock"]').count() === 0,
     'grid: settings entity is visible before Show settings entities');
  await page.locator('.x11-device-tools label', { hasText: 'Show settings entities' }).click();
  await page.waitForTimeout(300);
  ok(await page.locator('[data-device-row="switch.lamp_child_lock"]').count() >= 1,
     'grid: Show settings entities did not reveal the settings row');
  await page.locator('.x11-device-tools label', { hasText: 'Show settings entities' }).click();
  await page.waitForTimeout(300);

  // 7. Expiry remove sends expires_at: 0.
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
  await page.waitForTimeout(400);

  // 8. A new risky device: one section, one risky group, disabled Apply.
  await page.locator('[data-device="cover.garage"][data-action="cover.open_cover"]:visible').check();
  await page.waitForTimeout(300);
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(700);
  const preview = (await page.locator('#x11-preview-body').textContent()) || '';
  ok(/open 1 more device: Garage door/.test(preview), 'preview: plain sentence is wrong: ' + preview);
  ok(!/tools: all tools/.test(preview), 'preview: a device-only change lists tools');
  ok(/New or changed/.test(preview), 'preview: new risky group missing');
  ok(!/Already allowed, confirm again/.test(preview), 'preview: old risky group shown for a new item');
  ok(/Open · Garage door/.test(preview), 'preview: risky line does not name action and device: ' + preview);
  ok(await page.locator('#x11-preview-apply').isDisabled(), 'preview: Apply enabled before the risk box');
  ok(/Allow and save/.test(await page.locator('#x11-preview-apply').textContent()),
     'preview: risk apply is not called Allow and save');
  await page.locator('[data-x11-ack="risk"]').check();
  await page.waitForTimeout(200);
  ok(await page.locator('#x11-preview-apply').isEnabled(), 'preview: Apply stayed disabled after ticking');
  await page.locator('#x11-preview-apply').click();
  await page.waitForTimeout(1000);
  const riskyApply = bodies.filter((b) => b.preview === false).pop() || {};
  ok(riskyApply.risk_ack === true, 'risk: risk_ack was not sent');
  ok(!!riskyApply.preview_hash, 'risk: preview_hash was not sent');
  const lastPreview = bodies.filter((b) => b.preview === true).pop() || {};
  ok(lastPreview.preview_hash === '', 'preview: a preview request carried a hash');

  // 9. Both risky groups in one preview.
  await page.locator('[data-device="cover.garage"][data-action="cover.close_cover"]:visible').check();
  await page.waitForTimeout(300);
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(700);
  const both = (await page.locator('#x11-preview-body').textContent()) || '';
  ok(/New or changed/.test(both) && /Already allowed, confirm again/.test(both),
     'preview: the two risky groups are not both present: ' + both);
  await page.locator('#x11-preview-modal button', { hasText: 'Cancel' }).click();
  await page.waitForTimeout(300);
  await page.evaluate(() => x11Discard());

  // 10. Save bar never covers the last row.
  await page.locator('[data-device="switch.kettle"][data-action="switch.turn_off"]:visible').uncheck();
  await page.waitForTimeout(300);
  await scrollMain(page, true);
  const bar = await box(page.locator('#x11-savebar'));
  const lastRow = await box(page.locator('[data-perm-edit]').last());
  ok(bar && lastRow && lastRow.y + lastRow.height <= bar.y + 1,
     'savebar: covers the last row');
  await page.evaluate(() => x11Discard());

  // 11. JSON Apply stays disabled until Check succeeds, and after an edit.
  await page.locator('[data-x11-nav="json"]').click();
  await page.waitForTimeout(300);
  ok(await page.locator('#x11-json-apply').isDisabled(), 'JSON: Apply enabled before Check');
  await page.locator('#x11-json-check').click();
  await page.waitForTimeout(700);
  ok(await page.locator('#x11-preview-modal').isVisible(), 'JSON: Check did not preview');
  await page.locator('#x11-preview-modal button', { hasText: 'Cancel' }).click();
  await page.waitForTimeout(300);
  ok(!(await page.locator('#x11-json-apply').isDisabled()), 'JSON: Apply stayed disabled after Check');
  await page.locator('#x11-json-doc').fill('{"schema_version":1}');
  await page.waitForTimeout(200);
  ok(await page.locator('#x11-json-apply').isDisabled(), 'JSON: Apply stayed enabled after an edit');

  // 12. A stale version is a 409 and reloads with the reason.
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(300);
  await openGroup(page, 'Home Assistant');
  await page.locator('[data-tool-row="home_assistant_get_config"] [data-choice="ask"]').click();
  await page.waitForTimeout(300);
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(700);
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

  // 12b. Rules: short protected list, Change opens the picker, settings hidden.
  await page.evaluate(() => x11Back());
  await page.waitForTimeout(700);
  await page.locator('[data-x11-tab="rules"]').click();
  await page.waitForTimeout(700);
  await page.locator('[data-rules="protected"] summary').click();
  await page.waitForTimeout(300);
  ok(/Protected: TV plug/.test(await page.locator('[data-protected-summary]').textContent()),
     'rules: protected devices are not a short list');
  await page.locator('[data-protect-change]').click();
  await page.waitForTimeout(300);
  ok(await page.locator('[data-protect-settings]').count() === 1, 'rules: settings toggle missing');
  ok(await page.locator('[data-protect="switch.lamp_child_lock"]').count() === 0,
     'rules: settings entity is visible before Show settings entities');
  await page.locator('[data-protect-settings]').check();
  await page.waitForTimeout(300);
  ok(await page.locator('[data-protect="switch.lamp_child_lock"]').count() === 1,
     'rules: Show settings entities did not reveal the settings row');
  await page.locator('[data-rules="protected"] button', { hasText: 'Cancel' }).click();

  ok(errors.length === 0, 'browser errors: ' + errors.slice(0, 4).join(' | '));

  // 13. Phone width: cards, per-type settings, no overlap, no horizontal scroll.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await login(phone.context());
  await phone.goto(BASE + '#agent/' + aid + '/tools', { waitUntil: 'networkidle' });
  await phone.waitForTimeout(1400);
  ok(await phone.locator('.x11-section').count() === 1, '390: more than one section is visible');
  ok(await phone.locator('.x11-mobile-grid').isVisible(), '390: device cards are not shown');
  ok(await phone.locator('.x11-mobile-type-settings').first().isVisible(),
     '390: per-type settings are missing');
  ok(await phone.locator('.x11-mobile-device [data-action]').count() > 0,
     '390: device cards have no toggles');
  ok(await phone.locator('.x11-mobile-area').count() > 0,
     '390: device cards are not grouped by room');
  await phone.evaluate(() => {
    const m = document.querySelector('.main-body');
    const g = document.getElementById('x11-device-grid');
    if (m && g) m.scrollTop += g.getBoundingClientRect().top - 78;
  });
  await phone.waitForTimeout(300);
  const headerBg = await phone.evaluate(() => {
    const el = document.getElementById('x11-mobile-header-bg');
    const cs = el ? getComputedStyle(el) : null;
    const hit = document.elementFromPoint(innerWidth / 2, 30);
    return { display: cs && cs.display, bg: cs && cs.backgroundColor,
      hit: hit && (hit.id || hit.className || hit.tagName) };
  });
  ok(headerBg.display === 'block' && headerBg.bg !== 'rgba(0, 0, 0, 0)' &&
     /x11-mobile-header-bg/.test(headerBg.hit || ''),
     '390: the fixed header is not opaque over scrolled content ' + JSON.stringify(headerBg));
  const barBox = await box(phone.locator('#x11-mobile-header-bg'));
  const scrolledSearch = await box(phone.locator('.x11-device-tools input[type="search"]'));
  const scrolledFilters = await box(phone.locator('.x11-device-tools label').first());
  ok(barBox && scrolledSearch && scrolledFilters &&
     scrolledSearch.y >= barBox.y + barBox.height - 1 &&
     scrolledFilters.y >= barBox.y + barBox.height - 1,
     '390: scrolled search or filters sit under the fixed header');
  let m = await phone.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  ok(m[0] <= m[1] + 1, '390: horizontal scroll (' + m[0] + ' > ' + m[1] + ')');
  await scrollMain(phone, false);
  const search = await box(phone.locator('.x11-device-tools input[type="search"]'));
  const filters = await box(phone.locator('.x11-device-tools label').first());
  ok(search && filters && search.y + search.height <= filters.y + 1,
     '390: search and filters are not stacked');
  const menu = await box(phone.locator('#mobile-menu-btn'));
  const freeze = await box(phone.locator('#mobile-freeze'));
  ok(!overlap(menu, search) && !overlap(menu, filters) &&
     !overlap(freeze, search) && !overlap(freeze, filters),
     '390: header button overlaps search or filters ' +
     JSON.stringify({ menu, freeze, search, filters }));
  await phone.locator('[data-device="switch.kettle"][data-action="switch.turn_off"]:visible').uncheck();
  await phone.waitForTimeout(300);
  await phone.locator('[data-perm-edit]').last().scrollIntoViewIfNeeded();
  await phone.waitForTimeout(200);
  const pbar = await box(phone.locator('#x11-savebar'));
  const plast = await box(phone.locator('[data-perm-edit]').last());
  ok(pbar && plast && plast.y + plast.height <= pbar.y + 1,
     '390: save bar covers the last permission row ' + JSON.stringify({ pbar, plast }));

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

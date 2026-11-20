/* X11 Stage 4 browser check: integration tiles, per-integration pages, the
 * Home Assistant tabs, the device pill cycle, the Overview ledger and the
 * 390 px layout. Synthetic data only; run against tests/ui/serve_ui.py.
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
async function openFirstAgent(page) {
  await page.evaluate(() => switchView('agents'));
  await page.waitForTimeout(900);
  await page.locator('[data-agent-card]').first().click();
  await page.waitForTimeout(1200);
  return page.evaluate(() => x11State.agent.id);
}
async function gotoHash(page, hash) {
  await page.goto(BASE + hash, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1100);
}
async function cancelPreview(page) {
  const modal = page.locator('#x11-preview-modal');
  if (!(await modal.isVisible())) {
    failures.push('preview: no Cancel button; the preview did not open');
    return;
  }
  await modal.locator('button', { hasText: 'Cancel' }).click();
  await page.waitForTimeout(250);
}
async function scrollMain(page, bottom) {
  await page.evaluate((toBottom) => {
    const m = document.querySelector('.main-body');
    if (m) m.scrollTop = toBottom ? m.scrollHeight : 0;
    window.scrollTo(0, 0);
  }, bottom);
  await page.waitForTimeout(150);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => d.accept());
  const bodies = [];
  page.on('request', (r) => {
    if (r.method() !== 'PUT' || !/\/api\/agents\/\d+\/config$/.test(r.url())) return;
    try { bodies.push(JSON.parse(r.postData())); } catch (e) { /* ignore */ }
  });
  await login(page.context());
  // Two integrations created after startup carry no tools, so "Not connected"
  // and the phone fold are visible without a synthetic seed that the server
  // would immediately fill with the generic passthrough tools.
  for (const name of ['Empty shelf', 'Spare box']) {
    const r = await page.context().request.post(BASE + 'api/integrations', {
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
  // One seeded custom integration gives a generic `write` with the default
  // destructive gate: its row must say "Depends on the call", not "Asks".
  const generic = await page.context().request.post(BASE + 'api/integrations', {
    data: { name: 'Generic lab', base_url: 'http://192.0.2.72',
      auth_type: 'none', kind: 'custom' },
    headers: { 'X-Eshu-CSRF': '1' } });
  if (!generic.ok()) {
    const body = await generic.json().catch(() => ({}));
    if (!/already exists/.test(String(body.detail || ''))) {
      throw new Error('could not create Generic lab: HTTP ' + generic.status());
    }
  }
  await page.context().request.post(
    BASE + 'api/integrations/Generic%20lab/seed',
    { headers: { 'X-Eshu-CSRF': '1' } });
  await page.goto(BASE, { waitUntil: 'networkidle' });
  const aid = await openFirstAgent(page);

  // 1. The section nav is horizontal tabs under the agent name.
  ok(await page.locator('.x11-editor-tabs [data-x11-nav]').count() === 6,
     'tabs: expected six horizontal section tabs');
  ok(await page.locator('.x11-editor-tabs [data-x11-nav="overview"][aria-selected="true"]').count() === 1,
     'tabs: Overview is not the selected tab');
  ok(await page.locator('#x11-sec-overview').count() === 1, 'overview: section missing');

  // 2. Tools overview: summary, chips, tiles and the access bar.
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(600);
  ok(await page.locator('#x11-sec-tools').count() === 1, 'tools: overview did not open');
  const summary = (await page.locator('#x11-tools-summary').textContent()) || '';
  ok(/can use \d+ tools in \d+ of \d+ integrations/.test(summary) &&
     /\d+ ask you first; \d+ run on their own/.test(summary),
     'tools: summary sentence is wrong: ' + summary);
  const chips = await page.locator('[data-tile-filter]').allTextContents();
  ok(chips.length === 3 && /In use \(\d+\)/.test(chips[0]) &&
     /Not connected \(\d+\)/.test(chips[1]) && /All \(\d+\)/.test(chips[2]),
     'tools: filter chips are wrong: ' + chips.join(' | '));
  ok(await page.locator('[data-tile-filter="in_use"]').getAttribute('aria-pressed') === 'true',
     'tools: In use is not the default chip');
  const tileCount = await page.locator('[data-integration-tile]').count();
  ok(tileCount >= 10, 'tools: expected the integration tiles, found ' + tileCount);
  const bars = page.locator('[data-integration-tile] .x11-accessbar[role="img"]');
  ok(await bars.count() === tileCount, 'tools: a tile is missing its access bar');
  const barLabel = (await bars.first().getAttribute('aria-label')) || '';
  ok(/run on their own/.test(barLabel) && /ask you first/.test(barLabel) &&
     /not allowed/.test(barLabel), 'tools: access bar aria is not in words: ' + barLabel);
  ok(await page.locator('[data-integration-tile="Home Assistant"] .x11-tile-extra').count() === 1 &&
     /run on their own/.test(await page.locator('[data-integration-tile="Home Assistant"] .x11-tile-extra').textContent()),
     'tools: Home Assistant tile lacks its device line');
  ok(await page.locator('[data-integration-tile="SSH"] .x11-tile-extra').count() === 1,
     'tools: SSH tile is missing');
  ok(await page.locator('[data-integration-tile="Websites"] .x11-tile-extra').count() === 1,
     'tools: Websites tile is missing');
  const fold = await page.locator('[data-show-unused]').textContent().catch(() => '');
  ok(/Show \d+ integrations .* can.t use/.test(fold), 'tools: unused fold is missing: ' + fold);

  // 3. Search and the Not connected filter.
  await page.locator('#x11-tile-search').fill('Sonarr');
  await page.waitForTimeout(300);
  ok(await page.locator('[data-integration-tile]').count() === 1,
     'tools: search did not narrow to Sonarr');
  await page.locator('#x11-tile-search').fill('');
  await page.waitForTimeout(300);
  await page.locator('[data-tile-filter="not_connected"]').click();
  await page.waitForTimeout(300);
  ok(await page.locator('[data-integration-tile]').count() === 2,
     'tools: Not connected should show the two empty integrations');
  ok(await page.locator('[data-integration-tile].x11-tile-empty').count() === 2,
     'tools: an unconnected tile is not dashed');
  await page.locator('[data-tile-filter="in_use"]').click();
  await page.waitForTimeout(300);

  // 3b. SSH and Websites are ordinary integration pages too.
  await page.locator('[data-integration-tile="SSH"]').click();
  await page.waitForTimeout(600);
  ok(await page.evaluate(() => location.hash) === '#agent/' + aid + '/tools/SSH' &&
     await page.locator('.x11-section-title').textContent() === 'SSH' &&
     await page.locator('[data-ssh-mode]').count() === 1,
     'SSH: tile did not open its integration page');
  await page.evaluate(() => x11ToolsBack());
  await page.waitForTimeout(500);
  await page.locator('[data-integration-tile="Websites"]').click();
  await page.waitForTimeout(600);
  ok(await page.evaluate(() => location.hash) === '#agent/' + aid + '/tools/Websites' &&
     await page.locator('.x11-section-title').textContent() === 'Websites' &&
     await page.locator('#x11-web-allow').count() === 1,
     'Websites: tile did not open its integration page');
  await page.evaluate(() => x11ToolsBack());
  await page.waitForTimeout(500);

  // 4. Tile -> integration page deep link, Reading/Changing, always-ask.
  await page.locator('[data-integration-tile="Sonarr"]').click();
  await page.waitForTimeout(800);
  ok(await page.evaluate(() => location.hash) === '#agent/' + aid + '/tools/Sonarr',
     'deep link: tile did not open the integration route');
  ok(await page.locator('.x11-section-title').textContent() === 'Sonarr',
     'integration: title is not the integration name');
  ok(await page.locator('[data-tool-block="reading"]').count() === 1 &&
     await page.locator('[data-tool-block="changing"]').count() === 1,
     'integration: Reading/Changing groups missing');
  ok(await page.locator('[data-tool-row="sonarr_series"]').count() === 1,
     'integration: series tool missing');
  ok(await page.locator('[data-limits] [data-rate-limit]').count() === 1,
     'integration: the rate limit is missing');
  ok(await page.locator('[data-arg-tool="sonarr_series"]').count() === 1,
     'integration: a simple argument rule is not shown as a field');
  const always = page.locator('[data-tool-row="sonarr_add_series"] [data-choice="run"]');
  ok(await always.count() === 1 && await always.isDisabled(),
     'integration: always-ask tool still offers Runs on its own');
  ok(/Always asks you/.test(await page.locator('[data-tool-row="sonarr_add_series"] .x11-always-line').textContent()),
     'integration: always-ask reason missing');
  ok(await page.locator('#x11-json-tools').count() === 0, 'integration: tools JSON leaked from the overview');
  await page.locator('[data-int-json]').click();
  await page.waitForTimeout(300);
  const intJson = (await page.locator('#x11-json-int').textContent()) || '';
  ok(/"tools"/.test(intJson) && /"mode"/.test(intJson) && /"approval"/.test(intJson) &&
     !/"home_assistant"/.test(intJson), 'integration: JSON slice is wrong');
  await page.locator('[data-int-json]').click();
  await page.waitForTimeout(200);

  // 5. A read tool can be made to ask, and Check carries an empty hash.
  await page.locator('[data-tool-row="sonarr_series"] [data-choice="ask"]').click();
  await page.waitForTimeout(300);
  ok(await page.locator('#x11-savebar').isVisible(), 'integration: save bar did not appear');
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(800);
  ok(await page.locator('#x11-preview-modal').isVisible(), 'integration: Check did not preview');
  const checkBody = bodies.filter((b) => b.preview === true).pop() || {};
  ok(checkBody.preview_hash === '', 'integration: a preview request carried a hash');
  await cancelPreview(page);
  await page.evaluate(() => x11Discard());
  await page.waitForTimeout(300);

  // 6. Remove all access goes through Check -> preview, not a direct write.
  await page.locator('[data-remove-access]').click();
  await page.waitForTimeout(1300);
  ok(await page.locator('#x11-preview-modal').isVisible(),
     'remove access: did not open the Check preview');
  await cancelPreview(page);
  await page.evaluate(() => x11Discard());
  await page.waitForTimeout(300);

  // 6b. A generic write under the default destructive gate depends on the
  // call; it is neither counted as a run nor as an ask.
  await gotoHash(page, '#agent/' + aid + '/tools/Generic%20lab');
  await page.waitForTimeout(700);
  ok(await page.locator('[data-tool-row="generic_lab_write"][data-tool-outcome="depends"]').count() === 1 &&
     /Depends on the call/.test(await page.locator('[data-tool-row="generic_lab_write"] .x11-depends-line').textContent()),
     'integration: a destructive-gate write does not say Depends on the call');

  // 7. Back to Tools, then the Home Assistant page with four tabs.
  await gotoHash(page, '#agent/' + aid + '/tools');
  ok(await page.locator('[data-x11-section="tools"]').count() === 1,
     'tools: back route did not land on the overview');
  await page.locator('[data-integration-tile="Home Assistant"]').click();
  await page.waitForTimeout(800);
  ok(await page.locator('[data-int-tab]').count() === 4,
     'HA: expected four tabs');
  ok(await page.locator('[data-int-tab="tools"][aria-selected="true"]').count() === 1,
     'HA: Tools is not the default tab');
  ok(await page.locator('[data-tool-row="home_assistant_call_service"] [data-choice="run"]').isDisabled(),
     'HA tools: call_service still offers Runs on its own');
  ok(/Devices/.test(await page.locator('[data-tool-row="home_assistant_call_service"] .x11-always-line').textContent()),
     'HA tools: call_service does not point at Devices');

  // 8. Devices: rail, hidden line, protected pill, readings.
  await page.locator('[data-int-tab="devices"]').click();
  await page.waitForTimeout(700);
  ok(await page.locator('[data-device-rail] [data-device-filter]').count() >= 10,
     'devices: filter rail is missing');
  ok(await page.locator('[data-device-filter="switch"][aria-pressed="true"]').count() >= 1,
     'devices: the default filter is not Plugs and switches');
  ok(await page.locator('[data-device-filter="lock"]').count() >= 1,
     'devices: the Locks filter is missing');
  await page.locator('[data-device-filter="lock"]').first().click();
  await page.waitForTimeout(400);
  ok(await page.locator('[data-device-row="lock.front_door"]').count() === 1,
     'devices: a lock is not visible under its filter');
  await page.locator('[data-device-filter="switch"]').first().click();
  await page.waitForTimeout(400);
  ok(/settings, diagnostic and helper entities are hidden/.test(
       await page.locator('[data-hidden-line]').textContent()),
     'devices: hidden line is missing');
  ok(await page.locator('[data-device-row="switch.plug"] .x11-pill-protected').count() === 1,
     'devices: protected row should have exactly one Protected pill');
  ok(/Rules for all agents/.test(
       await page.locator('[data-device-row="switch.plug"] .x11-pill-link').textContent()),
     'devices: protected pill does not link to Rules');
  const readings = await page.locator('[data-room="Living room"] .x11-room-readings').textContent().catch(() => '');
  ok(/\d/.test(readings), 'devices: the room header has no key readings: ' + readings);
  const allSwitchRows = await page.locator('[data-device-row]').count();
  await page.locator('.x11-device-tools input[type="checkbox"]').check();
  await page.waitForTimeout(400);
  ok(await page.locator('[data-device-row]').count() < allSwitchRows,
     'devices: Only devices the agent can use did not narrow the list');
  await page.locator('.x11-device-tools input[type="checkbox"]').uncheck();
  await page.waitForTimeout(400);
  await page.locator('#x11-device-search').fill('kettle');
  await page.waitForTimeout(400);
  ok(await page.locator('[data-device-row]').count() === 1 &&
     await page.locator('[data-device-row="switch.kettle"]').count() === 1,
     'devices: search did not narrow to the kettle');
  await page.locator('#x11-device-search').fill('');
  await page.waitForTimeout(400);
  ok(await page.locator('[data-device-row="switch.lamp_child_lock"]').count() === 0,
     'devices: a hidden child-lock row is visible before Show them');
  await page.locator('[data-show-hidden]').click();
  await page.waitForTimeout(400);
  ok(await page.locator('[data-hidden-group]').count() === 1 &&
     await page.locator('[data-hidden-group] [data-device-row="switch.lamp_child_lock"]').count() === 1,
     'devices: Show them did not reveal the hidden rows');
  await page.locator('[data-show-hidden]').click();
  await page.waitForTimeout(300);

  // 9. A pill cycles ask -> run, and Check previews it.
  const coffee = page.locator('[data-device="switch.coffee"][data-device-action="switch.turn_on"]');
  ok(await coffee.getAttribute('data-state') === 'ask', 'devices: expected the coffee pill to ask');
  await coffee.click();
  await page.waitForTimeout(400);
  ok(await page.locator('[data-device="switch.coffee"][data-device-action="switch.turn_on"]').getAttribute('data-state') === 'run',
     'devices: pill did not cycle ask -> run');
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(800);
  const cyclePreview = (await page.locator('#x11-preview-body').textContent()) || '';
  ok(/Coffee machine/.test(cyclePreview) && /turn on 1 more device/.test(cyclePreview),
     'devices: Check preview does not name the new permission: ' + cyclePreview);
  await cancelPreview(page);
  await page.evaluate(() => x11Discard());
  await page.waitForTimeout(300);

  // 10. A risky device needs the second confirmation in the preview.
  await page.locator('[data-device-filter="cover"]').first().click();
  await page.waitForTimeout(400);
  const garage = page.locator('[data-device="cover.garage"][data-device-action="cover.open_cover"]');
  ok(await garage.count() === 1, 'devices: garage cover is missing');
  await garage.click();
  await page.waitForTimeout(300);
  await page.locator('#x11-save-changes').click();
  await page.waitForTimeout(800);
  const riskPreview = (await page.locator('#x11-preview-body').textContent()) || '';
  ok(/Garage door/.test(riskPreview) && /risky/i.test(riskPreview),
     'devices: risky preview is wrong: ' + riskPreview);
  ok(await page.locator('#x11-preview-apply').isDisabled(),
     'devices: risky Apply is enabled before the confirmation');
  await page.locator('[data-x11-ack="risk"]').check();
  await page.waitForTimeout(200);
  ok(await page.locator('#x11-preview-apply').isEnabled(),
     'devices: risky Apply stayed disabled after the confirmation');
  await cancelPreview(page);
  await page.evaluate(() => x11Discard());
  await page.waitForTimeout(300);

  // 11. Sensors: a reading and Can read, no pills.
  await page.locator('[data-device-filter="temperature"]').first().click();
  await page.waitForTimeout(400);
  const sensor = page.locator('[data-device-row="sensor.living_temp"]');
  ok(await sensor.count() === 1, 'sensors: living temperature is missing');
  ok(/\d/.test(await sensor.locator('.x11-sensor-reading b').textContent()),
     'sensors: no reading');
  ok(/Can read/.test(await sensor.locator('.x11-sensor-reading').textContent()),
     'sensors: Can read is missing');
  ok(await sensor.locator('[data-device-pill]').count() === 0,
     'sensors: a sensor has action pills');

  // 12. HA JSON on the tabs is the home_assistant section.
  await page.locator('[data-int-json]').click();
  await page.waitForTimeout(300);
  const haJson = (await page.locator('#x11-json-home_assistant').textContent()) || '';
  ok(/"permissions"/.test(haJson) && !/"tools"/.test(haJson),
     'HA: JSON tab is not the home_assistant section');
  await page.locator('[data-int-json]').click();
  await page.waitForTimeout(200);

  // 13. Runs on its own: cards, limits, paused Resume, waiting Accept/Dismiss.
  await page.locator('[data-int-tab="runs"]').click();
  await page.waitForTimeout(600);
  ok(await page.locator('[data-permission-card]').count() >= 4,
     'runs: permission cards are missing');
  ok(/At most \d+ an hour/.test(await page.locator('[data-permission-card]').first().textContent()),
     'runs: limits are not in words');
  ok(/Follows the label/.test(await page.locator('[data-permission-card]').nth(1).textContent()),
     'runs: label source is not in words');
  ok(await page.locator('[data-permission-card].x11-perm-paused [data-resume-id]').count() === 1,
     'runs: paused Resume is missing');
  await page.locator('[data-edit-permission]').first().click();
  await page.waitForTimeout(300);
  ok(await page.locator('.x11-perm-edit [data-rate]').count() === 1 &&
     await page.locator('.x11-perm-edit [data-expiry-set]').count() === 1,
     'runs: Edit does not open the rate/window/end-date editor');
  await page.locator('[data-edit-permission]').first().click();
  const waiting = page.locator('[data-waiting-grid] [data-waiting-row]');
  ok(await waiting.count() >= 1, 'runs: expected a waiting device');
  await page.locator('[data-waiting-grid] [data-dismiss-join]').first().click();
  await page.waitForSelector('[data-waiting-grid] [data-dismissed-line]', { timeout: 6000 }).catch(() => {});
  ok(await page.locator('[data-waiting-grid] [data-dismissed-line]').count() === 1,
     'runs: Dismiss did not leave a dismissed line');
  await page.locator('[data-waiting-grid] [data-reoffer-join]').first().click();
  await page.waitForFunction(
    () => document.querySelectorAll('[data-waiting-grid] [data-dismissed-line]').length === 0,
    null, { timeout: 6000 }).catch(() => {});
  ok(await page.locator('[data-waiting-grid] [data-dismissed-line]').count() === 0 &&
     await page.locator('[data-waiting-grid] [data-accept-join]').count() === 2,
     'runs: Re-offer did not return the device');
  await page.locator('[data-waiting-grid] [data-accept-join]').first().click();
  await page.waitForFunction(
    () => document.querySelectorAll('[data-waiting-grid] [data-waiting-row]').length === 1,
    null, { timeout: 6000 }).catch(() => {});
  ok(await page.locator('[data-waiting-grid] [data-waiting-row]').count() === 1,
     'runs: Accept did not remove the waiting device');

  // 14. Files: the pinned fingerprint and the write-always-asks note.
  await page.locator('[data-int-tab="files"]').click();
  await page.waitForTimeout(600);
  ok(await page.locator('[data-tool-row="ha_files_ha_config_write"]').count() === 1 ||
     await page.locator('[data-tool-row$="ha_config_write"]').count() === 1,
     'files: write tool is missing');
  ok(await page.locator('[data-tool-row$="ha_config_write"] [data-choice="run"]').isDisabled(),
     'files: a write still offers Runs on its own');
  const filesText = (await page.locator('[data-files-footer]').textContent()) || '';
  ok(/Pinned host key/.test(filesText) && /ssh-keygen -lf/.test(filesText),
     'files: fingerprint or compare line is missing: ' + filesText);

  // 15. Overview: the ledger comes first, then the permissions, no HA-first block.
  await gotoHash(page, '#agent/' + aid + '/overview');
  ok(await page.locator('[data-overview-ledger]').count() === 1,
     'overview: ledger is missing');
  ok(await page.locator('[data-overview-ledger] [data-ledger]').count() >= 10,
     'overview: ledger has no integration rows');
  ok(await page.locator('[data-paused-block]').count() === 1,
     'overview: paused block is missing');
  const ledgerBox = await box(page.locator('[data-overview-ledger]'));
  const permsBox = await box(page.locator('.x11-perm-cards').first());
  ok(ledgerBox && permsBox && ledgerBox.y < permsBox.y,
     'overview: permissions appear above the integration ledger');
  ok(await page.locator('[data-danger-zone]').count() === 1,
     'overview: danger zone is missing');

  // 16. Tools JSON is the whole tools section; JSON Apply is Check-gated.
  await page.locator('[data-x11-nav="tools"]').click();
  await page.waitForTimeout(400);
  await page.locator('[data-json-toggle="tools"]').click();
  await page.waitForTimeout(300);
  const toolsJson = (await page.locator('#x11-json-tools').textContent()) || '';
  ok(/"mode"/.test(toolsJson) && /"approval"/.test(toolsJson) && /"argument_rules"/.test(toolsJson),
     'tools: Show JSON is not the whole tools section');
  await page.locator('[data-x11-nav="json"]').click();
  await page.waitForTimeout(300);
  ok(await page.locator('#x11-json-apply').isDisabled(), 'JSON: Apply enabled before Check');
  await page.locator('#x11-json-check').click();
  await page.waitForTimeout(800);
  ok(await page.locator('#x11-preview-modal').isVisible(), 'JSON: Check did not preview');
  await cancelPreview(page);
  ok(!(await page.locator('#x11-json-apply').isDisabled()), 'JSON: Apply stayed disabled after Check');
  await page.locator('#x11-json-doc').fill('{"schema_version":1}');
  await page.waitForTimeout(200);
  ok(await page.locator('#x11-json-apply').isDisabled(), 'JSON: Apply stayed enabled after an edit');

  // 16b. Emergency Freeze is visible on the summary and on a write row.
  const frozen = await page.evaluate(async () => {
    const r = await fetch('/api/freeze', { method: 'POST',
      headers: { 'X-Eshu-CSRF': '1' } });
    return r.status;
  });
  ok(frozen === 200, 'freeze: could not turn the freeze on');
  await gotoHash(page, '#agent/' + aid + '/tools');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);
  ok(/Emergency Freeze/.test(await page.locator('#x11-tools-summary').textContent()),
     'freeze: the summary does not say the freeze is on');
  ok(/Frozen/.test(await page.locator('.x11-legend').textContent()),
     'freeze: the legend has no Frozen state');
  await gotoHash(page, '#agent/' + aid + '/tools/Generic%20lab');
  await page.waitForTimeout(600);
  ok(await page.locator('[data-tool-row="generic_lab_write"][data-tool-outcome="frozen"]').count() === 1 &&
     /Frozen/.test(await page.locator('[data-tool-row="generic_lab_write"] .x11-frozen-line').textContent()),
     'freeze: a write row does not show Frozen');
  await page.evaluate(async () => {
    await fetch('/api/unfreeze', { method: 'POST',
      headers: { 'X-Eshu-CSRF': '1' } });
  });

  ok(errors.length === 0, 'browser errors: ' + errors.slice(0, 4).join(' | '));

  // 17. Phone: one-column tiles, chips, 44 px buttons, no overlap or side scroll.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const phoneErrors = [];
  phone.on('pageerror', (e) => phoneErrors.push(e.message));
  await login(phone.context());
  await gotoHash(phone, '#agent/' + aid + '/tools');
  const t1 = await box(phone.locator('[data-integration-tile]').nth(0));
  const t2 = await box(phone.locator('[data-integration-tile]').nth(1));
  ok(t1 && t2 && Math.abs(t1.x - t2.x) < 2 && t2.y > t1.y,
     '390: integration cards are not one column');
  const tabScroll = await phone.locator('.x11-editor-tabs').evaluate((e) => [e.scrollWidth, e.clientWidth]);
  ok(tabScroll[0] > tabScroll[1], '390: section tabs do not scroll horizontally');
  const toolWidths = await phone.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  ok(toolWidths[0] <= toolWidths[1] + 1, '390: horizontal scroll on Tools');
  await gotoHash(phone, '#agent/' + aid + '/tools/Home%20Assistant/devices');
  ok(await phone.locator('[data-device-rail]').isHidden(), '390: the desktop rail is visible');
  ok(await phone.locator('[data-device-chips]').isVisible(), '390: device chips are missing');
  ok(await phone.locator('[data-device-chips] [data-device-filter="sensor"]').count() === 1,
     '390: the Sensors chip is missing');
  const chipBox = await box(phone.locator('[data-device-chips] [data-device-filter="sensor"]'));
  ok(chipBox && chipBox.height >= 44, '390: a filter chip is below 44 px');
  const big = phone.locator('[data-device-pill]').first();
  const bigBox = await box(big);
  ok(bigBox && bigBox.height >= 44, '390: a device pill is below 44 px');
  const row = phone.locator('[data-device-row]').first();
  const stateBox = await box(row.locator('.x11-device-readout'));
  const pillsBox = await box(row.locator('.x11-device-pills'));
  ok(stateBox && pillsBox && !overlap(stateBox, pillsBox) && stateBox.x > pillsBox.x,
     '390: device state is not on the right of the buttons');
  await phone.evaluate(() => {
    const m = document.querySelector('.main-body');
    if (m) m.scrollTop = 260;
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
  const widths = await phone.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  ok(widths[0] <= widths[1] + 1, '390: horizontal scroll (' + widths[0] + ' > ' + widths[1] + ')');
  const menu = await box(phone.locator('#mobile-menu-btn'));
  const freeze = await box(phone.locator('#mobile-freeze'));
  const firstCard = await box(phone.locator('[data-device-row]').first());
  ok(!overlap(menu, firstCard) && !overlap(freeze, firstCard),
     '390: the header buttons overlap the device card');
  ok(phoneErrors.length === 0, '390 browser errors: ' + phoneErrors.slice(0, 4).join(' | '));

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

// State variables are in state.js — loaded first.
// Constants and state declarations here are removed to avoid redeclaration.

function deriveGatewayIdentity(hostname) {
  if (!hostname) return { code: '??', color: GW_COLORS[0], label: 'Unknown' };
  let hash = 0;
  for (let i = 0; i < hostname.length; i++) { hash += hostname.charCodeAt(i); }
  const idx = hash % 8;
  const parts = hostname.split(/[-_.\s]+/).filter(p => p.length > 0);
  let code = hostname.substring(0, 2).toUpperCase();
  if (parts.length >= 2) { code = (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase(); }
  return { code, color: GW_COLORS[idx], label: GW_LABELS[idx] };
}

function gwPill(hostname) {
  const id = deriveGatewayIdentity(hostname);
  return '<span class="gw-pill" style="background:' + id.color + ';" title="' + id.label + '">' + id.code + '</span>';
}

function devBadge(g) {
  if (!g || (g.mode || 'prod') !== 'dev') return '';
  return '<span class="dev-pill">DEV</span>';
}

// ── Sound ────────────────────────────────────────────────────────────────
// State variables (_audioCtx, _knownOfflineIps, notifyJIT, etc.) are in state.js.

function updateNotifyUI() {
  const st = document.getElementById('sound-toggle');
  const jt = document.getElementById('notify-jit-toggle');
  const bt = document.getElementById('notify-blocked-toggle');
  const ot = document.getElementById('notify-offline-toggle');
  if (st) { st.textContent = notifySound ? 'On' : 'Off'; st.classList.toggle('on', !!notifySound); }
  if (jt) { jt.textContent = notifyJIT ? 'On' : 'Off'; jt.classList.toggle('on', !!notifyJIT); }
  if (bt) { bt.textContent = notifyBlocked ? 'On' : 'Off'; bt.classList.toggle('on', !!notifyBlocked); }
  if (ot) { ot.textContent = notifyOffline ? 'On' : 'Off'; ot.classList.toggle('on', !!notifyOffline); }
}
function toggleNotifyJIT() { notifyJIT = !notifyJIT; localStorage.setItem('notifyJIT', notifyJIT); updateNotifyUI(); }
function toggleNotifyBlocked() { notifyBlocked = !notifyBlocked; localStorage.setItem('notifyBlocked', notifyBlocked); updateNotifyUI(); }
function toggleNotifyOffline() { notifyOffline = !notifyOffline; localStorage.setItem('notifyOffline', notifyOffline); updateNotifyUI(); }
function toggleSound() { notifySound = !notifySound; soundMuted = !notifySound; localStorage.setItem('notifySound', notifySound); updateNotifyUI(); }

function getAudioCtx() { if (!_audioCtx) { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } return _audioCtx; }
function playJitChime(force) {
  if (!force && !notifySound) return;
  if (!force && soundMuted) return;
  try {
    const ctx = getAudioCtx();
    const now = ctx.currentTime;
    [587.33, 880.00].forEach(function(freq, i) {
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.type = 'sine'; osc.frequency.value = freq;
      const startTime = now + i * 0.15;
      gain.gain.setValueAtTime(0, startTime);
      gain.gain.linearRampToValueAtTime(0.3, startTime + 0.04);
      gain.gain.linearRampToValueAtTime(0.15, startTime + 0.1);
      gain.gain.linearRampToValueAtTime(0, startTime + 0.35);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(startTime); osc.stop(startTime + 0.35);
    });
  } catch(e) {}
}
function toggleMute() { soundMuted = !soundMuted; updateDropdownMuteLabel(); }
function updateDropdownMuteLabel() {
  const btn = document.getElementById('dd-mute-btn');
  btn.innerHTML = soundMuted ? '🔇 Sound Off' : '🔔 Sound On';
}
function testSound() { playJitChime(true); showToast('🔊 Chime played', 'success'); }

// ── Notifications ────────────────────────────────────────────────────────
function requestNotifPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') { _notifPerm = 'granted'; return; }
  if (Notification.permission === 'denied') { _notifPerm = 'denied'; return; }
  document.addEventListener('click', function askOnce() {
    Notification.requestPermission().then(function(perm) { _notifPerm = perm; });
    document.removeEventListener('click', askOnce);
  }, { once: true });
}
function testNotification() {
  playJitChime(false);
  if (_notifPerm === 'granted') {
    new Notification('⚡ JIT Approval Required', { body: '🧪 Test notification — 1 command requires your approval', tag: 'eshu-jit-test', icon: '/static/eshu_logo.png' });
    showToast('🧪 Test notification sent', 'success');
  } else if (_notifPerm === 'denied') { showToast('⚠️ Browser notifications are blocked.', 'error'); }
  else { showToast('⚠️ Click anywhere on the page to prompt notification permission.', 'error'); }
}
function notifyNewJIT(pendingCount) {
  const now = Date.now();
  if (now - lastJitNotifyTime < 5000) return;
  lastJitNotifyTime = now;
  playJitChime(false);
  if (_notifPerm === 'granted') {
    const n = new Notification('⚡ JIT Approval Required', { body: pendingCount === 1 ? '1 command requires your approval' : pendingCount + ' commands require your approval', tag: 'eshu-jit', icon: '/static/eshu_logo.png' });
    n.onclick = function() { window.focus(); switchView('home'); n.close(); };
  }
}

// ── Auth ─────────────────────────────────────────────────────────────────
function hideAllOverlays() {
  document.getElementById('setup-overlay').classList.add('hidden');
  document.getElementById('login-overlay').classList.add('hidden');
  document.getElementById('main-content').style.display = '';
  document.getElementById('main-sidebar').style.display = '';
}
function showDashboard() { hideAllOverlays(); }
function showSetup() {
  document.getElementById('setup-overlay').classList.remove('hidden');
  document.getElementById('login-overlay').classList.add('hidden');
  document.getElementById('main-content').style.display = 'none';
  document.getElementById('main-sidebar').style.display = 'none';
  document.getElementById('setup-password').value = '';
  document.getElementById('setup-password-confirm').value = '';
  document.getElementById('setup-error').classList.add('hidden');
  document.getElementById('setup-password').focus();
}
function showLogin() {
  document.getElementById('setup-overlay').classList.add('hidden');
  document.getElementById('login-overlay').classList.remove('hidden');
  document.getElementById('main-content').style.display = 'none';
  document.getElementById('main-sidebar').style.display = 'none';
  document.getElementById('login-error').classList.add('hidden');
  document.getElementById('login-password').value = '';
  document.getElementById('login-password').focus();
}
async function checkAuth() {
  try {
    const res = await fetch('/api/auth/status'); const data = await res.json();
    _passwordSet = data.password_set; _authChecked = true;
    if (!_passwordSet) { showSetup(); return false; }
    if (!data.authenticated) { showLogin(); return false; }
    showDashboard(); return true;
  } catch(e) { showDashboard(); _authChecked = true; return true; }
}
async function doSetup() {
  const pw = document.getElementById('setup-password').value;
  const pwConfirm = document.getElementById('setup-password-confirm').value;
  const errEl = document.getElementById('setup-error'), btn = document.getElementById('setup-btn');
  if (!pw || pw.length < 4) { errEl.textContent = 'Password must be at least 4 characters.'; errEl.classList.remove('hidden'); return; }
  if (pw !== pwConfirm) { errEl.textContent = 'Passwords do not match.'; errEl.classList.remove('hidden'); return; }
  btn.disabled = true; btn.textContent = 'Setting up...'; errEl.classList.add('hidden');
  try {
    const res = await fetch('/api/auth/set-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) });
    if (res.ok) {
      const loginRes = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) });
      if (loginRes.ok) { showDashboard(); initDashboard(); return; }
    }
    const data = await res.json().catch(function() { return {}; });
    errEl.textContent = data.detail || 'Failed to set password.'; errEl.classList.remove('hidden');
  } catch(e) { errEl.textContent = 'Network error.'; errEl.classList.remove('hidden'); }
  btn.disabled = false; btn.textContent = 'Set Password & Continue';
}
async function doLogin() {
  const pw = document.getElementById('login-password').value;
  const btn = document.getElementById('login-btn');
  btn.disabled = true; btn.textContent = 'Logging in...';
  try {
    const res = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) });
    if (res.ok) { showDashboard(); initDashboard(); }
    else { document.getElementById('login-error').classList.remove('hidden'); }
  } catch(e) { document.getElementById('login-error').classList.remove('hidden'); }
  btn.disabled = false; btn.textContent = 'Log In';
}
async function doLogout() { await fetch('/api/auth/logout', { method: 'POST' }); showLogin(); }
async function authFetch(url, options) {
  options = options || {};
  const res = await fetch(url, options);
  if (res.status === 401) { await checkAuth(); if (!_authChecked || (_passwordSet && document.getElementById('login-overlay').classList.contains('hidden'))) throw new Error('Unauthorized'); return fetch(url, options); }
  return res;
}

// ── Navigation ──────────────────────────────────────────────────────────
async function switchView(target) {
  var winModal = document.getElementById('create-window-modal');
  if (winModal && !winModal.classList.contains('hidden') && typeof customConfirm === 'function') {
    var ok = await customConfirm('You have unsaved changes in the Window editor. Leave anyway?');
    if (!ok) return;
    closeCreateWindowModal();
  }
  VIEWS.forEach(function(v) {
    document.getElementById('view-' + v).classList.toggle('hidden', v !== target);
    document.getElementById('view-' + v).classList.toggle('block', v === target);
    var navBtn = document.getElementById('nav-' + v);
    if (navBtn) navBtn.classList.toggle('active', v === target);
  });
  if (target === 'gateways') { fetchGateways(); fetchEnrollData(); }
  if (target === 'windows') { fetchWindowsTable(); }
  if (target === 'stats') { fetchStatistics(); fetchSuggestions(); }
  if (target === 'controls') { fetchFreezeStatus(); fetchGateways(); fetchPolicies(); fetchPolicyChanges(); }
  if (target === 'fleet') { fetchFleetCommands(); }
  if (target === 'integrations') { fetchIntegrations(); }
  if (target === 'settings') { refreshPasswordUI(); fetchNotifyConfig(); fetchDevTools(); if (_devToolsEnabled) { fetchDevStatus(); fetchDevGateways(); fetchFeatureFlags(); } }
  if (target === 'logs') { fetchAuditLog(); }
}

// ── Settings Dropdown ────────────────────────────────────────────────────
// (sidebar-settings-dropdown removed — dead code cleaned)

// ── Password Management ──────────────────────────────────────────────────
async function refreshPasswordUI() {
  const statusEl = document.getElementById('password-status');
  try {
    const res = await fetch('/api/auth/status'); const data = await res.json();
    statusEl.innerHTML = data.password_set
      ? '<span class="text-success">🔒 Password protection <strong>enabled</strong>.</span>'
      : '<span class="text-warning">⚠️ No password set yet — complete setup to protect the dashboard.</span>';
  } catch(e) {}
}
async function setDashboardPassword() {
  const pw = document.getElementById('new-password').value.trim();
  if (!pw || pw.length < 4) { showToast('Password must be at least 4 characters', 'error'); return; }
  try {
    const res = await authFetch('/api/auth/set-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) });
    if (res.ok) { document.getElementById('new-password').value = ''; refreshPasswordUI(); showToast('✅ Password updated', 'success'); }
    else { const data = await res.json().catch(function() { return {}; }); showToast('❌ ' + (data.detail || 'Failed'), 'error'); }
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

// ── Dev Tools ────────────────────────────────────────────────────────────
let _devToolsEnabled = false;
async function fetchDevTools() {
  try {
    const res = await authFetch('/api/settings/dev-tools');
    if (!res.ok) return;
    const data = await res.json();
    _devToolsEnabled = !!data.enabled;
    renderDevTools();
  } catch(e) {}
}
function renderDevTools() {
  const widget = document.getElementById('dev-deployment-widget');
  const toggle = document.getElementById('dev-tools-toggle');
  if (widget) widget.classList.toggle('hidden', !_devToolsEnabled);
  if (toggle) {
    toggle.textContent = _devToolsEnabled ? 'On' : 'Off';
    toggle.classList.toggle('on', !!_devToolsEnabled);
  }
}
async function toggleDevTools() {
  _devToolsEnabled = !_devToolsEnabled;
  try {
    await authFetch('/api/settings/dev-tools', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({enabled: _devToolsEnabled}) });
    renderDevTools();
    if (_devToolsEnabled) { fetchDevStatus(); fetchDevGateways(); fetchFeatureFlags(); }
    showToast(_devToolsEnabled ? '✅ Development tools enabled' : 'Development tools disabled', 'success');
  } catch(e) {
    _devToolsEnabled = !_devToolsEnabled;
    showToast('❌ Failed to update setting', 'error');
  }
}

// ── Dev Mode Features ────────────────────────────────────────────────
async function fetchFeatureFlags() {
  try {
    const res = await fetch('/api/feature-flags'); const flags = await res.json();
    const container = document.getElementById('feature-flags-list');
    if (!container) return;
    let html = '';
    Object.entries(flags).forEach(function(e) {
      var name = e[0], info = e[1], on = info.enabled, sc = info.scope || 'dev';
      var state = 'off';
      if (on && sc === 'dev') state = 'dev';
      if (on && sc === 'prod') state = 'prod';
      html += '<div class="flag-row">' +
        '<div><span class="text-sm text-main">' + escapeHtml(name) + '</span><p class="text-xs text-muted">' + escapeHtml(info.description) + '</p></div>' +
        '<div class="seg-group">' +
          renderSeg('off', state, name) +
          renderSeg('dev', state, name) +
          renderSeg('prod', state, name) +
        '</div></div>';
    });
    container.innerHTML = html || '<p class="text-muted">No feature flags configured.</p>';
  } catch(e) {}
}

function renderSeg(val, current, name) {
  var active = val === current;
  var label = val === 'off' ? 'Off' : val === 'dev' ? '🧪 Dev' : '🟢 Prod';
  return '<button onclick="setFeatureFlagState(\'' + name + '\',\'' + val + '\')" class="seg-btn' + (active ? ' active ' + val : '') + '">' + label + '</button>';
}

async function setFeatureFlagState(name, state) {
  try {
    await authFetch('/api/feature-flags/' + name + '/state', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({state:state}) });
    fetchFeatureFlags();
    showToast('Feature "' + name + '" → ' + (state === 'off' ? 'Off' : state === 'dev' ? 'Dev' : 'Prod'), 'success');
  } catch(e) { showToast('Failed to update feature state', 'error'); }
}

async function fetchDevGateways() {
  try {
    const res = await fetch('/api/dev-gateways'); _devGateways = await res.json();
    renderDevGatewayPills();
  } catch(e) {}
}

function renderDevGatewayPills() {
  var pills = document.getElementById('dev-gateway-pills');
  if (!pills) return;
  pills.innerHTML = _devGateways.map(function(g) {
    return '<span class="dev-gw-pill">' +
      escapeHtml(g.hostname||g.ip) + ' <span onclick="removeDevGateway(\'' + g.ip + '\')" class="remove" title="Remove from dev">&times;</span></span>';
  }).join('');
}

async function addDevGateway() {
  var input = document.getElementById('dev-gateway-input');
  var q = input.value.trim();
  if (!q) return;
  // Find matching gateway
  if (!_allGateways.length) {
    var r = await fetch('/api/gateways'); _allGateways = await r.json();
  }
  var match = _allGateways.find(function(g) { return g.ip === q || (g.hostname && g.hostname.toLowerCase().includes(q.toLowerCase())); });
  if (!match) { showToast('Gateway not found: ' + q, 'error'); return; }
  try {
    await authFetch('/api/gateways/' + match.ip + '/mode', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:'dev'}) });
    input.value = '';
    fetchDevGateways();
    showToast(match.hostname + ' set to dev mode', 'success');
  } catch(e) { showToast('Failed to set dev mode', 'error'); }
}

async function removeDevGateway(ip) {
  try {
    await authFetch('/api/gateways/' + ip + '/mode', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:'prod'}) });
    fetchDevGateways();
    showToast('Gateway removed from dev mode', 'success');
  } catch(e) { showToast('Failed', 'error'); }
}

async function pushDevGateways() {
  if (!_devGateways.length) { showToast('No dev gateways selected', 'error'); return; }
  var btn = document.getElementById('push-dev-btn');
  btn.disabled = true; btn.textContent = 'Pushing...';
  try {
    var r = await authFetch('/api/dev-gateways/push', { method:'POST' });
    var data = await r.json();
    if (data.stale_gateways && data.stale_gateways.length > 0) {
      showToast('⚠️ ' + data.stale_gateways.length + ' gateway(s) are on old versions: ' + data.stale_gateways.join(', ') + '. Run 🔄 Update Gateways first.', 'error');
    } else {
      showToast('✅ Dev update triggered for ' + (data.dev_gateway_count||0) + ' gateway(s)' + (data.dev_gateway_names ? ': ' + data.dev_gateway_names.join(', ') : ''), 'success');
    }
  } catch(e) { showToast('Failed to push', 'error'); }
  btn.disabled = false; btn.textContent = '🚀 Push to Dev Gateways';
}

function onDevGatewayInput() {
  var input = document.getElementById('dev-gateway-input');
  var q = input.value.trim();
  var results = document.getElementById('dev-gateway-results');
  if (!q) { results.innerHTML = ''; return; }
  var matches = _allGateways.filter(function(g) {
    return g.ip === q || (g.hostname && g.hostname.toLowerCase().includes(q.toLowerCase()));
  }).slice(0, 5);
  results.innerHTML = matches.map(function(g) {
    return '<div onclick="document.getElementById(\'dev-gateway-input\').value=\'' + g.hostname + '\';addDevGateway()" class="search-result">' +
      gwPill(g.hostname) + ' ' + escapeHtml(g.hostname) + ' <span class="text-muted">(' + g.ip + ')</span></div>';
  }).join('');
}
// ── Approved Windows ────────────────────────────────────────────────
// State variables (let/const) are in state.js
const DAY_NAMES = ['Mo','Tu','We','Th','Fr','Sa','Su'];
const DAY_BITS  = [1, 2, 4, 8, 16, 32, 64];

function winError(msg) {
  var el = document.getElementById('win-save-error');
  el.textContent = msg; el.classList.remove('hidden');
}

function winClearError() {
  document.getElementById('win-save-error').classList.add('hidden');
}

function resetWinForm() {
  _winEditId = null; _winSource = 'new'; _winType = 'recurring'; _winDays = 0;
  _winNeverExpire = true; _winHour = 0; _winMin = 0; _winMatchType = 'exact'; _selectedJIT = [];
  document.getElementById('win-modal-title').textContent = '🪟 Create Approved Window';
  document.getElementById('save-window-btn').textContent = 'Create Window';
  document.getElementById('win-command').value = '';
  document.getElementById('win-label').value = '';
  document.getElementById('win-max-exec').value = '1';
  document.getElementById('win-max-exec-single').value = '1';
  document.getElementById('win-single-start-date').value = '';
  document.getElementById('win-single-start-time').value = '';
  document.getElementById('win-single-expiry-date').value = '';
  document.getElementById('win-single-expiry-time').value = '';
  document.getElementById('win-expiry-date').value = '';
  document.getElementById('win-expiry-date').style.display = 'none';
  document.getElementById('win-never-expire').checked = true;
  document.getElementById('win-token-display').classList.add('hidden');
  winClearError();
  document.getElementById('win-summary-bar').classList.add('hidden-section');
  document.getElementById('win-summary-text').textContent = '';
  // Disable dynamic sections
  document.getElementById('win-src-section').classList.add('win-disabled');
  document.getElementById('win-label-section').classList.add('win-disabled');
  var lh = document.getElementById('win-label-hint'); if(lh) lh.textContent = 'Select a gateway and type a command first';
  var li = document.getElementById('win-label'); if(li) li.disabled = true;
  // Show recurring, hide single-use fields
  document.getElementById('win-recurring-fields').classList.remove('hidden-section');
  document.getElementById('win-single-fields').classList.add('hidden-section');
  // Command source: New Command is default, JIT hidden until gateway selected
  document.getElementById('win-src-new').classList.add('selected');
  document.getElementById('win-src-jit').classList.remove('selected');
  document.getElementById('win-cmd-input-wrap').classList.remove('hidden-section');
  document.getElementById('win-jit-list-wrap').classList.add('hidden-section');
  // Type: Recurring default
  document.getElementById('win-type-recurring').classList.add('selected');
  document.getElementById('win-type-single').classList.remove('selected');
  setWinMatch('exact');
  // Reset hidden gateway value
  document.getElementById('win-gateway').value = '';
  var dd = document.getElementById('gw-dropdown-display');
  dd.textContent = 'Select a gateway…';
  dd.classList.remove('text-main');
  dd.classList.add('text-muted');
  renderDayCircles();
  buildTimeScrolls();
}

function readSingleUseSchedule() {
  var d = document.getElementById('win-single-start-date').value;
  var t = document.getElementById('win-single-start-time').value;
  if (!d || !t) throw new Error('Set a start date and time for the single-use window');
  return { start: Math.floor(new Date(d + 'T' + t).getTime() / 1000), end: 4102444800 };
}

// ── Windows Table (redesigned) ────────────────────────────────────────

function formatWinSchedule(w) {
  // Coerce because legacy migrations may have created these columns as TEXT.
  var dow = Number(w.days_of_week) || 0;
  var et = Number(w.execution_time) || 0;
  var ws = Number(w.window_start) || 0;
  var exp = w.expires_at ? Number(w.expires_at) : 0;
  var sched;
  if (!dow && !et) {
    if (ws) {
      var d = new Date(ws * 1000);
      sched = 'Once: ' + d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
    } else {
      sched = 'N/A';
    }
  } else {
    var dayNames = [];
    for (var i = 0; i < 7; i++) { if (dow & DAY_BITS[i]) dayNames.push(DAY_NAMES[i]); }
    var days = dayNames.length ? dayNames.join(',') : 'Every day';
    var h = String(Math.floor(et / 60)).padStart(2,'0');
    var m = String(et % 60).padStart(2,'0');
    sched = days + ' ' + h + ':' + m + ' UTC';
  }
  if (exp) {
    var ed = new Date(exp * 1000);
    var expStr = ed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
    var past = Math.floor(Date.now() / 1000) > exp;
    var color = past ? 'var(--brand-red)' : 'var(--status-warning)';
    sched += '<br><span class="win-expiry" style="color:' + color + ';">Expires: ' + expStr + (past ? ' (expired)' : '') + '</span>';
  }
  return sched;
}

function computeNextRun(daysOfWeek, execTimeMin) {
  // Stored values are UTC; compute next occurrence as a UTC Date.
  var now = new Date();
  var utcDowIdx = (now.getUTCDay() + 6) % 7; // 0=Mon..6=Sun
  var nowMin = now.getUTCHours() * 60 + now.getUTCMinutes();
  var dow = Number(daysOfWeek) || 0;
  var et = Number(execTimeMin) || 0;
  for (var offset = 0; offset <= 7; offset++) {
    var idx = (utcDowIdx + offset) % 7;
    var bit = DAY_BITS[idx];
    var matches = dow === 0 || (dow & bit);
    var afterNow = offset === 0 ? et > nowMin : true;
    if (matches && afterNow) {
      var cand = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + offset, 0, et, 0, 0));
      return cand;
    }
  }
  return null;
}

function formatNextRun(date) {
  if (!date || isNaN(date.getTime())) return '';
  var now = new Date();
  var totalMins = Math.max(0, Math.ceil((date - now) / 60000));
  var hours = Math.floor(totalMins / 60);
  var mins = totalMins % 60;
  var countdown = hours > 0 ? hours + 'h ' + mins + 'm' : mins + 'm';
  var day = date.toLocaleDateString(undefined, { weekday: 'short' });
  var time = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
  return 'Next: ' + day + ' ' + time + ' (in ' + countdown + ')';
}

async function fetchWindowsTable() {
  try {
    var r = await fetch('/api/approved-windows'); var wins = await r.json();
    var gwMap = {};
    try { var gr = await fetch('/api/gateways'); (await gr.json()).forEach(function(g) { gwMap[g.ip] = g; }); } catch(e) {}
    var tbody = document.getElementById('windows-table-body');
    if (!tbody) return;
    if (!wins.length) { tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-3 text-muted">No approved windows created yet.</td></tr>'; return; }
    var tableNow = Math.floor(Date.now() / 1000);
    tbody.innerHTML = wins.map(function(w) {
      var stat = w.enabled ? '<span class="badge badge-approved cursor-pointer" onclick="toggleWindow(' + w.id + ',true)" title="Click to disable">🟢 On</span>' :
        (w.status === 'pending_review' ? '<span class="badge badge-pending">⏳ Review</span>' :
         (w.status === 'denied' ? '<span class="badge badge-window-rejected">🚫 Denied</span>' :
          '<span class="badge badge-expired cursor-pointer" onclick="toggleWindow(' + w.id + ',false)" title="Click to enable">⏸️ Off</span>'));
      var gw = gwMap[w.target_ip] || {};
      var originBadge = (w.origin === 'ai')
        ? '<span class="origin-ai" title="Inbound — AI requested">🤖 AI</span> '
        : '<span class="origin-human" title="Outbound — operator created">👤 Human</span> ';
      var labelHtml = originBadge + (w.label ? '<span class="text-main font-medium">' + escapeHtml(w.label) + '</span><br>' : '') +
        '<code class="text-xs font-mono text-muted" title="' + (w.token||'') + '">' + (w.token||'').substring(0, 8) + '…</code>' +
        ' <span onclick="copyToClipboard(\'' + (w.token||'') + '\')" class="cursor-pointer text-xs opacity-40 hover:opacity-100" title="Copy token">📋</span>';
      var execInfo = w.execution_count + (w.max_executions > 0 ? '/' + w.max_executions : '/∞');
      var lu = Number(w.last_used_at) || 0;
      if (lu > 0) execInfo += '<br><span class="text-xs text-muted">last ' + formatAgo(tableNow - lu) + '</span>';
      var dowNum = Number(w.days_of_week) || 0, etNum = Number(w.execution_time) || 0;
      var wsNum = Number(w.window_start) || 0;
      var expTs = w.expires_at ? Number(w.expires_at) : 0;
      var isExpired = expTs > 0 && expTs * 1000 < Date.now();
      var nextRunStr = isExpired ? '' : (dowNum || etNum
        ? formatNextRun(computeNextRun(dowNum, etNum))
        : (wsNum && wsNum * 1000 > Date.now() ? formatNextRun(new Date(wsNum * 1000)) : ''));
      return '<tr>' +
        '<td class="text-xs text-muted whitespace-nowrap" title="Numeric window ID — refer to it when coordinating with the agent">#' + w.id + '</td>' +
        '<td>' + stat + '</td>' +
        '<td>' + labelHtml + '</td>' +
        '<td class="text-xs">' + gwPill(gw.hostname||w.target_ip) + ' ' + escapeHtml(gw.hostname||w.target_ip) + '</td>' +
        '<td><code class="win-cmd-code" title="' + escapeHtml(w.command) + '">' + escapeHtml(w.match_type === 'prefix' ? w.command + '*' : w.command) + '</code></td>' +
        '<td class="text-xs text-muted">' + formatWinSchedule(w) + (nextRunStr ? '<br>' + nextRunStr : '') + '</td>' +
        '<td class="text-center text-xs text-muted">' + execInfo + '</td>' +
        '<td class="text-right"><div class="flex items-center justify-end gap-1">' +
          (w.status === 'pending_review'
            ? '<button onclick="approveWindowReq(' + w.id + ')" class="btn btn-approve btn-xs">✅ Approve</button>' +
              '<button onclick="denyWindowReq(' + w.id + ')" class="btn btn-deny btn-xs">❌ Deny</button>'
            : '<button onclick="openEditWindowModal(' + w.id + ')" class="btn btn-muted btn-xs" title="Edit">✏️</button>' +
              '<button onclick="showWindowHistory(' + w.id + ')" class="btn btn-muted btn-xs" title="Usage history">📜</button>' +
              '<button onclick="deleteWindow(' + w.id + ')" class="btn btn-deny btn-xs" title="Delete">🗑</button>') +
        '</div></td>' +
        '</tr>';
    }).join('');
  } catch(e) {}
}

// ── Gateway Dropdown ────────────────────────────────────────────────

function toggleGwDropdown() {
  _gwDropdownOpen = !_gwDropdownOpen;
  var menu = document.getElementById('gw-dropdown-menu');
  var caret = document.getElementById('gw-dropdown-caret');
  var trigger = document.getElementById('gw-dropdown-trigger');
  if (_gwDropdownOpen) {
    menu.classList.add('show');
    caret.classList.add('open');
    trigger.classList.add('open');
  } else {
    menu.classList.remove('show');
    caret.classList.remove('open');
    trigger.classList.remove('open');
  }
}

function selectGateway(ip) {
  var gw = _winGateways.find(function(g) { return g.ip === ip; });
  if (!gw) return;
  document.getElementById('win-gateway').value = ip;
  var display = document.getElementById('gw-dropdown-display');
  display.innerHTML = gwPill(gw.hostname||gw.ip) + ' ' + escapeHtml(gw.hostname||gw.ip) + ' <span class="text-xs text-muted">(' + ip + ')</span>';
  display.classList.remove('text-muted');
  display.classList.add('text-main');
  _gwDropdownOpen = false;
  document.getElementById('gw-dropdown-menu').classList.remove('show');
  document.getElementById('gw-dropdown-caret').classList.remove('open');
  document.getElementById('gw-dropdown-trigger').classList.remove('open');
  // Trigger the change
  var evt = document.createEvent('HTMLEvents');
  evt.initEvent('change', false, true);
  document.getElementById('win-gateway').dispatchEvent(evt);
}

// Close dropdown when clicking outside
document.addEventListener('click', function(e) {
  var dd = document.getElementById('gw-dropdown');
  if (dd && !dd.contains(e.target)) {
    _gwDropdownOpen = false;
    document.getElementById('gw-dropdown-menu').classList.remove('show');
    document.getElementById('gw-dropdown-caret').classList.remove('open');
    document.getElementById('gw-dropdown-trigger').classList.remove('open');
  }
});

// ── Modal: Gateway change → reveal source ────────────────────────────

async function onWinGatewayChange() {
  var ip = document.getElementById('win-gateway').value;
  var srcEl = document.getElementById('win-src-section');
  if (!ip) { srcEl.classList.add('win-disabled'); return; }
  srcEl.classList.remove('win-disabled');
  // Load JIT data for this gateway
  try {
    var r = await fetch('/api/approved-windows/recent-jit?hours=6&ip=' + encodeURIComponent(ip));
    _recentJITData = await r.json();
  } catch(e) { _recentJITData = []; }
  var list = document.getElementById('recent-jit-list');
  if (!_recentJITData.length) { list.innerHTML = '<p class="text-xs px-2 py-1 text-muted">No recent JIT approvals for this gateway.</p>'; }
  else {
    list.innerHTML = _recentJITData.slice(0, 20).map(function(j, i) {
      return '<div class="search-result flex items-center gap-2 text-xs" onclick="selectJIT(' + i + ')" title="Click to fill command">' +
        '<span class="text-xs">↩</span>' +
        gwPill(j.hostname||'') + ' ' + escapeHtml(j.hostname||j.target_ip) +
        ' <code class="truncate text-muted" style="max-width:260px;" title="' + escapeHtml(j.command) + '">' + escapeHtml(j.command.substring(0, 60)) + '</code>' +
        ' <span class="text-xs text-muted">' + formatAgo(Date.now()/1000 - j.created_at) + '</span>' +
      '</div>';
    }).join('');
  }
  updateWinSummary();
}

// ── Modal: open / close ──────────────────────────────────────────────

async function openCreateWindowModal() {
  document.getElementById('create-window-modal').classList.remove('hidden');
  resetWinForm();
  try {
    var r = await fetch('/api/gateways'); _winGateways = await r.json();
    var menu = document.getElementById('gw-dropdown-menu');
    if (!_winGateways.length) { menu.innerHTML = '<div class="p-3 text-xs text-muted">No gateways registered.</div>'; }
    else {
      menu.innerHTML = _winGateways.map(function(g) {
        return '<div class="gw-dropdown-item" onclick="selectGateway(\'' + g.ip + '\')">' + gwPill(g.hostname||g.ip) + ' ' + escapeHtml(g.hostname||g.ip) + ' <span class="gw-dropdown-ip">' + g.ip + '</span></div>';
      }).join('');
    }
  } catch(e) {}
}

async function openEditWindowModal(id) {
  _winEditId = id;
  document.getElementById('win-modal-title').textContent = '✏️ Edit Approved Window';
  document.getElementById('save-window-btn').textContent = 'Save Changes';
  winClearError();
  document.getElementById('win-token-display').classList.add('hidden');
  document.getElementById('create-window-modal').classList.remove('hidden');

  try {
    var gr = await fetch('/api/gateways'); _winGateways = await gr.json();
    var menu = document.getElementById('gw-dropdown-menu');
    menu.innerHTML = _winGateways.map(function(g) {
      return '<div class="gw-dropdown-item" onclick="selectGateway(\'' + g.ip + '\')">' + gwPill(g.hostname||g.ip) + ' ' + escapeHtml(g.hostname||g.ip) + ' <span class="gw-dropdown-ip">' + g.ip + '</span></div>';
    }).join('');

    var r = await fetch('/api/approved-windows/' + id);
    if (!r.ok) throw new Error('not found');
    var w = await r.json();

    // Coerce numeric fields because legacy migrations may have stored them as TEXT.
    var dowNum = Number(w.days_of_week) || 0;
    var etNum = Number(w.execution_time) || 0;
    var wsNum = Number(w.window_start) || 0;
    var weNum = Number(w.window_end) || 0;
    var expNum = w.expires_at ? Number(w.expires_at) : 0;

    // Determine type: if it has schedule data it's recurring, otherwise single-use
    var isRecurring = !!(dowNum || etNum);
    _winType = isRecurring ? 'recurring' : 'single';
    setWinType(_winType);

    // Set the gateway
    var gw = _winGateways.find(function(g) { return g.ip === w.target_ip; });
    if (gw) selectGateway(gw.ip);
    else { document.getElementById('win-gateway').value = w.target_ip; }

    setWinSource('new');
    document.getElementById('win-command').value = w.command || '';
    document.getElementById('win-label').value = w.label || '';
    _winMatchType = w.match_type || 'exact';
    setWinMatch(_winMatchType);

    // Enable sections
    document.getElementById('win-src-section').classList.remove('win-disabled');
    document.getElementById('win-label-section').classList.remove('win-disabled');
    var lh2 = document.getElementById('win-label-hint'); if(lh2) lh2.textContent = '';
    var li2 = document.getElementById('win-label'); if(li2) li2.disabled = false;

    _winDays = dowNum;
    _winHour = Math.floor(etNum / 60);
    _winMin = etNum % 60;

    var maxExec = Math.min(Math.max(w.max_executions || 1, 1), 5);
    document.getElementById('win-max-exec').value = maxExec;
    document.getElementById('win-max-exec-single').value = maxExec;

    // Single-use start date/time
    if (wsNum) {
      var sd = new Date(wsNum * 1000);
      document.getElementById('win-single-start-date').value =
        sd.getFullYear() + '-' + String(sd.getMonth()+1).padStart(2,'0') + '-' + String(sd.getDate()).padStart(2,'0');
      document.getElementById('win-single-start-time').value =
        String(sd.getHours()).padStart(2,'0') + ':' + String(sd.getMinutes()).padStart(2,'0');
    } else {
      document.getElementById('win-single-start-date').value = '';
      document.getElementById('win-single-start-time').value = '';
    }

    if (expNum) {
      _winNeverExpire = false;
      document.getElementById('win-never-expire').checked = false;
      document.getElementById('win-expiry-date').style.display = 'block';
      var ed = new Date(expNum * 1000).toISOString().slice(0, 10);
      document.getElementById('win-expiry-date').value = ed;
      if (!isRecurring) {
        var edt = new Date(expNum * 1000);
        document.getElementById('win-single-expiry-date').value =
          edt.getFullYear() + '-' + String(edt.getMonth()+1).padStart(2,'0') + '-' + String(edt.getDate()).padStart(2,'0');
        document.getElementById('win-single-expiry-time').value =
          String(edt.getHours()).padStart(2,'0') + ':' + String(edt.getMinutes()).padStart(2,'0');
      }
    } else {
      _winNeverExpire = true;
      document.getElementById('win-never-expire').checked = true;
      document.getElementById('win-expiry-date').style.display = 'none';
      document.getElementById('win-expiry-date').value = '';
    }

    renderDayCircles();
    buildTimeScrolls();
    setTimeScroll(_winHour, _winMin);
    updateWinSummary();
    document.getElementById('win-summary-bar').classList.remove('hidden-section');
  } catch(e) { winError('Failed to load window'); }
}

function closeCreateWindowModal() {
  document.getElementById('create-window-modal').classList.add('hidden');
  _winEditId = null;
  // Close gw dropdown if open
  _gwDropdownOpen = false;
  var menu = document.getElementById('gw-dropdown-menu');
  if (menu) menu.classList.remove('show');
}

// ── Source toggle ────────────────────────────────────────────────────

function setWinSource(src) {
  _winSource = src;
  document.getElementById('win-src-new').classList.toggle('selected', src === 'new');
  document.getElementById('win-src-jit').classList.toggle('selected', src === 'jit');
  document.getElementById('win-cmd-input-wrap').classList.toggle('hidden-section', src !== 'new');
  document.getElementById('win-jit-list-wrap').classList.toggle('hidden-section', src !== 'jit');
  if (src === 'new') {
    checkCommandReady();
  } else {
    // JIT mode: label disabled until a JIT is selected
    document.getElementById('win-label-section').classList.add('win-disabled');
    var lhs = document.getElementById('win-label-hint'); if(lhs) lhs.textContent = 'Select a JIT command from the list above';
    var lis = document.getElementById('win-label'); if(lis) lis.disabled = true;
  }
  updateWinSummary();
}

// ── Match type toggle ────────────────────────────────────────────────

function setWinMatch(type) {
  _winMatchType = type;
  var track = document.getElementById('win-match-toggle');
  var labelL = document.getElementById('match-label-exact');
  var labelR = document.getElementById('match-label-prefix');
  if (type === 'exact') {
    track.classList.add('on');
    if(labelL) labelL.style.color = 'var(--status-success)';
    if(labelR) labelR.style.color = 'var(--text-muted)';
  } else {
    track.classList.remove('on');
    if(labelL) labelL.style.color = 'var(--text-muted)';
    if(labelR) labelR.style.color = 'var(--status-success)';
  }
  updateWinSummary();
}

// ── Type toggle ──────────────────────────────────────────────────────

function setWinType(type) {
  _winType = type;
  document.getElementById('win-type-recurring').classList.toggle('selected', type === 'recurring');
  document.getElementById('win-type-single').classList.toggle('selected', type === 'single');
  // Recurring shows schedule+expiry, single-use shows only max exec
  document.getElementById('win-recurring-fields').classList.toggle('hidden-section', type !== 'recurring');
  document.getElementById('win-single-fields').classList.toggle('hidden-section', type !== 'single');
  updateWinSummary();
}

function checkCommandReady() {
  var ip = document.getElementById('win-gateway').value;
  var cmd = document.getElementById('win-command').value.trim();
  var hasCmd = cmd.length > 0;
  var ready = !!(ip && hasCmd);
  var labelEl = document.getElementById('win-label-section');
  var labelInput = document.getElementById('win-label');
  if (ready) {
    labelEl.classList.remove('win-disabled');
    var lhc = document.getElementById('win-label-hint'); if(lhc) lhc.textContent = '';
    if (labelInput) labelInput.disabled = false;
  } else {
    labelEl.classList.add('win-disabled');
    var lhc2 = document.getElementById('win-label-hint'); if(lhc2) lhc2.textContent = _winSource === 'jit' ? 'Select a JIT command from the list above' : 'Type a command above';
    if (labelInput) labelInput.disabled = true;
  }
}

// ── Match toggle ────────────────────────────────────────────────────

function toggleWinMatch() {
  setWinMatch(_winMatchType === 'exact' ? 'prefix' : 'exact');
}

// ── JIT selection (click-to-fill, single command) ────────────────────

function selectJIT(idx) {
  var item = _recentJITData[idx];
  if (!item) return;
  document.getElementById('win-command').value = item.command;
  // Switch to Command view so the user can see what was filled
  setWinSource('new');
  checkCommandReady();
  updateWinSummary();
}

// ── Test command ─────────────────────────────────────────────────────

async function testWinCommand() {
  var cmd = document.getElementById('win-command').value.trim();
  if (!cmd) return;
  var rd = document.getElementById('win-test-result'); rd.classList.remove('hidden');
  rd.innerHTML = '<span class="text-xs text-muted">Testing...</span>';
  try {
    var r = await fetch('/api/policies/test?command=' + encodeURIComponent(cmd)); var data = await r.json();
    var bg, border, text, icon, desc;
    if (data.action === 'blocked') { bg='rgba(251,146,60,0.1)'; border='rgba(251,146,60,0.3)'; text='#fb923c'; icon='🛡️'; desc='This command is BLOCKED by policy — cannot create window.'; }
    else if (data.action === 'auto_approved') { bg='rgba(74,222,128,0.1)'; border='rgba(74,222,128,0.3)'; text='var(--status-success)'; icon='✅'; desc='Already auto-approved — no window needed.'; }
    else { bg='rgba(96,165,250,0.1)'; border='rgba(96,165,250,0.3)'; text='var(--status-info)'; icon='⏳'; desc='Would require JIT — a window will auto-approve this.'; }
    rd.innerHTML = '<div class="result-box text-xs" style="background:' + bg + ';color:' + text + ';border-color:' + border + ';">' + icon + ' ' + desc + '</div>';
  } catch(e) { rd.innerHTML = '<span class="text-xs text-muted">Test failed</span>'; }
}

// ── Time scroll picker ───────────────────────────────────────────────

function buildTimeScrolls() {
  var hEl = document.getElementById('time-scroll-hours');
  var mEl = document.getElementById('time-scroll-mins');
  var hHtml = '', mHtml = '';
  for (var i = 0; i < 24; i++) {
    var v = String(i).padStart(2,'0');
    hHtml += '<div class="time-digit' + (i === _winHour ? ' active' : '') + '" onclick="setTimeScroll(' + i + ',' + _winMin + ')">' + v + '</div>';
  }
  for (var i = 0; i < 60; i++) {
    var v = String(i).padStart(2,'0');
    mHtml += '<div class="time-digit' + (i === _winMin ? ' active' : '') + '" onclick="setTimeScroll(' + _winHour + ',' + i + ')">' + v + '</div>';
  }
  hEl.innerHTML = hHtml; mEl.innerHTML = mHtml;
  hEl.scrollTop = _winHour * 40 - 30;
  mEl.scrollTop = _winMin * 40 - 30;
}

function setTimeScroll(h, m) {
  _winHour = h; _winMin = m;
  buildTimeScrolls();
  updateWinSummary();
}

function shiftTimeScroll(which, dir) {
  if (which === 'hours') {
    _winHour = (_winHour + dir + 24) % 24;
  } else {
    _winMin = (_winMin + dir + 60) % 60;
  }
  buildTimeScrolls();
  updateWinSummary();
}

function onTimeScroll(which) {
  var el = document.getElementById('time-scroll-' + which);
  var idx = Math.round((el.scrollTop + 20) / 40);
  if (which === 'hours') { _winHour = Math.max(0, Math.min(23, idx)); }
  else { _winMin = Math.max(0, Math.min(59, idx)); }
  buildTimeScrolls();
  updateWinSummary();
}

// ── Day circles ──────────────────────────────────────────────────────

function renderDayCircles() {
  var container = document.getElementById('day-circles-container');
  var html = '';
  for (var i = 0; i < 7; i++) {
    var on = _winDays & DAY_BITS[i];
    html += '<div class="day-circle' + (on ? ' active' : '') + '" onclick="toggleDay(' + DAY_BITS[i] + ')">' + DAY_NAMES[i] + '</div>';
  }
  container.innerHTML = html;
}

function toggleDay(bit) {
  _winDays ^= bit;
  renderDayCircles();
  updateWinSummary();
}

// ── Expiry checkbox ─────────────────────────────────────────────────

function onWinNeverExpireChange() {
  var cb = document.getElementById('win-never-expire');
  _winNeverExpire = cb.checked;
  var dateEl = document.getElementById('win-expiry-date');
  if (_winNeverExpire) {
    dateEl.style.display = 'none';
    dateEl.value = '';
  } else {
    dateEl.style.display = 'block';
  }
  updateWinSummary();
}

// ── Max exec clamping ───────────────────────────────────────────

function onMaxExecInput() {
  var elR = document.getElementById('win-max-exec');
  var elS = document.getElementById('win-max-exec-single');
  if (elR) {
    var v = parseInt(elR.value) || 1;
    if (v < 1) elR.value = 1;
    if (v > 5) elR.value = 5;
  }
  if (elS) {
    var v2 = parseInt(elS.value) || 1;
    if (v2 < 1) elS.value = 1;
    if (v2 > 5) elS.value = 5;
  }
}

// ── Summary bar ──────────────────────────────────────────────────────

function updateWinSummary() {
  var ip = document.getElementById('win-gateway').value;
  var display = document.getElementById('gw-dropdown-display');
  var gwName = display ? display.textContent.trim() : '';
  var cmd = document.getElementById('win-command').value.trim();
  var summaryEl = document.getElementById('win-summary-bar');
  var textEl = document.getElementById('win-summary-text');

  if (!ip) { summaryEl.classList.add('hidden-section'); return; }
  summaryEl.classList.remove('hidden-section');

  var parts = [];
  var nextRunEl = document.getElementById('win-next-run');
  if (_winType === 'single') {
    var maxExec = parseInt(document.getElementById('win-max-exec-single').value) || 1;
    parts.push('⚡ Single-use');
    parts.push(maxExec + ' execution' + (maxExec !== 1 ? 's' : ''));
    parts.push('auto-disables when exhausted');
    if (nextRunEl) nextRunEl.textContent = '';
    try {
      var sched = readSingleUseSchedule();
      if (sched.start) {
        var sdate = new Date(sched.start * 1000);
        var sstr = sdate.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
        parts.push('starts ' + sstr);
        if (sched.start * 1000 > Date.now()) {
          var countdown = formatNextRun(sdate);
          if (countdown) parts.push(countdown);
        }
      }
    } catch(e) { /* partial date/time typed; ignore for summary */ }
    // Read optional expiry (date + time, local)
    var seDateVal = document.getElementById('win-single-expiry-date').value;
    var seTimeVal = document.getElementById('win-single-expiry-time').value;
    if (seDateVal) {
      var expLocal = new Date(seDateVal + (seTimeVal ? 'T' + seTimeVal : 'T00:00'));
      parts.push('expires ' + expLocal.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }));
    }
  } else {
    var hh = String(_winHour).padStart(2,'0');
    var mm = String(_winMin).padStart(2,'0');
    var dayList = [];
    for (var i = 0; i < 7; i++) { if (_winDays & DAY_BITS[i]) dayList.push(DAY_NAMES[i]); }
    var dayStr = dayList.length ? dayList.join(', ') : 'Every day';
    parts.push('🔄 ' + dayStr + ' at ' + hh + ':' + mm + ' UTC');
    parts.push(_winNeverExpire ? 'never expires' : (document.getElementById('win-expiry-date').value || 'expiry set'));
    var maxExecR = parseInt(document.getElementById('win-max-exec').value) || 1;
    parts.push(maxExecR + ' max exec' + (maxExecR !== 1 ? 's' : ''));
    if (nextRunEl) nextRunEl.textContent = formatNextRun(computeNextRun(_winDays, _winHour * 60 + _winMin));
  }
  if (gwName) parts.push('on ' + gwName.replace(/\s+\([^)]+\)$/, ''));

  textEl.textContent = parts.filter(Boolean).join(' · ');
}

// ── Save (create or edit) ────────────────────────────────────────────

async function saveWindow() {
  winClearError();
  var gw = document.getElementById('win-gateway').value;
  var cmd = document.getElementById('win-command').value.trim();
  if (!gw) { winError('Select a target gateway'); return; }
  if (!cmd) { winError('Enter or select a command'); return; }

  if (_winType === 'recurring' && !_winNeverExpire && !document.getElementById('win-expiry-date').value) {
    winError('Set an expiration date or uncheck Never expire'); return;
  }

  var label = document.getElementById('win-label').value.trim();
  var body = { target_ip: gw, command: cmd, label: label, match_type: _winMatchType, window_start: 0, window_end: 0 };

  // max_executions: use the appropriate field per type
  if (_winType === 'single') {
    var sched = readSingleUseSchedule();
    body.window_start = sched.start;
    body.window_end = sched.end;
    var maxExecSingle = parseInt(document.getElementById('win-max-exec-single').value) || 1;
    body.max_executions = Math.min(Math.max(maxExecSingle, 1), 5);
    body.days_of_week = 0;
    body.execution_time = 0;
    var seDate = document.getElementById('win-single-expiry-date').value;
    var seTime = document.getElementById('win-single-expiry-time').value;
    body.expires_at = seDate ? Math.floor(new Date(seDate + (seTime ? 'T' + seTime : 'T00:00')).getTime() / 1000) : null;
  } else {
    var maxExecRecur = parseInt(document.getElementById('win-max-exec').value) || 1;
    body.max_executions = Math.min(Math.max(maxExecRecur, 1), 5);
    body.days_of_week = _winDays;
    body.execution_time = _winHour * 60 + _winMin;
    if (_winNeverExpire) {
      body.expires_at = null;
    } else {
      var dateStr = document.getElementById('win-expiry-date').value;
      body.expires_at = dateStr ? Math.floor(new Date(dateStr + 'T00:00:00Z').getTime() / 1000) : null;
    }
  }

  var isEdit = _winEditId !== null;
  var url = isEdit ? '/api/approved-windows/' + _winEditId : '/api/approved-windows';
  var method = isEdit ? 'PUT' : 'POST';
  if (isEdit) {
    body = { command: cmd, label: label, match_type: _winMatchType };
    if (_winType === 'single') {
      var sched2 = readSingleUseSchedule();
      body.window_start = sched2.start;
      body.window_end = sched2.end;
      var msEdit = parseInt(document.getElementById('win-max-exec-single').value) || 1;
      body.max_executions = Math.min(Math.max(msEdit, 1), 5);
      body.days_of_week = 0;
      body.execution_time = 0;
      var se2Date = document.getElementById('win-single-expiry-date').value;
      var se2Time = document.getElementById('win-single-expiry-time').value;
      body.expires_at = se2Date ? Math.floor(new Date(se2Date + (se2Time ? 'T' + se2Time : 'T00:00')).getTime() / 1000) : null;
    } else {
      var mrEdit = parseInt(document.getElementById('win-max-exec').value) || 1;
      body.max_executions = Math.min(Math.max(mrEdit, 1), 5);
      body.days_of_week = _winDays;
      body.execution_time = _winHour * 60 + _winMin;
      body.expires_at = _winNeverExpire ? null : (document.getElementById('win-expiry-date').value ? Math.floor(new Date(document.getElementById('win-expiry-date').value + 'T00:00:00Z').getTime() / 1000) : null);
    }
  }

  try {
    var r = await authFetch(url, { method: method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!r.ok) { var d = await r.json().catch(function(){return{};}); var msg = typeof d.detail === 'string' ? d.detail : (typeof d.detail === 'object' ? JSON.stringify(d.detail) : 'Save failed'); throw new Error(msg); }
    var data = await r.json();
    if (!isEdit) {
      document.getElementById('win-token-display').classList.remove('hidden');
      document.getElementById('win-token-value').textContent = data.token;
      document.getElementById('win-token-usage').textContent = 'ESHU_WINDOW_TOKEN=' + data.token + ' ' + cmd;
    }
    closeCreateWindowModal();
    showToast(isEdit ? '✅ Window updated' : '✅ Window created', 'success');
    fetchWindowsTable();
    if (!isEdit) _winEditId = data.id;
    document.getElementById('save-window-btn').textContent = 'Save Changes';
    document.getElementById('win-modal-title').textContent = '✏️ Edit Approved Window';
  } catch(e) { winError(e.message); }
}

// ── Toggle / Delete ──────────────────────────────────────────────────

async function toggleWindow(id, current) {
  try {
    await authFetch('/api/approved-windows/' + id + '/toggle', { method:'POST' });
    showToast(current ? 'Window disabled' : 'Window enabled', 'success');
    fetchWindowsTable();
  } catch(e) { showToast('Failed', 'error'); }
}

async function deleteWindow(id) {
  if (!(await customConfirm('Delete this approved window? This cannot be undone.'))) return;
  try {
    await authFetch('/api/approved-windows/' + id, { method:'DELETE' });
    showToast('Window deleted', 'success');
    fetchWindowsTable();
  } catch(e) { showToast('Failed to delete', 'error'); }
}

async function showWindowHistory(windowId) {
  var overlay = document.getElementById('window-history-overlay');
  var content = document.getElementById('window-history-content');
  content.innerHTML = '<p class="text-muted">Loading...</p>';
  overlay.classList.remove('hidden');
  try {
    var r = await fetch('/api/approved-windows/' + windowId + '/executions');
    var data = await r.json();
    if (!data.length) {
      content.innerHTML = '<p class="text-muted" style="padding:20px 0;">No executions recorded for this window yet.</p>';
    } else {
      var now = Math.floor(Date.now() / 1000);
      content.innerHTML = '<table><thead><tr><th>When</th><th>Command</th><th>Gateway</th></tr></thead><tbody>' +
        data.map(function(e) {
          var ago = formatAgo(now - Math.floor(e.executed_at || 0) || 0);
          var ok = Number(e.success) !== 0;
          var icon = ok ? '<span class="text-success">✅</span>' : '<span class="stat-blocked">❌</span>';
          var reasonHtml = (!ok && e.reason) ? '<br><span class="text-xs stat-blocked">' + escapeHtml(e.reason) + '</span>' : '';
          return '<tr>' +
            '<td class="text-xs text-muted whitespace-nowrap">' + ago + '</td>' +
            '<td><code class="text-xs">' + icon + ' ' + escapeHtml(e.command || '') + '</code>' + reasonHtml + '</td>' +
            '<td class="text-xs text-muted">' + escapeHtml(e.target_ip || '') + '</td></tr>';
        }).join('') + '</tbody></table>';
    }
  } catch(e) {
    content.innerHTML = '<p class="text-danger">Failed to load execution history.</p>';
  }
}

// ── Init ─────────────────────────────────────────────────────────────────
async function initDashboard() { updateNotifyUI(); fetchVersion(); fetchRequests(); fetchCmdDescs(); fetchSuggestions(); refreshPasswordUI(); fetchFreezeStatus(); fetchFleetCommands(); }

// ── Requests ─────────────────────────────────────────────────────────────
let _requestSearchQuery = '';
function onRequestSearch() {
  _requestSearchQuery = document.getElementById('request-search').value.trim();
  fetchRequests();
}
async function fetchRequests() {
  try {
    let url = '/api/requests';
    if (_requestSearchQuery) url += '?search=' + encodeURIComponent(_requestSearchQuery);
    const res = await fetch(url);
    if (res.status === 401) { await checkAuth(); return; }
    const data = await res.json();
    detectNewJITs(data);
    requestsData = data;
    // Also load AI-initiated window requests awaiting approval
    try {
      const wrRes = await fetch('/api/window-requests/pending');
      _pendingWinReqs = wrRes.ok ? await wrRes.json() : [];
    } catch(e) { _pendingWinReqs = []; }
    detectNewWinReqs();
    await refreshPolicyCache();
    renderJitTickets(); renderTable(); updateStats();
  } catch(err) {}
}
function detectNewJITs(newData) {
  const currentPendingIds = new Set();
  newData.forEach(function(r) { if (r.status === 'pending' && r.ttl > 0) currentPendingIds.add(r.id); });
  const newIds = [];
  currentPendingIds.forEach(function(id) { if (!_knownPendingIds.has(id)) newIds.push(id); });
  _knownPendingIds = currentPendingIds;
  if (newIds.length > 0) {
    // Only notify for JIT if toggle is on
    if (notifyJIT) {
      notifyNewJIT(currentPendingIds.size);
      var badge = document.getElementById('jit-new-badge');
      if (badge) { badge.classList.remove('hidden'); setTimeout(function() { badge.classList.add('hidden'); }, 5000); }
    }
  }
  // Check for new blocked commands (notifications when enabled)
  if (notifyBlocked) {
    var blockedData = newData.filter(function(r) { return r.status === 'blocked'; });
    var newBlocked = blockedData.filter(function(r) { return !_knownBlockedIds.has(r.id); });
    blockedData.forEach(function(r) { _knownBlockedIds.add(r.id); });
    // Notify only for NEW blocked commands (dedup by ID)
    if (newBlocked.length > 0) {
      var lastBlocked = newBlocked[newBlocked.length - 1];
      var now2 = Date.now();
      if (now2 - lastJitNotifyTime > 5000) {
        lastJitNotifyTime = now2;
        playJitChime(false);
        if (_notifPerm === 'granted') {
          var nb = new Notification('🛑 Command Blocked', { body: 'Blocked: ' + (lastBlocked.command.length > 80 ? lastBlocked.command.substring(0,80) + '...' : lastBlocked.command), tag: 'eshu-blocked', icon: '/static/eshu_logo.png' });
          nb.onclick = function() { window.focus(); switchView('home'); nb.close(); };
        }
      }
    }
  }
}

function detectNewWinReqs() {
  var winReqs = _pendingWinReqs || [];
  var currentIds = new Set();
  winReqs.forEach(function(w) { currentIds.add(w.id); });
  var newIds = [];
  currentIds.forEach(function(id) { if (!_knownWinReqIds.has(id)) newIds.push(id); });
  _knownWinReqIds = currentIds;
  if (newIds.length > 0 && notifyJIT) {
    var now = Date.now();
    if (now - lastJitNotifyTime >= 5000) {
      lastJitNotifyTime = now;
      playJitChime(false);
      var badge = document.getElementById('jit-new-badge');
      if (badge) { badge.classList.remove('hidden'); setTimeout(function() { badge.classList.add('hidden'); }, 5000); }
      if (_notifPerm === 'granted') {
        var n = new Notification('Window Request', { body: winReqs.length === 1 ? '1 window request awaits review' : winReqs.length + ' window requests await review', tag: 'eshu-win-req', icon: '/static/eshu_logo.png' });
        n.onclick = function() { window.focus(); switchView('home'); n.close(); };
      }
    }
  }
}

// ── JIT Ticket Rendering ─────────────────────────────────────────────────
function renderJitTickets() {
  const pending = requestsData.filter(r => r.status === 'pending' && r.ttl > 0);
  const winReqs = _pendingWinReqs || [];
  const total = pending.length + winReqs.length;
  document.getElementById('jit-pending-count').textContent = total + ' pending';
  const widget = document.getElementById('jit-pending-widget');
  if (total > 0) { widget.classList.add('glow'); } else { widget.classList.remove('glow'); }
  const container = document.getElementById('jit-tickets');
  if (total === 0) { container.innerHTML = '<p class="text-muted">No pending requests — all clear.</p>'; return; }

  let html = '';
  // Window requests (AI-initiated, operator must approve)
  html += winReqs.map(function(w) {
    return '<div class="jit-ticket">' +
      '<div class="jit-ticket-left">' +
        '<div class="jit-ticket-info">' +
          '<div class="jit-cmd">' + escapeHtml(w.command) + '</div>' +
          '<div class="jit-meta">#' + String(w.id).padStart(6,'0') + ' · Window request · ' + escapeHtml(w.target_ip) +
            (w.label ? ' · ' + escapeHtml(w.label) : '') +
            ' · ' + formatWinSchedule(w) + '</div>' +
        '</div>' +
      '</div>' +
      '<div class="jit-actions">' +
        '<button onclick="denyWindowReq(' + w.id + ')" class="btn btn-deny btn-sm">DENY</button>' +
        '<button onclick="approveWindowReq(' + w.id + ')" class="btn btn-approve btn-sm">APPROVE</button>' +
      '</div>' +
    '</div>';
  }).join('');

  html += pending.map(function(r) {
    const id = deriveGatewayIdentity(r.hostname || '');
    return '<div class="jit-ticket">' +
      '<div class="jit-ticket-left">' +
        '<div class="jit-ticket-icon">⚠</div>' +
        '<div class="jit-ticket-info">' +
          '<div class="jit-cmd">' + escapeHtml(r.command) + '</div>' +
          (describeCmd(r.command) ? '<div class="jit-desc">' + escapeHtml(describeCmd(r.command)) + '</div>' : '') +
          '<div class="jit-meta">#' + String(r.id).padStart(6,'0') + ' · ' + gwPill(r.hostname||'N/A') + ' ' + escapeHtml(r.hostname||'N/A') + ' (' + escapeHtml(r.target_ip) + ')</div>' +
          '<div class="jit-ttl"><span class="ttl-countdown" data-ttl="' + r.ttl + '">' + r.ttl + 's</span> remaining</div>' +
          (r.anomaly ? '<div class="jit-anomaly">🆕 ' + escapeHtml(r.anomaly) + '</div>' : '') +
        '</div>' +
      '</div>' +
      '<div class="jit-actions">' +
        '<button onclick="handleAction(' + r.id + ',\'deny\')" class="btn btn-deny btn-sm">DENY</button>' +
        '<button onclick="handleAction(' + r.id + ',\'approve\')" class="btn btn-approve btn-sm">APPROVE</button>' +
      '</div>' +
    '</div>';
  }).join('');
  container.innerHTML = html;
}

async function approveWindowReq(id) {
  try {
    const r = await authFetch('/api/window-requests/' + id + '/approve', { method: 'POST' });
    if (r.status === 401) { await checkAuth(); return; }
    if (!r.ok) throw new Error('Approve failed');
    const data = await r.json();
    if (data.token) copyToClipboard(data.token, true);
    showToast('✅ Window approved — token copied', 'success');
    fetchRequests(); fetchWindowsTable();
  } catch(e) { showToast('❌ ' + (e.message || 'Failed to approve window'), 'error'); }
}
async function denyWindowReq(id) {
  try {
    const r = await authFetch('/api/window-requests/' + id + '/deny', { method: 'POST' });
    if (r.status === 401) { await checkAuth(); return; }
    if (!r.ok) throw new Error('Deny failed');
    showToast('Window request denied', 'success');
    fetchRequests();
  } catch(e) { showToast('❌ Failed to deny window request', 'error'); }
}

// ── Deny with Blocklist ─────────────────────────────────────────────────
async function handleAction(id, action) {
  if (action === 'deny') {
    const res = await authFetch('/api/deny/' + id, { method: 'POST' });
    if (res.status === 401) { await checkAuth(); return; }
    let denyCount = 0, denyCmd = '';
    try { const data = await res.json(); denyCount = data.deny_count || 0; denyCmd = data.command || ''; } catch(e) {}
    const req = requestsData.find(r => r.id === id);
    const cmd = denyCmd || (req ? req.command : '');
    if (cmd && denyCount === DENY_BLOCKLIST_PROMPT_THRESHOLD) {
      lastDeniedCmd = cmd;
      document.getElementById('deny-bar-text').textContent = 'Command denied: "' + cmd + '" — Add to blocklist?';
      document.getElementById('deny-blacklist-bar').classList.remove('hidden');
    }
    fetchRequests();
  } else {
    const res = await authFetch('/api/' + action + '/' + id, { method: 'POST' });
    if (res.status === 401) { await checkAuth(); return; }
    fetchRequests();
  }
}
async function handleDenyBlocklist() {
  if (!lastDeniedCmd) return;
  const rbTextarea = document.getElementById('policy-regex-black'), exTextarea = document.getElementById('policy-exact');
  const currentBlocklist = rbTextarea.value.trim();
  rbTextarea.value = currentBlocklist ? currentBlocklist + '\n' + lastDeniedCmd : lastDeniedCmd;
  exTextarea.value = exTextarea.value.split('\n').filter(l => l.trim() !== lastDeniedCmd).join('\n');
  renderPolicyChips();
  await savePoliciesSilent();
  const gwRes = await fetch('/api/gateways'); const gws = await gwRes.json();
  showToast('✅ Blocklisted & pushed to ' + gws.length + ' gateway(s)', 'success');
  dismissDenyBar();
}
function dismissDenyBar() { document.getElementById('deny-blacklist-bar').classList.add('hidden'); lastDeniedCmd = ''; }

// ── Table ────────────────────────────────────────────────────────────────
function escapeHtml(str) { if (!str) return ''; return str.replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>').replace(/"/g, '"').replace(/'/g, '&#039;'); }
function formatTime(epoch) { return new Date(epoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
function formatDateTime(epoch) { return new Date(epoch * 1000).toLocaleString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
function formatAgo(seconds) { if (seconds < 0) seconds = 0; if (seconds < 60) return seconds + 's ago'; if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago'; if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago'; return Math.floor(seconds / 86400) + 'd ago'; }
function formatLastSeen(seconds) { if (seconds < 0) seconds = 0; if (seconds < 15) return 'Connected'; if (seconds < 60) return seconds + 's ago'; if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago'; if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago'; return Math.floor(seconds / 86400) + 'd ago'; }
async function copyToClipboard(text, silent) {
  try { await navigator.clipboard.writeText(text); }
  catch(e) { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); }
  if (!silent) showToast('📋 Copied', 'success');
}

function updateStats() {
  let p=0, a=0, au=0, b=0, d=0;
  requestsData.forEach(function(r) { if(r.status==='pending') p++; if(r.status==='approved'||r.status==='consumed') a++; if(r.status==='auto-approved') au++; if(r.status==='blocked'||r.status==='frozen') b++; if(r.status==='denied') d++; });
  document.getElementById('stat-pending').innerText = p; document.getElementById('stat-approved').innerText = a;
  document.getElementById('stat-auto').innerText = au; document.getElementById('stat-blocked').innerText = b;
  document.getElementById('stat-denied').innerText = d;
}
function getFilteredData() {
  if (!activeFilter) return requestsData;
  if (activeFilter === 'approved') return requestsData.filter(r => r.status === 'approved' || r.status === 'consumed');
  if (activeFilter === 'blocked') return requestsData.filter(r => r.status === 'blocked' || r.status === 'frozen');
  return requestsData.filter(r => r.status === activeFilter);
}
function buildGapMap(filtered) {
  const gapMap = new Map(); if (filtered.length < 2) return gapMap;
  const sorted = [].concat(filtered).sort((a, b) => a.id - b.id);
  for (let i = 0; i < sorted.length - 1; i++) {
    const diff = sorted[i + 1].id - sorted[i].id;
    if (diff > 1) {
      const missing = [];
      for (let g = sorted[i].id + 1; g < sorted[i + 1].id; g++) missing.push(g);
      gapMap.set(sorted[i].id, missing); gapMap.set(sorted[i + 1].id, missing);
    }
  }
  return gapMap;
}
function formatGapTooltip(missingIds) {
  if (missingIds.length === 1) return 'Gap: #000' + missingIds[0] + ' is missing';
  const first = missingIds[0], last = missingIds[missingIds.length - 1];
  return 'Gap: #000' + first + '–#000' + last + ' (' + missingIds.length + ' missing)';
}

function toggleFilter(status) {
  const boxMap = { 'pending': 'box-pending', 'approved': 'box-approved', 'auto-approved': 'box-auto', 'blocked': 'box-blocked', 'denied': 'box-denied' };
  Object.values(boxMap).forEach(function(id) { document.getElementById(id).classList.remove('active-filter'); });
  if (activeFilter === status) { activeFilter = null; document.getElementById('filter-badge').classList.add('hidden'); }
  else { activeFilter = status; document.getElementById(boxMap[status]).classList.add('active-filter'); document.getElementById('filter-badge').classList.remove('hidden'); document.getElementById('filter-badge').textContent = 'Filtering: ' + status; }
  renderTable();
}

async function refreshPolicyCache() {
  try {
    const res = await fetch('/api/policies'); const data = await res.json();
    const exact = data.exact_whitelist || '', regexWhite = data.regex_whitelist || '', regexBlack = data.regex_blacklist || '';
    policiesCache = {
      exactLines: exact.split('\n').filter(l => l.trim()),
      regexWhiteLines: regexWhite.split('\n').filter(l => l.trim()),
      regexBlackLines: regexBlack.split('\n').filter(l => l.trim()),
      corePatterns: data.core_patterns || [],
      hardPatterns: data.hard_patterns || [],
    };
  } catch(e) { policiesCache = { exactLines: [], regexWhiteLines: [], regexBlackLines: [], corePatterns: [], hardPatterns: [] }; }
}
function checkCommandMembership(cmd) {
  if (!policiesCache.exactLines) return { inExact: false, inRegexWhite: false, inBlacklist: false };
  const inExact = policiesCache.exactLines.includes(cmd);
  let inRegexWhite = false;
  for (let i = 0; i < policiesCache.regexWhiteLines.length; i++) { try { if (new RegExp(policiesCache.regexWhiteLines[i]).test(cmd)) { inRegexWhite = true; break; } } catch(e) {} }
  let inBlacklist = false;
  for (let i = 0; i < policiesCache.regexBlackLines.length; i++) { if (cmd.includes(policiesCache.regexBlackLines[i])) { inBlacklist = true; break; } }
  return { inExact: inExact, inRegexWhite: inRegexWhite, inBlacklist: inBlacklist };
}

function renderTable() {
  if (document.activeElement && document.activeElement.tagName === 'SELECT') return;
  const tbody = document.getElementById('table-body'), emptyState = document.getElementById('empty-state');
  let filtered = getFilteredData();
  // Apply client-side queue clear filter
  if (_queueClearBefore > 0) {
    filtered = filtered.filter(function(r) { return r.created_at >= _queueClearBefore; });
  }
  if (filtered.length === 0) { tbody.innerHTML = ''; emptyState.classList.remove('hidden'); emptyState.classList.add('flex'); return; }
  emptyState.classList.add('hidden'); emptyState.classList.remove('flex');
  const gapMap = buildGapMap(filtered);
  let html = '';
  filtered.forEach(function(req) {
    const isPP = req.status === 'pending' || req.status === 'approved', isExpired = isPP && req.ttl <= 0;
    let badge = '';
    if (isPP && !isExpired) {
      badge = '<span class="badge badge-' + (req.status==='pending'?'pending':'approved') + '"><span class="ttl-countdown font-mono w-8 text-center" data-ttl="' + req.ttl + '">' + req.ttl + 's</span> ' + req.status + '</span>';
    } else if (isPP && isExpired) badge = '<span class="badge badge-expired">Expired</span>';
    else if (req.status === 'consumed') badge = '<span class="badge badge-consumed">Ticket Claimed</span>';
    else if (req.status === 'blocked') badge = '<span class="badge badge-blocked">Blocked</span>';
    else if (req.status === 'denied') badge = '<span class="badge badge-denied">Denied</span>';
    else if (req.status === 'auto-approved') badge = '<span class="badge badge-auto">Auto-Approved</span>';
    else if (req.status === 'window-approved') badge = '<span class="badge badge-window">Window</span>';
    else if (req.status === 'window-rejected') badge = '<span class="badge badge-window-rejected" title="' + escapeHtml(req.reason || '') + '">Window Rejected</span>';
    else if (req.status === 'frozen') badge = '<span class="badge badge-blocked" title="Rejected while the fleet was frozen">Blocked</span>';
    else if (req.status === 'fleet-run') badge = '<span class="badge badge-approved" title="Dispatched via Fleet Run">Approved</span>';
    let actions = '<span class="text-muted">—</span>';
    if (req.status === 'pending' && !isExpired) {
      actions = '<button onclick="handleAction(' + req.id + ', \'approve\')" class="btn btn-approve btn-xs mr-1">Approve</button>' +
        '<button onclick="handleAction(' + req.id + ', \'deny\')" class="btn btn-deny btn-xs">Deny</button>';
    } else if (req.status === 'frozen') {
      actions = '<span class="chip chip-actions chip-frozen" title="Blocked by Emergency Freeze — the fleet is rejecting all commands until unfrozen.">' +
        '🧊 Fleet Frozen</span>';
    } else if (req.status === 'fleet-run') {
      actions = '<span class="chip chip-actions chip-fleet-run" title="Executed via Fleet Run — see the Fleet Run tab for per-gateway output.">' +
        '⚡ Fleet Run</span>';
    } else if (req.reason === 'override') {
      actions = '<span class="chip chip-actions chip-override" title="Auto-approved via Override Mode — every JIT is auto-approved while active">' +
        '🔓 Override</span>';
    } else if (req.status === 'blocked' && isHardcoreBlocked(req.command)) {
      actions = '<span class="chip chip-actions chip-block-core" title="Blocked by a shipped Eshu safety pattern — manage in Controls → Blocklist">' +
        '🛡️ Block by Core</span>';
    } else {
      const mem = fetchPolicyMembership(req.command);
      const inAnyAllowlist = mem.inExact || mem.inRegexWhite;
      const disabledStyle = 'opacity:0.5;pointer-events:none;';

      actions = '<select onchange="handlePolicyAction(this,\'' + encodeURIComponent(req.command) + '\')" class="btn-muted select-actions">' +
        '<option value="" disabled selected>⚙ Actions</option>' +
        '<option value="exact_whitelist"' + (mem.inExact ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inExact ? '✓ ' : '+ ') + 'AL Exact</option>' +
        '<option value="regex_whitelist"' + (mem.inRegexWhite ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inRegexWhite ? '✓ ' : '+ ') + 'AL Regex</option>' +
        '<option value="regex_blacklist"' + (mem.inBlacklist ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inBlacklist ? '✓ ' : '🚫 ') + 'Add to Blocklist</option>' +
        (inAnyAllowlist ? '<option value="regex_whitelist_remove">🔄 Remove from Allowlist</option>' : '') +
        (mem.inBlacklist ? '<option value="regex_blacklist_remove">🚫 Remove from Blocklist</option>' : '') +
      '</select>';
    }
    const gap = gapMap.get(req.id);
    const rowClass = gap ? 'gap-row' : '';
    const idClass = gap ? 'cell-id-warn' : 'cell-id';
    const gapTitle = gap ? ' title="' + escapeHtml(formatGapTooltip(gap)) + '"' : '';
    const idDisplay = gap ? '⚠ #' + String(req.id).padStart(6, '0') : '#' + String(req.id).padStart(6, '0');
    const escapedCmd = escapeHtml(req.command);
    const gwPillHtml = gwPill(req.hostname || 'N/A');
    const riskHtml = (req.status === 'pending' && req.risk) ?
      '<span class="flex-shrink-0 risk-flag" title="⚠ Risk: ' + escapeHtml(req.risk) + '">⚠</span>' : '';
    const anomalyHtml = (req.status === 'pending' && req.anomaly) ?
      '<span class="flex-shrink-0 anomaly-flag" title="🆕 ' + escapeHtml(req.anomaly) + '">🆕</span>' : '';
    html += '<tr class="' + rowClass + '">' +
      '<td class="' + idClass + '"' + gapTitle + '>' + idDisplay + '</td>' +
      '<td class="text-muted text-xs">' + formatTime(req.created_at) + '</td>' +
      '<td>' + gwPillHtml + ' ' + escapeHtml(req.hostname || 'N/A') + ' (' + escapeHtml(req.target_ip) + ')</td>' +
      '<td class="cell-cmd"><div class="flex items-center gap-1">' + riskHtml + anomalyHtml + '<code class="cmd-code" title="' + escapedCmd + '">' + escapedCmd + '</code><button class="js-copy-cmd flex-shrink-0 text-xs opacity-30 hover:opacity-80 px-1 py-0.5 rounded transition-opacity text-muted" data-cmd="' + encodeURIComponent(req.command) + '" title="Copy">📋</button></div>' +
        (function() {
          var _desc = describeCmd(req.command);
          if (!_desc) return '';
          var _maxLen = 120;
          if (_desc.length > _maxLen) {
            var _short = _desc.substring(0, _maxLen) + '...';
            return '<div class="cmd-desc">' +
              '<span id="desc-short-' + req.id + '">' + escapeHtml(_short) + ' <a href="#" onclick="toggleDesc(' + req.id + ');return false;" class="text-info">More</a></span>' +
              '<span id="desc-full-' + req.id + '" style="display:none;">' +
                '<a href="#" onclick="toggleDesc(' + req.id + ');return false;" class="text-muted">Less</a> ' + escapeHtml(_desc) +
              '</span>' +
              '</div>';
          }
          return '<div class="cmd-desc">' + escapeHtml(_desc) + '</div>';
        })() +
        '</td>' +
      '<td>' + badge + '</td>' +
      '<td class="text-right">' + actions + '</td></tr>';
  });
  tbody.innerHTML = html;
  restoreExpandedDescs();
}

function toggleDesc(id) {
  var s = document.getElementById('desc-short-' + id);
  var f = document.getElementById('desc-full-' + id);
  if (!s || !f) return;
  if (s.style.display === 'none') {
    s.style.display = '';
    f.style.display = 'none';
    _expandedDescs.delete(id);
  } else {
    s.style.display = 'none';
    f.style.display = '';
    _expandedDescs.add(id);
  }
}

function restoreExpandedDescs() {
  _expandedDescs.forEach(function(id) {
    var s = document.getElementById('desc-short-' + id);
    var f = document.getElementById('desc-full-' + id);
    if (s && f) { s.style.display = 'none'; f.style.display = ''; }
  });
  _expandedDescs.forEach(function(id) {
    if (!document.getElementById('desc-short-' + id)) _expandedDescs.delete(id);
  });
}

function decrementTimers() {
  document.querySelectorAll('.ttl-countdown').forEach(function(el) {
    let ttl = parseInt(el.getAttribute('data-ttl'));
    if (ttl > 0) { ttl--; el.setAttribute('data-ttl', ttl); el.innerText = ttl + 's'; } else fetchRequests();
  });
  checkOverrideBanner();
  tickGatewayTableCountdowns();
}

// ── Core blocklist client-side check ────────────────────────────────────
// Uses the registry fetched from /api/policies:
//   core_patterns = editable shipped command-safety patterns (seeded blocklist)
//   hard_patterns = non-editable self-protection + evasion patterns
function coreBlockPatterns() { return (policiesCache && policiesCache.corePatterns) || []; }
function hardBlockPatterns() { return (policiesCache && policiesCache.hardPatterns) || []; }
function _matchAny(cmd, list) {
  if (!cmd || !list) return null;
  for (var i = 0; i < list.length; i++) { if (cmd.indexOf(list[i]) !== -1) return list[i]; }
  return null;
}
function isHardcoreBlocked(cmd) {
  return !!( _matchAny(cmd, coreBlockPatterns()) || _matchAny(cmd, hardBlockPatterns()) );
}
function isCorePattern(line) {
  var list = coreBlockPatterns();
  for (var i = 0; i < list.length; i++) { if (list[i] === line) return true; }
  return false;
}

function fetchPolicyMembership(cmd) {
  return checkCommandMembership(cmd);
}

function encodeCmd(cmd) {
  return encodeURIComponent(cmd).replace(/'/g, '%27');
}

function addCommandToAllowlist(encodedCmd) {
  addToPolicy(decodeURIComponent(encodedCmd), 'exact_whitelist');
}

async function dismissPolicyGap(encodedCmd) {
  var cmd = decodeURIComponent(encodedCmd);
  if (!(await customConfirm('Dismiss "' + cmd + '" from policy gaps? It will stop appearing here but can still be allowlisted from the policy editor.'))) return;
  try {
    var res = await authFetch('/api/policies/dismiss-gap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({command: cmd})
    });
    if (res.ok) {
      showToast('Dismissed "' + cmd.substring(0, 40) + '"', 'success');
      fetchStatistics();
      refreshSuggestions();
    } else { showToast('Failed to dismiss', 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── Suggestions (persistent policy gaps) ─────────────────────────────────
var _suggestionsData = null;

async function fetchSuggestions() {
  try {
    var res = await authFetch('/api/learning/gaps');
    if (!res.ok) { if (res.status === 401) { checkAuth(); return; } }
    _suggestionsData = await res.json();
    renderSuggestions();
  } catch(e) {}
}

async function refreshSuggestions() {
  await authFetch('/api/learning/gaps/refresh', { method: 'POST' });
  fetchSuggestions();
}

function suggestionAllowlist(encodedCmd) {
  addToPolicy(decodeURIComponent(encodedCmd), 'exact_whitelist');
  setTimeout(refreshSuggestions, 3500);
}

function suggestionBlocklist(encodedCmd) {
  addToPolicy(decodeURIComponent(encodedCmd), 'regex_blacklist');
  setTimeout(refreshSuggestions, 3500);
}

function renderSuggestions() {
  var d = _suggestionsData;
  var list = document.getElementById('suggestions-list');
  var summary = document.getElementById('suggestions-summary');
  if (!d) { list.innerHTML = '<p class="text-muted">Loading...</p>'; return; }
  if (summary) {
    var s = (d.updated_at ? new Date(d.updated_at * 1000).toLocaleTimeString() : '') +
      ' · ' + d.total_gaps + ' gap(s)' +
      (d.new_gaps > 0 ? ' · ' + d.new_gaps + ' new' : '');
    summary.textContent = s;
  }
  if (!d.gateways || d.gateways.length === 0) {
    list.innerHTML = '<p class="text-muted">No suggestions — repeatedly-approved/denied commands are handled or dismissed.</p>';
    return;
  }
  list.innerHTML = d.gateways.map(function(g) {
    var rows = g.gaps.map(function(c) {
      var isDeny = c.kind === 'deny';
      var meta = isDeny
        ? '<div class="text-danger mt-1">Denied ' + c.count + 'x</div>'
        : '<div class="text-warning mt-1">Approved ' + c.count + 'x</div>';
      var actionBtn = isDeny
        ? '<button class="btn btn-xs" onclick="suggestionBlocklist(\'' + encodeCmd(c.command) + '\')">＋ Blocklist</button>'
        : '<button class="btn btn-xs" onclick="suggestionAllowlist(\'' + encodeCmd(c.command) + '\')">+ Allowlist</button>';
      return '<div class="flex items-start gap-2 mb-2 pb-2 text-xs divider-bottom">' +
        '<div class="flex-1 min-w-0">' +
          '<div class="flex items-center gap-2">' +
            '<span class="font-mono truncate">' + escapeHtml(c.command) + '</span>' +
            (c.is_new ? '<span class="new-tag">NEW</span>' : '') +
          '</div>' +
          (c.description ? '<div class="text-muted mt-1">' + escapeHtml(c.description) + '</div>' : '') +
          meta +
        '</div>' +
        '<div class="flex gap-1 mt-1 flex-shrink-0">' +
          actionBtn +
          '<button class="chip-btn" onclick="dismissPolicyGap(\'' + encodeCmd(c.command) + '\')">Dismiss</button>' +
        '</div>' +
        '</div>';
    }).join('');
    return '<div class="mb-4">' +
      '<div class="flex items-center gap-2 mb-2 divider-top pt-2">' +
        gwPill(g.hostname) + ' <strong class="text-main">' + escapeHtml(g.hostname) + '</strong>' +
        '<span class="text-xs text-muted">(' + escapeHtml(g.ip) + ')</span>' +
      '</div>' +
      rows +
      '</div>';
  }).join('');
}

async function markSuggestionsSeen() {
  try {
    var res = await authFetch('/api/learning/gaps/mark-seen', { method: 'POST' });
    if (res.ok) {
      showToast('All suggestions marked as seen', 'success');
      fetchSuggestions();
    } else { showToast('Failed to mark seen', 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── Policy Actions ───────────────────────────────────────────────────────
async function savePoliciesSilent() {
  const ex = document.getElementById('policy-exact').value, rw = document.getElementById('policy-regex-white').value, rb = document.getElementById('policy-regex-black').value;
  await authFetch('/api/policies', { method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({type:'exact_whitelist', content: ex}) });
  await authFetch('/api/policies', { method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({type:'regex_whitelist', content: rw}) });
  await authFetch('/api/policies', { method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({type:'regex_blacklist', content: rb}) });
  await authFetch('/api/policies/commit', { method: 'POST' });
  _committedPolicy = { exact: ex, regexWhite: rw, regexBlack: rb };
  updatePolicyDirtyIndicator();
  setTimeout(fetchGateways, 500);
}
async function addToPolicy(cmd, policyType) {
  const labels = { exact_whitelist: 'Exact Allowlist', regex_whitelist: 'Regex Allowlist', regex_blacklist: 'Blocklist' };
  if (policyType === 'regex_whitelist_remove') {
    if (!(await customConfirm('Remove "' + cmd + '" from allowlists?'))) return;
    const exTextarea = document.getElementById('policy-exact'), rwTextarea = document.getElementById('policy-regex-white');
    exTextarea.value = exTextarea.value.split('\n').filter(l => l.trim() !== cmd && l !== cmd).join('\n');
    rwTextarea.value = rwTextarea.value.split('\n').filter(l => l.trim() !== '^' + cmd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$').join('\n');
    await savePoliciesSilent(); await refreshPolicyCache();
    const gwRes = await fetch('/api/gateways'); const gws = await gwRes.json();
    showToast('✅ Removed from Allowlist & pushed to ' + gws.length + ' gateway(s)', 'success');
    fetchPolicies(); fetchRequests(); return;
  }
  if (policyType === 'regex_blacklist_remove') {
    if (!(await customConfirm('Remove "' + cmd + '" from blocklist?'))) return;
    const rbTextarea = document.getElementById('policy-regex-black');
    rbTextarea.value = rbTextarea.value.split('\n').filter(l => l.trim() !== cmd).join('\n');
    await savePoliciesSilent(); await refreshPolicyCache();
    const gwRes = await fetch('/api/gateways'); const gws = await gwRes.json();
    showToast('✅ Removed from Blocklist', 'success');
    fetchPolicies(); fetchRequests(); return;
  }
  // Dedupe: skip if the target line is already present
  const escapedRegex = '^' + cmd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$';
  if (policyType === 'exact_whitelist' && policyLines('exact_whitelist').indexOf(cmd) !== -1) { showToast('Already in Exact Allowlist', 'info'); return; }
  if (policyType === 'regex_whitelist' && policyLines('regex_whitelist').indexOf(escapedRegex) !== -1) { showToast('Already in Regex Allowlist', 'info'); return; }
  if (policyType === 'regex_blacklist' && policyLines('regex_blacklist').indexOf(cmd) !== -1) { showToast('Already in Blocklist', 'info'); return; }
  if (!(await customConfirm('Add "' + cmd + '" to ' + labels[policyType] + '?'))) return;
  await fetchPolicies();
  const exTextarea = document.getElementById('policy-exact'), rwTextarea = document.getElementById('policy-regex-white'), rbTextarea = document.getElementById('policy-regex-black');
  if (policyType === 'exact_whitelist') { exTextarea.value = exTextarea.value.trim() ? exTextarea.value.trim() + '\n' + cmd : cmd; rbTextarea.value = rbTextarea.value.split('\n').filter(l => l.trim() !== cmd).join('\n'); }
  else if (policyType === 'regex_whitelist') { rwTextarea.value = rwTextarea.value.trim() ? rwTextarea.value.trim() + '\n' + '^' + cmd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$' : '^' + cmd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'; rbTextarea.value = rbTextarea.value.split('\n').filter(l => l.trim() !== cmd).join('\n'); }
  else if (policyType === 'regex_blacklist') { rbTextarea.value = rbTextarea.value.trim() ? rbTextarea.value.trim() + '\n' + cmd : cmd; exTextarea.value = exTextarea.value.split('\n').filter(l => l.trim() !== cmd).join('\n'); rwTextarea.value = rwTextarea.value.split('\n').filter(l => l.trim() !== cmd).join('\n'); }
  await savePoliciesSilent(); await refreshPolicyCache();
  const gwRes = await fetch('/api/gateways'); const gws = await gwRes.json();
  showToast('✅ Added to ' + labels[policyType], 'success');
  fetchPolicies(); fetchRequests();
}
function handlePolicyAction(selectEl, encodedCmd) { const action = selectEl.value; if (!action) return; addToPolicy(decodeURIComponent(encodedCmd), action); setTimeout(function() { selectEl.selectedIndex = 0; }, 0); }

// ── Delegated copy handler for table command copy buttons ──────────────
(function() {
  function attachCopyHandler() {
    var tableBody = document.getElementById('table-body');
    if (tableBody) {
      tableBody.addEventListener('click', function(e) {
        var btn = e.target.closest('.js-copy-cmd');
        if (!btn) return;
        var cmd = decodeURIComponent(btn.getAttribute('data-cmd') || '');
        if (cmd) copyToClipboard(cmd);
      });
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attachCopyHandler);
  } else {
    attachCopyHandler();
  }
})();

// ── Gateways ─────────────────────────────────────────────────────────────
var _gatewaysData = [];
var _overrideExpiries = {}; // ip -> epoch seconds (client-side)
var _uninstallingIps = {};  // ip -> true while an uninstall is in flight (row state)
var _uninstallIp = null;    // ip currently being uninstalled (for the progress modal)
let _removeIp = null;       // ip in the "Remove Gateway" modal
let _removeHostname = null;
var _cmdDescs = {}; // merged command descriptions for quick lookup

function _describeSingle(cmd) {
  if (!cmd || !_cmdDescs || Object.keys(_cmdDescs).length === 0) return '';
  cmd = cmd.trim();
  // Strip common prefixes so sudo chown -> chown, nice ionice -> ionice, etc.
  var prefixes = ['sudo ', 'nice ', 'nohup ', 'ionice ', 'env ', 'time '];
  for (var i = 0; i < prefixes.length; i++) {
    if (cmd.indexOf(prefixes[i]) === 0) { cmd = cmd.slice(prefixes[i].length); break; }
  }
  var base = cmd.split(/\s+/)[0] || cmd;
  // Suppress trivial commands entirely (echo, date, cd, etc.)
  var trivial = { echo: 1, printf: 1, date: 1, whoami: 1, sleep: 1, yes: 1, 'true': 1, 'false': 1, env: 1, cd: 1 };
  if (trivial[base]) return '';
  // Longest-prefix match
  var best = '';
  for (var prefix in _cmdDescs) {
    if (cmd.indexOf(prefix) === 0 && prefix.length > best.length) {
      best = prefix;
    }
  }
  if (best) return _cmdDescs[best];
  // Fallback: find the static entry whose first word matches the base command
  for (var p2 in _cmdDescs) {
    if (p2.split(/\s+/)[0] === base && p2.length > best.length) {
      best = p2;
    }
  }
  return _cmdDescs[best] || '';
}

function describeCmd(cmd) {
  if (!cmd || !_cmdDescs || Object.keys(_cmdDescs).length === 0) return '';
  // For compound chains, describe each meaningful fragment and join them.
  var parts = cmd.trim().split(/\s*(?:&&|\|\||;)\s*|\s*\|\s*/).filter(function(p) { return p.trim() !== ''; });
  if (parts.length > 1) {
    var descs = [];
    for (var i = 0; i < parts.length; i++) {
      var d = _describeSingle(parts[i]);
      if (d) descs.push(d);
    }
    if (descs.length === 1) return descs[0];
    if (descs.length > 1) return descs.join(' · ');
    return '';
  }
  return _describeSingle(cmd);
}

async function fetchCmdDescs() {
  try {
    var res = await fetch('/api/cmd-descs');
    var data = await res.json();
    // Merge static + whatis into a single lookup dict
    var merged = {};
    if (data.static) { for (var k in data.static) { merged[k] = data.static[k]; } }
    if (data.whatis) { for (var w in data.whatis) { if (!merged[w]) merged[w] = data.whatis[w]; } }
    _cmdDescs = merged;
  } catch(e) {}
}

async function fetchGateways() {
  const res = await fetch('/api/gateways'); const data = await res.json();
  _gatewaysData = data;
  var clientNow = Math.floor(Date.now() / 1000);
  data.forEach(function(g) {
    if ((g.override_remaining || 0) > 0) {
      _overrideExpiries[g.ip] = clientNow + g.override_remaining;
    } else {
      delete _overrideExpiries[g.ip];
    }
  });
  const tbody = document.getElementById('gateways-table-body');
  if (!tbody) return;
  if (data.length === 0) { tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-3 text-muted">No gateways registered.</td></tr>'; return; }
  const now = Math.floor(Date.now() / 1000);
  tbody.innerHTML = data.map(function(g) {
    const diff = now - g.last_seen, isOnline = diff < 30;
    const connDot = isOnline ? '<span class="conn-dot on" title="Connected"></span>' : '<span class="conn-dot off" title="Disconnected"></span>';
    const statusCell = connDot + (isOnline ? '<span class="text-success text-xs">Connected</span>' : '<span class="text-muted text-xs">Offline · ' + formatLastSeen(diff) + '</span>');
    const pua = g.policy_updated_at || 0;
    const syncCell = isOnline
      ? (pua > 0 ? '<span class="text-muted text-xs">✓ synced ' + formatAgo(now - pua) + '</span>' : '<span class="text-muted text-xs">—</span>')
      : '<span class="text-muted text-xs">unknown</span>';
    const hbDot = function(ok, title) { return '<span class="' + (ok ? 'text-success' : 'text-danger') + '" title="' + title + '">⬤</span> '; };
    const healthCell = g.last_heartbeat > 0
      ? '<span class="hb-status">' +
          hbDot(Number(g.heartbeat_poller_ok), 'Poller') +
          hbDot(Number(g.heartbeat_gateway_ok), 'Gateway') +
          hbDot(Number(g.heartbeat_can_reach), 'Reachable') +
          hbDot(g.has_token ? 1 : 0, g.has_token ? 'Token valid' : 'No API token — JIT delivery broken. Re-enroll this gateway.') +
          '(' + formatAgo(now - g.last_heartbeat) + ')' +
        '</span>'
      : '<span class="hb-status">waiting…</span>';
    var ztBadge = g.zero_trust
      ? '<span class="zt-badge" title="Zero-Trust — every command requires JIT approval">🔒 ZT</span>'
      : '';
    var ztBtn = g.zero_trust
      ? '<button onclick="toggleZeroTrust(\'' + g.ip + '\', false)" class="chip-btn zt-on" title="Zero-Trust ON — allowlisted commands go through JIT. Click to disable.">🔒 ZT</button>'
      : '<button onclick="toggleZeroTrust(\'' + g.ip + '\', true)" class="chip-btn" title="Enable Zero-Trust — allowlisted commands must go through JIT">ZT</button>';
    var overrideCell;
    if (_uninstallingIps[g.ip]) {
      overrideCell = '<span class="text-warning text-xs">🗑 Uninstalling…</span>';
    } else {
      var overrideControl;
      if ((g.override_remaining || 0) > 0) {
        overrideControl = '<span class="override-badge active" data-ovr-ip="' + g.ip + '">Override</span>' +
          '<button onclick="cancelOverride(\'' + g.ip + '\')" class="chip-btn danger">Cancel</button>';
      } else {
        overrideControl = '<button onclick="openOverrideModal(\'' + g.ip + '\',\'' + g.hostname.replace(/'/g, "\\'") + '\')" class="chip-btn override" title="Override Mode — auto-approve all JIT for a set duration">Override</button>';
      }
      overrideCell = '<div class="flex items-center gap-2">' + overrideControl + ztBtn + '</div>';
    }
    return '<tr>' +
      '<td class="px-4 py-2 whitespace-nowrap">' + statusCell + '</td>' +
      '<td class="px-4 py-2 whitespace-nowrap">' + gwPill(g.hostname) + ' ' + escapeHtml(g.hostname) + devBadge(g) + ztBadge + '</td>' +
      '<td class="px-4 py-2 whitespace-nowrap text-muted">' + g.ip + '</td>' +
      '<td class="px-4 py-2 whitespace-nowrap text-muted">' + (g.first_seen ? formatAgo(now - g.first_seen) : '—') + '</td>' +
      '<td class="px-4 py-2">' + syncCell + '</td>' +
      '<td class="px-4 py-2">' + healthCell + '</td>' +
      '<td class="px-4 py-2">' + overrideCell + '</td>' +
      '<td class="px-4 py-2 text-right whitespace-nowrap"><button onclick="openRemoveGatewayModal(\'' + g.ip + '\', \'' + g.hostname + '\', ' + isOnline + ')" class="btn btn-deny btn-xs">🗑 Remove</button></td>' +
      '</tr>';
  }).join('');
  checkOverrideBanner();
}

async function toggleZeroTrust(ip, enabled) {
  try {
    var res = await authFetch('/api/gateways/' + encodeURIComponent(ip) + '/zero-trust', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled: enabled})
    });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast((enabled ? '🔒 Zero-Trust enabled on ' : 'Zero-Trust disabled on ') + ip + ' — allowlisted commands will ' + (enabled ? 'require JIT approval' : 'auto-run again') + '.', 'success');
      fetchGateways();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// Live countdown for Override badges in the Gateways table (called every 1s).
function tickGatewayTableCountdowns() {
  var now = Math.floor(Date.now() / 1000);
  document.querySelectorAll('#gateways-table-body [data-ovr-ip]').forEach(function(el) {
    var exp = _overrideExpiries[el.getAttribute('data-ovr-ip')] || 0;
    var rem = exp - now;
    if (rem > 0) {
      var mins = String(Math.floor(rem / 60)).padStart(2, '0');
      var secs = String(rem % 60).padStart(2, '0');
      el.textContent = 'Override ' + mins + ':' + secs;
    } else {
      el.textContent = 'Override';
    }
  });
}

// ── Override Mode ────────────────────────────────────────────────────────
function checkOverrideBanner() {
  var now = Math.floor(Date.now() / 1000);
  var activeIps = Object.keys(_overrideExpiries).filter(function(ip) {
    return _overrideExpiries[ip] > now;
  });
  var banner = document.getElementById('override-banner');
  var bannerText = document.getElementById('override-banner-text');
  if (activeIps.length === 0) {
    banner.classList.add('hidden');
    return;
  }
  banner.classList.remove('hidden');
  var parts = [];
  activeIps.forEach(function(ip) {
    var remaining = _overrideExpiries[ip] - now;
    var mins = Math.floor(remaining / 60);
    var secs = remaining % 60;
    var hostname = ip;
    var gw = (_gatewaysData || []).find(function(g) { return g.ip === ip; });
    if (gw) hostname = gw.hostname;
    parts.push(hostname + ' (' + String(mins).padStart(2, '0') + ':' + String(secs).padStart(2, '0') + ')');
  });
  bannerText.textContent = 'Override Mode — ' + parts.join(', ');
}

function openOverrideModal(ip, hostname) {
  document.getElementById('override-modal-gateway').textContent = hostname + ' (' + ip + ')';
  document.getElementById('override-modal').dataset.ip = ip;
  document.getElementById('override-modal-reason').value = '';
  document.getElementById('override-modal').classList.remove('hidden');
  document.getElementById('override-modal-reason').focus();
}

async function confirmOverride() {
  var ip = document.getElementById('override-modal').dataset.ip;
  var reason = document.getElementById('override-modal-reason').value.trim();
  var minutes = parseInt(document.getElementById('override-modal-minutes').value);
  if (!reason) { document.getElementById('override-modal-reason').focus(); return; }
  if (!ip) return;
  document.getElementById('override-modal').classList.add('hidden');
  var btn = document.getElementById('override-start-btn');
  btn.disabled = true; btn.textContent = 'Starting...';
  try {
    var res = await authFetch('/api/gateways/' + encodeURIComponent(ip) + '/override', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({minutes: minutes, reason: reason})
    });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Override Mode enabled on ' + ip, 'success');
      fetchGateways();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
  btn.disabled = false; btn.textContent = 'Start Override';
}

async function cancelOverride(ip) {
  if (!(await customConfirm('Cancel Override Mode for this gateway? JIT requests will require manual approval again.'))) return;
  try {
    var res = await authFetch('/api/gateways/' + encodeURIComponent(ip) + '/override', { method: 'DELETE' });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Override cancelled for ' + ip, 'success');
      fetchGateways();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── Emergency Freeze ─────────────────────────────────────────────────────
async function fetchFreezeStatus() {
  try {
    const res = await fetch('/api/freeze/status');
    if (res.status === 401) { await checkAuth(); return; }
    const data = await res.json();
    renderFreezeStatus(data);
  } catch(e) {}
}

function renderFreezeStatus(data) {
  var frozen = !!(data && data.frozen);
  var banner = document.getElementById('freeze-banner');
  var label = document.getElementById('freeze-status-label');
  var freezeBtn = document.getElementById('freeze-btn');
  var unfreezeBtn = document.getElementById('unfreeze-btn');
  var tsEl = document.getElementById('freeze-timestamp');
  var pill = document.getElementById('freeze-pill');
  var pillDot = document.getElementById('freeze-pill-dot');
  var pillText = document.getElementById('freeze-pill-text');
  if (banner) banner.classList.toggle('hidden', !frozen);
  if (label) {
    if (frozen) { label.textContent = 'FROZEN — all commands rejected'; label.style.background = 'var(--brand-red)'; label.style.color = 'white'; }
    else { label.textContent = 'Active — running normally'; label.style.background = 'var(--status-success)'; label.style.color = 'var(--bg-base)'; }
  }
  if (freezeBtn) freezeBtn.classList.toggle('hidden', frozen);
  if (unfreezeBtn) unfreezeBtn.classList.toggle('hidden', !frozen);
  if (tsEl) {
    if (frozen && data.triggered_at) {
      tsEl.textContent = 'Frozen since ' + formatDateTime(data.triggered_at) + ' — takes effect within 30s (next poll cycle).';
      tsEl.classList.remove('hidden');
    } else { tsEl.classList.add('hidden'); }
  }
  if (pill) {
    pill.classList.toggle('freeze-on', !!frozen);
    if (pillDot) pillDot.classList.toggle('on', !!frozen);
    if (pillText) pillText.textContent = frozen ? 'Frozen — tap to unfreeze' : 'Freeze';
  }
}

async function toggleFreezeFromSidebar() {
  var frozen = !!(await fetch('/api/freeze/status').then(function(r){return r.json();}).catch(function(){return {};})).frozen;
  if (frozen) { await triggerUnfreeze(); } else { await triggerFreeze(); }
}

async function triggerFreeze() {
  if (!(await customConfirm('FREEZE THE ENTIRE FLEET?\n\nEvery gateway will reject ALL commands — including whitelisted and approved-window commands — until you unfreeze. This takes effect within 30s.\n\nContinue?'))) return;
  var btn = document.getElementById('freeze-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Freezing...'; }
  try {
    var res = await authFetch('/api/freeze', { method: 'POST' });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Fleet FROZEN — all commands rejected within 30s', 'success');
      fetchFreezeStatus();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
  if (btn) { btn.disabled = false; btn.textContent = '🧊 Freeze Fleet'; }
}

async function triggerUnfreeze() {
  if (!(await customConfirm('Unfreeze the fleet?\n\nGateways will resume normal policy enforcement on their next poll cycle (within 30s).'))) return;
  var btn = document.getElementById('unfreeze-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Unfreezing...'; }
  try {
    var res = await authFetch('/api/unfreeze', { method: 'POST' });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Fleet unfrozen — normal operation resumes', 'success');
      fetchFreezeStatus();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
  if (btn) { btn.disabled = false; btn.textContent = 'Unfreeze Fleet'; }
}

// ── Fleet Run ────────────────────────────────────────────────────────────
var _fleetSelected = new Set();
var _fleetGateways = [];
var _fleetQueue = [];

async function fetchFleetCommands() {
  try {
    const res = await fetch('/api/fleet/commands');
    if (res.status === 401) { await checkAuth(); return; }
    const data = await res.json();
    renderFleetRun(data || []);
  } catch(e) {}
  renderFleetTargetList();
}

function renderFleetTargetList() {
  var list = document.getElementById('fleet-target-list');
  if (!list) return;
  if (!_allGateways.length) {
    fetch('/api/gateways').then(function(r) { return r.json(); }).then(function(gws) {
      _allGateways = gws || [];
      _fleetGateways = _allGateways;
      _fleetSelected.forEach(function(ip) { if (!_fleetGateways.some(function(g){return g.ip===ip;})) _fleetSelected.delete(ip); });
      drawFleetTargets();
    }).catch(function() { list.innerHTML = '<span class="text-xs text-muted">Failed to load gateways.</span>'; });
  } else {
    _fleetGateways = _allGateways;
    drawFleetTargets();
  }
}

function drawFleetTargets() {
  var list = document.getElementById('fleet-target-list');
  if (!list) return;
  if (!_fleetGateways.length) { list.innerHTML = '<span class="text-xs text-muted">No gateways registered.</span>'; return; }
  list.innerHTML = _fleetGateways.map(function(g) {
    var checked = _fleetSelected.has(g.ip);
    return '<label class="target-chip' + (checked ? ' checked' : '') + '">' +
      '<input type="checkbox" ' + (checked ? 'checked' : '') + ' onchange="toggleFleetTarget(\'' + g.ip + '\')">' +
      gwPill(g.hostname || g.ip) + ' ' + escapeHtml(g.hostname || g.ip) + ' <span class="text-xs text-muted">' + g.ip + '</span></label>';
  }).join('');
}

function toggleFleetTarget(ip) {
  if (_fleetSelected.has(ip)) _fleetSelected.delete(ip); else _fleetSelected.add(ip);
  drawFleetTargets();
}

function setFleetTargets(all) {
  _fleetSelected = new Set(all ? _fleetGateways.map(function(g) { return g.ip; }) : []);
  drawFleetTargets();
}

function addFleetToQueue() {
  var cmd = document.getElementById('fleet-cmd-input').value.trim();
  var reason = document.getElementById('fleet-reason-input').value.trim();
  var timeout = parseInt(document.getElementById('fleet-timeout-input').value) || 180;
  if (!cmd) { showToast('Enter a command', 'error'); return; }
  if (_fleetSelected.size === 0) { showToast('Select at least one target gateway', 'error'); return; }
  var idx = _fleetQueue.length;
  _fleetQueue.push({command: cmd, targets: Array.from(_fleetSelected), reason: reason, timeout: timeout, risk: null, dry_run: null});
  document.getElementById('fleet-cmd-input').value = '';
  document.getElementById('fleet-reason-input').value = '';
  renderFleetQueue();
  checkFleetRisk(idx);
}

async function checkFleetRisk(idx) {
  var d = _fleetQueue[idx];
  if (!d) return;
  try {
    var tr = await fetch('/api/policies/test?command=' + encodeURIComponent(d.command));
    var td = await tr.json();
    if (_fleetQueue[idx] !== d) return; // row changed meanwhile
    d.risk = td.risk || null;
    d.dry_run = td.dry_run || null;
    renderFleetQueue();
  } catch(e) {}
}

function useFleetDryRun(idx) {
  var d = _fleetQueue[idx];
  if (!d || !d.dry_run) return;
  d.command = d.dry_run;
  d.risk = null;
  d.dry_run = null;
  renderFleetQueue();
  checkFleetRisk(idx);
}

function removeFleetFromQueue(idx) {
  _fleetQueue.splice(idx, 1);
  renderFleetQueue();
}

function formatFleetTargets(ips) {
  var out = [];
  (ips || []).forEach(function(ip) {
    var gw = (_fleetGateways || []).find(function(g) { return g.ip === ip; });
    if (gw && gw.hostname && gw.hostname !== ip) {
      out.push(escapeHtml(gw.hostname) + ' <span class="text-muted">(.' + ip.split('.').pop() + ')</span>');
    } else {
      out.push(escapeHtml(ip));
    }
  });
  return out;
}

function renderFleetQueue() {
  var list = document.getElementById('fleet-queue-list');
  var btn = document.getElementById('fleet-dispatch-btn');
  if (btn) btn.textContent = '▶ Dispatch (' + _fleetQueue.length + ')';
  if (!list) return;
  if (_fleetQueue.length === 0) {
    list.innerHTML = '<p class="text-muted">Queue is empty — add a command above.</p>';
    return;
  }
  list.innerHTML = _fleetQueue.map(function(d, i) {
    var riskLine = d.risk ? '<div class="text-xs mt-0.5 text-warning">⚠ ' + escapeHtml(d.risk) + '</div>' : '';
    var dryLine = d.dry_run ? '<div class="text-xs mt-0.5 text-info">💡 Dry-run available: <code class="text-main">' + escapeHtml(d.dry_run) + '</code> <button onclick="useFleetDryRun(' + i + ')" class="chip-btn info">Use</button></div>' : '';
    return '<div class="queue-item">' +
      '<div class="flex items-center gap-2">' +
      '<code class="text-xs flex-1 text-main">' + escapeHtml(d.command) + '</code>' +
      '<span class="text-xs text-muted whitespace-nowrap">' + d.timeout + 's</span>' +
      '<button onclick="removeFleetFromQueue(' + i + ')" class="chip-btn danger">✕</button>' +
      '</div>' +
      '<div class="text-xs mt-0.5 text-muted">→ ' + formatFleetTargets(d.targets).join(', ') + '</div>' +
      riskLine + dryLine +
      '</div>';
  }).join('');
}

async function dispatchFleetQueue() {
  if (_fleetQueue.length === 0) { showToast('Queue is empty', 'error'); return; }
  var dryCount = _fleetQueue.filter(function(d) { return !!d.dry_run; }).length;
  var msg = 'Dispatch ' + _fleetQueue.length + ' command(s) to their target gateways?\n\nThis is the approval — they will run on each gateway\'s next poll cycle (≤30s).';
  if (dryCount > 0) {
    msg += '\n\n⚠ ' + dryCount + ' command(s) have a dry-run version available for safe testing — press Cancel to go back and upgrade them, or Continue to dispatch as-is.';
  }
  if (!(await customConfirm(msg))) return;

  var hardBlocked = [], blacklistedIdx = {};
  for (var i = 0; i < _fleetQueue.length; i++) {
    if (isHardcoreBlocked(_fleetQueue[i].command)) { hardBlocked.push(_fleetQueue[i].command); continue; }
    try {
      var tr = await fetch('/api/policies/test?command=' + encodeURIComponent(_fleetQueue[i].command));
      var td = await tr.json();
      if (td.action === 'blocked') blacklistedIdx[i] = true;
    } catch(e) {}
  }
  if (hardBlocked.length > 0) showToast('Hard-blocked, kept in queue: ' + hardBlocked.join('; '), 'error');
  if (Object.keys(blacklistedIdx).length > 0) {
    var ok = await customConfirm('⚠️ ' + Object.keys(blacklistedIdx).length + ' queued command(s) match the central blocklist. Dispatch them anyway with override?');
    if (!ok) return;
  }

  var btn = document.getElementById('fleet-dispatch-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Dispatching...'; }
  var dispatched = 0, keepIdx = {};
  for (var j = 0; j < _fleetQueue.length; j++) {
    var d = _fleetQueue[j];
    if (isHardcoreBlocked(d.command)) { keepIdx[j] = true; continue; }
    try {
      var res = await authFetch('/api/fleet/commands', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command: d.command, target_ips: d.targets, reason: d.reason, timeout: d.timeout, override: !!blacklistedIdx[j]})
      });
      var data = await res.json();
      if (data.status === 'ok') dispatched++;
      else { keepIdx[j] = true; showToast('Not dispatched: ' + (data.detail || 'unknown'), 'error'); }
    } catch(e) { keepIdx[j] = true; }
  }
  _fleetQueue = _fleetQueue.filter(function(d, j) { return !!keepIdx[j]; });
  if (btn) { btn.disabled = false; btn.textContent = '▶ Dispatch (' + _fleetQueue.length + ')'; }
  renderFleetQueue();
  fetchFleetCommands();
  if (dispatched > 0) showToast('Dispatched ' + dispatched + ' command(s)', 'success');
}

var _fleetData = [];
var _fleetRenderSig = null;

function renderFleetRun(cmds) {
  var resultsEl = document.getElementById('fleet-results-list');
  if (!resultsEl) return;
  _fleetData = cmds || [];
  var sig = JSON.stringify(_fleetData.map(function(c) {
    return [c.id, c.status, (c.results || []).map(function(r) { return [r.gateway_ip, r.status, r.exit_code, r.hostname, (r.output || '').length]; })];
  }));
  if (sig === _fleetRenderSig) return;
  _fleetRenderSig = sig;
  // Capture open output boxes + scroll positions before rewriting (scroll-stability)
  var openOuts = [], preScrolls = {};
  resultsEl.querySelectorAll('details[data-out]').forEach(function(d) { if (d.open) openOuts.push(d.getAttribute('data-out')); });
  resultsEl.querySelectorAll('pre[data-out]').forEach(function(p) { preScrolls[p.getAttribute('data-out')] = p.scrollTop; });
  if (cmds.length === 0) {
    resultsEl.innerHTML = '<p class="text-muted">No fleet commands yet.</p>';
    return;
  }
  resultsEl.innerHTML = cmds.map(function(c) {
    var statusBadge = c.status === 'pending' ? 'badge-pending' :
                      c.status === 'approved' ? 'badge-auto' :
                      c.status === 'denied' ? 'badge-denied' :
                      'badge-approved';
    var resultRows = (c.results || []).map(function(r) {
      var rBadge = r.status === 'success' ? 'badge-approved' :
                   r.status === 'failed' ? 'badge-denied' :
                   r.status === 'timeout' ? 'badge-blocked' :
                   'badge-expired';
      var times = '';
      if (r.status === 'queued') {
        times = ' <span class="text-muted">[queued — waiting for poll]</span>';
      } else if (r.status === 'skipped') {
        times = ' <span class="text-muted">[skipped — cleared]</span>';
      } else if (r.started_at) {
        var fin = r.finished_at ? ' → ' + formatDateTime(r.finished_at) : ' → running';
        times = ' <span class="text-muted">[' + formatDateTime(r.started_at) + fin + ']</span>';
      }
      var outId = c.id + '-' + r.gateway_ip;
      var outHtml = '';
      if (r.output) {
        outHtml = '<details data-out="' + outId + '" class="mt-2" ontoggle="loadFleetOutputIfNeeded(' + c.id + ',\'' + r.gateway_ip + '\',this)"><summary class="text-xs cursor-pointer text-info">Show output' + (r.has_more ? ' (full)' : '') + '</summary>' +
          '<div class="mt-1 rounded border">' +
          '<div class="flex items-center justify-end gap-1 px-1 pt-1">' +
          '<button onclick="copyFleetOutput(' + c.id + ',\'' + r.gateway_ip + '\')" class="chip-btn">📋 Copy</button>' +
          '<button onclick="openFleetOutputModal(' + c.id + ',\'' + r.gateway_ip + '\')" class="chip-btn info">View full</button>' +
          '</div>' +
          '<pre data-out="' + outId + '" class="fleet-out fleet-pre overflow-auto text-xs">' + escapeHtml(r.output) + '</pre>' +
          '</div></details>';
      }
      var gName = r.hostname ? escapeHtml(r.hostname) : escapeHtml(r.gateway_ip);
      var gIp = (r.hostname && r.hostname !== r.gateway_ip) ? ' <span class="text-muted">(' + escapeHtml(r.gateway_ip) + ')</span>' : '';
      var clearBtn = (r.status === 'queued') ?
        '<button onclick="clearFleetResult(' + c.id + ',\'' + r.gateway_ip + '\')" class="chip-btn" title="Clear this stuck gateway so its queue can move on (other results are kept)">✕</button>' : '';
      return '<div class="mt-1 text-xs text-muted">• <strong>' + gName + '</strong>' + gIp + ' ' +
        '<span class="badge ' + rBadge + '">' + r.status + '</span>' +
        (r.exit_code != null && r.exit_code !== '' ? ' <span class="text-muted">exit ' + r.exit_code + '</span>' : '') +
        times + clearBtn + outHtml + '</div>';
    }).join('');
    var runningRes = (c.results || []).filter(function(r) { return r.status === 'running' && r.started_at; });
    var cdHtml;
    if (c.status === 'approved' && runningRes.length > 0) {
      var start = Math.min.apply(null, runningRes.map(function(r) { return r.started_at; }));
      var deadline = start + c.timeout;
      cdHtml = '<span class="text-xs fleet-countdown text-warning whitespace-nowrap" data-deadline="' + deadline + '">⏳ counting…</span>';
    } else {
      cdHtml = '<span class="text-xs text-muted whitespace-nowrap">👤 ' + c.timeout + 's timeout</span>';
    }
    return '<div class="p-3 rounded-lg border mb-2 bg-base">' +
      '<div class="flex items-center justify-between gap-2 flex-wrap">' +
      '<div class="flex items-center gap-2 flex-wrap"><span class="text-xs font-mono text-muted">#' + c.id + '</span>' +
      '<code class="text-xs text-main">' + escapeHtml(c.command) + '</code>' +
      '<span class="badge ' + statusBadge + '">' + c.status + '</span></div>' +
      '<div class="flex items-center gap-2 flex-wrap">' + cdHtml + '</div></div>' +
      '<div class="mt-1 text-xs text-muted">sent ' + formatDateTime(c.created_at) + '</div>' +
      '<div class="mt-1 text-xs text-muted">→ ' + formatFleetTargets(c.target_ips || []).join(', ') + '</div>' +
      (c.reason ? '<div class="text-xs text-muted">Reason: ' + escapeHtml(c.reason) + '</div>' : '') +
      (resultRows ? '<div class="mt-2 border-t pt-1">' + resultRows + '</div>' : '') +
      '</div>';
  }).join('');
  openOuts.forEach(function(id) {
    var d = resultsEl.querySelector('details[data-out="' + id + '"]');
    if (d) d.open = true;
  });
  Object.keys(preScrolls).forEach(function(id) {
    var p = resultsEl.querySelector('pre[data-out="' + id + '"]');
    if (p) p.scrollTop = preScrolls[id];
  });
}

async function copyFleetOutput(cmdId, ip) {
  try {
    var res = await fetch('/api/fleet/commands/' + cmdId + '/output/' + encodeURIComponent(ip));
    if (res.status === 401) { await checkAuth(); return; }
    var data = await res.json();
    var text = data.output || '';
    if (!text) { showToast('No output to copy', 'error'); return; }
    await copyToClipboard(text, true);
    showToast('📋 Output copied', 'success');
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

var _fleetModal = { cmdId: null, ip: null, output: '' };

function _fleetModalEsc(e) {
  if (e.key === 'Escape') closeFleetOutputModal();
}

async function openFleetOutputModal(cmdId, ip) {
  var c = (_fleetData || []).find(function(x) { return x.id === cmdId; });
  var r = c && (c.results || []).find(function(x) { return x.gateway_ip === ip; });
  var label = (r && r.hostname && r.hostname !== ip ? r.hostname + ' (' + ip + ')' : ip);
  var cmdTxt = c ? c.command : ('#' + cmdId);
  if (cmdTxt.length > 80) cmdTxt = cmdTxt.substring(0, 80) + '…';
  document.getElementById('fleet-output-modal-title').textContent = label + ' — ' + cmdTxt;
  _fleetModal = { cmdId: cmdId, ip: ip, output: '' };
  document.getElementById('fleet-output-modal-pre').textContent = 'Loading full output…';
  document.getElementById('fleet-output-modal').classList.remove('hidden');
  window.addEventListener('keydown', _fleetModalEsc);
  await loadFleetFullOutputIntoModal(cmdId, ip);
}

async function loadFleetFullOutputIntoModal(cmdId, ip) {
  try {
    var res = await fetch('/api/fleet/commands/' + cmdId + '/output/' + encodeURIComponent(ip));
    if (res.status === 401) { await checkAuth(); return; }
    var data = await res.json();
    var full = data.output || '';
    _fleetModal.output = full;
    document.getElementById('fleet-output-modal-pre').textContent = full || '(no output)';
  } catch(e) {
    document.getElementById('fleet-output-modal-pre').textContent = 'Failed to load output.';
  }
}

function closeFleetOutputModal() {
  document.getElementById('fleet-output-modal').classList.add('hidden');
  window.removeEventListener('keydown', _fleetModalEsc);
}

async function copyFleetOutputModal() {
  if (!_fleetModal.output && _fleetModal.cmdId != null) {
    await loadFleetFullOutputIntoModal(_fleetModal.cmdId, _fleetModal.ip);
  }
  if (_fleetModal.output) {
    await copyToClipboard(_fleetModal.output, true);
    showToast('📋 Output copied', 'success');
  } else {
    showToast('No output to copy', 'error');
  }
}

async function clearFleetResult(cmdId, ip) {
  if (!(await customConfirm('Clear this gateway\'s stuck result for fleet #' + cmdId + '?\n\n' + ip + ' is marked queued (it never ran, e.g. the gateway is on an old poller). Clearing marks it skipped so this gateway\'s queue can move on — the command and the other gateways\' results are kept.'))) return;
  try {
    var res = await authFetch('/api/fleet/commands/' + cmdId + '/result/' + encodeURIComponent(ip), { method: 'DELETE' });
    var data = await res.json();
    if (data.status === 'ok') { showToast('Cleared ' + ip + ' for fleet #' + cmdId, 'success'); fetchFleetCommands(); }
    else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

async function loadFleetOutputIfNeeded(cmdId, ip, detailsEl) {
  if (!detailsEl.open || detailsEl.getAttribute('data-loaded') === '1') return;
  detailsEl.setAttribute('data-loaded', '1');
  var pre = detailsEl.querySelector('pre[data-out]');
  try {
    var res = await fetch('/api/fleet/commands/' + cmdId + '/output/' + encodeURIComponent(ip));
    if (res.status === 401) { await checkAuth(); return; }
    var data = await res.json();
    var full = data.output || '';
    if (pre) pre.textContent = full;
    var c = (_fleetData || []).find(function(x) { return x.id === cmdId; });
    if (c) {
      var r = (c.results || []).find(function(x) { return x.gateway_ip === ip; });
      if (r) { r.output = full; r.has_more = false; }
    }
  } catch(e) {
    detailsEl.removeAttribute('data-loaded');
    if (pre) pre.textContent = 'Failed to load output.';
  }
}

function updateFleetCountdowns() {
  var now = Math.floor(Date.now() / 1000);
  document.querySelectorAll('.fleet-countdown[data-deadline]').forEach(function(el) {
    var rem = parseInt(el.getAttribute('data-deadline')) - now;
    if (rem <= 0) {
      el.textContent = '⏳ timed out';
      el.style.color = 'var(--brand-red)';
      return;
    }
    if (rem >= 3600) {
      var h = Math.floor(rem / 3600), m = Math.floor((rem % 3600) / 60);
      el.textContent = '⏳ ' + h + 'h ' + m + 'm left';
    } else {
      var m2 = Math.floor(rem / 60), s = rem % 60;
      el.textContent = '⏳ ' + m2 + ':' + String(s).padStart(2, '0') + ' left';
    }
  });
}

// ── Policies ─────────────────────────────────────────────────────────────
// ── Policy chip editor ────────────────────────────────────────────────
const POLICY_IDS = { exact_whitelist: 'policy-exact', regex_whitelist: 'policy-regex-white', regex_blacklist: 'policy-regex-black' };
const POLICY_ADD_INPUTS = { exact_whitelist: 'policy-exact-add', regex_whitelist: 'policy-regex-white-add', regex_blacklist: 'policy-regex-black-add' };
const POLICY_LABELS = { exact_whitelist: 'Exact Allowlist', regex_whitelist: 'Regex Allowlist', regex_blacklist: 'Blocklist' };

function policyLines(type) {
  const ta = document.getElementById(POLICY_IDS[type]);
  return (ta ? ta.value : '').split('\n').map(function(s){ return s.trim(); }).filter(Boolean);
}
function setPolicyLines(type, lines) {
  const ta = document.getElementById(POLICY_IDS[type]);
  if (ta) ta.value = lines.join('\n');
}
function updatePolicyDirtyIndicator() {
  const el = document.getElementById('policy-dirty-banner');
  if (!el) return;
  const cur = {
    exact: document.getElementById('policy-exact').value || '',
    regexWhite: document.getElementById('policy-regex-white').value || '',
    regexBlack: document.getElementById('policy-regex-black').value || '',
  };
  const dirty = _committedPolicy && (
    cur.exact !== _committedPolicy.exact ||
    cur.regexWhite !== _committedPolicy.regexWhite ||
    cur.regexBlack !== _committedPolicy.regexBlack
  );
  el.classList.toggle('hidden', !dirty);
}
function renderPolicyChips() {
  Object.keys(POLICY_IDS).forEach(function(type) {
    const container = document.getElementById(POLICY_IDS[type] + '-chips');
    if (!container) return;
    const seen = new Set();
    const lines = policyLines(type).filter(function(l){ if (seen.has(l)) return false; seen.add(l); return true; });
    setPolicyLines(type, lines);
    // Display newest-first so a just-added entry appears at the top.
    const displayLines = lines.slice().reverse();
    let html = displayLines.map(function(line) {
      const isCore = isCorePattern(line);
      const shield = isCore ? '<span class="policy-shield" title="Shipped core block — relaxing this requires extra confirmation">🛡️</span> ' : '';
      return '<span class="policy-chip' + (isCore ? ' core' : '') + '" title="' + line.replace(/"/g, '&quot;') + '">' + shield + '<code>' + escapeHtml(line) + '</code><button class="remove" data-line="' + encodeURIComponent(line) + '" data-core="' + (isCore ? '1' : '0') + '" title="Remove">&times;</button></span>';
    }).join('');
    // Removed core patterns — shipped defaults no longer in the list: struck + Restore
    if (type === 'regex_blacklist') {
      const removed = coreBlockPatterns().filter(function(p){ return lines.indexOf(p) === -1; });
      html += removed.map(function(p) {
        return '<span class="policy-chip removed" title="' + p.replace(/"/g, '&quot;') + '"><span class="policy-shield">🛡️</span><code>' + escapeHtml(p) + '</code><button class="restore" data-line="' + encodeURIComponent(p) + '" title="Restore this core default">↺</button></span>';
      }).join('');
    }
    if (!html) html = '<span class="policy-empty text-muted">No entries yet.</span>';
    container.innerHTML = html;
  });
  updatePolicyDirtyIndicator();
}
function addPolicyEntry(type) {
  const input = document.getElementById(POLICY_ADD_INPUTS[type]);
  if (!input) return;
  const val = input.value.trim();
  if (!val) return;
  const lines = policyLines(type);
  if (lines.indexOf(val) !== -1) { showToast('Already in ' + POLICY_LABELS[type], 'info'); return; }
  lines.push(val);
  setPolicyLines(type, lines);
  input.value = '';
  renderPolicyChips();
}
function restoreCoreDefaults() {
  const missing = coreBlockPatterns().filter(function(p){ return policyLines('regex_blacklist').indexOf(p) === -1; });
  if (missing.length === 0) { showToast('All core defaults are present', 'info'); return; }
  const lines = policyLines('regex_blacklist');
  missing.forEach(function(p){ lines.push(p); });
  setPolicyLines('regex_blacklist', lines);
  renderPolicyChips();
  showToast('Core defaults staged — press "Save & Push Policies" to apply', 'info');
}
document.addEventListener('click', function(e) {
  const restoreBtn = e.target.closest('.policy-chip .restore');
  if (restoreBtn) {
    const editor = restoreBtn.closest('.policy-editor');
    if (!editor) return;
    const type = editor.getAttribute('data-policy');
    const line = decodeURIComponent(restoreBtn.getAttribute('data-line'));
    const lines = policyLines(type);
    if (lines.indexOf(line) === -1) { lines.push(line); setPolicyLines(type, lines); }
    renderPolicyChips();
    return;
  }
  const btn = e.target.closest('.policy-chip .remove');
  if (!btn) return;
  const editor = btn.closest('.policy-editor');
  if (!editor) return;
  const type = editor.getAttribute('data-policy');
  const line = decodeURIComponent(btn.getAttribute('data-line'));
  const isCore = btn.getAttribute('data-core') === '1';
  const doRemove = function() {
    setPolicyLines(type, policyLines(type).filter(function(l){ return l !== line; }));
    renderPolicyChips();
  };
  if (isCore) {
    customConfirm('⚠️ Remove a shipped core block?\n\n"' + line + '" is a safety-net pattern that ships with Eshu by default. Removing it lets this kind of command reach JIT/approval.\n\nOnly continue if you are sure. Re-add it any time via "↺ Core defaults".').then(function(ok){ if (ok) doRemove(); });
  } else {
    doRemove();
  }
});

async function fetchPolicies() {
  const res = await fetch('/api/policies'); const data = await res.json();
  document.getElementById('policy-exact').value = data.exact_whitelist || '';
  document.getElementById('policy-regex-white').value = data.regex_whitelist || '';
  document.getElementById('policy-regex-black').value = data.regex_blacklist || '';
  document.getElementById('policy-version-label').textContent = 'v' + (data.policy_version || '?');
  policiesCache = policiesCache || {};
  policiesCache.corePatterns = data.core_patterns || [];
  policiesCache.hardPatterns = data.hard_patterns || [];
  _committedPolicy = {
    exact: data.exact_whitelist || '',
    regexWhite: data.regex_whitelist || '',
    regexBlack: data.regex_blacklist || '',
  };
  renderPolicyChips();
}
async function fetchPolicyChanges() {
  const res = await fetch('/api/policy_changes'); const changes = await res.json();
  const changesList = document.getElementById('policy-changes-list');
  if (changes.length === 0) { changesList.innerHTML = '<p class="text-muted">No policy changes recorded.</p>'; return; }
  changesList.innerHTML = changes.map(function(c) {
    return '<div class="p-3 rounded-lg border mb-2 bg-base">' +
    '<div class="flex items-center justify-between"><p class="text-xs text-muted">' + formatDateTime(c.timestamp) + ' - <span class="text-main">' + c.policy_type + '</span></p>' +
    '<button class="chip-btn info flex-shrink-0" onclick="rollbackPolicyChange(' + c.id + ')">Restore</button></div>' +
    '<div class="mt-2 text-xs font-mono"><p class="text-danger">- ' + c.old_content.split('\n').filter(function(l){return l.trim()!=='';}).join('<br>- ') + '</p><p class="text-success">+ ' + c.new_content.split('\n').filter(function(l){return l.trim()!=='';}).join('<br>+ ') + '</p></div></div>';
  }).join('');
}

function closePolicyPreview() {
  document.getElementById('policy-preview-modal').classList.add('hidden');
}

async function previewPolicyImpact() {
  const modal = document.getElementById('policy-preview-modal');
  const body = document.getElementById('policy-preview-body');
  const daysEl = document.getElementById('preview-days');
  const days = daysEl ? parseInt(daysEl.value) : 30;
  modal.classList.remove('hidden');
  body.innerHTML = '<p class="text-muted">Evaluating command history…</p>';
  try {
    const res = await authFetch('/api/policies/preview', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        exact_whitelist: document.getElementById('policy-exact').value || '',
        regex_whitelist: document.getElementById('policy-regex-white').value || '',
        regex_blacklist: document.getElementById('policy-regex-black').value || '',
        days: days
      })
    });
    if (res.status === 401) { await checkAuth(); return; }
    if (!res.ok) throw new Error((await res.json().catch(function(){ return {}; })).detail || 'Preview failed');
    renderPolicyPreview(await res.json());
  } catch(e) {
    body.innerHTML = '<p class="text-danger">Preview failed: ' + escapeHtml(e.message || e) + '</p>';
  }
}

function renderPolicyPreview(data) {
  const body = document.getElementById('policy-preview-body');
  const actionBadge = function(a) {
    if (a === 'blocked') return '<span class="badge badge-blocked">Blocked</span>';
    if (a === 'auto_approved') return '<span class="badge badge-auto">Auto-Approved</span>';
    return '<span class="badge badge-pending">JIT</span>';
  };

  let html = '<p class="text-muted mb-3">' + data.total + ' distinct command(s) in the last ' + data.window_days + ' days' +
    (data.fatal_count ? ' · ' + data.fatal_count + ' permanently blocked (non-relaxable)' : '') + '.</p>';

  if (data.changed === 0) {
    html += '<p class="text-success">No commands would change behaviour under this staged policy.</p>';
  } else {
    html += '<div class="flex flex-wrap gap-2 mb-3">' +
      '<span class="badge badge-blocked">' + data.newly_blocked + ' newly blocked</span>' +
      '<span class="badge badge-approved">' + data.newly_allowed + ' newly allowed</span>' +
      '<span class="badge badge-auto">' + data.newly_auto + ' → auto-approve</span>' +
      '<span class="badge badge-pending">' + data.newly_jit + ' → JIT</span>' +
      '</div>';

    html += '<div class="space-y-2">' + data.flips.map(function(f) {
      return '<div class="p-2 rounded-lg border bg-base">' +
        '<div class="flex items-center gap-2 mb-1">' + actionBadge(f.before) + '<span class="text-muted">→</span>' + actionBadge(f.after) + '</div>' +
        '<code class="font-mono text-xs block truncate" title="' + escapeHtml(f.command) + '">' + escapeHtml(f.command) + '</code>' +
        '<div class="text-xs text-muted mt-1">' + escapeHtml(f.reason) + '</div>' +
        '</div>';
    }).join('') + '</div>';

    if (data.flips.length < data.changed) {
      html += '<p class="text-xs text-muted mt-2">…showing ' + data.flips.length + ' of ' + data.changed + ' changed commands.</p>';
    }
  }
  body.innerHTML = html;
}
async function savePolicies() {
  previewPolicyImpact();
}

async function confirmSavePolicies() {
  closePolicyPreview();
  const btn = document.getElementById('save-policies-btn'); btn.disabled = true; btn.textContent = 'Saving...';
  await savePoliciesSilent();
  btn.disabled = false; btn.textContent = 'Save & Push Policies';
  showToast('✅ Policies saved & pushed', 'success');
  setTimeout(fetchGateways, 4000);
  if (!document.getElementById('policy-changes-modal').classList.contains('hidden')) fetchPolicyChanges();
}

async function rollbackPolicyChange(changeId) {
  if (!(await customConfirm('Restore policy to the state before this change? This will overwrite the current policy and push to gateways.'))) return;
  try {
    const res = await authFetch('/api/policies/rollback/' + changeId, { method: 'POST' });
    if (!res.ok) { showToast('Rollback failed', 'error'); return; }
    const data = await res.json();
    showToast('Policy ' + data.policy_type + ' restored', 'success');
    fetchPolicies();
    fetchPolicyChanges();
    setTimeout(fetchGateways, 4000);
    refreshSuggestions();
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}
async function testPolicy() {
  const cmd = document.getElementById('tester-input').value.trim(); if (!cmd) return;
  const rd = document.getElementById('tester-result'); rd.innerHTML = '<span class="text-muted">Testing...</span>'; rd.classList.remove('hidden');
  try {
    const res = await fetch('/api/policies/test?command=' + encodeURIComponent(cmd)); const data = await res.json();
    let bg, border, text, icon, desc;
    if (data.action === 'blocked') { bg='rgba(251,146,60,0.1)'; border='rgba(251,146,60,0.3)'; text='#fb923c'; icon='🔴'; desc='Blocked: <code style="color:#fb923c;">' + (data.details[0] ? data.details[0].pattern : 'unknown') + '</code>'; }
    else if (data.action === 'auto_approved') { bg='rgba(74,222,128,0.1)'; border='rgba(74,222,128,0.3)'; text='var(--status-success)'; icon='✅'; desc=(data.details[0] && data.details[0].type === 'exact_whitelist') ? 'Auto-Approved (Exact Allowlist)' : 'Auto-Approved: <code style="color:var(--status-success);">' + (data.details[0] ? data.details[0].pattern : '') + '</code>'; }
    else { bg='rgba(251,191,36,0.1)'; border='rgba(251,191,36,0.3)'; text='var(--status-warning)'; icon='⏳'; desc='Would require JIT Approval'; }
    let memLine = '';
    try {
      const mc = await authFetch('/api/policies/check?command=' + encodeURIComponent(cmd));
      if (mc.ok) {
        const m = await mc.json();
        const mark = function(flag, danger) {
          return '<span class="' + (flag ? (danger ? 'text-danger' : 'text-success') : 'text-muted') + '">' + (flag ? '✓' : '—') + '</span>';
        };
        memLine = '<div class="text-xs mt-2 text-muted">In lists: Exact ' + mark(m.in_exact_whitelist, false) +
          ' · Regex ' + mark(m.in_regex_whitelist, false) +
          ' · Blocklist ' + mark(m.in_regex_blacklist, true) + '</div>';
      }
    } catch(e) {}
    rd.innerHTML = '<div class="result-box" style="background:' + bg + ';color:' + text + ';border-color:' + border + ';">' + icon + ' <strong>' + data.action.replace('_',' ').toUpperCase() + '</strong> — ' + desc + '</div>' + memLine + testerAddButtons(data.action, cmd);
  } catch(err) { rd.innerHTML = '<div class="result-box" style="background:var(--bg-base);color:var(--text-muted);">⚠️ ' + err.message + '</div>'; }
}

function testerAddButtons(action, cmd) {
  const enc = encodeURIComponent(cmd);
  if (action === 'blocked') {
    return '<div class="flex gap-2 mt-2">' +
      '<button class="btn btn-xs" onclick="addToPolicy(decodeURIComponent(\'' + enc + '\'), \'regex_blacklist\')">＋ Blocklist</button>' +
      '</div>';
  }
  return '<div class="flex gap-2 mt-2">' +
    '<button class="btn btn-xs" onclick="addToPolicy(decodeURIComponent(\'' + enc + '\'), \'exact_whitelist\')">＋ Exact</button>' +
    '<button class="btn btn-xs" onclick="addToPolicy(decodeURIComponent(\'' + enc + '\'), \'regex_whitelist\')">＋ Regex</button>' +
    '</div>';
}
async function seedEdge() {
  var confirmMsg = 'Seed Edge from current Build? This replaces any existing Edge installer.';
  try {
    var sr = await authFetch('/api/dev/status');
    if (sr.ok) {
      var sd = await sr.json();
      if (sd.pipeline_state === 'dev_in_progress') {
        confirmMsg = 'A previous dev push is active. Seed Edge will stop it and deploy the latest Build to dev gateways. Continue?';
      }
    }
  } catch(e) {}
  if (!(await customConfirm(confirmMsg))) return;
  const btn = document.getElementById('seed-edge-btn'); btn.disabled = true; btn.textContent = 'Seeding...';
  try {
    const res = await authFetch('/api/dev/seed', { method: 'POST' });
    const data = await res.json();
    showToast('Edge seeded from Build ' + data.version, 'success');
    fetchDevStatus();
  } catch(err) { showToast('Failed to seed Edge: ' + err.message, 'error'); }
  btn.disabled = false; btn.textContent = 'Seed Edge';
}

async function pushToDev() {
  const devCount = typeof _devGateways !== 'undefined' ? _devGateways.length : 0;
  if (devCount === 0) { showToast('No dev-mode gateways exist. Set a gateway to dev mode first.', 'error'); return; }
  if (!(await customConfirm('Push Edge to ' + devCount + ' dev gateway(s)? Production gateways are not affected.'))) return;
  const btn = document.getElementById('push-to-dev-btn'); btn.disabled = true; btn.textContent = 'Pushing...';
  try {
    const r = await authFetch('/api/dev-gateways/push', { method: 'POST' });
    const data = await r.json();
    showToast('Dev update triggered for ' + (data.dev_gateway_count||0) + ' gateway(s)', 'success');
  } catch(e) { showToast('Failed to push', 'error'); }
  btn.disabled = false; btn.textContent = 'Push to Dev Gateways';
  fetchDevStatus();
}

async function promoteToGolden() {
  if (!(await customConfirm('Deploy Edge to Fleet? All connected gateways will update within ~3 seconds.\n\nA backup of the current Build will be saved.'))) return;
  const btn = document.getElementById('promote-golden-btn'); btn.disabled = true; btn.textContent = 'Deploying...';
  try {
    const res = await authFetch('/api/dev/promote', { method: 'POST' });
    const data = await res.json();
    showToast('Deployed ' + data.version + ' to ' + data.gateway_count + ' gateway(s)', 'success');
    fetchDevStatus();
  } catch(err) { showToast('Failed to deploy: ' + err.message, 'error'); }
  btn.disabled = false; btn.textContent = 'Deploy to Fleet';
  setTimeout(fetchGateways, 6000);
}

async function rollbackGolden() {
  if (!(await customConfirm('Rollback Fleet to the previous version from backup?\n\nALL gateways will update within ~3 seconds.'))) return;
  const btn = document.getElementById('rollback-golden-btn'); btn.disabled = true; btn.textContent = 'Rolling back...';
  try {
    const res = await authFetch('/api/dev/rollback', { method: 'POST' });
    const data = await res.json();
    showToast('Fleet rolled back to ' + data.version + ' for ' + data.gateway_count + ' gateway(s)', 'success');
    fetchDevStatus();
  } catch(err) { showToast('Failed to rollback: ' + err.message, 'error'); }
  btn.disabled = false; btn.textContent = 'Rollback Fleet';
  setTimeout(fetchGateways, 6000);
}

async function fetchDevStatus() {
  try {
    const res = await authFetch('/api/dev/status');
    if (!res.ok) return;
    const data = await res.json();
    const goldenLabel = document.getElementById('golden-version-label');
    const edgeLabel = document.getElementById('edge-version-label');
    const fleetLabel = document.getElementById('fleet-version-label');
    const seedBtn = document.getElementById('seed-edge-btn');
    const pushBtn = document.getElementById('push-to-dev-btn');
    const promoteBtn = document.getElementById('promote-golden-btn');
    const rollbackBtn = document.getElementById('rollback-golden-btn');

    if (goldenLabel) goldenLabel.innerHTML = (data.dashboard_version || 'unset') + (data.golden_hash ? ' <span class="hash-tag">(' + data.golden_hash + ')</span>' : '');
    if (edgeLabel) edgeLabel.innerHTML = (data.edge_exists ? (data.dashboard_version || 'staged') : 'not staged') + (data.edge_hash ? ' <span class="hash-tag">(' + data.edge_hash + ')</span>' : '');
    if (fleetLabel) fleetLabel.innerHTML = (data.dashboard_version || 'unset') + (data.deployed_hash ? ' <span class="hash-tag">(' + data.deployed_hash + ')</span>' : '');

    // Button states
    if (seedBtn) seedBtn.disabled = false;
    if (pushBtn) pushBtn.disabled = !data.edge_exists || data.dev_gateway_count === 0;
    if (promoteBtn) promoteBtn.disabled = !data.edge_exists;
    if (rollbackBtn) rollbackBtn.disabled = !data.backup_exists;

    const countLabel = document.getElementById('dev-gateway-count');
    if (countLabel) countLabel.textContent = data.dev_gateway_count;

    // Pipeline banner
    const banner = document.getElementById('pipeline-banner');
    if (banner && data.pipeline_state) {
      banner.classList.remove('hidden', 'needs_seed', 'ready_for_dev', 'dev_in_progress', 'ready_for_promote');
      if (data.pipeline_state === 'clear') {
        banner.classList.add('hidden');
      } else {
        banner.classList.add(data.pipeline_state);
        var nDev = data.dev_gateway_count, nAll = data.gateway_count || 1;
        var msgs = {
          needs_seed: '<strong>New Build available</strong> &mdash; Edge is stale. <button onclick="seedEdge()" class="link-btn">Seed Edge &rarr;</button>',
          ready_for_dev: '<strong>Edge ready</strong> &mdash; <button onclick="pushToDev()" class="link-btn">Push to Dev Gateways &rarr;</button> (' + nDev + ' dev device' + (nDev !== 1 ? 's' : '') + ')',
          dev_in_progress: '<strong>Dev push active</strong> &mdash; ' + nDev + ' dev gate' + (nDev !== 1 ? 'ways' : 'way') + ' updating. Verify, then Deploy to Fleet.',
          ready_for_promote: '<strong>Dev verified</strong> &mdash; <button onclick="promoteToGolden()" class="link-btn">Deploy to Fleet &rarr;</button> (' + nAll + ' gateway' + (nAll !== 1 ? 's' : '') + ')'
        };
        banner.innerHTML = msgs[data.pipeline_state] || '';
      }
    }
  } catch(e) {}
}

function generateRegex() {
  const cmd = document.getElementById('tester-input').value.trim(); if (!cmd) return;
  const rd = document.getElementById('tester-result');
  rd.innerHTML = '<div class="regex-box"><p class="mb-1 text-danger">Generated Regex:</p><code>^' + cmd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$</code><p class="mt-1 text-muted">Copy into Regex Allowlist.</p></div>';
  rd.classList.remove('hidden');
}

// ── Enrollment ───────────────────────────────────────────────────────────
let currentToken = '', tokenExpiryTime = 0, tokenTimer = null, tokenStatusPoller = null;
function startTokenCountdown() {
  if (tokenTimer) clearInterval(tokenTimer);
  const el = document.getElementById('token-countdown');
  tokenTimer = setInterval(function() {
    const remaining = Math.ceil((tokenExpiryTime - Date.now() / 1000));
    if (remaining <= 0) { el.textContent = '⏰ Token expired'; el.style.color = 'var(--brand-red)'; clearInterval(tokenTimer); tokenTimer = null; if (tokenStatusPoller) { clearInterval(tokenStatusPoller); tokenStatusPoller = null; } document.getElementById('enroll-command').value = ''; document.getElementById('copy-enroll-btn').disabled = true; currentToken = ''; }
    else if (remaining <= 30) { el.textContent = '⏰ ' + remaining + 's remaining'; el.style.color = 'var(--status-warning)'; }
    else { el.textContent = '⏱ ' + remaining + 's remaining'; el.style.color = 'var(--text-muted)'; }
  }, 1000);
}
async function fetchEnrollData() {
  const res = await fetch('/api/enroll/keys'); const keys = await res.json();
  document.getElementById('display-eshu-key').textContent = keys.eshu_ssh_key ? keys.eshu_ssh_key.substring(0, 60) + '...' : 'Not configured';
  document.getElementById('edit-eshu-key').value = keys.eshu_ssh_key || '';
}
function toggleKeyEdit() {
  const d=document.getElementById('keys-display'), e=document.getElementById('keys-edit'), b=document.getElementById('edit-keys-btn');
  if(e.classList.contains('hidden')){d.classList.add('hidden');e.classList.remove('hidden');b.textContent='Cancel';}
  else{d.classList.remove('hidden');e.classList.add('hidden');b.textContent='Edit Keys';}
}
async function saveKeys() {
  await authFetch('/api/enroll/keys', { method: 'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({eshu_key: document.getElementById('edit-eshu-key').value}) });
  toggleKeyEdit(); fetchEnrollData(); showToast('✅ Keys saved', 'success');
}
function updateEnrollCommand() {
  if (!currentToken) return;
  const baseUrl = document.getElementById('enroll-base-url').value.trim() || window.location.origin;
  document.getElementById('enroll-command').value = 'curl -s "' + baseUrl + '/api/enroll?token=' + currentToken + '" | bash';
}
async function generateToken() {
  const btn = document.getElementById('generate-token-btn'); btn.disabled = true; btn.textContent = 'Generating...';
  const res = await authFetch('/api/enroll/generate', { method: 'POST' }); const data = await res.json();
  currentToken = data.token;
  const baseUrl = document.getElementById('enroll-base-url').value.trim() || window.location.origin;
  document.getElementById('enroll-command').value = 'curl -s "' + baseUrl + '/api/enroll?token=' + currentToken + '" | bash';
  tokenExpiryTime = Date.now() / 1000 + 120;
  document.getElementById('token-countdown').classList.remove('hidden');
  document.getElementById('copy-enroll-btn').disabled = false;
  startTokenCountdown();
  startTokenStatusPolling();
  btn.disabled = false; btn.textContent = 'Generate New Token';
}

function startTokenStatusPolling() {
  if (tokenStatusPoller) clearInterval(tokenStatusPoller);
  tokenStatusPoller = setInterval(async function() {
    if (!currentToken) { clearInterval(tokenStatusPoller); return; }
    try {
      const res = await fetch('/api/enroll/token-status?token=' + currentToken + '&_=' + Date.now());
      const data = await res.json();
      if (data.used) {
        if (tokenTimer) { clearInterval(tokenTimer); tokenTimer = null; }
        clearInterval(tokenStatusPoller);
        document.getElementById('enroll-command').value = '';
        document.getElementById('copy-enroll-btn').disabled = true;
        document.getElementById('token-countdown').textContent = '✅ Token consumed by gateway';
        document.getElementById('token-countdown').style.color = 'var(--status-success)';
        currentToken = '';
      }
    } catch(e) {}
  }, 3000);
}
function openRemoveGatewayModal(ip, hostname, isOnline) {
  _removeIp = ip;
  _removeHostname = hostname;
  document.getElementById('remove-gateway-name').textContent = hostname + ' (' + ip + ')';
  const statusEl = document.getElementById('remove-gateway-status');
  statusEl.textContent = isOnline
    ? 'This gateway is currently online.'
    : 'This gateway is currently offline — if it will not come back, use “Force remove from dashboard”.';
  statusEl.className = 'text-xs mb-3 ' + (isOnline ? 'text-success' : 'text-warning');
  document.getElementById('remove-gateway-modal').classList.remove('hidden');
}

function closeRemoveGatewayModal() {
  document.getElementById('remove-gateway-modal').classList.add('hidden');
  _removeIp = null;
  _removeHostname = null;
}

async function confirmRemoteUninstall() {
  const ip = _removeIp, hostname = _removeHostname;
  if (!ip) return;
  closeRemoveGatewayModal();
  try {
    const res = await authFetch('/api/gateways/' + ip + '/uninstall', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) { showToast('❌ ' + (data.detail || 'Failed to trigger uninstall'), 'error'); return; }
    showToast('✅ Uninstall triggered for ' + data.hostname, 'success');
    _uninstallingIps[ip] = true;
    fetchGateways();
    openUninstallModal(ip, data.hostname || hostname);
    pollUninstallProgress(ip);
  }
  catch(err) { showToast('❌ Failed', 'error'); }
}

// ── Uninstall Progress Modal ─────────────────────────────────────────────
function openUninstallModal(ip, hostname) {
  _uninstallIp = ip;
  document.getElementById('uninstall-modal-gateway').textContent = hostname + ' (' + ip + ')';
  document.getElementById('uninstall-progress-fill').style.width = '0%';
  document.getElementById('uninstall-modal-step').textContent = 'Starting…';
  var note = document.getElementById('uninstall-modal-note');
  note.classList.add('hidden');
  document.getElementById('uninstall-modal').classList.remove('hidden');
}

function closeUninstallModal() {
  document.getElementById('uninstall-modal').classList.add('hidden');
  if (_uninstallIp) { delete _uninstallingIps[_uninstallIp]; _uninstallIp = null; }
  fetchGateways();
}

async function pollUninstallProgress(ip) {
  const STEPS = ['started','stopping_poller','removing_binaries','removing_sudoers','removing_policies','removing_runtime','removing_user','cleaning_keys','deregistering','complete'];
  const STEP_LABELS = { started:'Initiated', stopping_poller:'Stopping poller', removing_binaries:'Removing binaries', removing_sudoers:'Removing sudoers', removing_policies:'Removing policies', removing_runtime:'Removing runtime', removing_user:'Removing user', cleaning_keys:'Cleaning SSH', deregistering:'Deregistering', complete:'Complete' };
  let currentStep = -1;
  let stopped = false;
  const finish = function() {
    if (stopped) return;
    stopped = true;
    if (_uninstallIp !== ip) return;
    document.getElementById('uninstall-progress-fill').style.width = '100%';
    document.getElementById('uninstall-modal-step').textContent = '✅ Removed';
    setTimeout(closeUninstallModal, 1800);
  };
  const tryFetch = async function() {
    if (stopped) return;
    if (_uninstallIp !== ip) { stopped = true; return; }
    try {
      const res = await fetch('/api/uninstall-progress/' + ip);
      const data = await res.json();
      if (stopped) return;
      if (data.progress) {
        // progress is stored as "step:message"
        var step = String(data.progress).split(':')[0];
        var stepIdx = STEPS.indexOf(step);
        if (stepIdx > currentStep) currentStep = stepIdx;
        var note = document.getElementById('uninstall-modal-note');
        if (stepIdx === 0 && note && !note.classList.contains('hidden')) note.classList.add('hidden');
        if (step === 'complete') { finish(); return; }
        var pct = currentStep < 0 ? 0 : Math.min(100, Math.round((currentStep / (STEPS.length - 1)) * 100));
        document.getElementById('uninstall-progress-fill').style.width = pct + '%';
        document.getElementById('uninstall-modal-step').textContent = STEP_LABELS[step] || step;
      } else {
        // No progress yet — either the poller hasn't picked up the trigger
        // (next poll ≤30s) or the gateway already deregistered. Keep polling.
        var gw = (_gatewaysData || []).find(function(g) { return g.ip === ip; });
        if (!gw) { finish(); return; }
        var noteEl = document.getElementById('uninstall-modal-note');
        noteEl.classList.remove('hidden');
        noteEl.textContent = 'Waiting for the gateway to pick up the uninstall (next poll ≤30s)…';
      }
      setTimeout(tryFetch, 2000);
    } catch(e) {
      setTimeout(tryFetch, 2000);
    }
  };
  tryFetch();
}
async function handleForceRemoveGateway(ip, hostname) {
  if (!(await customConfirm('Force remove ' + hostname + ' (' + ip + ') from the dashboard?\n\n⚠️ This only deletes the dashboard record — it does NOT uninstall anything on the host.\n\nIf the gateway is still running, it will re-register on its next poll (~30s), so this may not be permanent. Use it only for decommissioned or never-completed hosts.'))) return;
  try {
    const res = await authFetch('/api/gateways/' + ip, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) { showToast('❌ ' + (data.detail || 'Failed'), 'error'); return; }
    showToast('🗑 ' + data.hostname + ' removed from dashboard', 'success');
    if (_removeIp === ip) closeRemoveGatewayModal(); else fetchGateways();
  }
  catch(err) { showToast('❌ Failed', 'error'); }
}
function copyEnrollCommand() { document.getElementById('enroll-command').select(); document.execCommand('copy'); showToast('✅ Copied', 'success'); }

// ── Audit Log ────────────────────────────────────────────────────────────
const AUDIT_ICONS = { enrolled: '✅', version_updated: '⬆️', disconnected: '⚠️', policy_committed: '🔄', update_triggered: '📡', uninstall_triggered: '🗑', uninstalled: '❌', password_changed: '🔐', password_cleared: '🔓', window_created: '🪟', window_modified: '✏️', window_deleted: '🗑️', window_toggled: '🔘', window_claimed: '🟢', dev_update_pushed: '🧪', gateway_mode_changed: '🔄' };
const AUDIT_LABELS = { enrolled: 'enrolled', version_updated: 'updated', disconnected: 'disconnected', policy_committed: 'Policies committed', update_triggered: 'Update triggered', uninstall_triggered: 'Uninstall triggered', uninstalled: 'Uninstalled', password_changed: 'Password changed', password_cleared: 'Password removed', window_created: 'Window created', window_modified: 'Window updated', window_deleted: 'Window deleted', window_toggled: 'Window toggled', window_claimed: 'Window claimed', dev_update_pushed: 'Dev push', gateway_mode_changed: 'Gateway mode' };
let _auditLogSearchQuery = '';
function onAuditLogSearch() {
  _auditLogSearchQuery = document.getElementById('audit-log-search').value.trim();
  fetchAuditLog();
}
async function fetchAuditLog() {
  try {
    let url = '/api/audit_log';
    if (_auditLogSearchQuery) url += '?search=' + encodeURIComponent(_auditLogSearchQuery);
    const res = await fetch(url); const logs = await res.json();
    document.getElementById('audit-log-count').textContent = logs.length + ' events';
    const list = document.getElementById('audit-log-list');
    if (logs.length === 0) { list.innerHTML = '<p class="text-muted">No events recorded yet.</p>'; return; }
    list.innerHTML = logs.map(function(l) {
      const icon = AUDIT_ICONS[l.event_type] || '📌', label = AUDIT_LABELS[l.event_type] || l.event_type;
      const time = new Date(l.timestamp * 1000).toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', second:'2-digit' });
      const host = l.hostname ? ' ' + gwPill(l.hostname) + ' <strong class="text-main">' + l.hostname + '</strong>' + (l.gateway_ip ? ' ('+l.gateway_ip+')' : '') : '';
      const detail = l.details ? '<span class="block text-xs mt-0.5 text-muted">' + l.details.replace(/</g,'<').replace(/>/g,'>') + '</span>' : '';
      const borderColor = l.event_type === 'disconnected' ? 'var(--brand-red)' : l.event_type === 'version_updated' || l.event_type === 'password_changed' ? 'var(--status-success)' : 'var(--border-color)';
      return '<div class="p-2 rounded-lg border text-xs bg-base" style="border-color:' + borderColor + ';">' +
        '<span class="text-muted text-xs">' + time + '</span> ' + icon + ' ' + label + host + detail + '</div>';
    }).join('');
  } catch(e) {}
}

// ── Notes ────────────────────────────────────────────────────────────────
async function fetchNotes() { const res = await fetch('/api/notes'); const data = await res.json(); document.getElementById('notes-content').value = data.content || ''; }
async function saveNotes() { await authFetch('/api/notes', { method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({content: document.getElementById('notes-content').value}) }); showToast('✅ Notes saved', 'success'); }

async function fetchNotifyConfig() {
  try {
    const r = await authFetch('/api/notify-config');
    if (!r.ok) return;
    const c = await r.json();
    document.getElementById('notify-webhook-url').value = c.url || '';
    document.getElementById('notify-dashboard-url').value = c.dashboard_url || '';
    const events = (c.events || '').split(',').map(function(e){return e.trim();});
    document.getElementById('notify-event-jit').checked = events.indexOf('jit') !== -1;
    document.getElementById('notify-event-window').checked = events.indexOf('window') !== -1;
    document.getElementById('notify-event-blocked').checked = events.indexOf('blocked') !== -1;
    document.getElementById('notify-event-offline').checked = events.indexOf('offline') !== -1;
    document.getElementById('notify-event-online').checked = events.indexOf('online') !== -1;
  } catch(e) {}
}
function onNotifyConfigChange() {
  document.getElementById('notify-save-status').textContent = '';
}
async function saveNotifyConfig() {
  var url = document.getElementById('notify-webhook-url').value.trim();
  var dashUrl = document.getElementById('notify-dashboard-url').value.trim();
  var events = [];
  if (document.getElementById('notify-event-jit').checked) events.push('jit');
  if (document.getElementById('notify-event-window').checked) events.push('window');
  if (document.getElementById('notify-event-blocked').checked) events.push('blocked');
  if (document.getElementById('notify-event-offline').checked) events.push('offline');
  if (document.getElementById('notify-event-online').checked) events.push('online');
  try {
    var r = await authFetch('/api/notify-config', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({url:url, events:events.join(','), dashboard_url:dashUrl}) });
    if (!r.ok) { var d = await r.json().catch(function(){}); throw new Error((d && d.detail) || 'Save failed'); }
    document.getElementById('notify-save-status').textContent = '✅ Saved';
    showToast('✅ Notification config saved', 'success');
  } catch(e) { showToast('❌ ' + e.message, 'error'); }
}
async function testNotify() {
  try {
    var r = await authFetch('/api/notify-test', { method:'POST' });
    if (!r.ok) throw new Error('Test failed');
    var d = await r.json();
    showToast(d.delivered ? '🔊 Test notification delivered' : '❌ Test failed — webhook unreachable or no URL set', d.delivered ? 'success' : 'error');
  } catch(e) { showToast('❌ Test failed — ' + e.message, 'error'); }
}


// ── Toast ────────────────────────────────────────────────────────────────
function showToast(message, type) {
  type = type || 'success';
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = 'fixed top-4 left-1/2 transform -translate-x-1/2 z-50 px-6 py-3 rounded-lg text-sm font-medium shadow-lg toast';
  if (type === 'success') toast.classList.add('toast-success');
  else if (type === 'error') toast.classList.add('toast-error');
  else toast.classList.add('toast-info');
  toast.classList.remove('hidden');
  setTimeout(function() { toast.classList.add('hidden'); }, 3000);
}

// ── Dropdowns ────────────────────────────────────────────────────────────
let _queueClearBefore = 0; // Timestamp — hide requests created before this time

function toggleDropdown() { document.getElementById('purge-dropdown').classList.toggle('show'); }
async function purgeHistory(period) {
  document.getElementById('purge-dropdown').classList.remove('show');
  const label = period === 'all' ? 'all time' : period;
  if (!(await customConfirm('Clear the queue of requests older than ' + label + '?\n\n⚠ This hides them from the queue. Stats are preserved.'))) return;
  const now = Math.floor(Date.now() / 1000);
  const offsets = { '30m': 1800, '1h': 3600, '1d': 86400, '2d': 172800, '7d': 604800 };
  if (period === 'all') _queueClearBefore = now;
  else _queueClearBefore = now - (offsets[period] || 3600);
  showToast('✅ Queue cleared — stats unchanged', 'success');
  fetchRequests();
}
let _dbPurgeBefore = 0;
async function purgeDatabase(period) {
  const label = period === 'all' ? 'all time' : period;
  if (!(await customConfirm('⚠ PERMANENTLY DELETE all requests older than ' + label + ' from the database?\n\nThis CANNOT be undone. Stats, charts, and top commands will be affected.'))) return;
  await authFetch('/api/requests?older_than=' + period, { method: 'DELETE' });
  _queueClearBefore = 0; // Reset client-side filter after DB purge
  showToast('✅ Database purged (older than ' + label + ')', 'success');
  fetchRequests();
  if (document.getElementById('view-stats') && !document.getElementById('view-stats').classList.contains('hidden')) fetchStatistics();
}
document.addEventListener('click', function(e) { if (!e.target.closest('#purge-btn') && !e.target.closest('#purge-dropdown')) document.getElementById('purge-dropdown').classList.remove('show'); });

// ── Custom Confirm Dialog ────────────────────────────────────────────────
let _confirmResolve = null;
function customConfirm(msg) {
  return new Promise(function(resolve) {
    _confirmResolve = function(result) {
      document.getElementById('custom-confirm-overlay').classList.add('hidden');
      resolve(result);
    };
    document.getElementById('custom-confirm-msg').innerText = msg;
    document.getElementById('custom-confirm-overlay').classList.remove('hidden');
  });
}

// ── Modals ───────────────────────────────────────────────────────────────
function openModal(modalId) { document.getElementById(modalId).classList.remove('hidden'); fetchPolicyChanges(); }
function closeModal(modalId) { document.getElementById(modalId).classList.add('hidden'); }

// ── Statistics ────────────────────────────────────────────────────────
let _statsData = null;
let _selectedStatsGateways = new Set();

async function fetchStatistics() {
  try {
    var daysEl = document.getElementById('stats-days');
    var days = daysEl ? parseInt(daysEl.value) : 14;
    var res = await fetch('/api/statistics?days=' + days + '&extended=1');
    _statsData = await res.json();
    // Default: all gateways selected
    if (_selectedStatsGateways.size === 0 && _statsData.per_gateway.length > 0) {
      _statsData.per_gateway.forEach(function(g) { _selectedStatsGateways.add(g.ip); });
    }
    renderStatistics();
  } catch(e) {}
}

function toggleStatsGateway(ip) {
  if (_selectedStatsGateways.has(ip)) {
    _selectedStatsGateways.delete(ip);
  } else {
    _selectedStatsGateways.add(ip);
  }
  renderStatistics();
}

function renderStatistics() {
  if (!_statsData) return;
  var d = _statsData;

  // Build gateway filter pills
  var filterEl = document.getElementById('stats-gateway-filters');
  var pillHtml = '';
  d.per_gateway.forEach(function(g) {
    var id = deriveGatewayIdentity(g.hostname || g.ip);
    var selected = _selectedStatsGateways.has(g.ip);
    pillHtml += '<span class="stats-gw-pill ' + (selected ? 'selected' : '') + '" onclick="toggleStatsGateway(\'' + g.ip + '\')">' +
      '<span class="stats-gw-dot" style="background:' + id.color + ';"></span>' +
      escapeHtml(g.hostname || g.ip) + devBadge({mode: g.mode}) + ' <span class="text-xs text-muted">' + g.total + '</span>' +
      '</span>';
  });
  if (d.per_gateway.length > 0) {
    pillHtml += '<span class="text-xs text-muted cursor-pointer ml-1" onclick="_selectedStatsGateways.clear();_statsData.per_gateway.forEach(function(g){_selectedStatsGateways.add(g.ip)});renderStatistics();">All</span>';
    pillHtml += '<span class="text-xs text-muted cursor-pointer" onclick="_selectedStatsGateways.clear();renderStatistics();">None</span>';
  }
  filterEl.innerHTML = pillHtml;

  // Render summary table
  var filtered = d.per_gateway.filter(function(g) { return _selectedStatsGateways.has(g.ip); });
  filtered.sort(function(a, b) { return b.total - a.total; });
  var tbody = document.getElementById('stats-summary-body');
  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="px-4 py-3 text-muted">No gateways selected.</td></tr>';
  } else {
    tbody.innerHTML = filtered.map(function(g) {
      var id = deriveGatewayIdentity(g.hostname || g.ip);
      var jitApproved = (g.total || 0) - (g.auto_approved || 0) - (g.blocked || 0) - (g.denied || 0);
      var autoPct = g.total > 0 ? Math.round(((g.auto_approved || 0) + jitApproved) / g.total * 100) : 0;
      return '<tr>' +
        '<td>' + gwPill(g.hostname || g.ip) + '</td>' +
        '<td>' + escapeHtml(g.hostname || g.ip) + devBadge(g) + '</td>' +
        '<td class="text-muted font-mono">' + escapeHtml(g.ip) + '</td>' +
        '<td class="text-right font-semibold">' + (g.total || 0) + '</td>' +
        '<td class="text-right text-info">' + (g.auto_approved || 0) + '</td>' +
        '<td class="text-right text-success">' + jitApproved + '</td>' +
        '<td class="text-right stat-blocked">' + (g.blocked || 0) + '</td>' +
        '<td class="text-right text-danger">' + (g.denied || 0) + '</td>' +
        '<td class="text-right"><span class="font-semibold ' + (autoPct >= 80 ? 'pct-high' : autoPct >= 50 ? 'pct-mid' : 'pct-low') + '">' + autoPct + '%</span></td>' +
        '</tr>';
    }).join('');
  }

  // Render top commands
  var topCmdsBody = document.getElementById('stats-topcmds-body');
  if (d.top_commands && d.top_commands.length > 0) {
    var maxCount = d.top_commands[0].count;
    topCmdsBody.innerHTML = d.top_commands.map(function(c, i) {
      var pct = maxCount > 0 ? (c.count / maxCount) * 100 : 0;
      return '<tr>' +
        '<td class="text-center text-xs text-muted">' + (i + 1) + '</td>' +
        '<td class="font-mono text-xs">' +
          '<div class="flex items-center gap-2">' +
            '<span class="flex-1">' + escapeHtml(c.command) + '</span>' +
            '<div class="bar-track"><div class="fill" style="width:' + pct + '%;"></div></div>' +
          '</div>' +
          (c.description ? '<div class="cmd-desc">' + escapeHtml(c.description) + '</div>' : '') +
        '</td>' +
        '<td class="text-xs text-muted">' + c.pct + '%</td>' +
        '<td class="text-right font-mono text-xs">' + c.count + '</td>' +
        '</tr>';
    }).join('');
  } else {
    topCmdsBody.innerHTML = '<tr><td colspan="4" class="px-4 py-3 text-muted">No command data available.</td></tr>';
  }

  // Summary cards at the top
  var summaryEl = document.getElementById('stats-summary-cards');
  var totalCmds = 0, totalJit = 0, pctSum = 0;
  d.daily.forEach(function(day) { totalCmds += day.total; });
  if (d.automation_trend && d.automation_trend.length > 0) {
    d.automation_trend.forEach(function(a) { pctSum += a.automation_pct; });
  }
  var autoAvg = d.automation_trend && d.automation_trend.length > 0 ? Math.round(pctSum / d.automation_trend.length) : 0;
  if (d.per_gateway) {
    d.per_gateway.forEach(function(g) {
      totalJit += (g.total || 0) - (g.auto_approved || 0) - (g.blocked || 0) - (g.denied || 0);
    });
  }
  var gh = d.gateway_health || {};
  var autoCls = autoAvg >= 80 ? 'stat-approved' : autoAvg >= 50 ? 'stat-pending' : 'stat-denied';
  var gwCls = (gh.online_gateways || 0) === (gh.total_gateways || 0) ? 'stat-approved' : 'stat-pending';
  summaryEl.innerHTML =
    '<div class="stat-card"><div class="stat-value text-main">' + totalCmds + '</div><div class="stat-label">Commands</div></div>' +
    '<div class="stat-card"><div class="stat-value ' + autoCls + '">' + autoAvg + '%</div><div class="stat-label">Automation</div></div>' +
    '<div class="stat-card"><div class="stat-value ' + gwCls + '">' + (gh.online_gateways || 0) + ' / ' + (gh.total_gateways || 0) + '</div><div class="stat-label">Gateways Online</div></div>' +
    '<div class="stat-card"><div class="stat-value stat-auto">' + totalJit + '</div><div class="stat-label">JIT Approvals</div></div>';

  // Denied commands
  var deniedEl = document.getElementById('stats-denied');
  if (d.top_denied && d.top_denied.length > 0) {
    var maxDenied = d.top_denied[0].count;
    deniedEl.innerHTML = d.top_denied.map(function(c, i) {
      var pct = maxDenied > 0 ? (c.count / maxDenied) * 100 : 0;
      return '<div class="flex items-center gap-2 mb-1 text-xs">' +
        '<span class="w-4 text-right text-muted flex-shrink-0">' + (i + 1) + '</span>' +
        '<div class="flex-1 flex items-center gap-2 min-w-0">' +
          '<span class="flex-1 font-mono text-danger truncate">' + escapeHtml(c.command) + '</span>' +
          '<div class="bar-track sm"><div class="fill danger" style="width:' + pct + '%;"></div></div>' +
        '</div>' +
        '<span class="w-5 text-right text-muted flex-shrink-0">' + c.count + '</span>' +
        '</div>';
    }).join('');
  } else {
    deniedEl.innerHTML = '<p class="text-muted">No denied commands in this period.</p>';
  }

  // Command categories
  var catEl = document.getElementById('stats-categories');
  if (d.category_counts && d.category_counts.length > 0) {
    var maxCat = d.category_counts[0].count;
    catEl.innerHTML = d.category_counts.map(function(c) {
      var pct = maxCat > 0 ? Math.round(c.count / maxCat * 100) : 0;
      var catColors = {
        'Storage & FS': '#60a5fa', 'System Services': '#34d399', 'Network': '#f472b6',
        'Package Management': '#fbbf24', 'Containers': '#a78bfa', 'VMs': '#fb923c',
        'Proxmox': '#f87171', 'Monitoring': '#2dd4bf', 'Databases': '#818cf8',
        'Security': '#e879f9', 'Version Control': '#fca5a5', 'Scripting': '#6ee7b7',
        'Task Scheduling': '#fde68a', 'System Info': '#93c5fd', 'System': '#fdba74',
        'Editing': '#c4b5fd', 'Utilities': '#a7f3d0'
      };
      var color = catColors[c.category] || 'var(--text-muted)';
      return '<div class="flex items-center gap-2 mb-1 text-xs">' +
        '<span class="inline-flex w-3 h-3 flex-shrink-0" style="background:' + color + ';border-radius:3px;"></span>' +
        '<span class="flex-1">' + c.category + '</span>' +
        '<div class="bar-track sm"><div class="fill" style="width:' + pct + '%;background:' + color + ';"></div></div>' +
        '<span class="w-10 text-right text-muted flex-shrink-0">' + c.pct + '%</span>' +
        '</div>';
    }).join('');
  } else {
    catEl.innerHTML = '<p class="text-muted">No category data available.</p>';
  }
}

// ── Gateway Health Checker ────────────────────────────────────────────
async function checkGatewayHealth() {
  try {
    const res = await fetch('/api/gateways'); const data = await res.json();
    const now = Math.floor(Date.now() / 1000);
    const staleCount = data.filter(function(g) { return (now - g.last_seen) > 120; }).length;
    const noTokenCount = data.filter(function(g) { return !g.has_token; }).length;
    const total = data.length;
    const dot = document.getElementById('health-dot');
    const pill = document.getElementById('health-pill');
    if (!dot) return;
    dot.classList.remove('green', 'orange', 'red');
    if (staleCount === 0 && noTokenCount === 0) { dot.classList.add('green'); if (pill) pill.title = total + ' gateway(s) online — all healthy'; }
    else if (staleCount <= 1 && noTokenCount <= 1) { dot.classList.add('orange'); if (pill) pill.title = (noTokenCount > 0 ? noTokenCount + ' gateway(s) missing API token. ' : '') + (staleCount > 0 ? staleCount + ' gateway offline. ' : '') + (total - staleCount) + ' online'; }
    else { dot.classList.add('red'); if (pill) pill.title = (noTokenCount > 0 ? noTokenCount + ' gateway(s) missing API token. ' : '') + (staleCount + ' gateways offline — ') + (total - staleCount) + ' online'; }
    // Detect offline/online transitions for notifications
    var nextOffline = new Set();
    data.forEach(function(g) {
      var gateNow = now - Math.floor(g.last_seen);
      if (gateNow > 120) nextOffline.add(g.ip);
    });
    // Newly offline
    nextOffline.forEach(function(ip) {
      if (!_knownOfflineIps.has(ip) && notifyOffline) {
        playJitChime(false);
        if (_notifPerm === 'granted') {
          var n = new Notification('📡 Gateway Offline', { body: ip + ' has been unreachable for over 2 minutes', tag: 'eshu-offline', icon: '/static/eshu_logo.png' });
          n.onclick = function() { window.focus(); n.close(); };
        }
      }
    });
    // Back online
    _knownOfflineIps.forEach(function(ip) {
      if (!nextOffline.has(ip)) {
        /* gateway reconnected — could notify, but typically silent */
      }
    });
    _knownOfflineIps = nextOffline;
  } catch(e) {}
}

// ── Version ──────────────────────────────────────────────────────────────
async function fetchVersion() {
  try {
    const res = await fetch('/api/version');
    const data = await res.json();
    var label = document.getElementById('dashboard-version-label');
    label.textContent = data.version || '?';
    if (data.dev_mode) {
      label.innerHTML = (data.version || '?') + ' <span class="dev-badge">DEV</span>';
    }
    document.title = 'Eshu Gateway | ' + (data.version || 'v0.1.0') + ' Control Center';
  } catch(e) {}
}

// ── Startup ──────────────────────────────────────────────────────────────
requestNotifPermission();
(async function() {
  await checkAuth();
  if (!document.getElementById('login-overlay').classList.contains('hidden')) { document.getElementById('login-password').focus(); }
  else if (!document.getElementById('setup-overlay').classList.contains('hidden')) { document.getElementById('setup-password').focus(); }
  else { initDashboard(); checkGatewayHealth(); }
})();

setInterval(function() {
  if (!document.getElementById('login-overlay').classList.contains('hidden')) return;
  if (!document.getElementById('setup-overlay').classList.contains('hidden')) return;
  fetchRequests();
}, 3000);
setInterval(function() {
  if (!document.getElementById('login-overlay').classList.contains('hidden')) return;
  if (!document.getElementById('setup-overlay').classList.contains('hidden')) return;
  if(document.getElementById('view-gateways').classList.contains('block')) fetchGateways();
}, 5000);
setInterval(decrementTimers, 1000);
setInterval(updateFleetCountdowns, 1000);
setInterval(function() {
  if (!document.getElementById('login-overlay').classList.contains('hidden')) return;
  if (!document.getElementById('setup-overlay').classList.contains('hidden')) return;
  checkGatewayHealth();
  fetchGateways();
  fetchFreezeStatus();
  fetchFleetCommands();
}, 10000);
setInterval(function() {
  if (!document.getElementById('login-overlay').classList.contains('hidden')) return;
  if (!document.getElementById('setup-overlay').classList.contains('hidden')) return;
  if (document.getElementById('view-windows').classList.contains('block')) fetchWindowsTable();
}, 30000);

// ── Integrations & MCP ──────────────────────────────────────────────────
let _selectedIntegration = null;

async function fetchIntegrations() {
  fetchAgentTokens();
  fetchIntegrationList();
  fetchIntegrationPending();
  fetchIntegrationCalls();
  fetchMcpSettings();
}

async function fetchMcpSettings() {
  const input = document.getElementById('mcp-allowed-hosts');
  const current = document.getElementById('mcp-allowed-hosts-current');
  if (!input) return;
  try {
    const res = await authFetch('/api/mcp-settings');
    if (!res.ok) return;
    const data = await res.json();
    input.value = data.allowed_hosts || '';
    current.textContent = data.allowed_hosts || 'none (loopback only)';
  } catch(e) {}
}

async function saveMcpSettings() {
  const v = document.getElementById('mcp-allowed-hosts').value.trim();
  try {
    const res = await authFetch('/api/mcp-settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ allowed_hosts: v }) });
    if (!res.ok) { const d = await res.json().catch(function() { return {}; }); showToast('❌ ' + (d.detail || 'Failed'), 'error'); return; }
    fetchMcpSettings();
    showToast('MCP access updated', 'success');
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

async function fetchAgentTokens() {
  const el = document.getElementById('agent-token-list');
  if (!el) return;
  try {
    const res = await authFetch('/api/agents');
    if (!res.ok) return;
    const agents = await res.json();
    if (!agents.length) { el.innerHTML = '<p class="text-muted">No agent tokens yet.</p>'; return; }
    el.innerHTML = agents.map(function(a) {
      var used = a.last_used_at ? new Date(a.last_used_at * 1000).toLocaleString() : 'never';
      return '<div class="flex items-center justify-between gap-2 p-2 rounded bg-black/20">' +
        '<div><div class="text-sm">' + esc(a.name) + (a.revoked ? ' <span class="text-danger">(revoked)</span>' : '') + '</div>' +
        '<div class="text-xs text-muted">last used: ' + used + '</div></div>' +
        '<button onclick="deleteAgentToken(' + a.id + ')" class="btn btn-xs btn-muted">Delete</button></div>';
    }).join('');
  } catch(e) {}
}

async function createAgentToken() {
  const name = document.getElementById('agent-token-name').value.trim();
  if (!name) { showToast('Agent name is required', 'error'); return; }
  try {
    const res = await authFetch('/api/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) });
    const data = await res.json();
    if (!res.ok) { showToast('❌ ' + (data.detail || 'Failed'), 'error'); return; }
    document.getElementById('agent-token-name').value = '';
    const box = document.getElementById('agent-token-result');
    box.classList.remove('hidden');
    box.innerHTML = '<strong>' + esc(name) + ' token (copy now — shown once):</strong><br>' + esc(data.token);
    fetchAgentTokens();
    showToast('Agent token created', 'success');
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

async function deleteAgentToken(id) {
  if (!(await customConfirm('Delete this agent token? The agent will lose access immediately.'))) return;
  try {
    await authFetch('/api/agents/' + id, { method: 'DELETE' });
    fetchAgentTokens();
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

async function fetchIntegrationList() {
  const el = document.getElementById('integration-list');
  if (!el) return;
  try {
    const res = await authFetch('/api/integrations');
    if (!res.ok) return;
    const ints = await res.json();
    if (!ints.length) { el.innerHTML = '<p class="text-muted">No integrations yet.</p>'; return; }
    el.innerHTML = ints.map(function(i) {
      var active = i.enabled ? 'text-success' : 'text-muted';
      return '<div class="p-2 rounded bg-black/20">' +
        '<div class="flex items-center justify-between gap-2">' +
        '<button class="text-sm text-left ' + active + '" onclick="selectIntegration(\'' + esc(i.name) + '\')">' + esc(i.name) + '</button>' +
        '<div class="flex gap-1">' +
        '<button onclick="seedProxmox(\'' + esc(i.name) + '\')" class="btn btn-xs btn-muted" title="Seed Proxmox tools">Seed</button>' +
        '<button onclick="deleteIntegration(\'' + esc(i.name) + '\')" class="btn btn-xs btn-muted">Delete</button></div></div>' +
        '<div class="text-xs text-muted">' + esc(i.base_url) + ' · auth: ' + esc(i.auth_type) + '</div></div>';
    }).join('');
  } catch(e) {}
}

async function createIntegration() {
  const name = document.getElementById('int-name').value.trim();
  const baseUrl = document.getElementById('int-base-url').value.trim();
  if (!name || !baseUrl) { showToast('Name and base URL are required', 'error'); return; }
  const payload = {
    name: name,
    base_url: baseUrl,
    auth_type: document.getElementById('int-auth-type').value,
    auth_header_name: document.getElementById('int-auth-header').value.trim(),
    secret: document.getElementById('int-secret').value,
  };
  try {
    const res = await authFetch('/api/integrations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) { showToast('❌ ' + (data.detail || 'Failed'), 'error'); return; }
    document.getElementById('int-name').value = '';
    document.getElementById('int-base-url').value = '';
    document.getElementById('int-secret').value = '';
    fetchIntegrationList();
    showToast('Integration added', 'success');
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

async function deleteIntegration(name) {
  if (!(await customConfirm('Delete integration "' + name + '" and all its tools?'))) return;
  try {
    await authFetch('/api/integrations/' + encodeURIComponent(name), { method: 'DELETE' });
    if (_selectedIntegration === name) _selectedIntegration = null;
    fetchIntegrationList();
    renderTools([]);
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

async function seedProxmox(name) {
  try {
    const res = await authFetch('/api/integrations/' + encodeURIComponent(name) + '/seed-proxmox', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) { showToast('❌ ' + (data.detail || 'Failed'), 'error'); return; }
    showToast('Seeded Proxmox tools (' + data.created + ' new, ' + data.updated + ' updated)', 'success');
    if (_selectedIntegration === name) fetchTools(name);
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

async function selectIntegration(name) {
  _selectedIntegration = name;
  fetchTools(name);
}

async function fetchTools(name) {
  try {
    const res = await authFetch('/api/integrations/' + encodeURIComponent(name) + '/tools');
    if (!res.ok) return;
    renderTools(await res.json(), name);
  } catch(e) {}
}

function renderTools(tools, name) {
  const el = document.getElementById('integration-tools-list');
  if (!el) return;
  if (!name || !tools.length) { el.innerHTML = '<p class="text-muted">No tools for this integration.</p>'; return; }
  el.innerHTML = '<div class="text-xs text-muted mb-2">Integration: <strong>' + esc(name) + '</strong></div>' + tools.map(function(t) {
    var badge = t.read_only ? '<span class="text-success">read</span>' : '<span class="text-warning">mutating (approval)</span>';
    return '<div class="flex items-center justify-between gap-2 p-2 rounded bg-black/20">' +
      '<div class="flex-1"><div class="text-sm">' + esc(t.name) + ' <span class="text-xs">' + badge + ' · ' + esc(t.method) + '</span></div>' +
      '<div class="text-xs text-muted">' + esc(t.description || '') + '</div></div>' +
      '<button onclick="toggleTool(' + t.id + ', ' + (t.enabled ? 'false' : 'true') + ')" class="btn btn-xs ' + (t.enabled ? 'btn-muted' : '') + '">' + (t.enabled ? 'Disable' : 'Enable') + '</button>' +
      '<button onclick="deleteTool(' + t.id + ')" class="btn btn-xs btn-muted">×</button></div>';
  }).join('');
}

async function toggleTool(id, enabled) {
  try {
    const res = await authFetch('/api/integrations/' + encodeURIComponent(_selectedIntegration) + '/tools/' + id + '/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: enabled }) });
    if (res.ok) fetchTools(_selectedIntegration);
  } catch(e) {}
}

async function deleteTool(id) {
  if (!(await customConfirm('Delete this tool?'))) return;
  try {
    await authFetch('/api/integrations/' + encodeURIComponent(_selectedIntegration) + '/tools/' + id, { method: 'DELETE' });
    fetchTools(_selectedIntegration);
  } catch(e) {}
}

async function fetchIntegrationPending() {
  const el = document.getElementById('integration-pending-list');
  if (!el) return;
  try {
    const res = await authFetch('/api/integration-calls/pending');
    if (!res.ok) return;
    const calls = await res.json();
    if (!calls.length) { el.innerHTML = '<p class="text-muted">Nothing pending.</p>'; return; }
    el.innerHTML = calls.map(function(c) {
      var args = Object.keys(c.payload || {}).map(function(k) { return k + '=' + c.payload[k]; }).join(', ');
      return '<div class="p-2 rounded bg-black/20 mb-2">' +
        '<div class="text-sm">' + esc(c.integration) + '.' + esc(c.tool) + ' <span class="text-xs text-muted">#' + c.id + '</span></div>' +
        '<div class="text-xs text-muted">' + esc(args || '') + '</div>' +
        (c.reason ? '<div class="text-xs italic mt-1">"' + esc(c.reason) + '"</div>' : '') +
        '<div class="flex gap-2 mt-2">' +
        '<button onclick="approveIntegrationCall(' + c.id + ')" class="btn btn-xs">Approve</button>' +
        '<button onclick="denyIntegrationCall(' + c.id + ')" class="btn btn-xs btn-muted">Deny</button></div></div>';
    }).join('');
  } catch(e) {}
}

async function approveIntegrationCall(id) {
  try {
    const res = await authFetch('/api/integration-calls/' + id + '/approve', { method: 'POST' });
    if (res.ok) { fetchIntegrationPending(); fetchIntegrationCalls(); showToast('Approved and executed', 'success'); }
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

async function denyIntegrationCall(id) {
  try {
    const res = await authFetch('/api/integration-calls/' + id + '/deny', { method: 'POST' });
    if (res.ok) { fetchIntegrationPending(); }
  } catch(e) { showToast('❌ Failed: ' + e.message, 'error'); }
}

async function fetchIntegrationCalls() {
  const el = document.getElementById('integration-calls-list');
  if (!el) return;
  try {
    const res = await authFetch('/api/integration-calls');
    if (!res.ok) return;
    const calls = await res.json();
    if (!calls.length) { el.innerHTML = '<p class="text-muted">No calls yet.</p>'; return; }
    el.innerHTML = calls.map(function(c) {
      var when = new Date(c.created_at * 1000).toLocaleString();
      var outcome = c.outcome === 'ok' ? '' : ' <span class="text-danger">(' + esc(c.outcome) + ')</span>';
      return '<div class="p-2 rounded bg-black/20 mb-1 text-xs">' +
        '<span class="text-main">' + esc(c.integration) + (c.tool ? '.' + esc(c.tool) : '') + '</span> ' +
        esc(c.method) + ' ' + esc(c.path) + ' · ' + (c.status_code || '—') + ' · ' + c.latency_ms + 'ms' + outcome +
        '<span class="text-muted"> · ' + when + '</span></div>';
    }).join('');
  } catch(e) {}
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(ch) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
  });
}

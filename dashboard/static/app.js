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
  // initials pills removed — gateways are shown by name only
  return '';
}

function devBadge(g) {
  if (!g || (g.mode || 'prod') !== 'dev') return '';
  return '<span class="dev-pill">DEV</span>';
}

function toggleMobileSidebar() {
  var sidebar = document.getElementById('main-sidebar');
  var overlay = document.getElementById('sidebar-overlay');
  if (!sidebar || !overlay) return;
  sidebar.classList.toggle('open');
  overlay.classList.toggle('show');
}
// ── Command Center: Context Panel ────────────────────────────
var _ccSelectedId = null;
var _ccFilter = 'all';
var _ccSelectedIds = new Set();
var _activeSessionSid = null;

function openContextPanel(reqId) {
  var panel = document.getElementById('cc-context');
  var empty = document.getElementById('cc-context-empty');
  var content = document.getElementById('cc-context-content');
  if (!panel || !empty || !content) return;
  panel.classList.remove('collapsed');
  _ccSelectedId = reqId;

  // Highlight selected card
  document.querySelectorAll('.jit-ticket').forEach(function(el) {
    el.classList.toggle('selected', el.dataset.id === reqId);
  });

  // Find request data from the rendered tickets or fetch
  var req = null;
  // Check SSH requests
  if (typeof requestsData !== 'undefined') {
    req = requestsData.find(function(r) { return String(r.id) === String(reqId); });
  }
  // Check window requests
  if (!req && typeof _pendingWinReqs !== 'undefined') {
    req = _pendingWinReqs.find(function(w) { return 'win-' + w.id === reqId; });
    if (req) req._type = 'window';
  }
  // Check integration calls
  if (!req && typeof _pendingIntegrationCalls !== 'undefined') {
    req = _pendingIntegrationCalls.find(function(c) { return 'int-' + c.id === reqId; });
    if (req) req._type = 'integration';
  }
  if (!req) {
    empty.classList.remove('hidden');
    content.classList.add('hidden');
    return;
  }

  empty.classList.add('hidden');
  content.classList.remove('hidden');

  // Display different fields based on request type
  if (req._type === 'integration') {
    document.getElementById('ctx-cmd').textContent = req.integration + ' / ' + req.tool;
    document.getElementById('ctx-human').textContent = req.reason || 'Integration call';
    document.getElementById('ctx-target').innerHTML =
      '<span class="ctx-meta-item">Integration: ' + escapeHtml(req.integration) + '</span>';
  } else if (req._type === 'window') {
    document.getElementById('ctx-cmd').textContent = req.command || '';
    document.getElementById('ctx-human').textContent = 'Window request';
    document.getElementById('ctx-target').innerHTML =
      '<span class="ctx-meta-item">Target: ' + escapeHtml(req.target_ip || '—') + '</span>' +
      (req.label ? '<span class="ctx-meta-item">Label: ' + escapeHtml(req.label) + '</span>' : '');
  } else {
    document.getElementById('ctx-cmd').textContent = req.command || '';
    document.getElementById('ctx-human').textContent = describeCmd(req.command) || 'No description available';
    var hostLabel = req.hostname || req.target_ip || 'Unknown host';
    document.getElementById('ctx-target').innerHTML =
      '<span class="ctx-meta-item">Host: ' + escapeHtml(hostLabel) + '</span>' +
      (req.target_ip && req.target_ip !== hostLabel ? '<span class="ctx-meta-item">IP: ' + escapeHtml(req.target_ip) + '</span>' : '');
  }
  document.getElementById('ctx-status').textContent = req.status || '—';
  document.getElementById('ctx-time').textContent = req.created_at ? formatTime(req.created_at) : '—';

  // Risk assessment
  var riskEl = document.getElementById('ctx-risk');
  if (req.risk || req.anomaly) {
    var html = '';
    if (req.risk) html += '<div class="ctx-meta-item" style="color:var(--status-warning);margin-bottom:4px"> ' + escapeHtml(req.risk) + '</div>';
    if (req.anomaly) html += '<div class="ctx-meta-item" style="color:var(--danger)"> ' + escapeHtml(req.anomaly) + '</div>';
    riskEl.innerHTML = html;
  } else {
    riskEl.innerHTML = '<span class="ctx-meta-item" style="color:var(--text-muted)">No flags</span>';
  }

  // Action buttons
  var actionsEl = document.getElementById('ctx-actions');
  var actionsHtml = '';
  if (req.status === 'pending' || !req._type) {
    actionsHtml = '<button onclick="handleAction(\'' + req.id + '\',\'approve\')" class="btn btn-approve btn-sm" style="flex:1">Approve</button>' +
      '<button onclick="handleAction(\'' + req.id + '\',\'deny\')" class="btn btn-deny btn-sm" style="flex:1">Deny</button>';
  } else if (req._type === 'window') {
    actionsHtml = '<button onclick="approveWindowReq(' + req.id + ')" class="btn btn-approve btn-sm" style="flex:1">Approve</button>' +
      '<button onclick="denyWindowReq(' + req.id + ')" class="btn btn-deny btn-sm" style="flex:1">Deny</button>';
  } else if (req._type === 'integration') {
    actionsHtml = '<button onclick="approveIntegrationCall(' + req.id + ')" class="btn btn-approve btn-sm" style="flex:1">Approve</button>' +
      '<button onclick="denyIntegrationCall(' + req.id + ')" class="btn btn-deny btn-sm" style="flex:1">Deny</button>';
  } else {
    actionsHtml = '<button onclick="closeContextPanel()" class="btn btn-muted btn-sm" style="flex:1">Close</button>';
  }
  actionsEl.innerHTML = actionsHtml;
}

function closeContextPanel() {
  var panel = document.getElementById('cc-context');
  if (panel) panel.classList.add('collapsed');
  _ccSelectedId = null;
  document.querySelectorAll('.jit-ticket.selected').forEach(function(el) {
    el.classList.remove('selected');
  });
}

// ── History View: SSH / Proxied Tabs ─────────────────────────
function switchHistoryTab(name) {
  document.querySelectorAll('.history-tab').forEach(function(tab) {
    tab.classList.toggle('active', tab.dataset.tab === name);
  });
  document.getElementById('history-ssh-panel').classList.toggle('hidden', name !== 'ssh');
  document.getElementById('history-proxied-panel').classList.toggle('hidden', name !== 'proxied');
  if (name === 'ssh') { fetchRequests(); }
  else { fetchIntegrationCalls(); }
}

// ── Command Center: Filter Tabs ──────────────────────────────
function ccFilter(filter) {
  _ccFilter = filter;
  document.querySelectorAll('.cc-filter-tab').forEach(function(tab) {
    tab.classList.remove('active');
  });
  event.target.closest('.cc-filter-tab').classList.add('active');
  // Re-render tickets with filter
  if (typeof renderJitTickets === 'function') renderJitTickets();
}

function updateFilterCounts(counts) {
  var total = 0;
  Object.keys(counts).forEach(function(k) { total += counts[k] || 0; });
  var el = document.getElementById('cc-count-all');
  if (el) el.textContent = total ? ' ' + total : '';
  var map = { pending: 'cc-count-pending', approved: 'cc-count-approved', 'auto-approved': 'cc-count-auto', blocked: 'cc-count-blocked', denied: 'cc-count-denied' };
  Object.keys(map).forEach(function(k) {
    var e = document.getElementById(map[k]);
    if (e) e.textContent = counts[k] ? ' ' + counts[k] : '';
  });
}

// ── Command Center: Bulk Selection ────────────────────────────
function toggleBulkSelect(reqId, ev) {
  if (ev && ev.shiftKey && _ccLastClicked) {
    // Range select
    var tickets = Array.from(document.querySelectorAll('.jit-ticket'));
    var from = tickets.findIndex(function(t) { return t.dataset.id === _ccLastClicked; });
    var to = tickets.findIndex(function(t) { return t.dataset.id === reqId; });
    if (from !== -1 && to !== -1) {
      var start = Math.min(from, to), end = Math.max(from, to);
      for (var i = start; i <= end; i++) {
        _ccSelectedIds.add(tickets[i].dataset.id);
        tickets[i].querySelector('.jit-check').classList.add('checked');
      }
    }
  } else {
    var checkEl = (ev && ev.currentTarget) ? ev.currentTarget : null;
    if (_ccSelectedIds.has(reqId)) {
      _ccSelectedIds.delete(reqId);
      if (checkEl) checkEl.classList.remove('checked');
    } else {
      _ccSelectedIds.add(reqId);
      if (checkEl) checkEl.classList.add('checked');
    }
  }
  _ccLastClicked = reqId;
  updateBulkBar();
}

function updateBulkBar() {
  var bar = document.getElementById('cc-bulk-bar');
  var count = document.getElementById('cc-bulk-count');
  if (!bar || !count) return;
  if (_ccSelectedIds.size > 0) {
    bar.classList.add('visible');
    count.textContent = _ccSelectedIds.size;
  } else {
    bar.classList.remove('visible');
  }
}

function clearSelection() {
  _ccSelectedIds.clear();
  document.querySelectorAll('.jit-check.checked').forEach(function(el) { el.classList.remove('checked'); });
  updateBulkBar();
}

function bulkApprove() {
  _ccSelectedIds.forEach(function(id) {
    if (id.indexOf('int-') === 0) { approveIntegrationCall(id.slice(4)); }
    else if (id.indexOf('win-') === 0) { approveWindowReq(id.slice(4)); }
    else { handleAction(id, 'approve'); }
  });
  clearSelection();
}

function bulkDeny() {
  _ccSelectedIds.forEach(function(id) {
    if (id.indexOf('int-') === 0) { denyIntegrationCall(id.slice(4)); }
    else if (id.indexOf('win-') === 0) { denyWindowReq(id.slice(4)); }
    else { handleAction(id, 'deny'); }
  });
  clearSelection();
}

var _ccLastClicked = null;
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
  btn.innerHTML = soundMuted ? 'Sound Off' : 'Sound On';
}
function testSound() { playJitChime(true); showToast('Chime played', 'success'); }

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
    new Notification('JIT Approval Required', { body: 'Test notification — 1 command requires your approval', tag: 'eshu-jit-test', icon: '/static/eshu_logo.png' });
    showToast('Test notification sent', 'success');
  } else if (_notifPerm === 'denied') { showToast('Browser notifications are blocked.', 'error'); }
  else { showToast('Click anywhere on the page to prompt notification permission.', 'error'); }
}
function notifyNewJIT(pendingCount) {
  const now = Date.now();
  if (now - lastJitNotifyTime < 5000) return;
  lastJitNotifyTime = now;
  playJitChime(false);
  if (_notifPerm === 'granted') {
    const n = new Notification('JIT Approval Required', { body: pendingCount === 1 ? '1 command requires your approval' : pendingCount + ' commands require your approval', tag: 'eshu-jit', icon: '/static/eshu_logo.png' });
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
  if (target === 'history') { fetchRequests(); fetchIntegrationCalls(); }
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
      ? '<span class="text-success"> Password protection <strong>enabled</strong>.</span>'
      : '<span class="text-warning"> No password set yet — complete setup to protect the dashboard.</span>';
  } catch(e) {}
}
async function setDashboardPassword() {
  const pw = document.getElementById('new-password').value.trim();
  if (!pw || pw.length < 4) { showToast('Password must be at least 4 characters', 'error'); return; }
  try {
    const res = await authFetch('/api/auth/set-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) });
    if (res.ok) { document.getElementById('new-password').value = ''; refreshPasswordUI(); showToast('Password updated', 'success'); }
    else { const data = await res.json().catch(function() { return {}; }); showToast('' + (data.detail || 'Failed'), 'error'); }
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
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
    showToast(_devToolsEnabled ? 'Development tools enabled' : 'Development tools disabled', 'success');
  } catch(e) {
    _devToolsEnabled = !_devToolsEnabled;
    showToast('Failed to update setting', 'error');
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
  var label = val === 'off' ? 'Off' : val === 'dev' ? 'Dev' : 'Prod';
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
      showToast('' + data.stale_gateways.length + ' gateway(s) are on old versions: ' + data.stale_gateways.join(', ') + '. Run Update Gateways first.', 'error');
    } else {
      showToast('Dev update triggered for ' + (data.dev_gateway_count||0) + ' gateway(s)' + (data.dev_gateway_names ? ': ' + data.dev_gateway_names.join(', ') : ''), 'success');
    }
  } catch(e) { showToast('Failed to push', 'error'); }
  btn.disabled = false; btn.textContent = 'Push to Dev Gateways';
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
  document.getElementById('win-modal-title').textContent = 'Create Approved Window';
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

function winDowStrip(dow) {
  var html = '<span class="dow-strip">';
  for (var i = 0; i < 7; i++) {
    html += '<span class="dow-day' + ((dow & DAY_BITS[i]) ? ' on' : '') + '" title="' + DAY_NAMES[i] + '">' + DAY_NAMES[i] + '</span>';
  }
  return html + '</span>';
}

function winScheduleCell(w) {
  var dow = Number(w.days_of_week) || 0;
  var et = Number(w.execution_time) || 0;
  var ws = Number(w.window_start) || 0;
  var html = '';
  if (dow || et) {
    html += winDowStrip(dow);
    if (et) {
      var h = String(Math.floor(et / 60)).padStart(2,'0');
      var m = String(et % 60).padStart(2,'0');
      html += '<br><span class="text-muted">' + h + ':' + m + ' UTC</span>';
    }
  } else if (ws) {
    var d = new Date(ws * 1000);
    html += 'Once: ' + d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
  } else {
    html += 'N/A';
  }
  var exp = w.expires_at ? Number(w.expires_at) : 0;
  if (exp) {
    var ed = new Date(exp * 1000);
    var expStr = ed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
    var past = Math.floor(Date.now() / 1000) > exp;
    html += '<br><span class="win-expiry" style="color:' + (past ? 'var(--brand-red)' : 'var(--status-warning)') + ';">Expires: ' + expStr + (past ? ' (expired)' : '') + '</span>';
  }
  return html;
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
      var stat = w.enabled ? '<span class="badge badge-approved cursor-pointer" onclick="toggleWindow(' + w.id + ',true)" title="Click to disable"> On</span>' :
        (w.status === 'pending_review' ? '<span class="badge badge-pending"> Review</span>' :
         (w.status === 'denied' ? '<span class="badge badge-window-rejected"> Denied</span>' :
          '<span class="badge badge-expired cursor-pointer" onclick="toggleWindow(' + w.id + ',false)" title="Click to enable"> Off</span>'));
      var gw = gwMap[w.target_ip] || {};
      var originBadge = (w.origin === 'ai')
        ? '<span class="origin-ai" title="Inbound — AI requested">AI</span> '
        : '<span class="origin-human" title="Outbound — operator created">Human</span> ';
      var labelHtml = originBadge + (w.label ? '<span class="text-main font-medium">' + escapeHtml(w.label) + '</span><br>' : '') +
        '<code class="text-xs font-mono text-muted" title="' + (w.token||'') + '">' + (w.token||'').substring(0, 8) + '…</code>' +
        ' <span onclick="copyToClipboard(\'' + (w.token||'') + '\')" class="cursor-pointer text-xs opacity-40 hover:opacity-100" title="Copy token">Copy</span>';
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
        '<td class="text-xs text-muted">' + winScheduleCell(w) + (nextRunStr ? '<br>' + nextRunStr : '') + '</td>' +
        '<td class="text-center text-xs text-muted">' + execInfo + '</td>' +
        '<td class="text-right"><div class="flex items-center justify-end gap-1">' +
          (w.status === 'pending_review'
            ? '<button onclick="approveWindowReq(' + w.id + ')" class="btn btn-approve btn-xs"> Approve</button>' +
              '<button onclick="denyWindowReq(' + w.id + ')" class="btn btn-deny btn-xs"> Deny</button>'
            : '<button onclick="openEditWindowModal(' + w.id + ')" class="btn btn-muted btn-xs" title="Edit">Edit</button>' +
              '<button onclick="showWindowHistory(' + w.id + ')" class="btn btn-muted btn-xs" title="Usage history">History</button>' +
              '<button onclick="deleteWindow(' + w.id + ')" class="btn btn-deny btn-xs" title="Delete">Delete</button>') +
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
  document.getElementById('win-modal-title').textContent = 'Edit Approved Window';
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
    if (data.action === 'blocked') { bg='rgba(251,146,60,0.1)'; border='rgba(251,146,60,0.3)'; text='#fb923c'; icon=''; desc='This command is BLOCKED by policy — cannot create window.'; }
    else if (data.action === 'auto_approved') { bg='rgba(74,222,128,0.1)'; border='rgba(74,222,128,0.3)'; text='var(--status-success)'; icon=''; desc='Already auto-approved — no window needed.'; }
    else { bg='rgba(96,165,250,0.1)'; border='rgba(96,165,250,0.3)'; text='var(--status-info)'; icon=''; desc='Would require JIT — a window will auto-approve this.'; }
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
    parts.push('Single-use');
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
    parts.push(' ' + dayStr + ' at ' + hh + ':' + mm + ' UTC');
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
    showToast(isEdit ? 'Window updated' : 'Window created', 'success');
    fetchWindowsTable();
    if (!isEdit) _winEditId = data.id;
    document.getElementById('save-window-btn').textContent = 'Save Changes';
    document.getElementById('win-modal-title').textContent = 'Edit Approved Window';
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
          var icon = ok ? '<span class="text-success">✓</span>' : '<span class="stat-blocked">✕</span>';
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
async function initDashboard() { updateNotifyUI(); fetchVersion(); fetchRequests(); fetchCmdDescs(); fetchSuggestions(); refreshPasswordUI(); fetchFreezeStatus(); fetchFleetCommands(); fetchGateways(); fetchIntegrations(); loadSessionNames(); }

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
    // Also load pending integration (API) calls awaiting approval
    try {
      const icRes = await fetch('/api/integration-calls/pending');
      _pendingIntegrationCalls = icRes.ok ? await icRes.json() : [];
    } catch(e) { _pendingIntegrationCalls = []; }
    detectNewIntegrationCalls();
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
  // Detect new auto-approved commands → radar flash + sound
  var autoData = newData.filter(function(r) { return r.status === 'auto-approved'; });
  var newAuto = autoData.filter(function(r) { return !_knownAutoIds.has(r.id); });
  autoData.forEach(function(r) { _knownAutoIds.add(r.id); });
  if (newAuto.length > 0) {
    var lastAuto = newAuto[newAuto.length - 1];
    flashRadarNode(lastAuto.target_ip || lastAuto.hostname, 'auto');
    playAutoChime();
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
          var nb = new Notification('Command Blocked', { body: 'Blocked: ' + (lastBlocked.command.length > 80 ? lastBlocked.command.substring(0,80) + '...' : lastBlocked.command), tag: 'eshu-blocked', icon: '/static/eshu_logo.png' });
          nb.onclick = function() { window.focus(); switchView('home'); nb.close(); };
        }
      }
    }
  }
  // Detect new blocked commands → radar flash + sound (always, regardless of notify toggle)
  var allBlocked = newData.filter(function(r) { return r.status === 'blocked'; });
  var newBlockedFlash = allBlocked.filter(function(r) { return !_knownBlockedFlashIds.has(r.id); });
  newBlockedFlash.forEach(function(r) { _knownBlockedFlashIds.add(r.id); });
  if (newBlockedFlash.length > 0) {
    var lastB = newBlockedFlash[newBlockedFlash.length - 1];
    flashRadarNode(lastB.target_ip || lastB.hostname, 'blocked');
    playBlockedChime();
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

function detectNewIntegrationCalls() {
  var calls = _pendingIntegrationCalls || [];
  var currentIds = new Set();
  calls.forEach(function(c) { currentIds.add(c.id); });
  var newIds = [];
  currentIds.forEach(function(id) { if (!_knownIntegrationCallIds.has(id)) newIds.push(id); });
  _knownIntegrationCallIds = currentIds;
  if (newIds.length > 0 && notifyJIT) {
    var now = Date.now();
    if (now - lastJitNotifyTime >= 5000) {
      lastJitNotifyTime = now;
      playJitChime(false);
      var badge = document.getElementById('jit-new-badge');
      if (badge) { badge.classList.remove('hidden'); setTimeout(function() { badge.classList.add('hidden'); }, 5000); }
      if (_notifPerm === 'granted') {
        var n = new Notification('API Approval Required', { body: calls.length === 1 ? '1 API call requires your approval' : calls.length + ' API calls require your approval', tag: 'eshu-api-call', icon: '/static/eshu_logo.png' });
        n.onclick = function() { window.focus(); switchView('home'); n.close(); };
      }
    }
  }
}

// ── JIT Ticket Rendering ─────────────────────────────────────────────────
var GATEWAY_DISCONNECTED_AFTER = 60; // seconds without contact before a gateway is reported disconnected

function emptyStateSignature() {
  var now = Math.floor(Date.now() / 1000);
  var gwSig = (_gatewaysData || []).map(function(g){
    var stale = (now - (g.last_seen || 0)) >= GATEWAY_DISCONNECTED_AFTER;
    return (g.hostname || g.ip) + ':' + (stale ? '0' : '1');
  }).sort().join('|');
  // Include session count so recent sessions panel re-renders
  var sessionCount = 0;
  (requestsData || []).forEach(function(r) { if (r.session_id && r.session_id !== 'unknown') sessionCount++; });
  return gwSig + '@' + sessionCount;
}

// ── Constellation starfield (replaces the radar) ────────────────────────
var CONSTELLATIONS = [
  {name:'Orion', meaning:'the hunter', pts:[[.50,.06],[.43,.18],[.57,.18],[.46,.30],[.50,.32],[.54,.30],[.46,.52],[.56,.52]], lines:[[0,1],[0,2],[1,3],[2,5],[3,4],[4,5],[3,6],[5,7]]},
  {name:'Ursa Major', meaning:'the great bear', pts:[[.30,.20],[.38,.16],[.46,.22],[.50,.32],[.46,.44],[.36,.54],[.26,.54]], lines:[[0,1],[1,2],[2,6],[6,0],[2,3],[3,4],[4,5],[5,6]]},
  {name:'Cassiopeia', meaning:'the queen', pts:[[.30,.36],[.38,.28],[.48,.34],[.58,.28],[.66,.36]], lines:[[0,1],[1,2],[2,3],[3,4]]},
  {name:'Cygnus', meaning:'the swan', pts:[[.50,.06],[.50,.18],[.50,.32],[.50,.46],[.42,.28],[.58,.28]], lines:[[0,1],[1,2],[2,3],[4,2],[2,5]]},
  {name:'Lyra', meaning:'the lyre', pts:[[.58,.14],[.46,.30],[.52,.36],[.64,.36],[.58,.30]], lines:[[0,1],[1,2],[2,3],[3,4],[4,0],[0,2]]}
];
var LAYOUTS = 9, ASPECT = 0.62;
var _layout = parseInt(sessionStorage.getItem('eshu_layout') || '-1', 10);
if (isNaN(_layout) || _layout < 0 || _layout >= LAYOUTS) { _layout = Math.floor(Math.random() * LAYOUTS); sessionStorage.setItem('eshu_layout', _layout); }
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;var t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
function shuffleArr(a,R){for(var i=a.length-1;i>0;i--){var j=Math.floor(R()*(i+1));var t=a[i];a[i]=a[j];a[j]=t;}return a;}
function fitSafe(pts,x0,x1,y0,y1){
  var mnx=1e9,mxx=-1e9,mny=1e9,mxy=-1e9;
  pts.forEach(function(p){mnx=Math.min(mnx,p.x);mxx=Math.max(mxx,p.x);mny=Math.min(mny,p.y);mxy=Math.max(mxy,p.y);});
  if(mnx>=x0&&mxx<=x1&&mny>=y0&&mxy<=y1) return pts;
  var s=Math.min((x1-x0)/Math.max(mxx-mnx,1),(y1-y0)/Math.max(mxy-mny,1));
  var ncx=(x0+x1)/2, ncy=(y0+y1)/2, cxs=(mnx+mxx)/2, cys=(mny+mxy)/2;
  return pts.map(function(p){return {x:ncx+(p.x-cxs)*s, y:ncy+(p.y-cys)*s};});
}
function gwIni(name){ name=String(name||'').toLowerCase().replace(/[^a-z0-9]/g,''); return name.substring(0,2).toUpperCase(); }

function buildStaticSky(){
  var layer=document.getElementById('dust-layer');
  if(!layer || layer.dataset.built) return;
  layer.dataset.built='1';
  for(var i=0;i<130;i++){
    var d=document.createElement('span');
    d.className='dust'+(Math.random()>0.5?' b':'');
    d.style.left=(Math.random()*100).toFixed(1)+'%'; d.style.top=(Math.random()*100).toFixed(1)+'%';
    layer.appendChild(d);
  }
  var ints=(_integrationsData||[]).filter(function(i){return i.enabled;});
  ints.forEach(function(integration,idx){
    var name=integration.name;
    var h=_starHash(name);
    var ang=(idx*360/Math.max(ints.length,1)+(h%28))*Math.PI/180;
    var rad=41+(h%8);
    var x=Math.max(7,Math.min(93,50+rad*Math.cos(ang))), y=Math.max(11,Math.min(89,50+rad*Math.sin(ang)*ASPECT));
    var sz=2+(h%2);
    var st=document.createElement('span');
    st.className='int-star twinkle';
    st.setAttribute('data-name',name); st.title=name;
    st.style.left=x.toFixed(1)+'%'; st.style.top=y.toFixed(1)+'%';
    st.style.width=sz+'px'; st.style.height=sz+'px';
    st.style.boxShadow='0 0 6px '+sz+'px rgba(205,216,230,0.16)';
    st.style.animationDelay='-'+(h%60)/10+'s';
    layer.appendChild(st);
  });
}

function placeGatewayNode(g,x,y,cls){
  var sky=document.getElementById('sky');
  if(!sky) return;
  var n=document.createElement('div');
  var name=g.hostname||g.ip||'gw';
  n.className='gw-node '+(cls||'');
  n.setAttribute('data-name',name); n.setAttribute('data-ip', g.ip||''); n.title=name;
  n.style.left=x.toFixed(1)+'%'; n.style.top=y.toFixed(1)+'%';
  n.innerHTML='<span class="n-dot"></span><span class="n-ini">'+gwIni(name)+'</span>'+(cls==='overridden'?'<span class="n-ovr">OVR</span>':'');
  sky.appendChild(n);
}

function buildStarfield(layoutIdx){
  var sky=document.getElementById('sky');
  var svg=document.getElementById('const-lines');
  var legend=document.getElementById('const-legend');
  if(!sky||!svg||!legend) return;
  _activeConst=null;
  if(_selGw) deselectGateway();
  sky.querySelectorAll('.gw-node,.field-star').forEach(function(el){el.remove();});
  svg.innerHTML='';
  var R=mulberry32((layoutIdx+1)*2654435761);
  var now=Math.floor(Date.now()/1000);
  var gws=(_gatewaysData||[]).filter(function(g){return g.hostname||g.ip;});
  var pendingSet={};
  (requestsData||[]).forEach(function(r){ if(r.status==='pending'){ var k=r.hostname||r.target_ip; if(k) pendingSet[k]=true; } });
  var N=gws.length;
  var minStars=Math.max(16,N);
  var shuffled=shuffleArr(CONSTELLATIONS.slice(), R);
  var chosen=[], total=0;
  for(var c=0;c<shuffled.length&&total<minStars;c++){ chosen.push(shuffled[c]); total+=shuffled[c].pts.length; }
  var pts=[], groups=[];
  chosen.forEach(function(cons){
    var ang=(R()-0.5)*0.9, cos=Math.cos(ang), sin=Math.sin(ang);
    var scale=0.62+R()*0.75;
    var cx=0.16+R()*0.68, cy=0.20+R()*0.55;
    var raw=cons.pts.map(function(p){
      var dx=p[0]-0.5, dy=p[1]-0.5;
      var rx=dx*cos-dy*sin, ry=dx*sin+dy*cos;
      return {x:(cx+rx*scale)*100, y:(cy+ry*scale*ASPECT)*100};
    });
    var fp=fitSafe(raw, 9, 91, 13, 87);
    var base=pts.length;
    fp.forEach(function(p){ pts.push(p); });
    var glines=cons.lines.map(function(ln){ var a=pts[base+ln[0]], b=pts[base+ln[1]]; return [a.x,a.y,b.x,b.y]; });
    groups.push({name:cons.name, meaning:cons.meaning, lines:glines});
  });
  svg.innerHTML=groups.map(function(grp){
    return '<g class="const-group" data-const="'+grp.name+'">'+grp.lines.map(function(l){
      return '<line class="const-line" x1="'+l[0].toFixed(2)+'" y1="'+l[1].toFixed(2)+'" x2="'+l[2].toFixed(2)+'" y2="'+l[3].toFixed(2)+'"/>'
        +'<line class="const-hit" x1="'+l[0].toFixed(2)+'" y1="'+l[1].toFixed(2)+'" x2="'+l[2].toFixed(2)+'" y2="'+l[3].toFixed(2)+'"/>';
    }).join('')+'</g>';
  }).join('');
  legend.innerHTML=groups.slice().sort(function(a,b){return a.name<b.name?-1:1;}).map(function(grp){
    return '<span class="cname" data-const="'+grp.name+'">'+grp.name.toUpperCase()+' · '+grp.meaning+'</span>';
  }).join('');
  var order=shuffleArr(pts.map(function(_,i){return i;}), R);
  var used={};
  order.forEach(function(pi,gi){
    if(gi<N){
      var g=gws[gi], p=pts[pi];
      var isOnline=(now-(g.last_seen||0))<GATEWAY_DISCONNECTED_AFTER;
      var isPending=!!pendingSet[g.hostname||g.ip];
      var isOverridden=(g.override_remaining||0)>0;
      var cls=isOverridden?'overridden':(isPending?'pending':(isOnline?'':'offline'));
      placeGatewayNode(g, p.x, p.y, cls);
      used[pi]=true;
    }
  });
  pts.forEach(function(p,pi){ if(used[pi]) return; var s=document.createElement('span'); s.className='field-star'; s.style.left=p.x.toFixed(1)+'%'; s.style.top=p.y.toFixed(1)+'%'; sky.appendChild(s); });
}

var _activeConst=null;
function selectConstellation(name){
  if(_activeConst===name) name=null;
  _activeConst=name;
  var legend=document.getElementById('const-legend');
  var svg=document.getElementById('const-lines');
  if(legend) legend.querySelectorAll('.cname').forEach(function(el){ el.classList.toggle('active', el.getAttribute('data-const')===name); });
  if(svg) svg.querySelectorAll('.const-group').forEach(function(g){ g.classList.toggle('active', g.getAttribute('data-const')===name); });
}

var _selGw=null;
function selectGwNode(node){
  if(_selGw===node) return;
  deselectGateway();
  _selGw=node;
  node.classList.add('selected');
  var name=node.getAttribute('data-name')||'';
  var el=document.getElementById('sel-hint-name');
  if(el) el.textContent=name;
  var hint=document.getElementById('sel-hint');
  if(hint) hint.classList.add('show');
}
function deselectGateway(){
  if(_selGw) _selGw.classList.remove('selected');
  _selGw=null;
  var hint=document.getElementById('sel-hint');
  if(hint) hint.classList.remove('show');
}
function openOverride(){
  if(!_selGw) return;
  openOverrideModal(_selGw.getAttribute('data-ip'), _selGw.getAttribute('data-name'));
}

var FLY_DIRS=[
  {out:'scale(1.55)', in:'scale(0.62)'},
  {out:'translateX(-17%) scale(1.08)', in:'translateX(17%) scale(0.9)'},
  {out:'translateX(17%) scale(1.08)', in:'translateX(-17%) scale(0.9)'},
  {out:'translateY(17%) scale(1.08)', in:'translateY(-17%) scale(0.9)'},
  {out:'translateY(-17%) scale(1.08)', in:'translateY(17%) scale(0.9)'},
  {out:'translate(-13%,-13%) scale(1.12)', in:'translate(13%,13%) scale(0.88)'},
  {out:'translate(13%,-13%) scale(1.12)', in:'translate(-13%,13%) scale(0.88)'}
];
function nextConstellation(){
  var sky=document.getElementById('sky');
  if(!sky) return;
  var d=FLY_DIRS[Math.floor(Math.random()*FLY_DIRS.length)];
  whoosh(true);
  sky.style.transition='transform .7s cubic-bezier(.5,0,.75,.4), opacity .5s ease';
  sky.style.transform=d.out;
  sky.style.opacity='0';
  clearTimeout(nextConstellation._t);
  nextConstellation._t=setTimeout(function(){
    _layout=(_layout+1)%LAYOUTS;
    sessionStorage.setItem('eshu_layout',_layout);
    buildStarfield(_layout);
    sky.style.transition='none';
    sky.style.transform=d.in;
    sky.style.opacity='0';
    void sky.offsetWidth;
    sky.style.transition='transform 1.3s cubic-bezier(.16,1,.3,1), opacity .6s ease';
    sky.style.transform='none';
    sky.style.opacity='1';
    whoosh(false);
  },680);
}

function renderEmptyState(total) {
  total = total || 0;
  var now = Math.floor(Date.now() / 1000);
  var gws = (_gatewaysData || []).filter(function(g){ return g.hostname || g.ip; });
  var enrolled = gws.length;
  var disconnected = gws.filter(function(g){ return (now - (g.last_seen || 0)) >= GATEWAY_DISCONNECTED_AFTER; }).length;
  var online = enrolled - disconnected;

  var recent = (requestsData || []).find(function(r){ return r.created_at; });
  var lastAgo = recent ? formatAgo(now - recent.created_at) : '';

  var tickerHtml = '';
  var auto = (requestsData || []).filter(function(r){ return r.status === 'auto-approved'; }).slice(0, 8);
  if (auto.length) {
    var tickItems = auto.map(function(r){
      return '<span class="cc-ticker-item"><span class="t">' + formatTime(r.created_at) + '</span> <span>' + escapeHtml(r.hostname || r.target_ip) + '</span> <span class="tag">auto</span> ' + escapeHtml(String(r.command || '').substring(0, 42)) + '</span>';
    }).join('');
    tickerHtml = '<div class="cc-ticker"><div class="cc-ticker-track">' + tickItems + tickItems + '</div></div>';
  }

  var sparkHtml = renderSparkline();
  var sessionsHtml = renderRecentSessions();

  var headline, sub;
  if (total > 0) {
    headline = 'At the threshold \u00b7 ' + total + ' waiting';
    sub = total + ' command' + (total > 1 ? 's' : '') + ' wait for your word';
  } else {
    headline = 'The way is open \u00b7 ' + online + '/' + enrolled + ' online';
    sub = 'all clear';
    if (disconnected > 0) sub += ' \u00b7 ' + disconnected + ' disconnected';
    if (lastAgo) sub += ' \u00b7 last activity ' + lastAgo;
  }

  return '<div class="cc-empty">' +
    '<div class="starfield-wrap">' +
      '<div class="dust-layer" id="dust-layer"></div>' +
      '<div class="sky" id="sky"><svg id="const-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"></svg></div>' +
      '<div class="const-legend" id="const-legend"></div>' +
    '</div>' +
    '<div class="jit-deck' + (total > 0 ? ' show' : '') + '" id="jit-deck"><div class="jit-deck-scroll" id="jit-deck-scroll"></div></div>' +
    '<div class="cc-empty-headline">' + headline + '</div>' +
    '<div class="cc-empty-sub">' + sub + '</div>' +
    tickerHtml +
    sparkHtml +
    sessionsHtml +
  '</div>';
}

function renderDeck(items) {
  var sc = document.getElementById('jit-deck-scroll');
  if (!sc) return;
  sc.innerHTML = items.map(function(item) { return renderJitItem(item); }).join('');
  sc.addEventListener('scroll', updateDeckFocus);
  setTimeout(updateDeckFocus, 60);
}
function updateDeckFocus() {
  var sc = document.getElementById('jit-deck-scroll');
  if (!sc) return;
  var center = sc.scrollLeft + sc.clientWidth / 2;
  sc.querySelectorAll('.jit-ticket').forEach(function(card) {
    var c = card.offsetLeft + card.offsetWidth / 2;
    var d = Math.min(1, Math.abs(c - center) / (sc.clientWidth / 2 + 40));
    card.style.opacity = (1 - d * 0.72).toFixed(2);
    card.style.transform = 'translateY(' + (d * 16).toFixed(1) + 'px) scale(' + (1 - d * 0.05).toFixed(3) + ')';
  });
}

function renderSparkline() {
  var now = Math.floor(Date.now() / 1000);
  var buckets = new Array(24).fill(0);
  var hasData = false;
  (requestsData || []).forEach(function(r){
    if(!r.created_at) return;
    var ageH = (now - r.created_at) / 3600;
    if(ageH < 0 || ageH >= 24) return;
    var idx = 23 - Math.floor(ageH);
    if(idx >=0 && idx <24){ buckets[idx]++; hasData=true; }
  });
  var max = Math.max.apply(null, buckets);
  var bars = buckets;
  if(!hasData || max === 0){
    // fallback gentle rhythm so sparkline is not flat
    bars = [2,3,2,1,1,2,4,6,8,7,9,11,10,13,12,15,14,12,16,13,9,6,4,3];
    max = 16;
  }
  var barsHtml = bars.map(function(v){
    var h = max ? Math.max(6, Math.round(v / max * 100)) : 6;
    var op = 0.12 + (h/100)*0.55;
    return '<div class="cc-spark-bar" style="height:'+h+'%;opacity:'+op.toFixed(2)+'"></div>';
  }).join('');
  var total = (requestsData || []).length;
  var auto = (requestsData || []).filter(function(r){ return r.status === 'auto-approved'; }).length;
  var pct = total ? Math.round(auto/total*100) : 0;
  return '<div class="cc-spark-row">'+
    '<div class="cc-spark-card"><div class="h">Command rhythm \u00b7 24h</div><div class="cc-spark-wrap">'+barsHtml+'</div></div>'+
    '<div class="cc-spark-card"><div class="h">Today</div><div class="cc-spark-stats">'+total+' <span>commands</span></div><div class="cc-spark-sub">'+pct+'% automated \u00b7 '+buckets.reduce(function(a,b){return a+b;},0)+' in 24h</div></div>'+
  '</div>';
}

// ── Recent Sessions panel (below radar in empty state) ─────────────────
function renderRecentSessions() {
  var sessionMap = {};
  (requestsData || []).forEach(function(r) {
    var sid = r.session_id;
    if (!sid || sid === 'unknown') return;
    if (!sessionMap[sid]) sessionMap[sid] = { id: sid, requests: [], latest: 0 };
    sessionMap[sid].requests.push(r);
    if (r.created_at > sessionMap[sid].latest) sessionMap[sid].latest = r.created_at;
  });
  var sessions = Object.values(sessionMap);
  if (sessions.length === 0) return '';
  sessions.sort(function(a, b) { return b.latest - a.latest; });
  sessions = sessions.slice(0, 6);

  var names = _sessionNames || {};
  var now = Math.floor(Date.now() / 1000);

  var cards = sessions.map(function(s) {
    var meta = names[s.id] || {};
    var displayName = meta.name || s.id.substring(0, 8);
    var desc = meta.description || '';
    var host = s.requests[0] ? (s.requests[0].hostname || s.requests[0].target_ip || '') : '';
    var lastCmd = s.requests[s.requests.length - 1];
    var cmdPreview = lastCmd ? escapeHtml(String(lastCmd.command || '').substring(0, 64)) : '';
    var ago = formatAgo(now - s.latest);
    var count = s.requests.length;
    var pending = s.requests.filter(function(r){ return r.status === 'pending'; }).length;
    var badge = pending ? '<span style="color:var(--status-warning);font-weight:700;">' + pending + ' pending</span> \u00b7 ' : '';

    return '<div class="recent-session-card" onclick="openSessionModal(\'' + escapeHtml(s.id) + '\')">' +
      '<div class="rs-title"><span class="host">' + escapeHtml(host) + '</span> \u00b7 ' + escapeHtml(displayName) + ' <span style="color:var(--text-muted);font-weight:400;font-size:10px;">\u00b7 ' + escapeHtml(s.id.substring(0,6)) + '</span></div>' +
      '<div class="rs-cmd-block">' + cmdPreview + '</div>' +
      (desc ? '<div class="rs-desc">' + escapeHtml(desc) + '</div>' : '') +
      '<div class="rs-meta"><span>' + badge + count + ' command' + (count > 1 ? 's' : '') + '</span><span>\u00b7</span><span>' + ago + '</span><span style="margin-left:auto;color:var(--accent);">View \u2192</span></div>' +
    '</div>';
  }).join('');

  return '<div class="cc-recent-sessions">' +
    '<div class="cc-recent-sessions-header">Recent Sessions</div>' +
    '<div class="cc-recent-sessions-grid">' + cards + '</div>' +
  '</div>';
}

function jumpToSession(sid) {
  openSessionModal(sid);
}

async function loadSessionNames() {
  try {
    var res = await authFetch('/api/session-names');
    if (!res.ok) return;
    var serverNames = await res.json();
    var migrated = false;
    try {
      var legacy = JSON.parse(localStorage.getItem('eshu_session_names') || '{}');
      if (legacy && Object.keys(legacy).length) {
        for (var k in legacy) { if (!serverNames[k]) serverNames[k] = legacy[k]; }
        localStorage.removeItem('eshu_session_names');
        migrated = true;
      }
    } catch(e) {}
    _sessionNames = serverNames || {};
    if (migrated) saveSessionNames();
    if (typeof renderJitTickets === 'function') renderJitTickets();
  } catch(e) {}
}
async function saveSessionNames() {
  try {
    await authFetch('/api/session-names', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ names: _sessionNames || {} }) });
  } catch(e) {}
}

function openSessionModal(sid) {
  var modal = document.getElementById('session-modal');
  var nameEl = document.getElementById('sm-name');
  var descEl = document.getElementById('sm-desc');
  var metaEl = document.getElementById('sm-meta');
  var cmdsEl = document.getElementById('sm-commands');
  if (!modal || !nameEl || !descEl || !metaEl || !cmdsEl) return;

  // Gather all requests for this session
  var allReqs = (requestsData || []).filter(function(r) {
    return r.session_id === sid;
  });
  if (allReqs.length === 0) return;

  // Load saved name/desc
  var names = _sessionNames || {};
  var meta = names[sid] || {};

  nameEl.textContent = meta.name || '';
  descEl.textContent = meta.description || '';

  var host = allReqs[0] ? (allReqs[0].hostname || allReqs[0].target_ip || '') : '';
  var pending = allReqs.filter(function(r) { return r.status === 'pending'; }).length;
  var approved = allReqs.filter(function(r) { return r.status === 'approved' || r.status === 'consumed'; }).length;
  var auto = allReqs.filter(function(r) { return r.status === 'auto-approved'; }).length;
  var blocked = allReqs.filter(function(r) { return r.status === 'blocked' || r.status === 'denied'; }).length;
  metaEl.innerHTML = escapeHtml(host) + ' \u00b7 ' + allReqs.length + ' commands' +
    (pending ? ' \u00b7 <span class="sm-pending">' + pending + ' pending</span>' : '') +
    (approved ? ' \u00b7 <span class="sm-approved">' + approved + ' approved</span>' : '') +
    (auto ? ' \u00b7 <span class="sm-auto">' + auto + ' auto</span>' : '') +
    (blocked ? ' \u00b7 <span class="sm-blocked">' + blocked + ' blocked</span>' : '');

  // Render all requests as cards — pending first, then by time
  var sorted = allReqs.slice().sort(function(a, b) {
    if (a.status === 'pending' && b.status !== 'pending') return -1;
    if (b.status === 'pending' && a.status !== 'pending') return 1;
    return (b.created_at || 0) - (a.created_at || 0);
  });

  cmdsEl.innerHTML = sorted.map(function(r) {
    var isPending = r.status === 'pending' && r.ttl > 0;
    var statusClass = '', statusLabel = r.status;
    if (r.status === 'pending') { statusClass = 'sm-st-pending'; statusLabel = 'pending'; }
    else if (r.status === 'approved') { statusClass = 'sm-st-approved'; statusLabel = 'approved'; }
    else if (r.status === 'consumed') { statusClass = 'sm-st-approved'; statusLabel = 'ran'; }
    else if (r.status === 'auto-approved') { statusClass = 'sm-st-auto'; statusLabel = 'auto'; }
    else if (r.status === 'blocked') { statusClass = 'sm-st-blocked'; statusLabel = 'blocked'; }
    else if (r.status === 'denied') { statusClass = 'sm-st-blocked'; statusLabel = 'denied'; }
    else if (r.status === 'frozen') { statusClass = 'sm-st-blocked'; statusLabel = 'frozen'; }
    else if (r.status === 'override') { statusClass = 'sm-st-auto'; statusLabel = 'override'; }

    var human = describeCmd(r.command);
    var host = r.hostname || r.target_ip || '';

    var riskHtml = '';
    if (r.risk) riskHtml = '<div class="sm-risk">' + escapeHtml(r.risk) + '</div>';
    if (r.anomaly) riskHtml += '<div class="sm-risk" style="color:var(--danger)">' + escapeHtml(r.anomaly) + '</div>';

    var actionsHtml = '';
    if (isPending) {
      actionsHtml = '<div class="jit-actions">' +
        '<button onclick="event.stopPropagation();handleAction(' + r.id + ',\'deny\')" class="btn btn-deny btn-xs">Deny</button>' +
        '<button onclick="event.stopPropagation();handleAction(' + r.id + ',\'approve\')" class="btn btn-approve btn-xs">Approve</button>' +
      '</div>';
    }

    return '<div class="jit-ticket sm-ticket' + (isPending ? ' sm-pending' : ' sm-resolved') + '">' +
      '<div class="jit-check"' + (isPending ? '' : ' style="opacity:0"') + '></div>' +
      '<div style="flex:1;min-width:0">' +
        (human ? '<div class="jit-human">' + escapeHtml(human) + '</div>' : '') +
        '<div class="jit-cmd-text">' + escapeHtml(r.command) + '</div>' +
        '<div class="jit-meta">' +
          (host ? '<span class="jit-meta-item">' + escapeHtml(host) + '</span>' : '') +
          '<span class="jit-meta-item sm-status-badge ' + statusClass + '">' + statusLabel + '</span>' +
          '<span class="jit-meta-item" style="margin-left:auto">' + formatTime(r.created_at) + '</span>' +
          (isPending ? '<span class="jit-ttl"><span class="ttl-countdown" data-ttl="' + r.ttl + '">' + r.ttl + 's</span></span>' : '') +
        '</div>' +
        riskHtml +
      '</div>' +
      actionsHtml +
    '</div>';
  }).join('');

  modal.classList.remove('hidden');
  _activeSessionSid = sid;

  // Save name/desc on blur (persisted server-side so it survives across browsers)
  nameEl.onblur = function() {
    if (!_sessionNames[sid]) _sessionNames[sid] = {};
    _sessionNames[sid].name = nameEl.textContent.trim().substring(0, 40);
    saveSessionNames();
    renderJitTickets();
  };
  descEl.onblur = function() {
    if (!_sessionNames[sid]) _sessionNames[sid] = {};
    _sessionNames[sid].description = descEl.textContent.trim().substring(0, 100);
    saveSessionNames();
    renderJitTickets();
  };
  // Save on Enter (prevent newline)
  nameEl.onkeydown = function(e) { if (e.key === 'Enter') { e.preventDefault(); nameEl.blur(); } };
  descEl.onkeydown = function(e) { if (e.key === 'Enter') { e.preventDefault(); descEl.blur(); } };
}

function flashGlitch(text, danger) {
  var glitch = document.getElementById('glitch');
  if (!glitch) return;
  glitch.textContent = text;
  glitch.className = 'glitch show' + (danger ? ' danger' : '');
  clearTimeout(flashGlitch._t);
  flashGlitch._t = setTimeout(function(){ glitch.className = 'glitch'; }, 950);
}

// ── Node flash (auto-approved / blocked) ───────────────────────────────
function flashRadarNode(host, type) {
  document.querySelectorAll('.gw-node').forEach(function(node) {
    if (node.getAttribute('data-name') === host || node.getAttribute('title') === host) {
      node.classList.add('flash-' + type);
      setTimeout(function() { node.classList.remove('flash-' + type); }, 1200);
    }
  });
}

// ── Sound effects (Web Audio API) ──────────────────────────────────────
function _tone(ctx, freq, start, dur, type, peak) {
  var osc = ctx.createOscillator(), gain = ctx.createGain();
  osc.type = type || 'sine';
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(peak, start + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  osc.connect(gain); gain.connect(ctx.destination);
  osc.start(start); osc.stop(start + dur + 0.05);
}

function playAutoChime() {
  if (!notifySound || soundMuted) return;
  var nowTs = Date.now();
  if (nowTs - _lastAutoChimeAt < 1400) return;
  _lastAutoChimeAt = nowTs;
  try {
    var ctx = getAudioCtx(), now = ctx.currentTime;
    // Rising pentatonic arpeggio + soft pad — a calm "fleet at work" motif (~2s).
    var notes = [440.00, 523.25, 659.25, 783.99, 880.00, 1046.50];
    var times = [0, 0.16, 0.32, 0.48, 0.66, 0.86];
    notes.forEach(function(f, i) {
      _tone(ctx, f, now + times[i], 0.5, 'triangle', 0.15);
      _tone(ctx, f * 2, now + times[i], 0.32, 'sine', 0.05);
    });
    _tone(ctx, 110.00, now, 2.0, 'sine', 0.05);
  } catch(e) {}
}

function playMcpChime() {
  if (!notifySound || soundMuted) return;
  var nowTs = Date.now();
  if (nowTs - _lastMcpChimeAt < 1500) return;
  _lastMcpChimeAt = nowTs;
  try {
    var ctx = getAudioCtx(), now = ctx.currentTime;
    // Higher, airier twinkle — a distinct "sky" voice for MCP/API activity (~2s).
    var notes = [1174.66, 1567.98, 1318.51, 1975.53, 1760.00];
    var times = [0, 0.18, 0.36, 0.56, 0.78];
    notes.forEach(function(f, i) {
      _tone(ctx, f, now + times[i], 0.45, 'sine', 0.09);
      _tone(ctx, f * 1.5, now + times[i], 0.28, 'sine', 0.035);
    });
  } catch(e) {}
}

function playBlockedChime() {
  if (!notifySound || soundMuted) return;
  try {
    var ctx = getAudioCtx(), now = ctx.currentTime;
    // low descending two-note — harsh, alerting
    [329.63, 220.00].forEach(function(freq, i) {
      var osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.type = 'square'; osc.frequency.value = freq;
      var t = now + i * 0.15;
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(0.12, t + 0.02);
      gain.gain.linearRampToValueAtTime(0.06, t + 0.08);
      gain.gain.linearRampToValueAtTime(0, t + 0.35);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(t); osc.stop(t + 0.35);
    });
  } catch(e) {}
}

function whoosh(up){
  try{
    var ctx=getAudioCtx(), now=ctx.currentTime, dur=up?0.7:1.05;
    var len=Math.floor(ctx.sampleRate*dur);
    var buf=ctx.createBuffer(1,len,ctx.sampleRate), d=buf.getChannelData(0);
    for(var i=0;i<len;i++){ var env=Math.sin(Math.PI*i/len); d[i]=(Math.random()*2-1)*env*0.5; }
    var src=ctx.createBufferSource(); src.buffer=buf;
    var bp=ctx.createBiquadFilter(); bp.type='bandpass'; bp.Q.value=1.1;
    if(up){ bp.frequency.setValueAtTime(300,now); bp.frequency.exponentialRampToValueAtTime(2600,now+dur); }
    else { bp.frequency.setValueAtTime(2600,now); bp.frequency.exponentialRampToValueAtTime(260,now+dur); }
    var g=ctx.createGain(); g.gain.value=0.07;
    src.connect(bp); bp.connect(g); g.connect(ctx.destination);
    src.start(now); src.stop(now+dur+0.05);
    var bass=ctx.createOscillator(); bass.type='sine';
    if(up){ bass.frequency.setValueAtTime(75,now); bass.frequency.exponentialRampToValueAtTime(55,now+dur); }
    else { bass.frequency.setValueAtTime(55,now); bass.frequency.exponentialRampToValueAtTime(80,now+dur); }
    var bg=ctx.createGain(); bg.gain.setValueAtTime(0,now);
    bg.gain.linearRampToValueAtTime(0.07, now+dur*0.4);
    bg.gain.linearRampToValueAtTime(0, now+dur);
    bass.connect(bg); bg.connect(ctx.destination);
    bass.start(now); bass.stop(now+dur+0.05);
  }catch(e){}
}

// ── MCP starfield — faint integration "stars" behind the dashboard ─────
function _starHash(s) {
  var h = 0;
  for (var i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) >>> 0; }
  return h;
}
function ensureMcpStarfield() {
  var layer = document.getElementById('mcp-starfield');
  if (layer) return layer;
  layer = document.createElement('div');
  layer.id = 'mcp-starfield';
  layer.setAttribute('aria-hidden', 'true');
  document.body.appendChild(layer);
  return layer;
}
function syncMcpStars() {
  var layer = ensureMcpStarfield();
  var ints = (_integrationsData || []).filter(function(i) { return i.enabled; });
  var key = ints.map(function(i) { return i.name; }).sort().join('|');
  if (layer.dataset.key === key) return;
  layer.dataset.key = key;
  layer.innerHTML = ints.map(function(i) {
    var h = _starHash(i.name);
    var x = 5 + (h % 90);
    var y = 9 + ((h >>> 8) % 83);
    var size = 2 + (h % 3);
    var delay = (h % 60) / 10;
    return '<span class="mcp-star" data-name="' + esc(i.name) + '" title="' + esc(i.name) + '" style="left:' + x + '%;top:' + y + '%;width:' + size + 'px;height:' + size + 'px;animation-delay:-' + delay + 's"></span>';
  }).join('');
}
function pulseMcpStar(name) {
  document.querySelectorAll('.mcp-star').forEach(function(star) {
    if (star.getAttribute('data-name') !== name) return;
    star.classList.remove('pulse');
    void star.offsetWidth;
    star.classList.add('pulse');
    setTimeout(function() { star.classList.remove('pulse'); }, 2500);
  });
}
async function pollMcpActivity() {
  try {
    var res = await authFetch('/api/integration-calls?limit=25');
    if (!res.ok) return;
    var data = await res.json();
    var rows = (data.rows || []).filter(function(c) { return (c.agent || '') !== 'operator'; });
    if (!_mcpActivitySeeded) {
      _mcpActivitySeeded = true;
      var mx = 0;
      rows.forEach(function(c) { if (c.id > mx) mx = c.id; });
      _maxMcpCallId = mx;
      return;
    }
    var fresh = rows.filter(function(c) { return c.id > _maxMcpCallId; });
    if (fresh.length === 0) return;
    fresh.forEach(function(c) { if (c.id > _maxMcpCallId) _maxMcpCallId = c.id; });
    var seen = {};
    fresh.forEach(function(c) {
      if (!c.integration || seen[c.integration]) return;
      seen[c.integration] = true;
      pulseMcpStar(c.integration);
    });
    playMcpChime();
  } catch(e) {}
}

document.addEventListener('keydown', function(e) {
  var typing = e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT' || e.target.isContentEditable);
  if (e.key === 'f' || e.key === 'F') { if (typing) return; document.body.classList.toggle('focus'); }
  else if (e.key === 'c' || e.key === 'C') { if (typing) return; nextConstellation(); }
  else if (e.key === 'o' || e.key === 'O') { if (typing) return; openOverride(); }
  else if (e.key === 'Escape') {
    var om = document.getElementById('override-modal');
    if (om && !om.classList.contains('hidden')) { om.classList.add('hidden'); }
    else { deselectGateway(); }
    document.body.classList.remove('focus');
  }
});

document.addEventListener('click', function(e) {
  var t = e.target;
  if (!t || !t.closest) return;
  var hit = t.closest('.const-hit');
  if (hit) { var g = hit.closest('.const-group'); if (g) selectConstellation(g.getAttribute('data-const')); return; }
  var nm = t.closest('.cname');
  if (nm) { selectConstellation(nm.getAttribute('data-const')); return; }
  var gn = t.closest('.gw-node');
  if (gn) { selectGwNode(gn); return; }
  if (t.closest('.starfield-wrap')) { deselectGateway(); }
});

function renderJitTickets() {
  var pending = requestsData.filter(function(r) { return r.status === 'pending' && r.ttl > 0; });
  var winReqs = _pendingWinReqs || [];
  var integrationCalls = _pendingIntegrationCalls || [];
  var allItems = [];

  integrationCalls.forEach(function(c) { allItems.push({ type: 'integration', data: c }); });
  winReqs.forEach(function(w) { allItems.push({ type: 'window', data: w }); });
  pending.forEach(function(r) { allItems.push({ type: 'ssh', data: r }); });

  var total = allItems.length;
  document.body.dataset.state = total > 0 ? 'decision' : 'rest';
  var widget = document.getElementById('jit-pending-widget');
  if (widget) { widget.classList.add('flat'); widget.classList.remove('glow'); }

  var container = document.getElementById('jit-tickets');
  var sig = emptyStateSignature() + '|' + total;
  if (container.dataset.sig === sig && container.firstElementChild) return;
  container.dataset.sig = sig;
  container.innerHTML = renderEmptyState(total);
  buildStaticSky();
  buildStarfield(_layout);
  if (total > 0) renderDeck(allItems);
}

function renderJitItem(item) {
  if (item.type === 'integration') {
      var c = item.data;
      var args = Object.keys(c.payload || {}).map(function(k) { return k + '=' + c.payload[k]; }).join(', ');
      return '<div class="jit-ticket" data-id="int-' + c.id + '" onclick="openContextPanel(\'int-' + c.id + '\')">' +
        '<div class="jit-check" onclick="event.stopPropagation();toggleBulkSelect(\'int-' + c.id + '\',event)"></div>' +
        '<div style="flex:1;min-width:0">' +
          '<div class="jit-human">' + escapeHtml(c.integration) + ' / ' + escapeHtml(c.tool) + '</div>' +
          '<div class="jit-cmd-text">' + escapeHtml(c.integration) + ' ' + escapeHtml(c.tool) + (args ? '(' + escapeHtml(args) + ')' : '') + '</div>' +
          '<div class="jit-meta">' +
            '<span class="jit-meta-item">#' + String(c.id).padStart(6,'0') + '</span>' +
            '<span class="jit-meta-item">API call</span>' +
            (c.reason ? '<span class="jit-meta-item">\u201c' + escapeHtml(c.reason) + '\u201d</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="jit-actions">' +
          '<button onclick="event.stopPropagation();denyIntegrationCall(' + c.id + ')" class="btn btn-deny btn-xs">Deny</button>' +
          '<button onclick="event.stopPropagation();approveIntegrationCall(' + c.id + ')" class="btn btn-approve btn-xs">Approve</button>' +
        '</div>' +
      '</div>';
    } else if (item.type === 'window') {
      var w = item.data;
      return '<div class="jit-ticket" data-id="win-' + w.id + '" onclick="openContextPanel(\'win-' + w.id + '\')">' +
        '<div class="jit-check" onclick="event.stopPropagation();toggleBulkSelect(\'win-' + w.id + '\',event)"></div>' +
        '<div style="flex:1;min-width:0">' +
          '<div class="jit-human">Window request</div>' +
          '<div class="jit-cmd-text">' + escapeHtml(w.command) + '</div>' +
          '<div class="jit-meta">' +
            '<span class="jit-meta-item">#' + String(w.id).padStart(6,'0') + '</span>' +
            '<span class="jit-meta-item">' + escapeHtml(w.target_ip) + '</span>' +
            (w.label ? '<span class="jit-meta-item">' + escapeHtml(w.label) + '</span>' : '') +
            '<span class="jit-meta-item">' + formatWinSchedule(w) + '</span>' +
          '</div>' +
        '</div>' +
        '<div class="jit-actions">' +
          '<button onclick="event.stopPropagation();denyWindowReq(' + w.id + ')" class="btn btn-deny btn-xs">Deny</button>' +
          '<button onclick="event.stopPropagation();approveWindowReq(' + w.id + ')" class="btn btn-approve btn-xs">Approve</button>' +
        '</div>' +
      '</div>';
    } else {
      var r = item.data;
      var human = describeCmd(r.command);
      var hostLabel = r.hostname || r.target_ip || 'Unknown host';
      var hostCell = r.hostname
        ? gwPill(r.hostname) + ' ' + escapeHtml(r.hostname)
        : escapeHtml(hostLabel);
      var riskHtml = '';
      if (r.risk) riskHtml += '<div style="color:var(--status-warning);font-size:10px;margin-top:4px"> ' + escapeHtml(r.risk) + '</div>';
      if (r.anomaly) riskHtml += '<div style="color:var(--danger);font-size:10px;margin-top:4px"> ' + escapeHtml(r.anomaly) + '</div>';
      return '<div class="jit-ticket' + (r.anomaly ? ' urgent' : '') + '" data-id="' + r.id + '" onclick="openContextPanel(\'' + r.id + '\')">' +
        '<div class="jit-check" onclick="event.stopPropagation();toggleBulkSelect(\'' + r.id + '\',event)"></div>' +
        '<div style="flex:1;min-width:0">' +
          '<div class="jit-human">' + escapeHtml(human || 'Command request') + '</div>' +
          '<div class="jit-cmd-text">' + escapeHtml(r.command) + '</div>' +
          '<div class="jit-meta">' +
            '<span class="jit-meta-item">#' + String(r.id).padStart(6,'0') + '</span>' +
            '<span class="jit-meta-item">' + hostCell + '</span>' +
            (r.hostname && r.target_ip && r.target_ip !== r.hostname ? '<span class="jit-meta-item">' + escapeHtml(r.target_ip) + '</span>' : '') +
            '<span class="jit-ttl"><span class="ttl-countdown" data-ttl="' + r.ttl + '">' + r.ttl + 's</span></span>' +
          '</div>' +
          riskHtml +
        '</div>' +
        '<div class="jit-actions">' +
          '<button onclick="event.stopPropagation();handleAction(' + r.id + ',\'deny\')" class="btn btn-deny btn-xs">Deny</button>' +
          '<button onclick="event.stopPropagation();handleAction(' + r.id + ',\'approve\')" class="btn btn-approve btn-xs">Approve</button>' +
        '</div>' +
      '</div>';
    }
}

function renderSessionCard(s) {
  var items = s.items;
  var first = items[0].data;
  var host = first.hostname || first.target_ip || 'host';
  var age = formatAgo(Math.floor(Date.now() / 1000) - (first.created_at || 0));
  var short = escapeHtml(s.session_id.substring(0, 8));
  var names = _sessionNames || {};
  var meta = names[s.session_id] || {};
  var displayName = meta.name || short;
  var desc = meta.description || '';
  var historyHtml = '';
  if (s.history && s.history.length) {
    var lines = s.history.map(function(r) {
      var dot = 'muted', label = String(r.status || '');
      if (r.status === 'blocked' || r.status === 'denied') { dot = 'denied'; }
      else if (r.status === 'auto-approved' || r.status === 'approved' || r.status === 'consumed' || r.status === 'override') { dot = 'approved'; if (r.status === 'auto-approved') label = 'auto'; else if (r.status === 'consumed') label = 'ran'; }
      return '<div class="jit-history-item"><span class="jit-history-dot ' + dot + '"></span>' +
        '<span class="jit-history-status">' + escapeHtml(label) + '</span>' +
        '<span class="jit-history-cmd">' + escapeHtml(String(r.command || '')) + '</span>' +
        '<span class="jit-history-time">' + formatTime(r.created_at) + '</span></div>';
    }).join('');
    historyHtml = '<div class="jit-history">' + lines + '</div>';
  }
  return '<div class="jit-session open" data-sid="' + escapeHtml(s.session_id) + '">' +
    '<div class="jit-session-header" onclick="toggleSession(this)">' +
      '<span class="jit-session-caret">&#9656;</span>' +
      '<span class="jit-session-label">' + escapeHtml(displayName) + (meta.name ? ' <span class="jit-session-id">' + short + '</span>' : '') + '</span>' +
      (desc ? '<span class="jit-session-desc">' + escapeHtml(desc) + '</span>' : '') +
      '<span class="jit-session-meta">' + escapeHtml(host) + ' \u00b7 ' + items.length + ' command' + (items.length > 1 ? 's' : '') + ' \u00b7 ' + age + '</span>' +
      '<button class="jit-session-name-btn" onclick="event.stopPropagation();openSessionModal(\'' + escapeHtml(s.session_id) + '\')">View</button>' +
    '</div>' +
    '<div class="jit-session-body">' + items.map(function(it){ return renderJitItem(it); }).join('') + historyHtml + '</div>' +
  '</div>';
}

function toggleSession(el) {
  var sess = el.closest('.jit-session');
  if (sess) sess.classList.toggle('open');
}
async function approveWindowReq(id) {
  flashGlitch('APPROVED \u25B8 WINDOW', false);
  try {
    const r = await authFetch('/api/window-requests/' + id + '/approve', { method: 'POST' });
    if (r.status === 401) { await checkAuth(); return; }
    if (!r.ok) throw new Error('Approve failed');
    const data = await r.json();
    if (data.token) copyToClipboard(data.token, true);
    showToast('Window approved — token copied', 'success');
    fetchRequests(); fetchWindowsTable();
  } catch(e) { showToast('' + (e.message || 'Failed to approve window'), 'error'); }
}
async function denyWindowReq(id) {
  flashGlitch('DENIED \u25B8 WINDOW', true);
  try {
    const r = await authFetch('/api/window-requests/' + id + '/deny', { method: 'POST' });
    if (r.status === 401) { await checkAuth(); return; }
    if (!r.ok) throw new Error('Deny failed');
    showToast('Window request denied', 'success');
    fetchRequests();
  } catch(e) { showToast('Failed to deny window request', 'error'); }
}

// ── Deny with Blocklist ─────────────────────────────────────────────────
async function handleAction(id, action) {
  var ghReq = requestsData.find(function(r){ return String(r.id) === String(id); });
  var ghHost = ghReq ? (ghReq.hostname || ghReq.target_ip || '') : '';
  flashGlitch((action === 'deny' ? 'DENIED' : 'APPROVED') + (ghHost ? ' \u25B8 ' + ghHost.toUpperCase() : ''), action === 'deny');
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
    fetchRequests().then(function() { if (_activeSessionSid) openSessionModal(_activeSessionSid); });
  } else {
    const res = await authFetch('/api/' + action + '/' + id, { method: 'POST' });
    if (res.status === 401) { await checkAuth(); return; }
    fetchRequests().then(function() { if (_activeSessionSid) openSessionModal(_activeSessionSid); });
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
  showToast('Blocklisted & pushed to ' + gws.length + ' gateway(s)', 'success');
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
  if (!silent) showToast('Copied', 'success');
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
    else if (req.status === 'integration-approved') badge = '<span class="badge badge-approved" title="Executed via the API gateway">API Executed</span>';
    else if (req.status === 'integration-denied') badge = '<span class="badge badge-denied" title="Denied via the API gateway">API Denied</span>';
    let actions = '<span class="text-muted">—</span>';
    if (req.status === 'pending' && !isExpired) {
      actions = '<button onclick="handleAction(' + req.id + ', \'approve\')" class="btn btn-approve btn-xs mr-1">Approve</button>' +
        '<button onclick="handleAction(' + req.id + ', \'deny\')" class="btn btn-deny btn-xs">Deny</button>';
    } else if (req.status === 'frozen') {
      actions = '<span class="chip chip-actions chip-frozen" title="Blocked by Emergency Freeze — the fleet is rejecting all commands until unfrozen.">' +
        'Frozen</span>';
    } else if (req.status === 'fleet-run') {
      actions = '<span class="chip chip-actions chip-fleet-run" title="Executed via Fleet Run — see the Fleet Run tab for per-gateway output.">' +
        'Fleet Run</span>';
    } else if (req.status === 'integration-approved' || req.status === 'integration-denied') {
      actions = '<span class="chip chip-actions chip-integration" title="API-gateway call — see Integrations for the full audit.">API</span>';
    } else if (req.reason === 'override') {
      actions = '<span class="chip chip-actions chip-override" title="Auto-approved via Override Mode — every JIT is auto-approved while active">' +
        'Override</span>';
    } else if (req.status === 'blocked' && isHardcoreBlocked(req.command)) {
      actions = '<span class="chip chip-actions chip-block-core" title="Blocked by a shipped Eshu safety pattern — manage in Controls → Blocklist">' +
        'Block by Core</span>';
    } else {
      const mem = fetchPolicyMembership(req.command);
      const inAnyAllowlist = mem.inExact || mem.inRegexWhite;
      const disabledStyle = 'opacity:0.5;pointer-events:none;';

      actions = '<select onchange="handlePolicyAction(this,\'' + encodeURIComponent(req.command) + '\')" class="btn-muted select-actions">' +
        '<option value="" disabled selected>Actions</option>' +
        '<option value="exact_whitelist"' + (mem.inExact ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inExact ? '✓ ' : '+ ') + 'AL Exact</option>' +
        '<option value="regex_whitelist"' + (mem.inRegexWhite ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inRegexWhite ? '✓ ' : '+ ') + 'AL Regex</option>' +
        '<option value="regex_blacklist"' + (mem.inBlacklist ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inBlacklist ? '✓ ' : '+ ') + 'Add to Blocklist</option>' +
        (inAnyAllowlist ? '<option value="regex_whitelist_remove">Remove from Allowlist</option>' : '') +
        (mem.inBlacklist ? '<option value="regex_blacklist_remove">Remove from Blocklist</option>' : '') +
      '</select>';
    }
    const gap = gapMap.get(req.id);
    const rowClass = gap ? 'gap-row' : '';
    const idClass = gap ? 'cell-id-warn' : 'cell-id';
    const gapTitle = gap ? ' title="' + escapeHtml(formatGapTooltip(gap)) + '"' : '';
    const idDisplay = gap ? ' #' + String(req.id).padStart(6, '0') : '#' + String(req.id).padStart(6, '0');
    const escapedCmd = escapeHtml(req.command);
    const gwPillHtml = gwPill(req.hostname || 'N/A');
    const isIntegration = req.status === 'integration-approved' || req.status === 'integration-denied';
    const gatewayCell = isIntegration
      ? escapeHtml(req.target_ip)
      : gwPillHtml + ' ' + escapeHtml(req.hostname || 'N/A') + ' (' + escapeHtml(req.target_ip) + ')';
    const riskHtml = (req.status === 'pending' && req.risk) ?
      '<span class="flex-shrink-0 risk-flag" title="Risk: ' + escapeHtml(req.risk) + '">!</span>' : '';
    const anomalyHtml = (req.status === 'pending' && req.anomaly) ?
      '<span class="flex-shrink-0 anomaly-flag" title="New: ' + escapeHtml(req.anomaly) + '">!</span>' : '';
    html += '<tr class="' + rowClass + '">' +
      '<td class="' + idClass + '"' + gapTitle + '>' + idDisplay + '</td>' +
      '<td class="text-muted text-xs">' + formatTime(req.created_at) + '</td>' +
      '<td>' + gatewayCell + '</td>' +
      '<td class="cell-cmd"><div class="flex items-center gap-1">' + riskHtml + anomalyHtml + '<code class="cmd-code" title="' + escapedCmd + '">' + escapedCmd + '</code><button class="js-copy-cmd flex-shrink-0 text-xs opacity-30 hover:opacity-80 px-1 py-0.5 rounded transition-opacity text-muted" data-cmd="' + encodeURIComponent(req.command) + '" title="Copy"></button></div>' +
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
        (isIntegration && req.reason
          ? '<div class="cmd-desc cmd-reason">' + escapeHtml(req.reason) + '</div>'
          : '') +
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
    showToast('Removed from Allowlist & pushed to ' + gws.length + ' gateway(s)', 'success');
    fetchPolicies(); fetchRequests(); return;
  }
  if (policyType === 'regex_blacklist_remove') {
    if (!(await customConfirm('Remove "' + cmd + '" from blocklist?'))) return;
    const rbTextarea = document.getElementById('policy-regex-black');
    rbTextarea.value = rbTextarea.value.split('\n').filter(l => l.trim() !== cmd).join('\n');
    await savePoliciesSilent(); await refreshPolicyCache();
    const gwRes = await fetch('/api/gateways'); const gws = await gwRes.json();
    showToast('Removed from Blocklist', 'success');
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
  showToast('Added to ' + labels[policyType], 'success');
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
      ? '<span class="zt-badge" title="Zero-Trust — every command requires JIT approval"> ZT</span>'
      : '';
    var ztBtn = g.zero_trust
      ? '<button onclick="toggleZeroTrust(\'' + g.ip + '\', false)" class="chip-btn zt-on" title="Zero-Trust ON — allowlisted commands go through JIT. Click to disable."> ZT</button>'
      : '<button onclick="toggleZeroTrust(\'' + g.ip + '\', true)" class="chip-btn" title="Enable Zero-Trust — allowlisted commands must go through JIT">ZT</button>';
    var overrideCell;
    if (_uninstallingIps[g.ip]) {
      overrideCell = '<span class="text-warning text-xs"> Uninstalling…</span>';
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
      '<td class="px-4 py-2 text-right whitespace-nowrap"><button onclick="openRemoveGatewayModal(\'' + g.ip + '\', \'' + g.hostname + '\', ' + isOnline + ')" class="btn btn-deny btn-xs"> Remove</button></td>' +
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
      showToast((enabled ? 'Zero-Trust enabled on ' : 'Zero-Trust disabled on ') + ip + ' — allowlisted commands will ' + (enabled ? 'require JIT approval' : 'auto-run again') + '.', 'success');
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
  if (btn) { btn.disabled = false; btn.textContent = 'Freeze Fleet'; }
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
    var riskLine = d.risk ? '<div class="text-xs mt-0.5 text-warning"> ' + escapeHtml(d.risk) + '</div>' : '';
    var dryLine = d.dry_run ? '<div class="text-xs mt-0.5 text-info"> Dry-run available: <code class="text-main">' + escapeHtml(d.dry_run) + '</code> <button onclick="useFleetDryRun(' + i + ')" class="chip-btn info">Use</button></div>' : '';
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
    msg += '\n\n ' + dryCount + ' command(s) have a dry-run version available for safe testing — press Cancel to go back and upgrade them, or Continue to dispatch as-is.';
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
    var ok = await customConfirm(' ' + Object.keys(blacklistedIdx).length + ' queued command(s) match the central blocklist. Dispatch them anyway with override?');
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

function fleetDot(status) {
  var cls = 'fleet-dot';
  if (status === 'running' || status === 'queued') cls += ' running';
  else if (status === 'success') cls += ' success';
  else if (status === 'failed' || status === 'timeout') cls += ' fail';
  return '<span class="' + cls + '"></span>';
}

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
                   r.status === 'running' ? 'badge-pending' :
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
          '<button onclick="copyFleetOutput(' + c.id + ',\'' + r.gateway_ip + '\')" class="chip-btn">Copy</button>' +
          '<button onclick="openFleetOutputModal(' + c.id + ',\'' + r.gateway_ip + '\')" class="chip-btn info">View full</button>' +
          '</div>' +
          '<pre data-out="' + outId + '" class="fleet-out fleet-pre overflow-auto text-xs">' + escapeHtml(r.output) + '</pre>' +
          '</div></details>';
      }
      var gName = r.hostname ? escapeHtml(r.hostname) : escapeHtml(r.gateway_ip);
      var gIp = (r.hostname && r.hostname !== r.gateway_ip) ? ' <span class="text-muted">(' + escapeHtml(r.gateway_ip) + ')</span>' : '';
      var clearBtn = (r.status === 'queued') ?
        '<button onclick="clearFleetResult(' + c.id + ',\'' + r.gateway_ip + '\')" class="chip-btn" title="Clear this stuck gateway so its queue can move on (other results are kept)">✕</button>' : '';
      return '<div class="mt-1 text-xs text-muted">' + fleetDot(r.status) + ' <strong>' + gName + '</strong>' + gIp + ' ' +
        '<span class="badge ' + rBadge + '">' + r.status + '</span>' +
        (r.exit_code != null && r.exit_code !== '' ? ' <span class="text-muted">exit ' + r.exit_code + '</span>' : '') +
        times + clearBtn + outHtml + '</div>';
    }).join('');
    var runningRes = (c.results || []).filter(function(r) { return r.status === 'running' && r.started_at; });
    var cdHtml;
    if (c.status === 'approved' && runningRes.length > 0) {
      var start = Math.min.apply(null, runningRes.map(function(r) { return r.started_at; }));
      var deadline = start + c.timeout;
      cdHtml = '<span class="text-xs fleet-countdown text-warning whitespace-nowrap" data-deadline="' + deadline + '"> counting…</span>';
    } else {
      cdHtml = '<span class="text-xs text-muted whitespace-nowrap">' + c.timeout + 's timeout</span>';
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
    showToast('Output copied', 'success');
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
    showToast('Output copied', 'success');
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
      el.textContent = 'timed out';
      el.style.color = 'var(--brand-red)';
      return;
    }
    if (rem >= 3600) {
      var h = Math.floor(rem / 3600), m = Math.floor((rem % 3600) / 60);
      el.textContent = ' ' + h + 'h ' + m + 'm left';
    } else {
      var m2 = Math.floor(rem / 60), s = rem % 60;
      el.textContent = ' ' + m2 + ':' + String(s).padStart(2, '0') + ' left';
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
      const shield = '';
      return '<span class="policy-chip' + (isCore ? ' core' : '') + '" title="' + line.replace(/"/g, '&quot;') + '">' + shield + '<code>' + escapeHtml(line) + '</code><button class="remove" data-line="' + encodeURIComponent(line) + '" data-core="' + (isCore ? '1' : '0') + '" title="Remove">&times;</button></span>';
    }).join('');
    // Removed core patterns — shipped defaults no longer in the list: struck + Restore
    if (type === 'regex_blacklist') {
      const removed = coreBlockPatterns().filter(function(p){ return lines.indexOf(p) === -1; });
      html += removed.map(function(p) {
        return '<span class="policy-chip removed" title="' + p.replace(/"/g, '&quot;') + '"><code>' + escapeHtml(p) + '</code><button class="restore" data-line="' + encodeURIComponent(p) + '" title="Restore this core default">Restore</button></span>';
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
    customConfirm('Remove a shipped core block?\n\n"' + line + '" is a safety-net pattern that ships with Eshu by default. Removing it lets this kind of command reach JIT/approval.\n\nOnly continue if you are sure. Re-add it any time via "Core defaults".').then(function(ok){ if (ok) doRemove(); });
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
  showToast('Policies saved & pushed', 'success');
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
    let badge, verdict, desc;
    if (data.action === 'blocked') { badge='badge-blocked'; verdict='Blocked'; desc='Matched: <code class="text-main">' + (data.details[0] ? escapeHtml(data.details[0].pattern) : 'unknown') + '</code>'; }
    else if (data.action === 'auto_approved') { badge='badge-approved'; verdict='Auto-Approved'; desc=(data.details[0] && data.details[0].type === 'exact_whitelist') ? 'Exact allowlist match' : 'Matched: <code class="text-main">' + (data.details[0] ? escapeHtml(data.details[0].pattern) : '') + '</code>'; }
    else { badge='badge-pending'; verdict='JIT Required'; desc='No policy match — waits for operator approval'; }
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
    rd.innerHTML = '<div class="result-box"><span class="badge ' + badge + '">' + verdict + '</span> <span class="text-main">' + desc + '</span></div>' + memLine + testerAddButtons(data.action, cmd);
  } catch(err) { rd.innerHTML = '<div class="result-box" style="background:var(--bg-base);color:var(--text-muted);"> ' + err.message + '</div>'; }
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
    if (remaining <= 0) {       el.textContent = 'Token expired'; el.style.color = 'var(--brand-red)'; clearInterval(tokenTimer); tokenTimer = null; if (tokenStatusPoller) { clearInterval(tokenStatusPoller); tokenStatusPoller = null; } document.getElementById('enroll-command').value = ''; document.getElementById('copy-enroll-btn').disabled = true; currentToken = ''; }
    else if (remaining <= 30) { el.textContent = ' ' + remaining + 's remaining'; el.style.color = 'var(--status-warning)'; }
    else { el.textContent = ' ' + remaining + 's remaining'; el.style.color = 'var(--text-muted)'; }
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
  toggleKeyEdit(); fetchEnrollData(); showToast('Keys saved', 'success');
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
        document.getElementById('token-countdown').textContent = 'Token consumed by gateway';
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
    : 'This gateway is currently offline — if it will not come back, use "Force remove from dashboard".';
  statusEl.className = 'text-xs mb-3 ' + (isOnline ? 'text-success' : 'text-warning');
  var confirmSection = document.getElementById('uninstall-confirm-section');
  if (confirmSection) confirmSection.classList.remove('hidden');
  var confirmInput = document.getElementById('uninstall-confirm-input');
  if (confirmInput) { confirmInput.value = ''; confirmInput.placeholder = hostname; }
  var confirmCheck = document.getElementById('uninstall-confirm-check');
  if (confirmCheck) confirmCheck.checked = false;
  var confirmBtn = document.getElementById('uninstall-confirm-btn');
  if (confirmBtn) confirmBtn.disabled = true;
  document.getElementById('remove-gateway-modal').classList.remove('hidden');
}

function closeRemoveGatewayModal() {
  document.getElementById('remove-gateway-modal').classList.add('hidden');
  _removeIp = null;
  _removeHostname = null;
  var confirmSection = document.getElementById('uninstall-confirm-section');
  if (confirmSection) confirmSection.classList.add('hidden');
  var confirmBtn = document.getElementById('uninstall-confirm-btn');
  if (confirmBtn) confirmBtn.disabled = true;
}

function validateUninstallConfirm() {
  var check = document.getElementById('uninstall-confirm-check');
  var input = document.getElementById('uninstall-confirm-input');
  var btn = document.getElementById('uninstall-confirm-btn');
  if (!check || !input || !btn) return;
  var checked = check.checked;
  var textMatch = input.value.trim() === (_removeHostname || '');
  btn.disabled = !(checked && textMatch);
  if (input.value && !textMatch) {
    input.style.borderColor = 'var(--danger)';
  } else {
    input.style.borderColor = '';
  }
}

async function confirmRemoteUninstall() {
  const ip = _removeIp, hostname = _removeHostname;
  if (!ip) return;
  closeRemoveGatewayModal();
  try {
    const res = await authFetch('/api/gateways/' + ip + '/uninstall', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) { showToast('' + (data.detail || 'Failed to trigger uninstall'), 'error'); return; }
    showToast('Uninstall triggered for ' + data.hostname, 'success');
    _uninstallingIps[ip] = true;
    fetchGateways();
    openUninstallModal(ip, data.hostname || hostname);
    pollUninstallProgress(ip);
  }
  catch(err) { showToast('Failed', 'error'); }
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
    document.getElementById('uninstall-modal-step').textContent = 'Removed';
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
  if (!(await customConfirm('Force remove ' + hostname + ' (' + ip + ') from the dashboard?\n\n This only deletes the dashboard record — it does NOT uninstall anything on the host.\n\nIf the gateway is still running, it will re-register on its next poll (~30s), so this may not be permanent. Use it only for decommissioned or never-completed hosts.'))) return;
  try {
    const res = await authFetch('/api/gateways/' + ip, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) { showToast('' + (data.detail || 'Failed'), 'error'); return; }
    showToast('' + data.hostname + ' removed from dashboard', 'success');
    if (_removeIp === ip) closeRemoveGatewayModal(); else fetchGateways();
  }
  catch(err) { showToast('Failed', 'error'); }
}
function copyEnrollCommand() { document.getElementById('enroll-command').select(); document.execCommand('copy'); showToast('Copied', 'success'); }

// ── Audit Log ────────────────────────────────────────────────────────────
const AUDIT_ICONS = { enrolled: '✓', version_updated: '↑', disconnected: '!', policy_committed: '↻', update_triggered: '↻', uninstall_triggered: '✕', uninstalled: '✕', password_changed: '', password_cleared: '', window_created: '', window_modified: '', window_deleted: '', window_toggled: '', window_claimed: '', dev_update_pushed: '', gateway_mode_changed: '' };
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
    _auditLogs = logs;
    document.getElementById('audit-log-count').textContent = logs.length + ' events';
    renderAuditFilters();
    renderAuditLog();
  } catch(e) {}
}

var _auditLogs = [];
var _auditFilter = 'all';

function auditCategory(et) {
  if (et.indexOf('jit_') === 0) return 'jit';
  if (et.indexOf('policy_') === 0) return 'policy';
  if (et.indexOf('window_') === 0) return 'windows';
  if (et.indexOf('integration_') === 0 || et.indexOf('agent_token_') === 0) return 'integrations';
  if (et.indexOf('fleet_') === 0 || et.indexOf('freeze_') === 0 || et.indexOf('override_') === 0) return 'fleet';
  if (et.indexOf('zero_trust_') === 0 || ['enrolled','version_updated','disconnected','connected','uninstalled','uninstall_triggered','auto_deregistered','gateway_mode_changed'].indexOf(et) !== -1) return 'gateways';
  return 'config';
}

function auditBadgeClass(et) {
  if (['jit_approved','jit_override_approved','window_request_approved','integration_call_approved','enrolled','connected','integration_created','integration_updated','agent_token_created','fleet_dispatched'].indexOf(et) !== -1) return 'badge-approved';
  if (['jit_denied','disconnected','uninstalled','auto_deregistered','window_request_denied','integration_call_denied','integration_deleted','agent_token_deleted'].indexOf(et) !== -1) return 'badge-denied';
  return 'badge-info';
}

function renderAuditFilters() {
  var bar = document.getElementById('audit-filter-bar');
  if (!bar) return;
  var cats = [['all','All'],['jit','JIT'],['policy','Policy'],['gateways','Gateways'],['fleet','Fleet'],['config','Config'],['windows','Windows'],['integrations','Integrations']];
  var counts = {};
  _auditLogs.forEach(function(l){ var c = auditCategory(l.event_type); counts[c] = (counts[c]||0)+1; });
  bar.innerHTML = cats.map(function(c){
    var id = c[0], label = c[1];
    var count = id === 'all' ? _auditLogs.length : (counts[id]||0);
    return '<button class="audit-filter-tab' + (_auditFilter === id ? ' active' : '') + '" onclick="toggleAuditFilter(\'' + id + '\')">' + label + (count ? ' <span class="audit-filter-count">' + count + '</span>' : '') + '</button>';
  }).join('');
}

function toggleAuditFilter(cat) {
  _auditFilter = cat;
  renderAuditFilters();
  renderAuditLog();
}

function toggleAuditDetail(id) {
  var row = document.querySelector('.log-row[data-aid="' + id + '"]');
  var detail = document.getElementById('audit-detail-' + id);
  if (!row || !detail) return;
  var expanded = row.classList.contains('expanded');
  row.classList.toggle('expanded', !expanded);
  detail.classList.toggle('visible', !expanded);
}

function renderAuditLog() {
  var body = document.getElementById('audit-log-body');
  if (!body) return;
  var logs = _auditLogs.filter(function(l){ return _auditFilter === 'all' || auditCategory(l.event_type) === _auditFilter; });
  if (logs.length === 0) { body.innerHTML = '<tr><td colspan="5" class="px-4 py-3 text-muted">No events recorded yet.</td></tr>'; return; }
  body.innerHTML = logs.map(function(l, i) {
    var id = l.id || i;
    var label = AUDIT_LABELS[l.event_type] || String(l.event_type).replace(/_/g, ' ');
    var badge = auditBadgeClass(l.event_type);
    var d = new Date(l.timestamp * 1000);
    var time = d.toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', second:'2-digit' });
    var date = d.toLocaleDateString([], { month:'short', day:'numeric' });
    var gw = l.hostname || l.gateway_ip || '';
    var details = l.details ? escapeHtml(String(l.details)) : '';
    var row = '<tr class="log-row" data-aid="' + id + '" onclick="toggleAuditDetail(' + id + ')">' +
      '<td><span class="log-expand">&#9656;</span></td>' +
      '<td><div class="log-time">' + time + '</div><div class="log-date">' + date + '</div></td>' +
      '<td><span class="badge ' + badge + '">' + escapeHtml(label) + '</span></td>' +
      '<td class="log-details">' + (details || '<span class="text-muted">—</span>') + '</td>' +
      '<td class="log-gw">' + (gw ? escapeHtml(gw) + (l.gateway_ip && l.hostname ? ' <span class="text-muted">' + escapeHtml(l.gateway_ip) + '</span>' : '') : '<span class="text-muted">—</span>') + '</td>' +
      '</tr>';
    var detailRow = '<tr class="log-detail-row" id="audit-detail-' + id + '"><td class="log-detail-cell" colspan="5">' +
      '<div class="log-detail">' +
        '<div class="detail-row"><span class="detail-label">Timestamp</span><span class="detail-value">' + escapeHtml(d.toLocaleString()) + '</span></div>' +
        '<div class="detail-row"><span class="detail-label">Event</span><span class="detail-value">' + escapeHtml(label) + '</span></div>' +
        (gw ? '<div class="detail-row"><span class="detail-label">Gateway</span><span class="detail-value">' + escapeHtml(gw) + (l.gateway_ip ? ' (' + escapeHtml(l.gateway_ip) + ')' : '') + '</span></div>' : '') +
        (details ? '<div class="detail-cmd">' + details + '</div>' : '') +
      '</div>' +
      '</td></tr>';
    return row + detailRow;
  }).join('');
}

// ── Notes ────────────────────────────────────────────────────────────────
async function fetchNotes() { const res = await fetch('/api/notes'); const data = await res.json(); document.getElementById('notes-content').value = data.content || ''; }
async function saveNotes() { await authFetch('/api/notes', { method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({content: document.getElementById('notes-content').value}) }); showToast('Notes saved', 'success'); }

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
    document.getElementById('notify-save-status').textContent = 'Saved';
    showToast('Notification config saved', 'success');
  } catch(e) { showToast('' + e.message, 'error'); }
}
async function testNotify() {
  try {
    var r = await authFetch('/api/notify-test', { method:'POST' });
    if (!r.ok) throw new Error('Test failed');
    var d = await r.json();
    showToast(d.delivered ? 'Test notification delivered' : 'Test failed — webhook unreachable or no URL set', d.delivered ? 'success' : 'error');
  } catch(e) { showToast('Test failed — ' + e.message, 'error'); }
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
  if (!(await customConfirm('Clear the queue of requests older than ' + label + '?\n\n This hides them from the queue. Stats are preserved.'))) return;
  const now = Math.floor(Date.now() / 1000);
  const offsets = { '30m': 1800, '1h': 3600, '1d': 86400, '2d': 172800, '7d': 604800 };
  if (period === 'all') _queueClearBefore = now;
  else _queueClearBefore = now - (offsets[period] || 3600);
  showToast('Queue cleared — stats unchanged', 'success');
  fetchRequests();
}
let _dbPurgeBefore = 0;
async function purgeDatabase(period) {
  const label = period === 'all' ? 'all time' : period;
      if (!(await customConfirm('PERMANENTLY DELETE all requests older than ' + label + ' from the database?\n\nThis CANNOT be undone. Stats, charts, and top commands will be affected.'))) return;
  await authFetch('/api/requests?older_than=' + period, { method: 'DELETE' });
  _queueClearBefore = 0; // Reset client-side filter after DB purge
  showToast('Database purged (older than ' + label + ')', 'success');
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
function closeModal(modalId) {
  document.getElementById(modalId).classList.add('hidden');
  if (modalId === 'session-modal') _activeSessionSid = null;
}

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
    tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-3 text-muted">No gateways selected.</td></tr>';
  } else {
    tbody.innerHTML = filtered.map(function(g) {
      var id = deriveGatewayIdentity(g.hostname || g.ip);
      var jitApproved = (g.total || 0) - (g.auto_approved || 0) - (g.blocked || 0) - (g.denied || 0);
      var autoPct = g.total > 0 ? Math.round(((g.auto_approved || 0) + jitApproved) / g.total * 100) : 0;
      return '<tr>' +
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
          var n = new Notification('Gateway Offline', { body: ip + ' has been unreachable for over 2 minutes', tag: 'eshu-offline', icon: '/static/eshu_logo.png' });
          n.onclick = function() { window.focus(); n.close(); };
        }
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
setInterval(function() {
  if (!document.getElementById('login-overlay').classList.contains('hidden')) return;
  if (!document.getElementById('setup-overlay').classList.contains('hidden')) return;
  pollMcpActivity();
}, 4000);
setInterval(function() {
  if (!document.getElementById('login-overlay').classList.contains('hidden')) return;
  if (!document.getElementById('setup-overlay').classList.contains('hidden')) return;
  syncMcpStars();
}, 30000);

// ── Integrations & MCP ──────────────────────────────────────────────────
let _selectedIntegration = null;
let _editingIntegration = null;
let _integrationsData = [];

// Supported integration profiles: guidance + auth defaults + seed availability.
// `auth_type` set → the auth-type/header fields are hidden and pre-filled.
const INTEGRATION_PROFILES = {
  proxmox:     { label: 'Proxmox VE',   seedable: true,
                 auth_type: 'header', auth_header: 'Authorization',
                 secret_hint: 'PVEAPIToken=user@realm!tokenid=uuid',
                 guidance: 'PVE API token (custom scheme, not Bearer). Base URL ends in /api2/json.' },
  ha:          { label: 'Home Assistant', seedable: true,
                 auth_type: 'bearer', auth_header: '',
                 secret_hint: 'Long-lived access token',
                 guidance: 'Long-lived access token. Base URL is https://<ha>/api.' },
  pulse:       { label: 'Pulse', seedable: true,
                 auth_type: 'header', auth_header: 'X-API-Token',
                 secret_hint: 'Pulse API token',
                 guidance: 'Pulse (Proxmox monitoring). X-API-Token header; Bearer also accepted. Base URL ends in /api.' },
  omada:       { label: 'Omada', seedable: true,
                 auth_type: 'oauth2', auth_header: '',
                 secret_hint: 'Client Secret', insecure_tls: true,
                 guidance: 'OAuth2 client_credentials → Authorization: AccessToken=<token>. Base URL ends in /openapi/v1/<omadacId>; Client ID/Secret from the Omada portal. The controller uses a self-signed cert — TLS verification is skipped by default.' },
  uptime_kuma: { label: 'Uptime Kuma', seedable: false,
                 guidance: 'Not yet supported — configure manually.' },
  jellyfin:    { label: 'Jellyfin', seedable: true,
                 auth_type: 'header', auth_header: 'X-Emby-Token',
                 secret_hint: 'Jellyfin API key',
                 guidance: 'API key via X-Emby-Token header. Base URL is http://<jellyfin>:8096. Fully curated (no generic passthrough); all writes require approval.' },
  pihole:      { label: 'Pi-hole', seedable: true,
                 auth_type: 'query_token', auth_header: '',
                 secret_hint: 'Pi-hole API token',
                 guidance: 'v5 API token sent as ?auth=<token>. Base URL is http://<ip>/admin (tools append api.php?...). One integration record per Pi-hole instance (e.g. pihole2, pihole3) — each needs its own token.' },
  sonarr:      { label: 'Sonarr', seedable: true,
                 auth_type: 'header', auth_header: 'X-Api-Key',
                 secret_hint: 'Sonarr API key',
                 guidance: 'API key via X-Api-Key header (the ?apikey= query is gone in v4+). Base URL is http://<ip>:8989. Fully curated; writes are always approval-gated and searches/deletes never default on.' },
  radarr:      { label: 'Radarr', seedable: true,
                 auth_type: 'header', auth_header: 'X-Api-Key',
                 secret_hint: 'Radarr API key',
                 guidance: 'API key via X-Api-Key header (the ?apikey= query is gone in v5). Base URL is http://<ip>:7878. Fully curated; writes are always approval-gated and searches/deletes never default on.' },
  prowlarr:    { label: 'Prowlarr', seedable: true,
                 auth_type: 'header', auth_header: 'X-Api-Key',
                 secret_hint: 'Prowlarr API key',
                 guidance: 'API key via X-Api-Key header. Base URL is http://<ip>:9696 (uses /api/v1). Fully curated — indexer credentials in fields[] are never returned or shown on approval cards; all writes are approval-gated.' },
  npm:         { label: 'Nginx Proxy Manager', seedable: true,
                 auth_type: 'session', auth_header: '',
                 secret_hint: 'NPM user password',
                 guidance: 'Session auth (JWT + CSRF). Create a dedicated NPM admin user; Client ID = its email, Client Secret = its password, Token URL = http://<ip>:81/api/tokens. Base URL is http://<ip>:81/api. Fully curated; all writes require approval.' },
  truenas:     { label: 'TrueNAS', seedable: false,
                 guidance: 'Not yet supported — configure manually.' },
  custom:      { label: 'Custom', seedable: false,
                 guidance: 'Configure the upstream API manually.' },
};

function populateKindSelect() {
  const sel = document.getElementById('int-kind');
  if (!sel) return;
  sel.innerHTML = Object.keys(INTEGRATION_PROFILES).map(function(k) {
    return '<option value="' + k + '">' + INTEGRATION_PROFILES[k].label + '</option>';
  }).join('');
  onIntKindChange();
}

function onIntKindChange() {
  const kind = document.getElementById('int-kind').value;
  const profile = INTEGRATION_PROFILES[kind];
  if (!profile) return;
  const guidance = document.getElementById('int-guidance');
  const authFields = document.getElementById('int-auth-fields');
  const oauthFields = document.getElementById('int-oauth-fields');
  const authType = document.getElementById('int-auth-type');
  const authHeader = document.getElementById('int-auth-header');
  const secret = document.getElementById('int-secret');
  const verifyTls = document.getElementById('int-verify-tls');
  const isOAuth2 = profile.auth_type === 'oauth2' || profile.auth_type === 'session';
  if (guidance) {
    guidance.textContent = profile.guidance || '';
    guidance.classList.toggle('hidden', !profile.guidance);
  }
  if (oauthFields) oauthFields.classList.toggle('hidden', !isOAuth2);
  if (secret) secret.classList.toggle('hidden', isOAuth2);
  if (verifyTls && !_editingIntegration) verifyTls.checked = !!profile.insecure_tls;
  if (profile.auth_type) {
    if (authFields) authFields.classList.add('hidden');
    if (authType) authType.value = profile.auth_type;
    if (authHeader) authHeader.value = profile.auth_header || '';
  } else {
    if (authFields) authFields.classList.remove('hidden');
  }
  if (secret) secret.placeholder = profile.secret_hint || 'Secret / token';
  var clientSecret = document.getElementById('int-client-secret');
  if (clientSecret) clientSecret.placeholder = 'Client Secret';
}

async function fetchIntegrations() {
  populateKindSelect();
  fetchAgentTokens();
  fetchIntegrationList();
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
    if (!res.ok) { const d = await res.json().catch(function() { return {}; }); showToast('' + (d.detail || 'Failed'), 'error'); return; }
    fetchMcpSettings();
    showToast('MCP access updated', 'success');
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
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
    if (!res.ok) { showToast('' + (data.detail || 'Failed'), 'error'); return; }
    document.getElementById('agent-token-name').value = '';
    const box = document.getElementById('agent-token-result');
    box.classList.remove('hidden');
    box.innerHTML = '<strong>' + esc(name) + ' token (copy now — shown once):</strong><br>' + esc(data.token) +
      '<br><span class="text-muted">Paste this raw token into your client (e.g. Hermes) as-is — it adds the <code>Bearer </code> prefix itself.</span>';
    fetchAgentTokens();
    showToast('Agent token created', 'success');
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function deleteAgentToken(id) {
  if (!(await customConfirm('Delete this agent token? The agent will lose access immediately.'))) return;
  try {
    await authFetch('/api/agents/' + id, { method: 'DELETE' });
    fetchAgentTokens();
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

function renderIntegrationList() {
  const el = document.getElementById('integration-list');
  if (!el) return;
  if (!_integrationsData.length) { el.innerHTML = '<p class="text-muted">No integrations yet.</p>'; return; }
  el.innerHTML = _integrationsData.map(function(i) {
    var selected = _selectedIntegration === i.name;
    var active = i.enabled ? 'text-success' : 'text-muted';
    var seedable = !!(INTEGRATION_PROFILES[i.kind] && INTEGRATION_PROFILES[i.kind].seedable);
    var seedBtn = seedable
      ? '<button onclick="seedTools(\'' + esc(i.name) + '\')" class="btn btn-xs btn-muted" title="Seed the seed catalog for this integration">Seed</button>'
      : '';
    var label = INTEGRATION_PROFILES[i.kind] ? INTEGRATION_PROFILES[i.kind].label : (i.kind || 'custom');
    return '<div class="item-card selectable' + (selected ? ' selected' : '') + '" onclick="selectIntegration(\'' + esc(i.name) + '\')">' +
      '<div class="flex items-center justify-between gap-2">' +
      '<span class="text-sm font-semibold ' + active + '"><span class="' + (i.enabled ? 'dot-ok' : 'dot-off') + '"></span> ' + esc(i.name) + '</span>' +
      '<span class="text-xs text-muted">' + esc(label) + '</span></div>' +
      '<div class="text-xs text-muted mt-1">' + esc(i.base_url) + '</div>' +
      '<div class="text-xs text-muted">auth: ' + esc(i.auth_type) + ' · gate: ' + esc(i.gate_mode || 'destructive') + ' · mcp: ' + esc(i.mcp_mode || 'joined') + '</div>' +
      '<div class="flex flex-wrap gap-1 mt-2" onclick="event.stopPropagation()">' +
      '<button onclick="editIntegration(\'' + esc(i.name) + '\')" class="btn btn-xs btn-muted" title="Edit base URL / secret">Edit</button>' +
      '<button onclick="testIntegration(\'' + esc(i.name) + '\')" class="btn btn-xs btn-muted" title="Run a read call to verify the connection">Test</button>' +
      seedBtn +
      '<button onclick="deleteIntegration(\'' + esc(i.name) + '\')" class="btn btn-xs btn-muted">Delete</button>' +
      '</div></div>';
  }).join('');
}

async function fetchIntegrationList() {
  try {
    const res = await authFetch('/api/integrations');
    if (!res.ok) return;
    _integrationsData = await res.json();
    renderIntegrationList();
    syncMcpStars();
  } catch(e) {}
}

function resetIntegrationForm() {
  _editingIntegration = null;
  document.getElementById('int-name').disabled = false;
  document.getElementById('int-name').value = '';
  document.getElementById('int-base-url').value = '';
  document.getElementById('int-kind').value = 'proxmox';
  document.getElementById('int-secret').value = '';
  document.getElementById('int-client-id').value = '';
  document.getElementById('int-client-secret').value = '';
  document.getElementById('int-token-url').value = '';
  document.getElementById('int-gate-mode').value = 'destructive';
  var mcpMode = document.getElementById('int-mcp-mode');
  if (mcpMode) mcpMode.value = 'joined';
  var title = document.getElementById('int-form-title');
  if (title) title.textContent = 'Add New Integration';
  var badge = document.getElementById('int-edit-badge');
  if (badge) badge.classList.add('hidden');
  var cancel = document.getElementById('int-cancel-btn');
  if (cancel) cancel.classList.add('hidden');
  var formWidget = document.getElementById('int-form-widget');
  if (formWidget) formWidget.classList.remove('editing');
  document.getElementById('int-submit-btn').textContent = 'Add Integration';
  document.getElementById('integration-test-result').classList.add('hidden');
  onIntKindChange();
}

function editIntegration(name) {
  const i = _integrationsData.find(function(x) { return x.name === name; });
  if (!i) return;
  _editingIntegration = name;
  var kind = INTEGRATION_PROFILES[i.kind] ? i.kind : 'custom';
  document.getElementById('int-name').value = i.name;
  document.getElementById('int-name').disabled = true;
  document.getElementById('int-base-url').value = i.base_url || '';
  document.getElementById('int-kind').value = kind;
  document.getElementById('int-secret').value = '';
  onIntKindChange();
  // For profiles without a fixed auth, reflect the stored auth values.
  if (!INTEGRATION_PROFILES[kind].auth_type) {
    document.getElementById('int-auth-type').value = i.auth_type || 'bearer';
    document.getElementById('int-auth-header').value = i.auth_header_name || '';
  }
  // OAuth2 fields are never secrets in the list payload (client_secret is
  // stripped), so client_id / token_url round-trip; the secret stays blank.
  document.getElementById('int-client-id').value = i.client_id || '';
  document.getElementById('int-client-secret').value = '';
  document.getElementById('int-token-url').value = i.token_url || '';
  var verifyTls = document.getElementById('int-verify-tls');
  if (verifyTls) verifyTls.checked = i.verify_tls === 0 || i.verify_tls === false;
  var gateMode = document.getElementById('int-gate-mode');
  if (gateMode) gateMode.value = i.gate_mode || 'destructive';
  var mcpMode = document.getElementById('int-mcp-mode');
  if (mcpMode) mcpMode.value = i.mcp_mode || 'joined';
  var secretInput = document.getElementById('int-secret');
  if (secretInput && i.secret_suffix) {
    secretInput.placeholder = 'Key in use: ' + i.secret_suffix + ' — last 4 only; leave blank to keep current';
  }
  var clientSecretInput = document.getElementById('int-client-secret');
  if (clientSecretInput && i.client_secret_suffix) {
    clientSecretInput.placeholder = 'Client secret in use: ' + i.client_secret_suffix + ' — last 4 only; leave blank to keep current';
  }
  var title = document.getElementById('int-form-title');
  if (title) title.textContent = 'Edit Integration: ' + i.name;
  var badge = document.getElementById('int-edit-badge');
  if (badge) badge.classList.remove('hidden');
  var cancel = document.getElementById('int-cancel-btn');
  if (cancel) cancel.classList.remove('hidden');
  var formWidget = document.getElementById('int-form-widget');
  if (formWidget) formWidget.classList.add('editing');
  document.getElementById('int-submit-btn').textContent = 'Update Integration';
  document.getElementById('integration-test-result').classList.add('hidden');
  // Bring the form into view and flash it so the "editing" state is obvious
  var flashWidget = document.getElementById('int-base-url').closest('.widget');
  if (flashWidget) {
    flashWidget.scrollIntoView({ behavior: 'smooth', block: 'start' });
    flashWidget.style.outline = '2px solid var(--accent, #ffd700)';
    setTimeout(function() { flashWidget.style.outline = ''; }, 1600);
  }
}

async function createIntegration() {
  const name = document.getElementById('int-name').value.trim();
  const baseUrl = document.getElementById('int-base-url').value.trim();
  if (!name || !baseUrl) { showToast('Name and base URL are required', 'error'); return; }
  const payload = {
    base_url: baseUrl,
    kind: document.getElementById('int-kind').value,
    auth_type: document.getElementById('int-auth-type').value,
    auth_header_name: document.getElementById('int-auth-header').value.trim(),
  };
  const secret = document.getElementById('int-secret').value;
  if (secret) payload.secret = secret;
  const clientId = document.getElementById('int-client-id').value;
  const clientSecret = document.getElementById('int-client-secret').value;
  const tokenUrl = document.getElementById('int-token-url').value;
  if (clientId) payload.client_id = clientId;
  if (clientSecret) payload.client_secret = clientSecret;
  if (tokenUrl) payload.token_url = tokenUrl;
  const verifyTls = document.getElementById('int-verify-tls');
  if (verifyTls) payload.verify_tls = !verifyTls.checked;
  const gateMode = document.getElementById('int-gate-mode');
  if (gateMode) payload.gate_mode = gateMode.value;
  const mcpMode = document.getElementById('int-mcp-mode');
  if (mcpMode) payload.mcp_mode = mcpMode.value;
  if (_editingIntegration) {
    try {
      const res = await authFetch('/api/integrations/' + encodeURIComponent(_editingIntegration), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const data = await res.json();
      if (!res.ok) { showToast('' + (data.detail || 'Failed'), 'error'); return; }
      resetIntegrationForm();
      fetchIntegrationList();
      showToast('Integration updated', 'success');
    } catch(e) { showToast('Failed: ' + e.message, 'error'); }
    return;
  }
  payload.name = name;
  try {
    const res = await authFetch('/api/integrations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) { showToast('' + (data.detail || 'Failed'), 'error'); return; }
    resetIntegrationForm();
    fetchIntegrationList();
    showToast('Integration added', 'success');
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function testIntegration(name) {
  const box = document.getElementById('integration-test-result');
  if (!box) return;
  box.classList.remove('hidden');
  var close = '<button onclick="clearTestResult()" class="modal-close" title="Clear">×</button>';
  function line(inner) {
    return '<span class="flex items-center justify-between gap-2"><span class="min-w-0">' + inner + '</span>' + close + '</span>';
  }
  box.innerHTML = line('<span class="text-muted">Testing ' + esc(name) + '…</span>');
  try {
    const res = await authFetch('/api/integrations/' + encodeURIComponent(name) + '/test', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) { box.innerHTML = line('<span class="text-danger">Test failed: ' + esc(data.detail || 'error') + '</span>'); return; }
    var body = '';
    if (data.error) {
      body = '<span class="text-danger">Error: ' + esc(data.error) + '</span>';
    } else {
      body = 'HTTP ' + data.status_code + ' via ' + esc(data.tool) +
        (data.truncated ? ' (truncated)' : '') + ' — <code>' + esc(data.preview) + '</code>';
    }
    box.innerHTML = line(body);
  } catch(e) { box.innerHTML = line('<span class="text-danger">Test failed: ' + esc(e.message) + '</span>'); }
}

function clearTestResult() {
  var box = document.getElementById('integration-test-result');
  if (box) box.classList.add('hidden');
}

async function deleteIntegration(name) {
  if (!(await customConfirm('Delete integration "' + name + '" and all its tools?'))) return;
  try {
    await authFetch('/api/integrations/' + encodeURIComponent(name), { method: 'DELETE' });
    if (_selectedIntegration === name) _selectedIntegration = null;
    fetchIntegrationList();
    renderTools([]);
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function seedTools(name) {
  try {
    const res = await authFetch('/api/integrations/' + encodeURIComponent(name) + '/seed', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) { showToast('' + (data.detail || 'Failed'), 'error'); return; }
    showToast('Seeded tools (' + data.created + ' new, ' + data.updated + ' updated)', 'success');
    if (_selectedIntegration === name) fetchTools(name);
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function selectIntegration(name) {
  if (_selectedIntegration === name) {
    _selectedIntegration = null;
    renderIntegrationList();
    var el = document.getElementById('integration-tools-list');
    if (el) el.innerHTML = '<p class="text-muted">Select an integration to view its tools.</p>';
    return;
  }
  _selectedIntegration = name;
  renderIntegrationList();
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
  const bar = document.getElementById('integration-tools-bar');
  const count = document.getElementById('integration-tools-count');
  if (bar && count) {
    if (!name || !tools.length) { bar.classList.add('hidden'); }
    else {
      bar.classList.remove('hidden');
      const on = tools.filter(function(t){ return t.enabled; }).length;
      count.textContent = on + ' / ' + tools.length + ' enabled';
    }
  }
  if (!name || !tools.length) { el.innerHTML = '<p class="text-muted">No tools for this integration.</p>'; return; }
  el.innerHTML = tools.map(function(t) {
    var badge = t.read_only ? '<span class="text-success">read</span>' : '<span class="text-warning">mutating (approval)</span>';
    return '<div class="item-card">' +
      '<div class="flex items-center justify-between gap-2">' +
      '<span class="text-sm font-semibold">' + esc(t.name) + '</span>' +
      '<span class="text-xs">' + badge + ' · ' + esc(t.method) + '</span></div>' +
      '<div class="text-xs text-muted mt-1">' + esc(t.description || '') + '</div>' +
      '<div class="flex gap-1 mt-2">' +
      '<button onclick="toggleTool(' + t.id + ', ' + (t.enabled ? 'false' : 'true') + ')" class="btn btn-xs ' + (t.enabled ? 'btn-muted' : '') + '">' + (t.enabled ? 'Disable' : 'Enable') + '</button>' +
      '<button onclick="deleteTool(' + t.id + ', ' + (t.seeded ? 'true' : 'false') + ')" class="btn btn-xs btn-muted">×</button>' +
      '</div></div>';
  }).join('');
}

async function bulkSetTools(enabled) {
  if (!_selectedIntegration) return;
  const label = enabled ? 'Enable' : 'Disable';
  if (!(await customConfirm(label + ' all tools for ' + _selectedIntegration + '? They take effect for agents on the next /reload-mcp.'))) return;
  try {
    const res = await authFetch('/api/integrations/' + encodeURIComponent(_selectedIntegration) + '/tools/bulk', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: enabled }) });
    if (res.ok) { fetchTools(_selectedIntegration); showToast(label + 'd all tools for ' + _selectedIntegration, 'success'); }
    else showToast('Failed to ' + label.toLowerCase() + ' tools', 'error');
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function toggleTool(id, enabled) {
  try {
    const res = await authFetch('/api/integrations/' + encodeURIComponent(_selectedIntegration) + '/tools/' + id + '/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: enabled }) });
    if (res.ok) fetchTools(_selectedIntegration);
  } catch(e) {}
}

async function deleteTool(id, seeded) {
  var msg = seeded
    ? 'This tool is seed-managed (comes from the seed catalog) — it will be re-created, enabled, on the next reseed/restart. Use Disable to hide it from agents instead.\n\nDelete anyway?'
    : 'Delete this tool?';
  if (!(await customConfirm(msg))) return;
  try {
    await authFetch('/api/integrations/' + encodeURIComponent(_selectedIntegration) + '/tools/' + id, { method: 'DELETE' });
    fetchTools(_selectedIntegration);
  } catch(e) {}
}

async function approveIntegrationCall(id) {
  flashGlitch('APPROVED \u25B8 API', false);
  try {
    const res = await authFetch('/api/integration-calls/' + id + '/approve', { method: 'POST' });
    if (res.ok) { fetchRequests(); fetchIntegrationCalls(); showToast('Approved and executed', 'success'); }
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function denyIntegrationCall(id) {
  flashGlitch('DENIED \u25B8 API', true);
  try {
    const res = await authFetch('/api/integration-calls/' + id + '/deny', { method: 'POST' });
    if (res.ok) { fetchRequests(); }
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

let _callsSearchTimer = null;
let _callsPage = 1;
let _callsPageSize = 50;
let _callsSearch = '';
let _callsStart = null;
let _callsEnd = null;

function _localMidnightSec(y, m, d) {
  return Math.floor(new Date(y, m, d).getTime() / 1000);
}

function _callsDayRange(y, m, d) {
  // [midnight, next midnight) in the browser's local timezone (DST-safe).
  return { start: _localMidnightSec(y, m, d), end: _localMidnightSec(y, m, d + 1) };
}

function _parseCallsDate(str) {
  // Interpret a date-only query as a single local day range; otherwise null.
  var m;
  if ((m = str.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/))) return _callsDayRange(+m[3], +m[2] - 1, +m[1]);
  if ((m = str.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/))) return _callsDayRange(+m[1], +m[2] - 1, +m[3]);
  if ((m = str.match(/^(\d{4})-(\d{2})-(\d{2})$/))) return _callsDayRange(+m[1], +m[2] - 1, +m[3]);
  return null;
}

function setCallsRange(which) {
  var now = new Date();
  var range = null;
  if (which === 'today') {
    range = _callsDayRange(now.getFullYear(), now.getMonth(), now.getDate());
  } else if (which === 'yesterday') {
    var y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    range = _callsDayRange(y.getFullYear(), y.getMonth(), y.getDate());
  } else if (which === 'week') {
    var w = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6);
    range = { start: _localMidnightSec(w.getFullYear(), w.getMonth(), w.getDate()),
              end: Math.floor(now.getTime() / 1000) };
  }
  _callsStart = range ? range.start : null;
  _callsEnd = range ? range.end : null;
  _callsSearch = '';
  var box = document.getElementById('calls-search');
  if (box) box.value = '';
  _callsPage = 1;
  fetchIntegrationCalls();
}

function onCallsSearch() {
  clearTimeout(_callsSearchTimer);
  _callsSearchTimer = setTimeout(function() {
    var box = document.getElementById('calls-search');
    var val = box ? box.value.trim() : '';
    var range = _parseCallsDate(val);
    if (range) {
      _callsStart = range.start;
      _callsEnd = range.end;
      _callsSearch = '';
    } else {
      _callsStart = null;
      _callsEnd = null;
      _callsSearch = val;
    }
    _callsPage = 1;
    fetchIntegrationCalls();
  }, 250);
}

function clearCallsSearch() {
  var box = document.getElementById('calls-search');
  if (box) box.value = '';
  _callsSearch = '';
  _callsStart = null;
  _callsEnd = null;
  _callsPage = 1;
  fetchIntegrationCalls();
}

function onCallsPageSize() {
  var sel = document.getElementById('calls-page-size');
  _callsPageSize = parseInt(sel && sel.value, 10) || 50;
  _callsPage = 1;
  fetchIntegrationCalls();
}

function callsPrevPage() { if (_callsPage > 1) { _callsPage--; fetchIntegrationCalls(); } }
function callsNextPage() { _callsPage++; fetchIntegrationCalls(); }

async function fetchIntegrationCalls() {
  const el = document.getElementById('integration-calls-body');
  if (!el) return;
  try {
    var params = [];
    if (_callsSearch) params.push('search=' + encodeURIComponent(_callsSearch));
    if (_callsStart != null) params.push('start=' + _callsStart);
    if (_callsEnd != null) params.push('end=' + _callsEnd);
    params.push('limit=' + _callsPageSize);
    params.push('offset=' + ((_callsPage - 1) * _callsPageSize));
    var url = '/api/integration-calls' + (params.length ? '?' + params.join('&') : '');
    const res = await authFetch(url);
    if (!res.ok) return;
    const data = await res.json();
    const calls = data.rows || [];
    const total = data.total || 0;
    if (!calls.length) {
      el.innerHTML = '<tr><td colspan="9" class="px-4 py-3 text-muted">No calls match.</td></tr>';
    } else {
      el.innerHTML = calls.map(function(c) {
        var when = new Date(c.created_at * 1000).toLocaleString();
        var ok = c.outcome === 'ok';
        var cls = ok ? 'text-success' : 'text-danger';
        return '<tr>' +
          '<td class="text-muted">' + esc(when) + '</td>' +
          '<td>' + esc(c.integration) + '</td>' +
          '<td class="text-muted">' + esc(c.tool || '') + '</td>' +
          '<td class="text-muted">' + esc(c.agent || '') + '</td>' +
          '<td>' + esc(c.method) + '</td>' +
          '<td class="text-muted break-all">' + esc(c.path) + '</td>' +
          '<td class="' + cls + '">' + (c.status_code || '—') + '</td>' +
          '<td class="text-right text-muted">' + (c.latency_ms == null ? '—' : c.latency_ms + 'ms') + '</td>' +
          '<td class="' + cls + '">' + esc(c.outcome) + '</td>' +
          '</tr>';
      }).join('');
    }
    var pages = Math.max(1, Math.ceil(total / _callsPageSize));
    if (_callsPage > pages) _callsPage = pages;
    var countEl = document.getElementById('calls-count');
    var pageEl = document.getElementById('calls-page');
    var prevEl = document.getElementById('calls-prev');
    var nextEl = document.getElementById('calls-next');
    if (countEl) countEl.textContent = total + ' entries';
    if (pageEl) pageEl.textContent = 'Page ' + _callsPage + ' of ' + pages;
    if (prevEl) prevEl.disabled = _callsPage <= 1;
    if (nextEl) nextEl.disabled = _callsPage >= pages;
  } catch(e) {}
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(ch) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
  });
}

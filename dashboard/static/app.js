// State variables are in state.js — loaded first.
// Constants and state declarations here are removed to avoid redeclaration.

/* Chunk 4: one fetch wrapper adds the CSRF header to every state-changing
   same-origin call, including raw fetch() sites added later. The server still
   checks Origin or this header for session-cookie writes. */
(function () {
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    init = init ? Object.assign({}, init) : {};
    var method = (init.method || (input && input.method) || 'GET').toUpperCase();
    if (method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE') {
      var headers = new Headers(init.headers || (input && input.headers) || {});
      if (!headers.has('X-Eshu-CSRF')) headers.set('X-Eshu-CSRF', '1');
      init.headers = headers;
    }
    return nativeFetch(input, init);
  };
})();

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
  var ini = gwIni(hostname);
  if (!ini) return '';
  return '<span class="gw-pill">' + ini + '</span>';
}

function devBadge(g) {
  if (!g || (g.mode || 'prod') !== 'dev') return '';
  return '<span class="dev-pill">DEV</span>';
}

function toggleMobileSidebar(force) {
  var sidebar = document.getElementById('main-sidebar');
  var overlay = document.getElementById('sidebar-overlay');
  var btn = document.getElementById('mobile-menu-btn');
  if (!sidebar || !overlay) return;
  var open = (force === undefined) ? !sidebar.classList.contains('open') : !!force;
  sidebar.classList.toggle('open', open);
  overlay.classList.toggle('show', open);
  if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  document.body.classList.toggle('nav-open', open);
}
function closeMobileSidebar() {
  if (document.getElementById('main-sidebar').classList.contains('open')) toggleMobileSidebar(false);
}
// ── Command Center: Context Panel ────────────────────────────
var _ccSelectedId = null;
var _ccFilter = 'all';
var _ccSelectedIds = new Set();
var _activeSessionSid = null;
var _activeSessionHost = '';
var _activeSessionHostIp = '';

/* ── F18: request context sections ──────────────────────────────────────
   Five capped sections rendered from GET /api/requests/{id}/context, fetched
   only when the panel opens. Every agent string is escaped here; a section's
   open/closed state lives in localStorage (best effort). */
var _ctxReq = null;

function ctxCut(text) {
  return String(text == null ? '' : text).slice(0, 300);
}

function ctxOpenState(key) {
  try {
    return localStorage.getItem('ctx-open-' + key) !== '0';
  } catch (e) { return true; }
}

function ctxSaveOpen(key, open) {
  try { localStorage.setItem('ctx-open-' + key, open ? '1' : '0'); } catch (e) {}
}

function ctxSectionHtml(key, title, body) {
  if (!body) return '';
  var open = ctxOpenState(key);
  return '<details class="ctx-sec" data-ctx-sec="' + escapeHtml(key) + '"' +
    (open ? ' open' : '') + ' ontoggle="ctxSaveOpen(' + escJs(key) + ',this.open)">' +
    '<summary>' + escapeHtml(title) + '</summary>' +
    '<div class="ctx-sec-body">' + body + '</div></details>';
}

function ctxWaitingBody(d) {
  var waiting = (d || {}).waiting;
  // C3: only a pending row is waiting, and the reason is a re-derivation.
  if (!waiting) return '';
  return '<div class="ctx-wait"><span class="ctx-tag">Likely waiting because:</span> ' +
    escapeHtml(waiting) + '</div>';
}

function ctxSessionBody(d) {
  var s = (d || {}).session;
  if (!s) return '<div class="ctx-empty">No session on this request.</div>';
  var rows = (s.commands || []).map(function (c) {
    return '<div class="ctx-row"><code>' + escapeHtml(ctxCut(c.command)) + '</code>' +
      '<span class="ctx-tag">' + escapeHtml(c.status || '') + '</span>' +
      (c.exit_code === null || c.exit_code === undefined ? ''
        : '<span class="ctx-tag">exit ' + Number(c.exit_code) + '</span>') + '</div>';
  }).join('');
  return '<div class="ctx-goal">' + escapeHtml(s.goal || '') +
    (s.agent ? ' \u00b7 ' + escapeHtml(s.agent) : '') + '</div>' + rows +
    (s.plan ? '<div class="ctx-tag">plan #' + Number(s.plan.id) +
      ' until ' + formatTime(Number(s.plan.until)) + '</div>' : '') +
    (s.override ? '<div class="ctx-tag">session override active</div>' : '');
}

function ctxSeenBody(d) {
  var s = (d || {}).seen || {};
  if (!s.count) return '<div class="ctx-empty">Not seen before.</div>';
  var last = s.last_at ? formatAgo(Math.floor(Date.now() / 1000) - Number(s.last_at)) : '\u2014';
  // C1: the operator's decisions are not the same thing as the allowlist's.
  return '<div class="ctx-row">' + Number(s.count) + '\u00d7 on record \u00b7 approved by you ' +
    Number(s.operator_approved || 0) + ' \u00b7 auto ' + Number(s.auto || 0) +
    ' \u00b7 denied ' + Number(s.denied || 0) +
    ' \u00b7 last ' + escapeHtml(last) +
    (s.last_exit_code === null || s.last_exit_code === undefined ? ''
      : ' \u00b7 exit ' + Number(s.last_exit_code)) + '</div>' +
    '<button type="button" class="bd-hist-link" onclick="filterHistoryToCommand(' +
      escJs(ctxCut((d || {}).request_command || '')) + ')">show in history</button>';
}

function ctxLikeBody(d) {
  var s = (d || {}).like || {};
  // C2: a one-step interpreter shape matches thousands of unrelated commands.
  if (s.generic) return '<div class="ctx-empty">Too generic to compare.</div>';
  if (!s.count) return '<div class="ctx-empty">Nothing similar yet.</div>';
  var examples = (s.examples || []).map(function (e) {
    return '<div class="ctx-row"><code>' + escapeHtml(ctxCut(e.command)) + '</code>' +
      (e.hostname ? ' <span class="ctx-tag">' + escapeHtml(e.hostname) + '</span>' : '') +
      '</div>';
  }).join('');
  return '<div class="ctx-row">' + Number(s.count) + ' similar \u00b7 approved by you ' +
    Number(s.operator_approved || 0) + ' \u00b7 auto ' + Number(s.auto || 0) +
    ' \u00b7 denied ' + Number(s.denied || 0) + '</div>' + examples;
}

function ctxHostBody(d) {
  var h = (d || {}).host || {};
  return '<div class="ctx-row">' + escapeHtml(h.hostname || '') +
    (h.inner_target ? ' \u00b7 inside ' + escapeHtml(h.inner_target) : '') +
    (h.zero_trust ? ' \u00b7 Zero-Trust' : '') +
    ' \u00b7 read lane ' + escapeHtml(h.read_lane || 'off') +
    ' \u00b7 failed 24h ' + Number(h.failed_24h || 0) + '</div>';
}

function renderRequestContext(data) {
  var el = document.getElementById('ctx-context');
  if (!el) return;
  el.innerHTML =
    ctxSectionHtml('waiting', 'Why it is waiting for you', ctxWaitingBody(data)) +
    ctxSectionHtml('session', 'This session', ctxSessionBody(data)) +
    ctxSectionHtml('seen', 'Seen before', ctxSeenBody(data)) +
    ctxSectionHtml('like', 'Like this', ctxLikeBody(data)) +
    ctxSectionHtml('host', 'Host', ctxHostBody(data));
}

function loadRequestContext(requestId, command) {
  var el = document.getElementById('ctx-context');
  if (!el) return;
  el.innerHTML = '<div class="ctx-empty">Loading context\u2026</div>';
  authFetch('/api/requests/' + Number(requestId) + '/context').then(function (res) {
    return res.ok ? res.json() : null;
  }).then(function (data) {
    if (!data || !_ctxReq || String(_ctxReq.id) !== String(requestId)) return;
    data.request_command = command || (_ctxReq && _ctxReq.command) || '';
    renderRequestContext(data);
  }).catch(function () {
    if (el && _ctxReq && String(_ctxReq.id) === String(requestId)) {
      el.innerHTML = '<div class="ctx-empty">Could not load the context.</div>';
    }
  });
}

function filterHistoryToCommand(command) {
  var text = ctxCut(command);
  if (!text) return;
  _requestSearchQuery = text;
  var box = document.getElementById('request-search');
  if (box) box.value = text;
  switchView('history');
  fetchRequests();
}

/* X1: the sidebar's approve label reflects F2's two-press rule for the open
   request, and the actions dispatch on the request type first — a pending
   integration call used to fall into the SSH branch and POST /api/approve. */
function contextApproveLabel(req) {
  return (req && _bdUnknownConfirmed[req.id]) ? 'Press again to approve' : 'Approve';
}

function contextActionsHtml(req) {
  if (!req) return '';
  if (req._type === 'integration') {
    return '<button data-ctx-approve="1" onclick="approveIntegrationCall(' + idJs(req.id) + ')" class="btn btn-approve btn-sm" style="flex:1">' + contextApproveLabel(req) + '</button>' +
      '<button onclick="denyIntegrationCall(' + idJs(req.id) + ')" class="btn btn-deny btn-sm" style="flex:1">Deny</button>';
  }
  if (req._type === 'window') {
    return '<button onclick="approveWindowReq(' + idJs(req.id) + ')" class="btn btn-approve btn-sm" style="flex:1">Approve</button>' +
      '<button onclick="denyWindowReq(' + idJs(req.id) + ')" class="btn btn-deny btn-sm" style="flex:1">Deny</button>';
  }
  if (req.status === 'pending') {
    return '<button data-ctx-approve="1" onclick="handleAction(' + idJs(req.id) + ',\'approve\')" class="btn btn-approve btn-sm" style="flex:1">' + contextApproveLabel(req) + '</button>' +
      '<button onclick="handleAction(' + idJs(req.id) + ',\'deny\')" class="btn btn-deny btn-sm" style="flex:1">Deny</button>';
  }
  return '<button onclick="closeContextPanel()" class="btn btn-muted btn-sm" style="flex:1">Close</button>';
}

function renderContextActions(req) {
  var el = document.getElementById('ctx-actions');
  if (el) el.innerHTML = contextActionsHtml(req);
}

/* When the arming press happens while the sidebar shows the request, the
   sidebar must show it too: re-render its button and flash its own unknown
   step (the queue card's copy flashes separately). */
function armSidebarConfirm(reqId) {
  if (!_ctxReq || String(_ctxReq.id) !== String(reqId)) return;
  renderContextActions(_ctxReq);
  var steps = document.getElementById('ctx-breakdown-steps');
  if (steps && steps.querySelector) {
    var el = steps.querySelector('[data-bd-unknown-for="' + esc(String(reqId)) + '"]');
    if (el) {
      if (el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
      el.classList.add('bd-flash');
      setTimeout(function () { el.classList.remove('bd-flash'); }, 1800);
    }
  }
}

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
  _ctxReq = req;

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
    /* "What it does" is the whole reason this panel exists. It used to print
       "No description available" for any command outside the description map,
       while the card beside it already carried an impact line — so fall back
       through: plain description -> risk line -> an honest sentence. */
    var whatItDoes = describeCmd(req.command) || req.risk || '';
    if (!whatItDoes) whatItDoes = 'No plain-language description for this command — read it as written.';
    document.getElementById('ctx-human').textContent = whatItDoes;
    var hostLabel = req.hostname || req.target_ip || 'Unknown host';
    document.getElementById('ctx-target').innerHTML =
      '<span class="ctx-meta-item">Host: ' + escapeHtml(hostLabel) + '</span>' +
      (req.target_ip && req.target_ip !== hostLabel ? '<span class="ctx-meta-item">IP: ' + escapeHtml(req.target_ip) + '</span>' : '');
  }
  /* Breakdown (F2): headline at the top, steps under the raw command. */
  var bdHeadSec = document.getElementById('ctx-breakdown-head-section');
  var bdStepsSec = document.getElementById('ctx-breakdown-steps-section');
  var bdHeadEl = document.getElementById('ctx-breakdown-head');
  var bdStepsEl = document.getElementById('ctx-breakdown-steps');
  if (bdHeadSec && bdStepsSec && bdHeadEl && bdStepsEl) {
    var hasBd = bdUsable(req);
    bdHeadSec.classList.toggle('hidden', !hasBd);
    bdStepsSec.classList.toggle('hidden', !hasBd);
    bdHeadEl.innerHTML = hasBd ? bdHeadlineHtml(req) : '';
    bdStepsEl.innerHTML = hasBd ? bdBodyHtml(req) : '';
  }
  document.getElementById('ctx-status').textContent = req.status || '—';
  document.getElementById('ctx-time').textContent = req.created_at ? formatTime(req.created_at) : '—';

  // Risk assessment + decoded preview for encoded wrappers
  var riskEl = document.getElementById('ctx-risk');
  var decoded = bdDecodedPreviewHtml(req);
  if (req.risk || req.anomaly) {
    var html = '';
    if (req.risk) html += '<div class="ctx-meta-item" style="color:var(--status-warning);margin-bottom:4px"> ' + escapeHtml(req.risk) + '</div>';
    if (req.anomaly) html += '<div class="ctx-meta-item" style="color:var(--danger)"> ' + escapeHtml(req.anomaly) + '</div>';
    riskEl.innerHTML = html + decoded;
  } else if (decoded) {
    riskEl.innerHTML = decoded;
  } else {
    riskEl.innerHTML = '<span class="ctx-meta-item" style="color:var(--text-muted)">No flags</span>';
  }
  // History rows carry only the `encoded` flag (decoding every row on every 3s
  // poll would be megabytes). Fetch the preview for the open panel on demand.
  if (req.encoded && !req.decoded_command) {
    authFetch('/api/requests/' + req.id + '/decoded').then(function (res) {
      return res.json();
    }).then(function (d) {
      if (!d || !d.decoded_command) return;
      req.decoded_command = d.decoded_command;
      req.decoded_truncated = d.decoded_truncated;
      var el = document.getElementById('ctx-risk');
      if (el) el.innerHTML += decodedCmdHtml(req);
    }).catch(function () {});
  }

  // Action buttons (X1: dispatch on the request type first)
  renderContextActions(req);

  // F18: the context sections are fetched only when the panel opens.
  if (req._type === 'integration' || req._type === 'window') {
    var ctxEl = document.getElementById('ctx-context');
    if (ctxEl) ctxEl.innerHTML = '';
  } else {
    loadRequestContext(req.id, req.command);
  }
}

/* When the panel is showing the request you just approved or denied, the
   panel has nothing left to say — close it. */
function closePanelIfShowing(id) {
  if (String(_ccSelectedId) === String(id)) closeContextPanel();
}

function closeContextPanel() {
  var panel = document.getElementById('cc-context');
  if (panel) panel.classList.add('collapsed');
  _ccSelectedId = null;
  _ctxReq = null;
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
        var cb = tickets[i].querySelector('.jit-check');
        if (cb) { cb.classList.add('checked'); cb.setAttribute('aria-checked', 'true'); }
      }
    }
  } else {
    var checkEl = (ev && ev.currentTarget) ? ev.currentTarget : null;
    if (_ccSelectedIds.has(reqId)) {
      _ccSelectedIds.delete(reqId);
      if (checkEl) { checkEl.classList.remove('checked'); checkEl.setAttribute('aria-checked', 'false'); }
    } else {
      _ccSelectedIds.add(reqId);
      if (checkEl) { checkEl.classList.add('checked'); checkEl.setAttribute('aria-checked', 'true'); }
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
  /* B1: a request that still needs the two-press review is NOT approved here
     and stays selected, so the operator sees it instead of believing the
     whole selection went through. Everything else is sent immediately. */
  var kept = [];
  Array.from(_ccSelectedIds).forEach(function(id) {
    if (id.indexOf('int-') === 0) { approveIntegrationCall(id.slice(4)); }
    else if (id.indexOf('win-') === 0) { approveWindowReq(id.slice(4)); }
    else {
      var req = requestsData.find(function (r) { return String(r.id) === String(id); });
      if (req && bdNeedsConfirm(req)) { kept.push(id); return; }
      handleAction(id, 'approve');
    }
    if (kept.indexOf(id) < 0) _ccSelectedIds.delete(id);
  });
  /* Only the kept ones stay checked; clearSelection() would drop them too. */
  document.querySelectorAll('.jit-check.checked').forEach(function(el) {
    var holder = el.closest ? el.closest('.jit-ticket, .ccq-row') : null;
    var held = holder ? (holder.getAttribute('data-id')
                         || holder.getAttribute('data-queue-id')) : null;
    if (held && kept.indexOf(held) >= 0) return;
    el.classList.remove('checked');
    el.setAttribute('aria-checked', 'false');
  });
  updateBulkBar();
  if (kept.length) {
    showToast(kept.length + ' request' + (kept.length === 1 ? ' has' : 's have') +
      ' unknown steps \u2014 review and approve ' + (kept.length === 1 ? 'it' : 'them') +
      ' individually.', 'info');
  }
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
function toggleMute() {
  soundMuted = !soundMuted;
  var lbl = document.getElementById('mute-label');
  if (lbl) lbl.textContent = soundMuted ? 'Sound Off' : 'Sound On';
  showToast(soundMuted ? 'Muted' : 'Unmuted', 'success');
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
/* `hidden` (the attribute) as well as the class: before style.css lands, the
   class means nothing and the login card — banner and all — paints at full
   size for a frame on a cold start. */
function hideAllOverlays() {
  var so = document.getElementById('setup-overlay'), lo = document.getElementById('login-overlay');
  so.classList.add('hidden'); so.setAttribute('hidden', '');
  lo.classList.add('hidden'); lo.setAttribute('hidden', '');
  document.getElementById('main-content').style.display = '';
  document.getElementById('main-sidebar').style.display = '';
  document.body.classList.add('authed');
}
function showDashboard() { hideAllOverlays(); }
function showSetup() {
  document.body.classList.remove('authed');
  document.getElementById('setup-overlay').classList.remove('hidden');
  document.getElementById('setup-overlay').removeAttribute('hidden');
  document.getElementById('login-overlay').classList.add('hidden');
  document.getElementById('main-content').style.display = 'none';
  document.getElementById('main-sidebar').style.display = 'none';
  document.getElementById('setup-password').value = '';
  document.getElementById('setup-password-confirm').value = '';
  document.getElementById('setup-error').classList.add('hidden');
  document.getElementById('setup-password').focus();
}
function showLogin() {
  document.body.classList.remove('authed');
  document.getElementById('setup-overlay').classList.add('hidden');
  document.getElementById('login-overlay').classList.remove('hidden');
  document.getElementById('login-overlay').removeAttribute('hidden');
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
  const setupToken = (document.getElementById('setup-token') || {}).value || '';
  const errEl = document.getElementById('setup-error'), btn = document.getElementById('setup-btn');
  if (!pw || pw.length < 12) { errEl.textContent = 'Password must be at least 12 characters.'; errEl.classList.remove('hidden'); return; }
  if (pw !== pwConfirm) { errEl.textContent = 'Passwords do not match.'; errEl.classList.remove('hidden'); return; }
  btn.disabled = true; btn.textContent = 'Setting up...'; errEl.classList.add('hidden');
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (setupToken) headers['X-Eshu-Setup-Token'] = setupToken;
    const res = await fetch('/api/auth/set-password', { method: 'POST', headers: headers, body: JSON.stringify({ password: pw }) });
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
function syncNavAria() {
  document.querySelectorAll('.nav-item').forEach(function(el) {
    var on = el.classList.contains('active');
    if (on) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
}

async function switchView(target) {
  cancelInlineConfirm();
  var winModal = document.getElementById('create-window-modal');
  if (winModal && !winModal.classList.contains('hidden') && typeof customConfirm === 'function') {
    var ok = await customConfirm('You have unsaved changes in the Window editor.', { title: 'Discard unsaved changes?', confirmLabel: 'Discard' });
    if (!ok) return;
    closeCreateWindowModal();
  }
  VIEWS.forEach(function(v) {
    document.getElementById('view-' + v).classList.toggle('hidden', v !== target);
    document.getElementById('view-' + v).classList.toggle('block', v === target);
    var navBtn = document.getElementById('nav-' + v);
    if (navBtn) navBtn.classList.toggle('active', v === target);
  });
  syncNavAria();
  closeMobileSidebar();
  if (target === 'gateways') { fetchGateways(); fetchEnrollData(); fetchGatewayHourly(); }
  if (target === 'history') { fetchRequests(); fetchIntegrationCalls(); }
  if (target === 'windows') { fetchWindowsTable(); }
  if (target === 'stats') { fetchStatistics(); fetchSuggestions(); }
  if (target === 'controls') { fetchFreezeStatus(); fetchGateways(); fetchPolicies(); fetchPolicyChanges(); initWebFetchPolicyPanel(); }
  if (target === 'fleet') { fetchFleetCommands(); }
  if (target === 'integrations') { fetchIntegrations(); }
  if (target === 'inventory') { fetchInventory(); }
  if (target === 'runbooks') { fetchRunbooks(); }
  if (target === 'agents') { fetchAgentTokens(); }
  if (target === 'settings') {
    refreshPasswordUI();
    fetchNotifyConfig();
    // fetchDevTools() is async; wait for it before deciding whether the dev
    // widgets (and the feature-flag list) should load. Without this, the
    // first visit to Settings left the list on "Loading...".
    fetchDevTools().then(function () {
      if (_devToolsEnabled) {
        fetchDevStatus();
        fetchDevGateways().then(fetchFeatureFlags);
      }
    });
  }
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
  if (!pw || pw.length < 12) { showToast('Password must be at least 12 characters', 'error'); return; }
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
    if (_devToolsEnabled) {
      fetchDevStatus();
      fetchDevGateways().then(fetchFeatureFlags);
    }
    showToast(_devToolsEnabled ? 'Development tools enabled' : 'Development tools disabled', 'success');
  } catch(e) {
    _devToolsEnabled = !_devToolsEnabled;
    showToast('Failed to update setting', 'error');
  }
}

// ── Dev Mode Features ────────────────────────────────────────────────
// A short, plain-English name/help for the flags that operators are most
// likely to touch. The technical name stays visible beside the label.
const FLAG_META = {
  require_sessions: {
    label: 'Require named sessions',
    help: 'Agents must call session_begin(goal=..., session_id=...) and use the signed id on every SSH command and MCP call. Off by default; existing agents keep working until you enable it.'
  }
};

function flagStateText(on, scope) {
  if (!on) return 'Off — no enforcement.';
  return scope === 'prod'
    ? 'On for every gateway and all MCP calls.'
    : 'On for dev-mode gateways and all MCP calls.';
}

async function fetchFeatureFlags() {
  try {
    const res = await fetch('/api/feature-flags'); const flags = await res.json();
    const container = document.getElementById('feature-flags-list');
    if (!container) return;
    let html = '';
    var hasDev = (_devGateways || []).length > 0;
    Object.entries(flags).forEach(function(e) {
      var name = e[0], info = e[1], on = info.enabled, sc = info.scope || 'dev';
      var meta = FLAG_META[name] || {};
      var label = meta.label || name;
      var desc = meta.help || info.description || '';
      var state = 'off';
      if (on && sc === 'dev') state = 'dev';
      if (on && sc === 'prod') state = 'prod';
      html += '<div class="flag-row">' +
        '<div class="flag-copy">' +
          '<div><span class="text-sm text-main">' + escapeHtml(label) + '</span>' +
            (meta.label ? ' <code class="text-xs text-muted">' + escapeHtml(name) + '</code>' : '') +
          '</div>' +
          '<p class="text-xs text-muted">' + escapeHtml(desc) + '</p>' +
          '<p class="text-xs text-muted flag-state">' + escapeHtml(flagStateText(on, sc)) + '</p>' +
        '</div>' +
        '<div class="seg-group">' +
          renderSeg('off', state, name, false) +
          renderSeg('dev', state, name, !hasDev) +
          renderSeg('prod', state, name, false) +
        '</div></div>';
    });
    container.innerHTML = html || '<p class="text-muted">No feature flags configured.</p>';
  } catch(e) {}
}

function renderSeg(val, current, name, disabled) {
  var active = val === current;
  var label = val === 'off' ? 'Off' : val === 'dev' ? 'Dev' : 'Prod';
  var title = val === 'off'
    ? 'No enforcement'
    : val === 'dev'
      ? 'Dev-mode gateways and all MCP calls'
      : 'Every gateway and all MCP calls';
  if (disabled && !active) title = 'No dev gateways configured';
  return '<button onclick="setFeatureFlagState(' + escJs(name) + ',' + escJs(val) + ')" ' +
    'class="seg-btn' + (active ? ' active ' + val : '') + (disabled && !active ? ' seg-disabled' : '') + '" ' +
    (disabled && !active ? 'disabled ' : '') + 'title="' + escapeHtml(title) + '">' + label + '</button>';
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
      escapeHtml(g.hostname||g.ip) + ' <span onclick="removeDevGateway(' + escJs(g.ip) + ')" class="remove" title="Remove from dev">&times;</span></span>';
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
    return '<div data-dev-host="' + esc(g.hostname || '') + '" class="search-result">' +
      gwPill(g.hostname) + ' ' + escapeHtml(g.hostname) + ' <span class="text-muted">(' + escapeHtml(g.ip) + ')</span></div>';
  }).join('');
}

document.addEventListener('click', function(e) {
  var el = e.target && e.target.closest ? e.target.closest('[data-dev-host]') : null;
  if (!el) return;
  var input = document.getElementById('dev-gateway-input');
  if (input) input.value = el.getAttribute('data-dev-host') || '';
  addDevGateway();
});
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
    var color = past ? 'var(--state-denied)' : 'var(--state-pending)';
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
    html += '<br><span class="win-expiry" style="color:' + (past ? 'var(--state-denied)' : 'var(--state-pending)') + ';">Expires: ' + expStr + (past ? ' (expired)' : '') + '</span>';
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
    updateHudSummaries({ windows: wins });
    var gwMap = {};
    try { var gr = await fetch('/api/gateways'); (await gr.json()).forEach(function(g) { gwMap[g.ip] = g; }); } catch(e) {}
    var tbody = document.getElementById('windows-table-body');
    if (!tbody) return;
    if (!wins.length) { tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-3 text-muted">No approved windows created yet.</td></tr>'; return; }
    var tableNow = Math.floor(Date.now() / 1000);
    tbody.innerHTML = wins.map(function(w) {
      var stat = w.enabled ? '<span class="badge badge-approved cursor-pointer" onclick="toggleWindow(' + idJs(w.id) + ',true)" title="Click to disable"> On</span>' :
        (w.status === 'pending_review' ? '<span class="badge badge-pending"> Review</span>' :
         (w.status === 'denied' ? '<span class="badge badge-window-rejected"> Denied</span>' :
          '<span class="badge badge-expired cursor-pointer" onclick="toggleWindow(' + idJs(w.id) + ',false)" title="Click to enable"> Off</span>'));
      var gw = gwMap[w.target_ip] || {};
      var originBadge = (w.origin === 'ai')
        ? '<span class="origin-ai" title="Inbound — AI requested">AI</span> '
        : '<span class="origin-human" title="Outbound — operator created">Human</span> ';
      var labelHtml = originBadge + (w.label ? '<span class="text-main font-medium">' + escapeHtml(w.label) + '</span><br>' : '') +
        '<code class="text-xs font-mono text-muted" title="' + escapeHtml(w.token||'') + '">' + escapeHtml((w.token||'').substring(0, 8)) + '…</code>' +
        ' <span onclick="copyToClipboard(' + escJs(w.token||'') + ')" class="cursor-pointer text-xs opacity-40 hover:opacity-100" title="Copy token">Copy</span>';
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
        '<td class="text-xs text-muted whitespace-nowrap" title="Numeric window ID — refer to it when coordinating with the agent">#' + idText(w.id) + '</td>' +
        '<td>' + stat + '</td>' +
        '<td>' + labelHtml + '</td>' +
        '<td class="text-xs">' + gwPill(gw.hostname||w.target_ip) + ' ' + escapeHtml(gw.hostname||w.target_ip) + '</td>' +
        '<td><code class="win-cmd-code" title="' + escapeHtml(w.command) + '">' + escapeHtml(w.match_type === 'prefix' ? w.command + '*' : w.command) + '</code></td>' +
        '<td class="text-xs text-muted">' + winScheduleCell(w) + (nextRunStr ? '<br>' + nextRunStr : '') + '</td>' +
        '<td class="text-center text-xs text-muted">' + execInfo + '</td>' +
        '<td class="text-right"><div class="flex items-center justify-end gap-1">' +
          (w.status === 'pending_review'
            ? '<button onclick="approveWindowReq(' + idJs(w.id) + ')" class="btn btn-approve btn-xs"> Approve</button>' +
              '<button onclick="denyWindowReq(' + idJs(w.id) + ')" class="btn btn-deny btn-xs"> Deny</button>'
            : '<button onclick="openEditWindowModal(' + idJs(w.id) + ')" class="btn btn-muted btn-xs" title="Edit">Edit</button>' +
              '<button onclick="showWindowHistory(' + idJs(w.id) + ')" class="btn btn-muted btn-xs" title="Usage history">History</button>' +
              '<button onclick="deleteWindow(this, ' + idJs(w.id) + ')" class="btn btn-deny btn-xs" title="Delete">Delete</button>') +
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
  display.innerHTML = gwPill(gw.hostname||gw.ip) + ' ' + escapeHtml(gw.hostname||gw.ip) + ' <span class="text-xs text-muted">(' + escapeHtml(ip) + ')</span>';
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
        return '<div class="gw-dropdown-item" onclick="selectGateway(' + escJs(g.ip) + ')">' + gwPill(g.hostname||g.ip) + ' ' + escapeHtml(g.hostname||g.ip) + ' <span class="gw-dropdown-ip">' + escapeHtml(g.ip) + '</span></div>';
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
      return '<div class="gw-dropdown-item" onclick="selectGateway(' + escJs(g.ip) + ')">' + gwPill(g.hostname||g.ip) + ' ' + escapeHtml(g.hostname||g.ip) + ' <span class="gw-dropdown-ip">' + escapeHtml(g.ip) + '</span></div>';
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
    if (data.action === 'blocked') { bg='rgba(220,38,38,0.08)'; border='rgba(220,38,38,0.3)'; text='var(--danger)'; icon=''; desc='This command is BLOCKED by policy — cannot create window.'; }
    else if (data.action === 'auto_approved') { bg='rgba(245,158,11,0.08)'; border='rgba(245,158,11,0.3)'; text='var(--accent)'; icon=''; desc='Already auto-approved — no window needed.'; }
    else { bg='rgba(205,216,230,0.06)'; border='rgba(205,216,230,0.25)'; text='var(--ice)'; icon=''; desc='Would require JIT — a window will auto-approve this.'; }
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

async function deleteWindow(btn, id) {
  if (!(await inlineConfirm(btn, { question: 'Delete this window?', label: 'Delete' }))) return;
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
function pulseHudSky(key) {
  if (!key) return;
  document.querySelectorAll('.hud-sky circle.mg').forEach(function (c) {
    if (c.getAttribute('data-node') !== key) return;
    c.classList.remove('pulse');
    void c.getBoundingClientRect();
    c.classList.add('pulse');
    setTimeout(function () { c.classList.remove('pulse'); }, 2400);
  });
}

function ensureHudSky() {
  document.querySelectorAll('.hud').forEach(function (hud) {
    if (hud.querySelector('.hud-sky')) return;
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'hud-sky');
    svg.addEventListener('click', function () { switchView('gateways'); });
    svg.setAttribute('viewBox', '0 0 150 40');
    svg.setAttribute('role', 'img');
    /* Fixed slot on the right edge of the band. Inline next to the eyebrow it
       slid left and right as page titles changed length, which read as jitter. */
    hud.appendChild(svg);
  });
  renderHudSky();
}

async function initDashboard() { ensureHudSky(); updateNotifyUI(); fetchVersion(); fetchRequests(); fetchCmdDescs(); fetchSuggestions(); refreshPasswordUI(); fetchFreezeStatus(); fetchFleetCommands(); fetchGateways(); fetchIntegrations(); loadSessionNames(); refreshSessionOverrides(); maybeShowOnboarding(); }

// ── Requests ─────────────────────────────────────────────────────────────
let _requestSearchQuery = '';
function onRequestSearch() {
  _requestSearchQuery = document.getElementById('request-search').value.trim();
  fetchRequests();
}
/* The page headers used to print invented telemetry: "1,204 crossings this
   week", "15/16 online · 1 overriding · 0 pending", "3 open · next 03:00 UTC".
   Those were literals in index.html, never written by any code path — on a real
   dataset they claimed 0 pending while five requests were waiting. Every summary
   now comes from the data already in memory, and says nothing when it has none. */
function updateHudSummaries(opts) {
  opts = opts || {};
  var now = Math.floor(Date.now() / 1000);
  var reqs = (typeof requestsData !== 'undefined' && requestsData) || [];

  var hist = document.getElementById('history-hud-sum');
  if (hist) {
    var week = reqs.filter(function(r){ return r.created_at && (now - r.created_at) <= 7*86400 && (now - r.created_at) >= 0; }).length;
    hist.innerHTML = '<span><span class="n">' + week.toLocaleString() + '</span> crossing' + (week === 1 ? '' : 's') + ' this week</span>';
  }

  var gws = _gatewaysData || _allGateways || [];
  var gwEl = document.getElementById('gateways-hud-sum');
  if (gwEl && gws.length) {
    var online = gws.filter(function(g){ return gwIsOnline(g); }).length;
    var ovr = gws.filter(function(g){ return (g.override_remaining || 0) > 0; }).length;
    var pend = reqs.filter(function(r){ return r.status === 'pending'; }).length;
    gwEl.innerHTML = '<span><span class="n">' + online + '/' + gws.length + '</span> online</span>' +
      (ovr ? '<span><span class="o">' + ovr + '</span> overriding</span>' : '') +
      '<span><span class="p">' + pend + '</span> pending</span>';
  }

  var winEl = document.getElementById('windows-hud-sum');
  var wins = opts.windows || window._windowsCache;
  if (winEl && wins) {
    window._windowsCache = wins;
    var open = wins.filter(function(w){ return w.enabled; }).length;
    var next = null;
    wins.forEach(function(w){
      if (!w.enabled) return;
      var d = (Number(w.days_of_week) || Number(w.execution_time)) ? computeNextRun(Number(w.days_of_week)||0, Number(w.execution_time)||0)
              : (Number(w.window_start) && Number(w.window_start)*1000 > Date.now() ? new Date(Number(w.window_start)*1000) : null);
      if (d && (!next || d < next)) next = d;
    });
    winEl.innerHTML = '<span><span class="n">' + open + '</span> open</span>' +
      (next ? '<span>next <span class="n">' + formatNextRun(next) + '</span></span>' : '');
  }
}

async function fetchRequests() {
  try {
    let url = '/api/requests';
    let q = [];
    if (_requestSearchQuery) q.push('search=' + encodeURIComponent(_requestSearchQuery));
    if (_historySession) q.push('session=' + encodeURIComponent(_historySession));
    if (_historyInnerTarget) q.push('inner_target=' + encodeURIComponent(_historyInnerTarget));
    if (q.length) url += '?' + q.join('&');
    const res = await fetch(url);
    if (res.status === 401) { await checkAuth(); return; }
    const data = await res.json();
    detectNewJITs(data);
    requestsData = data;
    updateHudSummaries();
  renderHudSky();
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
    fetchSessionPlans();
  } catch(err) {}
}
function detectNewJITs(newData) {
  const currentPendingIds = new Set();
  newData.forEach(function(r) { if (r.status === 'pending' && r.ttl > 0) currentPendingIds.add(r.id); });
  const newIds = [];
  currentPendingIds.forEach(function(id) { if (!_knownPendingIds.has(id)) newIds.push(id); });
  _knownPendingIds = currentPendingIds;
  if (newIds.length > 0) {
    // the header map is the same sky: let the host's star pulse there too
    newData.forEach(function (r) {
      if (newIds.indexOf(r.id) !== -1) pulseHudSky(r.target_ip || r.hostname);
    });
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
// Single source of truth (mirrors core/gateway_watch.DISCONNECTED_AFTER).
// `/api/gateways` returns a derived `online` bool; gwIsOnline() prefers it so the
// UI never depends on the client clock. Fallback recomputes from last_seen.
var GATEWAY_OFFLINE_AFTER = 120; // seconds without contact before a gateway is reported offline
function gwIsOnline(g) {
  if (!g) return false;
  if (typeof g.online === 'boolean') return g.online;
  return (Math.floor(Date.now() / 1000) - (g.last_seen || 0)) < GATEWAY_OFFLINE_AFTER;
}

function emptyStateSignature() {
  var now = Math.floor(Date.now() / 1000);
  var gwSig = (_gatewaysData || []).map(function(g){
    var stale = !gwIsOnline(g);
    return (g.hostname || g.ip) + ':' + (stale ? '0' : '1');
  }).sort().join('|');
  // Include session count so recent sessions panel re-renders
  var sessionCount = 0;
  (requestsData || []).forEach(function(r) { if (r.session_id && r.session_id !== 'unknown') sessionCount++; });
  return gwSig + '@' + sessionCount;
}

// ── Constellation starfield (replaces the radar) ────────────────────────
/* Star charts, not doodles.
   Coordinates are projected from real equatorial positions (RA hours, Dec degrees):
   x = -RA·15·cos(dec), y = dec (north up), then uniformly normalised so the longer
   side is 1. `w`/`h` keep the true width:height ratio of each figure, which is what
   the renderer needs to draw it without stretching. Shapes that were eyeballed
   before (and then squashed by ASPECT and rotated at random) now read as their
   names: Orion with a level belt, Cassiopeia's W, the Northern Cross, Vega's
   parallelogram, the Big Dipper. */
var CONSTELLATIONS = [
  {name:'Orion', meaning:'the hunter', w:0.518, h:1.000,
   pts:[[0.000,0.129],[0.383,0.183],[0.184,0.606],[0.241,0.568],[0.295,0.522],[0.094,1.000],[0.518,0.925],[0.256,0.000]],
   lines:[[0,1],[0,2],[1,4],[2,3],[3,4],[2,5],[4,6],[7,0],[7,1]]},
  {name:'Ursa Major', meaning:'the great bear', w:1.000, h:0.531,
   pts:[[0.989,0.000],[1.000,0.229],[0.686,0.344],[0.556,0.202],[0.323,0.247],[0.142,0.292],[0.000,0.531]],
   lines:[[0,1],[1,2],[2,3],[3,0],[3,4],[4,5],[5,6]]},
  {name:'Cassiopeia', meaning:'the queen', w:1.000, h:0.544,
   pts:[[1.000,0.344],[0.702,0.544],[0.548,0.225],[0.272,0.262],[0.000,0.000]],
   lines:[[0,1],[1,2],[2,3],[3,4]]},
  {name:'Cygnus', meaning:'the swan', w:0.852, h:1.000,
   pts:[[0.054,0.000],[0.271,0.290],[0.852,1.000],[0.000,0.653],[0.691,0.009]],
   lines:[[0,1],[1,2],[1,3],[1,4]]},
  {name:'Lyra', meaning:'the lyre', w:0.732, h:1.000,
   pts:[[0.732,0.000],[0.471,0.193],[0.148,0.309],[0.295,0.889],[0.000,1.000]],
   lines:[[0,1],[0,2],[1,2],[2,4],[4,3],[3,1]]}
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
  /* Shrink to fit if the figure is too big — never stretch it to fill the box.
     The old version used a plain fit-to-bounds ratio, which upscaled any figure
     that merely sat too low, so a 561px constellation was drawn at 776px and
     ended up wider than the canvas. */
  var s=Math.min(1, Math.min((x1-x0)/Math.max(mxx-mnx,1),(y1-y0)/Math.max(mxy-mny,1)));
  var ncx=(x0+x1)/2, ncy=(y0+y1)/2, cxs=(mnx+mxx)/2, cys=(mny+mxy)/2;
  return pts.map(function(p){return {x:ncx+(p.x-cxs)*s, y:ncy+(p.y-cys)*s};});
}
function gwIni(name){ name=String(name||'').toLowerCase().replace(/[^a-z0-9]/g,''); return name.substring(0,2).toUpperCase(); }

/* The sky has depth: the dust drifts a few pixels against the pointer while the
   nodes and their lines stay exactly where the data put them. */
var _skyParallaxBound = false;
function initSkyParallax(){
  if (_skyParallaxBound) return;
  _skyParallaxBound = true;
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  /* Bound on the document: the Command Center re-renders its own markup on
     every poll, so a listener on .starfield-wrap would be left on a detached
     node after the first refresh. */
  var raf = null, tx = 0, ty = 0;
  function apply(){
    raf = null;
    var dust = document.getElementById('dust-layer');
    if (dust) dust.style.transform = 'translate3d(' + (tx * 9).toFixed(1) + 'px,' + (ty * 6).toFixed(1) + 'px,0)';
  }
  document.addEventListener('mousemove', function(e){
    var wrap = e.target && e.target.closest ? e.target.closest('.starfield-wrap') : null;
    if (!wrap) return;
    var r = wrap.getBoundingClientRect();
    tx = ((e.clientX - r.left) / r.width - 0.5) * 2;
    ty = ((e.clientY - r.top) / r.height - 0.5) * 2;
    if (!raf) raf = requestAnimationFrame(apply);
  });
  document.addEventListener('mouseout', function(e){
    var wrap = e.target && e.target.closest ? e.target.closest('.starfield-wrap') : null;
    if (!wrap) return;
    var to = e.relatedTarget;
    if (to && wrap.contains(to)) return;
    var dust = document.getElementById('dust-layer');
    if (dust) dust.style.transform = '';
  });
}

function buildStaticSky(){
  var layer=document.getElementById('dust-layer');
  if(!layer || layer.dataset.built) return;
  layer.dataset.built='1';
  for(var i=0;i<130;i++){
    var d=document.createElement('span');
    d.className='dust'+(Math.random()>0.5?' b':'');
    d.style.left=(Math.random()*100).toFixed(1)+'%'; d.style.top=(Math.random()*100).toFixed(1)+'%';
    /* each grain breathes on its own clock, so the field never pulses in step */
    d.style.animationDuration=(7+Math.random()*9).toFixed(1)+'s';
    d.style.animationDelay='-'+(Math.random()*12).toFixed(1)+'s';
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

function placeGatewayNode(g,x,y,cls,constName){
  var sky=document.getElementById('sky');
  if(!sky) return;
  var n=document.createElement('div');
  var name=g.hostname||g.ip||'gw';
  n.className='gw-node '+(cls||'');
  n.setAttribute('data-name',name); n.setAttribute('data-ip', g.ip||''); n.setAttribute('data-const', constName||''); n.title=name;
  /* the constellation is the fleet map: every node must be reachable and
     announced, not just clickable */
  n.setAttribute('role','button');
  n.setAttribute('tabindex','0');
  n.setAttribute('aria-label', name + ' \u2014 ' + (cls || 'online') + (g.ip ? ' (' + g.ip + ')' : ''));
  n.addEventListener('keydown', function(ev){
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); handleNodeClick(n); }
  });
  n.style.left=x.toFixed(1)+'%'; n.style.top=y.toFixed(1)+'%';
  n.innerHTML='<span class="n-dot"></span><span class="n-ini">'+gwIni(name)+'</span>'+(cls==='overridden'?'<span class="n-ovr">OVR</span>':'');
  sky.appendChild(n);
}

/* Which figure the fleet takes. By default: the smallest constellation that
   can hold every gateway. Pressing C cycles through every figure big enough
   for the fleet, and the choice is remembered — the header map and the big
   sky always show the same one, so changing it is visible everywhere. */
var _fleetFigure = localStorage.getItem('eshu_figure') || '';
var _figureOffset = 0;

function figureChoices(n) {
  return CONSTELLATIONS.filter(function (c) { return c.pts.length >= n; })
    .sort(function (a, b) { return a.pts.length - b.pts.length; });
}

function pickConstellations(n) {
  var choices = figureChoices(n);
  if (_fleetFigure) {
    var chosen = choices.filter(function (c) { return c.name === _fleetFigure; });
    if (chosen.length) return [chosen[0]];
  }
  if (choices.length) return [choices[(_figureOffset + choices.length) % choices.length]];
  /* The fleet is bigger than any single figure (16 gateways, 8 stars max):
     combine the largest ones, starting from a rotating offset so pressing C
     still changes what is drawn. */
  var sorted = CONSTELLATIONS.slice().sort(function (a, b) { return a.pts.length - b.pts.length; });
  var out = [], cap = 0;
  for (var i = 0; i < sorted.length && cap < n; i++) {
    var idx = (sorted.length - 1 - ((_figureOffset + i) % sorted.length) + sorted.length) % sorted.length;
    out.push(sorted[idx]);
    cap += sorted[idx].pts.length;
  }
  return out;
}

function cycleFleetFigure() {
  var n = Math.max(1, (_gatewaysData || []).filter(function (g) { return g.hostname || g.ip; }).length);
  var names = figureChoices(n).map(function (c) { return c.name; });
  /* Advance from what is actually on screen, not from the stored preference:
     "Auto" resolves to the smallest figure, so starting the cycle at "Auto"
     would make the first press appear to do nothing. */
  if (names.length > 1) {
    var cur = pickConstellations(n)[0].name;
    _fleetFigure = names[(names.indexOf(cur) + 1) % names.length];
    localStorage.setItem('eshu_figure', _fleetFigure);
  } else {
    /* no single figure can hold this fleet — cycle the arrangement instead,
       otherwise the key animates and nothing changes */
    _fleetFigure = '';
    localStorage.removeItem('eshu_figure');
    _figureOffset = (_figureOffset + 1) % CONSTELLATIONS.length;
    /* and shift the arrangement, so even a one-figure fleet visibly changes */
    _layout = (_layout + 1) % LAYOUTS;
    sessionStorage.setItem('eshu_layout', _layout);
  }
  _hudSkySig = '';
  renderHudSky();
  if (document.getElementById('sky')) buildStarfield(_layout);
  if (typeof playConstellationTune === 'function') playConstellationTune(pickConstellations(n)[0].name);
}

/* ── The fleet, in every page header ─────────────────────────────────────
   The same figure the Command Center draws at full size, at 150x46, on every
   inner page. It is not decoration: the dots are the gateways (amber online,
   muted offline) laid on the same constellation, so the header tells you the
   shape and health of the fleet from wherever you are. */
var _hudSkySig = '';
function renderHudSky() {
  var hosts = document.querySelectorAll('.hud-sky');
  if (!hosts.length) return;
  var gws = (_gatewaysData || []).filter(function (g) { return g.hostname || g.ip; });
  var chosen = pickConstellations(Math.max(gws.length, 1));
  // cheap signature: this runs on every poll, so skip the DOM work when the
  // fleet has not changed shape or health
  var sig = chosen.map(function (c) { return c.name; }).join('+') + '|' +
    gws.map(function (g) { return g.ip + (gwIsOnline(g) ? '1' : '0'); }).join(',');
  if (sig === _hudSkySig) return;
  _hudSkySig = sig;
  var W = 150, H = 40, PAD = 5;
  var slotW = (W - PAD * 2) / Math.max(chosen.length, 1);
  var svg = '', pts = [];
  chosen.forEach(function (cons, idx) {
    var s = Math.min((slotW * 0.86) / cons.w, (H - PAD * 2) / cons.h);
    var cx = PAD + slotW * (idx + 0.5), cy = H / 2;
    var p = cons.pts.map(function (q) { return [cx + (q[0] - cons.w / 2) * s, cy + (q[1] - cons.h / 2) * s]; });
    cons.lines.forEach(function (l) {
      svg += '<line x1="' + p[l[0]][0].toFixed(1) + '" y1="' + p[l[0]][1].toFixed(1) +
             '" x2="' + p[l[1]][0].toFixed(1) + '" y2="' + p[l[1]][1].toFixed(1) + '"/>';
    });
    p.forEach(function (q) { pts.push({ x: q[0], y: q[1] }); });
  });
  // figure stars
  pts.forEach(function (q) {
    svg += '<circle class="ms" cx="' + q.x.toFixed(1) + '" cy="' + q.y.toFixed(1) + '" r="1"/>';
  });
  // gateways sit on the first N stars
  gws.slice(0, pts.length).forEach(function (g, i) {
    var q = pts[i];
    var online = !g.last_seen || gwIsOnline(g);
    svg += '<circle class="' + (online ? 'mg on' : 'mg off') + '" data-node="' + escapeHtml(g.ip || g.hostname || '') + '"' +
           ' cx="' + q.x.toFixed(1) + '" cy="' + q.y.toFixed(1) + '" r="2"/>';
  });
  hosts.forEach(function (h) {
    h.innerHTML = svg;
    h.setAttribute('aria-label', 'Fleet: ' + chosen.map(function (c) { return c.name + ', ' + c.meaning; }).join(' and ') +
      ' — ' + gws.length + ' gateway' + (gws.length === 1 ? '' : 's'));
    h.setAttribute('title', 'Fleet constellation — click to open Gateways');
  });
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
  var chosen=pickConstellations(N);
  // shuffle only the order of multi-figure sets so layouts vary between visits
  if(chosen.length>1) chosen=shuffleArr(chosen, R);
  _activeConstellations = chosen.map(function(c){ return c.name; });
  /* Work in pixels: the sky is ~2.6:1, so anything expressed as raw percentages
     gets squashed vertically by 2.6 and no longer looks like a constellation. */
  var W=sky.clientWidth||1200, H=sky.clientHeight||520;
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  var pts=[], groups=[], ptConst=[];
  /* One column per figure — overlapping constellations produce a web of
     crossing lines that reads as no constellation at all. */
  var slotW=(W-88)/Math.max(chosen.length,1);
  chosen.forEach(function(cons,idx){
    var s=chosen.length===1
      ? Math.min((W*0.46)/cons.w,(H*0.82)/cons.h)
      : Math.min((slotW*0.72)/cons.w,(H*0.46)/cons.h);
    var hw=cons.w*s/2, hh=cons.h*s/2;
    var cx=44+slotW*(idx+0.5)+(R()-0.5)*slotW*0.08;
    var cy=H*0.48+(R()-0.5)*H*0.10;
    cx=Math.min(Math.max(cx,44+hw),Math.max(44+hw,W-44-hw));
    cy=Math.min(Math.max(cy,44+hh),Math.max(44+hh,H-54-hh));  /* keep the foot of the chart clear of the key line */
    var raw=cons.pts.map(function(p){ return {x:cx+(p[0]-cons.w/2)*s, y:cy+(p[1]-cons.h/2)*s}; });
    var fp=fitSafe(raw, 44, W-44, 44, H-54);
    var base=pts.length;
    fp.forEach(function(p){ pts.push(p); ptConst.push(cons.name); });
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
      var isOnline=gwIsOnline(g);
      var isPending=!!pendingSet[g.hostname||g.ip];
      var isOverridden=(g.override_remaining||0)>0;
      var cls=isOverridden?'overridden':(isPending?'pending':(isOnline?'':(g.updating?'updating':'offline')));
      placeGatewayNode(g, p.x/W*100, p.y/H*100, cls, ptConst[pi]);
      used[pi]=true;
    }
  });
  pts.forEach(function(p,pi){ if(used[pi]) return; var s=document.createElement('span'); s.className='field-star'; s.style.left=(p.x/W*100).toFixed(2)+'%'; s.style.top=(p.y/H*100).toFixed(2)+'%';
    s.style.animationDuration=(6+Math.random()*7).toFixed(1)+'s'; s.style.animationDelay='-'+(Math.random()*9).toFixed(1)+'s';
    sky.appendChild(s); });
}

var _activeConst=null;
/* The constellation is laid out in pixels, so a viewport change needs a redraw
   or the figures stretch. Debounced; cheap (no network, ~20 elements). */
var _skyResizeT=null;
window.addEventListener('resize', function(){
  clearTimeout(_skyResizeT);
  _skyResizeT=setTimeout(function(){
    if(document.getElementById('sky')) buildStarfield(_layout);
  }, 250);
});
function selectConstellation(name){
  if(_activeConst===name) name=null;
  _activeConst=name;
  var legend=document.getElementById('const-legend');
  var svg=document.getElementById('const-lines');
  if(legend) legend.querySelectorAll('.cname').forEach(function(el){ el.classList.toggle('active', el.getAttribute('data-const')===name); });
  if(svg) svg.querySelectorAll('.const-group').forEach(function(g){ g.classList.toggle('active', g.getAttribute('data-const')===name); });
}

var _selGw=null;
function burstNode(node){ if(!node) return; node.classList.remove('burst'); void node.offsetWidth; node.classList.add('burst'); setTimeout(function(){ node.classList.remove('burst'); }, 1900); }
var CONST_TUNES = {
  'Orion': [440, 523.25, 587.33, 659.25, 783.99, 880],
  'Ursa Major': [293.66, 349.23, 392, 440, 523.25, 587.33],
  'Cassiopeia': [329.63, 392, 440, 493.88, 587.33, 659.25],
  'Cygnus': [392, 466.16, 523.25, 587.33, 698.46, 783.99],
  'Lyra': [261.63, 311.13, 349.23, 392, 466.16, 523.25]
};
function playNodeNote(node){
  if(!notifySound || soundMuted) return;
  var tune = CONST_TUNES[node.getAttribute('data-const')] || CONST_TUNES['Orion'];
  try{
    var ctx=getAudioCtx(), now=ctx.currentTime;
    _tone(ctx, tune[0], now, 0.32, 'triangle', 0.12);
    _tone(ctx, tune[1], now+0.09, 0.32, 'triangle', 0.12);
    _tone(ctx, tune[2], now+0.18, 0.4, 'triangle', 0.12);
  }catch(e){}
}
function playConstellationTune(constName){
  if(!notifySound || soundMuted) return;
  var tune = CONST_TUNES[constName] || CONST_TUNES['Orion'];
  try{
    var ctx=getAudioCtx(), now=ctx.currentTime;
    tune.forEach(function(freq, i){
      _tone(ctx, freq, now+i*0.13, 0.3, 'triangle', 0.12);
      _tone(ctx, freq*1.5, now+i*0.13, 0.2, 'sine', 0.04);
    });
  }catch(e){}
}
function playApprovalPulse(){
  if(!notifySound || soundMuted) return;
  try{
    var ctx=getAudioCtx(), now=ctx.currentTime;
    _tone(ctx, 196.00, now, 0.6, 'sine', 0.09);
    _tone(ctx, 293.66, now+0.16, 0.7, 'sine', 0.07);
  }catch(e){}
}
var _nodeClickTimes = {};
function handleNodeClick(node){
  var name = node.getAttribute('data-name') || '';
  var now = Date.now();
  var prev = _nodeClickTimes[name] || 0;
  _nodeClickTimes[name] = now;
  burstNode(node);
  if (now - prev < 350) {
    playConstellationTune(node.getAttribute('data-const'));
  } else {
    playNodeNote(node);
  }
  selectGwNode(node);
}
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
    cycleFleetFigure();
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
  var disconnected = gws.filter(function(g){ return !gwIsOnline(g) && !g.updating; }).length;
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

  var attentionHtml = renderAttention();
  var sessionsHtml = '<div class="cc-sessions-host" id="cc-sessions-host"></div>';

  var headline, sub;
  if (total > 0) {
    headline = 'At the threshold \u00b7 ' + total + ' waiting';
    sub = total + ' command' + (total > 1 ? 's' : '') + ' wait for your word';
  } else {
    headline = 'Eshu \u00b7 The way is open \u00b7 ' + online + '/' + enrolled + ' gateways online';
    /* "all clear" said the same thing twice: the headline already reports the
       fleet, and the dot grid below shows it. Keep only what is not visible
       elsewhere — who is down, and when anything last happened. */
    sub = disconnected > 0 ? disconnected + ' disconnected' : 'every gateway answering';
    if (lastAgo) sub += ' \u00b7 last activity ' + lastAgo;
  }

  return '<div class="cc-empty' + (total === 0 ? ' idle' : '') + '">' +
    '<div class="starfield-wrap">' +
      '<div class="dust-layer" id="dust-layer"></div>' +
      '<div class="sky" id="sky"><svg id="const-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"></svg></div>' +
      '<div class="const-legend" id="const-legend"></div>' +
    '</div>' +
    '<div class="cc-queue' + (total > 0 ? ' show' : '') + '" id="cc-queue"></div>' +
    '<div class="cc-empty-headline">' + headline + '</div>' +
    '<div class="cc-empty-sub">' + sub + '</div>' +
    tickerHtml +
    attentionHtml +
    sessionsHtml +
    '<div class="cc-legend"><span class="k">↑↓</span> move · <span class="k">enter</span> open · <span class="k">O</span> override · <span class="k">B</span> ambience · <span class="k">C</span> constellation · <span class="k">M</span> mute · <span class="k">F</span> focus</div>' +
  '</div>';
}

/* ── The pending queue ──────────────────────────────────────────────────
   One decision in full, the rest of the queue in plain sight beneath it.
   The previous shape was a horizontal carousel: 5 pending requests rendered
   3.4 cards in a 1040px window with the scrollbar hidden, so the 4th and 5th
   were unreachable unless you guessed that a queue scrolls sideways.

   Priority is honest: an anomalous command first, then whichever request
   expires soonest, then the oldest. Same order for the hero and the list. */
var _queueCursor = 0;

function queueModel(item) {
  var d = item.data;
  if (item.type === 'integration') {
    var args = Object.keys(d.payload || {}).map(function(k){ return k + '=' + d.payload[k]; }).join(', ');
    return {
      domId: 'int-' + Number(d.id), ttl: Infinity, createdAt: d.created_at || 0, urgent: false,
      human: d.integration + ' / ' + d.tool,
      cmd: args ? args : '(no arguments)',
      host: 'API call', id: d.id,
      approve: 'approveIntegrationCall(' + Number(d.id) + ')',
      deny: 'denyIntegrationCall(' + Number(d.id) + ')',
      report: !!(d.reason)
    };
  }
  if (item.type === 'window') {
    return {
      domId: 'win-' + Number(d.id), ttl: Infinity, createdAt: d.created_at || 0, urgent: false,
      human: 'Window request', cmd: d.command,
      host: d.target_ip + (d.label ? ' \u00b7 ' + d.label : ''),
      id: d.id,
      approve: 'approveWindowReq(' + Number(d.id) + ')',
      deny: 'denyWindowReq(' + Number(d.id) + ')',
      report: false
    };
  }
  return {
    domId: String(Number(d.id)), ttl: (d.ttl || 0) > 0 ? d.ttl : Infinity,
    createdAt: d.created_at || 0, urgent: !!d.anomaly,
    human: describeCmd(d.command) || 'Command request',
    cmd: d.command,
    bd: d.breakdown,
    host: (d.hostname || d.target_ip || 'unknown host') + (d.hostname && d.target_ip ? ' \u00b7 ' + d.target_ip : ''),
    id: d.id,
    approve: 'handleAction(' + Number(d.id) + ',\'approve\')',
    deny: 'handleAction(' + Number(d.id) + ',\'deny\')',
    report: !!(d.risk || d.anomaly)
  };
}

function queueSort(models) {
  return models.sort(function(a, b) {
    if (a.urgent !== b.urgent) return a.urgent ? -1 : 1;
    if (a.ttl !== b.ttl) return a.ttl - b.ttl;
    return a.createdAt - b.createdAt;
  });
}

function renderQueue(items) {
  var q = document.getElementById('cc-queue');
  if (!q) return;
  var models = queueSort(items.map(queueModel));
  if (!models.length) { q.innerHTML = ''; return; }

  var hero = models[0];
  var rest = models.slice(1);

  /* The hero keeps the full card markup (command block, risk line, TTL,
     Approve/Deny) so it behaves exactly like the tickets it replaced. */
  var heroItem = items.filter(function(it){ return queueModel(it).domId === hero.domId; })[0];
  var heroHtml = '<div class="ccq-hero' + (hero.urgent ? ' urgent' : '') + '" data-queue-id="' + esc(hero.domId) + '">' +
      (models.length > 1 ? '<div class="ccq-hero-label"><span class="ccq-rank">Next</span>' +
        '<span class="ccq-more">' + (models.length - 1) + ' more waiting below</span></div>' : '') +
      renderJitItem(heroItem) +
    '</div>';

  var rowsHtml = rest.map(function(m, i) {
    return '<div class="ccq-row" role="button" tabindex="0" data-queue-id="' + esc(m.domId) + '" ' +
        'onclick="openContextPanel(' + escJs(m.domId) + ')" ' +
        'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openContextPanel(' + escJs(m.domId) + ');}">' +
      '<button type="button" class="ccq-chk ' + ccCheckClass(m.domId) + '" role="checkbox" aria-checked="' + ccCheckAria(m.domId) + '" aria-label="Select request" onclick="event.stopPropagation();toggleBulkSelect(' + escJs(m.domId) + ',event)"></button>' +
      '<span class="ccq-main">' +
        '<span class="ccq-human">' + escapeHtml(m.human) + '</span>' +
        bdRowHeadlineHtml({ breakdown: m.bd }) +
        '<code class="ccq-cmd" title="' + escapeHtml(m.cmd) + '">' + escapeHtml(m.cmd) + '</code>' +
      '</span>' +
      '<span class="ccq-host">' + escapeHtml(m.host) + '</span>' +
      (m.ttl !== Infinity ? '<span class="ccq-ttl"><span class="ttl-countdown" data-ttl="' + m.ttl + '">' + m.ttl + 's</span></span>' : '') +
      '<span class="ccq-acts" onclick="event.stopPropagation()">' +
        '<button class="btn btn-deny btn-xs" onclick="' + esc(m.deny) + '">Deny</button>' +
        '<button class="btn btn-approve btn-xs" onclick="' + esc(m.approve) + '">Approve</button>' +
      '</span>' +
    '</div>';
  }).join('');

  q.innerHTML = heroHtml + (rowsHtml ? '<div class="ccq-rest">' + rowsHtml + '</div>' : '');
  _queueCursor = 0;
  applyQueueCursor();
}

function queueNodes() {
  var q = document.getElementById('cc-queue');
  return q ? Array.prototype.slice.call(q.querySelectorAll('.ccq-hero, .ccq-row')) : [];
}
function applyQueueCursor() {
  queueNodes().forEach(function(el, i) { el.classList.toggle('is-cursor', i === _queueCursor); });
}
function moveQueueCursor(delta) {
  var nodes = queueNodes();
  if (!nodes.length) return;
  _queueCursor = Math.max(0, Math.min(nodes.length - 1, _queueCursor + delta));
  applyQueueCursor();
  if (nodes[_queueCursor].scrollIntoView) nodes[_queueCursor].scrollIntoView({ block: 'nearest' });
}
function openQueueCursor() {
  var nodes = queueNodes();
  var el = nodes[_queueCursor];
  if (!el) return;
  var id = el.getAttribute('data-queue-id') || (el.querySelector('.jit-ticket') || {}).getAttribute && el.querySelector('.jit-ticket').getAttribute('data-id');
  if (id) openContextPanel(id);
}

/* ── Needs attention ─────────────────────────────────────────────────────
   Replaces the "Command rhythm / Today" cards, which restated numbers that
   Statistics already owns and never told you anything you had to act on.
   This panel only appears with something to say: stale gateways, requests
   that have been waiting too long, or a burst of refusals. When there is
   nothing wrong it says so in one quiet line — which is itself the answer
   to "is anything broken right now?". */
function attentionItems() {
  var now = Math.floor(Date.now() / 1000);
  var items = [];
  var gws = (_gatewaysData || []).filter(function (g) { return g.hostname || g.ip; });
  var offline = gws.filter(function (g) { return !gwIsOnline(g) && !g.updating; });
  if (offline.length) {
    var oldest = Math.min.apply(null, offline.map(function (g) { return g.last_seen || now; }));
    items.push({
      tone: offline.length === gws.length ? 'bad' : 'warn',
      text: offline.length + ' of ' + gws.length + ' gateways offline',
      detail: 'oldest ' + formatAgo(now - oldest),
      action: "switchView('gateways')", cta: 'Gateways'
    });
  }
  var stale = (requestsData || []).filter(function (r) {
    return r.status === 'pending' && r.ttl > 0 && (now - (r.created_at || now)) > 600;
  });
  if (stale.length) {
    var wait = Math.max.apply(null, stale.map(function (r) { return now - (r.created_at || now); }));
    items.push({
      tone: 'warn',
      text: stale.length + ' request' + (stale.length === 1 ? '' : 's') + ' waiting over 10 min',
      detail: 'longest ' + formatAgo(wait),
      action: "document.querySelector('.cc-feed-scroll').scrollTop=0", cta: 'Review'
    });
  }
  var refusals = (requestsData || []).filter(function (r) {
    return (r.status === 'blocked' || r.status === 'denied') && (now - (r.created_at || 0)) < 3600;
  });
  if (refusals.length >= 3) {
    items.push({
      tone: 'info',
      text: refusals.length + ' commands refused in the last hour',
      detail: 'policy or operator',
      action: "switchView('history')", cta: 'History'
    });
  }
  return items;
}

function renderAttention() {
  var items = attentionItems();
  var gws = (_gatewaysData || []).filter(function (g) { return g.hostname || g.ip; });
  var online = gws.filter(gwIsOnline).length;
  /* Nothing to do means nothing to show: the constellation above already draws
     one star per gateway with the same information, and a second, smaller copy
     of a fact is decoration. */
  if (!items.length) return '';
  return '<div class="cc-attention">' +
    '<div class="att-head"><span>Needs attention</span><span class="att-count">' + items.length + '</span></div>' +
    items.map(function (it) {
      return '<button class="att-row ' + it.tone + '" onclick="' + it.action + '">' +
        '<span class="att-dot"></span>' +
        '<span class="att-text">' + escapeHtml(it.text) + '</span>' +
        '<span class="att-detail">' + escapeHtml(it.detail) + '</span>' +
        '<span class="att-cta">' + it.cta + ' \u2192</span>' +
      '</button>';
    }).join('') +
  '</div>';
}

// ── Recent Sessions panel (below radar in empty state) ─────────────────
var _recentSessions = [];
var _sessionCoverage = null;
var _sessionsExpanded = false;
var SESSIONS_COLLAPSED = 3;
var _historySession = '';
var _historyInnerTarget = '';

function sessionRow(s, names, now) {
  var meta = names[s.session_id] || {};
  var reqs = s.reqs || 0, mcps = s.mcps || 0;
  var kind = (reqs && mcps) ? 'mixed' : (mcps && !reqs) ? 'mcp' : 'ssh';
  /* A session has a name if the operator gave it one, otherwise the goal the
     agent passed to session_begin(). An id is the last resort. */
  var goal = (s.goal || '').trim();
  var title = meta.name
    ? escapeHtml(meta.name)
    : (goal ? escapeHtml(goal)
            : '<span class="sess-id">#' + escapeHtml(s.session_id.substring(0, 8)) + '</span>');
  var auto = s.source === 'mcp-transport' || String(s.session_id).indexOf('mcp-') === 0;
  var activity = [];
  if (reqs) activity.push(reqs + ' cmd');
  if (mcps) activity.push(mcps + ' mcp');
  var pending = s.pending || 0;
  var sidJs = escJs(s.session_id);
  return '<tr class="sess-row" tabindex="0" role="button" onclick="openSessionModal(' + sidJs + ')" ' +
      'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openSessionModal(' + sidJs + ');}">' +
    '<td class="sess-name">' +
      '<span class="sess-kind ' + kind + '">' + kind + '</span>' +
      (auto ? '<span class="sess-auto" title="The agent did not call session_begin; grouped by its MCP connection">auto</span>' : '') +
      '<span class="sess-title">' + title + '</span>' +
      (s.agent ? '<span class="sess-agent">' + escapeHtml(s.agent) + '</span>' : '') +
      (meta.description ? '<span class="sess-desc">' + escapeHtml(meta.description) + '</span>' : '') +
    '</td>' +
    '<td class="sess-host">' + escapeHtml(s.host || 'mcp') + '</td>' +
    '<td class="sess-act">' + (activity.join(' \u00b7 ') || '\u2014') +
      (pending ? ' <span class="sess-pending">' + pending + ' pending</span>' : '') + '</td>' +
    '<td class="sess-age">' + formatAgo(now - (s.last_seen || now)) + '</td>' +
    '<td class="sess-open">Open \u2192</td>' +
  '</tr>';
}

function renderSessionsTable() {
  var host = document.getElementById('cc-sessions-host');
  if (!host) return;
  var names = _sessionNames || {};
  var now = Math.floor(Date.now() / 1000);
  if (!_recentSessions.length) {
    host.innerHTML = '<div class="cc-sessions"><div class="cc-sessions-head"><span>Recent sessions</span></div>' +
      '<p class="text-muted text-xs" style="padding:10px 2px">No sessions recorded yet.</p></div>';
    return;
  }
  var shown = _sessionsExpanded ? _recentSessions : _recentSessions.slice(0, SESSIONS_COLLAPSED);
  var hidden = _recentSessions.length - shown.length;
  host.innerHTML =
    '<div class="cc-sessions">' +
      '<div class="cc-sessions-head">' +
        '<span>Recent sessions</span>' +
        '<span class="cc-sessions-count">' + _recentSessions.length + ' total</span>' +
      '</div>' +
      '<table class="sess-table"><thead><tr>' +
        '<th>Session</th><th>Host</th><th>Activity</th><th>Last</th><th></th>' +
      '</tr></thead><tbody>' + shown.map(function (s) { return sessionRow(s, names, now); }).join('') + '</tbody></table>' +
      (_recentSessions.length > SESSIONS_COLLAPSED
        ? '<button class="cc-sessions-more" onclick="toggleSessions()">' +
            (_sessionsExpanded ? 'Show fewer' : 'Show all ' + _recentSessions.length) +
          '</button>'
        : '') +
      sessionCoverageLine() +
    '</div>';
}

function sessionCoverageLine() {
  var cov = _sessionCoverage && _sessionCoverage.ssh;
  if (!cov || !cov.total) return '';
  return '<div class="cc-sessions-cov" title="Commands that carried a session id, last 7 days">' +
    cov.pct + '% of commands carried a session \u00b7 ' +
    (cov.total - cov.tagged) + ' untagged</div>';
}

function toggleSessions() {
  _sessionsExpanded = !_sessionsExpanded;
  renderSessionsTable();
}

/* Rebuild the Recent Sessions grid from the merged server summary so it also
   surfaces MCP-only and mixed (SSH + MCP) sessions, with real per-kind counts. */
async function refreshRecentSessions() {
  if (!document.getElementById('cc-sessions-host')) return;
  var summaries = [];
  var hostMap = {};
  (requestsData || []).forEach(function (r) {
    if (r.session_id && !hostMap[r.session_id]) hostMap[r.session_id] = r.hostname || r.target_ip || '';
  });
  try {
    var res = await authFetch('/api/sessions/recent?limit=12');
    var d = await res.json();
    summaries = d.sessions || [];
    _sessionCoverage = d.coverage || null;
  } catch (e) { console.warn('recent sessions fetch failed', e); return; }
  _recentSessions = summaries.map(function (s) {
    if (!s.host || s.host === 'unknown') s.host = hostMap[s.session_id] || '';
    return s;
  });
  renderSessionsTable();
}

function jumpToSession(sid) {
  openSessionModal(sid);
}

function smFmtTime(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function smMcpDetailCard(c) {
  var ok = c.outcome === 'ok';
  var head = (c.integration || '') + (c.tool ? ' / ' + c.tool : '');
  var meta = [];
  if (c.method) meta.push(c.method);
  if (c.path) meta.push(c.path);
  if (c.status_code != null) meta.push('status ' + c.status_code);
  if (c.latency_ms != null) meta.push(c.latency_ms + 'ms');
  if (c.response_bytes) meta.push(c.response_bytes + 'B' + (c.truncated ? ' (truncated)' : ''));
  return '<div class="sm-call ' + (ok ? 'ok' : 'bad') + '">' +
    '<div class="sm-call-top"><span class="mcp-dot"></span><b>' + escapeHtml(head) + '</b>' +
      '<span class="sm-call-kind mcp">MCP</span>' +
      (c.agent ? '<span class="sm-mcp-ag">' + escapeHtml(c.agent) + '</span>' : '') +
      (c.approval ? approvalBadge(c.approval) : '') +
      '<span class="sm-mcp-time">' + escapeHtml(smFmtTime(c.created_at)) + '</span></div>' +
    (meta.length ? '<div class="sm-mcp-meta">' + escapeHtml(meta.join(' \u00b7 ')) + '</div>' : '') +
    (c.execution_id ? '<div class="sm-mcp-meta">exec ' + escapeHtml(c.execution_id) +
      (c.decided_at ? ' \u00b7 decided ' + escapeHtml(smFmtTime(c.decided_at)) : '') + '</div>' : '') +
    (c.reason ? '<div class="sm-call-label">Reason</div><div class="sm-call-reason">' + escapeHtml(c.reason) + '</div>' : '') +
    (c.request_summary ? '<div class="sm-call-label">Request</div><pre class="sm-call-json">' + escapeHtml(c.request_summary) + '</pre>' : '') +
    (c.response_summary ? '<div class="sm-call-label">Response</div><pre class="sm-call-json">' + escapeHtml(c.response_summary) + '</pre>' : '') +
    '</div>';
}

function smSshDetailCard(r) {
  var stBad = ['blocked', 'denied', 'frozen', 'window-rejected'].indexOf(r.status) >= 0;
  var isPending = r.status === 'pending' && r.ttl > 0;
  var host = r.hostname || r.target_ip || '';
  var meta = [];
  if (r.target_ip) meta.push(r.target_ip);
  if (r.status) meta.push(r.status);
  if (isPending) meta.push(r.ttl + 's ttl');
  return '<div class="sm-call ssh ' + (stBad ? 'bad' : (isPending ? 'warn' : 'ok')) + '">' +
    '<div class="sm-call-top"><span class="mcp-dot"></span><b>' + escapeHtml(host || 'Unknown host') + '</b>' +
      '<span class="sm-call-kind ssh">SSH</span>' +
      '<span class="sm-mcp-time">' + escapeHtml(smFmtTime(r.created_at)) + '</span></div>' +
    bdHeadlineHtml(r) +
    '<pre class="sm-call-json cmd">' + escapeHtml(r.command || '') + '</pre>' +
    bdBodyHtml(r, { raw: false }) +
    bdDecodedPreviewHtml(r) +
    (r.risk ? '<div class="sm-call-reason">\u26a0 ' + escapeHtml(r.risk) + '</div>' : '') +
    (r.anomaly ? '<div class="sm-call-reason" style="color:var(--danger)">\u2605 ' + escapeHtml(r.anomaly) + '</div>' : '') +
    (execBadgeHtml(r) || execOutputLink(r)
      ? '<div class="sm-exec">' + execBadgeHtml(r) + execOutputLink(r) + '</div>' : '') +
    (meta.length ? '<div class="sm-mcp-meta">' + escapeHtml(meta.join(' \u00b7 ')) + '</div>' : '') +
    (r.execution_id ? '<div class="sm-mcp-meta">exec ' + escapeHtml(r.execution_id) + '</div>' : '') +
    (r.reason ? '<div class="sm-call-label">Reason</div><div class="sm-call-reason">' + escapeHtml(r.reason) + '</div>' : '') +
    (isPending ? '<div class="sm-ssh-actions">' +
        '<button onclick="event.stopPropagation();handleAction(' + idJs(r.id) + ',\'deny\')" class="btn btn-deny btn-xs">Deny</button>' +
        '<button onclick="event.stopPropagation();handleAction(' + idJs(r.id) + ',\'approve\')" class="btn btn-approve btn-xs">Approve</button>' +
      '</div>' : '') +
    '</div>';
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

/* ── F6: the wrap-up card ────────────────────────────────────────────────
   The number a session is judged by is not how many commands it ran but who
   cleared them: the override share is exactly what F3 plans and F4's lane
   exist to replace. The buckets come from the server's one mapping
   (`core/approval_buckets.py`) so this card and Statistics agree. */
var APPROVAL_BUCKETS = [
  ['allowlist', 'allowlist'], ['human', 'human'], ['plan', 'plan'],
  ['read-lane', 'read lane'], ['session-override', 'session override'],
  ['host-override', 'host override'], ['window', 'window'], ['fleet', 'fleet run']
];

function fmtDuration(seconds) {
  var s = Math.max(0, Math.round(seconds || 0));
  if (s < 60) return s + 's';
  var m = Math.floor(s / 60), h = Math.floor(m / 60);
  if (h) return h + 'h ' + (m % 60) + 'm';
  return m + 'm ' + (s % 60) + 's';
}

function approvalChipHtml(key, label, count) {
  var n = Math.max(0, parseInt(count, 10) || 0);
  return '<span class="appr-chip appr-' + key + (n ? '' : ' zero') + '" title="' +
    escapeHtml(n + ' cleared by ' + label) + '">' + escapeHtml(label) +
    ' <b>' + n + '</b></span>';
}

function sessionSummaryHtml(sum) {
  if (!sum || !sum.commands) return '';
  var buckets = sum.by_approval || {};
  var chips = APPROVAL_BUCKETS.map(function (b) {
    return approvalChipHtml(b[0], b[1], buckets[b[0]]);
  }).join('');
  var writes = sum.writes_by_host || {};
  var writeBits = Object.keys(writes).sort().map(function (h) {
    return escapeHtml(h) + ' <b>' + (parseInt(writes[h], 10) || 0) + '</b>';
  }).join(' \u00b7 ');
  var facts = [];
  // The modal title shows the session's name when it has one, so the goal
  // belongs in the card: it is the one line that says what the session was for.
  if (sum.goal) facts.push('goal &ldquo;' + escapeHtml(sum.goal) + '&rdquo;');
  if (sum.duration_seconds) facts.push(fmtDuration(sum.duration_seconds) + ' long');
  facts.push((parseInt(sum.commands, 10) || 0) + ' commands');
  if (sum.failed) facts.push('<span class="sm-blocked">' + (parseInt(sum.failed, 10) || 0) + ' failed</span>');
  facts.push(sum.decided ? ('waited ' + fmtDuration(sum.waited_seconds) + ' for a human')
                         : 'wait not recorded');
  var html = '<div class="session-summary-facts">' + facts.join(' \u00b7 ') + '</div>' +
    '<div class="session-summary-row"><span class="session-summary-label">how approved</span>' + chips + '</div>';
  if (writeBits) {
    html += '<div class="session-summary-row"><span class="session-summary-label">writes</span>' + writeBits + '</div>';
  }
  return html;
}

function renderSessionSummary(el, sum) {
  if (!el) return;
  var html = sessionSummaryHtml(sum);
  el.innerHTML = html;
  el.classList.toggle('hidden', !html);
}

/* ── F8: test-traffic hygiene ────────────────────────────────────────────
   Excluding a row is a one-way, audited flag: it never deletes anything and
   only the aggregate views stop counting. No rule excludes anything on its
   own; the session hint is a suggestion, and the operator presses the button. */
var TEST_GOAL_RE = /\b(smoke|tests?)\b/i;

function looksLikeTestGoal(goal) {
  return TEST_GOAL_RE.test(String(goal || ''));
}

function excludedMarkHtml(req) {
  if (req && req.excluded) {
    return '<span class="excluded-chip" title="Excluded from statistics, learning gaps and anomaly profiles">excluded</span>';
  }
  return '<button type="button" class="bd-hist-link" ' +
    'title="Exclude this request from statistics and learning" ' +
    'onclick="event.stopPropagation();excludeRequest(' + Number(req.id) + ')">exclude</button>';
}

async function excludeRequests(payload, label) {
  var ok = await customConfirm(
    'Exclude ' + label + ' from statistics, learning gaps and anomaly profiles? ' +
    'The rows stay in History, marked excluded.' +
    (payload && payload.session_id
      ? ' Rows the session adds later are not excluded.' : ''),
    { title: 'Exclude test traffic?', confirmLabel: 'Exclude', danger: false });
  if (!ok) return false;
  try {
    var res = await authFetch('/api/requests/exclude', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload) });
    var data = await res.json();
    if (!res.ok) { showToast(data.detail || 'Could not exclude', 'error'); return false; }
    showToast('Excluded ' + data.excluded + ' request' + (data.excluded === 1 ? '' : 's'), 'success');
    fetchRequests();
    var stats = document.getElementById('view-statistics');
    if (stats && !stats.classList.contains('hidden')) fetchStatistics();
    return true;
  } catch (e) {
    showToast('Could not exclude', 'error');
    return false;
  }
}

function excludeRequest(id) {
  return excludeRequests({ ids: [Number(id)] }, 'this request');
}

function excludeActiveSession() {
  if (!_activeSessionSid) return Promise.resolve(false);
  var sid = _activeSessionSid;
  return excludeRequests({ session_id: sid }, 'this session').then(function (ok) {
    if (!ok) return false;
    var el = document.getElementById('sm-test-hint');
    if (el) el.innerHTML = '<span>This session is excluded from statistics and learning.</span>';
    return true;
  });
}

function renderSessionTestHint(goal, sessionSummary) {
  var el = document.getElementById('sm-test-hint');
  if (!el) return;
  if (sessionSummary && sessionSummary.all_excluded) {
    // S3: already done — do not offer the button again.
    el.classList.remove('hidden');
    el.innerHTML = '<span>This session is excluded from statistics and learning.</span>';
    return;
  }
  if (!looksLikeTestGoal(goal)) {
    el.innerHTML = '';
    el.classList.add('hidden');
    return;
  }
  el.classList.remove('hidden');
  el.innerHTML = '<span>This looks like test traffic (goal: &ldquo;' +
    escapeHtml(goal) + '&rdquo;).</span> ' +
    '<button class="btn btn-muted btn-xs" onclick="excludeActiveSession()">Exclude from stats</button>';
}

/* ── F13: the session conclusion ─────────────────────────────────────────
   The agent's account of what the session meant, sitting under the evidence.
   It is untrusted text: escaped here, never executed, and only a confirmed
   conclusion is served to other agents. The operator confirms, edits or
   rejects it, and may promote a confirmed candidate to a runbook. */
var _conclusionEditing = {};

function conclusionStatusChip(status) {
  var label = {'draft': 'draft · awaiting review', 'confirmed': 'confirmed',
               'rejected': 'rejected'}[status] || status;
  return '<span class="cc-status cc-' + escapeHtml(status || 'draft') + '">' +
    escapeHtml(label) + '</span>';
}

function conclusionRefLabel(ref) {
  var m = String(ref || '').match(/^(req|call):([0-9]+)$/);
  if (!m) return '';
  return m[1] === 'req' ? ('request #' + m[2]) : ('call #' + m[2]);
}

function conclusionItemsHtml(items, cls) {
  var rows = (items || []).map(function (item) {
    var ref = conclusionRefLabel(item.ref);
    var note = escapeHtml(item.note || '');
    if (!ref && !note) return '';
    return '<li>' + (ref ? '<code class="cc-ref">' + escapeHtml(ref) + '</code> ' : '') +
      note + '</li>';
  }).filter(Boolean);
  if (!rows.length) return '';
  return '<div class="cc-sec"><span class="cc-k">' + escapeHtml(cls) + '</span>' +
    '<ul class="cc-list">' + rows.join('') + '</ul></div>';
}

function conclusionHtml(c, runbookId) {
  var editing = !!_conclusionEditing[c.id];
  var body = '';
  if (editing) {
    body += '<textarea class="cc-edit" id="cc-edit-summary-' + Number(c.id) + '" rows="3" ' +
      'placeholder="Summary">' + escapeHtml(c.summary || '') + '</textarea>';
    body += '<textarea class="cc-edit" id="cc-edit-root-' + Number(c.id) + '" rows="2" ' +
      'placeholder="Root cause">' + escapeHtml(c.root_cause || '') + '</textarea>';
    body += '<textarea class="cc-edit" id="cc-edit-fix-' + Number(c.id) + '" rows="2" ' +
      'placeholder="Fix">' + escapeHtml(c.fix || '') + '</textarea>';
  } else {
    body += '<div class="cc-summary">' + escapeHtml(c.summary || '') + '</div>';
    body += conclusionItemsHtml(c.worked, 'worked');
    body += conclusionItemsHtml(c.did_not_work, 'did not work');
    if (c.root_cause) {
      body += '<div class="cc-sec"><span class="cc-k">root cause</span> ' +
        escapeHtml(c.root_cause) + '</div>';
    }
    if (c.fix) {
      body += '<div class="cc-sec"><span class="cc-k">fix</span> ' +
        escapeHtml(c.fix) + '</div>';
    }
    if ((c.follow_ups || []).length) {
      body += '<div class="cc-sec"><span class="cc-k">follow-ups</span> ' +
        c.follow_ups.map(function (v) { return escapeHtml(v); }).join(' · ') + '</div>';
    }
    if ((c.operator_note || '').length) {
      body += '<div class="cc-sec cc-note"><span class="cc-k">operator note</span> ' +
        escapeHtml(c.operator_note) + '</div>';
    }
  }
  var tags = (c.tags || []).map(function (t) {
    return '<span class="cc-tag">' + escapeHtml(t) + '</span>';
  }).join(' ');
  var actions = '';
  if (c.status === 'draft' && editing) {
    actions = '<button class="btn btn-approve btn-xs" onclick="saveConclusionEdit(' +
      Number(c.id) + ')">Save</button>' +
      '<button class="btn btn-muted btn-xs" onclick="cancelConclusionEdit(' +
      Number(c.id) + ')">Cancel</button>';
  } else if (c.status === 'draft') {
    // F13 S3: say what confirming means; the text is served to other agents.
    actions = '<span class="cc-hint">Agents will follow this once confirmed.</span>' +
      '<button class="btn btn-approve btn-xs" onclick="confirmConclusion(' +
      Number(c.id) + ')">Confirm</button>' +
      '<button class="btn btn-muted btn-xs" onclick="startConclusionEdit(' +
      Number(c.id) + ')">Edit</button>' +
      '<button class="btn btn-deny btn-xs" onclick="rejectConclusion(' +
      Number(c.id) + ')">Reject</button>';
  } else if (c.status === 'confirmed') {
    if (runbookId) {
      actions = '<span class="cc-runbook">Runbook #' + Number(runbookId) + '</span>';
    } else if (c.runbook_candidate) {
      actions = '<button class="btn btn-sm" onclick="promoteConclusion(' +
        Number(c.id) + ')">Promote to runbook</button>';
    }
  }
  return '<div class="cc-card' + (c.status === 'confirmed' ? ' cc-confirmed' : '') +
    '" data-od-id="conclusion-card">' +
    '<div class="cc-head"><span class="cc-title">Conclusion</span>' +
    conclusionStatusChip(c.status) +
    '<span class="cc-outcome">' + escapeHtml(c.outcome || '') + '</span>' +
    (c.runbook_candidate && c.status === 'confirmed'
      ? '<span class="cc-candidate">runbook candidate</span>' : '') +
    '</div>' +
    (tags ? '<div class="cc-tags">' + tags + '</div>' : '') +
    body +
    (actions ? '<div class="cc-actions">' + actions + '</div>' : '') +
    '</div>';
}

function renderConclusionBlock(conclusions, runbooks) {
  var el = document.getElementById('sm-conclusion');
  if (!el) return;
  var byConclusion = {};
  (runbooks || []).forEach(function (rb) { byConclusion[rb.conclusion_id] = rb.id; });
  var rows = (conclusions || []).slice().sort(function (a, b) {
    return Number(b.id) - Number(a.id);
  });
  if (!rows.length) {
    el.innerHTML = '<div class="cc-empty">No conclusion yet. The agent can call ' +
      '<code>session_conclude</code> when the task is done.</div>';
    el.classList.remove('hidden');
    return;
  }
  el.innerHTML = rows.map(function (c) {
    return conclusionHtml(c, byConclusion[c.id]);
  }).join('');
  el.classList.remove('hidden');
}

function _conclusionReload() {
  if (!_activeSessionSid) return;
  openSessionModal(_activeSessionSid);
}

async function _conclusionPost(url, body, method) {
  try {
    var res = await authFetch(url, {
      method: method || 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}) });
    var data = await res.json();
    if (!res.ok) {
      showToast('Could not update the conclusion: ' + (data.detail || 'error'), 'error');
      return false;
    }
    return true;
  } catch (e) {
    showToast('Could not update the conclusion', 'error');
    return false;
  }
}

async function confirmConclusion(id) {
  if (await _conclusionPost('/api/conclusions/' + Number(id) + '/confirm', {})) {
    showToast('Conclusion confirmed', 'success');
    _conclusionReload();
  }
}

async function rejectConclusion(id) {
  var ok = await customConfirm(
    'Reject this conclusion? It stays in the session but is never served to agents.',
    { title: 'Reject conclusion', confirmLabel: 'Reject', danger: true });
  if (!ok) return;
  if (await _conclusionPost('/api/conclusions/' + Number(id) + '/reject', {})) {
    showToast('Conclusion rejected', 'info');
    _conclusionReload();
  }
}

function startConclusionEdit(id) {
  _conclusionEditing[id] = true;
  _conclusionReload();
}

function cancelConclusionEdit(id) {
  delete _conclusionEditing[id];
  _conclusionReload();
}

async function saveConclusionEdit(id) {
  var summary = document.getElementById('cc-edit-summary-' + Number(id));
  var root = document.getElementById('cc-edit-root-' + Number(id));
  var fix = document.getElementById('cc-edit-fix-' + Number(id));
  var body = {
    summary: summary ? summary.value : '',
    root_cause: root ? root.value : '',
    fix: fix ? fix.value : '',
  };
  if (await _conclusionPost('/api/conclusions/' + Number(id), body, 'PATCH')) {
    delete _conclusionEditing[id];
    showToast('Conclusion updated', 'success');
    _conclusionReload();
  }
}

async function promoteConclusion(id) {
  var ok = await customConfirm(
    'Promote this confirmed conclusion to a runbook? Steps are copied verbatim ' +
    'from the session evidence; nothing is executed.',
    { title: 'Promote to runbook', confirmLabel: 'Promote', danger: false });
  if (!ok) return;
  try {
    var res = await authFetch('/api/conclusions/' + Number(id) + '/runbook', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}) });
    var data = await res.json();
    if (!res.ok) {
      showToast('Could not promote: ' + (data.detail || 'error'), 'error');
      return;
    }
    showToast('Runbook #' + data.runbook.id + ' created', 'success');
    _conclusionReload();
  } catch (e) {
    showToast('Could not promote the conclusion', 'error');
  }
}

async function openSessionModal(sid) {
  var modal = document.getElementById('session-modal');
  var nameEl = document.getElementById('sm-name');
  var descEl = document.getElementById('sm-desc');
  var metaEl = document.getElementById('sm-meta');
  var cmdsEl = document.getElementById('sm-commands');
  if (!modal || !nameEl || !descEl || !metaEl || !cmdsEl) return;

  // Load saved name/desc
  var names = _sessionNames || {};
  var meta = names[sid] || {};
  var summary = (_recentSessions || []).filter(function (x) { return x.session_id === sid; })[0] || {};
  nameEl.textContent = meta.name || summary.goal || '';
  descEl.textContent = meta.description || '';
  metaEl.innerHTML = '<span class="sm-muted">loading\u2026</span>';
  cmdsEl.innerHTML = '<div class="sm-mcp-none">loading session\u2026</div>';
  modal.classList.remove('hidden');
  _activeSessionSid = sid;
  var idEl = document.getElementById('sm-id');
  if (idEl) idEl.textContent = sid;

  // One-stop detail: SSH commands + proxied API calls (reason, masked args,
  // response summary, approval decision) in a single request.
  var detail = { ssh: [], mcp: [] };
  try {
    var res = await authFetch('/api/sessions/' + encodeURIComponent(sid) + '/detail');
    if (res.ok) detail = await res.json();
  } catch (e) { /* leave empty */ }
  if (_activeSessionSid !== sid) return;  // a different session was opened meanwhile

  var ssh = detail.ssh || [];
  var mcp = detail.mcp || [];

  // Enrich SSH rows with the risk/anomaly hints computed client-side for
  // pending requests (the detail endpoint doesn't recompute them).
  var reqById = {};
  (requestsData || []).forEach(function (r) { reqById[r.id] = r; });
  ssh.forEach(function (r) {
    var lr = reqById[r.id];
    if (lr) { r.risk = lr.risk; r.anomaly = lr.anomaly; r.breakdown = lr.breakdown; }
  });

  var host = ssh.length ? (ssh[0].hostname || ssh[0].target_ip || '') : '';
  _activeSessionHost = host;
  _activeSessionHostIp = ssh.length ? (ssh[0].target_ip || '') : '';
  var pending = ssh.filter(function (r) { return r.status === 'pending' && r.ttl > 0; }).length;
  var approved = mcp.filter(function (c) { return c.approval === 'approved'; }).length;
  var denied = mcp.filter(function (c) { return c.approval === 'denied'; }).length;
  var auto = mcp.filter(function (c) { return c.approval === 'auto'; }).length;
  var bits = [];
  if (summary.source === 'mcp-transport' || String(sid).indexOf('mcp-') === 0) {
    bits.push('<span class="sm-muted" title="No session_begin call; grouped by the MCP connection">auto</span>');
  }
  if (summary.agent) bits.push(escapeHtml(summary.agent));
  if (host) bits.push(escapeHtml(host));
  var sessOv = _activeSessionOverrideFor(sid);
  if (sessOv) {
    bits.push('<span class="sm-pending" title="' + escapeHtml(sessOv.reason || '') + '">session override ' +
      sessionOverrideClock(sessOv) + '</span>' +
      ' <button class="btn btn-muted btn-xs" onclick="cancelSessionOverride(' + idJs(sessOv.id) + ')">Cancel</button>');
  }
  bits.push(ssh.length + ' SSH');
  bits.push(mcp.length + ' MCP');
  if (pending) bits.push('<span class="sm-pending">' + pending + ' pending</span>');
  if (approved) bits.push('<span class="sm-approved">' + approved + ' approved</span>');
  if (denied) bits.push('<span class="sm-blocked">' + denied + ' denied</span>');
  if (auto) bits.push('<span class="sm-muted">' + auto + ' auto</span>');
  metaEl.innerHTML = bits.join(' \u00b7 ');
  renderSessionSummary(document.getElementById('sm-summary'), detail.summary || {});
  renderSessionTestHint(summary.goal || '', detail.summary || {});
  renderConclusionBlock(detail.conclusions || [], detail.runbooks || []);

  // Unified, chronological timeline.
  var items = [];
  ssh.forEach(function (r) { items.push({ ts: r.created_at || 0, html: smSshDetailCard(r) }); });
  mcp.forEach(function (c) { items.push({ ts: c.created_at || 0, html: smMcpDetailCard(c) }); });
  items.sort(function (a, b) { return a.ts - b.ts; });
  cmdsEl.innerHTML = items.length
    ? items.map(function (i) { return i.html; }).join('')
    : '<div class="sm-mcp-none">No activity recorded for this session.</div>';

  // F3: the session's plan (approved or proposed) and per-item uses.
  loadSessionPlanIntoModal(sid);

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

/* The list shows a short id for compactness; the modal is where the exact id
   is read and copied (an adopted chat id can be longer than the 12-hex mint). */
function copySessionId() {
  if (!_activeSessionSid) return;
  copyToClipboard(_activeSessionSid);
}

/* Filter History to one session. The modal is where a session is read, so it
   owns the entry point; the History header carries the active chip. */
function setHistorySession(sid, skipFetch) {
  _historySession = (sid || '').slice(0, 64);
  var chip = document.getElementById('history-session-chip');
  var label = document.getElementById('history-session-label');
  if (chip && label) {
    var meta = (_sessionNames || {})[_historySession] || {};
    var summary = (_recentSessions || []).filter(function (x) {
      return x.session_id === _historySession;
    })[0] || {};
    label.textContent = _historySession
      ? (meta.name || summary.goal || _historySession.substring(0, 12))
      : '';
    chip.classList.toggle('hidden', !_historySession);
  }
  if (_historySession) {
    var box = document.getElementById('request-search');
    if (box && box.value) { box.value = ''; _requestSearchQuery = ''; }
  }
  if (!skipFetch) fetchRequests();
}

function clearHistorySession() { setHistorySession(''); }

/* F7: an inner-target filter, alongside the session filter. Clicking a chip
   on a history row, a pending card or the Statistics list lands here. */
function setHistoryInnerTarget(target, skipFetch) {
  _historyInnerTarget = (target || '').slice(0, 64);
  var chip = document.getElementById('history-inner-chip');
  var label = document.getElementById('history-inner-label');
  if (chip && label) {
    label.textContent = _historyInnerTarget ? innerTargetLabel(_historyInnerTarget) : '';
    chip.classList.toggle('hidden', !_historyInnerTarget);
  }
  if (!skipFetch) fetchRequests();
}

function clearHistoryInnerTarget() { setHistoryInnerTarget(''); }

function filterByInnerTarget(target) {
  if (!target) return;
  setHistoryInnerTarget(target, true);
  switchView('history');
  fetchRequests();
}

function filterHistoryToSession() {
  if (!_activeSessionSid) return;
  var sid = _activeSessionSid;
  closeModal('session-modal');
  /* Set the filter BEFORE switching views: switchView('history') triggers its
     own fetch, and an unfiltered response landing last would replace the table. */
  setHistorySession(sid, true);
  switchView('history');
  fetchRequests();
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

var _noise=null;
function brownNoiseBuffer(ctx){
  var size=ctx.sampleRate*3, buf=ctx.createBuffer(1,size,ctx.sampleRate), d=buf.getChannelData(0), last=0;
  for(var i=0;i<size;i++){ var w=Math.random()*2-1; last=(last+0.02*w)/1.02; d[i]=last*3.5; }
  return buf;
}
var _activeConstellations = [];
var _ambienceIndex = 0;
var _ambienceTimer = null;
function playAmbientTune(constName){
  if(!notifySound || soundMuted) return;
  var tune = CONST_TUNES[constName] || CONST_TUNES['Orion'];
  try{
    var ctx=getAudioCtx(), now=ctx.currentTime;
    tune.forEach(function(freq, i){
      _tone(ctx, freq, now+i*0.32, 0.5, 'sine', 0.05);
      _tone(ctx, freq*1.5, now+i*0.32, 0.35, 'sine', 0.02);
    });
  }catch(e){}
}
function startAmbienceMedley(){
  stopAmbienceMedley();
  var order = [];                       // every figure once, in random order
  for (var i = 0; i < _activeConstellations.length; i++) order.push(_activeConstellations[i]);
  for (var j = order.length - 1; j > 0; j--) {
    var k = Math.floor(Math.random() * (j + 1));
    var tmp = order[j]; order[j] = order[k]; order[k] = tmp;
  }
  if (!order.length) return;
  var step = 0;
  playAmbientTune(order[0]);
  (function next() {
    // 9–17s, so the medley never lands on a metronome
    _ambienceTimer = setTimeout(function () {
      if (!_noise) return;              // ambience stopped while waiting
      step = (step + 1) % order.length;
      playAmbientTune(order[step]);
      next();
    }, 9000 + Math.random() * 8000);
  })();
}

/* Sparse high notes over the bed, like a star catching the light: long decay,
   very quiet, one or two at a time, 7–15s apart. */
var _ambienceShimmer = null;
var _SHIMMER_NOTES = [1046.5, 1174.7, 1318.5, 1568.0, 1760.0, 2093.0, 2349.3];
function scheduleAmbienceShimmer(){
  clearTimeout(_ambienceShimmer);
  _ambienceShimmer = setTimeout(function () {
    if (!_noise) return;
    try {
      var ctx = getAudioCtx(), now = ctx.currentTime;
      var n = 1 + Math.floor(Math.random() * 2);
      for (var i = 0; i < n; i++) {
        var f = _SHIMMER_NOTES[Math.floor(Math.random() * _SHIMMER_NOTES.length)];
        _tone(ctx, f, now + i * 0.22, 2.4, 'sine', 0.014);
        _tone(ctx, f * 2, now + i * 0.22, 1.2, 'sine', 0.004);
      }
    } catch (e) {}
    scheduleAmbienceShimmer();
  }, 7000 + Math.random() * 8000);
}
function stopAmbienceMedley(){
  if(_ambienceTimer){ clearTimeout(_ambienceTimer); _ambienceTimer = null; }
  if(_ambienceShimmer){ clearTimeout(_ambienceShimmer); _ambienceShimmer = null; }
}
function startBrownNoise(){
  var ctx=getAudioCtx(), src=ctx.createBufferSource(); src.buffer=brownNoiseBuffer(ctx); src.loop=true;
  var lp=ctx.createBiquadFilter(); lp.type='lowpass'; lp.frequency.value=1100; lp.Q.value=0.5;
  var g=ctx.createGain(); g.gain.value=0;
  src.connect(lp); lp.connect(g); g.connect(ctx.destination); src.start();
  /* a very slow LFO on the cutoff: the bed breathes instead of sitting still */
  var lfo=ctx.createOscillator(); lfo.type='sine'; lfo.frequency.value=0.04;
  var lfoGain=ctx.createGain(); lfoGain.gain.value=380;
  lfo.connect(lfoGain); lfoGain.connect(lp.frequency); lfo.start();
  g.gain.linearRampToValueAtTime(0.16, ctx.currentTime+2.5);
  _noise={src:src,gain:g,lfo:lfo};
  startAmbienceMedley();
  scheduleAmbienceShimmer();
}
function stopBrownNoise(){
  stopAmbienceMedley();
  if(!_noise) return;
  var g=_noise.gain, t=getAudioCtx().currentTime;
  g.gain.cancelScheduledValues(t); g.gain.setValueAtTime(g.gain.value,t); g.gain.linearRampToValueAtTime(0,t+0.7);
  var s=_noise.src, lfo=_noise.lfo;
  setTimeout(function(){ try{s.stop();}catch(e){} try{ if(lfo) lfo.stop(); }catch(e){} },750);
  _noise=null;
}
function toggleBrownNoise(){ if(_noise){ stopBrownNoise(); showToast('Ambience off', 'success'); } else { startBrownNoise(); showToast('Ambience on', 'success'); } }

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
  document.querySelectorAll('.mcp-star, .int-star').forEach(function(star) {
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

function setSidebarCollapsed(collapsed){
  var sb = document.getElementById('main-sidebar');
  if (!sb) return;
  if (collapsed) { sb.style.width = '0px'; sb.style.minWidth = '0px'; }
  else { sb.style.width = ''; sb.style.minWidth = ''; }
}
/* ── Dialogs: keep Tab inside, restore focus on close ────────────────────
   Every modal is a div toggled by a class, so the trap watches the open state
   rather than hooking each opener. */
var _modalOpener = null;
function _visibleModal() {
  var list = document.querySelectorAll('.modal-overlay:not(.hidden)');
  return list.length ? list[list.length - 1] : null;
}
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Tab') return;
  var m = _visibleModal();
  if (!m) return;
  var f = Array.prototype.filter.call(
    m.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'),
    function (el) { return el.offsetParent !== null; });
  if (!f.length) return;
  var first = f[0], last = f[f.length - 1], active = document.activeElement;
  if (!m.contains(active)) { e.preventDefault(); first.focus(); return; }
  if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
});
(function () {
  /* Observe the whole body: modals are toggled by class from many places, and
     some are created after load, so per-element observers missed them. */
  var obs = new MutationObserver(function (records) {
    var touched = records.some(function (r) {
      return r.type === 'attributes' && r.target.classList && r.target.classList.contains('modal-overlay');
    });
    if (!touched) return;
    var open = _visibleModal();
    if (open) {
      if (!open.hasAttribute('role')) open.setAttribute('role', 'dialog');
      open.setAttribute('aria-modal', 'true');
      if (!open.hasAttribute('aria-label')) {
        var h = open.querySelector('.widget-header, .modal-header, h2, h3');
        if (h) open.setAttribute('aria-label', h.textContent.replace(/[\u00d7\u2715\u2716]/g, '').trim().replace(/\s+/g, ' ').slice(0, 60));
      }
      if (!_modalOpener) {
        _modalOpener = document.activeElement;
        var firstField = open.querySelector('input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled])');
        if (firstField && document.activeElement === document.body) firstField.focus();
      }
    } else if (_modalOpener) {
      var back = _modalOpener;
      _modalOpener = null;
      if (back && back.focus && document.contains(back)) back.focus();
    }
  });
  obs.observe(document.body, { attributes: true, attributeFilter: ['class'], subtree: true });
})();

/* The sky, the queue cursor and the ambience only exist on the Command Center.
   Their keys used to be global, so pressing C on History flew the constellation
   behind the table and O opened an override for a node that was not on screen.
   Focus mode and mute stay global: both are shell controls, visible in the rail
   from every view. */
function onCommandCenter() {
  var home = document.getElementById('view-home');
  return !!home && !home.classList.contains('hidden');
}

document.addEventListener('keydown', function(e) {
  var t = e.target;
  var typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  var onControl = t && (t.tagName === 'BUTTON' || t.tagName === 'A');
  var modalOpen = !!_visibleModal();
  var k = e.key;

  /* Nothing behind the login or set-password screen. */
  if (!document.body.classList.contains('authed')) return;

  if (!typing && !modalOpen) {
    if (k === 'f' || k === 'F') { document.body.classList.toggle('focus'); setSidebarCollapsed(document.body.classList.contains('focus')); return; }
    if (k === 'm' || k === 'M') { toggleMute(); return; }
  }

  if (k === 'Escape') {
    if (_inlineConfirm) return;
    var onbModal = document.getElementById('onboarding-modal');
    if (onbModal && !onbModal.classList.contains('hidden')) { closeOnboarding(); return; }
    closeMobileSidebar();
    var om = document.getElementById('override-modal');
    if (om && !om.classList.contains('hidden')) { om.classList.add('hidden'); }
    else { deselectGateway(); }
    document.body.classList.remove('focus');
    setSidebarCollapsed(false);
    return;
  }

  /* A dialog owns the keyboard: no page shortcut should act behind it. */
  if (typing || onControl || modalOpen || !onCommandCenter()) return;

  if (k === 'c' || k === 'C') nextConstellation();
  else if (k === 'o' || k === 'O') openOverride();
  else if (k === 'b' || k === 'B') toggleBrownNoise();
  else if (k === 'ArrowDown' || k === 'j') { moveQueueCursor(1); e.preventDefault(); }
  else if (k === 'ArrowUp' || k === 'k') { moveQueueCursor(-1); e.preventDefault(); }
  else if (k === 'Enter') openQueueCursor();
  else if (k === 'x' || k === 'X') { e.preventDefault(); toggleQueueRaw(); }
});

/* F2: `X` opens/closes the raw command on the card under the queue cursor. */
function toggleQueueRaw() {
  var nodes = queueNodes();
  var el = nodes[_queueCursor];
  var ticket = el ? el.querySelector('.jit-ticket') : null;
  if (ticket) ticket.classList.toggle('expanded');
}

document.addEventListener('click', function(e) {
  var t = e.target;
  if (!t || !t.closest) return;
  var hit = t.closest('.const-hit');
  if (hit) { var g = hit.closest('.const-group'); if (g) selectConstellation(g.getAttribute('data-const')); return; }
  var nm = t.closest('.cname');
  if (nm) { selectConstellation(nm.getAttribute('data-const')); return; }
  var gn = t.closest('.gw-node');
  if (gn) { handleNodeClick(gn); return; }
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
  initSkyParallax();
  if (total > 0) renderQueue(allItems);
  refreshRecentSessions();
}

/* ── F3: session plans awaiting approval ─────────────────────────────────
   The agent proposes the exact commands it intends to run; the operator
   approves the list once (possibly a subset, with a TTL) and those bytes then
   run without further prompts for that session. Items are rendered with the
   F2 breakdown; every agent-controlled string is escaped. */
var _sessionPlans = [];
var _planRemoved = {};    // plan id -> {item id: true} for unticked items
var _planTtl = {};        // plan id -> chosen TTL, so polling never resets it
var _planDisplayed = {};  // session id -> the plan id currently shown
var _planReplaced = {};   // plan id -> true when it replaced a plan mid-review
var _planConfirm = {};    // plan id -> true after the first (arming) press

function planHostLabel(host) {
  return (!host || host === '*') ? 'any host' : host;
}

async function fetchSessionPlans() {
  try {
    var res = await authFetch('/api/session-plans?status=proposed');
    if (!res.ok) return;
    var plans = await res.json();
    _sessionPlans = Array.isArray(plans) ? plans : [];
    renderPlans();
  } catch (e) { /* the queue keeps working without the plans card */ }
}

function togglePlanItem(el) {
  var planId = String(el.getAttribute('data-plan'));
  var itemId = Number(el.getAttribute('data-item'));
  if (!_planRemoved[planId]) _planRemoved[planId] = {};
  if (el.checked) delete _planRemoved[planId][itemId];
  else _planRemoved[planId][itemId] = true;
  renderPlans();
}

/* R2: the queue polls every 3 s and re-renders the card, so the TTL choice
   lives in state — not in the <select> that is about to be replaced. */
function setPlanTtl(el) {
  var planId = String(el.getAttribute('data-plan'));
  var minutes = parseInt(el.value, 10);
  if (!isNaN(minutes)) _planTtl[planId] = minutes;
}

function planTtlFor(plan) {
  return _planTtl[String(plan && plan.id)] || 60;
}

/* R3/S4: a plan that replaced the one on screen — or that carries a long
   command with an unclassified step — needs a deliberate second press. A plan
   can authorise many more runs than a single request, so it gets the F2 rule
   and the replacement rule on top. */
function planNeedsConfirm(plan) {
  if (!plan) return false;
  if (_planReplaced[String(plan.id)]) return true;
  var items = (plan.items || []).filter(function (item) { return !item.removed; });
  return items.some(function (item) {
    var summary = (item.breakdown || {}).summary || {};
    return (summary.unknown || 0) >= 1 && String(item.command || '').length > 500;
  });
}

function planItemHtml(plan, item) {
  var removed = (_planRemoved[String(plan.id)] || {})[item.id];
  var row = { id: plan.id, command: item.command, breakdown: item.breakdown };
  /* SR4: an item that reaches any host, or that may run more than once, grants
     more than one exact run on one host — make those stand out. */
  var anyHost = !item.host || item.host === '*';
  var multiUse = (Number(item.max_uses) || 1) > 1;
  return '<div class="plan-item' + (removed ? ' plan-item-off' : '') +
      (anyHost || multiUse ? ' plan-item-broad' : '') + '">' +
    '<label class="plan-pick" title="Untick to remove this item before approving">' +
      '<input type="checkbox" data-plan="' + Number(plan.id) + '" data-item="' + Number(item.id) + '"' +
        (removed ? '' : ' checked') + ' onchange="togglePlanItem(this)"></label>' +
    '<div class="plan-item-body">' +
      '<div class="plan-item-head">' +
        '<span class="plan-host' + (anyHost ? ' plan-host-any' : '') + '">' +
          escapeHtml(planHostLabel(item.host)) + '</span>' +
        '<span class="plan-uses' + (multiUse ? ' plan-uses-multi' : '') +
          '" title="How many times this exact command may run">\u00d7' +
          Number(item.max_uses || 1) + '</span>' +
      '</div>' +
      '<code class="plan-cmd">' + escapeHtml(item.command || '') + '</code>' +
      /* P1: the redacted arguments (and the preflight summary when there is
         one) are on the card, so an mcp item is not just a hash. */
      (item.summary
        ? '<div class="plan-note">' + escapeHtml(item.summary) + '</div>' : '') +
      (item.args
        ? '<code class="plan-cmd plan-args">' + escapeHtml(item.args) + '</code>' : '') +
      bdHeadlineHtml(row) +
      bdBodyHtml(row, { raw: false }) +
    '</div></div>';
}

function planCardHtml(plan) {
  var removed = _planRemoved[String(plan.id)] || {};
  var items = (plan.items || []).filter(function (item) { return !item.removed; });
  var kept = items.filter(function (item) { return !removed[item.id]; });
  var chosen = planTtlFor(plan);
  var replaced = !!_planReplaced[String(plan.id)];
  var armed = !!_planConfirm[String(plan.id)];
  var who = plan.agent || 'unknown agent';
  var goal = plan.session_goal || plan.session_id || '';
  return '<div class="plan-card' + (replaced ? ' plan-card-replaced' : '') +
      '" data-plan-card="' + Number(plan.id) + '">' +
    '<div class="plan-head">' +
      '<span class="plan-title">Plan #' + Number(plan.id) + '</span>' +
      '<span class="plan-who" title="Who proposed this plan">' + escapeHtml(who) + '</span>' +
      '<span class="plan-goal" title="Session goal">' + escapeHtml(goal) + '</span>' +
      /* AG1: who wrote the plan. A probe plan covers its commands for an
         approval-mode agent; an agent plan never does. */
      (plan.origin && plan.origin !== 'agent'
        ? '<span class="plan-origin" title="Written by Eshu, not the agent">' +
          escapeHtml(plan.origin) + '</span>'
        : '') +
      '<span class="plan-count">' + kept.length + ' change' + (kept.length === 1 ? '' : 's') +
        ' on ' + Number(plan.hosts || 0) + ' host' + (Number(plan.hosts) === 1 ? '' : 's') + '</span>' +
    '</div>' +
    (replaced
      ? '<div class="plan-warn">This proposal replaced the one you were reviewing. ' +
        'Check the items, then press Approve again.</div>'
      : '') +
    (plan.note ? '<div class="plan-note">' + escapeHtml(plan.note) + '</div>' : '') +
    (plan.strict_sessions ? '' :
      '<div class="plan-hint">Strict sessions are off — anyone with the gateway ' +
      'SSH key who knows this session id can use this plan.</div>') +
    items.map(function (item) { return planItemHtml(plan, item); }).join('') +
    '<div class="plan-actions">' +
      '<label class="plan-ttl">Approve for <select id="plan-ttl-' + Number(plan.id) +
        '" data-plan="' + Number(plan.id) + '" onchange="setPlanTtl(this)">' +
        [30, 60, 120, 240].map(function (minutes) {
          return '<option value="' + minutes + '"' + (minutes === chosen ? ' selected' : '') + '>' +
            (minutes >= 60 ? (minutes / 60) + ' h' : minutes + ' min') + '</option>';
        }).join('') +
      '</select></label>' +
      '<button class="btn btn-deny btn-xs" onclick="decidePlan(' + Number(plan.id) + ',\'deny\')">Deny</button>' +
      '<button class="btn btn-approve btn-xs" onclick="decidePlan(' + Number(plan.id) + ',\'approve\')">' +
        (armed ? 'Press again to approve' : 'Approve plan') + '</button>' +
    '</div></div>';
}

function renderPlans() {
  var widget = document.getElementById('plans-widget');
  var list = document.getElementById('plans-list');
  var count = document.getElementById('plans-count');
  if (!widget || !list) return;
  var plans = _sessionPlans || [];
  if (!plans.length) {
    widget.classList.add('hidden');
    list.innerHTML = '';
    if (count) count.textContent = '';
    _planDisplayed = {};
    return;
  }
  /* R3: if a session's proposal id changed while its card was on screen, the
     operator was looking at a different plan — flag the new one. */
  var nextDisplayed = {};
  plans.forEach(function (plan) {
    var key = String(plan.session_id || '');
    if (_planDisplayed[key] && Number(_planDisplayed[key]) !== Number(plan.id)) {
      _planReplaced[String(plan.id)] = true;
    }
    nextDisplayed[key] = Number(plan.id);
  });
  _planDisplayed = nextDisplayed;
  widget.classList.remove('hidden');
  if (count) count.textContent = plans.length + ' waiting';
  list.innerHTML = plans.map(planCardHtml).join('');
}

async function decidePlan(planId, action) {
  planId = Number(planId);
  var plan = (_sessionPlans || []).filter(function (p) { return Number(p.id) === planId; })[0];
  if (action === 'approve' && !_planConfirm[planId] && planNeedsConfirm(plan)) {
    _planConfirm[planId] = true;
    renderPlans();
    showToast('Check the plan again, then press Approve a second time.', 'info');
    return;
  }
  var init = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
  if (action === 'approve') {
    var removed = Object.keys(_planRemoved[String(planId)] || {}).map(Number);
    init.body = JSON.stringify({
      ttl_minutes: planTtlFor({ id: planId }),
      removed_item_ids: removed,
    });
  } else {
    init.body = '{}';
  }
  try {
    var res = await authFetch('/api/session-plans/' + planId + '/' + action, init);
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      showToast('Plan ' + action + ' failed: ' + (data.detail || 'unknown'), 'error');
      return;
    }
    delete _planRemoved[String(planId)];
    delete _planTtl[String(planId)];
    delete _planConfirm[planId];
    delete _planReplaced[String(planId)];
    showToast(action === 'approve' ? 'Plan approved' : 'Plan denied',
              action === 'approve' ? 'success' : 'info');
    fetchSessionPlans();
    fetchRequests();
    if (_activeSessionSid) loadSessionPlanIntoModal(_activeSessionSid);
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

/* The session modal shows the session's plan with per-item uses. */
async function loadSessionPlanIntoModal(sid) {
  var el = document.getElementById('sm-plan');
  if (!el) return;
  el.classList.add('hidden');
  el.innerHTML = '';
  var plans = [];
  try {
    var res = await authFetch('/api/session-plans?session_id=' + encodeURIComponent(sid));
    if (!res.ok) return;
    plans = await res.json();
  } catch (e) { return; }
  if (_activeSessionSid !== sid) return;
  var nowSec = Math.floor(Date.now() / 1000);
  var plan = (plans || []).filter(function (p) {
    return p.status === 'approved' && Number(p.until) > nowSec;
  })[0] || (plans || []).filter(function (p) { return p.status === 'proposed'; })[0];
  if (!plan) return;
  var items = (plan.items || []).filter(function (item) { return !item.removed; });
  var head = plan.status === 'approved'
    ? 'Plan #' + Number(plan.id) + ' \u00b7 approved until ' + formatTime(Number(plan.until))
    : 'Plan #' + Number(plan.id) + ' \u00b7 awaiting approval';
  el.innerHTML = '<div class="sm-plan-head">' + escapeHtml(head) + '</div>' +
    items.map(function (item) {
      return '<div class="sm-plan-item"><code>' + escapeHtml(item.command || '') + '</code>' +
        '<span class="sm-plan-uses">' + Number(item.uses || 0) + '/' + Number(item.max_uses || 1) +
        '</span></div>';
    }).join('') +
    (plan.status === 'approved'
      ? '<button class="btn btn-muted btn-xs" onclick="decidePlan(' + Number(plan.id) + ',\'end\')">End plan</button>'
      : '');
  el.classList.remove('hidden');
}

function renderJitItem(item) {
  if (item.type === 'integration') {
      var c = item.data;
      var args = Object.keys(c.payload || {}).map(function(k) { return k + '=' + c.payload[k]; }).join(', ');
      var cDom = 'int-' + Number(c.id);
      return '<div class="jit-ticket" data-id="' + esc(cDom) + '" onclick="openContextPanel(' + escJs(cDom) + ')">' +
        '<button type="button" class="' + ccCheckClass(cDom) + '" role="checkbox" aria-checked="' + ccCheckAria(cDom) + '" aria-label="Select request" onclick="event.stopPropagation();toggleBulkSelect(' + escJs(cDom) + ',event)"></button>' +
        '<div style="flex:1;min-width:0">' +
          '<div class="jit-human">' + escapeHtml(c.integration) + ' / ' + escapeHtml(c.tool) + '</div>' +
          '<div class="jit-cmd-text">' + escapeHtml(c.integration) + ' ' + escapeHtml(c.tool) + (args ? '(' + escapeHtml(args) + ')' : '') + '</div>' +
          '<div class="jit-meta">' +
            '<span class="jit-meta-item">#' + esc(String(Number(c.id)).padStart(6,'0')) + '</span>' +
            '<span class="jit-meta-item">API call</span>' +
            (c.summary && (c.tool === 'ha_host_reboot' || c.summary.indexOf('\u26a0') === 0)
              ? '<span class="jit-meta-item" style="color:var(--danger)">' + escapeHtml(c.summary) + '</span>'
              : (c.summary ? '<span class="jit-meta-item">' + escapeHtml(c.summary) + '</span>' : '')) +
            (c.agent ? '<span class="jit-meta-item">agent ' + escapeHtml(c.agent) + '</span>' : '') +
            (c.reason ? '<span class="jit-meta-item">\u201c' + escapeHtml(c.reason) + '\u201d</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="jit-actions">' +
          '<button onclick="event.stopPropagation();denyIntegrationCall(' + idJs(c.id) + ')" class="btn btn-deny btn-xs">Deny</button>' +
          '<button onclick="event.stopPropagation();approveIntegrationCall(' + idJs(c.id) + ')" class="btn btn-approve btn-xs">Approve</button>' +
        '</div>' +
      '</div>';
    } else if (item.type === 'window') {
      var w = item.data;
      var wDom = 'win-' + Number(w.id);
      return '<div class="jit-ticket" data-id="' + esc(wDom) + '" onclick="openContextPanel(' + escJs(wDom) + ')">' +
        '<button type="button" class="' + ccCheckClass(wDom) + '" role="checkbox" aria-checked="' + ccCheckAria(wDom) + '" aria-label="Select request" onclick="event.stopPropagation();toggleBulkSelect(' + escJs(wDom) + ',event)"></button>' +
        '<div style="flex:1;min-width:0">' +
          '<div class="jit-human">Window request</div>' +
          '<div class="jit-cmd-text">' + escapeHtml(w.command) + '</div>' +
          '<div class="jit-meta">' +
            '<span class="jit-meta-item">#' + esc(String(Number(w.id)).padStart(6,'0')) + '</span>' +
            '<span class="jit-meta-item">' + escapeHtml(w.target_ip) + '</span>' +
            (w.label ? '<span class="jit-meta-item">' + escapeHtml(w.label) + '</span>' : '') +
            '<span class="jit-meta-item">' + formatWinSchedule(w) + '</span>' +
          '</div>' +
        '</div>' +
        '<div class="jit-actions">' +
          '<button onclick="event.stopPropagation();denyWindowReq(' + idJs(w.id) + ')" class="btn btn-deny btn-xs">Deny</button>' +
          '<button onclick="event.stopPropagation();approveWindowReq(' + idJs(w.id) + ')" class="btn btn-approve btn-xs">Approve</button>' +
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
      if (r.risk) riskHtml += '<div style="color:var(--status-warning);font-size:11px;margin-top:4px"> ' + escapeHtml(r.risk) + '</div>';
      if (r.anomaly) riskHtml += '<div style="color:var(--danger);font-size:11px;margin-top:4px"> ' + escapeHtml(r.anomaly) + '</div>';
      riskHtml += bdDecodedPreviewHtml(r);
      var rDom = String(Number(r.id));
      return '<div class="jit-ticket' + (r.anomaly ? ' urgent' : '') + '" data-id="' + esc(rDom) + '" onclick="openContextPanel(' + escJs(rDom) + ')">' +
        '<button type="button" class="' + ccCheckClass(rDom) + '" role="checkbox" aria-checked="' + ccCheckAria(rDom) + '" aria-label="Select request" onclick="event.stopPropagation();toggleBulkSelect(' + escJs(rDom) + ',event)"></button>' +
        '<div class="jit-body" style="min-width:0">' +
          '<div class="jit-human">' + escapeHtml(human || 'Command request') + innerTargetChipHtml(r.inner_target) + '</div>' +
          bdHeadlineHtml(r) +
          '<div class="jit-cmd-text">' + escapeHtml(r.command) + '</div>' +
          bdBodyHtml(r) +
          '<div class="jit-meta">' +
            '<span class="jit-meta-item">#' + esc(String(Number(r.id)).padStart(6,'0')) + '</span>' +
            '<span class="jit-meta-item">' + hostCell + '</span>' +
            (r.hostname && r.target_ip && r.target_ip !== r.hostname ? '<span class="jit-meta-item">' + escapeHtml(r.target_ip) + '</span>' : '') +
            '<span class="jit-ttl"><span class="ttl-countdown" data-ttl="' + r.ttl + '">' + r.ttl + 's</span></span>' +
          '</div>' +
          riskHtml +
        '</div>' +
        '<div class="jit-actions">' +
          '<button onclick="event.stopPropagation();handleAction(' + idJs(r.id) + ',\'deny\')" class="btn btn-deny btn-xs">Deny</button>' +
          '<button onclick="event.stopPropagation();handleAction(' + idJs(r.id) + ',\'approve\')" class="btn btn-approve btn-xs">Approve</button>' +
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
      '<button class="jit-session-name-btn" onclick="event.stopPropagation();openSessionModal(' + escJs(s.session_id) + ')">View</button>' +
    '</div>' +
    '<div class="jit-session-body">' + items.map(function(it){ return renderJitItem(it); }).join('') + historyHtml + '</div>' +
  '</div>';
}

function toggleSession(el) {
  var sess = el.closest('.jit-session');
  if (sess) sess.classList.toggle('open');
}
async function approveWindowReq(id) {
  playApprovalPulse();
  try {
    const r = await authFetch('/api/window-requests/' + id + '/approve', { method: 'POST' });
    if (r.status === 401) { await checkAuth(); return; }
    if (!r.ok) throw new Error('Approve failed');
    const data = await r.json();
    if (data.token) copyToClipboard(data.token, true);
    showToast('Window approved — token copied', 'success');
    closePanelIfShowing('win-' + id);
    fetchRequests(); fetchWindowsTable();
  } catch(e) { showToast('' + (e.message || 'Failed to approve window'), 'error'); }
}
async function denyWindowReq(id) {
  playApprovalPulse();
  try {
    const r = await authFetch('/api/window-requests/' + id + '/deny', { method: 'POST' });
    if (r.status === 401) { await checkAuth(); return; }
    if (!r.ok) throw new Error('Deny failed');
    showToast('Window request denied', 'success');
    closePanelIfShowing('win-' + id);
    fetchRequests();
  } catch(e) { showToast('Failed to deny window request', 'error'); }
}

// ── Deny with Blocklist ─────────────────────────────────────────────────
async function handleAction(id, action, opts) {
  playApprovalPulse();
  opts = opts || {};
  var _req = requestsData.find(function (r) { return String(r.id) === String(id); });
  /* F2: a long command that still has an unclassified step asks for a second
     press — the first one scrolls to and highlights that step. Short and
     all-read commands keep the single click. Callers that have already made
     the decision for the operator (a session override) pass
     {confirmed: true}; bulk approve never does. */
  if (action === 'approve' && !opts.confirmed && _req && bdNeedsConfirm(_req)
      && !_bdUnknownConfirmed[id]) {
    _bdUnknownConfirmed[id] = true;
    bdHighlightFirstUnknown(id);
    armSidebarConfirm(id);
    showToast('Read the highlighted unknown step first. Approve again to approve it.', 'info');
    return;
  }
  if (action === 'deny') {
    const res = await authFetch('/api/deny/' + id, { method: 'POST' });
    if (res.status === 401) { await checkAuth(); return; }
    let denyCount = 0, denyCmd = '';
    try { const data = await res.json(); denyCount = data.deny_count || 0; denyCmd = data.command || ''; } catch(e) {}
    if (!res.ok) {
      showToast('Could not deny request #' + id + ': ' + (denyCount || 'not pending'), 'error');
      fetchRequests();
      return;
    }
    const req = requestsData.find(r => r.id === id);
    const cmd = denyCmd || (req ? req.command : '');
    if (cmd && denyCount === DENY_BLOCKLIST_PROMPT_THRESHOLD) {
      lastDeniedCmd = cmd;
      document.getElementById('deny-bar-text').textContent = 'Command denied: "' + cmd + '" — Add to blocklist?';
      document.getElementById('deny-blacklist-bar').classList.remove('hidden');
    }
    closePanelIfShowing(id);
    fetchRequests().then(function() { if (_activeSessionSid) openSessionModal(_activeSessionSid); });
  } else {
    const res = await authFetch('/api/' + action + '/' + id, { method: 'POST' });
    if (res.status === 401) { await checkAuth(); return; }
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).detail || ''; } catch(e) {}
      showToast('Could not ' + action + ' request #' + id + ': ' + (detail || res.status), 'error');
      fetchRequests();
      return;
    }
    closePanelIfShowing(id);
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
/* One escaper. This used to be a second, identity implementation whose
   replacement strings had been decoded in place — every agent-supplied
   command, hostname and label was inserted into innerHTML unescaped. */
function escapeHtml(str) { return esc(str); }
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
  Object.values(boxMap).forEach(function(id) {
    var el = document.getElementById(id);
    el.classList.remove('active-filter');
    el.setAttribute('aria-pressed', 'false');
  });
  if (activeFilter === status) { activeFilter = null; document.getElementById('filter-badge').classList.add('hidden'); }
  else { activeFilter = status; document.getElementById(boxMap[status]).classList.add('active-filter'); document.getElementById(boxMap[status]).setAttribute('aria-pressed', 'true'); document.getElementById('filter-badge').classList.remove('hidden'); document.getElementById('filter-badge').textContent = 'Filtering: ' + status; }
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

/* ── F5: execution results ───────────────────────────────────────────────
   The gateway posts the exit code, duration and the sanitized output tail.
   The badge is shown on history rows and in the session modal; the output
   itself opens on demand (the list payload never carries 16 KiB per row). */

function execDuration(ms) {
  ms = Number(ms) || 0;
  return ms < 1000 ? ms + ' ms' : (ms / 1000).toFixed(1) + ' s';
}

function execBadgeHtml(req) {
  if (!req || req.exit_code === null || req.exit_code === undefined) return '';
  var code = Number(req.exit_code);
  return '<span class="exec-badge ' + (code === 0 ? 'exec-ok' : 'exec-fail') +
    '" title="Exit code ' + escapeHtml(String(code)) + ' after ' +
    execDuration(req.duration_ms) + '">exit ' + code + '</span>' +
    '<span class="exec-dur">' + escapeHtml(execDuration(req.duration_ms)) + '</span>';
}

function execOutputLink(req) {
  if (!req || req.exit_code === null || req.exit_code === undefined) return '';
  return '<button type="button" class="bd-hist-link" title="Show the captured output tail" ' +
    'onclick="event.stopPropagation();showExecOutput(' + Number(req.id) + ')">output</button>';
}

function execOutputHtml(data) {
  return escapeHtml(String((data && data.output) || '(no output)')) +
    (data && data.truncated ? '\n\n\u2026 (tail of the last 16 KiB)' : '');
}

async function showExecOutput(id) {
  try {
    var res = await authFetch('/api/requests/' + Number(id) + '/output');
    if (!res.ok) { showToast('No output stored for this request', 'info'); return; }
    var data = await res.json();
    var meta = 'exit ' + Number(data.exit_code) + ' \u00b7 ' + execDuration(data.duration_ms);
    if (data.truncated) meta += ' \u00b7 the tail of the last 16 KiB is kept';
    document.getElementById('exec-output-meta').textContent = meta;
    document.getElementById('exec-output-body').innerHTML = execOutputHtml(data);
    document.getElementById('exec-output-modal').classList.remove('hidden');
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

function closeExecOutput() {
  document.getElementById('exec-output-modal').classList.add('hidden');
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
      badge = '<span class="badge badge-' + (req.status==='pending'?'pending':'approved') + '"><span class="ttl-countdown font-mono w-8 text-center" data-ttl="' + esc(req.ttl) + '">' + esc(req.ttl) + 's</span> ' + escapeHtml(req.status) + '</span>';
    } else if (isPP && isExpired) badge = '<span class="badge badge-expired">Expired</span>';
    else if (req.status === 'consumed') badge = '<span class="badge badge-consumed">Ticket Claimed</span>';
    else if (req.status === 'blocked') badge = '<span class="badge badge-blocked">Blocked</span>';
    else if (req.status === 'session-required') badge = '<span class="badge badge-blocked" title="No minted Session id/signature">Session required</span>';
    else if (req.status === 'denied') badge = '<span class="badge badge-denied">Denied</span>';
    else if (req.status === 'auto-approved') badge = '<span class="badge badge-auto">Auto-Approved</span>';
    else if (req.status === 'window-approved') badge = '<span class="badge badge-window">Window</span>';
    else if (req.status === 'window-rejected') badge = '<span class="badge badge-window-rejected" title="' + escapeHtml(req.reason || '') + '">Window Rejected</span>';
    else if (req.status === 'frozen') badge = '<span class="badge badge-blocked" title="Rejected while the fleet was frozen">Blocked</span>';
    else if (req.status === 'fleet-run') badge = '<span class="badge badge-approved" title="Dispatched via Fleet Run">Approved</span>';
    let actions = '<span class="text-muted">—</span>';
    if (req.status === 'pending' && !isExpired) {
      actions = '<button onclick="handleAction(' + idJs(req.id) + ', \'approve\')" class="btn btn-approve btn-xs mr-1">Approve</button>' +
        '<button onclick="handleAction(' + idJs(req.id) + ', \'deny\')" class="btn btn-deny btn-xs">Deny</button>';
      if (req.session_id && req.session_id !== 'unknown') {
        actions += ' <button onclick="event.stopPropagation();openSessionOverrideModal(' +
          escJs(req.session_id) + ',' + escJs(req.target_ip || '') + ',' + Number(req.id) + ')" ' +
          'class="btn btn-muted btn-xs" title="Auto-approve this session on this host for a bounded window">' +
          'Override session</button>';
      }
    } else if (req.status === 'frozen') {
      actions = '<span class="chip chip-actions chip-frozen" title="Blocked by Emergency Freeze — the fleet is rejecting all SSH commands until unfrozen.">' +
        'Frozen</span>';
    } else if (req.status === 'fleet-run') {
      actions = '<span class="chip chip-actions chip-fleet-run" title="Executed via Fleet Run — see the Fleet Run tab for per-gateway output.">' +
        'Fleet Run</span>';
    } else if (req.reason === 'override' || String(req.reason || '').indexOf('session-override:') === 0) {
      var _isSessOv = String(req.reason || '').indexOf('session-override:') === 0;
      actions = '<span class="chip chip-actions chip-override" title="' +
        (_isSessOv
          ? 'Auto-approved via a session override — only this session, on the granted host'
          : 'Auto-approved via Override Mode — every JIT is auto-approved while active') +
        '">' + (_isSessOv ? 'Session override' : 'Override') + '</span>';
    } else if (req.status === 'session-required') {
      actions = '<span class="chip chip-actions" title="The gateway refused this command because no minted Session id/signature was supplied">' +
        'Session required</span>';
    } else if (req.status === 'blocked' && isHardcoreBlocked(req.command)) {
      actions = '<span class="chip chip-actions chip-block-core" title="Blocked by a shipped Eshu safety pattern — manage in Controls → Blocklist">' +
        'Block by Core</span>';
    } else {
      const mem = fetchPolicyMembership(req.command);
      const inAnyAllowlist = mem.inExact || mem.inRegexWhite;
      const disabledStyle = 'opacity:0.5;pointer-events:none;';

      actions = '<select onchange="handlePolicyAction(this,' + escJs(encodeURIComponent(req.command)) + ')" class="btn-muted select-actions">' +
        '<option value="" disabled selected>Actions</option>' +
        '<option value="exact_whitelist"' + (mem.inExact ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inExact ? '✓ ' : '+ ') + 'AL Exact</option>' +
        '<option value="regex_whitelist"' + (mem.inRegexWhite ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inRegexWhite ? '✓ ' : '+ ') + 'AL Regex</option>' +
        '<option value="regex_blacklist"' + (mem.inBlacklist ? ' disabled style="'+disabledStyle+'"' : '') + '>' + (mem.inBlacklist ? '✓ ' : '+ ') + 'Add to Blocklist</option>' +
        (inAnyAllowlist ? '<option value="regex_whitelist_remove">Remove from Allowlist</option>' : '') +
        (mem.inBlacklist ? '<option value="regex_blacklist_remove">Remove from Blocklist</option>' : '') +
      '</select>';
    }
    const gap = gapMap.get(req.id);
    const rowClass = (gap ? 'gap-row ' : '') + (req.excluded ? 'excluded-row' : '');
    const idClass = gap ? 'cell-id-warn' : 'cell-id';
    const gapTitle = gap ? ' title="' + escapeHtml(formatGapTooltip(gap)) + '"' : '';
    const idDisplay = gap ? ' #' + esc(String(Number(req.id)).padStart(6, '0')) : '#' + esc(String(Number(req.id)).padStart(6, '0'));
    const escapedCmd = escapeHtml(req.command);
    const decodedChip = req.encoded
      ? '<span class="decoded-badge" title="Encoded payload — open for the decoded command">decoded</span>'
      : '';
    /* F2: history rows do not carry a breakdown (pending-only field), so this
       fetches /api/requests/{id}/breakdown on demand and expands a row. */
    const breakdownLink = '<button type="button" class="bd-hist-link" title="Show the step breakdown" ' +
      'onclick="event.stopPropagation();toggleHistoryBreakdown(' + Number(req.id) + ',this)">breakdown</button>';
    const innerChip = innerTargetChipHtml(req.inner_target);
    const readLaneChip = req.reason === 'read-lane-shadow'
      ? '<span class="read-lane-chip" title="Eligible for the read-only lane in shadow — the operator still decided">would auto-run</span>'
      : '';
    const gwPillHtml = gwPill(req.hostname || 'N/A');
    const _sid = req.session_id || '';
    const sCell = (_sid && _sid !== 'unknown')
      ? '<button class="c-session" data-sid="' + escapeHtml(_sid) + '" title="Session ' + escapeHtml(_sid) + '">' + escapeHtml(((_sessionNames || {})[_sid] || {}).name || _sid.substring(0, 8)) + '</button>'
      : '<span class="text-muted">—</span>';
    const gatewayCell = gwPillHtml + ' ' + escapeHtml(req.hostname || 'N/A') + ' (' + escapeHtml(req.target_ip) + ')';
    /* Two different facts used to share the glyph "!", so the only way to tell
       a risk flag from "first time this host runs X" was to hover it. */
    const riskHtml = (req.status === 'pending' && req.risk) ?
      '<span class="flex-shrink-0 risk-flag" role="img" aria-label="Risk: ' + escapeHtml(req.risk) + '" title="Risk: ' + escapeHtml(req.risk) + '">!</span>' : '';
    const anomalyHtml = (req.status === 'pending' && req.anomaly) ?
      '<span class="flex-shrink-0 anomaly-flag" role="img" aria-label="New: ' + escapeHtml(req.anomaly) + '" title="New behaviour: ' + escapeHtml(req.anomaly) + '">new</span>' : '';
    html += '<tr class="' + rowClass + '">' +
      '<td class="' + idClass + '"' + gapTitle + '>' + idDisplay + '</td>' +
      '<td class="text-muted text-xs">' + formatTime(req.created_at) + '</td>' +
      '<td>' + gatewayCell + '</td>' +
      '<td class="cell-cmd"><div class="flex items-center gap-1">' + riskHtml + anomalyHtml + '<code class="cmd-code" title="' + escapedCmd + '">' + escapedCmd + '</code><button class="js-copy-cmd flex-shrink-0 text-xs opacity-30 hover:opacity-80 px-1 py-0.5 rounded transition-opacity text-muted" data-cmd="' + encodeURIComponent(req.command) + '" title="Copy"></button>' + decodedChip + innerChip + execBadgeHtml(req) + execOutputLink(req) + breakdownLink + excludedMarkHtml(req) + '</div>' +
        (function() {
          var _desc = describeCmd(req.command);
          if (!_desc) return '';
          var _maxLen = 120;
          if (_desc.length > _maxLen) {
            var _short = _desc.substring(0, _maxLen) + '...';
            return '<div class="cmd-desc">' +
              '<span id="desc-short-' + idText(req.id) + '">' + escapeHtml(_short) + ' <a href="#" onclick="toggleDesc(' + Number(req.id) + ');return false;" class="text-info">More</a></span>' +
              '<span id="desc-full-' + idText(req.id) + '" style="display:none;">' +
                '<a href="#" onclick="toggleDesc(' + Number(req.id) + ');return false;" class="text-muted">Less</a> ' + escapeHtml(_desc) +
              '</span>' +
              '</div>';
          }
          return '<div class="cmd-desc">' + escapeHtml(_desc) + '</div>';
        })() +
        '</td>' +
      '<td>' + badge + readLaneChip + '</td>' +
      '<td>' + sCell + '</td>' +
      '<td class="text-right">' + actions + '</td></tr>' +
      (_bdHistory[req.id] ? bdHistoryRowHtml(req.id) : '');
  });
  tbody.innerHTML = html;
  restoreExpandedDescs();
}

/* F2: fetch one history row's breakdown and show it beneath the row. The
   endpoint is session-authenticated; the result is escaped by the same
   renderer the cards use. `_bdHistory[id]` holds the cell HTML (or the
   'loading' sentinel) so the expanded row is rebuilt on every poll instead of
   being wiped by the 3s table refresh. */
var _bdHistoryLoading = '<span class="bd-loading">Loading breakdown\u2026</span>';

function bdHistoryRowHtml(id) {
  var html = _bdHistory[id];
  if (!html) return '';
  return '<tr id="bd-hist-' + Number(id) + '" class="bd-history-row"><td colspan="7">' +
    '<div class="bd-history-cell">' + html + '</div></td></tr>';
}

function bdRefreshHistoryRow(id) {
  var cell = document.querySelector('#bd-hist-' + Number(id) + ' .bd-history-cell');
  if (cell) cell.innerHTML = _bdHistory[id] || '';
}

function toggleHistoryBreakdown(id, btn) {
  id = Number(id);
  if (_bdHistory[id]) {
    delete _bdHistory[id];
    var open = document.getElementById('bd-hist-' + id);
    if (open && open.parentNode) open.parentNode.removeChild(open);
    return;
  }
  var tr = btn && btn.closest ? btn.closest('tr') : null;
  if (!tr) return;
  _bdHistory[id] = _bdHistoryLoading;
  tr.insertAdjacentHTML('afterend', bdHistoryRowHtml(id));
  authFetch('/api/requests/' + encodeURIComponent(id) + '/breakdown').then(function (res) {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }).then(function (bd) {
    _bdHistory[id] = bdBlockHtml({ id: id, command: '', breakdown: bd }, { raw: false });
    bdRefreshHistoryRow(id);
  }).catch(function () {
    _bdHistory[id] = 'Breakdown unavailable.';
    bdRefreshHistoryRow(id);
  });
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
    if (ttl > 0) { ttl--; el.setAttribute('data-ttl', ttl); el.innerText = ttl + 's'; }
    else fetchRequests();
    // red is for the last stretch, not for the whole life of the request
    el.classList.toggle('urgent-ttl', ttl > 0 && ttl <= 15);
  });
  checkOverrideBanner();
  tickGatewayTableCountdowns();
  renderSessionOverrideBanner();
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

async function dismissPolicyGap(btn, encodedCmd) {
  var cmd = decodeURIComponent(encodedCmd);
  if (!(await inlineConfirm(btn, { question: 'Dismiss this gap?', label: 'Dismiss', danger: false }))) return;
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
          '<button class="chip-btn" onclick="dismissPolicyGap(this, \'' + encodeCmd(c.command) + '\')">Dismiss</button>' +
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
    if (!(await customConfirm('"' + cmd + '" will be removed from both allowlists.', { title: 'Remove from the policy?', confirmLabel: 'Remove' }))) return;
    const exTextarea = document.getElementById('policy-exact'), rwTextarea = document.getElementById('policy-regex-white');
    exTextarea.value = exTextarea.value.split('\n').filter(l => l.trim() !== cmd && l !== cmd).join('\n');
    rwTextarea.value = rwTextarea.value.split('\n').filter(l => l.trim() !== '^' + cmd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$').join('\n');
    await savePoliciesSilent(); await refreshPolicyCache();
    const gwRes = await fetch('/api/gateways'); const gws = await gwRes.json();
    showToast('Removed from Allowlist & pushed to ' + gws.length + ' gateway(s)', 'success');
    fetchPolicies(); fetchRequests(); return;
  }
  if (policyType === 'regex_blacklist_remove') {
    if (!(await customConfirm('"' + cmd + '" will be removed from the blocklist.', { title: 'Remove from the blocklist?', confirmLabel: 'Remove', danger: false }))) return;
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
  if (!(await customConfirm('"' + cmd + '" will be allowed by the ' + labels[policyType] + '.', { title: 'Add to the policy?', confirmLabel: 'Add', danger: false }))) return;
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
var _sessionOverrides = []; // rows from /api/session-overrides (active first)
var _sessionOverrideCtx = null; // {session_id, host, hostname} for the modal
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

/* Encoded wrappers (base64/eval) are forced to JIT; show the decoded payload
   beside the wrapper so the operator can read what will actually run. */
function decodedCmdHtml(r) {
  if (!r || !r.encoded) return '';
  if (!r.decoded_command) {
    return '<div class="decoded-wrap"><span class="decoded-badge">encoded</span> ' +
      '<span class="text-xs text-muted">decoded preview unavailable</span></div>';
  }
  var label = r.decoded_truncated ? 'decoded \u00b7 truncated' : 'decoded';
  return '<div class="decoded-wrap"><span class="decoded-badge">' + label + '</span>' +
    '<pre class="decoded-cmd">' + escapeHtml(r.decoded_command) + '</pre></div>';
}

/* ── F2: reviewable approval card ─────────────────────────────────────────
   Pending requests carry `breakdown` (F1): steps tagged read/write/unknown,
   section markers, wrapper context, flags and an optional decoded `script`.
   These helpers turn it into the headline, the step list and the flag chips.

   They are pure: a request row in, an HTML string out. Every agent-controlled
   string goes through escapeHtml — step text, section names, context chips,
   the script body, the flags and the server headline. The tests in
   tests/test_f2_review_card.py load this file and prove it by spying on
   escapeHtml. */

var _bdUnknownConfirmed = {};
/* History breakdowns stay open across the 3s table re-render: id -> HTML. */
var _bdHistory = {};

var BD_FLAGS = {
  'encoded': ['encoded', 'encoded payload — the decoded text is the real command'],
  'heredoc': ['heredoc', 'heredoc input'],
  'nested-ssh': ['nested ssh', 'runs a command on another host over ssh'],
  'sensitive-path': ['sensitive path', 'touches a secret path'],
  'redirect-to-file': ['redirect', 'writes a file through redirection'],
  'follows': ['runs until stopped', 'streams and never ends on its own'],
  'repo-config': ['repo can run programs', 'git may run repository-configured programs']
};

function bdHasScript(r) {
  var bd = r && r.breakdown;
  if (!bd || !bd.steps) return false;
  for (var i = 0; i < bd.steps.length; i++) {
    if (bd.steps[i] && bd.steps[i].script) return true;
  }
  return false;
}

/* N1: when the step already carries the decoded script, the older DECODED
   block would repeat it on the same card. */
function bdDecodedPreviewHtml(r) {
  if (bdHasScript(r)) return '';
  return decodedCmdHtml(r);
}

/* The queue re-renders on every poll, so a selection that outlives a poll
   (bulk approve keeps the ones that need review) must be drawn from state. */
function ccCheckClass(id) {
  return _ccSelectedIds.has(String(id)) ? 'jit-check checked' : 'jit-check';
}

function ccCheckAria(id) {
  return _ccSelectedIds.has(String(id)) ? 'true' : 'false';
}

function bdUsable(r) {
  var bd = r && r.breakdown;
  return !!(bd && bd.steps && bd.steps.length);
}

function bdTone(bd) {
  var s = (bd && bd.summary) || {};
  if ((s.unknown || 0) > 0) return 'unknown';
  if ((s.write || 0) > 0) return 'write';
  return 'read';
}

function bdHeadlineHtml(r) {
  if (!bdUsable(r)) return '';
  var bd = r.breakdown;
  var tone = bdTone(bd);
  var html = '<div class="bd-head bd-' + tone + '" data-bd-head="1">' +
    '<span class="bd-headline">' + escapeHtml(bd.headline || '') + '</span>';
  if (tone === 'unknown') html += '<span class="bd-advice bd-advice-unknown">read it as written</span>';
  if (bd.complete === false) {
    html += '<span class="bd-advice bd-advice-incomplete">could not fully parse; read the raw command</span>';
  }
  return html + '</div>';
}

function bdFlagsHtml(r) {
  if (!bdUsable(r)) return '';
  var flags = r.breakdown.flags || [];
  if (!flags.length) return '';
  var chips = flags.map(function (flag) {
    var meta = BD_FLAGS[flag];
    var label = meta ? meta[0] : flag;
    var title = meta ? meta[1] : flag;
    return '<span class="bd-flag bd-flag-' + escapeHtml(flag) + '" title="' + escapeHtml(title) + '">' +
      escapeHtml(label) + '</span>';
  }).join('');
  return '<div class="bd-flags">' + chips + '</div>';
}

function bdStepHtml(step, reqId, isFirstUnknown) {
  var kind = (step.kind === 'write' || step.kind === 'unknown') ? step.kind : 'read';
  var chips = (step.context || []).map(function (item) {
    return '<span class="bd-ctx">' + escapeHtml(item) + '</span>';
  }).join('');
  var html = '<div class="bd-step bd-' + kind + '"' +
    (isFirstUnknown ? ' data-bd-unknown-for="' + escapeHtml(reqId) + '"' : '') + '>' +
    '<span class="bd-kind">' + escapeHtml(kind) + '</span>' +
    (chips ? '<span class="bd-ctxs">' + chips + '</span>' : '') +
    '<code class="bd-text">' + escapeHtml(step.text || '') + '</code>';
  /* A read step's `why` is usually just the verb ("uptime"), which repeats the
     text; only show it when it adds something ("journalctl (no vacuum/rotate)"). */
  var why = step.why || '';
  var firstWord = String(step.text || '').split(/\s+/)[0];
  if (why && (kind !== 'read' || why !== firstWord)) {
    html += '<span class="bd-why">' + escapeHtml(why) + '</span>';
  }
  if (step.script) {
    html += '<details class="bd-script"><summary>decoded script</summary>' +
      '<pre class="bd-script-body">' + escapeHtml(step.script) + '</pre></details>';
  }
  return html + '</div>';
}

function bdStepsHtml(r) {
  if (!bdUsable(r)) return '';
  var steps = r.breakdown.steps || [];
  var firstUnknown = -1;
  for (var u = 0; u < steps.length; u++) {
    if (steps[u].kind === 'unknown') { firstUnknown = u; break; }
  }
  var out = [];
  var currentSection = null;
  var i = 0;
  while (i < steps.length) {
    var section = steps[i].section || '';
    if (section !== currentSection) {
      if (currentSection !== null) out.push('</div>');
      out.push('<div class="bd-group">');
      if (section) out.push('<div class="bd-section">' + escapeHtml(section) + '</div>');
      currentSection = section;
    }
    if (steps[i].kind === 'read') {
      var j = i;
      while (j < steps.length && steps[j].kind === 'read'
             && (steps[j].section || '') === currentSection) j++;
      if (j - i >= 2) {
        var run = [];
        for (var k = i; k < j; k++) run.push(bdStepHtml(steps[k], r.id, false));
        var count = String(j - i);
        out.push('<div class="bd-run">' +
          '<button type="button" class="bd-run-btn" data-bd-count="' + escapeHtml(count) + '" ' +
            'onclick="event.stopPropagation();bdToggleRun(this)">' +
            escapeHtml(count) + ' read steps ▸</button>' +
          '<div class="bd-run-body hidden">' + run.join('') + '</div></div>');
      } else {
        out.push(bdStepHtml(steps[i], r.id, false));
      }
      i = j;
      continue;
    }
    out.push(bdStepHtml(steps[i], r.id, i === firstUnknown));
    i++;
  }
  if (currentSection !== null) out.push('</div>');
  return out.join('');
}

function bdBodyHtml(r, opts) {
  if (!bdUsable(r)) return '';
  opts = opts || {};
  var html = '<div class="bd-block">';
  if (opts.raw !== false && (r.breakdown.steps || []).length >= 4) {
    html += '<div class="bd-tools"><button type="button" class="bd-raw-btn" ' +
      'onclick="event.stopPropagation();bdToggleRaw(this)" ' +
      'title="Show the raw command (X)">raw command ▸</button></div>';
  }
  html += bdFlagsHtml(r) + bdStepsHtml(r);
  return html + '</div>';
}

function bdBlockHtml(r, opts) {
  return bdHeadlineHtml(r) + bdBodyHtml(r, opts);
}

/* Compact one-line headline for the queue rows (the hero card carries the
   full step list). */
function bdRowHeadlineHtml(r) {
  if (!bdUsable(r)) return '';
  return '<span class="ccq-bd bd-' + bdTone(r.breakdown) + '">' +
    escapeHtml(r.breakdown.headline || '') + '</span>';
}

function bdToggleRun(btn) {
  var body = btn.parentNode ? btn.parentNode.querySelector('.bd-run-body') : null;
  if (!body) return;
  var collapsed = body.classList.toggle('hidden');
  var count = btn.getAttribute('data-bd-count') || '';
  btn.innerHTML = escapeHtml(count) + (collapsed ? ' read steps ▸' : ' read steps ▾');
}

function bdToggleRaw(el) {
  var ticket = el && el.closest ? el.closest('.jit-ticket') : null;
  if (!ticket) return;
  var open = ticket.classList.toggle('expanded');
  el.classList.toggle('is-open', open);
  el.innerHTML = escapeHtml(open ? 'hide raw command \u25be' : 'raw command \u25b8');
}

function bdHighlightFirstUnknown(id) {
  var selector = '[data-bd-unknown-for="' + esc(String(id)) + '"]';
  var nodes = document.querySelectorAll ? document.querySelectorAll(selector) : [];
  var el = nodes && nodes.length ? nodes[0] : null;
  if (!el) return false;
  if (el.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  el.classList.add('bd-flash');
  setTimeout(function () { el.classList.remove('bd-flash'); }, 1800);
  return true;
}

/* The two-press Approve: only for long commands that still contain a step we
   could not classify. Short or all-read commands approve on the first press. */
function bdNeedsConfirm(req) {
  if (!req || !req.breakdown) return false;
  var summary = req.breakdown.summary || {};
  return (summary.unknown || 0) >= 1 && String(req.command || '').length > 500;
}

/* ── F7: inner target attribution ─────────────────────────────────────────
   A command wrapped in pct/qm/docker runs inside a container or VM; the row
   records that target (`pct:103`, `qm:201`, `docker:sonarr`, or `multi`).
   These helpers render the chip and the host-override warning. The chip text
   is agent-adjacent data, so it goes through escapeHtml like everything else. */

var INNER_KIND_LABEL = { pct: 'pct', qm: 'qm', docker: 'docker' };

function innerTargetLabel(value) {
  if (!value) return '';
  if (value === 'multi') return 'multi';
  var idx = value.indexOf(':');
  if (idx <= 0) return value;
  var kind = value.substring(0, idx);
  return (INNER_KIND_LABEL[kind] || kind) + ' ' + value.substring(idx + 1);
}

function innerTargetChipHtml(value) {
  if (!value || typeof value !== 'string') return '';
  if (value === 'multi') {
    return '<span class="inner-chip inner-chip-multi" title="This command reaches several containers/VMs">multi</span>';
  }
  var idx = value.indexOf(':');
  if (idx <= 0 || idx === value.length - 1) return '';
  var kind = value.substring(0, idx);
  var label = innerTargetLabel(value);
  return '<button type="button" class="inner-chip inner-chip-' + escapeHtml(kind) + '" ' +
    'title="Runs inside ' + escapeHtml(label) + ' \u2014 click to filter history" ' +
    'onclick="event.stopPropagation();filterByInnerTarget(' + escJs(value) + ')">' +
    escapeHtml(label) + '</button>';
}

/* The host-override dialog warning: an override on a Proxmox host also covers
   every container/VM reached through pct/qm. Warning only. */
function overrideInnerWarningHtml(data) {
  if (!data) return '';
  var total = ((data.pct || []).length) + ((data.qm || []).length);
  if (!total) return '';
  var one = total === 1;
  var text = 'This host reaches ' + total + ' container' + (one ? '' : 's') +
    '/VM' + (one ? '' : 's') + ' via pct/qm. An override here also covers ' +
    'commands run inside ' + (one ? 'it' : 'them') + '.';
  return '<div class="override-inner-warning" role="status">' + escapeHtml(text) + '</div>';
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

/* 24 hourly buckets for one gateway, as an inline bar strip. */
var _gwHourly = {};
function gwSparkline(ip) {
  var buckets = _gwHourly[ip];
  if (!buckets || !buckets.length) return '<span class="gw-spark-empty">\u2014</span>';
  var max = Math.max.apply(null, buckets.concat([1]));
  var total = buckets.reduce(function (a, b) { return a + b; }, 0);
  if (!total) return '<span class="gw-spark-empty" title="no commands in 24h">\u2014</span>';
  var bars = buckets.map(function (v) {
    var h = Math.max(3, Math.round(v / max * 100));
    return '<span class="gws-b" style="height:' + h + '%" title="' + v + ' command' + (v === 1 ? '' : 's') + '"></span>';
  }).join('');
  return '<span class="gw-spark-bars" title="' + total + ' commands in the last 24h">' + bars + '</span>';
}

async function fetchGatewayHourly() {
  try {
    var res = await authFetch('/api/statistics?days=1&extended=1');
    if (!res.ok) return;
    var d = await res.json();
    _gwHourly = d.per_gateway_hourly || {};
    if (document.getElementById('gateways-table-body')) fetchGateways();
  } catch (e) {}
}

async function fetchGateways() {
  const res = await fetch('/api/gateways'); const data = await res.json();
  _gatewaysData = data;
  updateHudSummaries();
  renderHudSky();
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
    const diff = now - g.last_seen, isOnline = gwIsOnline(g);
    const updating = !!g.updating;
    const connDot = isOnline
      ? '<span class="conn-dot on" title="Connected"></span>'
      : (updating ? '<span class="conn-dot on" title="Updating"></span>' : '<span class="conn-dot off" title="Disconnected"></span>');
    const statusCell = connDot + (isOnline
      ? '<span class="text-success text-xs">Connected</span>'
      : (updating ? '<span class="text-warning text-xs">Updating\u2026</span>'
                  : '<span class="text-muted text-xs">Offline \u00b7 ' + formatLastSeen(diff) + '</span>'));
    const pua = g.policy_updated_at || 0;
    const syncCell = isOnline
      ? (pua > 0 ? '<span class="text-muted text-xs">✓ synced ' + formatAgo(now - pua) + '</span>' : '<span class="text-muted text-xs">—</span>')
      : (updating ? '<span class="text-muted text-xs">updating…</span>' : '<span class="text-muted text-xs">unknown</span>');
    /* Health used to be four identical dots distinguished only by colour.
       Filled vs hollow reads without colour, and each dot carries a label. */
    const hbDot = function(ok, title) {
      const state = ok ? 'ok' : 'fail';
      return '<span class="hb-dot ' + state + '" role="img" aria-label="' + title + ': ' + (ok ? 'ok' : 'failed') + '" title="' + title + ': ' + (ok ? 'ok' : 'failed') + '">' + (ok ? '●' : '○') + '</span>';
    };
    const hbBits = [
      'poller ' + (Number(g.heartbeat_poller_ok) ? 'ok' : 'failed'),
      'gateway ' + (Number(g.heartbeat_gateway_ok) ? 'ok' : 'failed'),
      'reachability ' + (Number(g.heartbeat_can_reach) ? 'ok' : 'failed'),
      'token ' + (g.has_token ? 'ok' : 'missing')
    ].join(', ');
    const spark = gwSparkline(g.ip);
    const healthCell = g.last_heartbeat > 0
      ? '<span class="hb-status" aria-label="Health: ' + hbBits + '">' +
          hbDot(Number(g.heartbeat_poller_ok), 'Poller') +
          hbDot(Number(g.heartbeat_gateway_ok), 'Gateway') +
          hbDot(Number(g.heartbeat_can_reach), 'Reachable') +
          hbDot(g.has_token ? 1 : 0, g.has_token ? 'Token valid' : 'No API token — JIT delivery broken. Re-enroll this gateway.') +
          '<span class="hb-age">' + formatAgo(now - g.last_heartbeat) + '</span>' +
        '</span>'
      : '<span class="hb-status">waiting…</span>';
    var ztBadge = g.zero_trust
      ? '<span class="zt-badge" title="Zero-Trust — every command requires JIT approval"> ZT</span>'
      : '';
    var readMode = g.read_lane || 'off';
    // `on` stays locked until the operator sets the meta key by hand after the
    // security sign-off; the 7-day shadow rule applies after that.
    var onLocked = !g.read_lane_on_enabled;
    var readSelect = '<select class="capture-select" title="Read-only lane (shadow first)" ' +
      'onchange="setReadLane(' + escJs(g.ip) + ',this.value)" aria-label="Read-only lane">' +
      [['off', 'RL off'], ['shadow', 'RL shadow'],
       ['on', onLocked ? 'RL on: locked until security sign-off' : 'RL on']].map(function (pair) {
        var locked = pair[0] === 'on' && (onLocked || !g.read_lane_ready);
        return '<option value="' + pair[0] + '"' + (readMode === pair[0] ? ' selected' : '') +
          (locked ? ' disabled title="' +
            (onLocked ? 'Locked until the security sign-off (set read_lane_on_enabled by hand)'
                      : 'Needs 7 days in shadow first') + '"' : '') + '>' +
          pair[1] + '</option>';
      }).join('') + '</select>';
    var capMode = g.capture_output || 'exit-only';
    var capSelect = '<select class="capture-select" title="Output capture for this gateway" ' +
      'onchange="setCaptureMode(' + escJs(g.ip) + ',this.value)" aria-label="Output capture">' +
      [['off', 'off'], ['exit-only', 'exit'], ['tail', 'tail']].map(function (pair) {
        return '<option value="' + pair[0] + '"' + (capMode === pair[0] ? ' selected' : '') + '>' +
          pair[1] + '</option>';
      }).join('') + '</select>';
    var ztBtn = g.zero_trust
      ? '<button onclick="toggleZeroTrust(' + escJs(g.ip) + ', false)" class="chip-btn zt-on" title="Zero-Trust ON — allowlisted commands go through JIT. Click to disable."> ZT</button>'
      : '<button onclick="toggleZeroTrust(' + escJs(g.ip) + ', true)" class="chip-btn" title="Enable Zero-Trust — allowlisted commands must go through JIT">ZT</button>';
    var overrideCell;
    if (_uninstallingIps[g.ip]) {
      overrideCell = '<span class="text-warning text-xs"> Uninstalling…</span>';
    } else {
      var overrideControl;
      if ((g.override_remaining || 0) > 0) {
        overrideControl = '<button class="chip-btn ovr-on" data-ovr-ip="' + esc(g.ip) + '" title="Override active — click to cancel">OVR 00:00</button>' +
          '<button onclick="cancelOverride(' + escJs(g.ip) + ')" class="chip-btn danger">Cancel</button>';
      } else {
        overrideControl = '<button onclick="openOverrideModal(' + escJs(g.ip) + ',' + escJs(g.hostname) + ')" class="chip-btn ovr" title="Override Mode — auto-approve all JIT for a set duration">Override</button>';
      }
      overrideCell = '<div class="flex items-center gap-2">' + overrideControl + ztBtn + capSelect + readSelect + '</div>';
    }
    return '<tr>' +
      '<td class="px-4 py-2 whitespace-nowrap">' + statusCell + '</td>' +
      '<td class="px-4 py-2 whitespace-nowrap">' + gwPill(g.hostname) + ' ' + escapeHtml(g.hostname) + devBadge(g) + ztBadge + '</td>' +
      '<td class="px-4 py-2 whitespace-nowrap text-muted">' + escapeHtml(g.ip) + '</td>' +
      '<td class="px-4 py-2 whitespace-nowrap text-muted">' + (g.first_seen ? formatAgo(now - g.first_seen) : '—') + '</td>' +
      '<td class="px-4 py-2">' + syncCell + '</td>' +
      '<td class="px-4 py-2">' + healthCell + '</td>' +
      '<td class="px-4 py-2 gw-spark-cell">' + spark + '</td>' +
      '<td class="px-4 py-2">' + overrideCell + '</td>' +
      '<td class="px-4 py-2 text-right whitespace-nowrap"><button onclick="openHostChanges(' + escJs(g.ip) + ', ' + escJs(g.hostname) + ')" class="chip-btn" title="What Eshu changed on this host, with its Pulse alerts">Changes</button> ' +
        '<button onclick="openRemoveGatewayModal(' + escJs(g.ip) + ', ' + escJs(g.hostname) + ', ' + isOnline + ')" class="btn btn-deny btn-xs"> Remove</button></td>' +
      '</tr>';
  }).join('');
  checkOverrideBanner();
}

async function setReadLane(ip, mode) {
  try {
    var res = await authFetch('/api/gateways/' + encodeURIComponent(ip) + '/read-lane', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode })
    });
    var data = await res.json();
    if (res.ok && data.status === 'ok') {
      showToast('Read-only lane: ' + data.read_lane + ' on ' + ip, 'success');
      fetchGateways();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function setCaptureMode(ip, mode) {
  try {
    var res = await authFetch('/api/gateways/' + encodeURIComponent(ip) + '/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode })
    });
    var data = await res.json();
    if (res.ok && data.status === 'ok') {
      showToast('Output capture: ' + data.capture_output + ' on ' + ip, 'success');
      fetchGateways();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
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
      el.textContent = 'OVR ' + mins + ':' + secs;
    } else {
      el.textContent = 'OVR';
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
  /* F7: warn when this host reaches containers/VMs through pct/qm — the
     override covers those guests too. Warning only, never a block. */
  var warn = document.getElementById('override-modal-inner-warning');
  if (warn) { warn.innerHTML = ''; warn.classList.add('hidden'); }
  if (ip) {
    authFetch('/api/gateways/' + encodeURIComponent(ip) + '/inner-targets?days=30')
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        var modal = document.getElementById('override-modal');
        if (!modal || modal.dataset.ip !== ip) return;  // reopened for another host
        var el = document.getElementById('override-modal-inner-warning');
        if (!el) return;
        el.innerHTML = overrideInnerWarningHtml(data);
        el.classList.toggle('hidden', !el.innerHTML);
      })
      .catch(function () {});
  }
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
  if (!(await customConfirm('JIT requests on this gateway will require manual approval again.', { title: 'Cancel override mode?', confirmLabel: 'Cancel override' }))) return;
  try {
    var res = await authFetch('/api/gateways/' + encodeURIComponent(ip) + '/override', { method: 'DELETE' });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Override cancelled for ' + ip, 'success');
      fetchGateways();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── F23: the change journal for one host ────────────────────────────────
var _hostChangesIp = '';

function openHostChanges(ip, hostname) {
  _hostChangesIp = ip;
  var title = document.getElementById('host-changes-title');
  if (title) title.textContent = 'Changes on ' + (hostname || '') + ' (' + ip + ')';
  var sel = document.getElementById('host-changes-days');
  if (sel) sel.value = '7';
  var body = document.getElementById('host-changes-body');
  if (body) body.innerHTML = '<span class="text-muted">Loading…</span>';
  var note = document.getElementById('host-changes-alert-note');
  if (note) { note.textContent = ''; note.classList.add('hidden'); }
  document.getElementById('host-changes-modal').classList.remove('hidden');
  fetchHostChanges();
}

function closeHostChanges() {
  _hostChangesIp = '';
  document.getElementById('host-changes-modal').classList.add('hidden');
}

function reloadHostChanges() { fetchHostChanges(); }

async function fetchHostChanges() {
  var ip = _hostChangesIp;
  if (!ip) return;
  var sel = document.getElementById('host-changes-days');
  var days = sel ? sel.value : '7';
  var body = document.getElementById('host-changes-body');
  try {
    var res = await authFetch('/api/hosts/' + encodeURIComponent(ip) + '/changes?days=' + encodeURIComponent(days));
    if (!res.ok) throw new Error('HTTP ' + res.status);
    var data = await res.json();
    if (_hostChangesIp !== ip) return;      // reopened for another host
    renderHostChanges(data);
  } catch (e) {
    if (body) body.innerHTML = '<span class="text-warning">Could not load the journal: ' + esc(e.message) + '</span>';
  }
}

function hostChangeWhen(ts) {
  if (!ts) return '';
  var d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
         d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function hostChangeRow(at, mark, main, detail, full) {
  var html = '<div class="host-change-row">' +
    '<span class="host-change-at">' + esc(at) + '</span>' +
    '<span class="host-change-mark ' + esc(mark.cls) + '">' + esc(mark.text) + '</span>' +
    '<span class="host-change-main">' + main +
      (detail ? '<div class="host-change-detail">' + detail + '</div>' : '') +
      (full ? '<details class="host-change-full"><summary>full text</summary><pre>' + esc(full) + '</pre></details>' : '') +
    '</span></div>';
  return html;
}

function renderHostChanges(data) {
  var body = document.getElementById('host-changes-body');
  if (!body) return;
  var counts = document.getElementById('host-changes-count');
  if (counts) {
    counts.textContent = data.counts.changes + ' change' + (data.counts.changes === 1 ? '' : 's') +
      ' · ' + data.counts.alerts + ' alert' + (data.counts.alerts === 1 ? '' : 's') +
      (data.counts.ties ? ' · ' + data.counts.ties + ' tied' : '');
  }
  var note = document.getElementById('host-changes-alert-note');
  if (note) {
    note.textContent = data.alerts_error || '';
    note.classList.toggle('hidden', !data.alerts_error);
  }
  var rows = [];
  (data.changes || []).forEach(function(c) {
    var mark = { cls: c.kind, text: c.kind === 'ssh' ? 'CHG' : (c.kind === 'audit' ? 'EVT' : 'INT') };
    var main, detail;
    if (c.kind === 'ssh') {
      main = '<strong>#' + esc(c.request_id) + '</strong> <code>' + esc(c.command) + '</code>';
      var bits = [];
      if (c.inner_target) bits.push('guest ' + esc(c.inner_target));
      bits.push('exit ' + (c.exit_code == null ? '—' : esc(c.exit_code)));
      if (c.agent) bits.push('agent ' + esc(c.agent));
      if (c.session_id) bits.push('session ' + esc(String(c.session_id).substring(0, 8)));
      if (c.session_goal) bits.push(esc(c.session_goal));
      if (c.excluded) bits.push('test traffic');
      detail = bits.join(' · ');
    } else if (c.kind === 'audit') {
      main = '<strong>' + esc(c.label) + '</strong> ' + esc(c.command);
      detail = c.agent ? 'by ' + esc(c.agent) : '';
    } else {
      main = '<strong>' + esc(c.integration) + '</strong> ' + esc(c.method) + ' <code>' + esc(c.command) + '</code>';
      detail = 'tool ' + esc(c.tool) + (c.agent ? ' · agent ' + esc(c.agent) : '');
    }
    var full = (c.command_full && c.command_full !== c.command) ? c.command_full : '';
    rows.push({ at: c.at, html: hostChangeRow(hostChangeWhen(c.at), mark, main, detail, full) });
  });
  (data.alerts || []).forEach(function(a) {
    var main = '<strong>' + esc(a.level || 'alert') + '</strong> ' + esc(a.message);
    var bits = [];
    if (a.resource) bits.push(esc(a.resource));
    if (a.acknowledged) bits.push('acknowledged');
    if (a.tie) {
      bits.push('<span class="host-change-tie">' + esc(a.tie.text) + '</span>' +
                (a.tie.others ? ' <span class="text-muted">and ' + esc(a.tie.others) + ' earlier</span>' : ''));
    }
    rows.push({ at: a.at, html: hostChangeRow(hostChangeWhen(a.at),
      { cls: 'alert', text: 'ALERT' }, main, bits.join(' · '), '') });
  });
  rows.sort(function(x, y) { return y.at - x.at; });
  body.innerHTML = rows.length
    ? rows.map(function(r) { return r.html; }).join('')
    : '<div class="host-changes-empty">No changes or alerts in this range.</div>';
}

// ── Session Override (narrower than the per-gateway one) ─────────────────
function sessionLabel(sid) {
  var meta = (_sessionNames || {})[sid] || {};
  return meta.name || sid.substring(0, 8);
}

function sessionOverrideClock(ov) {
  var rem = Math.max(0, (ov._clientUntil || 0) - Math.floor(Date.now() / 1000));
  var mins = String(Math.floor(rem / 60)).padStart(2, '0');
  var secs = String(rem % 60).padStart(2, '0');
  return mins + ':' + secs;
}

function _activeSessionOverrideFor(sid) {
  var now = Math.floor(Date.now() / 1000);
  var matches = (_sessionOverrides || []).filter(function(o) {
    return o.active !== false && o.session_id === sid && o._clientUntil > now;
  });
  if (!matches.length) return null;
  // Exact-host grants beat any-host, mirroring the server's resolution.
  matches.sort(function(a, b) { return (a.target_ip ? 0 : 1) - (b.target_ip ? 0 : 1); });
  return matches[0];
}

async function refreshSessionOverrides() {
  if (!document.getElementById('session-override-banner')) return;
  try {
    var res = await authFetch('/api/session-overrides');
    if (!res.ok) return;
    var data = await res.json();
    var now = Math.floor(Date.now() / 1000);
    var skew = (data.now || now) - now;
    _sessionOverrides = (data.overrides || []).map(function(o) {
      o._clientUntil = now + (o.remaining || 0) + skew;
      return o;
    });
    renderSessionOverrideBanner();
  } catch (e) { /* keep the last known state */ }
}

function renderSessionOverrideBanner() {
  var banner = document.getElementById('session-override-banner');
  var text = document.getElementById('session-override-banner-text');
  if (!banner || !text) return;
  var now = Math.floor(Date.now() / 1000);
  var active = (_sessionOverrides || []).filter(function(o) {
    return o.active !== false && o._clientUntil > now;
  });
  if (!active.length) {
    banner.classList.add('hidden');
    text.innerHTML = '';
    return;
  }
  banner.classList.remove('hidden');
  text.innerHTML = 'Session Override \u2014 ' + active.map(function(o) {
    var scope = o.target_ip ? o.target_ip : 'any host';
    return '<span class="session-ovr-item">' + escapeHtml(sessionLabel(o.session_id)) +
      ' \u00b7 ' + escapeHtml(scope) + ' (' + sessionOverrideClock(o) + ')' +
      ' <button class="btn btn-xs btn-on-banner" onclick="cancelSessionOverride(' + idJs(o.id) + ')">Cancel</button></span>';
  }).join(' <span class="session-ovr-sep">\u00b7</span> ');
}

function openSessionOverrideModal(sid, ip, reqId) {
  sid = sid || _activeSessionSid;
  if (!sid) return;
  if (ip === undefined) ip = _activeSessionHostIp;
  ip = ip || '';
  var hostname = '';
  var gw = (_gatewaysData || []).find(function(g) { return g.ip === ip; });
  if (gw) hostname = gw.hostname;
  _sessionOverrideCtx = { session_id: sid, host: ip, hostname: hostname,
                          request_id: reqId || null };
  document.getElementById('session-override-modal-session').textContent =
    sessionLabel(sid) + ' (' + sid + ')';
  document.getElementById('session-override-any-host').checked = !ip;
  document.getElementById('session-override-reason').value = '';
  document.getElementById('session-override-minutes').value = '30';
  syncSessionOverrideHost();
  document.getElementById('session-override-modal').classList.remove('hidden');
  document.getElementById('session-override-reason').focus();
}

function syncSessionOverrideHost() {
  var any = document.getElementById('session-override-any-host').checked;
  var ctx = _sessionOverrideCtx || {};
  var el = document.getElementById('session-override-modal-host');
  if (any) { el.textContent = 'Any host in this session'; return; }
  el.textContent = ctx.hostname
    ? ctx.hostname + ' (' + ctx.host + ')'
    : (ctx.host || 'No host recorded — tick "Any host"');
}

function closeSessionOverrideModal() {
  document.getElementById('session-override-modal').classList.add('hidden');
}

async function confirmSessionOverride() {
  var ctx = _sessionOverrideCtx;
  if (!ctx) return;
  var reason = document.getElementById('session-override-reason').value.trim();
  var minutes = parseInt(document.getElementById('session-override-minutes').value);
  var any = document.getElementById('session-override-any-host').checked;
  if (!reason) { document.getElementById('session-override-reason').focus(); return; }
  if (!any && !ctx.host) { showToast('Choose a host or tick "Any host"', 'error'); return; }
  var btn = document.getElementById('session-override-start-btn');
  btn.disabled = true; btn.textContent = 'Starting...';
  try {
    var res = await authFetch('/api/session-overrides', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: ctx.session_id,
                            target_ip: any ? '' : ctx.host,
                            minutes: minutes, reason: reason})
    });
    var data = await res.json();
    if (data.status === 'ok') {
      closeSessionOverrideModal();
      showToast('Session override started', 'success');
      await refreshSessionOverrides();
      // Granting from a pending row should also clear that row — otherwise
      // the operator grants the bypass and still has to approve by hand.
      if (ctx.request_id) { await handleAction(ctx.request_id, 'approve', { confirmed: true }); }
      else { fetchRequests(); }
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
  btn.disabled = false; btn.textContent = 'Start Session Override';
}

async function cancelSessionOverride(id) {
  if (!(await customConfirm('JIT commands in that session will need approval again.', { title: 'Cancel this session override?', confirmLabel: 'Cancel override' }))) return;
  try {
    var res = await authFetch('/api/session-overrides/' + id, { method: 'DELETE' });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Session override cancelled', 'success');
      await refreshSessionOverrides();
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
    if (frozen) { label.textContent = 'FROZEN — SSH and integration writes rejected'; label.style.background = 'var(--state-denied)'; label.style.color = 'white'; }
    else { label.textContent = 'Active — running normally'; label.style.background = 'var(--state-approved)'; label.style.color = 'var(--bg-base)'; }
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
  var mf = document.getElementById('mobile-freeze');
  if (mf) {
    mf.classList.toggle('is-frozen', !!frozen);
    var lbl = mf.querySelector('.mf-label');
    if (lbl) lbl.textContent = frozen ? 'Frozen' : 'Freeze';
    mf.setAttribute('aria-pressed', frozen ? 'true' : 'false');
  }
}

async function toggleFreezeFromSidebar() {
  var frozen = !!(await fetch('/api/freeze/status').then(function(r){return r.json();}).catch(function(){return {};})).frozen;
  if (frozen) { await triggerUnfreeze(); } else { await triggerFreeze(); }
}

async function triggerFreeze() {
  if (!(await customConfirm('Every gateway will reject ALL commands — including whitelisted and approved-window commands — until you unfreeze. This takes effect within 30s.', { title: 'Freeze the entire fleet?', confirmLabel: 'Freeze fleet' }))) return;
  var btn = document.getElementById('freeze-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Freezing...'; }
  try {
    var res = await authFetch('/api/freeze', { method: 'POST' });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Fleet FROZEN — SSH and integration writes rejected within 30s', 'success');
      fetchFreezeStatus();
    } else { showToast('Failed: ' + (data.detail || 'unknown'), 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
  if (btn) { btn.disabled = false; btn.textContent = 'Freeze Fleet'; }
}

async function triggerUnfreeze() {
  if (!(await customConfirm('Gateways will resume normal policy enforcement on their next poll cycle (within 30s).', { title: 'Unfreeze the fleet?', confirmLabel: 'Unfreeze', danger: false }))) return;
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
    _fleetData = data || [];
  } catch(e) {}
  renderFleetTargetList();
  fleetConsoleUpdate();
}

function renderFleetTargetList() {
  var list = document.getElementById('target-list');
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

/* ── The broadcast voice — each node a soft ping with its own pitch ──
   Command Center sings; Statistics chimes; the fleet answers. Sounds
   live in the dispatch, never on hover or selection. */
var _fleetActx = null, _fleetSoundInit = false;
var _fleetScale = [146.83, 174.61, 196.00, 220.00, 261.63, 293.66, 349.23, 392.00, 440.00, 523.25];
var _fleetNodeEls = {};
function audioFleet() {
  if (!_fleetActx) { _fleetActx = new (window.AudioContext || window.webkitAudioContext)(); }
  if (_fleetActx.state === 'suspended') { _fleetActx.resume(); }
  return _fleetActx;
}
function fleetNodePing(pitch) {
  if (soundMuted) return;
  try {
    var ctx = audioFleet(), t0 = ctx.currentTime, f = _fleetScale[pitch % _fleetScale.length];
    var o = ctx.createOscillator(); o.type = 'triangle'; o.frequency.value = f;
    var o2 = ctx.createOscillator(); o2.type = 'sine'; o2.frequency.value = f * 2;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0); g.gain.linearRampToValueAtTime(0.045, t0 + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.75);
    var g2 = ctx.createGain(); g2.gain.value = 0.35;
    o.connect(g); o2.connect(g2); g2.connect(g); g.connect(ctx.destination);
    o.start(t0); o2.start(t0); o.stop(t0 + 0.8); o2.stop(t0 + 0.8);
  } catch (e) {}
}
function fleetBroadcastPulse() {
  if (soundMuted) return;
  try {
    var ctx = audioFleet(), t0 = ctx.currentTime;
    var o = ctx.createOscillator(); o.type = 'sine'; o.frequency.setValueAtTime(64, t0); o.frequency.exponentialRampToValueAtTime(42, t0 + 0.55);
    var g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.linearRampToValueAtTime(0.13, t0 + 0.05); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.65);
    o.connect(g); g.connect(ctx.destination); o.start(t0); o.stop(t0 + 0.7);
    var o2 = ctx.createOscillator(); o2.type = 'sine'; o2.frequency.value = 880;
    var g2 = ctx.createGain(); g2.gain.setValueAtTime(0.0001, t0); g2.gain.linearRampToValueAtTime(0.02, t0 + 0.03); g2.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.4);
    o2.connect(g2); g2.connect(ctx.destination); o2.start(t0); o2.stop(t0 + 0.45);
  } catch (e) {}
}
function fleetFailThud() {
  if (soundMuted) return;
  try {
    var ctx = audioFleet(), t0 = ctx.currentTime;
    var o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = 96;
    var g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.linearRampToValueAtTime(0.06, t0 + 0.015); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.4);
    o.connect(g); g.connect(ctx.destination); o.start(t0); o.stop(t0 + 0.45);
  } catch (e) {}
}
function fleetReturnChord(failures) {
  if (soundMuted) return;
  try {
    var ctx = audioFleet(), t0 = ctx.currentTime;
    var notes = failures ? [146.83, 174.61, 220.00] : [146.83, 185.00, 220.00, 293.66];
    notes.forEach(function (f) {
      var o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = f;
      var g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.linearRampToValueAtTime(0.04, t0 + 0.09); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 2.0);
      o.connect(g); g.connect(ctx.destination); o.start(t0); o.stop(t0 + 2.05);
      var o2 = ctx.createOscillator(); o2.type = 'sine'; o2.frequency.value = f * 2;
      var g2 = ctx.createGain(); g2.gain.setValueAtTime(0.0001, t0); g2.gain.linearRampToValueAtTime(0.012, t0 + 0.12); g2.gain.exponentialRampToValueAtTime(0.0001, t0 + 1.4);
      o2.connect(g2); g2.connect(ctx.destination); o2.start(t0); o2.stop(t0 + 1.45);
    });
  } catch (e) {}
}
function fleetVoice(freq, when, dur, gain) {
  try {
    var ctx = audioFleet();
    var o = ctx.createOscillator(); o.type = 'triangle'; o.frequency.value = freq;
    var o2 = ctx.createOscillator(); o2.type = 'sine'; o2.frequency.value = freq * 2;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when); g.gain.linearRampToValueAtTime(gain || 0.04, when + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    var g2 = ctx.createGain(); g2.gain.value = 0.3;
    o.connect(g); o2.connect(g2); g2.connect(g); g.connect(ctx.destination);
    o.start(when); o2.start(when); o.stop(when + dur + 0.05); o2.stop(when + dur + 0.05);
  } catch (e) {}
}
function fleetBass(freq, when, dur, gain) {
  try {
    var ctx = audioFleet();
    var o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = freq;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when); g.gain.linearRampToValueAtTime(gain || 0.05, when + 0.06); g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    o.connect(g); g.connect(ctx.destination); o.start(when); o.stop(when + dur + 0.05);
  } catch (e) {}
}
var _fleetAnthemScale = [146.83, 164.81, 185.00, 196.00, 220.00, 246.94, 277.18, 293.66, 329.63, 369.99, 392.00, 440.00, 493.88, 587.33];
/* The return: one ascending step per gateway that answered, so the tune
   grows with the fleet. One node echoes its own voice twice; two or more
   climb into a phrase that finally melts into a held chord — major when
   all clear, minor (with a low D) when a node failed. */
function fleetReturnAnthem(finished, anyFail) {
  if (soundMuted) return;
  try {
    var ctx = audioFleet(), t0 = ctx.currentTime;
    var root = 146.83, sc = _fleetAnthemScale, n = finished.length;
    if (n <= 0) return;
    if (n === 1) {
      var solo = finished[0].f || 220;
      fleetVoice(solo, t0, 0.5, 0.05);
      fleetVoice(solo, t0 + 0.18, 0.6, 0.05);
      fleetBass(root, t0, 1.6, 0.05);
      return;
    }
    var steps = Math.min(n, 12);
    for (var i = 0; i < steps; i++) {
      var oct = Math.floor(i / sc.length);
      fleetVoice(sc[i % sc.length] * (oct >= 1 ? 2 : 1), t0 + i * 0.085, 0.5, 0.042);
    }
    var chordAt = t0 + steps * 0.085;
    var voices = anyFail ? [146.83, 174.61, 220.00] : [146.83, 185.00, 220.00];
    if (steps >= 5) voices.push(293.66);
    if (steps >= 8) voices.push(440.00);
    if (steps >= 11) voices.push(587.33);
    voices.forEach(function (f) { fleetVoice(f, chordAt, 2.2, 0.032); });
    fleetBass(root, t0, steps * 0.085 + 2.6, 0.05);
    if (anyFail) fleetBass(73.42, chordAt, 2.0, 0.06);
  } catch (e) {}
}
function fleetStageClick() {
  if (soundMuted) return;
  try {
    var ctx = audioFleet(), t0 = ctx.currentTime;
    var len = Math.floor(ctx.sampleRate * 0.04);
    var buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
    for (var i = 0; i < len; i++) { d[i] = (Math.random() * 2 - 1) * (1 - i / len); }
    var src = ctx.createBufferSource(); src.buffer = buf;
    var bp = ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = 1400; bp.Q.value = 1.5;
    var g = ctx.createGain(); g.gain.value = 0.09;
    src.connect(bp); bp.connect(g); g.connect(ctx.destination); src.start(t0);
  } catch (e) {}
}
function fleetLowClick() {
  if (soundMuted) return;
  try {
    var ctx = audioFleet(), t0 = ctx.currentTime;
    var o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = 160;
    var g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.linearRampToValueAtTime(0.035, t0 + 0.005); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.12);
    o.connect(g); g.connect(ctx.destination); o.start(t0); o.stop(t0 + 0.14);
  } catch (e) {}
}
var _fleetSkyPos = [
  [34,26],[46,18],[58,34],[30,44],[52,52],[66,24],[18,58],[42,66],
  [72,48],[26,76],[60,70],[48,82],[80,62],[12,34],[84,76],[70,86]
];
function initFleetSounds() {
  if (_fleetSoundInit) return;
  _fleetSoundInit = true;
  document.addEventListener('pointerdown', function () { try { audioFleet(); } catch (e) {} }, { passive: true });
}
function _gwOnline(g) { return gwIsOnline(g); }
function buildFleetSky() {
  var wrap = document.getElementById('nodes');
  if (!wrap || wrap.dataset.built) return;
  wrap.dataset.built = '1';
  _fleetGateways.forEach(function (g, i) {
    var on = _gwOnline(g);
    var r = i < 3 ? 20 : (i < 8 ? 33 : 46);
    var slot = i < 3 ? i : (i < 8 ? i - 3 : i - 8);
    var slots = r === 20 ? 3 : (r === 33 ? 5 : 8);
    var a0 = (slot + 0.5) / slots * Math.PI * 2 + (r === 20 ? 0 : (r === 33 ? Math.PI / 6 : Math.PI / 3));
    var n = document.createElement('div');
    n.className = 'node' + (on ? '' : ' off');
    /* offline nodes never animate, so without an initial position they all
       stacked on the sun in the middle of the dial */
    n.style.left = (50 + r * Math.cos(a0)) + '%';
    n.style.top = (50 + r * Math.sin(a0)) + '%';
    n.setAttribute('data-ip', g.ip || '');
    n._o = { a0: a0, a: a0, r: r, speed: r === 20 ? 0.05 : (r === 33 ? 0.032 : 0.02), on: on };
    n.innerHTML = '<span class="nd"></span><span class="ni">' + gwIni(g.hostname || g.ip) + '</span>' +
      '<span class="ntip"><b>' + escapeHtml(g.hostname || g.ip) + '</b> &middot; ' + escapeHtml(g.ip) + (on ? '' : ' &middot; offline') + '</span>';
    wrap.appendChild(n);
    _fleetNodeEls[g.ip] = n;
  });
  fleetOrbitRun();
  syncFleetTargets();
}
function syncFleetTargets() {
  _fleetGateways.forEach(function (g) {
    var el = _fleetNodeEls[g.ip];
    if (el) el.classList.toggle('target', _fleetSelected.has(g.ip));
  });
  var c = document.getElementById('fleet-sel-count'); if (c) c.textContent = _fleetSelected.size;
  var tc = document.getElementById('targ-count'); if (tc) tc.textContent = _fleetSelected.size + ' selected';
  var oc = document.getElementById('fleet-on-count'); if (oc) oc.textContent = _fleetGateways.filter(_gwOnline).length;
  var nn = document.getElementById('fleet-node-count'); if (nn) nn.textContent = _fleetGateways.length;
}
function drawFleetTargets() {
  var list = document.getElementById('target-list');
  if (!list) return;
  if (!_fleetGateways.length) { list.innerHTML = '<p class="text-muted">No gateways registered.</p>'; return; }
  initFleetSounds();
  list.innerHTML = _fleetGateways.map(function (g) {
    var checked = _fleetSelected.has(g.ip);
    var online = g.online !== false;
    return '<div class="trow' + (checked ? ' checked' : '') + (online ? '' : ' disabled') + '" data-ip="' + esc(g.ip) + '">' +
      '<span class="tchk"></span>' +
      /* hostname first, IP beneath it: the rail is 240px wide and the IP used to
         win the space fight, clipping every hostname down to one letter. */
      '<span class="tsum"><span class="trow-name">' +
        '<span class="nm tname">' + escapeHtml(g.hostname || g.ip) + '</span>' +
        (online ? '' : '<em class="off">offline</em>') +
      '</span>' +
      '<span class="tip">' + escapeHtml(g.ip) + '</span></span>' +
      '</div>';
  }).join('');
  list.querySelectorAll('.trow').forEach(function (row) {
    row.addEventListener('click', function () {
      var ip = row.getAttribute('data-ip');
      var g = _fleetGateways.filter(function (x) { return x.ip === ip; })[0];
      if (!g || g.online === false) return;
      if (_fleetSelected.has(ip)) { _fleetSelected.delete(ip); row.classList.remove('checked'); }
      else { _fleetSelected.add(ip); row.classList.add('checked'); }
      syncFleetTargets();
    });
  });
  buildFleetSky();
  var onlineCount = _fleetGateways.filter(function (g) { return g.online !== false; }).length;
  var nc = document.getElementById('fleet-node-count'); if (nc) nc.textContent = _fleetGateways.length;
  if (!window._fleetChipsWired) {
    window._fleetChipsWired = true;
    document.getElementById('all-online-btn').addEventListener('click', function () { setFleetTargets(true); });
    document.getElementById('all-btn').addEventListener('click', function () { setFleetTargets(true); });
    document.getElementById('none-btn').addEventListener('click', function () { setFleetTargets(false); });
  }
  syncFleetTargets();
}

function toggleFleetTarget(ip) {
  if (_fleetSelected.has(ip)) _fleetSelected.delete(ip); else _fleetSelected.add(ip);
  drawFleetTargets();
}

function setFleetTargets(all) {
  _fleetSelected = new Set(all ? _fleetGateways.filter(function (g) { return g.online !== false; }).map(function (g) { return g.ip; }) : []);
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
  fleetStageClick();
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
  var qc = document.getElementById('fleet-q-count');
  if (qc) qc.textContent = _fleetQueue.length;
  if (btn) { btn.disabled = !_fleetQueue.length; btn.innerHTML = '&#9654; Dispatch (' + _fleetQueue.length + ')'; }
  if (!list) return;
  if (_fleetQueue.length === 0) {
    list.innerHTML = '<p class="text-muted">Queue is empty — stage a command above.</p>';
    return;
  }
  list.innerHTML = _fleetQueue.map(function(d, i) {
    var riskLine = d.risk ? '<div class="text-xs mt-1" style="color:var(--info)">' + escapeHtml(d.risk) + '</div>' : '';
    var dryLine = d.dry_run ? '<div class="text-xs mt-1" style="color:var(--ice)">dry-run: <code class="text-main">' + escapeHtml(d.dry_run) + '</code> <button onclick="useFleetDryRun(' + i + ')" class="chip-btn info">Use</button></div>' : '';
    return '<div class="qitem"><span class="qidx">' + String(i + 1).padStart(2, '0') + '</span>' +
      '<code>' + escapeHtml(d.command) + '</code>' +
      '<span class="qmeta">' + d.targets.length + ' nodes · ' + d.timeout + 's</span>' +
      '<button onclick="removeFleetFromQueue(' + i + ')" class="qx" title="remove">&#10005;</button>' +
      riskLine + dryLine +
      '</div>';
  }).join('');
  list.querySelectorAll('.qx').forEach(function(b) {
    b.addEventListener('click', function() { fleetLowClick(); });
  });
}

async function dispatchFleetQueue() {
  if (_fleetQueue.length === 0) { showToast('Queue is empty', 'error'); return; }
  var dryCount = _fleetQueue.filter(function(d) { return !!d.dry_run; }).length;
  var msg = 'Dispatch ' + _fleetQueue.length + ' command(s) to their target gateways?\n\nThis is the approval — they will run on each gateway\'s next poll cycle (≤30s).';
  if (dryCount > 0) {
    msg += '\n\n ' + dryCount + ' command(s) have a dry-run version available for safe testing — press Cancel to go back and upgrade them, or Dispatch to send as-is.';
  }
  if (!(await customConfirm(msg, { title: 'Dispatch the fleet queue?', confirmLabel: 'Dispatch' }))) return;

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
    var ok = await customConfirm(Object.keys(blacklistedIdx).length + ' queued command(s) match the central blocklist and will run with override.', { title: 'Dispatch blocklisted commands?', confirmLabel: 'Dispatch anyway' });
    if (!ok) return;
  }

  // Broadcast across the sky
  var targetIps = {};
  _fleetQueue.forEach(function(d) { (d.targets || []).forEach(function(ip) { targetIps[ip] = true; }); });
  var skyState = document.getElementById('sky-state');
  if (skyState) skyState.textContent = 'broadcasting…';
  var rip = document.getElementById('ripple');
  if (rip) { rip.classList.remove('go'); void rip.offsetWidth; rip.classList.add('go'); }
  fleetBroadcastPulse();
  Object.keys(targetIps).forEach(function(ip) {
    var idx = _fleetGateways.findIndex(function(x) { return x.ip === ip; });
    var g = idx >= 0 ? _fleetGateways[idx] : null;
    var el = _fleetNodeEls[ip];
    if (!el) return;
    el.classList.remove('done', 'fail', 'target');
    if (!g || g.online === false) return;
    el.classList.add('running');
    fleetNodePing(Math.max(0, idx) % 10);
  });

  var btn = document.getElementById('fleet-dispatch-btn');
  if (btn) { btn.disabled = true; btn.innerHTML = '&#9654; dispatching…'; }
  var dispatched = 0, failed = 0, keepIdx = {};
  try {
    for (var j = 0; j < _fleetQueue.length; j++) {
      var d = _fleetQueue[j];
      if (isHardcoreBlocked(d.command)) { keepIdx[j] = true; failed++; continue; }
      try {
        var res = await authFetch('/api/fleet/commands', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({command: d.command, target_ips: d.targets, reason: d.reason, timeout: d.timeout, override: !!blacklistedIdx[j]})
        });
        var data = await res.json();
        if (data.status === 'ok') dispatched++;
        else { keepIdx[j] = true; failed++; showToast('Not dispatched: ' + (data.detail || 'unknown'), 'error'); }
      } catch(e) { keepIdx[j] = true; failed++; showToast('Dispatch failed: ' + (e.message || 'network error'), 'error'); }
    }
  } finally {
    _fleetQueue = _fleetQueue.filter(function(d, j) { return !!keepIdx[j]; });
    if (btn) { btn.disabled = false; btn.innerHTML = '&#9654; Dispatch (' + _fleetQueue.length + ')'; }
    var skyState2 = document.getElementById('sky-state');
    if (skyState2 && dispatched > 0) skyState2.textContent = 'awaiting poll';
    renderFleetQueue();
  }
  var echo = document.getElementById('fleet-echo');
  if (echo) {
    if (dispatched > 0) echo.textContent = 'dispatched ' + dispatched + (failed > 0 ? ' · kept ' + failed + ' in queue' : ' — watch the rings answer');
    else if (failed > 0) echo.textContent = failed + ' command(s) not dispatched — see above';
  }
  if (dispatched > 0) { fetchFleetCommands(); showToast('Dispatched ' + dispatched + ' command(s)', 'success'); }
}

var _fleetData = [];
var _fleetRenderSig = null;

function fleetDot(status) {
  var cls = 'fdot';
  if (status === 'running' || status === 'queued') cls += ' running';
  else if (status === 'success') cls += ' ok';
  else if (status === 'failed' || status === 'timeout') cls += ' no';
  else cls += ' skip';
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
        outHtml = '<details data-out="' + esc(outId) + '" class="mt-2" ontoggle="loadFleetOutputIfNeeded(' + idJs(c.id) + ',' + escJs(r.gateway_ip) + ',this)"><summary class="text-xs cursor-pointer text-info">Show output' + (r.has_more ? ' (full)' : '') + '</summary>' +
          '<div class="mt-1 rounded border">' +
          '<div class="flex items-center justify-end gap-1 px-1 pt-1">' +
          '<button onclick="copyFleetOutput(' + idJs(c.id) + ',' + escJs(r.gateway_ip) + ')" class="chip-btn">Copy</button>' +
          '<button onclick="openFleetOutputModal(' + idJs(c.id) + ',' + escJs(r.gateway_ip) + ')" class="chip-btn info">View full</button>' +
          '</div>' +
          '<pre data-out="' + esc(outId) + '" class="fleet-out fleet-pre overflow-auto text-xs">' + escapeHtml(r.output) + '</pre>' +
          '</div></details>';
      }
      var gName = r.hostname ? escapeHtml(r.hostname) : escapeHtml(r.gateway_ip);
      var gIp = (r.hostname && r.hostname !== r.gateway_ip) ? ' <span class="text-muted">(' + escapeHtml(r.gateway_ip) + ')</span>' : '';
      var clearBtn = (r.status === 'queued') ?
        '<button onclick="clearFleetResult(' + idJs(c.id) + ',' + escJs(r.gateway_ip) + ')" class="chip-btn" title="Clear this stuck gateway so its queue can move on (other results are kept)">&#10005;</button>' : '';
      return '<div class="rt">' + fleetDot(r.status) + ' <strong>' + gName + '</strong>' + gIp + ' ' +
        '<span class="badge ' + rBadge + '">' + escapeHtml(r.status) + '</span>' +
        (r.exit_code != null && r.exit_code !== '' ? ' <span class="rms">exit ' + escapeHtml(r.exit_code) + '</span>' : '') +
        times + clearBtn + outHtml + '</div>';
    }).join('');
    var runningRes = (c.results || []).filter(function(r) { return r.status === 'running' && r.started_at; });
    var cdHtml;
    if (c.status === 'approved' && runningRes.length > 0) {
      var start = Math.min.apply(null, runningRes.map(function(r) { return r.started_at; }));
      var deadline = start + c.timeout;
      cdHtml = '<span class="text-xs fleet-countdown text-warning whitespace-nowrap" data-deadline="' + deadline + '"> counting…</span>';
    } else {
      cdHtml = '<span class="text-xs text-muted whitespace-nowrap">' + escapeHtml(c.timeout) + 's timeout</span>';
    }
    return '<div class="run">' +
      '<div class="run-top">' +
      '<div class="flex items-center gap-2 flex-wrap"><span class="text-xs font-mono text-muted">#' + idText(c.id) + '</span>' +
      '<code>' + escapeHtml(c.command) + '</code>' +
      '<span class="badge ' + statusBadge + '">' + escapeHtml(c.status) + '</span></div>' +
      '<div class="flex items-center gap-2 flex-wrap">' + cdHtml + '</div></div>' +
      '<div class="run-time">sent ' + formatDateTime(c.created_at) + '</div>' +
      '<div class="run-time">&#8594; ' + formatFleetTargets(c.target_ips || []).join(', ') + '</div>' +
      (c.reason ? '<div class="run-reason">reason &middot; ' + escapeHtml(c.reason) + '</div>' : '') +
      (resultRows ? '<div class="run-targets">' + resultRows + '</div>' : '') +
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
  syncFleetSkyFromResults();
}

var _fleetChordPlayedId = 0;
function syncFleetSkyFromResults() {
  if (!_fleetData.length) return;
  var latest = _fleetData[0];
  var results = latest.results || [];
  results.forEach(function(r) {
    var el = _fleetNodeEls[r.gateway_ip];
    if (!el) return;
    el.classList.remove('running', 'done', 'fail');
    if (r.status === 'success') el.classList.add('done');
    else if (r.status === 'failed' || r.status === 'timeout') el.classList.add('fail');
    else if (r.status === 'running' || r.status === 'queued') el.classList.add('running');
  });
  var st = document.getElementById('sky-state');
  if (st && st.textContent === 'broadcasting…') st.textContent = 'awaiting poll';
  var resolved = results.length > 0 && results.every(function(r) {
    return r.status === 'success' || r.status === 'failed' || r.status === 'timeout' || r.status === 'skipped';
  });
  if (resolved && _fleetChordPlayedId !== latest.id) {
    _fleetChordPlayedId = latest.id;
    var anyFail = results.some(function(r) { return r.status === 'failed' || r.status === 'timeout'; });
    var finished = [];
    results.forEach(function(r) {
      if (r.status === 'success' || r.status === 'failed' || r.status === 'timeout') {
        var idx = _fleetGateways.findIndex(function(x) { return x.ip === r.gateway_ip; });
        finished.push({ f: _fleetScale[Math.max(0, idx % _fleetScale.length)] });
      }
    });
    fleetReturnAnthem(finished, anyFail);
    results.forEach(function(r) { var el = _fleetNodeEls[r.gateway_ip]; if (el) el.classList.add('burst'); });
    var field = document.getElementById('sky-field');
    if (field) { field.classList.remove('bright'); void field.offsetWidth; field.classList.add('bright'); }
    setTimeout(function() {
      Object.keys(_fleetNodeEls).forEach(function(ip) { _fleetNodeEls[ip].classList.remove('burst'); });
      if (field) field.classList.remove('bright');
    }, 1200);
    if (st) st.textContent = anyFail ? 'failures returned' : 'all answered';
    setTimeout(function() { if (st) st.textContent = 'listening'; }, 1900);
  } else if (!resolved && st && st.textContent === 'listening') {
    st.textContent = 'awaiting poll';
  }
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
  if (!(await customConfirm(ip + ' is marked queued (it never ran, e.g. the gateway is on an old poller). Clearing marks it skipped so this gateway\'s queue can move on — the command and the other gateways\' results are kept.', { title: 'Clear the stuck result for fleet #' + cmdId + '?', confirmLabel: 'Clear result' }))) return;
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
      el.style.color = 'var(--state-denied)';
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
      return '<span class="policy-chip' + (isCore ? ' core' : '') + '" title="' + escapeHtml(line) + '">' + shield + '<code>' + escapeHtml(line) + '</code><button class="remove" data-line="' + encodeURIComponent(line) + '" data-core="' + (isCore ? '1' : '0') + '" title="Remove">&times;</button></span>';
    }).join('');
    // Removed core patterns — shipped defaults no longer in the list: struck + Restore
    if (type === 'regex_blacklist') {
      const removed = coreBlockPatterns().filter(function(p){ return lines.indexOf(p) === -1; });
      html += removed.map(function(p) {
        return '<span class="policy-chip removed" title="' + escapeHtml(p) + '"><code>' + escapeHtml(p) + '</code><button class="restore" data-line="' + encodeURIComponent(p) + '" title="Restore this core default">Restore</button></span>';
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
    customConfirm('"' + line + '" is a safety-net pattern that ships with Eshu by default. Removing it lets this kind of command reach JIT/approval. Re-add it any time via "Core defaults".', { title: 'Remove a shipped safety pattern?', confirmLabel: 'Remove pattern' }).then(function(ok){ if (ok) doRemove(); });
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
  const res = await authFetch('/api/policy_changes'); const changes = await res.json();
  const changesList = document.getElementById('policy-changes-list');
  if (changes.length === 0) { changesList.innerHTML = '<p class="text-muted">No policy changes recorded.</p>'; return; }
  changesList.innerHTML = changes.map(function(c) {
    return '<div class="p-3 rounded-lg border mb-2 bg-base">' +
    '<div class="flex items-center justify-between"><p class="text-xs text-muted">' + formatDateTime(c.timestamp) + ' - <span class="text-main">' + escapeHtml(c.policy_type) + '</span></p>' +
    '<button class="chip-btn info flex-shrink-0" onclick="rollbackPolicyChange(' + idJs(c.id) + ')">Restore</button></div>' +
    '<div class="mt-2 text-xs font-mono"><p class="text-danger">- ' + c.old_content.split('\n').filter(function(l){return l.trim()!=='';}).map(escapeHtml).join('<br>- ') + '</p><p class="text-success">+ ' + c.new_content.split('\n').filter(function(l){return l.trim()!=='';}).map(escapeHtml).join('<br>+ ') + '</p></div></div>';
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
  if (!(await customConfirm('This overwrites the current policy and pushes it to gateways.', { title: 'Restore the previous policy?', confirmLabel: 'Restore' }))) return;
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
    let vcls = 'verdict pending', vhtml = '';
    if (data.action === 'blocked') {
      vcls = 'verdict blocked'; vhtml = '<b>BLOCKED</b> — matches the blocklist. The gate stays closed.';
    } else if (data.action === 'auto_approved') {
      vcls = 'verdict approved'; vhtml = '<b>APPROVED</b> — crosses without a word.';
    } else {
      vcls = 'verdict pending'; vhtml = '<b>WOULD WAIT</b> — requires a human word at the threshold (JIT).';
    }
    let memLine = '';
    try {
      const mc = await authFetch('/api/policies/check?command=' + encodeURIComponent(cmd));
      if (mc.ok) {
        const m = await mc.json();
        memLine = '<div class="v-marks">exact <span class="' + (m.in_exact_whitelist ? 'y' : '') + '">' + (m.in_exact_whitelist ? '✓' : '—') + '</span>' +
          ' &middot; regex <span class="' + (m.in_regex_whitelist ? 'y' : '') + '">' + (m.in_regex_whitelist ? '✓' : '—') + '</span>' +
          ' &middot; blocklist <span class="' + (m.in_regex_blacklist ? 'n' : '') + '">' + (m.in_regex_blacklist ? '✕' : '—') + '</span></div>';
      }
    } catch(e) {}
    rd.innerHTML = '<div class="' + vcls + '">' + vhtml + memLine + '</div>' + testerAddButtons(data.action, cmd);
  } catch(err) { rd.innerHTML = '<div class="verdict blocked"> ' + escapeHtml(err.message) + '</div>'; }
}

function testerAddButtons(action, cmd) {
  const enc = encodeCmd(cmd);
  if (action === 'blocked') {
    return '<div class="flex gap-2 mt-2">' +
      '<button class="btn btn-xs" onclick="addToPolicy(decodeURIComponent(' + escJs(enc) + '), \'regex_blacklist\')">＋ Blocklist</button>' +
      '</div>';
  }
  return '<div class="flex gap-2 mt-2">' +
    '<button class="btn btn-xs" onclick="addToPolicy(decodeURIComponent(' + escJs(enc) + '), \'exact_whitelist\')">＋ Exact</button>' +
    '<button class="btn btn-xs" onclick="addToPolicy(decodeURIComponent(' + escJs(enc) + '), \'regex_whitelist\')">＋ Regex</button>' +
    '</div>';
}
async function seedEdge() {
  var confirmMsg = 'Seed Edge from current Build? This replaces any existing Edge installer.';
  try {
    var sr = await authFetch('/api/dev/status');
    if (sr.ok) {
      var sd = await sr.json();
      if (sd.pipeline_state === 'dev_in_progress') {
        confirmMsg = 'A previous dev push is active. Seeding stops it and deploys the latest Build to dev gateways.';
      }
    }
  } catch(e) {}
  if (!(await customConfirm(confirmMsg, { title: 'Seed Edge from the current Build?', confirmLabel: 'Seed Edge', danger: false }))) return;
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
  if (!(await customConfirm('Production gateways are not affected.', { title: 'Push Edge to ' + devCount + ' dev gateway(s)?', confirmLabel: 'Push to dev', danger: false }))) return;
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
  if (!(await customConfirm('All connected gateways will update within ~3 seconds. A backup of the current Build will be saved.', { title: 'Deploy Edge to the fleet?', confirmLabel: 'Deploy' }))) return;
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
  if (!(await customConfirm('ALL gateways will update within ~3 seconds.', { title: 'Roll back the fleet?', confirmLabel: 'Roll back' }))) return;
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
    // Keep the dev-gateway pills in sync with the server on every status render;
    // otherwise the panel shows a stale/empty list until a gateway is added or
    // removed by hand.
    fetchDevGateways();
    const goldenLabel = document.getElementById('golden-version-label');
    const edgeLabel = document.getElementById('edge-version-label');
    const fleetLabel = document.getElementById('fleet-version-label');
    const seedBtn = document.getElementById('seed-edge-btn');
    const pushBtn = document.getElementById('push-to-dev-btn');
    const promoteBtn = document.getElementById('promote-golden-btn');
    const rollbackBtn = document.getElementById('rollback-golden-btn');

    if (goldenLabel) goldenLabel.innerHTML = escapeHtml(data.dashboard_version || 'unset') + (data.golden_hash ? ' <span class="hash-tag">(' + escapeHtml(data.golden_hash) + ')</span>' : '');
    if (edgeLabel) edgeLabel.innerHTML = escapeHtml(data.edge_exists ? (data.dashboard_version || 'staged') : 'not staged') + (data.edge_hash ? ' <span class="hash-tag">(' + escapeHtml(data.edge_hash) + ')</span>' : '');
    if (fleetLabel) fleetLabel.innerHTML = escapeHtml(data.dashboard_version || 'unset') + (data.deployed_hash ? ' <span class="hash-tag">(' + escapeHtml(data.deployed_hash) + ')</span>' : '');

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
  rd.innerHTML = '<div class="regex-box"><p class="mb-1 text-danger">Generated Regex:</p><code>^' + escapeHtml(cmd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) + '$</code><p class="mt-1 text-muted">Copy into Regex Allowlist.</p></div>';
  rd.classList.remove('hidden');
}

// ── Enrollment ───────────────────────────────────────────────────────────
let currentToken = '', tokenExpiryTime = 0, tokenTimer = null, tokenStatusPoller = null;
function startTokenCountdown() {
  if (tokenTimer) clearInterval(tokenTimer);
  const el = document.getElementById('token-countdown');
  tokenTimer = setInterval(function() {
    const remaining = Math.ceil((tokenExpiryTime - Date.now() / 1000));
    if (remaining <= 0) {       el.textContent = 'Token expired'; el.style.color = 'var(--state-denied)'; clearInterval(tokenTimer); tokenTimer = null; if (tokenStatusPoller) { clearInterval(tokenStatusPoller); tokenStatusPoller = null; } document.getElementById('enroll-command').value = ''; document.getElementById('copy-enroll-btn').disabled = true; currentToken = ''; }
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
  const STEPS = ['started','stopping_poller','removing_binaries','removing_sudoers','removing_policies','removing_runtime','removing_logger','removing_user','cleaning_keys','cleaning_legacy','deregistering','complete'];
  const STEP_LABELS = { started:'Initiated', stopping_poller:'Stopping poller', removing_binaries:'Removing binaries', removing_sudoers:'Removing sudoers', removing_policies:'Removing policies', removing_runtime:'Removing runtime', removing_logger:'Removing logger', removing_user:'Removing user', cleaning_keys:'Cleaning SSH', cleaning_legacy:'Cleaning legacy Hermes', deregistering:'Deregistering', complete:'Complete' };
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
        // The gateway deregisters itself as its final action, so a missing
        // row means the uninstall is done even if the last progress post was
        // lost (or the server cleared the "complete" record already).
        var gone = !(_gatewaysData || []).some(function(g) { return g.ip === ip; });
        if (gone) { finish(); return; }
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
  if (!(await customConfirm('This only deletes the dashboard record — it does NOT uninstall anything on the host. If the gateway is still running, it will re-register on its next poll (~30s), so this may not be permanent. Use it only for decommissioned or never-completed hosts.', { title: 'Remove ' + hostname + ' (' + ip + ')?', confirmLabel: 'Remove record' }))) return;
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
const AUDIT_LABELS = { enrolled: 'enrolled', version_updated: 'updated', disconnected: 'disconnected', policy_committed: 'Policies committed', update_triggered: 'Update triggered', uninstall_triggered: 'Uninstall triggered', uninstalled: 'Uninstalled', password_changed: 'Password changed', password_cleared: 'Password removed', window_created: 'Window created', window_modified: 'Window updated', window_deleted: 'Window deleted', window_toggled: 'Window toggled', window_claimed: 'Window claimed', dev_update_pushed: 'Dev push', gateway_mode_changed: 'Gateway mode', agent_policy_updated: 'Agent policy updated', agent_token_revoked: 'Agent token revoked', mcp_policy_denied: 'MCP policy denied', session_required: 'Session required', mcp_session_required: 'MCP session required' };
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
  if (et.indexOf('integration_') === 0 || et.indexOf('agent_token_') === 0 || et.indexOf('agent_policy_') === 0 || et.indexOf('mcp_policy_') === 0) return 'integrations';
  if (et.indexOf('fleet_') === 0 || et.indexOf('freeze_') === 0 || et.indexOf('override_') === 0) return 'fleet';
  if (et.indexOf('zero_trust_') === 0 || ['enrolled','version_updated','disconnected','connected','uninstalled','uninstall_triggered','auto_deregistered','gateway_mode_changed'].indexOf(et) !== -1) return 'gateways';
  return 'config';
}

function auditBadgeClass(et) {
  /* Severity decides the colour, not the event family. Before this, every
     gateway disconnect painted a red row, so a normal overnight log looked
     like an incident. */
  // a human or policy refused something
  if (['jit_denied','window_request_denied','integration_call_denied','mcp_policy_denied','command_blocked'].indexOf(et) !== -1) return 'badge-denied';
  // a person or the policy cleared something
  if (['jit_approved','jit_override_approved','window_request_approved','integration_call_approved','window_claimed'].indexOf(et) !== -1) return 'badge-approved';
  // irreversible / destructive
  if (['uninstalled','uninstall_triggered','auto_deregistered','integration_deleted','agent_token_deleted','agent_token_revoked'].indexOf(et) !== -1) return 'badge-blocked';
  // ordinary state change or configuration edit — a fact, not an alarm
  return 'badge-neutral';
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
    var id = Number(l.id || i);
    var label = AUDIT_LABELS[l.event_type] || String(l.event_type).replace(/_/g, ' ');
    var badge = auditBadgeClass(l.event_type);
    var d = new Date(l.timestamp * 1000);
    var time = d.toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', second:'2-digit' });
    var date = d.toLocaleDateString([], { month:'short', day:'numeric' });
    var gw = l.hostname || l.gateway_ip || '';
    var details = l.details ? escapeHtml(String(l.details)) : '';
    /* Audit rows said "Last seen 691233s ago" while every other screen said
       "8d ago" for the same fact. One phrasing, everywhere. */
    details = details.replace(/Last seen (\d+)s ago/, function(_, n){ return 'Last seen ' + formatAgo(parseInt(n, 10)); });
    var row = '<tr class="log-row" data-aid="' + esc(id) + '" onclick="toggleAuditDetail(' + idJs(id) + ')">' +
      '<td><span class="log-expand">&#9656;</span></td>' +
      '<td><div class="log-time">' + time + '</div><div class="log-date">' + date + '</div></td>' +
      '<td><span class="badge ' + badge + '">' + escapeHtml(label) + '</span></td>' +
      '<td class="log-details">' + (details || '<span class="text-muted">—</span>') + '</td>' +
      '<td class="log-gw">' + (gw ? escapeHtml(gw) + (l.gateway_ip && l.hostname ? ' <span class="text-muted">' + escapeHtml(l.gateway_ip) + '</span>' : '') : '<span class="text-muted">—</span>') + '</td>' +
      '</tr>';
    var detailRow = '<tr class="log-detail-row" id="audit-detail-' + esc(id) + '"><td class="log-detail-cell" colspan="5">' +
      '<div class="log-detail">' +
        '<div class="detail-row"><span class="detail-label">Timestamp</span><span class="detail-value">' + escapeHtml(d.toLocaleString()) + '</span></div>' +
        '<div class="detail-row"><span class="detail-label">Event</span><span class="detail-value">' + escapeHtml(label) + '</span></div>' +
        (l.actor ? '<div class="detail-row"><span class="detail-label">Actor</span><span class="detail-value">' + escapeHtml(l.actor) + '</span></div>' : '') +
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
  if (!(await customConfirm('This hides requests older than ' + label + ' from the queue. Stats are preserved.', { title: 'Clear old requests?', confirmLabel: 'Clear queue', danger: false }))) return;
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
      if (!(await customConfirm('Requests older than ' + label + ' are deleted from the database. This CANNOT be undone, and stats, charts and top commands will be affected.', { title: 'Permanently delete old requests?', confirmLabel: 'Delete permanently' }))) return;
  await authFetch('/api/requests?older_than=' + period, { method: 'DELETE' });
  _queueClearBefore = 0; // Reset client-side filter after DB purge
  showToast('Database purged (older than ' + label + ')', 'success');
  fetchRequests();
  if (document.getElementById('view-stats') && !document.getElementById('view-stats').classList.contains('hidden')) fetchStatistics();
}
document.addEventListener('click', function(e) { if (!e.target.closest('#purge-btn') && !e.target.closest('#purge-dropdown')) document.getElementById('purge-dropdown').classList.remove('show'); });

// ── Custom Confirm Dialog ────────────────────────────────────────────────
/* Two shapes, chosen by blast radius:
   - inlineConfirm() for an action that belongs to one row — the question takes
     the place of the button, so the row keeps saying *what* is being deleted
     and the pointer never leaves it;
   - customConfirm() for an action that belongs to the whole system (freeze,
     purge, deploy, rollback), where the extra pause and the room to explain
     are the point. */
let _confirmResolve = null;
function customConfirm(msg, opts) {
  opts = opts || {};
  return new Promise(function(resolve) {
    var ok = document.getElementById('custom-confirm-ok');
    var title = document.getElementById('custom-confirm-title');
    _confirmResolve = function(result) {
      document.getElementById('custom-confirm-overlay').classList.add('hidden');
      resolve(result);
    };
    if (title) title.textContent = opts.title || 'Confirm this action';
    if (ok) {
      ok.textContent = opts.confirmLabel || 'Confirm';
      ok.classList.toggle('btn-danger', opts.danger !== false);
    }
    document.getElementById('custom-confirm-msg').innerText = msg;
    document.getElementById('custom-confirm-overlay').classList.remove('hidden');
    setTimeout(function () { var c = document.getElementById('custom-confirm-cancel'); if (c) c.focus(); }, 30);
  });
}

/* In-row confirmation. Replaces the trigger button with "<question> Cancel
   <verb>" until the user answers; Esc or a click elsewhere cancels, and focus
   returns to the trigger. Only one can be open at a time, and it cancels
   itself if a re-render removes the row underneath it. */
var _inlineConfirm = null;

function inlineConfirm(btn, opts) {
  opts = opts || {};
  if (!btn) return Promise.resolve(false);
  cancelInlineConfirm();
  return new Promise(function(resolve) {
    var done = false;
    var danger = opts.danger !== false;
    var root = document.createElement('span');
    root.className = 'inline-confirm';
    root.setAttribute('role', 'group');
    /* Take over the whole action group, not just the trigger: the sibling
       buttons (Edit, History, Policy) share a nowrap flex row, and squeezing
       the question between them is what made the old inline attempt unusable. */
    var host = opts.host || btn.parentNode;

    var question = document.createElement('span');
    question.className = 'ic-q';
    question.id = 'ic-q-' + (Date.now() % 100000);
    question.textContent = opts.question || 'Delete?';
    root.setAttribute('aria-labelledby', question.id);

    var cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-muted btn-xs';
    cancel.textContent = 'Cancel';
    cancel.setAttribute('data-ic', 'cancel');

    var ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'btn btn-xs' + (danger ? ' btn-deny' : '');
    ok.textContent = opts.label || 'Delete';
    ok.setAttribute('data-ic', 'ok');

    var buttons = document.createElement('span');
    buttons.className = 'ic-btns';
    buttons.appendChild(cancel);
    buttons.appendChild(ok);
    root.appendChild(question);
    root.appendChild(buttons);

    btn.classList.add('hidden');
    if (host && host.style) host.style.display = 'none';
    if (host && host.parentNode) host.parentNode.insertBefore(root, host.nextSibling);
    else btn.parentNode.insertBefore(root, btn.nextSibling);

    var obs = new MutationObserver(function() {
      if (!document.contains(btn) || !document.contains(root)) finish(false);
    });
    obs.observe(document.body, { childList: true, subtree: true });

    function finish(result) {
      if (done) return;
      done = true;
      obs.disconnect();
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointerdown', onOutside, true);
      if (_inlineConfirm && _inlineConfirm.root === root) _inlineConfirm = null;
      if (root.parentNode) root.parentNode.removeChild(root);
      if (host && host.style) host.style.display = '';
      if (document.contains(btn)) {
        btn.classList.remove('hidden');
        if (!result) btn.focus();
      }
      resolve(result);
    }
    function onKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopImmediatePropagation();
        finish(false);
      }
    }
    function onOutside(e) {
      if (!root.contains(e.target)) finish(false);
    }

    cancel.addEventListener('click', function() { finish(false); });
    ok.addEventListener('click', function() { finish(true); });
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onOutside, true);

    _inlineConfirm = { root: root, finish: finish };
    cancel.focus();
  });
}

function cancelInlineConfirm() {
  if (_inlineConfirm) _inlineConfirm.finish(false);
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

/* ── The tally voice — bell-toned, distinct from the Command Center ──
   D-major pentatonic glass bells. Sound lives in the data: hover the
   rhythm chart to hear the week, or play it outright. */
var _tallyActx = null, _tallyInitDone = false, _tallyTimers = [];
var _tallyScale = [146.83, 164.81, 185.00, 220.00, 261.63, 293.66, 349.23, 392.00, 440.00, 493.88];
function audioTally() {
  if (!_tallyActx) { _tallyActx = new (window.AudioContext || window.webkitAudioContext)(); }
  if (_tallyActx.state === 'suspended') { _tallyActx.resume(); }
  return _tallyActx;
}
function tallyIdx(val, max) { var i = Math.round((val / max) * (_tallyScale.length - 1)); return Math.max(0, Math.min(_tallyScale.length - 1, i)); }
function tallyTone(f0, spark, gain, dur) {
  if (soundMuted) return;
  try {
    var ctx = audioTally(), t0 = ctx.currentTime;
    gain = gain || 0.045; dur = dur || 1.0;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0); g.gain.linearRampToValueAtTime(gain, t0 + 0.012); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    var o1 = ctx.createOscillator(); o1.type = 'sine'; o1.frequency.value = f0;
    var o2 = ctx.createOscillator(); o2.type = 'sine'; o2.frequency.value = f0 * 2.756;
    var g2 = ctx.createGain(); g2.gain.value = 0.35; o2.connect(g2); g2.connect(g);
    var o3 = ctx.createOscillator(); o3.type = 'triangle'; o3.frequency.value = f0 * 0.5; o3.detune.value = 6;
    var g3 = ctx.createGain(); g3.gain.value = 0.3; o3.connect(g3); g3.connect(g);
    o1.connect(g); g.connect(ctx.destination);
    o1.start(t0); o2.start(t0); o3.start(t0);
    o1.stop(t0 + dur + 0.05); o2.stop(t0 + dur + 0.05); o3.stop(t0 + dur + 0.05);
    if (spark) {
      var s = ctx.createOscillator(); s.type = 'sine'; s.frequency.value = f0 * 4;
      var sg = ctx.createGain(); sg.gain.setValueAtTime(0.0001, t0); sg.gain.linearRampToValueAtTime(gain * 0.6, t0 + 0.008); sg.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.5);
      s.connect(sg); sg.connect(ctx.destination); s.start(t0); s.stop(t0 + 0.55);
    }
  } catch (e) {}
}
function tallyNodeTone(cmds, off) {
  if (soundMuted) return;
  if (off) {
    try { var ctx = audioTally(), t0 = ctx.currentTime; var o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = 110;
      var g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.linearRampToValueAtTime(0.04, t0 + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.4);
      o.connect(g); g.connect(ctx.destination); o.start(t0); o.stop(t0 + 0.45); } catch (e) {}
    return;
  }
  tallyTone(_tallyScale[tallyIdx(cmds, 300)] / 2, cmds > 140, 0.045, 1.1);
}
function sparkBars(elId, series, warm) {
  var el = document.getElementById(elId); if (!el) return;
  var max = Math.max.apply(null, series.concat([1]));
  el.innerHTML = series.map(function (v) {
    var h = Math.max(8, Math.round(v / max * 100));
    var c = warm ? 'linear-gradient(180deg,rgba(220,38,38,0.8),rgba(220,38,38,0.2))' : 'linear-gradient(180deg,rgba(245,158,11,0.9),rgba(245,158,11,0.2))';
    return '<div class="sbar" style="height:' + h + '%;background:' + c + '"></div>';
  }).join('');
}
function toggleTallyRhythm() {
  var btn = document.getElementById('play-btn'); if (!btn) return;
  if (_tallyTimers.length) {
    _tallyTimers.forEach(clearTimeout); _tallyTimers = [];
    btn.innerHTML = '&#9654; play the week'; btn.classList.remove('playing');
    return;
  }
  var d = _statsData; if (!d) return;
  var daily = (d.daily || []).filter(function (x) { return x && x.total != null; });
  if (!daily.length) return;
  var max = Math.max.apply(null, daily.map(function (x) { return x.total || 0; }).concat([1]));
  btn.innerHTML = '&#9632; stop'; btn.classList.add('playing');
  try {
    var now = audioTally().currentTime;
    daily.forEach(function (day, i) {
      var ms = Math.max(0, (now + i * 0.26) - audioTally().currentTime) * 1000;
      _tallyTimers.push(setTimeout(function () {
        tallyTone(_tallyScale[tallyIdx(day.total || 0, max)], (day.total || 0) === max, 0.05, 1.2);
      }, ms));
    });
    var end = Math.max(0, (now + daily.length * 0.26) - audioTally().currentTime) * 1000;
    _tallyTimers.push(setTimeout(function () {
      tallyTone(440, true, 0.055, 1.8); tallyTone(293.66, false, 0.04, 2.2);
      btn.innerHTML = '&#9654; play the week'; btn.classList.remove('playing'); _tallyTimers = [];
    }, end));
  } catch (e) {}
}
function initTallyInteractions() {
  if (_tallyInitDone) return;
  _tallyInitDone = true;
  document.addEventListener('pointerdown', function () { try { audioTally(); } catch (e) {} }, { passive: true });
  var play = document.getElementById('play-btn');
  if (play) play.addEventListener('click', toggleTallyRhythm);
}

/* Drawn in the container's real pixel width. A fixed 560-wide viewBox with
   preserveAspectRatio="none" scaled x by ~2 while y stayed at 1, so every
   label was stretched sideways and the peak dot became an ellipse. The width
   is re-read on resize so collapsing the rail (or a phone rotation) does not
   reintroduce the distortion. */
var _rhythmData = null;
var _rhythmWidth = 0;
var _rhythmRo = null;
var _rhythmTimer = null;

function _rhythmObserve(wrap) {
  if (_rhythmRo || typeof ResizeObserver === 'undefined') return;
  _rhythmRo = new ResizeObserver(function (entries) {
    var w = entries[0] ? entries[0].contentRect.width : 0;
    if (!_rhythmData || Math.abs(w - _rhythmWidth) < 4) return;
    clearTimeout(_rhythmTimer);
    _rhythmTimer = setTimeout(function () {
      if (_rhythmData) renderRhythm(_rhythmData);
    }, 120);
  });
  _rhythmRo.observe(wrap);
}

function renderRhythm(d) {
  var wrap = document.getElementById('stats-chart-wrap'); if (!wrap) return;
  var daily = (d.daily || []).filter(function (x) { return x && x.total != null; });
  var peakEl = document.getElementById('rhythm-peak');
  if (!daily.length) { wrap.innerHTML = '<p class="text-muted" style="padding:20px">No daily data yet.</p>'; if (peakEl) peakEl.textContent = ''; return; }
  _rhythmData = d;
  var W = Math.max(320, Math.round(wrap.clientWidth || 560));
  _rhythmWidth = W;
  _rhythmObserve(wrap);
  var n = daily.length, max = Math.max.apply(null, daily.map(function (x) { return x.total || 0; }).concat([1]));
  var total = 0; daily.forEach(function (x) { total += x.total || 0; });
  if (peakEl) peakEl.textContent = 'peak ' + max + ' cmds';
  var padL = 20, padR = W - 20, base = 175;
  var pts = daily.map(function (x, i) {
    var px = n > 1 ? padL + i * ((padR - padL) / (n - 1)) : (padL + padR) / 2;
    return [Math.round(px * 10) / 10, 165 - Math.round((x.total || 0) / max * 140)];
  });
  var line = 'M' + pts.map(function (p) { return p[0] + ',' + p[1]; }).join(' L');
  var area = line + ' L' + padR + ',' + base + ' L' + padL + ',' + base + ' Z';
  var peakI = 0; daily.forEach(function (x, i) { if ((x.total || 0) > (daily[peakI].total || 0)) peakI = i; });
  var peakP = pts[peakI];
  var gl = '<text class="grid-label" x="' + (padR + 6) + '" y="28" text-anchor="end">' + max + '</text>' +
    '<text class="grid-label" x="' + (padR + 6) + '" y="103" text-anchor="end">' + Math.round(max / 2) + '</text>' +
    '<text class="grid-label" x="' + (padR + 6) + '" y="178" text-anchor="end">0</text>';
  var labelIdx = [];
  if (n <= 8) { for (var i = 0; i < n; i++) labelIdx.push(i); }
  else { labelIdx = [0, Math.floor((n - 1) / 3), Math.floor(2 * (n - 1) / 3), n - 1]; }
  var ticks = '', labels = '';
  daily.forEach(function (x, i) {
    ticks += '<line class="day-tick" x1="' + pts[i][0] + '" y1="180" x2="' + pts[i][0] + '" y2="190"/>';
    if (labelIdx.indexOf(i) >= 0) {
      var lb = (x.date || x.day || ''); if (lb.indexOf('-') >= 0) { var pp = lb.split('-'); lb = pp[2] + '/' + pp[1]; }
      labels += '<text class="day-label" x="' + pts[i][0] + '" y="203" text-anchor="middle">' + escapeHtml(lb) + '</text>';
    }
  });
  var autoTxt = '\u2014';
  if (d.automation_trend && d.automation_trend.length) {
    var psum = 0; d.automation_trend.forEach(function (a) { psum += a.automation_pct || 0; });
    autoTxt = Math.round(psum / d.automation_trend.length) + '%';
  }
  wrap.innerHTML =
    '<svg viewBox="0 0 ' + W + ' 300" aria-hidden="true">' +
    '<defs><linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" style="stop-color:var(--accent-line)"/><stop offset="55%" style="stop-color:var(--accent-wash)"/><stop offset="100%" style="stop-color:rgba(245,158,11,0)"/></linearGradient></defs>' +
    '<line class="grid-line" x1="' + padL + '" y1="25" x2="' + padR + '" y2="25"/><line class="grid-line" x1="' + padL + '" y1="75" x2="' + padR + '" y2="75"/><line class="grid-line" x1="' + padL + '" y1="125" x2="' + padR + '" y2="125"/><line class="grid-line" x1="' + padL + '" y1="175" x2="' + padR + '" y2="175"/>' + gl +
    '<path class="area-fill" d="' + area + '"/><path class="rhythm-line" d="' + line + '"/>' + ticks + labels +
    '<circle class="peak-dot" cx="' + peakP[0] + '" cy="' + peakP[1] + '" r="3.5"/><text class="peak-label" x="' + peakP[0] + '" y="12" text-anchor="middle">' + max + '</text>' +
    '</svg>' +
    '<div class="chart-overlay"><div class="chart-big">' + total + ' <span>commands</span></div><div class="chart-sub">' + n + '-day window &middot; automated ' + autoTxt + '</div></div>';
  for (var i = 0; i < n; i++) {
    (function (i) {
      var col = document.createElement('div');
      col.className = 'scrub-col';
      col.style.left = (i / n * 100) + '%';
      col.style.width = (100 / n) + '%';
      col.title = (daily[i].total || 0) + ' commands';
      col.addEventListener('mouseenter', function () {
        tallyTone(_tallyScale[tallyIdx(daily[i].total || 0, max)], i === peakI, 0.045, 1.0);
      });
      wrap.appendChild(col);
    })(i);
  }
}

/* ── F6: how approved ────────────────────────────────────────────────────
   A stacked bar per day, oldest first: the mix of who cleared the work. The
   headline is the override share — the number F3 plans and F4's read lane
   exist to bring down. Zero-count buckets stay in the legend, greyed, so the
   absence of overrides is visible rather than merely unmentioned. */
function approvalsHtml(ab) {
  ab = ab || {};
  var series = ab.series || [];
  var totals = ab.totals || {};
  var order = (ab.buckets && ab.buckets.length)
    ? ab.buckets.map(function (b) { return [b.key, b.label]; })
    : APPROVAL_BUCKETS;
  if (!series.length) return '<p class="text-muted">No decisions in this window.</p>';
  var max = 1;
  series.forEach(function (day) {
    var t = 0;
    order.forEach(function (b) { t += parseInt(day[b[0]], 10) || 0; });
    if (t > max) max = t;
  });
  var rows = series.map(function (day) {
    var total = 0;
    order.forEach(function (b) { total += parseInt(day[b[0]], 10) || 0; });
    var segs = order.map(function (b) {
      var n = parseInt(day[b[0]], 10) || 0;
      if (!n || !total) return '';
      return '<span class="appr-seg appr-' + b[0] + '" style="width:' +
        (n / total * 100).toFixed(2) + '%" title="' +
        escapeHtml(b[1] + ': ' + n) + '"></span>';
    }).join('');
    var width = Math.round(total / max * 100);
    var label = String(day.date || '').slice(5).replace('-', '/');
    return '<div class="appr-day" title="' +
      escapeHtml(String(day.date || '') + ' \u00b7 ' + total + ' approved') + '">' +
      '<span class="appr-date">' + escapeHtml(label) + '</span>' +
      '<span class="appr-track"><span class="appr-bar" style="width:' + width + '%">' + segs + '</span></span>' +
      '<span class="appr-total">' + (total || '') + '</span></div>';
  }).join('');
  var legend = order.map(function (b) {
    var n = parseInt(totals[b[0]], 10) || 0;
    return '<span class="appr-legend-item' + (n ? '' : ' zero') + '">' +
      '<i class="appr-dot appr-' + b[0] + '"></i>' + escapeHtml(b[1]) +
      ' <b>' + n + '</b></span>';
  }).join('');
  return '<div class="appr-days">' + rows + '</div>' +
    '<div class="appr-legend">' + legend + '</div>';
}

function renderStatsApprovals(d) {
  var el = document.getElementById('stats-approvals');
  var shareEl = document.getElementById('approvals-share');
  if (!el) return;
  var ab = (d && d.approval_breakdown) || {};
  var approved = parseInt(ab.approved, 10) || 0;
  if (shareEl) {
    shareEl.textContent = approved
      ? ('override share ' + (Number(ab.override_share) || 0) + '%')
      : 'no decisions yet';
  }
  el.innerHTML = approvalsHtml(ab);
}

function renderStatsKpis(d) {
  var el = document.getElementById('stats-summary-cards'); if (!el) return;
  /* The chart above already prints the command total and the automation rate.
     These four cards carry the facts it does not: who decided what. */
  var auto = 0, jit = 0, blocked = 0, denied = 0;
  (d.per_gateway || []).forEach(function (g) {
    auto += g.auto_approved || 0;
    blocked += g.blocked || 0;
    denied += g.denied || 0;
    jit += (g.total || 0) - (g.auto_approved || 0) - (g.blocked || 0) - (g.denied || 0);
  });
  var gh = d.gateway_health || {};
  var on = gh.online_gateways || 0, tot = gh.total_gateways || 0;
  var dots = '';
  for (var i = 0; i < tot; i++) dots += '<span class="d' + (i < on ? '' : ' off') + '"></span>';
  el.innerHTML =
    '<div class="kpi kpi-approved"><div class="kval">' + auto + '</div><div class="klbl">Auto-approved by policy</div></div>' +
    '<div class="kpi kpi-pending"><div class="kval">' + jit + '</div><div class="klbl">Cleared by a human</div></div>' +
    '<div class="kpi kpi-blocked"><div class="kval">' + (blocked + denied) + '</div><div class="klbl">Refused &middot; ' + blocked + ' policy / ' + denied + ' human</div></div>' +
    '<div class="kpi"><div class="kval plain">' + on + '<span class="kpi-sub"> / ' + tot + '</span></div><div class="klbl">Gateways online</div><div class="gw-dots">' + dots + '</div></div>';
  el.querySelectorAll('.kpi').forEach(function (k) {
    k.addEventListener('mouseenter', function () { tallyTone(1975.53, false, 0.02, 0.3); });
  });
}

/* ── When the fleet works ────────────────────────────────────────────────
   A weekday × hour grid. The API has returned hourly buckets for a while and
   nothing used them; a 7×24 grid answers a question a line chart cannot —
   "is this a nightly cron fleet or a workday fleet?" — and it reads like the
   star grid the rest of the product is built on. */
var _HEAT_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function heatLevel(v, max) {
  if (!v || !max) return 0;
  var r = v / max;
  if (r > 0.66) return 4;
  if (r > 0.4) return 3;
  if (r > 0.18) return 2;
  return 1;
}

/* ── Fleet hygiene ───────────────────────────────────────────────────────
   Three facts that need a person, from data the API already returns but
   nothing displayed: are the gateways on the same version, do they all have
   a token (without one, JIT delivery is silently broken), and are the
   pre-approved windows actually used. */
function renderStatsHygiene(d) {
  var el = document.getElementById('stats-hygiene');
  var flag = document.getElementById('hygiene-flag');
  if (!el) return;
  var gh = d.gateway_health || {};
  var versions = gh.version_distribution || [];
  var total = gh.total_gateways || 0;
  var coverage = gh.token_coverage;
  var wins = d.windows_summary || {};

  var versionItems = versions.slice(0, 3).map(function (v) {
    return '<b>' + escapeHtml(String(v.version || '?')) + '</b> \u00d7' + v.count;
  }).join(' \u00b7 ');
  var versionNote = versions.length > 1 ? (versions.length - 1) + ' release' + (versions.length > 2 ? 's' : '') + ' behind the newest' : 'single release';
  var tokNote = coverage == null ? '\u2014' : (coverage >= 100 ? 'every gateway can receive JIT' : 'gateways without a token cannot receive JIT');
  var winNote = wins.unused ? 'never executed' : 'all have been used';

  function item(label, value, note, warn) {
    return '<div class="hygiene-item' + (warn ? ' warn' : '') + '">' +
      '<div class="hy-lbl">' + label + '</div>' +
      '<div class="hy-val">' + value + '</div>' +
      '<div class="hy-note">' + note + '</div></div>';
  }

  el.innerHTML =
    item('Versions', versionItems || '\u2014', versionNote, versions.length > 1) +
    item('Token coverage', (coverage == null ? '\u2014' : coverage + '%'), tokNote, coverage != null && coverage < 100) +
    item('Approved windows', String(wins.total || 0), (wins.unused || 0) + ' ' + (wins.unused === 1 ? 'window' : 'windows') + ' ' + winNote, !!wins.unused);

  var issues = (versions.length > 1 ? 1 : 0) + (coverage != null && coverage < 100 ? 1 : 0) + (wins.unused ? 1 : 0);
  if (flag) flag.textContent = issues ? issues + ' to look at' : 'all consistent';
}

function renderStatsHeatmap(d) {
  var el = document.getElementById('stats-heatmap');
  var peakEl = document.getElementById('heat-peak');
  if (!el) return;
  var grid = d.weekly_heatmap || [];
  if (!grid.length || !grid.some(function (row) { return row.some(function (v) { return v > 0; }); })) {
    el.innerHTML = '<p class="text-muted" style="padding:14px 2px">No activity in this window yet.</p>';
    if (peakEl) peakEl.textContent = '';
    return;
  }
  var max = 0, total = 0;
  grid.forEach(function (row) { row.forEach(function (v) { max = Math.max(max, v); total += v; }); });

  var head = '<div class="heat-row heat-head"><span class="heat-day"></span>';
  for (var h = 0; h < 24; h++) {
    head += '<span class="heat-hour">' + (h % 3 === 0 ? String(h).padStart(2, '0') : '') + '</span>';
  }
  head += '</div>';

  var body = grid.map(function (row, di) {
    var cells = row.map(function (v, hi) {
      var lvl = heatLevel(v, max);
      var peak = d.busiest && d.busiest.count && d.busiest.dow === di && d.busiest.hour === hi;
      return '<span class="heat-cell l' + lvl + (peak ? ' peak' : '') + '" data-day="' + _HEAT_DAYS[di] + '" data-hour="' + hi + '"' +
        ' title="' + _HEAT_DAYS[di] + ' ' + String(hi).padStart(2, '0') + ':00 — ' + v + ' command' + (v === 1 ? '' : 's') + '"></span>';
    }).join('');
    return '<div class="heat-row"><span class="heat-day">' + _HEAT_DAYS[di] + '</span>' + cells + '</div>';
  }).join('');

  el.innerHTML = head + body +
    '<div class="heat-foot"><span>' + total.toLocaleString() + ' commands in this window</span>' +
      '<span class="heat-key"><span class="heat-cell l1"></span><span class="heat-cell l2"></span><span class="heat-cell l3"></span><span class="heat-cell l4"></span> busier</span>' +
    '</div>';

  if (peakEl && d.busiest && d.busiest.count) {
    peakEl.textContent = 'peak ' + _HEAT_DAYS[d.busiest.dow] + ' ' +
      String(d.busiest.hour).padStart(2, '0') + ':00 · ' + d.busiest.count + ' cmds';
  } else if (peakEl) { peakEl.textContent = ''; }
}

function renderStatsNodes(d) {
  var el = document.getElementById('stats-nodes'); if (!el) return;
  var gws = (d.per_gateway || []).slice();
  if (!gws.length) { el.innerHTML = '<p class="text-muted">No gateway data.</p>'; return; }

  /* x = commands handled, y = automation rate.
     The old y was "(total - blocked - denied) / total", which counted
     human-approved JITs as automation — so with nothing refused every gateway
     scored exactly 100% and the whole fleet collapsed onto one horizontal
     line. Rounding to whole percent did the same again whenever rates were
     close. Both are fixed: y is auto_approved / total, unrounded for
     placement, and colliding nodes are pushed apart deterministically. */
  var maxT = Math.max.apply(null, gws.map(function (g) { return g.total || 0; }).concat([1]));
  var rows = gws.map(function (g) {
    var total = g.total || 0;
    var auto = g.auto_approved || 0;
    var blocked = g.blocked || 0;
    var denied = g.denied || 0;
    return {
      g: g, total: total,
      autoRate: total ? (auto / total) * 100 : 0,
      human: Math.max(0, total - auto - blocked - denied),
      blocked: blocked, denied: denied
    };
  }).sort(function (a, b) { return b.total - a.total; });

  var seen = {};
  var placed = rows.map(function (row) {
    var x = 10 + (row.total / maxT) * 80;
    var y = 12 + (1 - row.autoRate / 100) * 76;
    // spread exact/near ties on a golden-angle spiral so a fleet with identical
    // numbers still reads as a cluster, not a single dot
    var key = Math.round(x / 3) + ':' + Math.round(y / 3);
    var n = seen[key] = (seen[key] || 0) + 1;
    if (n > 1) {
      var ang = (n - 2) * 2.39996;
      var rad = 2.6 + 1.7 * (n - 2);
      x += Math.cos(ang) * rad;
      y += Math.sin(ang) * rad;
    }
    return { x: Math.max(6, Math.min(94, x)), y: Math.max(8, Math.min(86, y)) };
  });

  var guides = '';
  [0, 25, 50, 75, 100].forEach(function (pct) {
    var gy = 12 + (1 - pct / 100) * 76;
    guides += '<line class="scatter-guide' + (pct === 50 ? ' mid' : '') + '" x1="6" y1="' + gy + '" x2="96" y2="' + gy + '"/>';
  });

  el.innerHTML =
    '<div class="scatter-axis" aria-hidden="true">' +
      '<span style="top:12%">100%</span><span style="top:50%">50%</span><span style="top:88%">0%</span>' +
      '<span class="ax-ylabel">automated</span>' +
    '</div>' +
    '<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">' + guides + '</svg>' +
    rows.map(function (row, i) {
      var p = placed[i], g = row.g;
      var online = !g.last_seen || gwIsOnline(g);
      var cls = 'g-node' + (row.total >= maxT * 0.5 ? ' big' : (row.total <= maxT * 0.15 ? ' small' : '')) +
        (online ? '' : ' off') + (_selectedStatsGateways.has(g.ip) ? '' : ' dim');
      var tip = '<b>' + escapeHtml(g.hostname || g.ip) + '</b>' +
        '<span><b>' + row.total + '</b> commands</span>' +
        '<span><b>' + Math.round(row.autoRate) + '%</b> automated</span>' +
        '<span><b>' + row.human + '</b> cleared by a human</span>' +
        '<span><b>' + (row.blocked + row.denied) + '</b> refused</span>';
      return '<div class="' + cls + '" style="left:' + p.x.toFixed(2) + '%;top:' + p.y.toFixed(2) + '%" ' +
        'data-cmds="' + row.total + '" data-name="' + escapeHtml(g.hostname || g.ip) + '" ' +
        'role="button" tabindex="0" aria-label="' + escapeHtml(g.hostname || g.ip) + ': ' + row.total + ' commands, ' + Math.round(row.autoRate) + '% automated" ' +
        'onclick="toggleStatsGateway(' + escJs(g.ip) + ')" ' +
        'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();toggleStatsGateway(' + escJs(g.ip) + ');}">' +
        '<span class="n-dot"></span><span class="n-ini">' + gwIni(g.hostname || g.ip) + '</span>' +
        '<span class="tip">' + tip + '</span></div>';
    }).join('');
  el.querySelectorAll('.g-node').forEach(function (n) {
    n.addEventListener('mouseenter', function () {
      tallyNodeTone(parseInt(n.getAttribute('data-cmds') || '0', 10), n.classList.contains('off'));
    });
  });
}

function renderStatsTop(d) {
  var el = document.getElementById('stats-topcmds'); if (!el) return;
  var top = d.top_commands || [];
  if (!top.length) { el.innerHTML = '<p class="text-muted">No command data available.</p>'; return; }
  var max = Math.max.apply(null, top.map(function (c) { return c.count || 0; }).concat([1]));
  el.innerHTML = top.slice(0, 10).map(function (c, i) {
    var pct = max > 0 ? Math.round((c.count || 0) / max * 100) : 0;
    return '<div class="cmd-row"><span class="cmd-rank">' + String(i + 1).padStart(2, '0') + '</span>' +
      '<div class="cmd-main"><div class="cmd-name">' + escapeHtml(c.command) +
        (c.hosts > 1 ? ' <span class="cmd-hosts">' + c.hosts + ' hosts</span>' : '') +
        '</div><div class="cmd-track"><div class="cmd-fill" style="width:' + pct + '%"></div></div></div>' +
      '<span class="cmd-count">' + (c.count || 0) + '</span></div>';
  }).join('');
}

function renderStatsReadLane(d) {
  var el = document.getElementById('stats-read-lane'); if (!el) return;
  var rl = d.read_lane || {};
  var eligible = Number(rl.shadow_eligible || 0);
  var auto = Number(rl.auto_approved || 0);
  if (!eligible && !auto) {
    el.innerHTML = '<p class="text-muted">No read-lane activity in this window.</p>';
    return;
  }
  var approved = Number(rl.shadow_approved || 0);
  var denied = Number(rl.shadow_denied || 0);
  var rate = eligible ? Math.round(approved / eligible * 100) : 0;
  el.innerHTML =
    '<div class="kpi"><div class="kval">' + eligible + '</div>' +
      '<div class="klbl">would have auto-run (shadow)</div></div>' +
    '<div class="cmd-row"><span class="cmd-count">' + approved + '</span> approved \u00b7 ' +
      denied + ' denied \u00b7 median ' + Number(rl.shadow_median_len || 0) + ' chars</div>' +
    '<div class="text-xs text-muted">' + rate + '% approved by the operator' +
      (auto ? ' \u00b7 ' + auto + ' auto-ran with the lane on' : '') + '</div>';
}

function renderStatsInnerTargets(d) {
  var el = document.getElementById('stats-inner-targets'); if (!el) return;
  var rows = d.inner_targets || [];
  if (!rows.length) {
    el.innerHTML = '<p class="text-muted">No pct/qm/docker targets recorded in this window.</p>';
    return;
  }
  var max = Math.max.apply(null, rows.map(function (r) { return r.count || 0; }).concat([1]));
  el.innerHTML = rows.slice(0, 10).map(function (r, i) {
    var pct = max > 0 ? Math.round((r.count || 0) / max * 100) : 0;
    return '<div class="cmd-row"><span class="cmd-rank">' + String(i + 1).padStart(2, '0') + '</span>' +
      '<div class="cmd-main"><div class="cmd-name">' + innerTargetChipHtml(r.target || '') + '</div>' +
      '<div class="cmd-track"><div class="cmd-fill" style="width:' + pct + '%"></div></div></div>' +
      '<span class="cmd-count">' + (r.count || 0) + '</span></div>';
  }).join('');
}

function renderStatsCats(d) {
  var el = document.getElementById('stats-categories'); if (!el) return;
  var cats = d.category_counts || [];
  if (!cats.length) { el.innerHTML = '<p class="text-muted">No category data available.</p>'; return; }
  var max = Math.max.apply(null, cats.map(function (c) { return c.count || 0; }).concat([1]));
  var catColors = {
    'Storage & FS': '#fbbf24', 'System Services': '#d97706', 'Network': '#cdd8e6',
    'Package Management': '#f59e0b', 'Containers': '#eab308', 'VMs': '#b45309',
    'Proxmox': '#f59e0b', 'Monitoring': '#fde68a', 'Databases': '#a89880',
    'Security': '#dc2626', 'Version Control': '#cdd8e6', 'Scripting': '#fbbf24',
    'Task Scheduling': '#7a6d5e', 'System Info': '#d6b98c', 'System': '#b45309',
    'Editing': '#a89880', 'Utilities': '#7a6d5e'
  };
  el.innerHTML = cats.map(function (c) {
    var pct = max > 0 ? Math.round((c.count || 0) / max * 100) : 0;
    var color = catColors[c.category] || 'var(--text-muted)';
    return '<div class="cat-row"><span class="cat-swatch" style="background:' + color + '"></span>' +
      '<span class="cat-name">' + escapeHtml(c.category) + '</span><div class="cat-track"><div class="cat-fill" style="width:' + pct + '%;background:' + color + '"></div></div>' +
      '<span class="cat-pct">' + (c.pct != null ? c.pct : pct) + '%</span></div>';
  }).join('');
}

function renderStatsDenied(d) {
  var el = document.getElementById('stats-denied'); if (!el) return;
  var den = d.top_denied || [];
  if (!den.length) { el.innerHTML = '<p class="text-muted">No denied commands in this period.</p>'; return; }
  var max = Math.max.apply(null, den.map(function (c) { return c.count || 0; }).concat([1]));
  el.innerHTML = den.slice(0, 5).map(function (c, i) {
    var pct = max > 0 ? Math.round((c.count || 0) / max * 100) : 0;
    return '<div class="den-row"><span class="den-rank">' + (i + 1) + '</span><span class="den-cmd">' + escapeHtml(c.command) + '</span>' +
      '<div class="den-track"><div class="den-fill" style="width:' + pct + '%"></div></div><span class="den-count">' + (c.count || 0) + '</span></div>';
  }).join('');
}

function renderStatistics() {
  if (!_statsData) return;
  var d = _statsData;
  initTallyInteractions();
  renderRhythm(d);
  renderStatsKpis(d);
  renderStatsApprovals(d);
  renderStatsHygiene(d);
  renderStatsHeatmap(d);
  renderStatsNodes(d);
  renderStatsTop(d);
  renderStatsInnerTargets(d);
  renderStatsReadLane(d);
  renderStatsCats(d);
  renderStatsDenied(d);
}

/* ── X2: the runbooks page ────────────────────────────────────────────────
   F13 runbooks, searchable by the operator with the same ranking the agents
   get. Everything below is agent-authored text: every value goes through
   esc() before it reaches innerHTML. */
var _rbSearchTimer = null;
var _rbRows = [];
var _rbOpenId = 0;

function debouncedRunbookSearch() {
  clearTimeout(_rbSearchTimer);
  _rbSearchTimer = setTimeout(fetchRunbooks, 250);
}

function runbookQuery() {
  var q = document.getElementById('rb-search');
  var tag = document.getElementById('rb-tag');
  var retired = document.getElementById('rb-show-retired');
  var url = '/api/runbooks?include_retired=' + (retired && retired.checked ? 1 : 0);
  if (q && q.value.trim()) url += '&q=' + encodeURIComponent(q.value.trim());
  if (tag && tag.value.trim()) url += '&tag=' + encodeURIComponent(tag.value.trim());
  return url;
}

async function fetchRunbooks() {
  var holder = document.getElementById('rb-results');
  try {
    var res = await authFetch(runbookQuery());
    if (!res.ok) throw new Error('HTTP ' + res.status);
    var data = await res.json();
    _rbRows = data.runbooks || [];
    renderRunbookList(_rbRows);
  } catch (e) {
    if (holder) holder.innerHTML = '<p class="text-warning">Could not load runbooks: ' + esc(e.message) + '</p>';
  }
}

function runbookTagsHtml(tags) {
  return (tags || []).map(function(t) {
    return '<span class="rb-tag">' + esc(t) + '</span>';
  }).join('');
}

function renderRunbookList(rows) {
  var holder = document.getElementById('rb-results');
  var count = document.getElementById('rb-count');
  if (count) count.textContent = rows.length + (rows.length === 1 ? ' runbook' : ' runbooks');
  var tags = {};
  rows.forEach(function(r) { (r.tags || []).forEach(function(t) { tags[t] = 1; }); });
  var dl = document.getElementById('rb-tags');
  if (dl) {
    dl.innerHTML = Object.keys(tags).sort().map(function(t) {
      return '<option value="' + esc(t) + '"></option>';
    }).join('');
  }
  if (!holder) return;
  holder.innerHTML = runbookListHtml(rows);
}

function runbookListHtml(rows) {
  if (!rows || !rows.length) {
    return '<p class="text-muted">No runbooks match. Agents get one by promoting a confirmed conclusion.</p>';
  }
  var now = Math.floor(Date.now() / 1000);
  return rows.map(function(r) {
    var retired = !!r.retired;
    var detail = [];
    if (r.step_count != null) detail.push(r.step_count + ' step' + (r.step_count === 1 ? '' : 's'));
    if (r.created_at) detail.push(formatAgo(now - r.created_at));
    if (r.session_id) detail.push('session ' + esc(String(r.session_id).substring(0, 8)));
    return '<div class="rb-row' + (retired ? ' rb-retired' : '') + '" role="button" tabindex="0" ' +
        'onclick="openRunbook(' + idJs(r.id) + ')" ' +
        'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openRunbook(' + idJs(r.id) + ');}">' +
      '<div class="rb-row-main">' +
        '<div class="rb-row-title">' + esc(r.title || '(untitled)') +
          (retired ? ' <span class="rb-retired-chip">retired</span>' : '') + '</div>' +
        '<div class="rb-row-when">' + esc(r.when_to_use || '') + '</div>' +
        '<div class="rb-row-meta">' + runbookTagsHtml(r.tags) +
          '<span class="text-muted">' + detail.join(' \u00b7 ') + '</span></div>' +
      '</div>' +
      '<button class="btn btn-muted btn-xs" onclick="event.stopPropagation();' +
        'toggleRunbookRetired(' + idJs(r.id) + ',' + (retired ? 'false' : 'true') + ')">' +
        (retired ? 'Restore' : 'Retire') + '</button>' +
    '</div>';
  }).join('');
}

function runbookStepHtml(step, index) {
  if (!step || typeof step !== 'object') return '';
  if (step.kind === 'command') {
    var host = step.host ? '<span class="rb-step-host">' + esc(step.host) + '</span>' : '';
    var bd = (step.breakdown && step.breakdown.steps) ? bdBlockHtml(
      { id: 'rb-step-' + index, command: step.command || '', breakdown: step.breakdown },
      { raw: false }) : '';
    return '<div class="rb-step">' +
      '<div class="rb-step-head">' + host + '<code class="rb-step-cmd">' + esc(step.command || '') + '</code></div>' +
      (bd ? '<div class="rb-step-bd">' + bd + '</div>' : '') +
      (step.note ? '<div class="rb-step-note">' + esc(step.note) + '</div>' : '') +
    '</div>';
  }
  if (step.kind === 'tool') {
    return '<div class="rb-step">' +
      '<div class="rb-step-head"><span class="rb-step-host">' + esc(step.integration || '') + '</span>' +
      '<code class="rb-step-cmd">' + esc(step.tool || '') + '</code>' +
      '<span class="text-muted">' + esc(step.method || '') + ' ' + esc(step.path || '') + '</span></div>' +
      (step.note ? '<div class="rb-step-note">' + esc(step.note) + '</div>' : '') +
    '</div>';
  }
  return '<div class="rb-step"><div class="rb-step-note">' + esc(step.text || '') + '</div></div>';
}

function renderRunbook(rb) {
  var body = document.getElementById('runbook-modal-body');
  var actions = document.getElementById('runbook-modal-actions');
  var title = document.getElementById('runbook-modal-title');
  if (!body) return;
  if (title) title.textContent = 'Runbook #' + rb.id;
  body.innerHTML = runbookDetailHtml(rb);
  if (actions) {
    actions.innerHTML = '<button class="btn btn-muted btn-sm" onclick="closeRunbook()">Close</button>' +
      '<button class="btn btn-sm" onclick="toggleRunbookRetired(' + idJs(rb.id) + ',' +
        (rb.retired ? 'false' : 'true') + ')">' + (rb.retired ? 'Restore' : 'Retire') + '</button>';
  }
}

function runbookDetailHtml(rb) {
  var created = rb.created_at
    ? new Date(rb.created_at * 1000).toLocaleString() : '';
  var meta = [];
  if (created) meta.push('created ' + esc(created));
  if (rb.session_id) meta.push('session ' + esc(String(rb.session_id).substring(0, 8)));
  if (rb.conclusion_id) meta.push('conclusion #' + esc(rb.conclusion_id));
  var steps = (rb.steps || []);
  var dead = (rb.dead_ends || []);
  return '<h3 class="rb-title">' + esc(rb.title || '(untitled)') +
      (rb.retired ? ' <span class="rb-retired-chip">retired</span>' : '') + '</h3>' +
    '<p class="text-muted">' + meta.join(' \u00b7 ') + '</p>' +
    (rb.when_to_use ? '<p class="rb-when">' + esc(rb.when_to_use) + '</p>' : '') +
    '<div class="rb-row-meta">' + runbookTagsHtml(rb.tags) + '</div>' +
    '<h4 class="rb-h">Steps (' + steps.length + ')</h4>' +
    (steps.length ? steps.map(runbookStepHtml).join('') : '<p class="text-muted">No steps recorded.</p>') +
    (dead.length ? '<h4 class="rb-h">Known dead ends</h4>' +
      dead.map(function(d) {
        return '<div class="rb-dead-end">' + esc(d.note || '') +
          (d.ref ? ' <span class="text-muted">(' + esc(d.ref) + ')</span>' : '') + '</div>';
      }).join('') : '');
}

async function openRunbook(id) {
  _rbOpenId = id;
  var body = document.getElementById('runbook-modal-body');
  if (body) body.innerHTML = '<p class="text-muted">Loading\u2026</p>';
  document.getElementById('runbook-modal').classList.remove('hidden');
  try {
    var res = await authFetch('/api/runbooks/' + encodeURIComponent(id));
    if (!res.ok) throw new Error(res.status === 404 ? 'not found (or retired)' : 'HTTP ' + res.status);
    var rb = await res.json();
    if (_rbOpenId !== id) return;             // reopened for another runbook
    renderRunbook(rb);
  } catch (e) {
    if (body) body.innerHTML = '<p class="text-warning">Could not open the runbook: ' + esc(e.message) + '</p>';
  }
}

function closeRunbook() {
  _rbOpenId = 0;
  document.getElementById('runbook-modal').classList.add('hidden');
}

async function toggleRunbookRetired(id, retire) {
  var ok = await customConfirm(
    retire
      ? 'Agents will no longer find this runbook. Nothing is deleted and you can restore it.'
      : 'Agents will find this runbook again.',
    { title: retire ? 'Retire runbook #' + id + '?' : 'Restore runbook #' + id + '?',
      confirmLabel: retire ? 'Retire' : 'Restore',
      danger: retire });
  if (!ok) return;
  try {
    var res = await authFetch('/api/runbooks/' + encodeURIComponent(id) + '/retire', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ retired: !!retire })
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    showToast('Runbook #' + id + (retire ? ' retired' : ' restored'), 'success');
    fetchRunbooks();
    if (_rbOpenId === id) openRunbook(id);
  } catch (e) {
    showToast('Could not change the runbook: ' + e.message, 'error');
  }
}

/* ── F14: the inventory ──────────────────────────────────────────────────
   One search box over the cached map: a service, host, container, IP, FQDN,
   vmid or MAC. Every source chip carries its own staleness, and an IP that
   two sources name differently is shown as a conflict rather than resolved
   silently. All agent/upstream text is escaped. */
var _invTimer = null;

function debouncedInventorySearch() {
  clearTimeout(_invTimer);
  _invTimer = setTimeout(function () {
    var box = document.getElementById('inv-search');
    fetchInventory(box ? box.value : '');
  }, 250);
}

async function fetchInventory(q) {
  try {
    var url = '/api/inventory' + (q ? '?q=' + encodeURIComponent(q) : '');
    var res = await authFetch(url);
    if (!res.ok) return;
    renderInventory(await res.json());
  } catch (e) {}
}

function invSourceChips(sources) {
  return (sources || []).map(function (s) {
    var label = s.label || s.source || '';
    return '<span class="inv-source' + (s.stale ? ' stale' : '') + '" title="' +
      escapeHtml((s.stale ? 'stale — ' : '') + 'from ' + (s.source || label)) + '">' +
      escapeHtml(label) + (s.stale ? ' \u00b7 stale' : '') + '</span>';
  }).join('');
}

function invEntityHtml(entity) {
  var facts = [];
  var add = function (key, values) {
    if (!values || !values.length) return;
    facts.push('<span class="inv-k">' + key + '</span> ' +
      values.map(function (v) { return escapeHtml(String(v)); }).join(', '));
  };
  add('ip', entity.ips);
  add('name', entity.fqdns);
  add('host', entity.hosts);
  add('guest', entity.vmids);
  add('url', entity.urls);
  if (entity.extra && entity.extra.mac) add('mac', [entity.extra.mac]);
  var conflicts = (entity.conflicts || []).map(function (c) {
    return '<div class="inv-conflict">' + escapeHtml(String(c.ip || '')) +
      ' is claimed by ' + (c.names || []).map(function (n) {
        return escapeHtml(String(n));
      }).join(' / ') + '</div>';
  }).join('');
  return '<div class="inv-entity' + (entity.stale ? ' stale' : '') + '" data-od-id="inventory-entity">' +
    '<div class="inv-head"><span class="inv-name">' + escapeHtml(entity.name || 'unnamed') + '</span>' +
    '<span class="inv-chips">' + invSourceChips(entity.sources) + '</span></div>' +
    (facts.length ? '<div class="inv-facts">' + facts.join(' \u00b7 ') + '</div>' : '') +
    conflicts + '</div>';
}

function renderInventory(data) {
  data = data || {};
  var results = document.getElementById('inv-results');
  var sourcesEl = document.getElementById('inv-sources');
  var refreshed = document.getElementById('inv-refreshed');
  var box = document.getElementById('inv-interval');
  if (box && !box.dataset.dirty && data.interval_minutes) box.value = data.interval_minutes;
  if (refreshed) {
    refreshed.textContent = data.refreshed_at
      ? ('refreshed ' + formatTime(data.refreshed_at)) : 'never refreshed';
  }
  if (sourcesEl) {
    sourcesEl.innerHTML = Object.keys(data.sources || {}).sort().map(function (source) {
      var s = data.sources[source] || {};
      return '<span class="inv-source' + (s.stale ? ' stale' : '') + '" title="' +
        escapeHtml(s.error || '') + '">' + escapeHtml(s.label || source) +
        ' <b>' + (Number(s.items) || 0) + '</b>' + (s.stale ? ' \u00b7 stale' : '') + '</span>';
    }).join('');
  }
  if (!results) return;
  var items = data.items || [];
  results.innerHTML = items.length
    ? items.map(invEntityHtml).join('')
    : '<p class="text-muted">Nothing matches yet. Configure an integration and press \u201cRefresh now\u201d.</p>';
}

async function refreshInventoryNow() {
  var btn = document.getElementById('inv-refresh-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Refreshing\u2026'; }
  try {
    var res = await authFetch('/api/inventory/refresh', { method: 'POST' });
    var data = await res.json();
    showToast('Inventory refreshed (' + (Number(data.sources) || 0) + ' source(s), ' +
      (Number(data.duration_ms) || 0) + ' ms)', 'success');
    var box = document.getElementById('inv-search');
    fetchInventory(box ? box.value : '');
  } catch (e) { showToast('Could not refresh the inventory', 'error'); }
  if (btn) { btn.disabled = false; btn.textContent = 'Refresh now'; }
}

async function saveInventoryInterval() {
  var box = document.getElementById('inv-interval');
  var minutes = box ? parseInt(box.value, 10) : 15;
  try {
    var res = await authFetch('/api/inventory/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ interval_minutes: minutes }) });
    var data = await res.json();
    if (!res.ok) { showToast(data.detail || 'Could not save the interval', 'error'); return; }
    if (box) { box.value = data.interval_minutes; box.dataset.dirty = ''; }
    showToast('Inventory refreshes every ' + data.interval_minutes + ' minutes', 'success');
  } catch (e) { showToast('Could not save the interval', 'error'); }
}

// ── Gateway Health Checker ────────────────────────────────────────────
async function checkGatewayHealth() {
  try {
    const res = await fetch('/api/gateways'); const data = await res.json();
    // Detect offline transitions for notifications (uses the server-derived
    // `online`, i.e. the same DISCONNECTED_AFTER definition as the table).
    var nextOffline = new Set();
    data.forEach(function(g) {
      if (!gwIsOnline(g) && !g.updating) nextOffline.add(g.ip);
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
    if (label) {
      label.textContent = data.version || '?';
      if (data.dev_mode) {
        label.innerHTML = escapeHtml(data.version || '?') + ' <span class="dev-badge">DEV</span>';
      }
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
  refreshSessionOverrides();
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
let _integrationHealth = {};

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
  seerr:       { label: 'Seerr', seedable: true,
                 auth_type: 'header', auth_header: 'X-Api-Key',
                 secret_hint: 'Seerr API key',
                 guidance: 'API key via X-Api-Key header. Base URL is https://<seerr>/api/v1. Fully curated (no generic passthrough); request_create is the only write and runs immediately — Seerr applies its own request approval.' },
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
  bazarr:      { label: 'Bazarr', seedable: true,
                 auth_type: 'header', auth_header: 'X-API-KEY',
                 secret_hint: 'Bazarr API key (32 hex)',
                 guidance: 'API key via X-API-KEY header (Bazarr auth_method apikey; the key is in the container config at /config/config/config.yaml). Base URL is the origin, e.g. http://<ip>:6767 — the API lives under /api/... (no /api/vX). Read tools run immediately; every write (including the generic write) is approval-gated because a write can trigger subtitle downloads.' },
  npm:         { label: 'Nginx Proxy Manager', seedable: true,
                 auth_type: 'session', auth_header: '',
                 secret_hint: 'NPM user password',
                 guidance: 'Session auth (JWT + CSRF). Create a dedicated NPM admin user; Client ID = its email, Client Secret = its password, Token URL = http://<ip>:81/api/tokens. Base URL is http://<ip>:81/api. Fully curated; all writes require approval.' },
  truenas:     { label: 'TrueNAS', seedable: false,
                 guidance: 'Not yet supported — configure manually.' },
  webfetch:    { label: 'Web fetch', seedable: true,
                 auth_type: 'none', auth_header: '', no_secret: true,
                 no_base_url: true, no_tls_toggle: true, no_gate: true,
                 no_test: true, name_hint: 'web',
                 guidance: 'Mediated outbound fetch for the household agent. No upstream base URL or credential. Name it "web" so the MCP tool is web_fetch_url; then Seed it, enable it, and configure the per-agent URL allowlist in Controls → Web Fetch Allowlist. Fail-closed: an empty allowlist denies every fetch.' },
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
  const verifyRow = verifyTls ? verifyTls.closest('label') : null;
  const gateMode = document.getElementById('int-gate-mode');
  const gateRow = gateMode ? gateMode.closest('label') : null;
  const baseUrl = document.getElementById('int-base-url');
  const nameInput = document.getElementById('int-name');
  const isOAuth2 = profile.auth_type === 'oauth2' || profile.auth_type === 'session';
  if (guidance) {
    guidance.textContent = profile.guidance || '';
    guidance.classList.toggle('hidden', !profile.guidance);
  }
  if (oauthFields) oauthFields.classList.toggle('hidden', !isOAuth2);
  if (baseUrl) {
    const noBase = !!profile.no_base_url;
    baseUrl.classList.toggle('hidden', noBase);
    baseUrl.disabled = noBase;
    if (noBase) baseUrl.value = 'https://invalid.local';
    else if (baseUrl.value === 'https://invalid.local') baseUrl.value = '';
  }
  if (profile.name_hint && !_editingIntegration && nameInput && !nameInput.value.trim()) {
    nameInput.value = profile.name_hint;
  }
  if (secret) secret.classList.toggle('hidden', isOAuth2 || !!profile.no_secret);
  if (verifyRow) verifyRow.classList.toggle('hidden', !!profile.no_tls_toggle);
  if (gateRow) gateRow.classList.toggle('hidden', !!profile.no_gate);
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

var _agentsData = [];
var _agentPolicy = null;
var _agentPolicyTools = [];
var _webFetchPolicies = [];
var _wfDeny = [];

function _wfSplit(value) {
  return (value || '').split(/[\n,]+/).map(function(s) { return s.trim(); }).filter(Boolean);
}

async function initWebFetchPolicyPanel() {
  await fetchAgentTokens();
  await fetchWebFetchPolicies();
  loadWebFetchPolicy();
}

async function fetchWebFetchPolicies() {
  try {
    const res = await authFetch('/api/web-fetch-policies');
    if (!res.ok) return;
    const data = await res.json();
    _webFetchPolicies = data.policies || [];
    refreshWebFetchPrincipals();
  } catch(e) {}
}

function refreshWebFetchPrincipals() {
  const sel = document.getElementById('wf-principal');
  if (!sel) return;
  const names = new Set();
  (_agentsData || []).forEach(function(a) { if (a.name) names.add(a.name); });
  (_webFetchPolicies || []).forEach(function(p) { if (p.principal) names.add(p.principal); });
  if (!names.size) names.add('household');
  const options = Array.from(names).sort();
  const current = sel.value;
  const preferred = options.find(function(n) { return /household/i.test(n); }) || options[0] || 'household';
  sel.innerHTML = options.map(function(n) {
    return '<option value="' + esc(n) + '">' + esc(n) + '</option>';
  }).join('');
  sel.value = names.has(current) ? current : preferred;
}

async function loadWebFetchPolicy() {
  const sel = document.getElementById('wf-principal');
  const principal = sel ? sel.value : '';
  const status = document.getElementById('wf-policy-status');
  if (!principal) {
    if (status) status.textContent = 'Create an agent token in Integrations first.';
    return;
  }
  try {
    /* Read the whole list and pick our principal out of it. Asking for
       /api/web-fetch-policies/<principal> returns 404 when the agent simply has
       no policy yet — a normal state, but it logged a console error on every
       visit to Controls. The list endpoint always answers 200. */
    const res = await authFetch('/api/web-fetch-policies');
    if (res.ok) {
      const all = (await res.json()).policies || [];
      const p = all.filter(function (x) { return x.principal === principal; })[0];
      if (!p) {
        _wfDeny = [];
        document.getElementById('wf-enabled').checked = false;
        document.getElementById('wf-allow').value = '';
        if (status) status.textContent = 'No policy yet — save one to enable.';
      } else {
        _wfDeny = p.deny || [];
        document.getElementById('wf-enabled').checked = !!p.enabled;
        document.getElementById('wf-allow').value = (p.allow || []).join('\n');
        if (status) status.textContent = 'Loaded.';
      }
    } else {
      if (status) status.textContent = 'Load failed.';
    }
    loadWebFetchStats(principal);
  } catch(e) {
    if (status) status.textContent = 'Load failed: ' + e.message;
  }
}

async function loadWebFetchStats(principal) {
  const el = document.getElementById('wf-stats');
  if (!el) return;
  try {
    const res = await authFetch('/api/web-fetch-stats' + (principal ? '?principal=' + encodeURIComponent(principal) : ''));
    if (!res.ok) { el.textContent = ''; return; }
    const s = await res.json();
    const c = s.counters || {};
    el.textContent = 'Attempts: ' + (s.total || 0) +
      ' · ok ' + (c.fetch_ok || 0) +
      ' · allowlist ' + (c.fetch_denied_allowlist || 0) +
      ' · ssrf ' + (c.fetch_denied_ssrf || 0) +
      ' · redirect ' + (c.fetch_denied_redirect || 0) +
      ' · upstream ' + (c.fetch_error_upstream || 0) +
      ' · rate ' + (c.fetch_rate_limited || 0);
  } catch(e) { el.textContent = ''; }
}

async function saveWebFetchPolicy() {
  const principal = document.getElementById('wf-principal').value || '';
  if (!principal) { showToast('Select an agent token first', 'error'); return; }
  const enabled = document.getElementById('wf-enabled').checked;
  const allow = _wfSplit(document.getElementById('wf-allow').value);
  const status = document.getElementById('wf-policy-status');
  try {
    const res = await authFetch('/api/web-fetch-policies/' + encodeURIComponent(principal), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled, allow: allow, deny: _wfDeny })
    });
    const data = await res.json().catch(function() { return {}; });
    if (!res.ok) {
      if (status) status.textContent = 'Save failed: ' + (data.detail || 'error');
      showToast('Save failed: ' + (data.detail || 'error'), 'error');
      return;
    }
    _webFetchPolicies = _webFetchPolicies.filter(function(p) { return p.principal !== data.principal; });
    _webFetchPolicies.push(data);
    if (status) status.textContent = 'Saved.';
    showToast('Web fetch policy saved', 'success');
    if (enabled && !allow.length) {
      showToast('Enabled with an empty allowlist — every fetch will be denied', 'error');
    }
    loadWebFetchStats(principal);
    refreshWebFetchPrincipals();
    fetchAgentTokens();
  } catch(e) {
    if (status) status.textContent = 'Save failed: ' + e.message;
  }
}

async function deleteWebFetchPolicy(btn) {
  const principal = document.getElementById('wf-principal').value || '';
  if (!principal) { showToast('Select an agent token first', 'error'); return; }
  if (!(await inlineConfirm(btn, { question: 'Delete this policy?', label: 'Delete' }))) return;
  try {
    const res = await authFetch('/api/web-fetch-policies/' + encodeURIComponent(principal), { method: 'DELETE' });
    if (!res.ok && res.status !== 404) { showToast('Delete failed', 'error'); return; }
    _webFetchPolicies = _webFetchPolicies.filter(function(p) { return p.principal !== principal; });
    _wfDeny = [];
    document.getElementById('wf-enabled').checked = false;
    document.getElementById('wf-allow').value = '';
    const status = document.getElementById('wf-policy-status');
    if (status) status.textContent = 'Deleted.';
    refreshWebFetchPrincipals();
    loadWebFetchStats(principal);
    fetchAgentTokens();
  } catch(e) { showToast('Delete failed: ' + e.message, 'error'); }
}

async function fetchAgentTokens() {
  const el = document.getElementById('agent-token-list');
  if (!el) return;
  try {
    const res = await authFetch('/api/agents');
    if (!res.ok) return;
    const agents = await res.json();
    _agentsData = agents;
    if (!(_webFetchPolicies || []).length) { await fetchWebFetchPolicies(); }
    (agents || []).forEach(function(a) {
      var name = (a.name || '').toLowerCase();
      a.web_fetch = (_webFetchPolicies || []).find(function(p) {
        return (p.principal || '').toLowerCase() === name;
      }) || null;
    });
    var hud = document.getElementById('agents-hud-sum');
    if (hud) hud.innerHTML = '<span><span class="n">' + agents.length + '</span> token' + (agents.length === 1 ? '' : 's') + '</span>';
    refreshWebFetchPrincipals();
    if (!agents.length) { el.innerHTML = '<p class="text-muted">No agent tokens yet.</p>'; return; }
    el.innerHTML = agents.map(function(a) {
      var used = a.last_used_at ? new Date(a.last_used_at * 1000).toLocaleString() : 'never';
      var bits = agentSummaryBits(a);
      return '<div class="agent-row' + (a.revoked ? ' agent-row-revoked' : '') + '" ' +
          'onclick="openAgentDetail(' + idJs(a.id) + ')" role="button" tabindex="0" ' +
          'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openAgentDetail(' + idJs(a.id) + ');}">' +
        '<div class="agent-row-main">' +
          '<div class="agent-row-name">' + esc(a.name) +
            (a.revoked ? ' <span class="text-danger">(revoked)</span>' : ' <span class="agent-on">active</span>') + '</div>' +
          '<div class="text-xs text-muted">last used: ' + esc(used) + '</div>' +
          '<div class="agent-row-bits">' + bits.join('') + '</div>' +
        '</div>' +
        '<div class="flex items-center gap-1" onclick="event.stopPropagation()">' +
        '<button onclick="openAgentDetail(' + idJs(a.id) + ')" class="btn btn-xs">Open</button>' +
        (a.revoked ? '' : '<button onclick="revokeAgentToken(this, ' + idJs(a.id) + ')" class="btn btn-xs btn-muted">Revoke</button>') +
        '<button onclick="deleteAgentToken(this, ' + idJs(a.id) + ')" class="btn btn-xs btn-muted">Delete</button></div></div>';
    }).join('');
  } catch(e) {}
}

/* X4: the four numbers that say what a token may do, at a glance. */
function agentSummaryBits(agent) {
  var p = agent.policy || {};
  var bits = [];
  if ((p.tools_mode || 'all') === 'allowlist') {
    var n = (p.tools || []).length;
    bits.push('<span class="agent-bit">tools ' + n + '</span>');
  } else {
    bits.push('<span class="agent-bit">all tools</span>');
  }
  bits.push('<span class="agent-bit">' + (p.rate_limit_per_min || 0) + '/min</span>');
  var hosts = p.ssh_hosts_allow || [];
  var any = hosts.indexOf('*') !== -1;
  var ssh = any ? 'SSH any gateway'
    : hosts.length ? 'SSH ' + hosts.length + ' gateway' + (hosts.length === 1 ? '' : 's')
    : 'SSH off';
  bits.push('<span class="agent-bit' + (any ? ' warn' : '') + '">' + esc(ssh) +
    (hosts.length ? ' \u00b7 ' + esc(p.ssh_mode || 'approval') : '') + '</span>');
  var wf = agent.web_fetch;
  bits.push('<span class="agent-bit' + (wf && wf.enabled ? '' : ' muted') + '">web ' +
    (wf && wf.enabled ? ((wf.allow || []).length + ' domain' + ((wf.allow || []).length === 1 ? '' : 's')) : 'off') + '</span>');
  return bits;
}

function backToAgents() {
  var detail = document.getElementById('agent-detail');
  var list = document.getElementById('agents-list-view');
  if (detail) detail.classList.add('hidden');
  if (list) list.classList.remove('hidden');
  _agentDetailId = null;
  fetchAgentTokens();
}

async function openAgentDetail(id) {
  const agent = (_agentsData || []).find(function(a) { return a.id === id; });
  if (!agent) return;
  try {
    const [pRes, tRes] = await Promise.all([
      authFetch('/api/agents/' + id + '/policy'),
      authFetch('/api/agents/' + id + '/available-tools')
    ]);
    if (!pRes.ok || !tRes.ok) { showToast('Failed to load agent policy', 'error'); return; }
    _agentPolicy = await pRes.json();
    const tools = await tRes.json();
    _agentPolicyTools = tools.integrations || [];
    if (!_agentSshGateways().length) {
      try {
        const gRes = await authFetch('/api/gateways');
        if (gRes.ok) _allGateways = await gRes.json();
      } catch(e) {}
    }
    _agentDetailId = id;
    renderAgentPolicy(agent);
    renderAgentDetailHeader(agent);
    var list = document.getElementById('agents-list-view');
    var detail = document.getElementById('agent-detail');
    if (list) list.classList.add('hidden');
    if (detail) detail.classList.remove('hidden');
    switchView('agents');
    // Web fetch is per principal: point its existing panel at this agent.
    refreshWebFetchPrincipals();
    var wf = document.getElementById('wf-principal');
    if (wf) { wf.value = agent.name; loadWebFetchPolicy(); }
    renderAgentActivity(agent);
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

/* Kept for older call sites; the detail page replaced the modal. */
function openAgentPolicy(id) { return openAgentDetail(id); }

function renderAgentDetailHeader(agent) {
  var name = document.getElementById('agent-detail-name');
  if (name) name.textContent = agent.name || 'Agent';
  var meta = document.getElementById('agent-detail-meta');
  if (meta) {
    var used = agent.last_used_at ? new Date(agent.last_used_at * 1000).toLocaleString() : 'never';
    meta.innerHTML = 'token id ' + Number(agent.id) + ' \u00b7 created ' +
      (agent.created_at ? new Date(agent.created_at * 1000).toLocaleString() : 'unknown') +
      ' \u00b7 last used ' + esc(used) +
      (agent.revoked ? ' \u00b7 <span class="text-danger">revoked</span>' : '');
  }
  var actions = document.getElementById('agent-detail-actions');
  if (actions) {
    actions.innerHTML = (agent.revoked ? '' :
        '<button onclick="revokeAgentToken(this, ' + idJs(agent.id) + ')" class="btn btn-sm btn-muted">Revoke token</button>') +
      '<button onclick="deleteAgentToken(this, ' + idJs(agent.id) + ')" class="btn btn-sm btn-deny">Delete token</button>';
  }
}

function closeAgentPolicyModal() {
  backToAgents();
}

function _splitList(value) {
  return (value || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean);
}

/* The policy modal is three panes, not one long scroll: the tool chips on
   Tools, the argument rules on Rules, raw JSON on Advanced. The selection is
   held in a Set so filtering and per-integration All/None can redraw the
   chips without losing what is ticked. */
var _agentDetailId = null;
var _agentPolicySelected = null;
var _agentPolicyMode = 'all';
var _agentPolicyTab = 'tools';
var _agentPolicyFilter = '';

function renderAgentPolicy(agent) {
  const p = _agentPolicy || {};
  document.getElementById('agent-policy-title').textContent = agent.name || 'Agent';
  _agentPolicySelected = new Set(p.tools || []);
  _agentPolicyFilter = '';

  const ha = (p.arg_policy || {})['home_assistant_call_service'] || {};
  document.getElementById('agent-ha-domain-allow').value = (ha.domain_allow || []).join(', ');
  document.getElementById('agent-ha-domain-deny').value = (ha.domain_deny || []).join(', ');
  document.getElementById('agent-ha-service-deny').value = (ha.service_deny || []).join(', ');
  setAgentHaApproval((p.approval || {})['home_assistant_call_service'] || 'inherit');
  document.getElementById('agent-rate-limit').value = p.rate_limit_per_min || 0;
  document.getElementById('agent-policy-json').value = JSON.stringify({
    arg_policy: p.arg_policy || {},
    approval: p.approval || {}
  }, null, 2);

  const filterEl = document.getElementById('pol-tool-filter');
  if (filterEl) filterEl.value = '';
  agentPolicyFilter('');
  _agentSshHosts = (p.ssh_hosts_allow || []).slice();
  const anyBox = document.getElementById('agent-ssh-any-confirm');
  if (anyBox) anyBox.classList.add('hidden');
  setAgentSshMode(p.ssh_mode || 'approval');
  renderAgentSshHosts();
  setAgentPolicyTab('tools');
  setAgentToolsMode(p.tools_mode === 'allowlist' ? 'allowlist' : 'all');
}

/* F12: the SSH tab. The host list is held as the stored strings (gateway IPs
   or hostnames, or '*'); a gateway is ticked when its IP or hostname is in
   the list, and ticking adds its IP. Entries that match no current gateway
   are kept and shown, so a save never drops them silently. */
var _agentSshHosts = [];
var _agentSshMode = 'approval';
var AGENT_SSH_MODE_HINTS = {
  read_only: 'Only plain reads are sent; anything that writes is refused before it reaches the gateway.',
  approval: 'Every command asks for approval, even ones the gateway would allow. The safest choice.',
  normal: 'The gateway decides as for any SSH command: allowlisted commands run, the rest ask.'
};

function setAgentSshMode(mode) {
  _agentSshMode = AGENT_SSH_MODE_HINTS[mode] ? mode : 'approval';
  document.querySelectorAll('#agent-ssh-mode-seg button').forEach(function(btn) {
    const on = btn.getAttribute('data-val') === _agentSshMode;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const hint = document.getElementById('agent-ssh-mode-hint');
  if (hint) hint.textContent = AGENT_SSH_MODE_HINTS[_agentSshMode];
  updateAgentSshCount();
}

function _agentSshGateways() {
  var gws = (_gatewaysData && _gatewaysData.length) ? _gatewaysData : (_allGateways || []);
  return gws.filter(function(g) { return g && g.ip; }).slice().sort(function(a, b) {
    return (a.hostname || a.ip).localeCompare(b.hostname || b.ip);
  });
}

function _agentSshHas(value) {
  var v = (value || '').toLowerCase();
  return !!v && _agentSshHosts.some(function(h) { return h.toLowerCase() === v; });
}

function renderAgentSshHosts() {
  const box = document.getElementById('agent-ssh-hosts');
  if (!box) return;
  const any = _agentSshHas('*');
  const anyEl = document.getElementById('agent-ssh-any');
  if (anyEl) anyEl.checked = any;
  const gws = _agentSshGateways();
  box.classList.toggle('is-any', any);
  box.innerHTML = gws.length ? gws.map(function(g) {
    const on = _agentSshHas(g.ip) || _agentSshHas(g.hostname);
    return '<label class="pol-check"><input type="checkbox" ' + (on ? 'checked ' : '') +
      'onchange="toggleAgentSshHost(' + escJs(g.ip) + ', this.checked)">' +
      '<span>' + esc(g.hostname || g.ip) + ' <span class="pol-check-ip">' + esc(g.ip) + '</span></span></label>';
  }).join('') : '<p class="pol-hint">No gateways loaded. Open the Gateways page once, then reopen this policy.</p>';
  const known = {};
  gws.forEach(function(g) {
    known[(g.ip || '').toLowerCase()] = 1;
    if (g.hostname) known[g.hostname.toLowerCase()] = 1;
  });
  const extra = _agentSshHosts.filter(function(h) { return h !== '*' && !known[h.toLowerCase()]; });
  const extraEl = document.getElementById('agent-ssh-extra');
  if (extraEl) extraEl.textContent = extra.length
    ? 'Also kept (not a current gateway): ' + extra.join(', ')
    : '';
  updateAgentSshCount();
}

function toggleAgentSshHost(ip, on) {
  const g = _agentSshGateways().find(function(x) { return x.ip === ip; }) || { ip: ip };
  const drop = [(g.ip || '').toLowerCase(), (g.hostname || '').toLowerCase()];
  _agentSshHosts = _agentSshHosts.filter(function(h) { return drop.indexOf(h.toLowerCase()) === -1; });
  if (on) _agentSshHosts.push(g.ip);
  renderAgentSshHosts();
}

/* Security BF1: '*' also covers every gateway enrolled later, so ticking it
   asks first, in words; unticking does not. */
function toggleAgentSshAny(on) {
  const box = document.getElementById('agent-ssh-any-confirm');
  if (on && !_agentSshHas('*')) {
    const anyEl = document.getElementById('agent-ssh-any');
    if (anyEl) anyEl.checked = false;
    if (box) box.classList.remove('hidden');
    return;
  }
  if (box) box.classList.add('hidden');
  _agentSshHosts = _agentSshHosts.filter(function(h) { return h !== '*'; });
  if (on) _agentSshHosts.push('*');
  renderAgentSshHosts();
}

function confirmAgentSshAny(yes) {
  const box = document.getElementById('agent-ssh-any-confirm');
  if (box) box.classList.add('hidden');
  if (!yes) return;
  _agentSshHosts = _agentSshHosts.filter(function(h) { return h !== '*'; });
  _agentSshHosts.push('*');
  renderAgentSshHosts();
}

function updateAgentSshCount() {
  const el = document.getElementById('pol-tab-ssh-n');
  if (!el) return;
  if (_agentSshHas('*')) { el.textContent = 'any gateway'; return; }
  const n = _agentSshHosts.length;
  el.textContent = n ? n + ' gateway' + (n === 1 ? '' : 's') : 'off';
}

function setAgentPolicyTab(name) {
  _agentPolicyTab = name;
  ['tools', 'rules', 'ssh', 'advanced'].forEach(function(t) {
    const pane = document.getElementById('pol-pane-' + t);
    const btn = document.getElementById('pol-tab-' + t);
    if (pane) pane.classList.toggle('hidden', t !== name);
    if (btn) {
      btn.classList.toggle('active', t === name);
      btn.setAttribute('aria-selected', t === name ? 'true' : 'false');
    }
  });
}

function setAgentToolsMode(mode) {
  _agentPolicyMode = mode === 'allowlist' ? 'allowlist' : 'all';
  document.querySelectorAll('.pol-mode').forEach(function(btn) {
    const on = btn.getAttribute('data-mode') === _agentPolicyMode;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const list = document.getElementById('pol-tools-pane');
  const note = document.getElementById('pol-all-note');
  const catalog = document.getElementById('pol-catalog');
  if (list) list.classList.toggle('hidden', _agentPolicyMode !== 'allowlist');
  if (note) note.classList.toggle('hidden', _agentPolicyMode === 'allowlist');
  if (catalog) catalog.classList.toggle('hidden', _agentPolicyMode === 'allowlist');
  renderAgentPolicyTools();
  renderAgentPolicyCatalog();
  updateAgentPolicyCounts();
}

/* What "all tools" actually means, in numbers: the catalog behind the mode. */
function renderAgentPolicyCatalog() {
  const host = document.getElementById('pol-catalog');
  if (!host) return;
  host.innerHTML = (_agentPolicyTools || []).map(function(group) {
    const tools = (group.tools || []).filter(function(t) { return t.enabled !== false; });
    const writes = tools.filter(function(t) { return !t.read_only; }).length;
    return '<div class="pol-cat-row">' +
      '<span class="pol-cat-name">' + esc(group.integration) + '</span>' +
      '<span class="pol-cat-n">' + tools.length + ' tools · ' + writes + ' write' + (writes === 1 ? '' : 's') + '</span>' +
    '</div>';
  }).join('');
}

function setAgentHaApproval(value) {
  const input = document.getElementById('agent-ha-approval');
  if (input) input.value = value;
  document.querySelectorAll('#agent-ha-approval-seg button').forEach(function(btn) {
    const on = btn.getAttribute('data-val') === value;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function _agentPolicyMatches(t) {
  if (!_agentPolicyFilter) return true;
  const q = _agentPolicyFilter.toLowerCase();
  return (t.name || '').toLowerCase().indexOf(q) !== -1 ||
    (t.description || '').toLowerCase().indexOf(q) !== -1;
}

function _agentPolicyVisible() {
  const out = [];
  _agentPolicyTools.forEach(function(group) {
    (group.tools || []).forEach(function(t) { if (_agentPolicyMatches(t)) out.push(t); });
  });
  return out;
}

function agentPolicyChip(t) {
  const on = _agentPolicySelected && _agentPolicySelected.has(t.name);
  const cls = 'pol-chip' + (on ? ' on' : '') + (t.read_only ? ' is-read' : ' is-write') +
    (t.enabled === false ? ' is-off' : '');
  const title = t.enabled === false
    ? 'Disabled in Integrations — not callable until it is enabled there'
    : (t.description || (t.read_only ? 'Read-only call' : 'Changes state — waits for approval'));
  return '<label class="' + cls + '" title="' + esc(title) + '">' +
    '<input type="checkbox" class="pol-chip-in" data-tool="' + esc(t.name) + '"' + (on ? ' checked' : '') +
      ' onchange="agentPolicyToggle(this)">' +
    '<span class="pol-chip-name">' + esc(t.name) + '</span>' +
    (t.enabled === false ? '<span class="pol-off">off</span>' : '') +
  '</label>';
}

function renderAgentPolicyTools() {
  const host = document.getElementById('agent-policy-tools');
  if (!host) return;
  if (!_agentPolicyTools || !_agentPolicyTools.length) {
    host.innerHTML = '<p class="pol-hint">No integrations are enabled, so there are no tools to allow.</p>';
    return;
  }
  let html = '';
  _agentPolicyTools.forEach(function(group, gi) {
    const tools = (group.tools || []).filter(_agentPolicyMatches);
    if (!tools.length) return;
    const sel = tools.filter(function(t) { return _agentPolicySelected && _agentPolicySelected.has(t.name); }).length;
    html += '<div class="pol-group">' +
      '<div class="pol-group-head">' +
        '<span class="pol-group-name">' + esc(group.integration) + '</span>' +
        '<span class="pol-group-count">' + sel + ' / ' + tools.length + '</span>' +
        '<button class="pol-mini" onclick="agentPolicyGroupAll(' + gi + ', true)">All</button>' +
        '<button class="pol-mini" onclick="agentPolicyGroupAll(' + gi + ', false)">None</button>' +
      '</div>' +
      '<div class="pol-chips">' + tools.map(agentPolicyChip).join('') + '</div>' +
    '</div>';
  });
  host.innerHTML = html || '<p class="pol-hint">No tools match that filter.</p>';
}

function agentPolicyToggle(cb) {
  if (!_agentPolicySelected) _agentPolicySelected = new Set();
  const name = cb.getAttribute('data-tool');
  if (cb.checked) _agentPolicySelected.add(name); else _agentPolicySelected.delete(name);
  const chip = cb.closest('.pol-chip');
  if (chip) chip.classList.toggle('on', cb.checked);
  const group = cb.closest('.pol-group');
  if (group) {
    const boxes = group.querySelectorAll('input[data-tool]');
    const on = group.querySelectorAll('input[data-tool]:checked').length;
    const count = group.querySelector('.pol-group-count');
    if (count) count.textContent = on + ' / ' + boxes.length;
  }
  updateAgentPolicyCounts();
}

function agentPolicyGroupAll(gi, on) {
  const group = _agentPolicyTools[gi];
  if (!group) return;
  if (!_agentPolicySelected) _agentPolicySelected = new Set();
  (group.tools || []).forEach(function(t) {
    if (on) _agentPolicySelected.add(t.name); else _agentPolicySelected.delete(t.name);
  });
  renderAgentPolicyTools();
  updateAgentPolicyCounts();
}

function agentPolicySelectAll(on) {
  if (!_agentPolicySelected) _agentPolicySelected = new Set();
  _agentPolicyVisible().forEach(function(t) {
    if (on) _agentPolicySelected.add(t.name); else _agentPolicySelected.delete(t.name);
  });
  renderAgentPolicyTools();
  updateAgentPolicyCounts();
}

function agentPolicyFilter(value) {
  _agentPolicyFilter = value || '';
  /* With a filter active these buttons act on what is shown, so say so. */
  const allBtn = document.getElementById('pol-select-all-btn');
  const clearBtn = document.getElementById('pol-clear-btn');
  if (allBtn) allBtn.textContent = _agentPolicyFilter ? 'Select shown' : 'Select all';
  if (clearBtn) clearBtn.textContent = _agentPolicyFilter ? 'Clear shown' : 'Clear';
  renderAgentPolicyTools();
}

function updateAgentPolicyCounts() {
  let total = 0, selected = 0, writes = 0;
  (_agentPolicyTools || []).forEach(function(group) {
    (group.tools || []).forEach(function(t) {
      if (t.enabled === false) return;
      total++;
      if (_agentPolicySelected && _agentPolicySelected.has(t.name)) {
        selected++;
        if (!t.read_only) writes++;
      }
    });
  });

  const count = document.getElementById('pol-tools-count');
  const tabN = document.getElementById('pol-tab-tools-n');
  const allSub = document.getElementById('pol-mode-all-sub');
  const foot = document.getElementById('pol-foot-note');
  if (allSub) allSub.textContent = 'Everything ' + (_agentPolicyTools || []).length + ' integrations expose — ' + total + ' tools.';

  if (_agentPolicyMode === 'all') {
    if (count) { count.textContent = 'All ' + total + ' tools are callable'; count.classList.remove('warn'); }
    if (tabN) tabN.textContent = 'All';
    if (foot) foot.textContent = 'Changes apply to new calls only.';
    return;
  }

  if (count) {
    count.textContent = selected + ' of ' + total + ' allowed · ' + writes + ' write' + (writes === 1 ? '' : 's');
    count.classList.toggle('warn', selected === 0);
  }
  if (tabN) tabN.textContent = selected + ' / ' + total;
  if (foot) {
    foot.textContent = selected === 0
      ? 'No tool is allowed — this token will be refused on every call.'
      : 'Changes apply to new calls only.';
    foot.classList.toggle('warn', selected === 0);
  }
}

function collectAgentPolicy() {
  const mode = _agentPolicyMode === 'allowlist' ? 'allowlist' : 'all';
  const tools = mode === 'allowlist'
    ? Array.from(_agentPolicySelected || []).sort()
    : [];
  let advanced;
  try {
    advanced = JSON.parse(document.getElementById('agent-policy-json').value || '{}');
  } catch(e) {
    showToast('Advanced JSON is invalid: ' + e.message, 'error');
    return null;
  }
  const argPolicy = Object.assign({}, advanced.arg_policy || {});
  const approval = Object.assign({}, advanced.approval || {});

  const ha = Object.assign({}, argPolicy['home_assistant_call_service'] || {});
  const allow = _splitList(document.getElementById('agent-ha-domain-allow').value);
  const deny = _splitList(document.getElementById('agent-ha-domain-deny').value);
  const serviceDeny = _splitList(document.getElementById('agent-ha-service-deny').value);
  if (allow.length) ha.domain_allow = allow; else delete ha.domain_allow;
  if (deny.length) ha.domain_deny = deny; else delete ha.domain_deny;
  if (serviceDeny.length) ha.service_deny = serviceDeny; else delete ha.service_deny;
  if (Object.keys(ha).length) argPolicy['home_assistant_call_service'] = ha;
  else delete argPolicy['home_assistant_call_service'];

  const haApproval = document.getElementById('agent-ha-approval').value;
  if (haApproval && haApproval !== 'inherit') approval['home_assistant_call_service'] = haApproval;
  else delete approval['home_assistant_call_service'];

  const rate = parseInt(document.getElementById('agent-rate-limit').value || '0', 10);
  return {
    tools_mode: mode,
    tools: tools,
    arg_policy: argPolicy,
    approval: approval,
    rate_limit_per_min: isNaN(rate) ? 0 : rate,
    ssh_hosts_allow: _agentSshHosts.slice(),
    ssh_mode: _agentSshMode
  };
}

/* X4: each section sends only its own keys. The API keeps every field the
   payload omits, so Save tools cannot clear SSH, the rate limit or the
   argument rules. */
function agentSectionPayload(kind) {
  if (kind === 'advanced') {
    let doc;
    try {
      doc = JSON.parse(document.getElementById('agent-policy-json').value || '{}');
    } catch(e) {
      showToast('Advanced JSON is invalid: ' + e.message, 'error');
      return null;
    }
    return { arg_policy: doc.arg_policy || {}, approval: doc.approval || {} };
  }
  const all = collectAgentPolicy();
  if (!all) return null;
  if (kind === 'tools') return { tools_mode: all.tools_mode, tools: all.tools };
  if (kind === 'rules') return {
    arg_policy: all.arg_policy, approval: all.approval,
    rate_limit_per_min: all.rate_limit_per_min
  };
  if (kind === 'ssh') return { ssh_hosts_allow: all.ssh_hosts_allow, ssh_mode: all.ssh_mode };
  return all;
}

async function saveAgentSection(kind) {
  if (!_agentPolicy || !_agentPolicy.agent_id) { showToast('No agent selected', 'error'); return; }
  const payload = agentSectionPayload(kind);
  if (!payload) return;
  const labels = { tools: 'Tools', rules: 'Rules', ssh: 'SSH', advanced: 'Advanced' };
  try {
    const res = await authFetch('/api/agents/' + _agentPolicy.agent_id + '/policy', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    const data = await res.json().catch(function() { return {}; });
    if (!res.ok) { showToast('' + (data.detail || 'Failed'), 'error'); return; }
    _agentPolicy = data;
    // Re-render from what the server stored, keeping the tab the operator is on.
    const agent = (_agentsData || []).find(function(a) { return a.id === _agentPolicy.agent_id; }) || {};
    renderAgentPolicy(Object.assign({}, agent, {name: (agent.name || document.getElementById('agent-policy-title').textContent)}));
    setAgentPolicyTab(_agentPolicyTab);
    showToast((labels[kind] || 'Section') + ' saved', 'success');
    fetchAgentTokens();
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function saveAgentPolicy() {
  return saveAgentSection(null);
}

function openHistoryForSession(sid) {
  if (!sid) return;
  setHistorySession(sid, true);
  switchView('history');
  fetchRequests();
  fetchIntegrationCalls();
}

/* X4: read-only. The same rows History shows, filtered to this agent: the
   sessions it opened (requests are joined through those ids), the newest
   commands, the newest proxied calls. Nothing here writes. */
async function renderAgentActivity(agent) {
  const box = document.getElementById('agent-activity-body');
  if (!box) return;
  box.innerHTML = '<p class="text-muted">Loading\u2026</p>';
  try {
    const res = await authFetch('/api/agents/' + agent.id + '/activity');
    if (!res.ok) { box.innerHTML = '<p class="text-muted">Could not load activity.</p>'; return; }
    const data = await res.json();
    const sessions = data.sessions || [];
    const reqs = data.requests || [];
    const calls = data.calls || [];
    if (!sessions.length && !reqs.length && !calls.length) {
      box.innerHTML = '<p class="text-muted">No activity yet. Commands and calls appear here once this agent runs something.</p>';
      return;
    }
    let html = '';
    const open = sessions.filter(function(x) { return !x.ended_at; });
    if (sessions.length) {
      html += '<div class="agent-act-sec"><div class="agent-act-h">Sessions' +
        (open.length ? ' <span class="agent-bit">' + open.length + ' open</span>' : '') + '</div>' +
        sessions.map(function(x) {
          const sid = String(x.id || '');
          const goal = x.goal ? esc(x.goal) : '<span class="text-muted">(no goal)</span>';
          return '<div class="agent-act-row">' +
            '<button class="agent-act-link" onclick="openHistoryForSession(' + escJs(sid) + ')" title="Open in History">' +
              esc(sid.substring(0, 12)) + '</button>' +
            '<span class="agent-act-main">' + goal + '</span>' +
            '<span class="agent-bit' + (x.ended_at ? ' muted' : '') + '">' +
              (x.ended_at ? 'closed' : 'open') + '</span>' +
            '<span class="text-muted">' + formatAgo(Math.floor(Date.now() / 1000) - (x.last_seen || 0)) + ' ago</span>' +
          '</div>';
        }).join('') + '</div>';
    }
    if (reqs.length) {
      html += '<div class="agent-act-sec"><div class="agent-act-h">Last commands</div>' +
        reqs.map(function(r) {
          const host = r.hostname || r.target_ip || '';
          return '<div class="agent-act-row">' +
            '<span class="text-muted">' + formatTime(r.created_at) + '</span>' +
            '<span class="agent-act-main"><code>' + esc(r.command || '') + '</code></span>' +
            '<span class="agent-bit">' + esc(r.status || '') + '</span>' +
            '<span class="text-muted">' + esc(host) + '</span>' +
          '</div>';
        }).join('') + '</div>';
    }
    if (calls.length) {
      html += '<div class="agent-act-sec"><div class="agent-act-h">Last proxied calls</div>' +
        calls.map(function(c) {
          return '<div class="agent-act-row">' +
            '<span class="text-muted">' + formatTime(c.created_at) + '</span>' +
            '<span class="agent-act-main">' + esc(c.integration || '') + ' / ' + esc(c.tool || '') + '</span>' +
            '<span class="agent-bit">' + esc(c.approval || c.outcome || '') + '</span>' +
            '<span class="text-muted">' + esc(c.method || '') + ' ' + esc(c.path || '') + '</span>' +
          '</div>';
        }).join('') + '</div>';
    }
    box.innerHTML = html;
  } catch(e) {
    box.innerHTML = '<p class="text-muted">Could not load activity.</p>';
  }
}

async function revokeAgentToken(btn, id) {
  if (!(await inlineConfirm(btn, { question: 'Revoke this token? It stops working now.', label: 'Revoke' }))) return;
  try {
    const res = await authFetch('/api/agents/' + id + '/revoke', { method: 'POST' });
    if (!res.ok) { showToast('Failed to revoke', 'error'); return; }
    showToast('Agent token revoked', 'success');
    fetchAgentTokens();
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
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

async function deleteAgentToken(btn, id) {
  if (!(await inlineConfirm(btn, { question: 'Delete this token?', label: 'Delete' }))) return;
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
    var profile = INTEGRATION_PROFILES[i.kind] || {};
    var seedable = !!profile.seedable;
    var seedBtn = seedable
      ? '<button onclick="seedTools(' + escJs(i.name) + ')" class="btn btn-xs btn-muted" title="Seed the seed catalog for this integration">Seed</button>'
      : '';
    var label = profile.label || i.kind || 'custom';
    var urlLabel = profile.no_base_url ? 'mediated fetch (no base URL)' : i.base_url;
    var testBtn = profile.no_test
      ? ''
      : '<button onclick="testIntegration(' + escJs(i.name) + ')" class="btn btn-xs btn-muted" title="Run a read call to verify the connection">Test</button>';
    var gateLabel = profile.no_gate ? 'n/a' : (i.gate_mode || 'destructive');
    return '<div class="item-card selectable' + (selected ? ' selected' : '') + '" onclick="selectIntegration(' + escJs(i.name) + ')">' +
      '<div class="flex items-center justify-between gap-2">' +
      '<span class="text-sm font-semibold ' + active + '"><span class="i-star' + (i.enabled ? '' : ' off') + '"></span> ' + esc(i.name) + '</span>' +
      '<span class="i-kind">' + esc(label) + '</span></div>' +
      '<div class="i-url">' + esc(urlLabel) + '</div>' +
      '<div class="i-meta">auth: ' + esc(i.auth_type) + ' &middot; gate: ' + esc(gateLabel) + ' &middot; mcp: ' + esc(i.mcp_mode || 'joined') + '</div>' +
      integrationHealthLine(i.name) +
      '<div class="flex flex-wrap gap-1 mt-2" onclick="event.stopPropagation()">' +
      '<button onclick="editIntegration(' + escJs(i.name) + ')" class="btn btn-xs btn-muted" title="Edit base URL / secret">Edit</button>' +
      testBtn +
      seedBtn +
      '<button onclick="deleteIntegration(this, ' + escJs(i.name) + ')" class="btn btn-xs btn-muted">Delete</button>' +
      '</div></div>';
  }).join('');
}

async function fetchIntegrationList() {
  try {
    const res = await authFetch('/api/integrations');
    if (!res.ok) return;
    _integrationsData = await res.json();
    try {
      var hr = await authFetch('/api/integrations/health');
      if (hr.ok) _integrationHealth = (await hr.json()).health || {};
    } catch (e) { _integrationHealth = {}; }
    renderIntegrationList();
    syncMcpStars();
  } catch(e) {}
}

/* One line per integration: is it answering, how often, how fast. */
function integrationHealthLine(name) {
  var h = _integrationHealth[name];
  if (!h || !h.last_call_at) {
    return '<div class="i-health"><span class="ih-dot never"></span>never called</div>';
  }
  var now = Math.floor(Date.now() / 1000);
  var ago = formatAgo(now - h.last_call_at);
  var bad = h.last_outcome !== 'ok' || h.failed > 0;
  var bits = [h.calls + ' call' + (h.calls === 1 ? '' : 's') + ' / 24h'];
  if (h.failed) bits.push(h.failed + ' failed');
  if (h.avg_latency_ms != null) bits.push(h.avg_latency_ms + 'ms avg');
  return '<div class="i-health' + (bad ? ' bad' : '') + '">' +
    '<span class="ih-dot ' + (h.last_outcome === 'ok' ? 'ok' : 'bad') + '"></span>' +
    '<span class="ih-last">last call ' + ago + '</span>' +
    '<span class="ih-stats">' + escapeHtml(bits.join(' · ')) + '</span>' +
  '</div>';
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
  const kind = document.getElementById('int-kind').value;
  const profile = INTEGRATION_PROFILES[kind] || {};
  const name = document.getElementById('int-name').value.trim();
  const baseUrl = document.getElementById('int-base-url').value.trim();
  const needsBase = !profile.no_base_url;
  if (!name || (needsBase && !baseUrl)) {
    showToast(needsBase ? 'Name and base URL are required' : 'Name is required', 'error');
    return;
  }
  const payload = {
    base_url: needsBase ? baseUrl : 'https://invalid.local',
    kind: kind,
    auth_type: profile.auth_type || document.getElementById('int-auth-type').value,
    auth_header_name: profile.auth_type
      ? (profile.auth_header || '')
      : document.getElementById('int-auth-header').value.trim(),
  };
  if (kind === 'webfetch') payload.retention_days = 90;
  const secret = document.getElementById('int-secret').value;
  if (secret && !profile.no_secret) payload.secret = secret;
  const clientId = document.getElementById('int-client-id').value;
  const clientSecret = document.getElementById('int-client-secret').value;
  const tokenUrl = document.getElementById('int-token-url').value;
  if (clientId) payload.client_id = clientId;
  if (clientSecret) payload.client_secret = clientSecret;
  if (tokenUrl) payload.token_url = tokenUrl;
  const verifyTls = document.getElementById('int-verify-tls');
  if (verifyTls && !profile.no_tls_toggle) payload.verify_tls = !verifyTls.checked;
  const gateMode = document.getElementById('int-gate-mode');
  if (gateMode && !profile.no_gate) payload.gate_mode = gateMode.value;
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

async function deleteIntegration(btn, name) {
  if (!(await inlineConfirm(btn, { question: 'Delete this integration and its tools?', label: 'Delete' }))) return;
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
    var type = t.read_only ? 'read' : 'mut';
    return '<div class="tool">' +
      '<div class="t-top"><span class="t-name">' + esc(t.name) + '</span><span class="t-type ' + type + '">' + (t.read_only ? 'read' : 'mutating') + '</span></div>' +
      '<div class="t-desc">' + esc(t.description || '') + '</div>' +
      '<div class="t-foot"><span class="t-method">' + esc(t.method) + '</span>' +
      '<button class="tgl' + (t.enabled ? ' on' : '') + '" onclick="toggleTool(' + idJs(t.id) + ', ' + (t.enabled ? 'false' : 'true') + ')" title="Enable/Disable"></button></div></div>';
  }).join('');
}

async function bulkSetTools(enabled) {
  if (!_selectedIntegration) return;
  const label = enabled ? 'Enable' : 'Disable';
  if (!(await customConfirm('They take effect for agents on the next /reload-mcp.', { title: label + ' all tools for ' + _selectedIntegration + '?', confirmLabel: label, danger: false }))) return;
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
  playApprovalPulse();
  try {
    const res = await authFetch('/api/integration-calls/' + id + '/approve', { method: 'POST' });
    if (res.ok) { closePanelIfShowing('int-' + id); fetchRequests(); fetchIntegrationCalls(); showToast('Approved and executed', 'success'); }
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function denyIntegrationCall(id) {
  playApprovalPulse();
  try {
    const res = await authFetch('/api/integration-calls/' + id + '/deny', { method: 'POST' });
    if (res.ok) { closePanelIfShowing('int-' + id); fetchRequests(); }
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

let _callsSearchTimer = null;
let _callsPage = 1;
let _callsPageSize = 50;
let _callsSearch = '';
let _callsStart = null;
let _callsEnd = null;
let _callsApproval = '';

function approvalBadge(a) {
  if (a === 'approved') return '<span class="approval-badge approved" title="Required operator approval — you approved it">Approved</span>';
  if (a === 'denied') return '<span class="approval-badge denied" title="Required operator approval — you denied it">Denied</span>';
  if (a === 'auto') return '<span class="approval-badge auto" title="Mutating call that ran without operator approval">Auto</span>';
  return '<span class="text-muted">—</span>';
}

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

function onCallsApproval() {
  var sel = document.getElementById('calls-approval');
  _callsApproval = (sel && sel.value) || '';
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
    if (_callsApproval) params.push('approval=' + encodeURIComponent(_callsApproval));
    params.push('limit=' + _callsPageSize);
    params.push('offset=' + ((_callsPage - 1) * _callsPageSize));
    var url = '/api/integration-calls' + (params.length ? '?' + params.join('&') : '');
    const res = await authFetch(url);
    if (!res.ok) return;
    const data = await res.json();
    const calls = data.rows || [];
    const total = data.total || 0;
    if (!calls.length) {
      el.innerHTML = '<tr><td colspan="11" class="px-4 py-3 text-muted">No calls match.</td></tr>';
    } else {
      var names = _sessionNames || {};
      el.innerHTML = calls.map(function(c) {
        var when = new Date(c.created_at * 1000).toLocaleString();
        var ok = c.outcome === 'ok';
        var cls = ok ? 'text-success' : 'text-danger';
        var sid = c.session_id || '';
        var sCell = (sid && sid !== 'unknown')
          ? '<button class="c-session" data-sid="' + esc(sid) + '" title="Session ' + esc(sid) + (c.execution_id ? ' · exec ' + esc(c.execution_id) : '') + '">' + esc((names[sid] || {}).name || sid.slice(0, 8)) + '</button>'
          : '<span class="text-muted">—</span>';
        return '<tr>' +
          '<td class="text-muted">' + esc(when) + '</td>' +
          '<td>' + esc(c.integration) + '</td>' +
          '<td class="text-muted" title="' + esc(c.reason || '') + '">' + esc(c.tool || '') + '</td>' +
          '<td class="text-muted">' + esc(c.agent || '') + '</td>' +
          '<td>' + esc(c.method) + '</td>' +
          '<td class="text-muted break-all">' + esc(c.path) + '</td>' +
          '<td class="' + cls + '">' + (c.status_code || '—') + '</td>' +
          '<td class="text-right text-muted">' + (c.latency_ms == null ? '—' : c.latency_ms + 'ms') + '</td>' +
          '<td>' + sCell + '</td>' +
          '<td>' + approvalBadge(c.approval) + '</td>' +
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
/* Safe value for an inline JS call: JSON.stringify quotes/escapes, esc makes
   it safe inside a double-quoted HTML attribute (entities decode back before
   the JS runs, so esc() alone is not enough for a single-quoted JS literal). */
function escJs(v) { return esc(JSON.stringify(v)); }
/* Server ids are integers today; coerce before putting them in HTML/JS. */
function idJs(v) { return escJs(Number(v)); }
function idText(v) { return esc(Number(v)); }

/* ==============================================================
   FLEET RUN — SOLAR BROADCAST CONSOLE
   Orbits the gateways on rings around a dark core, maps real command
   results onto the nodes, lists transmissions, and streams per-node
   output in a bottom deck. Safety gates + API flow are untouched.
   ============================================================== */
var _orbRafOn = false, _orbSeenRun = {}, _fleetSelCmdId = null, _deckSig = '';
function fleetOrbitRun() {
  if (_orbRafOn) return;
  _orbRafOn = true;
  (function loop(ts) { fleetOrbitStep(ts); requestAnimationFrame(loop); })();
}
function fleetOrbitStep(ts) {
  var wrap = document.getElementById('nodes');
  if (!wrap) return;
  Array.prototype.forEach.call(wrap.children, function (n) {
    var o = n._o; if (!o || o.on === false) return;
    o.a = o.a0 + ts * 0.001 * o.speed;
    n.style.left = (50 + o.r * Math.cos(o.a)) + '%';
    n.style.top = (50 + o.r * Math.sin(o.a)) + '%';
  });
}
function orbName(h) { return h && String(h).indexOf('.') === -1 ? h : (h || '??'); }
function orbGwByIp(ip) {
  for (var i = 0; i < _fleetGateways.length; i++) if (_fleetGateways[i].ip === ip) return _fleetGateways[i];
  return null;
}
function orbPitch(ip) {
  var idx = -1;
  for (var i = 0; i < _fleetGateways.length; i++) if (_fleetGateways[i].ip === ip) { idx = i; break; }
  return idx < 0 ? 0 : idx % _fleetScale.length;
}
function fleetConsoleUpdate() {
  renderFleetTxRail();
  fleetAutoSelect();
  syncFleetDialState();
  renderFleetDeck();
  orbReceiptPings();
  syncFleetSkyFromResults();
}
function fleetAutoSelect() {
  if (!_fleetData.length) { _fleetSelCmdId = null; return; }
  if (_fleetSelCmdId != null && _fleetData.some(function (c) { return c.id === _fleetSelCmdId; })) return;
  var pool = _fleetData.filter(function (c) { return c.status === 'approved' || (c.results && c.results.length); });
  if (!pool.length) pool = _fleetData;
  _fleetSelCmdId = pool[pool.length - 1].id;
}

/* the header dial says what the fleet is doing right now */
function syncFleetDialState() {
  var el = document.getElementById('sky-state');
  if (!el) return;
  var live = null;
  (_fleetData || []).forEach(function (c) {
    var v = orbCmdVerdict(c);
    if (v.cls === 'live') live = v;
  });
  var sel = orbFindCmd(_fleetSelCmdId);
  var v = sel ? orbCmdVerdict(sel) : (live || null);
  el.textContent = v ? v.label : 'idle';
  el.style.color = v && v.cls === 'err' ? 'var(--state-denied)'
    : v && v.cls === 'part' ? 'var(--state-blocked)'
    : v && v.cls === 'ok' ? 'var(--state-approved)'
    : v ? 'var(--state-pending)' : '';
}

/* transmissions rail */
function orbCmdVerdict(c) {
  var rs = c.results || [];
  var term = rs.filter(function (r) { return ['success', 'failed', 'timeout', 'skipped'].indexOf(r.status) >= 0; });
  var run = rs.filter(function (r) { return r.status === 'running' || r.status === 'queued'; });
  if (c.status === 'denied') return { cls: 'den', label: 'denied' };
  if (c.status === 'pending') return { cls: 'live', label: 'awaiting poll' };
  if (c.status === 'approved' && !rs.length) return { cls: 'live', label: 'dispatched' };
  if (run.length || term.length !== rs.length) return { cls: 'live', label: 'running ' + term.length + '/' + rs.length };
  var bad = term.filter(function (r) { return r.status === 'failed' || r.status === 'timeout'; }).length;
  if (bad === 0) return { cls: 'ok', label: 'all clear' };
  return { cls: bad === term.length ? 'err' : 'part', label: bad === term.length ? 'failed' : 'partial · ' + bad };
}
function orbChip(r) {
  var cls = r.status === 'success' ? 'ok' : ((r.status === 'failed' || r.status === 'timeout') ? 'bad' : 'wait');
  return '<span class="orb-tx-n ' + cls + '"><span class="d"></span>' + escapeHtml(orbName(r.hostname || r.gateway_ip).slice(0, 2)) + '</span>';
}
function renderFleetTxRail() {
  var wrap = document.getElementById('fleet-tx-list');
  if (!wrap) return;
  if (!_fleetData.length) { wrap.innerHTML = '<div class="orb-empty">nothing broadcast yet —<br>queued commands land here once dispatched.<br>click a run to read its output below</div>'; return; }
  wrap.innerHTML = _fleetData.slice().reverse().slice(0, 12).map(function (c) {
    var v = orbCmdVerdict(c);
    var rs = c.results || [];
    var chips = rs.length
      ? rs.map(orbChip).join('')
      : (c.target_ips || []).slice(0, 8).map(function (ip) {
          var g = orbGwByIp(ip);
          return '<span class="orb-tx-n wait"><span class="d"></span>' + escapeHtml((g && g.hostname ? g.hostname : ip).slice(0, 2)) + '</span>';
        }).join('');
    var sel = _fleetSelCmdId === c.id ? ' active' : '';
    return '<div class="orb-tx' + sel + '" data-id="' + idText(c.id) + '">' +
      '<div class="orb-tx-top"><span class="orb-tx-id">#' + idText(c.id) + '</span><span class="orb-tx-cmd">' + escapeHtml(c.command) + '</span><span class="orb-tx-v ' + v.cls + '">' + v.label + '</span></div>' +
      '<div class="orb-tx-meta"><span>' + (c.created_at ? formatDateTime(c.created_at) : '') + '</span><span>' +
        (function (n) { return n + ' ear' + (n === 1 ? '' : 's'); })(rs.length || (c.target_ips || []).length) + '</span></div>' +
      '<div class="orb-tx-nodes">' + chips + '</div></div>';
  }).join('');
  wrap.querySelectorAll('.orb-tx').forEach(function (t) {
    t.addEventListener('click', function () {
      _fleetSelCmdId = parseInt(t.getAttribute('data-id'), 10);
      renderFleetDeck();
      renderFleetTxRail();
    });
  });
}

function orbFindCmd(id) {
  if (id == null) return null;
  for (var i = 0; i < _fleetData.length; i++) if (_fleetData[i].id === id) return _fleetData[i];
  return null;
}
/* output deck */
function renderFleetDeck() {
  var body = document.getElementById('deck-body'), head = document.getElementById('deck-head');
  var empty = document.getElementById('deck-empty');
  if (!body || !head) return;
  var c = orbFindCmd(_fleetSelCmdId);
  if (!c && _fleetData.length) { fleetAutoSelect(); c = orbFindCmd(_fleetSelCmdId); }
  if (!c) {
    body.querySelectorAll('.orb-dn').forEach(function (n) { n.remove(); });
    if (empty) empty.style.display = '';
    head.style.display = 'none';
    _deckSig = '';
    return;
  }
  if (empty) empty.style.display = 'none';
  head.style.display = 'flex';
  document.getElementById('deck-id').textContent = '#' + c.id;
  document.getElementById('deck-cmd').textContent = c.command;
  var rs = c.results || [];
  var entries = rs.length ? rs : (c.target_ips || []).map(function (ip) { var g = orbGwByIp(ip); return { gateway_ip: ip, hostname: g ? g.hostname : ip, status: 'queued' }; });
  var sig = String(c.id) + ':' + entries.map(function (r) { return r.status || 'q'; }).join(',');
  if (sig === _deckSig) { orbDeckRefresh(c, entries); return; }
  _deckSig = sig;
  body.querySelectorAll('.orb-dn').forEach(function (n) { n.remove(); });
  entries.forEach(function (r) {
    var ip = r.gateway_ip;
    var row = document.createElement('div');
    row.className = 'orb-dn wait';
    row.innerHTML = '<div class="orb-dn-top"><span class="dot"></span><span class="orb-dn-name">' + escapeHtml(orbName(r.hostname || ip)) + '</span>' +
      '<span class="orb-dn-st">listening…</span>' +
      '<span class="orb-dn-ops"><button class="op copy" title="Copy output">&#10697;</button><button class="op full" title="Full output">full</button></span></div>' +
      '<div class="orb-wait" data-role="wait">awaiting the wave…</div>' +
      '<div class="orb-out-row"><button class="orb-dn-toggle">Show output <span class="car">&#8964;</span></button><pre class="orb-dn-out"></pre></div>';
    body.appendChild(row);
    row._info = { cmdId: c.id, ip: ip };
    row.querySelector('.orb-dn-toggle').addEventListener('click', function (e) { e.stopPropagation(); orbOpenOut(row); });
    row.querySelector('.op.copy').addEventListener('click', function (e) { e.stopPropagation(); orbCopy(row); });
    row.querySelector('.op.full').addEventListener('click', function (e) { e.stopPropagation(); openFleetOutputModal(c.id, ip); });
  });
  orbDeckRefresh(c, entries);
}
function orbDeckRefresh(c, entries) {
  var body = document.getElementById('deck-body');
  var ok = 0, bad = 0, ans = 0, total = entries.length;
  var rs = c.results || [];
  body.querySelectorAll('.orb-dn').forEach(function (row) {
    var key = row._info ? row._info.ip : null;
    var r = null;
    for (var i = 0; i < rs.length; i++) if (rs[i].gateway_ip === key) { r = rs[i]; break; }
    var wait = row.querySelector('[data-role="wait"]'), st = row.querySelector('.orb-dn-st');
    row.classList.remove('wait', 'run', 'ok', 'bad', 'resolved');
    if (!r || r.status === 'queued' || r.status === '') {
      row.classList.add('wait');
      st.textContent = 'listening…'; if (wait) wait.textContent = 'awaiting the wave…';
    } else if (r.status === 'running') {
      row.classList.add('run');
      st.textContent = 'running…'; if (wait) wait.textContent = 'wave received — running…';
    } else if (r.status === 'success') {
      row.classList.add('ok', 'resolved'); st.textContent = 'exit 0'; ans++; ok++;
      if (wait) wait.textContent = '';
    } else if (r.status === 'failed' || r.status === 'timeout') {
      row.classList.add('bad', 'resolved'); st.textContent = r.status === 'timeout' ? 'timed out' : 'exit 1'; ans++; bad++;
      if (wait) wait.textContent = '';
    } else if (r.status === 'skipped') {
      row.classList.add('resolved'); st.textContent = 'skipped'; ans++;
      if (wait) wait.textContent = '';
    }
  });
  document.getElementById('deck-count').innerHTML = '<b>' + ans + '</b> / ' + total + ' answered';
  var v = document.getElementById('deck-verdict');
  if (ans < total) { v.className = 'orb-deck-verdict live'; v.textContent = ans ? 'running · ' + ans + '/' + total : 'awaiting poll'; }
  else if (bad > 0) { v.className = 'orb-deck-verdict ' + (bad === total ? 'err' : 'part'); v.textContent = bad === total ? 'failed' : 'partial · ' + bad; }
  else { v.className = 'orb-deck-verdict ok'; v.textContent = 'all clear'; }
}
function orbOpenOut(row) {
  var open = row.classList.contains('out-open');
  row.classList.toggle('out-open', !open);
  if (!open) {
    var pre = row.querySelector('.orb-dn-out');
    if (pre && !pre.dataset.loaded) orbLoadOut(row._info.cmdId, row._info.ip, pre);
  }
}
function orbLoadOut(cmdId, ip, pre) {
  pre.dataset.loaded = '1';
  pre.textContent = 'loading output…';
  fetch('/api/fleet/commands/' + cmdId + '/output/' + encodeURIComponent(ip)).then(function (r) { return r.json(); }).then(function (d) {
    pre.textContent = d && d.output ? d.output : '(no output)';
  }).catch(function () { pre.textContent = '(failed to load)'; });
}
function orbCopy(row) {
  var pre = row.querySelector('.orb-dn-out');
  var btn = row.querySelector('.op.copy');
  var doCopy = function (txt) {
    copyToClipboard(txt || '(no output)', true);
    if (btn) { btn.textContent = 'copied'; btn.classList.add('copied'); setTimeout(function () { btn.textContent = '&#10697;'; btn.classList.remove('copied'); }, 1400); }
  };
  if (pre && pre.dataset.loaded) { doCopy(pre.textContent); }
  else if (pre && row._info) { orbLoadOut(row._info.cmdId, row._info.ip, pre); setTimeout(function () { doCopy(pre.textContent); }, 400); }
  else { doCopy(''); }
}
function orbReceiptPings() {
  if (!_fleetData.length) return;
  var c = _fleetData[0];
  (c.results || []).forEach(function (r) {
    var k = c.id + ':' + r.gateway_ip;
    if ((r.status === 'running' || r.status === 'queued') && !_orbSeenRun[k]) {
      _orbSeenRun[k] = true;
      var el = _fleetNodeEls[r.gateway_ip];
      if (el) { el.classList.add('receipt'); setTimeout(function () { if (el) el.classList.remove('receipt'); }, 950); }
      fleetNodePing(orbPitch(r.gateway_ip));
    } else if (r.status === 'success' || r.status === 'failed' || r.status === 'timeout' || r.status === 'skipped') {
      delete _orbSeenRun[k];
    }
  });
}
var _orbBound = false;
function orbBind() {
  if (_orbBound) return;
  _orbBound = true;
  var inp = document.getElementById('fleet-cmd-input');
  if (inp) inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addFleetToQueue(); } });
}
orbBind();
document.addEventListener('click', function (e) {
  var t = e.target; if (!t || !t.closest) return;
  var btn = t.closest('.c-session');
  if (btn && typeof openSessionModal === 'function') {
    var sid = btn.getAttribute('data-sid');
    if (sid) openSessionModal(sid);
  }
});


// ── First-run walkthrough ─────────────────────────────────────────────
/* Content lives in ONB_STEPS / ONB_INTEGRATIONS below; the machinery is
   generic. docs/FIRST_RUN.md explains how to change it and
   scripts/check_onboarding.py validates the shape. */
var ONB_STEPS = [
  { id: 'key', label: 'Key', title: 'The key your agent holds',
    lede: 'A pair of keys: the agent keeps one, the dashboard gets the other.',
    render: onbStepKey },
  { id: 'gateway', label: 'Gateway', title: 'A gateway to guard',
    lede: 'One one-liner installs the policy gate on a host and starts its poller.',
    render: onbStepGateway },
  { id: 'agent', label: 'Agent', title: 'Let the agent in',
    lede: 'Point the agent at its private key, then begin every task with a signed session.',
    render: onbStepAgent },
  { id: 'integrations', label: 'Integrations', title: 'Beyond SSH',
    lede: 'The same gate for Proxmox, Home Assistant, Commute and your own APIs.',
    render: onbStepIntegrations }
];

var ONB_INTEGRATIONS = [
  { name: 'Commute', kind: 'commute', url: '/api/commute/due',
    blurb: 'Profiles, the day gate and notify slots live in Eshu; the scheduler only asks what is due.',
    tools: ['commute_check', 'commute_override', 'profiles'] },
  { name: 'Proxmox', kind: 'proxmox', url: 'https://proxmox.home.lan:8006/api2/json',
    blurb: 'Nodes, VMs and containers. Reads are free; start, stop and reboot wait for your approval.',
    tools: ['list nodes', 'vm status', 'start / stop'] },
  { name: 'Home Assistant', kind: 'ha', url: 'https://homeassistant.home.lan/api',
    blurb: 'Entities, states and services, with per-domain rules for what the agent may call.',
    tools: ['list entities', 'get state', 'call service'] },
  { name: 'Any REST API', kind: 'custom', url: 'https://…',
    blurb: 'A generic read/write floor for everything else. Credentials stay in Eshu.',
    tools: ['read', 'write'] }
];

var _onb = {
  step: 0, state: null, token: '', gateways: 0, gatewayList: [],
  keyValue: '', poll: null, busy: false, checked: false
};

async function maybeShowOnboarding() {
  if (_onb.checked) return;
  _onb.checked = true;
  try {
    var res = await authFetch('/api/onboarding');
    if (!res.ok) return;
    _onb.state = await res.json();
    if (_onb.state.show) openOnboarding(_onb.state.key_configured ? 1 : 0);
  } catch (e) { /* a missing walkthrough must never block the dashboard */ }
}

function openOnboarding(step) {
  var m = document.getElementById('onboarding-modal');
  if (!m) return;
  _onb.step = Math.max(0, Math.min(step || 0, ONB_STEPS.length - 1));
  _onb.token = '';
  m.classList.remove('hidden');
  m.removeAttribute('hidden');
  document.body.classList.add('onb-open');
  loadOnbKeys();
  loadOnbGateways();
  renderOnboarding();
  setTimeout(function () { var c = m.querySelector('.onb-card'); if (c) c.focus(); }, 30);
}

function closeOnboarding() {
  stopOnbPoll();
  var m = document.getElementById('onboarding-modal');
  if (m) { m.classList.add('hidden'); m.setAttribute('hidden', ''); }
  document.body.classList.remove('onb-open');
}

async function skipOnboarding() {
  try { await authFetch('/api/onboarding/done', { method: 'POST' }); } catch (e) {}
  closeOnboarding();
}

async function finishOnboarding() {
  try { await authFetch('/api/onboarding/done', { method: 'POST' }); } catch (e) {}
  closeOnboarding();
  showToast('The way is open', 'success');
}

function onbGo(i) { stopOnbPoll(); _onb.step = i; renderOnboarding(); }
function onbBack() { if (_onb.step > 0) onbGo(_onb.step - 1); }
function onbNext() {
  if (_onb.step >= ONB_STEPS.length - 1) finishOnboarding();
  else onbGo(_onb.step + 1);
}

function renderOnboarding() {
  var s = ONB_STEPS[_onb.step];
  var title = document.getElementById('onb-title');
  var lede = document.getElementById('onb-lede');
  var count = document.getElementById('onb-count');
  var rail = document.getElementById('onb-rail');
  var body = document.getElementById('onb-body');
  if (!s || !title || !body) return;
  title.textContent = s.title;
  lede.textContent = s.lede;
  count.textContent = (_onb.step + 1) + ' / ' + ONB_STEPS.length;
  rail.innerHTML = ONB_STEPS.map(function (st, i) {
    var cls = i === _onb.step ? 'active' : (i < _onb.step ? 'done' : '');
    return '<button class="onb-seg ' + cls + '" onclick="onbGo(' + i + ')"' +
      (i === _onb.step ? ' aria-current="step"' : '') + '>' +
      '<span class="onb-seg-line"></span>' +
      '<span class="onb-seg-n">0' + (i + 1) + '</span>' +
      '<span class="onb-seg-l">' + escapeHtml(st.label) + '</span></button>';
  }).join('');
  body.innerHTML = s.render();
  var back = document.getElementById('onb-back');
  var next = document.getElementById('onb-next');
  if (back) back.classList.toggle('hidden', _onb.step === 0);
  if (next) next.textContent = _onb.step === ONB_STEPS.length - 1 ? 'Finish' : 'Continue';
  body.classList.remove('onb-enter');
  void body.offsetWidth;
  body.classList.add('onb-enter');
}

function onbCmd(text) {
  return '<div class="onb-cmd"><code>' + escapeHtml(text) + '</code>' +
    '<button class="onb-copy" data-copy="' + encodeURIComponent(text) + '" onclick="onbCopy(this)">Copy</button></div>';
}

function onbCopy(el) {
  var text = decodeURIComponent(el.getAttribute('data-copy') || '');
  if (!text) return;
  copyToClipboard(text, true);
  var old = el.textContent;
  el.textContent = 'Copied';
  el.classList.add('onb-copied');
  setTimeout(function () { el.textContent = old; el.classList.remove('onb-copied'); }, 1400);
}

async function loadOnbKeys() {
  try {
    var r = await authFetch('/api/enroll/keys');
    if (!r.ok) return;
    var d = await r.json();
    _onb.keyValue = d.eshu_ssh_key || '';
    var inp = document.getElementById('onb-key-input');
    if (inp && _onb.keyValue) inp.value = _onb.keyValue;
  } catch (e) {}
}

async function loadOnbGateways() {
  try {
    var r = await authFetch('/api/gateways');
    if (!r.ok) return;
    var g = await r.json();
    _onb.gatewayList = Array.isArray(g) ? g : [];
    _onb.gateways = _onb.gatewayList.length;
  } catch (e) {}
}

function onbStepKey() {
  var saved = _onb.state && _onb.state.key_configured;
  return '<p class="onb-para">Run this on the machine where your agent runs. The <b>private</b> key stays there; only the <b>.pub</b> file comes here.</p>' +
    onbCmd('ssh-keygen -t ed25519 -f ~/.ssh/eshu_gateway -C "agent@eshu"') +
    '<div class="onb-row">' +
      '<input id="onb-key-input" class="onb-input" spellcheck="false" autocomplete="off" placeholder="ssh-ed25519 AAAA… agent@eshu" value="' + escapeHtml(_onb.keyValue || '') + '">' +
      '<button class="btn btn-xs" onclick="onbSaveKey()">Save key</button>' +
    '</div>' +
    (saved ? '<div class="onb-ok"><span class="onb-check">✓</span> Public key saved</div>' : '') +
    '<p class="onb-note">Only the public half is stored — the dashboard never sees the private key.</p>';
}

async function onbSaveKey() {
  var inp = document.getElementById('onb-key-input');
  var key = inp ? (inp.value || '').trim() : '';
  if (!key || key.indexOf('ssh-') !== 0 || key.length < 40) {
    showToast('Paste a public key (ssh-ed25519 …)', 'error');
    return;
  }
  try {
    var r = await authFetch('/api/enroll/keys', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ eshu_key: key })
    });
    if (!r.ok) throw new Error();
    _onb.keyValue = key;
    if (!_onb.state) _onb.state = {};
    _onb.state.key_configured = true;
    renderOnboarding();
    showToast('Public key saved', 'success');
  } catch (e) { showToast('Could not save the key', 'error'); }
}

function onbStepGateway() {
  var enrolled = _onb.gateways > 0;
  var out = '<p class="onb-para">Generate a one-time token, then run the command on the host you want to protect. It installs the gate and starts the poller.</p>';
  if (enrolled) {
    var names = _onb.gatewayList.slice(0, 3).map(function (g) { return g.hostname || g.ip; }).join(', ');
    out += '<div class="onb-ok"><span class="onb-check">✓</span> ' + escapeHtml(names) + ' enrolled</div>';
  }
  if (!_onb.token) {
    if (!enrolled) out += '<div class="onb-row"><button class="btn btn-xs" onclick="onbGenerateToken()">Generate token</button></div>';
    return out;
  }
  var cmd = 'curl -s "' + window.location.origin + '/api/enroll?token=' + _onb.token + '" | bash';
  /* Enrolled: the ✓ line above already names the gateway, so a second
     "Gateway enrolled" row would just repeat it. */
  out += onbCmd(cmd);
  if (!enrolled) out += '<div class="onb-status wait"><span class="onb-dot"></span>Waiting for the gateway to check in…</div>';
  out += '<p class="onb-note">The token is single-use and expires in 30 minutes. Run the command with sudo.</p>';
  return out;
}

async function onbGenerateToken() {
  if (_onb.busy) return;
  _onb.busy = true;
  try {
    var r = await authFetch('/api/enroll/generate', { method: 'POST' });
    var d = await r.json();
    _onb.token = d.token || '';
    renderOnboarding();
    startOnbPoll();
  } catch (e) { showToast('Could not generate a token', 'error'); }
  _onb.busy = false;
}

function startOnbPoll() {
  stopOnbPoll();
  _onb.poll = setInterval(async function () {
    try {
      var r = await authFetch('/api/gateways');
      var g = await r.json();
      if (Array.isArray(g) && g.length) {
        _onb.gatewayList = g;
        _onb.gateways = g.length;
        stopOnbPoll();
        if (_onb.step === 1) renderOnboarding();
      }
    } catch (e) {}
  }, 3000);
}

function stopOnbPoll() {
  if (_onb.poll) { clearInterval(_onb.poll); _onb.poll = null; }
}

function onbStepAgent() {
  var g = _onb.gatewayList[0] || {};
  var host = g.hostname || g.ip || '<gateway>';
  var cfg = 'Host eshu-*\n  User eshu-gateway\n  IdentityFile ~/.ssh/eshu_gateway\n  IdentitiesOnly yes';
  return '<p class="onb-para">The private key stays on the agent\'s machine — owned by its user, <code>chmod 600</code>.</p>' +
    onbCmd(cfg) +
    '<p class="onb-sub">Every task starts with a goal</p>' +
    onbCmd('session_begin(goal="turn on the patio lights", session_id="<chat id>")') +
    '<p class="onb-sub">Then every command carries the prefix</p>' +
    onbCmd('ssh eshu-gateway@' + host + ' "ESHU_SESSION_ID=… ESHU_SESSION_SIG=… uptime"') +
    '<p class="onb-note">The prefix is the id plus its signature. A gateway with <code>/etc/eshu-require-session</code> refuses a command that does not carry both.</p>';
}

function onbStepIntegrations() {
  return '<p class="onb-para">Beyond SSH, Eshu holds the credentials and the rules: reads run where you allow them, and anything that changes state waits for you. Add one in <b>Integrations</b>.</p>' +
    '<div class="onb-cards">' + ONB_INTEGRATIONS.map(onbIntegrationCard).join('') + '</div>' +
    '<div class="onb-row"><button class="btn btn-muted btn-xs" onclick="onbOpenIntegrations()">Open Integrations</button>' +
      '<span class="onb-note">Add one, seed its tools, then create an agent token.</span></div>';
}

function onbIntegrationCard(c) {
  return '<div class="onb-int">' +
    '<div class="onb-int-head"><span class="onb-int-name">' + escapeHtml(c.name) + '</span>' +
      '<span class="onb-int-kind">' + escapeHtml(c.kind) + '</span></div>' +
    '<div class="onb-int-url">' + escapeHtml(c.url) + '</div>' +
    '<div class="onb-int-blurb">' + escapeHtml(c.blurb) + '</div>' +
    '<div class="onb-int-tools">' + c.tools.map(function (t) { return '<span>' + escapeHtml(t) + '</span>'; }).join('') + '</div>' +
  '</div>';
}

function onbOpenIntegrations() {
  closeOnboarding();
  switchView('integrations');
}

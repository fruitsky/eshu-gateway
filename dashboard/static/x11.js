/* X11 Stage 3 — Agents and the rules for all agents.
 *
 * Every save is Check -> preview -> Apply. The preview is the server's own
 * plain-words diff, and Apply sends its preview_hash. risk_ack / resume_ack
 * are sent only when the preview asked, and only from an explicit checkbox.
 *
 * All state here is operator-side; no agent token is ever put in this file,
 * in a log line or in an audit row.
 */
(function () {
  'use strict';

  var S = {
    screen: 'agents',
    tab: 'agents',
    agents: [],
    agentsReady: false,
    agent: null,
    config: null,
    draft: null,
    tools: [],
    activity: null,
    policy: null,
    allowances: [],
    gateways: [],
    devices: [],
    labels: [],
    ceiling: null,
    protected: [],
    integrations: [],
    section: 'overview',
    notice: null,
    lastPreview: null,
    pending: null,
    newCreated: false,
    jsonText: '',
    jsonSource: null,
    jsonError: '',
    deviceSearch: '',
    deviceSettings: false,
    deviceOnlyPerm: false,
    toolFilter: '',
    toolOnlyAllowed: false,
    toolOpen: {},
    protectedEditing: false,
    protectedSettings: false,
    jsonChecked: false,
  };
  window.x11State = S;

  function el(id) { return document.getElementById(id); }
  function clone(v) { return JSON.parse(JSON.stringify(v == null ? null : v)); }
  function esc(v) { return window.esc ? window.esc(v) : String(v == null ? '' : v); }
  function attr(v) { return esc(v); }
  function root() { return el('x11-root'); }

  function api(url, opts) {
    return window.authFetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (!res.ok) {
          var err = new Error('request failed');
          err.status = res.status;
          err.detail = body && body.detail !== undefined ? body.detail : body;
          throw err;
        }
        return body;
      });
    });
  }

  function errText(err) {
    var d = (err && err.detail) || {};
    if (typeof d === 'string') return d;
    var msg = d.message || d.detail || (err && err.message) || 'Something went wrong.';
    var field = d.field ? d.field + ': ' : (d.fields ? d.fields.join(', ') + ': ' : '');
    return field + msg;
  }

  function toast(text, kind) {
    if (window.showToast) window.showToast(text, kind || 'success');
  }

  function setNotice(text, kind) {
    S.notice = text ? { text: text, kind: kind || '' } : null;
    var box = el('x11-notice');
    if (!box) return;
    box.textContent = text || '';
    box.dataset.kind = kind || '';
    box.hidden = !text;
  }

  function ago(ts) {
    if (!ts) return 'never';
    if (window.formatAgo) return window.formatAgo(Math.max(0, Math.floor(Date.now() / 1000) - ts)) + ' ago';
    return new Date(ts * 1000).toLocaleString();
  }
  function when(ts) {
    return ts ? new Date(ts * 1000).toLocaleDateString() : '';
  }

  function docOf(cfg) {
    return {
      schema_version: cfg.schema_version || 1,
      tools: clone(cfg.tools || {}),
      ssh: clone(cfg.ssh || {}),
      websites: clone(cfg.websites || {}),
      home_assistant: clone(cfg.home_assistant || { permissions: [] }),
    };
  }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  function toolList(integrations) {
    var out = [];
    (integrations || S.tools || []).forEach(function (group) {
      (group.tools || []).forEach(function (tool) {
        out.push(Object.assign({ integration: group.integration, kind: group.kind }, tool));
      });
    });
    return out;
  }

  function outcomeFor(name, tool, cfg) {
    var draft = cfg || S.draft;
    if (!draft) return 'none';
    var tools = draft.tools || {};
    var mode = tools.mode || 'all';
    var allowed = mode !== 'allowlist' || (tools.allow || []).indexOf(name) !== -1;
    if (!allowed) return 'none';
    var approval = (tools.approval || {})[name];
    if (approval === 'ungated') return 'run';
    if (approval === 'gated') return 'ask';
    return tool && tool.read_only ? 'run' : 'ask';
  }

  function countsFor(cfg, integrations) {
    var draft = cfg || S.draft;
    var tools = toolList(integrations);
    var total = tools.length;
    if ((draft.tools || {}).mode === 'allowlist') {
      var allowed = (draft.tools || {}).allow || [];
      total = tools.filter(function (t) { return allowed.indexOf(t.name) !== -1; }).length;
    }
    var ask = 0, run = 0;
    tools.forEach(function (t) {
      var o = outcomeFor(t.name, t, draft);
      if (o === 'ask') ask += 1;
      else if (o === 'run') run += 1;
    });
    return { total: total, ask: ask, run: run };
  }

  function permissions(cfg) {
    return (((cfg || S.draft || {}).home_assistant || {}).permissions) || [];
  }
  function pausedCount(cfg) {
    return permissions(cfg).filter(function (p) { return p.suspended_at; }).length;
  }
  function pendingOf(row) {
    try {
      var list = JSON.parse((row && row.pending_json) || '[]');
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function dismissedOf(row) {
    try {
      var list = JSON.parse((row && row.dismissed_json) || '[]');
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function waitingRows(agentId) {
    return (S.allowances || []).filter(function (r) {
      return Number(r.agent_id) === Number(agentId) && !Number(r.revoked_at) &&
        (pendingOf(r).length || dismissedOf(r).length);
    });
  }
  function waitingCount(agentId) {
    return waitingRows(agentId).reduce(function (n, r) {
      return n + pendingOf(r).length;
    }, 0);
  }
  function waitingNames(row) {
    return pendingOf(row).map(function (p) { return deviceName(p.entity_id); });
  }
  function dismissedNames(row) {
    return dismissedOf(row).map(function (p) { return deviceName(p.entity_id); });
  }
  function labelForRow(row) {
    try {
      var src = JSON.parse(row.conditions_json || '{}');
      var d = (src.domain && src.domain['in'] && src.domain['in'][0]) || '';
      var s = (src.service && src.service['in'] && src.service['in'][0]) || '';
      return actionWords(d, s);
    } catch (e) { return 'Permission'; }
  }
  function waitingRowHtml(r) {
    var pend = pendingOf(r);
    var dis = dismissedOf(r);
    var id = Number(r.id);
    var head;
    if (pend.length) {
      head = '<b>' + pend.length + ' new device' + (pend.length === 1 ? '' : 's') +
        '</b> for ' + esc(labelForRow(r)) + ': ' + esc(waitingNames(r).join(', '));
    } else {
      head = '<b>Dismissed</b> for ' + esc(labelForRow(r)) + ': ' +
        esc(dismissedNames(r).join(', '));
    }
    var buttons = '';
    if (pend.length) {
      buttons += '<button type="button" class="btn btn-xs" data-accept-join="' + id +
        '" onclick="x11AcceptJoin(this)">Accept</button>' +
        '<button type="button" class="btn btn-muted btn-xs" data-dismiss-join="' + id +
        '" onclick="x11DismissJoin(this)">Dismiss</button>';
    }
    var dismissedLine = '';
    if (dis.length) {
      dismissedLine = '<div class="x11-dismissed-line" data-dismissed-line>' +
        '<span>dismissed (' + dis.length + ')</span>' +
        '<button type="button" class="btn btn-muted btn-xs" data-reoffer-join="' + id +
        '" onclick="x11ReofferJoin(this)">Re-offer</button>' +
        '<button type="button" class="btn btn-xs" data-accept-all-join="' + id +
        '" onclick="x11AcceptAllJoin(this)">Accept all</button></div>';
    }
    return '<div class="x11-waiting-row" data-waiting-row data-allowance-id="' + id + '">' +
      '<div>' + head + '</div>' +
      dismissedLine +
      '<div class="x11-btn-row">' + buttons + '</div></div>';
  }
  function sshText(cfg) {
    var hosts = ((cfg || S.draft || {}).ssh || {}).hosts || [];
    if (!hosts.length) return 'off';
    if (hosts.indexOf('*') !== -1) return 'any gateway';
    return hosts.length + ' host' + (hosts.length === 1 ? '' : 's');
  }
  function webText(cfg) {
    var w = ((cfg || S.draft || {}).websites) || {};
    if (!w.enabled) return 'off';
    var n = (w.allow || []).length;
    return n + ' site' + (n === 1 ? '' : 's');
  }
  function verbPhrase(action) {
    var words = String(action || '').toLowerCase().split(/\s+/);
    if (!words.length) return 'uses';
    if (!/s$/.test(words[0])) words[0] += 's';
    return words.join(' ');
  }
  function deviceSummaryFor(cfg) {
    var groups = {};
    permissions(cfg).filter(function (p) { return !p.suspended_at; }).forEach(function (p) {
      var n = p.label_id ? labelMembers(p.label_id, p.domain).length
        : (p.entities || []).length;
      if (!n) return;
      var verb = verbPhrase(actionWords(p.domain, p.service));
      groups[verb] = (groups[verb] || 0) + n;
    });
    var verbs = Object.keys(groups).sort(function (a, b) {
      return groups[b] - groups[a];
    });
    if (!verbs.length) return [];
    var verb = verbs[0];
    var n = groups[verb];
    return [verb + ' ' + n + ' device' + (n === 1 ? '' : 's') + ' on its own'];
  }
  function summaryFor(cfg, integrations) {
    var c = countsFor(cfg, integrations);
    if (!c.total) return 'No tools yet';
    var parts = [c.run + ' tools run without asking', c.ask + ' ask first'];
    parts = parts.concat(deviceSummaryFor(cfg));
    parts.push('SSH ' + sshText(cfg));
    return parts.join(' \u00b7 ');
  }

  function reasonWords(reason) {
    return ({
      registry_changed: 'the device changed in Home Assistant',
      ha_changed: 'Home Assistant itself changed',
      protected: 'the device is protected',
      config_write: 'a Home Assistant config file changed',
      config_write_unmapped: 'a Home Assistant restore changed the setup',
    })[reason] || (reason || 'suspended').replace(/_/g, ' ');
  }

  function deviceMap() {
    var map = {};
    (S.devices || []).forEach(function (d) { map[d.id] = d; });
    return map;
  }
  function deviceName(id) {
    var d = deviceMap()[id];
    return d ? d.name : id;
  }
  function labelName(id) {
    var hit = (S.labels || []).filter(function (l) { return l.id === id; })[0];
    return hit ? hit.name : id;
  }
  function labelMembers(id, domain) {
    return (S.devices || []).filter(function (d) {
      if (domain && d.domain !== domain) return false;
      return (d.labels || []).some(function (l) { return l.id === id; });
    });
  }
  function actionWords(domain, service) {
    var text = (service || '').replace(/_/g, ' ');
    return text.charAt(0).toUpperCase() + text.slice(1);
  }
  function permDevices(p) {
    if (p.label_id) return 'label \u201c' + labelName(p.label_id) + '\u201d (' + labelMembers(p.label_id, p.domain).length + ')';
    var ids = p.entities || [];
    var names = ids.slice(0, 4).map(deviceName);
    if (ids.length > 4) names.push('+' + (ids.length - 4) + ' more');
    return names.length ? names.join(', ') : 'no devices';
  }

  function permissionFor(domain, service) {
    return permissions().filter(function (p) {
      return p.domain === domain && p.service === service;
    })[0] || null;
  }
  function allowanceRowFor(p) {
    var rows = S.allowances || [];
    for (var i = 0; i < rows.length; i += 1) {
      var r = rows[i];
      var cond = {};
      try { cond = JSON.parse(r.conditions_json || '{}'); } catch (e) { cond = {}; }
      var d = (cond.domain && cond.domain['in'] && cond.domain['in'][0]) || '';
      var sv = (cond.service && cond.service['in'] && cond.service['in'][0]) || '';
      if (Number(r.agent_id) === Number(S.agent && S.agent.id) &&
          d === p.domain && sv === p.service && !Number(r.revoked_at)) return r;
    }
    return null;
  }

  function head(title, sub, jsonKey) {
    return '<div class="x11-section-head"><div><h2 class="x11-section-title">' + esc(title) +
      '</h2>' + (sub ? '<p class="x11-section-sub">' + esc(sub) + '</p>' : '') + '</div>' +
      (jsonKey ? '<button type="button" class="btn btn-muted btn-xs" data-json-toggle="' + esc(jsonKey) +
        '" aria-expanded="false" onclick="x11ToggleJson(this)">Show JSON</button>' : '') + '</div>';
  }
  function jsonBlock(key) {
    var part = null;
    if (S.draft) {
      if (key === 'overview') part = S.draft;
      else if (key === 'tools') part = S.draft.tools;
      else if (key === 'ssh') part = S.draft.ssh;
      else if (key === 'websites') part = S.draft.websites;
      else if (key === 'home_assistant') part = S.draft.home_assistant;
      else if (key === 'json') part = S.draft;
      else if (key === 'activity') part = S.activity;
    }
    return '<pre class="x11-json" id="x11-json-' + esc(key) + '" hidden>' +
      esc(JSON.stringify(part, null, 2)) + '</pre>';
  }

  function tabsHtml() {
    return '<div class="x11-tabs" role="tablist" aria-label="Agents">' +
      '<button type="button" class="x11-tab" role="tab" data-x11-tab="agents" aria-selected="' +
      (S.tab === 'agents') + '" onclick="x11SetTab(\'agents\')">Agents</button>' +
      '<button type="button" class="x11-tab" role="tab" data-x11-tab="rules" aria-selected="' +
      (S.tab === 'rules') + '" onclick="x11SetTab(\'rules\')">Rules for all agents</button></div>';
  }

  // ── Agents tab ────────────────────────────────────────────────────────
  var SECTIONS = ['overview', 'tools', 'ssh', 'websites', 'activity', 'json'];
  function parseAgentHash() {
    var m = /^#agent\/(\d+)\/([a-z]+)$/.exec(window.location.hash || '');
    if (!m) return null;
    return { id: Number(m[1]), section: SECTIONS.indexOf(m[2]) === -1 ? 'overview' : m[2] };
  }
  function setAgentHash(id, section) {
    var next = '#agent/' + Number(id) + '/' + (section || 'overview');
    if (window.location.hash !== next) window.history.replaceState(null, '', next);
  }
  function clearAgentHash() {
    if (/^#agent\//.test(window.location.hash || '')) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }
  function x11ShowAgents() {
    if (!root()) return;
    var target = parseAgentHash();
    if (target) {
      if (!S.agent || Number(S.agent.id) !== target.id) { x11OpenAgent(target.id, target.section); return; }
      if (S.section !== target.section) { S.section = target.section; renderEditor(); }
      return;
    }
    if (S.screen === 'editor' && S.agent) { renderEditor(); return; }
    S.screen = 'agents'; S.tab = S.tab === 'rules' ? 'rules' : 'agents';
    renderAgents();
    if (!S.agentsReady) loadAgents();
  }

  function loadAgents() {
    Promise.all([
      api('/api/agents'),
      api('/api/ha/devices').catch(function () { return { devices: S.devices || [] }; }),
      api('/api/ha/labels').catch(function () { return { labels: S.labels || [] }; }),
      api('/api/allowances').catch(function () { return []; }),
    ]).then(function (first) {
      var agents = first[0] || [];
      S.devices = first[1].devices || S.devices || [];
      S.labels = first[2].labels || S.labels || [];
      S.allowances = first[3] || S.allowances || [];
      return Promise.all(agents.map(function (a) {
        return Promise.all([
          api('/api/agents/' + a.id + '/config').catch(function () { return null; }),
          api('/api/agents/' + a.id + '/available-tools').catch(function () { return { integrations: [] }; }),
        ]).then(function (pair) {
          return { agent: a, config: pair[0], integrations: pair[1].integrations || [] };
        });
      }));
    }).then(function (rows) {
      S.agents = rows.filter(function (r) { return r.config; });
      S.agentsReady = true;
      if (S.screen === 'agents') renderAgents();
    }).catch(function (e) {
      S.agentsReady = true;
      renderAgents(errText(e));
    });
  }

  function x11SetTab(tab) {
    S.tab = tab === 'rules' ? 'rules' : 'agents';
    S.screen = 'agents';
    if (S.tab === 'rules') renderRules(); else renderAgents();
  }

  function renderAgents(message) {
    var r = root();
    if (!r) return;
    if (S.tab === 'rules') { renderRules(); return; }
    var paused = 0;
    S.agents.forEach(function (a) { paused += pausedCount(a.config); });
    var banner = paused ? '<div class="x11-paused-banner"><strong>' + paused +
      ' paused</strong><span>' + (paused === 1 ? 'One permission is waiting.' : 'Some permissions are waiting.') +
      '</span><button type="button" class="btn btn-muted btn-sm" data-paused-review onclick="x11ReviewPaused()">Review</button></div>' : '';
    var waiting = 0;
    S.agents.forEach(function (a) { waiting += waitingCount(a.agent.id); });
    if (waiting) {
      banner += '<div class="x11-paused-banner x11-waiting-banner" data-waiting-banner>' +
        '<strong>' + waiting + ' waiting</strong><span>' +
        (waiting === 1 ? 'A new device has the label.' : 'New devices have the label.') +
        '</span> \u00b7 <button type="button" class="btn btn-muted btn-sm" data-waiting-review ' +
        'onclick="x11ReviewWaiting()">Review</button></div>';
    }
    var cards = S.agents.map(function (entry) {
      var a = entry.agent, cfg = entry.config;
      var n = pausedCount(cfg);
      return '<article class="x11-card" data-agent-card data-agent-id="' + Number(a.id) +
        '" tabindex="0" role="button" aria-label="Edit agent ' + attr(a.name) + '" ' +
        'onclick="x11OpenAgent(this.dataset.agentId)" ' +
        'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();x11OpenAgent(this.dataset.agentId);}">' +
        '<div class="x11-card-head"><span class="x11-card-name">' + esc(a.name) + '</span>' +
        '<span>' + (a.policy && a.policy.test_agent ? '<span class="x11-badge">Test agent</span> ' : '') +
        (a.revoked ? '<span class="x11-badge">Revoked</span>' : '') + '</span></div>' +
        '<p class="x11-card-summary">' + esc(summaryFor(cfg, entry.integrations)) + '</p>' +
        '<p class="x11-card-meta">Last active ' + esc(ago(a.last_used_at)) + '</p>' +
        '<p class="x11-card-paused"' + (n ? '' : ' hidden') + '>' + n + ' paused</p>' +
        '<p class="x11-card-waiting"' + (waitingCount(a.id) ? '' : ' hidden') + '>' +
        waitingCount(a.id) + ' waiting to join</p>' +
        '<div class="x11-btn-row"><button type="button" class="btn btn-muted btn-xs" ' +
        'onclick="event.stopPropagation();x11OpenAgent(this.closest(\'[data-agent-id]\').dataset.agentId)">Edit agent</button></div>' +
        '</article>';
    }).join('');
    var empty = S.agentsReady && !S.agents.length
      ? '<p class="x11-plain">No agent tokens yet. Create one to give an agent a name and a token.</p>' : '';
    r.innerHTML = '<div class="x11-hud"><div><div class="x11-title-row"><h1 class="x11-title">Agents</h1>' +
      '<button type="button" class="btn btn-sm" data-new-agent onclick="x11NewAgent()">New agent</button></div>' +
      '<p class="x11-lede">What each agent can do on its own, and how to change it.</p></div>' +
      '<div class="x11-hud-right" id="x11-hud-count">' + S.agents.length + ' agent' + (S.agents.length === 1 ? '' : 's') + '</div></div>' +
      tabsHtml() +
      '<div id="x11-notice" class="x11-notice"' + (message || S.notice ? '' : ' hidden') + ' data-kind="' +
      esc((S.notice && S.notice.kind) || (message ? 'error' : '')) + '">' +
      esc(message || (S.notice && S.notice.text) || '') + '</div>' + banner +
      '<div class="x11-cards">' + cards + '</div>' + empty + previewModalHtml() + newAgentModalHtml();
    if (!S.agentsReady) {
      r.querySelector('.x11-cards').insertAdjacentHTML('afterend', '<p class="x11-plain">Loading agents\u2026</p>');
    }
  }

  function x11ReviewWaiting() {
    for (var i = 0; i < S.agents.length; i += 1) {
      if (waitingCount(S.agents[i].agent.id)) {
        x11OpenAgent(S.agents[i].agent.id, 'overview');
        return;
      }
    }
  }

  function x11ReviewPaused() {
    for (var i = 0; i < S.agents.length; i += 1) {
      if (pausedCount(S.agents[i].config)) { x11OpenAgent(S.agents[i].agent.id, 'overview'); return; }
    }
  }

  // ── Rules tab ─────────────────────────────────────────────────────────
  function renderRules() {
    var r = root();
    if (!r) return;
    r.innerHTML = '<div class="x11-hud"><div><h1 class="x11-title">Rules for all agents</h1>' +
      '<p class="x11-lede">Protected devices, the limits every agent shares, and what is never allowed.</p></div></div>' +
      tabsHtml() + '<div id="x11-notice" class="x11-notice"' + (S.notice ? '' : ' hidden') + ' data-kind="' +
      esc((S.notice && S.notice.kind) || '') + '">' + esc((S.notice && S.notice.text) || '') + '</div>' +
      '<div class="x11-rules" data-rules-panel>' +
      '<div class="x11-rules-help" data-permissions-help>' +
      '<h2>How permissions work</h2>' +
      '<p>Limits decide which kinds of action can ever be allowed. Protected devices can never be ' +
      'given to an agent. Each agent then picks devices or follows Home Assistant labels, within those limits.</p>' +
      '</div>' +
      '<details data-rules="protected"' + (S.protectedEditing ? ' open' : '') + '><summary>Protected devices</summary><div class="x11-rules-body" id="x11-protected-body">' +
      '<p class="x11-section-sub">No agent can be given a protected device. Existing permissions that hold one are paused; unprotecting does not resume them.</p>' +
      '<div id="x11-protected-list">' + protectedSummaryHtml() + '</div></div></details>' +
      '<details data-rules="limits"><summary>Limits for all agents</summary><div class="x11-rules-body" id="x11-limits-body">' +
      '<p class="x11-section-sub">The outer limit. An agent can only be given an action that is enabled here.</p>' +
      '<div id="x11-ceiling-list">' + (S.ceiling ? ceilingListHtml() : '<p class="x11-muted">Loading limits\u2026</p>') + '</div>' +
      '<div class="x11-btn-row"><button type="button" class="btn btn-sm" onclick="x11SaveCeiling()">Save limits</button></div></div></details>' +
      '<details data-rules="never"><summary>Never allowed</summary><div class="x11-rules-body" id="x11-never-body">' +
      '<p class="x11-section-sub">Read-only reference, except for words you add yourself.</p>' +
      (S.ceiling ? neverHtml() : '<p class="x11-muted">Loading\u2026</p>') + '</div></details></div>' +
      previewModalHtml() + newAgentModalHtml();
    if (!S.ceiling || !S.devices.length) loadRules();
  }

  function loadRules() {
    Promise.all([
      api('/api/ha/devices').catch(function (e) { return { devices: [], error: errText(e) }; }),
      api('/api/ha/labels').catch(function () { return { labels: [] }; }),
      api('/api/allowances/protected').catch(function () { return { entities: [] }; }),
      api('/api/allowances/ceiling').catch(function (e) { return { error: errText(e) }; }),
    ]).then(function (out) {
      S.devices = out[0].devices || [];
      S.devicesError = out[0].error || '';
      S.labels = out[1].labels || [];
      S.protected = (out[2].entities || []).map(function (x) { return x.entity_id; });
      S.ceiling = out[3].error ? null : out[3];
      if (S.tab === 'rules') renderRules();
    });
  }

  function protectedSummaryHtml() {
    if (!S.devices.length) return '<p class="x11-muted">Loading devices\u2026</p>';
    if (!S.protectedEditing) {
      var names = (S.protected || []).map(function (id) {
        var d = deviceMap()[id];
        return d ? d.name : id;
      });
      return '<p class="x11-plain" data-protected-summary>' +
        (names.length ? 'Protected: ' + esc(names.join(', ')) : 'Nothing is protected yet.') + '</p>' +
        '<div class="x11-btn-row"><button type="button" class="btn btn-sm" data-protect-change onclick="x11StartProtect()">' +
        (names.length ? 'Change' : 'Choose devices') + '</button></div>';
    }
    return protectedListHtml() +
      '<label class="x11-plain"><input type="checkbox" data-protect-settings onchange="x11ProtectedSettings(this.checked)"' +
      (S.protectedSettings ? ' checked' : '') + '> Show settings entities</label>' +
      '<div class="x11-btn-row"><button type="button" class="btn btn-sm" onclick="x11SaveProtected()">Save protected devices</button>' +
      '<button type="button" class="btn btn-muted btn-sm" onclick="x11CancelProtect()">Cancel</button></div>';
  }
  function protectedListHtml() {
    var rows = (S.devices || []).filter(function (d) {
      if (d.disabled) return false;
      if (d.settings_entity && !S.protectedSettings) return false;
      return true;
    });
    var groups = {};
    rows.forEach(function (d) {
      var area = d.area || 'No area';
      (groups[area] = groups[area] || []).push(d);
    });
    return Object.keys(groups).sort().map(function (area) {
      return '<div><div class="x11-mono">' + esc(area) + '</div><div class="x11-protected-list">' +
        groups[area].map(function (d) {
          var checked = (S.protected || []).indexOf(d.id) !== -1 || d.protected;
          return '<label><input type="checkbox" data-protect="' + attr(d.id) + '"' + (checked ? ' checked' : '') +
            '> <span>' + esc(d.name) + '</span> <span class="x11-dev-id">' + esc(d.id) + '</span></label>';
        }).join('') + '</div></div>';
    }).join('') || '<p class="x11-muted">No devices from Home Assistant.</p>';
  }
  function x11StartProtect() { S.protectedEditing = true; renderRules(); }
  function x11CancelProtect() { S.protectedEditing = false; renderRules(); }
  function x11ProtectedSettings(v) { S.protectedSettings = !!v; renderRules(); }

  function ceilingListHtml() {
    if (!S.ceiling || !S.ceiling.catalogue) return '<p class="x11-muted">Limits are unavailable.</p>';
    return S.ceiling.catalogue.map(function (type) {
      return '<div class="x11-tool-group"><h3>' + esc(type.label) + '</h3>' +
        (type.risk ? '<p class="x11-risk-note">' + esc(type.risk) + '</p>' : '') +
        '<div class="x11-check-grid">' + (type.actions || []).map(function (a) {
          return '<label><input type="checkbox" data-ceiling-cid="' + attr(a.id) + '"' + (a.enabled ? ' checked' : '') +
            '> ' + esc(a.label) + '</label>';
        }).join('') + '</div></div>';
    }).join('');
  }

  function neverHtml() {
    var c = S.ceiling;
    return '<div class="x11-tool-group"><h3>Never allowable</h3>' +
      ((c.never || {}).domains || []).map(function (d) {
        return '<div class="x11-plain"><code>' + esc(d.value) + '</code> \u2014 ' + esc(d.reason) + '</div>';
      }).join('') +
      ((c.never || {}).services || []).map(function (d) {
        return '<div class="x11-plain"><code>' + esc(d.value) + '</code> \u2014 ' + esc(d.reason) + '</div>';
      }).join('') + '</div>' +
      '<div class="x11-tool-group"><h3>Destructive words</h3><div class="x11-check-grid">' +
      ((c.words || {}).builtin || []).map(function (w) {
        return '<span class="x11-badge">' + esc(w) + '</span>';
      }).join('') + '</div>' +
      '<div class="x11-check-grid">' + ((c.words || {}).operator || []).map(function (w) {
        return '<span class="x11-badge">' + esc(w) + ' <button type="button" class="link-btn" ' +
          'data-word="' + attr(w) + '" onclick="x11RemoveWord(this)" aria-label="Remove word ' + attr(w) + '">\u00d7</button></span>';
      }).join('') + '</div>' +
      '<div class="x11-btn-row"><input type="text" id="x11-word-input" placeholder="word (a-z0-9_, 3-32)" maxlength="32" aria-label="New never word">' +
      '<button type="button" class="btn btn-xs" onclick="x11AddWord()">Add word</button></div></div>';
  }

  function x11SaveProtected() {
    var ids = Array.prototype.slice.call(document.querySelectorAll('[data-protect]'))
      .filter(function (x) { return x.checked; }).map(function (x) { return x.getAttribute('data-protect'); });
    api('/api/allowances/protected', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entities: ids }),
    }).then(function () {
      toast('Protected devices saved', 'success');
      S.protected = ids;
      S.protectedEditing = false;
      S.devices = [];
      S.ceiling = null;
      S.agentsReady = false;
      renderRules();
    }).catch(function (e) { setNotice(errText(e), 'error'); });
  }

  function x11SaveCeiling() {
    var ids = Array.prototype.slice.call(document.querySelectorAll('[data-ceiling-cid]'))
      .filter(function (x) { return x.checked; }).map(function (x) { return x.getAttribute('data-ceiling-cid'); });
    var risky = [];
    ((S.ceiling && S.ceiling.catalogue) || []).forEach(function (t) {
      if (!t.risk) return;
      (t.actions || []).forEach(function (a) { if (ids.indexOf(a.id) !== -1) risky.push(t.risk); });
    });
    if (risky.length && !window.confirm(risky[0] + '\n\nEnable these rows?')) return;
    api('/api/allowances/ceiling', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: ids, risk_ack: !!risky.length, confirm: true,
        set_hash: S.ceiling.set_hash }),
    }).then(function (body) {
      S.ceiling = body && body.set_hash ? null : S.ceiling;
      toast('Limits saved', 'success');
      loadRules();
    }).catch(function (e) {
      if (e.status === 409) { setNotice('The limits changed since you opened them. Reloading.', 'warn'); loadRules(); }
      else setNotice(errText(e), 'error');
    });
  }

  function x11AddWord() {
    var input = el('x11-word-input');
    var word = input ? input.value.trim() : '';
    if (!word) return;
    api('/api/allowances/words', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ word: word }),
    }).then(function () { toast('Word added', 'success'); loadRules(); })
      .catch(function (e) { setNotice(errText(e), 'error'); });
  }
  function x11RemoveWord(btn) {
    var word = btn.getAttribute('data-word');
    if (!window.confirm('Remove the word "' + word + '"?')) return;
    api('/api/allowances/words/' + encodeURIComponent(word), { method: 'DELETE' })
      .then(function () { toast('Word removed', 'success'); loadRules(); })
      .catch(function (e) { setNotice(errText(e), 'error'); });
  }

  // ── Agent editor ──────────────────────────────────────────────────────
  function x11OpenAgent(id, section, keepNotice) {
    S.screen = 'editor';
    S.section = SECTIONS.indexOf(section) === -1 ? 'overview' : (section || 'overview');
    setAgentHash(id, S.section);
    Promise.all([
      api('/api/agents/' + Number(id) + '/config'),
      api('/api/agents/' + Number(id) + '/available-tools').catch(function () { return { integrations: [] }; }),
      api('/api/agents/' + Number(id) + '/activity').catch(function () { return {}; }),
      api('/api/allowances').catch(function () { return []; }),
      api('/api/gateways').catch(function () { return []; }),
      api('/api/ha/devices').catch(function (e) { return { devices: [], error: errText(e) }; }),
      api('/api/ha/labels').catch(function () { return { labels: [] }; }),
      api('/api/allowances/ceiling').catch(function () { return null; }),
      api('/api/agents/' + Number(id) + '/policy').catch(function () { return null; }),
      api('/api/allowances/protected').catch(function () { return { entities: [] }; }),
      api('/api/integrations').catch(function () { return []; }),
    ]).then(function (out) {
      S.config = out[0];
      S.draft = docOf(out[0]);
      S.agent = out[0].agent;
      S.integrations = out[10] || [];
      S.tools = mergeHATools((out[1].integrations) || [], S.integrations);
      S.activity = out[2] || {};
      S.allowances = (out[3] || []).filter(function (r) { return !Number(r.revoked_at); });
      S.gateways = out[4] || [];
      S.devices = out[5].devices || [];
      S.devicesError = out[5].error || '';
      S.labels = out[6].labels || [];
      S.ceiling = out[7] && !out[7].error ? out[7] : null;
      S.policy = out[8] || {};
      S.protected = (out[9].entities || []).map(function (r) { return r.entity_id; });
      S.integrations = out[10] || [];
      S.jsonText = JSON.stringify(S.draft, null, 2);
      S.jsonError = '';
      S.lastPreview = null;
      if (!keepNotice) S.notice = null;
      window.scrollTo(0, 0);
      renderEditor();
    }).catch(function (e) {
      S.screen = 'agents';
      renderAgents('Could not open that agent: ' + errText(e));
    });
  }
  window.x11OpenAgent = x11OpenAgent;

  function sectionDiffCount() {
    if (!S.config || !S.draft) return 0;
    var base = docOf(S.config);
    var n = 0;
    ['tools', 'ssh', 'websites', 'home_assistant'].forEach(function (k) {
      if (!same(base[k], S.draft[k])) n += 1;
    });
    return n;
  }
  function isDirty() { return sectionDiffCount() > 0; }

  function renderEditor() {
    var r = root();
    if (!r || !S.draft) return;
    var a = S.agent || {};
    var n = sectionDiffCount();
    if (!document.activeElement || document.activeElement.id !== 'x11-json-doc') {
      S.jsonText = JSON.stringify(S.draft, null, 2);
    }
    var nav = [['overview', 'Overview'], ['tools', 'Tools'], ['ssh', 'SSH'],
      ['websites', 'Websites'], ['activity', 'Activity'], ['json', 'JSON']];
    var navHtml = nav.map(function (x) {
      return '<button type="button" data-x11-nav="' + x[0] + '" aria-current="' +
        (S.section === x[0]) + '" onclick="x11Nav(\'' + x[0] + '\')">' + x[1] + '</button>';
    }).join('');
    var sectionFn = { overview: sectionOverview, tools: sectionTools, ssh: sectionSsh,
      websites: sectionWebsites, activity: sectionActivity, json: sectionJson };
    var content = (sectionFn[S.section] || sectionOverview)();
    r.innerHTML = '<div class="x11-editor">' +
      '<aside class="x11-editor-nav"><div class="x11-editor-name">' + esc(a.name || 'Agent') + '</div>' +
      '<div class="x11-editor-status">' + (a.revoked ? 'Revoked' : 'Active') + ' \u00b7 last active ' + esc(ago(a.last_used_at)) + '</div>' +
      '<nav aria-label="Agent sections">' + navHtml + '</nav>' +
      '<div class="x11-token-actions">' +
      '<button type="button" class="btn btn-muted btn-xs" onclick="x11Back()">All agents</button></div></aside>' +
      '<div class="x11-editor-content">' +
      '<div id="x11-notice" class="x11-notice"' + (S.notice ? '' : ' hidden') + ' data-kind="' +
      esc((S.notice && S.notice.kind) || '') + '">' + esc((S.notice && S.notice.text) || '') + '</div>' +
      content + '</div></div>' +
      '<div class="x11-savebar" id="x11-savebar"' + (n ? '' : ' hidden') + '>' +
      '<span class="x11-change-count" id="x11-change-count">' + n + ' change' + (n === 1 ? '' : 's') + '</span>' +
      '<button type="button" class="btn btn-muted btn-sm" onclick="x11Discard()">Discard</button>' +
      '<button type="button" class="btn btn-sm" id="x11-save-changes" onclick="x11Check()">Save changes</button></div>' +
      previewModalHtml() + newAgentModalHtml();
    syncSavebarSpace();
    syncJsonApplyButton();
    if (S.section === 'overview') x11LoadApproverTokens();
  }

  function x11Nav(section) {
    S.section = SECTIONS.indexOf(section) === -1 ? 'overview' : section;
    setAgentHash(S.agent.id, S.section);
    window.scrollTo(0, 0);
    renderEditor();
  }
  function scrollToSection(section) {
    var target = el('x11-sec-' + section);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function syncSavebarSpace() {
    var bar = el('x11-savebar');
    var space = bar && !bar.hidden ? Math.ceil(bar.getBoundingClientRect().height) + 28 : 0;
    if (root()) root().style.paddingBottom = space ? space + 'px' : '';
  }

  function sectionOverview() {
    var c = countsFor();
    var perms = permissions();
    var lines = [];
    lines.push('<p class="x11-plain"><strong>' + c.run + '</strong> tools run without asking; <strong>' +
      c.ask + '</strong> ask you first; ' + c.total + ' tools in reach.</p>');
    lines.push('<p class="x11-plain">SSH: ' + esc(sshText()) + '. Websites: ' + esc(webText()) + '.</p>');
    var permHtml = perms.map(function (p, i) {
      var paused = p.suspended_at;
      var row = allowanceRowFor(p);
      return '<div class="x11-perm" data-permission-row="' + i + '">' +
        '<div class="x11-perm-name">' + esc(actionWords(p.domain, p.service)) + ' \u00b7 ' + esc(permDevices(p)) + '</div>' +
        '<div class="x11-perm-line"><span>At most ' + Number(p.rate_per_hour || 0) + ' times an hour</span>' +
        '<span>' + (p.window ? 'Only between ' + esc(p.window) : 'Any time') + '</span>' +
        '<span class="x11-expiry">' + (p.expires_at ? 'Ends ' + esc(when(p.expires_at)) : 'No end date') + '</span></div>' +
        (paused ? '<div class="x11-paused-item"><div class="x11-paused-reason">Paused: ' +
          esc(reasonWords(p.suspended_reason)) + ' \u00b7 since ' + esc(when(p.suspended_at)) + '</div>' +
          '<p class="x11-plain">Editing this permission will resume it.</p>' +
          (row ? '<button type="button" class="btn btn-xs" data-resume-id="' + Number(row.id) +
            '" onclick="x11Resume(this)">Resume</button>' : '<span class="x11-muted">Resume from Tools after the next refresh.</span>') +
          '</div>' : '') + '</div>';
    }).join('');
    if (!perms.length) permHtml = '<p class="x11-plain">Nothing runs without asking yet. Choose a tool action under Tools, then pick devices.</p>';
    var waits = waitingRows(S.agent && S.agent.id);
    var waitHtml = '';
    if (waits.length) {
      waitHtml = '<div class="x11-waiting-list" data-waiting-list><h3>Waiting to join</h3>' +
        waits.map(waitingRowHtml).join('') + '</div>';
    }
    return '<section class="x11-section" id="x11-sec-overview" data-x11-section="overview">' +
      head('Overview', 'What this agent can do on its own, in plain words.', 'overview') +
      lines.join('') + waitHtml + '<div class="x11-perm-list">' + permHtml + '</div>' +
      '<details class="x11-e2e"><summary>Test agent and E2E approver tokens</summary>' +
      '<p class="x11-section-sub">A test agent may only use HA reads and call service, with exactly one SSH host. An approver token can decide only its E2E rows.</p>' +
      '<label class="x11-plain"><input type="checkbox" id="agent-test-toggle" onchange="x11ToggleTestAgent(this)"' +
      ((S.policy && S.policy.test_agent) ? ' checked' : '') + '> This agent is a test agent</label>' +
      '<p class="x11-section-sub" id="agent-test-hint">' +
      esc((S.policy && S.policy.test_agent)
        ? "On: HA reads and call service only, exactly one SSH host. Clearing this revokes the agent's approver tokens."
        : 'Off: enabling is refused once the agent has any session, request or call.') + '</p>' +
      '<div class="x11-tool-group"><h3>Approver tokens</h3>' +
      '<label class="x11-plain" for="approver-commands">Exact commands (one per line)</label>' +
      '<textarea id="approver-commands" class="w-full text-xs" rows="3" placeholder="hostname&#10;id -un"></textarea>' +
      '<div class="x11-btn-row"><label class="x11-plain">Test gateway IP <input id="approver-gateway" placeholder="192.0.2.10"></label>' +
      '<label class="x11-plain">Days <input id="approver-days" type="number" min="1" max="30" value="30"></label>' +
      '<button type="button" class="btn btn-sm" onclick="x11MintApproverToken()">Mint approver token</button></div>' +
      '<div id="approver-token-result" class="x11-token-once" hidden></div>' +
      '<div id="approver-token-list" class="x11-perm-list"><p class="x11-muted">Loading\u2026</p></div></div>' +
      '</details>' +
      '<div class="x11-danger" data-danger-zone><h3>Danger zone</h3>' +
      '<p class="x11-section-sub">These actions affect the token this agent uses to connect. They cannot be undone.</p>' +
      '<div class="x11-btn-row">' +
      '<button type="button" class="btn btn-muted btn-sm" data-danger-revoke onclick="x11RevokeAgent()">Revoke token</button>' +
      '<button type="button" class="btn btn-deny btn-sm" data-danger-delete onclick="x11DeleteAgent()">Delete agent</button>' +
      '</div></div>' + jsonBlock('overview') + '</section>';
  }

  function x11LoadApproverTokens() {
    var box = el('approver-token-list');
    if (!box || !S.agent) return;
    api('/api/approver-tokens?agent_id=' + Number(S.agent.id)).then(function (rows) {
      if (!el('approver-token-list')) return;
      if (!rows || !rows.length) { box.innerHTML = '<p class="x11-muted">No approver tokens.</p>'; return; }
      box.innerHTML = rows.map(function (t) {
        var state = t.revoked ? 'revoked' : (t.expired ? 'expired' : 'live');
        return '<div class="x11-perm"><div class="x11-perm-line"><b>#' + esc(t.id) + '</b> ' +
          '<span>' + esc(state) + '</span><span>gateway ' + esc(t.gateway_ip || '\u2014') + '</span>' +
          '<span>' + esc(String((t.commands || []).length)) + ' command(s)</span>' +
          '<span>expires ' + esc(t.expires_at ? when(t.expires_at) : '\u2014') + '</span></div>' +
          '<div class="x11-mono">' + esc((t.commands || []).join('\n')) + '</div>' +
          (state === 'live' ? '<button type="button" class="btn btn-deny btn-xs" onclick="x11RevokeApproverToken(' +
            Number(t.id) + ')">Revoke</button>' : '') + '</div>';
      }).join('');
    }).catch(function () {
      if (box) box.innerHTML = '<p class="x11-muted">Could not load approver tokens.</p>';
    });
  }

  function x11ToggleTestAgent(cb) {
    if (!S.agent) return;
    var wanted = !!cb.checked;
    cb.disabled = true;
    api('/api/agents/' + Number(S.agent.id) + '/test-agent', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: wanted }),
    }).then(function (policy) {
      S.policy = policy || {};
      toast(wanted ? 'Test agent enabled' : 'Test agent disabled', 'success');
      x11LoadApproverTokens();
    }).catch(function (e) {
      cb.checked = !wanted;
      setNotice(errText(e), 'error');
    }).then(function () { cb.disabled = false; });
  }

  function x11MintApproverToken() {
    if (!S.agent) return;
    var raw = (el('approver-commands') || {}).value || '';
    var commands = raw.split('\n').map(function (x) { return x.replace(/\r$/, ''); }).filter(Boolean);
    var gateway = ((el('approver-gateway') || {}).value || '').trim();
    var days = parseInt((el('approver-days') || {}).value || '30', 10);
    if (!commands.length) { toast('Add at least one exact command', 'error'); return; }
    if (!gateway) { toast('Set the test gateway IP', 'error'); return; }
    api('/api/approver-tokens', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: Number(S.agent.id), commands: commands,
        gateway_ip: gateway, days: days }),
    }).then(function (body) {
      var out = el('approver-token-result');
      if (out) {
        out.hidden = false;
        out.textContent = 'Token #' + body.id + ' (shown once): ' + body.token;
      }
      var input = el('approver-commands'); if (input) input.value = '';
      x11LoadApproverTokens();
      toast('Approver token minted', 'success');
    }).catch(function (e) { setNotice(errText(e), 'error'); });
  }

  function x11RevokeApproverToken(id) {
    if (!window.confirm('Revoke approver token #' + id + '? It stops working at once.')) return;
    api('/api/approver-tokens/' + Number(id) + '/revoke', { method: 'POST' })
      .then(function () { toast('Approver token revoked', 'success'); x11LoadApproverTokens(); })
      .catch(function (e) { setNotice(errText(e), 'error'); });
  }

  function plainDescription(text) {
    return String(text || '')
      .replace(/REQUIRES OPERATOR APPROVAL/g, 'Requires operator approval')
      .replace(/REQUIRES APPROVAL/g, 'Requires approval')
      .replace(/\bALWAYS\b/g, 'Always')
      .replace(/\s+/g, ' ')
      .trim();
  }
  function firstSentence(text) {
    var plain = plainDescription(text);
    for (var i = 0; i < plain.length; i += 1) {
      if ('.!?'.indexOf(plain.charAt(i)) === -1) continue;
      if (i + 1 < plain.length && !/\s/.test(plain.charAt(i + 1))) continue;
      var before = plain.slice(0, i);
      if (/\b(e\.g|i\.e|etc|vs|Mr|Dr|St)$/i.test(before)) continue;
      return { first: plain.slice(0, i + 1), rest: plain.slice(i + 1).trim() };
    }
    return { first: plain, rest: '' };
  }
  function toolDescription(tool) {
    var parts = firstSentence(tool.description);
    if (!parts.first) return '';
    return '<div class="x11-tool-desc"><span>' + esc(parts.first) + '</span>' +
      (parts.rest ? '<span class="x11-desc-rest" hidden> ' + esc(parts.rest) + '</span> ' +
        '<button type="button" class="x11-more" aria-expanded="false" onclick="x11More(this)">more</button>' : '') +
      '</div>';
  }
  function x11More(btn) {
    var rest = btn.parentElement.querySelector('.x11-desc-rest');
    if (!rest) return;
    var open = rest.hidden;
    rest.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    btn.textContent = open ? 'less' : 'more';
  }
  function x11ToolFilter(v) {
    S.toolFilter = v;
    renderEditor();
    var input = el('x11-tool-search');
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }
  function x11ToolOnlyAllowed(v) { S.toolOnlyAllowed = !!v; renderEditor(); }
  function x11ToolGroupToggle(details) {
    var key = details.getAttribute('data-tool-group');
    S.toolOpen[key] = !!details.open;
  }
  function x11OpenDevices() {
    var grid = el('x11-device-grid');
    if (grid) grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function integrationConfig(row) {
    try {
      var cfg = JSON.parse((row && row.config_json) || '{}');
      return cfg && typeof cfg === 'object' ? cfg : {};
    } catch (e) { return {}; }
  }
  function mergeHATools(groups, integrations) {
    var has = (integrations || []).filter(function (i) {
      return i.kind === 'ha' && i.enabled;
    });
    var children = [];
    (integrations || []).forEach(function (i) {
      if (i.kind !== 'ha_ssh' || !i.enabled) return;
      var cfg = integrationConfig(i);
      var parent = cfg.ha_integration;
      if (!parent) parent = has.length === 1 ? has[0].name : '';
      if (has.some(function (h) { return h.name === parent; })) {
        children.push({ name: i.name, parent: parent });
      }
    });
    if (!children.length) return groups;
    var byName = {};
    groups.forEach(function (g) { byName[g.integration] = g; });
    children.forEach(function (c) {
      var group = byName[c.name];
      var parent = byName[c.parent];
      if (!group || !parent) return;
      (group.tools || []).forEach(function (tool) {
        parent.tools.push(Object.assign({}, tool, { files: true }));
      });
    });
    return groups.filter(function (g) {
      return !children.some(function (c) { return c.name === g.integration; });
    });
  }

  function sectionTools() {
    var filter = S.toolFilter.toLowerCase();
    var filtering = !!filter || S.toolOnlyAllowed;
    var groups = (S.tools || []).map(function (g) {
      var all = g.tools || [];
      var shown = all.filter(function (tool) {
        var outcome = outcomeFor(tool.name, tool);
        if (S.toolOnlyAllowed && outcome === 'none') return false;
        if (!filter) return true;
        return ((tool.name || '') + ' ' + (tool.tool || '') + ' ' +
          (tool.description || '')).toLowerCase().indexOf(filter) !== -1;
      });
      var ask = all.filter(function (t) { return outcomeFor(t.name, t) === 'ask'; }).length;
      var summary = esc(g.integration) + ' \u00b7 ' + all.length + ' tool' +
        (all.length === 1 ? '' : 's') + ' \u00b7 ' + ask + ' ask first';
      var open = filtering || !!S.toolOpen[g.integration];
      var renderRows = function (list) { return list.map(function (tool) {
        var outcome = outcomeFor(tool.name, tool);
        function choice(value, label) {
          return '<button type="button" data-tool-name="' + attr(tool.name) + '" data-choice="' + value +
            '" aria-pressed="' + (outcome === value) + '"' +
            (value === 'run' && tool.always_gate ? ' disabled title="Always asks; use device permissions"' : '') +
            ' onclick="x11ToolChoice(this)">' + label + '</button>';
        }
        return '<div class="x11-tool-row" data-tool-row="' + attr(tool.name) + '"' +
          (tool.always_gate ? ' data-always-gate="1"' : '') + '>' +
          '<div><div class="x11-tool-name">' + esc((tool.tool || tool.name).replace(/_/g, ' ')) + '</div>' +
          toolDescription(tool) + '</div>' +
          '<div class="x11-seg" role="group" aria-label="' + attr(tool.name) + ' outcome">' +
          choice('none', 'None') + choice('ask', 'Asks you first') + choice('run', 'Runs without asking') +
          '</div>' +
          (tool.always_gate ? (g.kind === 'ha' && tool.tool === 'call_service'
            ? '<div class="x11-always-line" data-always-line>Always asks you. To let it run for some devices, use ' +
              '<button type="button" class="link-btn" onclick="x11OpenDevices()">Runs without asking \u2192 pick devices</button>.</div>'
            : '<div class="x11-always-line" data-always-line>Always asks you: Eshu requires your approval for this tool.</div>') : '') +
          '</div>';
      }).join(''); };
      var normal = shown.filter(function (t) { return !t.files; });
      var files = shown.filter(function (t) { return t.files; });
      var rows = renderRows(normal) +
        (files.length ? '<h4 class="x11-subhead">Files</h4>' + renderRows(files) : '');
      return '<details class="x11-int-group" data-tool-group="' + attr(g.integration) + '"' +
        (open ? ' open' : '') + ' ontoggle="x11ToolGroupToggle(this)">' +
        '<summary><span>' + summary + '</span></summary>' +
        (g.kind === 'ha' ? '<p class="x11-int-note">Home Assistant calls. Device permissions live under call service.</p>' : '') +
        (rows || '<p class="x11-muted">No tools match.</p>') + '</details>';
    }).join('');
    var ha = toolList().filter(function (t) { return t.kind === 'ha' && t.tool === 'call_service'; })[0];
    return '<section class="x11-section" id="x11-sec-tools" data-x11-section="tools">' +
      head('Tools', 'Choose an outcome per tool. A device permission belongs to Home Assistant call service.', 'tools') +
      '<div class="x11-tool-tools"><input id="x11-tool-search" type="search" placeholder="Search tools" aria-label="Search tools" value="' +
      attr(S.toolFilter) + '" oninput="x11ToolFilter(this.value)">' +
      '<label class="x11-plain"><input type="checkbox" onchange="x11ToolOnlyAllowed(this.checked)"' +
      (S.toolOnlyAllowed ? ' checked' : '') + '> Only allowed</label></div>' +
      groups + (ha ? deviceGridHtml() : '') + jsonBlock('tools') + '</section>';
  }

  function x11ToolChoice(btn) {
    var name = btn.getAttribute('data-tool-name');
    var choice = btn.getAttribute('data-choice');
    var tool = toolList().filter(function (t) { return t.name === name; })[0] || {};
    if (choice === 'run' && tool.always_gate) {
      setNotice('This tool always asks. Use Runs without asking with device permissions for Home Assistant.', 'warn');
      return;
    }
    var tools = S.draft.tools;
    if (choice === 'none') {
      if ((tools.mode || 'all') === 'all') {
        tools.mode = 'allowlist';
        tools.allow = toolList().map(function (t) { return t.name; })
          .filter(function (n) { return n !== name; });
      } else {
        tools.allow = (tools.allow || []).filter(function (n) { return n !== name; });
      }
      delete (tools.approval || {})[name];
    } else {
      if ((tools.mode || 'all') === 'allowlist' && (tools.allow || []).indexOf(name) === -1) {
        tools.allow = (tools.allow || []).concat([name]);
      }
      tools.approval = tools.approval || {};
      tools.approval[name] = choice === 'run' ? 'ungated' : 'gated';
    }
    S.section = 'tools';
    renderEditor();
    markDirty();
  }

  function deviceActions() {
    var byDomain = {};
    (S.devices || []).forEach(function (d) { byDomain[d.domain] = true; });
    var out = [];
    ((S.ceiling && S.ceiling.catalogue) || []).forEach(function (type) {
      if (!byDomain[type.domain]) return;
      (type.actions || []).forEach(function (a) {
        // Stage 3's grid is the on/off + open/close editor. Toggle and the
        // media/stop verbs stay out so the table fits and every column is a
        // clear physical action.
        var wanted = ['turn_on', 'turn_off', 'open_cover', 'close_cover',
          'media_play', 'media_pause'];
        if (a.enabled && wanted.indexOf(a.service) !== -1) {
          out.push({ id: a.id, domain: type.domain, service: a.service,
            label: a.label, risk: !!type.risk });
        }
      });
    });
    return out;
  }

  function actionShort(service) {
    return ({ turn_on: 'on', turn_off: 'off', open_cover: 'open',
      close_cover: 'close', media_play: 'play', media_pause: 'pause' })[service] || service;
  }
  function typeLabel(domain) {
    return ({ light: 'Lights', switch: 'Plugs and switches', cover: 'Covers',
      fan: 'Fans', climate: 'Heating', media_player: 'Media players',
      input_boolean: 'Helpers' })[domain] || domain;
  }
  function actionTypes(actions) {
    var by = {};
    actions.forEach(function (a) {
      if (!by[a.domain]) by[a.domain] = { domain: a.domain, label: typeLabel(a.domain), actions: [] };
      by[a.domain].actions.push(a);
    });
    return Object.keys(by).map(function (k) { return by[k]; });
  }
  function labelSelectHtml(a, p) {
    if (!p || !p.label_id) return '';
    return '<select data-action-label="' + attr(a.id) + '" title="' + attr(labelName(p.label_id)) +
      '" aria-label="Home Assistant label for ' + attr(a.label) + '" onchange="x11SetLabel(this)">' +
      '<option value="">Pick a label</option>' + (S.labels || []).map(function (l) {
        return '<option value="' + attr(l.id) + '"' + (p.label_id === l.id ? ' selected' : '') + '>' +
          esc(l.name) + '</option>';
      }).join('') + '</select>';
  }
  function modeSelectHtml(a, p) {
    var mode = p && p.label_id ? 'label' : 'pick';
    return '<select data-action-mode="' + attr(a.id) + '" aria-label="Devices or label for ' +
      attr(a.label) + '" onchange="x11SetActionMode(this)">' +
      '<option value="pick"' + (mode === 'pick' ? ' selected' : '') + '>Pick devices</option>' +
      '<option value="label"' + (mode === 'label' ? ' selected' : '') + '>Follow label</option></select>';
  }
  function actionHeadHtml(a) {
    var p = permissionFor(a.domain, a.service);
    var label = p && p.label_id ? '<div class="x11-label-full" title="' + attr(labelName(p.label_id)) + '">Label: ' +
      esc(labelName(p.label_id)) + '</div>' : '';
    return '<div class="x11-action-head" data-action-head="' + attr(a.id) + '">' +
      modeSelectHtml(a, p) + labelSelectHtml(a, p) + label + '</div>';
  }
  function protectedLine() {
    return '<div class="x11-protected-line"><span aria-hidden="true">\u25a0</span> <b>Protected</b> ' +
      '<a href="#" onclick="x11GoRules();return false;">Rules for all agents</a></div>';
  }
  function deviceToolsHtml() {
    return '<div class="x11-device-tools">' +
      '<input type="search" placeholder="Search devices" aria-label="Search devices" value="' + attr(S.deviceSearch) +
      '" oninput="x11DeviceSearch(this.value)">' +
      '<label class="x11-plain"><input type="checkbox" onchange="x11DeviceOnlyPerm(this.checked)"' +
      (S.deviceOnlyPerm ? ' checked' : '') + '> Only devices with permissions</label>' +
      '<label class="x11-plain"><input type="checkbox" onchange="x11DeviceSettings(this.checked)"' +
      (S.deviceSettings ? ' checked' : '') + '> Show settings entities</label></div>';
  }
  function deviceStateLine(d) {
    return esc([d.area, d.state].filter(Boolean).join(' \u00b7 '));
  }
  function mobileDeviceHtml(d, actions) {
    var toggles = actions.filter(function (a) { return a.domain === d.domain; }).map(function (a) {
      var p = permissionFor(a.domain, a.service);
      if (p && p.label_id) {
        var member = (d.labels || []).some(function (l) { return l.id === p.label_id; });
        return '<span class="x11-mobile-readonly">' + esc(a.label) + ': ' + (member ? '\u2713 in label' : '\u2014') + '</span>';
      }
      var checked = p && (p.entities || []).indexOf(d.id) !== -1;
      return '<label class="x11-mobile-toggle"><input type="checkbox" data-device="' + attr(d.id) +
        '" data-action="' + attr(a.id) + '"' + (checked ? ' checked' : '') +
        ' aria-label="' + attr(a.label + ' for ' + d.name) + '" onchange="x11SetDevice(this)"> ' +
        esc(actionShort(a.service)) + '</label>';
    }).join('');
    return '<div class="x11-mobile-device' + (d.protected ? ' x11-locked' : '') + '" data-device-row="' + attr(d.id) + '">' +
      '<div class="x11-device-name">' + esc(d.name) + '</div>' +
      '<div class="x11-device-id">' + esc(d.id) + '</div>' +
      '<div class="x11-device-state">' + deviceStateLine(d) + '</div>' +
      (d.risky ? '<div class="x11-risky">Risky: ' + esc(d.device_class || 'device') + '</div>' : '') +
      (d.protected ? protectedLine() : '<div class="x11-mobile-toggles">' + toggles + '</div>') + '</div>';
  }
  function deviceGridHtml() {
    if (S.devicesError) {
      return '<div class="x11-device-wrap" id="x11-device-grid"><p class="x11-plain">The device list is unavailable: ' +
        esc(S.devicesError) + '</p></div>';
    }
    var actions = deviceActions();
    if (!actions.length) {
      return '<div class="x11-device-wrap" id="x11-device-grid"><p class="x11-plain">No device actions are enabled under Rules for all agents.</p></div>';
    }
    var search = S.deviceSearch.toLowerCase();
    var devices = (S.devices || []).filter(function (d) {
      if (d.disabled && !S.deviceSettings) return false;
      if (d.settings_entity && !S.deviceSettings) return false;
      if (S.deviceOnlyPerm && !permissions().some(function (p) {
        return p.domain === d.domain && (p.suspended_at || (p.entities || []).indexOf(d.id) !== -1 ||
          (p.label_id && (d.labels || []).some(function (l) { return l.id === p.label_id; })));
      })) return false;
      if (search && (d.name || '').toLowerCase().indexOf(search) === -1 &&
          (d.id || '').toLowerCase().indexOf(search) === -1) return false;
      return true;
    });
    var groups = {};
    devices.forEach(function (d) {
      var area = d.area || 'No area';
      (groups[area] = groups[area] || []).push(d);
    });
    var types = actionTypes(actions);
    var groupHeads = types.map(function (t) {
      return '<th scope="colgroup" colspan="' + t.actions.length + '">' + esc(t.label) + ': ' +
        t.actions.map(function (a) { return esc(actionShort(a.service)); }).join(' / ') + '</th>';
    }).join('');
    var actionHeads = actions.map(function (a) {
      return '<th scope="col">' + actionHeadHtml(a) + '</th>';
    }).join('');
    var body = Object.keys(groups).sort().map(function (area) {
      var rows = groups[area].map(function (d) {
        var cells = actions.map(function (a) {
          if (d.domain !== a.domain) return '<td class="x11-action-cell" data-cell-label="' + attr(a.label) + '">\u2014</td>';
          if (d.protected) return '<td class="x11-action-cell" data-cell-label="' + attr(a.label) + '">\u2014</td>';
          var p = permissionFor(a.domain, a.service);
          if (p && p.label_id) {
            var member = (d.labels || []).some(function (l) { return l.id === p.label_id; });
            return '<td class="x11-action-cell" data-cell-label="' + attr(a.label) +
              '"><span aria-label="' + (member ? 'In the label' : 'Not in the label') + '">' +
              (member ? '\u2713' : '\u2014') + '</span></td>';
          }
          var checked = p && (p.entities || []).indexOf(d.id) !== -1;
          return '<td class="x11-action-cell" data-cell-label="' + attr(a.label) +
            '"><input type="checkbox" data-device="' + attr(d.id) + '" data-action="' + attr(a.id) + '"' +
            (checked ? ' checked' : '') + ' aria-label="' + attr(a.label + ' for ' + d.name) + '" onchange="x11SetDevice(this)"></td>';
        }).join('');
        return '<tr class="x11-device-row' + (d.protected ? ' x11-locked' : '') + '" data-device-row="' + attr(d.id) + '">' +
          '<td><div class="x11-device-name">' + esc(d.name) + '</div>' +
          '<div class="x11-device-id">' + esc(d.id) + '</div>' +
          '<div class="x11-device-state">' + deviceStateLine(d) + '</div>' +
          (d.risky ? '<div class="x11-risky">Risky: ' + esc(d.device_class || 'device') + '</div>' : '') +
          (d.protected ? protectedLine() : '') + '</td>' + cells + '</tr>';
      }).join('');
      return '<tr class="x11-area-row"><td colspan="' + (actions.length + 1) + '">' + esc(area) + '</td></tr>' + rows;
    }).join('');
    var mobile = types.map(function (t) {
      var typeDevices = {};
      devices.filter(function (d) { return d.domain === t.domain; }).forEach(function (d) {
        var area = d.area || 'No area';
        (typeDevices[area] = typeDevices[area] || []).push(d);
      });
      var settings = t.actions.map(function (a) {
        var p = permissionFor(a.domain, a.service);
        return '<div class="x11-mobile-type-setting"><span>' + esc(a.label) + '</span>' +
          modeSelectHtml(a, p) + labelSelectHtml(a, p) + '</div>';
      }).join('');
      var cards = Object.keys(typeDevices).sort().map(function (area) {
        return '<div class="x11-mobile-area">' + esc(area) + '</div>' +
          typeDevices[area].map(function (d) { return mobileDeviceHtml(d, actions); }).join('');
      }).join('');
      return '<section class="x11-mobile-type" data-mobile-type="' + attr(t.domain) + '">' +
        '<h4>' + esc(t.label) + '</h4><div class="x11-mobile-type-settings">' + settings + '</div>' +
        cards + '</section>';
    }).join('');
    var labelModeAny = actions.some(function (a) {
      var p = permissionFor(a.domain, a.service);
      return p && p.label_id;
    });
    var perms = permissions().map(function (p, i) { return permEditor(p, i); }).join('');
    var help = '<div class="x11-perm-help" data-perm-help>' +
      '<p><b>Pick devices</b> Only the devices you tick. No labels needed.</p>' +
      '<p><b>Follow a Home Assistant label</b> The devices come from Home Assistant. ' +
      'Remove the label there and the agent loses the device at once. Add the label to a new ' +
      'device and it waits here for your one-click Accept, so a change in Home Assistant alone ' +
      'never gives an agent a new device.</p></div>';
    return '<div id="x11-device-grid" data-device-grid>' + deviceToolsHtml() + help +
      '<div class="x11-mobile-grid" data-mobile-grid>' + mobile + '</div>' +
      '<div class="x11-device-wrap"><table class="x11-devices"><thead>' +
      '<tr><th scope="col" rowspan="2">Device</th>' + groupHeads + '</tr>' +
      '<tr>' + actionHeads + '</tr></thead><tbody>' + body + '</tbody></table></div>' +
      (labelModeAny ? '<p class="x11-label-hint">Add or remove the label in Home Assistant; new devices wait for your review here.</p>' : '') +
      waitingGridHtml() +
      '<div class="x11-perm-list">' + (perms || '<p class="x11-plain">No device permissions yet.</p>') + '</div></div>';
  }

  function waitingGridHtml() {
    var waits = waitingRows(S.agent && S.agent.id);
    if (!waits.length) return '';
    return '<div class="x11-waiting-list x11-waiting-grid" data-waiting-grid>' +
      waits.map(waitingRowHtml).join('') + '</div>';
  }

  function x11AcceptJoin(btn) {
    acceptJoin(Number(btn.getAttribute('data-accept-join')), false);
  }

  function x11AcceptAllJoin(btn) {
    acceptJoin(Number(btn.getAttribute('data-accept-all-join')), true);
  }

  function acceptJoin(id, includeDismissed) {
    if (!id) return;
    var row = (S.allowances || []).filter(function (r) {
      return Number(r.id) === id;
    })[0];
    if (row) {
      var names = waitingNames(row).concat(includeDismissed ? dismissedNames(row) : []);
      if (!window.confirm('Accept ' + names.length + ' device' + (names.length === 1 ? '' : 's') +
          ' for ' + labelForRow(row) + ': ' + names.join(', ') + '?')) return;
    }
    var call = function (risk) {
      api('/api/allowances/' + id + '/accept-join', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ risk_ack: !!risk,
                               include_dismissed: !!includeDismissed }),
      }).then(function () {
        toast(includeDismissed ? 'All devices accepted' : 'Devices accepted', 'success');
        var section = S.section;
        x11OpenAgent(S.agent.id, section);
      }).catch(function (e) {
        var d = e.detail || {};
        if (e.status === 400 && d.code === 'risk_ack_required') {
          var risky = (d.risky_entities || []).map(function (x) {
            return x.entity_id || x;
          }).join(', ');
          if (window.confirm('Accepting these devices needs a risk confirmation' +
              (risky ? ': ' + risky : '') + '. Accept anyway?')) {
            call(true);
            return;
          }
        }
        setNotice(errText(e), 'error');
      });
    };
    call(false);
  }

  function x11DismissJoin(btn) {
    var id = Number(btn.getAttribute('data-dismiss-join'));
    if (!id) return;
    if (!window.confirm('Dismiss these waiting devices? They stay in Home Assistant; this permission will not offer them again while the label keeps them.')) return;
    api('/api/allowances/' + id + '/dismiss-join', { method: 'POST' })
      .then(function () {
        toast('Waiting devices dismissed', 'success');
        var section = S.section;
        x11OpenAgent(S.agent.id, section);
      }).catch(function (e) { setNotice(errText(e), 'error'); });
  }

  function x11ReofferJoin(btn) {
    var id = Number(btn.getAttribute('data-reoffer-join'));
    if (!id) return;
    api('/api/allowances/' + id + '/reoffer-join', { method: 'POST' })
      .then(function () {
        toast('Dismissed devices offered again', 'success');
        var section = S.section;
        x11OpenAgent(S.agent.id, section);
      }).catch(function (e) { setNotice(errText(e), 'error'); });
  }

  function permEditor(p, i) {
    var expiry = p.expires_at ? when(p.expires_at) : 'No end date';
    return '<div class="x11-perm" data-perm-edit="' + i + '" data-domain="' + attr(p.domain) + '" data-service="' + attr(p.service) + '">' +
      (p.suspended_at ? '<div class="x11-paused-reason">Paused: ' + esc(reasonWords(p.suspended_reason)) + '. Changing this permission will resume it.</div>' : '') +
      '<div class="x11-perm-name">' + esc(actionWords(p.domain, p.service)) + ' \u00b7 ' + esc(permDevices(p)) + '</div>' +
      '<div class="x11-perm-line"><label>At most <input type="number" min="0" max="1000" value="' + Number(p.rate_per_hour || 0) +
      '" data-rate="' + attr(p.domain) + '|' + attr(p.service) + '" onchange="x11SetRate(this)" aria-label="Times per hour"> times an hour</label>' +
      '<label>Only between <input type="text" value="' + attr(p.window || '') + '" placeholder="Any time" data-window="' +
      attr(p.domain) + '|' + attr(p.service) + '" onchange="x11SetWindow(this)" aria-label="Time window"></label>' +
      '<span class="x11-expiry" data-expiry-text="' + i + '">' + (p.expires_at ? 'Ends ' + esc(expiry) : 'No end date') + '</span>' +
      '<input type="date" data-expiry-date="' + i + '" aria-label="End date">' +
      '<button type="button" class="btn btn-muted btn-xs" data-expiry-set="' + i + '" onclick="x11SetExpiry(this)">' +
      (p.expires_at ? 'Change end date' : 'Set end date') + '</button>' +
      '<button type="button" class="btn btn-muted btn-xs" data-expiry-clear="' + i + '" onclick="x11ClearExpiry(this)"' +
      (p.expires_at ? '' : ' disabled') + '>Remove end date</button></div></div>';
  }

  function x11SetDevice(cb) {
    var id = cb.getAttribute('data-device');
    var action = cb.getAttribute('data-action').split('.');
    var domain = action[0], service = action.slice(1).join('.');
    var p = permissionFor(domain, service);
    if (!p) {
      p = { name: actionWords(domain, service), domain: domain, service: service,
        entities: [], rate_per_hour: 30, window: '', expires_at: 0 };
      S.draft.home_assistant.permissions.push(p);
    }
    p.entities = p.entities || [];
    if (cb.checked) {
      if (p.entities.indexOf(id) === -1) p.entities.push(id);
    } else {
      p.entities = p.entities.filter(function (x) { return x !== id; });
      if (!p.entities.length) {
        S.draft.home_assistant.permissions = permissions().filter(function (x) { return x !== p; });
      }
    }
    renderEditor(); markDirty();
  }

  function x11SetActionMode(sel) {
    var parts = sel.getAttribute('data-action-mode').split('.');
    var domain = parts[0], service = parts.slice(1).join('.');
    var p = permissionFor(domain, service);
    if (!p) {
      p = { name: actionWords(domain, service), domain: domain, service: service,
        entities: [], rate_per_hour: 30, window: '', expires_at: 0 };
      S.draft.home_assistant.permissions.push(p);
    }
    if (sel.value === 'label') {
      p.label_id = (S.labels[0] || {}).id || '';
      p.entities = [];
      if (!p.label_id) { delete p.label_id; toast('No Home Assistant labels are available.', 'error'); }
    } else {
      delete p.label_id;
      p.entities = p.entities || [];
    }
    renderEditor(); markDirty();
  }

  function x11SetLabel(sel) {
    var parts = sel.getAttribute('data-action-label').split('.');
    var domain = parts[0], service = parts.slice(1).join('.');
    var p = permissionFor(domain, service);
    if (!p) return;
    if (!sel.value) return;
    p.label_id = sel.value;
    p.entities = [];
    renderEditor(); markDirty();
  }

  function x11SetRate(input) {
    var parts = input.getAttribute('data-rate').split('|');
    var p = permissionFor(parts[0], parts[1]);
    if (p) { p.rate_per_hour = Math.max(0, Math.min(1000, Number(input.value) || 0)); markDirty(); }
  }
  function x11SetWindow(input) {
    var parts = input.getAttribute('data-window').split('|');
    var p = permissionFor(parts[0], parts[1]);
    if (p) { p.window = input.value.trim(); markDirty(); }
  }
  function x11SetExpiry(btn) {
    var i = Number(btn.getAttribute('data-expiry-set'));
    var p = permissions()[i];
    var input = document.querySelector('[data-expiry-date="' + i + '"]');
    if (!p || !input || !input.value) { toast('Pick a date first.', 'error'); return; }
    p.expires_at = Math.floor(new Date(input.value + 'T23:59:59').getTime() / 1000);
    renderEditor(); markDirty();
  }
  function x11ClearExpiry(btn) {
    var p = permissions()[Number(btn.getAttribute('data-expiry-clear'))];
    if (!p) return;
    p.expires_at = 0;
    renderEditor(); markDirty();
  }
  function x11DeviceSearch(v) { S.deviceSearch = v; var g = el('x11-device-grid'); if (g) { var y = window.scrollY; renderEditor(); window.scrollTo(0, y); } }
  function x11DeviceSettings(v) { S.deviceSettings = !!v; renderEditor(); }
  function x11DeviceOnlyPerm(v) { S.deviceOnlyPerm = !!v; renderEditor(); }
  function x11GoRules() { S.screen = 'agents'; S.tab = 'rules'; renderRules(); }

  function sectionSsh() {
    var ssh = S.draft.ssh || {};
    var hosts = ssh.hosts || [];
    var all = (S.gateways || []).map(function (g) { return g.ip; });
    hosts.forEach(function (h) { if (h !== '*' && all.indexOf(h) === -1) all.push(h); });
    all.sort();
    var hostHtml = all.map(function (ip) {
      return '<label><input type="checkbox" data-ssh-host="' + attr(ip) + '"' + (hosts.indexOf(ip) !== -1 ? ' checked' : '') +
        ' onchange="x11ToggleHost(this)"> <span>' + esc(ip) + '</span></label>';
    }).join('');
    return '<section class="x11-section" id="x11-sec-ssh" data-x11-section="ssh">' +
      head('SSH', 'Which gateways this agent may run commands on. The gateway policy still applies on top.', 'ssh') +
      '<label class="x11-plain">Mode <select data-ssh-mode onchange="x11SetSshMode(this)">' +
      '<option value="read_only"' + (ssh.mode === 'read_only' ? ' selected' : '') + '>Read only</option>' +
      '<option value="approval"' + (ssh.mode === 'approval' ? ' selected' : '') + '>Always approve</option>' +
      '<option value="normal"' + (ssh.mode === 'normal' ? ' selected' : '') + '>Gateway policy</option></select></label>' +
      '<div class="x11-check-grid" style="margin-top:10px"><label><input type="checkbox" data-ssh-any ' +
      (hosts.indexOf('*') !== -1 ? ' checked' : '') + ' onchange="x11ToggleAnyHost(this)"> Any registered gateway <code>*</code></label></div>' +
      '<div class="x11-check-grid" style="margin-top:8px">' + (hostHtml || '<p class="x11-muted">No gateways yet.</p>') + '</div>' +
      jsonBlock('ssh') + '</section>';
  }
  function x11SetSshMode(sel) { S.draft.ssh.mode = sel.value; markDirty(); }
  function x11ToggleHost(cb) {
    var ip = cb.getAttribute('data-ssh-host');
    var hosts = S.draft.ssh.hosts || [];
    if (cb.checked) { if (hosts.indexOf(ip) === -1) hosts.push(ip); }
    else hosts = hosts.filter(function (x) { return x !== ip; });
    S.draft.ssh.hosts = hosts;
    markDirty();
  }
  function x11ToggleAnyHost(cb) {
    var hosts = (S.draft.ssh.hosts || []).filter(function (x) { return x !== '*'; });
    if (cb.checked) {
      if (!window.confirm('Allow this agent on every gateway, including every gateway enrolled later?')) { cb.checked = false; return; }
      hosts.push('*');
    }
    S.draft.ssh.hosts = hosts;
    renderEditor(); markDirty();
  }

  function sectionWebsites() {
    var w = S.draft.websites || {};
    return '<section class="x11-section" id="x11-sec-websites" data-x11-section="websites">' +
      head('Websites', 'Which sites this agent may fetch. Everything not listed is blocked.', 'websites') +
      '<label class="x11-plain"><input type="checkbox" data-web-enabled' + (w.enabled ? ' checked' : '') +
      ' onchange="x11SetWebEnabled(this)"> Web fetch is enabled</label>' +
      '<div style="margin-top:10px"><label class="x11-plain" for="x11-web-allow">Allowed sites (one per line)</label>' +
      '<textarea id="x11-web-allow" class="w-full text-xs" rows="4" data-web-list="allow" oninput="x11SetWebList(this)">' +
      esc((w.allow || []).join('\n')) + '</textarea></div>' +
      '<div style="margin-top:10px"><label class="x11-plain" for="x11-web-deny">Blocked sites (one per line)</label>' +
      '<textarea id="x11-web-deny" class="w-full text-xs" rows="3" data-web-list="deny" oninput="x11SetWebList(this)">' +
      esc((w.deny || []).join('\n')) + '</textarea></div>' + jsonBlock('websites') + '</section>';
  }
  function x11SetWebEnabled(cb) { S.draft.websites.enabled = !!cb.checked; markDirty(); }
  function x11SetWebList(input) {
    var list = input.value.split('\n').map(function (x) { return x.trim(); }).filter(Boolean);
    S.draft.websites[input.getAttribute('data-web-list')] = list;
    markDirty();
  }

  function sectionActivity() {
    var a = S.activity || {};
    var sessions = a.sessions || [], reqs = a.requests || [], calls = a.calls || [];
    var html = '';
    if (sessions.length) {
      html += '<div class="x11-act-sec"><div class="x11-act-h">Sessions</div>' + sessions.map(function (s) {
        return '<div class="x11-act-row"><span class="x11-muted">' + esc(s.id || '') + '</span><span>' +
          esc(s.goal || '(no goal)') + '</span><span class="x11-muted">' + (s.ended_at ? 'closed' : 'open') + '</span></div>';
      }).join('') + '</div>';
    }
    if (reqs.length) {
      html += '<div class="x11-act-sec"><div class="x11-act-h">Last commands</div>' + reqs.map(function (r) {
        return '<div class="x11-act-row"><span class="x11-muted">' + esc(when(r.created_at)) + '</span><span>' +
          esc(r.command || '') + '</span><span class="x11-muted">' + esc(r.status || '') + '</span></div>';
      }).join('') + '</div>';
    }
    if (calls.length) {
      html += '<div class="x11-act-sec"><div class="x11-act-h">Last calls</div>' + calls.map(function (c) {
        return '<div class="x11-act-row"><span class="x11-muted">' + esc(when(c.created_at)) + '</span><span>' +
          esc((c.integration || '') + ' / ' + (c.tool || '')) + '</span><span class="x11-muted">' +
          esc(c.approval || c.outcome || '') + '</span></div>';
      }).join('') + '</div>';
    }
    return '<section class="x11-section" id="x11-sec-activity" data-x11-section="activity">' +
      head('Activity', 'Recent sessions, commands and calls for this agent.', 'activity') +
      '<div class="x11-activity">' + (html || '<p class="x11-plain">No activity yet.</p>') + '</div>' +
      jsonBlock('activity') + '</section>';
  }

  function sectionJson() {
    return '<section class="x11-section" id="x11-sec-json" data-x11-section="json">' +
      head('JSON', 'The whole agent document. Check first; Apply saves all of it or nothing.') +
      '<textarea id="x11-json-doc" class="x11-json-editor" spellcheck="false" aria-label="Agent JSON document" ' +
      'oninput="x11JsonInput(this.value)">' + esc(S.jsonText) + '</textarea>' +
      '<div class="x11-json-error" id="x11-json-error" role="alert">' + esc(S.jsonError) + '</div>' +
      '<div class="x11-btn-row"><button type="button" class="btn btn-sm" id="x11-json-check" onclick="x11JsonCheck()">Check</button>' +
      '<button type="button" class="btn btn-muted btn-sm" id="x11-json-apply" onclick="x11JsonApply()"' +
      (S.jsonChecked ? '' : ' disabled') + '>Apply</button>' +
      '<button type="button" class="btn btn-muted btn-sm" onclick="x11JsonCopy()">Copy</button>' +
      '<button type="button" class="btn btn-muted btn-sm" onclick="x11JsonReset()">Reset</button></div>' +
      '<p class="x11-section-sub">Pasting another agent\u2019s document is the copy feature; the preview names the target agent.</p></section>';
  }

  function x11JsonInput(v) {
    S.jsonText = v; S.jsonError = ''; S.jsonChecked = false;
    var e = el('x11-json-error'); if (e) e.textContent = '';
    syncJsonApplyButton();
  }
  function syncJsonApplyButton() {
    var btn = el('x11-json-apply');
    if (btn) btn.disabled = !S.jsonChecked;
  }
  function x11JsonCopy() {
    var text = S.jsonText;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { toast('JSON copied', 'success'); });
    } else {
      var t = document.createElement('textarea'); t.value = text; document.body.appendChild(t);
      t.select(); document.execCommand('copy'); t.remove(); toast('JSON copied', 'success');
    }
  }
  function x11JsonReset() {
    S.jsonText = JSON.stringify(docOf(S.config), null, 2);
    S.jsonError = ''; S.jsonChecked = false;
    renderEditor(); toast('JSON reset to the saved document', 'success');
  }
  function parseJsonDoc() {
    try { return JSON.parse(S.jsonText); }
    catch (e) { S.jsonError = 'JSON error: ' + e.message; var box = el('x11-json-error'); if (box) box.textContent = S.jsonError; return null; }
  }
  function x11JsonCheck() {
    var doc = parseJsonDoc();
    if (!doc) { S.jsonChecked = false; syncJsonApplyButton(); return; }
    S.jsonSource = doc;
    S.jsonChecked = false;
    checkDocument(doc, 'json');
  }
  function x11JsonApply() {
    if (!S.lastPreview || S.lastPreview.source !== 'json' || !S.pending) { x11JsonCheck(); return; }
    x11ApplyPreview();
  }

  // ── Check / preview / apply ───────────────────────────────────────────
  function x11Check() {
    if (S.section === 'json' && el('x11-json-doc')) {
      var parsed = parseJsonDoc();
      if (!parsed) return;
      checkDocument(parsed, 'form');
      return;
    }
    checkDocument(docOf({ schema_version: S.draft.schema_version, tools: S.draft.tools,
      ssh: S.draft.ssh, websites: S.draft.websites, home_assistant: S.draft.home_assistant }), 'form');
  }

  function checkDocument(document, source) {
    api('/api/agents/' + Number(S.agent.id) + '/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document: document, preview: true, version: S.config.version,
        risk_ack: false, preview_hash: '', resume_ack: false }),
    }).then(function (body) {
      S.pending = { document: document, version: S.config.version, hash: body.preview_hash, source: source };
      S.lastPreview = { body: body, source: source };
      S.jsonError = '';
      if (source === 'json') { S.jsonChecked = true; syncJsonApplyButton(); }
      showPreview();
    }).catch(function (e) {
      if (source === 'json') {
        S.jsonChecked = false; syncJsonApplyButton();
        S.jsonError = errText(e);
        var box = el('x11-json-error'); if (box) box.textContent = S.jsonError;
      } else {
        setNotice(errText(e), 'error');
      }
    });
  }

  function previewModalHtml() {
    return '<div id="x11-preview-modal" class="hidden" role="dialog" aria-modal="true" aria-labelledby="x11-preview-title">' +
      '<div class="x11-preview-card"><h2 id="x11-preview-title">Check before you save</h2>' +
      '<div id="x11-preview-body"></div></div></div>';
  }
  function newAgentModalHtml() {
    return '<div id="x11-new-modal" class="hidden" role="dialog" aria-modal="true" aria-labelledby="x11-new-title">' +
      '<div class="x11-new-card-modal"><h2 id="x11-new-title">New agent</h2>' +
      '<p class="x11-plain">Give the agent a name. The token is shown once.</p>' +
      '<label class="x11-plain" for="x11-new-name">Name</label><input id="x11-new-name" type="text" placeholder="household-agent">' +
      '<div class="x11-btn-row"><button type="button" class="btn btn-sm" id="x11-new-create" onclick="x11CreateAgent()">Create agent</button>' +
      '<button type="button" class="btn btn-muted btn-sm" onclick="x11CloseNew()">Cancel</button></div>' +
      '<div id="x11-new-result"></div></div></div>';
  }

  function previewItems(p) {
    var ha = p.home_assistant || {};
    return { add: ha.add || [], change: ha.change || [], remove: ha.remove || [] };
  }
  function itemKey(item) {
    return (item.domain || '') + '|' + (item.service || '');
  }
  function changedItems(p) {
    var items = previewItems(p);
    return items.add.concat(items.change.map(function (c) { return c.after || {}; }));
  }
  function groupSentence(items) {
    var groups = {};
    items.forEach(function (item) {
      var action = String(item.action || item.service || 'change').toLowerCase();
      var names = (item.entities || []).map(deviceName);
      if (item.label_id) names = [labelName(item.label_id)];
      if (!names.length) names = (item.entity_names || []).slice();
      groups[action] = (groups[action] || []).concat(names);
    });
    return Object.keys(groups).map(function (action) {
      var names = groups[action];
      var n = names.length;
      return action + ' ' + n + ' more device' + (n === 1 ? '' : 's') + ': ' + names.join(', ');
    }).join(' and ');
  }
  function previewSentence(p) {
    var name = (p.agent || {}).name || (S.agent && S.agent.name) || 'This agent';
    var items = previewItems(p);
    if (!items.add.length && !items.change.length && !items.remove.length) {
      return p.summary || name + ' is unchanged.';
    }
    var text = name;
    if (items.add.length) text += ' will be able to ' + groupSentence(items.add);
    else text += ' will change';
    if (items.change.length) text += '. Changed: ' + groupSentence(items.change);
    if (items.remove.length) {
      text += '. Removed: ' + items.remove.map(function (r) {
        return (r.name || r.action || r.service) + ' (' + String(r.action || r.service).toLowerCase() + ')';
      }).join(', ');
    }
    return text + '.';
  }
  function deviceRiskReason(kind) {
    return ({ garage: 'garage doors always need this confirmation',
      garage_door: 'garage doors always need this confirmation',
      gate: 'gates always need this confirmation',
      door: 'doors always need this confirmation',
      lock: 'locks always need this confirmation' })[kind] ||
      'this device always needs this confirmation';
  }
  function riskLine(r, p) {
    if (r.kind === 'category') {
      var action = String(r.service || '').replace(/_/g, ' ');
      return action + ' \u00b7 every device \u2014 ' + (r.reason || 'this action is risky');
    }
    var hit = changedItems(p).filter(function (item) {
      return (item.entities || []).indexOf(r.entity_id) !== -1;
    })[0];
    var name = r.entity_id ? deviceName(r.entity_id) : '';
    if (!name && hit && hit.label_id) name = labelName(hit.label_id);
    if (!name) name = (hit && (hit.entity_names || [])[(hit.entities || []).indexOf(r.entity_id)]) || '';
    var action = hit ? String(hit.action || '').toLowerCase() : 'This action';
    return (action ? action.charAt(0).toUpperCase() + action.slice(1) : 'This action') +
      ' \u00b7 ' + (name || r.entity_id || 'this device') + ' \u2014 ' +
      deviceRiskReason(r.device_class || '');
  }
  function showPreview() {
    var p = S.lastPreview && S.lastPreview.body;
    var modal = el('x11-preview-modal');
    if (!p || !modal) return;
    var risks = p.risky || [];
    var resumes = p.resumes || [];
    var waiting = p.waiting || [];
    var changedKeys = {};
    changedItems(p).forEach(function (item) { changedKeys[itemKey(item)] = true; });
    (resumes || []).forEach(function (r) { changedKeys[(r.domain || '') + '|' + (r.service || '')] = true; });
    var newRisks = [], oldRisks = [];
    risks.forEach(function (r) {
      var key = r.kind === 'device'
        ? (changedItems(p).filter(function (item) { return (item.entities || []).indexOf(r.entity_id) !== -1; })[0] || {})
        : r;
      var isNew = r.kind === 'device'
        ? !!(key && changedKeys[itemKey(key)])
        : !!changedKeys[(r.domain || '') + '|' + (r.service || '')];
      (isNew ? newRisks : oldRisks).push(r);
    });
    function withoutDuplicatedCategory(rows) {
      return rows.filter(function (r) {
        if (r.kind !== 'category') return true;
        var key = (r.domain || '') + '|' + (r.service || '');
        var entities = [];
        changedItems(p).forEach(function (item) {
          if (itemKey(item) === key) entities = entities.concat(item.entities || []);
        });
        return !risks.some(function (other) {
          return other.kind === 'device' && entities.indexOf(other.entity_id) !== -1;
        });
      });
    }
    newRisks = withoutDuplicatedCategory(newRisks);
    oldRisks = withoutDuplicatedCategory(oldRisks);
    var sectionLines = [];
    var seen = {};
    (p.sections || []).forEach(function (s) {
      if (s.section === 'home_assistant' || seen[s.section]) return;
      seen[s.section] = true;
      sectionLines.push('<div>' + esc(s.section) + ': ' + esc(s.summary) + '</div>');
    });
    var riskHtml = '';
    if (risks.length) {
      function block(title, rows) {
        if (!rows.length) return '';
        return '<div class="x11-risk-group"><h3>' + title + '</h3>' +
          rows.map(function (r) { return '<p class="x11-preview-line">' + esc(riskLine(r, p)) + '</p>'; }).join('') + '</div>';
      }
      riskHtml = '<div class="x11-preview-warn"><p class="x11-preview-line"><strong>Risky items</strong></p>' +
        block('New or changed', newRisks) + block('Already allowed, confirm again', oldRisks) +
        '<label class="x11-ack"><input type="checkbox" data-x11-ack="risk" onchange="x11PreviewAck()"> I understand these are risky.</label></div>';
    }
    var body = '<div class="x11-preview-target">Applying to <strong>' + esc((p.agent || {}).name || S.agent.name) + '</strong></div>' +
      '<p class="x11-preview-line">' + esc(previewSentence(p)) + '</p>' +
      (sectionLines.length ? '<div class="x11-preview-line x11-muted">' + sectionLines.join('') + '</div>' : '') +
      riskHtml +
      (resumes.length ? '<div class="x11-preview-warn"><p class="x11-preview-line"><strong>Will resume</strong></p>' +
        resumes.map(function (r) { return '<p class="x11-preview-line">' + esc(r.name || (r.domain + '.' + r.service)) + ' \u2014 ' + esc(reasonWords(r.reason)) + (r.will_pin_to_current_ha ? ' (re-pins to the current Home Assistant)' : '') + '</p>'; }).join('') +
        '<label class="x11-ack"><input type="checkbox" data-x11-ack="resume" onchange="x11PreviewAck()"> Resume these paused permissions.</label></div>' : '') +
      (waiting.length ? '<div class="x11-preview-line"><strong>Will also add, waiting to join</strong><br>' +
        waiting.map(function (w) { return esc((w.entities || []).map(function (e) { return e.name || e.entity_id; }).join(', ')); }).join('<br>') + '</div>' : '') +
      '<p class="x11-preview-line x11-muted">Nothing has changed yet.</p>' +
      '<div class="x11-preview-actions"><button type="button" class="btn btn-muted btn-sm" onclick="x11ClosePreview()">Cancel</button>' +
      '<button type="button" class="btn btn-sm" id="x11-preview-apply" onclick="x11ApplyPreview()"' +
      ((p.risk_ack_required || p.resume_ack_required) ? ' disabled' : '') + '>' +
      (p.risk_ack_required ? 'Allow and save' : 'Apply') + '</button></div>';
    el('x11-preview-body').innerHTML = body;
    modal.classList.remove('hidden');
    var first = modal.querySelector('input[type="checkbox"], button');
    if (first) first.focus();
  }
  function x11PreviewAck() {
    var p = S.lastPreview && S.lastPreview.body;
    var risk = document.querySelector('[data-x11-ack="risk"]');
    var resume = document.querySelector('[data-x11-ack="resume"]');
    var ok = (!p.risk_ack_required || (risk && risk.checked)) &&
             (!p.resume_ack_required || (resume && resume.checked));
    var btn = el('x11-preview-apply'); if (btn) btn.disabled = !ok;
  }
  function x11ClosePreview() {
    var m = el('x11-preview-modal'); if (m) m.classList.add('hidden');
  }
  function x11ApplyPreview() {
    if (!S.pending) return;
    var p = S.lastPreview && S.lastPreview.body;
    var risk = document.querySelector('[data-x11-ack="risk"]');
    var resume = document.querySelector('[data-x11-ack="resume"]');
    var btn = el('x11-preview-apply');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving\u2026'; }
    api('/api/agents/' + Number(S.agent.id) + '/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        document: S.pending.document,
        version: S.pending.version,
        preview: false,
        preview_hash: S.pending.hash,
        risk_ack: !!(p.risk_ack_required && risk && risk.checked),
        resume_ack: !!(p.resume_ack_required && resume && resume.checked),
      }),
    }).then(function () {
      x11ClosePreview();
      toast('Saved', 'success');
      var section = S.section;
      x11OpenAgent(S.agent.id, section);
    }).catch(function (e) {
      x11ClosePreview();
      var code = e.detail && e.detail.code;
      var text = code === 'preview_changed'
        ? 'This agent changed since the preview. The form has been reloaded; review the new preview.'
        : code === 'config_changed'
          ? 'This agent changed since you opened it. The form has been reloaded.'
          : errText(e);
      S.notice = { text: text, kind: 'warn' };
      var section = S.section;
      x11OpenAgent(S.agent.id, section, true);
    });
  }

  function markDirty() {
    var n = sectionDiffCount();
    var bar = el('x11-savebar');
    var count = el('x11-change-count');
    if (count) count.textContent = n + ' change' + (n === 1 ? '' : 's');
    if (bar) bar.hidden = !n;
    syncSavebarSpace();
  }
  function x11Discard() {
    S.draft = docOf(S.config);
    S.jsonText = JSON.stringify(S.draft, null, 2);
    renderEditor();
    toast('Changes discarded', 'success');
  }
  function x11Back() {
    clearAgentHash();
    S.screen = 'agents'; S.agent = null; S.draft = null; S.config = null;
    if (root()) root().style.paddingBottom = '';
    S.tab = 'agents'; renderAgents(); loadAgents();
  }

  function x11ToggleJson(btn) {
    var key = btn.getAttribute('data-json-toggle');
    var block = el('x11-json-' + key);
    if (!block) return;
    var open = block.hidden;
    block.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    btn.textContent = open ? 'Hide JSON' : 'Show JSON';
  }

  function x11Resume(btn) {
    var id = Number(btn.getAttribute('data-resume-id'));
    if (!id) return;
    if (!window.confirm('Resume this permission? It is checked against the limits and protected devices again.')) return;
    api('/api/allowances/' + id + '/resume', { method: 'POST' })
      .then(function () {
        toast('Permission resumed', 'success');
        var section = S.section;
        x11OpenAgent(S.agent.id, section);
      })
      .catch(function (e) { setNotice(errText(e), 'error'); });
  }

  function x11RevokeAgent() {
    if (!window.confirm('Revoke this token? It stops working immediately.')) return;
    api('/api/agents/' + Number(S.agent.id) + '/revoke', { method: 'POST' })
      .then(function () { toast('Token revoked', 'success'); x11Back(); })
      .catch(function (e) { setNotice(errText(e), 'error'); });
  }
  function x11DeleteAgent() {
    if (!window.confirm('Delete this agent? This cannot be undone.')) return;
    api('/api/agents/' + Number(S.agent.id), { method: 'DELETE' })
      .then(function () { toast('Agent deleted', 'success'); x11Back(); })
      .catch(function (e) { setNotice(errText(e), 'error'); });
  }

  function x11NewAgent() {
    var m = el('x11-new-modal'); if (!m) return;
    var result = el('x11-new-result'); if (result) result.innerHTML = '';
    var name = el('x11-new-name'); if (name) name.value = '';
    m.classList.remove('hidden');
    if (name) name.focus();
  }
  function x11CloseNew() {
    var m = el('x11-new-modal'); if (m) m.classList.add('hidden');
    if (S.newCreated) { S.newCreated = false; S.agentsReady = false; x11Back(); }
  }
  function x11CreateAgent() {
    var name = (el('x11-new-name') || {}).value || '';
    name = name.trim();
    if (!name) { toast('Agent name is required', 'error'); return; }
    api('/api/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name }) })
      .then(function (body) {
        var box = el('x11-new-result');
        if (box) box.innerHTML = '<p class="x11-plain">Token (copy now, shown once):</p>' +
          '<div class="x11-token-once">' + esc(body.token) + '</div>' +
          '<div class="x11-btn-row"><button type="button" class="btn btn-sm" onclick="x11CloseNew()">Done</button></div>';
        S.newCreated = true;
      })
      .catch(function (e) { toast(errText(e), 'error'); });
  }

  window.setTimeout(function () {
    if (parseAgentHash() && window.switchView) {
      window.switchView('agents');
    }
  }, 600);

  window.addEventListener('hashchange', function () {
    if (!root()) return;
    var target = parseAgentHash();
    if (target) {
      if (!S.agent || Number(S.agent.id) !== target.id) x11OpenAgent(target.id, target.section);
      else if (S.section !== target.section) { S.section = target.section; renderEditor(); }
    } else {
      x11ShowAgents();
    }
  });

  // Public entry points used by switchView and inline handlers.
  window.x11ShowAgents = x11ShowAgents;
  window.x11SetTab = x11SetTab;
  window.x11ReviewPaused = x11ReviewPaused;
  window.x11ReviewWaiting = x11ReviewWaiting;
  window.x11AcceptJoin = x11AcceptJoin;
  window.x11AcceptAllJoin = x11AcceptAllJoin;
  window.x11DismissJoin = x11DismissJoin;
  window.x11ReofferJoin = x11ReofferJoin;
  window.x11Nav = x11Nav;
  window.x11ToolChoice = x11ToolChoice;
  window.x11SetDevice = x11SetDevice;
  window.x11SetActionMode = x11SetActionMode;
  window.x11SetLabel = x11SetLabel;
  window.x11SetRate = x11SetRate;
  window.x11SetWindow = x11SetWindow;
  window.x11SetExpiry = x11SetExpiry;
  window.x11ClearExpiry = x11ClearExpiry;
  window.x11DeviceSearch = x11DeviceSearch;
  window.x11DeviceSettings = x11DeviceSettings;
  window.x11DeviceOnlyPerm = x11DeviceOnlyPerm;
  window.x11GoRules = x11GoRules;
  window.x11SetSshMode = x11SetSshMode;
  window.x11ToggleHost = x11ToggleHost;
  window.x11ToggleAnyHost = x11ToggleAnyHost;
  window.x11SetWebEnabled = x11SetWebEnabled;
  window.x11SetWebList = x11SetWebList;
  window.x11JsonInput = x11JsonInput;
  window.x11JsonCopy = x11JsonCopy;
  window.x11JsonReset = x11JsonReset;
  window.x11JsonCheck = x11JsonCheck;
  window.x11JsonApply = x11JsonApply;
  window.x11Check = x11Check;
  window.x11ApplyPreview = x11ApplyPreview;
  window.x11ClosePreview = x11ClosePreview;
  window.x11PreviewAck = x11PreviewAck;
  window.x11Discard = x11Discard;
  window.x11ToggleJson = x11ToggleJson;
  window.x11Resume = x11Resume;
  window.x11RevokeAgent = x11RevokeAgent;
  window.x11DeleteAgent = x11DeleteAgent;
  window.x11NewAgent = x11NewAgent;
  window.x11CreateAgent = x11CreateAgent;
  window.x11CloseNew = x11CloseNew;
  window.x11Back = x11Back;
  window.x11More = x11More;
  window.x11ToolFilter = x11ToolFilter;
  window.x11ToolOnlyAllowed = x11ToolOnlyAllowed;
  window.x11ToolGroupToggle = x11ToolGroupToggle;
  window.x11OpenDevices = x11OpenDevices;
  window.x11StartProtect = x11StartProtect;
  window.x11CancelProtect = x11CancelProtect;
  window.x11ProtectedSettings = x11ProtectedSettings;
  window.x11LoadApproverTokens = x11LoadApproverTokens;
  window.x11ToggleTestAgent = x11ToggleTestAgent;
  window.x11MintApproverToken = x11MintApproverToken;
  window.x11RevokeApproverToken = x11RevokeApproverToken;
  window.x11SaveProtected = x11SaveProtected;
  window.x11SaveCeiling = x11SaveCeiling;
  window.x11AddWord = x11AddWord;
  window.x11RemoveWord = x11RemoveWord;
})();

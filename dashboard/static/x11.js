/* X11 Stage 3/4 — Agents, the integration-first Tools pages and the rules for all agents.
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
    deviceHidden: false,
    deviceFilter: 'switch',
    deviceFilters: {},
    toolFilter: '',
    toolOnlyAllowed: false,
    toolOpen: {},
    protectedEditing: false,
    protectedSettings: false,
    jsonChecked: false,
    summary: null,
    tileFilter: 'in_use',
    tileSearch: '',
    integration: '',
    integrationTab: 'tools',
    rawTools: [],
    filesGroups: [],
    editingPermission: null,
    intJsonOpen: false,
    toolJsonOpen: false,
  };
  var HA_TABS = ['tools', 'devices', 'runs', 'files'];
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

  function globMatches(value, pattern) {
    var re = '^' + String(pattern || '').replace(/[.+?^${}()|[\]\\]/g, '\\$&')
      .replace(/\*/g, '.*') + '$';
    return new RegExp(re).test(String(value || ''));
  }
  function approvalFor(name, tool, mapping) {
    var map = mapping || {};
    if (Object.prototype.hasOwnProperty.call(map, name)) return map[name];
    var ns = (tool && tool.namespace) || '';
    if (ns && Object.prototype.hasOwnProperty.call(map, ns + '_*')) return map[ns + '_*'];
    if (ns && Object.prototype.hasOwnProperty.call(map, ns + '.*')) return map[ns + '.*'];
    if (Object.prototype.hasOwnProperty.call(map, '*')) return map['*'];
    return null;
  }
  function outcomeFor(name, tool, cfg) {
    var draft = cfg || S.draft;
    if (!draft) return 'none';
    var tools = draft.tools || {};
    var mode = tools.mode || 'all';
    var allowed = mode !== 'allowlist' ||
      (tools.allow || []).some(function (p) { return globMatches(name, p); });
    if (!allowed) return 'none';
    var principal = (tool && tool.principal_allow) || [];
    if (principal.length) {
      var who = String((S.agent && S.agent.name) || '').toLowerCase();
      if (principal.map(function (p) { return String(p).toLowerCase(); }).indexOf(who) === -1) {
        return 'none';
      }
    }
    if (tool && tool.read_only) return 'run';
    if (S.summary && S.summary.frozen) return 'frozen';
    var approval = approvalFor(name, tool, tools.approval);
    if (tool && tool.always_gate) return 'ask';
    if (approval === 'gated') return 'ask';
    if (approval === 'ungated') return 'run';
    var gate = String((tool && tool.gate_mode) || 'destructive').toLowerCase();
    if (gate === 'all') return 'ask';
    if (gate === 'destructive') return 'depends';
    return 'run';
  }

  function countsFor(cfg, integrations) {
    var draft = cfg || S.draft;
    var tools = toolList(integrations);
    var total = 0, ask = 0, run = 0, depends = 0, frozen = 0;
    tools.forEach(function (t) {
      var o = outcomeFor(t.name, t, draft);
      if (o === 'none') return;
      total += 1;
      if (o === 'ask') ask += 1;
      else if (o === 'run') run += 1;
      else if (o === 'depends') depends += 1;
      else if (o === 'frozen') frozen += 1;
    });
    return { total: total, ask: ask, run: run, depends: depends,
      frozen: frozen };
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
    if (c.depends) parts.push(c.depends + ' depend on the call');
    if (c.frozen) parts.push(c.frozen + ' frozen');
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
  function pinnedIdsFor(p) {
    if (p.label_id) {
      var row = allowanceRowFor(p);
      if (row) {
        try {
          var pins = JSON.parse(row.entities_json || '[]');
          return pins.map(function (x) { return x.entity_id || x; }).filter(Boolean);
        } catch (e) { /* fall through to the label */ }
      }
      return labelMembers(p.label_id, p.domain).map(function (d) { return d.id; });
    }
    return (p.entities || []).map(String);
  }
  function waitingIdsFor(p) {
    var row = p.label_id ? allowanceRowFor(p) : null;
    if (!row) return [];
    try {
      return JSON.parse(row.pending_json || '[]')
        .map(function (x) { return x.entity_id || x; }).filter(Boolean);
    } catch (e) { return []; }
  }
  function permDevices(p) {
    if (p.label_id) {
      var pinned = pinnedIdsFor(p);
      var waiting = waitingIdsFor(p).filter(function (id) { return pinned.indexOf(id) === -1; });
      return 'label \u201c' + labelName(p.label_id) + '\u201d (' + pinned.length +
        (waiting.length ? ' pinned, ' + waiting.length + ' waiting' : '') + ')';
    }
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
          d === p.domain && sv === p.service && !Number(r.revoked_at) &&
          (p.label_id ? r.source_label_id === p.label_id : !r.source_label_id)) return r;
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
    var raw = String(window.location.hash || '');
    var m = /^#agent\/(\d+)(?:\/([a-z]+))?(?:\/([^/]+))?(?:\/([a-z]+))?$/.exec(raw);
    if (!m) return null;
    var section = m[2] || 'overview';
    if (SECTIONS.indexOf(section) === -1) section = 'overview';
    var out = { id: Number(m[1]), section: section, integration: '', tab: '' };
    if (section === 'tools' && m[3]) {
      try { out.integration = decodeURIComponent(m[3]); } catch (e) { out.integration = m[3]; }
      out.tab = HA_TABS.indexOf(m[4]) === -1 ? '' : m[4];
    }
    return out;
  }
  function setAgentHash(id, section, integration, tab) {
    var next = '#agent/' + Number(id) + '/' + (section || 'overview');
    if (section === 'tools' && integration) {
      next += '/' + encodeURIComponent(integration) + (tab ? '/' + tab : '');
    }
    if (window.location.hash !== next) window.history.replaceState(null, '', next);
  }
  function clearAgentHash() {
    if (/^#agent\//.test(window.location.hash || '')) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }
  function applyRoute(target) {
    S.section = target.section || 'overview';
    S.integration = target.integration || '';
    S.integrationTab = target.tab || '';
    if (S.integration && S.section === 'tools' && !S.integrationTab) {
      var group = toolGroup(S.integration);
      S.integrationTab = group && group.kind === 'ha' ? 'tools' : '';
    }
  }
  function x11ShowAgents() {
    if (!root()) return;
    var target = parseAgentHash();
    if (target) {
      if (!S.agent || Number(S.agent.id) !== target.id) {
        x11OpenAgent(target.id, target.section, false, target);
        return;
      }
      var old = S.section + '|' + S.integration + '|' + S.integrationTab;
      applyRoute(target);
      if (old !== S.section + '|' + S.integration + '|' + S.integrationTab) renderEditor();
      return;
    }
    S.integration = ''; S.integrationTab = '';
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
  function x11OpenAgent(id, section, keepNotice, route) {
    S.screen = 'editor';
    var target = route || { section: section };
    applyRoute(target);
    setAgentHash(id, S.section, S.integration, S.integrationTab);
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
      api('/api/agents/' + Number(id) + '/tools-summary').catch(function () { return null; }),
    ]).then(function (out) {
      S.config = out[0];
      S.draft = docOf(out[0]);
      S.agent = out[0].agent;
      S.integrations = out[10] || [];
      S.rawTools = (out[1].integrations) || [];
      var merged = mergeHATools(S.rawTools, S.integrations);
      S.tools = merged.groups;
      S.filesGroups = merged.files;
      S.activity = out[2] || {};
      S.allowances = (out[3] || []).filter(function (r) { return !Number(r.revoked_at); });
      S.gateways = out[4] || [];
      S.devices = out[5].devices || [];
      S.devicesError = out[5].error || '';
      S.labels = out[6].labels || [];
      S.ceiling = out[7] && !out[7].error ? out[7] : null;
      S.policy = out[8] || {};
      S.protected = (out[9].entities || []).map(function (r) { return r.entity_id; });
      S.summary = out[11] || null;
      S.jsonText = JSON.stringify(S.draft, null, 2);
      S.jsonError = '';
      S.lastPreview = null;
      S.editingPermission = null;
      S.intJsonOpen = false;
      S.toolJsonOpen = false;
      defaultDeviceFilter();
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
      return '<button type="button" class="x11-tab" role="tab" data-x11-nav="' + x[0] +
        '" aria-selected="' + (S.section === x[0]) + '" aria-current="' +
        (S.section === x[0]) + '" onclick="x11Nav(\'' + x[0] + '\')">' + x[1] + '</button>';
    }).join('');
    var content;
    if (S.section === 'tools' && S.integration) content = sectionIntegration();
    else if (S.section === 'tools') content = sectionTools();
    else {
      var sectionFn = { overview: sectionOverview, ssh: sectionSsh,
        websites: sectionWebsites, activity: sectionActivity, json: sectionJson };
      content = (sectionFn[S.section] || sectionOverview)();
    }
    r.innerHTML = '<div class="x11-editor">' +
      '<header class="x11-editor-head"><div class="x11-editor-title-row">' +
      '<div><div class="x11-editor-name">' + esc(a.name || 'Agent') + '</div>' +
      '<div class="x11-editor-status">' + (a.revoked ? 'Revoked' : 'Active') +
      ' \u00b7 last active ' + esc(ago(a.last_used_at)) + '</div></div>' +
      '<div class="x11-editor-actions"><button type="button" class="btn btn-muted btn-xs" onclick="x11Back()">All agents</button>' +
      '<button type="button" class="btn btn-muted btn-xs" data-token-actions onclick="x11ShowDanger()">Token</button></div></div>' +
      '<div class="x11-tabs x11-editor-tabs" role="tablist" aria-label="Agent sections">' + navHtml + '</div></header>' +
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

  function x11ShowDanger() {
    if (S.section !== 'overview') x11Nav('overview');
    window.setTimeout(function () {
      var box = document.querySelector('[data-danger-zone]');
      if (box) box.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 60);
  }
  function x11Nav(section) {
    S.section = SECTIONS.indexOf(section) === -1 ? 'overview' : section;
    S.integration = '';
    S.integrationTab = '';
    setAgentHash(S.agent.id, S.section);
    window.scrollTo(0, 0);
    renderEditor();
  }
  function x11OpenIntegration(name) {
    S.section = 'tools';
    S.integration = name || '';
    var group = toolGroup(S.integration);
    S.integrationTab = group && group.kind === 'ha' ? 'tools' : '';
    setAgentHash(S.agent.id, 'tools', S.integration, S.integrationTab);
    window.scrollTo(0, 0);
    renderEditor();
  }
  function x11IntegrationTab(tab) {
    var group = toolGroup(S.integration);
    if (!group || group.kind !== 'ha' || HA_TABS.indexOf(tab) === -1) return;
    S.integrationTab = tab;
    setAgentHash(S.agent.id, 'tools', S.integration, tab);
    window.scrollTo(0, 0);
    renderEditor();
  }
  function x11ToolsBack() {
    S.integration = '';
    S.integrationTab = '';
    S.section = 'tools';
    setAgentHash(S.agent.id, 'tools');
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

  var DEVICE_FILTERS = [
    { key: 'switch', group: 'Things it can switch', label: 'Plugs and switches', category: 'switch' },
    { key: 'light', group: 'Things it can switch', label: 'Lights', category: 'light' },
    { key: 'cover', group: 'Things it can switch', label: 'Covers and garage', category: 'cover' },
    { key: 'climate', group: 'Things it can switch', label: 'Heating and climate', category: 'climate' },
    { key: 'fan', group: 'Things it can switch', label: 'Fans', category: 'fan' },
    { key: 'media', group: 'Things it can switch', label: 'Media players', category: 'media' },
    { key: 'lock', group: 'Things it can switch', label: 'Locks', category: 'lock' },
    { key: 'valve', group: 'Things it can switch', label: 'Valves', category: 'valve' },
    { key: 'button', group: 'Things it can run', label: 'Buttons', category: 'button' },
    { key: 'scene', group: 'Things it can run', label: 'Scenes', category: 'scene' },
    { key: 'script', group: 'Things it can run', label: 'Scripts', category: 'script' },
    { key: 'vacuum', group: 'Things it can run', label: 'Vacuums', category: 'vacuum' },
    { key: 'temperature', group: 'Things it can read', label: 'Temperature', sensor: 'temperature' },
    { key: 'humidity', group: 'Things it can read', label: 'Humidity', sensor: 'humidity' },
    { key: 'motion', group: 'Things it can read', label: 'Motion', sensor: 'motion' },
    { key: 'door', group: 'Things it can read', label: 'Doors and windows', sensor: 'door' },
    { key: 'power', group: 'Things it can read', label: 'Power and energy', sensor: 'power' },
    { key: 'battery', group: 'Things it can read', label: 'Batteries', sensor: 'battery' },
    { key: 'other', group: 'Things it can read', label: 'Other sensors', sensor: 'other' },
  ];
  function usefulDevices() {
    return (S.devices || []).filter(function (d) { return d.useful; });
  }
  function filterMatches(d, key) {
    if (key === 'sensor') return d.category === 'sensor';
    var f = DEVICE_FILTERS.filter(function (x) { return x.key === key; })[0];
    if (!f) return false;
    if (f.category) return d.category === f.category;
    return d.category === 'sensor' && d.sensor_kind === f.sensor;
  }
  function filterCount(key) {
    return usefulDevices().filter(function (d) { return filterMatches(d, key); }).length;
  }
  function callServiceTool() {
    return toolList().filter(function (t) {
      return t.kind === 'ha' && t.tool === 'call_service';
    })[0] || null;
  }
  function callServiceAllowed() {
    var tool = callServiceTool();
    if (!tool) return false;
    return outcomeFor(tool.name, tool, S.draft) !== 'none';
  }
  function ensureCallServiceAllowed() {
    var tool = callServiceTool();
    if (!tool) return;
    var tools = S.draft.tools;
    if ((tools.mode || 'all') === 'allowlist' && (tools.allow || []).indexOf(tool.name) === -1) {
      tools.allow = (tools.allow || []).concat([tool.name]);
    }
  }
  function removeCallService() {
    var tool = callServiceTool();
    if (!tool) return;
    var tools = S.draft.tools;
    if ((tools.mode || 'all') === 'all') {
      tools.mode = 'allowlist';
      tools.allow = toolList().map(function (t) { return t.name; })
        .filter(function (n) { return n !== tool.name; });
    } else {
      tools.allow = (tools.allow || []).filter(function (n) { return n !== tool.name; });
    }
    delete (tools.approval || {})[tool.name];
  }
  function permissionCovers(p, entity) {
    if (!livePermission(p)) return false;
    if (p.label_id) {
      var d = (S.devices || []).filter(function (x) { return x.id === entity; })[0] || {};
      return (d.labels || []).some(function (l) { return l.id === p.label_id; });
    }
    return (p.entities || []).map(String).indexOf(entity) !== -1;
  }
  function permissionForAction(domain, service) {
    return permissions().filter(function (p) {
      return p.domain === domain && p.service === service;
    })[0] || null;
  }
  function hasAllowance(domain, service, entity) {
    return permissions().some(function (p) {
      return p.domain === domain && p.service === service && permissionCovers(p, entity);
    });
  }
  function pickPermissionForAction(domain, service) {
    return permissions().filter(function (p) {
      return p.domain === domain && p.service === service && !p.label_id;
    })[0] || null;
  }
  function addAllowanceEntity(domain, service, entity) {
    // A label permission and a pick permission can coexist for one action:
    // the label covers its members and a pick adds one device outside it.
    var p = pickPermissionForAction(domain, service);
    if (!p) {
      p = { name: actionWords(domain, service), domain: domain, service: service,
        entities: [], rate_per_hour: 30, window: '', expires_at: 0 };
      S.draft.home_assistant.permissions.push(p);
    }
    p.entities = p.entities || [];
    if (p.entities.map(String).indexOf(entity) === -1) p.entities.push(entity);
    return true;
  }
  function removeAllowanceEntity(domain, service, entity) {
    var p = pickPermissionForAction(domain, service);
    if (!p) {
      var label = permissionForAction(domain, service);
      if (label && label.label_id) {
        setNotice('This device runs because of the label \u201c' + labelName(label.label_id) +
          '\u201d. Remove the device from that label in Home Assistant.', 'warn');
      }
      return false;
    }
    p.entities = (p.entities || []).filter(function (x) { return x !== entity; });
    if (!p.entities.length) {
      S.draft.home_assistant.permissions = permissions().filter(function (x) { return x !== p; });
    }
    return true;
  }
  function ceilingAllows(domain, service) {
    if (!S.ceiling) return true;
    var type = (S.ceiling.catalogue || []).filter(function (t) { return t.domain === domain; })[0];
    if (!type) return false;
    return (type.actions || []).some(function (a) { return a.service === service && a.enabled; });
  }
  function actionsFor(d) {
    if (d.category === 'cover') {
      return [{ service: 'open_cover', label: 'Open' }, { service: 'close_cover', label: 'Close' }];
    }
    if (d.category === 'media') {
      return [{ service: 'media_play', label: 'Play' }, { service: 'media_pause', label: 'Pause' }];
    }
    if (d.category === 'switch' || d.category === 'light' ||
        d.category === 'fan' || d.category === 'climate') {
      return [{ service: 'turn_on', label: 'On' }, { service: 'turn_off', label: 'Off' }];
    }
    return [];
  }
  function deviceState(d) {
    var state = String(d.state || 'unknown');
    if (d.category !== 'sensor') return state;
    if (d.sensor_kind === 'temperature') return state + ' ' + (d.unit || '\u00b0C');
    if (d.sensor_kind === 'humidity' || d.sensor_kind === 'battery') {
      return state + (d.unit || '%');
    }
    if (d.sensor_kind === 'power') return state + (d.unit ? ' ' + d.unit : ' W');
    if (d.sensor_kind === 'motion') return state === 'on' ? 'Motion detected' : 'Clear';
    if (d.sensor_kind === 'door') return state === 'on' ? 'Open' : 'Closed';
    return state;
  }
  function canRead() {
    var names = ['get_entity', 'list_entities'];
    return names.some(function (name) {
      var tool = toolList().filter(function (t) { return t.kind === 'ha' && t.tool === name; })[0];
      return tool && outcomeFor(tool.name, tool, S.draft) !== 'none';
    });
  }
  function devicePill(d, action) {
    var domain = d.domain;
    var service = action.service;
    var state;
    if (d.protected) state = 'protected';
    else if (!callServiceAllowed() || !ceilingAllows(domain, service)) state = 'none';
    else if (hasAllowance(domain, service, d.id)) state = 'run';
    else state = 'ask';
    if (state === 'protected') {
      return '<span class="x11-pill x11-pill-protected" data-device-pill="' + attr(d.id) + '" ' +
        'data-state="protected">Protected</span> ' +
        '<a class="x11-pill-link" href="#" onclick="x11GoRules();return false;">Rules for all agents</a>';
    }
    var word = ({ none: 'not allowed', ask: 'asks you', run: 'on its own' })[state];
    return '<button type="button" class="x11-pill x11-pill-' + state + '" ' +
      'data-device-pill="' + attr(d.id) + '" data-device-action="' + attr(domain + '.' + service) +
      '" data-device="' + attr(d.id) + '" data-state="' + state + '" ' +
      'aria-label="' + attr(action.label + ' for ' + d.name + ': ' + word) +
      '" onclick="x11DeviceChoice(this)"><span class="x11-pill-label">' + esc(action.label) +
      '</span><span class="x11-pill-word">' + esc(word) + '</span></button>';
  }
  function readingLine(d) {
    if (d.category === 'sensor') {
      var read = canRead();
      return '<div class="x11-sensor-reading"><b>' + esc(deviceState(d)) + '</b>' +
        (read ? ' <span class="x11-can-read">Can read</span>'
          : ' <span class="x11-cant-read">Can\u2019t read</span> ' +
            '<a href="#" onclick="x11IntegrationTab(\'tools\');return false;">Tools</a>') + '</div>';
    }
    return '';
  }
  function protectedDeviceHtml() {
    return '<span class="x11-pill x11-pill-protected">Protected</span> ' +
      '<a class="x11-pill-link" href="#" onclick="x11GoRules();return false;">Rules for all agents</a>';
  }
  function deviceRowHtml(d, hiddenMode) {
    var actions = hiddenMode ? [] : actionsFor(d);
    var pills = d.protected ? protectedDeviceHtml()
      : actions.map(function (a) { return devicePill(d, a); }).join('');
    return '<div class="x11-device-row' + (d.protected ? ' x11-locked' : '') + '" data-device-row="' +
      attr(d.id) + '">' +
      '<div class="x11-device-identity"><div class="x11-device-name">' + esc(d.name) + '</div>' +
      '<div class="x11-device-id">' + esc(d.id) + '</div></div>' +
      '<div class="x11-device-readout">' +
      (d.category === 'sensor' ? '' :
        '<div class="x11-device-state">' + esc(deviceState(d)) + '</div>') +
      readingLine(d) + '</div>' +
      '<div class="x11-device-pills">' + pills + '</div></div>';
  }
  function roomReadings(rows) {
    var temp = rows.filter(function (d) { return d.sensor_kind === 'temperature'; })[0];
    var hum = rows.filter(function (d) { return d.sensor_kind === 'humidity'; })[0];
    var motion = rows.filter(function (d) { return d.sensor_kind === 'motion'; })[0];
    var bits = [];
    if (temp) bits.push(deviceState(temp));
    if (hum) bits.push(deviceState(hum));
    if (motion) bits.push(deviceState(motion));
    return bits.join(' \u00b7 ');
  }
  function deviceGroupsHtml(rows, hiddenMode) {
    var groups = {};
    rows.forEach(function (d) {
      var area = d.area || 'No area';
      (groups[area] = groups[area] || []).push(d);
    });
    return Object.keys(groups).sort().map(function (area) {
      var roomDevices = hiddenMode ? [] : usefulDevices().filter(function (d) {
        return (d.area || 'No area') === area;
      });
      var readings = hiddenMode ? '' : roomReadings(roomDevices);
      return '<section class="x11-room" data-room="' + attr(area) + '">' +
        '<h3 class="x11-room-head">' + esc(area) +
        (readings ? '<span class="x11-room-readings">' + esc(readings) + '</span>' : '') + '</h3>' +
        groups[area].map(function (d) { return deviceRowHtml(d, hiddenMode); }).join('') + '</section>';
    }).join('');
  }
  function deviceFilterChips() {
    var sensorCount = usefulDevices().filter(function (d) {
      return d.category === 'sensor';
    }).length;
    var chips = '<button type="button" class="x11-chip" data-device-filter="sensor" ' +
      'aria-pressed="' + (S.deviceFilter === 'sensor') + '" onclick="x11DeviceFilter(\'sensor\')">' +
      'Sensors ' + sensorCount + '</button>';
    return chips + DEVICE_FILTERS.map(function (f) {
      var n = filterCount(f.key);
      if (!n) return '';
      return '<button type="button" class="x11-chip" data-device-filter="' + f.key +
        '" aria-pressed="' + (S.deviceFilter === f.key) + '" onclick="x11DeviceFilter(\'' + f.key +
        '\')">' + esc(f.label) + ' ' + n + '</button>';
    }).join('');
  }
  function deviceRailHtml() {
    var groups = {};
    DEVICE_FILTERS.forEach(function (f) {
      var n = filterCount(f.key);
      if (!n) return;
      (groups[f.group] = groups[f.group] || []).push(
        '<button type="button" class="x11-rail-item" data-device-filter="' + f.key +
        '" aria-pressed="' + (S.deviceFilter === f.key) + '" onclick="x11DeviceFilter(\'' +
        f.key + '\')"><span>' + esc(f.label) + '</span><span>' + n + '</span></button>');
    });
    return Object.keys(groups).map(function (g) {
      return '<div class="x11-rail-group"><div class="x11-rail-head">' + esc(g) + '</div>' +
        groups[g].join('') + '</div>';
    }).join('');
  }
  function devicesTabHtml(group) {
    var all = usefulDevices();
    var hidden = (S.devices || []).filter(function (d) { return !d.useful; });
    var rows = all.filter(function (d) { return filterMatches(d, S.deviceFilter); });
    var q = (S.deviceSearch || '').toLowerCase();
    if (q) {
      rows = rows.filter(function (d) {
        return (d.name || '').toLowerCase().indexOf(q) !== -1 ||
          (d.id || '').toLowerCase().indexOf(q) !== -1;
      });
    }
    if (S.deviceOnlyPerm) {
      rows = rows.filter(function (d) {
        return permissions().some(function (p) {
          return permissionCovers(p, d.id) &&
            (p.domain === d.domain || (d.category === 'sensor' && p.domain === 'sensor'));
        });
      });
    }
    var hiddenRows = hidden.filter(function (d) {
      if (!S.deviceHidden) return false;
      return true;
    });
    var line = '<div class="x11-hidden-line" data-hidden-line>' + hidden.length +
      ' settings, diagnostic and helper entities are hidden. ' +
      '<button type="button" class="link-btn" data-show-hidden onclick="x11ShowHidden()">' +
      (S.deviceHidden ? 'Hide them' : 'Show them') + '</button></div>';
    return '<div class="x11-devices-tab" data-devices-tab>' +
      '<p class="x11-int-sentence">Every device Home Assistant knows about, grouped by room. ' +
      'Tap a pill to cycle not allowed \u2192 asks you \u2192 runs on its own.</p>' +
      '<div class="x11-device-layout"><aside class="x11-device-rail" data-device-rail aria-label="Device filters">' +
      deviceRailHtml() + '</aside><div class="x11-device-main">' +
      '<div class="x11-device-chips" data-device-chips role="group" aria-label="Device filters">' + deviceFilterChips() + '</div>' +
      '<div class="x11-device-tools"><input id="x11-device-search" type="search" placeholder="Search devices" aria-label="Search devices" ' +
      'value="' + attr(S.deviceSearch) + '" oninput="x11DeviceSearch(this.value)">' +
      '<label class="x11-plain"><input type="checkbox" onchange="x11DeviceOnlyPerm(this.checked)"' +
      (S.deviceOnlyPerm ? ' checked' : '') + '> Only devices ' + esc(S.agent ? S.agent.name : 'the agent') + ' can use</label></div>' +
      line +
      (rows.length ? deviceGroupsHtml(rows, false) : '<p class="x11-plain">No devices match this filter.</p>') +
      (hiddenRows.length ? '<div class="x11-hidden-group" data-hidden-group><h3>Settings, diagnostics and helpers</h3>' +
        deviceGroupsHtml(hiddenRows, true) + '</div>' : '') +
      '</div></div></div>';
  }
  function x11DeviceFilter(key) {
    S.deviceFilter = key;
    renderEditor();
  }
  function x11ShowHidden() {
    S.deviceHidden = !S.deviceHidden;
    renderEditor();
  }
  function x11CycleDevice(domain, service, entity) {
    var d = (S.devices || []).filter(function (x) { return x.id === entity; })[0];
    if (!d || d.protected) return;
    if (!ceilingAllows(domain, service)) {
      setNotice('The limits for all agents do not allow ' + domain + '.' + service + '.', 'warn');
      return;
    }
    var state;
    if (!callServiceAllowed()) state = 'none';
    else if (hasAllowance(domain, service, entity)) state = 'run';
    else state = 'ask';
    if (state === 'none') {
      ensureCallServiceAllowed();
    } else if (state === 'ask') {
      if (!addAllowanceEntity(domain, service, entity)) return;
    } else {
      if (!removeAllowanceEntity(domain, service, entity)) return;
      if (!permissions().filter(livePermission).length) removeCallService();
    }
    renderEditor();
    markDirty();
  }

  function permissionSource(p) {
    if (p.label_id) {
      var members = labelMembers(p.label_id, p.domain);
      return 'Follows the label \u201c' + labelName(p.label_id) + '\u201d (' +
        members.length + ' device' + (members.length === 1 ? '' : 's') + ')';
    }
    var names = (p.entities || []).map(deviceName);
    var shown = names.slice(0, 6);
    if (names.length > 6) shown.push('+' + (names.length - 6) + ' more');
    return names.length ? names.length + ' device' + (names.length === 1 ? '' : 's') +
      ' picked: ' + shown.join(', ') : 'No devices picked';
  }
  function permissionLimits(p) {
    var parts = ['At most ' + Number(p.rate_per_hour || 0) + ' an hour'];
    parts.push(p.window ? 'only between ' + p.window : 'any time');
    parts.push(p.expires_at ? 'ends ' + when(p.expires_at) : 'no end date');
    return parts.join(', ') + '.';
  }
  function permissionCardHtml(p, i) {
    var paused = p.suspended_at;
    return '<article class="x11-perm-card' + (paused ? ' x11-perm-paused' : '') +
      '" data-permission-card="' + i + '"><div class="x11-perm-card-head">' +
      '<div><h3>' + esc(actionWords(p.domain, p.service)) + '</h3>' +
      '<p class="x11-perm-source">' + esc(permissionSource(p)) + '</p></div>' +
      '<button type="button" class="btn btn-muted btn-xs" data-edit-permission="' + i +
      '" onclick="x11EditPermission(' + i + ')">Edit</button></div>' +
      '<p class="x11-perm-limits">' + esc(permissionLimits(p)) + '</p>' +
      (paused ? '<div class="x11-paused-item"><div class="x11-paused-reason">Paused: ' +
        esc(reasonWords(p.suspended_reason)) + ' \u00b7 since ' + esc(when(p.suspended_at)) +
        '</div><p class="x11-plain">Editing this permission resumes it.</p>' +
        (allowanceRowFor(p) ? '<button type="button" class="btn btn-xs" data-resume-id="' +
          Number(allowanceRowFor(p).id) + '" onclick="x11Resume(this)">Resume</button>' :
          '<span class="x11-muted">Resume from Tools after the next refresh.</span>') + '</div>' : '') +
      (S.editingPermission === i ? '<div class="x11-perm-edit">' +
        actionHeadHtml({ id: p.domain + '.' + p.service, domain: p.domain,
          service: p.service, label: actionWords(p.domain, p.service) }) +
        permEditor(p, i) + '</div>' : '') + '</article>';
  }
  function runsTabHtml(group) {
    var perms = permissions();
    var waits = waitingRows(S.agent && S.agent.id);
    var cards = perms.map(permissionCardHtml).join('');
    var help = '<div class="x11-perm-help" data-perm-help>' +
      '<p><b>Pick devices</b> Only the devices you tick. No labels needed.</p>' +
      '<p><b>Follow a Home Assistant label</b> The devices come from Home Assistant. ' +
      'Remove the label there and the agent loses the device at once. Add the label to a new ' +
      'device and it waits here for your one-click Accept, so a change in Home Assistant alone ' +
      'never gives an agent a new device.</p></div>';
    return '<div class="x11-runs-tab" data-runs-tab>' +
      '<p class="x11-int-sentence">Each card is one permission: what it does, where the devices come from, ' +
      'and the limits that apply.</p>' +
      '<div class="x11-btn-row"><button type="button" class="btn btn-sm" data-add-permission ' +
      'onclick="x11AddPermission()">Add a permission</button></div>' +
      (cards ? '<div class="x11-perm-cards">' + cards + '</div>' :
        '<p class="x11-plain">No permissions yet. Add one, or pick a device under Devices.</p>') +
      (waits.length ? '<div class="x11-waiting-list x11-waiting-grid" data-waiting-grid>' +
        waits.map(waitingRowHtml).join('') + '</div>' : '') +
      help +
      '<p class="x11-plain"><a href="#" onclick="x11GoRules();return false;">Rules for all agents</a> ' +
      'holds the limits, protected devices and the actions no agent may use.</p></div>';
  }
  function x11EditPermission(i) {
    S.editingPermission = S.editingPermission === i ? null : i;
    renderEditor();
  }
  function x11AddPermission() {
    S.integrationTab = 'devices';
    S.section = 'tools';
    setAgentHash(S.agent.id, 'tools', S.integration, 'devices');
    setNotice('Pick a room and tap a device pill. The first tap adds a permission that runs on its own.', '');
    renderEditor();
  }
  function filesFooterHtml(group) {
    var files = filesForHA(group.integration);
    if (!files.length) {
      return '<p class="x11-plain">No Files integration is linked to this Home Assistant. ' +
        'Add an Advanced SSH &amp; Web Terminal integration on the Integrations page.</p>';
    }
    var cfg = integrationConfig(files[0].integration);
    var fingerprint = cfg.host_fingerprint || '';
    return '<div class="x11-files-footer" data-files-footer>' +
      (fingerprint
        ? '<p class="x11-plain"><strong>Pinned host key ' + esc(fingerprint) + '</strong></p>' +
          '<p class="x11-section-sub">Compare this with the add-on: ' +
          '<code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code></p>'
        : '<p class="x11-plain">The host key is not pinned yet. Run Test under Integrations to pin it, ' +
          'then compare the fingerprint with the add-on before the first write.</p>') +
      '<p class="x11-section-sub">Writes always ask you; the pinned key is checked on every Files call.</p>' +
      '<button type="button" class="btn btn-muted btn-xs" onclick="switchView(\'integrations\')">Open Integrations</button></div>';
  }

  function sectionOverview() {
    var tiles = currentTiles().filter(function (t) { return t.in_use; });
    var perms = permissions();
    var live = perms.filter(livePermission);
    var paused = perms.filter(function (p) { return !!p.suspended_at; });
    var ledger = tiles.map(function (t) {
      return '<a class="x11-ledger-row" href="' + attr(tileHref(t)) + '" data-ledger="' + attr(t.name) + '">' +
        '<span class="x11-ledger-name"><span class="x11-tile-mark" aria-hidden="true">' + esc(t.mark) + '</span>' +
        '<span><b>' + esc(t.name) + '</b><span class="x11-ledger-kind">' + esc(t.kind_words) + '</span></span></span>' +
        '<span class="x11-ledger-bar">' + accessBar(t.run, t.ask, t.none, tileBarLabel(t), t.depends, t.frozen) + '</span>' +
        '<span class="x11-ledger-count">' + t.run + ' run \u00b7 ' + t.ask + ' ask' +
        (t.depends ? ' \u00b7 ' + t.depends +
          (t.depends === 1 ? ' depends' : ' depend') + ' on the call' : '') +
        (t.frozen ? ' \u00b7 ' + t.frozen + ' frozen' : '') + '</span>' +
        (t.extra ? '<span class="x11-ledger-extra">' + esc(t.extra) + '</span>' : '') + '</a>';
    }).join('');
    var permCards = perms.filter(function (p) { return !p.suspended_at; })
      .map(function (p) { return permissionCardHtml(p, perms.indexOf(p)); }).join('');
    var pausedCards = paused.map(function (p) {
      return permissionCardHtml(p, perms.indexOf(p));
    }).join('');
    var waits = waitingRows(S.agent && S.agent.id);
    var waitHtml = waits.length ? '<div class="x11-waiting-list" data-waiting-list>' +
      '<h3>Waiting to join</h3>' + waits.map(waitingRowHtml).join('') + '</div>' : '';
    var tests = '<details class="x11-e2e"><summary>Test agent and E2E approver tokens</summary>' +
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
      '</details>';
    return '<section class="x11-section" id="x11-sec-overview" data-x11-section="overview">' +
      '<div class="x11-section-head"><div><h2 class="x11-section-title">Overview</h2>' +
      '<p class="x11-section-sub">What this agent can do on its own, in plain words.</p></div>' +
      '<button type="button" class="btn btn-muted btn-xs" data-json-toggle="overview" aria-expanded="false" ' +
      'onclick="x11ToggleJson(this)">Show JSON</button></div>' +
      '<p class="x11-overview-summary" data-overview-summary>' + esc(currentSummary()) + '</p>' +
      '<div class="x11-ledger" data-overview-ledger>' + (ledger ||
        '<p class="x11-plain">Nothing is in use yet. Open Tools to switch something on.</p>') + '</div>' +
      '<h3 class="x11-subhead">Home Assistant permissions</h3>' +
      (permCards ? '<div class="x11-perm-cards">' + permCards + '</div>' :
        '<p class="x11-plain">Nothing runs without asking yet. Choose a device under Tools \u2192 Devices.</p>') +
      (paused.length ? '<div class="x11-paused-block" data-paused-block><h3 class="x11-subhead">Paused</h3>' +
        '<p class="x11-section-sub">The limits or Home Assistant itself paused these. They do not run until resumed.</p>' +
        '<div class="x11-perm-cards">' + pausedCards + '</div></div>' : '') +
      waitHtml + tests +
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
  function decorateToolGroups(groups) {
    (groups || []).forEach(function (g) {
      var ns = String(g.integration || '').replace(/[^A-Za-z0-9_]/g, '_').toLowerCase();
      (g.tools || []).forEach(function (t) {
        t.gate_mode = g.gate_mode || 'destructive';
        t.namespace = ns;
        t.principal_allow = t.principal_allow || [];
      });
    });
  }
  function mergeHATools(groups, integrations) {
    decorateToolGroups(groups);
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
        children.push({ name: i.name, parent: parent, integration: i });
      }
    });
    if (!children.length) return { groups: groups, files: [] };
    var byName = {};
    groups.forEach(function (g) { byName[g.integration] = g; });
    var files = [];
    children.forEach(function (c) {
      var group = byName[c.name];
      var parent = byName[c.parent];
      if (!group || !parent) return;
      (group.tools || []).forEach(function (tool) {
        parent.tools.push(Object.assign({}, tool, { files: true,
          fileIntegration: c.name }));
      });
      files.push({ name: c.name, parent: c.parent, group: group,
        integration: c.integration });
    });
    return {
      groups: groups.filter(function (g) {
        return !children.some(function (c) { return c.name === g.integration; });
      }),
      files: files,
    };
  }

  function toolGroup(name) {
    var group = (S.tools || []).filter(function (g) { return g.integration === name; })[0];
    if (group) return group;
    if (name === 'SSH') return { integration: 'SSH', kind: 'ssh', tools: [], virtual: true };
    if (name === 'Websites') {
      return { integration: 'Websites', kind: 'websites', tools: [], virtual: true };
    }
    return null;
  }
  function toolsOf(group) {
    return ((group || {}).tools || []).filter(function (t) { return t.enabled !== false; });
  }
  function groupForKind(kind) {
    return (S.tools || []).filter(function (g) { return g.kind === kind; })[0] || null;
  }
  function filesForHA(name) {
    return (S.filesGroups || []).filter(function (f) { return f.parent === name; });
  }
  function haFilesTools(name) {
    var out = [];
    filesForHA(name).forEach(function (f) {
      (f.group.tools || []).forEach(function (t) { out.push(t); });
    });
    return out;
  }
  function defaultDeviceFilter() {
    var hasSwitch = permissions().some(function (p) { return p.domain === 'switch'; });
    S.deviceFilter = hasSwitch ? 'switch' : 'light';
  }
  function livePermission(p) {
    if (p.suspended_at) return false;
    var expires = Number(p.expires_at || 0);
    return !expires || expires * 1000 > Date.now();
  }
  function permissionEntities(p) {
    if (p.label_id) return pinnedIdsFor(p);
    return (p.entities || []).map(String);
  }
  function coveredDeviceRows() {
    var ids = {};
    permissions().filter(livePermission).forEach(function (p) {
      permissionEntities(p).forEach(function (id) { ids[String(id).toLowerCase()] = true; });
    });
    return (S.devices || []).filter(function (d) { return ids[d.id]; });
  }
  function deviceNoun(rows) {
    var cats = {};
    (rows || []).forEach(function (d) { cats[d.category || ''] = true; });
    var keys = Object.keys(cats);
    if (keys.length === 1 && keys[0] === 'switch') return 'plugs';
    if (keys.length === 1 && keys[0] === 'light') return 'lights';
    if (keys.length === 1 && keys[0] === 'cover') return 'covers';
    return 'devices';
  }
  function currentTiles() {
    if (S.summary && S.summary.tiles && !isDirty()) return S.summary.tiles;
    var out = [];
    (S.tools || []).forEach(function (g) {
      var tools = toolsOf(g);
      var run = 0, ask = 0, depends = 0, frozen = 0;
      tools.forEach(function (tool) {
        var o = outcomeFor(tool.name, tool, S.draft);
        if (o === 'run') run += 1;
        else if (o === 'ask') ask += 1;
        else if (o === 'depends') depends += 1;
        else if (o === 'frozen') frozen += 1;
      });
      var extra = '';
      var inUse = run + ask + depends + frozen > 0;
      var devices = [];
      if (g.kind === 'ha') {
        devices = coveredDeviceRows();
        if (devices.length) extra = devices.length + ' ' + deviceNoun(devices) + ' run on their own';
        inUse = inUse || !!devices.length;
      }
      out.push({
        key: g.integration, name: g.integration, kind: g.kind,
        kind_words: kindWords(g.kind), mark: markFor(g.integration, g.kind),
        total: tools.length, run: run, ask: ask,
        depends: depends, frozen: frozen,
        none: Math.max(0, tools.length - run - ask - depends - frozen),
        usable: tools.length - Math.max(0, tools.length - run - ask - depends - frozen),
        in_use: inUse, extra: extra, virtual: false,
      });
    });
    var ssh = (S.draft.ssh || {});
    var hosts = ssh.hosts || [];
    var sshRun = hosts.length && ssh.mode === 'normal' ? 1 : 0;
    var sshAsk = hosts.length && ssh.mode !== 'normal' ? 1 : 0;
    out.push({ key: 'SSH', name: 'SSH', kind: 'ssh', kind_words: 'SSH',
      mark: 'SS', total: 1, run: sshRun, ask: sshAsk, depends: 0, frozen: 0,
      none: 1 - sshRun - sshAsk, usable: sshRun + sshAsk,
      in_use: !!hosts.length, virtual: true,
      extra: hosts.length === 1 && hosts[0] === '*' ? 'Any gateway'
        : (hosts.length ? hosts.length + ' hosts' : 'No hosts yet') });
    var web = (S.draft.websites || {});
    var sites = (web.allow || []).length;
    out.push({ key: 'Websites', name: 'Websites', kind: 'websites',
      kind_words: 'Web', mark: 'WB', total: 1,
      run: web.enabled && sites ? 1 : 0, ask: 0, depends: 0, frozen: 0,
      none: web.enabled && sites ? 0 : 1,
      usable: web.enabled && sites ? 1 : 0, in_use: !!web.enabled,
      virtual: true,
      extra: sites ? sites + ' sites allowed' : 'No sites allowed' });
    out.sort(function (a, b) {
      if (a.in_use !== b.in_use) return a.in_use ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    return out;
  }
  function currentSummary(tiles) {
    if (!tiles && S.summary && S.summary.summary && !isDirty()) return S.summary.summary;
    var list = tiles || currentTiles();
    var tools = 0, run = 0, ask = 0, depends = 0, frozen = 0, used = 0;
    list.forEach(function (t) {
      tools += t.usable; run += t.run; ask += t.ask;
      depends += t.depends || 0; frozen += t.frozen || 0;
      if (t.in_use) used += 1;
    });
    var devices = coveredDeviceRows();
    var name = (S.agent && S.agent.name) || 'This agent';
    if (!tools) {
      if (devices.length) return name + ' has no tools switched on. It can still use ' +
        devices.length + ' ' + deviceNoun(devices) + ' through Home Assistant.';
      return name + ' has no tools switched on yet.';
    }
    var text = name + (frozen ? ' has ' : ' can use ') + tools + ' tools in ' +
      used + ' of ' + list.length + ' integrations. ' + ask + ' ask you first; ' +
      run + ' run on their own';
    if (depends) text += '; ' + depends + ' depend on the call';
    if (devices.length) text += ', plus ' + devices.length + ' ' + deviceNoun(devices);
    if (frozen) text += '. Emergency Freeze is on: ' + frozen + ' writes are paused';
    return text + '.';
  }
  function kindWords(kind) {
    return ({ ha: 'Home', sonarr: 'TV', radarr: 'TV', prowlarr: 'TV',
      bazarr: 'TV', jellyfin: 'TV', seerr: 'TV', trains: 'Trains',
      proxmox: 'Servers', pihole: 'Network', omada: 'Network', npm: 'Network',
      pulse: 'Health', webfetch: 'Web', commute: 'Commute', ha_ssh: 'Files',
      custom: 'Other', ssh: 'SSH', websites: 'Web' })[kind] || kind || 'Other';
  }
  function markFor(name, kind) {
    var known = { 'Home Assistant': 'HA', 'Sonarr': 'SO', 'Radarr': 'RA',
      'Prowlarr': 'PR', 'Bazarr': 'BA', 'Jellyfin': 'JE', 'Seerr': 'SE',
      'Pi-hole': 'PI', 'Omada': 'OM', 'Proxmox': 'PX', 'Pulse': 'PU',
      'Trains': 'TR', 'Commute': 'CO', 'Web fetch': 'WF' };
    if (known[name]) return known[name];
    var letters = String(name || '').replace(/[^A-Za-z0-9]/g, '');
    return (letters.slice(0, 2) || kind || '??').toUpperCase();
  }
  function accessBar(run, ask, none, label, depends, frozen) {
    depends = depends || 0; frozen = frozen || 0;
    var total = run + ask + none + depends + frozen;
    if (!total) {
      return '<span class="x11-accessbar x11-accessbar-empty" role="img" aria-label="' +
        attr(label || 'No tools') + '"><span class="x11-ab-none" style="flex:1"></span></span>';
    }
    return '<span class="x11-accessbar" role="img" aria-label="' + attr(label) + '">' +
      (run ? '<span class="x11-ab-run" style="flex:' + run + '"></span>' : '') +
      (ask ? '<span class="x11-ab-ask" style="flex:' + ask + '"></span>' : '') +
      (depends ? '<span class="x11-ab-depends" style="flex:' + depends + '"></span>' : '') +
      (frozen ? '<span class="x11-ab-frozen" style="flex:' + frozen + '"></span>' : '') +
      (none ? '<span class="x11-ab-none" style="flex:' + none + '"></span>' : '') +
      '</span>';
  }
  function tileBarLabel(t) {
    if (!t.total) return t.name + ': no tools';
    var label = t.run + ' of ' + t.total + ' tools run on their own, ' + t.ask +
      ' ask you first';
    if (t.depends) label += ', ' + t.depends + ' depend on the call';
    if (t.frozen) label += ', ' + t.frozen + ' frozen';
    return label + ', ' + t.none + ' not allowed';
  }
  function tileHref(t) {
    return '#agent/' + Number(S.agent.id) + '/tools/' + encodeURIComponent(t.name);
  }
  function tileHtml(t) {
    var empty = !t.usable;
    return '<a class="x11-tile' + (empty ? ' x11-tile-empty' : '') +
      '" data-integration-tile="' + attr(t.name) + '" href="' + attr(tileHref(t)) + '">' +
      '<span class="x11-tile-top"><span class="x11-tile-mark" aria-hidden="true">' +
      esc(t.mark) + '</span><span class="x11-tile-kind">' + esc(t.kind_words) + '</span></span>' +
      '<span class="x11-tile-name">' + esc(t.name) + '</span>' +
      '<span class="x11-tile-counts"><b>' + t.usable + '</b> of <b>' + t.total + '</b> tools</span>' +
      '<span class="x11-tile-outcomes">' + t.run + ' run \u00b7 ' + t.ask + ' ask' +
      (t.depends ? ' \u00b7 ' + t.depends +
        (t.depends === 1 ? ' depends' : ' depend') + ' on the call' : '') +
      (t.frozen ? ' \u00b7 ' + t.frozen + ' frozen' : '') + '</span>' +
      accessBar(t.run, t.ask, t.none, tileBarLabel(t), t.depends, t.frozen) +
      (t.extra ? '<span class="x11-tile-extra">' + esc(t.extra) + '</span>' : '') +
      '</a>';
  }
  function legendHtml() {
    var legend = '<div class="x11-legend" aria-label="Access bar legend">' +
      '<span>' + accessBar(1, 0, 0, 'Runs on its own') + ' Runs on its own</span>' +
      '<span>' + accessBar(0, 1, 0, 'Asks you first') + ' Asks you first</span>' +
      '<span>' + accessBar(0, 0, 0, 'Depends on the call', 1) + ' Depends on the call</span>' +
      '<span>' + accessBar(0, 0, 1, 'Not allowed') + ' Not allowed</span>';
    if (S.summary && S.summary.frozen) {
      legend += '<span>' + accessBar(0, 0, 0, 'Frozen', 0, 1) + ' Frozen</span>';
    }
    return legend + '</div>';
  }
  function sectionTools() {
    var tiles = currentTiles();
    var q = (S.tileSearch || '').toLowerCase();
    var inUse = tiles.filter(function (t) { return t.in_use; }).length;
    var unused = tiles.length - inUse;
    var filtered = tiles.filter(function (t) {
      if (S.tileFilter === 'in_use' && !t.in_use) return false;
      if (S.tileFilter === 'not_connected' && t.in_use) return false;
      if (q && (t.name + ' ' + t.kind_words + ' ' + t.kind).toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
    var chips = [
      ['in_use', 'In use', inUse], ['not_connected', 'Not connected', unused],
      ['all', 'All', tiles.length],
    ].map(function (c) {
      return '<button type="button" class="x11-chip" data-tile-filter="' + c[0] +
        '" aria-pressed="' + (S.tileFilter === c[0]) + '" onclick="x11TileFilter(\'' + c[0] +
        '\')">' + c[1] + ' (' + c[2] + ')</button>';
    }).join('');
    var fold = '';
    if (S.tileFilter === 'in_use' && unused) {
      fold = '<button type="button" class="x11-fold" data-show-unused onclick="x11TileFilter(\'not_connected\')">' +
        'Show ' + unused + ' integrations ' + esc((S.agent && S.agent.name) || 'this agent') +
        ' can\u2019t use</button>';
    }
    return '<section class="x11-section" id="x11-sec-tools" data-x11-section="tools">' +
      '<div class="x11-tools-head"><div><h2 class="x11-section-title">Tools</h2>' +
      '<p class="x11-tools-summary" id="x11-tools-summary">' + esc(currentSummary(tiles)) + '</p></div>' +
      '<button type="button" class="btn btn-muted btn-xs" data-json-toggle="tools" aria-expanded="false" ' +
      'onclick="x11ToggleJson(this)">Show JSON</button></div>' +
      '<div class="x11-chip-row" role="group" aria-label="Filter integrations">' + chips + '</div>' +
      '<div class="x11-tool-tools"><input id="x11-tile-search" type="search" placeholder="Search integrations" ' +
      'aria-label="Search integrations" value="' + attr(S.tileSearch) + '" oninput="x11TileSearch(this.value)"></div>' +
      '<div class="x11-tile-grid" data-tile-grid>' +
      (filtered.map(tileHtml).join('') || '<p class="x11-plain">No integrations match.</p>') + '</div>' +
      legendHtml() + fold + jsonBlock('tools') + '</section>';
  }
  function x11TileFilter(v) {
    S.tileFilter = ['in_use', 'not_connected', 'all'].indexOf(v) === -1 ? 'in_use' : v;
    renderEditor();
  }
  function x11TileSearch(v) {
    S.tileSearch = v;
    renderEditor();
    var input = el('x11-tile-search');
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }

  function humanToolName(name) {
    var text = String(name || '').replace(/_/g, ' ').trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
  }
  function toolTitle(tool) {
    return tool.label || humanToolName(tool.tool || tool.name);
  }
  function toolStateLabel(outcome) {
    return ({ none: 'Not allowed', ask: 'Asks you first',
      run: 'Runs on its own', depends: 'Depends on the call',
      frozen: 'Frozen' })[outcome] || 'Not allowed';
  }
  function integrationTools(group) {
    if (!group) return [];
    if (group.kind === 'ha') {
      return toolsOf(group).filter(function (t) { return !t.files; });
    }
    return toolsOf(group);
  }
  function integrationSentence(group, tools) {
    var total = tools.length;
    var used = tools.filter(function (t) {
      var o = outcomeFor(t.name, t, S.draft);
      return o === 'run' || o === 'ask' || o === 'depends' || o === 'frozen';
    }).length;
    var changing = tools.filter(function (t) { return !t.read_only; });
    var text = used + ' of ' + total + ' tool' + (total === 1 ? '' : 's');
    if (!total) return 'No tools are available in ' + group.integration + ' yet.';
    if (!changing.length) return text + ', all reading.';
    var changingRun = changing.filter(function (t) {
      return outcomeFor(t.name, t, S.draft) === 'run';
    }).length;
    text += '.';
    if (!changingRun) return text + ' Changes in ' + group.integration + ' always ask you.';
    if (changingRun === changing.length) {
      return text + ' Changes in ' + group.integration + ' run on their own.';
    }
    return text + ' Some changes in ' + group.integration + ' run on their own; the rest ask you first.';
  }
  function toolRowHtml(tool, group) {
    var outcome = outcomeFor(tool.name, tool, S.draft);
    function choice(value) {
      var always = value === 'run' && tool.always_gate;
      return '<button type="button" data-tool-name="' + attr(tool.name) +
        '" data-choice="' + value + '" aria-pressed="' + (outcome === value) + '"' +
        (always ? ' disabled title="Always asks; use device permissions"' : '') +
        ' onclick="x11ToolChoice(this)">' + toolStateLabel(value) + '</button>';
    }
    var note = '';
    if (tool.always_gate) {
      note = '<div class="x11-always-line" data-always-line>Always asks you. Eshu requires your approval for this tool.</div>';
      if (group.kind === 'ha' && tool.tool === 'call_service') {
        note = '<div class="x11-always-line" data-always-line>Always asks you. To let it run for some devices, use ' +
          '<button type="button" class="link-btn" onclick="x11IntegrationTab(\'devices\')">Devices</button>.</div>';
      }
    }
    if (tool.files) {
      note = '<div class="x11-file-note">' + (tool.read_only
        ? 'Reads a file on the add-on. Nothing changes.'
        : 'Writes always ask you. This tool is always approval-gated.') + '</div>';
    }
    if (outcome === 'depends') {
      note += '<div class="x11-depends-line">Depends on the call. Eshu checks the method and path when the agent calls it.</div>';
    }
    if (outcome === 'frozen') {
      note += '<div class="x11-frozen-line">Frozen. Emergency Freeze pauses writes; reads still run.</div>';
    }
    return '<div class="x11-tool-row" data-tool-row="' + attr(tool.name) + '"' +
      ' data-tool-outcome="' + attr(outcome) + '"' +
      (tool.always_gate ? ' data-always-gate="1"' : '') + '>' +
      '<div><div class="x11-tool-name">' + esc(toolTitle(tool)) + '</div>' +
      toolDescription(tool) + note + '</div>' +
      '<div class="x11-seg" role="group" aria-label="' + attr(toolTitle(tool)) + ' outcome">' +
      choice('none') + choice('ask') + choice('run') + '</div></div>';
  }
  function toolGroupsHtml(tools, group) {
    var reading = tools.filter(function (t) { return t.read_only; });
    var changing = tools.filter(function (t) { return !t.read_only; });
    var html = '';
    if (reading.length) {
      html += '<div class="x11-tool-block" data-tool-block="reading"><h3>Reading</h3>' +
        '<p class="x11-tool-hint">Looks things up. Nothing changes.</p>' +
        reading.map(function (t) { return toolRowHtml(t, group); }).join('') + '</div>';
    }
    if (changing.length) {
      html += '<div class="x11-tool-block" data-tool-block="changing"><h3>Changing</h3>' +
        '<p class="x11-tool-hint">Changes ' + esc(group.integration) + '.</p>' +
        changing.map(function (t) { return toolRowHtml(t, group); }).join('') + '</div>';
    }
    return html || '<p class="x11-plain">No tools are enabled for this integration.</p>';
  }
  function simpleArgRules(rule) {
    if (!rule || typeof rule !== 'object' || Array.isArray(rule)) return false;
    var keys = Object.keys(rule);
    if (!keys.length) return false;
    return keys.every(function (k) {
      var v = rule[k];
      return Array.isArray(v) && v.every(function (x) { return typeof x === 'string'; });
    });
  }
  function toolsJsonSlice(group, names) {
    var tools = S.draft.tools || {};
    var wanted = {};
    (names || []).forEach(function (n) { wanted[n] = true; });
    var allow = (tools.allow || []).filter(function (n) { return wanted[n]; });
    var approval = {};
    Object.keys(tools.approval || {}).forEach(function (n) {
      if (wanted[n]) approval[n] = tools.approval[n];
    });
    var rules = {};
    Object.keys(tools.argument_rules || {}).forEach(function (n) {
      if (wanted[n]) rules[n] = tools.argument_rules[n];
    });
    return {
      mode: tools.mode || 'all',
      allow: allow,
      approval: approval,
      argument_rules: rules,
      rate_limit_per_min: Number(tools.rate_limit_per_min || 0),
    };
  }
  function integrationJsonSlice(group, tab) {
    if (!group) return {};
    if (group.kind === 'ssh') return { ssh: clone(S.draft.ssh || {}) };
    if (group.kind === 'websites') return { websites: clone(S.draft.websites || {}) };
    if (group.kind === 'ha') {
      if (tab === 'files') {
        var files = haFilesTools(group.integration).map(function (t) { return t.name; });
        return { tools: toolsJsonSlice(group, files) };
      }
      return { home_assistant: clone(S.draft.home_assistant || { permissions: [] }) };
    }
    return { tools: toolsJsonSlice(group, integrationTools(group).map(function (t) { return t.name; })) };
  }
  function jsonBlockData(key, data, open) {
    return '<pre class="x11-json" id="x11-json-' + esc(key) + '"' + (open ? '' : ' hidden') + '>' +
      esc(JSON.stringify(data, null, 2)) + '</pre>';
  }
  function limitsHtml(group, tools) {
    var rules = (S.draft.tools || {}).argument_rules || {};
    var rows = '';
    tools.forEach(function (tool) {
      var rule = rules[tool.name];
      if (!rule) return;
      if (simpleArgRules(rule)) {
        Object.keys(rule).forEach(function (key) {
          rows += '<label class="x11-limit-field">' + esc(toolTitle(tool)) + ' \u00b7 ' + esc(key) +
            '<input type="text" value="' + attr((rule[key] || []).join(', ')) +
            '" data-arg-tool="' + attr(tool.name) + '" data-arg-key="' + attr(key) +
            '" aria-label="Allowed values for ' + attr(toolTitle(tool) + ' ' + key) +
            '" onchange="x11SetArgRule(this)"></label>';
        });
      } else {
        rows += '<div class="x11-limit-json"><span>' + esc(toolTitle(tool)) +
          ' has a rule shape that needs JSON.</span> ' +
          '<button type="button" class="link-btn" onclick="x11EditRuleJson(\'' +
          attr(tool.name) + '\')">Edit as JSON</button></div>';
      }
    });
    if (!rows) rows = '<p class="x11-muted">No argument rules for these tools.</p>';
    return '<aside class="x11-limits" data-limits><h2>Limits</h2>' +
      '<label class="x11-limit-field">At most <input type="number" min="0" max="1000" ' +
      'value="' + Number(S.draft.tools.rate_limit_per_min || 0) +
      '" data-rate-limit aria-label="Tool calls per minute" onchange="x11SetRateLimit(this)"> ' +
      'tool calls a minute<span class="x11-limit-note">0 means no limit.</span></label>' +
      '<p class="x11-limit-note">The rate applies to every tool call this agent makes, not just this integration.</p>' +
      rows +
      (group && group.kind === 'ha'
        ? '<p class="x11-limit-note">Device permissions and their own rate limits live under Runs on its own.</p>'
        : '') +
      '</aside>';
  }
  function sectionIntegration() {
    var group = toolGroup(S.integration);
    if (!group) {
      return '<section class="x11-section"><p class="x11-plain">That integration is no longer here. ' +
        '<button type="button" class="link-btn" onclick="x11ToolsBack()">Back to Tools</button>.</p></section>';
    }
    if (group.kind === 'ssh' || group.kind === 'websites') {
      var virtualBody = group.kind === 'ssh'
        ? '<p class="x11-int-sentence">Which gateways this agent may run commands on. The gateway policy still applies on top.</p>' + sshControlsHtml()
        : '<p class="x11-int-sentence">Which sites this agent may fetch. Everything not listed is blocked.</p>' + websitesControlsHtml();
      return '<section class="x11-section" id="x11-sec-tools" data-x11-section="tools" data-integration="' +
        attr(group.integration) + '">' +
        '<button type="button" class="x11-back" onclick="x11ToolsBack()">\u2190 Back to tools</button>' +
        '<nav class="x11-breadcrumb" aria-label="Breadcrumb"><a href="#" onclick="x11Back();return false;">Agents</a>' +
        ' <span aria-hidden="true">/</span> <a href="#agent/' + Number(S.agent.id) + '/overview">' +
        esc(S.agent.name) + '</a> <span aria-hidden="true">/</span> <a href="#agent/' +
        Number(S.agent.id) + '/tools" onclick="x11ToolsBack();return false;">Tools</a></nav>' +
        '<div class="x11-int-head"><div><h2 class="x11-section-title">' + esc(group.integration) + '</h2></div>' +
        '<div class="x11-int-actions"><button type="button" class="btn btn-muted btn-xs" data-int-json ' +
        'onclick="x11ShowIntJson()">' + (S.intJsonOpen ? 'Hide JSON' : 'Show JSON') + '</button>' +
        '<button type="button" class="btn btn-deny btn-xs" data-remove-access onclick="x11RemoveIntegrationAccess()">' +
        'Remove all access</button></div></div>' +
        '<div class="x11-int-layout"><div class="x11-int-main">' + virtualBody + '</div>' +
        limitsHtml(group, []) + '</div>' +
        jsonBlockData('int', integrationJsonSlice(group, ''), S.intJsonOpen) + '</section>';
    }
    var isHA = group.kind === 'ha';
    if (isHA && HA_TABS.indexOf(S.integrationTab) === -1) S.integrationTab = 'tools';
    var tab = isHA ? S.integrationTab : '';
    var tools = integrationTools(group);
    var body = '';
    var limits = '';
    if (!isHA || tab === 'tools') {
      body = '<p class="x11-int-sentence" data-int-sentence>' + esc(integrationSentence(group, tools)) + '</p>' +
        toolGroupsHtml(tools, group);
      limits = limitsHtml(group, tools);
    } else if (tab === 'devices') {
      body = devicesTabHtml(group);
    } else if (tab === 'runs') {
      body = runsTabHtml(group);
    } else if (tab === 'files') {
      var fileTools = haFilesTools(group.integration);
      body = '<p class="x11-int-sentence">The tools that read and write Home Assistant YAML files over the add-on.</p>' +
        (fileTools.length ? toolGroupsHtml(fileTools, group)
          : '<p class="x11-plain">No Files integration is linked to this Home Assistant.</p>') +
        filesFooterHtml(group);
      limits = limitsHtml(group, fileTools);
    }
    var tabs = '';
    if (isHA) {
      tabs = '<div class="x11-tabs x11-int-tabs" role="tablist" aria-label="Home Assistant sections">' +
        HA_TABS.map(function (x) {
          var label = ({ tools: 'Tools', devices: 'Devices', runs: 'Runs on its own',
            files: 'Files' })[x];
          return '<button type="button" class="x11-tab" role="tab" data-int-tab="' + x +
            '" aria-selected="' + (tab === x) + '" onclick="x11IntegrationTab(\'' + x + '\')">' +
            label + '</button>';
        }).join('') + '</div>';
    }
    var showJsonKey = (isHA && tab !== 'files') ? 'home_assistant' : 'int';
    return '<section class="x11-section" id="x11-sec-tools" data-x11-section="tools" data-integration="' +
      attr(group.integration) + '">' +
      '<button type="button" class="x11-back" onclick="x11ToolsBack()">\u2190 Back to tools</button>' +
      '<nav class="x11-breadcrumb" aria-label="Breadcrumb"><a href="#" onclick="x11Back();return false;">Agents</a>' +
      ' <span aria-hidden="true">/</span> <a href="#agent/' + Number(S.agent.id) + '/overview">' +
      esc(S.agent.name) + '</a> <span aria-hidden="true">/</span> <a href="#agent/' +
      Number(S.agent.id) + '/tools" onclick="x11ToolsBack();return false;">Tools</a></nav>' +
      '<div class="x11-int-head"><div><h2 class="x11-section-title">' + esc(group.integration) + '</h2></div>' +
      '<div class="x11-int-actions"><button type="button" class="btn btn-muted btn-xs" data-int-json ' +
      'onclick="x11ShowIntJson()">' + (S.intJsonOpen ? 'Hide JSON' : 'Show JSON') + '</button>' +
      '<button type="button" class="btn btn-deny btn-xs" data-remove-access onclick="x11RemoveIntegrationAccess()">' +
      'Remove all access</button></div></div>' + tabs +
      (tab === 'tools' || !isHA
        ? '<div class="x11-int-layout"><div class="x11-int-main">' + body +
          '</div>' + limits + '</div>'
        : body) +
      jsonBlockData(showJsonKey, integrationJsonSlice(group, tab), S.intJsonOpen) +
      '</section>';
  }
  function x11ShowIntJson() {
    S.intJsonOpen = !S.intJsonOpen;
    renderEditor();
  }
  function x11SetRateLimit(input) {
    var value = Math.max(0, Math.min(1000, Number(input.value) || 0));
    S.draft.tools.rate_limit_per_min = value;
    markDirty();
  }
  function x11SetArgRule(input) {
    var toolName = input.getAttribute('data-arg-tool');
    var key = input.getAttribute('data-arg-key');
    var list = input.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean);
    S.draft.tools.argument_rules = S.draft.tools.argument_rules || {};
    S.draft.tools.argument_rules[toolName] = S.draft.tools.argument_rules[toolName] || {};
    S.draft.tools.argument_rules[toolName][key] = list;
    markDirty();
  }
  function x11EditRuleJson(toolName) {
    S.section = 'json';
    S.integration = '';
    S.integrationTab = '';
    setAgentHash(S.agent.id, 'json');
    renderEditor();
    setNotice('Editing tools.argument_rules["' + toolName + '"] in the JSON section.', '');
    var doc = el('x11-json-doc');
    if (doc) {
      var pos = S.jsonText.indexOf('"' + toolName + '"');
      if (pos > 0) { doc.focus(); doc.setSelectionRange(pos, pos + toolName.length + 2); }
    }
  }
  function removeToolAccess(names) {
    var tools = S.draft.tools = S.draft.tools || {};
    var wanted = {};
    names.forEach(function (n) { wanted[n] = true; });
    if ((tools.mode || 'all') === 'all') {
      tools.mode = 'allowlist';
      tools.allow = toolList().map(function (t) { return t.name; })
        .filter(function (n) { return !wanted[n]; });
    } else {
      tools.allow = (tools.allow || []).filter(function (n) { return !wanted[n]; });
    }
    Object.keys(tools.approval || {}).forEach(function (n) {
      if (wanted[n]) delete tools.approval[n];
    });
    Object.keys(tools.argument_rules || {}).forEach(function (n) {
      if (wanted[n]) delete tools.argument_rules[n];
    });
  }
  function x11RemoveIntegrationAccess() {
    var group = toolGroup(S.integration);
    if (!group) return;
    if (!window.confirm('Remove all of this agent\u2019s access to ' + group.integration +
        '? You will see a Check preview before anything changes.')) return;
    if (group.kind === 'ssh') {
      S.draft.ssh.hosts = [];
      S.draft.ssh.mode = 'approval';
    } else if (group.kind === 'websites') {
      S.draft.websites = { enabled: false, allow: [], deny: [] };
    } else {
      var names = (group.tools || []).map(function (t) { return t.name; });
      removeToolAccess(names);
      if (group.kind === 'ha') {
        S.draft.home_assistant = { permissions: [] };
      }
    }
    if (S.section === 'tools') {
      markDirty();
      x11Check();
    }
  }

  function x11ToolChoice(btn) {
    var name = btn.getAttribute('data-tool-name');
    var choice = btn.getAttribute('data-choice');
    var tool = toolList().filter(function (t) { return t.name === name; })[0] || {};
    if (choice === 'run' && tool.always_gate) {
      setNotice('This tool always asks. Use Runs on its own with device permissions for Home Assistant.', 'warn');
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
  function x11DeviceChoice(btn) {
    var parts = (btn.getAttribute('data-device-action') || '').split('.');
    x11CycleDevice(parts[0], parts.slice(1).join('.'), btn.getAttribute('data-device'));
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

  function reloadAgent(keepNotice) {
    x11OpenAgent(S.agent.id, S.section, !!keepNotice,
      { section: S.section, integration: S.integration, tab: S.integrationTab });
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
        reloadAgent(false);
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
        reloadAgent(false);
      }).catch(function (e) { setNotice(errText(e), 'error'); });
  }

  function x11ReofferJoin(btn) {
    var id = Number(btn.getAttribute('data-reoffer-join'));
    if (!id) return;
    api('/api/allowances/' + id + '/reoffer-join', { method: 'POST' })
      .then(function () {
        toast('Dismissed devices offered again', 'success');
        reloadAgent(false);
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
  function x11DeviceSearch(v) {
    S.deviceSearch = v;
    var y = window.scrollY;
    renderEditor();
    window.scrollTo(0, y);
    var input = el('x11-device-search');
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }
  function x11DeviceSettings(v) { S.deviceSettings = !!v; renderEditor(); }
  function x11DeviceOnlyPerm(v) { S.deviceOnlyPerm = !!v; renderEditor(); }
  function x11GoRules() { S.screen = 'agents'; S.tab = 'rules'; renderRules(); }

  function sshControlsHtml() {
    var ssh = S.draft.ssh || {};
    var hosts = ssh.hosts || [];
    var all = (S.gateways || []).map(function (g) { return g.ip; });
    hosts.forEach(function (h) { if (h !== '*' && all.indexOf(h) === -1) all.push(h); });
    all.sort();
    var hostHtml = all.map(function (ip) {
      return '<label><input type="checkbox" data-ssh-host="' + attr(ip) + '"' + (hosts.indexOf(ip) !== -1 ? ' checked' : '') +
        ' onchange="x11ToggleHost(this)"> <span>' + esc(ip) + '</span></label>';
    }).join('');
    return '<label class="x11-plain">Mode <select data-ssh-mode onchange="x11SetSshMode(this)">' +
      '<option value="read_only"' + (ssh.mode === 'read_only' ? ' selected' : '') + '>Read only</option>' +
      '<option value="approval"' + (ssh.mode === 'approval' ? ' selected' : '') + '>Always approve</option>' +
      '<option value="normal"' + (ssh.mode === 'normal' ? ' selected' : '') + '>Gateway policy</option></select></label>' +
      '<div class="x11-check-grid" style="margin-top:10px"><label><input type="checkbox" data-ssh-any ' +
      (hosts.indexOf('*') !== -1 ? ' checked' : '') + ' onchange="x11ToggleAnyHost(this)"> Any registered gateway <code>*</code></label></div>' +
      '<div class="x11-check-grid" style="margin-top:8px">' + (hostHtml || '<p class="x11-muted">No gateways yet.</p>') + '</div>';
  }
  function sectionSsh() {
    return '<section class="x11-section" id="x11-sec-ssh" data-x11-section="ssh">' +
      head('SSH', 'Which gateways this agent may run commands on. The gateway policy still applies on top.', 'ssh') +
      sshControlsHtml() + jsonBlock('ssh') + '</section>';
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

  function websitesControlsHtml() {
    var w = S.draft.websites || {};
    return '<label class="x11-plain"><input type="checkbox" data-web-enabled' + (w.enabled ? ' checked' : '') +
      ' onchange="x11SetWebEnabled(this)"> Web fetch is enabled</label>' +
      '<div style="margin-top:10px"><label class="x11-plain" for="x11-web-allow">Allowed sites (one per line)</label>' +
      '<textarea id="x11-web-allow" class="w-full text-xs" rows="4" data-web-list="allow" oninput="x11SetWebList(this)">' +
      esc((w.allow || []).join('\n')) + '</textarea></div>' +
      '<div style="margin-top:10px"><label class="x11-plain" for="x11-web-deny">Blocked sites (one per line)</label>' +
      '<textarea id="x11-web-deny" class="w-full text-xs" rows="3" data-web-list="deny" oninput="x11SetWebList(this)">' +
      esc((w.deny || []).join('\n')) + '</textarea></div>';
  }
  function sectionWebsites() {
    var w = S.draft.websites || {};
    return '<section class="x11-section" id="x11-sec-websites" data-x11-section="websites">' +
      head('Websites', 'Which sites this agent may fetch. Everything not listed is blocked.', 'websites') +
      websitesControlsHtml() + jsonBlock('websites') + '</section>';
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
      reloadAgent(false);
    }).catch(function (e) {
      x11ClosePreview();
      var code = e.detail && e.detail.code;
      var text = code === 'preview_changed'
        ? 'This agent changed since the preview. The form has been reloaded; review the new preview.'
        : code === 'config_changed'
          ? 'This agent changed since you opened it. The form has been reloaded.'
          : errText(e);
      S.notice = { text: text, kind: 'warn' };
      reloadAgent(true);
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
    S.integration = ''; S.integrationTab = '';
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
        reloadAgent(false);
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
      var view = document.getElementById('view-agents');
      if (view && view.classList.contains('hidden') && window.switchView) {
        window.switchView('agents');
        return;
      }
      if (!S.agent || Number(S.agent.id) !== target.id) x11OpenAgent(target.id, target.section, false, target);
      else {
        var old = S.section + '|' + S.integration + '|' + S.integrationTab;
        applyRoute(target);
        if (old !== S.section + '|' + S.integration + '|' + S.integrationTab) renderEditor();
      }
    } else {
      x11ShowAgents();
    }
  });

  // Public entry points used by switchView and inline handlers.
  window.x11ShowAgents = x11ShowAgents;
  window.x11SetTab = x11SetTab;
  window.x11OpenIntegration = x11OpenIntegration;
  window.x11IntegrationTab = x11IntegrationTab;
  window.x11ToolsBack = x11ToolsBack;
  window.x11ShowIntJson = x11ShowIntJson;
  window.x11SetRateLimit = x11SetRateLimit;
  window.x11SetArgRule = x11SetArgRule;
  window.x11EditRuleJson = x11EditRuleJson;
  window.x11RemoveIntegrationAccess = x11RemoveIntegrationAccess;
  window.x11TileFilter = x11TileFilter;
  window.x11TileSearch = x11TileSearch;
  window.x11DeviceFilter = x11DeviceFilter;
  window.x11ShowHidden = x11ShowHidden;
  window.x11DeviceChoice = x11DeviceChoice;
  window.x11CycleDevice = x11CycleDevice;
  window.x11EditPermission = x11EditPermission;
  window.x11AddPermission = x11AddPermission;
  window.x11ShowDanger = x11ShowDanger;
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

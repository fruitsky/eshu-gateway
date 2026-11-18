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
    allowances: [],
    gateways: [],
    devices: [],
    labels: [],
    ceiling: null,
    protected: [],
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
  function summaryFor(cfg, integrations) {
    var c = countsFor(cfg, integrations);
    return c.total + ' tools \u00b7 asks first for ' + c.ask +
      ' \u00b7 runs ' + c.run + ' without asking \u00b7 SSH ' + sshText(cfg);
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
  function x11ShowAgents() {
    if (!root()) return;
    if (S.screen === 'editor' && S.agent) { renderEditor(); return; }
    S.screen = 'agents'; S.tab = S.tab === 'rules' ? 'rules' : 'agents';
    renderAgents();
    if (!S.agentsReady) loadAgents();
  }

  function loadAgents() {
    api('/api/agents').then(function (agents) {
      return Promise.all((agents || []).map(function (a) {
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
        '<div class="x11-btn-row"><button type="button" class="btn btn-muted btn-xs" ' +
        'onclick="event.stopPropagation();x11OpenAgent(this.closest(\'[data-agent-id]\').dataset.agentId)">Edit agent</button></div>' +
        '</article>';
    }).join('');
    var empty = S.agentsReady && !S.agents.length
      ? '<p class="x11-plain">No agent tokens yet. Create one to give an agent a name and a token.</p>' : '';
    r.innerHTML = '<div class="x11-hud"><div><h1 class="x11-title">Agents</h1>' +
      '<p class="x11-lede">What each agent can do on its own, and how to change it.</p></div>' +
      '<div class="x11-hud-right" id="x11-hud-count">' + S.agents.length + ' agent' + (S.agents.length === 1 ? '' : 's') + '</div></div>' +
      tabsHtml() +
      '<div id="x11-notice" class="x11-notice"' + (message || S.notice ? '' : ' hidden') + ' data-kind="' +
      esc((S.notice && S.notice.kind) || (message ? 'error' : '')) + '">' +
      esc(message || (S.notice && S.notice.text) || '') + '</div>' + banner +
      '<div class="x11-cards">' +
      '<div class="x11-new-card"><button type="button" class="btn btn-sm" data-new-agent onclick="x11NewAgent()">New agent</button></div>' +
      cards + '</div>' + empty + previewModalHtml() + newAgentModalHtml();
    if (!S.agentsReady) {
      r.querySelector('.x11-cards').insertAdjacentHTML('afterend', '<p class="x11-plain">Loading agents\u2026</p>');
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
      '<div class="x11-rules">' +
      '<details data-rules="protected"><summary>Protected devices</summary><div class="x11-rules-body" id="x11-protected-body">' +
      '<p class="x11-section-sub">No agent can be given a protected device. Existing permissions that hold one are paused; unprotecting does not resume them.</p>' +
      '<div id="x11-protected-list">' + (S.devices.length ? protectedListHtml() : '<p class="x11-muted">Loading devices\u2026</p>') + '</div>' +
      '<div class="x11-btn-row"><button type="button" class="btn btn-sm" onclick="x11SaveProtected()">Save protected devices</button></div></div></details>' +
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

  function protectedListHtml() {
    var map = deviceMap();
    var rows = (S.devices || []).filter(function (d) { return !d.disabled; });
    var groups = {};
    rows.forEach(function (d) {
      var area = d.area || 'No area';
      (groups[area] = groups[area] || []).push(d);
    });
    return Object.keys(groups).sort().map(function (area) {
      return '<div><div class="x11-mono">' + esc(area) + '</div><div class="x11-protected-list">' +
        groups[area].map(function (d) {
          var checked = S.protected.indexOf(d.id) !== -1 || d.protected;
          return '<label><input type="checkbox" data-protect="' + attr(d.id) + '"' + (checked ? ' checked' : '') +
            '> <span>' + esc(d.name) + '</span> <span class="x11-dev-id">' + esc(d.id) + '</span></label>';
        }).join('') + '</div></div>';
    }).join('') || '<p class="x11-muted">No devices from Home Assistant.</p>';
  }

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
    S.section = section || 'overview';
    Promise.all([
      api('/api/agents/' + Number(id) + '/config'),
      api('/api/agents/' + Number(id) + '/available-tools').catch(function () { return { integrations: [] }; }),
      api('/api/agents/' + Number(id) + '/activity').catch(function () { return {}; }),
      api('/api/allowances').catch(function () { return []; }),
      api('/api/gateways').catch(function () { return []; }),
      api('/api/ha/devices').catch(function (e) { return { devices: [], error: errText(e) }; }),
      api('/api/ha/labels').catch(function () { return { labels: [] }; }),
      api('/api/allowances/ceiling').catch(function () { return null; }),
    ]).then(function (out) {
      S.config = out[0];
      S.draft = docOf(out[0]);
      S.agent = out[0].agent;
      S.tools = (out[1].integrations) || [];
      S.activity = out[2] || {};
      S.allowances = (out[3] || []).filter(function (r) { return !Number(r.revoked_at); });
      S.gateways = out[4] || [];
      S.devices = out[5].devices || [];
      S.devicesError = out[5].error || '';
      S.labels = out[6].labels || [];
      S.ceiling = out[7] && !out[7].error ? out[7] : null;
      S.jsonText = JSON.stringify(S.draft, null, 2);
      S.jsonError = '';
      S.lastPreview = null;
      if (!keepNotice) S.notice = null;
      window.scrollTo(0, 0);
      renderEditor();
      if (S.section !== 'overview') scrollToSection(S.section);
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
    r.innerHTML = '<div class="x11-editor">' +
      '<aside class="x11-editor-nav"><div class="x11-editor-name">' + esc(a.name || 'Agent') + '</div>' +
      '<div class="x11-editor-status">' + (a.revoked ? 'Revoked' : 'Active') + ' \u00b7 last active ' + esc(ago(a.last_used_at)) + '</div>' +
      '<nav aria-label="Agent sections">' + navHtml + '</nav>' +
      '<div class="x11-token-actions">' +
      '<button type="button" class="btn btn-muted btn-xs" onclick="x11Back()">All agents</button>' +
      '<button type="button" class="btn btn-muted btn-xs" onclick="x11RevokeAgent()">Revoke token</button>' +
      '<button type="button" class="btn btn-deny btn-xs" onclick="x11DeleteAgent()">Delete agent</button></div></aside>' +
      '<div class="x11-editor-content">' +
      '<div id="x11-notice" class="x11-notice"' + (S.notice ? '' : ' hidden') + ' data-kind="' +
      esc((S.notice && S.notice.kind) || '') + '">' + esc((S.notice && S.notice.text) || '') + '</div>' +
      sectionOverview() + sectionTools() + sectionSsh() + sectionWebsites() +
      sectionActivity() + sectionJson() +
      '</div></div>' +
      '<div class="x11-savebar" id="x11-savebar"' + (n ? '' : ' hidden') + '>' +
      '<span class="x11-change-count" id="x11-change-count">' + n + ' change' + (n === 1 ? '' : 's') + '</span>' +
      '<button type="button" class="btn btn-muted btn-sm" onclick="x11Discard()">Discard</button>' +
      '<button type="button" class="btn btn-sm" id="x11-save-changes" onclick="x11Check()">Save changes</button></div>' +
      previewModalHtml() + newAgentModalHtml();
  }

  function x11Nav(section) {
    S.section = section;
    Array.prototype.forEach.call(document.querySelectorAll('[data-x11-nav]'), function (b) {
      b.setAttribute('aria-current', String(b.getAttribute('data-x11-nav') === section));
    });
    scrollToSection(section);
  }
  function scrollToSection(section) {
    var target = el('x11-sec-' + section);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
    return '<section class="x11-section" id="x11-sec-overview" data-x11-section="overview">' +
      head('Overview', 'What this agent can do on its own, in plain words.', 'overview') +
      lines.join('') + '<div class="x11-perm-list">' + permHtml + '</div>' + jsonBlock('overview') + '</section>';
  }

  function sectionTools() {
    var groups = (S.tools || []).map(function (g) {
      var rows = (g.tools || []).map(function (tool) {
        var outcome = outcomeFor(tool.name, tool);
        function choice(value, label) {
          return '<button type="button" data-tool-name="' + attr(tool.name) + '" data-choice="' + value +
            '" aria-pressed="' + (outcome === value) + '" onclick="x11ToolChoice(this)">' + label + '</button>';
        }
        return '<div class="x11-tool-row" data-tool-row="' + attr(tool.name) + '">' +
          '<div><div class="x11-tool-name">' + esc((tool.tool || tool.name).replace(/_/g, ' ')) + '</div>' +
          (tool.description ? '<div class="x11-tool-desc">' + esc(tool.description) + '</div>' : '') + '</div>' +
          '<div class="x11-seg" role="group" aria-label="' + attr(tool.name) + ' outcome">' +
          choice('none', 'None') + choice('ask', 'Asks you first') + choice('run', 'Runs without asking') +
          '</div></div>';
      }).join('');
      return '<div class="x11-tool-group"><h3>' + esc(g.integration) + '</h3>' +
        (g.kind === 'ha' ? '<p class="x11-int-note">Home Assistant calls. Device permissions live under call service.</p>' : '') +
        rows + '</div>';
    }).join('');
    var ha = toolList().filter(function (t) { return t.kind === 'ha' && t.tool === 'call_service'; })[0];
    var showGrid = ha && outcomeFor(ha.name, ha) === 'run';
    return '<section class="x11-section" id="x11-sec-tools" data-x11-section="tools">' +
      head('Tools', 'Choose an outcome per tool. A device permission belongs to Home Assistant call service.', 'tools') +
      groups + (showGrid ? deviceGridHtml() : '') + jsonBlock('tools') + '</section>';
  }

  function x11ToolChoice(btn) {
    var name = btn.getAttribute('data-tool-name');
    var choice = btn.getAttribute('data-choice');
    var tool = toolList().filter(function (t) { return t.name === name; })[0] || {};
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
    if (choice === 'run' && tool.kind === 'ha' && tool.tool === 'call_service') {
      var grid = el('x11-device-grid');
      if (grid) grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
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
    var heads = actions.map(function (a) {
      var p = permissionFor(a.domain, a.service);
      var mode = p && p.label_id ? 'label' : 'pick';
      return '<th scope="col"><div class="x11-action-head"><span>' + esc(a.label) + '</span>' +
        '<select data-action-mode="' + attr(a.id) + '" aria-label="Devices or label for ' + attr(a.label) + '" onchange="x11SetActionMode(this)">' +
        '<option value="pick"' + (mode === 'pick' ? ' selected' : '') + '>Pick devices</option>' +
        '<option value="label"' + (mode === 'label' ? ' selected' : '') + '>Follow label</option></select>' +
        (mode === 'label' ? '<select data-action-label="' + attr(a.id) + '" aria-label="Home Assistant label for ' + attr(a.label) + '" onchange="x11SetLabel(this)">' +
          '<option value="">Pick a label</option>' + (S.labels || []).map(function (l) {
            return '<option value="' + attr(l.id) + '"' + (p && p.label_id === l.id ? ' selected' : '') + '>' + esc(l.name) + '</option>';
          }).join('') + '</select>' : '') +
        '</div></th>';
    }).join('');
    var body = Object.keys(groups).sort().map(function (area) {
      var rows = groups[area].map(function (d) {
        var cells = actions.map(function (a) {
          if (d.domain !== a.domain) return '<td class="x11-action-cell" data-cell-label="' + attr(a.label) + '">\u2014</td>';
          var p = permissionFor(a.domain, a.service);
          var labelMode = !!(p && p.label_id);
          if (d.protected) {
            return '<td class="x11-action-cell" data-cell-label="' + attr(a.label) +
              '"><span class="x11-lock" aria-label="Locked, protected device">\u25a0 Locked</span></td>';
          }
          if (labelMode) {
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
        var meta = [d.area, d.domain].filter(Boolean).join(' \u00b7 ');
        return '<tr class="x11-device-row' + (d.protected ? ' x11-locked' : '') + '" data-device-row="' + attr(d.id) + '">' +
          '<td><div class="x11-device-name">' + esc(d.name) + '</div><div class="x11-device-meta">' +
          esc(meta) + '</div><div class="x11-device-state">' + esc(d.state || '') + '</div>' +
          (d.risky ? '<div class="x11-risky">Risky: ' + esc(d.device_class || 'device') + '</div>' : '') +
          (d.protected ? '<div class="x11-lock" aria-label="Locked, protected device">\u25a0 Locked \u00b7 <a href="#" onclick="x11GoRules();return false;">Rules for all agents</a></div>' : '') +
          '</td>' + cells + '</tr>';
      }).join('');
      return '<tr class="x11-area-row"><td colspan="' + (actions.length + 1) + '">' + esc(area) + '</td></tr>' + rows;
    }).join('');
    var labelModeAny = actions.some(function (a) {
      var p = permissionFor(a.domain, a.service);
      return p && p.label_id;
    });
    var perms = permissions().map(function (p, i) { return permEditor(p, i); }).join('');
    return '<div id="x11-device-grid" data-device-grid>' +
      '<div class="x11-device-tools">' +
      '<input type="search" placeholder="Search devices" aria-label="Search devices" value="' + attr(S.deviceSearch) +
      '" oninput="x11DeviceSearch(this.value)">' +
      '<label class="x11-plain"><input type="checkbox" onchange="x11DeviceOnlyPerm(this.checked)"' + (S.deviceOnlyPerm ? ' checked' : '') + '> Only devices with permissions</label>' +
      '<label class="x11-plain"><input type="checkbox" onchange="x11DeviceSettings(this.checked)"' + (S.deviceSettings ? ' checked' : '') + '> Show settings entities</label>' +
      '</div>' +
      '<div class="x11-mobile-modes">' + actions.map(function (a) {
        var p = permissionFor(a.domain, a.service);
        var mode = p && p.label_id ? 'label' : 'pick';
        return '<div class="x11-mobile-mode"><span>' + esc(a.label) + '</span>' +
          '<select data-action-mode="' + attr(a.id) + '" aria-label="Devices or label for ' + attr(a.label) + '" onchange="x11SetActionMode(this)">' +
          '<option value="pick"' + (mode === 'pick' ? ' selected' : '') + '>Pick devices</option>' +
          '<option value="label"' + (mode === 'label' ? ' selected' : '') + '>Follow label</option></select>' +
          (mode === 'label' ? '<select data-action-label="' + attr(a.id) + '" aria-label="Home Assistant label for ' + attr(a.label) + '" onchange="x11SetLabel(this)">' +
            '<option value="">Pick a label</option>' + (S.labels || []).map(function (l) {
              return '<option value="' + attr(l.id) + '"' + (p && p.label_id === l.id ? ' selected' : '') + '>' + esc(l.name) + '</option>';
            }).join('') + '</select>' : '') + '</div>';
      }).join('') + '</div>' +
      '<div class="x11-device-wrap"><table class="x11-devices"><thead><tr><th scope="col">Device</th>' +
      heads + '</tr></thead><tbody>' + body + '</tbody></table></div>' +
      (labelModeAny ? '<p class="x11-label-hint">Add or remove the label in Home Assistant; new devices wait for your review here.</p>' : '') +
      '<div class="x11-perm-list">' + (perms || '<p class="x11-plain">No device permissions yet.</p>') + '</div></div>';
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
      if (!window.confirm('Allow this agent on every gateway, including ones enrolled later?')) { cb.checked = false; return; }
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
      '<button type="button" class="btn btn-muted btn-sm" id="x11-json-apply" onclick="x11JsonApply()">Apply</button>' +
      '<button type="button" class="btn btn-muted btn-sm" onclick="x11JsonCopy()">Copy</button>' +
      '<button type="button" class="btn btn-muted btn-sm" onclick="x11JsonReset()">Reset</button></div>' +
      '<p class="x11-section-sub">Pasting another agent\u2019s document is the copy feature; the preview names the target agent.</p></section>';
  }

  function x11JsonInput(v) { S.jsonText = v; S.jsonError = ''; var e = el('x11-json-error'); if (e) e.textContent = ''; }
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
    S.jsonError = ''; renderEditor(); toast('JSON reset to the saved document', 'success');
  }
  function parseJsonDoc() {
    try { return JSON.parse(S.jsonText); }
    catch (e) { S.jsonError = 'JSON error: ' + e.message; var box = el('x11-json-error'); if (box) box.textContent = S.jsonError; return null; }
  }
  function x11JsonCheck() {
    var doc = parseJsonDoc();
    if (!doc) return;
    S.jsonSource = doc;
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
      showPreview();
    }).catch(function (e) {
      if (source === 'json') {
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

  function showPreview() {
    var p = S.lastPreview && S.lastPreview.body;
    var modal = el('x11-preview-modal');
    if (!p || !modal) return;
    var risks = p.risky || [];
    var resumes = p.resumes || [];
    var waiting = p.waiting || [];
    var body = '<div class="x11-preview-target">Applying to <strong>' + esc((p.agent || {}).name || S.agent.name) + '</strong></div>' +
      '<p class="x11-preview-line">' + esc(p.summary || 'No visible changes.') + '</p>' +
      (p.sections && p.sections.length ? '<div class="x11-preview-line x11-muted">' +
        p.sections.map(function (s) { return '<div>' + esc(s.section) + ': ' + esc(s.summary) + '</div>'; }).join('') + '</div>' : '') +
      (risks.length ? '<div class="x11-preview-warn"><p class="x11-preview-line"><strong>Risky items</strong></p>' +
        risks.map(function (r) { return '<p class="x11-preview-line">' + esc(r.entity_id || (r.domain + '.' + r.service)) + ' \u2014 ' + esc(r.reason) + '</p>'; }).join('') +
        '<label class="x11-ack"><input type="checkbox" data-x11-ack="risk" onchange="x11PreviewAck()"> I understand these are risky.</label></div>' : '') +
      (resumes.length ? '<div class="x11-preview-warn"><p class="x11-preview-line"><strong>Will resume</strong></p>' +
        resumes.map(function (r) { return '<p class="x11-preview-line">' + esc(r.name || (r.domain + '.' + r.service)) + ' \u2014 ' + esc(reasonWords(r.reason)) + (r.will_pin_to_current_ha ? ' (re-pins to the current Home Assistant)' : '') + '</p>'; }).join('') +
        '<label class="x11-ack"><input type="checkbox" data-x11-ack="resume" onchange="x11PreviewAck()"> Resume these paused permissions.</label></div>' : '') +
      (waiting.length ? '<div class="x11-preview-line"><strong>Will also add, waiting to join</strong><br>' +
        waiting.map(function (w) { return esc((w.entities || []).map(function (e) { return e.name || e.entity_id; }).join(', ')); }).join('<br>') + '</div>' : '') +
      '<p class="x11-preview-line x11-muted">Nothing has changed yet.</p>' +
      '<div class="x11-preview-actions"><button type="button" class="btn btn-muted btn-sm" onclick="x11ClosePreview()">Cancel</button>' +
      '<button type="button" class="btn btn-sm" id="x11-preview-apply" onclick="x11ApplyPreview()"' +
      ((p.risk_ack_required || p.resume_ack_required) ? ' disabled' : '') + '>Apply</button></div>';
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
  }
  function x11Discard() {
    S.draft = docOf(S.config);
    S.jsonText = JSON.stringify(S.draft, null, 2);
    renderEditor();
    toast('Changes discarded', 'success');
  }
  function x11Back() { S.screen = 'agents'; S.agent = null; S.draft = null; S.config = null; S.tab = 'agents'; renderAgents(); loadAgents(); }

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

  // Public entry points used by switchView and inline handlers.
  window.x11ShowAgents = x11ShowAgents;
  window.x11SetTab = x11SetTab;
  window.x11ReviewPaused = x11ReviewPaused;
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
  window.x11SaveProtected = x11SaveProtected;
  window.x11SaveCeiling = x11SaveCeiling;
  window.x11AddWord = x11AddWord;
  window.x11RemoveWord = x11RemoveWord;
})();

"""X4 — the Agents page.

Two kinds of check:

* the API contract the page relies on: each section's PUT carries only its
  own keys, and a field the caller leaves out keeps its stored value, so
  saving Tools cannot reset SSH, the rate limit or the web-fetch policy (and
  the other way round);
* the UI move: one Agents view, the policy editor out of its modal and into
  that page, web fetch moved off Controls, and Controls renamed.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HTML = os.path.join(ROOT, "dashboard", "static", "index.html")
JS = os.path.join(ROOT, "dashboard", "static", "app.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _agent(name='x4-agent'):
    from db.agent_tokens import create_agent_token
    _raw, agent_id = create_agent_token(name)
    return agent_id


def _policy(agent_id):
    from db.agent_policies import get_agent_policy
    return get_agent_policy(agent_id)


def _put(client, agent_id, body):
    r = client.put('/api/agents/%d/policy' % agent_id, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_x4_tools_save_keeps_ssh_and_rules(auth_client):
    aid = _agent('x4-tools')
    _put(auth_client, aid, {
        'tools_mode': 'allowlist', 'tools': ['a_b'],
        'arg_policy': {'a_b': {'domain_allow': ['light']}},
        'approval': {'a_b': 'gated'}, 'rate_limit_per_min': 7,
        'ssh_hosts_allow': ['192.0.2.10'], 'ssh_mode': 'read_only'})
    got = _put(auth_client, aid, {'tools_mode': 'allowlist', 'tools': ['c_d']})
    assert got['tools'] == ['c_d']
    assert got['ssh_hosts_allow'] == ['192.0.2.10']
    assert got['ssh_mode'] == 'read_only'
    assert got['rate_limit_per_min'] == 7
    assert got['approval'] == {'a_b': 'gated'}
    assert got['arg_policy'] == {'a_b': {'domain_allow': ['light']}}


def test_x4_ssh_save_keeps_tools_rules_and_rate(auth_client):
    aid = _agent('x4-ssh')
    _put(auth_client, aid, {
        'tools_mode': 'allowlist', 'tools': ['a_b', 'c_d'],
        'approval': {'a_b': 'gated'}, 'rate_limit_per_min': 3,
        'ssh_hosts_allow': [], 'ssh_mode': 'approval'})
    got = _put(auth_client, aid, {
        'ssh_hosts_allow': ['192.0.2.11'], 'ssh_mode': 'normal'})
    assert got['ssh_hosts_allow'] == ['192.0.2.11']
    assert got['ssh_mode'] == 'normal'
    assert got['tools'] == ['a_b', 'c_d']
    assert got['tools_mode'] == 'allowlist'
    assert got['approval'] == {'a_b': 'gated'}
    assert got['rate_limit_per_min'] == 3


def test_x4_rules_save_keeps_tools_and_ssh(auth_client):
    aid = _agent('x4-rules')
    _put(auth_client, aid, {
        'tools_mode': 'allowlist', 'tools': ['a_b'],
        'ssh_hosts_allow': ['*'], 'ssh_mode': 'normal'})
    got = _put(auth_client, aid, {
        'rate_limit_per_min': 9, 'approval': {'a_b': 'gated'}})
    assert got['rate_limit_per_min'] == 9
    assert got['approval'] == {'a_b': 'gated'}
    assert got['tools'] == ['a_b']
    assert got['ssh_hosts_allow'] == ['*']
    assert got['ssh_mode'] == 'normal'


def test_x4_tools_save_keeps_the_web_fetch_policy(auth_client):
    aid = _agent('x4-web')
    name = 'x4-web'
    r = auth_client.put('/api/web-fetch-policies/' + name,
                        json={'enabled': True, 'allow': ['example.test']})
    assert r.status_code == 200, r.text
    _put(auth_client, aid, {'tools_mode': 'allowlist', 'tools': []})
    pol = auth_client.get('/api/web-fetch-policies/' + name).json()
    assert pol['enabled'] is True
    assert pol['allow'] == ['example.test']


def test_x4_web_save_keeps_the_agent_tools(auth_client):
    aid = _agent('x4-web2')
    _put(auth_client, aid, {'tools_mode': 'allowlist', 'tools': ['a_b']})
    r = auth_client.put('/api/web-fetch-policies/x4-web2',
                        json={'enabled': True, 'allow': ['example.test']})
    assert r.status_code == 200, r.text
    assert _policy(aid)['tools_mode'] == 'allowlist'
    assert 'a_b' in _policy(aid)['tools']


def test_x4_activity_is_read_only_and_scoped(auth_client):
    aid = _agent('x4-activity')
    r = auth_client.get('/api/agents/%d/activity' % aid)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['agent']['name'] == 'x4-activity'
    assert data['requests'] == [] and data['calls'] == [] and data['sessions'] == []
    assert auth_client.get('/api/agents/999999/activity').status_code == 404


def test_x4_nav_has_agents_and_controls_is_renamed():
    html = _read(HTML)
    assert 'id="nav-agents"' in html
    assert "switchView('agents')" in html
    nav = html[html.index('id="nav-agents"'):]
    nav = nav[:nav.index('</button>')]
    assert 'Agents' in nav
    assert 'id="nav-controls"' in html
    controls = html[html.index('id="nav-controls"'):]
    controls = controls[:controls.index('</button>')]
    assert 'Command policy' in controls
    # The view id stays `controls`, so deep links keep working.
    assert 'id="view-controls"' in html


def test_x4_one_agents_view_holds_tokens_the_policy_the_web_panel_and_activity():
    html = _read(HTML)
    view = html[html.index('id="view-agents"'):]
    view = view[:view.index('<!-- CONTROLS VIEW')]
    for needed in ('id="agent-token-list"', 'id="agent-token-name"',
                   'id="agent-detail"', 'id="pol-pane-tools"', 'id="pol-pane-rules"',
                   'id="pol-pane-ssh"', 'id="pol-pane-advanced"',
                   'id="wf-policy-widget"', 'id="agent-activity-body"'):
        assert needed in view, needed
    # The policy editor is not a modal any more, and Settings/Integrations only
    # point at the page.
    assert 'agent-policy-modal' not in html
    assert 'class="pol-card pol-card-paged"' in html
    integrations = html[html.index('id="view-integrations"'):]
    integrations = integrations[:integrations.index('id="view-settings"')]
    assert 'agent-token-list' not in integrations
    assert "switchView('agents')" in integrations
    controls = html[html.index('id="view-controls"'):]
    controls = controls[:controls.index('id="view-fleet"')]
    assert 'id="wf-policy-widget"' not in controls
    assert "switchView('agents')" in controls


def test_x4_each_section_saves_only_its_own_keys():
    js = _read(JS)
    block = js[js.index('function agentSectionPayload(kind)'):]
    block = block[:block.index('async function saveAgentSection')]
    assert "if (kind === 'tools') return { tools_mode: all.tools_mode, tools: all.tools };" in block
    assert "if (kind === 'ssh') return { ssh_hosts_allow: all.ssh_hosts_allow, ssh_mode: all.ssh_mode };" in block
    assert "if (kind === 'rules') return {" in block
    assert "rate_limit_per_min: all.rate_limit_per_min" in block
    # Advanced saves the JSON only, without rewriting it from the Rules tab.
    adv = block[block.index("if (kind === 'advanced')"):block.index('const all')]
    assert "return { arg_policy: doc.arg_policy || {}, approval: doc.approval || {} }" in adv
    html = _read(HTML)
    for kind in ('tools', 'rules', 'ssh', 'advanced'):
        assert "saveAgentSection('%s')" % kind in html


def test_x4_the_details_are_reachable_from_every_entry_point():
    js = _read(JS)
    assert 'function openAgentDetail(id)' in js
    assert 'function backToAgents()' in js
    assert 'function openHistoryForSession(sid)' in js
    assert '/activity' in js
    # Opening an agent points the existing web-fetch panel at that principal.
    assert "wf.value = agent.name; loadWebFetchPolicy();" in js

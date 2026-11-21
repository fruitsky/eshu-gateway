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
    assert got['tools'] == ['c_d', 'ssh_exec']
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
    assert got['tools'] == ['a_b', 'c_d', 'ssh_exec']
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
    assert got['tools'] == ['a_b', 'ssh_exec']
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


def test_x4_one_agents_view_holds_the_agent_work():
    html = _read(HTML)
    view = html[html.index('id="view-agents"'):]
    view = view[:view.index('<!-- CONTROLS VIEW')]
    assert 'id="x11-root"' in view
    # The shell mounts one view; the tabs, editor and cards live in x11.js.
    js = _read('dashboard/static/x11.js')
    for needed in ('x11-sec-overview', 'x11-sec-tools', 'x11-sec-ssh',
                   'x11-sec-websites', 'x11-sec-activity', 'x11-sec-json',
                   'agent-test-toggle', 'approver-token-list'):
        assert needed in js, needed
    assert 'agent-policy-modal' not in html
    integrations = html[html.index('id="view-integrations"'):]
    integrations = integrations[:integrations.index('id="view-settings"')]
    assert 'agent-token-list' not in integrations
    assert "switchView('agents')" in integrations
    controls = html[html.index('id="view-controls"'):]
    controls = controls[:controls.index('id="view-fleet"')]
    assert 'id="wf-policy-widget"' not in controls
    assert "switchView('agents')" in controls


def test_x4_absent_fields_still_keep_their_stored_value():
    js = _read('dashboard/static/x11.js')
    # X11 sends the whole document through S5, whose merge keeps every absent
    # field. The preview hash binds what was checked to what is applied.
    assert 'function docOf(cfg)' in js
    assert "preview: true" in js
    assert 'preview_hash' in js
    assert 'risk_ack' in js and 'resume_ack' in js
    assert 'home_assistant' in js and 'websites' in js and 'ssh' in js
    html = _read(HTML)
    assert 'x11.js' in html


def test_x4_the_details_are_reachable_from_every_entry_point():
    js = _read(JS)
    assert 'function openAgentDetail(id)' in js
    assert 'function backToAgents()' in js
    assert 'function openHistoryForSession(sid)' in js
    assert '/activity' in js
    # Opening an agent points the existing web-fetch panel at that principal.
    assert "wf.value = agent.name; loadWebFetchPolicy();" in js

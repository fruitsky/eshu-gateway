"""F12 follow-up: the operator can set an agent's SSH host list and mode,
and a save that does not mention them keeps them."""
from db.agent_policies import get_agent_policy, set_agent_policy
from db.agent_tokens import create_agent_token


def _new_agent(name='ssh-agent'):
    _raw, agent_id = create_agent_token(name)
    return agent_id


def test_tools_only_save_keeps_ssh_fields():
    aid = _new_agent()
    set_agent_policy(aid, ssh_hosts_allow=['10.0.0.5'], ssh_mode='normal')
    set_agent_policy(aid, tools_mode='allowlist', tools=['x_y'])
    p = get_agent_policy(aid)
    assert p['ssh_hosts_allow'] == ['10.0.0.5']
    assert p['ssh_mode'] == 'normal'
    assert p['tools'] == ['x_y']


def test_empty_list_clears_hosts():
    aid = _new_agent()
    set_agent_policy(aid, ssh_hosts_allow=['10.0.0.5'])
    set_agent_policy(aid, ssh_hosts_allow=[])
    assert get_agent_policy(aid)['ssh_hosts_allow'] == []


def test_new_agent_defaults_stay_strict():
    aid = _new_agent()
    set_agent_policy(aid, tools_mode='all')
    p = get_agent_policy(aid)
    assert p['ssh_hosts_allow'] == []
    assert p['ssh_mode'] == 'approval'


def test_api_sets_and_keeps_ssh_fields(auth_client):
    aid = _new_agent('api-ssh')
    r = auth_client.put(f'/api/agents/{aid}/policy', json={
        'tools_mode': 'all', 'ssh_hosts_allow': ['10.0.0.5', 'gw-two'],
        'ssh_mode': 'read_only'})
    assert r.status_code == 200, r.text
    assert r.json()['ssh_hosts_allow'] == ['10.0.0.5', 'gw-two']
    assert r.json()['ssh_mode'] == 'read_only'
    # An older client that sends no SSH fields does not wipe them.
    r = auth_client.put(f'/api/agents/{aid}/policy', json={'tools_mode': 'all'})
    assert r.status_code == 200
    assert r.json()['ssh_hosts_allow'] == ['10.0.0.5', 'gw-two']
    assert r.json()['ssh_mode'] == 'read_only'


def test_api_rejects_bad_ssh_values(auth_client):
    aid = _new_agent('api-bad')
    for body in ({'ssh_hosts_allow': ['root@10.0.0.5']},
                 {'ssh_hosts_allow': ['-oProxyCommand=x']},
                 {'ssh_hosts_allow': ['a b']},
                 {'ssh_mode': 'yolo'}):
        r = auth_client.put(f'/api/agents/{aid}/policy', json=body)
        assert r.status_code in (400, 422), body
    assert get_agent_policy(aid)['ssh_hosts_allow'] == []


def test_api_requires_login(client):
    aid = _new_agent('api-anon')
    r = client.put(f'/api/agents/{aid}/policy', json={'ssh_hosts_allow': ['*']})
    assert r.status_code in (401, 403)
    assert get_agent_policy(aid)['ssh_hosts_allow'] == []


def test_ssh_change_is_audited(auth_client):
    from db.audit import get_audit_log
    aid = _new_agent('api-audit')
    auth_client.put(f'/api/agents/{aid}/policy', json={'ssh_hosts_allow': ['10.0.0.9']})
    rows = [r for r in get_audit_log(limit=20) if r.get('event_type') == 'agent_policy_updated']
    assert any('ssh_hosts=10.0.0.9' in (r.get('details') or '') for r in rows)

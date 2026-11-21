"""F12 follow-up: the operator can set an agent's SSH host list and mode,
and a save that does not mention them keeps them."""
from db.agent_policies import (
    get_agent_config_version, get_agent_policy, migrate_ssh_hosts_without_exec,
    set_agent_policy, SSH_EXEC_TOOL, SSH_HOSTS_CLEARED_KEY, validate_policy,
    write_policy_row,
)
from db.agent_tokens import create_agent_token
from db.audit import get_audit_log
from db.core import db_conn


def _new_agent(name='ssh-agent'):
    _raw, agent_id = create_agent_token(name)
    return agent_id


def _legacy_hosts_without_exec(agent_id, hosts=('10.0.0.5',)):
    """A row from before hosts implied `ssh_exec`: hosts, no tool key."""
    clean = validate_policy(
        'allowlist', ['home_assistant_call_service'], {}, {}, 0,
        list(hosts), 'normal')
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        write_policy_row(conn, agent_id, clean)
        conn.commit()


def _forget_host_migration():
    with db_conn() as conn:
        conn.execute('DELETE FROM meta WHERE key = ?', (SSH_HOSTS_CLEARED_KEY,))
        conn.commit()


def test_tools_only_save_keeps_ssh_fields():
    aid = _new_agent()
    set_agent_policy(aid, ssh_hosts_allow=['10.0.0.5'], ssh_mode='normal')
    set_agent_policy(aid, tools_mode='allowlist', tools=['x_y'])
    p = get_agent_policy(aid)
    assert p['ssh_hosts_allow'] == ['10.0.0.5']
    assert p['ssh_mode'] == 'normal'
    assert p['tools'] == ['ssh_exec', 'x_y']


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


def test_startup_clears_hosts_that_never_had_ssh_exec():
    """CB18: an agent that could not run SSH must not gain it on a save."""
    aid = _new_agent('legacy-hosts')
    _legacy_hosts_without_exec(aid, hosts=['10.0.0.5', 'gw-two'])
    before = get_agent_config_version(aid)
    _forget_host_migration()

    assert migrate_ssh_hosts_without_exec() == 1
    policy = get_agent_policy(aid)
    assert policy['ssh_hosts_allow'] == []
    assert SSH_EXEC_TOOL not in policy['tools']
    assert get_agent_config_version(aid) == before + 1

    rows = [r for r in get_audit_log(limit=30)
            if r['event_type'] == 'agent_ssh_hosts_cleared']
    assert len(rows) == 1
    details = rows[0]['details']
    assert "agent #%d 'legacy-hosts'" % aid in details
    assert 'SSH hosts cleared: they had no ssh_exec' in details
    assert 're-pick machines to grant SSH' in details
    assert '10.0.0.5, gw-two' in details
    # The meta flag makes a second startup a no-op.
    assert migrate_ssh_hosts_without_exec() == 0


def test_a_websites_only_save_grants_nothing_after_the_clear(auth_client):
    aid = _new_agent('legacy-web')
    _legacy_hosts_without_exec(aid)
    _forget_host_migration()
    migrate_ssh_hosts_without_exec()
    got = auth_client.get('/api/agents/%d/config' % aid).json()
    document = {'schema_version': 1, 'websites': {
        'enabled': True, 'allow': ['docs.example.com'], 'deny': []}}
    preview = auth_client.put('/api/agents/%d/config' % aid, json={
        'document': document, 'version': got['version'], 'preview': True,
        'risk_ack': False, 'preview_hash': '', 'resume_ack': False})
    assert preview.status_code == 200, preview.text
    assert 'run commands' not in preview.json()['summary']
    applied = auth_client.put('/api/agents/%d/config' % aid, json={
        'document': document, 'version': got['version'], 'preview': False,
        'preview_hash': preview.json()['preview_hash'],
        'risk_ack': False, 'resume_ack': False})
    assert applied.status_code == 200, applied.text
    policy = get_agent_policy(aid)
    assert policy['ssh_hosts_allow'] == []
    assert SSH_EXEC_TOOL not in policy['tools']


def test_the_preview_names_an_ssh_exec_change_from_another_section(
        auth_client):
    """CB18: the derived key, not only a host edit, drives the sentence."""
    aid = _new_agent('legacy-preview')
    _legacy_hosts_without_exec(aid)
    got = auth_client.get('/api/agents/%d/config' % aid).json()
    document = {
        'schema_version': 1,
        'tools': {'mode': 'allowlist',
                  'allow': ['home_assistant_call_service'],
                  'approval': {}, 'argument_rules': {},
                  'rate_limit_per_min': 0},
        'ssh': {'mode': 'normal', 'hosts': ['10.0.0.5']},
        'websites': {'enabled': True, 'allow': ['docs.example.com'],
                     'deny': []},
    }
    preview = auth_client.put('/api/agents/%d/config' % aid, json={
        'document': document, 'version': got['version'], 'preview': True,
        'risk_ack': False, 'preview_hash': '', 'resume_ack': False})
    assert preview.status_code == 200, preview.text
    assert ('will be able to run commands on 10.0.0.5'
            in preview.json()['summary'])
    # The same preview names it in the other direction.
    set_agent_policy(aid, tools_mode='allowlist',
                     tools=['home_assistant_call_service'],
                     ssh_hosts_allow=['10.0.0.5'], ssh_mode='normal')
    got = auth_client.get('/api/agents/%d/config' % aid).json()
    document['ssh']['hosts'] = []
    preview = auth_client.put('/api/agents/%d/config' % aid, json={
        'document': document, 'version': got['version'], 'preview': True,
        'risk_ack': False, 'preview_hash': '', 'resume_ack': False})
    assert preview.status_code == 200, preview.text
    assert 'will no longer be able to run commands' in preview.json()['summary']


def test_any_gateway_asks_first_in_the_ui():
    """Security BF1: ticking '*' shows a confirmation that says it also
    covers gateways enrolled later; only the confirm button stores it."""
    import pathlib
    static = pathlib.Path(__file__).resolve().parent.parent / 'dashboard' / 'static'
    js = (static / 'x11.js').read_text()
    assert 'every machine enrolled later' in js
    toggle = js.split('function x11ToggleAnyHost(cb) {', 1)[1].split('\n  }', 1)[0]
    first_set = toggle.find("hosts = ['*']")
    assert first_set != -1
    assert toggle.find('window.confirm(') < first_set
    assert 'data-ssh-any' in js

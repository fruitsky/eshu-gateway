"""F11 operator feedback: handler Test, SSH key form validation, HA linking."""
import json
import subprocess

import pytest

from core import ha_config_ssh
from db.audit import get_audit_log
from db.integrations import (
    create_integration, create_tool, get_integration, get_integration_config,
    set_integration_config,
)


def _ha(name='Home Assistant', kind='ha'):
    create_integration(name, 'http://192.0.2.50/api', 'none', '', kind=kind)
    return get_integration(name)


def _key(tmp_path, passphrase=''):
    path = tmp_path / ('ha-key' + ('-enc' if passphrase else ''))
    cmd = ['ssh-keygen', '-q', '-t', 'ed25519', '-N', passphrase,
           '-f', str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=20)
    return path.read_text(encoding='utf-8')


def test_test_uses_the_handler_and_shows_the_tofu_fingerprint(
        auth_client, monkeypatch, tmp_path):
    import main as app_main
    _ha()
    from core.ha_ssh_seed import seed_ha_ssh_tools
    from db.integrations import set_integration_config
    create_integration('ha-files', '192.0.2.40', 'none', '', kind='ha_ssh')
    integration = get_integration('ha-files')
    from db.integrations import update_integration
    update_integration('ha-files', secret=(
        '-----BEGIN OPENSSH PRIVATE KEY-----\nplaceholder\n'
        '-----END OPENSSH PRIVATE KEY-----'))
    integration = get_integration('ha-files')
    seed_ha_ssh_tools(integration['id'])
    called = {'http': 0}
    monkeypatch.setenv('ESHU_SSH_KEY_DIR', str(tmp_path / 'ssh'))

    def _fake_http(*args, **kwargs):
        called['http'] += 1
        raise AssertionError('the HTTP proxy must not be used')

    set_integration_config(integration, {
        'host_key': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample',
        'host_fingerprint': 'SHA256:examplefingerprint'})

    def _fake_ssh(*args, **kwargs):
        return {'rc': 0, 'stdout': '1 1 /config/configuration.yaml\n',
                'stderr': '', 'timed_out': False}

    monkeypatch.setattr(app_main, 'execute_integration_call', _fake_http)
    monkeypatch.setattr(ha_config_ssh.ssh_client, 'run_ssh', _fake_ssh)
    r = auth_client.post('/api/integrations/ha-files/test')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['status_code'] == 200
    assert body['tool'] == 'ha_config_list'
    assert body['fingerprint'] == 'SHA256:examplefingerprint'
    assert 'ssh-keygen -lf' in body['fingerprint_hint']
    assert called['http'] == 0
    from db.integrations import get_integration_calls
    rows = get_integration_calls()['rows']
    assert any(r.get('integration') == 'ha-files' and r.get('method') == 'SSH'
               for r in rows), 'the handler Test must write its call row'


def test_test_shows_the_plain_not_root_refusal(auth_client, monkeypatch):
    _ha()
    from core.ha_ssh_seed import seed_ha_ssh_tools
    create_integration('ha-files', '192.0.2.40', 'none', '', kind='ha_ssh')
    seed_ha_ssh_tools(get_integration('ha-files')['id'])

    def _fake_list(integration, ctx=None):
        raise ha_config_ssh.HaConfigError(
            'config_not_root',
            'The add-on user is not root. Set the SSH user to root.')

    monkeypatch.setattr(ha_config_ssh, 'list_configs', _fake_list)
    r = auth_client.post('/api/integrations/ha-files/test')
    assert r.status_code == 200
    assert r.json()['status_code'] == 400
    assert r.json()['error'] == 'The add-on user is not root. Set the SSH user to root.'


def test_an_http_integration_test_is_unchanged(auth_client, monkeypatch):
    import main as app_main
    _ha('api', kind='custom')
    integration = get_integration('api')
    create_integration('api2', 'http://192.0.2.41', 'none', '', kind='custom')
    integration = get_integration('api2')
    create_tool(integration['id'], 'read', 'Read', 'GET', '/x', [], '{}',
                read_only=True)
    seen = {}

    def _fake(*args, **kwargs):
        seen['called'] = True
        return {'status_code': 200, 'body': '{"ok":true}', 'truncated': False,
                'error': None}

    monkeypatch.setattr(app_main, 'execute_integration_call', _fake)
    r = auth_client.post('/api/integrations/api2/test')
    assert r.status_code == 200, r.text
    assert r.json()['preview'] == '{"ok":true}'
    assert seen.get('called') is True


def test_create_ha_ssh_validates_the_key_and_hides_it(auth_client, tmp_path):
    _ha()
    key = _key(tmp_path)
    r = auth_client.post('/api/integrations', json={
        'name': 'ha-files', 'base_url': '192.0.2.40', 'kind': 'ha_ssh',
        'auth_type': 'none', 'secret': key, 'ssh_user': 'root',
        'ssh_port': 2222,
    })
    assert r.status_code == 200, r.text
    row = [i for i in auth_client.get('/api/integrations').json()
           if i['name'] == 'ha-files'][0]
    assert row['secret_set'] is True
    assert row['secret_suffix'] == ''
    assert 'secret' not in row
    cfg = get_integration_config(get_integration('ha-files'))
    assert cfg['ssh_user'] == 'root'
    assert cfg['ssh_port'] == 2222
    assert cfg['ha_integration'] == 'Home Assistant'


def test_create_ha_ssh_refuses_a_bad_key_passphrase_user_or_port(
        auth_client, tmp_path):
    _ha()
    bad = auth_client.post('/api/integrations', json={
        'name': 'bad', 'base_url': '192.0.2.40', 'kind': 'ha_ssh',
        'auth_type': 'none', 'secret': 'not a key'})
    assert bad.status_code == 400
    encrypted = _key(tmp_path, 'passphrase')
    enc = auth_client.post('/api/integrations', json={
        'name': 'enc', 'base_url': '192.0.2.40', 'kind': 'ha_ssh',
        'auth_type': 'none', 'secret': encrypted})
    assert enc.status_code == 400
    good = _key(tmp_path)
    user = auth_client.post('/api/integrations', json={
        'name': 'user', 'base_url': '192.0.2.40', 'kind': 'ha_ssh',
        'auth_type': 'none', 'secret': good, 'ssh_user': 'not root'})
    assert user.status_code == 400
    port = auth_client.post('/api/integrations', json={
        'name': 'port', 'base_url': '192.0.2.40', 'kind': 'ha_ssh',
        'auth_type': 'none', 'secret': good, 'ssh_port': 0})
    assert port.status_code == 400


def test_dismiss_join_clears_pending_and_is_audited(auth_client, monkeypatch):
    _ha()
    from core import allowances as a
    from db.agent_tokens import create_agent_token
    monkeypatch.setattr(a, '_ha_registry', lambda: {
        'switch.kettle': {'id': 'reg-kettle', 'labels': []}})
    monkeypatch.setattr(a, '_risky_classes', lambda entities: [])
    from db.core import db_conn
    raw, aid = create_agent_token('household')
    a.ensure_initialised()
    row = a.create_allowance('operator', aid, 'Plugs', 'switch', 'turn_off',
                             ['switch.kettle'])
    with db_conn() as conn:
        conn.execute('UPDATE allowances SET pending_json = ? WHERE id = ?',
                     (json.dumps([{'entity_id': 'switch.joiner',
                                   'registry_id': 'reg-joiner'}]), row['id']))
        conn.commit()
    r = auth_client.post('/api/allowances/%d/dismiss-join' % row['id'])
    assert r.status_code == 200, r.text
    assert r.json()['dismissed'] == ['switch.joiner']
    assert a.get_allowance(row['id'])['pending_json'] == '[]'
    assert 'allowance_join_dismissed' in [x['event_type']
                                          for x in get_audit_log(20)]


def test_dismiss_join_is_operator_only(client):
    assert client.post('/api/allowances/1/dismiss-join').status_code == 401

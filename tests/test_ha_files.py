"""F11 operator feedback: handler Test, SSH key form validation, HA linking."""
import json
import os
import subprocess
import tempfile

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
    row = next(r for r in rows
               if r.get('integration') == 'ha-files' and r.get('method') == 'SSH')
    assert row.get('agent') == 'operator-test', row


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


def test_operator_test_refuses_a_write_tool_directly(temp_db):
    from core.tool_runner import run_tool_for_operator_test
    create_integration('writer', 'http://192.0.2.60', 'none', '', kind='custom')
    integration = get_integration('writer')
    create_tool(integration['id'], 'write_thing', 'Write', 'POST', '/x', [], '{}',
                read_only=False)
    out = json.loads(run_tool_for_operator_test('writer', 'write_thing', {}))
    assert out['status_code'] == 403
    assert out['error'] == 'operator_test_read_only'


def test_operator_test_names_the_actor_in_the_pin_audit(
        auth_client, monkeypatch, tmp_path):
    _ha()
    from core.ha_ssh_seed import seed_ha_ssh_tools
    from db.integrations import update_integration
    create_integration('ha-files', '192.0.2.40', 'none', '', kind='ha_ssh')
    update_integration('ha-files', secret=(
        '-----BEGIN OPENSSH PRIVATE KEY-----\nplaceholder\n'
        '-----END OPENSSH PRIVATE KEY-----'))
    seed_ha_ssh_tools(get_integration('ha-files')['id'])
    monkeypatch.setenv('ESHU_SSH_KEY_DIR', str(tmp_path / 'ssh'))

    def _fake_ssh(target, command, known_hosts, key_path, timeout,
                  stdin=None, strict_host_key=True, binary=False):
        if not strict_host_key:
            with open(known_hosts, 'w') as fh:
                fh.write('%s ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample\n'
                         % target['ip'])
        return {'rc': 0, 'stdout': '1 1 /config/configuration.yaml\n',
                'stderr': '', 'timed_out': False}

    monkeypatch.setattr(ha_config_ssh.ssh_client, 'run_ssh', _fake_ssh)
    r = auth_client.post('/api/integrations/ha-files/test')
    assert r.status_code == 200, r.text
    pin = [x for x in get_audit_log(20) if x['event_type'] == 'ha_config_pin']
    assert pin and pin[0]['actor'] == 'operator-test'


def test_edit_bad_key_is_a_400_and_a_good_key_is_checked_once(
        auth_client, monkeypatch, tmp_path):
    import main as app_main
    _ha()
    key = _key(tmp_path)
    create_integration('ha-files', '192.0.2.40', 'none', key, kind='ha_ssh')
    bad = auth_client.put('/api/integrations/ha-files', json={'secret': 'not a key'})
    assert bad.status_code == 400, bad.text
    calls = []
    original = app_main.validate_private_key

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(app_main, 'validate_private_key', counted)
    good = auth_client.put('/api/integrations/ha-files', json={'secret': key})
    assert good.status_code == 200, good.text
    assert len(calls) == 1


def test_changing_the_kind_to_ha_ssh_validates_the_stored_secret(
        auth_client, tmp_path):
    _ha()
    create_integration('thing', '192.0.2.61', 'none', 'not a key', kind='custom')
    bad = auth_client.put('/api/integrations/thing', json={'kind': 'ha_ssh'})
    assert bad.status_code == 400, bad.text
    key = _key(tmp_path)
    create_integration('good', '192.0.2.62', 'none', key, kind='custom')
    ok = auth_client.put('/api/integrations/good', json={'kind': 'ha_ssh'})
    assert ok.status_code == 200, ok.text


def test_a_disabled_ha_link_does_not_block_edits_and_is_kept(
        auth_client, tmp_path):
    from db.integrations import update_integration
    ha = _ha()
    key = _key(tmp_path)
    r = auth_client.post('/api/integrations', json={
        'name': 'ha-files', 'base_url': '192.0.2.40', 'kind': 'ha_ssh',
        'auth_type': 'none', 'secret': key})
    assert r.status_code == 200, r.text
    assert get_integration_config(get_integration('ha-files'))['ha_integration'] \
        == ha['name']
    update_integration(ha['name'], enabled=False)
    edit = auth_client.put('/api/integrations/ha-files',
                           json={'base_url': '192.0.2.41'})
    assert edit.status_code == 200, edit.text
    cfg = get_integration_config(get_integration('ha-files'))
    assert cfg['ha_integration'] == ha['name']
    row = [i for i in auth_client.get('/api/integrations').json()
           if i['name'] == 'ha-files'][0]
    assert json.loads(row['config_json'])['ha_integration'] == ha['name']


def test_the_key_check_uses_and_removes_a_private_temp_directory(monkeypatch):
    seen = {}
    original = tempfile.mkdtemp

    def make(*args, **kwargs):
        path = original(*args, **kwargs)
        seen['dir'] = path
        return path

    monkeypatch.setattr(ha_config_ssh.tempfile, 'mkdtemp', make)
    with pytest.raises(ha_config_ssh.HaConfigError):
        ha_config_ssh.validate_private_key(
            '-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-key\n'
            '-----END OPENSSH PRIVATE KEY-----')
    assert seen.get('dir') and not os.path.exists(seen['dir'])


def test_dismissed_joiners_stay_dismissed_until_reoffered_or_accepted(
        auth_client, monkeypatch):
    _ha()
    from core import allowances as a
    from db.agent_tokens import create_agent_token
    entities = {'switch.lamp': {'id': 'reg-lamp', 'labels': ['lbl-lights']}}
    monkeypatch.setattr(a, '_ha_registry', lambda: entities)
    monkeypatch.setattr(a, '_risky_classes', lambda rows: [])
    _raw, aid = create_agent_token('household')
    a.ensure_initialised()
    row = a.create_allowance('operator', aid, 'Lights', 'switch', 'turn_on',
                             ['switch.lamp'], source_label_id='lbl-lights')
    aid_allowance = row['id']
    entities['switch.joiner'] = {'id': 'reg-joiner', 'labels': ['lbl-lights']}
    a.refresh_labels()
    assert {p['entity_id'] for p in json.loads(
        a.get_allowance(aid_allowance)['pending_json'])} == {'switch.joiner'}
    dismissed = auth_client.post('/api/allowances/%d/dismiss-join' % aid_allowance)
    assert dismissed.status_code == 200, dismissed.text
    row = a.get_allowance(aid_allowance)
    assert json.loads(row['pending_json']) == []
    assert json.loads(row['dismissed_json']) == [
        {'entity_id': 'switch.joiner', 'registry_id': 'reg-joiner'}]
    a.refresh_labels()
    row = a.get_allowance(aid_allowance)
    assert json.loads(row['pending_json']) == []
    assert json.loads(row['dismissed_json'])
    reoffered = auth_client.post('/api/allowances/%d/reoffer-join' % aid_allowance)
    assert reoffered.status_code == 200, reoffered.text
    row = a.get_allowance(aid_allowance)
    assert {p['entity_id'] for p in json.loads(row['pending_json'])} == {'switch.joiner'}
    assert json.loads(row['dismissed_json']) == []
    auth_client.post('/api/allowances/%d/dismiss-join' % aid_allowance)
    out = a.accept_join('operator', aid_allowance, include_dismissed=True)
    assert out['accepted'] == ['switch.joiner']
    row = a.get_allowance(aid_allowance)
    assert 'switch.joiner' in {p['entity_id'] for p in json.loads(row['entities_json'])}
    assert json.loads(row['dismissed_json']) == []


def test_a_dismissal_clears_when_the_device_leaves_the_label(
        auth_client, monkeypatch):
    _ha()
    from core import allowances as a
    from db.agent_tokens import create_agent_token
    entities = {'switch.lamp': {'id': 'reg-lamp', 'labels': ['lbl-lights']}}
    monkeypatch.setattr(a, '_ha_registry', lambda: entities)
    monkeypatch.setattr(a, '_risky_classes', lambda rows: [])
    _raw, aid = create_agent_token('household')
    a.ensure_initialised()
    row = a.create_allowance('operator', aid, 'Lights', 'switch', 'turn_on',
                             ['switch.lamp'], source_label_id='lbl-lights')
    entities['switch.joiner'] = {'id': 'reg-joiner', 'labels': ['lbl-lights']}
    a.refresh_labels()
    auth_client.post('/api/allowances/%d/dismiss-join' % row['id'])
    entities['switch.joiner']['labels'] = []
    a.refresh_labels()
    assert json.loads(a.get_allowance(row['id'])['dismissed_json']) == []
    entities['switch.joiner']['labels'] = ['lbl-lights']
    a.refresh_labels()
    assert {p['entity_id'] for p in json.loads(
        a.get_allowance(row['id'])['pending_json'])} == {'switch.joiner'}


def test_reoffer_join_is_operator_only(client):
    assert client.post('/api/allowances/1/reoffer-join').status_code == 401


def _label_with_joiner(monkeypatch, registry_id='reg-joiner'):
    from core import allowances as a
    from db.agent_tokens import create_agent_token
    entities = {'switch.lamp': {'id': 'reg-lamp', 'labels': ['lbl-lights']}}
    monkeypatch.setattr(a, '_ha_registry', lambda: entities)
    monkeypatch.setattr(a, '_risky_classes', lambda rows: [])
    _raw, aid = create_agent_token('household')
    a.ensure_initialised()
    row = a.create_allowance('operator', aid, 'Lights', 'switch', 'turn_on',
                             ['switch.lamp'], source_label_id='lbl-lights')
    entities['switch.joiner'] = {'id': registry_id,
                                 'labels': ['lbl-lights']}
    a.refresh_labels()
    return a, aid, row['id'], entities


def test_two_refreshes_with_the_same_dismissed_joiner_write_nothing(
        auth_client, monkeypatch):
    from db.agent_policies import get_agent_config_version
    from db.audit import get_audit_log
    a, _aid, allowance_id, _entities = _label_with_joiner(monkeypatch)
    assert auth_client.post('/api/allowances/%d/dismiss-join'
                            % allowance_id).status_code == 200
    before_version = get_agent_config_version(_aid)
    before_audit = len([r for r in get_audit_log(50)
                        if r['event_type'] == 'allowance_label_refresh'])
    a.refresh_labels()
    a.refresh_labels()
    assert get_agent_config_version(_aid) == before_version
    after_audit = len([r for r in get_audit_log(50)
                       if r['event_type'] == 'allowance_label_refresh'])
    assert after_audit == before_audit


def test_an_empty_registry_dismissal_expires_when_the_id_becomes_known(
        auth_client, monkeypatch):
    a, _aid, allowance_id, entities = _label_with_joiner(monkeypatch,
                                                         registry_id='')
    assert auth_client.post('/api/allowances/%d/dismiss-join'
                            % allowance_id).status_code == 200
    entities['switch.joiner']['id'] = 'reg-known'
    a.refresh_labels()
    row = a.get_allowance(allowance_id)
    assert json.loads(row['dismissed_json']) == []
    assert {p['entity_id'] for p in json.loads(row['pending_json'])} == {
        'switch.joiner'}

"""X11/S5: one JSON document per agent, checked like the GUI and atomic."""
import pytest

from core import allowances as a
from db.agent_tokens import create_agent_token
from db.agent_policies import get_agent_policy, set_agent_policy
from db.integrations import create_integration
from db.web_fetch import get_web_fetch_policy


ENTITIES = {
    'switch.kettle': {'id': 'reg-kettle', 'labels': []},
    'switch.lamp': {'id': 'reg-lamp', 'labels': []},
    'cover.garage': {'id': 'reg-garage', 'labels': []},
}


@pytest.fixture
def agent():
    raw, aid = create_agent_token('household')
    return {'id': aid, 'token': raw, 'name': 'household'}


@pytest.fixture
def ha(monkeypatch):
    create_integration('Home Assistant', 'http://192.0.2.50:8123/api',
                       'none', 'topsecret', kind='ha')
    monkeypatch.setattr(a, '_ha_registry', lambda: ENTITIES)
    monkeypatch.setattr(
        a, '_risky_classes',
        lambda entities: ([{'entity_id': 'cover.garage',
                            'device_class': 'garage'}]
                          if 'cover.garage' in entities else []))
    return ENTITIES


def _get(c, aid):
    r = c.get('/api/agents/%d/config' % aid)
    assert r.status_code == 200, r.text
    return r.json()


def _document(got):
    return {k: v for k, v in got.items() if k != 'version'}


def _put(c, aid, document, version, preview=False, risk_ack=False):
    return c.put('/api/agents/%d/config' % aid, json={
        'document': document, 'version': version,
        'preview': preview, 'risk_ack': risk_ack})


def _perm(**kw):
    body = {'name': 'Plugs', 'domain': 'switch', 'service': 'turn_off',
            'entities': ['switch.kettle']}
    body.update(kw)
    return body


def test_the_document_has_no_token_or_secret_and_round_trips(
        auth_client, agent, ha):
    got = _get(auth_client, agent['id'])
    text = str(got)
    assert agent['token'] not in text
    assert 'token' not in text.lower()
    assert 'topsecret' not in text
    assert got['schema_version'] == 1
    assert got['agent'] == {'id': agent['id'], 'name': 'household',
                            'revoked': False}
    for section in ('tools', 'ssh', 'websites', 'home_assistant'):
        assert section in got
    document = _document(got)
    r = _put(auth_client, agent['id'], document, got['version'])
    assert r.status_code == 200, r.text
    assert r.json()['changed'] is False
    assert r.json()['version'] == got['version']
    assert r.json()['summary'] == 'household is unchanged.'


def test_absent_fields_keep_their_stored_value(auth_client, agent, ha):
    set_agent_policy(agent['id'], tools_mode='allowlist',
                     tools=['home_assistant_call_service'],
                     ssh_hosts_allow=['192.0.2.10'], ssh_mode='approval')
    got = _get(auth_client, agent['id'])
    assert got['tools']['allow'] == ['home_assistant_call_service']
    r = _put(auth_client, agent['id'],
             {'schema_version': 1, 'ssh': {'mode': 'normal'}}, got['version'])
    assert r.status_code == 200, r.text
    after = _get(auth_client, agent['id'])
    assert after['ssh']['mode'] == 'normal'
    assert after['tools']['allow'] == ['home_assistant_call_service']
    assert after['tools']['mode'] == 'allowlist'
    assert after['websites'] == got['websites']
    assert after['home_assistant'] == got['home_assistant']


def test_a_protected_device_is_refused_even_with_a_tools_change(
        auth_client, agent, ha):
    set_agent_policy(agent['id'], tools_mode='allowlist',
                     tools=['home_assistant_call_service'])
    before = _get(auth_client, agent['id'])
    a.set_protected_entities('operator', ['switch.kettle'])
    document = {
        'schema_version': 1,
        'tools': {'mode': 'all', 'allow': []},
        'home_assistant': {'permissions': [_perm()]},
    }
    r = _put(auth_client, agent['id'], document, before['version'])
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'protected_device'
    after = _get(auth_client, agent['id'])
    assert after['version'] == before['version']
    assert after['tools'] == before['tools']
    assert after['home_assistant'] == before['home_assistant']


def test_the_ceiling_never_allowed_and_risk_ack_are_enforced(
        auth_client, agent, ha):
    got = _get(auth_client, agent['id'])
    cover = {'schema_version': 1, 'home_assistant': {'permissions': [
        _perm(domain='cover', service='open_cover', entities=['cover.garage'])]}}
    r = _put(auth_client, agent['id'], cover, got['version'])
    assert r.status_code == 400
    assert 'ceiling' in r.text.lower()

    lock = {'schema_version': 1, 'home_assistant': {'permissions': [
        _perm(domain='lock', service='unlock', entities=['lock.front'])]}}
    r = _put(auth_client, agent['id'], lock, got['version'])
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'never_allowed'

    ids = a.enabled_ids() + ['cover.open_cover']
    assert auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'confirm': True,
        'set_hash': a.set_hash()}).status_code == 200
    preview = _put(auth_client, agent['id'], cover, got['version'],
                   preview=True)
    assert preview.status_code == 200, preview.text
    assert preview.json()['risk_ack_required'] is True
    assert any(x['kind'] == 'device' for x in preview.json()['risky'])
    refused = _put(auth_client, agent['id'], cover, got['version'])
    assert refused.status_code == 400
    assert refused.json()['detail']['code'] == 'risk_ack_required'
    accepted = _put(auth_client, agent['id'], cover, got['version'],
                    risk_ack=True)
    assert accepted.status_code == 200, accepted.text


def test_schema_unknown_field_and_stale_version_are_refused(
        auth_client, agent, ha):
    got = _get(auth_client, agent['id'])
    r = _put(auth_client, agent['id'],
             {'schema_version': True}, got['version'])
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'schema_version'
    r = _put(auth_client, agent['id'],
             {'schema_version': 1, 'mystery': 1}, got['version'])
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'unknown_field'
    ok = _put(auth_client, agent['id'],
              {'schema_version': 1, 'home_assistant': {'permissions': [
                  _perm()]}}, got['version'])
    assert ok.status_code == 200, ok.text
    stale = _put(auth_client, agent['id'],
                 {'schema_version': 1, 'ssh': {'mode': 'normal'}},
                 got['version'])
    assert stale.status_code == 409
    assert stale.json()['detail']['code'] == 'config_changed'


def test_a_website_section_is_applied(auth_client, agent, ha):
    got = _get(auth_client, agent['id'])
    document = {'schema_version': 1, 'websites': {
        'enabled': True, 'allow': ['docs.example.com'], 'deny': []}}
    r = _put(auth_client, agent['id'], document, got['version'])
    assert r.status_code == 200, r.text
    policy = get_web_fetch_policy('household')
    assert policy['enabled'] is True
    assert policy['allow'] == ['docs.example.com']
    assert _get(auth_client, agent['id'])['websites'] == {
        'enabled': True, 'allow': ['docs.example.com'], 'deny': []}


def test_pasting_another_agents_document_names_the_target_in_the_preview(
        auth_client, agent, ha):
    set_agent_policy(agent['id'], tools_mode='allowlist',
                     tools=['home_assistant_call_service'])
    source = _document(_get(auth_client, agent['id']))
    _raw, other = create_agent_token('target')
    got = _get(auth_client, other)
    r = _put(auth_client, other, source, got['version'], preview=True)
    assert r.status_code == 200, r.text
    assert 'target' in r.json()['summary']
    assert r.json()['changed'] is True
    assert get_agent_policy(other)['tools_mode'] == 'all'


def test_operator_only(client):
    assert client.get('/api/agents/1/config').status_code == 401
    assert client.put('/api/agents/1/config', json={
        'document': {'schema_version': 1}, 'version': 1}).status_code == 401


def test_a_write_failure_after_the_policy_rolls_the_policy_back(
        auth_client, agent, ha, monkeypatch):
    set_agent_policy(agent['id'], tools_mode='allowlist',
                     tools=['home_assistant_call_service'])
    before = _get(auth_client, agent['id'])
    document = {
        'schema_version': 1,
        'tools': {'mode': 'all', 'allow': []},
        'websites': {'enabled': True, 'allow': ['example.org'], 'deny': []},
    }
    from core import agent_config

    def boom(*args, **kwargs):
        raise RuntimeError('web write failed')

    monkeypatch.setattr(agent_config, '_write_web', boom)
    with pytest.raises(RuntimeError):
        _put(auth_client, agent['id'], document, before['version'])
    after = _get(auth_client, agent['id'])
    assert after['version'] == before['version']
    assert after['tools'] == before['tools']
    assert after['websites'] == before['websites']


def test_legacy_policy_and_website_writes_bump_the_config_version(
        auth_client, agent, ha):
    got = _get(auth_client, agent['id'])
    r = auth_client.put('/api/agents/%d/policy' % agent['id'],
                        json={'rate_limit_per_min': 7})
    assert r.status_code == 200, r.text
    after_policy = _get(auth_client, agent['id'])
    assert after_policy['version'] == got['version'] + 1
    r = auth_client.put('/api/web-fetch-policies/household', json={
        'enabled': True, 'allow': ['example.org'], 'deny': []})
    assert r.status_code == 200, r.text
    after_web = _get(auth_client, agent['id'])
    assert after_web['version'] == after_policy['version'] + 1


def test_a_website_change_returns_the_final_synced_version(
        auth_client, agent, ha):
    set_agent_policy(agent['id'], tools_mode='allowlist',
                     tools=['home_assistant_call_service'])
    got = _get(auth_client, agent['id'])
    document = {'schema_version': 1, 'websites': {
        'enabled': True, 'allow': ['example.org'], 'deny': []}}
    r = _put(auth_client, agent['id'], document, got['version'])
    assert r.status_code == 200, r.text
    after = _get(auth_client, agent['id'])
    assert r.json()['version'] == after['version']
    assert 'web_fetch_url' in after['tools']['allow']


def test_the_plain_words_name_the_target_only_once(auth_client, agent, ha):
    got = _get(auth_client, agent['id'])
    document = {'schema_version': 1, 'home_assistant': {'permissions': [
        _perm()]}}
    r = _put(auth_client, agent['id'], document, got['version'], preview=True)
    assert r.status_code == 200, r.text
    summary = r.json()['summary']
    assert summary.startswith('household will be able to turn off 1 device.')
    assert summary.count('household') == 1

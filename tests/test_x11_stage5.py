"""X11 Stage 5: the task-first agent screens, server side.

The E2E command list is the suite's own constant, and the T8 guided setup
must produce a policy the suite's preflight accepts with no other clicks.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.agent_policies import get_agent_policy
from db.agent_tokens import create_agent_token
from db.gateways import register_gateway
from db.integrations import create_integration, create_tool, get_integration

GATEWAY = '192.0.2.10'


def _put(client, agent_id, document, version):
    preview = client.put('/api/agents/%d/config' % agent_id, json={
        'document': document, 'version': version, 'preview': True,
        'risk_ack': False, 'preview_hash': '', 'resume_ack': False})
    assert preview.status_code == 200, preview.text
    return client.put('/api/agents/%d/config' % agent_id, json={
        'document': document, 'version': version, 'preview': False,
        'preview_hash': preview.json()['preview_hash'],
        'risk_ack': False, 'resume_ack': False})


def _ha():
    create_integration('Home Assistant', 'http://192.0.2.30:8123',
                       'none', '', kind='ha')
    ha = get_integration('Home Assistant')
    create_tool(ha['id'], 'list_entities', 'List', 'GET', '/states', [], '',
                read_only=True)
    create_tool(ha['id'], 'call_service', 'Call', 'POST', '/services', [], '',
                read_only=False)


def test_the_e2e_command_endpoint_is_the_suite_constant(auth_client):
    from scripts.e2e import run
    r = auth_client.get('/api/e2e/commands')
    assert r.status_code == 200, r.text
    assert r.json()['commands'] == list(run.COMMANDS)
    assert 'hostname' in r.json()['commands']
    assert 'printf e2e-window' in r.json()['commands']


def test_t8_setup_passes_the_e2e_preflight(auth_client, monkeypatch):
    register_gateway(GATEWAY, 'web01.example.test', 'v-test')
    _ha()
    _raw, aid = create_agent_token('e2e-guided')
    got = auth_client.get('/api/agents/%d/config' % aid).json()
    document = {
        'schema_version': 1,
        'tools': {'mode': 'allowlist',
                  'allow': ['home_assistant_list_entities',
                            'home_assistant_call_service'],
                  'approval': {'home_assistant_call_service': 'gated'},
                  'argument_rules': {}, 'rate_limit_per_min': 0},
        'ssh': {'mode': 'normal', 'hosts': [GATEWAY]},
    }
    assert _put(auth_client, aid, document, got['version']).status_code == 200
    r = auth_client.post('/api/agents/%d/test-agent' % aid,
                         json={'enabled': True})
    assert r.status_code == 200, r.text
    policy = get_agent_policy(aid)
    assert 'ssh_exec' in policy['tools']
    # The suite's own preflight, not a copy of its rules.
    import scripts.e2e.run as run
    checked = run.check_test_agent({'policy': policy},
                                   SimpleNamespace(gateway=GATEWAY))
    assert checked['test_agent'] is True

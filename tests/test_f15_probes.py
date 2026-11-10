"""F15: diagnostic probes.

The probes are code: typed parameters in, a fixed command list out, a capped
digest back. These tests pin the parameter rules (P1), the digest parsing,
scrubbing (P5), the cap (P7), and that a probe proposes ONE plan (P4).
"""
import json

import pytest


@pytest.fixture
def gateway(temp_db):
    from db.gateways import register_gateway
    register_gateway('10.0.0.1', 'web01', 'v15.3')
    return '10.0.0.1'


# ── P1: typed parameters, no option injection ───────────────────────────────

@pytest.mark.parametrize('value', [
    '--help', '-x', '../etc/passwd', 'a b', 'a;id', 'a$(id)', "a'b",
    'a\nb', '', '@',
])
def test_a_hostile_unit_is_refused(value):
    from core.probes import ProbeError, validate
    with pytest.raises(ProbeError) as exc:
        validate('diagnose_service', {'host': '10.0.0.1', 'unit': value})
    assert exc.value.code in ('probe_invalid_param', 'probe_unknown_host')


@pytest.mark.parametrize('value', ['--help', '-v', 'a b', 'a;b', 'a$(b)', ''])
def test_a_hostile_container_is_refused(value):
    from core.probes import ProbeError, validate
    with pytest.raises(ProbeError):
        validate('diagnose_container', {'host': '10.0.0.1',
                                        'container': value})


@pytest.mark.parametrize('value', [
    '-bad.example.test', 'a..b', '.example.test', 'a b.example.test',
    'a;id.example.test', 'localhost', 'a-.example.test', 'x' * 70 + '.test',
])
def test_a_hostile_name_is_refused(value):
    from core.probes import ProbeError, validate
    with pytest.raises(ProbeError):
        validate('dns_check', {'name': value})


def test_a_valid_fqdn_is_lowercased(gateway):
    from core.probes import validate
    assert validate('dns_check', {'name': 'App.Example.Test'})['name'] == \
        'app.example.test'


def test_an_unknown_host_is_refused(temp_db):
    from core.probes import ProbeError, validate
    with pytest.raises(ProbeError) as exc:
        validate('disk_pressure', {'host': '192.0.2.99'})
    assert exc.value.code == 'probe_unknown_host'


def test_unknown_parameters_are_refused(gateway):
    from core.probes import ProbeError, validate
    with pytest.raises(ProbeError) as exc:
        validate('diagnose_service', {'host': gateway, 'unit': 'nginx',
                                      'command': 'id'})
    assert 'command' in exc.value.message


# ── the command lists ───────────────────────────────────────────────────────

def test_service_uses_a_structured_state_not_prose(gateway):
    from core.probes import commands, validate
    params = validate('diagnose_service', {'host': gateway, 'unit': 'nginx'})
    cmds = {c['label']: c['command'] for c in commands('diagnose_service', params)}
    assert cmds['state'].startswith('systemctl is-active')
    assert 'status' not in cmds['state']
    assert cmds['journal'].startswith('journalctl -u nginx -n 200')
    assert '--no-pager' in cmds['journal']
    assert cmds['ports'] == 'ss -tlnp'


def test_a_unit_is_shell_quoted(gateway):
    from core.probes import commands, validate
    params = validate('diagnose_service', {'host': gateway, 'unit': 'a@b.service'})
    text = ' '.join(c['command'] for c in commands('diagnose_service', params))
    assert 'a@b.service' in text and ';' not in text


def test_disk_probes_are_bounded(gateway):
    from core.probes import commands, validate
    params = validate('disk_pressure', {'host': gateway})
    cmds = [c['command'] for c in commands('disk_pressure', params)]
    assert 'df -h' in cmds and 'df -i' in cmds
    du = next(c for c in cmds if c.startswith('du '))
    assert '-x' in du and '-d 1' in du and '/var/log' in du


def test_dns_check_runs_no_gateway_command(gateway):
    from core.probes import commands, validate
    params = validate('dns_check', {'name': 'app.example.test'})
    assert commands('dns_check', params) == []


# ── the digest ──────────────────────────────────────────────────────────────

def test_errors_are_deduplicated_and_counted():
    from core.probes import parse_errors
    text = '\n'.join([
        '2026-11-10T10:00:00+0000 host nginx[1]: connect() failed to 10.0.0.5:8080',
        '2026-11-10T10:00:01+0000 host nginx[1]: connect() failed to 10.0.0.6:8080',
        '2026-11-10T10:00:02+0000 host nginx[1]: connect() failed to 10.0.0.7:8080',
        '2026-11-10T10:00:03+0000 host nginx[1]: upstream timed out',
    ])
    rows = parse_errors(text)
    assert len(rows) == 2
    assert rows[0]['count'] == 3 and 'connect() failed' in rows[0]['msg']
    assert rows[1]['count'] == 1


def test_errors_are_capped_at_ten():
    from core.probes import parse_errors
    text = '\n'.join('error kind %s' % chr(ord('a') + i) for i in range(25))
    assert len(parse_errors(text)) == 10


def test_ports_are_parsed_from_ss():
    from core.probes import parse_ss
    text = ('State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n'
            'LISTEN 0      511          0.0.0.0:80        0.0.0.0:*    '
            'users:(("nginx",pid=1,fd=6))\n'
            'LISTEN 0      128             [::]:22           [::]:*    '
            'users:(("sshd",pid=2,fd=3))\n')
    rows = parse_ss(text)
    assert [r['port'] for r in rows] == [80, 22]
    assert rows[0]['process'] == 'nginx'


def test_a_synthetic_token_never_reaches_the_digest():
    from core.probes import build_digest
    outputs = {
        'state': 'active\n',
        'journal': 'API_KEY=SECRETVALUE123 connection refused\n',
        'ports': '',
    }
    digest = build_digest('diagnose_service', outputs,
                          {'params': {'host': '10.0.0.1', 'unit': 'x'}})
    blob = json.dumps(digest)
    assert 'SECRETVALUE123' not in blob
    assert 'connection refused' in blob


def test_the_cap_marks_truncated_and_drops_ports_first():
    from core.probes import MAX_JSON, build_digest, cap_digest
    digest = {'errors': [{'msg': 'm' * 200, 'count': 1, 'last': 0}
                         for _ in range(10)],
              'ports': [{'addr': '0.0.0.0', 'port': p, 'process': 'x' * 40}
                        for p in range(200)],
              'truncated': False}
    capped = cap_digest(digest)
    assert capped['truncated'] is True
    assert len(json.dumps(capped)) <= MAX_JSON
    assert len(capped['ports']) < 200


# ── P4: one grouped plan, then the digest ──────────────────────────────────

def test_a_probe_proposes_one_plan_not_many_requests(gateway):
    from core.probes import plan_probe
    from db.requests import get_all_requests
    from db.sessions import upsert_session
    upsert_session('sess-1', agent='agent-a')
    out = plan_probe('diagnose_service',
                     {'host': gateway, 'unit': 'nginx'}, 'sess-1')
    assert out['status'] == 'pending'
    assert out['items'] == 3
    assert get_all_requests() == []          # nothing ran, no JIT rows


def test_an_unknown_probe_is_refused(gateway):
    from core.probes import plan_probe
    out = plan_probe('diagnose_everything', {}, 'sess-1')
    assert out['error'] == 'probe_unknown'


def test_a_probe_without_a_session_is_refused(gateway):
    from core.probes import plan_probe
    out = plan_probe('disk_pressure', {'host': gateway}, '')
    assert out['error'] == 'probe_no_session'


def test_probe_result_is_pending_before_approval(gateway):
    from core.probes import plan_probe, probe_result
    from db.sessions import upsert_session
    upsert_session('sess-1', agent='agent-a')
    out = plan_probe('disk_pressure', {'host': gateway}, 'sess-1')
    res = probe_result(out['plan_id'], 'sess-1')
    assert res['status'] == 'pending'


def test_probe_result_runs_the_commands_after_approval(gateway, monkeypatch):
    from core.probes import plan_probe, probe_result
    from db.session_plans import approve_plan
    from db.sessions import upsert_session
    upsert_session('sess-1', agent='agent-a')
    out = plan_probe('disk_pressure', {'host': gateway}, 'sess-1')
    approve_plan(out['plan_id'], ttl_minutes=30)
    seen = []

    def fake_exec(host, command, session_id='', wait=False, agent=None):
        seen.append(command)
        if command == 'df -h':
            return {'status': 'ran', 'output':
                    'Filesystem Size Used Avail Use% Mounted on\n'
                    '/dev/sda1 100G 90G 10G 90% /\n'}
        if command == 'df -i':
            return {'status': 'ran', 'output':
                    'Filesystem Inodes IUsed IFree IUse% Mounted on\n'
                    '/dev/sda1 100 90 10 90% /\n'}
        return {'status': 'ran', 'output': '5G\t/var/log\n'}

    monkeypatch.setattr('core.ssh_exec.ssh_exec', fake_exec)
    res = probe_result(out['plan_id'], 'sess-1')
    assert res['status'] == 'ran'
    assert seen == ['df -h', 'df -i', 'du -x -d 1 /var/log']
    digest = res['digest']
    assert digest['filesystems'][0]['mount'] == '/'
    assert digest['log_dirs'][0]['path'] == '/var/log'
    # A second call returns the stored digest without re-running.
    seen.clear()
    again = probe_result(out['plan_id'], 'sess-1')
    assert seen == [] and again['digest'] == digest


def test_the_dns_record_is_found(gateway, monkeypatch):
    from core.probes import _run_dns
    from db.integrations import create_integration

    create_integration('Pi-hole', 'http://192.0.2.10', 'none', '',
                       kind='pihole')

    def fake_call(integration, tool, args, **kw):
        return {'body': json.dumps({'hosts': [
            {'domain': 'app.example.test', 'ip': '192.0.2.20'}]})}

    monkeypatch.setattr('core.integration_proxy.execute_integration_call',
                        fake_call)
    out = _run_dns({'name': 'app.example.test'})
    assert out['digest']['record'] == {'domain': 'app.example.test',
                                       'ip': '192.0.2.20'}
    assert out['digest']['gateway'] is None


def test_dns_check_reports_no_record(gateway, monkeypatch):
    from core.probes import _run_dns
    from db.integrations import create_integration
    create_integration('Pi-hole', 'http://192.0.2.10', 'none', '',
                       kind='pihole')
    monkeypatch.setattr('core.integration_proxy.execute_integration_call',
                        lambda *a, **k: {'body': '{"hosts": []}'})
    out = _run_dns({'name': 'nope.example.test'})
    assert out['digest']['record'] is None


# ── AG1: probe plans cover approval-mode agents, agent plans never do ──────

def _approval_agent(name='agent-a'):
    from core.agent_context import set_current_agent
    from db.agent_policies import get_agent_policy, set_agent_policy
    from db.agent_tokens import create_agent_token, get_agent_by_name
    create_agent_token(name)
    row = get_agent_by_name(name)
    set_agent_policy(row['id'], ssh_mode='approval')
    set_current_agent({'id': row['id'], 'name': name,
                       'policy': get_agent_policy(row['id'])})
    return name


def _post_command(client, headers, command, sid, agent, force_jit=True):
    import base64
    return client.post('/api/request', json={
        'target_ip': '10.0.0.1',
        'encoded_command': base64.b64encode(command.encode()).decode(),
        'session_id': sid, 'agent': agent, 'force_jit': force_jit,
    }, headers=headers)


def test_a_probe_plan_covers_an_approval_mode_agent(gateway, client,
                                                    gateway_headers):
    from core.probes import plan_probe
    from db.session_plans import approve_plan
    from db.requests import get_request_row
    from db.sessions import upsert_session
    agent = _approval_agent()
    upsert_session('sess-1', agent=agent)
    out = plan_probe('disk_pressure', {'host': gateway}, 'sess-1')
    approve_plan(out['plan_id'], ttl_minutes=30)
    response = _post_command(client, gateway_headers, 'df -h', 'sess-1', agent)
    body = response.json()
    assert body.get('plan') is True, body
    row = get_request_row(body['request_id'])
    assert row['reason'].endswith(':probe')
    # The item is spent: the same command is no longer covered.
    again = _post_command(client, gateway_headers, 'df -h', 'sess-1', agent)
    assert 'plan' not in again.json()


def test_an_agent_plan_does_not_cover_an_approval_mode_agent(
        gateway, client, gateway_headers):
    from db.session_plans import approve_plan, create_plan
    from db.sessions import upsert_session
    agent = _approval_agent()
    upsert_session('sess-2', agent=agent)
    plan = create_plan('sess-2', [{'host': gateway, 'command': 'uptime'}])
    approve_plan(plan['plan_id'], ttl_minutes=30)
    response = _post_command(client, gateway_headers, 'uptime', 'sess-2', agent)
    assert 'plan' not in response.json()


def test_another_agents_session_cannot_spend_a_probe_plan(
        gateway, client, gateway_headers):
    """AG1 condition 3: the claim checks the session's owner."""
    from core.probes import plan_probe
    from db.session_plans import approve_plan
    from db.sessions import upsert_session
    _approval_agent('agent-a')
    upsert_session('sess-a', agent='agent-a')
    out = plan_probe('disk_pressure', {'host': gateway}, 'sess-a')
    approve_plan(out['plan_id'], ttl_minutes=30)
    _approval_agent('agent-b')
    response = _post_command(client, gateway_headers, 'df -h', 'sess-a',
                             'agent-b')
    assert 'plan' not in response.json()


def test_a_proposed_origin_field_cannot_make_a_probe_plan(gateway):
    """AG1 condition 1: origin is server-only; a request field is ignored."""
    from db.session_plans import create_plan, get_plan
    from db.sessions import upsert_session
    upsert_session('sess-3', agent='agent-a')
    created = create_plan('sess-3', [{'host': gateway, 'command': 'uptime',
                                      'origin': 'probe'}])
    assert get_plan(created['plan_id'])['origin'] == 'agent'


def test_a_server_plan_must_name_a_specific_host():
    """AG1 condition 2: an any-host item is an agent-style plan."""
    from db.session_plans import PlanError, create_plan
    from db.sessions import upsert_session
    upsert_session('sess-4', agent='agent-a')
    with pytest.raises(PlanError) as exc:
        create_plan('sess-4', [{'host': '*', 'command': 'uptime'}],
                    origin='probe')
    assert exc.value.code == 'invalid_host'

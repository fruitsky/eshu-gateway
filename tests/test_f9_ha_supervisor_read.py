"""Chunk F9 — freeze covers integrations, and read-only HA Supervisor views.

The Supervisor API has no endpoint allowlist upstream, so `core/ha_supervisor.py`
*is* the allowlist: these tests prove every tool sends exactly its fixed
endpoint, that a slug cannot move it, and that a new endpoint cannot appear
outside the table. The freeze tests prove writes (and their approvals) stop
while reads keep working.
"""

import glob
import json
import os

import pytest

from core.ha_seed import HA_SEED_TOOLS, seed_ha_tools
from core.ha_supervisor import SUPERVISOR_READS
from core.integration_proxy import ProxyError
from core.tool_runner import run_tool
from db.audit import get_audit_log
from db.gateways import clear_trigger_freeze, set_trigger_freeze
from db.integrations import (
    create_integration, create_pending_call, get_integration, get_pending_call,
    get_tools,
)

DASHBOARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'dashboard')


@pytest.fixture
def ha(mock_upstream):
    create_integration('ha', mock_upstream['base_url'], 'bearer', 'tok', kind='ha')
    integration = get_integration('ha')
    seed_ha_tools(integration['id'])
    return integration


def _patch_ws(monkeypatch, result=None, error=None):
    calls = []

    def fake(integration, command, payload=None, **kwargs):
        calls.append({'command': command, 'payload': payload,
                      'tool': kwargs.get('tool_name')})
        if error is not None:
            raise error
        return result

    monkeypatch.setattr('core.ha_supervisor.ha_ws_exec', fake)
    return calls


class TestFreezeCoversIntegrations:

    def test_f9_freeze_refuses_a_mutating_integration_call(self, ha):
        set_trigger_freeze()
        try:
            out = json.loads(run_tool(
                'ha', 'call_service',
                {'domain': 'light', 'service': 'turn_on',
                 'data': {'entity_id': 'light.x'}}))
        finally:
            clear_trigger_freeze()
        assert out['error']['code'] == 'frozen'
        assert out['error']['status_code'] == 409
        assert 'integration writes' in out['error']['message']
        assert [e for e in get_audit_log()
                if e['event_type'] == 'mcp_frozen_refused']

    def test_f9_freeze_queues_nothing(self, ha):
        set_trigger_freeze()
        try:
            run_tool('ha', 'call_service',
                     {'domain': 'light', 'service': 'turn_on'})
        finally:
            clear_trigger_freeze()
        from db.integrations import get_pending_calls
        assert get_pending_calls() == []

    def test_f9_freeze_allows_reads(self, ha, mock_upstream):
        set_trigger_freeze()
        try:
            out = run_tool('ha', 'list_entities', {})
        finally:
            clear_trigger_freeze()
        assert 'frozen' not in out
        assert any(rec['method'] == 'GET' and rec['path'] == '/states'
                   for rec in mock_upstream['requests'])

    def test_f9_freeze_allows_a_handler_dry_run(self, ha, monkeypatch):
        _patch_ws(monkeypatch, result={'title': 'Home', 'views': []})
        set_trigger_freeze()
        try:
            out = run_tool('ha', 'lovelace_save_config',
                           {'url_path': 'water-monitor', 'ops': [],
                            'dry_run': True})
        finally:
            clear_trigger_freeze()
        assert 'frozen' not in out

    def test_f9_freeze_refuses_the_approval(self, ha, auth_client):
        call_id = create_pending_call(
            'ha', 'call_service',
            {'domain': 'light', 'service': 'turn_on'}, 'test', agent='operator')
        set_trigger_freeze()
        try:
            response = auth_client.post(
                f'/api/integration-calls/{call_id}/approve')
        finally:
            clear_trigger_freeze()
        assert response.status_code == 409
        assert 'Emergency Freeze' in response.json()['detail']
        assert get_pending_call(call_id)['status'] == 'pending'
        assert [e for e in get_audit_log()
                if e['event_type'] == 'integration_call_refused_frozen']

    def test_f9_the_call_can_be_approved_after_unfreezing(self, ha, auth_client):
        call_id = create_pending_call(
            'ha', 'call_service',
            {'domain': 'light', 'service': 'turn_off'}, 'test', agent='operator')
        response = auth_client.post(f'/api/integration-calls/{call_id}/approve')
        assert response.status_code == 200
        assert get_pending_call(call_id)['status'] == 'approved'


class TestReadPolicy:
    """§2.6a: one notion of read, and the freeze path uses it."""

    def test_f9_a_curated_write_tool_is_not_a_read(self):
        from core.read_policy import is_read_integration_call
        assert is_read_integration_call({'read_only': False}, 'http', {}) == \
            (False, 'write_tool')

    def test_f9_a_curated_read_tool_is_a_read(self):
        from core.read_policy import is_read_integration_call
        assert is_read_integration_call(
            {'read_only': True}, 'ws',
            {'endpoint': '/addons', 'method': 'get'}) == (True, '')

    def test_f9_a_generic_read_tool_asked_to_write_is_not_a_read(self):
        from core.read_policy import is_read_integration_call
        tool = {'read_only': True, 'generic': True}
        assert is_read_integration_call(tool, 'http', {'method': 'POST'}) == \
            (False, 'not_a_read_method')
        assert is_read_integration_call(tool, 'ws', {'command': 'call_service'}) == \
            (False, 'not_a_read_command')

    def test_f9_freeze_uses_the_read_policy(self, ha, mock_upstream, monkeypatch):
        from core import read_policy
        seen = []
        real = read_policy.is_read_integration_call

        def spy(tool, transport, args):
            seen.append((tool or {}).get('name'))
            return real(tool, transport, args)

        monkeypatch.setattr(read_policy, 'is_read_integration_call', spy)
        set_trigger_freeze()
        try:
            run_tool('ha', 'list_entities', {})
            run_tool('ha', 'call_service', {'domain': 'light', 'service': 'turn_on'})
        finally:
            clear_trigger_freeze()
        assert 'list_entities' in seen and 'call_service' in seen


class TestSupervisorTools:

    @pytest.mark.parametrize('tool,args,endpoint', [
        ('ha_supervisor_info', {}, '/supervisor/info'),
        ('ha_core_info', {}, '/core/info'),
        ('ha_os_info', {}, '/os/info'),
        ('ha_host_info', {}, '/host/info'),
        ('ha_addons', {}, '/addons'),
        ('ha_addon_info', {'slug': 'core_mosquitto'}, '/addons/core_mosquitto/info'),
        ('ha_backups', {}, '/backups'),
        ('ha_resolution', {}, '/resolution/info'),
    ])
    def test_f9_each_read_tool_sends_its_fixed_endpoint(self, ha, monkeypatch,
                                                        tool, args, endpoint):
        calls = _patch_ws(monkeypatch, result={})
        run_tool('ha', tool, args)
        assert len(calls) == 1
        assert calls[0]['command'] == 'supervisor/api'
        assert calls[0]['payload'] == {'endpoint': endpoint, 'method': 'get'}
        assert calls[0]['tool'] == tool

    @pytest.mark.parametrize('slug', ['a/b', '../core_x', 'core_x?x=1', 'Core_X',
                                      '', 'core.x', 'a' * 65])
    def test_f9_a_bad_slug_never_reaches_the_upstream(self, ha, monkeypatch, slug):
        calls = _patch_ws(monkeypatch, result={})
        out = json.loads(run_tool('ha', 'ha_addon_info', {'slug': slug}))
        assert out['error'] == 'ha_supervisor_bad_slug'
        assert calls == []

    def test_f9_addon_info_never_returns_options(self, ha, monkeypatch):
        _patch_ws(monkeypatch, result={
            'slug': 'core_mosquitto', 'name': 'Mosquitto', 'version': '6.5.1',
            'options': {'password': 'hunter2'}, 'ingress': False})
        out = json.loads(run_tool('ha', 'ha_addon_info',
                                  {'slug': 'core_mosquitto'}))
        assert out['slug'] == 'core_mosquitto' and out['version'] == '6.5.1'
        assert 'options' not in out
        assert 'hunter2' not in json.dumps(out)

    def test_f9_addons_are_compact(self, ha, monkeypatch):
        _patch_ws(monkeypatch, result=[
            {'slug': 'core_x', 'name': 'X', 'state': 'started',
             'version': '1', 'options': {'password': 'hunter2'},
             'secrets_note': 'nope'}])
        out = json.loads(run_tool('ha', 'ha_addons', {}))
        assert out[0]['slug'] == 'core_x' and out[0]['state'] == 'started'
        assert 'options' not in out[0] and 'secrets_note' not in out[0]

    def test_f9_a_non_admin_token_is_labelled(self, ha, monkeypatch):
        _patch_ws(monkeypatch, error=ProxyError(403, 'Unauthorized'))
        out = json.loads(run_tool('ha', 'ha_supervisor_info', {}))
        assert out['error'] == 'ha_admin_required'
        assert 'admin' in out['message']
        assert out['status_code'] == 403

    def test_f9_an_upstream_failure_is_reported(self, ha, monkeypatch):
        _patch_ws(monkeypatch, error=ProxyError(502, 'Supervisor unavailable'))
        out = json.loads(run_tool('ha', 'ha_backups', {}))
        assert out['error'] == 'ha_supervisor_error'
        assert out['status_code'] == 502

    def test_f9_whoami_reports_the_token_user(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch, result={
            'name': 'eshu', 'is_admin': True, 'is_owner': False, 'id': 'u1'})
        out = json.loads(run_tool('ha', 'ha_whoami', {}))
        assert out == {'name': 'eshu', 'is_admin': True, 'is_owner': False}
        assert calls[0]['command'] == 'auth/current_user'

    def test_f9_check_config_is_a_read(self, ha, mock_upstream):
        out = run_tool('ha', 'ha_check_config', {})
        assert 'frozen' not in out
        assert any(rec['method'] == 'POST'
                   and rec['path'] == '/config/core/check_config'
                   for rec in mock_upstream['requests'])
        from db.integrations import get_pending_calls
        assert get_pending_calls() == []

    def test_f9_supervisor_output_is_scrubbed(self, ha, monkeypatch):
        _patch_ws(monkeypatch, result={
            'slug': 'core_x', 'name': 'X',
            'description': 'Bearer eyJabcdefgh.abcdefgh.abcdefgh'})
        out = run_tool('ha', 'ha_addon_info', {'slug': 'core_x'})
        assert 'eyJabcdefgh' not in out
        assert '[redacted]' in out


class TestAllowlist:

    def test_f9_the_endpoint_table_is_the_whole_allowlist(self):
        from core.ha_supervisor import endpoint_for
        endpoints = {endpoint_for(name, {'slug': 'core_x'})[0]
                     for name in SUPERVISOR_READS}
        assert endpoints == {
            '/supervisor/info', '/core/info', '/os/info', '/host/info',
            '/addons', '/addons/core_x/info', '/backups', '/resolution/info'}
        seeded = {t['name'] for t in HA_SEED_TOOLS
                  if t.get('handler') == 'ha_supervisor_read'}
        assert seeded == set(SUPERVISOR_READS)

    def test_f9_no_other_module_builds_a_supervisor_payload(self):
        for path in glob.glob(os.path.join(DASHBOARD, '**', '*.py'),
                              recursive=True):
            if os.path.basename(path) == 'ha_supervisor.py':
                continue
            text = open(path, encoding='utf-8').read()
            assert "'supervisor/api'" not in text, path
            assert '"supervisor/api"' not in text, path

    def test_f9_every_supervisor_tool_is_read_only(self):
        for tool in HA_SEED_TOOLS:
            if tool.get('handler') == 'ha_supervisor_read' \
                    or tool.get('handler') == 'ha_whoami':
                assert tool['read_only'] is True, tool['name']
                assert not tool.get('always_gate'), tool['name']

    def test_f9_seeding_is_idempotent_and_adds_new_tools(self):
        create_integration('ha', 'http://192.0.2.50:8123/api', 'bearer', 'tok',
                           kind='ha')
        integration = get_integration('ha')
        created, updated = seed_ha_tools(integration['id'])
        assert created == len(HA_SEED_TOOLS) and updated == 0
        created, updated = seed_ha_tools(integration['id'])
        assert created == 0 and updated == len(HA_SEED_TOOLS)
        names = [t['name'] for t in get_tools(integration['id'])]
        assert len(names) == len(set(names))
        for name in SUPERVISOR_READS:
            assert name in names
        assert 'ha_check_config' in names and 'ha_whoami' in names

    def test_f9_tools_are_read_only_in_the_catalog(self):
        create_integration('ha', 'http://192.0.2.51:8123/api', 'bearer', 'tok',
                           kind='ha')
        integration = get_integration('ha')
        seed_ha_tools(integration['id'])
        rows = {t['name']: t for t in get_tools(integration['id'])}
        for name in list(SUPERVISOR_READS) + ['ha_whoami', 'ha_check_config']:
            assert rows[name]['read_only'] == 1, name

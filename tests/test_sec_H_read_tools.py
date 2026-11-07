"""Chunk H — read-only integration tools must really be read-only.

Two *generic* read tools take the operation from the agent's arguments and
never checked that it reads:

* `read` (generic HTTP) is documented as GET/HEAD, but the runner forwarded
  `args['method']` as-is, so a `read` call with POST/PUT/PATCH/DELETE reached
  the upstream with no approval.
* `ws_read` (generic Home Assistant WebSocket) forwarded any command type, so
  `call_service`, registry `update`/`remove` and `supervisor/*` ran un-gated.

Every test here is a regression test: each failed on `security-hardening`
before the fix (H1/H2 reproduce the attack; H4 is the catalog invariant that
stops the next tool from reintroducing it).
"""

import http.server
import json
import threading
from urllib.parse import urlparse

import pytest

from db.audit import get_audit_log
from db.integrations import (
    create_integration,
    get_integration,
    get_integration_calls,
    get_tools,
)
from core.generic_tools import seed_generic_tools
from core.integration_proxy import ProxyError, execute_generic_call
from core.tool_runner import run_tool


def _seed(name, url, kind='custom'):
    create_integration(name, url, "none", "", kind=kind)
    integration = get_integration(name)
    seed_generic_tools(integration['id'], kind)
    return integration


@pytest.fixture
def head_upstream():
    """Upstream that answers GET and HEAD, recording the method used."""
    state = {'requests': []}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self):
            state['requests'].append((self.command, urlparse(self.path).path))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '2')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(b'{}')

        def do_GET(self):
            self._handle()

        def do_HEAD(self):
            self._handle()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}", 'state': state}
    server.shutdown()
    server.server_close()


def _last_call():
    calls = [c for c in get_integration_calls()["rows"]]
    return calls[0] if calls else None


class TestH1GenericReadMethods:
    """H1 — the generic HTTP `read` tool is GET/HEAD only."""

    def test_sec_H_read_tool_refuses_mutating_methods(self, mock_upstream):
        _seed("api", mock_upstream["base_url"])
        for method in ('POST', 'PUT', 'PATCH', 'DELETE',
                       'post', 'put', 'patch', 'delete'):
            before = len(mock_upstream["requests"])
            out = json.loads(run_tool("api", "read", {
                "path": "/states", "method": method,
                "data": {"payload-secret": "do-not-log"},
            }))
            assert out.get('error') == 'not_a_read_method', method
            assert out.get('status_code') == 400
            assert out.get('tool') == 'write'
            # The upstream must never see the request.
            assert len(mock_upstream["requests"]) == before, method

    def test_sec_H_read_tool_refusal_is_audited(self, mock_upstream):
        _seed("api", mock_upstream["base_url"])
        json.loads(run_tool("api", "read", {
            "path": "/states", "method": "DELETE",
            "data": {"payload-secret": "do-not-log"},
        }))
        call = _last_call()
        assert call['outcome'] == 'refused'
        assert call['method'] == 'DELETE'
        assert call['reason_code'] == 'not_a_read_method'
        assert 'DELETE' in call['request_summary']
        # Payload values are never stored on a refusal.
        assert 'payload-secret' not in call['request_summary']
        assert call['request_summary'] == '{"method":"DELETE"}'
        events = [a for a in get_audit_log() if a['event_type'] == 'read_tool_refused']
        assert events, 'expected a read_tool_refused audit event'
        assert 'DELETE' in events[0]['details']

    def test_sec_H_read_tool_still_runs_get_and_head(self, head_upstream):
        _seed("api", head_upstream["base_url"])
        run_tool("api", "read", {"path": "/states"})
        assert head_upstream["state"]["requests"][-1] == ("GET", "/states")
        run_tool("api", "read", {"path": "/states", "method": "HEAD"})
        assert head_upstream["state"]["requests"][-1] == ("HEAD", "/states")

    def test_sec_H_execute_generic_call_read_only_guard(self, mock_upstream):
        """Defence in depth: the executor itself refuses a non-read method."""
        _seed("api", mock_upstream["base_url"])
        integration = get_integration("api")
        before = len(mock_upstream["requests"])
        with pytest.raises(ProxyError) as excinfo:
            execute_generic_call(integration, "DELETE", "/things/1",
                                 agent="test", tool_name="read", read_only=True)
        assert excinfo.value.status_code == 400
        assert 'not_a_read_method' in excinfo.value.message
        assert len(mock_upstream["requests"]) == before
        # The write path keeps working when it is explicit about being a write.
        res = execute_generic_call(integration, "DELETE", "/things/1",
                                   agent="test", tool_name="write", read_only=False)
        assert res["status_code"] == 200


class TestH2WsReadCommands:
    """H2 — the generic HA `ws_read` tool only runs reviewed read commands."""

    ALLOWED = [
        'automation/config',
        'recorder/statistics_during_period',
        'persistent_notification/get',
        'system_log/list',
        'config/area_registry/list',
        'config/entity_registry/get',
        'config_entity_registry/list',
        'config_entries/get_single',
        'media_source/browse_media',
        'render_template',
        'zha/devices',
        'get_states',
        'get_config',
        'get_services',
    ]

    REFUSED = [
        'call_service',
        'supervisor/api',
        'execute_script',
        'config/entity_registry/update',
        'config/device_registry/remove',
        'input_boolean/create',
        'backup/generate',
        'auth/delete_refresh_token',
        'subscribe_events',
        'unknown/command',
        '',
    ]

    def test_sec_H_ws_read_command_allowlist(self):
        from core.ha_ws_policy import is_read_command
        for command in self.ALLOWED:
            assert is_read_command(command) is True, command
        for command in self.REFUSED:
            assert is_read_command(command) is False, command

    def test_sec_H_ws_read_refuses_before_connecting(self, monkeypatch):
        import core.ha_ws as ha_ws
        _seed("ha", "http://192.0.2.10:8123/api", kind='ha')
        monkeypatch.setattr(
            ha_ws, "ha_ws_request",
            lambda *args, **kwargs: pytest.fail("opened a WebSocket for a refused command"))
        for command in ('call_service', 'supervisor/api',
                        'config/device_registry/remove', ''):
            out = json.loads(run_tool("ha", "ws_read", {
                "command": command,
                "payload": {"entity_id": "light.payload-secret"},
            }))
            assert out.get('error') == 'not_a_read_command', command
            assert out.get('status_code') == 400
            assert out.get('tool') == 'ws_write'

    def test_sec_H_ws_read_refusal_is_audited(self, monkeypatch):
        import core.ha_ws as ha_ws
        _seed("ha", "http://192.0.2.10:8123/api", kind='ha')
        monkeypatch.setattr(ha_ws, "ha_ws_request",
                            lambda *args, **kwargs: pytest.fail("opened a WebSocket"))
        json.loads(run_tool("ha", "ws_read", {
            "command": "config/device_registry/remove",
            "payload": {"device_id": "payload-secret"},
        }))
        call = _last_call()
        assert call['outcome'] == 'refused'
        assert call['method'] == 'WS'
        assert call['path'] == 'config/device_registry/remove'
        assert call['reason_code'] == 'not_a_read_command'
        assert 'payload-secret' not in call['request_summary']
        events = [a for a in get_audit_log() if a['event_type'] == 'read_tool_refused']
        assert events, 'expected a read_tool_refused audit event'

    def test_sec_H_ws_read_still_runs_a_read_command(self, monkeypatch):
        import core.ha_ws as ha_ws
        _seed("ha", "http://192.0.2.10:8123/api", kind='ha')
        seen = {}

        def _fake(integration, command, payload):
            seen['command'] = command
            return [{"entity_id": "sensor.x"}]

        monkeypatch.setattr(ha_ws, "ha_ws_request", _fake)
        out = json.loads(run_tool("ha", "ws_read", {"command": "config/entity_registry/list"}))
        assert seen['command'] == 'config/entity_registry/list'
        assert out == [{"entity_id": "sensor.x"}]


class TestH4CatalogInvariant:
    """H4 — no seeded read-only tool may be a write in disguise."""

    # Read-by-POST endpoints that really only read. Each entry is reviewed in
    # chunk H; adding one is a security change, not a formality.
    READ_BY_POST_ALLOWLIST = {
        # Omada's controller API has no GET list endpoints: the client lists are
        # POSTed, but the calls only read (no body beyond the pagination/query).
        ('omada', 'list_site_clients'),
        ('omada', 'list_known_clients'),
    }

    def test_sec_H_catalog_read_only_tools_are_reads(self):
        from core import seeds
        from core.generic_tools import generic_tools_for

        entries = []
        for kind, catalog in sorted(seeds._CATALOGS.items()):
            entries.extend((kind, tool) for tool in catalog)
        for kind in sorted(seeds._CATALOGS):
            entries.extend((kind, tool) for tool in generic_tools_for(kind))
        violations = self.violations(entries)
        assert violations == [], '\n'.join(violations)
        # Sanity: the catalogs really were walked.
        assert len([e for e in entries if e[1].get('read_only')]) >= 50

    def test_sec_H_catalog_invariant_catches_a_bad_tool(self):
        bad = [
            ('ha', {'name': 'sneaky_read', 'read_only': True, 'method': 'DELETE',
                    'transport': 'http', 'path_template': '/states'}),
            ('ha', {'name': 'sneaky_ws', 'read_only': True, 'method': 'GET',
                    'transport': 'ws', 'path_template': 'call_service'}),
        ]
        violations = self.violations(bad)
        assert len(violations) == 2
        assert 'sneaky_read' in violations[0]
        assert 'sneaky_ws' in violations[1]
        # `not_implemented` tools are exempt (their method is a placeholder).
        exempt = [('pulse', {'name': 'plan_action', 'read_only': True,
                             'method': 'POST', 'not_implemented': True})]
        assert self.violations(exempt) == []
        # The reviewed read-by-POST allowlist is honoured.
        allowed = [('omada', {'name': 'list_site_clients', 'read_only': True,
                              'method': 'POST', 'transport': 'http'})]
        assert self.violations(allowed) == []

    def violations(self, entries):
        from core.ha_ws_policy import is_read_command
        problems = []
        for kind, tool in entries:
            if not tool.get('read_only') or tool.get('not_implemented'):
                continue
            method = (tool.get('method') or 'GET').upper()
            if method not in ('GET', 'HEAD'):
                if (kind, tool.get('name')) not in self.READ_BY_POST_ALLOWLIST:
                    problems.append(
                        '%s.%s is read_only but uses %s' % (kind, tool.get('name'), method))
            if tool.get('transport') == 'ws' and tool.get('path_template'):
                if not is_read_command(tool['path_template']):
                    problems.append(
                        '%s.%s is a read-only WS tool but %r is not a read command'
                        % (kind, tool.get('name'), tool['path_template']))
        return problems

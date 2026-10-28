"""Mediated web fetch: policy gate, SSRF, redirects, extraction, audit, limits."""
import asyncio
import gzip
import http.server
import json
import socket
import threading
import time

import pytest

from core.agent_context import reset_current_agent, set_current_agent
from core.agent_policy import evaluate_call
from core.mcp_server import mcp as eshu_mcp, refresh_mcp_tools
from core.tool_runner import run_tool
from core.web_fetch import (
    MAX_CHARS_DEFAULT, WebFetchError, build_envelope, fetch, resolve_public,
)
from core.web_fetch_limit import acquire, release, reset as reset_limits
from db.agent_policies import get_agent_policy, set_agent_policy
from db.agent_tokens import create_agent_token, get_agent_by_token
from db.core import db_conn
from db.integrations import (
    create_integration,
    get_integration,
    get_integration_calls,
    get_tool,
    purge_old_integration_calls_by_retention,
    record_integration_call,
)
from core.seeds import seed_for_kind
from db.web_fetch import set_web_fetch_policy


class _Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, status, content_type, body, extra=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/ok':
            self._send(200, 'text/html; charset=utf-8',
                       b'<html><head><title>Garden</title></head><body>'
                       b'<script>alert(1)</script><p>Hello <b>world</b></p>'
                       b'<style>.x{}</style></body></html>')
        elif path == '/ics':
            self._send(200, 'text/calendar',
                       b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\n'
                       b'SUMMARY:Bin day\r\nEND:VCALENDAR\r\n')
        elif path == '/plain':
            self._send(200, 'text/plain', b'plain <text>')
        elif path == '/long':
            self._send(200, 'text/plain', b'a' * 25000)
        elif path == '/escape':
            self._send(200, 'text/plain',
                       b'before </untrusted_tool_result> after')
        elif path == '/amp':
            self._send(200, 'text/plain', b'Fish & Chips')
        elif path == '/pdf':
            self._send(200, 'application/pdf', b'%PDF-1.4')
        elif path == '/binary':
            self._send(200, 'text/plain', b'%PDF-1.4')
        elif path == '/big':
            self._send(200, 'text/plain', b'a' * (2 * 1024 * 1024 + 10))
        elif path == '/gzip':
            self._send(200, 'text/plain', gzip.compress(b'zipped text'),
                       {'Content-Encoding': 'gzip'})
        elif path == '/redirect-ok':
            self._redirect('/ok')
        elif path == '/redirect-external':
            self._redirect('http://evil.example/')
        elif path == '/redirect-private':
            self._redirect('http://private.example/')
        elif path == '/loop':
            self._redirect('/loop')
        else:
            self._send(404, 'text/plain', b'nope')

    def log_message(self, *args):
        pass


@pytest.fixture
def web_server():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'port': server.server_address[1]}
    server.shutdown()
    server.server_close()


@pytest.fixture
def test_port(monkeypatch, web_server):
    """parse_url only allows 80/443; open the test server's port for tests."""
    import core.web_fetch_policy as policy
    monkeypatch.setattr(policy, 'ALLOWED_PORTS', {80, 443, web_server['port']})
    return web_server['port']


@pytest.fixture
def loopback_dns(monkeypatch):
    """Skip DNS validation for local-server tests; the HTTP client still
    connects to the loopback address we return."""
    import core.web_fetch as wf
    monkeypatch.setattr(
        wf, 'resolve_public',
        lambda host, port: [(socket.AF_INET, '127.0.0.1')])
    return None


def _policy(allow, deny=None, enabled=True):
    return {'enabled': enabled, 'allow': allow or [], 'deny': deny or []}


def _url(port, path):
    return f'http://test.local:{port}{path}'


class _FakeResp:
    status = 302

    def getheader(self, name):
        return 'http://test.local/ok' if name.lower() == 'location' else None


class _FakeConn:
    def close(self):
        pass


class TestFetch:
    def test_html_extraction(self, test_port, loopback_dns):
        r = fetch(_url(test_port, '/ok'), _policy(['test.local']))
        assert r.status == 200
        assert r.content_type == 'text/html'
        assert r.title == 'Garden'
        assert 'Hello world' in r.text
        assert 'alert(1)' not in r.text
        assert '.x{}' not in r.text
        assert r.resolved_ips == ['127.0.0.1']
        assert r.truncated is False

    def test_only_first_title_is_used(self):
        from core.web_fetch import _html_to_text
        text, title = _html_to_text(
            '<html><head><title>Page</title></head><body>'
            '<svg><title>Logo</title></svg><p>Body</p></body></html>')
        assert title == 'Page'
        assert 'Body' in text

    def test_truncation_is_fixed_at_20k(self, test_port, loopback_dns):
        r = fetch(_url(test_port, '/long'), _policy(['test.local']))
        assert len(r.text) == MAX_CHARS_DEFAULT
        assert r.truncated is True

    def test_ics_is_raw_text(self, test_port, loopback_dns):
        r = fetch(_url(test_port, '/ics'), _policy(['test.local']))
        assert r.content_type == 'text/calendar'
        assert 'SUMMARY:Bin day' in r.text

    def test_gzip_identity_request_still_decodes(self, test_port, loopback_dns):
        r = fetch(_url(test_port, '/gzip'), _policy(['test.local']))
        assert 'zipped text' in r.text

    def test_envelope_escapes_closing_tag(self, test_port, loopback_dns):
        r = fetch(_url(test_port, '/escape'), _policy(['test.local']))
        out = build_envelope(r)
        assert out.count('</untrusted_tool_result>') == 1
        assert '&lt;/untrusted_tool_result&gt;' in out

    def test_envelope_uses_hermes_native_underscore_tag(self):
        from core.web_fetch import FetchResult
        r = FetchResult('https://x/', 'https://x/', 200, 'text/plain', 'body',
                        '', False, 4, [], '2026-10-28T00:00:00Z')
        out = build_envelope(r)
        assert out.startswith('<untrusted_tool_result ')
        assert out.endswith('</untrusted_tool_result>')
        assert '<untrusted-tool-result' not in out

    def test_envelope_keeps_ampersands(self, test_port, loopback_dns):
        r = fetch(_url(test_port, '/amp'), _policy(['test.local']))
        out = build_envelope(r)
        assert 'Fish & Chips' in out
        assert '&amp;' not in out

    def test_unsupported_content_type(self, test_port, loopback_dns):
        with pytest.raises(WebFetchError) as exc:
            fetch(_url(test_port, '/pdf'), _policy(['test.local']))
        assert exc.value.code == 'unsupported_content_type'

    def test_binary_sniff(self, test_port, loopback_dns):
        with pytest.raises(WebFetchError) as exc:
            fetch(_url(test_port, '/binary'), _policy(['test.local']))
        assert exc.value.code == 'binary_content'

    def test_size_cap(self, test_port, loopback_dns):
        with pytest.raises(WebFetchError) as exc:
            fetch(_url(test_port, '/big'), _policy(['test.local']))
        assert exc.value.code == 'size_cap'

    def test_upstream_error(self, test_port, loopback_dns):
        with pytest.raises(WebFetchError) as exc:
            fetch(_url(test_port, '/missing'), _policy(['test.local']))
        assert exc.value.code == 'upstream_error'
        assert exc.value.status == 404

    def test_redirect_same_host(self, test_port, loopback_dns):
        r = fetch(_url(test_port, '/redirect-ok'), _policy(['test.local']))
        assert r.final_url.endswith('/ok')
        assert 'Hello world' in r.text

    def test_redirect_to_non_allowlisted_host(self, test_port, loopback_dns):
        with pytest.raises(WebFetchError) as exc:
            fetch(_url(test_port, '/redirect-external'), _policy(['test.local']))
        assert exc.value.code == 'redirect_not_allowed'

    def test_redirect_downgrade(self, test_port, monkeypatch):
        import core.web_fetch as wf
        monkeypatch.setattr(
            wf, '_open',
            lambda parsed, deadline: (_FakeResp(), _FakeConn(), '127.0.0.1'))
        with pytest.raises(WebFetchError) as exc:
            fetch(f'https://test.local:{test_port}/start', _policy(['test.local']))
        assert exc.value.code == 'redirect_not_allowed'

    def test_redirect_to_allowlisted_host_that_resolves_private(
            self, test_port, monkeypatch):
        """The redirect target passes the allowlist but must still pass the
        hop-level SSRF resolver check."""
        import core.web_fetch as wf

        real_resolve = wf.resolve_public
        real_getaddrinfo = socket.getaddrinfo

        def fake_resolve(host, port):
            if host == 'test.local':
                # Bypass validation only for the local test server; the
                # redirect target below goes through the real resolver.
                return [(socket.AF_INET, '127.0.0.1')]
            return real_resolve(host, port)

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == 'private.example':
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '',
                         ('127.0.0.1', port))]
            if host == '127.0.0.1':
                return real_getaddrinfo(host, port, *args, **kwargs)
            raise AssertionError('unexpected DNS for ' + host)

        monkeypatch.setattr(wf, 'resolve_public', fake_resolve)
        monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)
        with pytest.raises(WebFetchError) as exc:
            fetch(_url(test_port, '/redirect-private'),
                  _policy(['test.local', 'private.example']))
        assert exc.value.code == 'private_address'

    def test_too_many_redirects(self, test_port, loopback_dns):
        with pytest.raises(WebFetchError) as exc:
            fetch(_url(test_port, '/loop'), _policy(['test.local']))
        assert exc.value.code == 'too_many_redirects'


class TestPolicyBeforeDns:
    def test_denied_host_never_resolves(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError('DNS must not be called for a denied host')
        monkeypatch.setattr(socket, 'getaddrinfo', boom)
        with pytest.raises(WebFetchError) as exc:
            fetch('http://denied.example/', _policy(['allowed.example']))
        assert exc.value.code == 'host_not_allowed'

    def test_deny_beats_allow(self, test_port):
        with pytest.raises(WebFetchError) as exc:
            fetch(_url(test_port, '/ok'),
                  _policy(['test.local'], ['test.local']))
        assert exc.value.code == 'host_denied'


class TestResolvePublic:
    @pytest.mark.parametrize('ip', [
        '10.0.0.5', '127.0.0.1', '169.254.169.254', '192.168.1.1',
        '100.64.0.1', '0.0.0.0', '::1', '::ffff:127.0.0.1', 'fe80::1',
    ])
    def test_private_addresses_rejected(self, monkeypatch, ip):
        family = socket.AF_INET6 if ':' in ip else socket.AF_INET
        sockaddr = (ip, 80, 0, 0) if family == socket.AF_INET6 else (ip, 80)
        monkeypatch.setattr(
            socket, 'getaddrinfo',
            lambda *a, **k: [(family, socket.SOCK_STREAM, 6, '', sockaddr)])
        with pytest.raises(WebFetchError) as exc:
            resolve_public('test.local', 80)
        assert exc.value.code == 'private_address'

    def test_public_address_accepted(self, monkeypatch):
        monkeypatch.setattr(
            socket, 'getaddrinfo',
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '',
                              ('93.184.216.34', 80))])
        assert resolve_public('example.com', 80) == [
            (socket.AF_INET, '93.184.216.34')]

    def test_mixed_answer_rejected(self, monkeypatch):
        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', 80)),
        ]
        monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: infos)
        with pytest.raises(WebFetchError) as exc:
            resolve_public('example.com', 80)
        assert exc.value.code == 'private_address'


def _web_agent(principal='household', allow=None, deny=None, enabled=True):
    create_integration('web', 'https://invalid.local', 'none', '', kind='webfetch',
                       enabled=True, retention_days=90)
    integration = get_integration('web')
    seed_for_kind(integration)
    set_web_fetch_policy(principal, enabled=enabled,
                         allow=allow or [], deny=deny or [])
    raw, agent_id = create_agent_token(principal)
    agent = get_agent_by_token(raw)
    agent['policy'] = get_agent_policy(agent_id)
    refresh_mcp_tools()
    return agent


class TestPrincipalGate:
    def test_tool_round_trips_principal_allow(self):
        create_integration('web', 'https://invalid.local', 'none', '',
                           kind='webfetch', enabled=True)
        integration = get_integration('web')
        seed_for_kind(integration)
        tool = get_tool('web', 'fetch_url')
        assert tool['principal_allow'] == ['household']
        assert [p['name'] for p in tool['params']] == ['url']

    def test_admin_cannot_see_or_call(self, test_port, loopback_dns):
        _web_agent(allow=['test.local'])
        raw, admin_id = create_agent_token('hermes-admin')
        admin = get_agent_by_token(raw)
        admin['policy'] = get_agent_policy(admin_id)

        with _acting_as(admin):
            names = {t.name for t in asyncio.run(eshu_mcp.list_tools())}
            assert 'web_fetch_url' not in names
            res = asyncio.run(eshu_mcp.call_tool(
                'web_fetch_url', {'url': _url(test_port, '/ok')}))
        assert _text(res)['error'] == 'forbidden'

    def test_household_sees_and_calls(self, test_port, loopback_dns):
        agent = _web_agent(allow=['test.local'])
        with _acting_as(agent):
            names = {t.name for t in asyncio.run(eshu_mcp.list_tools())}
            assert 'web_fetch_url' in names
            res = asyncio.run(eshu_mcp.call_tool(
                'web_fetch_url', {'url': _url(test_port, '/ok')}))
        assert '<untrusted_tool_result' in _text(res)['text']

    def test_tool_description_includes_live_allowlist(self):
        agent = _web_agent(allow=['test.local', 'gov.uk'])
        with _acting_as(agent):
            tools = {t.name: t for t in asyncio.run(eshu_mcp.list_tools())}
        desc = tools['web_fetch_url'].description
        assert 'Allowed domains for this agent:' in desc
        assert 'test.local' in desc and 'gov.uk' in desc
        assert 'any other host is refused' in desc.lower()

    def test_tool_description_empty_allowlist(self):
        agent = _web_agent(allow=[])
        with _acting_as(agent):
            tools = {t.name: t for t in asyncio.run(eshu_mcp.list_tools())}
        assert 'none — every fetch will be refused' in tools['web_fetch_url'].description

    def test_tool_hidden_when_policy_disabled(self):
        agent = _web_agent(allow=['test.local'], enabled=False)
        with _acting_as(agent):
            names = {t.name for t in asyncio.run(eshu_mcp.list_tools())}
        assert 'web_fetch_url' not in names


class TestHandler:
    def test_success_audit_and_session_grouping(self, test_port, loopback_dns):
        agent = _web_agent(allow=['test.local'])
        with _acting_as(agent):
            out = run_tool('web', 'fetch_url', {
                'url': _url(test_port, '/ok'), 'session_id': 'sess-1'})
        assert out.startswith('<untrusted_tool_result')
        row = get_integration_calls(limit=1)['rows'][0]
        assert row['integration'] == 'web'
        assert row['tool'] == 'fetch_url'
        assert row['agent'] == 'household'
        assert row['session_id'] == 'sess-1'
        assert row['reason_code'] == 'ok'
        assert row['host'] == 'test.local'
        assert row['resolved_ips'] == '127.0.0.1'
        assert row['outcome'] == 'ok'
        assert row['response_bytes'] > 0
        assert 'url' not in row['request_summary']

    def test_legacy_max_chars_argument_is_ignored(self, test_port, loopback_dns):
        agent = _web_agent(allow=['test.local'])
        with _acting_as(agent):
            out = run_tool('web', 'fetch_url', {
                'url': _url(test_port, '/long'), 'max_chars': 600})
        assert 'truncated: true' in out
        row = get_integration_calls(limit=1)['rows'][0]
        summary = json.loads(row['request_summary'])
        assert summary['max_chars'] == MAX_CHARS_DEFAULT
        assert summary['requested_max_chars'] == 600
        assert row['truncated'] == 1
        assert row['response_bytes'] == 25000

    def test_policy_denial_audited_without_dns(self, test_port, monkeypatch):
        agent = _web_agent(allow=['allowed.example'])

        def boom(*args, **kwargs):
            raise AssertionError('DNS must not be called')
        monkeypatch.setattr(socket, 'getaddrinfo', boom)

        with _acting_as(agent):
            out = json.loads(run_tool('web', 'fetch_url', {
                'url': _url(test_port, '/ok')}))
        assert out['error'] == 'forbidden'
        assert out['code'] == 'host_not_allowed'
        assert out['allowed_domains'] == ['allowed.example']
        row = get_integration_calls(limit=1)['rows'][0]
        assert row['outcome'] == 'denied'
        assert row['reason_code'] == 'host_not_allowed'
        assert row['resolved_ips'] == ''

    def test_host_denied_refusal_includes_allowlist(self, test_port):
        agent = _web_agent(allow=['test.local'], deny=['test.local'])
        with _acting_as(agent):
            out = json.loads(run_tool('web', 'fetch_url', {
                'url': _url(test_port, '/ok')}))
        assert out['code'] == 'host_denied'
        assert out['allowed_domains'] == ['test.local']

    def test_redirect_refusal_includes_allowlist(self, test_port, loopback_dns):
        agent = _web_agent(allow=['test.local'])
        with _acting_as(agent):
            out = json.loads(run_tool('web', 'fetch_url', {
                'url': _url(test_port, '/redirect-external')}))
        assert out['code'] == 'redirect_not_allowed'
        assert out['allowed_domains'] == ['test.local']

    @pytest.mark.parametrize('enabled,allow,code', [
        (True, [], 'host_not_allowed'),
        (False, ['test.local'], 'disabled'),
    ])
    def test_fail_closed(self, test_port, enabled, allow, code):
        agent = _web_agent(allow=allow, enabled=enabled)
        with _acting_as(agent):
            out = json.loads(run_tool('web', 'fetch_url', {
                'url': _url(test_port, '/ok')}))
        assert out['error'] == 'forbidden'
        assert out['code'] == code
        assert out['allowed_domains'] == []
        if not enabled:
            assert out['enabled'] is False

    def test_rate_limit(self, test_port, loopback_dns):
        agent = _web_agent(allow=['test.local'])
        with _acting_as(agent):
            for _ in range(20):
                out = run_tool('web', 'fetch_url', {
                    'url': _url(test_port, '/plain')})
                assert out.startswith('<untrusted_tool_result')
            out = json.loads(run_tool('web', 'fetch_url', {
                'url': _url(test_port, '/plain')}))
        assert out['code'] == 'rate_limited'
        assert out['retry_after'] >= 1

    def test_principal_gate_in_evaluate_call(self):
        create_integration('web', 'https://invalid.local', 'none', '',
                           kind='webfetch', enabled=True)
        integration = get_integration('web')
        seed_for_kind(integration)
        tool = get_tool('web', 'fetch_url')
        allowed, reason = evaluate_call(
            {'name': 'hermes-admin', 'policy': {'tools_mode': 'all'}},
            'web', 'fetch_url', {'url': 'https://example.com/'},
            tool=tool, integration=integration)
        assert allowed is False
        assert 'principal' in reason
        # An unidentified caller also fails closed.
        allowed, _ = evaluate_call(
            None, 'web', 'fetch_url', {'url': 'https://example.com/'},
            tool=tool, integration=integration)
        assert allowed is False


class TestLimiter:
    def test_concurrency_cap(self):
        reset_limits()
        held = []
        for _ in range(3):
            sem, err = acquire('household')
            assert err is None
            held.append(sem)
        sem, err = acquire('household')
        assert sem is None
        assert err.code == 'concurrency_limited'
        for sem in held:
            release('household', sem)

    def test_minute_limit(self):
        reset_limits()
        for _ in range(20):
            sem, err = acquire('household')
            assert err is None
            release('household', sem)
        sem, err = acquire('household')
        assert sem is None
        assert err.code == 'rate_limited'
        assert err.retry_after >= 1


class TestRetention:
    def test_web_fetch_rows_keep_90_days(self):
        create_integration('web', 'https://invalid.local', 'none', '',
                           kind='webfetch', enabled=True, retention_days=90)
        create_integration('other', 'https://invalid.local', 'none', '',
                           kind='custom', enabled=True, retention_days=30)
        for name in ('web', 'other'):
            record_integration_call(
                integration=name, tool='x', agent='a', method='GET', path='/',
                status_code=200, latency_ms=1, response_summary='',
                response_bytes=0, truncated=0)
        old = int(time.time()) - 60 * 86400
        with db_conn() as conn:
            conn.execute('UPDATE integration_calls SET created_at = ?', (old,))
            conn.commit()

        removed = purge_old_integration_calls_by_retention(int(time.time()))
        assert removed == 1
        names = {r['integration'] for r in get_integration_calls(limit=10)['rows']}
        assert 'web' in names
        assert 'other' not in names


class TestStats:
    def test_counters_from_audit_rows(self):
        from db.web_fetch import get_web_fetch_stats
        record_integration_call(
            integration='web', tool='fetch_url', agent='household', method='GET',
            path='test.local/ok', status_code=200, latency_ms=1,
            response_summary='', response_bytes=10, truncated=0,
            outcome='ok', reason_code='ok')
        record_integration_call(
            integration='web', tool='fetch_url', agent='household', method='GET',
            path='', status_code=0, latency_ms=0,
            response_summary='denied', response_bytes=0, truncated=0,
            outcome='denied', reason_code='host_not_allowed')
        stats = get_web_fetch_stats('household')
        assert stats['total'] == 2
        assert stats['counters']['fetch_ok'] == 1
        assert stats['counters']['fetch_denied_allowlist'] == 1
        assert stats['by_code']['host_not_allowed'] == 1


class TestAccessSync:
    def _setup(self):
        create_integration('web', 'https://invalid.local', 'none', '',
                           kind='webfetch', enabled=True, retention_days=90)
        seed_for_kind(get_integration('web'))
        _raw, agent_id = create_agent_token('rowana-household')
        set_agent_policy(agent_id, tools_mode='allowlist',
                         tools=['home_assistant_list_entities'],
                         arg_policy={}, approval={}, rate_limit_per_min=20)
        refresh_mcp_tools()
        return agent_id

    def test_policy_save_grants_tool_and_tool_allowlist(self, auth_client):
        agent_id = self._setup()
        r = auth_client.put('/api/web-fetch-policies/rowana-household',
                            json={'enabled': True, 'allow': ['gov.uk']})
        assert r.status_code == 200, r.text
        assert get_tool('web', 'fetch_url')['principal_allow'] == ['rowana-household']
        policy = get_agent_policy(agent_id)
        assert 'web_fetch_url' in policy['tools']
        assert policy['rate_limit_per_min'] == 20

    def test_policy_disable_and_delete_revoke(self, auth_client):
        agent_id = self._setup()
        auth_client.put('/api/web-fetch-policies/rowana-household',
                        json={'enabled': True, 'allow': ['gov.uk']})
        r = auth_client.put('/api/web-fetch-policies/rowana-household',
                            json={'enabled': False, 'allow': ['gov.uk']})
        assert r.status_code == 200, r.text
        assert get_tool('web', 'fetch_url')['principal_allow'] == ['household']
        assert 'web_fetch_url' not in get_agent_policy(agent_id)['tools']

        auth_client.put('/api/web-fetch-policies/rowana-household',
                        json={'enabled': True, 'allow': ['gov.uk']})
        assert auth_client.delete('/api/web-fetch-policies/rowana-household').status_code == 200
        assert get_tool('web', 'fetch_url')['principal_allow'] == ['household']
        assert 'web_fetch_url' not in get_agent_policy(agent_id)['tools']

    def test_seed_after_policy_uses_enabled_callers(self, auth_client):
        create_integration('web', 'https://invalid.local', 'none', '',
                           kind='webfetch', enabled=True, retention_days=90)
        _raw, agent_id = create_agent_token('rowana-household')
        set_agent_policy(agent_id, tools_mode='allowlist',
                         tools=['home_assistant_list_entities'],
                         arg_policy={}, approval={}, rate_limit_per_min=20)
        r = auth_client.put('/api/web-fetch-policies/rowana-household',
                            json={'enabled': True, 'allow': ['gov.uk']})
        assert r.status_code == 200, r.text
        # The policy exists but the tool does not yet; the sync still adds the
        # tool key to the agent's allowlist.
        assert 'web_fetch_url' in get_agent_policy(agent_id)['tools']
        seed_for_kind(get_integration('web'))
        assert get_tool('web', 'fetch_url')['principal_allow'] == ['rowana-household']


class TestPolicyApi:
    def test_round_trip_and_validation(self, auth_client):
        r = auth_client.put('/api/web-fetch-policies/household', json={
            'enabled': True,
            'allow': ['Bromley.gov.uk.', 'gov.uk'],
            'deny': ['Reddit.com'],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['allow'] == ['bromley.gov.uk', 'gov.uk']
        assert body['deny'] == ['reddit.com']
        assert body['enabled'] is True

        assert auth_client.get('/api/web-fetch-policies/household').status_code == 200
        listed = auth_client.get('/api/web-fetch-policies').json()['policies']
        assert any(p['principal'] == 'household' for p in listed)

        bad = auth_client.put('/api/web-fetch-policies/household',
                              json={'enabled': True, 'allow': ['*.gov.uk']})
        assert bad.status_code == 400

        assert auth_client.delete('/api/web-fetch-policies/household').status_code == 200
        assert auth_client.get('/api/web-fetch-policies/household').status_code == 404

    def test_stats_endpoint_empty(self, auth_client):
        r = auth_client.get('/api/web-fetch-stats')
        assert r.status_code == 200
        body = r.json()
        assert body['total'] == 0
        assert body['counters']['fetch_ok'] == 0


def _text(result):
    """Normalise FastMCP's content-block / dict return into a dict."""
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)):
        text = getattr(result[0], 'text', '')
        try:
            return {'text': text, **json.loads(text)}
        except (TypeError, ValueError):
            return {'text': text}
    return {'text': str(result)}


class _acting_as:
    def __init__(self, agent):
        self.agent = agent
        self.token = None

    def __enter__(self):
        self.token = set_current_agent(self.agent)
        return self.agent

    def __exit__(self, *exc):
        reset_current_agent(self.token)

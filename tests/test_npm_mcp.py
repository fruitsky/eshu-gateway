import http.server
import json
import threading

import pytest

from db.integrations import create_integration, get_integration, get_tools
from core.seeds import seed_for_kind
from core.tool_runner import run_tool


@pytest.fixture(autouse=True)
def _clear_session_cache():
    from core import session_auth
    session_auth._sessions.clear()
    yield
    session_auth._sessions.clear()


@pytest.fixture
def npm_upstream():
    """Threaded mock of NPM v2: POST /api/tokens (login), GET /api/tokens
    (CSRF), /api/nginx/proxy-hosts (list). First login returns jwt-1; after
    `invalidate` it returns jwt-2. `/api/nginx/proxy-hosts` 401s on the first
    hit when `fail_first` is set (session-expiry drill)."""
    state = {
        'logins': 0, 'csrf_calls': 0, 'list_hits': 0, 'mutation_hits': 0,
        'fail_first': False, 'csrf_fail_mutation': False,
        'last_authorization': '', 'last_csrf': '',
    }

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _path(self):
            from urllib.parse import urlparse
            return urlparse(self.path).path

        def _respond(self, status, payload):
            body = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            p = self._path()
            if p == '/api/tokens':
                state['logins'] += 1
                state['last_authorization'] = self.headers.get('Authorization', '')
                jwt = 'jwt-1' if state['logins'] == 1 else 'jwt-2'
                self._respond(200, {'token': jwt, 'refreshToken': 'rt', 'expires_in': 3600})
                return
            if p == '/api/nginx/proxy-hosts':
                state['mutation_hits'] += 1
                state['last_authorization'] = self.headers.get('Authorization', '')
                state['last_csrf'] = self.headers.get('X-Csrf-Token', '')
                if state['csrf_fail_mutation']:
                    self._respond(403, {'error': 'invalid csrf token'})
                    return
                self._respond(200, {'ok': True})
                return
            self._respond(404, {'error': 'not found'})

        def do_GET(self):
            p = self._path()
            if p == '/api/tokens':
                state['csrf_calls'] += 1
                self._respond(200, {'token': 'csrf-1'})
                return
            if p == '/api/nginx/proxy-hosts':
                state['list_hits'] += 1
                state['last_authorization'] = self.headers.get('Authorization', '')
                if state['fail_first'] and state['list_hits'] == 1:
                    self._respond(401, {'error': 'Unauthorized'})
                    return
                self._respond(200, [{'id': 22,
                                     'domain_names': ['haos.local.kenguelacloud.com'],
                                     'forward_scheme': 'http',
                                     'forward_host': '192.168.1.235',
                                     'forward_port': 8123,
                                     'enabled': True, 'ssl_forced': True,
                                     'certificate_id': 2,
                                     'meta': {'nginx_online': True, 'nginx_err': ''}}])
                return
            if p == '/api/':
                self._respond(200, {'status': 'OK', 'version': {'major': 2, 'minor': 11, 'revision': 2}})
                return
            self._respond(404, {'error': 'not found'})

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}", 'state': state}
    server.shutdown()
    server.server_close()


def _make(upstream, gate_mode='destructive'):
    create_integration(
        "npm", upstream['base_url'] + '/api', "session", "",
        client_id="eshu@local.kenguelacloud.com", client_secret="pw",
        token_url=upstream['base_url'] + '/api/tokens',
        kind="npm", gate_mode=gate_mode)
    seed_for_kind(get_integration("npm"))
    return get_integration("npm")


class TestSessionManager:

    def test_login_and_csrf_once_then_cached(self, npm_upstream):
        _make(npm_upstream)
        run_tool("npm", "proxy_hosts", {})
        assert npm_upstream["state"]["logins"] == 1
        assert npm_upstream["state"]["csrf_calls"] == 1
        assert npm_upstream["state"]["last_authorization"] == "Bearer jwt-1"
        # second read reuses the session
        run_tool("npm", "proxy_hosts", {})
        assert npm_upstream["state"]["logins"] == 1

    def test_401_triggers_relogin_and_retry(self, npm_upstream):
        npm_upstream["state"]["fail_first"] = True
        _make(npm_upstream)
        run_tool("npm", "proxy_hosts", {})
        # first list 401 -> re-login (jwt-2) -> retry succeeds
        assert npm_upstream["state"]["logins"] == 2
        assert npm_upstream["state"]["last_authorization"] == "Bearer jwt-2"
        assert npm_upstream["state"]["list_hits"] == 2

    def test_mutation_sends_csrf(self, npm_upstream):
        from core.integration_proxy import execute_integration_call
        from db.integrations import get_tool
        _make(npm_upstream)
        tool = get_tool("npm", "create_proxy_host")
        body = {"domain_names": ["x.local"], "forward_host": "1.2.3.4",
                "forward_port": 80, "enabled": False}
        # the approval executor calls execute_integration_call directly, so
        # this is the path that must carry the CSRF header
        res = execute_integration_call(get_integration("npm"), tool,
                                       {"body": body}, agent="test")
        assert res.get("status_code") == 200
        assert npm_upstream["state"]["mutation_hits"] == 1
        assert npm_upstream["state"]["last_csrf"] == "csrf-1"
        assert npm_upstream["state"]["last_authorization"] == "Bearer jwt-1"

    def test_persistent_403_surfaces_csrf_failed(self, npm_upstream):
        """A 403 that survives the re-login retry is a server-side session/CSRF
        bug — surfaced as csrf_failed (not retried forever)."""
        from core.integration_proxy import execute_integration_call
        from db.integrations import get_tool
        npm_upstream["state"]["csrf_fail_mutation"] = True
        _make(npm_upstream)
        tool = get_tool("npm", "create_proxy_host")
        res = execute_integration_call(get_integration("npm"), tool,
                                       {"body": {"enabled": False}}, agent="test")
        # one re-login retry, then the error_codes map turns the 403 into csrf_failed
        assert npm_upstream["state"]["logins"] == 2
        assert res["error"] == "csrf_failed: invalid csrf token"
        assert res["status_code"] == 403

    def test_missing_credentials_error(self, npm_upstream):
        create_integration("npm", npm_upstream['base_url'] + '/api', "session", "",
                           kind="npm")
        seed_for_kind(get_integration("npm"))
        out = json.loads(run_tool("npm", "proxy_hosts", {}))
        assert out.get("error")

    def test_login_401_hints_credentials(self):
        from core.session_auth import _login_error
        import urllib.error
        exc = urllib.error.HTTPError("http://x", 401, "Unauthorized", None, None)
        err = _login_error("login", exc)
        assert "401" in str(err)
        assert "Client ID" in str(err) and "Client Secret" in str(err)

    def test_ssl_error_hints_http(self):
        from core.session_auth import _login_error
        import ssl, urllib.error
        exc = urllib.error.URLError(ssl.SSLError(1, "WRONG_VERSION_NUMBER"))
        err = _login_error("login", exc)
        assert "http://" in str(err)
        assert "port 81" in str(err)

    def test_auth_type_accepted(self, auth_client):
        r = auth_client.post("/api/integrations", json={
            "name": "npm", "base_url": "http://192.168.1.242:81/api",
            "auth_type": "session", "client_id": "eshu@local.kenguelacloud.com",
            "client_secret": "pw", "token_url": "http://192.168.1.242:81/api/tokens",
            "kind": "npm",
        })
        assert r.status_code == 200


class TestNpmCatalog:

    def test_seed_creates_tools(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "npm", "base_url": "http://192.168.1.242:81/api",
            "auth_type": "session", "client_id": "eshu@local.kenguelacloud.com",
            "client_secret": "pw", "token_url": "http://192.168.1.242:81/api/tokens",
            "kind": "npm",
        })
        r = auth_client.post("/api/integrations/npm/seed")
        assert r.status_code == 200
        tools = auth_client.get("/api/integrations/npm/tools").json()
        names = {t["name"] for t in tools}
        assert names == {"proxy_hosts", "proxy_host", "redirection_hosts", "streams",
                         "certificates", "version",
                         "create_proxy_host", "update_proxy_host", "delete_proxy_host"}
        # no generic floor for npm
        assert "read" not in names and "write" not in names
        # writes are always gated
        cp = next(t for t in tools if t["name"] == "create_proxy_host")
        assert cp["read_only"] == 0 and cp["always_gate"] == 1

    def test_tools_namespaced_by_name(self, auth_client):
        import asyncio
        from core.mcp_server import mcp, refresh_mcp_tools
        auth_client.post("/api/integrations", json={
            "name": "npm", "base_url": "http://192.168.1.242:81/api",
            "auth_type": "session", "client_id": "eshu@local.kenguelacloud.com",
            "client_secret": "pw", "token_url": "http://192.168.1.242:81/api/tokens",
            "kind": "npm",
        })
        auth_client.post("/api/integrations/npm/seed")
        refresh_mcp_tools()

        async def _names():
            return {t.name for t in await mcp.list_tools()}
        names = asyncio.run(_names())
        assert "npm_proxy_hosts" in names
        assert "npm_delete_proxy_host" in names


class TestNpmTransforms:

    def test_proxy_hosts_compact(self, npm_upstream):
        _make(npm_upstream)
        out = json.loads(run_tool("npm", "proxy_hosts", {}))
        row = out[0]
        assert row["id"] == 22
        assert row["nginxOnline"] is True
        assert "meta" not in row
        assert "nginxOnline" in row

    def test_proxy_hosts_search(self, npm_upstream):
        _make(npm_upstream)
        out = json.loads(run_tool("npm", "proxy_hosts", {"search": "HAOS"}))
        assert len(out) == 1
        out = json.loads(run_tool("npm", "proxy_hosts", {"search": "nomatch"}))
        assert out == []

    def test_version_transform(self, npm_upstream):
        _make(npm_upstream)
        out = json.loads(run_tool("npm", "version", {}))
        assert out == {"version": "2.11.2"}


class TestNpmGating:

    def test_write_tool_pending_requires_approval(self, npm_upstream):
        _make(npm_upstream)
        out = json.loads(run_tool("npm", "delete_proxy_host", {"id": 22, "reason": "test"}))
        assert out.get("status") == "pending"
        assert "check_approval" in out.get("message", "")
        assert npm_upstream["state"]["mutation_hits"] == 0
        from db.integrations import get_pending_calls
        pending = get_pending_calls()
        assert any(p["tool"] == "delete_proxy_host" for p in pending)


class TestStaleSeedCleanup:

    def test_reseed_drops_removed_seed_tool_keeps_handmade(self, auth_client):
        """When a tool is removed from the catalog, reseed deletes the stale
        seed-managed tool but preserves operator-created tools."""
        from db.integrations import create_tool, get_tools
        from core.seeds import seed_for_kind
        auth_client.post("/api/integrations", json={
            "name": "npm", "base_url": "http://192.168.1.242:81/api",
            "auth_type": "session", "client_id": "eshu@local.kenguelacloud.com",
            "client_secret": "pw", "token_url": "http://192.168.1.242:81/api/tokens",
            "kind": "npm",
        })
        auth_client.post("/api/integrations/npm/seed")
        # simulate a tool that an older catalog seeded but the current one dropped
        iid = get_integration("npm")["id"]
        create_tool(iid, "custom_locations", "old", "GET", "/nginx/custom-locations",
                    [], "{}", read_only=True, seeded=True)
        create_tool(iid, "my_hand_tool", "mine", "GET", "/x", [], "{}", read_only=True)
        seed_for_kind(get_integration("npm"))
        names = {t["name"] for t in get_tools(iid)}
        assert "custom_locations" not in names   # stale seed tool dropped
        assert "my_hand_tool" in names           # hand-created preserved
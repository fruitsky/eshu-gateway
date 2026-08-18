import http.server
import json
import threading

import pytest

from db.integrations import create_integration, get_integration
from core.integration_proxy import execute_integration_call, ProxyError


@pytest.fixture(autouse=True)
def _clear_oauth_cache():
    """The OAuth2 token cache is module-level and must not leak across tests."""
    from core import integration_proxy
    integration_proxy._oauth_tokens.clear()
    yield
    integration_proxy._oauth_tokens.clear()


@pytest.fixture
def omada_upstream():
    """Threaded upstream mimicking Omada's OAuth2 token exchange + an API
    endpoint. `/openapi/authorize/token` returns the next access token under
    `result.accessToken`; `/sites` returns 401 on its first hit when
    `fail_first_api` is set, then 200."""
    state = {
        'token_hits': 0,
        'api_hits': 0,
        'api_auth': '',
        'fail_first_api': False,
        'tokens': ['omada-token-1', 'omada-token-2'],
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
            if self._path() == '/openapi/authorize/token':
                idx = min(state['token_hits'], len(state['tokens']) - 1)
                state['token_hits'] += 1
                self._respond(200, {'errorCode': 0, 'result': {'accessToken': state['tokens'][idx]}})
                return
            self._respond(404, {'error': 'not found'})

        def do_GET(self):
            if self._path().endswith('/sites'):
                state['api_hits'] += 1
                state['api_auth'] = self.headers.get('Authorization', '')
                if state['fail_first_api'] and state['api_hits'] == 1:
                    self._respond(401, {'error': 'unauthorized'})
                    return
                self._respond(200, [{'site': 'Home', 'id': 1}])
                return
            self._respond(404, {'error': 'not found'})

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}", 'state': state}
    server.shutdown()
    server.server_close()


def _omada_integration(upstream):
    create_integration(
        "omada", upstream['base_url'] + "/openapi/v1/omadac-1", "oauth2", "",
        client_id="cid", client_secret="csecret",
        token_url=upstream['base_url'] + "/openapi/authorize/token",
    )
    return get_integration("omada")


def _list_sites_tool():
    return {
        "id": 1, "name": "list_sites", "enabled": True, "method": "GET",
        "path_template": "/sites", "params": [],
        "fields": [], "search_field": "", "read_only": True,
    }


class TestOmadaOAuth2:

    def test_token_fetched_once_and_used(self, omada_upstream):
        res = execute_integration_call(
            _omada_integration(omada_upstream), _list_sites_tool(), {}, agent="test")
        assert res["status_code"] == 200
        assert omada_upstream["state"]["token_hits"] == 1
        assert omada_upstream["state"]["api_auth"] == "AccessToken=omada-token-1"

    def test_token_cached_across_calls(self, omada_upstream):
        integration = _omada_integration(omada_upstream)
        execute_integration_call(integration, _list_sites_tool(), {}, agent="test")
        execute_integration_call(integration, _list_sites_tool(), {}, agent="test")
        assert omada_upstream["state"]["token_hits"] == 1

    def test_401_triggers_refetch(self, omada_upstream):
        omada_upstream["state"]["fail_first_api"] = True
        res = execute_integration_call(
            _omada_integration(omada_upstream), _list_sites_tool(), {}, agent="test")
        assert res["status_code"] == 200
        assert omada_upstream["state"]["token_hits"] == 2
        assert omada_upstream["state"]["api_auth"] == "AccessToken=omada-token-2"

    def test_missing_client_credentials(self, omada_upstream):
        create_integration("omada", omada_upstream["base_url"], "oauth2", "",
                           token_url=omada_upstream["base_url"] + "/token")
        integration = get_integration("omada")
        try:
            execute_integration_call(integration, _list_sites_tool(), {}, agent="test")
            assert False, "expected ProxyError"
        except ProxyError as e:
            assert e.status_code == 500

    def test_missing_token_url(self, omada_upstream):
        create_integration("omada", omada_upstream["base_url"], "oauth2", "",
                           client_id="cid", client_secret="csecret")
        integration = get_integration("omada")
        try:
            execute_integration_call(integration, _list_sites_tool(), {}, agent="test")
            assert False, "expected ProxyError"
        except ProxyError as e:
            assert e.status_code == 500


class TestOmadaApi:

    def test_oauth2_auth_type_accepted(self, auth_client):
        r = auth_client.post("/api/integrations", json={
            "name": "omada", "base_url": "https://omada.local:8043/api/v2",
            "auth_type": "oauth2", "client_id": "cid",
            "client_secret": "csecret",
            "token_url": "https://omada.local:8043/api/v2/oauth/token",
            "kind": "omada",
        })
        assert r.status_code == 200

    def test_client_secret_stripped_from_list(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "omada", "base_url": "https://omada.local:8043/api/v2",
            "auth_type": "oauth2", "client_id": "cid",
            "client_secret": "csecret",
            "token_url": "https://omada.local:8043/api/v2/oauth/token",
            "kind": "omada",
        })
        ints = auth_client.get("/api/integrations").json()
        row = next(i for i in ints if i["name"] == "omada")
        assert "client_secret" not in row
        assert "secret" not in row
        assert row["client_id"] == "cid"
        assert row["token_url"] == "https://omada.local:8043/api/v2/oauth/token"

    def test_invalid_auth_type_rejected(self, auth_client):
        r = auth_client.post("/api/integrations", json={
            "name": "bad", "base_url": "https://x.local", "auth_type": "weird",
        })
        assert r.status_code == 400


class _FakeResp:
    def __init__(self, body, status=200):
        self._body = body.encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self._body


def _patch_urlopen(monkeypatch, body):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=30, context=None: _FakeResp(body))


def _omada_read_tool(name="list_sites", path="/sites", fields=None, search_field="name"):
    return {
        "id": 1, "name": name, "enabled": True, "method": "GET",
        "path_template": path, "params": [],
        "fields": fields or [], "search_field": search_field or "", "read_only": True,
    }


class TestOmadaEnvelope:
    """Omada wraps payloads in {"result": {"data": [...]}} — shaping must unwrap
    the `result` envelope before projecting/searching/limiting."""

    def test_project_body_unwraps_omada_result(self):
        from core.integration_proxy import _project_body
        body = json.dumps({"errorCode": 0, "msg": "Success", "result": {
            "totalRows": 1, "currentPage": 1, "currentSize": 1,
            "data": [{"siteId": "a", "name": "Home", "type": 0, "region": "x"}]}})
        out = json.loads(_project_body(body, ["siteId", "name", "type"]))
        assert out == [{"siteId": "a", "name": "Home", "type": 0}]

    def test_projection_through_result_grid(self, monkeypatch):
        from core.integration_proxy import execute_integration_call
        create_integration("omada", "https://omada.local/openapi/v1/omadac-1", "none", "")
        integration = get_integration("omada")
        body = json.dumps({"errorCode": 0, "result": {
            "totalRows": 2, "currentPage": 1, "currentSize": 2,
            "data": [{"siteId": "a", "name": "Home", "type": 0, "region": "x"},
                     {"siteId": "b", "name": "Office", "type": 0, "region": "y"}]}})
        _patch_urlopen(monkeypatch, body)
        res = execute_integration_call(
            integration, _omada_read_tool(fields=["siteId", "name", "type"]), {"limit": 50}, agent="test")
        out = json.loads(res["body"])
        assert [s["name"] for s in out] == ["Home", "Office"]
        assert set(out[0].keys()) == {"siteId", "name", "type"}

    def test_search_and_limit_through_result_envelope(self, monkeypatch):
        from core.integration_proxy import execute_integration_call
        create_integration("omada", "https://omada.local/openapi/v1/omadac-1", "none", "")
        integration = get_integration("omada")
        body = json.dumps({"errorCode": 0, "result": {
            "totalRows": 3, "currentPage": 1, "currentSize": 3,
            "data": [{"siteId": "a", "name": "Home", "type": 0, "region": "x"},
                     {"siteId": "b", "name": "Office", "type": 0, "region": "y"},
                     {"siteId": "c", "name": "Holiday Home", "type": 0, "region": "z"}]}})
        _patch_urlopen(monkeypatch, body)
        res = execute_integration_call(
            integration, _omada_read_tool(fields=["siteId", "name"]),
            {"search": "HOME", "limit": 50}, agent="test")
        out = json.loads(res["body"])
        assert [s["name"] for s in out] == ["Home", "Holiday Home"]
        assert set(out[0].keys()) == {"siteId", "name"}


class TestOmadaSeed:

    def test_seed_creates_tools(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "omada", "base_url": "https://omada.local:8043/openapi/v1/omadac-1",
            "auth_type": "oauth2", "client_id": "cid", "client_secret": "csecret",
            "token_url": "https://omada.local:8043/openapi/authorize/token",
            "kind": "omada",
        })
        r = auth_client.post("/api/integrations/omada/seed")
        assert r.status_code == 200
        assert r.json()["created"] == 9
        tools = auth_client.get("/api/integrations/omada/tools").json()
        names = {t["name"] for t in tools}
        assert names == {"list_sites", "get_site", "list_site_devices", "search_devices",
                         "list_site_clients", "get_client", "list_site_ssids",
                         "block_client", "reconnect_client"}
        bc = next(t for t in tools if t["name"] == "block_client")
        assert bc["read_only"] == 0
        ls = next(t for t in tools if t["name"] == "list_sites")
        assert ls["search_field"] == "name"
        assert "siteId" in ls["fields"]
        # Grid endpoints need page/pageSize; the seed defaults them so calls
        # (and the /test endpoint) work without the agent passing them.
        params = {p["name"]: p for p in ls["params"]}
        assert params["page"]["default"] == 1
        assert params["pageSize"]["default"] == 50

    def test_tools_namespaced_by_kind(self, auth_client):
        import asyncio
        from core.mcp_server import mcp, refresh_mcp_tools
        auth_client.post("/api/integrations", json={
            "name": "omada", "base_url": "https://omada.local:8043/openapi/v1/omadac-1",
            "auth_type": "none", "secret": "", "kind": "omada",
        })
        auth_client.post("/api/integrations/omada/seed")
        refresh_mcp_tools()

        async def _names():
            tools = await mcp.list_tools()
            return {t.name for t in tools}
        names = asyncio.run(_names())

        assert "omada_list_sites" in names
        assert "omada_list_site_clients" in names
        assert "omada_block_client" in names

    def test_list_tools_expose_search_limit(self, auth_client):
        import inspect
        from core.mcp_server import _build_tool_fn
        auth_client.post("/api/integrations", json={
            "name": "omada", "base_url": "https://omada.local:8043/openapi/v1/omadac-1",
            "auth_type": "none", "secret": "", "kind": "omada",
        })
        auth_client.post("/api/integrations/omada/seed")
        from db.integrations import get_integration, get_tool
        integration = get_integration("omada")
        list_sites = get_tool("omada", "list_sites")
        get_site = get_tool("omada", "get_site")
        fn = _build_tool_fn("omada", list_sites)
        params = list(inspect.signature(fn).parameters)
        assert "search" in params and "limit" in params and "full" in params
        fn2 = _build_tool_fn("omada", get_site)
        params2 = list(inspect.signature(fn2).parameters)
        assert "search" not in params2 and "limit" not in params2


class TestParamDefaults:
    """`_build_request` falls back to a param's `default` when the agent omits
    it, so required-query-param APIs (Omada Grid endpoints) work out of the box
    and callers can still override."""

    def test_missing_arg_uses_default(self):
        from core.integration_proxy import _build_request
        tool = {
            "method": "GET", "path_template": "/sites", "params": [
                {"name": "page", "type": "integer", "default": 1},
                {"name": "pageSize", "type": "integer", "default": 50},
            ],
        }
        _, _, qs, _, _ = _build_request(tool, {})
        assert qs == "page=1&pageSize=50"

    def test_explicit_arg_overrides_default(self):
        from core.integration_proxy import _build_request
        tool = {
            "method": "GET", "path_template": "/sites", "params": [
                {"name": "page", "type": "integer", "default": 1},
                {"name": "pageSize", "type": "integer", "default": 50},
            ],
        }
        _, _, qs, _, _ = _build_request(tool, {"page": 2, "pageSize": 10})
        assert qs == "page=2&pageSize=10"

    def test_no_default_omits_param(self):
        from core.integration_proxy import _build_request
        tool = {
            "method": "GET", "path_template": "/sites", "params": [
                {"name": "searchKey", "type": "string", "default": None},
            ],
        }
        _, _, qs, _, _ = _build_request(tool, {})
        assert qs == ""


class TestTlsVerify:
    """The verify_tls toggle is available on every integration and controls
    whether the proxy verifies the controller's TLS certificate."""

    def test_ssl_context_defaults_to_verified(self):
        from core.integration_proxy import _ssl_context
        assert _ssl_context({"verify_tls": 1}) is None
        assert _ssl_context({}) is None

    def test_ssl_context_unverified_when_disabled(self):
        import ssl
        from core.integration_proxy import _ssl_context
        ctx = _ssl_context({"verify_tls": 0})
        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_verify_tls_roundtrip(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "omada", "base_url": "https://omada.local:8043/openapi/v1/omadac-1",
            "auth_type": "oauth2", "client_id": "cid", "client_secret": "csecret",
            "token_url": "https://omada.local:8043/openapi/authorize/token",
            "kind": "omada", "verify_tls": False,
        })
        ints = auth_client.get("/api/integrations").json()
        row = next(i for i in ints if i["name"] == "omada")
        assert row["verify_tls"] == 0
        r = auth_client.put("/api/integrations/omada", json={"verify_tls": True})
        assert r.status_code == 200
        row = next(i for i in auth_client.get("/api/integrations").json() if i["name"] == "omada")
        assert row["verify_tls"] == 1

    def test_test_endpoint_returns_json_on_proxy_error(self, auth_client):
        """An OAuth2 integration with no token_url raises ProxyError inside the
        test endpoint — it must surface as JSON (not a non-JSON 500)."""
        auth_client.post("/api/integrations", json={
            "name": "broken", "base_url": "https://omada.local/openapi/v1/omadac-1",
            "auth_type": "oauth2", "client_id": "cid", "client_secret": "csecret",
            "kind": "omada",
        })
        auth_client.post("/api/integrations/broken/seed")
        r = auth_client.post("/api/integrations/broken/test")
        assert r.status_code == 200
        data = r.json()
        assert data["error"] and "token_url" in data["error"]

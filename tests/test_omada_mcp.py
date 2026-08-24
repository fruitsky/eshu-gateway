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
    `result.accessToken` (+ `expiresIn`); `/sites` returns 401 on its first
    hit when `fail_first_api` is set, or a token-expired error (-44112) when
    `expired_first` is set, then the normal list."""
    state = {
        'token_hits': 0,
        'api_hits': 0,
        'api_auth': '',
        'fail_first_api': False,
        'expired_first': False,
        'expires_in': 7200,
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
                self._respond(200, {'errorCode': 0, 'result': {
                    'accessToken': state['tokens'][idx], 'expiresIn': state['expires_in']}})
                return
            self._respond(404, {'error': 'not found'})

        def do_GET(self):
            if self._path().endswith('/sites'):
                state['api_hits'] += 1
                state['api_auth'] = self.headers.get('Authorization', '')
                if state['fail_first_api'] and state['api_hits'] == 1:
                    self._respond(401, {'error': 'unauthorized'})
                    return
                if state['expired_first'] and state['api_hits'] == 1:
                    self._respond(200, {'errorCode': -44112,
                                        'msg': 'The access token has expired. Please re-initiate the refreshToken process to obtain the access token.'})
                    return
                self._respond(200, [{'site': 'Home', 'id': 1}])
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

    def test_expired_token_error_triggers_refetch(self, omada_upstream):
        """Omada reports an expired access token as HTTP 200 with
        errorCode -44112 — the proxy must re-auth and retry, not serve {}."""
        omada_upstream["state"]["expired_first"] = True
        res = execute_integration_call(
            _omada_integration(omada_upstream), _list_sites_tool(), {}, agent="test")
        assert res["status_code"] == 200
        assert omada_upstream["state"]["token_hits"] == 2
        assert omada_upstream["state"]["api_auth"] == "AccessToken=omada-token-2"

    def test_expired_token_error_retry_reused_on_next_call(self, omada_upstream):
        """After the retried re-auth, the fresh token is cached and reused."""
        omada_upstream["state"]["expired_first"] = True
        integration = _omada_integration(omada_upstream)
        execute_integration_call(integration, _list_sites_tool(), {}, agent="test")
        assert omada_upstream["state"]["token_hits"] == 2
        execute_integration_call(integration, _list_sites_tool(), {}, agent="test")
        assert omada_upstream["state"]["token_hits"] == 2  # no third fetch

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
        assert r.json()["created"] == 12  # 10 curated + generic read/write
        tools = auth_client.get("/api/integrations/omada/tools").json()
        names = {t["name"] for t in tools}
        assert names == {"list_sites", "get_site", "list_site_devices", "search_devices",
                         "list_site_clients", "get_client", "list_site_ssids",
                         "list_site_alerts", "block_client", "reconnect_client", "read", "write"}
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
        # Clients uses the working v2 POST endpoint (v1 GET is broken on v6.2)
        lsc = next(t for t in tools if t["name"] == "list_site_clients")
        assert lsc["method"] == "POST"
        assert lsc["version"] == "v2"
        # search_devices requires a keyword and strips the envelope
        sd = next(t for t in tools if t["name"] == "search_devices")
        assert next(p for p in sd["params"] if p["name"] == "searchKey")["required"]
        assert sd["strip_envelope"] == 1
        # get_client compact carries the diagnostic core + traffic
        gc = next(t for t in tools if t["name"] == "get_client")
        assert "deviceCategory" in gc["fields"] and "vid" in gc["fields"] and "uptime" in gc["fields"]
        assert "trafficDown" in gc["fields"] and "trafficUp" in gc["fields"]
        # device compact includes firmware/uptime
        dev = next(t for t in tools if t["name"] == "list_site_devices")
        assert "firmwareVersion" in dev["fields"] and "uptime" in dev["fields"]
        # clients compact includes traffic stats
        assert "trafficDown" in lsc["fields"] and "trafficUp" in lsc["fields"]
        # alerts params use clean names mapped to the dotted wire keys
        la = next(t for t in tools if t["name"] == "list_site_alerts")
        ap = {p["name"]: p for p in la["params"]}
        assert "timeStart" in ap and "timeEnd" in ap
        assert ap["timeStart"]["query_key"] == "filters.timeStart"

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


class TestOmadaErrorDiscipline:
    """Non-zero errorCode envelopes must surface as errors, not be projected
    to {} — the root cause of the broken clients/alerts tools."""

    def test_upstream_error_detector(self):
        from core.integration_proxy import _upstream_error
        assert _upstream_error('{"errorCode": -1, "msg": "General error"}') == "Omada error -1: General error"
        assert _upstream_error('{"errorCode": 0, "result": []}') is None
        assert _upstream_error('{"errorCode": 0}') is None
        assert _upstream_error('not json') is None

    def test_projected_tool_surfaces_error_not_empty(self, monkeypatch):
        from core.integration_proxy import execute_integration_call
        create_integration("omada", "https://omada.local/openapi/v1/omadac-1", "none", "")
        tool = {
            "id": 1, "name": "list_site_clients", "enabled": True, "method": "POST",
            "path_template": "/sites/{siteId}/clients",
            "params": [{"name": "siteId", "type": "string", "required": True}],
            "fields": ["id", "mac", "name"], "search_field": "name", "read_only": True,
        }
        _patch_urlopen(monkeypatch, '{"errorCode": -1, "msg": "General error"}')
        res = execute_integration_call(get_integration("omada"), tool, {"siteId": "s1"}, agent="test")
        assert res["error"] == "Omada error -1: General error"
        assert res["body"] == ""

    def test_success_still_shapes(self, monkeypatch):
        from core.integration_proxy import execute_integration_call
        create_integration("omada", "https://omada.local/openapi/v1/omadac-1", "none", "")
        tool = {
            "id": 1, "name": "list_site_clients", "enabled": True, "method": "POST",
            "path_template": "/sites/{siteId}/clients",
            "params": [{"name": "siteId", "type": "string", "required": True}],
            "fields": ["id", "name"], "search_field": "name", "read_only": True,
        }
        body = '{"errorCode": 0, "result": {"data": [{"id": "a", "name": "X", "mac": "AA"}, {"id": "b", "name": "Y", "mac": "BB"}]}}'
        _patch_urlopen(monkeypatch, body)
        res = execute_integration_call(get_integration("omada"), tool, {"siteId": "s1"}, agent="test")
        assert not res["error"]
        assert json.loads(res["body"]) == [{"id": "a", "name": "X"}, {"id": "b", "name": "Y"}]


class TestOmadaVersionSwap:
    """version=v2 tools swap /openapi/v1/ for /openapi/v2/ in the URL base."""

    def test_v2_tool_hits_v2_path(self, mock_upstream):
        from core.integration_proxy import execute_integration_call
        create_integration("omada", mock_upstream["base_url"] + "/openapi/v1/omadac-1", "none", "")
        tool = {
            "id": 1, "name": "list_site_clients", "enabled": True, "method": "POST",
            "version": "v2", "path_template": "/sites/{siteId}/clients",
            "params": [
                {"name": "siteId", "type": "string", "required": True},
                {"name": "page", "type": "integer", "default": 1},
                {"name": "pageSize", "type": "integer", "default": 50},
            ],
            "fields": ["id", "name"], "read_only": True,
        }
        res = execute_integration_call(get_integration("omada"), tool, {"siteId": "s1"}, agent="test")
        assert res["status_code"] == 200
        rec = mock_upstream["requests"][-1]
        assert rec["path"] == "/openapi/v2/omadac-1/sites/s1/clients"
        assert rec["method"] == "POST"
        assert json.loads(rec["body"]) == {"page": 1, "pageSize": 50}

    def test_v1_tool_stays_on_v1(self, mock_upstream):
        from core.integration_proxy import execute_integration_call
        create_integration("omada", mock_upstream["base_url"] + "/openapi/v1/omadac-1", "none", "")
        tool = {
            "id": 2, "name": "list_sites", "enabled": True, "method": "GET",
            "path_template": "/sites", "params": [], "fields": ["siteId"], "read_only": True,
        }
        execute_integration_call(get_integration("omada"), tool, {}, agent="test")
        rec = mock_upstream["requests"][-1]
        assert rec["path"] == "/openapi/v1/omadac-1/sites"


class TestQueryKeyAndStripEnvelope:

    def test_build_request_uses_query_key(self):
        from core.integration_proxy import _build_request
        tool = {
            "method": "GET", "path_template": "/sites/{siteId}/logs/alerts", "params": [
                {"name": "siteId", "type": "string", "required": True},
                {"name": "timeStart", "type": "integer", "query_key": "filters.timeStart", "required": True},
                {"name": "timeEnd", "type": "integer", "query_key": "filters.timeEnd", "required": True},
            ],
        }
        _, _, qs, _, _ = _build_request(tool, {"siteId": "s1", "timeStart": 1, "timeEnd": 2})
        assert set(qs.split('&')) == {"filters.timeStart=1", "filters.timeEnd=2"}

    def test_build_request_path_still_uses_name(self):
        from core.integration_proxy import _build_request
        tool = {
            "method": "GET", "path_template": "/sites/{siteId}/clients", "params": [
                {"name": "siteId", "type": "string", "required": True},
            ],
        }
        _, path, _, _, _ = _build_request(tool, {"siteId": "s1"})
        assert path == "/sites/s1/clients"

    def test_strip_envelope_unwraps_passthrough(self):
        from core.integration_proxy import _apply_shaping
        body = ('{"errorCode": 0, "msg": "Success", "result": '
                '{"siteNames": {"s1": "Home"}, "devices": [{"mac": "AA", "name": "AP"}]}}')
        tool = {"fields": [], "search_field": "", "filter_fields": [], "strip_envelope": True}
        out = json.loads(_apply_shaping(body, tool, {}))
        assert out == {"siteNames": {"s1": "Home"}, "devices": [{"mac": "AA", "name": "AP"}]}

    def test_no_strip_envelope_passthrough_stays_raw(self):
        from core.integration_proxy import _apply_shaping
        body = '{"errorCode": 0, "result": {"x": 1}}'
        tool = {"fields": [], "search_field": "", "filter_fields": [], "strip_envelope": False}
        assert _apply_shaping(body, tool, {}) == body


class TestOauth2TokenTtl:

    def test_token_expired_detector(self):
        from core.integration_proxy import _oauth2_token_expired
        assert _oauth2_token_expired('{"errorCode": -44112, "msg": "expired"}') is True
        assert _oauth2_token_expired('{"errorCode": -44113, "msg": "invalid"}') is True
        assert _oauth2_token_expired('{"errorCode": 0, "result": []}') is False
        assert _oauth2_token_expired('[1, 2]') is False
        assert _oauth2_token_expired('not json') is False

    def test_token_refetched_after_expiry(self, monkeypatch):
        from core import integration_proxy
        integration = {"name": "omada", "auth_type": "oauth2", "base_url": "https://x/openapi/v1/omadac-1"}
        calls = iter([("tok-1", 1), ("tok-2", 1)])  # 1s → expires immediately
        monkeypatch.setattr(integration_proxy, "_fetch_oauth2_token", lambda i: next(calls))
        assert integration_proxy._oauth2_headers(integration) == {"Authorization": "AccessToken=tok-1"}
        assert integration_proxy._oauth2_headers(integration) == {"Authorization": "AccessToken=tok-2"}

    def test_token_reused_while_valid(self, monkeypatch):
        from core import integration_proxy
        integration = {"name": "omada", "auth_type": "oauth2", "base_url": "https://x/openapi/v1/omadac-1"}
        count = {"n": 0}

        def fake(i):
            count["n"] += 1
            return ("tok-long", 7200)
        monkeypatch.setattr(integration_proxy, "_fetch_oauth2_token", fake)
        assert integration_proxy._oauth2_headers(integration) == {"Authorization": "AccessToken=tok-long"}
        assert integration_proxy._oauth2_headers(integration) == {"Authorization": "AccessToken=tok-long"}
        assert count["n"] == 1


class TestPerIntegrationMcp:
    """Each enabled integration gets its own FastMCP server (mounted at
    /mcp/<ns>) exposing only its tools + check_approval, un-namespaced — so a
    client can mount just the integrations it uses instead of loading all tools
    from the single /mcp surface."""

    def _omada(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "omada", "base_url": "https://omada.local:8043/openapi/v1/omadac-1",
            "auth_type": "none", "secret": "", "kind": "omada",
        })
        auth_client.post("/api/integrations/omada/seed")

    def test_per_integration_server_lists_only_its_tools(self, auth_client):
        import asyncio
        from core import mcp_server as ms
        # a second, unrelated integration so isolation is provable
        auth_client.post("/api/integrations", json={
            "name": "home-assistant", "base_url": "https://ha.local/api",
            "auth_type": "bearer", "secret": "tok", "kind": "ha",
        })
        auth_client.post("/api/integrations/home-assistant/seed")
        self._omada(auth_client)
        ms.refresh_mcp_tools()
        apps = ms.build_per_integration_mcp()

        assert "omada" in apps and "home_assistant" in apps

        async def _names(inst):
            return {t.name for t in await inst.list_tools()}

        omada = asyncio.run(_names(ms._per_integration["omada"]))
        assert "list_sites" in omada            # un-namespaced within its server
        assert "block_client" in omada          # mutating tool present
        assert "check_approval" in omada
        assert not any(n.startswith("home_assistant_") or n.startswith("omada_")
                       for n in omada)          # no cross-integration / no prefix

        ha = asyncio.run(_names(ms._per_integration["home_assistant"]))
        assert not any(n in ("list_sites", "block_client") for n in ha)  # no omada tools leak

    def test_disabled_integration_excluded(self, auth_client):
        import asyncio
        from core import mcp_server as ms
        auth_client.post("/api/integrations", json={
            "name": "disabled-one", "base_url": "https://x.local/api",
            "auth_type": "none", "secret": "", "kind": "custom",
        })
        auth_client.put("/api/integrations/disabled-one", json={"enabled": False})
        ms.refresh_mcp_tools()
        apps = ms.build_per_integration_mcp()
        assert "disabled_one" not in apps

    def test_tool_toggle_updates_per_integration_server(self, auth_client):
        import asyncio
        from core import mcp_server as ms
        self._omada(auth_client)
        ms.refresh_mcp_tools()
        ms.build_per_integration_mcp()

        tools = auth_client.get("/api/integrations/omada/tools").json()
        bc = next(t for t in tools if t["name"] == "block_client")
        auth_client.post(f"/api/integrations/omada/tools/{bc['id']}/toggle", json={"enabled": False})
        ms.refresh_mcp_tools()

        async def _names(inst):
            return {t.name for t in await inst.list_tools()}
        names = asyncio.run(_names(ms._per_integration["omada"]))
        assert "block_client" not in names
        assert "list_sites" in names

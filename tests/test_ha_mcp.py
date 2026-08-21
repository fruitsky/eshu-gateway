import json

from db.integrations import (
    create_integration,
    create_pending_call,
    create_tool,
    get_integration,
)
from core.integration_proxy import execute_integration_call


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


def _call_service_tool():
    return {
        "id": 1,
        "name": "call_service",
        "enabled": True,
        "method": "POST",
        "path_template": "/services/{domain}/{service}",
        "params": [
            {"name": "domain", "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True},
            {"name": "data", "type": "json", "required": False},
        ],
        "fields": [],
        "read_only": False,
    }


class TestHaJsonBody:
    """The `json` param type becomes the raw request body (for HA call_service)."""

    def test_call_service_sends_json_body(self, mock_upstream):
        create_integration("ha", mock_upstream["base_url"], "bearer", "tok")
        integration = get_integration("ha")
        res = execute_integration_call(
            integration, _call_service_tool(),
            {"domain": "light", "service": "turn_on",
             "data": {"entity_id": "light.x", "brightness": 128}}, agent="test")
        assert res["status_code"] == 200
        rec = mock_upstream["requests"][-1]
        assert rec["method"] == "POST"
        assert rec["path"] == "/services/light/turn_on"
        assert json.loads(rec["body"]) == {"entity_id": "light.x", "brightness": 128}

    def test_call_service_empty_data_omits_body(self, mock_upstream):
        create_integration("ha", mock_upstream["base_url"], "bearer", "tok")
        integration = get_integration("ha")
        res = execute_integration_call(
            integration, _call_service_tool(),
            {"domain": "light", "service": "turn_off", "data": None}, agent="test")
        assert res["status_code"] == 200
        rec = mock_upstream["requests"][-1]
        assert rec["body"] == ""


class TestHaProjection:

    def test_dotted_path_projection(self):
        from core.integration_proxy import _project_body
        body = json.dumps([
            {"entity_id": "light.x", "state": "on",
             "attributes": {"friendly_name": "Living Room"}}
        ])
        out = json.loads(_project_body(body, ["entity_id", "state", "attributes.friendly_name"]))
        assert out == [{"entity_id": "light.x", "state": "on", "friendly_name": "Living Room"}]

    def test_missing_nested_field_omitted(self):
        from core.integration_proxy import _project_body
        body = json.dumps([{"entity_id": "sensor.x", "state": "23", "attributes": {}}])
        out = json.loads(_project_body(body, ["entity_id", "state", "attributes.friendly_name"]))
        assert out == [{"entity_id": "sensor.x", "state": "23"}]


class TestHaSeed:

    def test_seed_creates_tools(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "ha", "base_url": "https://ha.local/api",
            "auth_type": "bearer", "secret": "tok", "kind": "ha",
        })
        r = auth_client.post("/api/integrations/ha/seed")
        assert r.status_code == 200
        assert r.json()["created"] == 12  # 8 curated + generic read/write/ws_read/ws_write
        tools = auth_client.get("/api/integrations/ha/tools").json()
        names = {t["name"] for t in tools}
        assert names == {"list_entities", "get_entity", "call_service",
                         "list_services", "get_config", "get_history",
                         "list_entity_registry", "list_device_registry",
                         "read", "write", "ws_read", "ws_write"}
        cs = next(t for t in tools if t["name"] == "call_service")
        assert cs["read_only"] == 0
        assert next(p for p in cs["params"] if p["name"] == "data")["type"] == "json"
        gh = next(t for t in tools if t["name"] == "get_history")
        assert next(p for p in gh["params"] if p["name"] == "start")["required"]
        er = next(t for t in tools if t["name"] == "list_entity_registry")
        assert er["transport"] == "ws"
        assert er["filter_fields"] == ["device_id"]

    def test_call_service_signature(self):
        import inspect
        from core.mcp_server import _build_tool_fn
        fn = _build_tool_fn("ha", _call_service_tool())
        params = list(inspect.signature(fn).parameters)
        assert "domain" in params and "service" in params
        assert "data" in params and "reason" in params
        assert "full" not in params  # mutating, no projection

    def test_seed_requires_kind(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "other", "base_url": "https://x.local/api",
            "auth_type": "none", "secret": "", "kind": "custom",
        })
        r = auth_client.post("/api/integrations/other/seed")
        # Every integration gets the generic passthrough floor, even with no
        # curated catalog.
        assert r.status_code == 200
        assert r.json()["created"] == 2
        tools = auth_client.get("/api/integrations/other/tools").json()
        assert {"read", "write"} <= {t["name"] for t in tools}

    def test_tools_namespaced_by_name(self, auth_client):
        """MCP tool names use the integration's name (clean lowercase slug), not
        the kind — so multiple instances of one software get distinct tool sets.
        A space in the name must not leak into tool names."""
        import asyncio
        from core.mcp_server import mcp, refresh_mcp_tools
        auth_client.post("/api/integrations", json={
            "name": "Home Assistant", "base_url": "https://ha.local/api",
            "auth_type": "bearer", "secret": "tok", "kind": "ha",
        })
        auth_client.post("/api/integrations/Home%20Assistant/seed")
        refresh_mcp_tools()

        async def _names():
            tools = await mcp.list_tools()
            return {t.name for t in tools}
        names = asyncio.run(_names())

        assert "home_assistant_list_entities" in names
        assert "home_assistant_get_entity" in names
        assert "home_assistant_call_service" in names
        assert not any(" " in n for n in names)


class TestHaSearchLimit:
    """Client-side search/limit shaping on list tools with a search_field."""

    def _tool(self):
        return {
            "id": 1, "name": "list_entities", "enabled": True, "method": "GET",
            "path_template": "/states", "params": [],
            "fields": ["entity_id", "state", "attributes.friendly_name"],
            "search_field": "entity_id", "read_only": True,
        }

    def _entities(self):
        return json.dumps([
            {"entity_id": "light.a", "state": "on", "attributes": {"friendly_name": "A"}},
            {"entity_id": "sensor.x", "state": "23", "attributes": {"friendly_name": "X"}},
            {"entity_id": "light.b", "state": "off", "attributes": {"friendly_name": "B"}},
            {"entity_id": "switch.y", "state": "on", "attributes": {"friendly_name": "Y"}},
        ])

    def test_search_filters_case_insensitive(self, monkeypatch):
        from core.integration_proxy import execute_integration_call
        create_integration("ha", "https://ha.local/api", "bearer", "tok", kind="ha")
        integration = get_integration("ha")
        _patch_urlopen(monkeypatch, self._entities())
        res = execute_integration_call(integration, self._tool(),
                                       {"search": "LIGHT", "limit": 50}, agent="test")
        out = json.loads(res["body"])
        assert [e["entity_id"] for e in out] == ["light.a", "light.b"]
        assert out[0]["friendly_name"] == "A"  # projected

    def test_limit_caps(self, monkeypatch):
        from core.integration_proxy import execute_integration_call
        create_integration("ha", "https://ha.local/api", "bearer", "tok", kind="ha")
        integration = get_integration("ha")
        _patch_urlopen(monkeypatch, self._entities())
        res = execute_integration_call(integration, self._tool(), {"limit": 2}, agent="test")
        assert len(json.loads(res["body"])) == 2

    def test_search_limit_not_forwarded_upstream(self, mock_upstream):
        from core.integration_proxy import execute_integration_call
        create_integration("ha", mock_upstream["base_url"], "bearer", "tok", kind="ha")
        integration = get_integration("ha")
        res = execute_integration_call(integration, self._tool(),
                                       {"search": "light", "limit": 50}, agent="test")
        assert res["status_code"] == 200
        rec = mock_upstream["requests"][-1]
        assert rec["method"] == "GET"
        assert rec["path"] == "/states"  # no ?search= / ?limit=

    def test_schema_exposes_search_and_limit(self, auth_client):
        import asyncio
        from core.mcp_server import mcp, refresh_mcp_tools
        auth_client.post("/api/integrations", json={
            "name": "ha", "base_url": "https://ha.local/api",
            "auth_type": "bearer", "secret": "tok", "kind": "ha",
        })
        auth_client.post("/api/integrations/ha/seed")
        refresh_mcp_tools()

        async def _go():
            tools = await mcp.list_tools()
            return {t.name: t.inputSchema.get("properties", {}) for t in tools}
        props = asyncio.run(_go())

        le = props["ha_list_entities"]
        assert "search" in le and "limit" in le
        # non-list tool (get_entity) has no search/limit
        assert "search" not in props["ha_get_entity"]
        assert "limit" not in props["ha_get_entity"]


class TestHaMoreReads:
    """The Phase-0 REST read tools (services, config, history)."""

    def _services_tool(self):
        return {
            "id": 1, "name": "list_services", "enabled": True, "method": "GET",
            "path_template": "/services", "params": [], "fields": [], "read_only": True,
        }

    def _config_tool(self):
        return {
            "id": 2, "name": "get_config", "enabled": True, "method": "GET",
            "path_template": "/config", "params": [],
            "fields": ["location_name", "latitude", "time_zone"], "read_only": True,
        }

    def test_list_services_passthrough(self, monkeypatch):
        from core.integration_proxy import execute_integration_call
        create_integration("ha", "https://ha.local/api", "bearer", "tok", kind="ha")
        body = '{"light": {"turn_on": {"name": "Turn On", "fields": {"entity_id": {"selector": {"entity": {}}}}}}}'
        _patch_urlopen(monkeypatch, body)
        res = execute_integration_call(get_integration("ha"), self._services_tool(), {}, agent="test")
        assert res["body"] == body

    def test_get_config_projects(self, monkeypatch):
        from core.integration_proxy import execute_integration_call
        create_integration("ha", "https://ha.local/api", "bearer", "tok", kind="ha")
        body = json.dumps({"location_name": "Home", "latitude": 51.5, "longitude": -0.12,
                           "time_zone": "Europe/London", "version": "2026.7.3"})
        _patch_urlopen(monkeypatch, body)
        res = execute_integration_call(get_integration("ha"), self._config_tool(), {}, agent="test")
        assert json.loads(res["body"]) == {"location_name": "Home", "latitude": 51.5, "time_zone": "Europe/London"}

    def test_get_history_path_and_query(self, mock_upstream):
        from core.integration_proxy import execute_integration_call
        create_integration("ha", mock_upstream["base_url"], "bearer", "tok", kind="ha")
        tool = {
            "id": 3, "name": "get_history", "enabled": True, "method": "GET",
            "path_template": "/history/period/{start}", "params": [
                {"name": "start", "type": "string", "required": True},
                {"name": "filter_entity_id", "type": "string", "required": False},
                {"name": "end_time", "type": "string", "required": False},
            ], "fields": [], "read_only": True,
        }
        res = execute_integration_call(get_integration("ha"), tool, {
            "start": "2026-08-18T00:00:00", "filter_entity_id": "sensor.x",
            "end_time": "2026-08-18T01:00:00"}, agent="test")
        assert res["status_code"] == 200
        rec = mock_upstream["requests"][-1]
        assert rec["path"] == "/history/period/2026-08-18T00%3A00%3A00?filter_entity_id=sensor.x&end_time=2026-08-18T01%3A00%3A00"


class TestHaApproval:

    def test_call_service_approval_executes(self, mock_upstream, auth_client):
        create_integration("ha", mock_upstream["base_url"], "bearer", "tok", kind="ha")
        integration = get_integration("ha")
        create_tool(integration["id"], "call_service", "Call a service", "POST",
                    "/services/{domain}/{service}",
                    [
                        {"name": "domain", "type": "string", "required": True},
                        {"name": "service", "type": "string", "required": True},
                        {"name": "data", "type": "json", "required": False},
                    ], "", read_only=False)
        call_id = create_pending_call("ha", "call_service",
                                      {"domain": "light", "service": "turn_on",
                                       "data": {"entity_id": "light.x"}}, "turn it on")
        r = auth_client.post(f"/api/integration-calls/{call_id}/approve")
        assert r.status_code == 200
        rec = mock_upstream["requests"][-1]
        assert rec["method"] == "POST"
        assert rec["path"] == "/services/light/turn_on"
        assert json.loads(rec["body"]) == {"entity_id": "light.x"}

import json

from db.integrations import (
    create_integration,
    create_pending_call,
    create_tool,
    get_integration,
)
from core.integration_proxy import execute_integration_call


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
        assert r.json()["created"] == 3
        tools = auth_client.get("/api/integrations/ha/tools").json()
        names = {t["name"] for t in tools}
        assert names == {"list_entities", "get_entity", "call_service"}
        cs = next(t for t in tools if t["name"] == "call_service")
        assert cs["read_only"] == 0
        assert next(p for p in cs["params"] if p["name"] == "data")["type"] == "json"

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
        assert r.status_code == 400


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

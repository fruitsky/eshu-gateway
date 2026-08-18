import json

from db.integrations import (
    create_integration,
    create_pending_call,
    create_tool,
    get_integration,
    get_integration_calls,
    get_tool,
)
from core.integration_proxy import ProxyError, execute_generic_call, is_destructive
from core.tool_runner import run_tool


class TestIsDestructive:

    def test_delete_method_is_destructive(self):
        assert is_destructive('DELETE', '/nodes/{vmid}')
        assert is_destructive('delete', '/x')

    def test_destructive_verb_in_path(self):
        assert is_destructive('POST', '/sites/1/devices/remove')
        assert is_destructive('POST', '/config/device_registry/remove')
        assert is_destructive('POST', '/nodes/{vmid}/reset')
        assert is_destructive('POST', '/things/purge')

    def test_benign_post_not_destructive(self):
        assert not is_destructive('POST', '/services/light/turn_on')
        assert not is_destructive('POST', '/nodes/pve/qemu/100/status/start')
        assert not is_destructive('POST', '/config/entity_registry/list')
        # disruptive-but-reversible verbs are deliberately not gated
        assert not is_destructive('POST', '/nodes/pve/qemu/100/status/restart')
        assert not is_destructive('POST', '/nodes/pve/qemu/100/status/stop')

    def test_get_not_destructive(self):
        assert not is_destructive('GET', '/sites')


class TestExecuteGenericCall:

    def test_generic_get_with_query(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "")
        res = execute_generic_call(get_integration("api"), "GET", "/states", {"group": "light"},
                                   agent="test", tool_name="read")
        assert res["status_code"] == 200
        rec = mock_upstream["requests"][-1]
        assert rec["method"] == "GET"
        assert rec["path"] == "/states?group=light"

    def test_generic_post_body_credential_and_audit(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "bearer", "tok")
        res = execute_generic_call(get_integration("api"), "POST", "/services/light/turn_on",
                                   None, {"entity_id": "light.x"}, agent="test", tool_name="write")
        assert res["status_code"] == 200
        rec = mock_upstream["requests"][-1]
        assert rec["method"] == "POST"
        assert json.loads(rec["body"]) == {"entity_id": "light.x"}
        assert rec["authorization"] == "Bearer tok"
        calls = get_integration_calls()
        assert calls[-1]["tool"] == "write"
        assert calls[-1]["method"] == "POST"

    def test_generic_delete_method(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "")
        res = execute_generic_call(get_integration("api"), "DELETE", "/things/1", agent="test")
        assert res["status_code"] == 200
        assert mock_upstream["requests"][-1]["method"] == "DELETE"

    def test_generic_ssrf_guard(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "")
        try:
            execute_generic_call(get_integration("api"), "GET", "../../evil.com", agent="test")
            assert False, "expected ProxyError"
        except ProxyError as e:
            assert e.status_code == 403

    def test_generic_invalid_method(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "")
        try:
            execute_generic_call(get_integration("api"), "BREW", "/x", agent="test")
            assert False, "expected ProxyError"
        except ProxyError as e:
            assert e.status_code == 400


def _seed_generic_tools(integration):
    create_tool(integration["id"], "read", "Read any endpoint", "GET", "",
                [{"name": "path", "type": "string", "required": True},
                 {"name": "params", "type": "json", "required": False}],
                "", read_only=True, generic=True)
    create_tool(integration["id"], "write", "Write any endpoint", "POST", "",
                [{"name": "method", "type": "string", "required": True, "default": "POST"},
                 {"name": "path", "type": "string", "required": True},
                 {"name": "params", "type": "json", "required": False},
                 {"name": "data", "type": "json", "required": False}],
                "", read_only=False, generic=True)


class TestRunToolGating:
    """The integration's gate_mode decides when mutating calls route to the
    approval queue instead of auto-running."""

    def test_gate_all_always_pending(self, auth_client, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="all")
        _seed_generic_tools(get_integration("api"))
        out = json.loads(run_tool("api", "write", {"method": "POST", "path": "/things",
                                                   "data": {"x": 1}}, "do it"))
        assert out["status"] == "pending"
        assert mock_upstream["requests"] == []

    def test_gate_destructive_benign_auto_runs(self, auth_client, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="destructive")
        _seed_generic_tools(get_integration("api"))
        out = json.loads(run_tool("api", "write", {"method": "POST", "path": "/things",
                                                   "data": {"x": 1}}, "do it"))
        assert "status" not in out
        assert mock_upstream["requests"][-1]["method"] == "POST"
        assert mock_upstream["requests"][-1]["path"] == "/things"

    def test_gate_destructive_delete_pending(self, auth_client, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="destructive")
        _seed_generic_tools(get_integration("api"))
        out = json.loads(run_tool("api", "write", {"method": "DELETE", "path": "/things/1"}, "delete it"))
        assert out["status"] == "pending"
        assert mock_upstream["requests"] == []

    def test_gate_none_auto_runs_delete(self, auth_client, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="none")
        _seed_generic_tools(get_integration("api"))
        out = json.loads(run_tool("api", "write", {"method": "DELETE", "path": "/things/1"}, "delete it"))
        assert "status" not in out
        assert mock_upstream["requests"][-1]["method"] == "DELETE"

    def test_generic_read_runs(self, auth_client, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "")
        _seed_generic_tools(get_integration("api"))
        out = json.loads(run_tool("api", "read", {"path": "/states", "params": {"group": "light"}}))
        assert mock_upstream["requests"][-1]["path"] == "/states?group=light"
        assert out["ok"] is True

    def test_missing_tool_returns_error(self, auth_client):
        create_integration("api", "https://api.local", "none", "")
        out = json.loads(run_tool("api", "nope", {}))
        assert "error" in out


class TestGenericApproval:

    def test_approve_generic_write_executes(self, auth_client, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="all")
        _seed_generic_tools(get_integration("api"))
        call_id = create_pending_call("api", "write",
                                      {"method": "POST", "path": "/things", "data": {"x": 1}}, "reason")
        r = auth_client.post(f"/api/integration-calls/{call_id}/approve")
        assert r.status_code == 200
        rec = mock_upstream["requests"][-1]
        assert rec["method"] == "POST"
        assert rec["path"] == "/things"
        assert json.loads(rec["body"]) == {"x": 1}


class TestGateModeRoundTrip:

    def test_gate_mode_create_list_update(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "api", "base_url": "https://api.local", "auth_type": "none",
            "kind": "custom", "gate_mode": "none",
        })
        row = next(i for i in auth_client.get("/api/integrations").json() if i["name"] == "api")
        assert row["gate_mode"] == "none"
        assert get_integration("api")["gate_mode"] == "none"
        r = auth_client.put("/api/integrations/api", json={"gate_mode": "all"})
        assert r.status_code == 200
        assert get_integration("api")["gate_mode"] == "all"

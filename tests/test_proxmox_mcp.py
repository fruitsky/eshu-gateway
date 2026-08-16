import json

from db.integrations import (
    create_integration,
    create_pending_call,
    create_tool,
    get_integration,
    get_integration_calls,
    get_pending_call,
    get_pending_calls,
)
from db.agent_tokens import create_agent_token
from core.integration_proxy import execute_integration_call, ProxyError


def _read_tool(name="list_nodes", method="GET", path="/nodes", params=None, read_only=True):
    return {
        "name": name,
        "enabled": True,
        "method": method,
        "path_template": path,
        "params": params or [],
        "read_only": read_only,
    }


class TestProxyForwarding:

    def test_read_call_forwards_and_injects_credential(self, mock_upstream):
        create_integration("proxmox", mock_upstream["base_url"], "header",
                           "PVEAPIToken=u!t=v", auth_header_name="Authorization")
        integration = get_integration("proxmox")
        tool = _read_tool()
        res = execute_integration_call(integration, tool, {}, agent="test")
        assert res["status_code"] == 200
        assert mock_upstream["requests"][-1]["method"] == "GET"
        assert mock_upstream["requests"][-1]["path"] == "/nodes"
        assert mock_upstream["requests"][-1]["authorization"] == "PVEAPIToken=u!t=v"
        # audit row recorded
        calls = get_integration_calls()
        assert len(calls) == 1
        assert calls[0]["integration"] == "proxmox"
        assert calls[0]["status_code"] == 200

    def test_bearer_auth(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "bearer", "sekret")
        integration = get_integration("api")
        res = execute_integration_call(integration, _read_tool(), {}, agent="test")
        assert res["status_code"] == 200
        assert mock_upstream["requests"][-1]["authorization"] == "Bearer sekret"

    def test_path_and_query_params(self, mock_upstream):
        create_integration("proxmox", mock_upstream["base_url"], "none", "")
        integration = get_integration("proxmox")
        tool = _read_tool(name="get_vm_status", path="/nodes/{node}/qemu/{vmid}/status/current",
                          params=[
                              {"name": "node", "type": "string", "required": True},
                              {"name": "vmid", "type": "integer", "required": True},
                          ])
        res = execute_integration_call(integration, tool, {"node": "pve", "vmid": 100}, agent="test")
        assert res["status_code"] == 200
        assert mock_upstream["requests"][-1]["path"] == "/nodes/pve/qemu/100/status/current"

    def test_ssrf_guard_rejects_traversal(self, mock_upstream):
        create_integration("proxmox", mock_upstream["base_url"], "none", "")
        integration = get_integration("proxmox")
        tool = _read_tool(path="/nodes/{node}", params=[{"name": "node", "type": "string", "required": True}])
        try:
            execute_integration_call(integration, tool, {"node": "../../evil.com"}, agent="test")
            assert False, "expected ProxyError"
        except ProxyError as e:
            assert e.status_code == 403

    def test_ssrf_guard_rejects_scheme_injection(self, mock_upstream):
        create_integration("proxmox", mock_upstream["base_url"], "none", "")
        integration = get_integration("proxmox")
        tool = _read_tool(path="/{node}", params=[{"name": "node", "type": "string", "required": True}])
        # A scheme-bearing value is URL-encoded, so the request stays on the
        # configured host — no SSRF, no scheme change.
        res = execute_integration_call(integration, tool, {"node": "http://evil.com"}, agent="test")
        assert res["status_code"] == 200
        assert mock_upstream["requests"][-1]["path"] == "/http%3A%2F%2Fevil.com"


class TestApprovalFlow:

    def test_mutating_pending_approve_executes(self, mock_upstream, auth_client):
        create_integration("proxmox", mock_upstream["base_url"], "header",
                           "PVEAPIToken=u!t=v", auth_header_name="Authorization")
        integration = get_integration("proxmox")
        create_tool(integration["id"], "start_vm", "Start a VM", "POST",
                    "/nodes/{node}/qemu/{vmid}/status/start",
                    [
                        {"name": "node", "type": "string", "required": True},
                        {"name": "vmid", "type": "integer", "required": True},
                    ], "", read_only=False)
        call_id = create_pending_call("proxmox", "start_vm",
                                      {"node": "pve", "vmid": 100}, "testing approval")
        assert get_pending_call(call_id)["status"] == "pending"
        # Nothing executed yet
        assert mock_upstream["requests"] == []

        r = auth_client.post(f"/api/integration-calls/{call_id}/approve")
        assert r.status_code == 200
        call = get_pending_call(call_id)
        assert call["status"] == "approved"
        # Executed against upstream with the credential
        assert mock_upstream["requests"][-1]["method"] == "POST"
        assert mock_upstream["requests"][-1]["path"] == "/nodes/pve/qemu/100/status/start"
        assert mock_upstream["requests"][-1]["authorization"] == "PVEAPIToken=u!t=v"
        # Result cached for the agent to poll
        result = json.loads(call["result"])
        assert result["status_code"] == 200

    def test_deny(self, auth_client):
        create_integration("proxmox", "http://localhost:1/api2/json", "none", "")
        call_id = create_pending_call("proxmox", "start_vm", {"vmid": 100}, "test")
        r = auth_client.post(f"/api/integration-calls/{call_id}/deny")
        assert r.status_code == 200
        assert get_pending_call(call_id)["status"] == "denied"

    def test_approve_requires_session(self, client):
        create_integration("proxmox", "http://localhost:1/api2/json", "none", "")
        call_id = create_pending_call("proxmox", "start_vm", {}, "")
        r = client.post(f"/api/integration-calls/{call_id}/approve")
        assert r.status_code == 401


class TestToolFnArgumentMarshalling:
    """Regression: the generated tool function must forward the actual argument
    values (node='pve') to the proxy, not the parameter name string ('node')."""

    def test_path_param_substituted_with_value(self, mock_upstream):
        from core.mcp_server import _build_tool_fn
        create_integration("proxmox", mock_upstream["base_url"], "none", "")
        fn = _build_tool_fn("proxmox", {
            "id": 1, "name": "list_vms", "method": "GET",
            "path_template": "/nodes/{node}/qemu",
            "params": [{"name": "node", "type": "string", "required": True}],
            "read_only": True, "enabled": True,
        })
        fn(node="pve")
        assert mock_upstream["requests"][-1]["path"] == "/nodes/pve/qemu"
        assert "/nodes/node/qemu" not in mock_upstream["requests"][-1]["path"]

    def test_query_param_substituted_with_value(self, mock_upstream):
        from core.mcp_server import _build_tool_fn
        create_integration("proxmox", mock_upstream["base_url"], "none", "")
        fn = _build_tool_fn("proxmox", {
            "id": 2, "name": "get_cluster_resources", "method": "GET",
            "path_template": "/cluster/resources",
            "params": [{"name": "type", "type": "string", "required": False}],
            "read_only": True, "enabled": True,
        })
        fn(type="vm")
        assert mock_upstream["requests"][-1]["path"] == "/cluster/resources?type=vm"

    def test_mutating_tool_stores_real_args(self, mock_upstream, auth_client):
        from core.mcp_server import _build_tool_fn
        create_integration("proxmox", mock_upstream["base_url"], "none", "")
        fn = _build_tool_fn("proxmox", {
            "id": 3, "name": "start_vm", "method": "POST",
            "path_template": "/nodes/{node}/qemu/{vmid}/status/start",
            "params": [
                {"name": "node", "type": "string", "required": True},
                {"name": "vmid", "type": "integer", "required": True},
            ],
            "read_only": False, "enabled": True,
        })
        fn(node="pve", vmid=100, reason="test")
        pending = get_pending_calls()
        assert pending and pending[0]["payload"] == {"node": "pve", "vmid": 100}


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
                        lambda req, timeout=30: _FakeResp(body))


def _proj_tool(name="list_vms", fields=None, params=None, path=None, tool_id=1):
    return {
        "id": tool_id,
        "name": name,
        "enabled": True,
        "method": "GET",
        "path_template": path or "/nodes/{node}/qemu",
        "params": params or [{"name": "node", "type": "string", "required": True}],
        "fields": fields or [],
        "read_only": True,
    }


class TestProjection:
    """Field projection trims list/dict JSON responses to the tool's `fields`
    unless the caller passes `full=True`."""

    def _integration(self):
        create_integration("proxmox", "https://pve.local/api2/json", "none", "")
        return get_integration("proxmox")

    def test_list_projection(self, monkeypatch):
        _patch_urlopen(monkeypatch,
                       '{"data":[{"vmid":100,"name":"a","status":"running","cpus":4,"blockstat":"x"}]}')
        res = execute_integration_call(self._integration(), _proj_tool(fields=["vmid", "name", "status"]),
                                       {"node": "pve"}, agent="test")
        data = json.loads(res["body"])
        assert isinstance(data, list) and len(data) == 1
        assert set(data[0].keys()) == {"vmid", "name", "status"}

    def test_dict_projection(self, monkeypatch):
        _patch_urlopen(monkeypatch,
                       '{"data":{"status":"running","qmpstatus":"running","uptime":86400,"blockstat":"x"}}')
        tool = _proj_tool(name="get_vm_status", path="/x", params=[],
                          fields=["status", "qmpstatus", "uptime"])
        res = execute_integration_call(self._integration(), tool, {}, agent="test")
        data = json.loads(res["body"])
        assert set(data.keys()) == {"status", "qmpstatus", "uptime"}

    def test_full_returns_full_object(self, monkeypatch):
        _patch_urlopen(monkeypatch,
                       '{"data":[{"vmid":100,"name":"a","status":"running","cpus":4,"blockstat":"x"}]}')
        res = execute_integration_call(self._integration(), _proj_tool(fields=["vmid"]),
                                       {"node": "pve", "full": True}, agent="test")
        data = json.loads(res["body"])
        assert data["data"][0]["blockstat"] == "x"
        assert data["data"][0]["cpus"] == 4

    def test_no_fields_passthrough(self, monkeypatch):
        _patch_urlopen(monkeypatch, '{"data":[{"vmid":1,"name":"a"}]}')
        res = execute_integration_call(self._integration(), _proj_tool(fields=[]),
                                       {"node": "pve"}, agent="test")
        assert '"vmid"' in res["body"] and '"data"' in res["body"]

    def test_non_json_passthrough(self, monkeypatch):
        _patch_urlopen(monkeypatch, "not json at all")
        res = execute_integration_call(self._integration(), _proj_tool(fields=["vmid"]),
                                       {"node": "pve"}, agent="test")
        assert res["body"] == "not json at all"

    def test_full_param_only_in_signature_when_projected(self):
        import inspect
        from core.mcp_server import _build_tool_fn
        with_fields = _build_tool_fn("proxmox", _proj_tool(fields=["vmid", "name"]))
        no_fields = _build_tool_fn("proxmox", _proj_tool(fields=[]))
        assert "full" in inspect.signature(with_fields).parameters
        assert "full" not in inspect.signature(no_fields).parameters


class TestHistorySurface:
    """Resolved mutating API calls surface as rows in the main dashboard history
    (the `requests` table), mirroring the fleet-run pattern."""

    def test_approve_surfaces_in_history(self, mock_upstream, auth_client):
        from db.requests import get_all_requests
        create_integration("proxmox", mock_upstream["base_url"], "none", "")
        integration = get_integration("proxmox")
        create_tool(integration["id"], "start_vm", "Start", "POST",
                    "/nodes/{node}/qemu/{vmid}/status/start",
                    [
                        {"name": "node", "type": "string", "required": True},
                        {"name": "vmid", "type": "integer", "required": True},
                    ], "", read_only=False)
        call_id = create_pending_call("proxmox", "start_vm",
                                      {"node": "pve", "vmid": 100}, "test reason")
        r = auth_client.post(f"/api/integration-calls/{call_id}/approve")
        assert r.status_code == 200
        rows = get_all_requests()
        match = [x for x in rows if x["status"] == "integration-approved"]
        assert len(match) == 1
        assert match[0]["target_ip"] == "proxmox"
        assert match[0]["command"] == "proxmox.start_vm(node=pve, vmid=100)"
        assert match[0]["reason"] == "test reason"

    def test_deny_surfaces_in_history(self, auth_client):
        from db.requests import get_all_requests
        create_integration("proxmox", "http://localhost:1/api2/json", "none", "")
        call_id = create_pending_call("proxmox", "start_vm", {"vmid": 100}, "test")
        r = auth_client.post(f"/api/integration-calls/{call_id}/deny")
        assert r.status_code == 200
        rows = get_all_requests()
        assert any(x["status"] == "integration-denied" for x in rows)

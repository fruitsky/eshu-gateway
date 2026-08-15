import json

from db.integrations import (
    create_integration,
    create_pending_call,
    create_tool,
    get_integration,
    get_integration_calls,
    get_pending_call,
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

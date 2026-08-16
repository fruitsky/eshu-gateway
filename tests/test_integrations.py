import json

from db.integrations import (
    create_integration,
    get_integration,
    get_integrations,
    get_tools,
)
from db.agent_tokens import get_agent_by_token, get_agent_tokens


class TestIntegrationCRUD:

    def test_create_and_list_integration_hides_secret(self, auth_client):
        r = auth_client.post("/api/integrations", json={
            "name": "proxmox",
            "base_url": "https://pve.local:8006/api2/json",
            "auth_type": "header",
            "auth_header_name": "Authorization",
            "secret": "PVEAPIToken=u!t=v",
        })
        assert r.status_code == 200
        # Stored server-side with the secret
        assert get_integration("proxmox")["secret"] == "PVEAPIToken=u!t=v"
        # The list endpoint must never return the secret
        r = auth_client.get("/api/integrations")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["name"] == "proxmox"
        assert "secret" not in rows[0]

    def test_update_keeps_secret_when_omitted(self, auth_client):
        create_integration("proxmox", "https://pve.local/api2/json", "header",
                           "SECRET", auth_header_name="Authorization")
        r = auth_client.put("/api/integrations/proxmox", json={"base_url": "https://new.local/api2/json"})
        assert r.status_code == 200
        assert get_integration("proxmox")["secret"] == "SECRET"
        assert get_integration("proxmox")["base_url"] == "https://new.local/api2/json"

    def test_update_can_change_secret(self, auth_client):
        create_integration("proxmox", "https://pve.local/api2/json", "header", "OLD")
        r = auth_client.put("/api/integrations/proxmox", json={"secret": "NEW"})
        assert r.status_code == 200
        assert get_integration("proxmox")["secret"] == "NEW"

    def test_test_endpoint_runs_read_tool(self, auth_client, mock_upstream):
        """POST /api/integrations/{name}/test runs the first enabled read-only
        tool with no required params and reports the upstream result."""
        create_integration("proxmox", mock_upstream["base_url"], "header",
                           "PVEAPIToken=u!t=v", auth_header_name="Authorization")
        auth_client.post("/api/integrations/proxmox/seed-proxmox")
        r = auth_client.post("/api/integrations/proxmox/test")
        assert r.status_code == 200
        data = r.json()
        assert data["status_code"] == 200
        assert data["error"] is None
        assert data["tool"] in ("get_cluster_resources", "list_nodes")
        # Preview is the (projected) upstream body — must parse as JSON.
        json.loads(data["preview"])

    def test_test_endpoint_requires_session(self, client):
        create_integration("proxmox", "https://pve.local/api2/json", "none", "")
        r = client.post("/api/integrations/proxmox/test")
        assert r.status_code == 401

    def test_test_endpoint_reports_connection_error(self, auth_client, mock_upstream):
        # A base_url that refuses connections should surface as a clear error,
        # not crash.
        create_integration("proxmox", "http://127.0.0.1:1/api2/json", "none", "")
        auth_client.post("/api/integrations/proxmox/seed-proxmox")
        r = auth_client.post("/api/integrations/proxmox/test")
        assert r.status_code == 200
        assert r.json()["error"] is not None

    def test_delete_integration(self, auth_client):
        create_integration("proxmox", "https://pve.local/api2/json", "none", "")
        r = auth_client.delete("/api/integrations/proxmox")
        assert r.status_code == 200
        assert get_integration("proxmox") is None

    def test_routes_require_session(self, client):
        assert client.get("/api/integrations").status_code == 401
        assert client.post("/api/integrations", json={"name": "x", "base_url": "http://x"}).status_code == 401
        assert client.get("/api/agents").status_code == 401


class TestAgentTokens:

    def test_create_shows_token_once(self, auth_client):
        r = auth_client.post("/api/agents", json={"name": "hermes"})
        assert r.status_code == 200
        data = r.json()
        assert "token" in data and len(data["token"]) >= 32
        # Raw token resolves to the agent; only the hash is persisted
        assert get_agent_by_token(data["token"])["name"] == "hermes"
        # List returns no raw token
        agents = auth_client.get("/api/agents").json()
        assert len(agents) == 1
        assert "token" not in agents[0]
        assert "token_hash" not in agents[0]

    def test_revoke(self, auth_client):
        token = auth_client.post("/api/agents", json={"name": "hermes"}).json()["token"]
        agent_id = get_agent_by_token(token)["id"]
        assert auth_client.delete(f"/api/agents/{agent_id}").status_code == 200
        assert get_agent_by_token(token) is None

    def test_mcp_requires_agent_token(self, client):
        r = client.get("/mcp")
        assert r.status_code == 401


class TestProxmoxSeed:

    def test_seed_proxmox_populates_tools(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "proxmox",
            "base_url": "https://pve.local:8006/api2/json",
            "auth_type": "header",
            "auth_header_name": "Authorization",
            "secret": "tok",
        })
        r = auth_client.post("/api/integrations/proxmox/seed-proxmox")
        assert r.status_code == 200
        data = r.json()
        assert data["created"] == 16
        tools = auth_client.get("/api/integrations/proxmox/tools").json()
        assert len(tools) == 16
        names = {t["name"] for t in tools}
        assert "list_vms" in names
        assert "start_vm" in names
        # Seeding again is idempotent (updates in place)
        r2 = auth_client.post("/api/integrations/proxmox/seed-proxmox")
        assert r2.json()["created"] == 0
        assert r2.json()["updated"] == 16

    def test_toggle_tool(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "proxmox", "base_url": "https://pve.local/api2/json",
            "auth_type": "none", "secret": "",
        })
        auth_client.post("/api/integrations/proxmox/seed-proxmox")
        tools = auth_client.get("/api/integrations/proxmox/tools").json()
        tid = next(t["id"] for t in tools if t["name"] == "list_vms")
        r = auth_client.post(f"/api/integrations/proxmox/tools/{tid}/toggle", json={"enabled": False})
        assert r.status_code == 200
        tools = auth_client.get("/api/integrations/proxmox/tools").json()
        assert next(t for t in tools if t["id"] == tid)["enabled"] == 0


class TestMcpSettings:

    def test_get_default_is_empty(self, auth_client):
        r = auth_client.get("/api/mcp-settings")
        assert r.status_code == 200
        assert r.json()["allowed_hosts"] == ""

    def test_set_reflects_in_transport_allowlist(self, auth_client):
        from db.misc import get_mcp_allowed_hosts
        from core.mcp_server import mcp, refresh_mcp_allowed_hosts

        r = auth_client.put("/api/mcp-settings", json={"allowed_hosts": "eshu.local.example.com, 192.168.1.114"})
        assert r.status_code == 200
        assert get_mcp_allowed_hosts() == "eshu.local.example.com, 192.168.1.114"

        refresh_mcp_allowed_hosts()
        hosts = mcp.settings.transport_security.allowed_hosts
        # exact (no-port) form for the HTTPS/proxy host, wildcard for IP-with-port
        assert "eshu.local.example.com" in hosts
        assert "eshu.local.example.com:*" in hosts
        assert "192.168.1.114:*" in hosts
        # loopback defaults preserved
        assert "127.0.0.1:*" in hosts
        assert "localhost:*" in hosts

    def test_clear_sets_empty(self, auth_client):
        auth_client.put("/api/mcp-settings", json={"allowed_hosts": "example.com"})
        r = auth_client.put("/api/mcp-settings", json={"allowed_hosts": ""})
        assert r.status_code == 200
        assert auth_client.get("/api/mcp-settings").json()["allowed_hosts"] == ""

    def test_requires_session(self, client):
        assert client.get("/api/mcp-settings").status_code == 401
        assert client.put("/api/mcp-settings", json={"allowed_hosts": "x"}).status_code == 401


class TestMcpToolNaming:

    def test_tools_namespaced_by_integration(self, auth_client):
        import asyncio
        from core.mcp_server import mcp, refresh_mcp_tools
        auth_client.post("/api/integrations", json={
            "name": "proxmox", "base_url": "https://pve.local/api2/json",
            "auth_type": "none", "secret": "",
        })
        auth_client.post("/api/integrations/proxmox/seed-proxmox")
        refresh_mcp_tools()

        async def _names():
            tools = await mcp.list_tools()
            return {t.name for t in tools}
        names = asyncio.run(_names())

        assert "proxmox_list_nodes" in names
        assert "proxmox_start_vm" in names
        assert "check_approval" in names
        # Un-namespaced short names must not leak into the MCP surface
        assert "list_nodes" not in names
        assert "start_vm" not in names


class TestProxyScheme:

    def test_mcp_redirect_uses_forwarded_proto(self, auth_client):
        """The /mcp trailing-slash redirect must use the X-Forwarded-Proto
        scheme so a proxy client (e.g. Hermes) keeps its Authorization header
        on the follow-up request (a scheme change would drop it)."""
        from db.agent_tokens import create_agent_token
        token, _ = create_agent_token("hermes")
        r = auth_client.get(
            "/mcp",
            headers={"Authorization": "Bearer " + token, "X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert r.status_code == 307
        assert r.headers["location"].startswith("https://")

    def test_mcp_redirect_defaults_to_http_without_forwarded_proto(self, auth_client):
        from db.agent_tokens import create_agent_token
        token, _ = create_agent_token("hermes")
        r = auth_client.get(
            "/mcp",
            headers={"Authorization": "Bearer " + token},
            follow_redirects=False,
        )
        assert r.status_code == 307
        assert r.headers["location"].startswith("http://")

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

import json

from db.integrations import (
    create_integration,
    get_integration,
    get_integrations,
    get_tools,
    update_integration,
)
from db.agent_tokens import get_agent_by_token, get_agent_tokens
from core.seeds import seed_tool_names


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
                           "PVEAPIToken=u!t=v", auth_header_name="Authorization", kind="proxmox")
        auth_client.post("/api/integrations/proxmox/seed")
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
        create_integration("proxmox", "http://127.0.0.1:1/api2/json", "none", "", kind="proxmox")
        auth_client.post("/api/integrations/proxmox/seed")
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
            "kind": "proxmox",
        })
        r = auth_client.post("/api/integrations/proxmox/seed")
        assert r.status_code == 200
        data = r.json()
        assert data["created"] == 18  # 16 curated + generic read/write
        tools = auth_client.get("/api/integrations/proxmox/tools").json()
        assert len(tools) == 18
        names = {t["name"] for t in tools}
        assert "list_vms" in names
        assert "start_vm" in names
        assert {"read", "write"} <= names
        # Seeding again is idempotent (updates in place)
        r2 = auth_client.post("/api/integrations/proxmox/seed")
        assert r2.json()["created"] == 0
        assert r2.json()["updated"] == 18

    def test_toggle_tool(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "proxmox", "base_url": "https://pve.local/api2/json",
            "auth_type": "none", "secret": "", "kind": "proxmox",
        })
        auth_client.post("/api/integrations/proxmox/seed")
        tools = auth_client.get("/api/integrations/proxmox/tools").json()
        tid = next(t["id"] for t in tools if t["name"] == "list_vms")
        r = auth_client.post(f"/api/integrations/proxmox/tools/{tid}/toggle", json={"enabled": False})
        assert r.status_code == 200
        tools = auth_client.get("/api/integrations/proxmox/tools").json()
        assert next(t for t in tools if t["id"] == tid)["enabled"] == 0


class TestKindMigration:
    """The one-time backfill infers 'proxmox' for integrations that predate the
    kind column but already carry known Proxmox tool names."""

    def test_backfills_proxmox_kind_from_tools(self, auth_client):
        from db.integrations import create_tool, get_integration
        from db.core import init_db
        create_integration("proxmox", "https://pve.local/api2/json", "none", "")
        assert get_integration("proxmox")["kind"] == "custom"
        integration = get_integration("proxmox")
        create_tool(integration["id"], "list_nodes", "List nodes", "GET", "/nodes", [], "", True)
        init_db()  # re-run migrations, as on an existing install
        assert get_integration("proxmox")["kind"] == "proxmox"

    def test_custom_integration_not_backfilled(self, auth_client):
        from db.integrations import get_integration
        from db.core import init_db
        create_integration("custom", "https://x.local/api", "none", "")
        init_db()
        assert get_integration("custom")["kind"] == "custom"


class TestReseed:
    """Automatic re-seed on startup applies each integration's seed catalog,
    updates changed fields in place, and preserves enable/disable state."""

    def test_reseed_creates_tools_for_known_kinds(self):
        from core.seeds import reseed_all_integrations
        from db.integrations import get_tools
        create_integration("proxmox", "https://pve.local/api2/json", "none", "", kind="proxmox")
        create_integration("ha", "https://ha.local/api", "bearer", "tok", kind="ha")
        create_integration("custom", "https://x.local/api", "none", "")
        reseed_all_integrations()
        assert len(get_tools(1)) == 18  # proxmox + generic read/write
        assert len(get_tools(2)) == 12  # ha + generic read/write/ws_read/ws_write
        assert {t["name"] for t in get_tools(3)} == {'read', 'write'}  # custom: generic floor only

    def test_reseed_updates_changed_fields(self):
        from core.seeds import reseed_all_integrations
        from db.integrations import get_tools, update_tool
        create_integration("ha", "https://ha.local/api", "bearer", "tok", kind="ha")
        reseed_all_integrations()
        tools = get_tools(1)
        le = next(t for t in tools if t["name"] == "list_entities")
        assert le["search_field"] == "entity_id"
        # Simulate a stale tool definition, then re-seed restores it
        update_tool(le["id"], search_field="")
        reseed_all_integrations()
        tools = get_tools(1)
        le = next(t for t in tools if t["name"] == "list_entities")
        assert le["search_field"] == "entity_id"

    def test_reseed_preserves_enabled_state(self):
        from core.seeds import reseed_all_integrations
        from db.integrations import get_tools, set_tool_enabled
        create_integration("proxmox", "https://pve.local/api2/json", "none", "", kind="proxmox")
        reseed_all_integrations()
        tools = get_tools(1)
        tid = next(t["id"] for t in tools if t["name"] == "start_vm")
        set_tool_enabled(tid, False)
        reseed_all_integrations()
        tools = get_tools(1)
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
            "auth_type": "none", "secret": "", "kind": "proxmox",
        })
        auth_client.post("/api/integrations/proxmox/seed")
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


class TestIntegrationCallSearch:

    def test_db_search_filters(self):
        from db.integrations import get_integration_calls, record_integration_call
        record_integration_call("proxmox", "list_nodes", "mcp", "GET", "/nodes", 200, 12, "ok", 10, 0, "ok")
        record_integration_call("ha", "call_service", "mcp", "POST", "/services/light/turn_on", 200, 20, "ok", 12, 0, "ok")
        assert get_integration_calls()["total"] == 2
        assert len(get_integration_calls()["rows"]) == 2
        assert get_integration_calls(search="proxmox")["total"] == 1
        assert get_integration_calls(search="proxmox")["rows"][0]["integration"] == "proxmox"
        assert get_integration_calls(search="turn_on")["rows"][0]["tool"] == "call_service"
        assert get_integration_calls(search="POST")["rows"][0]["method"] == "POST"
        assert get_integration_calls(search="nope")["total"] == 0

    def test_db_range_and_pagination(self):
        import time
        from db.core import db_conn
        from db.integrations import get_integration_calls
        base = int(time.time()) - 1000
        with db_conn() as conn:
            cur = conn.cursor()
            for i, tool in enumerate(["a", "b", "c"]):
                cur.execute('''
                    INSERT INTO integration_calls
                        (integration, tool, agent, method, path, status_code, latency_ms,
                         response_summary, response_bytes, truncated, outcome, created_at)
                    VALUES (?, ?, 'mcp', 'GET', '/1', 200, 1, 'ok', 1, 0, 'ok', ?)
                ''', ('proxmox', tool, base + i))
            conn.commit()
        res = get_integration_calls(start=base, end=base + 1)
        assert res["total"] == 1 and res["rows"][0]["tool"] == "a"
        res = get_integration_calls(limit=2, offset=1)
        assert res["total"] == 3 and len(res["rows"]) == 2
        assert res["rows"][0]["tool"] == "b" and res["rows"][1]["tool"] == "a"

    def test_endpoint_search(self, auth_client):
        from db.integrations import record_integration_call
        record_integration_call("proxmox", "list_nodes", "mcp", "GET", "/nodes", 200, 12, "ok", 10, 0, "ok")
        record_integration_call("ha", "list_entities", "mcp", "GET", "/states", 200, 5, "ok", 100, 0, "ok")
        assert auth_client.get("/api/integration-calls").json()["total"] == 2
        r = auth_client.get("/api/integration-calls?search=ha")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1 and data["rows"][0]["integration"] == "ha"


class TestSecretSuffix:

    def test_list_exposes_suffix_not_secret(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "pulse", "base_url": "http://x/api", "auth_type": "header",
            "auth_header_name": "X-API-Token", "secret": "tok1234567890abcdef", "kind": "pulse",
        })
        row = next(x for x in auth_client.get("/api/integrations").json() if x["name"] == "pulse")
        assert "secret" not in row
        assert row["secret_suffix"] == "…cdef"

    def test_empty_secret_empty_suffix(self, auth_client):
        create_integration("nokeys", "http://x/api", "none", "")
        row = next(x for x in auth_client.get("/api/integrations").json() if x["name"] == "nokeys")
        assert row["secret_suffix"] == ""

    def test_short_secret_shown_full(self, auth_client):
        create_integration("short", "http://x/api", "none", "ab")
        row = next(x for x in auth_client.get("/api/integrations").json() if x["name"] == "short")
        assert row["secret_suffix"] == "ab"

    def test_client_secret_suffix(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "omada", "base_url": "https://x/openapi/v1/acct", "auth_type": "oauth2",
            "client_id": "cid", "client_secret": "superSecretClient", "token_url": "https://x/token",
        })
        row = next(x for x in auth_client.get("/api/integrations").json() if x["name"] == "omada")
        assert "client_secret" not in row
        assert row["client_secret_suffix"] == "…ient"


class TestSeedToolNames:

    def test_pulse_includes_curated_and_generic(self):
        names = seed_tool_names("pulse")
        assert "health" in names and "read" in names and "write" in names

    def test_jellyfin_excludes_generic(self):
        names = seed_tool_names("jellyfin")
        assert "system_info" in names
        assert "read" not in names and "write" not in names

    def test_ha_includes_ws_generic(self):
        names = seed_tool_names("ha")
        assert "ws_read" in names and "ws_write" in names

    def test_unknown_kind_generic_only(self):
        names = seed_tool_names("custom")
        assert "read" in names and "write" in names


class TestSeededAnnotation:

    def test_tools_list_marks_seeded(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "pulse", "base_url": "http://x/api", "auth_type": "none", "kind": "pulse",
        })
        assert auth_client.post("/api/integrations/pulse/seed").status_code == 200
        auth_client.post("/api/integrations/pulse/tools", json={
            "name": "my_custom", "method": "GET", "path_template": "/x", "read_only": True,
        })
        tools = auth_client.get("/api/integrations/pulse/tools").json()
        by_name = {t["name"]: t for t in tools}
        assert by_name["health"]["seeded"] is True
        assert by_name["read"]["seeded"] is True
        assert by_name["my_custom"]["seeded"] is False


class TestBulkToggleTools:

    def _pulse(self, auth_client):
        auth_client.post("/api/integrations", json={
            "name": "pulse", "base_url": "http://x/api", "auth_type": "none", "kind": "pulse",
        })
        assert auth_client.post("/api/integrations/pulse/seed").status_code == 200

    def test_disable_all_then_enable_all(self, auth_client):
        self._pulse(auth_client)
        tools = auth_client.get("/api/integrations/pulse/tools").json()
        assert all(t["enabled"] for t in tools)

        r = auth_client.post("/api/integrations/pulse/tools/bulk", json={"enabled": False})
        assert r.status_code == 200 and r.json()["updated"] == len(tools)
        tools = auth_client.get("/api/integrations/pulse/tools").json()
        assert all(not t["enabled"] for t in tools)

        r = auth_client.post("/api/integrations/pulse/tools/bulk", json={"enabled": True})
        assert r.status_code == 200
        tools = auth_client.get("/api/integrations/pulse/tools").json()
        assert all(t["enabled"] for t in tools)

    def test_bulk_requires_session(self, client):
        r = client.post("/api/integrations/pulse/tools/bulk", json={"enabled": False})
        assert r.status_code == 401

    def test_bulk_unknown_integration_404(self, auth_client):
        r = auth_client.post("/api/integrations/nope/tools/bulk", json={"enabled": False})
        assert r.status_code == 404


class TestMcpMode:

    def test_defaults_to_joined(self, temp_db):
        create_integration("x", "http://x/api", "none", "")
        assert get_integration("x")["mcp_mode"] == "joined"

    def test_create_roundtrip(self, temp_db):
        create_integration("x", "http://x/api", "none", "", mcp_mode="standalone")
        assert get_integration("x")["mcp_mode"] == "standalone"
        row = next(i for i in get_integrations() if i["name"] == "x")
        assert row["mcp_mode"] == "standalone"

    def test_update_roundtrip(self, temp_db):
        create_integration("x", "http://x/api", "none", "")
        update_integration("x", mcp_mode="both")
        assert get_integration("x")["mcp_mode"] == "both"
        update_integration("x", mcp_mode="standalone")
        assert get_integration("x")["mcp_mode"] == "standalone"

import asyncio
import json
import threading

import pytest

from db.integrations import create_integration, get_integration
from core.ha_ws import ha_ws_request
from core.integration_proxy import ProxyError

ENTITIES = [
    {"entity_id": "update.smoke_firmware", "name": "Smoke Detector Firmware", "platform": "mqtt",
     "disabled_by": None, "device_id": "dev-smoke", "config_entry_id": "ce1", "area_id": "a1"},
    {"entity_id": "sensor.smoke_rssi", "name": "Smoke Detector RSSI", "platform": "mqtt",
     "disabled_by": "integration", "device_id": "dev-smoke", "config_entry_id": "ce1", "area_id": "a1"},
    {"entity_id": "sensor.smoke_lqi", "name": "Smoke Detector LQI", "platform": "mqtt",
     "disabled_by": "integration", "device_id": "dev-smoke", "config_entry_id": "ce1", "area_id": "a1"},
]

DEVICES = [
    {"id": "dev-smoke", "name": "Smoke Detector", "name_by_user": None, "manufacturer": "Tuya",
     "model": "_TZE284_gyzlwu5q TS0601",
     "identifiers": [["zigbee", "a4:c1:38:53:2b:6a:d6:5f"]],
     "connections": [["zigbee", "a4:c1:38:53:2b:6a:d6:5f"]],
     "via_device_id": "dev-dongle", "entry_type": "device", "area_id": "a1"},
    {"id": "dev-dongle", "name": "SONOFF Dongle Plus MG24", "name_by_user": None, "manufacturer": "SONOFF",
     "model": "ZBDongle-P", "identifiers": [["zigbee", "dongle-mac"]], "connections": [],
     "via_device_id": None, "entry_type": None, "area_id": "a1"},
]


@pytest.fixture
def ha_ws_server():
    """A minimal HA-style WebSocket server for the ws:// base_url (no TLS)."""
    import websockets

    loop = asyncio.new_event_loop()

    async def _start():
        async def handler(ws):
            await ws.send(json.dumps({"type": "auth_required", "ha_version": "2026.7.3"}))
            auth = json.loads(await ws.recv())
            if auth.get("access_token") == "bad-token":
                await ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
                return
            await ws.send(json.dumps({"type": "auth_ok", "ha_version": "2026.7.3"}))
            while True:
                msg = json.loads(await ws.recv())
                typ = msg.get("type")
                if typ == "config/entity_registry/list":
                    result = ENTITIES
                elif typ == "config/device_registry/list":
                    result = DEVICES
                else:
                    await ws.send(json.dumps({"id": msg["id"], "type": "result", "success": False,
                                              "error": {"code": "unknown", "message": "nope"}}))
                    continue
                await ws.send(json.dumps({"id": msg["id"], "type": "result", "success": True, "result": result}))

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        return server, port

    server, port = loop.run_until_complete(_start())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield {"base_url": f"http://127.0.0.1:{port}/api", "port": port}

    async def _stop():
        server.close()
        await server.wait_closed()
        loop.stop()

    loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_stop()))
    thread.join(timeout=3)


class TestHaWsRequest:

    def test_entity_registry_list(self, ha_ws_server):
        create_integration("ha", ha_ws_server["base_url"], "bearer", "tok", kind="ha")
        res = ha_ws_request(get_integration("ha"), "config/entity_registry/list", {})
        assert len(res) == 3
        assert res[1]["disabled_by"] == "integration"

    def test_device_registry_list(self, ha_ws_server):
        create_integration("ha", ha_ws_server["base_url"], "bearer", "tok", kind="ha")
        res = ha_ws_request(get_integration("ha"), "config/device_registry/list", {})
        assert res[0]["model"] == "_TZE284_gyzlwu5q TS0601"
        assert res[0]["via_device_id"] == "dev-dongle"

    def test_auth_invalid_maps_to_401(self, ha_ws_server):
        create_integration("ha", ha_ws_server["base_url"], "bearer", "bad-token", kind="ha")
        try:
            ha_ws_request(get_integration("ha"), "config/entity_registry/list", {})
            assert False, "expected ProxyError"
        except ProxyError as e:
            assert e.status_code == 401
            assert "auth_invalid" in e.message

    def test_missing_secret(self):
        create_integration("ha", "https://ha.local/api", "bearer", "", kind="ha")
        try:
            ha_ws_request(get_integration("ha"), "config/entity_registry/list", {})
            assert False, "expected ProxyError"
        except ProxyError as e:
            assert e.status_code == 500


class TestHaWsTool:
    """The generated MCP tool functions delegate to core.tool_runner.run_tool,
    which resolves the tool from the DB — so these seed real DB tool rows and
    apply the same client-side shaping as HTTP tools."""

    def _seed(self, ha_ws_server, name, fields, transport, filter_fields=None, search_field="", read_only=True, path_template=""):
        from db.integrations import create_tool, get_tool
        create_integration("ha", ha_ws_server["base_url"], "bearer", "tok", kind="ha")
        integration = get_integration("ha")
        create_tool(integration["id"], name, "desc", "GET", path_template, [],
                    "", read_only=read_only, fields=fields, search_field=search_field,
                    transport=transport, filter_fields=filter_fields)
        return get_tool("ha", name)

    def test_registry_tool_with_device_filter(self, ha_ws_server):
        from core.mcp_server import _build_tool_fn
        tool = self._seed(ha_ws_server, "list_entity_registry",
                          ["entity_id", "name", "disabled_by", "device_id"],
                          "ws", filter_fields=["device_id"], search_field="entity_id",
                          path_template="config/entity_registry/list")
        fn = _build_tool_fn("ha", tool)
        out = json.loads(fn(device_id="dev-smoke"))
        assert [e["entity_id"] for e in out] == ["update.smoke_firmware", "sensor.smoke_rssi", "sensor.smoke_lqi"]
        assert out[1]["disabled_by"] == "integration"

    def test_device_registry_tool_search(self, ha_ws_server):
        from core.mcp_server import _build_tool_fn
        tool = self._seed(ha_ws_server, "list_device_registry",
                          ["id", "name", "model", "via_device_id"], "ws", search_field="name",
                          path_template="config/device_registry/list")
        fn = _build_tool_fn("ha", tool)
        out = json.loads(fn(search="smoke"))
        assert len(out) == 1
        assert out[0]["model"] == "_TZE284_gyzlwu5q TS0601"
        assert out[0]["via_device_id"] == "dev-dongle"

    def test_ws_tool_signature_exposes_filter(self):
        import inspect
        from core.mcp_server import _build_tool_fn
        fn = _build_tool_fn("ha", {
            "id": 9, "name": "list_entity_registry", "enabled": True, "read_only": True,
            "method": "GET", "path_template": "config/entity_registry/list", "params": [],
            "fields": ["entity_id", "name", "disabled_by", "device_id"],
            "search_field": "entity_id", "filter_fields": ["device_id"], "transport": "ws",
        })
        params = list(inspect.signature(fn).parameters)
        assert "device_id" in params
        assert "search" in params and "limit" in params and "full" in params

    def test_full_skips_shaping(self, ha_ws_server):
        from core.mcp_server import _build_tool_fn
        tool = self._seed(ha_ws_server, "list_entity_registry",
                          ["entity_id", "name", "disabled_by", "device_id"],
                          "ws", filter_fields=["device_id"], search_field="entity_id",
                          path_template="config/entity_registry/list")
        fn = _build_tool_fn("ha", tool)
        out = json.loads(fn(full=True))
        assert len(out) == 3  # no device_id filter applied


class TestExactFilter:
    """The `filter_fields` exact-match shaping used by the registry device_id
    killer feature."""

    def test_apply_shaping_exact_filter(self):
        from core.integration_proxy import _apply_shaping
        body = json.dumps([{"device_id": "a", "name": "X"}, {"device_id": "b", "name": "Y"}])
        tool = {"fields": ["name"], "search_field": "", "filter_fields": ["device_id"]}
        out = json.loads(_apply_shaping(body, tool, {"device_id": "b"}))
        assert out == [{"name": "Y"}]

    def test_filter_only_tool_no_fields(self):
        from core.integration_proxy import _apply_shaping
        body = json.dumps([{"device_id": "a", "x": 1}, {"device_id": "b", "x": 2}])
        tool = {"fields": [], "search_field": "", "filter_fields": ["device_id"]}
        out = json.loads(_apply_shaping(body, tool, {"device_id": "b"}))
        assert out == [{"device_id": "b", "x": 2}]

    def test_filter_and_search_compose(self):
        from core.integration_proxy import _apply_shaping
        body = json.dumps([
            {"device_id": "a", "name": "Alpha"},
            {"device_id": "b", "name": "Beta"},
            {"device_id": "b", "name": "Gamma"},
        ])
        tool = {"fields": ["name"], "search_field": "name", "filter_fields": ["device_id"]}
        out = json.loads(_apply_shaping(body, tool, {"device_id": "b", "search": "beta"}))
        assert out == [{"name": "Beta"}]

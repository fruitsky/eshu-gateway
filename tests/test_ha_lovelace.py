"""Lovelace dashboard handler tests.

Exercises the curated `lovelace_*` handlers end to end against a stateful
mock HA WebSocket server (dashboards collection + config store), covering the
summary projection, the safe patch/save path, create/update/delete, and the
gating + dry-run rules.
"""
import asyncio
import json
import threading

import pytest

from db.integrations import create_integration, get_integration
from core.ha_seed import seed_ha_tools
from core.tool_handlers import run_handler, _hash
from core.tool_runner import run_tool


def _make_state():
    return {
        "dashboards": [
            {"id": "water_monitor", "icon": "mdi:water", "title": "Water",
             "url_path": "water-monitor", "mode": "storage",
             "show_in_sidebar": True, "require_admin": False},
            {"id": None, "title": "Dwains Dashboard", "url_path": "dwains-dashboard",
             "mode": "yaml",
             "filename": "custom_components/dwains_dashboard/lovelace/ui-lovelace.yaml",
             "show_in_sidebar": True, "require_admin": False},
        ],
        "configs": {
            "water-monitor": {"title": "Water", "views": [{
                "path": "water", "title": "Consumption", "cards": [
                    {"type": "statistics-graph", "title": "Daily water use",
                     "entities": ["thames_water:thameswater_consumption"]},
                    {"type": "entities", "entities": ["sensor.thames_water_meter"]},
                    {"type": "picture-elements"},
                    {"type": "custom:card"},
                    {"type": "markdown", "content": "hello"},
                ]}]},
            "dwains-dashboard": {"views": []},
        },
    }


@pytest.fixture
def lovelace_server():
    """A stateful HA-style WS server for the lovelace commands."""
    import websockets

    loop = asyncio.new_event_loop()
    state = _make_state()

    async def _start():
        async def handler(ws):
            await ws.send(json.dumps({"type": "auth_required", "ha_version": "2026.9.3"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "auth_ok", "ha_version": "2026.9.3"}))

            async def ok(rid, result):
                await ws.send(json.dumps({"id": rid, "type": "result",
                                          "success": True, "result": result}))

            async def err(rid, code, message):
                await ws.send(json.dumps({"id": rid, "type": "result", "success": False,
                                          "error": {"code": code, "message": message}}))

            while True:
                msg = json.loads(await ws.recv())
                rid = msg.get("id")
                typ = msg.get("type")
                if typ == "lovelace/dashboards/list":
                    await ok(rid, state["dashboards"])
                elif typ == "lovelace/dashboards/create":
                    url_path = msg.get("url_path")
                    if not msg.get("allow_single_word") and "-" not in (url_path or ""):
                        await err(rid, "invalid_format", "Url path needs to contain a hyphen (-)")
                        continue
                    if any(d.get("url_path") == url_path for d in state["dashboards"]):
                        await err(rid, "invalid_format", "url_already_exists")
                        continue
                    item = {"id": url_path.replace("-", "_"), "url_path": url_path,
                            "title": msg.get("title"), "mode": "storage",
                            "show_in_sidebar": msg.get("show_in_sidebar", True),
                            "require_admin": msg.get("require_admin", False)}
                    if msg.get("icon"):
                        item["icon"] = msg["icon"]
                    state["dashboards"].append(item)
                    await ok(rid, item)
                elif typ == "lovelace/dashboards/update":
                    item = next((d for d in state["dashboards"]
                                 if d.get("id") == msg.get("dashboard_id")), None)
                    if item is None:
                        await err(rid, "not_found",
                                  "Unable to find dashboard_id %s" % msg.get("dashboard_id"))
                        continue
                    for k in ("title", "icon", "show_in_sidebar", "require_admin"):
                        if k in msg:
                            item[k] = msg[k]
                    await ok(rid, item)
                elif typ == "lovelace/dashboards/delete":
                    idx = next((i for i, d in enumerate(state["dashboards"])
                                if d.get("id") == msg.get("dashboard_id")), None)
                    if idx is None:
                        await err(rid, "not_found",
                                  "Unable to find dashboard_id %s" % msg.get("dashboard_id"))
                        continue
                    item = state["dashboards"].pop(idx)
                    state["configs"].pop(item.get("url_path"), None)
                    await ok(rid, None)
                elif typ == "lovelace/config":
                    url_path = msg.get("url_path")
                    if url_path is None:
                        if None in state["configs"]:
                            await ok(rid, state["configs"][None])
                        else:
                            await err(rid, "config_not_found", "No config found.")
                    elif url_path in state["configs"]:
                        await ok(rid, state["configs"][url_path])
                    elif any(d.get("url_path") == url_path for d in state["dashboards"]):
                        # Known dashboard that has never been saved -> HA's
                        # StorageLovelaceConfig raises ConfigNotFound.
                        await err(rid, "config_not_found", "No config found.")
                    else:
                        await err(rid, "config_not_found",
                                  "Unknown config specified: %s" % url_path)
                elif typ == "lovelace/config/save":
                    state["configs"][msg.get("url_path")] = msg.get("config")
                    await ok(rid, None)
                elif typ == "lovelace/config/delete":
                    state["configs"].pop(msg.get("url_path"), None)
                    await ok(rid, None)
                else:
                    await err(rid, "unknown_command", "nope")

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        return server, port

    server, port = loop.run_until_complete(_start())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield {"base_url": f"http://127.0.0.1:{port}/api", "state": state}

    async def _stop():
        server.close()
        await server.wait_closed()
        loop.stop()

    loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_stop()))
    thread.join(timeout=3)


def _seed(server):
    create_integration("ha", server["base_url"], "bearer", "tok", kind="ha")
    integration = get_integration("ha")
    seed_ha_tools(integration["id"])
    return integration


def _tool(name):
    from db.integrations import get_tool
    return get_tool("ha", name)


class TestLovelaceReads:

    def test_inventory_compact(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_dashboards", {}))
        assert out[0]["dashboard_id"] == "water_monitor"
        assert out[0]["url_path"] == "water-monitor"
        assert "filename" not in out[0]
        assert out[1]["mode"] == "yaml" and out[1]["filename"].endswith(".yaml")

    def test_summary_collects_statistic_targets(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_dashboard", {"url_path": "water-monitor"}))
        assert out["view_count"] == 1
        assert out["top_level_keys"] == ["title", "views"]
        view = out["views"][0]
        assert view["card_count"] == 5
        assert view["cards"][0]["targets"] == ["thames_water:thameswater_consumption"]
        assert view["cards"][1]["targets"] == ["sensor.thames_water_meter"]
        # markdown cards carry a first-line preview so index edits are unambiguous
        assert view["cards"][4]["preview"] == "hello"

    def test_view_filter(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_dashboard",
                                  {"url_path": "water-monitor", "view": "water"}))
        assert len(out["views"]) == 1
        out2 = json.loads(run_tool("ha", "lovelace_dashboard",
                                   {"url_path": "water-monitor", "view": 5}))
        assert out2["views"] == []

    def test_full_includes_size(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_dashboard",
                                  {"url_path": "water-monitor", "full": True}))
        assert out["truncated"] is False
        assert out["size_bytes"] > 0 and "config" in out

    def test_full_truncates(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_dashboard",
                                  {"url_path": "water-monitor", "full": True, "max_bytes": 10}))
        assert out["truncated"] is True and out["config"] is None

    def test_default_no_config_is_empty_summary(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_dashboard", {}))
        assert "error" not in out
        assert out["view_count"] == 0 and out["views"] == []

    def test_unknown_url_path_is_dashboard_not_found(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_dashboard", {"url_path": "nope-nope"}))
        assert out["error"] == "ha_dashboard_not_found"

    def test_default_untargetable(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_dashboard", {"url_path": "lovelace"}))
        assert out["error"] == "ha_default_dashboard_untargetable"


class TestLovelaceCreateUpdateDelete:

    def test_create_hyphen_refused(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_create_dashboard",
                                  {"title": "T", "url_path": "hermestest"}, "make it"))
        assert out["error"] == "ha_dashboard_url_invalid"

    def test_create_duplicate(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_create_dashboard",
                                  {"title": "T", "url_path": "water-monitor"}, "make it"))
        assert out["error"] == "ha_dashboard_url_exists"
        assert "water_monitor" in out["message"]

    def test_create_success_verified(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_create_dashboard",
                                  {"title": "Test", "url_path": "hermes-test",
                                   "icon": "mdi:test-tube"}, "make it"))
        assert out["dashboard_id"] == "hermes_test"
        assert out["verified"] is True
        assert any(d["url_path"] == "hermes-test" for d in lovelace_server["state"]["dashboards"])

    def test_update_by_url_path_resolves_id(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_update_dashboard",
                                  {"url_path": "water-monitor", "title": "Water usage"}, "rename"))
        assert out["dashboard_id"] == "water_monitor"
        assert out["title"] == "Water usage"

    def test_update_yaml_refused(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_update_dashboard",
                                  {"url_path": "dwains-dashboard", "title": "x"}, "rename"))
        assert out["error"] == "ha_dashboard_yaml_readonly"

    def test_delete_removes_dashboard_and_config(self, lovelace_server):
        _seed(lovelace_server)
        tool = _tool("lovelace_delete_dashboard")
        out = json.loads(run_handler("ha_lovelace_delete_dashboard",
                                     get_integration("ha"), tool, {"url_path": "water-monitor"}))
        assert out["deleted"] is True and out["verified"] is True
        assert "water-monitor" not in lovelace_server["state"]["configs"]

    def test_delete_yaml_refused(self, lovelace_server):
        _seed(lovelace_server)
        tool = _tool("lovelace_delete_dashboard")
        out = json.loads(run_handler("ha_lovelace_delete_dashboard",
                                     get_integration("ha"), tool,
                                     {"url_path": "dwains-dashboard"}))
        assert out["error"] == "ha_dashboard_yaml_readonly"


class TestLovelaceSaveConfig:

    def test_dry_run_reports_diff_without_writing(self, lovelace_server):
        _seed(lovelace_server)
        before = json.dumps(lovelace_server["state"]["configs"]["water-monitor"], sort_keys=True)
        out = json.loads(run_tool("ha", "lovelace_save_config", {
            "url_path": "water-monitor", "dry_run": True,
            "ops": [{"op": "append_card", "view": "water", "card": {"type": "markdown"}}]}))
        assert out["dry_run"] is True and out["changed"] is True
        assert out["diff"]["cards_added"] == 1
        # a pure append at the end is not also reported as a changed card
        assert out["diff"]["cards_changed"] == 0
        after = json.dumps(lovelace_server["state"]["configs"]["water-monitor"], sort_keys=True)
        assert before == after  # nothing written

    def test_save_ops_verified_and_preserves_title(self, lovelace_server):
        _seed(lovelace_server)
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "water-monitor",
            "ops": [{"op": "delete_card", "view": "water", "card_index": 4}]}))
        assert out["verified"] is True and out["changed"] is True
        cfg = lovelace_server["state"]["configs"]["water-monitor"]
        assert cfg["title"] == "Water"  # untouched top-level key preserved
        assert len(cfg["views"][0]["cards"]) == 4

    def test_expect_hash_mismatch_refused(self, lovelace_server):
        _seed(lovelace_server)
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "water-monitor", "expect_hash": "deadbeef",
            "ops": [{"op": "append_card", "view": "water", "card": {"type": "markdown"}}]}))
        assert out["error"] == "ha_config_changed"

    def test_expect_hash_match_allows(self, lovelace_server):
        _seed(lovelace_server)
        tool = _tool("lovelace_save_config")
        cfg = lovelace_server["state"]["configs"]["water-monitor"]
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "water-monitor", "expect_hash": _hash(cfg),
            "ops": [{"op": "append_card", "view": "water", "card": {"type": "markdown"}}]}))
        assert out["verified"] is True

    def test_config_hash_round_trips_from_read(self, lovelace_server):
        _seed(lovelace_server)
        read = json.loads(run_tool("ha", "lovelace_dashboard",
                                   {"url_path": "water-monitor"}))
        assert read["config_hash"] == _hash(lovelace_server["state"]["configs"]["water-monitor"])
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "water-monitor", "expect_hash": read["config_hash"],
            "ops": [{"op": "append_card", "view": "water", "card": {"type": "markdown"}}]}))
        assert out["verified"] is True

    def test_full_replace_requires_confirm(self, lovelace_server):
        _seed(lovelace_server)
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "water-monitor", "config": {"views": []}}))
        assert out["error"] == "confirm_required"

    def test_full_replace_with_confirm(self, lovelace_server):
        _seed(lovelace_server)
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "water-monitor", "config": {"views": []}, "confirm": "replace"}))
        assert out["verified"] is True
        assert lovelace_server["state"]["configs"]["water-monitor"] == {"views": []}

    def test_yaml_refused(self, lovelace_server):
        _seed(lovelace_server)
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "dwains-dashboard",
            "ops": [{"op": "append_card", "view": "new", "card": {"type": "markdown"}}]}))
        assert out["error"] == "ha_dashboard_yaml_readonly"

    def test_configless_dashboard_set_view_new(self, lovelace_server):
        """Bug 1: a freshly created dashboard has no saved config yet — saving
        must initialise it rather than failing with a hard error."""
        _seed(lovelace_server)
        run_tool("ha", "lovelace_create_dashboard",
                 {"title": "Test", "url_path": "hermes-test"}, "make it")
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "hermes-test",
            "ops": [{"op": "set_view", "view": "new",
                     "view_config": {"path": "main", "cards": [{"type": "markdown"}]}}]}))
        assert out["verified"] is True
        assert lovelace_server["state"]["configs"]["hermes-test"]["views"][0]["path"] == "main"

    def test_configless_full_replace(self, lovelace_server):
        _seed(lovelace_server)
        run_tool("ha", "lovelace_create_dashboard",
                 {"title": "Test", "url_path": "hermes-test"}, "make it")
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "hermes-test", "config": {"views": [{"path": "main"}]},
            "confirm": "replace"}))
        assert out["verified"] is True

    def test_configless_card_op_hints_set_view_new(self, lovelace_server):
        _seed(lovelace_server)
        run_tool("ha", "lovelace_create_dashboard",
                 {"title": "Test", "url_path": "hermes-test"}, "make it")
        tool = _tool("lovelace_save_config")
        out = json.loads(run_handler("ha_lovelace_save_config", get_integration("ha"), tool, {
            "url_path": "hermes-test",
            "ops": [{"op": "append_card", "view": "main", "card": {"type": "markdown"}}]}))
        assert out["error"] == "invalid_request"
        assert "set_view" in out["message"]


class TestLovelaceGating:

    def test_tool_schema_declares_work_and_session_ids(self, lovelace_server):
        _seed(lovelace_server)
        import inspect
        from core.mcp_server import _build_tool_fn
        fn = _build_tool_fn("ha", _tool("lovelace_save_config"))
        params = list(inspect.signature(fn).parameters)
        for p in ("url_path", "ops", "config", "confirm", "expect_hash", "dry_run",
                  "session_id", "execution_id", "reason"):
            assert p in params, p

    def test_save_is_gated(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_save_config", {
            "url_path": "water-monitor",
            "ops": [{"op": "append_card", "view": "water", "card": {"type": "markdown"}}]},
            "please edit"))
        assert out["status"] == "pending" and "id" in out

    def test_delete_is_gated(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_delete_dashboard",
                                  {"url_path": "water-monitor"}, "delete it"))
        assert out["status"] == "pending"

    def test_dry_run_bypasses_gate(self, lovelace_server):
        _seed(lovelace_server)
        out = json.loads(run_tool("ha", "lovelace_save_config", {
            "url_path": "water-monitor", "dry_run": True,
            "ops": [{"op": "append_card", "view": "water", "card": {"type": "markdown"}}]}))
        assert "status" not in out and out["dry_run"] is True

    def test_invalid_gated_save_not_queued(self, lovelace_server):
        """Second-order fix: a doomed write must not consume an approval."""
        from db.integrations import get_pending_calls
        _seed(lovelace_server)
        before = len(get_pending_calls())
        out = json.loads(run_tool("ha", "lovelace_save_config", {
            "url_path": "water-monitor",
            "ops": [{"op": "append_card", "view": "nope", "card": {"type": "markdown"}}]},
            "edit"))
        assert out["error"] == "invalid_request"
        assert len(get_pending_calls()) == before

    def test_unknown_gated_delete_not_queued(self, lovelace_server):
        from db.integrations import get_pending_calls
        _seed(lovelace_server)
        before = len(get_pending_calls())
        out = json.loads(run_tool("ha", "lovelace_delete_dashboard",
                                  {"url_path": "nope-nope"}, "delete it"))
        assert out["error"] == "ha_dashboard_not_found"
        assert len(get_pending_calls()) == before


class TestLovelaceAttribution:
    """Regression 2: handler inner rows must carry the caller context exactly
    like the REST/`mcp` path."""

    def test_read_rows_carry_agent_and_session(self, lovelace_server):
        from db.integrations import get_integration_calls
        _seed(lovelace_server)
        run_tool("ha", "lovelace_dashboards", {"session_id": "sess-attrib"})
        rows = get_integration_calls(session="sess-attrib")["rows"]
        assert rows
        assert all(r["agent"] == "mcp" for r in rows)
        assert all(r["session_id"] == "sess-attrib" for r in rows)
        assert all(r["method"] == "WS" for r in rows)

    def test_save_emits_multiple_attributed_rows(self, lovelace_server):
        from db.integrations import get_integration_calls
        _seed(lovelace_server)
        tool = _tool("lovelace_save_config")
        run_handler("ha_lovelace_save_config", get_integration("ha"), tool,
                    {"url_path": "water-monitor",
                     "ops": [{"op": "append_card", "view": "water",
                              "card": {"type": "markdown"}}]},
                    agent="mcp", session_id="sess-save", execution_id="exec-save")
        rows = get_integration_calls(session="sess-save")["rows"]
        assert len(rows) >= 3  # config read + save + read-back
        assert all(r["agent"] == "mcp" for r in rows)
        assert all(r["execution_id"] == "exec-save" for r in rows)

    def test_approved_save_attributes_operator_and_session(self, lovelace_server, auth_client):
        from db.integrations import create_pending_call, get_integration_calls
        _seed(lovelace_server)
        args = {"url_path": "water-monitor",
                "ops": [{"op": "append_card", "view": "water",
                         "card": {"type": "markdown", "content": "x"}}]}
        call_id = create_pending_call("ha", "lovelace_save_config", args, "approve",
                                      session_id="sess-appr", execution_id="exec-appr")
        r = auth_client.post(f"/api/integration-calls/{call_id}/approve")
        assert r.status_code == 200
        rows = get_integration_calls(session="sess-appr")["rows"]
        assert rows
        assert all(r["agent"] == "operator" for r in rows)


class TestLovelaceApproval:

    def test_approved_save_executes_handler(self, lovelace_server, auth_client):
        from db.integrations import create_pending_call
        _seed(lovelace_server)
        args = {"url_path": "water-monitor",
                "ops": [{"op": "append_card", "view": "water",
                         "card": {"type": "markdown", "content": "new"}}]}
        call_id = create_pending_call("ha", "lovelace_save_config", args, "approve me")
        r = auth_client.post(f"/api/integration-calls/{call_id}/approve")
        assert r.status_code == 200
        cfg = lovelace_server["state"]["configs"]["water-monitor"]
        assert len(cfg["views"][0]["cards"]) == 6


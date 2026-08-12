import base64
import time


def b64(s):
    return base64.b64encode(s.encode()).decode()


class TestFleetDb:

    def test_create_and_get(self):
        from db.fleet import create_fleet_command, get_fleet_command
        cid = create_fleet_command("uptime", ["10.0.0.1", "10.0.0.2"], "operator", "testing", 180)
        cmd = get_fleet_command(cid)
        assert cmd["status"] == "pending"
        assert cmd["command"] == "uptime"
        assert cmd["target_ips"] == ["10.0.0.1", "10.0.0.2"]
        assert cmd["origin"] == "operator"
        assert cmd["results"] == []

    def test_approve_creates_queued_results(self):
        from db.fleet import create_fleet_command, approve_fleet_command, get_fleet_command
        cid = create_fleet_command("date", ["10.0.0.1", "10.0.0.2"], "operator", "r", 60)
        approve_fleet_command(cid)
        cmd = get_fleet_command(cid)
        assert cmd["status"] == "approved"
        assert len(cmd["results"]) == 2
        assert {r["gateway_ip"] for r in cmd["results"]} == {"10.0.0.1", "10.0.0.2"}
        assert all(r["status"] == "queued" for r in cmd["results"])

    def test_result_upsert_unique(self):
        from db.fleet import create_fleet_command, approve_fleet_command, upsert_fleet_result, get_fleet_command
        cid = create_fleet_command("hostname", ["10.0.0.1"], "operator", "r", 60)
        approve_fleet_command(cid)
        upsert_fleet_result(cid, "10.0.0.1", "running")
        upsert_fleet_result(cid, "10.0.0.1", "success", 0, "ok")
        cmd = get_fleet_command(cid)
        assert len(cmd["results"]) == 1
        assert cmd["results"][0]["status"] == "success"

    def test_complete_when_all_terminal(self):
        from db.fleet import create_fleet_command, approve_fleet_command, upsert_fleet_result, get_fleet_command
        cid = create_fleet_command("echo hi", ["10.0.0.1", "10.0.0.2"], "operator", "r", 60)
        approve_fleet_command(cid)
        upsert_fleet_result(cid, "10.0.0.1", "success", 0, "hi")
        assert get_fleet_command(cid)["status"] == "approved"
        upsert_fleet_result(cid, "10.0.0.2", "failed", 1, "boom")
        assert get_fleet_command(cid)["status"] == "complete"

    def test_injectable_until_terminal_result(self):
        from db.fleet import create_fleet_command, approve_fleet_command, upsert_fleet_result, get_injectable_fleet_cmd
        cid = create_fleet_command("uptime", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(cid)
        assert get_injectable_fleet_cmd("10.0.0.1")["id"] == cid
        assert get_injectable_fleet_cmd("10.0.0.9") is None
        upsert_fleet_result(cid, "10.0.0.1", "running")
        assert get_injectable_fleet_cmd("10.0.0.1") is None

    def test_serialize_two_commands_same_gateway(self):
        from db.fleet import create_fleet_command, approve_fleet_command, upsert_fleet_result, get_injectable_fleet_cmd
        c1 = create_fleet_command("cmd A", ["10.0.0.1"], "operator", "r", 180)
        c2 = create_fleet_command("cmd B", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(c1)
        approve_fleet_command(c2)
        # Earliest first
        assert get_injectable_fleet_cmd("10.0.0.1")["id"] == c1
        # c1 starts → gateway busy, nothing injectable
        upsert_fleet_result(c1, "10.0.0.1", "running")
        assert get_injectable_fleet_cmd("10.0.0.1") is None
        # c1 finishes → c2 becomes injectable
        upsert_fleet_result(c1, "10.0.0.1", "success", 0, "ok")
        assert get_injectable_fleet_cmd("10.0.0.1")["id"] == c2
        upsert_fleet_result(c2, "10.0.0.1", "success", 0, "ok")
        assert get_injectable_fleet_cmd("10.0.0.1") is None

    def test_serialize_respects_dispatch_order(self):
        from db.fleet import create_fleet_command, approve_fleet_command, get_injectable_fleet_cmd
        c1 = create_fleet_command("cmd A", ["10.0.0.1"], "operator", "r", 180)
        c2 = create_fleet_command("cmd B", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(c2)
        approve_fleet_command(c1)
        # Even though c2 was approved first, c1 has the lower id (dispatch order)
        assert get_injectable_fleet_cmd("10.0.0.1")["id"] == c1

    def test_different_gateways_run_independently(self):
        from db.fleet import create_fleet_command, approve_fleet_command, upsert_fleet_result, get_injectable_fleet_cmd
        c1 = create_fleet_command("cmd A", ["10.0.0.1"], "operator", "r", 180)
        c2 = create_fleet_command("cmd B", ["10.0.0.2"], "operator", "r", 180)
        approve_fleet_command(c1)
        approve_fleet_command(c2)
        assert get_injectable_fleet_cmd("10.0.0.1")["id"] == c1
        assert get_injectable_fleet_cmd("10.0.0.2")["id"] == c2
        # c1 running on its gateway doesn't block the other gateway
        upsert_fleet_result(c1, "10.0.0.1", "running")
        assert get_injectable_fleet_cmd("10.0.0.1") is None
        assert get_injectable_fleet_cmd("10.0.0.2")["id"] == c2

    def test_results_include_hostname(self):
        from db.gateways import register_gateway
        from db.fleet import create_fleet_command, approve_fleet_command, get_fleet_results
        register_gateway("10.0.0.1", "my-node", "v15.4")
        cid = create_fleet_command("uptime", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(cid)
        results = get_fleet_results(cid)
        assert results[0]["hostname"] == "my-node"

    def test_purge_removes_old_completed_only(self):
        from db.core import get_db
        from db.fleet import (
            create_fleet_command, approve_fleet_command, upsert_fleet_result,
            get_fleet_command, get_fleet_results, purge_old_fleet_commands,
        )
        now = int(time.time())
        # Old completed command
        old = create_fleet_command("old", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(old)
        upsert_fleet_result(old, "10.0.0.1", "success", 0, "x")
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("UPDATE fleet_commands SET created_at = ? WHERE id = ?", (now - 8 * 86400, old))
        conn.commit(); conn.close()
        # Recent completed command
        recent = create_fleet_command("recent", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(recent)
        upsert_fleet_result(recent, "10.0.0.1", "success", 0, "y")
        # In-flight (approved, not complete) command
        inflight = create_fleet_command("inflight", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(inflight)

        removed = purge_old_fleet_commands(now - 7 * 86400)
        assert removed == 1
        assert get_fleet_command(old) is None
        assert get_fleet_results(old) == []
        assert get_fleet_command(recent) is not None
        assert get_fleet_command(inflight) is not None

    def test_skipped_result_is_terminal(self):
        from db.fleet import create_fleet_command, approve_fleet_command, upsert_fleet_result, get_fleet_command, get_injectable_fleet_cmd
        cid = create_fleet_command("cmd", ["10.0.0.1", "10.0.0.2"], "operator", "r", 180)
        approve_fleet_command(cid)
        upsert_fleet_result(cid, "10.0.0.1", "success", 0, "ok")
        upsert_fleet_result(cid, "10.0.0.2", "skipped", None, "cleared")
        cmd = get_fleet_command(cid)
        assert cmd["status"] == "complete"
        # a cleared gateway must not be re-injected
        assert get_injectable_fleet_cmd("10.0.0.2") is None
        assert get_injectable_fleet_cmd("10.0.0.1") is None


class TestFleetApi:

    def _submit(self, auth_client, command="uptime", targets=None, reason="testing", **kw):
        auth_client.post("/api/register", json={"ip": "10.0.0.1", "hostname": "test-host", "version": "v15.4"})
        return auth_client.post("/api/fleet/commands", json={
            "command": command,
            "target_ips": targets or ["10.0.0.1"],
            "reason": reason,
            **kw,
        })

    def test_submit_requires_auth(self, client):
        r = client.post("/api/fleet/commands", json={"command": "uptime", "target_ips": ["10.0.0.1"], "reason": "r"})
        assert r.status_code in (401, 403)

    def test_list_requires_auth(self, client):
        r = client.get("/api/fleet/commands")
        assert r.status_code in (401, 403)

    def test_submit_without_reason_ok(self, auth_client):
        r = self._submit(auth_client, reason="")
        assert r.status_code == 200

    def test_submit_requires_command(self, auth_client):
        r = self._submit(auth_client, command="  ")
        assert r.status_code == 400

    def test_submit_rejects_unknown_target(self, auth_client):
        r = self._submit(auth_client, targets=["10.99.99.99"])
        assert r.status_code == 400

    def test_submit_validates_timeout(self, auth_client):
        r = self._submit(auth_client, timeout=99999)
        assert r.status_code == 400

    def test_submit_dispatches_immediately(self, auth_client, gateway_headers):
        r = self._submit(auth_client, command="hostname")
        assert r.status_code == 200
        assert r.json()["dispatched"] is True
        assert r.json()["gateway_count"] == 1
        cmds = auth_client.get("/api/fleet/commands").json()
        cmd = next(c for c in cmds if c["id"] == r.json()["id"])
        assert cmd["status"] == "approved"
        assert cmd["origin"] == "operator"
        assert len(cmd["results"]) == 1
        assert cmd["results"][0]["status"] == "queued"
        # Injection present right after dispatch
        pol = auth_client.get("/api/policy/10.0.0.1", headers=gateway_headers).json()
        assert pol.get("pending_fleet_cmd_id") == cmd["id"]

    def test_dispatch_creates_fleet_run_history(self, auth_client):
        from db.gateways import register_gateway
        register_gateway("10.0.0.2", "other-host", "v15.4")
        r = self._submit(auth_client, command="uptime", targets=["10.0.0.1", "10.0.0.2"])
        assert r.status_code == 200
        reqs = auth_client.get("/api/requests").json()
        fleet_rows = [q for q in reqs if q["status"] == "fleet-run"]
        assert len(fleet_rows) == 2
        assert {q["target_ip"] for q in fleet_rows} == {"10.0.0.1", "10.0.0.2"}
        assert all(q["command"] == "uptime" for q in fleet_rows)

    def test_submit_requires_session(self, client, gateway_headers):
        r = client.post("/api/fleet/commands", json={
            "command": "uptime", "target_ips": ["10.0.0.1"], "reason": "r"
        }, headers=gateway_headers)
        assert r.status_code in (401, 403)

    def test_submit_hard_block_reject(self, auth_client):
        r = self._submit(auth_client, command="rm -rf /tmp/x")
        assert r.status_code == 400
        assert "blocklist" in r.json()["detail"].lower()

    def test_submit_while_frozen_refused(self, auth_client):
        auth_client.post("/api/freeze")
        r = self._submit(auth_client)
        assert r.status_code == 409
        auth_client.post("/api/unfreeze")

    def test_submit_blacklist_requires_override(self, auth_client):
        auth_client.post("/api/policies", json={"type": "regex_blacklist", "content": "bad-cmd"})
        r = self._submit(auth_client, command="bad-cmd --go")
        assert r.status_code == 400
        r = self._submit(auth_client, command="bad-cmd --go", override=True)
        assert r.status_code == 200
        assert r.json()["dispatched"] is True

    def test_result_requires_matching_token(self, auth_client, gateway_headers):
        cid = self._submit(auth_client).json()["id"]
        r = auth_client.post(f"/api/fleet/commands/{cid}/result", json={
            "gateway_ip": "10.0.0.2", "status": "success", "exit_code": 0, "output": "x"
        }, headers=gateway_headers)
        assert r.status_code == 401

    def test_result_flow_and_injection(self, auth_client, gateway_headers):
        cid = self._submit(auth_client, command="hostname").json()["id"]
        # Post running then success
        r = auth_client.post(f"/api/fleet/commands/{cid}/result", json={
            "gateway_ip": "10.0.0.1", "status": "running"
        }, headers=gateway_headers)
        assert r.status_code == 200
        r = auth_client.post(f"/api/fleet/commands/{cid}/result", json={
            "gateway_ip": "10.0.0.1", "status": "success", "exit_code": 0, "output": "myhost"
        }, headers=gateway_headers)
        assert r.status_code == 200
        # Injection gone after terminal result
        pol = auth_client.get("/api/policy/10.0.0.1", headers=gateway_headers).json()
        assert "pending_fleet_cmd_id" not in pol
        cmds = auth_client.get("/api/fleet/commands").json()
        cmd = next(c for c in cmds if c["id"] == cid)
        assert cmd["status"] == "complete"
        assert cmd["results"][0]["output"] == "myhost"
        assert cmd["results"][0]["started_at"] > 0
        assert cmd["results"][0]["finished_at"] >= cmd["results"][0]["started_at"]

    def test_audit_events_logged(self, auth_client, gateway_headers):
        cid = self._submit(auth_client).json()["id"]
        auth_client.post(f"/api/fleet/commands/{cid}/result", json={
            "gateway_ip": "10.0.0.1", "status": "success", "exit_code": 0, "output": "ok"
        }, headers=gateway_headers)
        events = auth_client.get("/api/audit_log").json()
        types = {e["event_type"] for e in events}
        assert "fleet_created" in types
        assert "fleet_dispatched" in types
        assert "fleet_result" in types

    def test_list_previews_large_output(self, auth_client, gateway_headers):
        cid = self._submit(auth_client, command="hostname").json()["id"]
        big = "x" * 5000
        r = auth_client.post(f"/api/fleet/commands/{cid}/result", json={
            "gateway_ip": "10.0.0.1", "status": "success", "exit_code": 0, "output": big
        }, headers=gateway_headers)
        assert r.status_code == 200
        cmds = auth_client.get("/api/fleet/commands").json()
        cmd = next(c for c in cmds if c["id"] == cid)
        res = cmd["results"][0]
        assert res["has_more"] is True
        assert len(res["output"]) < 5000
        assert res["output"].endswith("…")

    def test_short_output_stays_inline(self, auth_client, gateway_headers):
        cid = self._submit(auth_client).json()["id"]
        auth_client.post(f"/api/fleet/commands/{cid}/result", json={
            "gateway_ip": "10.0.0.1", "status": "success", "exit_code": 0, "output": "short"
        }, headers=gateway_headers)
        cmds = auth_client.get("/api/fleet/commands").json()
        cmd = next(c for c in cmds if c["id"] == cid)
        res = cmd["results"][0]
        assert res["has_more"] is False
        assert res["output"] == "short"

    def test_output_endpoint_returns_full(self, auth_client, gateway_headers):
        cid = self._submit(auth_client).json()["id"]
        big = "y" * 5000
        auth_client.post(f"/api/fleet/commands/{cid}/result", json={
            "gateway_ip": "10.0.0.1", "status": "success", "exit_code": 0, "output": big
        }, headers=gateway_headers)
        r = auth_client.get(f"/api/fleet/commands/{cid}/output/10.0.0.1")
        assert r.status_code == 200
        assert r.json()["output"] == big

    def test_output_endpoint_requires_auth(self, client):
        r = client.get("/api/fleet/commands/1/output/10.0.0.1")
        assert r.status_code in (401, 403)

    def test_clear_requires_auth(self, client):
        from db.fleet import create_fleet_command
        cid = create_fleet_command("uptime", ["10.0.0.1"], "operator", "r", 180)
        r = client.delete(f"/api/fleet/commands/{cid}")
        assert r.status_code in (401, 403)

    def test_clear_stuck_command_unblocks_queue(self, auth_client):
        from db.fleet import create_fleet_command, approve_fleet_command, get_fleet_command, get_injectable_fleet_cmd
        stuck = create_fleet_command("stuck", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(stuck)
        nxt = create_fleet_command("next", ["10.0.0.1"], "operator", "r", 180)
        approve_fleet_command(nxt)
        assert get_injectable_fleet_cmd("10.0.0.1")["id"] == stuck
        r = auth_client.delete(f"/api/fleet/commands/{stuck}")
        assert r.status_code == 200
        assert get_fleet_command(stuck) is None
        assert get_injectable_fleet_cmd("10.0.0.1")["id"] == nxt
        events = auth_client.get("/api/audit_log").json()
        assert "fleet_cleared" in {e["event_type"] for e in events}

    def test_clear_unknown_returns_404(self, auth_client):
        r = auth_client.delete("/api/fleet/commands/999999")
        assert r.status_code == 404

    def test_clear_single_result_keeps_others(self, auth_client):
        from db.gateways import register_gateway
        register_gateway("10.0.0.2", "other-host", "v15.4")
        cid = self._submit(auth_client, command="uptime", targets=["10.0.0.1", "10.0.0.2"]).json()["id"]
        r = auth_client.delete(f"/api/fleet/commands/{cid}/result/10.0.0.2")
        assert r.status_code == 200
        cmds = auth_client.get("/api/fleet/commands").json()
        cmd = next(c for c in cmds if c["id"] == cid)
        by_ip = {x["gateway_ip"]: x["status"] for x in cmd["results"]}
        assert by_ip["10.0.0.2"] == "skipped"
        assert by_ip["10.0.0.1"] == "queued"
        assert cmd["status"] == "approved"
        # clearing a non-queued result is refused
        r = auth_client.delete(f"/api/fleet/commands/{cid}/result/10.0.0.2")
        assert r.status_code == 409
        events = auth_client.get("/api/audit_log").json()
        assert "fleet_result_cleared" in {e["event_type"] for e in events}

    def test_clear_result_requires_auth(self, client):
        r = client.delete("/api/fleet/commands/1/result/10.0.0.1")
        assert r.status_code in (401, 403)

    def test_clear_result_unknown_404(self, auth_client):
        r = auth_client.delete("/api/fleet/commands/999999/result/10.0.0.1")
        assert r.status_code == 404

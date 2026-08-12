import time
import base64


def b64(s):
    return base64.b64encode(s.encode()).decode()


class TestOverrideDb:

    def test_override_columns_exist(self):
        from db.gateways import register_gateway, get_gateways
        register_gateway("10.0.0.1", "test-host", "v15.3")
        gws = get_gateways()
        gw = next(g for g in gws if g["ip"] == "10.0.0.1")
        assert "override_until" in gw
        assert "override_reason" in gw
        assert gw["override_until"] == 0
        assert gw["override_reason"] == ""

    def test_set_override(self):
        from db.gateways import register_gateway, get_gateways, set_override
        register_gateway("10.0.0.2", "test-host", "v15.3")
        until = int(time.time()) + 1800
        set_override("10.0.0.2", until, "testing")
        gws = get_gateways()
        gw = next(g for g in gws if g["ip"] == "10.0.0.2")
        assert gw["override_until"] == until
        assert gw["override_reason"] == "testing"

    def test_clear_override(self):
        from db.gateways import register_gateway, get_gateways, set_override, clear_override
        register_gateway("10.0.0.3", "test-host", "v15.3")
        set_override("10.0.0.3", int(time.time()) + 1800, "testing")
        clear_override("10.0.0.3")
        gws = get_gateways()
        gw = next(g for g in gws if g["ip"] == "10.0.0.3")
        assert gw["override_until"] == 0
        assert gw["override_reason"] == ""

    def test_override_survives_reregister(self):
        from db.gateways import register_gateway, get_gateways, set_override
        register_gateway("10.0.0.4", "test-host", "v15.3")
        until = int(time.time()) + 1800
        set_override("10.0.0.4", until, "testing")
        register_gateway("10.0.0.4", "new-name", "v15.3")
        gws = get_gateways()
        gw = next(g for g in gws if g["ip"] == "10.0.0.4")
        assert gw["override_until"] == until
        assert gw["override_reason"] == "testing"


class TestOverrideApi:

    def test_start_override_requires_auth(self, client, gateway_headers):
        r = client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": "testing"})
        assert r.status_code in (401, 403)

    def test_start_override_requires_reason(self, auth_client, gateway_headers):
        r = auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": ""})
        assert r.status_code == 400

    def test_start_override_requires_valid_minutes(self, auth_client, gateway_headers):
        r = auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 0, "reason": "testing"})
        assert r.status_code == 400
        r = auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 1441, "reason": "testing"})
        assert r.status_code == 400

    def test_start_override_works(self, auth_client, gateway_headers):
        r = auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": "testing override"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "override_until" in data
        assert data["override_until"] > int(time.time())

    def test_start_override_custom_minutes(self, auth_client, gateway_headers):
        r = auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 60, "reason": "custom duration"})
        assert r.status_code == 200

    def test_cancel_override(self, auth_client, gateway_headers):
        r = auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": "will cancel"})
        assert r.status_code == 200
        r = auth_client.delete("/api/gateways/10.0.0.1/override")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_cancel_override_requires_auth(self, client, gateway_headers):
        r = client.delete("/api/gateways/10.0.0.1/override")
        assert r.status_code in (401, 403)

    def test_gateway_listing_includes_override(self, auth_client, gateway_headers):
        auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": "check listing"})
        r = auth_client.get("/api/gateways")
        assert r.status_code == 200
        gws = r.json()
        gw = next(g for g in gws if g["ip"] == "10.0.0.1")
        assert "override_remaining" in gw
        assert gw["override_remaining"] > 0

    def test_override_auto_approves_jit(self, auth_client, gateway_headers):
        auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": "auto-approve test"})
        r = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("uptime")
        }, headers=gateway_headers)
        assert r.status_code == 200
        data = r.json()
        assert data.get("override") is True
        assert "Auto-approved" in data.get("message", "")

    def test_override_auto_approved_ticket_claimable(self, auth_client, gateway_headers):
        auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": "ticket test"})
        rid = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("whoami")
        }, headers=gateway_headers).json()["id"]
        r = auth_client.get(f"/api/request_status/{rid}", headers=gateway_headers)
        assert r.json()["status"] == "approved"
        r = auth_client.get(f"/api/ticket/{rid}", headers=gateway_headers)
        assert r.status_code == 200
        assert r.json()["ticket"] is not None
        assert "whoami" in r.json()["ticket"]

    def test_override_audit_events_logged(self, auth_client, gateway_headers):
        auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 15, "reason": "audit test"})
        auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("date")
        }, headers=gateway_headers)
        auth_client.delete("/api/gateways/10.0.0.1/override")
        r = auth_client.get("/api/audit_log")
        assert r.status_code == 200
        events = r.json()
        event_types = {e["event_type"] for e in events}
        assert "override_started" in event_types
        assert "override_cancelled" in event_types
        assert "jit_override_approved" in event_types
        start_event = next(e for e in events if e["event_type"] == "override_started")
        assert "audit test" in start_event.get("details", "")

    def test_override_expires_and_normal_flow_resumes(self, auth_client, gateway_headers):
        from db.gateways import set_override
        set_override("10.0.0.1", int(time.time()) - 1, "expired")
        r = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("hostname")
        }, headers=gateway_headers)
        assert r.status_code == 200
        data = r.json()
        assert data.get("override") is not True
        rid = data["id"]
        r = auth_client.get(f"/api/request_status/{rid}", headers=gateway_headers)
        assert r.json()["status"] == "pending"

    def test_override_does_not_affect_other_gateways(self, auth_client, client):
        from db.gateways import register_gateway, set_gateway_token
        import secrets
        register_gateway("10.0.0.99", "other-gw", "v15.3")
        tok = secrets.token_hex(32)
        set_gateway_token("10.0.0.99", tok)
        auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": "gateway A"})
        r = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.99",
            "encoded_command": b64("ls")
        }, headers={"X-Gateway-Token": tok})
        assert r.status_code == 200
        data = r.json()
        assert data.get("override") is not True
        rid = data["id"]
        r = auth_client.get(f"/api/request_status/{rid}", headers={"X-Gateway-Token": tok})
        assert r.json()["status"] == "pending"

    def test_start_override_for_nonexistent_gateway(self, auth_client):
        r = auth_client.post("/api/gateways/10.99.99.99/override", json={"minutes": 30, "reason": "no gateway"})
        assert r.status_code == 200

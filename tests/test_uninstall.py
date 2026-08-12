import pytest


def _register(auth_client):
    """Register a gateway for use in uninstall tests."""
    auth_client.post("/api/register", json={
        "ip": "10.0.0.1",
        "hostname": "uninstall-test",
        "version": "v15.3"
    })


class TestTriggerLifecycle:

    def test_set_check_clear(self):
        from db.gateways import set_trigger_uninstall, check_trigger_uninstall, clear_trigger_uninstall
        set_trigger_uninstall("10.0.0.1")
        assert check_trigger_uninstall("10.0.0.1") is not None
        clear_trigger_uninstall("10.0.0.1")
        assert check_trigger_uninstall("10.0.0.1") is None

    def test_returns_none_for_unknown_ip(self):
        from db.gateways import check_trigger_uninstall
        assert check_trigger_uninstall("10.0.0.99") is None


class TestProgressLifecycle:

    def test_set_get_clear(self):
        from db.gateways import set_uninstall_progress, get_uninstall_progress, clear_uninstall_progress
        set_uninstall_progress("10.0.0.1", "test_step", "test message")
        assert get_uninstall_progress("10.0.0.1") == "test_step:test message"
        clear_uninstall_progress("10.0.0.1")
        assert get_uninstall_progress("10.0.0.1") is None


class TestTriggerAPI:

    def test_401_without_auth(self, client):
        r = client.post("/api/gateways/10.0.0.1/uninstall")
        assert r.status_code == 401

    def test_succeeds_for_registered_gateway(self, auth_client):
        _register(auth_client)
        r = auth_client.post("/api/gateways/10.0.0.1/uninstall")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["ip"] == "10.0.0.1"

    def test_404_for_unknown_gateway(self, auth_client):
        r = auth_client.post("/api/gateways/99.99.99.99/uninstall")
        assert r.status_code == 404


class TestProgressAPI:

    def test_store_and_retrieve(self, auth_client):
        r = auth_client.post("/api/uninstall-progress", json={
            "ip": "10.0.0.1",
            "step": "cleaning",
            "message": "Removing scripts"
        })
        assert r.status_code == 200
        r = auth_client.get("/api/uninstall-progress/10.0.0.1")
        assert r.status_code == 200
        assert r.json()["progress"] == "cleaning:Removing scripts"

    def test_returns_none_for_unknown(self, auth_client):
        r = auth_client.get("/api/uninstall-progress/99.99.99.99")
        assert r.status_code == 200
        assert r.json()["progress"] is None


class TestCompleteUninstall:

    def test_deregisters_gateway(self, auth_client):
        _register(auth_client)
        r = auth_client.post("/api/uninstall-progress", json={
            "ip": "10.0.0.1",
            "step": "complete",
            "message": "Uninstall finished"
        })
        assert r.status_code == 200
        from db.gateways import get_gateways
        gws = get_gateways()
        assert all(g["ip"] != "10.0.0.1" for g in gws)

    def test_clears_trigger_and_progress(self, auth_client):
        from db.gateways import set_trigger_uninstall, set_uninstall_progress, \
            check_trigger_uninstall, get_uninstall_progress
        _register(auth_client)
        set_trigger_uninstall("10.0.0.1")
        set_uninstall_progress("10.0.0.1", "step", "msg")
        auth_client.post("/api/uninstall-progress", json={
            "ip": "10.0.0.1",
            "step": "complete",
            "message": ""
        })
        assert check_trigger_uninstall("10.0.0.1") is None
        assert get_uninstall_progress("10.0.0.1") is None

    def test_records_audit_event(self, auth_client):
        _register(auth_client)
        auth_client.post("/api/uninstall-progress", json={
            "ip": "10.0.0.1",
            "step": "complete",
            "message": ""
        })
        from db.audit import get_audit_log
        events = get_audit_log()
        assert any(e["event_type"] == "uninstalled" for e in events)


class TestForceDeregister:

    def test_401_without_auth_or_token(self, client):
        from db.gateways import register_gateway
        register_gateway("10.0.0.1", "noauth-host", "v15.3")
        r = client.delete("/api/gateways/10.0.0.1")
        assert r.status_code == 401

    def test_via_gateway_token(self, auth_client, gateway_headers):
        r = auth_client.delete("/api/gateways/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["ip"] == "10.0.0.1"

    def test_404_for_unknown_gateway(self, auth_client):
        r = auth_client.delete("/api/gateways/99.99.99.99")
        assert r.status_code == 404

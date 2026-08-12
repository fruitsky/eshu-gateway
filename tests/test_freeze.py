import pytest


class TestFreezeDb:

    def test_set_check_clear(self):
        from db.gateways import set_trigger_freeze, get_trigger_freeze, clear_trigger_freeze
        set_trigger_freeze()
        assert get_trigger_freeze() is not None
        clear_trigger_freeze()
        assert get_trigger_freeze() is None

    def test_returns_none_when_never_frozen(self):
        from db.gateways import get_trigger_freeze
        assert get_trigger_freeze() is None

    def test_set_is_idempotent(self):
        from db.gateways import set_trigger_freeze, get_trigger_freeze
        set_trigger_freeze()
        first = get_trigger_freeze()
        set_trigger_freeze()
        assert get_trigger_freeze() is not None


class TestFreezeApi:

    def test_freeze_requires_auth(self, client):
        r = client.post("/api/freeze")
        assert r.status_code in (401, 403)

    def test_unfreeze_requires_auth(self, client):
        r = client.post("/api/unfreeze")
        assert r.status_code in (401, 403)

    def test_freeze_status_requires_auth(self, client):
        r = client.get("/api/freeze/status")
        assert r.status_code in (401, 403)

    def test_freeze_and_unfreeze_roundtrip(self, auth_client):
        r = auth_client.post("/api/freeze")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["triggered_at"] is not None
        r = auth_client.get("/api/freeze/status")
        assert r.json()["frozen"] is True
        r = auth_client.post("/api/unfreeze")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        r = auth_client.get("/api/freeze/status")
        assert r.json()["frozen"] is False

    def test_freeze_status_reports_timestamp(self, auth_client):
        import time
        before = int(time.time())
        auth_client.post("/api/freeze")
        r = auth_client.get("/api/freeze/status")
        assert r.json()["frozen"] is True
        assert r.json()["triggered_at"] >= before

    def test_freeze_audit_events_logged(self, auth_client):
        auth_client.post("/api/freeze")
        auth_client.post("/api/unfreeze")
        r = auth_client.get("/api/audit_log")
        events = r.json()
        event_types = {e["event_type"] for e in events}
        assert "freeze_started" in event_types
        assert "freeze_ended" in event_types


class TestFreezePolicy:

    def test_policy_includes_freeze_true_when_frozen(self, auth_client, gateway_headers):
        auth_client.post("/api/freeze")
        r = auth_client.get("/api/policy/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200
        assert r.json()["trigger_freeze"] is True

    def test_policy_includes_freeze_false_when_unfrozen(self, auth_client, gateway_headers):
        r = auth_client.get("/api/policy/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200
        assert r.json()["trigger_freeze"] is False

    def test_freeze_resolves_via_token_not_url_ip(self, auth_client, gateway_headers):
        auth_client.post("/api/freeze")
        r = auth_client.get("/api/policy/10.0.0.1", headers=gateway_headers)
        assert r.json()["trigger_freeze"] is True

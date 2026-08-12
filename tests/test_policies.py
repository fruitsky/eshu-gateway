class TestSaveAndCommit:

    def test_save_policy_persists(self, auth_client):
        r = auth_client.post("/api/policies", json={
            "type": "exact_whitelist",
            "content": "uptime\ndate\nls"
        })
        assert r.status_code == 200
        r = auth_client.get("/api/policies")
        assert r.status_code == 200
        data = r.json()
        assert data.get("exact_whitelist") == "uptime\ndate\nls"

    def test_save_policy_requires_auth(self, client):
        r = client.post("/api/policies", json={
            "type": "exact_whitelist",
            "content": "test"
        })
        assert r.status_code == 401

    def test_commit_increments_version(self, auth_client):
        before = auth_client.get("/api/policies").json().get("policy_version", 0)
        r = auth_client.post("/api/policies/commit")
        assert r.status_code == 200
        after = auth_client.get("/api/policies").json().get("policy_version", 0)
        assert after > before

    def test_policy_changes_recorded(self, auth_client):
        auth_client.post("/api/policies", json={
            "type": "exact_whitelist",
            "content": "new content"
        })
        r = auth_client.get("/api/policy_changes")
        assert r.status_code == 200
        changes = r.json()
        assert len(changes) > 0


class TestPolicyTest:

    def test_policy_test_matches_exact(self, auth_client):
        auth_client.post("/api/policies", json={
            "type": "exact_whitelist",
            "content": "uptime"
        })
        auth_client.post("/api/policies", json={
            "type": "regex_whitelist",
            "content": ""
        })
        auth_client.post("/api/policies", json={
            "type": "regex_blacklist",
            "content": ""
        })
        r = auth_client.get("/api/policies/test?command=uptime")
        assert r.status_code == 200
        assert r.json().get("action") == "auto_approved"

    def test_policy_test_returns_jit_for_unknown(self, auth_client):
        r = auth_client.get("/api/policies/test?command=unsafe_command")
        assert r.status_code == 200
        assert r.json().get("action") == "jit"


class TestTriggers:

    def test_update_version_set_and_get(self):
        from db.gateways import set_trigger_update_version, get_trigger_update_version
        set_trigger_update_version("test-123")
        assert get_trigger_update_version() == "test-123"

    def test_rollback_set_get_clear(self):
        from db.gateways import set_trigger_rollback, get_trigger_rollback, clear_trigger_rollback
        set_trigger_rollback("rollback-456")
        assert get_trigger_rollback() == "rollback-456"
        clear_trigger_rollback()
        assert get_trigger_rollback() is None or get_trigger_rollback() == ""

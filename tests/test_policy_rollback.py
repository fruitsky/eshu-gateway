class TestPolicyRollback:

    def test_rollback_requires_auth(self, client):
        r = client.post("/api/policies/rollback/1")
        assert r.status_code in (401, 403)

    def test_rollback_nonexistent_change(self, auth_client):
        r = auth_client.post("/api/policies/rollback/99999")
        assert r.status_code == 404

    def test_rollback_restores_old_content(self, auth_client):
        from db.policies import update_policy, get_policies, record_policy_change
        # Initial state: allowlist has "uptime"
        update_policy("exact_whitelist", "uptime")
        old_policies = get_policies()
        # Record a change: old -> new (adds hostname)
        update_policy("exact_whitelist", "uptime\nhostname")
        new_policies = get_policies()
        record_policy_change("exact_whitelist", old_policies.get("exact_whitelist", ""), new_policies.get("exact_whitelist", ""))
        from db.policies import get_policy_changes
        change_id = get_policy_changes()[0]["id"]
        # Roll back
        r = auth_client.post(f"/api/policies/rollback/{change_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["policy_type"] == "exact_whitelist"
        # Policy restored to old content
        assert get_policies()["exact_whitelist"] == "uptime"

    def test_rollback_creates_new_history_entry(self, auth_client):
        from db.policies import update_policy, get_policies, record_policy_change, get_policy_changes
        update_policy("regex_blacklist", "rm -rf")
        old_policies = get_policies()
        update_policy("regex_blacklist", "rm -rf\nmkfs")
        new_policies = get_policies()
        record_policy_change("regex_blacklist", old_policies.get("regex_blacklist", ""), new_policies.get("regex_blacklist", ""))
        before = len(get_policy_changes())
        change_id = get_policy_changes()[0]["id"]
        auth_client.post(f"/api/policies/rollback/{change_id}")
        # A new entry is recorded for the rollback itself
        assert len(get_policy_changes()) == before + 1

    def test_rollback_bumps_policy_version(self, auth_client):
        from db.policies import update_policy, get_policies, record_policy_change, get_policy_changes, get_policy_version
        update_policy("exact_whitelist", "date")
        old_policies = get_policies()
        update_policy("exact_whitelist", "date\nuptime")
        new_policies = get_policies()
        record_policy_change("exact_whitelist", old_policies.get("exact_whitelist", ""), new_policies.get("exact_whitelist", ""))
        v_before = get_policy_version()
        change_id = get_policy_changes()[0]["id"]
        auth_client.post(f"/api/policies/rollback/{change_id}")
        assert get_policy_version() == v_before + 1

    def test_rollback_records_audit_event(self, auth_client):
        from db.policies import update_policy, get_policies, record_policy_change, get_policy_changes
        from db.audit import get_audit_log
        update_policy("exact_whitelist", "whoami")
        old_policies = get_policies()
        update_policy("exact_whitelist", "whoami\ndate")
        new_policies = get_policies()
        record_policy_change("exact_whitelist", old_policies.get("exact_whitelist", ""), new_policies.get("exact_whitelist", ""))
        change_id = get_policy_changes()[0]["id"]
        auth_client.post(f"/api/policies/rollback/{change_id}")
        events = get_audit_log(200)
        assert any(e["event_type"] == "policy_rolled_back" for e in events)

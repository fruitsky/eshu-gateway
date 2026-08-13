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

    def test_policy_test_stays_public(self, client):
        # Agent pre-flight must stay open (no session) so agents can check
        # whether a command will auto-approve, hard-block, or need JIT.
        r = client.get("/api/policies/test?command=uptime")
        assert r.status_code == 200

    def test_policy_check_requires_auth(self, client):
        # /api/policies/check reveals allowlist/blocklist membership — the
        # operator-only Tester uses it, so it must be session-gated.
        assert client.get("/api/policies/check?command=uptime").status_code == 401

    def test_policy_check_works_for_session(self, auth_client):
        r = auth_client.get("/api/policies/check?command=uptime")
        assert r.status_code == 200
        assert "in_exact_whitelist" in r.json()


class TestBlocklistSubstringSemantics:

    def _set_blocklist(self, auth_client, content):
        auth_client.post("/api/policies", json={"type": "regex_blacklist", "content": content})
        auth_client.post("/api/policies", json={"type": "regex_whitelist", "content": ""})
        auth_client.post("/api/policies", json={"type": "exact_whitelist", "content": ""})

    def test_blocklist_is_substring_match(self, auth_client):
        # Mirrors the gateway: /etc/eshu-rblack.txt is a literal substring match.
        self._set_blocklist(auth_client, "foo")
        r = auth_client.get("/api/policies/test?command=xxfooYY")
        assert r.status_code == 200
        assert r.json()["action"] == "blocked"

    def test_blocklist_anchor_is_stripped(self, auth_client):
        # ^...$ anchors are stripped before the substring match, like the gateway.
        self._set_blocklist(auth_client, "^foo$")
        for cmd in ("foo", "xxfooYY"):
            r = auth_client.get("/api/policies/test?command=" + cmd)
            assert r.json()["action"] == "blocked"

    def test_blocklist_metachars_are_literal(self, auth_client):
        # "$(which " is invalid as regex but must still block as a substring,
        # exactly as the gateway enforces it.
        self._set_blocklist(auth_client, "$(which ")
        r = auth_client.get("/api/policies/test", params={"command": "$(which python)"})
        assert r.status_code == 200
        assert r.json()["action"] == "blocked"

    def test_blocklist_comment_lines_ignored(self, auth_client):
        self._set_blocklist(auth_client, "# a comment\nfoo")
        r = auth_client.get("/api/policies/test?command=bar foo baz")
        assert r.json()["action"] == "blocked"

    def test_check_membership_substring(self, auth_client):
        self._set_blocklist(auth_client, "docker rm")
        r = auth_client.get("/api/policies/check?command=docker rm -f")
        assert r.status_code == 200
        assert r.json()["in_regex_blacklist"] is True


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

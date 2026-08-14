import base64


def b64(s):
    return base64.b64encode(s.encode()).decode()


class TestJitLifecycle:

    def test_submit_jit_request(self, auth_client, gateway_headers):
        r = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("uptime")
        }, headers=gateway_headers)
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert len(data["id"]) == 6

    def test_full_jit_flow(self, auth_client, gateway_headers):
        rid = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("uptime")
        }, headers=gateway_headers).json()["id"]

        r = auth_client.get(f"/api/request_status/{rid}", headers=gateway_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

        r = auth_client.post(f"/api/approve/{rid}")
        assert r.status_code == 200

        r = auth_client.get(f"/api/request_status/{rid}", headers=gateway_headers)
        assert r.json()["status"] == "approved"

        r = auth_client.get(f"/api/ticket/{rid}", headers=gateway_headers)
        assert r.status_code == 200
        assert r.json()["ticket"] is not None
        assert "uptime" in r.json()["ticket"]

        r = auth_client.get(f"/api/request_status/{rid}", headers=gateway_headers)
        assert r.json()["status"] == "consumed"

    def test_deny_request(self, auth_client, gateway_headers):
        rid = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("rm -rf /")
        }, headers=gateway_headers).json()["id"]

        r = auth_client.post(f"/api/deny/{rid}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["command"] == "rm -rf /"
        assert data["deny_count"] == 1

        r = auth_client.get(f"/api/request_status/{rid}", headers=gateway_headers)
        assert r.json()["status"] == "denied"

    def test_deny_count_accumulates(self, auth_client, gateway_headers):
        ids = []
        for i in range(3):
            ids.append(auth_client.post("/api/request", json={
                "target_ip": "10.0.0.1",
                "encoded_command": b64("systemctl restart bad-service")
            }, headers=gateway_headers).json()["id"])
        counts = []
        for rid in ids:
            counts.append(auth_client.post(f"/api/deny/{rid}").json()["deny_count"])
        assert counts == [1, 2, 3]

    def test_request_status_no_token(self, client):
        from db.requests import create_request
        rid = create_request("10.0.0.1", "uptime")
        r = client.get(f"/api/request_status/{rid}")
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

    def test_ticket_no_token(self, client):
        from db.requests import create_request, update_request_status
        rid = create_request("10.0.0.1", "uptime")
        update_request_status(rid, "approved")
        r = client.get(f"/api/ticket/{rid}")
        assert r.status_code == 200
        assert r.json()["ticket"] is not None

    def test_request_status_404(self, auth_client, gateway_headers):
        r = auth_client.get("/api/request_status/99999", headers=gateway_headers)
        assert r.status_code == 404

    def test_window_rejected_creates_single_ticket(self, client, gateway_headers):
        # A rejected window attempt must create exactly ONE request
        # (window-rejected) and must NOT also create a JIT ticket.
        import base64
        enc = base64.b64encode(b"hostname").decode()
        r = client.post("/api/log", json={
            "target_ip": "10.0.0.1",
            "encoded_command": enc,
            "status": "window-rejected",
            "reason": "unknown token",
            "token": "tok123"
        }, headers=gateway_headers)
        assert r.status_code == 200
        reqs = client.get("/api/requests").json()
        wr = [x for x in reqs if x["status"] == "window-rejected"]
        assert len(wr) == 1
        assert wr[0]["command"] == "hostname"
        assert wr[0]["reason"] == "unknown token"
        # No JIT/pending ticket is created by a window rejection
        assert not any(x["status"] == "pending" for x in reqs)


class TestGatewayRegistration:

    def test_register_returns_token(self, client):
        r = client.post("/api/register", json={
            "ip": "10.0.0.5",
            "hostname": "new-host",
            "version": "v15.3"
        })
        assert r.status_code == 200
        token = r.json().get("gateway_token")
        assert token and len(token) > 0

    def test_register_preserves_token(self, client):
        r1 = client.post("/api/register", json={
            "ip": "10.0.0.6",
            "hostname": "persist-host",
            "version": "v15.3"
        })
        token1 = r1.json()["gateway_token"]
        r2 = client.post("/api/register", json={
            "ip": "10.0.0.6",
            "hostname": "persist-host",
            "version": "v15.3"
        })
        assert r2.json()["gateway_token"] == token1

    def test_register_repairs_literal_None_token(self, client):
        # Regression: a stored literal 'None' token (v15.0 DEFAULT None schema
        # bug) must be treated as "no token" — register must mint a real one
        # instead of returning 'None' (which the installer rejects and the
        # poller self-heal then floods on).
        from db.gateways import set_gateway_token, get_gateway_token
        set_gateway_token("10.0.0.9", "None")
        r = client.post("/api/register", json={
            "ip": "10.0.0.9",
            "hostname": "none-token-host",
            "version": "v15.3"
        })
        assert r.status_code == 200
        token = r.json().get("gateway_token")
        assert token and token != "None" and len(token) >= 32
        # And it's persisted, so the next register returns the real token.
        assert get_gateway_token("10.0.0.9") == token
        r2 = client.post("/api/register", json={
            "ip": "10.0.0.9",
            "hostname": "none-token-host",
            "version": "v15.3"
        })
        assert r2.json()["gateway_token"] == token

    def test_gateway_list_has_token_field(self, auth_client):
        client = auth_client
        r = client.post("/api/register", json={
            "ip": "10.0.0.7",
            "hostname": "token-test",
            "version": "v15.3"
        })
        token = r.json()["gateway_token"]
        r = client.get("/api/gateways")
        assert r.status_code == 200
        gws = r.json()
        target = next(g for g in gws if g["ip"] == "10.0.0.7")
        assert target["has_token"] is True
        assert target["api_token"] == token

    def test_zero_trust_toggle(self, auth_client):
        client = auth_client
        client.post("/api/register", json={
            "ip": "10.0.0.8",
            "hostname": "zt-test",
            "version": "v15.3"
        })
        # Off by default
        r = client.get("/api/policy/10.0.0.8")
        assert r.status_code == 200
        assert r.json().get("zero_trust") == 0
        # Enable
        r = client.post("/api/gateways/10.0.0.8/zero-trust", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["zero_trust"] is True
        # Reflected in the gateway list
        gws = client.get("/api/gateways").json()
        assert next(g for g in gws if g["ip"] == "10.0.0.8")["zero_trust"] == 1
        # Reflected in the policy payload
        r = client.get("/api/policy/10.0.0.8")
        assert r.json().get("zero_trust") == 1
        # Disable
        r = client.post("/api/gateways/10.0.0.8/zero-trust", json={"enabled": False})
        assert r.status_code == 200
        r = client.get("/api/policy/10.0.0.8")
        assert r.json().get("zero_trust") == 0

    def test_zero_trust_toggle_requires_session(self, client):
        r = client.post("/api/gateways/10.0.0.9/zero-trust", json={"enabled": True})
        assert r.status_code in (401, 403)

    def test_override_blocked_on_zero_trust(self, auth_client):
        client = auth_client
        client.post("/api/register", json={"ip": "10.0.0.10", "hostname": "zt-ov-1", "version": "v15.3"})
        r = client.post("/api/gateways/10.0.0.10/zero-trust", json={"enabled": True})
        assert r.status_code == 200
        r = client.post("/api/gateways/10.0.0.10/override", json={"minutes": 30, "reason": "test"})
        assert r.status_code == 400
        assert "mutually exclusive" in r.json()["detail"]

    def test_zero_trust_blocked_while_override(self, auth_client):
        client = auth_client
        client.post("/api/register", json={"ip": "10.0.0.11", "hostname": "zt-ov-2", "version": "v15.3"})
        r = client.post("/api/gateways/10.0.0.11/override", json={"minutes": 30, "reason": "test"})
        assert r.status_code == 200
        r = client.post("/api/gateways/10.0.0.11/zero-trust", json={"enabled": True})
        assert r.status_code == 400
        assert "mutually exclusive" in r.json()["detail"]

    def test_request_never_auto_approved_on_zero_trust(self, client, gateway_headers):
        # Defense-in-depth: even if Override and ZT are both set (direct DB edit),
        # ZT wins and the request routes to pending, never auto-approved.
        import time, base64
        from db.gateways import set_gateway_zero_trust, set_override
        set_gateway_zero_trust("10.0.0.1", True)
        set_override("10.0.0.1", int(time.time()) + 3600, "forced")
        enc = base64.b64encode(b"uptime").decode()
        r = client.post("/api/request", json={"target_ip": "10.0.0.1", "encoded_command": enc}, headers=gateway_headers)
        assert r.status_code == 200
        data = r.json()
        assert data.get("override") is not True
        assert data.get("message") != "Auto-approved via Override Mode"

    def test_override_auto_approved_request_is_marked(self, auth_client, gateway_headers):
        # Override-auto-approved requests must carry reason="override" so the
        # History table can show a distinct indicator.
        auth_client.post("/api/gateways/10.0.0.1/override", json={"minutes": 30, "reason": "test"})
        r = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("docker ps")
        }, headers=gateway_headers)
        assert r.status_code == 200
        assert r.json().get("override") is True

        reqs = auth_client.get("/api/requests").json()
        match = [x for x in reqs if x["command"] == "docker ps"]
        assert len(match) >= 1
        assert match[0]["status"] == "approved"
        assert match[0]["reason"] == "override"


class TestAuth:

    def test_login_sets_cookie(self, client):
        r = client.post("/api/auth/login", json={"password": "test"})
        assert r.status_code == 200
        assert r.cookies.get("eshu_session") is not None

    def test_login_wrong_password(self, client):
        r = client.post("/api/auth/login", json={"password": "wrong"})
        assert r.status_code == 401

    def test_protected_endpoint_needs_login(self, client):
        r = client.post("/api/approve/1")
        assert r.status_code == 401

    def test_authenticated_client_can_approve(self, auth_client):
        r = auth_client.post("/api/approve/1")
        assert r.status_code in (200, 404)

    def test_auth_status(self, client):
        r = client.get("/api/auth/status")
        assert r.status_code == 200
        data = r.json()
        assert data["password_set"] is True


class TestWindows:

    def test_window_by_token_lookup(self, auth_client, gateway_headers):
        from db.windows import create_approved_window
        result = create_approved_window("10.0.0.1", "docker ps", 0, 0, 1, "test-win", 0, 0)
        token = result["token"]
        r = auth_client.get(f"/api/window-by-token/{token}")
        assert r.status_code == 200
        data = r.json()
        assert data["command"] == "docker ps"
        assert data["token"] == token
        assert data["target_ip"] == "10.0.0.1"

    def test_window_by_token_404(self, client):
        r = client.get("/api/window-by-token/nonexistent")
        assert r.status_code == 404


class TestPolicy:

    def test_policy_sync(self, auth_client, gateway_headers):
        r = auth_client.get("/api/policy/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200
        data = r.json()
        assert "policy_version" in data
        assert "policy_updated_at" in data
        assert "dashboard_version" in data
        assert "mode" in data
        assert "trigger_update_version" in data

    def test_poll_endpoint(self, auth_client, gateway_headers):
        r = auth_client.get("/api/poll/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200
        data = r.json()
        assert "ticket" in data
        assert "mode" in data
        assert "version" in data

    def test_windows_delivered_without_feature_flag(self, auth_client, gateway_headers):
        # Approved Windows are core/always-on — delivered by data, not a flag.
        from db.windows import create_approved_window
        create_approved_window("10.0.0.1", "docker ps", 0, 0, 1, "test-win", 0, 0)
        r = auth_client.get("/api/policy/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200
        wins = r.json().get("approved_windows", [])
        assert any(w.get("command") == "docker ps" for w in wins)
        # And the flag is gone from the toggleable feature-flags list entirely.
        r = auth_client.get("/api/feature-flags")
        assert "approved_windows" not in r.json()

    def test_dev_mode_gateway_gets_dev_url_without_flag(self, auth_client, gateway_headers):
        # Dev-installer URL must not depend on a (removed) windows feature flag.
        from db.gateways import set_gateway_mode
        set_gateway_mode("10.0.0.1", "dev")
        r = auth_client.get("/api/policy/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200
        assert r.json().get("dev_installer_url") == "/static/dev/eshu-gateway-install.sh"

    def test_policy_test_surfaces_fatal_tier(self, auth_client):
        # The non-editable core blocklist (self-protection + evasion) must be
        # surfaced by /api/policies/test so agent pre-flight isn't told "jit"
        # for a command that is actually a permanent hard block.
        r = auth_client.get("/api/policies/test?command=cat /etc/eshu-freeze")
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "blocked"
        assert data["tier"] == "fatal"
        assert data["matched"] is True

        r2 = auth_client.get("/api/policies/test", params={"command": "$(which python)"})
        assert r2.status_code == 200
        assert r2.json()["action"] == "blocked"
        assert r2.json()["tier"] == "fatal"

    def test_policy_test_safe_command_still_jit(self, auth_client):
        r = auth_client.get("/api/policies/test?command=docker ps")
        assert r.status_code == 200
        assert r.json()["action"] in ("jit", "auto_approved")


class TestRateLimit:

    def test_rate_limit_does_not_block_normal_use(self, auth_client, gateway_headers):
        for _ in range(3):
            r = auth_client.post("/api/request", json={
                "target_ip": "10.0.0.1",
                "encoded_command": b64("uptime")
            }, headers=gateway_headers)
            assert r.status_code == 200

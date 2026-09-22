"""Phase 1 gateway-endpoint authentication.

The gateway-facing API was reachable by any host on the LAN: /api/policy
returned the full whitelist/regex/blacklist unauthenticated, /api/policy_changes
had no auth, /api/ticket and /api/request_status accepted no/optional tokens,
and /api/register returned a valid gateway token for any self-reported IP (a
token oracle that bypassed the rest). These tests lock in the new contract.
"""
from db.gateways import register_gateway, set_gateway_token
from db.enrollment import save_ssh_keys


class TestPolicyAuth:

    def test_policy_requires_auth(self, client):
        r = client.get("/api/policy/10.0.0.1")
        assert r.status_code == 401

    def test_policy_with_matching_token(self, client, gateway_headers):
        from db.policies import update_policy
        update_policy("exact_whitelist", "uptime\nhostname")
        r = client.get("/api/policy/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200
        body = r.json()
        assert body.get("exact_whitelist") == "uptime\nhostname"
        assert "dashboard_version" in body

    def test_policy_token_ip_mismatch(self, client, gateway_headers):
        r = client.get("/api/policy/10.0.0.2", headers=gateway_headers)  # token is for .1
        assert r.status_code == 401

    def test_policy_with_session(self, auth_client):
        register_gateway("10.0.0.8", "sess-gw", "v15.3")
        r = auth_client.get("/api/policy/10.0.0.8")
        assert r.status_code == 200

    def test_policy_changes_requires_session(self, client):
        assert client.get("/api/policy_changes").status_code == 401

    def test_policy_changes_with_session(self, auth_client):
        assert auth_client.get("/api/policy_changes").status_code == 200


class TestPollAuth:

    def test_poll_requires_auth(self, client):
        assert client.get("/api/poll/10.0.0.1").status_code == 401

    def test_poll_with_token(self, client, gateway_headers):
        r = client.get("/api/poll/10.0.0.1", headers=gateway_headers)
        assert r.status_code == 200

    def test_poll_token_ip_mismatch(self, client, gateway_headers):
        assert client.get("/api/poll/10.0.0.2", headers=gateway_headers).status_code == 401


class TestRegisterOracle:

    def test_register_no_longer_leaks_existing_token(self, client, gateway_headers):
        # The seeded gateway 10.0.0.1 has a token; an unauthenticated caller
        # must not be able to obtain it.
        r = client.post("/api/register", json={
            "ip": "10.0.0.1", "hostname": "attacker", "version": "v0"})
        assert r.status_code == 401
        assert "gateway_token" not in r.json()

    def test_enrollment_mints_token_and_is_single_use(self, client, enrollment_token):
        h = {"X-Enrollment-Token": enrollment_token}
        r = client.post("/api/register", json={
            "ip": "10.0.9.9", "hostname": "fresh", "version": "v15.3"}, headers=h)
        assert r.status_code == 200
        token = r.json()["gateway_token"]
        assert token and len(token) >= 32
        # The freshly enrolled gateway can now read its own policy...
        p = client.get("/api/policy/10.0.9.9", headers={"X-Gateway-Token": token})
        assert p.status_code == 200
        # ...and the enrollment token cannot be replayed.
        r2 = client.post("/api/register", json={
            "ip": "10.0.9.10", "hostname": "again", "version": "v15.3"}, headers=h)
        assert r2.status_code == 401

    def test_gateway_token_cannot_register_a_different_ip(self, client, gateway_headers):
        r = client.post("/api/register", json={
            "ip": "10.0.0.42", "hostname": "spoof", "version": "v15.3"},
            headers=gateway_headers)  # token is for 10.0.0.1
        assert r.status_code == 401


class TestEnrollmentServing:

    def test_enroll_serve_does_not_consume(self, client, enrollment_token):
        save_ssh_keys("ssh-ed25519 AAAAtestkey test@example")
        r1 = client.get(f"/api/enroll?token={enrollment_token}")
        assert r1.status_code == 200
        assert f"ESHU_ENROLL_TOKEN='{enrollment_token}'" in r1.text
        # Peeking at the bootstrap must NOT burn the token (it is consumed at
        # /api/register, the authoritative enrollment step).
        r2 = client.get(f"/api/enroll?token={enrollment_token}")
        assert r2.status_code == 200
        assert f"ESHU_ENROLL_TOKEN='{enrollment_token}'" in r2.text

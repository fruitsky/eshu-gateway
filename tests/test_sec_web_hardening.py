"""Chunk 4: XSS input validation, CSP/security headers and CSRF.

The browser is the operator's root console, so server data must be validated at
the API boundary, scripts/frames locked down, and session-cookie writes forced
through a same-origin or custom-header check.
"""
import base64


def b64(s):
    return base64.b64encode(s.encode()).decode()


# ── server-side input validation ───────────────────────────────────────────
def test_sec_chunk4_heartbeat_rejects_script_hostname(client, gateway_headers):
    r = client.post("/api/gateway-heartbeat", json={
        "ip": "10.0.0.1", "hostname": "x');alert(1);//",
    }, headers=gateway_headers)
    assert r.status_code == 400


def test_sec_chunk4_register_rejects_script_ip(client):
    r = client.post("/api/register", json={
        "ip": "1.2.3.4');//", "hostname": "ok", "version": "v15.3",
    })
    assert r.status_code == 400


def test_sec_chunk4_register_rejects_script_hostname(client, gateway_headers):
    r = client.post("/api/register", json={
        "ip": "10.0.0.1", "hostname": "x');alert(1);//", "version": "v15.3",
    }, headers=gateway_headers)
    assert r.status_code == 400


def test_sec_chunk4_valid_gateway_identity_is_accepted(client, gateway_headers):
    r = client.post("/api/gateway-heartbeat", json={
        "ip": "10.0.0.1", "hostname": "my-gateway.local",
    }, headers=gateway_headers)
    assert r.status_code == 200


# ── CSRF on session-cookie writes ──────────────────────────────────────────
def _without_csrf(client):
    client.headers.pop("X-Eshu-CSRF", None)
    return client


def test_sec_chunk4_session_write_without_csrf_is_403(auth_client):
    _without_csrf(auth_client)
    r = auth_client.post("/api/freeze")
    assert r.status_code == 403


def test_sec_chunk4_session_write_with_csrf_header_is_allowed(auth_client):
    _without_csrf(auth_client)
    r = auth_client.post("/api/freeze", headers={"X-Eshu-CSRF": "1"})
    assert r.status_code == 200


def test_sec_chunk4_session_write_with_matching_origin_is_allowed(auth_client):
    _without_csrf(auth_client)
    r = auth_client.post("/api/freeze", headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_sec_chunk4_session_write_with_foreign_origin_is_403(auth_client):
    _without_csrf(auth_client)
    r = auth_client.post("/api/freeze",
                         headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_sec_chunk4_login_requires_csrf(client):
    r = client.post("/api/auth/login", json={"password": "test"})
    assert r.status_code == 403
    r2 = client.post("/api/auth/login", json={"password": "test"},
                     headers={"X-Eshu-CSRF": "1"})
    assert r2.status_code == 200


def test_sec_chunk4_unauthenticated_write_still_401(client):
    # No session cookie and no token: the auth gate owns the response (401),
    # not the CSRF gate (403).
    r = client.post("/api/freeze")
    assert r.status_code == 401


def test_sec_chunk4_gateway_token_write_is_exempt(client, gateway_headers):
    r = client.post("/api/request", json={
        "target_ip": "10.0.0.1",
        "encoded_command": b64("uptime"),
    }, headers=gateway_headers)
    assert r.status_code == 200


# ── CSP / security headers ─────────────────────────────────────────────────
def test_sec_chunk4_security_headers_on_html_and_api(client):
    for path in ("/", "/api/version"):
        r = client.get(path)
        assert r.status_code == 200
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "same-origin"

"""Chunk 5: authorisation gaps, weak tokens and first-run exposure."""
import re
import time

import pytest


@pytest.fixture
def agent_headers(temp_db):
    from db.agent_tokens import create_agent_token
    raw, _ = create_agent_token("sec-chunk5-agent")
    return {"Authorization": "Bearer " + raw}


def _window(ip, command="uptime"):
    from db.windows import create_approved_window
    return create_approved_window(ip, command)


def _window_request_payload():
    return {
        "gateway_ip": "10.0.0.1",
        "command": "uptime",
        "window_start": int(time.time()) + 3600,
    }


# ── 5a /api/policies/test ─────────────────────────────────────────────────
def test_sec_chunk5_policy_test_requires_session(client):
    assert client.get("/api/policies/test",
                      params={"command": "uptime"}).status_code == 401


def test_sec_chunk5_policy_test_allows_session(auth_client):
    r = auth_client.get("/api/policies/test", params={"command": "uptime"})
    assert r.status_code == 200


# ── 5b /api/approved-windows ──────────────────────────────────────────────
def test_sec_chunk5_approved_windows_requires_auth(client):
    assert client.get("/api/approved-windows").status_code == 401


def test_sec_chunk5_approved_windows_gateway_is_scoped(client, gateway_headers):
    _window("10.0.0.2")
    _window("10.0.0.1")
    r = client.get("/api/approved-windows", headers=gateway_headers)
    assert r.status_code == 200
    rows = r.json()
    assert rows
    assert all(w["target_ip"] == "10.0.0.1" for w in rows)
    assert all("token" not in w for w in rows)


def test_sec_chunk5_approved_windows_agent_is_allowed(client, agent_headers):
    _window("10.0.0.1")
    assert client.get("/api/approved-windows",
                      headers=agent_headers).status_code == 200


def test_sec_chunk5_approved_windows_session_is_allowed(auth_client):
    _window("10.0.0.1")
    assert auth_client.get("/api/approved-windows").status_code == 200


# ── 5c POST /api/window-requests ─────────────────────────────────────────
def test_sec_chunk5_window_request_requires_a_token(client):
    r = client.post("/api/window-requests", json=_window_request_payload())
    assert r.status_code == 401


def test_sec_chunk5_window_request_agent_is_allowed(client, agent_headers):
    r = client.post("/api/window-requests", json=_window_request_payload(),
                    headers=agent_headers)
    assert r.status_code == 200


def test_sec_chunk5_window_request_gateway_is_allowed(client, gateway_headers):
    r = client.post("/api/window-requests", json=_window_request_payload(),
                    headers=gateway_headers)
    assert r.status_code == 200


# ── 5d /api/enroll/token-status ──────────────────────────────────────────
def test_sec_chunk5_token_status_requires_session(client):
    assert client.get("/api/enroll/token-status",
                      params={"token": "x"}).status_code == 401


def test_sec_chunk5_token_status_allows_session(auth_client):
    from db.enrollment import generate_enrollment_token
    token = generate_enrollment_token()
    assert auth_client.get("/api/enroll/token-status",
                           params={"token": token}).status_code == 200


# ── 5e first-run set-password ────────────────────────────────────────────
def test_sec_chunk5_first_run_requires_setup_token(client):
    import main
    from db.auth import set_password_hash
    set_password_hash("")
    r = client.post("/api/auth/set-password",
                    json={"password": "a" * 12})
    assert r.status_code == 403
    r2 = client.post("/api/auth/set-password",
                     json={"password": "a" * 12},
                     headers={"X-Eshu-Setup-Token": main._SETUP_TOKEN})
    assert r2.status_code == 200


def test_sec_chunk5_first_run_allows_loopback(temp_db):
    import main
    from db.auth import set_password_hash
    from fastapi.testclient import TestClient
    set_password_hash("")
    c = TestClient(main.app, client=("127.0.0.1", 45678))
    r = c.post("/api/auth/set-password", json={"password": "a" * 12})
    assert r.status_code == 200


# ── 5f enrollment tokens ─────────────────────────────────────────────────
def test_sec_chunk5_enrollment_tokens_are_strong(temp_db):
    from db.enrollment import generate_enrollment_token
    tokens = {generate_enrollment_token() for _ in range(5)}
    assert len(tokens) == 5
    for token in tokens:
        assert re.fullmatch(r"[A-Za-z0-9_-]{32}", token), token


# ── 5g window tokens ─────────────────────────────────────────────────────
def test_sec_chunk5_window_tokens_are_24_alnum(temp_db):
    from db.windows import create_window_request
    w = _window("10.0.0.1")
    assert re.fullmatch(r"[A-Za-z0-9]{24}", w["token"]), w["token"]
    wr = create_window_request("10.0.0.1", "uptime",
                               window_start=int(time.time()) + 3600)
    assert re.fullmatch(r"[A-Za-z0-9]{24}", wr["token"]), wr["token"]


# ── 5h Host header in the enrollment bootstrap ───────────────────────────
def test_sec_chunk5_enroll_rejects_host_injection(client, enrollment_token):
    from db.enrollment import save_ssh_keys
    save_ssh_keys("ssh-ed25519 AAAAtestkey test@example")
    r = client.get(f"/api/enroll?token={enrollment_token}",
                   headers={"Host": 'evil"$(id)'})
    assert r.status_code == 400


def test_sec_chunk5_enroll_accepts_valid_hosts(client, enrollment_token):
    from db.enrollment import save_ssh_keys
    save_ssh_keys("ssh-ed25519 AAAAtestkey test@example")
    for host in ("dashboard.local:8000", "[::1]:8000"):
        r = client.get(f"/api/enroll?token={enrollment_token}",
                       headers={"Host": host})
        assert r.status_code == 200, (host, r.text)


# ── 5j password length ───────────────────────────────────────────────────
def test_sec_chunk5_password_minimum_is_12(auth_client):
    r = auth_client.post("/api/auth/set-password",
                         json={"password": "shortpass1"})  # 10 chars
    assert r.status_code == 400
    r2 = auth_client.post("/api/auth/set-password",
                          json={"password": "a" * 12})
    assert r2.status_code == 200


# ── 5k no plaintext password fallback ────────────────────────────────────
def test_sec_chunk5_plaintext_password_is_not_accepted(client):
    from db.auth import set_password_hash
    set_password_hash("legacy-plain")
    r = client.post("/api/auth/login", json={"password": "legacy-plain"},
                    headers={"X-Eshu-CSRF": "1"})
    assert r.status_code == 401


def test_sec_chunk5_pbkdf2_password_still_verified():
    from core.utils import _hash_password, _verify_password
    assert _verify_password("correct-horse", _hash_password("correct-horse")) is True
    assert _verify_password("wrong", _hash_password("correct-horse")) is False


# ── Chunk R4: forwarded headers defeat the loopback first-run check ───────
@pytest.mark.parametrize("header,value", [
    ("X-Forwarded-For", "203.0.113.5"),
    ("X-Real-IP", "203.0.113.5"),
    ("Forwarded", "for=203.0.113.5"),
])
def test_sec_R_forwarded_loopback_requires_setup_token(temp_db, header, value):
    import main
    from db.auth import set_password_hash
    from fastapi.testclient import TestClient
    set_password_hash("")
    c = TestClient(main.app, client=("127.0.0.1", 45680))
    r = c.post("/api/auth/set-password", json={"password": "a" * 12},
               headers={header: value})
    assert r.status_code == 403
    r2 = c.post("/api/auth/set-password", json={"password": "a" * 12},
                headers={header: value,
                         "X-Eshu-Setup-Token": main._SETUP_TOKEN})
    assert r2.status_code == 200

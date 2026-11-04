"""Session Overrides: a bounded JIT bypass for one session, one host at a time.

The host override is deliberately broad; these tests pin the narrower contract:
an explicit session id must match, an exact host beats any-host, an expired or
cancelled grant stops matching, and Zero-Trust/Freeze always win.
"""
import base64
import time

from db.gateways import (
    clear_trigger_freeze,
    register_gateway,
    set_gateway_token,
    set_gateway_zero_trust,
    set_trigger_freeze,
)
from db.overrides import (
    create_session_override,
    get_session_override,
    list_session_overrides,
    stop_session_override,
)
from db.requests import get_all_requests


def b64(s):
    return base64.b64encode(s.encode()).decode()


def post_request(client, headers, session_id, cmd="uptime", ip="10.0.0.1"):
    return client.post("/api/request", json={
        "target_ip": ip,
        "encoded_command": b64(cmd),
        "session_id": session_id,
    }, headers=headers)


def row_by_id(req_id):
    return next(r for r in get_all_requests() if r["id"] == int(req_id))


class TestSessionOverrideDb:

    def test_exact_host_beats_any_host(self):
        now = int(time.time())
        create_session_override("chat-1", "", now + 600, "any")
        exact = create_session_override("chat-1", "10.0.0.1", now + 600, "exact")
        found = get_session_override("chat-1", "10.0.0.1", now)
        assert found["id"] == exact["id"]
        # A second host still matches the any-host grant.
        assert get_session_override("chat-1", "10.0.0.2", now)["target_ip"] == ""

    def test_wrong_session_does_not_match(self):
        now = int(time.time())
        create_session_override("chat-1", "10.0.0.1", now + 600, "mine")
        assert get_session_override("chat-2", "10.0.0.1", now) == {}
        assert get_session_override("", "10.0.0.1", now) == {}

    def test_expired_and_ended_grants_are_invisible(self):
        now = int(time.time())
        create_session_override("chat-1", "", now - 1, "expired")
        assert get_session_override("chat-1", "10.0.0.1", now) == {}
        live = create_session_override("chat-2", "", now + 600, "live")
        stop_session_override(live["id"], now)
        assert get_session_override("chat-2", "10.0.0.1", now) == {}

    def test_regrant_replaces_the_same_scope(self):
        now = int(time.time())
        first = create_session_override("chat-1", "10.0.0.1", now + 600, "first")
        second = create_session_override("chat-1", "10.0.0.1", now + 900, "second")
        live = [o for o in list_session_overrides(now) if o["active"]]
        assert [o["id"] for o in live] == [second["id"]]
        assert get_session_override("chat-1", "10.0.0.1", now)["reason"] == "second"
        ended = next(o for o in list_session_overrides(now) if o["id"] == first["id"])
        assert ended["active"] is False


class TestSessionOverrideApi:

    def test_requires_auth(self, client, gateway_headers):
        assert client.get("/api/session-overrides").status_code in (401, 403)
        assert client.post("/api/session-overrides", json={
            "session_id": "chat-1", "minutes": 30, "reason": "x"
        }).status_code in (401, 403)

    def test_validates_the_grant(self, auth_client, gateway_headers):
        bad_id = auth_client.post("/api/session-overrides", json={
            "session_id": "not a valid id!", "minutes": 30, "reason": "x"
        })
        assert bad_id.status_code == 400
        no_reason = auth_client.post("/api/session-overrides", json={
            "session_id": "chat-1", "minutes": 30, "reason": ""
        })
        assert no_reason.status_code == 400
        bad_minutes = auth_client.post("/api/session-overrides", json={
            "session_id": "chat-1", "minutes": 0, "reason": "x"
        })
        assert bad_minutes.status_code == 400
        unknown_host = auth_client.post("/api/session-overrides", json={
            "session_id": "chat-1", "target_ip": "10.99.99.99",
            "minutes": 30, "reason": "x"
        })
        assert unknown_host.status_code == 404

    def test_create_list_and_cancel(self, auth_client, gateway_headers):
        r = auth_client.post("/api/session-overrides", json={
            "session_id": "chat-1", "target_ip": "10.0.0.1",
            "minutes": 30, "reason": "watching this task"
        })
        assert r.status_code == 200
        ov = r.json()["override"]
        assert ov["session_id"] == "chat-1"
        assert ov["target_ip"] == "10.0.0.1"
        assert ov["until"] > int(time.time())

        listing = auth_client.get("/api/session-overrides").json()
        active = [o for o in listing["overrides"] if o["active"]]
        assert [o["id"] for o in active] == [ov["id"]]
        assert active[0]["remaining"] > 0

        r = auth_client.delete("/api/session-overrides/%d" % ov["id"])
        assert r.status_code == 200
        listing = auth_client.get("/api/session-overrides").json()
        assert not [o for o in listing["overrides"] if o["active"]]

    def test_refuses_a_zero_trust_host(self, auth_client, gateway_headers):
        set_gateway_zero_trust("10.0.0.1", True)
        r = auth_client.post("/api/session-overrides", json={
            "session_id": "chat-1", "target_ip": "10.0.0.1",
            "minutes": 30, "reason": "x"
        })
        assert r.status_code == 400


class TestSessionOverrideAutoApprove:

    def test_matching_session_and_host_auto_approves(self, auth_client, gateway_headers):
        create_session_override("chat-1", "10.0.0.1",
                                int(time.time()) + 600, "watching")
        r = post_request(auth_client, gateway_headers, "chat-1")
        data = r.json()
        assert data.get("override") is True
        assert data.get("scope") == "session"
        row = row_by_id(data["id"])
        assert row["status"] == "approved"
        assert row["reason"].startswith("session-override:")

    def test_any_host_covers_another_gateway(self, auth_client, gateway_headers):
        register_gateway("10.0.0.2", "second", "v15.3")
        set_gateway_token("10.0.0.2", "testtoken-10-0-0-2")
        create_session_override("chat-1", "", int(time.time()) + 600, "any host")
        r = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.2",
            "encoded_command": b64("uptime"),
            "session_id": "chat-1",
        }, headers={"X-Gateway-Token": "testtoken-10-0-0-2"})
        assert r.json().get("override") is True
        assert r.json().get("scope") == "session"

    def test_wrong_host_does_not_match(self, auth_client, gateway_headers):
        create_session_override("chat-1", "10.0.0.2",
                                int(time.time()) + 600, "other host")
        r = post_request(auth_client, gateway_headers, "chat-1")
        assert r.json().get("override") is not True
        assert row_by_id(r.json()["id"])["status"] == "pending"

    def test_untagged_command_does_not_match(self, auth_client, gateway_headers):
        create_session_override("chat-1", "", int(time.time()) + 600, "any")
        r = post_request(auth_client, gateway_headers, "")
        assert r.json().get("override") is not True
        assert row_by_id(r.json()["id"])["status"] == "pending"

    def test_expired_override_does_not_match(self, auth_client, gateway_headers):
        create_session_override("chat-1", "", int(time.time()) - 1, "expired")
        r = post_request(auth_client, gateway_headers, "chat-1")
        assert r.json().get("override") is not True
        assert row_by_id(r.json()["id"])["status"] == "pending"

    def test_zero_trust_beats_the_override(self, auth_client, gateway_headers):
        set_gateway_zero_trust("10.0.0.1", True)
        create_session_override("chat-1", "10.0.0.1",
                                int(time.time()) + 600, "should not win")
        r = post_request(auth_client, gateway_headers, "chat-1")
        assert r.json().get("override") is not True
        assert row_by_id(r.json()["id"])["status"] == "pending"

    def test_freeze_beats_the_override(self, auth_client, gateway_headers):
        create_session_override("chat-1", "10.0.0.1",
                                int(time.time()) + 600, "should not win")
        set_trigger_freeze()
        try:
            r = post_request(auth_client, gateway_headers, "chat-1")
        finally:
            clear_trigger_freeze()
        assert r.json().get("override") is not True
        assert row_by_id(r.json()["id"])["status"] == "pending"

    def test_host_override_still_works_for_untagged_traffic(self, auth_client, gateway_headers):
        auth_client.post("/api/gateways/10.0.0.1/override",
                         json={"minutes": 30, "reason": "onboarding"})
        r = post_request(auth_client, gateway_headers, "")
        data = r.json()
        assert data.get("override") is True
        assert data.get("scope") == "host"

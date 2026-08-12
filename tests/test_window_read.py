from fastapi.testclient import TestClient
from main import app
import os

_FEATURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard", "static", "features", "approved_windows.sh")


def _read_feature():
    with open(_FEATURE, "r", encoding="utf-8") as f:
        return f.read()


def _anon():
    """A fresh unauthenticated TestClient (no session cookie) against the
    same app/DB as the session-authed fixtures."""
    return TestClient(app)


class TestWindowReadOpen:

    def _make_window(self, auth_client, command="uptime", recurring=True):
        body = {"target_ip": "10.0.0.1", "command": command, "match_type": "exact"}
        if recurring:
            body.update({"days_of_week": 62, "execution_time": 10800})
        else:
            body.update({"window_start": 4102444800, "max_executions": 1})
        return auth_client.post("/api/approved-windows", json=body)

    def test_list_open_without_token_for_anonymous(self, client, auth_client):
        r = self._make_window(auth_client)
        assert r.status_code == 200
        anon = _anon()
        wins = anon.get("/api/approved-windows").json()
        assert len(wins) >= 1
        assert all("token" not in w for w in wins)
        assert all("retrieval_key" not in w for w in wins)
        assert all("command" in w and "status" in w and "target_ip" in w for w in wins)

    def test_list_includes_token_for_session(self, client, auth_client):
        self._make_window(auth_client)
        wins = auth_client.get("/api/approved-windows").json()
        assert len(wins) >= 1
        assert any("token" in w for w in wins)
        assert any("retrieval_key" in w for w in wins)

    def test_single_window_open_by_retrieval_key(self, client, auth_client):
        w = self._make_window(auth_client).json()
        anon = _anon()
        # Anonymous callers must use the opaque retrieval_key, not the numeric id.
        r = anon.get(f"/api/approved-windows/{w['retrieval_key']}")
        assert r.status_code == 200
        assert "token" in r.json()
        assert r.json()["command"] == "uptime"

    def test_single_window_numeric_id_requires_session(self, client, auth_client):
        wid = self._make_window(auth_client).json()["id"]
        # Anonymous enumeration of sequential ids must not leak the token.
        assert _anon().get(f"/api/approved-windows/{wid}").status_code == 404
        assert _anon().get("/api/approved-windows/999999").status_code == 404
        # Session-authed callers (the UI) may still use the numeric id.
        assert auth_client.get(f"/api/approved-windows/{wid}").status_code == 200

    def test_request_poll_open_by_key_flow(self, client, auth_client):
        req = client.post("/api/window-requests", json={
            "gateway_ip": "10.0.0.1", "command": "hostname", "match_type": "exact",
            "window_start": 4102444800, "max_executions": 1
        }).json()
        assert "retrieval_key" in req
        rkey = req["retrieval_key"]
        anon = _anon()
        r = anon.get(f"/api/window-requests/{rkey}")
        assert r.status_code == 200
        assert r.json()["status"] == "pending_review"
        token = auth_client.post(f"/api/window-requests/{req['id']}/approve").json()["token"]
        r = anon.get(f"/api/window-requests/{rkey}")
        assert r.json()["status"] == "approved"
        assert r.json()["token"] == token
        # Numeric id no longer works for anonymous poll (enumeration blocked).
        assert anon.get(f"/api/window-requests/{req['id']}").status_code == 404
        assert anon.get("/api/window-requests/999999").status_code == 404

    def test_request_poll_open_by_key_recurring(self, client, auth_client):
        req = client.post("/api/window-requests", json={
            "gateway_ip": "10.0.0.1", "command": "apt update", "match_type": "prefix",
            "days_of_week": 62, "execution_time": 10800
        }).json()
        rkey = req["retrieval_key"]
        anon = _anon()
        assert anon.get(f"/api/window-requests/{rkey}").json()["status"] == "pending_review"
        token = auth_client.post(f"/api/window-requests/{req['id']}/approve").json()["token"]
        assert anon.get(f"/api/window-requests/{rkey}").json()["token"] == token

    def test_write_endpoints_still_gated(self, client):
        assert client.post("/api/approved-windows", json={
            "target_ip": "10.0.0.1", "command": "uptime", "days_of_week": 62, "execution_time": 10800
        }).status_code in (401, 403)
        assert client.post("/api/window-requests/1/approve").status_code in (401, 403)
        assert client.post("/api/window-requests/1/deny").status_code in (401, 403)
        assert client.delete("/api/approved-windows/1").status_code in (401, 403)

    def _submit(self, client, command, **kw):
        body = {"gateway_ip": "10.0.0.1", "command": command, "match_type": "exact",
                "window_start": 4102444800, "max_executions": 1}
        body.update(kw)
        return client.post("/api/window-requests", json=body)

    def test_multiple_pending_requests_no_collision(self, client):
        # Regression: agent requests all used token='' which collided on the
        # UNIQUE token column -> 500 IntegrityError for every request after the
        # first. Each pending request now gets a unique placeholder token.
        r1 = self._submit(client, "hostname")
        assert r1.status_code == 200
        r2 = self._submit(client, "date")
        assert r2.status_code == 200
        assert r2.json()["id"] != r1.json()["id"]
        r3 = self._submit(client, "uptime")
        assert r3.status_code == 200

    def test_expires_at_null_accepted(self, client):
        # The documented payload may send "expires_at": null (meaning never);
        # it must not 422.
        r = self._submit(client, "uptime", expires_at=None)
        assert r.status_code == 200
        assert r.json()["status"] == "pending_review"

    def test_denied_request_then_new_submission(self, client, auth_client):
        # A denied request keeps its placeholder token; a subsequent submission
        # must still succeed (previously both held token='' and collided).
        r1 = self._submit(client, "hostname")
        rid = r1.json()["id"]
        assert auth_client.post(f"/api/window-requests/{rid}/deny").status_code == 200
        r2 = self._submit(client, "date")
        assert r2.status_code == 200


class TestAtomicWindowConsumption:

    def test_gateway_captures_claim_response_and_fails_closed(self):
        # Regression (Hermes findings #1/#2): single-use tokens must not be
        # replayable inside the ~30s local-cache sync lag. The gateway must
        # claim-and-burn against the server BEFORE executing and only run on a
        # 200 response; 404/403 must fail closed. Network errors (000) fail open.
        src = _read_feature()
        assert '%{http_code}' in src
        assert 'WINDOW_CLAIM_HTTP' in src
        assert 'window consumed or disabled' in src
        # Fail-closed branch comes before execution.
        assert src.index('"$WINDOW_CLAIM_HTTP" = "404"') < src.index('run_sanitized')
        # The old fire-and-forget notify must be gone.
        assert '>/dev/null 2>&1 || true' not in src.split('WINDOW_CLAIM_HTTP')[0]

    def test_single_use_execute_rejects_second_claim(self, client, auth_client, gateway_headers):
        # Server-side atomicity: the second /execute call for a max_executions=1
        # window must fail (404) so a gateway that checks the response fails
        # closed. This is what the approved_windows.sh fix relies on.
        import time
        now = int(time.time())
        r = auth_client.post("/api/approved-windows", json={
            "target_ip": "10.0.0.1", "command": "uptime", "match_type": "exact",
            "window_start": now - 10, "window_end": now + 3600, "max_executions": 1
        })
        token = r.json()["token"]
        first = auth_client.post(f"/api/approved-windows/execute/{token}", headers=gateway_headers)
        second = auth_client.post(f"/api/approved-windows/execute/{token}", headers=gateway_headers)
        assert first.status_code == 200
        assert second.status_code == 404

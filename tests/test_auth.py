class TestSetPassword:

    def test_set_password_succeeds(self, auth_client):
        r = auth_client.post("/api/auth/set-password", json={"password": "newpass"})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_set_password_min_length(self, auth_client):
        r = auth_client.post("/api/auth/set-password", json={"password": "ab"})
        assert r.status_code == 400


class TestPasswordIsMandatory:

    def test_clear_password_endpoint_removed(self, auth_client):
        # Password protection cannot be removed — the endpoint is gone.
        r = auth_client.post("/api/auth/clear-password")
        assert r.status_code == 404

    def test_protected_endpoint_fails_closed_without_password(self, client):
        # Even with no password set, protected endpoints must return 401 —
        # the only way in is the first-launch setup overlay.
        from db.auth import set_password_hash
        set_password_hash('')
        r = client.post("/api/approve/1")
        assert r.status_code == 401

    def test_set_password_still_allowed_on_first_run(self, client):
        # First-run setup: with no password, set-password works unauth'd.
        from db.auth import set_password_hash
        set_password_hash('')
        r = client.post("/api/auth/set-password", json={"password": "firstrun"})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestLogout:

    def test_logout_clears_cookie(self, client):
        client.post("/api/auth/login", json={"password": "test"})
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        assert r.cookies.get("eshu_session") is None or r.cookies.get("eshu_session") == ""


class TestProtectedEndpoint:

    def test_requires_auth(self, client):
        r = client.post("/api/approve/1")
        assert r.status_code == 401

class TestSSHKeys:

    def test_save_and_get_ssh_keys(self):
        from db.enrollment import save_ssh_keys, get_ssh_keys
        save_ssh_keys("my-eshu-key")
        keys = get_ssh_keys()
        assert keys.get("eshu_ssh_key") == "my-eshu-key"

    def test_keys_not_set_initially(self):
        from db.enrollment import get_ssh_keys
        keys = get_ssh_keys()
        assert keys.get("eshu_ssh_key") == ""


class TestTokenGeneration:

    def test_generate_token_returns_nonempty(self):
        from db.enrollment import generate_enrollment_token
        token = generate_enrollment_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_status_valid_and_unused(self, client):
        from db.enrollment import generate_enrollment_token
        token = generate_enrollment_token()
        r = client.get(f"/api/enroll/token-status?token={token}")
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["used"] is False

    def test_token_status_invalid_for_nonexistent(self, client):
        r = client.get("/api/enroll/token-status?token=nonexistent")
        assert r.status_code == 200
        assert r.json()["valid"] is False


class TestEnrollmentScript:

    def test_invalid_token_returns_error(self, client):
        r = client.get("/api/enroll?token=bad-token")
        assert r.status_code == 200
        assert "exit 1" in r.text

    def test_no_ssh_keys_returns_error(self, client):
        from db.enrollment import generate_enrollment_token
        token = generate_enrollment_token()
        r = client.get(f"/api/enroll?token={token}")
        assert r.status_code == 200
        assert "exit 1" in r.text

    def test_valid_script_contains_installer_url(self, client):
        from db.enrollment import generate_enrollment_token, save_ssh_keys
        save_ssh_keys("eshu-key")
        token = generate_enrollment_token()
        r = client.get(f"/api/enroll?token={token}")
        assert r.status_code == 200
        assert "static/eshu-gateway-install.sh" in r.text
        assert "DOWNLOAD_URL" not in r.text
        assert "eshu-gateway" in r.text

    def test_script_is_privilege_aware(self, client):
        # The bootstrap must never hardcode `sudo` (breaks root-only shells like
        # TrueNAS and rootless hosts like HA OS). It branches on id -u / sudo.
        from db.enrollment import generate_enrollment_token, save_ssh_keys
        save_ssh_keys("eshu-key")
        token = generate_enrollment_token()
        r = client.get(f"/api/enroll?token={token}")
        assert r.status_code == 200
        assert '"$(id -u)"' in r.text                # root branch present
        assert "command -v sudo" in r.text           # sudo branch present
        assert "requires root or sudo" in r.text     # clear no-root/no-sudo message
        assert 'sudo bash /tmp/eshu-install.sh' in r.text  # sudo still used when available
        assert r.text.index('"$(id -u)"') < r.text.index("command -v sudo")  # root checked first

    def test_script_consumes_token(self, client):
        from db.enrollment import generate_enrollment_token, save_ssh_keys
        save_ssh_keys("eshu-key")
        token = generate_enrollment_token()
        client.get(f"/api/enroll?token={token}")
        r = client.get(f"/api/enroll/token-status?token={token}")
        assert r.json()["used"] is True


class TestFetchKeysAPI:

    def test_requires_auth(self, client):
        r = client.get("/api/enroll/keys")
        assert r.status_code == 401

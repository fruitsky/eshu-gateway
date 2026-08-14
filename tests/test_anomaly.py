import pytest

from db.requests import create_request
from db.gateways import register_gateway


@pytest.fixture(autouse=True)
def reset_profiles_cache():
    """Reset the module-level profile cache between tests."""
    from core import cmd_profiles
    cmd_profiles._profiles_cache = {'updated_at': 0, 'gateways': {}}
    yield
    cmd_profiles._profiles_cache = {'updated_at': 0, 'gateways': {}}


def _seed(gateway_ip, commands):
    for cmd in commands:
        create_request(gateway_ip, cmd, status="consumed")


class TestBaseCommand:

    def test_strips_common_prefix(self):
        from core.cmd_profiles import base_command
        assert base_command("sudo systemctl restart nginx") == "systemctl"
        assert base_command("docker ps") == "docker"
        assert base_command("ls -la") == "ls"
        assert base_command("  uptime  ") == "uptime"

    def test_empty(self):
        from core.cmd_profiles import base_command
        assert base_command("") is None
        assert base_command("   ") is None


class TestAnomaly:

    def test_grace_period_no_flag(self, temp_db):
        from core import cmd_profiles
        _seed("10.0.0.1", ["uptime"] * 5)  # fewer than MIN_SAMPLES
        cmd_profiles.refresh_profiles()
        assert cmd_profiles.get_anomaly("10.0.0.1", "reboot") is None

    def test_seen_binary_not_flagged(self, temp_db):
        from core import cmd_profiles
        _seed("10.0.0.1", ["docker ps"] * 15)
        cmd_profiles.refresh_profiles()
        assert cmd_profiles.get_anomaly("10.0.0.1", "docker ps -a") is None

    def test_unseen_binary_flagged(self, temp_db):
        from core import cmd_profiles
        _seed("10.0.0.1", ["docker ps"] * 15)
        cmd_profiles.refresh_profiles()
        a = cmd_profiles.get_anomaly("10.0.0.1", "dd if=/dev/zero of=/tmp/x")
        assert a is not None
        assert "dd" in a


class TestAnomalyApi:

    def test_requests_include_anomaly_for_pending(self, auth_client):
        for i in range(15):
            create_request("10.0.0.1", "docker ps", status="consumed")
        create_request("10.0.0.1", "dd if=/dev/zero of=/tmp/x", status="pending")
        from core import cmd_profiles
        cmd_profiles.refresh_profiles()

        r = auth_client.get("/api/requests")
        assert r.status_code == 200
        reqs = r.json()
        pending = [x for x in reqs if x["status"] == "pending"]
        dd = [x for x in pending if x["command"] == "dd if=/dev/zero of=/tmp/x"]
        assert dd
        assert dd[0].get("anomaly") is not None
        assert "dd" in dd[0]["anomaly"]

    def test_requests_anomaly_none_for_non_pending(self, auth_client):
        for i in range(15):
            create_request("10.0.0.1", "docker ps", status="consumed")
        from core import cmd_profiles
        cmd_profiles.refresh_profiles()

        r = auth_client.get("/api/requests")
        consumed = [x for x in r.json() if x["status"] == "consumed"]
        assert consumed
        assert all(x.get("anomaly") is None for x in consumed)

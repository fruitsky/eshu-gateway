import pytest

from db.requests import create_request
from db.gateways import register_gateway, get_gateways


@pytest.fixture(autouse=True)
def reset_learning_cache():
    """Reset the module-level gap cache between tests to avoid cross-test leakage."""
    from core import learning
    learning._gaps_cache = {'updated_at': 0, 'gateways': [], 'total_gaps': 0, 'new_gaps': 0}
    yield
    learning._gaps_cache = {'updated_at': 0, 'gateways': [], 'total_gaps': 0, 'new_gaps': 0}


def _seed(gateway_headers):
    """Register gateway + create repeated JIT approvals."""
    # create_request with status approved simulates JIT approvals
    for i in range(4):
        create_request("10.0.0.1", "systemctl restart nginx", status="approved")
    for i in range(2):
        create_request("10.0.0.1", "apt update", status="approved")


class TestLearningScanner:

    def test_compute_gaps_finds_repeated_approvals(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(4):
            create_request("10.0.0.1", "systemctl restart nginx", status="approved")
        from core.learning import compute_gaps
        gaps = compute_gaps()
        assert gaps["total_gaps"] == 1
        gw = gaps["gateways"][0]
        assert gw["ip"] == "10.0.0.1"
        assert gw["hostname"] == "test-host"
        assert gw["gaps"][0]["command"] == "systemctl restart nginx"
        assert gw["gaps"][0]["approved_count"] == 4
        assert gw["gaps"][0]["is_new"] is True

    def test_ignores_below_threshold(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(2):  # only 2, below threshold of 3
            create_request("10.0.0.1", "uptime", status="approved")
        from core.learning import compute_gaps
        gaps = compute_gaps()
        assert gaps["total_gaps"] == 0

    def test_ignores_allowlisted(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(4):
            create_request("10.0.0.1", "uptime", status="approved")
        # Add uptime to exact allowlist
        from db.policies import update_policy
        update_policy("exact_whitelist", "uptime")
        from core.learning import compute_gaps
        gaps = compute_gaps()
        assert gaps["total_gaps"] == 0

    def test_ignores_dismissed(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(4):
            create_request("10.0.0.1", "whoami", status="approved")
        from db.misc import dismiss_policy_gap
        dismiss_policy_gap("whoami")
        from core.learning import compute_gaps
        gaps = compute_gaps()
        assert gaps["total_gaps"] == 0

    def test_finds_repeated_denials_as_blocklist_suggestions(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        from core.learning import MIN_DENIALS
        for i in range(MIN_DENIALS):
            create_request("10.0.0.1", "docker rm -f", status="denied")
        from core.learning import compute_gaps
        gaps = compute_gaps()
        deny_gaps = [g for g in gaps["gateways"][0]["gaps"] if g["kind"] == "deny"]
        assert len(deny_gaps) == 1
        assert deny_gaps[0]["command"] == "docker rm -f"
        assert deny_gaps[0]["count"] == MIN_DENIALS
        assert deny_gaps[0]["is_new"] is True

    def test_ignores_denials_below_threshold(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        from core.learning import MIN_DENIALS
        for i in range(MIN_DENIALS - 1):
            create_request("10.0.0.1", "kill -9 1", status="denied")
        from core.learning import compute_gaps
        gaps = compute_gaps()
        assert gaps["total_gaps"] == 0

    def test_ignores_blocklisted_denials(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        from core.learning import MIN_DENIALS
        for i in range(MIN_DENIALS):
            create_request("10.0.0.1", "docker rm -f", status="denied")
        from db.policies import update_policy
        update_policy("regex_blacklist", "docker rm -f")
        from core.learning import compute_gaps
        gaps = compute_gaps()
        assert gaps["total_gaps"] == 0

    def test_mark_all_seen_dismisses_gaps(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(4):
            create_request("10.0.0.1", "df -h", status="approved")
        from core.learning import mark_all_seen, refresh_gaps
        # Initial: gap is new
        gaps = refresh_gaps()
        assert gaps["new_gaps"] == 1
        # Mark seen dismisses all current gaps so the screen clears
        mark_all_seen()
        gaps = refresh_gaps()
        assert gaps["total_gaps"] == 0
        assert gaps["new_gaps"] == 0

    def test_seen_persists_across_refresh(self, temp_db):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(4):
            create_request("10.0.0.1", "ls -la", status="approved")
        from core.learning import mark_all_seen, refresh_gaps
        mark_all_seen()
        refresh_gaps()
        refresh_gaps()
        gaps = refresh_gaps()
        assert gaps["new_gaps"] == 0
        assert gaps["total_gaps"] == 0


class TestLearningApi:

    def test_gaps_requires_auth(self, client):
        r = client.get("/api/learning/gaps")
        assert r.status_code in (401, 403)

    def test_refresh_requires_auth(self, client):
        r = client.post("/api/learning/gaps/refresh")
        assert r.status_code in (401, 403)

    def test_mark_seen_requires_auth(self, client):
        r = client.post("/api/learning/gaps/mark-seen")
        assert r.status_code in (401, 403)

    def test_gaps_endpoint_returns_data(self, auth_client):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(4):
            create_request("10.0.0.1", "systemctl restart nginx", status="approved")
        from core.learning import refresh_gaps
        refresh_gaps()
        r = auth_client.get("/api/learning/gaps")
        assert r.status_code == 200
        data = r.json()
        assert "gateways" in data
        assert "total_gaps" in data
        assert "new_gaps" in data
        assert data["total_gaps"] >= 1

    def test_mark_seen_endpoint(self, auth_client):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(4):
            create_request("10.0.0.1", "date", status="approved")
        from core.learning import refresh_gaps
        refresh_gaps()
        r = auth_client.post("/api/learning/gaps/mark-seen")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        r = auth_client.get("/api/learning/gaps")
        assert r.json()["new_gaps"] == 0

    def test_refresh_endpoint(self, auth_client):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        r = auth_client.post("/api/learning/gaps/refresh")
        assert r.status_code == 200
        assert "gateways" in r.json()

    def test_allowlisted_command_not_in_gaps(self, auth_client):
        register_gateway("10.0.0.1", "test-host", "v15.3")
        for i in range(4):
            create_request("10.0.0.1", "systemctl restart nginx", status="approved")
        from db.policies import update_policy
        from core.learning import refresh_gaps
        update_policy("exact_whitelist", "systemctl restart nginx")
        refresh_gaps()
        r = auth_client.get("/api/learning/gaps")
        assert r.status_code == 200
        # After allowlisting, gap should disappear on next scan
        r2 = auth_client.post("/api/learning/gaps/refresh")
        assert r2.json()["total_gaps"] == 0

import base64
import time


def b64(s):
    return base64.b64encode(s.encode()).decode()


class TestStatsExtended:

    def _seed_data(self, auth_client, gateway_headers):
        """Create a mix of requests and windows for stats testing."""
        from db.requests import create_request
        from db.windows import create_approved_window
        req_ids = []
        for cmd in ["uptime", "df -h", "ls -la", "whoami", "cat /etc/hostname"]:
            rid = create_request("10.0.0.1", cmd, status="auto-approved")
            req_ids.append(rid)
        for cmd in ["systemctl restart nginx", "journalctl -xe"]:
            rid = create_request("10.0.0.1", cmd, status="approved")
            req_ids.append(rid)
        rid = create_request("10.0.0.1", "rm -rf /tmp/test", status="blocked")
        req_ids.append(rid)
        rid = create_request("10.0.0.1", "reboot", status="denied")
        req_ids.append(rid)
        create_approved_window("10.0.0.1", "uptime", window_end=3600, label="test-window", origin="human")
        create_approved_window("10.0.0.1", "date", window_end=3600, label="ai-window", origin="ai")
        return req_ids

    def test_extended_returns_new_keys(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=30&extended=1")
        assert r.status_code == 200
        data = r.json()
        assert "daily" in data
        assert "per_gateway" in data
        assert "top_commands" in data
        assert "hourly_heatmap" in data
        assert "automation_trend" in data
        assert "windows_summary" in data
        assert "gateway_health" in data

    def test_hourly_heatmap_has_24_entries(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=30&extended=1")
        data = r.json()
        assert len(data["hourly_heatmap"]) == 24

    def test_hourly_heatmap_values_are_numbers(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=30&extended=1")
        data = r.json()
        for v in data["hourly_heatmap"]:
            assert isinstance(v, int)

    def test_automation_trend_dates(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=7&extended=1")
        data = r.json()
        assert len(data["automation_trend"]) == 7
        for entry in data["automation_trend"]:
            assert "date" in entry
            assert "auto_approved" in entry
            assert "jit_approved" in entry
            assert "automation_pct" in entry

    def test_windows_summary(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=30&extended=1")
        data = r.json()
        ws = data["windows_summary"]
        assert ws["total"] >= 2
        assert ws["ai_created"] >= 1

    def test_gateway_health_has_keys(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=30&extended=1")
        data = r.json()
        gh = data["gateway_health"]
        assert "version_distribution" in gh
        assert "total_gateways" in gh
        assert "online_gateways" in gh
        assert "token_coverage" in gh
        assert gh["total_gateways"] >= 1
        assert gh["token_coverage"] >= 0

    def test_extended_does_not_affect_basic_response(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=30")
        data = r.json()
        assert "daily" in data
        assert "per_gateway" in data
        assert "top_commands" in data
        assert "hourly_heatmap" not in data
        assert "automation_trend" not in data
        assert "windows_summary" not in data
        assert "gateway_health" not in data

    def test_extended_empty_database(self, client):
        r = client.get("/api/statistics?days=7&extended=1")
        assert r.status_code == 200
        data = r.json()
        assert len(data["hourly_heatmap"]) == 24
        assert sum(data["hourly_heatmap"]) == 0
        assert len(data["automation_trend"]) == 7
        for entry in data["automation_trend"]:
            assert entry["automation_pct"] == 0

    def test_extended_filter_by_days(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=1&extended=1")
        data = r.json()
        assert len(data["daily"]) == 1
        assert len(data["automation_trend"]) == 1

    def test_per_gateway_includes_mode(self, auth_client, gateway_headers):
        self._seed_data(auth_client, gateway_headers)
        r = auth_client.get("/api/statistics?days=30&extended=1")
        data = r.json()
        for gw in data["per_gateway"]:
            if gw["ip"] == "10.0.0.1":
                assert "mode" in gw

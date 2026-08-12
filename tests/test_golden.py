import os
import shutil

import pytest

_dashboard_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard")
_golden = os.path.join(_dashboard_dir, "static", "eshu-gateway-install.sh")
_edge = os.path.join(_dashboard_dir, "static", "dev", "eshu-gateway-install.sh")
_bak = os.path.join(_dashboard_dir, "static", "eshu-gateway-install.sh.bak")
_source = os.path.join(_dashboard_dir, "eshu-gateway-install.sh")
_source_bak = _source + ".test-bak"


def setup_module():
    if os.path.exists(_source):
        shutil.copy(_source, _source_bak)


def teardown_module():
    if os.path.exists(_source_bak):
        shutil.copy(_source_bak, _source)
        os.remove(_source_bak)


def _ensure_golden(auth_client):
    """Ensure golden exists and matches source installer before each test."""
    if os.path.exists(_source):
        shutil.copy(_source, _golden)


class TestSeed:

    def test_401_without_auth(self, client):
        r = client.post("/api/dev/seed")
        assert r.status_code == 401

    def test_seed_creates_edge_from_golden(self, auth_client):
        _ensure_golden(auth_client)
        if os.path.exists(_edge):
            os.remove(_edge)
        r = auth_client.post("/api/dev/seed")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert os.path.exists(_edge)
        assert os.path.getsize(_edge) == os.path.getsize(_golden)

    def test_seed_overwrites_existing_edge(self, auth_client):
        _ensure_golden(auth_client)
        with open(_edge, "w") as f:
            f.write("old content")
        r = auth_client.post("/api/dev/seed")
        assert r.status_code == 200
        with open(_golden) as f:
            golden_content = f.read()
        with open(_edge) as f:
            edge_content = f.read()
        assert edge_content == golden_content


class TestPromote:

    def test_401_without_auth(self, client):
        r = client.post("/api/dev/promote")
        assert r.status_code == 401

    def test_404_without_edge(self, auth_client):
        if os.path.exists(_edge):
            os.remove(_edge)
        r = auth_client.post("/api/dev/promote")
        assert r.status_code == 404

    def test_promote_creates_backup(self, auth_client):
        _ensure_golden(auth_client)
        if not os.path.exists(_edge):
            auth_client.post("/api/dev/seed")
        if os.path.exists(_bak):
            os.remove(_bak)
        r = auth_client.post("/api/dev/promote")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert os.path.exists(_bak)
        assert os.path.getsize(_bak) > 0

    def test_promote_updates_deployed_hash(self, auth_client):
        _ensure_golden(auth_client)
        if not os.path.exists(_edge):
            auth_client.post("/api/dev/seed")
        r = auth_client.post("/api/dev/promote")
        assert r.status_code == 200
        from db.misc import get_deployed_golden_hash
        assert get_deployed_golden_hash() is not None


class TestRollback:

    def test_401_without_auth(self, client):
        r = client.post("/api/dev/rollback")
        assert r.status_code == 401

    def test_404_without_backup(self, auth_client):
        if os.path.exists(_bak):
            os.remove(_bak)
        r = auth_client.post("/api/dev/rollback")
        assert r.status_code == 404

    def test_rollback_restores_golden(self, auth_client):
        _ensure_golden(auth_client)
        if not os.path.exists(_edge):
            auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        assert os.path.exists(_bak)
        with open(_bak) as f:
            bak_content = f.read()
        with open(_golden, "w") as f:
            f.write("modified content")
        r = auth_client.post("/api/dev/rollback")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        with open(_golden) as f:
            restored = f.read()
        assert restored == bak_content


class TestDevStatus:

    def test_401_without_auth(self, client):
        r = client.get("/api/dev/status")
        assert r.status_code == 401

    def test_returns_fields(self, auth_client):
        r = auth_client.get("/api/dev/status")
        assert r.status_code == 200
        data = r.json()
        assert "dashboard_version" in data
        assert "edge_exists" in data
        assert "backup_exists" in data
        assert "dev_gateway_count" in data
        assert "dev_gateways" in data


class TestGoldenVersion:

    def test_promote_returns_product_version(self, auth_client):
        _ensure_golden(auth_client)
        if not os.path.exists(_edge):
            auth_client.post("/api/dev/seed")
        from core.utils import DASHBOARD_VERSION
        r = auth_client.post("/api/dev/promote")
        assert r.status_code == 200
        # The promote response carries the product version — no counter exists anymore.
        assert r.json()["version"] == DASHBOARD_VERSION


class TestPipelineState:

    def test_dev_status_includes_pipeline_state(self, auth_client):
        r = auth_client.get("/api/dev/status")
        assert r.status_code == 200
        data = r.json()
        for key in ("pipeline_state", "golden_hash", "edge_hash",
                     "edge_matches_golden", "source_hash", "deployed_hash",
                     "gateway_count", "dashboard_version"):
            assert key in data, f"missing key: {key}"
        assert data["pipeline_state"] in ("needs_seed", "ready_for_dev",
                "dev_in_progress", "ready_for_promote", "clear")

    def test_dev_status_has_no_fleet_or_golden_version(self, auth_client):
        # The deploy counter and golden version were removed — only the product
        # version (dashboard_version) and hashes are reported now.
        r = auth_client.get("/api/dev/status")
        assert r.status_code == 200
        data = r.json()
        assert "fleet_version" not in data
        assert "golden_version" not in data
        assert data["dashboard_version"] == "v0.1.0"

    def test_deploy_sets_deployed_hash_not_counter(self, auth_client):
        _ensure_golden(auth_client)
        auth_client.post("/api/dev/seed")
        r1 = auth_client.post("/api/dev/promote")
        assert r1.status_code == 200
        assert r1.json()["version"] == "v0.1.0"
        # Second deploy also returns the same product version (no bump).
        auth_client.post("/api/dev/seed")
        r2 = auth_client.post("/api/dev/promote")
        assert r2.status_code == 200
        assert r2.json()["version"] == "v0.1.0"
        # But the deployed golden hash is tracked.
        assert "deployed_hash" in auth_client.get("/api/dev/status").json()

    def test_version_endpoint_reflects_product_version(self, auth_client):
        _ensure_golden(auth_client)
        auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        r = auth_client.get("/api/version")
        assert r.status_code == 200
        assert r.json()["version"] == "v0.1.0"

    def test_version_endpoint_blocked_for_gateway_token(self, client):
        r = client.post("/api/register", json={
            "ip": "10.0.0.1", "hostname": "token-host", "version": "v0.1.0"})
        token = r.json()["gateway_token"]
        r = client.get("/api/version", headers={"X-Gateway-Token": token})
        assert r.status_code == 401

    def test_pipeline_state_needs_seed_when_edge_differs(self, auth_client):
        _ensure_golden(auth_client)
        auth_client.post("/api/dev/seed")
        with open(_golden, "w") as f:
            f.write("new golden content")
        r = auth_client.get("/api/dev/status")
        assert r.json()["pipeline_state"] == "needs_seed"
        assert r.json()["edge_matches_golden"] is False

    def test_pipeline_state_dev_in_progress_when_triggered(self, auth_client):
        _ensure_golden(auth_client)
        auth_client.post("/api/dev/seed")
        from db.gateways import set_trigger_dev_update
        import time
        set_trigger_dev_update(str(int(time.time())))
        r = auth_client.get("/api/dev/status")
        assert r.json()["pipeline_state"] == "dev_in_progress"

    def test_promote_stores_deployed_hash(self, auth_client):
        _ensure_golden(auth_client)
        auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        r = auth_client.get("/api/dev/status")
        data = r.json()
        assert data["deployed_hash"] is not None
        assert data["golden_hash"] == data["deployed_hash"]

    def test_pipeline_state_clear_after_promote(self, auth_client):
        _ensure_golden(auth_client)
        auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        r = auth_client.get("/api/dev/status")
        assert r.json()["pipeline_state"] == "clear"

    def test_pipeline_state_clear_after_promote_clears_dev_trigger(self, auth_client):
        _ensure_golden(auth_client)
        auth_client.post("/api/dev/seed")
        from db.gateways import set_trigger_dev_update
        import time
        set_trigger_dev_update(str(int(time.time())))
        r = auth_client.get("/api/dev/status")
        assert r.json()["pipeline_state"] == "dev_in_progress"
        auth_client.post("/api/dev/promote")
        r = auth_client.get("/api/dev/status")
        assert r.json()["pipeline_state"] == "clear"

    def test_stale_dev_trigger_with_deployed_golden_shows_clear(self, auth_client):
        _ensure_golden(auth_client)
        auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        from db.gateways import set_trigger_dev_update
        import time
        set_trigger_dev_update(str(int(time.time())))
        r = auth_client.get("/api/dev/status")
        assert r.json()["pipeline_state"] == "clear"


class TestDevGatewaysEndpoint:

    def test_requires_auth(self, client):
        r = client.get("/api/dev-gateways")
        assert r.status_code == 401

    def test_returns_only_ip_hostname(self, auth_client):
        from db.gateways import register_gateway, set_gateway_mode
        register_gateway("10.0.0.1", "dev-host", "v15.4")
        set_gateway_mode("10.0.0.1", "dev")
        r = auth_client.get("/api/dev-gateways")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert set(data[0].keys()) == {"ip", "hostname"}
        assert "api_token" not in data[0]
        assert data[0]["ip"] == "10.0.0.1"

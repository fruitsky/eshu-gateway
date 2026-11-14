"""The Build → Edge → Fleet installer pipeline (X7).

Build is the committed, generated installer. Edge and Fleet are runtime
channels in their own directory (one per test, see conftest). Each test points
Build at a temp file, so a "dashboard deploy" is simulated by rewriting it and
the checkout is never touched.
"""
import os

import pytest

from core import installer_channels as ic


@pytest.fixture
def build(tmp_path, monkeypatch):
    path = tmp_path / "build-installer.sh"
    path.write_bytes(b"#!/bin/bash\necho build-1\n")
    monkeypatch.setattr(ic, "build_path", lambda: str(path))
    return path


def _deploy(build, text):
    """A dashboard deploy: only Build changes."""
    build.write_bytes(text.encode())


def _status(c):
    r = c.get("/api/dev/status")
    assert r.status_code == 200
    return r.json()


class TestAuth:

    @pytest.mark.parametrize("method,path", [
        ("post", "/api/dev/seed"), ("post", "/api/dev/promote"),
        ("post", "/api/dev/rollback"), ("get", "/api/dev/status")])
    def test_401_without_auth(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401


class TestFirstStart:

    def test_channels_start_as_build(self, auth_client, build):
        data = _status(auth_client)
        assert data["build_hash"] == data["edge_hash"] == data["fleet_hash"]
        assert data["pipeline_state"] == "clear"
        assert data["deployed_hash"] == data["fleet_hash"]

    def test_migration_starts_from_build_and_removes_the_old_copies(
            self, auth_client, build, tmp_path, monkeypatch):
        """Security BP1 and question 3: the pre-X7 copies are never used,
        there is no Rollback target until the first Deploy to Fleet, and the
        old files are deleted (the static mount served them publicly)."""
        dev = tmp_path / "static" / "dev"
        dev.mkdir(parents=True)
        legacy = {ic.FLEET: tmp_path / "static" / "old-fleet.sh",
                  ic.EDGE: dev / "old-edge.sh",
                  ic.FLEET_PREV: tmp_path / "static" / "old.bak"}
        for path in legacy.values():
            path.write_bytes(b"an installer of unknown age")
        monkeypatch.setattr(ic, "_legacy_paths", lambda: {k: str(v) for k, v in legacy.items()})
        assert auth_client.get("/api/gateway-script").content == build.read_bytes()
        assert auth_client.get("/static/dev/eshu-gateway-install.sh").content == build.read_bytes()
        assert ic.has_previous() is False
        assert _status(auth_client)["backup_exists"] is False
        assert auth_client.post("/api/dev/rollback").status_code == 404
        assert not any(p.exists() for p in legacy.values())
        assert not dev.exists()
        from db.audit import get_audit_log
        row = [r for r in get_audit_log(50) if r["event_type"] == "installer_channels_initialised"][0]
        assert "removed pre-X7 copies" in row["details"]
        assert "Rollback is available after the first Deploy to Fleet" in row["details"]

    def test_rollback_becomes_available_after_the_first_promote(self, auth_client, build):
        assert _status(auth_client)["backup_exists"] is False
        auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        assert _status(auth_client)["backup_exists"] is True

    def test_existing_channel_dir_is_forced_to_0700(self, auth_client, build):
        os.makedirs(ic.channel_dir(), mode=0o755, exist_ok=True)
        os.chmod(ic.channel_dir(), 0o755)
        _status(auth_client)
        assert os.stat(ic.channel_dir()).st_mode & 0o777 == 0o700

    def test_records_an_audit_row_once(self, auth_client, build):
        from db.audit import get_audit_log
        _status(auth_client)
        _status(auth_client)
        rows = [r for r in get_audit_log(50) if r["event_type"] == "installer_channels_initialised"]
        assert len(rows) == 1

    def test_notes_a_fleet_that_differs_from_the_last_promote(self, auth_client, build):
        from db.audit import get_audit_log
        from db.misc import set_deployed_golden_hash
        set_deployed_golden_hash("aaaaaaaaaaaa")
        _status(auth_client)
        row = [r for r in get_audit_log(50) if r["event_type"] == "installer_channels_initialised"][0]
        assert "differs from the last recorded promote" in row["details"]


class TestDeployChangesOnlyBuild:

    def test_a_deploy_leaves_edge_and_fleet_alone(self, auth_client, build):
        before = _status(auth_client)
        _deploy(build, "#!/bin/bash\necho build-2\n")
        after = _status(auth_client)
        assert after["build_hash"] != before["build_hash"]
        assert after["edge_hash"] == before["edge_hash"]
        assert after["fleet_hash"] == before["fleet_hash"]
        assert after["pipeline_state"] == "needs_seed"
        assert b"build-1" in auth_client.get("/api/gateway-script").content
        assert b"build-1" in auth_client.get("/static/eshu-gateway-install.sh").content
        assert b"build-1" in auth_client.get("/static/dev/eshu-gateway-install.sh").content


class TestPipeline:

    def test_seed_copies_build_to_edge_only(self, auth_client, build):
        _status(auth_client)
        _deploy(build, "#!/bin/bash\necho build-2\n")
        r = auth_client.post("/api/dev/seed")
        assert r.status_code == 200
        data = _status(auth_client)
        assert data["edge_hash"] == data["build_hash"] != data["fleet_hash"]
        assert data["edge_seeded_at"]
        assert data["pipeline_state"] == "ready_for_dev"
        assert auth_client.get("/static/dev/eshu-gateway-install.sh").content == build.read_bytes()
        assert b"build-1" in auth_client.get("/api/gateway-script").content

    def test_dev_push_then_promote(self, auth_client, build):
        from db.gateways import clear_trigger_dev_update
        _status(auth_client)
        _deploy(build, "#!/bin/bash\necho build-2\n")
        auth_client.post("/api/dev/seed")
        assert auth_client.post("/api/dev-gateways/push").status_code == 200
        assert _status(auth_client)["pipeline_state"] == "dev_in_progress"
        clear_trigger_dev_update()
        assert _status(auth_client)["pipeline_state"] == "ready_for_promote"
        r = auth_client.post("/api/dev/promote")
        assert r.status_code == 200
        data = _status(auth_client)
        assert data["fleet_hash"] == data["edge_hash"] == data["build_hash"]
        assert data["deployed_hash"] == data["fleet_hash"]
        assert data["fleet_promoted_at"]
        assert data["pipeline_state"] == "clear"
        assert data["backup_exists"] is True
        served = auth_client.get("/api/gateway-script").content
        assert served == build.read_bytes()
        assert auth_client.get("/static/eshu-gateway-install.sh").content == served

    def test_promote_sets_the_fleet_update_trigger(self, auth_client, build):
        from db.gateways import get_trigger_update_version
        auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        assert get_trigger_update_version()

    def test_rollback_restores_the_previous_fleet(self, auth_client, build):
        _status(auth_client)
        _deploy(build, "#!/bin/bash\necho build-2\n")
        auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        assert b"build-2" in auth_client.get("/api/gateway-script").content
        r = auth_client.post("/api/dev/rollback")
        assert r.status_code == 200
        assert b"build-1" in auth_client.get("/api/gateway-script").content
        assert b"build-1" in auth_client.get("/api/gateway-script-rollback").content
        assert _status(auth_client)["deployed_hash"] == ic.file_hash(ic.channel_path(ic.FLEET))

    def test_rollback_without_a_previous_fleet_is_404(self, auth_client, build):
        assert auth_client.post("/api/dev/rollback").status_code == 404

    def test_promote_never_writes_the_checkout(self, auth_client, build):
        repo_build = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "dashboard", "eshu-gateway-install.sh")
        before = open(repo_build, "rb").read()
        _deploy(build, "#!/bin/bash\necho build-2\n")
        auth_client.post("/api/dev/seed")
        auth_client.post("/api/dev/promote")
        auth_client.post("/api/dev/rollback")
        assert open(repo_build, "rb").read() == before


class TestServing:

    def test_missing_fleet_is_503_never_build(self, auth_client, build):
        _status(auth_client)
        os.remove(ic.channel_path(ic.FLEET))
        for path in ("/api/gateway-script", "/static/eshu-gateway-install.sh",
                     "/api/gateway-script-rollback"):
            r = auth_client.get(path)
            assert r.status_code == 503, path
            assert b"build-1" not in r.content

    def test_missing_edge_is_503(self, auth_client, build):
        _status(auth_client)
        os.remove(ic.channel_path(ic.EDGE))
        assert auth_client.get("/static/dev/eshu-gateway-install.sh").status_code == 503

    def test_installers_are_not_cached(self, auth_client, build):
        r = auth_client.get("/static/eshu-gateway-install.sh")
        assert r.headers.get("cache-control") == "no-store"

    def test_installer_routes_win_over_a_stray_static_file(self, auth_client, build):
        # A pre-X7 checkout may still have a file at the static path.
        static = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "dashboard", "static", "eshu-gateway-install.sh")
        created = not os.path.exists(static)
        if created:
            with open(static, "w") as f:
                f.write("stray pre-X7 copy\n")
        try:
            r = auth_client.get("/static/eshu-gateway-install.sh")
            assert b"build-1" in r.content and b"stray" not in r.content
        finally:
            if created:
                os.remove(static)


class TestAtomicWrites:

    def test_failed_write_leaves_the_old_file(self, auth_client, build, monkeypatch):
        _status(auth_client)
        before = open(ic.channel_path(ic.EDGE), "rb").read()
        _deploy(build, "#!/bin/bash\necho build-2\n")

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(ic.os, "replace", boom)
        with pytest.raises(OSError):
            ic.seed_edge()
        assert open(ic.channel_path(ic.EDGE), "rb").read() == before
        leftovers = [n for n in os.listdir(ic.channel_dir()) if n.startswith(".")]
        assert leftovers == []

    def test_channel_files_are_0644(self, auth_client, build):
        _status(auth_client)
        auth_client.post("/api/dev/seed")
        mode = os.stat(ic.channel_path(ic.EDGE)).st_mode & 0o777
        assert mode == 0o644


class TestStatusShape:

    def test_fields(self, auth_client, build):
        data = _status(auth_client)
        for key in ("pipeline_state", "build_hash", "edge_hash", "fleet_hash",
                    "edge_matches_build", "deployed_hash", "gateway_count",
                    "dashboard_version", "edge_exists", "backup_exists",
                    "dev_gateway_count", "dev_gateways", "edge_seeded_at",
                    "fleet_promoted_at"):
            assert key in data, key

    def test_version_endpoint(self, auth_client):
        r = auth_client.get("/api/version")
        assert r.status_code == 200
        assert r.json()["version"] == "v0.1.0"

    def test_version_endpoint_blocked_for_gateway_token(self, client, gateway_headers):
        assert client.get("/api/version", headers=gateway_headers).status_code == 401


class TestDevGatewaysEndpoint:

    def test_requires_auth(self, client):
        assert client.get("/api/dev-gateways").status_code == 401

    def test_returns_only_ip_hostname(self, auth_client):
        from db.gateways import register_gateway, set_gateway_mode
        register_gateway("10.0.0.1", "dev-host", "v15.4")
        set_gateway_mode("10.0.0.1", "dev")
        r = auth_client.get("/api/dev-gateways")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert set(data[0].keys()) == {"ip", "hostname"}

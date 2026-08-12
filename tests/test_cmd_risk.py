import base64
from urllib.parse import quote


def b64(s):
    return base64.b64encode(s.encode()).decode()


class TestCmdRisk:

    def test_service_restart(self):
        from core.cmd_risk import get_cmd_risk
        assert get_cmd_risk("systemctl restart nginx") == "Restarts/stops a service — brief outage"

    def test_service_stop(self):
        from core.cmd_risk import get_cmd_risk
        assert get_cmd_risk("systemctl stop docker") == "Restarts/stops a service — brief outage"

    def test_docker_rm_and_rmi(self):
        from core.cmd_risk import get_cmd_risk
        assert "Removes" in get_cmd_risk("docker rm old-container")
        assert "Removes" in get_cmd_risk("docker rmi my-image")

    def test_log_rotation(self):
        from core.cmd_risk import get_cmd_risk
        assert get_cmd_risk("journalctl --vacuum-time=7d") is not None
        assert get_cmd_risk("truncate -s 0 /var/log/app.log") is not None

    def test_installs(self):
        from core.cmd_risk import get_cmd_risk
        assert "Installs packages" in get_cmd_risk("apt-get install nginx")
        assert "Installs packages" in get_cmd_risk("pip install flask")
        assert "Installs packages" in get_cmd_risk("npm install -g yarn")

    def test_rm(self):
        from core.cmd_risk import get_cmd_risk
        assert get_cmd_risk("rm /tmp/file") == "Deletes files — irreversible"

    def test_no_false_positives(self):
        from core.cmd_risk import get_cmd_risk
        assert get_cmd_risk("warm boot") is None
        assert get_cmd_risk("systemctl status nginx") is None
        assert get_cmd_risk("uptime") is None
        assert get_cmd_risk("ls -la") is None

    def test_dry_run_command_has_no_risk(self):
        from core.cmd_risk import get_cmd_risk
        assert get_cmd_risk("apt-get --dry-run install nginx") is None


class TestDryRunSuggestion:

    def test_aptget(self):
        from core.cmd_risk import get_dry_run_suggestion
        assert get_dry_run_suggestion("apt-get install nginx") == "apt-get --dry-run install nginx"

    def test_npm(self):
        from core.cmd_risk import get_dry_run_suggestion
        assert get_dry_run_suggestion("npm install foo") == "npm install --dry-run foo"

    def test_pip(self):
        from core.cmd_risk import get_dry_run_suggestion
        assert get_dry_run_suggestion("pip install foo") == "pip install --dry-run foo"

    def test_none_for_others(self):
        from core.cmd_risk import get_dry_run_suggestion
        assert get_dry_run_suggestion("systemctl restart nginx") is None
        assert get_dry_run_suggestion("rm /tmp/x") is None
        assert get_dry_run_suggestion("uptime") is None

    def test_no_double_flag(self):
        from core.cmd_risk import get_dry_run_suggestion
        assert get_dry_run_suggestion("npm install --dry-run foo") is None


class TestRiskApi:

    def test_requests_includes_risk_for_pending(self, auth_client, gateway_headers):
        auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("rm /tmp/file")
        }, headers=gateway_headers)
        reqs = auth_client.get("/api/requests").json()
        row = next(r for r in reqs if r["status"] == "pending")
        assert row["risk"] == "Deletes files — irreversible"

    def test_requests_risk_none_when_safe(self, auth_client, gateway_headers):
        auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("uptime")
        }, headers=gateway_headers)
        reqs = auth_client.get("/api/requests").json()
        row = next(r for r in reqs if r["status"] == "pending")
        assert row["risk"] is None

    def test_requests_risk_none_when_not_pending(self, auth_client, gateway_headers):
        rid = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": b64("rm /tmp/file")
        }, headers=gateway_headers).json()["id"]
        auth_client.post(f"/api/approve/{rid}")
        reqs = auth_client.get("/api/requests").json()
        row = next(r for r in reqs if r["id"] == int(rid))
        assert row["risk"] is None

    def test_policies_test_includes_risk_and_dry_run(self, client):
        r = client.get("/api/policies/test?command=" + quote("apt-get install nginx"))
        assert r.status_code == 200
        data = r.json()
        assert data["risk"] is not None
        assert data["dry_run"] == "apt-get --dry-run install nginx"

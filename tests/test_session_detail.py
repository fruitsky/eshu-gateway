"""Sessions carousel + session-detail view + approval labelling.

Covers the regression where `/api/sessions/recent` selected a non-existent
`hostname` column from `requests` (500 → carousel silently fell back to an
SSH-only client render, so MCP sessions vanished), plus the richer session
detail (reason / masked request summary / approval decision) and the approval
audit labels written by the runner and the approve/deny endpoints.
"""
import json

from db.integrations import (
    create_integration,
    create_pending_call,
    create_tool,
    get_integration,
    get_integration_calls,
    record_integration_call,
)
from db.requests import create_request
from core.integration_proxy import safe_request_summary
from core.tool_runner import run_tool


def _mk(name="ha", kind="ha", **kw):
    create_integration(name, "https://ha.local/api", "bearer", "tok", kind=kind, **kw)
    return get_integration(name)


class TestSessionsRecent:

    def test_mcp_only_session_is_returned(self, auth_client):
        _mk()
        record_integration_call(integration="ha", tool="list_entities", agent="mcp",
                                method="GET", path="/states", status_code=200,
                                latency_ms=5, response_summary="[]", response_bytes=2,
                                truncated=0, outcome="ok", session_id="sess-mcp-1",
                                execution_id="exec-1")
        r = auth_client.get("/api/sessions/recent?limit=6")
        assert r.status_code == 200  # regression: was 500 (no such column: hostname)
        sessions = r.json()["sessions"]
        assert any(s["session_id"] == "sess-mcp-1" and s["mcps"] >= 1 for s in sessions)

    def test_ssh_session_includes_hostname(self, auth_client):
        _mk()
        create_request(target_ip="10.0.0.5", command="uptime", status="auto-approved",
                       ttl=0, session_id="sess-ssh-1")
        r = auth_client.get("/api/sessions/recent?limit=6")
        assert r.status_code == 200
        row = next(s for s in r.json()["sessions"] if s["session_id"] == "sess-ssh-1")
        assert row["reqs"] == 1


class TestSessionDetail:

    def test_detail_returns_ssh_and_rich_mcp(self, auth_client):
        _mk()
        create_request(target_ip="10.0.0.5", command="uptime", status="auto-approved",
                       ttl=0, reason="check the box", session_id="sess-detail",
                       execution_id="exec-ssh")
        record_integration_call(integration="ha", tool="lovelace_save_config",
                                agent="operator", method="WS", path="lovelace/config/save",
                                status_code=200, latency_ms=12, response_summary='{"ok":true}',
                                response_bytes=11, truncated=0, outcome="ok",
                                session_id="sess-detail", execution_id="exec-9",
                                reason="tidy the dashboard",
                                request_summary='{"url_path":"water-monitor"}',
                                approval="approved", decided_at=123456)
        r = auth_client.get("/api/sessions/sess-detail/detail")
        assert r.status_code == 200
        d = r.json()
        ssh = next(x for x in d["ssh"] if x["command"] == "uptime")
        assert ssh["reason"] == "check the box"
        assert ssh["execution_id"] == "exec-ssh"
        m = next(x for x in d["mcp"] if x["tool"] == "lovelace_save_config")
        assert m["reason"] == "tidy the dashboard"
        assert m["request_summary"] == '{"url_path":"water-monitor"}'
        assert m["response_summary"] == '{"ok":true}'
        assert m["approval"] == "approved"
        assert m["decided_at"] == 123456
        assert m["execution_id"] == "exec-9"


class TestApprovalLabels:

    def test_immediate_mutating_is_auto(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="none")
        integration = get_integration("api")
        create_tool(integration["id"], "do_thing", "Do", "POST", "/do", [], "",
                    read_only=False)
        run_tool("api", "do_thing", {"x": 1}, "because I asked")
        row = next(r for r in get_integration_calls()["rows"] if r["tool"] == "do_thing")
        assert row["approval"] == "auto"
        assert row["reason"] == "because I asked"
        assert row["agent"] == "mcp"

    def test_approved_call_is_labelled(self, mock_upstream, auth_client):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="all")
        integration = get_integration("api")
        create_tool(integration["id"], "do_thing", "Do", "POST", "/do", [], "",
                    read_only=False)
        call_id = create_pending_call("api", "do_thing", {"x": 1}, "sign off",
                                      session_id="s-appr", execution_id="e-appr")
        r = auth_client.post(f"/api/integration-calls/{call_id}/approve")
        assert r.status_code == 200
        row = next(r for r in get_integration_calls(session="s-appr")["rows"]
                   if r["tool"] == "do_thing")
        assert row["approval"] == "approved"
        assert row["agent"] == "operator"
        assert row["decided_at"] > 0
        assert row["reason"] == "sign off"

    def test_denied_call_is_labelled(self, auth_client):
        create_integration("api", "http://localhost:1/api", "none", "")
        call_id = create_pending_call("api", "do_thing", {"x": 1}, "no thanks")
        r = auth_client.post(f"/api/integration-calls/{call_id}/deny")
        assert r.status_code == 200
        row = next(r for r in get_integration_calls()["rows"] if r["outcome"] == "denied")
        assert row["approval"] == "denied"
        assert row["agent"] == "operator"
        assert row["decided_at"] > 0
        assert row["reason"] == "no thanks"

    def test_approval_filter(self, auth_client):
        create_integration("api", "http://localhost:1/api", "none", "")
        call_id = create_pending_call("api", "do_thing", {"x": 1}, "no thanks")
        auth_client.post(f"/api/integration-calls/{call_id}/deny")
        record_integration_call(integration="api", tool="read", agent="mcp", method="GET",
                                path="/x", status_code=200, latency_ms=1, response_summary="{}",
                                response_bytes=2, truncated=0, outcome="ok", approval="auto")
        denied = auth_client.get("/api/integration-calls?approval=denied").json()
        assert denied["total"] == 1 and denied["rows"][0]["tool"] == "do_thing"
        required = auth_client.get("/api/integration-calls?approval=required").json()
        assert all(r["approval"] in ("approved", "denied") for r in required["rows"])
        assert required["total"] >= 1


class TestSafeRequestSummary:

    def test_redacted_params_are_masked(self):
        tool = {"params": [{"name": "password", "redact": True}, {"name": "host"}]}
        s = safe_request_summary({"host": "h", "password": "sekret"}, tool)
        assert "sekret" not in s
        assert "redacted" in s
        assert "h" in s

    def test_large_payload_is_capped(self):
        s = safe_request_summary({"blob": "x" * 5000})
        assert s.endswith("[truncated]")
        assert len(s) <= 2100

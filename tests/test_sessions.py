"""Dashboard Sessions: the id an agent can get instead of inventing one.

Covers the three sources (session_begin, caller argument, MCP transport), the
fallback that keeps a call from landing in a blank session, the annotation that
teaches the model the id, and the quality metric the Sessions list exposes.
"""
import json

from core.agent_context import reset_current_session, set_current_session
from core.mcp_server import (
    _ALWAYS_ALLOWED,
    check_approval_fn,
    check_request_fn,
    session_begin_fn,
    session_summary_fn,
)
from core.session_sign import sign_session
from core.tool_runner import _annotate_session, run_tool
from db.gateways import set_gateway_mode
from db.misc import set_feature_flag, set_feature_flag_scope
from db.integrations import (
    create_integration,
    create_tool,
    get_integration,
    get_integration_calls,
    record_integration_call,
)
from db.requests import (
    create_request,
    expire_stale_requests,
    get_request_row,
)
from db.sessions import (
    get_session,
    get_session_coverage,
    get_session_summary,
    upsert_session,
)


class TestSessionBegin:

    def test_begin_returns_an_id_and_the_ssh_prefix(self):
        out = json.loads(session_begin_fn()("Investigate the media server"))
        sid = out["session_id"]
        assert len(sid) == 12 and sid.isalnum()
        assert out["session_sig"] == sign_session(sid)
        assert out["ssh_prefix"] == (
            f"ESHU_SESSION_ID={sid} ESHU_SESSION_SIG={out['session_sig']} ")
        row = get_session(sid)
        assert row["goal"] == "Investigate the media server"
        assert row["source"] == "agent"

    def test_begin_requires_a_goal(self):
        out = json.loads(session_begin_fn()(""))
        assert out["error"] == "goal_required"
        out = json.loads(session_begin_fn()("   "))
        assert out["error"] == "goal_required"

    def test_goal_survives_touches_and_can_be_replaced(self):
        sid = "abc123abc123"
        upsert_session(sid, goal="first goal")
        upsert_session(sid, goal=None)          # a touch must not erase the name
        assert get_session(sid)["goal"] == "first goal"
        upsert_session(sid, goal="second goal")
        assert get_session(sid)["goal"] == "second goal"

    def test_session_tools_ignore_agent_allowlists(self):
        assert {"session_begin", "session_summary"} <= _ALWAYS_ALLOWED
        # F14: whereis is an ordinary policy-gated tool, not a session built-in.
        assert "whereis" not in _ALWAYS_ALLOWED

    def test_begin_can_adopt_an_existing_chat_id(self):
        out = json.loads(session_begin_fn()("adopted goal", "chat-123"))
        assert out["session_id"] == "chat-123"
        assert out["adopted"] is True
        assert out["ssh_prefix"] == (
            "ESHU_SESSION_ID=chat-123 ESHU_SESSION_SIG=%s " % out["session_sig"])
        assert get_session("chat-123")["goal"] == "adopted goal"

    def test_begin_rejects_an_invalid_id(self):
        out = json.loads(session_begin_fn()("goal", "not a valid id!"))
        assert out["error"] == "invalid_session_id"


class TestTransportFallback:

    def _read_tool(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="none")
        integration = get_integration("api")
        create_tool(integration["id"], "read_thing", "Read", "GET", "/thing", [], "",
                    read_only=True)

    def test_missing_id_is_bound_to_the_transport_session(self, mock_upstream):
        self._read_tool(mock_upstream)
        token = set_current_session("mcp-deadbeef")
        try:
            out = run_tool("api", "read_thing", {}, "")
        finally:
            reset_current_session(token)
        data = json.loads(out)
        assert data["_eshu"] == {"session_id": "mcp-deadbeef", "source": "transport"}
        rows = get_integration_calls(session="mcp-deadbeef")["rows"]
        assert any(r["tool"] == "read_thing" for r in rows)

    def test_caller_id_wins_and_is_not_annotated(self, mock_upstream):
        self._read_tool(mock_upstream)
        token = set_current_session("mcp-deadbeef")
        try:
            out = run_tool("api", "read_thing", {"session_id": "mine-1"}, "")
        finally:
            reset_current_session(token)
        assert "_eshu" not in json.loads(out)
        assert any(r["tool"] == "read_thing"
                   for r in get_integration_calls(session="mine-1")["rows"])
        assert not get_integration_calls(session="mcp-deadbeef")["rows"]

    def test_missing_id_inherits_the_session_just_begun(self, mock_upstream):
        """Name it once, then forget: later calls follow session_begin."""
        self._read_tool(mock_upstream)
        token = set_current_session("mcp-inherit")
        try:
            started = json.loads(session_begin_fn()("inherited goal"))
            out = run_tool("api", "read_thing", {}, "")
        finally:
            reset_current_session(token)
        data = json.loads(out)
        assert data["_eshu"] == {"session_id": started["session_id"],
                                 "source": "inherited"}
        rows = get_integration_calls(session=started["session_id"])["rows"]
        assert any(r["tool"] == "read_thing" and r["session_source"] == "inherited"
                   for r in rows)
        assert not get_integration_calls(session="mcp-inherit")["rows"]

    def test_explicit_id_beats_the_inherited_one(self, mock_upstream):
        self._read_tool(mock_upstream)
        token = set_current_session("mcp-inherit-2")
        try:
            session_begin_fn()("a task")
            out = run_tool("api", "read_thing", {"session_id": "explicit-1"}, "")
        finally:
            reset_current_session(token)
        assert "_eshu" not in json.loads(out)
        rows = get_integration_calls(session="explicit-1")["rows"]
        assert any(r["session_source"] == "caller" for r in rows)
        assert not get_integration_calls(session="mcp-inherit-2")["rows"]

    def test_annotation_leaves_arrays_and_text_alone(self):
        assert _annotate_session("[1, 2]", "mcp-x") == "[1, 2]"
        assert _annotate_session("not json", "mcp-x") == "not json"
        wrapped = json.loads(_annotate_session('{"a": 1}', "mcp-x"))
        assert wrapped == {"a": 1, "_eshu": {"session_id": "mcp-x", "source": "transport"}}


class TestStrictSessions:

    def _read_tool(self, mock_upstream):
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="none")
        integration = get_integration("api")
        create_tool(integration["id"], "read_thing", "Read", "GET", "/thing", [], "",
                    read_only=True)

    def test_unminted_transport_call_is_refused(self, mock_upstream):
        set_feature_flag("require_sessions", True)
        self._read_tool(mock_upstream)
        token = set_current_session("mcp-strict")
        try:
            out = json.loads(run_tool("api", "read_thing", {}, ""))
        finally:
            reset_current_session(token)
        assert out["error"] == "session_required"
        assert not get_integration_calls(session="mcp-strict")["rows"]

    def test_invented_id_is_refused(self, mock_upstream):
        set_feature_flag("require_sessions", True)
        self._read_tool(mock_upstream)
        out = json.loads(run_tool("api", "read_thing",
                                  {"session_id": "invented"}, ""))
        assert out["error"] == "session_required"
        assert not get_integration_calls(session="invented")["rows"]

    def test_unnamed_row_is_refused(self, mock_upstream):
        set_feature_flag("require_sessions", True)
        self._read_tool(mock_upstream)
        upsert_session("silent-session", goal="")
        out = json.loads(run_tool("api", "read_thing",
                                  {"session_id": "silent-session"}, ""))
        assert out["error"] == "session_required"

    def test_minted_named_session_runs(self, mock_upstream):
        set_feature_flag("require_sessions", True)
        self._read_tool(mock_upstream)
        token = set_current_session("mcp-strict-ok")
        try:
            started = json.loads(session_begin_fn()("strict goal"))
            out = run_tool("api", "read_thing", {}, "")
        finally:
            reset_current_session(token)
        data = json.loads(out)
        assert data.get("_eshu", {}).get("session_id") == started["session_id"]
        assert get_integration_calls(session=started["session_id"])["rows"]

    def test_flag_off_keeps_the_transport_fallback(self, mock_upstream):
        set_feature_flag("require_sessions", False)
        self._read_tool(mock_upstream)
        token = set_current_session("mcp-strict-off")
        try:
            out = run_tool("api", "read_thing", {}, "")
        finally:
            reset_current_session(token)
        assert "_eshu" in json.loads(out)
        assert get_integration_calls(session="mcp-strict-off")["rows"]


class TestSessionSummary:

    def test_summary_counts_ssh_and_mcp(self):
        create_request(target_ip="10.0.0.9", command="uptime", status="auto-approved",
                       ttl=0, session_id="sum-1")
        record_integration_call(integration="ha", tool="list_entities", agent="mcp",
                                method="GET", path="/states", status_code=200,
                                latency_ms=1, response_summary="[]", response_bytes=2,
                                truncated=0, outcome="ok", session_id="sum-1")
        s = get_session_summary("sum-1")
        assert s["commands"] == 1
        assert s["approved"] == 1
        assert s["whitelisted"] == 1
        assert s["jit_approved"] == 0
        assert s["mcp_calls"] == 1
        assert s["first_seen"] and s["last_seen"]

    def test_summary_splits_permitted_without_claiming_success(self):
        """`approved` is the sum; the split says *why* each row was permitted."""
        create_request(target_ip="10.0.0.9", command="allowlisted",
                       status="auto-approved", ttl=0, session_id="sum-split")
        create_request(target_ip="10.0.0.9", command="human jit",
                       status="approved", ttl=0, session_id="sum-split")
        create_request(target_ip="10.0.0.9", command="host override",
                       status="approved", ttl=0, reason="override",
                       session_id="sum-split")
        create_request(target_ip="10.0.0.9", command="session override",
                       status="approved", ttl=0, reason="session-override:7",
                       session_id="sum-split")
        create_request(target_ip="10.0.0.9", command="window",
                       status="window-approved", ttl=0, session_id="sum-split")
        create_request(target_ip="10.0.0.9", command="fleet",
                       status="fleet-run", ttl=0, session_id="sum-split")
        create_request(target_ip="10.0.0.9", command="claimed ticket",
                       status="consumed", ttl=0, session_id="sum-split")
        s = get_session_summary("sum-split")
        assert s["commands"] == 7
        assert s["whitelisted"] == 1
        assert s["jit_approved"] == 2       # human JIT + the claimed ticket
        assert s["override_approved"] == 2  # host + session override
        assert s["window_approved"] == 1
        assert s["fleet_run"] == 1
        assert s["consumed"] == 1
        assert s["approved"] == 7

    def test_tool_defaults_to_the_transport_session(self):
        record_integration_call(integration="ha", tool="list_entities", agent="mcp",
                                method="GET", path="/states", status_code=200,
                                latency_ms=1, response_summary="[]", response_bytes=2,
                                truncated=0, outcome="ok", session_id="mcp-cafebabe")
        token = set_current_session("mcp-cafebabe")
        try:
            out = json.loads(session_summary_fn()())
        finally:
            reset_current_session(token)
        assert out["session_id"] == "mcp-cafebabe"
        assert out["mcp_calls"] == 1
        assert out["resolved_from"] == "transport"

    def test_summary_says_how_the_id_was_resolved(self):
        explicit = json.loads(session_summary_fn()("sum-x"))
        assert explicit["resolved_from"] == "argument"

        token = set_current_session("mcp-resolve")
        try:
            started = json.loads(session_begin_fn()("resolve me"))
            bound = json.loads(session_summary_fn()(""))
        finally:
            reset_current_session(token)
        assert bound["session_id"] == started["session_id"]
        assert bound["resolved_from"] == "connection"

    def test_tool_without_any_session_says_how_to_start_one(self):
        out = json.loads(session_summary_fn()(""))
        assert out["error"] == "no_session"
        assert "session_begin" in out["message"]

    def test_default_summary_follows_the_last_id_the_connection_used(self, mock_upstream):
        """No-arg summary must describe the task, not the transport bucket."""
        create_integration("api", mock_upstream["base_url"], "none", "", gate_mode="none")
        integration = get_integration("api")
        create_tool(integration["id"], "read_thing", "Read", "GET", "/thing", [], "",
                    read_only=True)
        token = set_current_session("mcp-test-default")
        try:
            run_tool("api", "read_thing", {"session_id": "chat-9"}, "")
            summary = json.loads(session_summary_fn()(""))
        finally:
            reset_current_session(token)
        assert summary["session_id"] == "chat-9"
        assert summary["mcp_calls"] == 1
        assert summary["commands"] == 0

    def test_default_summary_uses_the_id_just_begun(self):
        token = set_current_session("mcp-test-begin")
        try:
            started = json.loads(session_begin_fn()("fresh goal"))
            summary = json.loads(session_summary_fn()(""))
        finally:
            reset_current_session(token)
        assert summary["session_id"] == started["session_id"]


class TestSessionPolicy:

    def test_dev_scope_enforces_dev_gateways_and_carries_the_key(
            self, client, gateway_headers):
        set_gateway_mode("10.0.0.1", "dev")
        set_feature_flag("require_sessions", True)
        set_feature_flag_scope("require_sessions", "dev")
        body = client.get("/api/policy/10.0.0.1", headers=gateway_headers).json()
        assert body["require_sessions"] == 1
        assert len(body["session_signing_key"]) == 64

    def test_dev_scope_leaves_prod_gateways_alone(self, client, gateway_headers):
        set_gateway_mode("10.0.0.1", "prod")
        set_feature_flag("require_sessions", True)
        set_feature_flag_scope("require_sessions", "dev")
        body = client.get("/api/policy/10.0.0.1", headers=gateway_headers).json()
        assert body["require_sessions"] == 0

    def test_prod_scope_enforces_prod_gateways(self, client, gateway_headers):
        set_gateway_mode("10.0.0.1", "prod")
        set_feature_flag("require_sessions", True)
        set_feature_flag_scope("require_sessions", "prod")
        body = client.get("/api/policy/10.0.0.1", headers=gateway_headers).json()
        assert body["require_sessions"] == 1


class TestSessionsApi:

    def test_recent_list_carries_the_goal_and_coverage(self, auth_client):
        sid = json.loads(session_begin_fn()("Nightly maintenance"))["session_id"]
        create_request(target_ip="10.0.0.5", command="uptime", status="auto-approved",
                       ttl=0, session_id=sid)
        r = auth_client.get("/api/sessions/recent?limit=6")
        assert r.status_code == 200
        body = r.json()
        row = next(s for s in body["sessions"] if s["session_id"] == sid)
        assert row["goal"] == "Nightly maintenance"
        assert row["source"] == "agent"
        assert body["coverage"]["days"] == 7
        assert body["coverage"]["ssh"]["total"] >= 1

    def test_history_can_filter_to_one_session(self, auth_client):
        create_request(target_ip="10.0.0.5", command="uptime", status="auto-approved",
                       ttl=0, session_id="filter-a")
        create_request(target_ip="10.0.0.5", command="df -h", status="auto-approved",
                       ttl=0, session_id="filter-b")
        only = auth_client.get("/api/requests?session=filter-a").json()
        assert [r["command"] for r in only] == ["uptime"]
        every = auth_client.get("/api/requests").json()
        assert len(every) >= 2


class TestCoverage:

    def test_coverage_counts_only_explicit_mcp_ids(self):
        for sid, source in (("caller-1", "caller"),
                            ("named-1", "inherited"),
                            ("mcp-transport-1", "transport")):
            record_integration_call(integration="ha", tool="read", agent="mcp",
                                    method="GET", path="/x", status_code=200,
                                    latency_ms=1, response_summary="{}",
                                    response_bytes=2, truncated=0, outcome="ok",
                                    session_id=sid, session_source=source)
        cov = get_session_coverage(7)
        assert cov["mcp"]["total"] == 3
        assert cov["mcp"]["tagged"] == 1
        assert cov["mcp"]["auto"] == 2

    def test_coverage_counts_only_real_ids(self):
        create_request(target_ip="10.0.0.5", command="a", status="auto-approved", ttl=0,
                       session_id="real-1")
        create_request(target_ip="10.0.0.5", command="b", status="auto-approved", ttl=0,
                       session_id="")
        create_request(target_ip="10.0.0.5", command="c", status="auto-approved", ttl=0,
                       session_id="unknown")
        cov = get_session_coverage(7)
        assert cov["ssh"]["total"] == 3
        assert cov["ssh"]["tagged"] == 1
        assert cov["ssh"]["pct"] == 33


class TestSshRequestLookup:
    """check_request is the agent-facing view of SSH JIT ids (the other
    approval namespace); check_approval must redirect, not say 'not found'."""

    def test_returns_the_request_by_integer_id(self):
        rid = create_request("10.0.0.7", "uptime", status="approved",
                             ttl=90, session_id="chat-req")
        out = json.loads(check_request_fn()(rid, session_id="chat-req"))
        assert out["kind"] == "ssh"
        assert out["status"] == "approved"
        assert out["command"] == "uptime"
        assert out["target_ip"] == "10.0.0.7"

    def test_other_session_is_not_found(self):
        rid = create_request("10.0.0.7", "uptime", status="pending",
                             ttl=90, session_id="chat-req")
        out = json.loads(check_request_fn()(rid, session_id="chat-other"))
        assert out["error"] == "not found"

    def test_check_approval_redirects_an_ssh_id(self):
        rid = create_request("10.0.0.7", "uptime", status="approved",
                             ttl=90, session_id="chat-req")
        out = json.loads(check_approval_fn()(rid))
        assert out["error"] == "wrong_namespace"
        assert out["kind"] == "ssh"
        assert "check_request" in out["message"]

    def test_pending_expires_and_approved_is_abandoned(self):
        import time
        now = int(time.time())
        rid_p = create_request("10.0.0.7", "never approved", status="pending", ttl=90)
        rid_a = create_request("10.0.0.7", "approved but unclaimed", status="approved", ttl=90)
        expired, abandoned = expire_stale_requests(now + 200)
        assert rid_p in expired
        assert rid_a in abandoned
        assert get_request_row(rid_p)["status"] == "expired"
        assert get_request_row(rid_a)["status"] == "abandoned"

    def test_next_step_is_ready_only_after_consumed(self):
        """M2: `approved` is not a green light — the ticket is still on its way."""
        from db.requests import update_request_status
        rid = create_request("10.0.0.7", "uptime", status="approved", ttl=90)
        out = json.loads(check_request_fn()(rid))
        assert "delivering" in out["next_step"]
        update_request_status(rid, "consumed")
        out = json.loads(check_request_fn()(rid))
        assert "re-run" in out["next_step"]

    def test_terminal_state_is_derived_before_the_sweep(self):
        import time
        now = int(time.time())
        rid = create_request("10.0.0.7", "stale pending", status="pending", ttl=90)
        # Force the row past its TTL without running the background sweep.
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute("UPDATE requests SET expires_at = ? WHERE id = ?",
                         (now - 1, rid))
            conn.commit()
        out = json.loads(check_request_fn()(rid))
        assert out["status"] == "expired"

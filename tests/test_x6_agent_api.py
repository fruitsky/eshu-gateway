"""X6 (part 1) — agent-API fixes from the Release 1.2 test round."""
import json

from db.requests import create_request


class TestCheckRequestSessionScope:
    """D3/D4: an id that exists but belongs to a session this connection never
    named is not "not found"."""

    def test_no_named_session_asks_for_the_id(self):
        from core.mcp_server import check_request_fn
        rid = create_request("10.0.0.7", "uptime", status="approved",
                             ttl=90, session_id="chat-req")
        out = json.loads(check_request_fn()(rid))
        assert out["error"] == "session_id_required", out
        assert out["id"] == rid
        assert "session_id" in out["message"]

    def test_the_owning_session_still_reads_it(self):
        from core.mcp_server import check_request_fn
        rid = create_request("10.0.0.7", "uptime", status="approved",
                             ttl=90, session_id="chat-req")
        out = json.loads(check_request_fn()(rid, session_id="chat-req"))
        assert out["status"] == "approved"

    def test_another_named_session_is_not_found(self):
        from core.mcp_server import check_request_fn
        rid = create_request("10.0.0.7", "uptime", status="pending",
                             ttl=90, session_id="chat-req")
        out = json.loads(check_request_fn()(rid, session_id="chat-other"))
        assert out["error"] == "not found"

    def test_a_missing_id_is_still_not_found(self):
        from core.mcp_server import check_request_fn
        out = json.loads(check_request_fn()(987654))
        assert out["error"] in ("not found", "session_id_required")

    def test_check_approval_names_the_full_call(self):
        from core.mcp_server import check_approval_fn
        rid = create_request("10.0.0.7", "uptime", status="approved",
                             ttl=90, session_id="chat-req")
        out = json.loads(check_approval_fn()(rid))
        assert out["error"] == "wrong_namespace"
        assert "check_request(request_id=%d" % rid in out["message"]
        assert "session_id=" in out["message"]


class TestGetRunbookValidation:
    def test_missing_id_is_a_validation_error(self):
        from core.mcp_server import get_runbook_fn
        out = json.loads(get_runbook_fn()())
        assert out["error"] == "validation_error", out
        assert "runbook_id" in out["message"]

    def test_unknown_id_is_still_not_found(self):
        from core.mcp_server import get_runbook_fn
        out = json.loads(get_runbook_fn()(999999))
        assert out["error"] == "not_found"


class TestDiagnoseContainerDescription:
    def test_the_description_names_the_parameter_and_an_example(self):
        import inspect
        from core import mcp_server
        source = inspect.getsource(mcp_server)
        start = source.index("name=\"diagnose_container\"")
        block = source[start:start + 1200]
        assert '"container"' in block
        assert "vmid" in block
        assert 'params={"host"' in block


class TestSessionSummaryCounts:
    """D8: the summary counts MCP calls and plan-claimed requests."""

    def test_one_of_each(self):
        from db.sessions import get_session_summary, upsert_session
        from db.integrations import record_integration_call
        upsert_session('sess-x6', agent='a6', goal='count things')
        create_request('10.0.0.1', 'uptime', status='approved', ttl=90,
                       session_id='sess-x6',
                       reason='session-plan:1:2:agent')
        record_integration_call(
            'home_assistant', 'ha_get_state', 'a6', 'GET', '/api/states',
            200, 12, 'ok', 10, 0, session_id='sess-x6')
        out = get_session_summary('sess-x6')
        assert out['mcp_calls'] == 1, out
        assert out['by_approval']['plan'] == 1, out['by_approval']


class TestSupersededPlans:
    """D7: a replaced plan reads `superseded` with the new id, not not_found."""

    def test_check_plan_reports_the_replacement(self):
        from core.agent_context import set_current_agent
        from core.mcp_server import check_plan_fn
        from db.session_plans import create_plan
        from db.sessions import upsert_session
        set_current_agent({'id': 1, 'name': 'a7'})
        upsert_session('sess-x6d', agent='a7', goal='g')
        first = create_plan('sess-x6d', [{'host': '*', 'command': 'uptime',
                                          'max_uses': 1}])
        second = create_plan('sess-x6d', [{'host': '*', 'command': 'date',
                                           'max_uses': 1}])
        out = json.loads(check_plan_fn()(first['plan_id']))
        assert out['status'] == 'superseded', out
        assert out['superseded_by'] == second['plan_id']
        assert 'check_plan(%d)' % second['plan_id'] in out['message']
        # The new plan is still proposed.
        assert json.loads(check_plan_fn()(second['plan_id']))['status'] == 'proposed'

    def test_probe_result_reports_the_replacement(self, temp_db):
        from core.agent_context import set_current_agent
        from core.mcp_server import probe_result_fn
        from core.probes import plan_probe
        from db.gateways import register_gateway
        from db.session_plans import create_plan
        from db.sessions import upsert_session
        set_current_agent({'id': 1, 'name': 'a7'})
        upsert_session('sess-x6e', agent='a7', goal='g')
        register_gateway('10.0.0.1', 'web01', 'v15.3')
        first = plan_probe('disk_pressure', {'host': '10.0.0.1'}, 'sess-x6e')
        second = create_plan('sess-x6e', [{'host': '10.0.0.1',
                                           'command': 'uptime', 'max_uses': 1}])
        out = json.loads(probe_result_fn()(first['plan_id'], 'sess-x6e'))
        assert out['status'] == 'superseded', out
        assert out['superseded_by'] == second['plan_id']


class TestD6RequestReuse:
    def test_an_approved_row_is_reused_and_pending_is_not(self, auth_client,
                                                          gateway_headers):
        import base64
        cmd = base64.b64encode(b'systemctl restart x6-demo').decode()

        def post():
            return auth_client.post('/api/request', json={
                'target_ip': '10.0.0.1', 'encoded_command': cmd,
            }, headers=gateway_headers).json()

        first = post()
        assert not first.get('reused')
        # Two identical pending cards stay two cards (an operator may deny one
        # and approve the other; the deny counter depends on it).
        second = post()
        assert not second.get('reused')
        assert second['request_id'] != first['request_id']
        # Once approved, a re-run returns the live id instead of a third card.
        from db.requests import update_request_status
        update_request_status(first['request_id'], 'approved')
        third = post()
        assert third.get('reused') is True
        assert third['request_id'] == first['request_id']

"""Chunk F13 — session conclusions and runbooks.

The agent's text is untrusted: it is scrubbed on write and read, escaped in
the UI, and only a confirmed conclusion (or a runbook promoted from one) is
ever served to another agent. Synthetic data only.
"""

import json
import os
import shutil
import subprocess
from contextlib import contextmanager

import pytest

from core.agent_context import reset_current_agent, set_current_agent
from core.conclusions import (
    find_runbooks, get_runbook_payload, promote_conclusion, save_conclusion,
    similar_incidents,
)
from core.mcp_server import (
    find_runbooks_fn, get_runbook_fn, session_conclude_fn, session_summary_fn,
    similar_incidents_fn,
)
from db.agent_policies import get_agent_policy, set_agent_policy
from db.agent_tokens import create_agent_token, get_agent_by_token
from db.conclusions import (
    ConclusionError, confirm_conclusion, edit_conclusion, get_conclusion,
    list_for_session, reject_conclusion,
)
from db.integrations import record_integration_call
from db.requests import create_request
from db.sessions import upsert_session

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'f13_harness.js')
NODE = shutil.which('node')


@contextmanager
def _acting_as(agent):
    token = set_current_agent(agent)
    try:
        yield agent
    finally:
        reset_current_agent(token)


def _agent(name):
    raw, agent_id = create_agent_token(name)
    agent = get_agent_by_token(raw)
    agent['policy'] = get_agent_policy(agent_id)
    return agent


def _session(sid='sess-f13', agent='agent-a', goal='fix the web proxy'):
    upsert_session(sid, agent=agent, goal=goal)
    return sid


def _request(sid, command='systemctl restart nginx', status='approved'):
    return create_request('192.0.2.10', command, status=status, ttl=0,
                          session_id=sid)


def _call(sid, tool='list_entities'):
    return record_integration_call(
        integration='ha', tool=tool, agent='agent-a', method='GET',
        path='/states', status_code=200, latency_ms=1,
        response_summary='ok', response_bytes=2, truncated=0, session_id=sid)


def _save(sid, **overrides):
    payload = dict(outcome='solved', summary='Restarted nginx and fixed the proxy.',
                   worked=[], did_not_work=[], root_cause='stale upstream',
                   fix='systemctl restart nginx', follow_ups=['add a health check'],
                   tags=['nginx', 'proxy'], runbook_candidate=True)
    payload.update(overrides)
    return save_conclusion(sid, overrides.pop('_agent', 'agent-a'), **payload)


class TestValidation:

    def test_f13_refs_must_belong_to_the_session(self, temp_db):
        sid = _session()
        other = 'sess-other'
        upsert_session(other, agent='agent-a', goal='other')
        mine = _request(sid)
        other_req = _request(other, 'uptime')
        other_call = _call(other)
        row = _save(sid, worked=[{'ref': 'req:%d' % mine, 'note': 'ran'}])
        assert row['worked'][0]['ref'] == 'req:%d' % mine
        for bad in ('req:%d' % other_req, 'call:%d' % other_call,
                    'req:999999', 'call:999999', 'req:abc'):
            with pytest.raises(ValueError) as exc:
                _save(sid, worked=[{'ref': bad, 'note': 'x'}])
            assert exc.value.args[0] == 'ref_not_in_session', bad

    def test_f13_bad_outcome_and_limits(self, temp_db):
        sid = _session()
        with pytest.raises(ValueError) as exc:
            _save(sid, outcome='great')
        assert exc.value.args[0] == 'bad_outcome'
        with pytest.raises(ValueError) as exc:
            _save(sid, summary='x' * 5000)
        assert exc.value.args[0] == 'summary_too_long'
        with pytest.raises(ValueError) as exc:
            _save(sid, tags=['t%d' % i for i in range(21)])
        assert exc.value.args[0] == 'too_many_tags'

    def test_f13_another_agents_session_is_refused(self, temp_db):
        sid = _session(agent='owner-agent')
        with pytest.raises(ValueError) as exc:
            save_conclusion(sid, 'intruder', outcome='solved', summary='x')
        assert exc.value.args[0] == 'session_not_yours'

    def test_f13_ownerless_session_is_refused_closed(self, temp_db):
        """S1: no owner means no one can vouch for the caller, so writing a
        conclusion for it fails closed (the old "adopted chat id" exception is
        gone: a conclusion is trusted by other agents later)."""
        upsert_session('sess-open', agent='', goal='open')
        with pytest.raises(ValueError) as exc:
            save_conclusion('sess-open', 'anyone', outcome='solved',
                            summary='open session')
        assert exc.value.args[0] == 'session_not_yours'
        # And through the tool the agent actually calls.
        with _acting_as(_agent('anyone')):
            out = json.loads(session_conclude_fn()(
                outcome='solved', summary='open session', session_id='sess-open'))
        assert out['error'] == 'session_not_yours'


class TestDraftFlow:

    def test_f13_a_second_call_replaces_the_draft(self, temp_db):
        sid = _session()
        first = _save(sid, summary='first')
        second = _save(sid, summary='second')
        assert first['id'] == second['id']
        assert second['status'] == 'draft'
        assert second['summary'] == 'second'
        assert len(list_for_session(sid)) == 1

    def test_f13_a_confirmed_conclusion_stays_and_a_new_draft_is_added(self, temp_db):
        """X6/D15: the confirmed row is final, but it must not block a later
        conclusion — the new one is a separate draft for the operator."""
        sid = _session()
        row = _save(sid)
        confirm_conclusion(row['id'], note='looks right', by='operator')
        again = _save(sid, summary='rewrite')
        assert again['id'] != row['id']
        assert again['status'] == 'draft'
        assert again['summary'] == 'rewrite'
        assert get_conclusion(row['id'])['status'] == 'confirmed'
        assert len(list_for_session(sid)) == 2

    def test_f13_reject_then_draft_again(self, temp_db):
        sid = _session()
        row = _save(sid)
        reject_conclusion(row['id'], note='wrong host', by='operator')
        assert get_conclusion(row['id'])['status'] == 'rejected'
        again = _save(sid, summary='take two')
        assert again['id'] == row['id'] and again['status'] == 'draft'

    def test_f13_confirm_and_edit_only_apply_to_a_draft(self, temp_db):
        sid = _session()
        row = _save(sid)
        confirm_conclusion(row['id'], by='operator')
        with pytest.raises(ConclusionError) as exc:
            confirm_conclusion(row['id'], by='operator')
        assert exc.value.code == 'bad_state'
        with pytest.raises(ConclusionError) as exc:
            edit_conclusion(row['id'], summary='x')
        assert exc.value.code == 'bad_state'
        # A different session (same agent) gives a fresh draft to edit.
        sid2 = _session('sess-f13-edit')
        draft = _save(sid2, summary='draft only')
        edited = edit_conclusion(draft['id'], summary='operator words',
                                 root_cause='operator root')
        assert edited['summary'] == 'operator words'
        assert edited['root_cause'] == 'operator root'
        assert edited['status'] == 'draft'


class TestRetrieval:

    def test_f13_only_confirmed_conclusions_are_served(self, temp_db):
        sid = _session()
        draft = _save(sid, summary='draft about nginx')
        assert similar_incidents('nginx')['count'] == 0
        confirm_conclusion(draft['id'], by='operator')
        found = similar_incidents('nginx')
        assert found['count'] == 1
        assert found['incidents'][0]['id'] == draft['id']
        assert found['incidents'][0]['session_id'] == sid
        # A rejected conclusion is never served (another session, same agent).
        other_sid = _session('sess-f13-rejected')
        other = _save(other_sid, summary='rejected about nginx')
        reject_conclusion(other['id'], by='operator')
        assert similar_incidents('nginx')['count'] == 1

    def test_f13_fts_ranking_smoke(self, temp_db):
        sid = _session()
        a = _save(sid, summary='nginx certificate expired on the proxy',
                  root_cause='certbot timer stopped', tags=['nginx', 'tls'])
        confirm_conclusion(a['id'], by='operator')
        sid_b = _session('sess-f13-b')
        b = _save(sid_b, summary='postgres backup failed', root_cause='disk full',
                  tags=['postgres'])
        confirm_conclusion(b['id'], by='operator')
        hits = similar_incidents('certificate nginx')['incidents']
        assert [h['id'] for h in hits] == [a['id']]
        assert similar_incidents('disk')['incidents'][0]['id'] == b['id']

    def test_f13_secrets_are_scrubbed_on_write_and_read(self, temp_db):
        sid = _session()
        token = 'Bearer eyJabcdefgh.abcdefgh.abcdefgh'
        row = _save(sid, summary='used ' + token + ' to call the API',
                    fix='export TOKEN=' + token)
        assert token not in row['summary']
        assert '[redacted]' in row['summary']
        confirm_conclusion(row['id'], by='operator')
        payload = json.dumps(similar_incidents('used'))
        assert token not in payload
        assert '[redacted]' in payload

    def test_f13_incidents_are_capped_at_five(self, temp_db):
        sid = _session()
        for i in range(7):
            row = _save(_session('sess-f13-cap-%d' % i),
                        summary='backup problem %d' % i)
            confirm_conclusion(row['id'], by='operator')
        result = similar_incidents('backup')
        assert result['count'] == 5 and result['more'] is True
        for incident in result['incidents']:
            assert len(json.dumps(incident)) <= 6200


class TestRunbooks:

    def test_f13_promotion_copies_steps_verbatim_and_records_refs(self, temp_db):
        sid = _session()
        req = _request(sid, 'systemctl restart nginx')
        call = _call(sid, 'get_config')
        row = _save(sid, worked=[
            {'ref': 'req:%d' % req, 'note': 'restart worked'},
            {'ref': 'call:%d' % call, 'note': 'config checked'},
            {'note': 'then reload the proxy'},
        ], did_not_work=[{'ref': 'req:%d' % req, 'note': 'reload alone was not enough'}])
        confirm_conclusion(row['id'], by='operator')
        runbook = promote_conclusion(row['id'], title='Fix the proxy',
                                     by='operator')
        steps = runbook['steps']
        assert steps[0]['kind'] == 'command'
        assert steps[0]['command'] == 'systemctl restart nginx'
        assert steps[0]['ref'] == 'req:%d' % req
        assert steps[0]['breakdown']['steps'][0]['kind'] == 'write'
        assert steps[1]['kind'] == 'tool'
        assert steps[1]['integration'] == 'ha' and steps[1]['tool'] == 'get_config'
        assert steps[2] == {'kind': 'instruction', 'text': 'then reload the proxy'}
        assert runbook['dead_ends'][0]['ref'] == 'req:%d' % req
        assert runbook['session_id'] == sid
        assert runbook['conclusion_id'] == row['id']

    def test_f13_promotion_requires_confirmed_and_candidate(self, temp_db):
        sid = _session()
        draft = _save(sid)
        with pytest.raises(ConclusionError) as exc:
            promote_conclusion(draft['id'])
        assert exc.value.code == 'not_confirmed'
        confirm_conclusion(draft['id'], by='operator')
        other = _save(_session('sess-f13-norun'), summary='no runbook please',
                      runbook_candidate=False)
        confirm_conclusion(other['id'], by='operator')
        with pytest.raises(ConclusionError) as exc:
            promote_conclusion(other['id'])
        assert exc.value.code == 'not_a_candidate'

    def test_f13_runbooks_are_searchable_and_only_confirmed(self, temp_db):
        sid = _session()
        row = _save(sid, summary='nginx proxy restart', tags=['nginx'],
                    runbook_candidate=True)
        confirm_conclusion(row['id'], by='operator')
        runbook = promote_conclusion(row['id'], title='Restart the proxy')
        found = find_runbooks('proxy')
        assert found['count'] == 1
        assert found['runbooks'][0]['id'] == runbook['id']
        assert found['runbooks'][0]['step_count'] == 0
        tags_hit = find_runbooks('', tags=['nginx'])
        assert tags_hit['count'] == 1
        assert get_runbook_payload(runbook['id'])['title'] == 'Restart the proxy'
        assert get_runbook_payload(999999) is None

    def test_f13_runbook_search_matches_each_term(self, temp_db):
        """S2: terms are matched one by one, across fields -- the old query
        required the whole phrase inside a single column."""
        sid = _session()
        row = _save(sid, summary='reload nginx after the certificate renews',
                    tags=['nginx', 'certificate'], runbook_candidate=True)
        confirm_conclusion(row['id'], by='operator')
        promote_conclusion(row['id'], title='Proxy certificate renewal')
        # Two terms, only one of which is in the title, the other in the
        # summary.
        found = find_runbooks('renew proxy')
        assert found['count'] == 1
        # A term from the tags and one from the title.
        assert find_runbooks('nginx renewal')['count'] == 1
        # Any unmatched term excludes the runbook.
        assert find_runbooks('renew ssh')['count'] == 0
        # Order and spacing do not matter; duplicates are harmless.
        assert find_runbooks('  proxy   renew ')['count'] == 1


class TestMcpTools:

    def test_f13_session_conclude_and_summary_reminder(self, temp_db):
        sid = _session(agent='agent-a')
        agent = _agent('agent-a')
        with _acting_as(agent):
            out = json.loads(session_conclude_fn()(
                outcome='solved', summary='restarted nginx',
                worked=[], session_id=sid))
            assert out['status'] == 'draft'
            summary = json.loads(session_summary_fn()(sid))
            assert summary['conclusion']['status'] == 'draft'
            assert 'operator review' in summary['next_step']
            confirm_conclusion(out['id'], by='operator')
            summary = json.loads(session_summary_fn()(sid))
            assert summary['conclusion']['status'] == 'confirmed'
            assert 'similar_incidents' in summary['next_step']

    def test_f13_a_bad_ref_is_an_error_not_a_crash(self, temp_db):
        sid = _session(agent='agent-a')
        agent = _agent('agent-a')
        with _acting_as(agent):
            out = json.loads(session_conclude_fn()(
                outcome='solved', summary='x',
                worked=[{'ref': 'req:999999'}], session_id=sid))
        assert out['error'] == 'ref_not_in_session'

    def test_f13_retrieval_tools_return_confirmed_only(self, temp_db):
        sid = _session(agent='agent-a')
        row = _save(sid, summary='nginx certificate fix')
        confirm_conclusion(row['id'], by='operator')
        promote_conclusion(row['id'], title='Fix the certificate')
        with _acting_as(_agent('agent-a')):
            incidents = json.loads(similar_incidents_fn()('certificate'))
            assert incidents['count'] == 1
            runbooks = json.loads(find_runbooks_fn()('certificate'))
            assert runbooks['count'] == 1
            runbook = json.loads(get_runbook_fn()(runbooks['runbooks'][0]['id']))
            assert runbook['title'] == 'Fix the certificate'
            assert json.loads(get_runbook_fn()(999999))['error'] == 'not_found'


class TestApi:

    def test_f13_review_and_promote_endpoints(self, auth_client, temp_db):
        sid = _session(agent='agent-a')
        row = _save(sid, summary='api review path')
        detail = auth_client.get(f'/api/sessions/{sid}/detail').json()
        assert detail['conclusions'][0]['id'] == row['id']
        assert detail['conclusions'][0]['status'] == 'draft'
        # Edit, then confirm, then promote.
        edited = auth_client.patch(f'/api/conclusions/{row["id"]}',
                                   json={'summary': 'operator summary'})
        assert edited.status_code == 200
        assert edited.json()['conclusion']['summary'] == 'operator summary'
        confirmed = auth_client.post(f'/api/conclusions/{row["id"]}/confirm',
                                     json={'note': 'looks right'})
        assert confirmed.status_code == 200
        assert confirmed.json()['conclusion']['status'] == 'confirmed'
        promoted = auth_client.post(f'/api/conclusions/{row["id"]}/runbook',
                                    json={'title': 'Operator runbook'})
        assert promoted.status_code == 200
        runbook_id = promoted.json()['runbook']['id']
        detail = auth_client.get(f'/api/sessions/{sid}/detail').json()
        assert detail['conclusions'][0]['runbook_id'] == runbook_id
        assert detail['runbooks'][0]['id'] == runbook_id
        fetched = auth_client.get(f'/api/runbooks/{runbook_id}')
        assert fetched.status_code == 200
        assert fetched.json()['title'] == 'Operator runbook'

    def test_f13_reject_and_error_codes(self, auth_client, temp_db):
        sid = _session(agent='agent-a')
        row = _save(sid)
        assert auth_client.post(f'/api/conclusions/{row["id"]}/reject',
                                json={}).json()['conclusion']['status'] == 'rejected'
        # A rejected conclusion cannot be promoted or confirmed.
        assert auth_client.post(f'/api/conclusions/{row["id"]}/confirm',
                                json={}).status_code == 409
        assert auth_client.post(f'/api/conclusions/{row["id"]}/runbook',
                                json={}).status_code == 409
        assert auth_client.post('/api/conclusions/999999/confirm',
                                json={}).status_code == 404
        assert auth_client.get('/api/runbooks/999999').status_code == 404

    def test_f13_requires_a_session(self, client):
        assert client.post('/api/conclusions/1/confirm', json={}).status_code in (401, 403)
        assert client.get('/api/sessions/sess/detail').status_code in (401, 403)


@pytest.mark.skipif(NODE is None, reason='node is not installed')
class TestUi:

    def _run(self, payload):
        proc = subprocess.run([NODE, HARNESS, json.dumps(payload)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert 'error' not in out, out['error']
        return out

    def test_f13_conclusion_card_escapes_agent_text(self):
        payload = '<img src=x onerror=alert(1)>'
        out = self._run({'payload': payload})
        for key in ('draft', 'confirmed'):
            html = out[key]
            assert payload not in html, key
            assert '&lt;img' in html, key

    def test_f13_card_states_and_buttons(self):
        out = self._run({'payload': 'x'})
        assert 'Confirm' in out['draft'] and 'Reject' in out['draft']
        assert 'Edit' in out['draft']
        assert 'Promote to runbook' in out['confirmed']
        assert 'Runbook #7' in out['withRunbook']
        assert 'cc-status cc-rejected' in out['rejected']
        # S3: the draft says what confirming means; a confirmed one need not.
        assert 'Agents will follow this once confirmed.' in out['draft']
        assert 'Agents will follow this once confirmed.' not in out['confirmed']

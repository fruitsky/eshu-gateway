"""Chunk X1 — sidebar actions and pending-only decisions (BUG)."""

import json
import os
import shutil
import subprocess

import pytest

from db.audit import get_audit_log
from db.requests import create_request, get_request_row

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'x1_harness.js')
NODE = shutil.which('node')


def run_node(payload):
    proc = subprocess.run([NODE, HARNESS, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert 'error' not in out, out['error']
    return out


def _approved_events():
    return [e for e in get_audit_log() if e['event_type'] == 'jit_approved']


class TestPendingOnly:

    def test_x1_approve_pending_still_works(self, auth_client):
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        response = auth_client.post('/api/approve/%d' % req_id)
        assert response.status_code == 200
        assert get_request_row(req_id)['status'] == 'approved'

    def test_x1_deny_pending_still_works(self, auth_client):
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        response = auth_client.post('/api/deny/%d' % req_id)
        assert response.status_code == 200
        assert get_request_row(req_id)['status'] == 'denied'

    @pytest.mark.parametrize('status', ['denied', 'expired', 'consumed', 'blocked',
                                        'approved', 'auto-approved'])
    def test_x1_approve_non_pending_is_409(self, auth_client, status):
        req_id = create_request('10.0.0.1', 'uptime', status=status, ttl=0)
        response = auth_client.post('/api/approve/%d' % req_id)
        assert response.status_code == 409
        assert response.json()['detail'] == 'not_pending'
        assert get_request_row(req_id)['status'] == status
        assert _approved_events() == []

    @pytest.mark.parametrize('status', ['approved', 'denied', 'expired', 'consumed',
                                        'blocked'])
    def test_x1_deny_non_pending_is_409(self, auth_client, status):
        req_id = create_request('10.0.0.1', 'uptime', status=status, ttl=0)
        response = auth_client.post('/api/deny/%d' % req_id)
        assert response.status_code == 409
        assert get_request_row(req_id)['status'] == status
        assert [e for e in get_audit_log() if e['event_type'] == 'jit_denied'] == []

    def test_x1_approve_twice_is_409(self, auth_client):
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        assert auth_client.post('/api/approve/%d' % req_id).status_code == 200
        second = auth_client.post('/api/approve/%d' % req_id)
        assert second.status_code == 409
        assert len(_approved_events()) == 1

    def test_x1_unknown_request_is_404(self, auth_client):
        assert auth_client.post('/api/approve/999999').status_code == 404
        assert auth_client.post('/api/deny/999999').status_code == 404

    def test_x1_bulk_approve_sequence_still_works(self, auth_client):
        """The bulk path posts one approve per selected pending id."""
        ids = [create_request('10.0.0.1', 'uptime %d' % i, status='pending', ttl=90)
               for i in range(3)]
        for req_id in ids:
            assert auth_client.post('/api/approve/%d' % req_id).status_code == 200
        assert all(get_request_row(req_id)['status'] == 'approved' for req_id in ids)


@pytest.mark.skipif(NODE is None, reason='node is not installed')
class TestSidebar:

    def test_x1_panel_dispatches_by_type(self):
        out = run_node({'action': 'panel'})
        assert 'approveIntegrationCall(3)' in out['integration']
        assert 'handleAction(3' not in out['integration']
        assert 'approveWindowReq(4)' in out['window']
        assert 'handleAction(4' not in out['window']
        assert 'handleAction(5' in out['ssh']
        assert 'approveIntegrationCall(5)' not in out['ssh']
        assert 'Close' in out['closed']

    def test_x1_sidebar_two_press_shows_armed_state(self):
        out = run_node({'action': 'two-press'})
        assert out['firstPosts'] == 0
        assert out['labelAfterFirst'] == 'Press again to approve'
        assert out['secondPosts'] == 1
        assert out['secondUrl'] == '/api/approve/7'

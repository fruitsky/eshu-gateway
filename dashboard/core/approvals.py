"""The one approve path.

`POST /api/approve/{id}` and F21's "Snapshot, then run" must produce the same
result: `decided_at` stamped by the conditional UPDATE (F6/F18 read it), and
the `jit_approved` audit row. Both call `approve_pending_request()` so the two
paths cannot drift (F21 AS3).

`decided_by` is not a `requests` column — the tables that carry it are
`integration_calls` and `session_plans` — so "the same shape" here means the
request row and the audit row the plain button writes.
"""
from db.audit import record_audit_event
from db.requests import get_request_row, update_request_status_if_pending


def approve_pending_request(request_id):
    """Approve one pending request.

    Returns 'approved', 'not_found' or 'not_pending'. The status UPDATE is
    conditional, so a racing press (or an old id) can never be approved twice.
    """
    request_id = int(request_id)
    if not get_request_row(request_id):
        return 'not_found'
    if not update_request_status_if_pending(request_id, 'approved'):
        return 'not_pending'
    record_audit_event('jit_approved', details='JIT #%d approved' % request_id)
    return 'approved'

"""BY1–BY3: the scoped E2E approver token.

The token is accepted only as `Authorization: Bearer e2e_...` on the small
route set listed here. Every other route is refused before the handler runs.
The row-level rules (agent id, session owner, command list, gateway, test
agent) live in the route helpers in main.py.
"""
import re

from fastapi import HTTPException, Request

from db.approver_tokens import get_approver_by_token, touch_approver_token
from db.audit import record_audit_event

PREFIX = 'e2e_'

_ALLOWED = (
    ('POST', r'/api/approve/\d+'),
    ('POST', r'/api/deny/\d+'),
    ('POST', r'/api/session-plans/\d+/approve'),
    ('POST', r'/api/session-plans/\d+/deny'),
    ('POST', r'/api/window-requests/\d+/approve'),
    ('POST', r'/api/window-requests/\d+/deny'),
    ('POST', r'/api/integration-calls/\d+/deny'),
    ('POST', r'/api/requests/exclude'),
    ('POST', r'/api/conclusions/\d+/reject'),
    ('DELETE', r'/api/approved-windows/\d+'),
    ('POST', r'/api/allowances/\d+/revoke'),
    ('GET', r'/api/requests'),
    ('GET', r'/api/sessions/[^/]+/detail'),
    ('GET', r'/api/session-plans'),
    ('GET', r'/api/session-plans/\d+'),
    ('GET', r'/api/integration-calls'),
    ('GET', r'/api/integration-calls/pending'),
    ('GET', r'/api/agents'),
    ('GET', r'/api/agents/\d+/activity'),
    ('GET', r'/api/e2e/installer-hash'),
)
_ALLOWED = tuple((m.upper(), re.compile(p + r'$')) for m, p in _ALLOWED)


def extract_approver_token(request: Request) -> str:
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        token = auth[7:].strip()
        return token if token.startswith(PREFIX) else ''
    return ''


def resolve_approver_optional(request: Request):
    raw = extract_approver_token(request)
    if not raw:
        return None
    row = get_approver_by_token(raw)
    if row:
        touch_approver_token(row['id'])
    return row


def route_allowed(method: str, path: str) -> bool:
    method = (method or '').upper()
    for allowed_method, pattern in _ALLOWED:
        if method == allowed_method and pattern.match(path or ''):
            return True
    return False


def audit(approver, event_type: str, details: str, target: str = None):
    token_id = (approver or {}).get('id') or '?'
    record_audit_event(
        event_type, gateway_ip=target,
        details='approver token #%s: %s' % (token_id, details),
        actor='e2e-approver')


def require_test_agent(approver):
    from db.agent_policies import get_agent_policy
    from db.agent_tokens import get_agent
    agent = get_agent(int(approver.get('agent_id') or 0))
    if not agent or agent.get('revoked'):
        raise HTTPException(status_code=403, detail='approver agent is not live')
    policy = get_agent_policy(agent['id'])
    if not policy.get('test_agent'):
        raise HTTPException(status_code=403, detail='approver agent is not a test agent')
    return agent, policy


def session_owned_by_approver(approver, session_id: str):
    """BY1: the session must have been opened by the token's agent id, and the
    goal must start E2E: (the extra check, not the boundary)."""
    from db.sessions import get_session
    sid = (session_id or '').strip()[:64]
    if not sid:
        return None
    session = get_session(sid)
    if not session:
        return None
    if int(session.get('agent_id') or 0) != int(approver.get('agent_id') or 0):
        return None
    if not (session.get('goal') or '').startswith('E2E:'):
        return None
    return session


def row_owned_by_approver(approver, row, require_session=True):
    if not row:
        return False
    if int(row.get('agent_id') or 0) != int(approver.get('agent_id') or 0):
        return False
    if require_session and not session_owned_by_approver(
            approver, row.get('session_id') or ''):
        return False
    return True


def command_allowed(approver, command: str) -> bool:
    return str(command or '') in set(approver.get('commands') or [])


def gateway_allowed(approver, gateway_ip: str) -> bool:
    return str(gateway_ip or '') == str(approver.get('gateway_ip') or '')


def refuse(detail: str, status: int = 403):
    raise HTTPException(status_code=status, detail=detail)

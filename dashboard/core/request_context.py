"""F18: the sidebar's request context, computed on demand.

One request, five capped sections. Everything comes from indexed columns
(`command_sha`, `shape_sha`, `session_id`) plus the existing approval-path
helpers; nothing here runs in the 3 s poll. Agent text is returned raw — the
UI escapes it.
"""
import time

from core.cmd_parse import breakdown
from core.cmd_blocklist import has_shell_metacharacters
from core.policy_eval import evaluate_policy_verdict

try:
    # F4 may not be merged into this branch's base yet; the read-lane reasons
    # switch on automatically when it is (§ 2.6a: one read policy).
    from core.read_lane import eligible as read_lane_eligible
except Exception:  # pragma: no cover - depends on the merge order
    read_lane_eligible = None

from db.core import db_conn
from db.exec_results import get_result
from db.gateways import get_gateway, get_gateway_zero_trust
try:
    from db.gateways import get_gateway_read_lane
except Exception:  # pragma: no cover - F4 not merged yet
    def get_gateway_read_lane(ip):  # noqa: E306 - tiny fallback
        return 'off'

from db.overrides import get_session_override
from db.policies import get_policies
from db.requests import get_request_row
from db.session_plans import get_active_plan
from db.sessions import get_session

SESSION_ROWS = 10
EXAMPLES = 3
MAX_TEXT = 300
SEEN_DAYS = 90
APPROVED_STATUSES = ('approved', 'consumed', 'auto-approved', 'window-approved')

# The gateway's HAS_META set (stage 2.7), longest forms first so the reason can
# name what it found (`&&`, not `&`).
_METACHARS = ('$(', '&&', '||', ';', '&', '|', '`', '>', '<', '\n', '\r', '\\')

# C1: "approved" in this panel means a human decision. Plan items and the read
# lane are automatic, so they are counted separately.
OPERATOR_APPROVED_SQL = """
    r.status IN ('approved','consumed')
    AND COALESCE(r.reason, '') NOT LIKE 'session-plan:%'
    AND COALESCE(r.reason, '') NOT LIKE 'read-lane%'
"""
AUTO_SQL = """
    r.status IN ('auto-approved','window-approved')
    OR (r.status IN ('approved','consumed')
        AND (COALESCE(r.reason, '') LIKE 'session-plan:%'
             OR COALESCE(r.reason, '') LIKE 'read-lane%'))
"""

_INTERPRETERS = ('bash', 'sh', 'python', 'python2', 'python3')


def _cut(text) -> str:
    return (text or '')[:MAX_TEXT]


def _metachar(command: str):
    for token in _METACHARS:
        if token in (command or ''):
            return token
    return ''


def waiting_reason(row) -> str:
    """Why this row is waiting for a human (first rule that applies)."""
    command = row.get('command') or ''
    ip = row.get('target_ip') or ''
    bd = breakdown(command)
    flags = bd.get('flags') or []
    if 'encoded' in flags:
        return 'contains an encoded payload'
    meta = _metachar(command)
    if meta and has_shell_metacharacters(command):
        return 'contains %s (metacharacter rule)' % meta
    if get_gateway_zero_trust(ip):
        return 'Zero-Trust is on for this host'
    policies = get_policies()
    verdict = evaluate_policy_verdict(
        command, policies.get('exact_whitelist', ''),
        policies.get('regex_whitelist', ''), policies.get('regex_blacklist', ''))
    mode = get_gateway_read_lane(ip)
    if read_lane_eligible is not None:
        lane_ok, lane_why = read_lane_eligible(bd, verdict, command, 'on', False, False)
        if lane_ok:
            if mode == 'shadow':
                return 'the read lane is in shadow mode (would auto-run, waiting for a human)'
            if mode == 'off':
                return 'the read lane is off on this host'
        elif mode in ('on', 'shadow'):
            return 'read lane ineligible: %s' % lane_why
    if verdict.get('action') != 'auto_approved':
        return 'no allowlist match'
    return 'no plan or session override matched'


def _session_section(row) -> dict:
    sid = (row.get('session_id') or '')[:64]
    if not sid:
        return None
    session = get_session(sid)
    with db_conn() as conn:
        rows = conn.execute('''
            SELECT r.id, r.command, r.status, r.created_at,
                   x.exit_code, x.duration_ms
            FROM requests r
            LEFT JOIN request_results x ON x.request_id = r.id
            WHERE r.session_id = ?
            ORDER BY r.id DESC LIMIT ?
        ''', (sid, SESSION_ROWS)).fetchall()
        plan = get_active_plan(sid)
        override = get_session_override(sid, row.get('target_ip') or '', int(time.time()))
    return {
        'session_id': sid,
        'goal': (session or {}).get('goal') or '',
        'agent': (session or {}).get('agent') or '',
        'commands': [{'id': r['id'], 'command': _cut(r['command']),
                      'status': r['status'], 'created_at': r['created_at'],
                      'exit_code': r['exit_code'], 'duration_ms': r['duration_ms']}
                     for r in rows],
        'plan': ({'id': plan['id'], 'until': plan['until'], 'items': len(plan['items'])}
                 if plan else None),
        'override': ({'until': override.get('until'), 'reason': override.get('reason')}
                     if override else None),
    }


def _seen_section(row) -> dict:
    sha = row.get('command_sha')
    empty = {'count': 0, 'last_at': 0, 'operator_approved': 0, 'auto': 0,
             'denied': 0, 'last_exit_code': None}
    if not sha:
        return empty
    since = int(time.time()) - SEEN_DAYS * 86400
    with db_conn() as conn:
        agg = conn.execute('''
            SELECT COUNT(*) AS n, MAX(r.created_at) AS last_at,
                   SUM(CASE WHEN %s THEN 1 ELSE 0 END) AS operator_approved,
                   SUM(CASE WHEN %s THEN 1 ELSE 0 END) AS auto,
                   SUM(CASE WHEN r.status = 'denied' THEN 1 ELSE 0 END) AS denied
            FROM requests r WHERE r.command_sha = ? AND r.created_at >= ?
        ''' % (OPERATOR_APPROVED_SQL, AUTO_SQL), (sha, since)).fetchone()
        last = conn.execute('''
            SELECT x.exit_code FROM requests r
            JOIN request_results x ON x.request_id = r.id
            WHERE r.command_sha = ? AND r.created_at >= ?
            ORDER BY r.id DESC LIMIT 1
        ''', (sha, since)).fetchone()
    return {
        'count': int(agg['n'] or 0),
        'last_at': int(agg['last_at'] or 0),
        'operator_approved': int(agg['operator_approved'] or 0),
        'auto': int(agg['auto'] or 0),
        'denied': int(agg['denied'] or 0),
        'last_exit_code': last['exit_code'] if last else None,
    }


def _shape_is_generic(breakdown_result) -> bool:
    """C2: one-step interpreter shapes have no comparable shape."""
    steps = (breakdown_result or {}).get('steps') or []
    if len(steps) >= 2:
        return False
    verb = ((steps[0].get('text') or '').split() or [''])[0] if steps else ''
    return verb in _INTERPRETERS or verb.startswith('python')


def _like_section(row, breakdown_result=None) -> dict:
    sha = row.get('shape_sha')
    empty = {'count': 0, 'operator_approved': 0, 'auto': 0, 'denied': 0,
             'examples': [], 'generic': False}
    if not sha:
        return empty
    if _shape_is_generic(breakdown_result):
        return {'count': 0, 'operator_approved': 0, 'auto': 0, 'denied': 0,
                'examples': [], 'generic': True}
    since = int(time.time()) - SEEN_DAYS * 86400
    with db_conn() as conn:
        agg = conn.execute('''
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN %s THEN 1 ELSE 0 END) AS operator_approved,
                   SUM(CASE WHEN %s THEN 1 ELSE 0 END) AS auto,
                   SUM(CASE WHEN r.status = 'denied' THEN 1 ELSE 0 END) AS denied
            FROM requests r
            WHERE r.shape_sha = ? AND r.created_at >= ? AND r.id != ?
        ''' % (OPERATOR_APPROVED_SQL, AUTO_SQL),
            (sha, since, row['id'])).fetchone()
        examples = conn.execute('''
            SELECT r.id, r.command, r.created_at, g.hostname
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            WHERE r.shape_sha = ? AND r.created_at >= ? AND r.id != ?
            ORDER BY r.id DESC LIMIT ?
        ''', (sha, since, row['id'], EXAMPLES)).fetchall()
    return {
        'count': int(agg['n'] or 0),
        'operator_approved': int(agg['operator_approved'] or 0),
        'auto': int(agg['auto'] or 0),
        'denied': int(agg['denied'] or 0),
        'generic': False,
        'examples': [{'id': r['id'], 'command': _cut(r['command']),
                      'hostname': r['hostname'] or '', 'created_at': r['created_at']}
                     for r in examples],
    }


def _host_section(row) -> dict:
    ip = row.get('target_ip') or ''
    gateway = get_gateway(ip) or {}
    now = int(time.time())
    with db_conn() as conn:
        failed = conn.execute('''
            SELECT COUNT(*) AS n FROM requests r
            JOIN request_results x ON x.request_id = r.id
            WHERE r.target_ip = ? AND x.exit_code != 0 AND r.created_at >= ?
        ''', (ip, now - 86400)).fetchone()['n']
    return {
        'hostname': gateway.get('hostname') or '',
        'inner_target': row.get('inner_target') or '',
        'zero_trust': bool(gateway.get('zero_trust')),
        'read_lane': get_gateway_read_lane(ip),
        'last_seen': gateway.get('last_seen') or 0,
        'failed_24h': int(failed or 0),
    }


def build_context(request_id: int):
    """The whole context payload, or None when the request does not exist."""
    row = get_request_row(request_id)
    if not row:
        return None
    bd = breakdown(row.get('command') or '')
    return {
        'request_id': int(request_id),
        # C3: only a pending row is "waiting", and the reason is re-derived —
        # the UI words it as a likely reason, not a verdict.
        'waiting': waiting_reason(row) if row.get('status') == 'pending' else None,
        'session': _session_section(row),
        'seen': _seen_section(row),
        'like': _like_section(row, bd),
        'host': _host_section(row),
    }

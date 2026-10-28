"""Per-principal policy storage for the mediated web fetch.

Keyed by the agent/principal **name** (e.g. ``household``), not the token id, so
an allowlist survives token rotation. The URL matcher lives in
``core.web_fetch_policy``; this module only persists validated entries.
"""
import json
import time

from core.web_fetch_policy import validate_policy
from db.core import db_conn


def init_web_fetch_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS web_fetch_policies (
            principal TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            allow_json TEXT NOT NULL DEFAULT '[]',
            deny_json TEXT NOT NULL DEFAULT '[]',
            updated_at INTEGER NOT NULL DEFAULT 0
        )
    ''')


def _load_list(text):
    try:
        value = json.loads(text or '[]')
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _row_to_policy(row):
    if not row:
        return None
    item = dict(row)
    return {
        'principal': item.get('principal') or '',
        'enabled': bool(item.get('enabled')),
        'allow': _load_list(item.get('allow_json')),
        'deny': _load_list(item.get('deny_json')),
        'updated_at': int(item.get('updated_at') or 0),
    }


def get_web_fetch_policy(principal: str):
    """Return one principal's policy, or None when it has never been configured."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM web_fetch_policies WHERE principal = ?',
                       (principal or '',))
        return _row_to_policy(cursor.fetchone())


def get_web_fetch_policies():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM web_fetch_policies ORDER BY principal ASC')
        return [_row_to_policy(row) for row in cursor.fetchall()]


def set_web_fetch_policy(principal: str, enabled: bool = False, allow=None,
                         deny=None):
    """Validate + persist one principal's policy. Raises ValueError on bad input."""
    principal = (principal or '').strip()
    if not principal:
        raise ValueError('principal is required')
    clean_allow, clean_deny = validate_policy(allow or [], deny or [])
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO web_fetch_policies
                (principal, enabled, allow_json, deny_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (principal, 1 if enabled else 0, json.dumps(clean_allow),
              json.dumps(clean_deny), int(time.time())))
        conn.commit()
    return get_web_fetch_policy(principal)


def delete_web_fetch_policy(principal: str) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM web_fetch_policies WHERE principal = ?',
                       (principal or '',))
        conn.commit()
        return cursor.rowcount > 0


def _counter_for(code: str) -> str:
    if code == 'ok':
        return 'fetch_ok'
    if code in {'host_not_allowed', 'host_denied', 'disabled',
                'principal_not_allowed', 'tool_not_allowed'}:
        return 'fetch_denied_allowlist'
    if code == 'private_address':
        return 'fetch_denied_ssrf'
    if code in {'redirect_not_allowed', 'too_many_redirects'}:
        return 'fetch_denied_redirect'
    if code in {'rate_limited', 'concurrency_limited'}:
        return 'fetch_rate_limited'
    return 'fetch_error_upstream'


def get_web_fetch_stats(principal: str = None) -> dict:
    """Counters derived from the audit rows (no separate metrics store)."""
    where = "tool = 'fetch_url'"
    params = []
    if principal:
        where += ' AND agent = ?'
        params.append(principal)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT reason_code, COUNT(*) AS n FROM integration_calls '
            f'WHERE {where} GROUP BY reason_code', params)
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.execute(
            f'SELECT COUNT(*) AS total, COALESCE(SUM(response_bytes), 0) AS bytes '
            f'FROM integration_calls WHERE {where}', params)
        totals = dict(cursor.fetchone())

    counters = {
        'fetch_ok': 0,
        'fetch_denied_allowlist': 0,
        'fetch_denied_ssrf': 0,
        'fetch_denied_redirect': 0,
        'fetch_error_upstream': 0,
        'fetch_rate_limited': 0,
    }
    by_code = {}
    for row in rows:
        code = row.get('reason_code') or 'unknown'
        count = int(row.get('n') or 0)
        by_code[code] = count
        counters[_counter_for(code)] += count
    return {
        'principal': principal or 'all',
        'total': int(totals.get('total') or 0),
        'bytes': int(totals.get('bytes') or 0),
        'counters': counters,
        'by_code': by_code,
    }

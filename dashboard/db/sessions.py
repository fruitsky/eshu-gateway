"""Dashboard Sessions — the unit an agent's work is grouped by.

A session id can come from three places, in order of quality:

  * `session_begin(goal)` — the agent names the task and gets an id to reuse
    across SSH and MCP                    (source = 'agent')
  * the caller's own session_id argument  (source = 'caller')
  * the MCP transport session             (source = 'mcp-transport', id `mcp-…`)

Only the first can have a goal; the other two are still real activity and are
kept so the audit is never blank. Nothing here is used for a decision — the
table is metadata for the session view, and the request/call tables remain the
source of truth for what actually happened.
"""
import secrets
import time

from db.core import db_conn


def init_sessions_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            agent TEXT NOT NULL DEFAULT '',
            goal TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'agent',
            started_at INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            ended_at INTEGER
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_last_seen ON sessions(last_seen)')


def new_session_id() -> str:
    """12 hex chars — the shape the agents already use (e.g. cf0f09869854)."""
    return secrets.token_hex(6)


def upsert_session(session_id: str, agent: str = '', goal: str = None,
                   source: str = 'agent', seen_at: int = None) -> dict:
    """Create the row on first sight, touch it afterwards.

    `goal=None` leaves an existing goal alone (touching a session must not
    erase its name); an empty goal on a new row is allowed.
    """
    sid = (session_id or '')[:64]
    if not sid:
        return {}
    now = int(seen_at or time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO sessions (id, agent, goal, source, started_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_seen = excluded.last_seen,
                agent = CASE WHEN sessions.agent = '' THEN excluded.agent ELSE sessions.agent END,
                goal = CASE WHEN excluded.goal != '' THEN excluded.goal ELSE sessions.goal END
        ''', (sid, (agent or '')[:64], (goal or '')[:160], source, now, now))
        conn.commit()
        cur.execute('SELECT * FROM sessions WHERE id = ?', (sid,))
        row = cur.fetchone()
        return dict(row) if row else {}


def get_session(session_id: str) -> dict:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT * FROM sessions WHERE id = ?', ((session_id or '')[:64],))
        row = cur.fetchone()
        return dict(row) if row else {}


def get_session_meta_map(session_ids) -> dict:
    """{id: {goal, agent, source}} for the ids that have a row, for the list."""
    ids = [s for s in (session_ids or []) if s]
    if not ids:
        return {}
    out = {}
    with db_conn() as conn:
        cur = conn.cursor()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            ph = ','.join('?' * len(chunk))
            cur.execute(f'SELECT id, goal, agent, source FROM sessions WHERE id IN ({ph})', chunk)
            for row in cur.fetchall():
                out[row['id']] = dict(row)
    return out


def get_session_summary(session_id: str) -> dict:
    """What this session did: SSH commands by outcome, MCP calls, decisions."""
    sid = (session_id or '')[:64]
    out = {
        'session_id': sid,
        'commands': 0, 'pending': 0, 'approved': 0, 'refused': 0, 'expired': 0,
        'mcp_calls': 0, 'mcp_pending': 0,
        'gateways': [], 'first_seen': 0, 'last_seen': 0,
    }
    if not sid:
        return out
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT
              COUNT(*) AS n,
              SUM(status = 'pending') AS pending,
              SUM(status IN ('approved','auto-approved','consumed','window-approved','fleet-run')) AS approved,
              SUM(status IN ('denied','blocked','window-rejected','integration-denied','frozen')) AS refused,
              SUM(status = 'expired') AS expired,
              MIN(created_at) AS first_seen,
              MAX(created_at) AS last_seen
            FROM requests WHERE session_id = ?
        ''', (sid,))
        row = cur.fetchone()
        if row:
            out.update({k: int(row[k] or 0) for k in
                        ('n', 'pending', 'approved', 'refused', 'expired',
                         'first_seen', 'last_seen')})
            out['commands'] = out.pop('n')
        cur.execute('''
            SELECT g.hostname, r.target_ip
            FROM requests r LEFT JOIN gateways g ON r.target_ip = g.ip
            WHERE r.session_id = ?
            GROUP BY r.target_ip
        ''', (sid,))
        out['gateways'] = [r['hostname'] or r['target_ip'] for r in cur.fetchall()]
        cur.execute('SELECT COUNT(*) AS n FROM integration_calls WHERE session_id = ?', (sid,))
        out['mcp_calls'] = int(cur.fetchone()['n'] or 0)
        cur.execute('SELECT COUNT(*) AS n FROM pending_integration_calls WHERE session_id = ?', (sid,))
        out['mcp_pending'] = int(cur.fetchone()['n'] or 0)
    return out


def get_session_coverage(days: int = 7) -> dict:
    """How much traffic carries a caller-supplied session id.

    A quality metric, not a rule: SSH tagging is voluntary, so this is the
    number that shows whether asking the agent is working. On the MCP side only
    an explicit argument counts: an inherited id (bound by session_begin) and
    the `mcp-…` transport fallback are reported as `auto`, so making the
    fallback smarter does not make the agents look more compliant than they
    are. Historical rows (session_source = '') keep the old behaviour.
    """
    since = int(time.time()) - max(1, int(days)) * 86400
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN session_id NOT IN ('', 'unknown') THEN 1 ELSE 0 END) AS tagged,
                   0 AS auto
            FROM requests WHERE created_at >= ?
        ''', (since,))
        ssh = dict(cur.fetchone())
        cur.execute('''
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN session_id NOT IN ('', 'unknown')
                             AND session_source NOT IN ('inherited', 'transport')
                             AND session_id NOT LIKE 'mcp-%' THEN 1 ELSE 0 END) AS tagged,
                   SUM(CASE WHEN session_source IN ('inherited', 'transport')
                             OR session_id LIKE 'mcp-%' THEN 1 ELSE 0 END) AS auto
            FROM integration_calls WHERE created_at >= ?
        ''', (since,))
        mcp = dict(cur.fetchone())
    out = {'days': days}
    for label, row in (('ssh', ssh), ('mcp', mcp)):
        total = int(row.get('total') or 0)
        tagged = int(row.get('tagged') or 0)
        out[label] = {'total': total, 'tagged': tagged,
                      'auto': int(row.get('auto') or 0),
                      'pct': round(100.0 * tagged / total) if total else 0}
    return out

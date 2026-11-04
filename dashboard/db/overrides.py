"""Session-scoped overrides — pre-authorise one session's JIT commands.

A host override (`gateways.override_until`) is deliberately broad: while it is
active every JIT command on that host is auto-approved, no matter which session
sent it. A session override is the narrow version: it only covers commands
carrying one session id, and by default only on one host (an empty `target_ip`
means "any host in this session", an explicit operator choice).

It is an approval bypass, so it is dashboard-only (never an MCP tool) and it
never overrides Zero-Trust or Freeze. The host override stays in place for the
cases it is actually good at: onboarding a host, or work that has no session.
"""
import time

from db.core import db_conn


def init_session_overrides_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS session_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            target_ip TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            until INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'operator',
            ended_at INTEGER NOT NULL DEFAULT 0
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_session_overrides_live '
        'ON session_overrides(session_id, until)')


def create_session_override(session_id: str, target_ip: str, until: int,
                            reason: str = '', created_by: str = 'operator',
                            now: int = None) -> dict:
    """Grant (or replace) an override for one session on one host.

    Re-granting the same `(session_id, target_ip)` ends the previous live row
    first, so the list never shows two identical grants with different clocks.
    """
    sid = (session_id or '')[:64]
    if not sid:
        return {}
    now = int(now or time.time())
    ip = (target_ip or '')[:64]
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_overrides SET ended_at = ?
            WHERE session_id = ? AND target_ip = ? AND ended_at = 0
        ''', (now, sid, ip))
        cur.execute('''
            INSERT INTO session_overrides
                (session_id, target_ip, reason, until, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (sid, ip, (reason or '')[:160], int(until), now,
              (created_by or 'operator')[:64]))
        conn.commit()
        cur.execute('SELECT * FROM session_overrides WHERE id = ?',
                    (cur.lastrowid,))
        row = cur.fetchone()
        return dict(row) if row else {}


def get_session_override(session_id: str, target_ip: str,
                         now: int = None) -> dict:
    """The live override for this call: exact host beats any-host.

    Returns `{}` when the session has no live grant. An expired grant and a
    cancelled one are both invisible here; the audit/log still has them.
    """
    sid = (session_id or '')[:64]
    if not sid:
        return {}
    now = int(now or time.time())
    ip = (target_ip or '')[:64]
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT * FROM session_overrides
            WHERE session_id = ? AND ended_at = 0 AND until > ?
              AND (target_ip = ? OR target_ip = '')
            ORDER BY CASE WHEN target_ip = ? THEN 0 ELSE 1 END, id DESC
            LIMIT 1
        ''', (sid, now, ip, ip))
        row = cur.fetchone()
        return dict(row) if row else {}


def list_session_overrides(now: int = None, limit: int = 50) -> list:
    """Active grants first, then the recent ended/expired ones for the audit."""
    now = int(now or time.time())
    limit = max(1, min(int(limit or 50), 200))
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT * FROM session_overrides
            ORDER BY (ended_at = 0 AND until > ?) DESC, created_at DESC
            LIMIT ?
        ''', (now, limit))
        rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        row['active'] = bool(row['ended_at'] == 0 and row['until'] > now)
        row['remaining'] = max(0, int(row['until']) - now) if row['active'] else 0
    return rows


def stop_session_override(override_id: int, now: int = None) -> dict:
    """Cancel a live grant. Returns {} when it was already ended/expired."""
    now = int(now or time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_overrides SET ended_at = ?
            WHERE id = ? AND ended_at = 0
        ''', (now, int(override_id)))
        changed = cur.rowcount
        conn.commit()
        if not changed:
            return {}
        cur.execute('SELECT * FROM session_overrides WHERE id = ?',
                    (int(override_id),))
        row = cur.fetchone()
        return dict(row) if row else {}

import time
from db.core import db_conn

def init_requests_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_ip TEXT NOT NULL,
            command TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
    ''')
    # Migrations: add optional columns over time (session_id groups SSH requests into sessions)
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(requests)").fetchall()}
    for col, defval in [('reason', "''"), ('session_id', "''"), ('execution_id', "''")]:
        if col in existing:
            continue
        try:
            cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT DEFAULT {defval}")
        except Exception:
            pass
    # F7: the real target of a pct/qm/docker wrapper. Deliberately nullable:
    # NULL marks a row that predates the column and still needs the startup
    # backfill, while '' means "this command has no inner target".
    if 'inner_target' not in existing:
        try:
            cursor.execute("ALTER TABLE requests ADD COLUMN inner_target TEXT")
        except Exception:
            pass
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_requests_inner_target "
        "ON requests(target_ip, inner_target)")

def create_request(target_ip: str, command: str, status: str = 'pending', ttl: int = 90, reason: str = '', session_id: str = '', execution_id: str = '', inner_target: str = ''):
    with db_conn() as conn:
        cursor = conn.cursor()
        created_at = int(time.time())
        expires_at = created_at + ttl
        cursor.execute('''
            INSERT INTO requests (target_ip, command, status, created_at, expires_at, reason, session_id, execution_id, inner_target)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (target_ip, command, status, created_at, expires_at, reason, session_id, execution_id, inner_target or ''))
        conn.commit()
        return cursor.lastrowid

def update_request_status(req_id: int, status: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE requests SET status = ? WHERE id = ?', (status, req_id))
        conn.commit()


def update_request_status_if_pending(req_id: int, status: str) -> bool:
    """X1: approve/deny may only move a `pending` row.

    The UPDATE is conditional so two racing presses cannot both "win", and an
    old id (denied, expired, consumed, blocked) can never be re-approved by a
    stale click or a colliding integration-call id.
    """
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE requests SET status = ? WHERE id = ? AND status = 'pending'",
            (status, req_id))
        conn.commit()
        return cursor.rowcount == 1

def update_ticket_consumed_by_ip(target_ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        expired_ago = 60
        cursor.execute('''
            SELECT id, created_at, expires_at, command FROM requests
            WHERE target_ip = ? AND status = 'approved' AND expires_at > ?
            LIMIT 1
        ''', (target_ip, now - expired_ago))
        row = cursor.fetchone()
        if row:
            req_id = row['id']
            cursor.execute("UPDATE requests SET status = 'consumed', expires_at = ? WHERE id = ?", (now, req_id))
            conn.commit()
            ticket_ts = row['created_at']
            return f"{ticket_ts}|{row['command']}"
        return None

def get_all_requests(session_id: str = None, inner_target: str = None):
    """Newest 200 requests, optionally narrowed to one session or inner target.

    A session filter has to run in SQL, not on the returned page: a session's
    oldest commands are usually outside the newest 200 rows. `inner_target`
    (F7) is an exact match on the recorded container/VM target.
    """
    with db_conn() as conn:
        cursor = conn.cursor()
        base = '''
            SELECT r.*, g.hostname, x.exit_code, x.duration_ms,
                   x.truncated AS output_truncated
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            LEFT JOIN request_results x ON x.request_id = r.id
            WHERE r.status NOT IN ('integration-approved', 'integration-denied')
        '''
        params = []
        if session_id:
            base += ' AND r.session_id = ?'
            params.append(session_id)
        if inner_target:
            base += ' AND r.inner_target = ?'
            params.append(inner_target)
        cursor.execute(base + ' ORDER BY r.id DESC LIMIT 200', tuple(params))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_pending_request_by_cmd(target_ip: str, command: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM requests WHERE target_ip = ? AND command = ? AND status IN ('pending','approved') LIMIT 1
        ''', (target_ip, command))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_request_status(req_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT status FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return row['status'] if row else None


def get_request_row(req_id: int):
    """Full row for one request (used by the agent-facing check_request tool)."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def expire_stale_requests(now_ts: int = None):
    """Move timed-out JIT rows to terminal states without deleting them.

    `expired`  = never approved within the 90s TTL.
    `abandoned`= approved but not claimed within the TTL + the 60s grace used by
                 update_ticket_consumed_by_ip (so a late poll can still claim it).

    Returns (expired_ids, abandoned_ids). The rows and their commands stay for
    the audit trail; only the status changes."""
    now = int(now_ts or time.time())
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM requests WHERE status = 'pending' AND expires_at < ?", (now,))
        expired = [row['id'] for row in cursor.fetchall()]
        cursor.execute("UPDATE requests SET status = 'expired' WHERE status = 'pending' AND expires_at < ?", (now,))
        cursor.execute("SELECT id FROM requests WHERE status = 'approved' AND expires_at < ?", (now - 60,))
        abandoned = [row['id'] for row in cursor.fetchall()]
        cursor.execute("UPDATE requests SET status = 'abandoned' WHERE status = 'approved' AND expires_at < ?", (now - 60,))
        conn.commit()
        return expired, abandoned

def get_request_command(req_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT command FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return row['command'] if row else None

def get_request_target_ip(req_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT target_ip FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return row['target_ip'] if row else None

def count_denied(command: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM requests WHERE command = ? AND status = 'denied'", (command,))
        row = cursor.fetchone()
        return row[0] if row else 0

def get_ticket_by_request_id(req_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT command, created_at, status FROM requests WHERE id = ? AND status IN ("approved", "consumed")', (req_id,))
        row = cursor.fetchone()
        if row:
            if row['status'] == 'approved':
                now = int(time.time())
                cursor.execute("UPDATE requests SET status = 'consumed', expires_at = ? WHERE id = ?", (now, req_id))
                conn.commit()
            return {"command": row['command'], "ticket": f"{row['created_at']}|{row['command']}"}
        return None

def delete_old_requests(before_ts: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM requests WHERE created_at < ?', (before_ts,))
        conn.commit()

def search_requests(query: str, limit: int = 200, session_id: str = None,
                    inner_target: str = None):
    with db_conn() as conn:
        cursor = conn.cursor()
        pattern = f"%{query}%"
        sql = '''
            SELECT r.*, g.hostname, x.exit_code, x.duration_ms,
                   x.truncated AS output_truncated
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            LEFT JOIN request_results x ON x.request_id = r.id
            WHERE (r.command LIKE ? OR r.target_ip LIKE ? OR r.status LIKE ? OR g.hostname LIKE ?)
              AND r.status NOT IN ('integration-approved', 'integration-denied')
        '''
        params = [pattern, pattern, pattern, pattern]
        if session_id:
            sql += ' AND r.session_id = ?'
            params.append(session_id)
        if inner_target:
            sql += ' AND r.inner_target = ?'
            params.append(inner_target)
        sql += ' ORDER BY r.id DESC LIMIT ?'
        params.append(limit)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_session_requests(session_id: str, limit: int = 500):
    """All SSH requests for one session (oldest first), with the gateway
    hostname joined. Excludes the legacy API-surfaced statuses. Backs the
    session-detail view."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.*, g.hostname, x.exit_code, x.duration_ms,
                   x.truncated AS output_truncated
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            LEFT JOIN request_results x ON x.request_id = r.id
            WHERE r.session_id = ?
              AND r.status NOT IN ('integration-approved', 'integration-denied')
            ORDER BY r.id ASC LIMIT ?
        ''', (session_id, limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

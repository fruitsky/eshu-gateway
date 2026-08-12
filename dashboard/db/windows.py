import time
import random
import string
import secrets
import sqlite3
from db.core import db_conn

def _make_retrieval_key():
    return secrets.token_urlsafe(24)

def init_windows_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS approved_windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            target_ip TEXT NOT NULL,
            command TEXT NOT NULL,
            window_start INTEGER NOT NULL,
            window_end INTEGER NOT NULL,
            max_executions INTEGER DEFAULT 1,
            execution_count INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            label TEXT DEFAULT '',
            created_at INTEGER NOT NULL,
            last_used_at INTEGER DEFAULT 0,
            days_of_week INTEGER DEFAULT 0,
            execution_time INTEGER DEFAULT 0,
            expires_at INTEGER DEFAULT NULL,
            match_type TEXT DEFAULT 'exact'
        )
    ''')
    for col, defval in [('days_of_week', '0'), ('execution_time', '0'), ('expires_at', 'NULL'), ('match_type', "'exact'"), ('status', "'active'")]:
        try:
            cursor.execute(f"ALTER TABLE approved_windows ADD COLUMN {col} TEXT DEFAULT {defval}")
        except sqlite3.OperationalError:
            pass
    cursor.execute("UPDATE approved_windows SET status = 'disabled' WHERE enabled = 0 AND status IS NULL")
    cursor.execute("UPDATE approved_windows SET status = 'active' WHERE enabled = 1 AND status IS NULL")
    for col, defval in [('origin', "'human'")]:
        try:
            cursor.execute(f"ALTER TABLE approved_windows ADD COLUMN {col} TEXT DEFAULT {defval}")
        except sqlite3.OperationalError:
            pass
    # v15.x migration: opaque retrieval key for public window/request reads —
    # kills numeric-id enumeration (sequential AUTOINCREMENT). Backfill existing rows.
    try:
        cursor.execute("ALTER TABLE approved_windows ADD COLUMN retrieval_key TEXT")
    except sqlite3.OperationalError:
        pass
    cursor.execute("SELECT id FROM approved_windows WHERE retrieval_key IS NULL OR retrieval_key = ''")
    for (wid,) in cursor.fetchall():
        cursor.execute("UPDATE approved_windows SET retrieval_key = ? WHERE id = ?", (_make_retrieval_key(), wid))
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_win_retrieval_key ON approved_windows(retrieval_key)")
    try:
        cursor.execute("ALTER TABLE requests ADD COLUMN reason TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # v15.5 migration: rebuild approved_windows with proper INTEGER columns
    cursor.execute("PRAGMA table_info(approved_windows)")
    cols = {row[1]: row[2] for row in cursor.fetchall()}
    if cols.get('days_of_week', '').upper() == 'TEXT':
        cursor.execute('''
            CREATE TABLE approved_windows_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                target_ip TEXT NOT NULL,
                command TEXT NOT NULL,
                window_start INTEGER NOT NULL,
                window_end INTEGER NOT NULL,
                max_executions INTEGER DEFAULT 1,
                execution_count INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                label TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                last_used_at INTEGER DEFAULT 0,
                days_of_week INTEGER DEFAULT 0,
                execution_time INTEGER DEFAULT 0,
                expires_at INTEGER DEFAULT NULL,
                match_type TEXT DEFAULT 'exact',
                status TEXT DEFAULT 'active',
                origin TEXT DEFAULT 'human',
                retrieval_key TEXT
            )
        ''')
        cursor.execute('''
            INSERT INTO approved_windows_new SELECT
                id, token, target_ip, command,
                CAST(window_start AS INTEGER), CAST(window_end AS INTEGER),
                CAST(max_executions AS INTEGER), CAST(execution_count AS INTEGER),
                CAST(enabled AS INTEGER), label, CAST(created_at AS INTEGER),
                CAST(last_used_at AS INTEGER), CAST(days_of_week AS INTEGER),
                CAST(execution_time AS INTEGER),
                CASE WHEN expires_at IS NULL OR expires_at = '' OR expires_at = 'None'
                     THEN NULL ELSE CAST(expires_at AS INTEGER) END,
                match_type, COALESCE(status, 'active'), COALESCE(origin, 'human'),
                retrieval_key
            FROM approved_windows
        ''')
        cursor.execute("DROP TABLE approved_windows")
        cursor.execute("ALTER TABLE approved_windows_new RENAME TO approved_windows")
        cursor.execute("SELECT id FROM approved_windows WHERE retrieval_key IS NULL OR retrieval_key = ''")
        for (wid,) in cursor.fetchall():
            cursor.execute("UPDATE approved_windows SET retrieval_key = ? WHERE id = ?", (_make_retrieval_key(), wid))
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_win_retrieval_key ON approved_windows(retrieval_key)")
    # Window execution history
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS window_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_id INTEGER NOT NULL,
            token TEXT NOT NULL,
            target_ip TEXT NOT NULL,
            command TEXT NOT NULL,
            executed_at INTEGER NOT NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_win_exec_window ON window_executions(window_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_win_exec_at ON window_executions(executed_at)')
    for col, defval in [('success', '1'), ('reason', "''")]:
        try:
            cursor.execute(f"ALTER TABLE window_executions ADD COLUMN {col} INTEGER DEFAULT {defval}")
        except sqlite3.OperationalError:
            pass

def create_approved_window(target_ip: str, command: str, window_start: int = 0, window_end: int = 0,
                           max_executions: int = 1, label: str = '', days_of_week: int = 0,
                           execution_time: int = 0, expires_at: int = None,
                           match_type: str = 'exact', origin: str = 'human') -> dict:
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    retrieval_key = _make_retrieval_key()
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO approved_windows
            (token, target_ip, command, window_start, window_end,
             max_executions, label, created_at,
             days_of_week, execution_time, expires_at, match_type, origin, retrieval_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (token, target_ip, command, window_start, window_end,
              max_executions, label, now,
              days_of_week, execution_time, expires_at, match_type, origin, retrieval_key))
        conn.commit()
        wid = cursor.lastrowid
        cursor.execute('SELECT * FROM approved_windows WHERE id = ?', (wid,))
        row = cursor.fetchone()
        return dict(row) if row else {}

def update_approved_window(window_id: int, **kwargs) -> bool:
    allowed = ['command', 'label', 'max_executions', 'days_of_week',
               'execution_time', 'expires_at', 'match_type',
               'window_start', 'window_end']
    sets = []
    values = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            values.append(v)
    if not sets:
        return False
    values.append(window_id)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE approved_windows SET {', '.join(sets)} WHERE id = ?", values)
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

def get_approved_windows(ip: str = None) -> list:
    with db_conn() as conn:
        cursor = conn.cursor()
        expire_sql = '''
            UPDATE approved_windows SET enabled = 0
            WHERE expires_at IS NOT NULL AND enabled = 1
              AND CAST(expires_at AS INTEGER) < CAST(strftime('%s','now') AS INTEGER)
        '''
        if ip:
            cursor.execute(expire_sql + ' AND target_ip = ?', (ip,))
            cursor.execute('SELECT * FROM approved_windows WHERE target_ip = ? ORDER BY window_start DESC', (ip,))
        else:
            cursor.execute(expire_sql)
            cursor.execute('SELECT * FROM approved_windows ORDER BY window_start DESC')
        rows = cursor.fetchall()
        conn.commit()
        return [dict(row) for row in rows]

def get_active_approved_windows(ip: str = None) -> list:
    now = int(time.time())
    with db_conn() as conn:
        cursor = conn.cursor()
        if ip:
            cursor.execute('''
                SELECT * FROM approved_windows
                WHERE target_ip = ? AND enabled = 1
                  AND (
                    (window_start = 0 AND window_end = 0)
                    OR (window_start <= ? AND window_end >= ?)
                  )
                  AND (max_executions = 0 OR execution_count < max_executions)
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY window_start DESC
            ''', (ip, now, now, now))
        else:
            cursor.execute('''
                SELECT * FROM approved_windows
                WHERE enabled = 1
                  AND (
                    (window_start = 0 AND window_end = 0)
                    OR (window_start <= ? AND window_end >= ?)
                  )
                  AND (max_executions = 0 OR execution_count < max_executions)
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY window_start DESC
            ''', (now, now, now))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def delete_approved_window(window_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM approved_windows WHERE id = ?', (window_id,))
        conn.commit()

def toggle_approved_window(window_id: int, enabled: bool):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE approved_windows SET enabled = ? WHERE id = ?', (1 if enabled else 0, window_id))
        conn.commit()

def increment_window_execution(token: str) -> bool:
    now = int(time.time())
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE approved_windows
            SET execution_count = execution_count + 1, last_used_at = ?
            WHERE token = ? AND enabled = 1
              AND (
                (window_start = 0 AND window_end = 0)
                OR (window_start <= ? AND window_end >= ?)
              )
              AND (max_executions = 0 OR execution_count < max_executions)
              AND (expires_at IS NULL OR expires_at > ?)
        ''', (now, token, now, now, now))
        updated = cursor.rowcount > 0
        if updated:
            cursor.execute('''
                UPDATE approved_windows
                SET enabled = 0
                WHERE token = ? AND max_executions > 0 AND execution_count >= max_executions
            ''', (token,))
        conn.commit()
        return updated

def get_recent_jit_approved(hours: int = 6, limit: int = 50, ip: str = None) -> list:
    cutoff = int(time.time()) - (hours * 3600)
    with db_conn() as conn:
        cursor = conn.cursor()
        if ip:
            cursor.execute('''
                SELECT r.*, g.hostname, g.mode
                FROM requests r
                LEFT JOIN gateways g ON r.target_ip = g.ip
                WHERE r.target_ip = ? AND r.status IN ('approved', 'consumed')
                  AND r.created_at > ?
                ORDER BY r.created_at DESC
                LIMIT ?
            ''', (ip, cutoff, limit))
        else:
            cursor.execute('''
                SELECT r.*, g.hostname, g.mode
                FROM requests r
                LEFT JOIN gateways g ON r.target_ip = g.ip
                WHERE r.status IN ('approved', 'consumed')
                  AND r.created_at > ?
                ORDER BY r.created_at DESC
                LIMIT ?
            ''', (cutoff, limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def create_window_request(target_ip: str, command: str, days_of_week: int = 0,
                          execution_time: int = 0, expires_at: int = None,
                          match_type: str = 'exact', max_executions: int = 0,
                          label: str = '', window_start: int = 0) -> dict:
    # Unique placeholder token — the column is UNIQUE, and an empty string would
    # collide with any other pending/denied agent request (500 IntegrityError).
    # approve_window_request() replaces it with the real token on approval.
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    retrieval_key = _make_retrieval_key()
    now = int(time.time())
    window_end = 0
    if window_start:
        window_end = expires_at if expires_at else 4102444800
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO approved_windows
            (token, target_ip, command, window_start, window_end,
             max_executions, label, created_at, enabled,
             days_of_week, execution_time, expires_at, match_type, status, origin, retrieval_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'pending_review', 'ai', ?)
        ''', (token, target_ip, command, window_start, window_end,
              max_executions, label, now,
              days_of_week, execution_time, expires_at, match_type, retrieval_key))
        conn.commit()
        wid = cursor.lastrowid
        cursor.execute('SELECT * FROM approved_windows WHERE id = ?', (wid,))
        row = cursor.fetchone()
        return dict(row) if row else {}

def get_window_request(request_id: int) -> dict:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM approved_windows WHERE id = ? AND status = "pending_review"', (request_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_window_request_by_key(retrieval_key: str) -> dict:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM approved_windows WHERE retrieval_key = ? AND status = "pending_review"', (retrieval_key,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_approved_window_by_id(window_id: int) -> dict:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM approved_windows WHERE id = ?', (window_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_approved_window_by_key(retrieval_key: str) -> dict:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM approved_windows WHERE retrieval_key = ?', (retrieval_key,))
        row = cursor.fetchone()
        return dict(row) if row else None

def approve_window_request(request_id: int) -> dict:
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE approved_windows
            SET status = 'active', enabled = 1, token = ?
            WHERE id = ? AND status = 'pending_review'
        ''', (token, request_id))
        updated = cursor.rowcount > 0
        conn.commit()
        if not updated:
            return None
        cursor.execute('SELECT * FROM approved_windows WHERE id = ?', (request_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_pending_window_requests() -> list:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM approved_windows WHERE status = "pending_review" ORDER BY created_at DESC')
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def record_window_execution(window_id: int, token: str, target_ip: str, command: str, success: int = 1, reason: str = ''):
    now = int(time.time())
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO window_executions (window_id, token, target_ip, command, executed_at, success, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (window_id, token, target_ip, command, now, success, reason))
        cursor.execute('''
            DELETE FROM window_executions
            WHERE window_id = ? AND id NOT IN (
                SELECT id FROM window_executions WHERE window_id = ?
                ORDER BY executed_at DESC LIMIT 500
            )
        ''', (window_id, window_id))
        conn.commit()

def get_window_executions(window_id: int, limit: int = 50) -> list:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM window_executions WHERE window_id = ?
            ORDER BY executed_at DESC, id DESC LIMIT ?
        ''', (window_id, limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

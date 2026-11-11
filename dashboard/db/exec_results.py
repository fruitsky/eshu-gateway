"""Execution results (F5): exit code, duration and the sanitized output tail.

One row per request (`request_results`), written by the gateway after the
command ends. The dashboard scrubs the output a second time and never lets a
later post overwrite a stored result (first result wins).

`exec_result_orphans` closes the race between the result post and the
`/api/log` post that creates the row it belongs to: an unmatched result waits
there (bounded, 120 s) and is attached when the row appears.
"""
import hashlib
import time

from db.core import db_conn

MAX_OUTPUT = 16384
ORPHAN_TTL = 120
ORPHAN_MAX_PER_GATEWAY = 50
RETENTION_META_KEY = 'exec_result_retention_days'
DEFAULT_RETENTION_DAYS = 30

# `path` is an internal gateway literal; the statuses it may match (D3).
PATH_STATUSES = {
    'allowlist': ('auto-approved',),
    'window': ('window-approved',),
    'lockbox': ('approved', 'consumed'),
    'jit': ('approved', 'consumed'),
}
EXECUTABLE_STATUSES = ('approved', 'consumed')


def init_exec_results_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS request_results (
            request_id INTEGER PRIMARY KEY,
            exit_code INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            output TEXT NOT NULL DEFAULT '',
            truncated INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
    ''')
    # F19: which run limit fired ('time', 'bytes' or ''). Additive: a row from
    # before this column reads as '' (no limit).
    _cols = {row[1] for row in cursor.execute(
        "PRAGMA table_info(request_results)").fetchall()}
    if 'limit_kind' not in _cols:
        try:
            cursor.execute(
                "ALTER TABLE request_results ADD COLUMN limit_kind TEXT DEFAULT ''")
        except Exception:
            pass
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exec_result_orphans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_ip TEXT NOT NULL,
            cmd_sha TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            output TEXT NOT NULL DEFAULT '',
            truncated INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_exec_orphans '
        'ON exec_result_orphans(target_ip, cmd_sha, created_at)')


def cmd_sha(command: str) -> str:
    """The exact-bytes key the gateway's `encoded_command` decodes to."""
    return hashlib.sha256((command or '').encode('utf-8', 'surrogatepass')).hexdigest()


def save_result(request_id: int, exit_code: int, duration_ms: int,
                output: str = '', truncated: bool = False, now: int = None,
                limit_kind: str = '') -> bool:
    """Store the first result for a request. False if one already exists (D4)."""
    limit_kind = limit_kind if limit_kind in ('', 'time', 'bytes') else ''
    with db_conn() as conn:
        cur = conn.execute('''
            INSERT OR IGNORE INTO request_results
                (request_id, exit_code, duration_ms, output, truncated,
                 created_at, limit_kind)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (int(request_id), int(exit_code), max(0, int(duration_ms)),
              output or '', 1 if truncated else 0, int(now or time.time()),
              limit_kind))
        conn.commit()
        return cur.rowcount == 1


def get_result(request_id: int):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM request_results WHERE request_id = ?',
                           (int(request_id),)).fetchone()
        return dict(row) if row else None


def find_match_request(target_ip: str, command: str, statuses, since: int):
    """Newest row for (ip, exact command) in `statuses` with no result yet."""
    statuses = tuple(statuses)
    if not statuses:
        return None
    placeholders = ','.join('?' for _ in statuses)
    sha = cmd_sha(command)
    with db_conn() as conn:
        rows = conn.execute(f'''
            SELECT r.id, r.command
            FROM requests r
            LEFT JOIN request_results x ON x.request_id = r.id
            WHERE r.target_ip = ? AND r.created_at >= ?
              AND r.status IN ({placeholders}) AND x.request_id IS NULL
            ORDER BY r.id DESC
            LIMIT 50
        ''', (target_ip, int(since)) + statuses).fetchall()
    for row in rows:
        if cmd_sha(row['command']) == sha:
            return row['id']
    return None


def add_orphan(target_ip: str, command: str, exit_code: int, duration_ms: int,
               output: str = '', truncated: bool = False, now: int = None):
    """Hold an unmatched result until `/api/log` creates its row (bounded)."""
    now = int(now or time.time())
    with db_conn() as conn:
        conn.execute('''
            INSERT INTO exec_result_orphans
                (target_ip, cmd_sha, exit_code, duration_ms, output, truncated, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (target_ip, cmd_sha(command), int(exit_code), max(0, int(duration_ms)),
              output or '', 1 if truncated else 0, now))
        conn.execute('DELETE FROM exec_result_orphans WHERE created_at < ?',
                     (now - ORPHAN_TTL,))
        # D6: bound the table per gateway, oldest first.
        stale = conn.execute('''
            SELECT id FROM exec_result_orphans WHERE target_ip = ?
            ORDER BY id DESC LIMIT -1 OFFSET ?
        ''', (target_ip, ORPHAN_MAX_PER_GATEWAY)).fetchall()
        for row in stale:
            conn.execute('DELETE FROM exec_result_orphans WHERE id = ?', (row['id'],))
        conn.commit()


def attach_orphan(target_ip: str, command: str, request_id: int, now: int = None) -> bool:
    """Attach the oldest live orphan for this exact command, if any."""
    now = int(now or time.time())
    sha = cmd_sha(command)
    with db_conn() as conn:
        row = conn.execute('''
            SELECT * FROM exec_result_orphans
            WHERE target_ip = ? AND cmd_sha = ? AND created_at >= ?
            ORDER BY id ASC LIMIT 1
        ''', (target_ip, sha, now - ORPHAN_TTL)).fetchone()
        if not row:
            return False
        cur = conn.execute('''
            INSERT OR IGNORE INTO request_results
                (request_id, exit_code, duration_ms, output, truncated, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (int(request_id), row['exit_code'], row['duration_ms'], row['output'],
              row['truncated'], row['created_at']))
        conn.execute('DELETE FROM exec_result_orphans WHERE id = ?', (row['id'],))
        conn.commit()
        return cur.rowcount == 1


def get_retention_days() -> int:
    with db_conn() as conn:
        row = conn.execute('SELECT value FROM meta WHERE key = ?',
                           (RETENTION_META_KEY,)).fetchone()
    try:
        days = int(row['value']) if row else DEFAULT_RETENTION_DAYS
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    return max(1, min(days, 365))


def set_retention_days(days: int) -> int:
    days = max(1, min(int(days), 365))
    with db_conn() as conn:
        conn.execute('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
                     (RETENTION_META_KEY, str(days)))
        conn.commit()
    return days


def cleanup_exec_results(days: int = None, now: int = None) -> int:
    """Drop results older than the retention window; returns rows deleted."""
    days = get_retention_days() if days is None else max(1, min(int(days), 365))
    now = int(now or time.time())
    with db_conn() as conn:
        cur = conn.execute('DELETE FROM request_results WHERE created_at < ?',
                           (now - days * 86400,))
        deleted = cur.rowcount
        conn.execute('DELETE FROM exec_result_orphans WHERE created_at < ?',
                     (now - ORPHAN_TTL,))
        conn.commit()
        return deleted

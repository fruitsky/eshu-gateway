import hashlib
import json
import re
import time
from db.core import db_conn

WINDOW_TOKEN_MIGRATION_KEY = 'window_token_audit_fp_v1'
_WINDOW_EVENTS = ('window_created', 'window_claimed',
                  'window_request_approved', 'window_deleted')
_TOKEN_FIELD = re.compile(r'token=([A-Za-z0-9]{1,64})(?![A-Za-z0-9_-])')
_TOKEN_CLAIM = re.compile(r'Token ([A-Za-z0-9]{1,64}) claimed')


def _token_fp(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]

def init_audit_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            gateway_ip TEXT,
            hostname TEXT,
            details TEXT,
            actor TEXT NOT NULL DEFAULT ''
        )
    ''')
    try:
        cursor.execute("ALTER TABLE audit_log ADD COLUMN actor TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass

def record_audit_event(event_type: str, gateway_ip: str = None, hostname: str = None,
                       details: str = None, actor: str = ''):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO audit_log (event_type, timestamp, gateway_ip, hostname, details, actor)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (event_type, now, gateway_ip, hostname, details, actor or ''))
        conn.commit()

def migrate_window_token_details():
    """Mask legacy window tokens in the four window audit event types once.

    Only the token field is rewritten; `?`/empty placeholders and rows that
    already carry `token_fp=` are skipped. The meta flag is written in the
    same transaction as the updates, so a second startup does nothing. The
    migration's own audit row contains a count, never a token.
    """
    with db_conn() as conn:
        row = conn.execute('SELECT value FROM meta WHERE key = ?',
                           (WINDOW_TOKEN_MIGRATION_KEY,)).fetchone()
        if row is not None:
            return 0
        conn.execute('BEGIN IMMEDIATE')
        rows = conn.execute(
            'SELECT id, event_type, details FROM audit_log '
            'WHERE event_type IN (?, ?, ?, ?)',
            _WINDOW_EVENTS).fetchall()
        changed = 0
        for item in rows:
            details = item['details'] or ''
            if 'token_fp=' in details:
                continue
            masked = _TOKEN_CLAIM.sub(
                lambda m: 'token_fp=%s claimed' % _token_fp(m.group(1)),
                details)
            masked = _TOKEN_FIELD.sub(
                lambda m: 'token_fp=%s' % _token_fp(m.group(1)), masked)
            if masked == details:
                continue
            conn.execute('UPDATE audit_log SET details = ? WHERE id = ?',
                         (masked, item['id']))
            changed += 1
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (WINDOW_TOKEN_MIGRATION_KEY, json.dumps(1)))
        conn.commit()
    if changed:
        record_audit_event(
            'window_token_audit_masked',
            details='masked %d window audit row(s)' % changed,
            actor='system')
    return changed


def get_audit_log(limit: int = 200):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?', (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def search_audit_log(query: str, limit: int = 200):
    with db_conn() as conn:
        cursor = conn.cursor()
        pattern = f"%{query}%"
        cursor.execute('''
            SELECT * FROM audit_log
            WHERE event_type LIKE ? OR gateway_ip LIKE ? OR hostname LIKE ?
               OR details LIKE ? OR actor LIKE ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (pattern, pattern, pattern, pattern, pattern, limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

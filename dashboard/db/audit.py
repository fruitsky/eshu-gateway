import time
from db.core import db_conn

def init_audit_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            gateway_ip TEXT,
            hostname TEXT,
            details TEXT
        )
    ''')

def record_audit_event(event_type: str, gateway_ip: str = None, hostname: str = None, details: str = None):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO audit_log (event_type, timestamp, gateway_ip, hostname, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (event_type, now, gateway_ip, hostname, details))
        conn.commit()

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
            WHERE event_type LIKE ? OR gateway_ip LIKE ? OR hostname LIKE ? OR details LIKE ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (pattern, pattern, pattern, pattern, limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

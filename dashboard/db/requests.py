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
    # v15.5 migration: add reason column
    for col, defval in [('reason', "''")]:
        try:
            cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT DEFAULT {defval}")
        except Exception:
            pass

def create_request(target_ip: str, command: str, status: str = 'pending', ttl: int = 90, reason: str = ''):
    with db_conn() as conn:
        cursor = conn.cursor()
        created_at = int(time.time())
        expires_at = created_at + ttl
        cursor.execute('''
            INSERT INTO requests (target_ip, command, status, created_at, expires_at, reason)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (target_ip, command, status, created_at, expires_at, reason))
        conn.commit()
        return cursor.lastrowid

def update_request_status(req_id: int, status: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE requests SET status = ? WHERE id = ?', (status, req_id))
        conn.commit()

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

def get_all_requests():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.*, g.hostname
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            ORDER BY r.id DESC LIMIT 200
        ''')
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

def get_request_command(req_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT command FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return row['command'] if row else None

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

def search_requests(query: str, limit: int = 200):
    with db_conn() as conn:
        cursor = conn.cursor()
        pattern = f"%{query}%"
        cursor.execute('''
            SELECT r.*, g.hostname
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            WHERE r.command LIKE ? OR r.target_ip LIKE ? OR r.status LIKE ? OR g.hostname LIKE ?
            ORDER BY r.id DESC LIMIT ?
        ''', (pattern, pattern, pattern, pattern, limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

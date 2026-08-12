import time
import hashlib
from db.core import db_conn

def init_enrollment_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS enrollment_tokens (
            token TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER DEFAULT 0
        )
    ''')

def get_ssh_keys():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM meta WHERE key IN ('eshu_ssh_key')")
        rows = cursor.fetchall()
        return {row['key']: row['value'] for row in rows}

def save_ssh_keys(eshu_key: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('eshu_ssh_key', ?)", (eshu_key,))
        conn.commit()

def generate_enrollment_token(ttl_seconds: int = 120):
    with db_conn() as conn:
        cursor = conn.cursor()
        token = hashlib.sha256(str(time.time() + hash(str(conn))).encode()).hexdigest()[:24]
        now = int(time.time())
        cursor.execute('''
            INSERT INTO enrollment_tokens (token, created_at, expires_at, used)
            VALUES (?, ?, ?, 0)
        ''', (token, now, now + ttl_seconds))
        conn.commit()
        return token

def validate_enrollment_token(token: str) -> tuple:
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('SELECT * FROM enrollment_tokens WHERE token = ?', (token,))
        row = cursor.fetchone()
        if not row:
            return (False, "Token not found")
        if row['used']:
            return (False, "Token already used")
        if now > row['expires_at']:
            return (False, "Token expired")
        cursor.execute('UPDATE enrollment_tokens SET used = 1 WHERE token = ?', (token,))
        conn.commit()
        return (True, "OK")

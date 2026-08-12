import time
from db.core import db_conn

def init_policies_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS policies (
            type TEXT PRIMARY KEY,
            content TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS policy_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            policy_type TEXT NOT NULL,
            old_content TEXT NOT NULL,
            new_content TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('policy_version', '1')")
    cursor.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('policy_updated_at', '0')")
    cursor.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('eshu_ssh_key', '')")
    cursor.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('dashboard_password', '')")
    cursor.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('notify_webhook_url', '')")
    cursor.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('notify_webhook_events', 'jit,window')")

def get_policies():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM policies')
        rows = cursor.fetchall()
        return {row['type']: row['content'] for row in rows}

def update_policy(p_type: str, content: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO policies (type, content) VALUES (?, ?)', (p_type, content))
        conn.commit()

def get_policy_version():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'policy_version'")
        row = cursor.fetchone()
        return int(row['value']) if row else 0

def increment_policy_version():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'policy_version'")
        conn.commit()

def get_policy_updated_at():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'policy_updated_at'")
        row = cursor.fetchone()
        return int(row['value']) if row else 0

def set_policy_updated_at(ts: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('policy_updated_at', ?)", (str(ts),))
        conn.commit()

def record_policy_change(policy_type: str, old_content: str, new_content: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO policy_changes (timestamp, policy_type, old_content, new_content)
            VALUES (?, ?, ?, ?)
        ''', (now, policy_type, old_content, new_content))
        conn.commit()

def get_policy_changes():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM policy_changes ORDER BY timestamp DESC LIMIT 50')
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_policy_change(change_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM policy_changes WHERE id = ?', (change_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

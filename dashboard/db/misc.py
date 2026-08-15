import time
import json
from db.core import db_conn

def init_misc_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY,
            content TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS feature_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flag_name TEXT UNIQUE NOT NULL,
            enabled INTEGER NOT NULL,
            description TEXT
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO notes (id, content) VALUES (1, '--- Eshu Gateway Notes ---\nWelcome to your dashboard.')")
    try:
        cursor.execute("ALTER TABLE feature_flags ADD COLUMN scope TEXT DEFAULT 'dev'")
    except Exception:
        pass
    # Approved Windows are core/always-on now (decoupled from the feature-flag
    # system) — remove the old toggleable flag if it exists.
    cursor.execute("DELETE FROM feature_flags WHERE flag_name = 'approved_windows'")

def get_note():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT content FROM notes WHERE id = 1')
        row = cursor.fetchone()
        return row['content'] if row else ''

def update_note(content: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO notes (id, content) VALUES (1, ?)', (content,))
        conn.commit()

def get_feature_flags():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM feature_flags')
        rows = cursor.fetchall()
        return {row['flag_name']: {'enabled': bool(row['enabled']), 'description': row['description'], 'scope': dict(row).get('scope', 'dev')} for row in rows}

def set_feature_flag(flag_name: str, enabled: bool):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE feature_flags SET enabled = ? WHERE flag_name = ?', (1 if enabled else 0, flag_name))
        conn.commit()

def set_feature_flag_scope(flag_name: str, scope: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE feature_flags SET scope = ? WHERE flag_name = ?", (scope, flag_name))
        conn.commit()

def get_notify_config() -> dict:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'notify_webhook_url'")
        row = cursor.fetchone()
        url = row['value'] if row else ''
        cursor.execute("SELECT value FROM meta WHERE key = 'notify_webhook_events'")
        row = cursor.fetchone()
        events = row['value'] if row else 'jit,window'
        cursor.execute("SELECT value FROM meta WHERE key = 'notify_dashboard_url'")
        row = cursor.fetchone()
        dash_url = row['value'] if row else ''
        return {'url': url, 'events': events, 'dashboard_url': dash_url}

def set_notify_config(url: str, events: str, dashboard_url: str = ''):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('notify_webhook_url', ?)", (url,))
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('notify_webhook_events', ?)", (events,))
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('notify_dashboard_url', ?)", (dashboard_url,))
        conn.commit()

def get_dev_tools_enabled() -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'dev_tools_enabled'")
        row = cursor.fetchone()
        return bool(row and row['value'] == '1')

def set_dev_tools_enabled(enabled: bool):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('dev_tools_enabled', ?)", ('1' if enabled else '',))
        conn.commit()

# Dismissed policy gaps
def init_dismissed_gaps_table():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dismissed_policy_gaps (
                command TEXT PRIMARY KEY
            )
        ''')
        conn.commit()

def get_deployed_golden_hash() -> str:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'deployed_golden_hash'")
        row = cursor.fetchone()
        return row['value'] if row else None

def set_deployed_golden_hash(h: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('deployed_golden_hash', ?)", (h,))
        conn.commit()

def get_dev_push_initiated() -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'dev_push_initiated'")
        row = cursor.fetchone()
        return row['value'] == '1' if row else False

def set_dev_push_initiated():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('dev_push_initiated', '1')")
        conn.commit()

def clear_dev_push_initiated():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM meta WHERE key = 'dev_push_initiated'")
        conn.commit()

def dismiss_policy_gap(cmd: str):
    init_dismissed_gaps_table()
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO dismissed_policy_gaps (command) VALUES (?)', (cmd,))
        conn.commit()

def get_dismissed_policy_gaps() -> set:
    init_dismissed_gaps_table()
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT command FROM dismissed_policy_gaps')
        rows = cursor.fetchall()
        return {r['command'] for r in rows}

# Seen-gaps tracking (persistent "new" detection)
def get_seen_gaps() -> dict:
    """Return dict of {ip: {command: timestamp}} for gaps the operator has seen."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'learning_seen_gaps'")
        row = cursor.fetchone()
        if not row or not row['value']:
            return {}
        try:
            return json.loads(row['value'])
        except Exception:
            return {}

def set_seen_gaps(seen: dict):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('learning_seen_gaps', ?)",
                       (json.dumps(seen),))
        conn.commit()

def get_mcp_allowed_hosts() -> str:
    """Comma-separated Host allowlist for the MCP endpoint (DNS-rebinding
    protection). Loopback hosts are always allowed regardless of this value."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'mcp_allowed_hosts'")
        row = cursor.fetchone()
        return row['value'] if row else ''

def set_mcp_allowed_hosts(value: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('mcp_allowed_hosts', ?)",
                       (value,))
        conn.commit()

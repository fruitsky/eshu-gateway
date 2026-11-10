import secrets
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
    # Session enforcement: off by default. Set to Dev to enforce on dev-mode
    # gateways (and MCP) while testing, Prod to enforce everywhere.
    cursor.execute('''
        INSERT OR IGNORE INTO feature_flags
            (flag_name, enabled, description, scope)
        VALUES
            ('require_sessions', 0,
             'Refuse SSH commands and MCP calls that are not in a minted, named Session',
             'dev')
    ''')

def get_meta_json(key: str, default):
    """Small JSON values kept in the `meta` table (watch state, etc.)."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = cursor.fetchone()
    if not row:
        return default
    try:
        return json.loads(row['value'])
    except Exception:
        return default


def set_meta_json(key: str, value):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                       (key, json.dumps(value)))
        conn.commit()


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

# ── Session signing key ───────────────────────────────────────────────────
# One per dashboard. Sent to authenticated gateways in the policy response so
# they can verify a session id was minted here, without a per-session sync or
# a round-trip back to the dashboard.
_SESSION_KEY = 'session_signing_key'
_SESSION_KEY_PREV = 'session_signing_key_prev'


def _meta_value(cursor, key: str) -> str:
    cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row['value'] if row and row['value'] else ''


def get_session_signing_key() -> str:
    """Return the dashboard's signing key, creating it on first use."""
    with db_conn() as conn:
        cursor = conn.cursor()
        key = _meta_value(cursor, _SESSION_KEY)
        if key:
            return key
        candidate = secrets.token_hex(32)
        cursor.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            (_SESSION_KEY, candidate))
        conn.commit()
        return _meta_value(cursor, _SESSION_KEY) or candidate


def get_previous_session_signing_key() -> str:
    """The previous key during a rotation; gateways accept both."""
    with db_conn() as conn:
        return _meta_value(conn.cursor(), _SESSION_KEY_PREV)


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

def get_session_names() -> dict:
    """Return {session_id: {name, description}} persisted server-side so session
    names survive across browsers (previously localStorage-only)."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'session_names'")
        row = cursor.fetchone()
        if not row or not row['value']:
            return {}
        try:
            return json.loads(row['value'])
        except Exception:
            return {}

def set_session_names(names: dict):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('session_names', ?)",
                       (json.dumps(names or {}),))
        conn.commit()


# ── F19: run limits for unattended runs ─────────────────────────────────────
# One fleet-wide pair of numbers, kept in a meta key so the operator can change
# it without a deploy. A missing or corrupt value falls back to the defaults:
# a broken setting must never remove the bound.
RUN_LIMIT_META_KEY = 'run_limit'
RUN_LIMIT_DEFAULTS = {'seconds': 60, 'bytes': 1048576}
RUN_LIMIT_RANGE = {'seconds': (5, 300), 'bytes': (65536, 8388608)}


def run_limit_problem(seconds, bytes_):
    """Return an error string when a proposed limit is out of range."""
    for key, value in (('seconds', seconds), ('bytes', bytes_)):
        lo, hi = RUN_LIMIT_RANGE[key]
        if isinstance(value, bool) or not isinstance(value, int):
            return '%s must be an integer' % key
        if value < lo or value > hi:
            return '%s must be between %d and %d' % (key, lo, hi)
    return ''


def get_run_limit_settings() -> dict:
    """The limit sent with an unattended row; always inside the valid range."""
    raw = get_meta_json(RUN_LIMIT_META_KEY, None)
    out = dict(RUN_LIMIT_DEFAULTS)
    if isinstance(raw, dict):
        for key in ('seconds', 'bytes'):
            value = raw.get(key)
            if not isinstance(value, bool) and isinstance(value, int) and \
                    RUN_LIMIT_RANGE[key][0] <= value <= RUN_LIMIT_RANGE[key][1]:
                out[key] = value
    return out


def set_run_limit_settings(seconds: int, bytes_: int):
    set_meta_json(RUN_LIMIT_META_KEY, {'seconds': int(seconds),
                                       'bytes': int(bytes_)})
